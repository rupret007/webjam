import os
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable
from urllib.parse import urlparse
import socket
import time

from core.settings import AppSettings, load_settings
from ui.theme import DEFAULT_THEME


class SetupWizard:
    """Guided first-run setup wizard with basic preflight validation."""

    def __init__(
        self,
        root: tk.Misc,
        on_complete: Callable[[], None],
        settings: AppSettings | None = None,
        find_jamulus: Callable[[], str | None] | None = None,
        diagnostics_provider: Callable[[], dict[str, str]] | None = None,
        mode_label: str = "Music Jam",
        mode_help: str = "",
        bg_color: str | None = None,
        fg_color: str | None = None,
    ):
        self._bg = bg_color or DEFAULT_THEME.bg_secondary
        self._fg = fg_color or DEFAULT_THEME.text_primary
        self.root = root
        self.on_complete = on_complete
        self.settings = settings or load_settings()
        self.find_jamulus = find_jamulus or (lambda: None)
        self.diagnostics_provider = diagnostics_provider or (lambda: {})
        self.mode_label = mode_label
        self.mode_help = mode_help

        self.step_index = 0
        self.steps = [
            "Welcome",
            "Preflight Checks",
            "Finish",
        ]
        self.check_results: list[tuple[str, bool, str]] = []
        self._checks_inflight = False
        self.window: tk.Toplevel | None = None
        self.title_var = tk.StringVar(value="")
        self.body_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="")
        self.next_btn: tk.Button | None = None
        self.back_btn: tk.Button | None = None
        self.rerun_btn: tk.Button | None = None

    def show(self) -> None:
        self.window = tk.Toplevel(self.root)
        self.window.title("WebJam Setup Wizard")
        self.window.geometry("680x430")
        self.window.resizable(False, False)
        self.window.grab_set()
        self.window.transient(self.root)
        self.window.configure(bg=self._bg)
        self.window.protocol("WM_DELETE_WINDOW", self._close_without_complete)
        self.window.bind("<Escape>", lambda _e: self._close_without_complete())
        self.window.bind("<Return>", lambda _e: self._next_step())

        frame = tk.Frame(self.window, padx=16, pady=16, bg=self._bg)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(frame, textvariable=self.title_var, font=("Arial", 15, "bold"), anchor="w", justify=tk.LEFT, bg=self._bg, fg=self._fg)
        title.pack(fill=tk.X, pady=(0, 8))

        body = tk.Label(frame, textvariable=self.body_var, justify=tk.LEFT, anchor="nw", font=("Arial", 10), wraplength=640, bg=self._bg, fg=self._fg)
        body.pack(fill=tk.X, pady=(0, 12))

        results_box = tk.Text(frame, height=11, width=76, state=tk.DISABLED, wrap=tk.WORD, bg=self._bg, fg=self._fg)
        results_box.pack(fill=tk.BOTH, expand=True)
        self.results_box = results_box

        summary = tk.Label(frame, textvariable=self.summary_var, justify=tk.LEFT, anchor="w", font=("Arial", 10, "bold"), bg=self._bg, fg=self._fg)
        summary.pack(fill=tk.X, pady=(10, 8))

        actions = tk.Frame(frame)
        actions.pack(fill=tk.X, pady=(4, 0))

        self.back_btn = tk.Button(actions, text="Back", width=10, command=self._prev_step)
        self.back_btn.pack(side=tk.LEFT)

        self.rerun_btn = tk.Button(actions, text="Run Checks", width=12, command=self._run_checks_and_render)
        self.rerun_btn.pack(side=tk.LEFT, padx=(8, 0))

        tk.Button(actions, text="Open Help", width=10, command=self._open_help).pack(side=tk.RIGHT)
        self.next_btn = tk.Button(actions, text="Next", width=10, command=self._next_step)
        self.next_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self._render_step()

    def _open_help(self) -> None:
        help_text = (
            "Quick setup checklist:\n"
            "1) Use wired Ethernet when possible.\n"
            "2) Use headphones to avoid feedback.\n"
            "3) Keep Jamulus buffer around 64-128 samples.\n"
            "4) Confirm input/output devices before joining.\n\n"
            f"Current mode: {self.mode_label}\n"
            f"Mode hint: {self.mode_help or 'Use session canvas notes and prompts to align creative goals.'}"
        )
        messagebox.showinfo("Setup Help", help_text, parent=self.window)

    def _next_step(self) -> None:
        if self.step_index == 0:
            self.step_index = 1
            self._render_step()
            if not self.check_results:
                self._run_checks_and_render()
            return
        if self.step_index == 1 and not self.check_results:
            self._run_checks_and_render()
            return

        if self.step_index >= len(self.steps) - 1:
            self.on_complete()
            if self.window is not None:
                self.window.destroy()
            return

        self.step_index += 1
        self._render_step()

    def _prev_step(self) -> None:
        if self.step_index > 0:
            self.step_index -= 1
            self._render_step()

    def _close_without_complete(self) -> None:
        if self.window is not None:
            self.window.destroy()

    def _render_step(self) -> None:
        if self.step_index == 0:
            self.title_var.set("Step 1 of 3 - Welcome")
            self.body_var.set(
                "This wizard helps you verify audio and connection basics before your first session.\n\n"
                f"Current creative mode: {self.mode_label}\n"
                "Click Next to run preflight checks."
            )
            self._set_results_text(
                "- Jamulus executable discovery\n"
                "- Jamulus server DNS/connectivity check\n"
                "- Webex URL validity and reachability hint\n"
                "- Audio engine diagnostics snapshot"
            )
            self.summary_var.set("Action: Continue to run checks.")
        elif self.step_index == 1:
            self.title_var.set("Step 2 of 3 - Preflight Checks")
            if self._checks_inflight:
                self.body_var.set("Running preflight checks now. This can take a few seconds on a slow or blocked network.")
                self._set_results_text("Running checks...\n\nPlease wait while WebJam verifies Jamulus, Webex, and audio diagnostics.")
                self.summary_var.set("Checks in progress...")
            else:
                self.body_var.set("Review results below. If any check fails, fix it and click 'Run Checks' again.")
            if not self.check_results and not self._checks_inflight:
                self._set_results_text("No checks run yet.")
                self.summary_var.set("Action: Click 'Run Checks'.")
            elif not self._checks_inflight:
                self._render_results()
        else:
            self.title_var.set("Step 3 of 3 - Finish")
            self.body_var.set(
                "You are ready to start. Recommended sequence:\n"
                "1) Launch Jamulus\n"
                "2) Launch Webex\n"
                "3) Verify participants and adjust mixer levels\n"
                "4) Save your mix preset"
            )
            if self.check_results:
                passed = sum(1 for _, ok, _ in self.check_results if ok)
                total = len(self.check_results)
                self._set_results_text("Setup complete. You can re-open this wizard from Help -> Run Setup Wizard.")
                self.summary_var.set(f"Checks passed: {passed}/{total}. Click Finish.")
            else:
                self._set_results_text("No preflight checks were run. You can run them from Step 2 at any time.")
                self.summary_var.set("Checks passed: 0/0 (skipped). Click Finish.")

        self._update_navigation_controls()

    def _update_navigation_controls(self) -> None:
        if self.back_btn is not None:
            back_state = tk.DISABLED if self._checks_inflight else (tk.NORMAL if self.step_index > 0 else tk.DISABLED)
            self.back_btn.configure(state=back_state)
        if self.rerun_btn is not None:
            rerun_state = tk.NORMAL if self.step_index == 1 and not self._checks_inflight else tk.DISABLED
            self.rerun_btn.configure(state=rerun_state)
        if self.next_btn is not None:
            next_label = "Finish" if self.step_index == len(self.steps) - 1 else "Next"
            next_state = tk.DISABLED if self._checks_inflight else tk.NORMAL
            self.next_btn.configure(text=next_label, state=next_state)

    def _run_checks_and_render(self) -> None:
        if self._checks_inflight:
            return
        self._checks_inflight = True
        self.body_var.set("Running preflight checks now. This can take a few seconds on a slow or blocked network.")
        self.summary_var.set("Checks in progress...")
        self._set_results_text("Running checks...\n\nPlease wait while WebJam verifies Jamulus, Webex, and audio diagnostics.")
        self._update_navigation_controls()

        def _worker() -> None:
            try:
                results = self.run_preflight_checks(self.settings, self.find_jamulus, self.diagnostics_provider)
            except Exception as exc:
                results = [("Preflight checks", False, f"Checks failed unexpectedly: {exc}")]

            def _finish() -> None:
                self._checks_inflight = False
                self.check_results = results
                self._render_step()

            if self.window is None:
                self._checks_inflight = False
                self.check_results = results
                return
            try:
                if not self.window.winfo_exists():
                    self._checks_inflight = False
                    self.check_results = results
                    return
                self.window.after(0, _finish)
            except (tk.TclError, RuntimeError):
                self._checks_inflight = False
                self.check_results = results

        threading.Thread(target=_worker, daemon=True).start()

    def _render_results(self) -> None:
        lines: list[str] = []
        for name, ok, detail in self.check_results:
            icon = "PASS" if ok else "FAIL"
            lines.append(f"[{icon}] {name}\n{detail}\n")
        self._set_results_text("\n".join(lines).strip())
        failed = [name for name, ok, _ in self.check_results if not ok]
        if not failed:
            self.summary_var.set("All checks passed. Action: Continue to Finish.")
        else:
            joined = ", ".join(failed)
            self.summary_var.set(f"Action needed: resolve failed checks ({joined}) and run checks again.")

    def _set_results_text(self, text: str) -> None:
        self.results_box.configure(state=tk.NORMAL)
        self.results_box.delete("1.0", tk.END)
        self.results_box.insert("1.0", text)
        self.results_box.configure(state=tk.DISABLED)

    @staticmethod
    def run_preflight_checks(
        settings: AppSettings,
        find_jamulus: Callable[[], str | None],
        diagnostics_provider: Callable[[], dict[str, str]],
    ) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []

        jamulus_path_raw = find_jamulus()
        jamulus_path: str | None = None
        if isinstance(jamulus_path_raw, (str, bytes, os.PathLike)):
            jamulus_path = os.fspath(jamulus_path_raw)
        jamulus_ok = False
        if jamulus_path:
            try:
                jamulus_ok = os.access(jamulus_path, os.X_OK)
            except (TypeError, ValueError, OSError):
                jamulus_ok = False

        if jamulus_path_raw is not None and jamulus_path is None:
            jamulus_detail = (
                "Jamulus path provider returned an invalid path type "
                f"({type(jamulus_path_raw).__name__})."
            )
        elif jamulus_path and not jamulus_ok:
            jamulus_detail = f"Found {jamulus_path} but it is not marked as executable."
        elif jamulus_ok:
            jamulus_detail = f"Jamulus found at: {jamulus_path}"
        else:
            jamulus_detail = "Jamulus executable not found in default install paths. Run installer or install Jamulus manually."
        results.append(("Jamulus executable", jamulus_ok, jamulus_detail))

        host = settings.jamulus_server
        try:
            port = int(settings.jamulus_port)
            if not (1 <= port <= 65535):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            results.append(("Jamulus server reachability", False, f"Invalid port: {settings.jamulus_port}"))
            port = None
        if port is not None:
            server_ok, server_detail = SetupWizard.check_tcp_hint(host, port)
            results.append(("Jamulus server reachability", server_ok, server_detail))

        webex_ok, webex_detail = SetupWizard.check_webex_url(settings.webex_url)
        results.append(("Webex URL", webex_ok, webex_detail))

        try:
            raw_diagnostics = diagnostics_provider()
        except Exception as exc:
            active = False
            diag_detail = f"Audio diagnostics callback failed: {exc}"
        else:
            if isinstance(raw_diagnostics, dict):
                diagnostics = {str(k): str(v) for k, v in raw_diagnostics.items()}
                active = diagnostics.get("active", "False").strip().lower() == "true"
                diag_detail = (
                    f"Audio backend: {diagnostics.get('backend', 'unknown')}, "
                    f"samplerate: {diagnostics.get('samplerate', 'unknown')}, "
                    f"active: {diagnostics.get('active', 'unknown')}, "
                    f"message: {diagnostics.get('message', 'n/a')}"
                )
            else:
                active = False
                diag_detail = (
                    "Audio diagnostics unavailable: expected a mapping payload "
                    f"but got {type(raw_diagnostics).__name__}."
                )
        results.append(("Audio diagnostics", active, diag_detail))

        return results

    @staticmethod
    def check_webex_url(webex_url: str) -> tuple[bool, str]:
        if not isinstance(webex_url, str):
            return False, f"Invalid Webex URL: {webex_url}"
        candidate = webex_url.strip()
        if not candidate:
            return False, f"Invalid Webex URL: {webex_url}"
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            return False, f"Invalid Webex URL: {webex_url}"
        return True, f"Webex URL looks valid ({parsed.netloc})."

    @staticmethod
    def check_tcp_hint(host: str, port: int, retries: int = 3) -> tuple[bool, str]:
        """TCP connectivity hint. Jamulus uses UDP, so this only verifies DNS and basic network path."""
        if not isinstance(host, str) or not host.strip():
            return False, f"Invalid host: {host}"
        try:
            retries = int(retries)
        except (TypeError, ValueError):
            retries = 3
        retries = max(1, retries)
        last_exc: Exception | None = None
        note = " (Note: Jamulus uses UDP; this TCP check is a hint only.)"
        for attempt in range(retries):
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    if attempt == 0:
                        return True, f"Resolved and reached {host}:{port}.{note}"
                    return True, f"Reached {host}:{port} after retry {attempt}.{note}"
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(0.25 * (attempt + 1))
        return False, (
            f"Could not reach {host}:{port} after {retries} attempts ({last_exc}). "
            f"Confirm server/port and network path, then retry.{note}"
        )


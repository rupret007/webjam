import tkinter as tk
from tkinter import messagebox
from typing import Callable
from urllib.parse import urlparse
import socket
import time

from core.settings import AppSettings, load_settings


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
    ):
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
        self.window.protocol("WM_DELETE_WINDOW", self._close_without_complete)

        frame = tk.Frame(self.window, padx=16, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(frame, textvariable=self.title_var, font=("Arial", 15, "bold"), anchor="w", justify=tk.LEFT)
        title.pack(fill=tk.X, pady=(0, 8))

        body = tk.Label(frame, textvariable=self.body_var, justify=tk.LEFT, anchor="nw", font=("Arial", 10), wraplength=640)
        body.pack(fill=tk.X, pady=(0, 12))

        results_box = tk.Text(frame, height=11, width=76, state=tk.DISABLED, wrap=tk.WORD)
        results_box.pack(fill=tk.BOTH, expand=True)
        self.results_box = results_box

        summary = tk.Label(frame, textvariable=self.summary_var, justify=tk.LEFT, anchor="w", font=("Arial", 10, "bold"))
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
            self.body_var.set("Review results below. If any check fails, fix it and click 'Run Checks' again.")
            if not self.check_results:
                self._set_results_text("No checks run yet.")
                self.summary_var.set("Action: Click 'Run Checks'.")
            else:
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
            passed = sum(1 for _, ok, _ in self.check_results if ok)
            total = len(self.check_results)
            self._set_results_text("Setup complete. You can re-open this wizard from Help -> Run Setup Wizard.")
            self.summary_var.set(f"Checks passed: {passed}/{total}. Click Finish.")

        if self.back_btn is not None:
            self.back_btn.configure(state=(tk.NORMAL if self.step_index > 0 else tk.DISABLED))
        if self.rerun_btn is not None:
            self.rerun_btn.configure(state=(tk.NORMAL if self.step_index == 1 else tk.DISABLED))
        if self.next_btn is not None:
            self.next_btn.configure(text=("Finish" if self.step_index == len(self.steps) - 1 else "Next"))

    def _run_checks_and_render(self) -> None:
        self.check_results = self.run_preflight_checks(self.settings, self.find_jamulus, self.diagnostics_provider)
        self._render_results()

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

        jamulus_path = find_jamulus()
        jamulus_ok = jamulus_path is not None
        jamulus_detail = (
            f"Jamulus found at: {jamulus_path}"
            if jamulus_ok
            else "Jamulus executable not found in default install paths. Run installer or install Jamulus manually."
        )
        results.append(("Jamulus executable", jamulus_ok, jamulus_detail))

        host = settings.jamulus_server
        port = int(settings.jamulus_port)
        server_ok, server_detail = SetupWizard.check_tcp_hint(host, port)
        results.append(("Jamulus server reachability", server_ok, server_detail))

        webex_ok, webex_detail = SetupWizard.check_webex_url(settings.webex_url)
        results.append(("Webex URL", webex_ok, webex_detail))

        diagnostics = diagnostics_provider() or {}
        active = diagnostics.get("active", "False").lower() == "true"
        diag_detail = (
            f"Audio backend: {diagnostics.get('backend', 'unknown')}, "
            f"samplerate: {diagnostics.get('samplerate', 'unknown')}, "
            f"active: {diagnostics.get('active', 'unknown')}, "
            f"message: {diagnostics.get('message', 'n/a')}"
        )
        results.append(("Audio diagnostics", active, diag_detail))

        return results

    @staticmethod
    def check_webex_url(webex_url: str) -> tuple[bool, str]:
        parsed = urlparse(webex_url)
        if parsed.scheme != "https" or not parsed.netloc:
            return False, f"Invalid Webex URL: {webex_url}"
        return True, f"Webex URL looks valid ({parsed.netloc})."

    @staticmethod
    def check_tcp_hint(host: str, port: int, retries: int = 3) -> tuple[bool, str]:
        # Jamulus uses UDP, but a quick DNS/socket hint still helps catch obvious config errors.
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    if attempt == 0:
                        return True, f"Resolved and reached {host}:{port}."
                    return True, f"Reached {host}:{port} after retry {attempt}."
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(0.25 * (attempt + 1))
        return False, (
            f"Could not reach {host}:{port} after {retries} attempts ({last_exc}). "
            "Confirm server/port and network path, then retry."
        )


from __future__ import annotations

import tkinter as tk
from typing import Callable

from ui.theme import DEFAULT_THEME


def show_ready_check_panel(
    root: tk.Misc,
    checks: list[tuple[str, bool, str]],
    latency_label: str,
    participant_count: int,
    on_run_setup: Callable[[], None],
    on_open_diagnostics: Callable[[], None],
    on_export_bundle: Callable[[], None],
    bg_color: str | None = None,
    fg_color: str | None = None,
) -> None:
    bg = bg_color or DEFAULT_THEME.bg_secondary
    fg = fg_color or DEFAULT_THEME.text_primary
    panel = tk.Toplevel(root)
    panel.title("WebJam Ready Check")
    panel.geometry("740x460")
    panel.transient(root)
    panel.grab_set()
    panel.configure(bg=bg)
    panel.protocol("WM_DELETE_WINDOW", panel.destroy)
    panel.bind("<Escape>", lambda _e: panel.destroy())

    frame = tk.Frame(panel, padx=12, pady=12, bg=bg)
    frame.pack(fill=tk.BOTH, expand=True)

    total = len(checks)
    passed = sum(1 for _name, ok, _detail in checks if ok)
    failed_names = [name for name, ok, _detail in checks if not ok]
    if failed_names:
        status_line = f"Status: NOT READY ({passed}/{total} checks passed)"
        action_line = "Action: Run Setup Wizard and re-check failed items."
    else:
        status_line = f"Status: READY ({passed}/{total} checks passed)"
        action_line = "Action: Launch Jamulus + Webex when you are ready."

    text = tk.Text(frame, wrap=tk.WORD, bg=bg, fg=fg)
    text.pack(fill=tk.BOTH, expand=True)
    lines = [
        "WebJam Ready Check",
        "",
        status_line,
        latency_label,
        f"Participant channels detected: {participant_count}",
        "",
        "Preflight results:",
        "",
    ]
    for name, ok, detail in checks:
        marker = "PASS" if ok else "FAIL"
        lines.append(f"[{marker}] {name}")
        lines.append(f"  {detail}")
    lines.extend(["", action_line])
    if failed_names:
        lines.append(f"Failed checks: {', '.join(failed_names)}")
    text.insert("1.0", "\n".join(lines))
    text.configure(state=tk.DISABLED)

    btn_style = {"bg": bg, "fg": fg, "activebackground": fg, "activeforeground": bg}
    btn_row = tk.Frame(frame, bg=bg)
    btn_row.pack(fill=tk.X, pady=(8, 0))
    tk.Button(btn_row, text="Run Setup Wizard", command=on_run_setup, **btn_style).pack(side=tk.LEFT)
    tk.Button(btn_row, text="Open Diagnostics", command=on_open_diagnostics, **btn_style).pack(side=tk.LEFT, padx=8)
    tk.Button(btn_row, text="Export Bundle", command=on_export_bundle, **btn_style).pack(side=tk.LEFT, padx=8)
    tk.Button(btn_row, text="Close", command=panel.destroy, **btn_style).pack(side=tk.RIGHT)

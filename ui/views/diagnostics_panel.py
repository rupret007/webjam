from __future__ import annotations

import tkinter as tk
from typing import Callable

from ui.theme import DEFAULT_THEME


def show_diagnostics_panel(
    root: tk.Misc,
    jamulus_path: str,
    jamulus_server: str,
    jamulus_port: str,
    host_ok: bool,
    host_detail: str,
    webex_url: str,
    webex_last_error: str,
    audio_diagnostics: dict[str, str],
    on_run_setup: Callable[[], None],
    on_open_help: Callable[[], None],
    on_export_snapshot: Callable[[], None],
    on_reset_metrics: Callable[[], None],
    bg_color: str | None = None,
    fg_color: str | None = None,
) -> None:
    bg = bg_color or DEFAULT_THEME.bg_secondary
    fg = fg_color or DEFAULT_THEME.text_primary
    panel = tk.Toplevel(root)
    panel.title("WebJam Diagnostics")
    panel.geometry("700x420")
    panel.transient(root)
    panel.grab_set()
    panel.configure(bg=bg)
    panel.protocol("WM_DELETE_WINDOW", panel.destroy)

    frame = tk.Frame(panel, padx=12, pady=12, bg=bg)
    frame.pack(fill=tk.BOTH, expand=True)

    text = tk.Text(frame, wrap=tk.WORD)
    text.pack(fill=tk.BOTH, expand=True)
    lines = [
        "WebJam Diagnostics",
        "",
        f"Jamulus path: {jamulus_path}",
        f"Jamulus endpoint: {jamulus_server}:{jamulus_port}",
        f"Endpoint check: {'PASS' if host_ok else 'FAIL'} - {host_detail}",
        f"Webex URL: {webex_url}",
        f"Webex last error: {webex_last_error or 'none'}",
        "",
        "Audio Diagnostics:",
    ]
    for key, value in audio_diagnostics.items():
        lines.append(f"- {key}: {value}")
    text.insert("1.0", "\n".join(lines))
    text.configure(state=tk.DISABLED)

    btn_style = {"bg": bg, "fg": fg, "activebackground": fg, "activeforeground": bg}
    btn_row = tk.Frame(frame, bg=bg)
    btn_row.pack(fill=tk.X, pady=(8, 0))

    def _run_setup_and_regrab():
        on_run_setup()
        try:
            panel.grab_set()
        except tk.TclError:
            pass

    tk.Button(btn_row, text="Run Setup Wizard", command=_run_setup_and_regrab, **btn_style).pack(side=tk.LEFT)
    tk.Button(btn_row, text="Open Help", command=on_open_help, **btn_style).pack(side=tk.LEFT, padx=8)
    tk.Button(btn_row, text="Export Snapshot", command=on_export_snapshot, **btn_style).pack(side=tk.LEFT, padx=8)
    tk.Button(btn_row, text="Reset Metrics", command=on_reset_metrics, **btn_style).pack(side=tk.LEFT, padx=8)
    tk.Button(btn_row, text="Close", command=panel.destroy, **btn_style).pack(side=tk.RIGHT)

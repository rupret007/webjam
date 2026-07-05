from __future__ import annotations

import tkinter as tk


class Tooltip:
    """Small hover tooltip compatible with tkinter/customtkinter widgets."""

    def __init__(self, widget: tk.Misc, text: str, bg: str = "#111111", fg: str = "#f0f0f0"):
        self.widget = widget
        self.text = text
        self._tip_bg = bg
        self._tip_fg = fg
        self.tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None

        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel_schedule()
        self._after_id = self.widget.after(500, self._show)

    def _cancel_schedule(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self.tip_window is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            screen_w = self.widget.winfo_screenwidth()
            screen_h = self.widget.winfo_screenheight()
        except (tk.TclError, Exception):
            return

        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            wraplength=320,
            background=self._tip_bg,
            foreground=self._tip_fg,
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
            font=("Arial", 9),
        )
        label.pack()
        tw.update_idletasks()
        tip_w = tw.winfo_reqwidth()
        tip_h = tw.winfo_reqheight()
        if x + tip_w > screen_w:
            x = max(0, screen_w - tip_w - 4)
        if y + tip_h > screen_h:
            y = max(0, self.widget.winfo_rooty() - tip_h - 4)
        tw.wm_geometry(f"+{x}+{y}")
        self.tip_window = tw

    def _hide(self, _event=None) -> None:
        self._cancel_schedule()
        if self.tip_window is not None:
            try:
                self.tip_window.destroy()
            except (tk.TclError, Exception):
                pass
            self.tip_window = None

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable


def show_bootstrap_admin_notice(bootstrap_password: str) -> None:
    if not bootstrap_password:
        return
    messagebox.showwarning(
        "Admin Security Notice",
        "A one-time bootstrap admin password is active.\n\n"
        f"Username: admin\nPassword: {bootstrap_password}\n\n"
        "Sign in and set a new password immediately. "
        "The bootstrap password is removed after password change.",
    )


def prompt_password_change_dialog(
    username: str,
    update_password: Callable[[str, str], bool],
) -> bool:
    messagebox.showinfo(
        "Password Change Required",
        "For security, you must set a new password before using admin features.",
    )
    new_pw_1 = simpledialog.askstring("Set New Password", "Enter new password (8+ chars):", show="*")
    new_pw_2 = simpledialog.askstring("Confirm Password", "Re-enter new password:", show="*")
    if not new_pw_1 or not new_pw_2:
        return False
    if new_pw_1 != new_pw_2:
        messagebox.showerror("Password Mismatch", "Passwords do not match.")
        return False
    if len(new_pw_1) < 8:
        messagebox.showerror("Weak Password", "Password must be at least 8 characters.")
        return False
    ok = update_password(username, new_pw_1)
    if not ok:
        messagebox.showerror("Password Update Failed", "Could not update password.")
        return False
    return True


def show_usage_metrics_window(
    root: tk.Misc,
    metrics: dict[str, str],
    on_export: Callable[[], None],
    on_reset: Callable[[], None],
    refresh_metrics: Callable[[], dict[str, str]] | None = None,
) -> None:
    metrics_window = tk.Toplevel(root)
    metrics_window.title("Usage Metrics")
    metrics_window.geometry("680x460")
    metrics_window.transient(root)
    metrics_window.grab_set()

    container = tk.Frame(metrics_window, padx=12, pady=12)
    container.pack(fill=tk.BOTH, expand=True)
    text = tk.Text(container, wrap=tk.WORD)
    text.pack(fill=tk.BOTH, expand=True)

    lines = ["WebJam Local Usage Metrics", ""]
    for key, value in metrics.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("These counters are local-only and stored in your WebJam settings database.")
    text.insert("1.0", "\n".join(lines))
    text.configure(state=tk.DISABLED)

    buttons = tk.Frame(container)
    buttons.pack(fill=tk.X, pady=(8, 0))
    tk.Button(buttons, text="Export Snapshot", command=on_export).pack(side=tk.LEFT)

    def _render(current_metrics: dict[str, str]) -> None:
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        lines = ["WebJam Local Usage Metrics", ""]
        for key, value in current_metrics.items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append("These counters are local-only and stored in your WebJam settings database.")
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def _on_reset() -> None:
        on_reset()
        if refresh_metrics is not None:
            _render(refresh_metrics())

    tk.Button(buttons, text="Reset Metrics", command=_on_reset).pack(side=tk.LEFT, padx=8)
    tk.Button(buttons, text="Close", command=metrics_window.destroy).pack(side=tk.RIGHT)

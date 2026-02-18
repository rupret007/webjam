from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, messagebox
from typing import Optional

from admin.policy import UserContext
from storage.repository import WebJamRepository


class AdminPanel:
    def __init__(self, root: tk.Misc, repository: WebJamRepository, user: Optional[UserContext]):
        self.root = root
        self.repository = repository
        self.user = user

    def show(self) -> None:
        if not self.user:
            messagebox.showwarning("Admin Panel", "Sign in as admin or operator to open admin panel.")
            return

        window = tk.Toplevel(self.root)
        window.title("WebJam Admin Panel")
        window.geometry("800x500")

        title = tk.Label(window, text=f"Admin Panel - {self.user.username} ({self.user.role})", font=("Arial", 13, "bold"))
        title.pack(pady=10)

        settings_frame = tk.Frame(window)
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(settings_frame, text="Stored Settings", font=("Arial", 11, "bold")).pack(anchor="w")

        listbox = tk.Listbox(settings_frame, height=8)
        listbox.pack(fill=tk.X)
        for key, value in self.repository.list_settings().items():
            listbox.insert(tk.END, f"{key} = {value}")

        btn_frame = tk.Frame(window)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Button(btn_frame, text="Set Endpoint", command=lambda: self._set_endpoint(listbox)).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Refresh", command=lambda: self._refresh_settings(listbox)).pack(side=tk.LEFT, padx=5)

        audit_frame = tk.Frame(window)
        audit_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        tk.Label(audit_frame, text="Audit Log", font=("Arial", 11, "bold")).pack(anchor="w")
        audit_box = tk.Text(audit_frame, height=12)
        audit_box.pack(fill=tk.BOTH, expand=True)
        for row in self.repository.get_audit_log(limit=100):
            audit_box.insert(
                tk.END,
                f"[{row[4]}] #{row[0]} {row[2]} -> {row[1]} ({row[3]})\n",
            )

    def _set_endpoint(self, listbox: tk.Listbox) -> None:
        server = simpledialog.askstring("Server", "Jamulus Server Host:")
        port = simpledialog.askstring("Port", "Jamulus Server Port:")
        if not server or not port:
            return
        server = server.strip()
        if not server:
            messagebox.showerror("Invalid Server", "Server host cannot be empty.")
            return
        try:
            port_number = int(port.strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port must be a whole number between 1 and 65535.")
            return
        if port_number < 1 or port_number > 65535:
            messagebox.showerror("Invalid Port", "Port must be between 1 and 65535.")
            return

        self.repository.set_setting("jamulus_server", server)
        self.repository.set_setting("jamulus_port", str(port_number))
        self.repository.add_audit("change_endpoint", self.user.username if self.user else "unknown", f"{server}:{port_number}")
        self._refresh_settings(listbox)
        messagebox.showinfo("Saved", "Server endpoint updated in local settings.")

    def _refresh_settings(self, listbox: tk.Listbox) -> None:
        listbox.delete(0, tk.END)
        for key, value in self.repository.list_settings().items():
            listbox.insert(tk.END, f"{key} = {value}")


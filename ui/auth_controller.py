from __future__ import annotations

from tkinter import messagebox, simpledialog
from typing import Optional, Any

from admin.policy import PolicyEngine, UserContext
from ui.dialogs import prompt_password_change_dialog


class AuthController:
    def __init__(self, repository: Any, policy: PolicyEngine):
        self.repository = repository
        self.policy = policy

    def sign_in_interactive(self) -> Optional[UserContext]:
        username = simpledialog.askstring("Sign In", "Username:")
        password = simpledialog.askstring("Sign In", "Password:", show="*")
        if not username or not password:
            return None

        role, status = self.repository.authenticate_with_status(username, password)
        if status == "locked":
            messagebox.showerror(
                "Account Locked",
                "Too many failed sign-in attempts.\n\nTry again in about 5 minutes.",
            )
            return None
        if status == "invalid_credentials" or not role:
            messagebox.showerror("Sign In Failed", "Invalid username or password.")
            return None

        if status == "password_change_required":
            changed = prompt_password_change_dialog(username, self.repository.update_password)
            if not changed:
                messagebox.showwarning("Sign In Blocked", "Password change is required before continuing.")
                return None
            self.repository.add_audit("password_change", username, "initial password rotation complete")

        user = UserContext(username=username, role=role)
        self.repository.add_audit("signin", username, f"role={role}")
        messagebox.showinfo("Signed In", f"Signed in as {username} ({role}).")
        return user

    def authorize(self, current_user: Optional[UserContext], action: str, require_sign_in: bool = False) -> bool:
        if current_user is None:
            if require_sign_in:
                messagebox.showwarning("Permission Required", "Sign in required.")
                return False
            return True
        if not self.policy.allows(current_user, action):
            messagebox.showwarning("Permission Denied", f"Role '{current_user.role}' cannot perform '{action}'.")
            return False
        return True

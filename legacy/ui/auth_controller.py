from __future__ import annotations

from tkinter import messagebox, simpledialog
from typing import Optional, Any

from admin.policy import PolicyEngine, UserContext
from ui.dialogs import prompt_password_change_dialog


class AuthController:
    def __init__(self, repository: Any, policy: PolicyEngine):
        self.repository = repository
        self.policy = policy
        self._bootstrap_hint_shown = False

    def sign_in_interactive(self, parent: Any = None) -> Optional[UserContext]:
        bootstrap_path = None
        if not self._bootstrap_hint_shown:
            try:
                raw_path = self.repository.get_bootstrap_admin_credentials_path()
            except Exception:
                raw_path = None
            if isinstance(raw_path, str) and raw_path.strip():
                bootstrap_path = raw_path.strip()
        if bootstrap_path:
            messagebox.showinfo(
                "Bootstrap Admin Credentials",
                "If this is the first admin sign-in, use the bootstrap credentials stored at:\n\n"
                f"{bootstrap_path}\n\n"
                "Sign in as 'admin'. WebJam will require an immediate password change and then remove that file.",
                parent=parent,
            )
            self._bootstrap_hint_shown = True

        username_raw = simpledialog.askstring("Sign In", "Username:", parent=parent)
        if username_raw is None:
            return None
        password_raw = simpledialog.askstring("Sign In", "Password:", show="*", parent=parent)
        if password_raw is None:
            return None
        username = username_raw.strip()
        password = password_raw
        if not username or not password:
            messagebox.showerror("Sign In Failed", "Username and password are required.", parent=parent)
            return None

        try:
            role, status = self.repository.authenticate_with_status(username, password)
        except Exception as exc:
            messagebox.showerror("Sign In Failed", f"Could not verify credentials: {exc}", parent=parent)
            return None
        if status == "locked":
            messagebox.showerror(
                "Account Locked",
                "Too many failed sign-in attempts.\n\nTry again in about 5 minutes.",
                parent=parent,
            )
            return None
        if status == "invalid_credentials" or not role:
            messagebox.showerror("Sign In Failed", "Invalid username or password.", parent=parent)
            return None

        if status == "password_change_required":
            try:
                changed = prompt_password_change_dialog(username, self.repository.update_password, parent=parent)
            except Exception as exc:
                messagebox.showerror("Password Update Failed", f"Could not update password: {exc}", parent=parent)
                return None
            if not changed:
                messagebox.showwarning("Sign In Blocked", "Password change is required before continuing.", parent=parent)
                return None
            self.repository.add_audit("password_change", username, "initial password rotation complete")

        user = UserContext(username=username, role=role)
        self.repository.add_audit("signin", username, f"role={role}")
        messagebox.showinfo("Signed In", f"Signed in as {username} ({role}).", parent=parent)
        return user

    def authorize(
        self,
        current_user: Optional[UserContext],
        action: str,
        require_sign_in: bool = False,
        allow_anonymous: bool = False,
        parent: Any = None,
    ) -> bool:
        if current_user is None:
            if allow_anonymous and not require_sign_in:
                return True
            messagebox.showwarning("Permission Required", "Sign in required.", parent=parent)
            return False
        if not self.policy.allows(current_user, action):
            messagebox.showwarning("Permission Denied", f"Role '{current_user.role}' cannot perform '{action}'.", parent=parent)
            return False
        return True

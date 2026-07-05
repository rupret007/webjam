from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from admin.admin_panel import AdminPanel
from admin.policy import PolicyEngine, UserContext
from webjam_app_enhanced import WebJamEnhancedApp


class TestAdminPanelAuthorization(unittest.TestCase):
    def test_show_denies_when_not_signed_in(self):
        panel = AdminPanel(root=object(), repository=MagicMock(), user=None, policy=PolicyEngine())
        with patch("admin.admin_panel.messagebox") as messagebox_mock, patch("admin.admin_panel.tk.Toplevel") as top_level:
            panel.show()
        messagebox_mock.showwarning.assert_called_once()
        top_level.assert_not_called()

    def test_refresh_settings_redacts_sensitive_values(self):
        repo = MagicMock()
        repo.list_settings.return_value = {
            "safe_setting": "ok",
            "admin_bootstrap_password": "super-secret",
            "api_token": "token-value",
        }
        panel = AdminPanel(
            root=object(),
            repository=repo,
            user=UserContext(username="admin", role="admin"),
            policy=PolicyEngine(),
        )
        listbox = MagicMock()

        panel._refresh_settings(listbox)

        inserted = [call.args[1] for call in listbox.insert.call_args_list]
        self.assertIn("safe_setting = ok", inserted)
        self.assertIn("admin_bootstrap_password = [redacted]", inserted)
        self.assertIn("api_token = [redacted]", inserted)
        self.assertNotIn("admin_bootstrap_password = super-secret", inserted)

    def test_show_denies_role_without_admin_access(self):
        panel = AdminPanel(
            root=object(),
            repository=MagicMock(),
            user=UserContext(username="performer1", role="performer"),
            policy=PolicyEngine(),
        )
        with patch("admin.admin_panel.messagebox") as messagebox_mock, patch("admin.admin_panel.tk.Toplevel") as top_level:
            panel.show()
        messagebox_mock.showwarning.assert_called_once()
        top_level.assert_not_called()

    @patch("admin.admin_panel.simpledialog.askstring", side_effect=[None])
    @patch("admin.admin_panel.messagebox")
    def test_set_endpoint_stops_after_server_cancel(self, messagebox_mock, askstring_mock):
        repo = MagicMock()
        panel = AdminPanel(
            root=object(),
            repository=repo,
            user=UserContext(username="admin", role="admin"),
            policy=PolicyEngine(),
        )

        panel._set_endpoint(MagicMock(), parent=object())

        self.assertEqual(askstring_mock.call_count, 1)
        repo.set_setting.assert_not_called()
        messagebox_mock.showinfo.assert_not_called()


class TestAppAdminPanelFlow(unittest.TestCase):
    def _make_app(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.repository = MagicMock()
        app.policy = PolicyEngine()
        app.auth_controller = MagicMock()
        return app

    def test_open_admin_panel_does_not_prompt_reauth_for_unauthorized_signed_in_user(self):
        app = self._make_app()
        app.current_user = UserContext(username="performer1", role="performer")
        app.sign_in = MagicMock(return_value=True)
        app.auth_controller.authorize.return_value = False

        with patch("webjam_app_enhanced.AdminPanel") as admin_panel_cls:
            app.open_admin_panel()

        app.sign_in.assert_not_called()
        admin_panel_cls.assert_not_called()

    def test_open_admin_panel_signs_in_when_user_missing(self):
        app = self._make_app()
        app.current_user = None

        def _sign_in() -> bool:
            app.current_user = UserContext(username="admin", role="admin")
            return True

        app.sign_in = MagicMock(side_effect=_sign_in)
        app.auth_controller.authorize.return_value = True

        with patch("webjam_app_enhanced.AdminPanel") as admin_panel_cls:
            app.open_admin_panel()

        app.sign_in.assert_called_once()
        admin_panel_cls.assert_called_once_with(app.root, app.repository, app.current_user, app.policy)
        admin_panel_cls.return_value.show.assert_called_once()


if __name__ == "__main__":
    unittest.main()

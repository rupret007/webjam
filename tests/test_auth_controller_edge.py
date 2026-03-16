"""Edge-case tests for ui.auth_controller.AuthController."""

import unittest
from unittest.mock import MagicMock, patch

from admin.policy import PolicyEngine, UserContext
from ui.auth_controller import AuthController


class TestSignInCancel(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.policy = PolicyEngine()
        self.ctrl = AuthController(self.repo, self.policy)

    @patch("ui.auth_controller.simpledialog.askstring", return_value=None)
    def test_cancel_username(self, _ask):
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        self.assertEqual(_ask.call_count, 1)

    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", None])
    def test_cancel_password(self, _ask):
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        self.assertEqual(_ask.call_count, 2)

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["   ", "password"])
    def test_whitespace_username_rejected(self, _ask, _mb):
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        _mb.showerror.assert_called_once()


class TestSignInWrongPassword(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.policy = PolicyEngine()
        self.ctrl = AuthController(self.repo, self.policy)

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", "wrong"])
    def test_invalid_credentials(self, _ask, _mb):
        self.repo.authenticate_with_status.return_value = (None, "invalid_credentials")
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        _mb.showerror.assert_called_once()

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", "pass"])
    def test_locked_account(self, _ask, _mb):
        self.repo.authenticate_with_status.return_value = (None, "locked")
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        _mb.showerror.assert_called_once()
        self.assertIn("Locked", _mb.showerror.call_args[0][0])

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", "pass"])
    def test_repository_auth_exception_shows_error(self, _ask, _mb):
        self.repo.authenticate_with_status.side_effect = RuntimeError("db unavailable")
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        _mb.showerror.assert_called_once()


class TestSignInSuccess(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.policy = PolicyEngine()
        self.ctrl = AuthController(self.repo, self.policy)

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", "correct"])
    def test_success_returns_user_context(self, _ask, _mb):
        self.repo.authenticate_with_status.return_value = ("admin", "ok")
        result = self.ctrl.sign_in_interactive()
        self.assertIsNotNone(result)
        self.assertEqual(result.username, "admin")
        self.assertEqual(result.role, "admin")

    @patch("ui.auth_controller.messagebox")
    @patch("ui.auth_controller.prompt_password_change_dialog", side_effect=RuntimeError("dialog error"))
    @patch("ui.auth_controller.simpledialog.askstring", side_effect=["admin", "correct"])
    def test_password_change_dialog_exception_is_handled(self, _ask, _prompt, _mb):
        self.repo.authenticate_with_status.return_value = ("admin", "password_change_required")
        result = self.ctrl.sign_in_interactive()
        self.assertIsNone(result)
        _mb.showerror.assert_called_once()


class TestAuthorize(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.policy = PolicyEngine()
        self.ctrl = AuthController(self.repo, self.policy)

    @patch("ui.auth_controller.messagebox")
    def test_no_user_require_sign_in(self, _mb):
        self.assertFalse(self.ctrl.authorize(None, "some_action", require_sign_in=True))
        _mb.showwarning.assert_called_once()

    @patch("ui.auth_controller.messagebox")
    def test_no_user_denied_when_anonymous_not_allowed(self, _mb):
        self.assertFalse(self.ctrl.authorize(None, "some_action"))
        _mb.showwarning.assert_called_once()

    def test_no_user_allowed_when_anonymous_is_explicitly_allowed(self):
        self.assertTrue(self.ctrl.authorize(None, "some_action", allow_anonymous=True))

    @patch("ui.auth_controller.messagebox")
    def test_denied_action(self, _mb):
        user = UserContext(username="performer", role="performer")
        self.assertFalse(self.ctrl.authorize(user, "change_endpoint"))

    def test_allowed_action(self):
        user = UserContext(username="admin", role="admin")
        self.assertTrue(self.ctrl.authorize(user, "change_endpoint"))


if __name__ == "__main__":
    unittest.main()

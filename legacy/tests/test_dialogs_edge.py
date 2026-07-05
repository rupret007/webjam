from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ui.dialogs import prompt_password_change_dialog


class TestPromptPasswordChangeDialogEdge(unittest.TestCase):
    @patch("ui.dialogs.messagebox")
    @patch("ui.dialogs.simpledialog.askstring", return_value=None)
    def test_cancel_first_prompt_does_not_open_confirmation(self, ask_mock, messagebox_mock):
        result = prompt_password_change_dialog("admin", MagicMock(), parent=object())

        self.assertFalse(result)
        self.assertEqual(ask_mock.call_count, 1)
        messagebox_mock.showerror.assert_not_called()

    @patch("ui.dialogs.messagebox")
    @patch("ui.dialogs.simpledialog.askstring", side_effect=["NewStrongPass1", None])
    def test_cancel_confirmation_prompt_returns_false(self, ask_mock, messagebox_mock):
        result = prompt_password_change_dialog("admin", MagicMock(), parent=object())

        self.assertFalse(result)
        self.assertEqual(ask_mock.call_count, 2)
        messagebox_mock.showerror.assert_not_called()


if __name__ == "__main__":
    unittest.main()

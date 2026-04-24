from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

# All tests in this file require Windows (ctypes.windll). They are skipped
# automatically on macOS/Linux CI runners.
_WIN_ONLY = unittest.skipUnless(sys.platform == "win32", "Windows-only (ctypes.windll)")


@_WIN_ONLY
class TestInstallerElevationEdge(unittest.TestCase):
    def setUp(self):
        import webjam_installer  # deferred — module-level import fails on non-Windows
        self.webjam_installer = webjam_installer

    @patch("webjam_installer.is_admin", return_value=True)
    @patch("webjam_installer.ctypes.windll.shell32.ShellExecuteW")
    def test_elevate_if_needed_noop_when_already_admin(self, shell_mock, _is_admin_mock):
        self.webjam_installer.elevate_if_needed()
        shell_mock.assert_not_called()

    @patch("webjam_installer.is_admin", return_value=False)
    @patch("webjam_installer.ctypes.windll.shell32.ShellExecuteW", return_value=31)
    @patch("webjam_installer.input", return_value="")
    def test_elevate_if_needed_denied_exits_error(self, _input_mock, _shell_mock, _is_admin_mock):
        with self.assertRaises(SystemExit) as ctx:
            self.webjam_installer.elevate_if_needed()
        self.assertEqual(ctx.exception.code, 1)

    @patch("webjam_installer.is_admin", return_value=False)
    @patch("webjam_installer.ctypes.windll.shell32.ShellExecuteW", return_value=33)
    def test_elevate_if_needed_success_exits_zero(self, _shell_mock, _is_admin_mock):
        with self.assertRaises(SystemExit) as ctx:
            self.webjam_installer.elevate_if_needed()
        self.assertEqual(ctx.exception.code, 0)


@_WIN_ONLY
class TestLegacyLauncherElevationEdge(unittest.TestCase):
    def setUp(self):
        import webjam_launch_session
        import webjam_win_oneclick
        self.webjam_launch_session = webjam_launch_session
        self.webjam_win_oneclick = webjam_win_oneclick

    @patch("webjam_launch_session.is_admin", return_value=False)
    @patch("webjam_launch_session.ctypes.windll.shell32.ShellExecuteW", return_value=31)
    def test_launch_session_denied_exits_error(self, _shell_mock, _is_admin_mock):
        with self.assertRaises(SystemExit) as ctx:
            self.webjam_launch_session.elevate_if_needed()
        self.assertEqual(ctx.exception.code, 1)

    @patch("webjam_win_oneclick.is_admin", return_value=False)
    @patch("webjam_win_oneclick.ctypes.windll.shell32.ShellExecuteW", return_value=31)
    def test_win_oneclick_denied_exits_error(self, _shell_mock, _is_admin_mock):
        with self.assertRaises(SystemExit) as ctx:
            self.webjam_win_oneclick.elevate_if_needed()
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()

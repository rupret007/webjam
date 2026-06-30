"""The Ready Check (core/preflight) is reachable from the Conductor via F2."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestReadyCheckShortcut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = cls._tmp.name
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="RC",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()
        if cls._old_home is not None:
            os.environ["HOME"] = cls._old_home
        else:
            os.environ.pop("HOME", None)
        cls._tmp.cleanup()

    def test_f2_shortcut_exists(self):
        self.assertEqual(self.window._ready_check_shortcut.key().toString(), "F2")

    def test_handler_runs_report_without_blocking(self):
        with mock.patch.object(QMessageBox, "exec", return_value=0), \
             mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(all_ok=True, to_text=lambda: "Ready Check\n  ✓ ok")
            self.controller._on_ready_check()  # must not raise or block
        run.assert_called_once_with(self.controller.settings)


if __name__ == "__main__":
    unittest.main()

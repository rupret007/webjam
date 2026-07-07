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


class _FakeMessageBox:
    class Icon:
        Information = object()
        Warning = object()

    class ButtonRole:
        ActionRole = object()
        RejectRole = object()

    clicked = None
    instances = []

    def __init__(self, parent=None):
        self.parent = parent
        self.buttons = []
        self.text = ""
        _FakeMessageBox.instances.append(self)

    def setWindowTitle(self, title):
        self.title = title

    def setIcon(self, icon):
        self.icon = icon

    def setText(self, text):
        self.text = text

    def addButton(self, label, role):
        button = object()
        self.buttons.append((label, role, button))
        return button

    def exec(self):
        return 0

    def clickedButton(self):
        if _FakeMessageBox.clicked == "settings":
            return next(
                button for label, _role, button in self.buttons
                if label == "Open Settings"
            )
        return None


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

    def test_visible_ready_check_button_exists_and_emits(self):
        strip = self.window.session_strip
        self.assertEqual(strip._ready_button.accessibleName(), "Run Ready Check")
        received = []
        strip.ready_check_requested.connect(lambda: received.append(True))
        with mock.patch.object(QMessageBox, "exec", return_value=0), \
             mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=True, to_text=lambda: "Ready Check\n  ✓ ok"
            )
            strip._ready_button.click()
        self.assertEqual(received, [True])

    def test_handler_runs_report_without_blocking(self):
        with mock.patch.object(QMessageBox, "exec", return_value=0), \
             mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(all_ok=True, to_text=lambda: "Ready Check\n  ✓ ok")
            self.controller._on_ready_check()  # must not raise or block
        run.assert_called_once_with(self.controller.settings)

    def test_failed_report_close_does_not_open_settings(self):
        _FakeMessageBox.instances = []
        _FakeMessageBox.clicked = None
        self.controller._open_settings_wizard = mock.Mock()
        with mock.patch("PySide6.QtWidgets.QMessageBox", _FakeMessageBox), \
             mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=False, to_text=lambda: "Ready Check\n  ✗ missing"
            )

            self.controller._on_ready_check()

        self.controller._open_settings_wizard.assert_not_called()
        self.assertEqual(
            [label for label, _role, _button in _FakeMessageBox.instances[-1].buttons],
            ["Open Settings", "Close"],
        )

    def test_failed_report_open_settings_action_runs_wizard(self):
        _FakeMessageBox.instances = []
        _FakeMessageBox.clicked = "settings"
        self.controller._open_settings_wizard = mock.Mock()
        with mock.patch("PySide6.QtWidgets.QMessageBox", _FakeMessageBox), \
             mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=False, to_text=lambda: "Ready Check\n  ✗ missing"
            )

            self.controller._on_ready_check()

        self.controller._open_settings_wizard.assert_called_once()


if __name__ == "__main__":
    unittest.main()

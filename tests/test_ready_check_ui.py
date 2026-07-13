"""Band Check is reachable from the Conductor via F2."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from core.preflight import CheckItem, ReadyCheckReport  # noqa: E402
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

    def setUp(self):
        dialog = getattr(self.controller, "_ready_check_dialog", None)
        if dialog is not None:
            dialog.close()
            _app.processEvents()
        self.controller._ready_check_dialog = None

    def test_f2_shortcut_exists(self):
        self.assertEqual(self.window._ready_check_shortcut.key().toString(), "F2")

    def test_visible_ready_check_button_exists_and_emits(self):
        strip = self.window.session_strip
        self.assertEqual(
            strip._test_button.accessibleName(), "Band Check and solo practice"
        )
        self.assertEqual(strip._ready_action.text(), "Band Check\tF2")
        received = []
        strip.ready_check_requested.connect(lambda: received.append(True))
        strip._ready_action.trigger()
        self.assertEqual(received, [True])

    def test_handler_runs_report_without_blocking(self):
        with mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=True, to_text=lambda: "Ready Check\n  ✓ ok"
            )
            self.controller._on_ready_check()  # must not raise or block
            for _ in range(20):
                _app.processEvents()
                if run.called:
                    break
        run.assert_called_once_with(self.controller.settings)
        self.assertTrue(self.controller._ready_check_dialog.isVisible())

    def test_failed_report_close_does_not_open_settings(self):
        self.controller._open_settings_wizard = mock.Mock()
        with mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=False, to_text=lambda: "Ready Check\n  ✗ missing"
            )
            self.controller._on_ready_check()
            _app.processEvents()
            self.controller._ready_check_dialog.close()
            _app.processEvents()
        self.controller._open_settings_wizard.assert_not_called()

    def test_failed_report_open_settings_action_runs_wizard(self):
        self.controller._open_settings_wizard = mock.Mock()
        with mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = mock.Mock(
                all_ok=False, to_text=lambda: "Ready Check\n  ✗ missing"
            )
            self.controller._on_ready_check()
            _app.processEvents()
            self.controller._ready_check_dialog.settings_requested.emit()
        self.controller._open_settings_wizard.assert_called_once()

    def test_practice_button_label_matches_checks_menu(self):
        """One name everywhere: the Checks menu calls it 'Practice Solo'."""
        from PySide6.QtWidgets import QPushButton
        from webjam_qt.windows.ready_check import ReadyCheckDialog
        dialog = ReadyCheckDialog(lambda: AppSettings())
        labels = [b.text() for b in dialog.findChildren(QPushButton)]
        self.assertIn("Practice Solo", labels)
        self.assertNotIn("Start Practice", labels)
        dialog.close()

    def test_structured_report_separates_required_and_optional_results(self):
        from webjam_qt.windows.ready_check import ReadyCheckDialog
        dialog = ReadyCheckDialog(lambda: AppSettings())
        dialog.show()
        _app.processEvents()
        dialog._scan_id += 1
        report = ReadyCheckReport(items=[
            CheckItem("Jamulus", True, "found"),
            CheckItem("Server", False, "enter a host"),
            CheckItem("Webex bridge", False, "not needed", required=False),
        ])
        dialog._apply_report((dialog._scan_id, report))
        self.assertIn("Fix 1 required item", dialog._summary.text())
        self.assertIn("1 optional warning", dialog._summary.text())
        rows = dialog._report_content.findChildren(QFrame, "ReadyCheckRow")
        self.assertEqual([row.property("result") for row in rows], ["pass", "fail", "warn"])
        _app.processEvents()
        self.assertEqual(rows[1].focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertIn("Required failure", rows[1].accessibleName())
        dialog.close()

    def test_hosted_recorder_pre_start_audio_renders_as_warning_not_fix(self):
        """Hosting Mac before Start Audio: recorder unreachable is expected
        and must not block the jam with a required FIX row."""
        from webjam_qt.windows.ready_check import ReadyCheckDialog
        dialog = ReadyCheckDialog(lambda: AppSettings())
        dialog.show()
        _app.processEvents()
        dialog._scan_id += 1
        report = ReadyCheckReport(items=[
            CheckItem("Jamulus", True, "found"),
            CheckItem(
                "Host recorder",
                False,
                "couldn't reach the band server's recorder — this Mac hosts "
                "the server, and WebJam starts it with Start Audio.",
                required=False,
            ),
        ])
        dialog._apply_report((dialog._scan_id, report))
        self.assertNotIn("Fix", dialog._summary.text())
        self.assertIn("1 optional warning", dialog._summary.text())
        rows = dialog._report_content.findChildren(QFrame, "ReadyCheckRow")
        self.assertEqual([row.property("result") for row in rows], ["pass", "warn"])
        dialog.close()

    def test_manual_verify_rows_update_summary_and_reset_on_rerun(self):
        from webjam_qt.windows.ready_check import ReadyCheckDialog

        dialog = ReadyCheckDialog(lambda: AppSettings())
        dialog.show()
        _app.processEvents()
        dialog._scan_id += 1
        report = ReadyCheckReport(items=[
            CheckItem("Jamulus", True, "found"),
            CheckItem(
                "Webex muted for Play",
                False,
                "Confirm mute",
                manual_verification=True,
            ),
        ])
        dialog._apply_report((dialog._scan_id, report))
        self.assertEqual(
            dialog._summary.text(),
            "Automated checks passed; confirm 1 Webex setting.",
        )
        verify = dialog._report_content.findChild(QCheckBox, "ReadyCheckMark")
        self.assertIsNotNone(verify)
        self.assertEqual(verify.text(), "VERIFY")
        verify.setChecked(True)
        self.assertEqual(
            dialog._summary.text(), "Ready to play — all required checks passed."
        )
        with mock.patch("core.preflight.run_ready_check") as run:
            run.return_value = report
            dialog.run_checks()
            self.assertFalse(report.items[1].ok)
        dialog.close()

    def test_full_manual_report_scrolls_without_clipping_rows(self):
        from webjam_qt.windows.ready_check import ReadyCheckDialog

        dialog = ReadyCheckDialog(lambda: AppSettings())
        dialog.show()
        _app.processEvents()
        dialog._scan_id += 1
        report = ReadyCheckReport(items=[
            CheckItem(
                f"Webex setting {index}",
                False,
                "Confirm this setting in native Webex before the rehearsal.",
                manual_verification=True,
            )
            for index in range(10)
        ])
        dialog._apply_report((dialog._scan_id, report))
        _app.processEvents()

        rows = dialog._report_content.findChildren(QFrame, "ReadyCheckRow")
        self.assertEqual(len(rows), 10)
        self.assertTrue(
            all(row.height() >= row.minimumSizeHint().height() for row in rows)
        )
        self.assertGreater(dialog._report.verticalScrollBar().maximum(), 0)
        dialog.close()


if __name__ == "__main__":
    unittest.main()

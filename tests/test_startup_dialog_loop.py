"""Startup can close normally without changing in-session modality or ownership."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from webjam_qt.startup_dialog import exec_startup_dialog


class StartupDialogLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.hide()
            widget.deleteLater()
        self.app.processEvents()

    def dialog(self, parent=None, kind=QDialog):
        dialog = kind(parent)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.widgets.append(dialog)
        return dialog

    def run_dialog(self, dialog, platform, action=None, *, qt_platform="cocoa"):
        observed = {}
        timed_out = []
        watchdog = QTimer(dialog)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: (timed_out.append(True), dialog.reject()))

        def interact():
            observed["modality"] = dialog.windowModality()
            observed["active_modal"] = self.app.activeModalWidget()
            (action or dialog.close)()

        watchdog.start(1000)
        QTimer.singleShot(0, dialog, interact)
        try:
            with (
                patch("sys.platform", platform),
                patch.object(QApplication, "platformName", return_value=qt_platform),
                patch.object(QApplication, "quit") as quit_app,
            ):
                result = exec_startup_dialog(self.app, dialog)
                quit_app.assert_not_called()
        finally:
            watchdog.stop()
        self.assertFalse(timed_out, "Startup did not complete within the test deadline")
        self.assertFalse(dialog.isVisible())
        return result, observed

    def test_mac_standalone_close_uses_no_modal_window_and_restores_modality(self):
        dialog = self.dialog()
        original_quit_setting = self.app.quitOnLastWindowClosed()
        result, observed = self.run_dialog(dialog, "darwin")
        self.assertEqual(result, QDialog.DialogCode.Rejected)
        self.assertEqual(observed["modality"], Qt.WindowModality.NonModal)
        self.assertIsNone(observed["active_modal"])
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.ApplicationModal)
        self.assertEqual(self.app.quitOnLastWindowClosed(), original_quit_setting)

    def test_mac_accept_returns_accepted(self):
        dialog = self.dialog()
        result, observed = self.run_dialog(dialog, "darwin", dialog.accept)
        self.assertEqual(result, QDialog.DialogCode.Accepted)
        self.assertIsNone(observed["active_modal"])

    def test_mac_close_still_obeys_busy_dialog_guard(self):
        class BusyDialog(QDialog):
            busy = True

            def closeEvent(self, event):
                if self.busy:
                    event.ignore()
                else:
                    super().closeEvent(event)

        dialog = self.dialog(kind=BusyDialog)
        survived = []

        def close_when_ready():
            dialog.close()
            survived.append(dialog.isVisible())
            dialog.busy = False
            QTimer.singleShot(0, dialog, dialog.close)

        result, _ = self.run_dialog(dialog, "darwin", close_when_ready)
        self.assertEqual(survived, [True])
        self.assertEqual(result, QDialog.DialogCode.Rejected)

    def test_mac_existing_visible_window_keeps_modal_behavior(self):
        peer = QWidget()
        self.widgets.append(peer)
        peer.show()
        dialog = self.dialog()
        result, observed = self.run_dialog(dialog, "darwin")
        self.assertEqual(result, QDialog.DialogCode.Rejected)
        self.assertEqual(observed["modality"], Qt.WindowModality.ApplicationModal)
        self.assertIs(observed["active_modal"], dialog)
        self.assertTrue(peer.isVisible())

    def test_mac_parented_dialog_keeps_modal_behavior(self):
        parent = QWidget()
        self.widgets.append(parent)
        dialog = self.dialog(parent)
        _, observed = self.run_dialog(dialog, "darwin")
        self.assertIs(observed["active_modal"], dialog)
        self.assertEqual(observed["modality"], Qt.WindowModality.ApplicationModal)

    def test_windows_and_linux_keep_modal_behavior(self):
        for platform in ("win32", "linux"):
            with self.subTest(platform=platform):
                dialog = self.dialog()
                _, observed = self.run_dialog(dialog, platform)
                self.assertEqual(observed["modality"], Qt.WindowModality.ApplicationModal)
                self.assertIs(observed["active_modal"], dialog)

    def test_mac_offscreen_keeps_modal_dialog_completion(self):
        dialog = self.dialog()
        result, observed = self.run_dialog(dialog, "darwin", qt_platform="offscreen")
        self.assertEqual(result, QDialog.DialogCode.Rejected)
        self.assertEqual(observed["modality"], Qt.WindowModality.ApplicationModal)
        self.assertIs(observed["active_modal"], dialog)

    def test_mac_synchronous_finish_during_show_does_not_wait(self):
        class AlreadyFinishedDialog(QDialog):
            def showEvent(self, event):
                super().showEvent(event)
                self.accept()

        dialog = self.dialog(kind=AlreadyFinishedDialog)
        result, _ = self.run_dialog(dialog, "darwin")
        self.assertEqual(result, QDialog.DialogCode.Accepted)

    def test_failed_show_restores_modality_and_allows_a_later_attempt(self):
        dialog = self.dialog()
        with (
            patch("sys.platform", "darwin"),
            patch.object(QApplication, "platformName", return_value="cocoa"),
            patch.object(dialog, "show", side_effect=RuntimeError("show failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "show failed"):
                exec_startup_dialog(self.app, dialog)
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.ApplicationModal)
        result, _ = self.run_dialog(dialog, "darwin", dialog.accept)
        self.assertEqual(result, QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()

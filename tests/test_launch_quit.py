"""The startup menu exits the idle door without starting or saving a session."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from core.settings import AppSettings
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path: Path, profile: str = "music", **kwargs) -> LaunchDialog:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile,
    )
    dialog = LaunchDialog(settings, **kwargs)
    dialog._menu_bar.setNativeMenuBar(False)
    return dialog


def _quit_actions(dialog: LaunchDialog) -> list[QAction]:
    return [
        action
        for action in dialog.findChildren(QAction)
        if action.menuRole() == QAction.MenuRole.QuitRole
    ]


@pytest.mark.parametrize("profile", ["art", "music"])
def test_idle_menu_quit_rejects_without_saving_or_quitting_qapplication(
    qapp, tmp_path: Path, profile: str,
):
    dialog = _dialog(tmp_path, profile)
    try:
        actions = _quit_actions(dialog)
        assert len(actions) == 1
        assert actions[0].isEnabled() and actions[0].isVisible()
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        dialog.show()
        qapp.processEvents()
        with patch.object(QApplication, "quit") as app_quit:
            actions[0].trigger()
            qapp.processEvents()
        assert rejected == [True]
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert not dialog.isVisible()
        app_quit.assert_not_called()
        assert not (tmp_path / "settings.json").exists()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_idle_standard_quit_shortcut_closes_the_modal_door(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    watchdog = QTimer(dialog)
    watchdog.setSingleShot(True)
    timed_out = []
    watchdog.timeout.connect(lambda: (timed_out.append(True), dialog.reject()))
    try:
        actions = _quit_actions(dialog)
        assert len(actions) == 1
        assert actions[0].shortcut() == QKeySequence(QKeySequence.StandardKey.Quit)
        dialog.show()
        dialog.activateWindow()
        qapp.processEvents()
        watchdog.start(2000)
        QTimer.singleShot(
            0, dialog,
            lambda: QTest.keySequence(dialog, QKeySequence(QKeySequence.StandardKey.Quit)),
        )
        result = dialog.exec()
        assert not timed_out, "The standard Quit shortcut did not close startup"
        assert result == QDialog.DialogCode.Rejected
        assert not (tmp_path / "settings.json").exists()
    finally:
        watchdog.stop()
        dialog.close()
        dialog.deleteLater()


def test_in_session_profile_picker_does_not_add_an_application_quit_action(
    qapp, tmp_path: Path,
):
    parent = QWidget()
    parent.show()
    dialog = _dialog(tmp_path, parent=parent, allow_workspace_choices=False)
    try:
        assert not _quit_actions(dialog)
        dialog.show()
        qapp.processEvents()
        dialog.reject()
        assert parent.isVisible()
        assert not (tmp_path / "settings.json").exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        parent.close()
        parent.deleteLater()

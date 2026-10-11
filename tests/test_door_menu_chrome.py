"""W11: the live door must not expose File / Help menu chrome."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from tests.support.start_ux import harvest_first_screen
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path: Path) -> LaunchDialog:
    with patch.object(sys, "platform", "darwin"):
        return LaunchDialog(
            AppSettings(
                config_file=str(tmp_path / "settings.json"),
                last_creator_profile_key="art",
            )
        )


def _windows_installer_dialog(
    tmp_path: Path,
    *,
    last_creator_profile_key: str = "art",
) -> LaunchDialog:
    with patch.object(sys, "platform", "win32"), patch(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
        return_value="C:/WebJam/Jamulus-installer.exe",
    ):
        return LaunchDialog(
            AppSettings(
                config_file=str(tmp_path / "settings.json"),
                last_creator_profile_key=last_creator_profile_key,
            )
        )


def test_door_has_no_menu_bar_and_file_is_not_spoken(qapp, tmp_path):
    dialog = _dialog(tmp_path)
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog._menu_bar is None
        spoken = harvest_first_screen(dialog)
        assert "new music project" not in spoken
        assert "session library" not in spoken
        assert not dialog._door_help_button.isVisibleTo(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize(
    "page",
    ("choice", "join"),
    ids=("choice_page", "join_page"),
)
@pytest.mark.parametrize(
    "key",
    (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space),
    ids=("return", "enter", "space"),
)
def test_focused_help_keyboard_opens_setup_without_submitting(
    qapp, tmp_path, page, key
):
    dialog = _windows_installer_dialog(tmp_path)
    try:
        dialog.show()
        qapp.processEvents()
        if page == "join":
            dialog.show_join()
            qapp.processEvents()
        dialog._door_help_button.setFocus()
        qapp.processEvents()
        assert dialog._door_help_button.hasFocus()
        QTest.keyClick(dialog._door_help_button, key)
        qapp.processEvents()
        assert dialog.selected_role == ""
        assert dialog.isVisible()
        assert dialog._pages.currentWidget() is dialog._setup_page
    finally:
        dialog.close()
        dialog.deleteLater()


def test_windows_parity_build_shows_one_inline_help_link(qapp, tmp_path):
    dialog = _windows_installer_dialog(tmp_path)
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog._menu_bar is None
        assert dialog._door_help_button.isVisibleTo(dialog)
        assert dialog._door_help_button.text() == "Help"
        assert "new music project" not in harvest_first_screen(dialog)
        dialog._door_help_button.click()
        qapp.processEvents()
        assert dialog._pages.currentWidget() is dialog._setup_page
    finally:
        dialog.close()
        dialog.deleteLater()

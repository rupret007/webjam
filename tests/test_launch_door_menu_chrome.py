"""Door must not expose File / Help menu chrome (W11)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMenuBar

import pytest
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(config_file=str(tmp_path / "settings.json"))


def test_door_has_no_file_or_help_menu_bar(qapp, tmp_path: Path):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog._menu_bar is None
        assert not dialog.findChildren(QMenuBar)
        assert "file" not in dialog.windowTitle().casefold()
        spoken = " ".join(
            widget.text()
            for widget in dialog.findChildren(type(dialog._host_button))
            if hasattr(widget, "text") and widget.isVisibleTo(dialog)
        ).casefold()
        assert "&file" not in spoken and "file" not in {
            widget.accessibleName().casefold()
            for widget in dialog.findChildren(type(dialog._host_button))
            if widget.isVisibleTo(dialog) and widget.accessibleName()
        }
    finally:
        dialog.close()
        dialog.deleteLater()


def test_file_workspace_routes_stay_off_the_door_when_choices_are_hidden(
    qapp, tmp_path: Path
):
    dialog = LaunchDialog(_settings(tmp_path), allow_workspace_choices=False)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog._workspace_actions == {}
        assert not hasattr(dialog, "_session_library_action")
        dialog._open_workspace("music")
        dialog._open_session_library()
        assert dialog.selected_role == ""
    finally:
        dialog.close()
        dialog.deleteLater()


def test_windows_door_offers_one_inline_help_for_music_setup(qapp, tmp_path: Path):
    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
            return_value="C:/WebJam/Jamulus-installer.exe",
        ),
    ):
        dialog = LaunchDialog(_settings(tmp_path))
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog._menu_bar is None
        link = dialog._music_setup_link
        assert link.text() == "Help"
        assert link.isVisibleTo(dialog)
        assert dialog.rect().contains(link.mapTo(dialog, link.rect().center()))
        link.click()
        qapp.processEvents()
        assert dialog._pages.currentWidget() is dialog._setup_page
        assert dialog._install_jamulus_button.isVisibleTo(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_hidden_workspace_actions_remain_for_recovery_flows(qapp, tmp_path: Path):
    dialog = LaunchDialog(_settings(tmp_path), allow_workspace_choices=True)
    dialog.show()
    qapp.processEvents()
    try:
        assert set(dialog._workspace_actions) == {
            "music",
            "podcast_voice",
            "review_rehearsal",
        }
        assert not dialog.findChildren(QMenuBar)
    finally:
        dialog.close()
        dialog.deleteLater()

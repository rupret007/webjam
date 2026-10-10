"""Paint along door helper stays readable at the supported 800×600 floor."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog, PAINT_ALONG_DOOR_HELPER


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dialog(tmp_path: Path) -> LaunchDialog:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key="art",
        last_creator_start_key="paint_along",
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._menu_bar.setNativeMenuBar(False)
    return dialog


def test_paint_along_helper_sits_above_host_at_800_by_600(qapp, tmp_path: Path):
    """The longer Paint along line belongs above Host so it cannot fall below the fold."""

    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        host = dialog._host_button
        assert dialog.selected_start_key == "paint_along"
        assert helper.text() == PAINT_ALONG_DOOR_HELPER
        assert host.isDefault() is True

        helper_bottom = helper.mapTo(dialog, helper.rect().bottomRight()).y()
        host_top = host.mapTo(dialog, host.rect().topLeft()).y()
        assert helper_bottom < host_top

        bounds = dialog.rect()
        mapped = QRect(helper.mapTo(dialog, QPoint()), helper.size())
        assert bounds.contains(mapped)
        assert helper.height() >= helper.heightForWidth(helper.width())

        spoken = harvest_first_screen(dialog)
        assert PAINT_ALONG_DOOR_HELPER.casefold() in spoken
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_paint_along_helper_survives_larger_door_text_at_800_by_600(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        assert helper.text() == PAINT_ALONG_DOOR_HELPER
        assert dialog.rect().contains(
            helper.mapTo(dialog, helper.rect().bottomRight())
        )
        assert helper.height() >= helper.heightForWidth(helper.width())
        assert dialog._host_button.isDefault() is True
    finally:
        dialog.close()
        dialog.deleteLater()

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


# Visible door line after W06. The pre-change helper was two sentences and
# belongs in accessible description only (see PAINT_ALONG_DOOR_HELPER_DETAIL).
_PAINT_ALONG_ONE_LINE = "Host picks a file or link, then shares the invite."
_PAINT_ALONG_FULL_SENTENCE = (
    "Host starts Paint along, then chooses a file or lesson link. "
    "Join uses the host's invite."
)


def _mapped_rect(widget, ancestor) -> QRect:
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def test_paint_along_helper_is_one_visible_line_at_800_by_600(qapp, tmp_path: Path):
    """Sighted copy is one short line; Host and Join stay fully on the door."""

    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        host = dialog._host_button
        join = dialog._join_button
        assert dialog.selected_start_key == "paint_along"
        assert helper.isVisibleTo(dialog)
        assert helper.text() == _PAINT_ALONG_ONE_LINE
        assert helper.text() == PAINT_ALONG_DOOR_HELPER
        assert "\n" not in helper.text()
        assert helper.text().count(".") == 1

        metrics = helper.fontMetrics()
        assert metrics.horizontalAdvance(helper.text()) <= helper.contentsRect().width()
        one_line = helper.heightForWidth(2000)
        assert helper.heightForWidth(helper.width()) == one_line
        assert helper.height() < 2 * metrics.lineSpacing()

        bounds = dialog.rect()
        helper_rect = _mapped_rect(helper, dialog)
        host_rect = _mapped_rect(host, dialog)
        join_rect = _mapped_rect(join, dialog)
        assert bounds.contains(helper_rect)
        assert bounds.contains(host_rect)
        assert bounds.contains(join_rect)
        assert host.isVisibleTo(dialog) and host.isEnabled()
        assert join.isVisibleTo(dialog) and join.isEnabled()
        assert helper_rect.bottom() < host_rect.top()
        assert host_rect.bottom() < join_rect.top()
        assert host.isDefault() is True
    finally:
        dialog.close()
        dialog.deleteLater()


def test_paint_along_full_sentence_stays_in_accessible_description_only(
    qapp, tmp_path: Path
):
    """The two-sentence explanation is spoken, not painted on the door."""

    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        assert helper.text() == _PAINT_ALONG_ONE_LINE
        assert helper.accessibleDescription() == _PAINT_ALONG_FULL_SENTENCE
        assert helper.toolTip() in ("", helper.text())
        assert _PAINT_ALONG_FULL_SENTENCE not in helper.text()
        assert _PAINT_ALONG_ONE_LINE != _PAINT_ALONG_FULL_SENTENCE

        spoken = harvest_first_screen(dialog)
        assert _PAINT_ALONG_ONE_LINE.casefold() in spoken
        assert _PAINT_ALONG_FULL_SENTENCE.casefold() in spoken
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_paint_along_one_line_helper_keeps_host_join_inside_supported_doors(
    qapp, tmp_path: Path, size: tuple[int, int]
):
    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        assert helper.text() == _PAINT_ALONG_ONE_LINE
        assert helper.accessibleDescription() == _PAINT_ALONG_FULL_SENTENCE
        bounds = dialog.rect()
        for widget in (helper, dialog._host_button, dialog._join_button):
            assert widget.isVisibleTo(dialog)
            assert bounds.contains(_mapped_rect(widget, dialog)), widget.objectName()
        metrics = helper.fontMetrics()
        assert metrics.horizontalAdvance(helper.text()) <= helper.contentsRect().width()
        assert helper.heightForWidth(helper.width()) == helper.heightForWidth(2000)
    finally:
        dialog.close()
        dialog.deleteLater()

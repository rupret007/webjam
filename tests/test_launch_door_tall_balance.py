"""Launch door vertical balance on supported tall window sizes."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous)


def _rect_in(child, ancestor) -> QRect:
    return QRect(child.mapTo(ancestor, QPoint(0, 0)), child.size())


def _door_anchor_widgets(dialog: LaunchDialog) -> list:
    anchors = [
        dialog._art_profile_card,
        dialog._music_profile_card,
        dialog._host_button,
        dialog._join_button,
    ]
    if dialog.selected_creator_profile_key == "art":
        anchors.extend(dialog._visible_start_cards())
    if dialog._choice_helper.isVisibleTo(dialog):
        anchors.append(dialog._choice_helper)
    return anchors


def _vertical_slack_in_pages(dialog: LaunchDialog) -> tuple[int, int]:
    pages = _rect_in(dialog._pages, dialog)
    rects = [_rect_in(widget, dialog) for widget in _door_anchor_widgets(dialog)]
    content_top = min(rect.top() for rect in rects)
    content_bottom = max(rect.bottom() for rect in rects)
    top_slack = max(0, content_top - pages.top())
    bottom_slack = max(0, pages.bottom() - content_bottom)
    return top_slack, bottom_slack


def _assert_music_block_centered_in_dialog(dialog: LaunchDialog) -> None:
    # Music fits the choice page to its content and centers that block with
    # the brand row in the outer layout, so slack lives around the group
    # rather than inside the pages stack.
    top = min(
        dialog._logo.mapTo(dialog, QPoint(0, 0)).y(),
        dialog._wordmark.mapTo(dialog, QPoint(0, 0)).y(),
    )
    bottom = (
        dialog._join_button.mapTo(dialog, QPoint(0, 0)).y()
        + dialog._join_button.height()
    )
    margins = dialog.layout().contentsMargins()
    available_top = dialog._menu_bar.height() + margins.top()
    available_bottom = dialog.height() - margins.bottom()
    assert abs((top - available_top) - (available_bottom - bottom)) <= 2


@pytest.mark.parametrize("profile_key", ["art", "music"])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_launch_door_centers_in_tall_windows(qapp, tmp_path: Path, profile_key: str, size: tuple[int, int]):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile_key,
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._menu_bar.setNativeMenuBar(False)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()

        for widget in _door_anchor_widgets(dialog):
            rect = _rect_in(widget, dialog)
            assert dialog.rect().contains(rect), widget.accessibleName()

        if dialog._visible_start_cards():
            top_slack, bottom_slack = _vertical_slack_in_pages(dialog)
            extra = top_slack + bottom_slack
            assert extra >= 40, "tall sizes should leave slack inside the pages stack"
            # Before centering, essentially all slack sat below Host/Join.
            assert top_slack >= 24
            assert bottom_slack >= 24
            assert abs(top_slack - bottom_slack) <= max(48, extra // 4)
        else:
            _assert_music_block_centered_in_dialog(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()

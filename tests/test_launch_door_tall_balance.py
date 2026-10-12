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
    if dialog.selected_creator_profile_key == "music":
        # Music's content-sized page is centered together with the brand row
        # by the outer layout, so its balancing space is outside the pages.
        margins = dialog.layout().contentsMargins()
        content_top = min(_rect_in(widget, dialog).top()
                          for widget in (dialog._logo, dialog._wordmark))
        content_bottom = _rect_in(dialog._join_button, dialog).bottom()
        return (
            content_top - dialog._menu_bar.height() - margins.top(),
            dialog.rect().bottom() - margins.bottom() - content_bottom,
        )
    pages = _rect_in(dialog._pages, dialog)
    rects = [_rect_in(widget, dialog) for widget in _door_anchor_widgets(dialog)]
    content_top = min(rect.top() for rect in rects)
    content_bottom = max(rect.bottom() for rect in rects)
    top_slack = max(0, content_top - pages.top())
    bottom_slack = max(0, pages.bottom() - content_bottom)
    return top_slack, bottom_slack


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

        top_slack, bottom_slack = _vertical_slack_in_pages(dialog)
        extra = top_slack + bottom_slack
        assert extra >= 40, "tall sizes should leave slack around the door content"
        # Before centering, essentially all slack sat below Host/Join.
        assert top_slack >= 24
        assert bottom_slack >= 24
        assert abs(top_slack - bottom_slack) <= max(48, extra // 4)
    finally:
        dialog.close()
        dialog.deleteLater()

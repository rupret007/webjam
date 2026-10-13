"""CI smoke: launch door and Join stay readable at 800×600 and compact sizes."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QApplication, QLabel

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog

# 800×600 is the dialog's compact-screen floor / max size. 620×520 is the
# designed default compact door (a 520 px client plus title-bar allowance
# still sits inside the 760×600 physical floor).
FLOOR_800 = (800, 600)
COMPACT = (620, 520)
_LARGE_TEXT = "\nQWidget { font-size: 20px; }"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous)


def _dialog(tmp_path: Path, profile_key: str) -> LaunchDialog:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile_key,
        musician_name="Artist",
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._menu_bar.setNativeMenuBar(False)
    return dialog


def _settle(qapp) -> None:
    for _ in range(4):
        qapp.processEvents()


def _mapped(widget, owner) -> QRect:
    return QRect(widget.mapTo(owner, QPoint()), widget.size())


def _assert_fits(dialog, size: tuple[int, int], page) -> None:
    assert (dialog.width(), dialog.height()) == size
    bounds = dialog.rect()
    visible = [
        widget
        for widget in page.findChildren(QAbstractButton) + page.findChildren(QLabel)
        if widget.isVisibleTo(dialog)
    ]
    assert visible
    for widget in visible:
        mapped = _mapped(widget, dialog)
        assert bounds.contains(mapped), (widget.objectName(), getattr(widget, "text", lambda: "")(), mapped)
        if isinstance(widget, QAbstractButton):
            assert widget.accessibleName(), widget.text()
        if isinstance(widget, QLabel) and widget.wordWrap() and widget.text():
            assert widget.height() >= widget.heightForWidth(max(1, widget.width()))
    for area in dialog.findChildren(QAbstractScrollArea):
        bar = area.horizontalScrollBar()
        assert bar is None or bar.maximum() == 0 or not bar.isVisibleTo(dialog)


@pytest.mark.parametrize("profile_key", ["art", "music"])
@pytest.mark.parametrize("size", [FLOOR_800, COMPACT], ids=["800x600", "compact"])
def test_choice_page_fits_800x600_and_compact(qapp, tmp_path: Path, profile_key: str, size):
    dialog = _dialog(tmp_path, profile_key)
    try:
        dialog.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
        dialog.resize(*size)
        dialog.show()
        _settle(qapp)
        _assert_fits(dialog, size, dialog._choice_page)
        labels = [
            button.text()
            for button in dialog._choice_page.findChildren(QAbstractButton)
            if button.isVisibleTo(dialog)
        ]
        assert "Host" in labels and "Join" in labels
        if profile_key == "art":
            assert "Make together" in labels
            assert "Paint along" in labels
        assert_no_banned_first_screen_words(harvest_first_screen(dialog))
    finally:
        dialog.close()
        dialog.deleteLater()
        _settle(qapp)


@pytest.mark.parametrize("profile_key", ["art", "music"])
@pytest.mark.parametrize("size", [FLOOR_800, COMPACT], ids=["800x600", "compact"])
def test_join_page_fits_800x600_and_compact(qapp, tmp_path: Path, profile_key: str, size):
    dialog = _dialog(tmp_path, profile_key)
    try:
        dialog.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
        dialog.show_join()
        dialog.resize(*size)
        dialog.show()
        _settle(qapp)
        _assert_fits(dialog, size, dialog._join_page)
        assert dialog._join_button_primary.isVisibleTo(dialog)
        assert dialog._invite_input.isVisibleTo(dialog)
        assert dialog._invite_input.accessibleName() == "Invite"
        assert dialog._join_button_primary.accessibleName() == "Join"
        assert dialog.rect().contains(_mapped(dialog._join_button_primary, dialog))
        assert dialog.rect().contains(_mapped(dialog._invite_input, dialog))
        assert Qt.FocusPolicy.TabFocus & dialog._join_button_primary.focusPolicy()
        assert Qt.FocusPolicy.TabFocus & dialog._invite_input.focusPolicy()
    finally:
        dialog.close()
        dialog.deleteLater()
        _settle(qapp)

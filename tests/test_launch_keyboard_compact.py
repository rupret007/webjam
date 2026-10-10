"""First-screen keyboard reachability and compact 800x600 / larger-text layout."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog, ProfileCard, StartCard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous)


def _dialog(tmp_path: Path, profile_key: str = "art") -> LaunchDialog:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile_key,
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    return dialog


def _tab_cycle(qapp, dialog, steps: int = 12) -> list[str]:
    labels = []
    for _ in range(steps):
        focused = qapp.focusWidget()
        labels.append(focused.text() if focused is not None and hasattr(focused, "text") else "")
        QTest.keyClick(dialog, Qt.Key.Key_Tab)
        qapp.processEvents()
    return labels


def test_art_and_music_doors_are_both_tab_and_arrow_reachable(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(620, 520)
        dialog.show()
        qapp.processEvents()

        assert dialog._art_profile_card.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert dialog._music_profile_card.focusPolicy() == Qt.FocusPolicy.StrongFocus

        dialog._host_button.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        cycle = _tab_cycle(qapp, dialog, 10)
        assert "Art" in cycle
        assert "Host" in cycle
        assert "Join" in cycle
        assert "Make together" in cycle or "Paint along" in cycle
        # Art | Music is one choice. Tab lands on the selected door; arrows
        # move to the other, matching the start-card group.

        dialog._art_profile_card.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        QTest.keyClick(dialog._art_profile_card, Qt.Key.Key_Right)
        qapp.processEvents()
        assert dialog.selected_creator_profile_key == "music"
        assert dialog._music_profile_card.hasFocus()

        dialog._art_profile_card.click()
        qapp.processEvents()
        make = next(
            card
            for card in dialog._visible_start_cards()
            if card.start_key == "talk_and_make"
        )
        make.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(make, Qt.Key.Key_Down)
        qapp.processEvents()
        assert dialog.selected_start_key == "paint_along"
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("profile_key", ["art", "music"])
def test_live_door_fits_800x600_with_larger_text(qapp, tmp_path: Path, profile_key: str):
    dialog = _dialog(tmp_path, profile_key)
    try:
        dialog.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        assert dialog.width() == 800
        assert dialog.height() == 600
        bounds = dialog.rect()
        visible = [
            widget
            for widget in dialog.findChildren(QAbstractButton) + dialog.findChildren(QLabel)
            if widget.isVisibleTo(dialog)
        ]
        assert visible
        for widget in visible:
            mapped = QRect(widget.mapTo(dialog, QPoint()), widget.size())
            assert bounds.contains(mapped), (widget.objectName(), widget.text(), mapped)
            if isinstance(widget, QAbstractButton):
                assert widget.accessibleName()
            if isinstance(widget, QLabel) and widget.wordWrap() and widget.text():
                assert widget.height() >= widget.heightForWidth(widget.width())
        assert_no_banned_first_screen_words(harvest_first_screen(dialog))
        labels = [
            button.text()
            for button in visible
            if isinstance(button, QAbstractButton)
        ]
        assert "Host" in labels and "Join" in labels
        assert "New Music Project" not in labels
        if profile_key == "art":
            assert "Make together" in labels
            assert "Paint along" in labels
    finally:
        dialog.close()
        dialog.deleteLater()


def test_every_visible_door_control_has_an_accessible_name(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.show()
        qapp.processEvents()
        for button in dialog._choice_page.findChildren(QAbstractButton):
            if not button.isVisibleTo(dialog._choice_page):
                continue
            assert button.accessibleName(), button.text()
            assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
        for card in dialog.findChildren(ProfileCard) + dialog.findChildren(StartCard):
            if card.isVisibleTo(dialog):
                assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    finally:
        dialog.close()
        dialog.deleteLater()

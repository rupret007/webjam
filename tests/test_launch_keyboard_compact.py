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
    dialog._menu_bar.setNativeMenuBar(False)
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
        else:
            _assert_music_block_centered(dialog)
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


def _assert_music_block_centered(dialog):
    # Include the shared brand row, not just the choice page below it.
    top = min(widget.mapTo(dialog, QPoint()).y()
              for widget in (dialog._logo, dialog._wordmark))
    bottom = dialog._join_button.mapTo(dialog, QPoint()).y() + dialog._join_button.height()
    margins = dialog.layout().contentsMargins()
    available_top = dialog._menu_bar.height() + margins.top()
    available_bottom = dialog.height() - margins.bottom()
    assert abs((top - available_top) - (available_bottom - bottom)) <= 2


@pytest.mark.parametrize("size", [(620, 520), (800, 600), (1280, 800)])
@pytest.mark.parametrize("styled", [False, True])
def test_music_choice_block_is_vertically_centered(qapp, tmp_path, size, styled):
    dialog = _dialog(tmp_path, "music")
    try:
        if styled:
            dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()
        _assert_music_block_centered(dialog)
        assert not dialog._visible_start_cards()
        dialog.resize(760, 600)
        qapp.processEvents()
        _assert_music_block_centered(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_music_name_validation_keeps_the_block_centered_and_visible(qapp, tmp_path):
    dialog = _dialog(tmp_path, "music")
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        dialog._name_input.clear()
        dialog._host_button.click()
        qapp.processEvents()
        assert dialog._name_input.isVisibleTo(dialog)
        assert dialog._name_error.isVisibleTo(dialog)
        _assert_music_block_centered(dialog)
        for widget in (dialog._name_input, dialog._name_error, dialog._host_button,
                       dialog._join_button):
            assert dialog.rect().contains(QRect(widget.mapTo(dialog, QPoint()), widget.size()))
        assert not (tmp_path / "settings.json").exists()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_centering_tracks_profile_and_page_changes(qapp, tmp_path):
    dialog = _dialog(tmp_path, "art")
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        widgets = [dialog._logo, dialog._wordmark, dialog._art_profile_card,
                   dialog._music_profile_card, *dialog._visible_start_cards(),
                   dialog._host_button, dialog._join_button]
        art_geometry = [QRect(widget.mapTo(dialog, QPoint()), widget.size())
                        for widget in widgets]
        original_brand_top = dialog._logo.mapTo(dialog, QPoint()).y()
        dialog._music_profile_card.click()
        qapp.processEvents()
        _assert_music_block_centered(dialog)
        dialog.show_join()
        qapp.processEvents()
        assert dialog._logo.mapTo(dialog, QPoint()).y() == original_brand_top
        assert dialog._invite_input.isVisibleTo(dialog)
        dialog.show_choices()
        qapp.processEvents()
        _assert_music_block_centered(dialog)
        dialog._art_profile_card.click()
        qapp.processEvents()
        assert [QRect(widget.mapTo(dialog, QPoint()), widget.size())
                for widget in widgets] == art_geometry
    finally:
        dialog.close()
        dialog.deleteLater()

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
from webjam_qt.theme import Space, load_stylesheet
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
        _assert_choice_block_centered(dialog)
        if profile_key == "art":
            assert "Make together" in labels
            assert "Paint along" in labels
    finally:
        dialog.close()
        dialog.deleteLater()


def _rect_in_dialog(widget, dialog: LaunchDialog) -> QRect:
    return QRect(widget.mapTo(dialog, QPoint()), widget.size())


@pytest.mark.parametrize("start_key", ["talk_and_make", "paint_along"])
def test_art_door_helper_sits_with_host_join_at_800x600(qapp, tmp_path: Path, start_key: str):
    """Host/Join consequence stays beside the buttons, not above dead footer space."""

    dialog = _dialog(tmp_path, "art")
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        # Exercise both directions: the helper must follow the selected start.
        for key in ("paint_along", "talk_and_make", start_key):
            next(card for card in dialog._visible_start_cards()
                 if card.start_key == key).click()
            qapp.processEvents()
        paint_along = dialog.selected_start_key == "paint_along"
        helper_label = dialog._choice_helper
        assert helper_label.isVisibleTo(dialog) is paint_along
        assert helper_label.text() == (
            "Then choose a file or lesson link." if paint_along else ""
        )
        join = _rect_in_dialog(dialog._join_button, dialog)
        host = _rect_in_dialog(dialog._host_button, dialog)
        bounds = dialog.rect()
        if paint_along:
            helper = _rect_in_dialog(helper_label, dialog)
            assert bounds.contains(helper)
            assert helper.height() >= helper_label.heightForWidth(helper.width())
            # Paint along's one extra step stays above Host, not in dead footer space.
            assert 0 < host.top() - helper.bottom() <= 24
        else:
            assert helper_label.minimumHeight() == 0
        assert 0 < join.top() - host.bottom() <= 24
        for card in dialog._visible_start_cards():
            card_rect = _rect_in_dialog(card, dialog)
            assert card_rect.bottom() < join.top()
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


def _mapped(dialog, widget) -> QRect:
    return QRect(widget.mapTo(dialog, QPoint()), widget.size())


def _assert_choice_block_centered(dialog):
    # Include the shared brand row, not just the choice page below it.
    top = min(widget.mapTo(dialog, QPoint()).y()
              for widget in (dialog._logo, dialog._wordmark))
    bottom = dialog._join_button.mapTo(dialog, QPoint()).y() + dialog._join_button.height()
    margins = dialog.layout().contentsMargins()
    available_top = dialog._menu_bar.height() + margins.top()
    available_bottom = dialog.height() - margins.bottom()
    assert abs((top - available_top) - (available_bottom - bottom)) <= 2


def _gap_under_logo(dialog) -> int:
    brand_bottom = max(
        dialog._logo.mapTo(dialog, QPoint()).y() + dialog._logo.height(),
        dialog._wordmark.mapTo(dialog, QPoint()).y() + dialog._wordmark.height(),
    )
    return dialog._art_profile_card.mapTo(dialog, QPoint()).y() - brand_bottom


def test_art_door_clusters_like_music_at_800x600(qapp, tmp_path: Path):
    """Art brand + two cards + Host/Join cluster; no empty third under the logo."""

    dialog = _dialog(tmp_path, "art")
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        gap = _gap_under_logo(dialog)
        assert gap < dialog.height() // 3
        # Music keeps brand close to the cards (capped 28px page bookend + spacing).
        assert gap <= 28 + Space.MD * 2
        assert [card.text() for card in dialog._visible_start_cards()] == [
            "Make together",
            "Paint along",
        ]
        for widget in (
            dialog._logo,
            dialog._wordmark,
            dialog._art_profile_card,
            dialog._music_profile_card,
            *dialog._visible_start_cards(),
            dialog._host_button,
            dialog._join_button,
        ):
            assert dialog.rect().contains(_mapped(dialog, widget)), widget.objectName()
        assert dialog._host_button.isVisibleTo(dialog)
        assert dialog._join_button.isVisibleTo(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("profile_key", ["art", "music"])
@pytest.mark.parametrize("size", [(620, 520), (800, 600), (1280, 800)])
@pytest.mark.parametrize("styled", [False, True])
def test_choice_block_is_vertically_centered(qapp, tmp_path, profile_key, size, styled):
    dialog = _dialog(tmp_path, profile_key)
    try:
        if styled:
            dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        if profile_key == "music":
            assert not dialog._visible_start_cards()
        else:
            assert [card.text() for card in dialog._visible_start_cards()] == [
                "Make together",
                "Paint along",
            ]
            for widget in (dialog._host_button, dialog._join_button):
                assert dialog.rect().contains(_mapped(dialog, widget))
        dialog.resize(760, 600)
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        for widget in (dialog._host_button, dialog._join_button):
            assert dialog.rect().contains(_mapped(dialog, widget))
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
        _assert_choice_block_centered(dialog)
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
        _assert_choice_block_centered(dialog)
        art_geometry = [QRect(widget.mapTo(dialog, QPoint()), widget.size())
                        for widget in widgets]
        art_brand_top = dialog._logo.mapTo(dialog, QPoint()).y()
        dialog._music_profile_card.click()
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        dialog.show_join()
        qapp.processEvents()
        join_brand_top = dialog._logo.mapTo(dialog, QPoint()).y()
        # Join is not the live door, so the brand returns toward the top.
        assert join_brand_top < art_brand_top
        assert dialog.layout().centered_pages is None
        assert dialog._invite_input.isVisibleTo(dialog)
        dialog.show_choices()
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        dialog._art_profile_card.click()
        qapp.processEvents()
        _assert_choice_block_centered(dialog)
        assert [QRect(widget.mapTo(dialog, QPoint()), widget.size())
                for widget in widgets] == art_geometry
    finally:
        dialog.close()
        dialog.deleteLater()

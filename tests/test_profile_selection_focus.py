"""Rendered door selection stays distinct from keyboard focus (W13)."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Color
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


def _dialog(tmp_path, profile):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile,
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._menu_bar.setNativeMenuBar(False)
    return dialog


def _colors(card):
    """Sample flat fill and outline, away from text and rounded corners."""
    image = card.grab().toImage()
    scale = image.devicePixelRatio()
    fill = image.pixelColor(int((card.width() - 20) * scale), int(card.height() / 2 * scale))
    outline = image.pixelColor(int(card.width() / 2 * scale), int(scale))
    return fill, outline


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("focused", ["art", "music", "host"])
def test_selected_fill_and_only_focused_profile_outline(qapp, tmp_path, profile, focused):
    dialog = _dialog(tmp_path, profile)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        cards = {"art": dialog._art_profile_card, "music": dialog._music_profile_card}
        QTest.mouseMove(dialog._host_button)
        target = dialog._host_button if focused == "host" else cards[focused]
        target.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert target.hasFocus()
        assert dialog.selected_creator_profile_key == profile
        for key, card in cards.items():
            fill, outline = _colors(card)
            selected = key == profile
            assert card.isChecked() == selected
            assert fill == QColor(Color.ACCENT_PRIMARY if selected else Color.BG_INPUT)
            expected_outline = (
                Color.TEXT_PRIMARY if key == focused
                else Color.ACCENT_PRIMARY if selected
                else Color.BORDER_SUBTLE
            )
            assert outline == QColor(expected_outline)
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_keyboard_selection_preserves_focus_outline_and_card_geometry(qapp, tmp_path):
    dialog = _dialog(tmp_path, "art")
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.show()
        qapp.processEvents()
        art, music = dialog._art_profile_card, dialog._music_profile_card
        QTest.mouseMove(dialog._host_button)
        art.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        geometry = [card.geometry() for card in (art, music)]
        for source, destination, key, profile in (
            (art, music, Qt.Key.Key_Right, "music"),
            (music, art, Qt.Key.Key_Left, "art"),
        ):
            QTest.keyClick(source, key)
            qapp.processEvents()
            assert destination.hasFocus()
            assert dialog.selected_creator_profile_key == profile
            assert _colors(destination) == (QColor(Color.ACCENT_PRIMARY), QColor(Color.TEXT_PRIMARY))
            assert _colors(source) == (QColor(Color.BG_INPUT), QColor(Color.BORDER_SUBTLE))
            assert [card.geometry() for card in (art, music)] == geometry

        # Hover cannot erase the selected fill or impersonate keyboard focus.
        dialog._host_button.setFocus(Qt.FocusReason.TabFocusReason)
        for card in (art, music):
            QTest.mouseMove(card, QPoint(card.width() - 10, card.height() // 2))
            qapp.processEvents()
            fill, outline = _colors(card)
            assert fill == QColor(Color.ACCENT_PRIMARY if card.isChecked() else Color.BG_CARD)
            assert outline != QColor(Color.TEXT_PRIMARY)
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()

"""Round-2 polish: one Paint along primary, compact Join/room/Conversation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.art_room_overview import art_room_overview
from core.creative_modes import get_creator_profile_by_key
from core.session_conductor import ArtRoomState
from core.settings import AppSettings
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.webex_embed import WebexEmbed
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog, ProfileCard
from webjam_qt.windows.reference_video import ReferenceVideoDialog

_LARGE_TEXT = "\nQWidget { font-size: 20px; }"
_COMPACT = (760, 600)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous)


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def _mapped(widget, owner):
    return QRect(widget.mapTo(owner, QPoint()), widget.size())


def _assert_no_horizontal_scroll(owner):
    for area in owner.findChildren(QAbstractScrollArea):
        bar = area.horizontalScrollBar()
        assert bar is None or bar.maximum() == 0 or not bar.isVisibleTo(owner), (
            area.objectName(), bar.maximum() if bar is not None else None
        )


def _assert_unclipped_text(root, window):
    bounds = window.rect()
    for widget in root.findChildren(QLabel) + root.findChildren(QAbstractButton):
        if not widget.isVisibleTo(window):
            continue
        mapped = _mapped(widget, window)
        assert bounds.contains(mapped), (widget.objectName(), widget.text(), mapped)
        if isinstance(widget, QLabel) and widget.wordWrap() and widget.text():
            assert widget.height() >= widget.heightForWidth(max(1, widget.width())), (
                widget.objectName(), widget.text()
            )


def _dialog(tmp_path: Path, profile_key: str = "art") -> LaunchDialog:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key=profile_key,
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._menu_bar.setNativeMenuBar(False)
    return dialog


def test_profile_card_set_focus_policy_honors_its_argument(qapp):
    card = ProfileCard("music", "Music", "Write songs or play live together.")
    try:
        card.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        assert card.focusPolicy() == Qt.FocusPolicy.NoFocus
        card.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        assert card.focusPolicy() == Qt.FocusPolicy.ClickFocus
        card.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    finally:
        card.deleteLater()


def test_paint_along_host_empty_has_one_visual_primary(qapp):
    panel = ReferenceVideoDialog(hosting=True)
    panel.set_embedded(True)
    panel.resize(*_COMPACT)
    panel.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
    panel.show()
    _settle(qapp)
    try:
        assert panel._watch_lesson_button.text() == "Watch a shared lesson"
        assert panel._share_button.text() == "Choose process video…"
        assert panel._youtube_button.text() == "YouTube link…"
        for button in (
            panel._watch_lesson_button, panel._share_button, panel._youtube_button,
        ):
            assert button.isVisibleTo(panel) and button.isEnabled()
            assert button.accessibleName()
            assert button.accessibleDescription()
            assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
        primaries = [
            button for button in panel.findChildren(QPushButton)
            if button.isVisibleTo(panel) and button.isEnabled()
            and button.objectName() == "PrimaryButton"
        ]
        assert [button.text() for button in primaries] == ["Watch a shared lesson"]
        assert panel._share_button.objectName() == "GhostButton"
        assert panel._youtube_button.objectName() == "GhostButton"
        _assert_no_horizontal_scroll(panel)
        assert panel.rect().contains(_mapped(panel._watch_lesson_button, panel))
        for label in (panel._headline, panel._status, panel._lesson_hint):
            if label.isVisibleTo(panel) and label.text():
                assert panel.rect().contains(_mapped(label, panel))
                if label.wordWrap():
                    assert label.height() >= label.heightForWidth(max(1, label.width()))
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


def test_join_page_fits_compact_larger_text_with_named_tab_order(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
        dialog.show_join()
        dialog.resize(*_COMPACT)
        dialog.show()
        _settle(qapp)
        assert dialog.width() == 760 and dialog.height() == 600
        page = dialog._join_page
        bounds = dialog.rect()
        _assert_unclipped_text(page, dialog)
        _assert_no_horizontal_scroll(dialog)
        join = dialog._join_button_primary
        back = dialog._join_back_button
        assert join.isVisibleTo(dialog)
        assert bounds.contains(_mapped(join, dialog))
        assert join.accessibleName() == "Join"
        assert join.accessibleDescription()
        assert back.accessibleName() == "Back"
        assert back.accessibleDescription()
        assert dialog._invite_input.accessibleName() == "Invite"
        assert dialog._join_title.accessibleName() == "Join the room."
        assert dialog._join_subtitle.accessibleName() == "How to join"
        dialog._invite_input.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(dialog, Qt.Key.Key_Tab)
        _settle(qapp)
        assert join.hasFocus()
        QTest.keyClick(dialog, Qt.Key.Key_Tab)
        _settle(qapp)
        assert back.hasFocus()
    finally:
        dialog.close()
        dialog.deleteLater()
        _settle(qapp)


def test_make_together_room_fits_compact_larger_text(qapp):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam",
        initial_title="Making together",
    )
    window.menuBar().setNativeMenuBar(False)
    window.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.set_art_room_overview(
        art_room_overview(state=ArtRoomState.CONNECTED, hosting=True)
    )
    window.resize(*_COMPACT)
    window.show()
    _settle(qapp)
    try:
        assert window.width() == 760 and window.height() == 600
        panel = window.art_room_overview
        conversation = panel.conversation_button()
        assert conversation.isVisibleTo(window)
        assert conversation.text() == "Set Up Conversation"
        assert conversation.accessibleName() == "Set Up Conversation"
        assert conversation.accessibleDescription()
        assert conversation.focusPolicy() & Qt.FocusPolicy.TabFocus
        viewport = panel.viewport()
        mapped = _mapped(conversation, viewport)
        assert viewport.rect().intersects(mapped)
        _assert_no_horizontal_scroll(window)
        assert "Preview" in window.windowTitle()
        assert "Preview" in window.session_strip._subtitle.text()
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        window.close()
        window.deleteLater()
        _settle(qapp)


def test_conversation_panel_fits_compact_larger_text(qapp):
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = WebexEmbed()
    layout.addWidget(panel)
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    owner.setStyleSheet(load_stylesheet() + _LARGE_TEXT)
    owner.resize(*_COMPACT)
    owner.show()
    _settle(qapp)
    try:
        assert owner.width() == 760 and owner.height() == 600
        add_link = panel.change_link_button()
        assert add_link.isVisibleTo(owner)
        assert add_link.accessibleName()
        assert add_link.accessibleDescription()
        assert add_link.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert add_link.objectName() == "PrimaryButton"
        assert owner.rect().contains(_mapped(add_link, owner))
        for label in (panel._title_label, panel._mode_label, panel._status_label):
            assert label.isVisibleTo(owner)
            assert owner.rect().contains(_mapped(label, owner))
            if label.wordWrap() and label.text():
                assert label.height() >= label.heightForWidth(max(1, label.width()))
        _assert_no_horizontal_scroll(owner)
        assert panel._scroll.horizontalScrollBar().maximum() == 0
    finally:
        owner.close()
        owner.deleteLater()
        _settle(qapp)

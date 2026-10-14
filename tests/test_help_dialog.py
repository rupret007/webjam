"""Real searchable Help stays readable without blocking session controls."""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from tests.support.start_ux import assert_no_banned_first_screen_words
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow

_ART_HELP_DETAIL_TOKENS = (
    "YouTube",
    "Shared Canvas",
    "F11",
    "Choose process video",
    "YouTube link",
    "Choose YouTube",
    "Open in browser",
    "Add Link",
)


def _art_overview_topic(window, app):
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.show()
    window.show_help()
    app.processEvents()
    dialog = window._workflow_help_dialog
    assert dialog._topics.item(0).text() == "Current workspace"
    dialog._topics.setCurrentRow(0)
    app.processEvents()
    return dialog._topics.item(0).data(Qt.ItemDataRole.UserRole), dialog._body.toPlainText()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    window = ConductorWindow(mode_entries=[("music_jam", "Music Jam")],
                             initial_mode_key="music_jam", initial_title="Help regression")
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("route", ["f1", "more"])
@pytest.mark.parametrize("large_text", [False, True])
@pytest.mark.parametrize("size", [(760, 680), (480, 500)])
def test_real_help_fits_scrolls_and_returns_without_changing_work(app, window, profile, route, large_text, size):
    previous_style = app.styleSheet()
    # ID selectors override bare QWidget font rules. Enlarge the browser too.
    app.setStyleSheet(load_stylesheet() + (
        "\n#HelpDialog QWidget, QTextBrowser#HelpBody { font-size: 22px; }" if large_text else ""))
    available = QRect(80, 40, *size)
    window.set_creator_profile(get_creator_profile_by_key(profile))
    window.session_canvas.restore_notes("Keep these local notes.")
    window.show()
    window.activateWindow()
    origin = window.session_strip._tools_button
    origin.setFocus()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 2000)
    workspace = window.workspace_stack.currentWidget()
    owner = SimpleNamespace(window=window)
    window.session_strip.tool_requested.connect(
        lambda key: ApplicationController._on_rail_view_changed(owner, key))
    try:
        with patch.object(QGuiApplication, "screenAt", return_value=SimpleNamespace(availableGeometry=lambda: available)):
            if route == "f1":
                QTest.keyClick(window, Qt.Key.Key_F1)
            else:
                next(a for a in origin.menu().actions() if a.text() == "Help").trigger()
        app.processEvents()
        dialog = window._workflow_help_dialog
        assert dialog.isVisible() and dialog.parentWidget() is window
        assert QApplication.activeModalWidget() is None
        assert not dialog.isModal()
        assert available.contains(dialog.frameGeometry())
        for control in (dialog._search, dialog._topics, dialog._body, dialog._ok):
            assert control.isVisible()
            assert available.contains(QRect(control.mapToGlobal(QPoint()), control.size()))
        assert not dialog._body.openLinks() and not dialog._body.openExternalLinks()
        assert dialog._body.isReadOnly()
        assert dialog._body.horizontalScrollBar().maximum() == 0
        text = dialog._body.toPlainText()
        # Music also explains the Art launch choice in its first step.
        assert "Paint along" in text
        assert ("— Art" in text) == (profile == "art")
        # The overview keeps the three door beats; roster details remain
        # readable in their dedicated Art Help topic.
        assert "full artist list" not in text
        people_rows = [
            row for row in range(dialog._topics.count())
            if dialog._topics.item(row).data(Qt.ItemDataRole.UserRole).key == "art_room_people"
        ]
        assert len(people_rows) == (1 if profile == "art" else 0)
        if profile == "art":
            dialog._topics.setCurrentRow(people_rows[0])
            assert "full artist list" in dialog._body.toPlainText()
            dialog._topics.setCurrentRow(0)
            app.processEvents()
        bar = dialog._body.verticalScrollBar()
        # Music's long card still overflows at 22px. Art's three door beats
        # can fit on 760×680; compact Art still overflows and must stay
        # keyboard-scrollable.
        if large_text and (profile != "art" or size == (480, 500)):
            assert bar.maximum() > 0
        dialog._body.setFocus()
        for _ in range(2 + bar.maximum() // max(1, bar.pageStep())):
            QTest.keyClick(dialog._body, Qt.Key.Key_PageDown)
        assert bar.value() == bar.maximum()
        QTest.keyClick(dialog._body, Qt.Key.Key_Tab)
        assert dialog._ok.hasFocus()
        QTest.keyClick(dialog._ok, Qt.Key.Key_Return if route == "f1" else Qt.Key.Key_Escape)
        assert not dialog.isVisible()
        assert window.workspace_stack.currentWidget() is workspace
        assert window.session_canvas._notes.toPlainText() == "Keep these local notes."
        assert window._creator_profile.key == profile
        app.processEvents()
        assert origin.hasFocus()
    finally:
        app.setStyleSheet(previous_style)


def test_help_falls_back_to_primary_display_for_an_offscreen_parent(app, window):
    available = QRect(100, 100, 760, 600)
    window.move(-12000, -12000)
    with patch.object(QGuiApplication, "screenAt", return_value=None), patch.object(
        QGuiApplication, "primaryScreen", return_value=SimpleNamespace(availableGeometry=lambda: available)
    ):
        window.show_help()
    dialog = window._workflow_help_dialog
    app.processEvents()
    assert dialog._available_geometry == available
    assert available.contains(dialog.frameGeometry())


def test_search_keyboard_and_selection_never_navigate_until_explicit_action(app, window):
    window.show()
    navigation = Mock()
    window.workflow_help_requested.connect(navigation)
    window.show_help()
    dialog = window._workflow_help_dialog
    app.processEvents()
    assert QTest.qWaitForWindowActive(dialog, 2000)
    QTest.keyClicks(dialog._search, "draft")
    assert dialog._topics.count() >= 1
    assert "retry" in dialog._body.toPlainText()
    QTest.keyClick(dialog._search, Qt.Key.Key_Return)
    assert dialog.isVisible()
    QTest.keyClick(dialog._search, Qt.Key.Key_Tab)
    assert dialog._topics.hasFocus()
    QTest.keyClick(dialog._topics, Qt.Key.Key_Tab)
    assert dialog._body.hasFocus()
    QTest.keyClick(dialog._body, Qt.Key.Key_Tab)
    assert dialog._navigate.hasFocus()
    navigation.assert_not_called()
    QTest.keyClick(dialog._navigate, Qt.Key.Key_Return)
    navigation.assert_called_once_with("library")
    assert not dialog.isVisible()


def test_no_results_disables_navigation_and_find_shortcut_returns_to_search(app, window):
    window.show()
    navigation = Mock()
    window.workflow_help_requested.connect(navigation)
    window.show_help()
    dialog = window._workflow_help_dialog
    dialog._search.setText("NoSuchPrivateWorkspaceName")
    assert dialog._topics.count() == 0
    assert "No matching" in dialog._body.toPlainText()
    assert not dialog._navigate.isEnabled()
    dialog._body.setFocus()
    app.processEvents()
    QTest.keySequence(dialog, QKeySequence.StandardKey.Find)
    assert dialog._search.hasFocus()
    dialog._search.clear()
    assert dialog._topics.count() > 1
    navigation.assert_not_called()


def test_help_reuses_one_window_refreshes_profile_and_leaves_stop_reachable(app, window):
    window.show()
    window.show_help()
    dialog = window._workflow_help_dialog
    window.set_creator_profile(get_creator_profile_by_key("art"))
    assert window._workflow_help_dialog is dialog
    assert "Paint along" in dialog._body.toPlainText()
    stop = Mock()
    window.session_strip.launch_audio_requested.connect(stop)
    button = window.session_strip._audio_button
    button.setEnabled(True)
    button.setText("End Room")
    app.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    stop.assert_called_once()
    assert dialog.isVisible()
    assert QApplication.activeModalWidget() is None


@pytest.mark.parametrize("focus", ["search", "body", "workspace"])
def test_visible_help_tracks_profile_without_losing_search_or_stealing_focus(app, window, focus):
    window.show()
    window.session_canvas.restore_notes("Keep these notes while Help follows the workspace.")
    navigation, audio = Mock(), Mock()
    window.workflow_help_requested.connect(navigation)
    window.session_strip.launch_audio_requested.connect(audio)
    window.show_help()
    dialog = window._workflow_help_dialog
    dialog._search.setText("favorite")
    app.processEvents()
    assert dialog._navigate.isVisible() and dialog._route == "studio"
    target = {"search": dialog._search, "body": dialog._body,
              "workspace": window.session_strip._title_input}[focus]
    active = window if focus == "workspace" else dialog
    active.activateWindow()
    assert QTest.qWaitForWindowActive(active, 2000)
    target.setFocus()
    app.processEvents()
    assert target.hasFocus()
    workspace = window.workspace_stack.currentWidget()
    for profile in ("art", "music"):
        window.set_creator_profile(get_creator_profile_by_key(profile))
        app.processEvents()
        assert window._workflow_help_dialog is dialog and dialog.isVisible()
        assert dialog._search.text() == "favorite"
        assert target.hasFocus()
        assert window.workspace_stack.currentWidget() is workspace
        if profile == "art":
            assert dialog._topics.count() == 0
            assert "No matching" in dialog._body.toPlainText()
            assert not dialog._navigate.isVisible() and not dialog._navigate.isEnabled()
        else:
            assert dialog._topics.count() >= 1
            assert dialog._navigate.isVisible() and dialog._route == "studio"
        navigation.assert_not_called()
        audio.assert_not_called()
    assert window.session_canvas.current_notes() == "Keep these notes while Help follows the workspace."


def test_profile_change_does_not_create_or_reopen_hidden_help(app, window):
    window.show()
    window.set_creator_profile(get_creator_profile_by_key("art"))
    assert getattr(window, "_workflow_help_dialog", None) is None
    window.show_help()
    dialog = window._workflow_help_dialog
    dialog.hide()
    window.set_creator_profile(get_creator_profile_by_key("music"))
    app.processEvents()
    assert window._workflow_help_dialog is dialog and not dialog.isVisible()


def test_repeated_profile_presentation_preserves_help_reading_position(app, window):
    window.show()
    window.show_help()
    dialog = window._workflow_help_dialog
    # A short reading viewport makes a lost scroll position observable.
    dialog._body.setMaximumHeight(96)
    app.processEvents()
    cursor = dialog._body.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(18, cursor.MoveMode.KeepAnchor)
    dialog._body.setTextCursor(cursor)
    scroll = dialog._body.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    assert scroll.value() > 0
    before = (dialog._topics.currentItem().text(), scroll.value(), cursor.selectedText())
    window.set_creator_profile(get_creator_profile_by_key("music"))
    app.processEvents()
    assert (dialog._topics.currentItem().text(), scroll.value(),
            dialog._body.textCursor().selectedText()) == before


def test_hidden_or_retired_help_cannot_dispatch_a_late_request(app, window):
    window.show()
    window.show_help()
    dialog = window._workflow_help_dialog
    navigation = Mock()
    window.workflow_help_requested.connect(navigation)
    window.hide()
    dialog.navigation_requested.emit("library")
    window.show()
    dialog.reject()
    dialog.navigation_requested.emit("studio")
    navigation.assert_not_called()


@pytest.mark.parametrize("profile,query", [("music", "draft"), ("art", "relink")])
def test_compact_large_text_keeps_navigation_and_readable_body_visible(app, window, profile, query):
    previous_style = app.styleSheet()
    app.setStyleSheet(load_stylesheet() +
        "\n#HelpDialog QWidget, QTextBrowser#HelpBody { font-size: 22px; }")
    available = QRect(80, 40, 480, 500)
    window.set_creator_profile(get_creator_profile_by_key(profile))
    window.show()
    try:
        with patch.object(QGuiApplication, "screenAt", return_value=SimpleNamespace(availableGeometry=lambda: available)):
            window.show_help()
        dialog = window._workflow_help_dialog
        dialog._search.setText(query)
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        for widget in (dialog._search, dialog._topics, dialog._body, dialog._navigate, dialog._ok):
            assert widget.isVisible()
            assert available.contains(QRect(widget.mapToGlobal(QPoint()), widget.size()))
        assert dialog._body.viewport().height() >= 90
        assert dialog._body.horizontalScrollBar().maximum() == 0
        assert dialog._navigate.fontMetrics().horizontalAdvance(dialog._navigate.text()) < dialog._navigate.width() - 20
    finally:
        app.setStyleSheet(previous_style)


def test_art_help_opens_on_three_door_beats(app, window):
    topic, text = _art_overview_topic(window, app)
    steps = re.findall(r"<b>(\d+)\.</b>", topic.body)
    assert steps == ["1", "2", "3"], steps
    beats = re.split(r"<b>\d+\.</b>", topic.body)[1:]
    assert len(beats) == 3
    for beat in beats:
        words = re.findall(r"\w+", re.sub(r"<[^>]*>", " ", beat))
        assert len(words) <= 28, beat
    assert "Make together" in text
    assert "Paint along" in text
    assert "Host" in text
    assert "Join" in text
    assert "invite" in text.casefold()
    assert "meeting" in text.casefold()
    assert "silent" in text.casefold()
    assert "Continue your work" not in text
    beat_two = re.sub(r"<[^>]*>", " ", beats[1])
    assert "invite" in beat_two.casefold()
    assert "keep making" in beat_two.casefold()
    assert "open the lesson" in beat_two.casefold()
    assert "one next action" not in beat_two.casefold()


def test_art_help_first_page_keeps_youtube_canvas_and_keyboard_in_topics(app, window):
    topic, text = _art_overview_topic(window, app)
    for token in _ART_HELP_DETAIL_TOKENS:
        assert token not in topic.body, token
        assert token not in text, token
    assert_no_banned_first_screen_words(text)


@pytest.mark.parametrize("size", [(760, 680), (480, 500)])
def test_art_follow_along_topic_overflows_and_scrolls_with_large_text(app, window, size):
    previous_style = app.styleSheet()
    app.setStyleSheet(load_stylesheet() +
        "\n#HelpDialog QWidget, QTextBrowser#HelpBody { font-size: 22px; }")
    available = QRect(80, 40, *size)
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.session_canvas.restore_notes("Keep these local notes.")
    window.show()
    try:
        with patch.object(
            QGuiApplication, "screenAt",
            return_value=SimpleNamespace(availableGeometry=lambda: available),
        ):
            window.show_help()
        app.processEvents()
        dialog = window._workflow_help_dialog
        row = next(
            i for i in range(dialog._topics.count())
            if dialog._topics.item(i).text() == "Paint along and play along with sound"
        )
        dialog._topics.setCurrentRow(row)
        app.processEvents()
        bar = dialog._body.verticalScrollBar()
        assert bar.maximum() > 0
        dialog._body.setFocus()
        for _ in range(2 + bar.maximum() // max(1, bar.pageStep())):
            QTest.keyClick(dialog._body, Qt.Key.Key_PageDown)
        assert bar.value() == bar.maximum()
        assert window.session_canvas._notes.toPlainText() == "Keep these local notes."
    finally:
        app.setStyleSheet(previous_style)

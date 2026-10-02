"""Real searchable Help stays readable without blocking session controls."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


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
        assert ("Paint along" in text) == (profile == "art")
        bar = dialog._body.verticalScrollBar()
        if large_text:
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
    window.show_help()
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

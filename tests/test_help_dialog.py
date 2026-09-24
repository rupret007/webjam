"""Real modal Help stays readable and dismissible on compact displays."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.help_dialog import HelpDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("route", ["f1", "more"])
@pytest.mark.parametrize("large_text", [False, True])
def test_real_help_fits_scrolls_and_returns_without_changing_work(app, profile, route, large_text):
    previous_style = app.styleSheet()
    app.setStyleSheet(load_stylesheet() + ("\nQWidget { font-size: 22px; }" if large_text else ""))
    available = QRect(80, 40, 760, 600)
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music Jam")], initial_mode_key="music_jam",
        initial_title="Help regression",
    )
    window.set_creator_profile(get_creator_profile_by_key(profile))
    window.session_canvas.restore_notes("Keep these local notes.")
    window.show()
    window.activateWindow()
    origin = window.session_strip._tools_button
    origin.setFocus()
    app.processEvents()
    assert QTest.qWaitForWindowActive(window, 2000)
    workspace = window.workspace_stack.currentWidget()
    tool_requests = []
    window.session_strip.tool_requested.connect(tool_requests.append)
    owner = SimpleNamespace(window=window)
    window.session_strip.tool_requested.connect(
        lambda key: ApplicationController._on_rail_view_changed(owner, key)
    )
    observed = {}

    def inspect_and_close():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, HelpDialog)
            assert dialog.parentWidget() is None
            assert available.contains(dialog.frameGeometry())
            button = QRect(dialog._ok.mapToGlobal(QPoint()), dialog._ok.size())
            assert available.contains(button)
            assert dialog._ok.isVisible() and dialog._ok.isEnabled()
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
            for _ in range(2 + bar.maximum() // max(1, bar.pageStep())):
                QTest.keyClick(dialog._body, Qt.Key.Key_PageUp)
            assert bar.value() == 0
            # Backtab from the read-only body reaches the fixed footer.
            QTest.keyClick(dialog._body, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
            assert dialog._ok.hasFocus()
            if route == "f1":
                QTest.keyClick(dialog._ok, Qt.Key.Key_Return)
            else:
                QTest.keyClick(dialog._ok, Qt.Key.Key_Escape)
            assert not dialog.isVisible()
            observed["passed"] = True
        except BaseException as error:
            observed["error"] = error
        finally:
            if isinstance(dialog, HelpDialog):
                dialog.reject()

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(lambda: QApplication.activeModalWidget().reject()
                          if isinstance(QApplication.activeModalWidget(), HelpDialog) else None)
    QTimer.singleShot(100, inspect_and_close)
    guard.start(3000)
    try:
        with patch.object(QGuiApplication, "screenAt", return_value=SimpleNamespace(availableGeometry=lambda: available)):
            if route == "f1":
                QTest.keyClick(window, Qt.Key.Key_F1)
            else:
                next(a for a in origin.menu().actions() if a.text() == "Help").trigger()
        app.processEvents()
        if "error" in observed:
            raise observed["error"]
        assert observed.get("passed"), "Help did not open or modal probe timed out"
        assert window.workspace_stack.currentWidget() is workspace
        assert window.session_canvas._notes.toPlainText() == "Keep these local notes."
        assert window._creator_profile.key == profile
        assert tool_requests == (["help"] if route == "more" else [])
        assert QTest.qWaitForWindowActive(window, 2000)
        assert origin.hasFocus()
    finally:
        guard.stop()
        window.close()
        window.deleteLater()
        app.processEvents()
        app.setStyleSheet(previous_style)


def test_help_text_browser_is_selectable_only(app):
    """The Help dialog's text browser allows selection but not editing."""
    dialog = HelpDialog("<p>Test content</p>")
    try:
        dialog.show()
        app.processEvents()

        flags = dialog._body.textInteractionFlags()
        assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
        assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard
        assert not (flags & Qt.TextInteractionFlag.TextEditable)
        assert not (flags & Qt.TextInteractionFlag.LinksAccessibleByMouse)
        assert not (flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard)

        dialog.close()
    finally:
        dialog.deleteLater()


def test_help_falls_back_to_primary_display_for_an_offscreen_parent(app):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music Jam")], initial_mode_key="music_jam",
        initial_title="Disconnected display",
    )
    available = QRect(100, 100, 760, 600)
    dialogs = []
    try:
        window.move(-12000, -12000)
        with patch.object(QGuiApplication, "screenAt", return_value=None), patch.object(
            QGuiApplication, "primaryScreen", return_value=SimpleNamespace(availableGeometry=lambda: available)
        ), patch.object(HelpDialog, "exec", lambda dialog: dialogs.append(dialog) or 0):
            window.show_help()
        dialog = dialogs[0]
        assert dialog._available_geometry == available
        dialog.show()
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        dialog.close()
    finally:
        window.close()

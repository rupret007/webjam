"""Art Notes offers room navigation without changing the artist's work."""

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.creative_modes import get_creator_profile_by_key
from core.musician_guidance import build_musician_guidance
from core.session_conductor import (
    ArtRoomState, SessionConductor, SessionConductorFacts, SessionRole,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.session_canvas import SessionCanvas
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_font = app.font()
    previous_tab = app.styleHints().tabFocusBehavior()
    # This Qt process exercises full keyboard navigation; do not change the
    # macOS preference that normally lets Tab skip non-text controls.
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    font_ids = []
    for path in sorted((Path(__file__).resolve().parents[1] / "webjam_qt/theme/fonts").glob("Inter-*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            font_ids.append(font_id)
    font = QFont("Inter") if "Inter" in QFontDatabase.families() else QFont(previous_font)
    font.setPixelSize(13)
    app.setFont(font)
    try:
        yield app
    finally:
        app.setFont(previous_font)
        app.styleHints().setTabFocusBehavior(previous_tab)
        for font_id in font_ids:
            QFontDatabase.removeApplicationFont(font_id)


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def _delete(widget, qapp):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
    assert not isValid(widget)
    _settle(qapp)


@pytest.fixture
def canvas(qapp):
    panel = SessionCanvas()
    panel.setStyleSheet(load_stylesheet())
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.resize(400, 650)
    panel.show()
    panel.activateWindow()
    _settle(qapp)
    try:
        yield panel
    finally:
        _delete(panel, qapp)


def test_back_to_room_is_a_quiet_keyboard_action_separate_from_notes_tools(canvas, qapp):
    button = canvas.room_return_button()
    returns, notes, suggestions = [], [], []
    canvas.return_to_room_requested.connect(lambda: returns.append(True))
    canvas.notes_changed.connect(notes.append)
    canvas.suggestion_requested.connect(lambda: suggestions.append(True))
    assert button.text() == "Back to room"
    assert button.objectName() == "QuietButton"
    assert button.isVisible() and button.isEnabled()
    assert "Keep your local notes" in button.accessibleDescription()
    assert button not in canvas._toolbar_buttons
    button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Tab)
    _settle(qapp)
    assert canvas._toolbar_buttons[0].hasFocus()
    QTest.keyClick(canvas._toolbar_buttons[0], Qt.Key.Key_Backtab)
    _settle(qapp)
    assert button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert returns == [True]
    assert notes == [] and suggestions == []


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_profile_switch_removes_return_and_rejects_a_stale_art_click(canvas, profile):
    returns = []
    canvas.return_to_room_requested.connect(lambda: returns.append(True))
    button = canvas.room_return_button()
    canvas.set_creator_profile(get_creator_profile_by_key(profile))
    assert not button.isVisible() and not button.isEnabled()
    button.clicked.emit()
    assert returns == []
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    assert canvas.room_return_button() is button
    button.click()
    assert returns == [True]


@pytest.mark.parametrize("save_state", ["saved", "pending", "failed", "too_large"])
def test_return_keeps_draft_selection_undo_and_unsent_chat(canvas, qapp, save_state):
    editor = canvas._notes
    initial = "Try a softer edge."
    canvas.set_notes(initial)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    editor.setFocus()
    QTest.keyClicks(editor, " Keep these layers.")
    cursor = editor.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    canvas.set_notes_save_state(save_state)
    canvas._chat_input.setText("Unsent idea")
    canvas._chat_input.setCursorPosition(4)
    _settle(qapp)
    document = editor.document()
    draft = canvas.current_notes()
    undo_steps = document.availableUndoSteps()
    assert document.isUndoAvailable() and undo_steps > 0
    changes, returns = [], []
    canvas.notes_changed.connect(changes.append)
    canvas.return_to_room_requested.connect(lambda: returns.append(True))
    QTest.mouseClick(canvas.room_return_button(), Qt.MouseButton.LeftButton)
    canvas.hide()
    canvas.show()
    _settle(qapp)
    assert returns == [True] and changes == []
    assert canvas.current_notes() == draft
    assert editor.document() is document
    assert (editor.textCursor().anchor(), editor.textCursor().position()) == (4, 10)
    assert document.availableUndoSteps() == undo_steps
    assert canvas._notes_save_state == save_state
    assert canvas._chat_input.text() == "Unsent idea"
    assert canvas._chat_input.cursorPosition() == 4
    editor.undo()
    assert canvas.current_notes() == initial


def _assert_header_and_toolbar_fit(panel):
    button = panel.room_return_button()
    header = panel._header
    assert button.isVisible() and button.isEnabled()
    assert button.width() >= button.minimumSizeHint().width()
    assert button.height() >= button.minimumSizeHint().height()
    assert header.height() >= header.heightForWidth(header.width())
    rects = []
    for widget in (header, button, *panel._toolbar_buttons, panel._save_notes_button):
        if not widget.isVisibleTo(panel):
            continue
        rect = QRect(widget.mapTo(panel, QPoint()), widget.size())
        assert panel.rect().contains(rect), (getattr(widget, "text", lambda: "")(), rect)
        assert not any(previous.intersects(rect) for previous in rects), (
            widget.text(), rect, rects
        )
        rects.append(rect)
    assert all(rect.bottom() < panel._notes.geometry().top() for rect in rects)


@pytest.mark.parametrize("width", [280, 400])
@pytest.mark.parametrize("font_stretch", [100, 125])
def test_return_header_fits_the_supported_notes_rail_without_crowding_tools(
    canvas, qapp, width, font_stretch
):
    for widget in (canvas._header, canvas.room_return_button()):
        font = widget.font()
        font.setStretch(font_stretch)
        widget.setFont(font)
    canvas.resize(width, 650)
    _settle(qapp)
    assert canvas.size() == QSize(width, 650)
    _assert_header_and_toolbar_fit(canvas)


@pytest.mark.parametrize("size", [(720, 560), (1040, 720)])
@pytest.mark.parametrize("save_state", ["saved", "failed"])
def test_room_return_stays_reachable_in_the_actual_notes_workspace(qapp, size, save_state):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")], initial_mode_key="music_jam",
        initial_title="Making together",
    )
    window.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.set_room_stage_visible(size[0] >= 900)
    window.session_canvas.setVisible(True)
    # Use the same accepted Art connection presentation as the controller.
    facts = SessionConductorFacts(
        creator_profile_key="art", role=SessionRole.GUEST,
        setup_requested=True, art_room=ArtRoomState.CONNECTED,
    )
    window.session_canvas.set_musician_guidance(
        build_musician_guidance(SessionConductor(facts).snapshot)
    )
    window.session_canvas.set_notes_save_state(save_state)
    window.session_strip.set_audio_state("Leave Room")
    window.session_hud.set_state("Your Art room", "Keep making with your own tools.", action_visible=False)
    window.resize(*size)
    window.show()
    window.activateWindow()
    _settle(qapp)
    # Match the Notes route's supported compact/full workspace allocation.
    total = sum(window.center_splitter.sizes())
    window.center_splitter.setSizes(
        [0, total] if size[0] < 900 else [int(total * .28), int(total * .72)]
    )
    _settle(qapp)
    try:
        assert window.size() == QSize(*size)
        panel = window.session_canvas
        _assert_header_and_toolbar_fit(panel)
        button = panel.room_return_button()
        button.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        assert button.hasFocus()
        rect = QRect(button.mapTo(window, QPoint()), button.size())
        assert window.rect().contains(rect)
        assert window.session_strip._audio_button.isVisibleTo(window)
        assert panel._notes.isVisibleTo(window)
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        _delete(window, qapp)


def test_compact_controls_restore_normal_and_other_profile_geometry(qapp):
    # Notes is a splitter child in the app. A standalone top-level QFrame
    # enforces its full minimumSizeHint and cannot model compact allocation.
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")], initial_mode_key="music_jam",
        initial_title="Making together",
    )
    window.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.set_room_stage_visible(False)
    canvas = window.session_canvas
    canvas.show()
    window.resize(1040, 720)
    window.show()
    _settle(qapp)
    window.center_splitter.setSizes([0, window.width()])
    _settle(qapp)
    try:
        button = canvas._toolbar_buttons[0]
        normal_height = button.height()
        assert canvas.layout().spacing() == Space.SM
        window.resize(720, 560)
        _settle(qapp)
        assert window.size() == QSize(720, 560)
        assert canvas.height() < 500
        assert canvas.layout().spacing() == Space.XS
        assert button.height() == 34 < normal_height
        _assert_header_and_toolbar_fit(canvas)
        canvas.set_creator_profile(get_creator_profile_by_key("music"))
        _settle(qapp)
        assert canvas.layout().spacing() == Space.SM
        assert button.height() == normal_height
        assert all(not item.styleSheet() for item in canvas._toolbar_buttons)
        assert not canvas.room_return_button().isVisible()
        canvas.set_creator_profile(get_creator_profile_by_key("art"))
        _settle(qapp)
        assert button.height() == 34
        window.resize(1040, 720)
        _settle(qapp)
        assert canvas.height() >= 500
        assert canvas.layout().spacing() == Space.SM
        assert button.height() == normal_height
        assert all(not item.styleSheet() for item in canvas._toolbar_buttons)
        _assert_header_and_toolbar_fit(canvas)
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        _delete(window, qapp)

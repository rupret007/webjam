"""Art Notes reveals meeting controls and cannot send or receive Jamulus chat."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from shiboken6 import isValid

from core.creative_modes import get_creator_profile_by_key
from core.musician_guidance import build_musician_guidance
from core.session_conductor import (
    ArtRoomState, SessionConductor, SessionConductorFacts, SessionRole,
)
from tests.test_art_room_return_ui import qapp as _qapp_fixture
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.session_canvas import SessionCanvas
from webjam_qt.windows.conductor_window import ConductorWindow

qapp = _qapp_fixture


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


def _destroy(widget, qapp):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
    assert not isValid(widget)
    _settle(qapp)


@pytest.fixture
def canvas(qapp):
    panel = SessionCanvas()
    panel.setStyleSheet(load_stylesheet())
    panel.set_creator_profile(get_creator_profile_by_key("music"))
    panel.resize(400, 650)
    panel.show()
    panel.activateWindow()
    _settle(qapp)
    try:
        yield panel
    finally:
        _destroy(panel, qapp)


class ChatController:
    """Use the production profile and dispatch methods with a held UI queue."""

    _apply_creator_profile_key = ApplicationController._apply_creator_profile_key
    _on_chat_submitted = ApplicationController._on_chat_submitted
    _on_jamulus_chat = ApplicationController._on_jamulus_chat
    _on_art_notes_talk_share = ApplicationController._on_art_notes_talk_share
    creator_profile = ApplicationController.creator_profile

    def __init__(self, canvas):
        self._active_creator_profile_key = "music"
        self._creator_profile_host_owned = False
        self._chat_profile_generation = 0
        self._shutdown = False
        self.settings = SimpleNamespace(last_creator_profile_key="music")
        self.window = SimpleNamespace(
            session_canvas=canvas, flash_message=Mock(),
            set_creator_profile=lambda profile, **kw: canvas.set_creator_profile(profile),
        )
        self.jamulus = SimpleNamespace(send_chat=Mock(return_value=True))
        self.queued = []
        self._ui_invoker = SimpleNamespace(invoke=self.queued.append)
        self._rpc_ui_source_is_current = Mock(return_value=True)
        self._on_art_overview_conversation = Mock()
        canvas.chat_submitted.connect(self._on_chat_submitted)
        canvas.talk_share_requested.connect(self._on_art_notes_talk_share)

    def drain(self):
        pending, self.queued = self.queued, []
        for callback in pending:
            callback()


@pytest.fixture
def controller(canvas):
    return ChatController(canvas)


def _draft(input):
    return (input.text(), input.cursorPosition(), input.selectionStart(), input.selectedText())


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_art_hides_chat_and_preserves_non_art_draft_selection_roundtrip(
    canvas, controller, profile,
):
    controller._apply_creator_profile_key(profile)
    composer = canvas._chat_input
    composer.setText("Keep this unsent message")
    composer.setSelection(3, 8)
    before = _draft(composer)
    controller._apply_creator_profile_key("art", host_owned=True)
    assert composer.isHidden() and not composer.isEnabled()
    assert canvas.talk_share_button().isVisible() and canvas.talk_share_button().isEnabled()
    # Hidden keyboard callbacks and direct semantic delivery are both inert.
    composer.returnPressed.emit()
    canvas.chat_submitted.emit("A stale send")
    assert _draft(composer) == before
    controller.jamulus.send_chat.assert_not_called()
    assert canvas.current_notes() == ""
    controller._apply_creator_profile_key(profile)
    assert composer.isVisible() and composer.isEnabled()
    assert not canvas.talk_share_button().isVisible()
    assert not canvas.talk_share_button().isEnabled()
    assert _draft(composer) == before


def test_talk_share_keyboard_action_does_not_send_notes(canvas, controller, qapp):
    controller._apply_creator_profile_key("art")
    canvas.set_notes("PRIVATE_LOCAL_NOTES")
    button = canvas.talk_share_button()
    assert button.objectName() == "QuietButton"
    assert button.accessibleName() == "Talk & share"
    assert "not sent" in button.accessibleDescription()
    button.setFocus(Qt.FocusReason.TabFocusReason)
    _settle(qapp)
    assert button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    controller._on_art_overview_conversation.assert_called_once_with()
    controller.jamulus.send_chat.assert_not_called()
    assert canvas.current_notes() == "PRIVATE_LOCAL_NOTES"


@pytest.mark.parametrize("blocked", ["unavailable", "music", "shutdown"])
def test_stale_talk_share_never_routes_when_its_owner_is_unavailable(
    canvas, controller, blocked,
):
    controller._apply_creator_profile_key("art")
    if blocked == "unavailable":
        canvas.set_talk_share_available(False)
        assert not canvas.talk_share_button().isEnabled()
    elif blocked == "music":
        controller._apply_creator_profile_key("music")
    else:
        controller._shutdown = True
    canvas.talk_share_button().clicked.emit()
    if blocked != "unavailable":
        canvas.talk_share_requested.emit()
    controller._on_art_overview_conversation.assert_not_called()


def test_talk_share_availability_survives_profile_switch_and_recovers(canvas):
    canvas.set_talk_share_available(False)
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    assert canvas.talk_share_button().isVisible()
    assert not canvas.talk_share_button().isEnabled()
    canvas.set_creator_profile(get_creator_profile_by_key("music"))
    canvas.set_talk_share_available(True)
    assert not canvas.talk_share_button().isEnabled()
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    assert canvas.talk_share_button().isEnabled()


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("accepted", [True, False])
def test_non_art_chat_keeps_accepted_send_and_failed_draft_semantics(
    canvas, controller, profile, accepted,
):
    controller._apply_creator_profile_key(profile)
    controller.jamulus.send_chat.return_value = accepted
    canvas._chat_input.setText("  Keep this message  ")
    canvas._chat_input.returnPressed.emit()
    controller.jamulus.send_chat.assert_called_once_with("Keep this message")
    if accepted:
        assert canvas._chat_input.text() == ""
        assert canvas.current_notes() == "You: Keep this message"
    else:
        assert canvas._chat_input.text() == "Keep this message"
        assert canvas.current_notes() == ""
        controller.window.flash_message.assert_called_once()


def test_restoring_failed_send_does_not_focus_hidden_art_or_overwrite_a_new_draft(
    canvas, controller, qapp,
):
    controller._apply_creator_profile_key("art")
    button = canvas.talk_share_button()
    button.setFocus()
    _settle(qapp)
    canvas.restore_unsent_chat("Earlier unsent message")
    assert button.hasFocus()
    assert canvas._chat_input.selectedText() == ""
    assert canvas._chat_input.text() == "Earlier unsent message"
    canvas._chat_input.setSelection(2, 4)
    before = _draft(canvas._chat_input)
    canvas.restore_unsent_chat("Do not overwrite this")
    assert _draft(canvas._chat_input) == before
    assert button.hasFocus()


@pytest.mark.parametrize("source", [None, "authenticated-source"])
@pytest.mark.parametrize("transition", ["art_receipt", "art_delivery", "roundtrip", "shutdown"])
def test_incoming_chat_cannot_cross_art_or_shutdown_boundary(
    canvas, controller, source, transition,
):
    canvas.set_notes("PRIVATE_LOCAL_NOTES")
    if transition == "art_receipt":
        controller._apply_creator_profile_key("art")
    controller._on_jamulus_chat("<b>Earlier band text</b>", source)
    if transition in {"art_delivery", "roundtrip"}:
        controller._apply_creator_profile_key("art")
    if transition == "roundtrip":
        controller._apply_creator_profile_key("music")
    elif transition == "shutdown":
        controller._shutdown = True
    controller.drain()
    assert canvas.current_notes() == "PRIVATE_LOCAL_NOTES"
    controller._rpc_ui_source_is_current.assert_not_called()


@pytest.mark.parametrize("source", [None, "authenticated-source"])
@pytest.mark.parametrize("current", [True, False])
def test_current_non_art_chat_keeps_existing_rpc_source_validation(
    canvas, controller, source, current,
):
    controller._rpc_ui_source_is_current.return_value = current
    controller._on_jamulus_chat("<b>A current band message</b>", source)
    assert canvas.current_notes() == ""
    controller.drain()
    assert canvas.current_notes() == ("A current band message" if source is None or current else "")
    if source is None:
        controller._rpc_ui_source_is_current.assert_not_called()
    else:
        controller._rpc_ui_source_is_current.assert_called_once_with(source)


def test_shutdown_enter_restores_the_cleared_music_draft_without_moving_focus(
    canvas, controller, qapp,
):
    composer = canvas._chat_input
    composer.setText("Keep this unsent message")
    canvas._notes.setFocus()
    _settle(qapp)
    assert canvas._notes.hasFocus()
    changes = []
    composer.textChanged.connect(changes.append)
    controller._shutdown = True
    QTest.keyClick(composer, Qt.Key.Key_Return)
    assert changes == ["", "Keep this unsent message"]
    assert composer.text() == "Keep this unsent message"
    assert composer.selectedText() == ""
    assert canvas._notes.hasFocus()
    controller.jamulus.send_chat.assert_not_called()
    assert canvas.current_notes() == ""


def test_shutdown_stale_send_keeps_a_newer_draft_and_its_selection(
    canvas, controller, qapp,
):
    composer = canvas._chat_input
    composer.setText("Keep the newer draft")
    composer.setSelection(2, 6)
    before = _draft(composer)
    canvas._notes.setFocus()
    _settle(qapp)
    controller._shutdown = True
    canvas.chat_submitted.emit("An older submitted message")
    assert _draft(composer) == before
    assert canvas._notes.hasFocus()
    controller.jamulus.send_chat.assert_not_called()
    assert canvas.current_notes() == ""


def test_art_shutdown_stale_send_cannot_populate_the_hidden_music_draft(canvas, controller):
    controller._apply_creator_profile_key("art")
    controller._shutdown = True
    assert canvas._chat_input.text() == ""
    canvas.chat_submitted.emit("An old message from another profile")
    assert canvas._chat_input.text() == ""
    controller.jamulus.send_chat.assert_not_called()
    assert canvas.current_notes() == ""


def _assert_communication_fit(panel):
    button, hint = panel.talk_share_button(), panel._communication_hint
    assert button.isVisibleTo(panel) and hint.isVisibleTo(panel)
    assert button.width() >= button.minimumSizeHint().width()
    assert button.height() >= button.minimumSizeHint().height()
    assert hint.height() >= hint.heightForWidth(hint.width())
    rectangles = [QRect(widget.mapTo(panel, QPoint()), widget.size()) for widget in (button, hint)]
    assert all(panel.rect().contains(rect) for rect in rectangles)
    assert not rectangles[0].intersects(rectangles[1])
    assert all(panel._notes.geometry().bottom() < rect.top() for rect in rectangles)
    assert panel._notes.height() >= panel._notes.minimumSizeHint().height()
    assert not panel._chat_input.isVisibleTo(panel)
    for readout in (panel._guidance, panel._pulse):
        rects = []
        for label in readout.findChildren(QLabel):
            if not label.isVisibleTo(panel):
                continue
            rect = QRect(label.mapTo(readout, QPoint()), label.size())
            assert readout.rect().contains(rect)
            assert label.height() >= label.heightForWidth(label.width())
            assert not any(previous.intersects(rect) for previous in rects)
            rects.append(rect)


@pytest.mark.parametrize("width", [280, 400])
@pytest.mark.parametrize("stretch", [100, 125])
def test_communication_fits_small_notes_width_with_readable_text(canvas, qapp, width, stretch):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    for widget in (canvas.talk_share_button(), canvas._communication_hint):
        font = widget.font()
        font.setStretch(stretch)
        widget.setFont(font)
    canvas.resize(width, 650)
    _settle(qapp)
    assert canvas.size() == QSize(width, 650)
    _assert_communication_fit(canvas)


@pytest.mark.parametrize("size", [(720, 560), (760, 600), (1040, 720)])
@pytest.mark.parametrize("save_state", ["saved", "failed"])
def test_communication_fits_real_notes_workspace(qapp, size, save_state):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")], initial_mode_key="music_jam",
        initial_title="Making together",
    )
    try:
        window.setStyleSheet(load_stylesheet())
        window.set_creator_profile(get_creator_profile_by_key("art"))
        window.set_room_stage_visible(False)
        panel = window.session_canvas
        panel.show()
        facts = SessionConductorFacts(
            creator_profile_key="art", role=SessionRole.GUEST,
            setup_requested=True, art_room=ArtRoomState.CONNECTED,
        )
        panel.set_musician_guidance(build_musician_guidance(SessionConductor(facts).snapshot))
        panel.set_notes_save_state(save_state)
        window.session_strip.set_audio_state("Leave Room")
        window.session_hud.set_state("Your Art room", "Keep making with your own tools.", action_visible=False)
        window.resize(*size)
        window.show()
        _settle(qapp)
        window.center_splitter.setSizes([0, window.width()])
        _settle(qapp)
        assert window.size() == QSize(*size)
        _assert_communication_fit(panel)
        assert panel.room_return_button().isVisibleTo(window)
        assert window.session_strip._audio_button.isVisibleTo(window)
        compact = panel.height() < 500
        assert panel.layout().contentsMargins().bottom() == (Space.XS if compact else Space.MD)
        for readout in (panel._guidance, panel._pulse):
            assert readout.layout().contentsMargins().top() == (Space.XS if compact else Space.SM)
            assert readout.layout().spacing() == (0 if compact else Space.XS)
        panel.set_creator_profile(get_creator_profile_by_key("music"))
        assert panel.layout().contentsMargins().bottom() == Space.MD
        for readout in (panel._guidance, panel._pulse):
            assert readout.layout().contentsMargins().top() == Space.SM
            assert readout.layout().spacing() == Space.XS
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        _destroy(window, qapp)


@pytest.mark.parametrize("font_stretch", [100, 125])
def test_suggestion_moves_only_when_all_tools_fit_and_keeps_focus(canvas, qapp, font_stretch):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    button = canvas._suggestion_button
    for item in canvas._toolbar_buttons:
        font = item.font()
        font.setStretch(font_stretch)
        item.setFont(font)
    for width in (280, 650, 280):
        button.setFocus()
        _settle(qapp)
        canvas.resize(width, 650)
        _settle(qapp)
        assert canvas.size() == QSize(width, 650)
        assert canvas._suggestion_button is button and button.hasFocus()
        assert canvas._suggestion_row.isVisible() == (width == 280)
        assert canvas._suggestion_inline == (width == 650)
        rectangles = []
        for item in canvas._toolbar_buttons:
            rect = QRect(item.mapTo(canvas, QPoint()), item.size())
            assert canvas.rect().contains(rect)
            assert item.width() >= item.minimumSizeHint().width()
            assert not any(previous.intersects(rect) for previous in rectangles)
            rectangles.append(rect)
    canvas.set_creator_profile(get_creator_profile_by_key("music"))
    assert not button.isVisible()
    assert not canvas._suggestion_inline
    assert not canvas._suggestion_row.isVisible()


@pytest.mark.parametrize("save_state", ["failed", "too_large"])
def test_wide_suggestion_stays_hidden_during_note_save_recovery(canvas, qapp, save_state):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    canvas.resize(650, 650)
    _settle(qapp)
    assert canvas._suggestion_inline
    canvas.set_notes_save_state(save_state)
    _settle(qapp)
    assert not canvas._suggestion_button.isVisible()
    assert not canvas._suggestion_row.isVisible()
    assert canvas._save_notes_button.isVisible()
    canvas.set_notes_save_state("saved")
    _settle(qapp)
    assert canvas._suggestion_button.isVisible()
    assert not canvas._suggestion_row.isVisible()



def test_inline_tools_reflow_after_font_change_without_panel_resize(canvas, qapp):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    canvas.resize(470, 650)
    _settle(qapp)
    assert canvas._suggestion_inline
    before = canvas.size()
    for item in canvas._toolbar_buttons:
        font = item.font()
        font.setStretch(150)
        item.setFont(font)
    _settle(qapp)
    assert canvas.size() == before
    assert not canvas._suggestion_inline
    assert canvas._suggestion_row.isVisible()
    for item in canvas._toolbar_buttons:
        assert item.width() >= item.minimumSizeHint().width()


def test_inline_tools_follow_their_visible_keyboard_order(canvas, qapp):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    canvas.resize(650, 650)
    _settle(qapp)
    assert canvas._suggestion_inline
    current = canvas._toolbar_buttons[0]
    current.setFocus()
    _settle(qapp)
    for expected in (*canvas._toolbar_buttons[1:], canvas._notes):
        QTest.keyClick(current, Qt.Key.Key_Tab)
        _settle(qapp)
        assert expected.hasFocus(), expected.objectName()
        current = expected
    control = (Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin"
               else Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(canvas._notes, Qt.Key.Key_Tab, control)
    _settle(qapp)
    assert canvas.talk_share_button().hasFocus()


@pytest.mark.parametrize("backward", [False, True])
def test_art_control_tab_preserves_notes_selection_undo_and_plain_tab_editing(
    canvas, qapp, backward,
):
    canvas.set_creator_profile(get_creator_profile_by_key("art"))
    canvas.resize(650, 650)
    _settle(qapp)
    editor = canvas._notes
    editor.setFocus()
    QTest.keyClicks(editor, "Keep the orange")
    cursor = editor.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(8, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    before = (editor.toPlainText(), cursor.position(), cursor.anchor(),
              editor.document().availableUndoSteps())
    control = (Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin"
               else Qt.KeyboardModifier.ControlModifier)
    modifiers = control | Qt.KeyboardModifier.ShiftModifier if backward else control
    QTest.keyClick(editor, Qt.Key.Key_Tab, modifiers)
    _settle(qapp)
    expected = canvas._toolbar_buttons[-1] if backward else canvas.talk_share_button()
    assert expected.hasFocus()
    cursor = editor.textCursor()
    assert (editor.toPlainText(), cursor.position(), cursor.anchor(),
            editor.document().availableUndoSteps()) == before
    # Ordinary Tab remains a user edit, in both Art and the retained Music UI.
    for profile in ("art", "music"):
        canvas.set_creator_profile(get_creator_profile_by_key(profile))
        editor.setPlainText("")
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.toPlainText() == "\t"

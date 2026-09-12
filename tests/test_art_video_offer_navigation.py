"""A host's new video offer must not replace the guest's chosen work."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QVBoxLayout

from core.art_room_presence import ArtPresenceTarget
from core.reference_video import ReferenceVideoFollowState, session_identity_signer
from core.session_transfer import ReferenceVideoSessionSnapshot, SharedCanvasSessionSnapshot
from tests.test_art_notes_conversation_journey import (
    qapp as _qapp_fixture,
    room as _room_fixture,
)
from tests.test_art_room_controller import drain
from webjam_qt.controllers.application_controller import ApplicationController

qapp = _qapp_fixture
room = _room_fixture


def _offer(pair, qapp, *, generation=1, shared=True):
    app: ApplicationController = pair.app
    _, session_id, session_key = pair.identity
    video = ReferenceVideoSessionSnapshot(
        generation=generation, playback_generation=generation,
        state="paused" if shared else "idle", shared=shared,
        source_display_name="PRIVATE_HOST_REFERENCE.mp4" if shared else "",
        identity_digest=session_identity_signer(
            session_id=session_id, session_key=session_key,
        )("a" * 64) if shared else "",
        position_s=25.0 if shared else 0.0,
        duration_s=300.0 if shared else 0.0,
    )
    if pair.role == "lan":
        snapshot = replace(pair.owner.last_state, reference_video=video)
        pair.owner.client.state = lambda *_: snapshot
        pair.owner.poll_once()
    else:
        current = app._room_participant.native_state
        pair.backend.emit(replace(current, revision=current.revision + 1,
            art_start_key="paint_along", reference_video=video))
        drain(qapp, lambda: app._room_participant.native_state.revision == current.revision + 1)
    qapp.processEvents()
    app._tick_reference_video()
    app._tick_creator_start()
    qapp.processEvents()


def _work(pair, qapp, surface):
    app = pair.app
    if surface == "notes":
        app.window.side_rail.trigger("canvas")
        panel = app.window.session_canvas
        editor = panel._notes
        editor.setPlainText("PRIVATE_GUEST_NOTES: ink and clay")
        editor.moveCursor(QTextCursor.MoveOperation.End)
        editor.setFocus()
        QTest.keyClicks(editor, " with room to grow")
        assert app._save_notes()
        cursor = editor.textCursor()
        cursor.setPosition(4)
        cursor.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
    else:
        app._show_webex_conversation()
        panel = app.window.webex_embed
        if surface == "opening_meeting":
            def accept_handoff(*, manual, meeting_url):
                assert manual
                assert meeting_url == app._effective_meeting_url()
                app.bridge.webex_state = "Opening…"
                return True
            app.bridge.launch_webex.side_effect = accept_handoff
            QTest.mouseClick(panel.fallback_button(), Qt.MouseButton.LeftButton)
            assert panel._launch_status == "Opening…"
        panel.change_link_button().setFocus()
    qapp.processEvents()
    assert panel.isVisibleTo(app.window)
    return panel


def _notes(app):
    editor = app.window.session_canvas._notes
    cursor = editor.textCursor()
    return (editor.toPlainText(), cursor.position(), cursor.anchor(),
            editor.document().isUndoAvailable(), app._persistence.notes_save_state)


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("surface", ["notes", "conversation", "opening_meeting"])
@pytest.mark.parametrize("width", [720, 1100])
def test_first_video_offer_preserves_the_guests_work_and_deliberate_entry(
    room, qapp, caplog, role, surface, width,
):
    pair = room(role=role, configured=True)
    app, window = pair.app, pair.app.window
    window.resize(width, 640)
    panel = _work(pair, qapp, surface)
    workspace, focused = window.workspace_stack.currentWidget(), window.focusWidget()
    notes = _notes(app)
    meeting_state = window.webex_embed._launch_status
    handoffs = app.bridge.launch_webex.call_count

    _offer(pair, qapp)

    assert window.workspace_stack.currentWidget() is workspace
    assert panel.isVisibleTo(window)
    assert window.focusWidget() is focused
    assert _notes(app) == notes
    assert window.webex_embed._launch_status == meeting_state
    assert app._reference_video_dialog is None
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert window.session_strip.art_room_chip.presence.target is ArtPresenceTarget.VIDEO
    assert "video" in window.art_room_overview._overview.activity_actions

    # Navigation back to Room must not replay the deferred automatic offer.
    window.side_rail.trigger("stage")
    app._tick_reference_video()
    app._tick_creator_start()
    qapp.processEvents()
    assert window.art_room_overview.isVisibleTo(window)
    assert app._reference_video_dialog is None
    button = window.art_room_overview.activity_button()
    assert button.isVisibleTo(window) and button.isEnabled()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    qapp.processEvents()
    video_panel = app._reference_video_dialog
    assert video_panel is not None and video_panel.isVisibleTo(window)
    assert video_panel._open_button.isEnabled()
    assert not video_panel._position.isEnabled()
    assert _notes(app) == notes
    assert app._reference_video_identity() == pair.identity
    assert app._room_participant.generation == pair.generation
    pair.player_factory.assert_not_called()
    assert app.bridge.launch_webex.call_count == handoffs
    app.jamulus.send_chat.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    public = (repr(app.art_room_state()) + repr(window.art_room_overview._overview)
              + window.art_room_overview.accessibleDescription() + caplog.text)
    assert "PRIVATE_GUEST_NOTES" not in public
    assert "PRIVATE_HOST_REFERENCE" not in public


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("layer", ["dialog", "modal", "menu"])
def test_first_video_offer_cannot_replace_the_room_under_an_active_dialog_or_menu(
    room, qapp, role, layer,
):
    pair = room(role=role)
    window = pair.app.window
    workspace = window.workspace_stack.currentWidget()
    if layer == "menu":
        overlay = QMenu(window)
        overlay.addAction("Keep making")
        overlay.popup(window.mapToGlobal(window.rect().center()))
    else:
        overlay = QDialog(window)
        QVBoxLayout(overlay).addWidget(QLineEdit("Keep this choice", overlay))
        overlay.setModal(layer == "modal")
        overlay.show()
        overlay.activateWindow()
    qapp.processEvents()
    if layer == "menu":
        assert QApplication.activePopupWidget() is overlay
    elif layer == "modal":
        assert QApplication.activeModalWidget() is overlay
    else:
        assert QApplication.activeWindow() is overlay
    focused = QApplication.focusWidget()
    try:
        _offer(pair, qapp)
        assert window.workspace_stack.currentWidget() is workspace
        assert pair.app._reference_video_dialog is None
        assert overlay.isVisible()
        assert QApplication.focusWidget() is focused
    finally:
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()
    pair.app._tick_reference_video()
    pair.app._tick_creator_start()
    qapp.processEvents()
    assert window.workspace_stack.currentWidget() is workspace
    assert pair.app._reference_video_dialog is None
    assert window.art_room_overview._overview.activity_actions == ("video",)
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_deferred_offer_withdrawal_and_replacement_keep_the_current_notes(room, qapp, role):
    pair = room(role=role)
    panel = _work(pair, qapp, "notes")
    notes = _notes(pair.app)
    for generation, shared in ((1, True), (2, False), (3, True)):
        _offer(pair, qapp, generation=generation, shared=shared)
        assert panel.isVisibleTo(pair.app.window)
        assert _notes(pair.app) == notes
        assert pair.app._reference_video_dialog is None
        actions = pair.app.window.art_room_overview._overview.activity_actions
        if shared:
            assert actions == ("video",)
        else:
            # Between shares, a Paint along room keeps one way back to the
            # panel where its room fact is known (native carries the host's
            # published start; the LAN path does not yet). It never invents
            # any other action.
            assert actions in ((), ("video",))
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_deferred_video_is_reachable_when_canvas_recovery_owns_the_room_chip(room, qapp, role):
    pair = room(role=role)
    app = pair.app
    notes = _work(pair, qapp, "notes")
    pair.launcher.installed = False
    canvas = SharedCanvasSessionSnapshot(
        generation=1, shared=True,
        join_url="drawpile://studio.example/lesson?p=PRIVATE_CANVAS_INVITE",
        server_label="studio.example", session_label="lesson",
    )
    if role == "lan":
        snapshot = replace(pair.owner.last_state, shared_canvas=canvas)
        pair.owner.client.state = lambda *_: snapshot
        pair.owner.poll_once()
    else:
        current = app._room_participant.native_state
        pair.backend.emit(replace(current, revision=current.revision + 1, shared_canvas=canvas))
        drain(qapp, lambda: app._room_participant.native_state.revision == current.revision + 1)
    qapp.processEvents()

    _offer(pair, qapp)

    assert notes.isVisibleTo(app.window)
    assert app._reference_video_dialog is None
    assert app.window.session_strip.art_room_chip.presence.target is ArtPresenceTarget.CANVAS
    app.window.side_rail.trigger("stage")
    app._tick_creator_start()
    qapp.processEvents()
    overview = app.window.art_room_overview
    assert overview._overview.activity_actions == ("canvas", "video")
    button = overview.secondary_activity_button()
    assert button.isVisibleTo(app.window) and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert app._reference_video_dialog.isVisibleTo(app.window)
    pair.player_factory.assert_not_called()
    assert not pair.launcher.joined and not pair.launcher.host_pages


@pytest.mark.parametrize("role", ["lan", "native"])
def test_first_video_offer_still_opens_from_the_untouched_room(room, qapp, role):
    pair = room(role=role)
    assert pair.app.window.art_room_overview.isVisibleTo(pair.app.window)
    _offer(pair, qapp)
    panel = pair.app._reference_video_dialog
    assert panel is not None and panel.isVisibleTo(pair.app.window)
    assert panel._open_button.isEnabled()
    assert not panel._position.isEnabled()
    pair.player_factory.assert_not_called()


def test_host_selected_paint_along_start_keeps_its_existing_first_action(room, qapp):
    pair = room(role="host", profile="art")
    _work(pair, qapp, "notes")
    notes = _notes(pair.app)
    pair.app.settings.last_creator_start_key = "paint_along"
    pair.app._tick_creator_start()
    qapp.processEvents()
    panel = pair.app._reference_video_dialog
    assert panel is not None and panel.isVisibleTo(pair.app.window)
    assert panel._hosting
    assert _notes(pair.app) == notes
    pair.player_factory.assert_not_called()

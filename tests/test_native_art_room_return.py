"""Real ApplicationController returns preserve work and reveal the current native room."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from core.reference_video import ReferenceVideoFollowState
from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoSessionSnapshot, SharedCanvasSessionSnapshot
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import RoomBackend, drain, remote
from tests.test_native_art_activities import (
    _assert_private_and_stable,
    native_room as _native_room_fixture,
    qapp as _qapp_fixture,
)
from webjam_qt.theme import load_stylesheet

native_room = _native_room_fixture
qapp = _qapp_fixture

_BASE_NOTES = "PRIVATELOCALNOTES: paint the background first."
_NOTE_INSERT = " Keep the orange."
_MESSAGE_DRAFT = "PRIVATEUNSENTDRAFT"


@pytest.fixture(autouse=True)
def local_notes_and_theme(qapp, monkeypatch, tmp_path):
    persistence = tmp_path / "personal-notes"
    persistence.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: persistence,
    )
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    yield
    qapp.setStyleSheet(previous)


def _click(button, qapp):
    assert button.isVisible() and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _notes(pair, qapp):
    window = pair.app.window
    window.resize(720, 560)
    window.side_rail.trigger("canvas")
    qapp.processEvents()
    assert window.workspace_stack.currentWidget() is window.center_splitter
    assert window.session_canvas.isVisibleTo(window)
    assert not window.art_room_overview.isVisibleTo(window)
    return window.session_canvas


def _draft(canvas):
    editor = canvas._notes
    editor.setPlainText(_BASE_NOTES)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    cursor.insertText(_NOTE_INSERT)
    cursor.endEditBlock()
    cursor.setPosition(4)
    cursor.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    canvas._chat_input.setText(_MESSAGE_DRAFT)
    canvas._chat_input.setCursorPosition(5)
    assert editor.document().isUndoAvailable()
    return (editor.toPlainText(), cursor.position(), cursor.anchor(),
            canvas._chat_input.text(), canvas._chat_input.cursorPosition())


def _assert_draft(canvas, expected, *, exercise_undo=False):
    editor = canvas._notes
    cursor = editor.textCursor()
    assert (editor.toPlainText(), cursor.position(), cursor.anchor(),
            canvas._chat_input.text(), canvas._chat_input.cursorPosition()) == expected
    assert editor.document().isUndoAvailable()
    if exercise_undo:
        editor.undo()
        assert editor.toPlainText() == _BASE_NOTES
        editor.redo()
        assert editor.toPlainText() == _BASE_NOTES + _NOTE_INSERT


def _paint_along_from_notes(pair, qapp):
    # This existing semantic menu route is available while Notes owns the workspace.
    pair.app._on_rail_view_changed("reference_video")
    qapp.processEvents()
    panel = pair.app._reference_video_dialog
    assert panel is not None and panel.isVisibleTo(pair.app.window)
    assert pair.app.window.workspace_stack.currentWidget() is panel
    return panel


def _assert_room(pair, qapp, *, phase="connected", actions=("video", "canvas")):
    app, window = pair.app, pair.app.window
    qapp.processEvents()
    overview = window.art_room_overview
    assert window.workspace_stack.currentWidget() is window.center_splitter
    assert window._room_stage.isVisibleTo(window)
    assert overview.isVisibleTo(window)
    assert not window.session_canvas.isVisibleTo(window)
    assert window.center_splitter.sizes()[1] == 0
    assert window.side_rail.current_key() == "stage"
    assert app._last_content_key == "stage"
    assert overview._overview.phase == phase
    assert set(overview._overview.activity_actions) == set(actions)
    focus = window.focusWidget()
    assert focus is overview or focus is not None and overview.isAncestorOf(focus)
    public = repr(overview._overview) + overview.accessibleDescription()
    assert "PRIVATELOCALNOTES" not in public and _MESSAGE_DRAFT not in public


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("via_video", [False, True])
def test_native_notes_return_reaches_full_room_and_preserves_local_work(
    native_room, qapp, monkeypatch, caplog, profile, via_video,
):
    pair = native_room(profile=profile)
    canvas = _notes(pair, qapp)
    draft = _draft(canvas)
    coordinator = pair.app._reference_video
    if via_video:
        panel = _paint_along_from_notes(pair, qapp)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(pair.copy), ""))
        _click(panel._open_button, qapp)
        pair.app._tick_reference_video()
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
        player = pair.players[0]
        assert player.loads == [pair.copy] and player.muted
        transport = (player.state, player.position, tuple(player.seeks))
        _click(panel._back_button, qapp)
        assert pair.app._reference_video_dialog is panel
        assert not panel.isVisibleTo(pair.app.window)
        assert len(pair.players) == 1 and player.loads == [pair.copy]
        assert (player.state, player.position, tuple(player.seeks)) == transport
    else:
        _click(canvas.room_return_button(), qapp)
        assert pair.players == []
    _assert_room(pair, qapp)
    assert pair.app._reference_video is coordinator
    _assert_draft(canvas, draft, exercise_undo=True)
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    _assert_private_and_stable(pair, caplog)


def test_native_loss_releases_video_safely_then_notes_returns_to_failed_room(
    native_room, qapp,
):
    pair = native_room(profile="art")
    app = pair.app
    canvas = _notes(pair, qapp)
    draft = _draft(canvas)
    panel = _paint_along_from_notes(pair, qapp)
    source = app._remote_session
    source.mark_connection_lost(expected_generation=source.snapshot.generation)
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.FAILED)
    app._tick_creator_start()
    qapp.processEvents()
    assert app._reference_video_dialog is None
    assert app.window.workspace_stack.currentWidget() is app.window.center_splitter
    assert canvas.isVisibleTo(app.window)
    _assert_draft(canvas, draft)
    # A callback retained from the released video must not reinterpret that
    # implicit cleanup as an explicit request to change the artist's workspace.
    app._return_to_art_room(panel)
    assert canvas.isVisibleTo(app.window)
    _click(canvas.room_return_button(), qapp)
    _assert_room(pair, qapp, phase="failed", actions=())
    assert app._remote_session is source
    assert app._remote_invitation_requires_replacement
    assert pair.players == [] and pair.launcher.joined == []
    _assert_draft(canvas, draft, exercise_undo=True)


@pytest.mark.parametrize("withdrawn", ["video", "canvas"])
def test_video_back_renders_latest_native_withdrawal_and_remaining_activity(
    native_room, qapp, caplog, withdrawn,
):
    pair = native_room()
    canvas = _notes(pair, qapp)
    draft = _draft(canvas)
    panel = _paint_along_from_notes(pair, qapp)
    updated = replace(pair.offered, revision=2, **{
        "reference_video" if withdrawn == "video" else "shared_canvas": (
            ReferenceVideoSessionSnapshot(generation=2) if withdrawn == "video"
            else SharedCanvasSessionSnapshot(generation=2)
        ),
    })
    pair.backend.emit(updated)
    drain(qapp, lambda: pair.app._room_participant.native_state.revision == 2)
    pair.app._tick_reference_video()
    assert pair.app.window.workspace_stack.currentWidget() is panel
    _click(panel._back_button, qapp)
    remaining = "canvas" if withdrawn == "video" else "video"
    _assert_room(pair, qapp, actions=(remaining,))
    _assert_draft(canvas, draft)
    overview = pair.app.window.art_room_overview
    overview.ensureWidgetVisible(overview.activity_button())
    _click(overview.activity_button(), qapp)
    if remaining == "canvas":
        assert pair.app._shared_canvas_dialog.isVisible()
    else:
        assert pair.app._reference_video_dialog is panel and panel.isVisibleTo(pair.app.window)
    assert pair.players == [] and pair.launcher.joined == []
    _assert_private_and_stable(pair, caplog)


@pytest.mark.parametrize("replacement", ["new_art_room", "saved_music"])
def test_retired_video_return_cannot_redirect_a_replacement_workspace(
    native_room, qapp, replacement,
):
    pair = native_room(profile="music")
    app = pair.app
    _notes(pair, qapp)
    retired = _paint_along_from_notes(pair, qapp)
    previous_source = app._remote_session
    # Native invitations require an explicit Leave before a replacement Join.
    app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == "music"
    if replacement == "new_art_room":
        assert app.accept_invitation(remote())
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        assert app._remote_session is not previous_source
        current_backend = RoomBackend.instances[-1]
        current_backend.emit(RoomState(1, "art", "talk_and_make"))
        drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
        assert app._remote_session.room_identity != pair.identity
    canvas = _notes(pair, qapp)
    draft = _draft(canvas)
    source, generation = app._remote_session, app._room_participant.generation
    app._return_to_art_room(retired)
    qapp.processEvents()
    assert canvas.isVisibleTo(app.window)
    assert app.window.side_rail.current_key() == "canvas"
    assert app._remote_session is source and app._room_participant.generation == generation
    _assert_draft(canvas, draft)
    assert pair.players == [] and pair.launcher.joined == []
    if replacement == "new_art_room":
        _click(canvas.room_return_button(), qapp)
        _assert_room(pair, qapp, actions=())
    else:
        assert not canvas.room_return_button().isVisible()
        canvas.return_to_room_requested.emit()
        qapp.processEvents()
        assert canvas.isVisibleTo(app.window)
        assert app.window.side_rail.current_key() == "canvas"

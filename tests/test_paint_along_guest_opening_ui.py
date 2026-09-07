"""A guest's explicit copy opening has one honest action and one owner."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog
from shiboken6 import isValid

from core.reference_video import (
    ReferenceVideoFollowState, load_reference_video_source, session_identity_signer,
)
from core.session_transfer import ReferenceVideoSessionSnapshot
from tests.test_art_notes_conversation_journey import qapp as _qapp, room as _room
from tests.test_art_room_controller import drain
from tests.test_paint_along_host_opening_ui import SurfacePlayer
from webjam_qt.controllers.application_controller import ApplicationController

qapp, room = _qapp, _room


def _offer(pair, qapp, path, *, shared=True):
    app: ApplicationController = pair.app
    _, session_id, key = pair.identity
    video = ReferenceVideoSessionSnapshot(
        generation=1 if shared else 2, playback_generation=1 if shared else 2,
        state="paused" if shared else "idle", shared=shared,
        source_display_name="process.mp4" if shared else "",
        identity_digest=session_identity_signer(session_id=session_id, session_key=key)(
            load_reference_video_source(path).content_sha256,
        ) if shared else "",
        duration_s=300.0 if shared else 0.0,
    )
    if pair.role == "lan":
        snapshot = replace(pair.owner.last_state, reference_video=video)
        pair.owner.client.state = lambda *_: snapshot
        pair.owner.poll_once()
    else:
        state = app._room_participant.native_state
        pair.backend.emit(replace(state, revision=state.revision + 1,
                                  art_start_key="paint_along", reference_video=video))
        drain(qapp, lambda: app._room_participant.native_state.revision == state.revision + 1)
    app._tick_reference_video()
    qapp.processEvents()


@pytest.fixture
def guest(room, qapp, tmp_path):
    def make(role):
        pair = room(role=role)
        pair.path = tmp_path / "PRIVATE_LOCAL_COPY.mp4"
        pair.path.write_bytes(b"synthetic guest process video")
        pair.players = []

        def player(parent):
            result = SurfacePlayer(parent)
            pair.players.append(result)
            result.on_load = lambda: pair.during_load(result)
            return result

        pair.player_factory.side_effect = player
        pair.during_load = lambda player: None
        _offer(pair, qapp, pair.path)
        pair.app._open_reference_video()
        return pair

    return make


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("outcome", ["success", "decode_error", "withdraw"])
def test_guest_opening_suppresses_another_chooser_then_uses_current_truth(
    guest, qapp, monkeypatch, caplog, role, outcome,
):
    pair = guest(role)
    app, dialog = pair.app, pair.app._reference_video_dialog
    chooser = Mock(return_value=(str(pair.path), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", chooser)
    notes = app.window.session_canvas.current_notes()
    observed = []
    seek = Mock()
    dialog.seek_requested.connect(seek)

    def loading(player):
        app._tick_reference_video()
        qapp.processEvents()
        observed.append((dialog._open_button.isEnabled(), dialog._status.text(),
                         dialog._position.isEnabled(), dialog._attached_surface))
        # A queued duplicate activation must not open another native chooser.
        dialog._choose_local_copy()
        if outcome == "decode_error":
            player.after_load_error = True
        elif outcome == "withdraw":
            _offer(pair, qapp, pair.path, shared=False)

    pair.during_load = loading
    dialog._open_button.click()

    assert observed == [(False, "Checking this local file before following the host.", False, None)]
    assert chooser.call_count == 1
    assert len(pair.players) == len(pair.players[0].loads) == 1
    assert pair.players[0].muted
    assert not dialog._position.isEnabled()
    seek.assert_not_called()
    if outcome == "success":
        assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
        assert dialog._hide_button.isEnabled() and not dialog._open_button.isVisibleTo(dialog)
    elif outcome == "decode_error":
        assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
        assert dialog._open_button.isEnabled() and dialog._open_button.text() == "Open my copy…"
    else:
        assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        assert not dialog._open_button.isVisibleTo(dialog)
    assert app.window.session_canvas.current_notes() == notes
    assert app._reference_video_identity() == pair.identity
    app.bridge.launch_webex.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    assert "PRIVATE_LOCAL_COPY" not in caplog.text


@pytest.mark.parametrize("role", ["lan", "native"])
def test_guest_file_chooser_return_after_panel_retirement_is_ignored(guest, qapp, monkeypatch, role):
    pair = guest(role)
    app, dialog = pair.app, pair.app._reference_video_dialog

    def choose(*args):
        app._release_reference_video()
        app._open_reference_video()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
        assert not isValid(dialog)
        return str(pair.path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    dialog._choose_local_copy()
    assert app._reference_video_dialog is not dialog
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_cancelling_guest_file_choice_preserves_the_next_action(guest, monkeypatch, role):
    pair = guest(role)
    dialog = pair.app._reference_video_dialog
    status = dialog._status.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: ("", ""))
    dialog._open_button.click()
    assert dialog._open_button.isEnabled() and dialog._status.text() == status
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("change", ["back", "cleanup", "retired"])
def test_guest_load_completion_cannot_overrule_navigation_or_room_ownership(
    guest, qapp, monkeypatch, role, change,
):
    pair = guest(role)
    app, dialog = pair.app, pair.app._reference_video_dialog
    app.window.resize(720, 640)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(pair.path), ""))
    observed = []

    def loading(player):
        if change == "back":
            dialog._back_button.click()
        elif change == "cleanup":
            app.audio.require_cleanup_retry(
                hosting=False, art_room=True, error="The room is still closing.",
                title="Finish leaving the room",
            )
            app._sync_paint_along_room()
            observed.append((dialog._return_button.isEnabled(), dialog._open_button.isEnabled()))
        else:
            app._release_reference_video()
            app._open_reference_video()
            QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
            observed.append(isValid(dialog))
        qapp.processEvents()

    pair.during_load = loading
    dialog._choose_local_copy()
    assert len(pair.players) == 1
    if change == "back":
        assert app.window.art_room_overview.isVisibleTo(app.window)
        app._open_reference_video()
        assert dialog._hide_button.isEnabled()
    elif change == "cleanup":
        assert observed == [(True, False)]
        assert dialog._return_button.isVisibleTo(dialog)
        assert not dialog._open_button.isVisibleTo(dialog)
    else:
        assert observed == [False]
        assert app._reference_video_dialog is not dialog
        assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        assert pair.players[0].state == "closed"
    app.bridge.launch_webex.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()

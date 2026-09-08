"""An early local copy is preparation, never permission to follow a video."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from core.reference_video import (
    ReferenceVideoFollowState, load_reference_video_source, session_identity_signer,
)
from core.session_transfer import ReferenceVideoSessionSnapshot
from tests.test_art_notes_conversation_journey import qapp as _qapp, room as _room
from tests.test_art_room_controller import drain
from tests.test_paint_along_host_opening_ui import SurfacePlayer
from webjam_qt.controllers.application_controller import ApplicationController

qapp, room = _qapp, _room


def _publish(pair, qapp, video=None):
    app: ApplicationController = pair.app
    if pair.role == "lan":
        state = replace(pair.owner.last_state,
                        reference_video=video or ReferenceVideoSessionSnapshot())
        pair.owner.client.state = lambda *_: state
        pair.owner.poll_once()
        qapp.processEvents()
    else:
        state = app._room_participant.native_state
        pair.backend.emit(replace(state, revision=state.revision + 1,
            art_start_key="paint_along",
            reference_video=video or ReferenceVideoSessionSnapshot()))
        drain(qapp, lambda: app._room_participant.native_state.revision == state.revision + 1)
    app._tick_reference_video()
    app._tick_creator_start()
    qapp.processEvents()


@pytest.fixture
def waiting_guest(room, qapp, tmp_path):
    def make(role):
        pair = room(role=role)
        pair.path = tmp_path / "PRIVATE_EARLY_COPY.mp4"
        pair.path.write_bytes(b"a synthetic process video for any artist")
        pair.players = []
        pair.during_load = lambda player: None

        def player(parent):
            result = SurfacePlayer(parent)
            result.on_load = lambda: pair.during_load(result)
            pair.players.append(result)
            return result

        pair.player_factory.side_effect = player
        _publish(pair, qapp)
        if role == "native":
            button = pair.app.window.art_room_overview.activity_button()
            assert button.isVisibleTo(pair.app.window) and button.isEnabled()
            button.click()
        else:
            # LAN does not publish the host's start before a video offer.
            # Exercise its existing, explicit in-room Paint along entry.
            pair.app._open_reference_video()
        pair.dialog = pair.app._reference_video_dialog
        assert pair.dialog.isVisibleTo(pair.app.window)
        assert pair.app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        return pair

    return make


def _choose(pair, monkeypatch, qapp, *, path=None):
    chooser = Mock(return_value=(str(path or pair.path), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", chooser)
    button = pair.dialog._open_button
    assert button.isVisibleTo(pair.app.window) and button.isEnabled()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    qapp.processEvents()
    assert chooser.call_count == 1
    return chooser


def _offer(pair, qapp, *, matching=True):
    _, session_id, key = pair.identity
    digest = load_reference_video_source(pair.path).content_sha256 if matching else "b" * 64
    _publish(pair, qapp, ReferenceVideoSessionSnapshot(
        generation=1, playback_generation=1, state="playing", shared=True,
        source_display_name="PRIVATE_HOST_TITLE.mp4", duration_s=300.0, position_s=25.0,
        identity_digest=session_identity_signer(session_id=session_id, session_key=key)(digest),
    ))


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("matching", [True, False])
@pytest.mark.parametrize("width", [720, 1100])
def test_early_copy_waits_for_a_matching_host_offer(
    waiting_guest, monkeypatch, qapp, caplog, role, matching, width,
):
    pair = waiting_guest(role)
    app, dialog = pair.app, pair.dialog
    app.window.resize(width, 560)
    qapp.processEvents()
    assert dialog.rect().contains(dialog._open_button.geometry())
    assert dialog.rect().contains(dialog._hint.geometry())
    notes = app.window.session_canvas.current_notes()
    seeks = Mock()
    dialog.seek_requested.connect(seeks)
    opening = []

    def during_load(player):
        _publish(pair, qapp)
        opening.append(dialog._headline._full_text)
        assert not dialog._open_button.isEnabled()
        assert not app._reference_video.follow_snapshot.local_copy_prepared
        dialog._choose_local_copy()

    pair.during_load = during_load
    chooser = _choose(pair, monkeypatch, qapp)
    assert opening == ["Opening your copy"]
    assert len(pair.players) == len(pair.players[0].loads) == 1
    for _ in range(2):
        _publish(pair, qapp)
        snapshot = app._reference_video.follow_snapshot
        assert snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        assert snapshot.local_copy_prepared and snapshot.can_close_local_copy
        assert not snapshot.can_follow and not snapshot.should_play
        assert dialog._headline._full_text == "Your copy is open"
        assert "check" in dialog._status.text() and "host" in dialog._status.text()
        assert not dialog._open_button.isVisibleTo(dialog)
        assert not dialog._position.isVisibleTo(dialog)
        assert dialog._attached_surface is None
        assert pair.players[0].state != "playing"
    _offer(pair, qapp, matching=matching)
    snapshot = app._reference_video.follow_snapshot
    if matching:
        assert snapshot.state is ReferenceVideoFollowState.FOLLOWING
        assert snapshot.can_follow and snapshot.should_play
        assert pair.players[0].state == "playing"
        assert dialog._attached_surface is pair.players[0].surface
    else:
        assert snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
        assert not snapshot.can_follow and pair.players[0].state != "playing"
        assert dialog._open_button.isVisibleTo(dialog) and dialog._open_button.isEnabled()
        assert dialog._attached_surface is None
    assert not dialog._position.isEnabled()
    seeks.assert_not_called()
    assert pair.players[0].muted and chooser.call_count == 1
    assert app._reference_video_identity() == pair.identity
    assert app._room_participant.generation == pair.generation
    assert app.window.session_canvas.current_notes() == notes
    app.bridge.launch_webex.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    assert "PRIVATE_EARLY_COPY" not in caplog.text
    assert "PRIVATE_HOST_TITLE" not in caplog.text


@pytest.mark.parametrize("role", ["lan", "native"])
def test_withdrawn_offer_returns_a_prepared_copy_to_silent_waiting(
    waiting_guest, monkeypatch, qapp, role,
):
    pair = waiting_guest(role)
    _choose(pair, monkeypatch, qapp)
    _offer(pair, qapp)
    assert pair.players[0].state == "playing"
    _publish(pair, qapp, ReferenceVideoSessionSnapshot(generation=2, playback_generation=2))
    snapshot = pair.app._reference_video.follow_snapshot
    assert snapshot.local_copy_prepared and not snapshot.can_follow
    assert pair.players[0].state == "paused"
    assert pair.dialog._headline._full_text == "Your copy is open"
    assert pair.dialog._attached_surface is None
    assert not pair.dialog._position.isEnabled()
    assert pair.dialog._close_action.isVisible()


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("change", ["delete", "replace"])
def test_early_copy_status_does_not_outlive_its_local_file(
    waiting_guest, monkeypatch, qapp, role, change,
):
    pair = waiting_guest(role)
    _choose(pair, monkeypatch, qapp)
    if change == "delete":
        pair.path.unlink()
    else:
        pair.path.write_bytes(b"different bytes")
    pair.app._tick_reference_video()
    qapp.processEvents()
    snapshot = pair.app._reference_video.follow_snapshot
    assert not snapshot.local_copy_prepared and snapshot.can_close_local_copy
    assert not snapshot.can_follow and not snapshot.should_play
    assert pair.dialog._headline._full_text != "Your copy is open"
    assert pair.dialog._open_button.isVisibleTo(pair.dialog)
    assert pair.dialog._open_button.isEnabled()
    assert pair.dialog._attached_surface is None


@pytest.mark.parametrize("role", ["lan", "native"])
def test_failed_early_decode_offers_retry_without_claiming_a_prepared_copy(
    waiting_guest, monkeypatch, qapp, caplog, role,
):
    pair = waiting_guest(role)
    pair.during_load = lambda player: setattr(player, "after_load_error", True)
    _choose(pair, monkeypatch, qapp)
    snapshot = pair.app._reference_video.follow_snapshot
    assert not snapshot.local_copy_prepared and not snapshot.can_follow
    assert pair.dialog._open_button.isVisibleTo(pair.dialog)
    assert pair.dialog._open_button.isEnabled()
    assert "again" in pair.dialog._status.text()
    assert "PRIVATE decoder" not in caplog.text
    assert "PRIVATE_EARLY_COPY" not in caplog.text


@pytest.mark.parametrize("role", ["lan", "native"])
def test_cancelled_early_picker_keeps_one_action(waiting_guest, monkeypatch, qapp, role):
    pair = waiting_guest(role)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    assert pair.dialog._open_button.isVisibleTo(pair.dialog)
    pair.dialog._open_button.click()
    assert pair.dialog._open_button.isEnabled()
    assert not pair.app._reference_video.follow_snapshot.local_copy_prepared
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_early_picker_cannot_open_a_file_after_connection_loss(
    waiting_guest, monkeypatch, qapp, role,
):
    pair = waiting_guest(role)

    def choose(*args):
        if role == "lan":
            pair.app._room_participant.lose_lan(pair.owner, pair.generation, True)
        else:
            pair.backend.connection_available = False
        pair.app._sync_paint_along_room()
        return str(pair.path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    assert pair.dialog._open_button.isVisibleTo(pair.dialog)
    pair.dialog._open_button.click()
    assert not pair.dialog._open_button.isEnabled()
    assert pair.dialog._return_button.isVisibleTo(pair.dialog)
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_make_together_still_requires_no_video_preparation(room, qapp, role):
    pair = room(role=role)
    pair.app.settings.last_creator_start_key = "paint_along"
    pair.app._tick_creator_start()
    overview = pair.app.window.art_room_overview._overview
    assert overview.activity_label == "Bring your own tools"
    assert "video" not in overview.activity_actions
    assert pair.app._reference_video_dialog is None
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_prepared_copy_can_be_closed_before_the_host_shares(
    waiting_guest, monkeypatch, qapp, role,
):
    pair = waiting_guest(role)
    _choose(pair, monkeypatch, qapp)
    assert pair.dialog._close_action.isVisible()
    pair.dialog._close_action.trigger()
    qapp.processEvents()
    snapshot = pair.app._reference_video.follow_snapshot
    assert not snapshot.local_copy_prepared and not snapshot.can_close_local_copy
    assert pair.dialog._open_button.isVisibleTo(pair.dialog)
    assert pair.dialog._open_button.isEnabled()

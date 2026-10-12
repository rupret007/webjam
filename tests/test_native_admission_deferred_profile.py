"""Native admission must not complete while profile adoption is deferred."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import mock

import pytest

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import (
    RoomBackend,
    controllers as _controllers_fixture,
    drain,
    qapp as _qapp_fixture,
    remote,
)

qapp = _qapp_fixture
controllers = _controllers_fixture


@pytest.mark.parametrize("retry_elapsed", [4.9, 5.0])
def test_aged_first_state_can_defer_adoption_with_a_bounded_retry_deadline(
    qapp, controllers, monkeypatch, retry_elapsed,
):
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    room = app._room_participant
    source = app._remote_session
    now = time.monotonic()
    started = now
    room.native_source = source
    room.native_generation = source.snapshot.generation
    room.native_state = RoomState(1, "art", "paint_along")
    room.native_received_at = now - 6.0
    room.native_wait_started = 0
    scheduled = []
    monkeypatch.setattr(
        "webjam_qt.controllers.room_participant.time",
        SimpleNamespace(monotonic=lambda: now),
    )
    monkeypatch.setattr(
        "webjam_qt.controllers.room_participant.QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append((delay, callback))),
    )
    app.window.recording_studio._workspace_review_active = True
    try:
        room.connected_native(source, source.snapshot)
        assert room.probing
        assert room.native_applied is None
        assert app.creator_profile.key == "music"
        assert [delay for delay, _ in scheduled] == [200, 5000]

        # A retry while media work is pending must retain the original deadline.
        now = started + 1.0
        scheduled.pop(0)[1]()
        assert [delay for delay, _ in scheduled] == [5000, 200]
        assert room.probing
        assert room.native_applied is None
        app.window.recording_studio._workspace_review_active = False
        now = started + retry_elapsed
        scheduled.pop()[1]()

        if retry_elapsed < 5.0:
            assert app.creator_profile.key == "art"
            assert app.creator_start.key == "paint_along"
            assert room.state is ArtRoomState.CONNECTED
            assert not room.probing
            assert app._remote_invitation is None
            scheduled.pop()[1]()
            assert not app._remote_invitation_requires_replacement
        else:
            assert app.creator_profile.key == "music"
            assert room.state is not ArtRoomState.CONNECTED
            assert room.probing
            assert room.native_applied is None
            scheduled.pop()[1]()
            assert app._remote_invitation_requires_replacement
            assert "Update WebJam" in app._remote_fresh_invitation_detail()
        assert scheduled == []
        app._activate_remote_guest_route.assert_not_called()
    finally:
        app.window.recording_studio._workspace_review_active = False


def test_native_admission_waits_for_pending_media_operation_then_retries(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    room = app._room_participant
    assert room.probing

    # Hold a Library media operation pending while the current native
    # RoomState requests the Art profile.
    app.window.recording_studio._workspace_review_active = True
    try:
        backend.emit(RoomState(1, "art", "paint_along"))
        drain(qapp, lambda: room.native_state is not None and room.native_state.revision == 1)

        # Give the deferred retry a few passes through the event loop; none
        # of them may declare admission complete while media work is open.
        for _ in range(10):
            qapp.processEvents()
        assert app.creator_profile.key == "music"
        assert room.state is not ArtRoomState.CONNECTED
        assert room.probing
        assert app._remote_invitation is not None
        assert room.native_applied is None

        # Releasing and reapplying the same snapshot must still be able to
        # retry profile adoption once the operation finishes.
        app.window.recording_studio._workspace_review_active = False
        drain(qapp, lambda: app.creator_profile.key == "art")
    finally:
        app.window.recording_studio._workspace_review_active = False

    assert room.state is ArtRoomState.CONNECTED
    assert not room.probing
    assert app._remote_invitation is None
    assert app.creator_start.key == "paint_along"
    app._activate_remote_guest_route.assert_not_called()


def test_deferred_retry_cannot_admit_a_connection_that_already_failed(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    room = app._room_participant
    source = app._remote_session
    generation = source.snapshot.generation

    app.window.recording_studio._workspace_review_active = True
    try:
        backend.emit(RoomState(1, "art", "paint_along"))
        drain(qapp, lambda: room.native_state is not None and room.native_state.revision == 1)
        # The 200ms retry is now queued behind the pending Library operation.
        # Fail the guest transport before that operation ever clears; the
        # retry must not treat the stale (source, generation, revision) key
        # as still admissible once it fires.
        assert source.mark_connection_lost(expected_generation=generation)
        assert source.snapshot.phase is RemoteSessionPhase.FAILED
    finally:
        app.window.recording_studio._workspace_review_active = False

    # Give the deferred retry real wall-clock time to fire against the now
    # terminal transport; there is no future positive state to drain toward.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)

    assert app.creator_profile.key == "music"
    assert room.state is not ArtRoomState.CONNECTED
    assert room.native_applied is None
    app._activate_remote_guest_route.assert_not_called()

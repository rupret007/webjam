"""Native admission must not complete while profile adoption is deferred."""
from __future__ import annotations

import time
from unittest import mock

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

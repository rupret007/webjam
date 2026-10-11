"""Native admission must not complete while profile adoption is deferred."""
from __future__ import annotations

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

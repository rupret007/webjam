"""Native admission must not complete while profile adoption is deferred."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import mock

import pytest

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from services.remote_session_runtime import RemoteSessionErrorCode, RemoteSessionPhase
from tests.test_art_room_controller import (
    RoomBackend,
    controllers as _controllers_fixture,
    drain,
    qapp as _qapp_fixture,
    remote,
)

qapp = _qapp_fixture
controllers = _controllers_fixture


@pytest.mark.parametrize("owner", ["library", "studio"])
@pytest.mark.parametrize("entry", ["accept", "startup", "enroll"])
def test_pending_media_keeps_native_invitation_unconsumed(
    qapp, controllers, monkeypatch, owner, entry,
):
    construct = mock.Mock(side_effect=RoomBackend)
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", construct,
    )
    app = controllers()
    invitation = remote()
    flash = mock.Mock()
    monkeypatch.setattr(app.window, "flash_message", flash)
    app._activate_remote_guest_route = mock.Mock()
    if owner == "library":
        app.session_library.show()
        app.session_library.dialog._import_in_progress = True
    else:
        app.window.recording_studio._workspace_review_active = True

    def join():
        if entry == "accept":
            return app.accept_invitation(invitation)
        if entry == "startup":
            return app.begin_startup_journey()
        return app._begin_remote_join()

    if entry != "accept":
        app._remote_invitation = invitation
    try:
        result = join()
        if entry != "enroll":
            assert result is False
        qapp.processEvents()
        construct.assert_not_called()
        assert app._remote_session is None
        assert not app._remote_invitation_requires_replacement
        assert app.creator_profile.key == "music"
        assert app._remote_invitation is (None if entry == "accept" else invitation)
        assert any("Wait for the workspace operation result" in call.args[0]
                   for call in flash.call_args_list)
    finally:
        if owner == "library":
            app.session_library.dialog._import_in_progress = False
        else:
            app.window.recording_studio._workspace_review_active = False

    qapp.processEvents()
    construct.assert_not_called()  # Completion alone must never replay a join.
    join()  # The same still-valid capability is explicitly requested again.
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    construct.assert_called_once()
    RoomBackend.instances[-1].emit(RoomState(1, "art", "paint_along"))
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    assert app._remote_invitation is None
    app._activate_remote_guest_route.assert_not_called()


@pytest.mark.parametrize("received", ["deferred", "absent", "stale"])
def test_native_deadline_fail_closes_without_blaming_local_work(
    qapp, controllers, monkeypatch, received,
):
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    source = app._remote_session
    room = app._room_participant
    generation = source.snapshot.generation
    app.window.recording_studio._workspace_review_active = received == "deferred"
    try:
        if received == "deferred":
            RoomBackend.instances[-1].emit(RoomState(1, "art", "paint_along"))
            drain(qapp, lambda: room.native_state is not None)
        elif received == "stale":
            room.native_source = source
            room.native_generation = generation
            room.native_state = RoomState(1, "art", "paint_along")
        # Advance only the room clock; retain real Qt/runtime scheduling.
        room.native_received_at = 100.0
        monkeypatch.setattr("webjam_qt.controllers.room_participant.time",
                            SimpleNamespace(monotonic=lambda: 106.0))
        room.apply_native(source, source.snapshot)
        room.check_native_timeout(source, generation, room.generation)
        assert source.snapshot.phase is RemoteSessionPhase.FAILED
        if received in {"absent", "stale"}:
            assert source.snapshot.error_code is RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED
        else:
            assert source.snapshot.error_code is not RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED
            assert "Update WebJam" not in app._remote_fresh_invitation_detail()
        if received == "deferred":
            assert "Local media work" in app._remote_fresh_invitation_detail()
            assert "Local media work" in app.window.session_hud._detail.text()
        assert not source.snapshot.invitation_retry_safe
        assert app._remote_invitation is None
        assert app._remote_invitation_requires_replacement
    finally:
        app.window.recording_studio._workspace_review_active = False
    room.apply_native(source, source.snapshot)
    app._retry_session()
    assert app._remote_session is source
    assert app.creator_profile.key == "music"
    assert room.native_applied is None
    app._activate_remote_guest_route.assert_not_called()


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

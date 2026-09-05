"""Native Art retries open a new attempt without reviving old callbacks."""
from __future__ import annotations

import threading
from dataclasses import replace
from unittest import mock

import pytest

from core.room_state import RoomIdentity, RoomState
from core.session_conductor import (
    ArtRoomState,
    CleanupState,
    EvidenceState,
    RecorderState,
    SessionConductorPhase,
    SessionRole as ConductorRole,
    derive_session_presentation,
)
from core.session_transport import SessionRole
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
)
from services.transport_runtime import TransportEvent
from webjam_qt.controllers.application_controller import ApplicationController
from tests.test_art_room_controller import (
    RoomBackend,
    controllers as _controllers_fixture,
    drain,
    qapp as _qapp_fixture,
    remote,
)

qapp = _qapp_fixture
controllers = _controllers_fixture


def room_event(generation, state):
    return TransportEvent(
        0, "room_state_received", code="ok", state="connected",
        mode="guest", generation=generation, room_state=state,
    )


@pytest.mark.parametrize("saved_profile", ["music", "art"])
def test_safe_guest_retry_enters_art_with_new_token_and_rejects_old_work(
    qapp, controllers, monkeypatch, saved_profile,
):
    entered, release = threading.Event(), threading.Event()
    attempts = []

    class RetryBackend(RoomBackend):
        instances = []

        def start_guest(self, invitation, *, generation):
            attempts.append(invitation)
            if len(attempts) == 1:
                raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)
            entered.set()
            assert release.wait(3)
            return super().start_guest(invitation, generation=generation)

    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RetryBackend,
    )
    app: ApplicationController = controllers(profile=saved_profile)
    app._activate_remote_guest_route = mock.Mock()
    app._launch_native_jamulus_for_startup = mock.Mock()
    invitation = remote()
    try:
        assert app.accept_invitation(invitation)
        drain(qapp, lambda: (
            app._remote_join_retry_pending() and not app._room_participant.probing
        ))
        assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
        failed_source = app._remote_session
        failed_token = app._session_conductor_token
        failed_facts = app._session_conductor_facts()
        assert failed_source.snapshot.invitation_retry_safe
        assert app._remote_invitation is invitation
        assert app.window.session_hud._action_kind in {"retry", "try_reconnect"}
        assert app.window.session_hud._action.isEnabled()
        assert not app.window.session_hud._action.isHidden()

        app.window.session_hud._action.click()
        drain(qapp, entered.is_set)
        source = app._remote_session
        token = app._session_conductor_token
        room = app._room_participant
        generation = room.generation
        assert source is not failed_source
        assert token.generation > failed_token.generation
        assert token.role is ConductorRole.GUEST

        # Repeated commands while this runtime is preparing keep one attempt.
        app._begin_remote_join()
        app._begin_remote_join()
        assert app._remote_session is source
        assert app._session_conductor_token == token
        assert room.generation == generation
        assert len(RetryBackend.instances) == 2
        release.set()
        drain(qapp, lambda: source.snapshot.phase is RemoteSessionPhase.CONNECTED)
        app._begin_remote_join()
        assert app._session_conductor_token == token
        assert room.generation == generation

        # The old runtime can use the same numeric wire generation. Its source
        # identity still cannot choose this attempt's profile or room state.
        room.receive_native(
            room_event(failed_source.snapshot.generation, RoomState(20, "music")),
            source=failed_source,
        )
        room.receive_native(
            room_event(source.snapshot.generation + 1, RoomState(21, "music")),
            source=source,
        )
        assert room.native_state is None
        RetryBackend.instances[-1].emit(RoomState(1, "art", "talk_and_make"))
        drain(qapp, lambda: app._last_session_conductor.phase is SessionConductorPhase.CONNECTED)
        accepted_state = room.native_state
        assert room.state is ArtRoomState.CONNECTED
        assert app.creator_profile.key == "art"
        assert app.settings.last_creator_profile_key == saved_profile
        assert app._last_session_conductor.title == "You’re in"
        assert app._remote_invitation is None

        app._on_remote_session_snapshot(failed_source.snapshot, source=failed_source)
        app._on_remote_session_snapshot(
            replace(source.snapshot, generation=source.snapshot.generation + 1,
                    phase=RemoteSessionPhase.FAILED,
                    error_code=RemoteSessionErrorCode.TRANSPORT_FAILED),
            source=source,
        )
        room.receive_native(
            room_event(source.snapshot.generation + 1, RoomState(22, "music")),
            source=source,
        )
        app._observe_session_conductor_facts(failed_facts, token=failed_token)
        assert room.native_state is accepted_state
        assert app._last_session_conductor.phase is SessionConductorPhase.CONNECTED
        assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
        assert app._session_conductor_token == token
        assert app._remote_session is source

        facts = app._session_conductor_facts()
        assert facts.local_participant is EvidenceState.NOT_STARTED
        assert derive_session_presentation(replace(facts, cleanup=CleanupState.ENDING)).phase is SessionConductorPhase.ENDING
        assert derive_session_presentation(replace(facts, recorder=RecorderState.RECORDING)).phase is SessionConductorPhase.RECORDING
        assert app.guest_peer is None
        assert not app._jamulus_connected
        app._activate_remote_guest_route.assert_not_called()
        app._launch_native_jamulus_for_startup.assert_not_called()
    finally:
        release.set()
        drain(qapp, lambda: app._remote_session.snapshot.phase is not RemoteSessionPhase.PREPARING)


def test_host_preparation_retry_opens_art_room_and_keeps_one_pending_attempt(
    qapp, controllers, monkeypatch,
):
    entered, release = threading.Event(), threading.Event()
    attempts = []

    class RetryHost:
        def __init__(self, **kwargs):
            attempts.append(self)
            if len(attempts) == 1:
                raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)
            self.room_identity = RoomIdentity("retry-room", "retry-room-key")
            self.snapshot = RemoteSessionSnapshot(
                RemoteSessionPhase.IDLE, SessionRole.HOST, 1,
            )
            self.connection_available = False
            self.invitation_available = True
            self.publish_room_state = mock.Mock(return_value=True)
            self.stop = mock.Mock(return_value=True)
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(
        "services.native_remote_transport.NativeHostTransportOwner", RetryHost,
    )
    monkeypatch.setattr(
        "services.native_remote_transport.reference_local_host_requested", lambda: True,
    )
    app: ApplicationController = controllers(profile="art", hosting=True)
    app.bridge.enable_remote_host_mode = mock.Mock()
    app._continue_startup_from_remote = mock.Mock()
    try:
        assert app.begin_startup_journey()
        drain(qapp, lambda: not app._remote_host_preparing)
        assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
        failed_token = app._session_conductor_token
        failed_facts = app._session_conductor_facts()
        assert app.window.session_hud._action.text() == "Try Again"
        app.window.session_hud._action.click()
        drain(qapp, entered.is_set)
        token = app._session_conductor_token
        room = app._room_participant
        generation = room.generation
        assert token.generation > failed_token.generation
        assert token.role is ConductorRole.HOST
        assert app.begin_startup_journey()
        assert app.begin_startup_journey()
        assert app._session_conductor_token == token
        assert room.generation == generation
        assert len(attempts) == 2

        release.set()
        drain(qapp, lambda: not app._remote_host_preparing)
        owner = attempts[-1]
        assert app._remote_invite_owner is owner
        assert room.state is ArtRoomState.WAITING
        assert app._last_session_conductor.title == "Your room is open"
        owner.connection_available = True
        owner.snapshot = replace(owner.snapshot, phase=RemoteSessionPhase.CONNECTED)
        app._on_remote_session_snapshot(owner.snapshot, source=owner)
        assert room.state is ArtRoomState.CONNECTED
        assert app._last_session_conductor.title == "You’re in"
        app._observe_session_conductor_facts(failed_facts, token=failed_token)
        assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
        assert app._session_conductor_token == token
        app._continue_startup_from_remote.assert_not_called()
        assert not app._jamulus_connected
        assert app.bridge.jamulus_process is None
    finally:
        release.set()
        drain(qapp, lambda: not app._remote_host_preparing)

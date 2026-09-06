"""Current guest discovery cannot inherit audio failure from a completed room."""

from __future__ import annotations

import threading
import time

import pytest

from core.room_state import RoomState
from core.session_conductor import (
    ArtRoomState, CleanupState, EvidenceState, FailureDisposition,
    MusicPathState, SessionConductorPhase,
)
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_profile_guidance_journey import (
    _assert_art_guidance, _assert_private_and_owned, _leave, guest, qapp,
)
from tests.test_art_room_controller import RoomBackend, arm_lan, drain, invitation, remote, state
from webjam_qt.controllers.application_controller import ApplicationController

# Explicit aliases make the real-controller fixtures available to pytest here.
assert guest and qapp


def _prepare_rejoin(pair, qapp, monkeypatch):
    """Leave a real Art room, then install a new owner without its profile."""
    pair.connect()
    retired = (pair.owner, pair.app._room_participant.generation,
               getattr(pair, "backend", None), getattr(pair, "invite", None))
    _leave(pair, qapp)
    app = pair.app
    assert app.bridge.jamulus_state == "Stopped"
    if pair.transport == "lan":
        pair.invite = invitation()
        arm_lan(monkeypatch, pair.invite)
        assert app.accept_invitation(pair.invite)
        pair.owner = app._room_participant.lan_guest
    else:
        assert app.accept_invitation(remote())
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        pair.owner = app._remote_session
        pair.backend = RoomBackend.instances[-1]
        pair.revision = 0
    # Runtime completion is published by its worker before the queued Qt
    # callback; wait for that real presentation rather than sampling old IDLE.
    drain(qapp, lambda: app._last_session_conductor.phase is SessionConductorPhase.JOINING)
    pair.generation = app._room_participant.generation
    pair.identity = app._reference_video_identity()
    assert app._room_participant.probing
    assert app.creator_profile.key == "music"
    assert pair.owner is not retired[0]
    return retired


def _pending(app):
    source = app._remote_session
    return app._guest_profile_probe_pending(source, getattr(source, "snapshot", None))


def _accept_profile(pair, qapp, profile):
    if pair.transport == "lan":
        pair.owner.client.state = lambda *args: state(pair.invite, profile)
        pair.owner.poll_once()
    else:
        pair.revision += 1
        pair.backend.emit(RoomState(
            pair.revision, profile, "talk_and_make" if profile == "art" else "",
        ))
    drain(qapp, lambda: not pair.app._room_participant.probing
          and pair.app.creator_profile.key == profile)
    pair.generation = pair.app._room_participant.generation
    pair.identity = pair.app._reference_video_identity()


def _assert_no_audio(facts, app):
    assert facts.local_participant is EvidenceState.NOT_STARTED
    assert not app.audio.connected
    assert not app._jamulus_connected
    assert app.bridge.jamulus_process is None


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_live_rejoin_probe_waits_for_profile_without_inheriting_stopped_audio(
    guest, qapp, monkeypatch, caplog, transport,
):
    pair = guest(transport=transport)
    _prepare_rejoin(pair, qapp, monkeypatch)
    app = pair.app
    facts = app._session_conductor_facts()
    assert _pending(app)
    assert facts.music_path is MusicPathState.NOT_STARTED
    assert facts.guest_enrollment is EvidenceState.IN_PROGRESS
    assert facts.failure is FailureDisposition.NONE
    assert app._last_session_conductor.phase is SessionConductorPhase.JOINING
    _assert_no_audio(facts, app)
    app._launch_native_jamulus_for_startup.assert_not_called()
    assert app._current_session_pulse.mode_key == "music"

    _accept_profile(pair, qapp, "art")

    assert not _pending(app)
    assert app._room_participant.state is ArtRoomState.CONNECTED
    _assert_art_guidance(pair)
    _assert_private_and_owned(pair, caplog)


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_authenticated_music_handoff_keeps_current_startup_failure_authoritative(
    guest, qapp, monkeypatch, transport,
):
    pair = guest(transport=transport)
    _prepare_rejoin(pair, qapp, monkeypatch)
    app = pair.app
    assert _pending(app)

    _accept_profile(pair, qapp, "music")
    drain(qapp, lambda: app._launch_native_jamulus_for_startup.call_count == 1)
    assert not _pending(app)
    attempt = app._startup_attempt
    assert attempt is not None
    assert app._session_conductor_facts().music_path is MusicPathState.STARTING
    _assert_no_audio(app._session_conductor_facts(), app)

    app._fail_startup_journey(attempt["generation"], "component_open_failed")

    assert attempt["phase"] == "failed"
    facts = app._session_conductor_facts()
    assert facts.failure is FailureDisposition.RETRYABLE
    assert facts.music_path is MusicPathState.FAILED
    assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
    assert app._current_session_pulse.mode_key == "music"
    _assert_no_audio(facts, app)


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_current_profile_deadline_or_terminal_owner_cannot_hide_failure(
    guest, qapp, monkeypatch, transport,
):
    pair = guest(transport=transport)
    _prepare_rejoin(pair, qapp, monkeypatch)
    app, room = pair.app, pair.app._room_participant
    assert _pending(app)

    if transport == "lan":
        room.lose_lan(pair.owner, pair.generation, True)
        assert room.lan_failed and room.probe_failed
    else:
        room.check_native_timeout(pair.owner, pair.owner.snapshot.generation, pair.generation)
        drain(qapp, lambda: app._remote_invitation_requires_replacement)
        assert pair.owner.snapshot.phase is RemoteSessionPhase.FAILED

    facts = app._session_conductor_facts()
    assert not _pending(app)
    assert facts.failure is not FailureDisposition.NONE
    assert facts.music_path is MusicPathState.FAILED
    assert app._last_session_conductor.phase in {
        SessionConductorPhase.FAILED, SessionConductorPhase.BLOCKED,
    }
    assert app.creator_profile.key == "music"
    _assert_no_audio(facts, app)


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_cleanup_gate_retains_owner_and_failure_during_profile_discovery(
    guest, qapp, monkeypatch, transport,
):
    pair = guest(transport=transport)
    _prepare_rejoin(pair, qapp, monkeypatch)
    app, room = pair.app, pair.app._room_participant

    app.audio.require_cleanup_retry(
        hosting=False, art_room=True, error="The previous room is still closing.",
    )
    facts = app._session_conductor_facts()
    assert not _pending(app)
    assert room.blocked
    assert facts.cleanup is CleanupState.FAILED
    assert facts.failure is FailureDisposition.RETRYABLE
    assert facts.music_path is MusicPathState.FAILED
    assert (room.lan_guest if transport == "lan" else app._remote_session) is pair.owner
    _assert_no_audio(facts, app)
    # Finish through the actual retained-owner retry before fixture shutdown.
    app.audio.retry_stop()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_retired_profile_and_loss_callbacks_cannot_replace_current_probe(
    guest, qapp, monkeypatch, transport,
):
    pair = guest(transport=transport)
    old_owner, old_generation, old_backend, old_invite = _prepare_rejoin(pair, qapp, monkeypatch)
    app, room = pair.app, pair.app._room_participant
    conductor_token = app.session_conductor.token

    if transport == "lan":
        room.receive_lan(old_owner, old_generation, state(old_invite))
        room.lose_lan(old_owner, old_generation, True)
    else:
        old_backend.emit(RoomState(999, "art", "talk_and_make"))
        room.check_native_timeout(old_owner, old_owner.snapshot.generation, old_generation)
        qapp.processEvents()

    assert _pending(app)
    assert room.generation == pair.generation
    assert app.session_conductor.token == conductor_token
    assert app.creator_profile.key == "music"
    assert app._last_session_conductor.phase is SessionConductorPhase.JOINING
    assert app._session_conductor_facts().music_path is MusicPathState.NOT_STARTED
    assert not app._remote_invitation_requires_replacement
    _accept_profile(pair, qapp, "art")
    _assert_art_guidance(pair)


@pytest.mark.parametrize("transport", ["lan", "native"])
@pytest.mark.parametrize("failure", ["Launch failed", "Not found", "Port in use"])
def test_real_bridge_launch_error_remains_failed_even_while_profile_is_pending(
    guest, qapp, monkeypatch, transport, failure,
):
    pair = guest(transport=transport)
    _prepare_rejoin(pair, qapp, monkeypatch)
    app = pair.app
    app.bridge._set_jamulus_state(failure)
    app._refresh_readiness()

    facts = app._session_conductor_facts()
    assert _pending(app)
    assert facts.music_path is MusicPathState.FAILED
    assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
    _assert_no_audio(facts, app)


def test_native_worker_stage_advance_during_facts_read_keeps_pending_owner_truth(
    guest, qapp, monkeypatch,
):
    pair = guest(transport="native")
    pair.connect()
    _leave(pair, qapp)
    app = pair.app
    assert app.bridge.jamulus_state == "Stopped"
    entered, release = threading.Event(), threading.Event()
    original_start = RoomBackend.start_guest

    def start(backend, invite, *, generation):
        entered.set()
        assert release.wait(2), "Controlled native worker was not released"
        return original_start(backend, invite, generation=generation)

    monkeypatch.setattr(RoomBackend, "start_guest", start)
    try:
        assert app.accept_invitation(remote())
        assert entered.wait(1)
        owner = app._remote_session
        assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
        original_probe = ApplicationController._guest_profile_probe_pending
        captured = []

        def advance(controller, source, snapshot):
            captured.append((source is owner, snapshot.phase, snapshot.generation))
            release.set()
            deadline = time.monotonic() + 1
            while owner.snapshot.phase is RemoteSessionPhase.PREPARING and time.monotonic() < deadline:
                time.sleep(.005)
            assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
            assert owner.snapshot.generation == snapshot.generation
            return original_probe(controller, source, snapshot)

        with monkeypatch.context() as scoped:
            scoped.setattr(ApplicationController, "_guest_profile_probe_pending", advance)
            facts = app._session_conductor_facts()
        assert captured == [(True, RemoteSessionPhase.PREPARING, owner.snapshot.generation)]
        assert facts.music_path is MusicPathState.NOT_STARTED
        assert facts.guest_enrollment is EvidenceState.IN_PROGRESS
        assert facts.failure is FailureDisposition.NONE
        _assert_no_audio(facts, app)
        drain(qapp, lambda: app._last_session_conductor.phase is SessionConductorPhase.JOINING)
    finally:
        release.set()

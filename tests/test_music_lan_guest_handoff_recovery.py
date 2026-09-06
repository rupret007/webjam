"""Music LAN recovery keeps discovery and live audio with their real owners."""
from __future__ import annotations

from contextlib import contextmanager
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.jamulus_rpc_client import JamulusRpcMonitorIdentity, JamulusRpcMonitorSnapshot
from core.session_conductor import (
    ArtRoomState, MusicPathState, SessionPrimaryAction,
)
from services.bridge_service import JamulusRecoverySnapshot, JamulusRpcFreshness
from services.lan_room_guest import LanRoomGuest
from tests.test_art_lan_retry import controllers as _controllers_fixture
from tests.test_art_room_controller import (
    arm_lan,
    drain,
    invitation,
    qapp as _qapp_fixture,
    state,
)
from webjam_qt.controllers.application_controller import ApplicationController

qapp = _qapp_fixture
controllers = _controllers_fixture
_PRIVATE_STOP = "PRIVATE_MUSIC_DISCOVERY_STOP"


@pytest.fixture
def discovery(controllers, monkeypatch):
    invite = invitation()
    arm_lan(monkeypatch, invite, "music")
    app: ApplicationController = controllers(invite=invite, profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    observer = room.lan_guest
    configure = Mock(wraps=app._configure_guest_peer)
    continuation = Mock(return_value=True)
    monkeypatch.setattr(app, "_configure_guest_peer", configure)
    monkeypatch.setattr(app, "begin_startup_journey", continuation)
    return SimpleNamespace(
        app=app, room=room, observer=observer, invite=invite,
        generation=room.generation, configure=configure,
        continuation=continuation,
    )


def test_authenticated_music_handoff_stops_observer_before_recording_owner(discovery):
    rig = discovery
    stop = rig.observer.stop
    events = []

    def confirmed_stop():
        events.append("observer stop")
        return stop()

    def configure(invite):
        assert rig.observer._stop.is_set()
        assert rig.room.lan_guest is None
        events.append("recording owner")
        return ApplicationController._configure_guest_peer(rig.app, invite)

    rig.observer.stop = confirmed_stop
    rig.configure.side_effect = configure
    rig.room.receive_lan(rig.observer, rig.generation, state(rig.invite, "music"))
    assert events == ["observer stop", "recording owner"]
    rig.continuation.assert_called_once_with()
    assert rig.app.creator_profile.key == "music"
    assert rig.app.guest_peer is not None
    assert rig.room.music_invite is rig.invite
    assert not rig.app.audio.connected and rig.app.bridge.jamulus_process is None


@pytest.mark.parametrize("stop_result", [False, None, "private exception"])
def test_unconfirmed_discovery_stop_keeps_one_reachable_leave_action(
    discovery, qapp, monkeypatch, caplog, stop_result,
):
    rig = discovery

    def uncertain_stop():
        if stop_result == "private exception":
            raise RuntimeError(_PRIVATE_STOP)
        return stop_result

    with monkeypatch.context() as patch:
        patch.setattr(rig.observer, "stop", uncertain_stop)
        rig.room.receive_lan(rig.observer, rig.generation, state(rig.invite, "music"))
    rig.configure.assert_not_called()
    rig.continuation.assert_not_called()
    assert rig.room.lan_guest is rig.observer
    assert rig.app._guest_invite is rig.invite
    assert rig.app.creator_profile.key == "art"
    assert rig.app.audio.cleanup_retry_required
    assert rig.app.audio._stop_art_room
    rig.app._refresh_readiness()
    guidance = rig.app._last_musician_guidance
    assert guidance.primary_action is SessionPrimaryAction.END_SESSION
    assert guidance.next_step == "Try Leave Room"
    assert rig.app.window.session_hud._action.text() == "Try Leave Room"
    assert _PRIVATE_STOP not in caplog.text
    rig.app.audio.retry_stop()
    drain(qapp, lambda: not rig.app.audio.stopping)
    assert not rig.app.audio.cleanup_retry_required
    assert rig.room.lan_guest is None
    rig.configure.assert_not_called()
    rig.continuation.assert_not_called()


@pytest.mark.parametrize("retirement", ["end", "quit", "replacement"])
def test_retiring_discovery_cannot_start_music_after_a_newer_owner_wins(
    discovery, monkeypatch, retirement,
):
    rig = discovery
    stop = rig.observer.stop
    entered = False
    replacement = []

    def retire_during_stop():
        nonlocal entered
        if entered:
            return stop()
        entered = True
        if retirement == "end":
            assert rig.app._stop_session_peer(clear_invite=True)
        elif retirement == "quit":
            assert rig.app.shutdown()
        else:
            assert rig.app._stop_session_peer(clear_invite=True)
            new_invite = invitation()
            rig.app._guest_invite = new_invite
            assert rig.room.start_lan_guest(new_invite)
            replacement.append((rig.room.lan_guest, new_invite, rig.room.generation))
        return stop()

    with monkeypatch.context() as patch:
        patch.setattr(rig.observer, "stop", retire_during_stop)
        rig.room.receive_lan(rig.observer, rig.generation, state(rig.invite, "music"))
    rig.configure.assert_not_called()
    rig.continuation.assert_not_called()
    assert rig.app.creator_profile.key == "art"
    assert rig.app.guest_peer is None
    if retirement == "replacement":
        owner, invite, generation = replacement[0]
        assert isinstance(owner, LanRoomGuest)
        assert rig.room.lan_guest is owner and rig.room.generation == generation
        assert rig.app._guest_invite is invite
        assert not owner._stop.is_set()
        assert rig.room.probing and rig.room.state is ArtRoomState.STARTING
    else:
        assert rig.room.lan_guest is None
        assert rig.app._guest_invite is None
        assert rig.room.state is ArtRoomState.NONE
        assert not rig.room.probing
        if retirement == "quit":
            assert rig.app._shutdown


@pytest.mark.parametrize("action", ["retry", "try_reconnect", "check_session"])
def test_running_music_guest_retry_rechecks_audio_owner_instead_of_host_invite(
    controllers, monkeypatch, action,
):
    app: ApplicationController = controllers(profile="music")
    process = Mock()
    process.poll.return_value = None
    with monkeypatch.context() as patch:
        patch.setattr(app.bridge, "jamulus_process", process)
        patch.setattr(app.bridge, "jamulus_state", "Running")
        patch.setattr(app.bridge, "jamulus_launch_intended", True)
        patch.setattr(app, "_on_reconnect_tick", Mock())
        patch.setattr(app, "_current_invite_url", Mock(return_value=""))
        patch.setattr(app.window, "flash_message", Mock())
        patch.setattr(app, "_begin_explicit_startup_journey", Mock())
        app.audio.connected = False
        app._on_conductor_action_requested(action)
        app._on_reconnect_tick.assert_called_once_with()
        app._current_invite_url.assert_not_called()
        app._begin_explicit_startup_journey.assert_not_called()
        assert not app.audio.connected
        assert not any("band network" in str(call)
                       for call in app.window.flash_message.call_args_list)


@pytest.fixture
def supervised_guest(discovery, monkeypatch):
    """Keep the real supervisor/Bridge; replace only native process boundaries."""
    rig = discovery
    rig.room.receive_lan(rig.observer, rig.generation, state(rig.invite, "music"))
    app, guest = rig.app, rig.app.guest_peer
    assert guest is not None

    @contextmanager
    def supervise(*, rpc_available=True, rpc_age=31.0):
        process = Mock()
        process.pid = 7331
        process.poll.return_value = None
        capture = SimpleNamespace(stop=Mock(), abort=Mock())
        observed = time.monotonic()
        identity = JamulusRpcMonitorIdentity(
            monitor_epoch=2, process_generation=17, process_id=process.pid,
        )
        monitor = JamulusRpcMonitorSnapshot(
            identity=identity, running=True,
            available=rpc_available, authenticated=rpc_available,
            last_activity_at=observed - rpc_age,
            last_activity_age_seconds=rpc_age,
        )
        with monkeypatch.context() as patch:
            fields = {
                "jamulus_process": process,
                "jamulus_state": "Running",
                "jamulus_launch_intended": True,
                "_jamulus_process_generation": identity.process_generation,
                "_jamulus_process_started_at": observed - 120.0,
                "_jamulus_process_recovery_generation": 4,
                "_jamulus_recovery_generation": 4,
                "_jamulus_recovery_active": True,
                "_jamulus_recovery_exhausted": False,
                "jamulus_reconnect_attempts": 0,
                "jamulus_next_reconnect_at": 0.0,
                "jamulus_reconnect_inflight": False,
            }
            for name, value in fields.items():
                patch.setattr(app.bridge, name, value)
            patch.setattr(app.jamulus, "rpc_monitor_snapshot_for", Mock(return_value=monitor))
            patch.setattr(app.bridge, "launch_jamulus", Mock(return_value=True))
            patch.setattr(app.bridge, "attempt_auto_reconnects", Mock(wraps=app.bridge.attempt_auto_reconnects))
            patch.setattr(app.bridge, "stop_jamulus", Mock(return_value=True))
            patch.setattr(guest, "_capture", capture)
            patch.setattr(guest, "_active_take_id", "PRIVATE_RETAINED_TAKE")
            patch.setattr(guest, "stop", Mock(wraps=guest.stop))
            patch.setattr(app, "_configure_guest_peer", Mock(wraps=app._configure_guest_peer))
            patch.setattr(app, "_begin_explicit_startup_journey", Mock())
            patch.setattr(app.window, "flash_message", Mock())
            patch.setattr(app.audio, "connected", False)
            patch.setattr(app.audio, "recovering", False)
            patch.setattr(app, "_last_reconnect_tick_monotonic", observed)
            patch.setattr(app, "_last_reconnect_tick_wall", time.time())
            current = app.bridge.jamulus_recovery_snapshot()
            assert isinstance(current, JamulusRecoverySnapshot)
            assert current.rpc_freshness is JamulusRpcFreshness.STALE
            assert current.process_id == process.pid and current.process_alive
            yield SimpleNamespace(
                app=app, guest=guest, invite=rig.invite, capture=capture,
                process=process, generation=current.generation,
                recovery_generation=current.recovery_generation,
            )

    return supervise


def _assert_guest_ownership_retained(rig):
    assert rig.app.guest_peer is rig.guest
    assert rig.app._guest_invite is rig.invite
    assert rig.app._room_participant.music_invite is rig.invite
    assert rig.guest._capture is rig.capture
    assert rig.guest.active_take_id == "PRIVATE_RETAINED_TAKE"
    rig.guest.stop.assert_not_called()
    rig.capture.stop.assert_not_called()
    rig.capture.abort.assert_not_called()
    rig.app._configure_guest_peer.assert_not_called()
    rig.app._begin_explicit_startup_journey.assert_not_called()
    assert not rig.app.audio.connected


@pytest.mark.parametrize("rpc_available", [True, False], ids=["stale-rpc", "dead-rpc"])
def test_music_retry_runs_real_bounded_supervision_and_preserves_guest_media(
    supervised_guest, caplog, rpc_available,
):
    with supervised_guest(rpc_available=rpc_available) as rig:
        app = rig.app
        for action in ("try_reconnect", "retry", "check_session"):
            app._on_conductor_action_requested(action)
        app.bridge.attempt_auto_reconnects.assert_called()
        # The real Bridge reserves only one attempt; repeated clicks cannot
        # start a second native worker while the accepted request is in flight.
        app.bridge.launch_jamulus.assert_called_once_with(
            manual=False, reconnect=True, force_restart=True,
        )
        current = app.bridge.jamulus_recovery_snapshot()
        assert current.inflight and current.attempts_started == 1
        assert current.generation == rig.generation
        assert current.recovery_generation == rig.recovery_generation
        assert app.audio.recovering
        assert app._session_conductor_facts().music_path is MusicPathState.RECONNECTING
        _assert_guest_ownership_retained(rig)
        assert "PRIVATE_RETAINED_TAKE" not in caplog.text
        assert rig.invite.invite_token not in caplog.text
        assert rig.invite.session_id not in caplog.text
        assert rig.invite.host not in caplog.text


@pytest.mark.parametrize("hold", ["end", "cleanup", "quit", "invitation-switch", "exhausted-latch"])
def test_music_retry_respects_existing_lifecycle_holds(
    supervised_guest, monkeypatch, hold,
):
    with supervised_guest() as rig, monkeypatch.context() as patch:
        app = rig.app
        target, field = {
            "end": (app.audio, "stopping"),
            "cleanup": (app.audio, "cleanup_retry_required"),
            "quit": (app, "_shutdown_cleanup_pending"),
            "invitation-switch": (app, "_invite_switch_in_flight"),
            "exhausted-latch": (app, "_reconnect_gave_up"),
        }[hold]
        patch.setattr(target, field, True)
        app._on_conductor_action_requested("try_reconnect")
        app.bridge.launch_jamulus.assert_not_called()
        app.bridge.attempt_auto_reconnects.assert_not_called()
        _assert_guest_ownership_retained(rig)


def test_music_retry_at_fifth_failure_uses_existing_retirement_without_sixth_attempt(
    supervised_guest, monkeypatch,
):
    with supervised_guest() as rig, monkeypatch.context() as patch:
        app = rig.app
        patch.setattr(app.bridge, "jamulus_reconnect_attempts", 5)
        patch.setattr(app.bridge, "_jamulus_recovery_exhausted", True)
        patch.setattr(app, "_retire_primary_after_recovery_exhaustion", Mock(return_value=True))
        app._on_conductor_action_requested("try_reconnect")
        app._retire_primary_after_recovery_exhaustion.assert_called_once_with(unresponsive=True)
        app.bridge.launch_jamulus.assert_not_called()
        app.bridge.attempt_auto_reconnects.assert_not_called()
        assert app.bridge.jamulus_recovery_snapshot().attempts_started == 5
        _assert_guest_ownership_retained(rig)


def test_retryable_native_enrollment_keeps_its_existing_owner(supervised_guest, monkeypatch):
    with supervised_guest() as rig, monkeypatch.context() as patch:
        app = rig.app
        runtime = SimpleNamespace(snapshot=SimpleNamespace(invitation_retry_safe=True))
        invitation_owner = object()
        patch.setattr(app, "_remote_session", runtime)
        patch.setattr(app, "_remote_invitation", invitation_owner)
        patch.setattr(app, "_begin_remote_join", Mock())
        patch.setattr(app, "_on_reconnect_tick", Mock(wraps=app._on_reconnect_tick))
        app._on_conductor_action_requested("try_reconnect")
        app._begin_remote_join.assert_called_once_with()
        app._on_reconnect_tick.assert_not_called()
        app.bridge.launch_jamulus.assert_not_called()
        assert app._remote_session is runtime
        assert app._remote_invitation is invitation_owner
        _assert_guest_ownership_retained(rig)


@pytest.mark.parametrize("retirement", ["end", "replacement"])
def test_music_configuration_does_not_overwrite_invitation_retired_during_peer_stop(
    discovery, monkeypatch, retirement,
):
    rig = discovery
    app = rig.app
    original_stop = app.host_peer.stop
    entered = False
    replacement = []

    def retire_during_configuration_stop():
        nonlocal entered
        if entered:
            return original_stop()
        entered = True
        assert app._stop_session_peer(clear_invite=True)
        if retirement == "replacement":
            new_invite = invitation()
            app._guest_invite = new_invite
            assert rig.room.start_lan_guest(new_invite)
            replacement.append((rig.room.lan_guest, new_invite, rig.room.generation))
        return original_stop()

    with monkeypatch.context() as patch:
        patch.setattr(app.host_peer, "stop", retire_during_configuration_stop)
        rig.room.receive_lan(rig.observer, rig.generation, state(rig.invite, "music"))
    rig.configure.assert_called_once_with(rig.invite)
    rig.continuation.assert_not_called()
    assert app.creator_profile.key == "art"
    assert app.guest_peer is None
    if retirement == "replacement":
        owner, invite, generation = replacement[0]
        assert rig.room.lan_guest is owner and rig.room.generation == generation
        assert app._guest_invite is invite
        assert not owner._stop.is_set()
    else:
        assert rig.room.lan_guest is None
        assert app._guest_invite is None


def test_music_guest_cleanup_guidance_matches_the_retained_leave_owner(supervised_guest, monkeypatch):
    with supervised_guest() as rig:
        app = rig.app
        app.audio.require_cleanup_retry(hosting=False, art_room=False, error="Room still closing")
        app._update_session_hud()
        guidance = app._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.END_SESSION
        assert guidance.next_step == "Try Leave Jam"
        assert app.window.session_strip._audio_button.accessibleName() == guidance.next_step
        assert app.window.session_hud._action.isHidden()
        retry = Mock()
        monkeypatch.setattr(app.audio, "retry_stop", retry)
        app._on_conductor_action_requested("end_session")
        retry.assert_called_once_with()
        app.bridge.launch_jamulus.assert_not_called()

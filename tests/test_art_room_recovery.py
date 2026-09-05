"""Faults after entry must leave one retry and a reachable concrete owner."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from core.room_state import RoomIdentity
from core.session_conductor import SessionPrimaryAction
from core.session_transport import SessionRole
from services.remote_session_runtime import (
    RemoteSessionErrorCode, RemoteSessionPhase, RemoteSessionSnapshot,
)
from tests.test_art_room_controller import (
    arm_lan,
    controllers as _controllers_fixture,
    drain,
    invitation,
    qapp as _qapp_fixture,
    state,
)

qapp = _qapp_fixture
controllers = _controllers_fixture


@pytest.mark.parametrize("first_stop_succeeds", [True, False])
def test_end_during_host_construction_never_installs_a_late_room(
    qapp, controllers, monkeypatch, first_stop_succeeds,
):
    entered, release = threading.Event(), threading.Event()
    made = []

    class PendingOwner:
        def __init__(self, **kwargs):
            self.room_identity = RoomIdentity("pending", "pending-key")
            self.snapshot = RemoteSessionSnapshot(RemoteSessionPhase.IDLE, SessionRole.HOST, 1)
            self.connection_available = False
            self.invitation_available = True
            self.publish_room_state = mock.Mock(return_value=True)
            self.stop = mock.Mock(side_effect=[first_stop_succeeds, True, True])
            made.append(self)
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr("services.native_remote_transport.NativeHostTransportOwner", PendingOwner)
    app = controllers(profile="art", hosting=True)
    app.bridge.enable_remote_host_mode = mock.Mock()
    app.begin_startup_journey = mock.Mock()
    try:
        app._begin_remote_host()
        drain(qapp, entered.is_set)
        app.audio._begin_session_stop(True, art_room=True)
        drain(qapp, lambda: not app.audio.stopping)
        assert app.audio.cleanup_retry_required
        assert app._remote_host_preparing
        release.set()
        drain(qapp, lambda: not app._remote_host_preparing)
        owner = made[0]
        owner.stop.assert_called_once_with()
        owner.publish_room_state.assert_not_called()
        app.bridge.enable_remote_host_mode.assert_not_called()
        app.begin_startup_journey.assert_not_called()
        assert app._remote_invite_owner is (None if first_stop_succeeds else owner)
        app.audio.retry_stop()
        drain(qapp, lambda: not app.audio.stopping)
        assert not app.audio.cleanup_retry_required
        assert app._remote_invite_owner is None
        assert app._remote_session is None
    finally:
        release.set()


def test_failed_native_host_guidance_dispatches_real_cleanup(controllers):
    app = controllers(profile="art", hosting=True)
    owner = SimpleNamespace(
        snapshot=RemoteSessionSnapshot(
            RemoteSessionPhase.FAILED, SessionRole.HOST, 1,
            error_code=RemoteSessionErrorCode.TRANSPORT_FAILED,
        ),
        invitation_available=False, connection_available=False,
        room_identity=None, stop=lambda: True,
    )
    app._remote_session = app._remote_invite_owner = owner
    app._room_participant.role = "host"
    app._on_remote_session_snapshot(owner.snapshot, source=owner)
    guidance = app._last_guidance_display_override
    assert guidance.primary_action is SessionPrimaryAction.END_SESSION
    assert "End Room" in guidance.message
    app.audio.stop = mock.Mock()
    app._on_conductor_action_requested(app._conductor_action_kind(guidance.primary_action))
    app.audio.stop.assert_called_once_with()


def test_outbound_room_labels_normalize_macos_unicode(controllers):
    from webjam_qt.controllers.room_participant import NativeRoomPublisher

    app = controllers(profile="art", hosting=True)
    published = []
    owner = SimpleNamespace(room_identity=RoomIdentity("room", "key"),
                            publish_room_state=lambda value: (published.append(value) or True))
    app._remote_invite_owner = owner
    publisher = NativeRoomPublisher(app, owner)
    publisher.publish_reference_video_state(
        state="ready", shared=True, source_display_name="Cafe\u0301.mp4",
        identity_digest="a" * 64, duration_s=120.0,
    )
    assert published[-1].reference_video.source_display_name == "Café.mp4"
    publisher.publish_shared_canvas_state(
        shared=True, join_url="drawpile://example.com/room",
        server_label="Cafe\u0301", session_label="Cre\u0301ation",
    )
    assert published[-1].shared_canvas.server_label == "Café"
    assert published[-1].shared_canvas.session_label == "Création"
    app._remote_invite_owner = None


def test_real_borrowed_profile_restore_retries_failed_metadata_step(
    controllers, monkeypatch,
):
    app = controllers(profile="music")
    app.window.session_canvas.set_notes("Saved Music notes")
    app._apply_creator_profile_key("art", host_owned=True)
    app.window.session_canvas.set_notes("Art notes before cleanup")
    app.window.session_strip.set_session_title("Borrowed Art room")
    app._persistence.mark_title_borrowed("Borrowed Art room")
    app.audio._stop_art_room = True
    app.audio.stopping = True
    restore_metadata = mock.Mock(side_effect=[OSError("temporary storage failure"), None])
    monkeypatch.setattr(app._persistence, "_load_session_metadata", restore_metadata)
    assert app._stop_session_peer(clear_invite=True)
    pending = app._pending_room_profile_restore
    with pytest.raises(OSError, match="temporary storage failure"):
        app._finish_art_room_profile_restore()
    assert app._pending_room_profile_restore is pending
    assert app.creator_profile.key == "art"
    assert app._creator_profile_host_owned
    assert app._persistence.profile_key == "art"
    assert app._persistence._borrowed_title == "Borrowed Art room"
    assert app.window.session_strip.current_title() == "Borrowed Art room"
    assert app.window.session_canvas.current_notes() == "Art notes before cleanup"
    # A retryable failure leaves Notes editable. This edit must remain an Art
    # draft rather than overwriting the Music notes loaded during the attempt.
    app.window.session_canvas.set_notes("Art notes revised while cleanup waits")
    assert app._persistence._save_notes_only()
    assert app._persistence._settled_notes["art"] == "Art notes revised while cleanup waits"
    assert app._persistence._settled_notes["music"] == "Saved Music notes"
    assert app._stop_session_peer(clear_invite=True)
    assert app._pending_room_profile_restore is pending
    app._finish_art_room_profile_restore()
    assert app._pending_room_profile_restore is None
    assert app.creator_profile.key == "music"
    assert not app._creator_profile_host_owned
    assert app._persistence.profile_key == "music"
    assert app._persistence._borrowed_title is None
    assert app.window.session_canvas.current_notes() == "Saved Music notes"
    assert app._persistence._settled_notes["art"] == "Art notes revised while cleanup waits"
    assert restore_metadata.call_count == 2
    app.audio.stopping = False


def test_music_profile_probe_cleanup_shares_working_room_action_everywhere(
    qapp, controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite, "music")
    app = controllers(invite=invite, profile="music")
    assert app.begin_startup_journey()
    room = app._room_participant
    observer = room.lan_guest
    observer.stop = mock.Mock(side_effect=[False, True])
    app.recording.stop_server_recording_for_shutdown = mock.Mock(
        wraps=app.recording.stop_server_recording_for_shutdown,
    )
    app.recording.on_audio_session_stopped = mock.Mock(
        wraps=app.recording.on_audio_session_stopped,
    )
    app._launch_native_jamulus_for_startup = mock.Mock()

    room.receive_lan(observer, room.generation, state(invite, "music"))
    app._refresh_readiness()

    assert app.audio.cleanup_retry_required and app.audio._stop_art_room
    assert app.creator_profile.key == "music"
    assert app.settings.last_creator_profile_key == "music"
    assert app.guest_peer is None
    assert not app._jamulus_connected
    guidance = app._last_musician_guidance
    assert guidance.primary_action is SessionPrimaryAction.END_SESSION
    assert guidance.next_step == "Try Leave Room"
    assert app.window.session_canvas._current_guidance is guidance
    assert app.window.session_hud._action.text() == "Try Leave Room"
    app._refresh_pocket_projection()
    pocket = app._get_pocket_projection()
    assert pocket.primary_action is SessionPrimaryAction.END_SESSION
    assert pocket.primary_enabled
    assert pocket.cue == guidance.title

    retry = mock.Mock(wraps=app.audio.retry_stop)
    monkeypatch.setattr(app.audio, "retry_stop", retry)
    app.window.session_hud._action.click()
    drain(qapp, lambda: not app.audio.stopping)

    retry.assert_called_once_with()
    assert not app.audio.cleanup_retry_required
    assert room.lan_guest is None
    app._launch_native_jamulus_for_startup.assert_not_called()
    app.recording.stop_server_recording_for_shutdown.assert_not_called()
    app.recording.on_audio_session_stopped.assert_not_called()

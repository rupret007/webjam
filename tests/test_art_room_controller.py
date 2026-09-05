"""Real controller entry/follow evidence, separate from Music audio proofs."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.network_invite import BandInvite
from core.remote_invitation import issue_remote_invitation
from core.room_state import RoomIdentity, RoomState
from core.session_conductor import ArtRoomState, EvidenceState, SessionConductorPhase
from core.session_transfer import RecordingSignal, SessionCredentials, SessionStateSnapshot
from core.session_transport import ConnectionQuality, TransportPath
from core.settings import AppSettings
from services.remote_session_runtime import RemoteGuestConnection, RemoteSessionPhase
from services.transport_runtime import TransportEvent
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controllers(qapp, tmp_path):
    made = []

    def create(*, invite=None, profile="music", hosting=False):
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"),
            host_server_enabled=hosting,
            last_creator_profile_key=profile,
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Making room",
        )
        app = ApplicationController(window, settings=settings, session_invite=invite)
        made.append(app)
        return app

    yield create
    for app in reversed(made):
        app.shutdown()
        app.window.deleteLater()
    qapp.processEvents()


def drain(qapp, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


def invitation():
    credentials = SessionCredentials.create()
    return BandInvite("192.168.1.20", session_id=credentials.session_id,
                      peer_port=22125, invite_token=credentials.invite_token)


def state(invite, profile="art"):
    return SessionStateSnapshot(invite.session_id, 0, RecordingSignal.IDLE,
                                creator_profile_key=profile)


def arm_lan(monkeypatch, invite, profile="art"):
    monkeypatch.setattr("services.lan_room_guest.LanRoomGuest.start", lambda owner: None)
    monkeypatch.setattr("services.lan_room_guest.SessionPeerClient", lambda *a, **k: SimpleNamespace(
        enroll=lambda *a: object(), state=lambda *a: state(invite, profile),
    ))


def test_cold_music_guest_enters_art_from_first_idle_state_without_audio(
    qapp, controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    recording_guest = mock.Mock(side_effect=AssertionError("Art must not construct a recorder"))
    permission = mock.Mock(side_effect=AssertionError("Art must not probe microphone permission"))
    monkeypatch.setattr("webjam_qt.controllers.application_controller.GuestPeerSession", recording_guest)
    monkeypatch.setattr("webjam_qt.platform_permissions.microphone_permission_status", permission)
    app = controllers(invite=invite)
    app._launch_native_jamulus_for_startup = mock.Mock()
    app._start_hosted_server_for_startup = mock.Mock()
    assert app.guest_peer is None
    assert app.begin_startup_journey()
    owner = app._room_participant.lan_guest
    owner.poll_once()
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    assert app.creator_profile.key == "art"
    assert app.settings.last_creator_profile_key == "music"
    assert app._last_session_conductor.phase is SessionConductorPhase.CONNECTED
    assert app._last_session_conductor.title == "You’re in"
    assert app._session_conductor_facts().local_participant is EvidenceState.NOT_STARTED
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    permission.assert_not_called()
    recording_guest.assert_not_called()
    assert app._reference_video_identity() == ("guest", invite.session_id, invite.invite_token)


def test_authenticated_music_discovery_hands_off_to_existing_audio_start(
    qapp, controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite, "music")
    app = controllers(invite=invite, profile="art")
    launch = mock.Mock()
    app._launch_native_jamulus_for_startup = launch
    assert app.begin_startup_journey()
    observer = app._room_participant.lan_guest
    observer.poll_once()
    drain(qapp, lambda: launch.call_count == 1)
    assert app.creator_profile.key == "music"
    assert app.guest_peer is not None
    assert app._room_participant.lan_guest is None
    assert observer._stop.is_set()
    assert not app._jamulus_connected


def test_lan_loss_and_retired_callbacks_cannot_claim_connected(
    qapp, controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite)
    app.begin_startup_journey()
    room = app._room_participant
    observer, generation = room.lan_guest, room.generation
    observer.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    room.lose_lan(observer, generation, False)
    assert app._last_session_conductor.phase is SessionConductorPhase.RECONNECTING
    observer.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    assert app._stop_session_peer(clear_invite=True)
    room.receive_lan(observer, generation, state(invite))
    assert room.state is ArtRoomState.NONE
    assert app._reference_video_identity() == ("", "", "")
    assert app.creator_profile.key == "music"


class RoomBackend:
    instances = []

    def __init__(self, *, on_room_state, schedule_callback, **kwargs):
        self.receive = on_room_state
        self.schedule = schedule_callback
        self.connection_available = False
        self.room_identity = None
        self.generation = 0
        self.instances.append(self)

    def start_guest(self, invite, *, generation):
        self.generation = generation
        self.room_identity = RoomIdentity.from_invitation(invite)
        self.connection_available = True
        return RemoteGuestConnection(43123, TransportPath.SECURE_RELAY,
                                     ConnectionQuality.UNKNOWN, generation)

    def emit(self, room):
        event = TransportEvent(0, "room_state_received", code="ok", state="connected",
                               mode="guest", generation=self.generation, room_state=room)
        self.schedule(lambda: self.receive(event))

    def stop(self):
        self.connection_available = False
        self.room_identity = None
        return True


def remote():
    return issue_remote_invitation("reference-local", allowed_profiles={"reference-local"},
                                   host_spki_sha256=bytes.fromhex("44" * 32)).invitation


def test_native_transport_waits_for_host_profile_then_enters_art(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    assert app._room_participant.probing
    app._activate_remote_guest_route.assert_not_called()
    backend.emit(RoomState(1, "art", "paint_along"))
    drain(qapp, lambda: app.creator_profile.key == "art")
    assert app._last_session_conductor.title == "You’re in"
    assert app.creator_start.key == "paint_along"
    assert app.settings.last_creator_profile_key == "music"
    assert app._reference_video_identity()[0] == "guest"
    app._activate_remote_guest_route.assert_not_called()
    assert app.bridge.jamulus_process is None


def test_native_missing_profile_has_bounded_update_rejoin_action(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    source = app._remote_session
    room = app._room_participant
    room.check_native_timeout(source, source.snapshot.generation, room.generation)
    drain(qapp, lambda: app._remote_invitation_requires_replacement)
    assert "Update WebJam" in app._remote_fresh_invitation_detail()
    app._activate_remote_guest_route.assert_not_called()


def test_native_ui_ticks_do_not_replay_cached_creative_receipts(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = controllers()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    observe = mock.Mock(wraps=app._room_participant.observe_creative_state)
    app._room_participant.observe_creative_state = observe
    backend = RoomBackend.instances[-1]
    backend.emit(RoomState(1, "art", "paint_along"))
    drain(qapp, lambda: observe.call_count == 1)
    app._tick_creator_start()
    app._refresh_readiness()
    app._on_remote_session_snapshot(app._remote_session.snapshot, source=app._remote_session)
    assert observe.call_count == 1
    backend.emit(RoomState(2, "art", "paint_along"))
    drain(qapp, lambda: observe.call_count == 2)


def test_actual_strip_end_button_uses_room_cleanup_without_audio(controllers):
    app = controllers(profile="art")
    app._room_participant.role = "guest"
    app._room_participant.state = ArtRoomState.CONNECTED
    app.audio.stop = mock.Mock()
    app.begin_startup_journey = mock.Mock()
    app.window.session_strip.launch_audio_requested.emit()
    app.audio.stop.assert_called_once_with()
    app.begin_startup_journey.assert_not_called()


def test_actual_strip_retry_keeps_art_cleanup_as_the_one_action(controllers):
    from core.session_conductor import CleanupState, SessionPrimaryAction

    app = controllers(profile="art")
    app.audio._stop_art_room = True
    app.audio._stop_hosting = False
    app.audio.cleanup_retry_required = True
    app.audio.retry_stop = mock.Mock()
    app._refresh_readiness()
    assert app._session_conductor_facts().cleanup is CleanupState.FAILED
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.END_SESSION
    assert app._last_session_conductor.action_label == "Try Leave Room"
    app.window.session_strip.launch_audio_requested.emit()
    app.audio.retry_stop.assert_called_once_with()
    app.audio.cleanup_retry_required = False


def test_end_fences_first_lan_profile_before_worker_reaches_peer_stop(
    controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite)
    app.begin_startup_journey()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    app.audio.stopping = True
    app._configure_guest_peer = mock.Mock()
    room.receive_lan(owner, generation, state(invite, "music"))
    app._configure_guest_peer.assert_not_called()
    assert app.creator_profile.key == "music"
    assert room.probing
    app.audio.stopping = False


def test_native_shared_canvas_follows_without_launch_and_withdraws(
    qapp, controllers, monkeypatch,
):
    from core.session_transfer import SharedCanvasSessionSnapshot
    from core.shared_canvas import SharedCanvasFollowState

    launcher = mock.Mock()
    launcher.available.return_value = True
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = controllers()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    backend.emit(RoomState(1, "art", "talk_and_make", shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True, join_url="drawpile://example.com/room?password=private-canvas",
        server_label="Shared studio", session_label="Sketch",
    )))
    drain(qapp, lambda: app.creator_profile.key == "art")
    canvas = app._shared_canvas_coordinator()
    assert canvas.follow_snapshot.state is SharedCanvasFollowState.READY
    assert canvas.follow_snapshot.can_open
    launcher.open_canvas.assert_not_called()
    assert "private-canvas" not in repr(app._last_musician_guidance)
    backend.emit(RoomState(2, "art", "talk_and_make", shared_canvas=SharedCanvasSessionSnapshot(generation=2)))
    drain(qapp, lambda: canvas.follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS)
    launcher.open_canvas.assert_not_called()


def test_reset_retires_old_media_before_replacement_invite(controllers):
    from core.session_transport import SessionRole
    from services.remote_session_runtime import RemoteSessionSnapshot

    app = controllers(profile="art", hosting=True)
    old_video = mock.Mock()
    old_canvas = mock.Mock()
    old_clock = mock.Mock()
    app._reference_video = old_video
    app._shared_canvas = old_canvas
    app._room_clock = old_clock
    identity = RoomIdentity("first-room", "first-key")
    owner = SimpleNamespace(
        room_identity=identity, invitation_available=True, connection_available=False,
        snapshot=RemoteSessionSnapshot(RemoteSessionPhase.IDLE, SessionRole.HOST, 1),
        publish_room_state=mock.Mock(return_value=True), stop=lambda: True,
    )

    def reset():
        old_video.end.assert_called_once_with()
        old_canvas.end.assert_called_once_with()
        old_clock.end.assert_called_once_with()
        owner.room_identity = RoomIdentity("next-room", "next-key")
        owner.snapshot = RemoteSessionSnapshot(RemoteSessionPhase.IDLE, SessionRole.HOST, 2)

    owner.reset = reset
    app._remote_invite_owner = app._remote_session = owner
    app._room_participant.role = "host"
    app._reset_remote_invite()
    assert app._reference_video is None
    fresh = owner.publish_room_state.call_args.args[0]
    assert not fresh.reference_video.shared and not fresh.shared_canvas.shared
    assert app._room_participant.publisher.identity == owner.room_identity


class SilentTestPlayer:
    def __init__(self):
        self.surface = None
        self.position = 0.0
        self.state = "idle"
        self.muted = False

    def set_muted(self, muted):
        self.muted = bool(muted)

    def load(self, path):
        self.state = "ready"
        return 120.0

    def play(self):
        self.state = "playing"

    def pause(self):
        self.state = "paused"

    def stop(self):
        self.state = "ready"
        self.position = 0.0

    def seek(self, position_s):
        self.position = float(position_s)

    def position_s(self):
        return self.position

    def close(self):
        self.state = "closed"


def test_native_room_reuses_actual_video_matching_and_silent_follow(
    qapp, controllers, monkeypatch, tmp_path,
):
    from core.reference_video import ReferenceVideoError, ReferenceVideoFollowState
    from core.session_transport import SessionRole
    from services.remote_session_runtime import RemoteSessionSnapshot

    players = []

    def player_factory(window):
        player = SilentTestPlayer()
        players.append(player)
        return player

    monkeypatch.setattr("webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", player_factory)
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    invited = remote()
    guest = controllers()
    assert guest.accept_invitation(invited)
    drain(qapp, lambda: guest._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    host = controllers(profile="art", hosting=True)
    host._room_participant.role = "host"
    host._room_participant.state = ArtRoomState.CONNECTED
    owner = SimpleNamespace(
        room_identity=RoomIdentity.from_invitation(invited), invitation_available=False,
        connection_available=True,
        snapshot=RemoteSessionSnapshot(RemoteSessionPhase.CONNECTED, SessionRole.HOST, 1),
        publish_room_state=lambda value: (backend.emit(value) or True), stop=lambda: True,
    )
    host._remote_invite_owner = host._remote_session = owner
    publish = host._room_host_publisher()
    publish.publish()
    drain(qapp, lambda: guest.creator_profile.key == "art")
    host_video = host._reference_video_coordinator()
    guest_video = guest._reference_video_coordinator()
    original = tmp_path / "host-process-video.mp4"
    original.write_bytes(b"shared video contents" * 100)
    wrong = tmp_path / "different-video.mp4"
    wrong.write_bytes(b"another video" * 100)
    own_copy = tmp_path / "guest-local-copy.mp4"
    own_copy.write_bytes(original.read_bytes())
    host_video.share(str(original))
    drain(qapp, lambda: guest_video.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE)
    with pytest.raises(ReferenceVideoError, match="not the same file"):
        guest_video.open_local_copy(str(wrong))
    assert guest_video.follow_snapshot.state is not ReferenceVideoFollowState.FOLLOWING
    guest_video.open_local_copy(str(own_copy))
    host_video.play()
    host_video.seek(18.0)
    drain(qapp, lambda: guest._room_participant.native_state.reference_video.position_s == 18.0)
    guest_video.tick()
    assert guest_video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert players[-1].position == pytest.approx(18.0, abs=1.0)
    assert players[-1].state == "playing"
    assert all(player.muted for player in players)
    host_video.withdraw()
    drain(qapp, lambda: guest_video.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO)
    assert guest.bridge.jamulus_process is None


def test_lan_host_membership_uses_fresh_room_readers_and_actual_bind(
    controllers, monkeypatch,
):
    app = controllers(profile="art", hosting=True)
    readers = set()
    address = ["192.168.1.20"]
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: address[0])
    app.host_peer = SimpleNamespace(
        active=True, credentials=SessionCredentials.create(),
        server=SimpleNamespace(address=(address[0], 22125), room_participants=lambda: frozenset(readers)),
        invite_link=lambda **kwargs: "private-invite", stop=lambda: True,
    )
    room = app._room_participant
    room.role = "host"
    room.state = ArtRoomState.WAITING
    app._conductor_setup_requested = True
    room.tick()
    assert room.readiness().shareable
    assert app._last_session_conductor.title == "Your room is open"
    readers.add("authenticated-polling-artist")
    room.tick()
    assert app._last_session_conductor.title == "You’re in"
    readers.clear()
    room.tick()
    assert app._last_session_conductor.title == "Your room is open"
    address[0] = "192.168.1.21"
    room.tick()
    assert not room.readiness().shareable
    assert room.state is ArtRoomState.FAILED
    assert app._current_invite_url() == ""
    app.host_peer.active = False


def test_stale_first_native_state_keeps_a_bounded_profile_deadline(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = controllers()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    room, source = app._room_participant, app._remote_session
    room.native_state = RoomState(1, "art", "talk_and_make")
    room.native_source = source
    room.native_generation = source.snapshot.generation
    room.native_received_at = time.monotonic() - 6.0
    room.native_wait_started = 0
    scheduled = []
    monkeypatch.setattr("webjam_qt.controllers.room_participant.QTimer.singleShot", lambda delay, fn: scheduled.append((delay, fn)))
    room.connected_native(source, source.snapshot)
    assert room.probing
    assert len(scheduled) == 1 and scheduled[0][0] == 5000
    room.connected_native(source, source.snapshot)
    assert len(scheduled) == 1
    scheduled[0][1]()
    assert app._remote_invitation_requires_replacement
    assert "Update WebJam" in app._remote_fresh_invitation_detail()


def test_real_art_leave_worker_releases_room_then_restores_saved_profile(
    qapp, controllers, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite)
    app.begin_startup_journey()
    room = app._room_participant
    room.lan_guest.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    recorder_stop = mock.Mock(wraps=app.recording.stop_server_recording_for_shutdown)
    recorder_retire = mock.Mock(wraps=app.recording.on_audio_session_stopped)
    app.recording.stop_server_recording_for_shutdown = recorder_stop
    app.recording.on_audio_session_stopped = recorder_retire
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert room.lan_guest is None and room.state is ArtRoomState.NONE
    assert app._guest_invite is None
    assert app.creator_profile.key == "music"
    assert app._reference_video is None and app._shared_canvas is None
    recorder_stop.assert_not_called()
    recorder_retire.assert_not_called()


def test_art_host_opens_owned_private_listener_before_invite_without_audio(
    controllers, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.reference_local_host_requested", lambda: False)
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: "192.168.1.20")
    app = controllers(profile="art", hosting=True)
    host = SimpleNamespace(active=False, credentials=None, server=None,
                           invite_link=lambda **kw: "private-invite")

    def start(address, **kwargs):
        assert kwargs["creator_profile_key"] == "art"
        host.credentials = SessionCredentials.create()
        host.server = SimpleNamespace(address=(address, 22125), room_participants=lambda: frozenset())
        host.active = True

    def stop():
        host.active = False
        host.server = host.credentials = None
        return True

    host.start, host.stop = start, stop
    app.host_peer = host
    app._start_hosted_server_for_startup = mock.Mock()
    app._launch_native_jamulus_for_startup = mock.Mock()
    assert app._current_invite_url() == ""
    assert app.begin_startup_journey()
    assert host.active
    assert app._current_invite_url() == "private-invite"
    assert app._last_session_conductor.title == "Your room is open"
    app._start_hosted_server_for_startup.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()

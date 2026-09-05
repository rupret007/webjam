"""A terminal LAN observer can retry its reusable invitation without audio."""
from __future__ import annotations

import queue
import threading
from types import SimpleNamespace
from unittest import mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid

from core.session_conductor import (
    ArtRoomState,
    FailureDisposition,
    SessionConductorPhase,
    SessionPrimaryAction,
    derive_session_presentation,
)
from core.session_transfer import SessionTransferError
from core.settings import AppSettings
from services.lan_room_guest import LanRoomGuest
from tests.test_art_room_controller import (
    drain,
    invitation,
    qapp as _qapp_fixture,
    state,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

qapp = _qapp_fixture


@pytest.fixture
def controllers(qapp, tmp_path):
    made = []

    def create(*, invite=None, profile="music"):
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"),
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
    windows = [app.window for app in made]
    for app in reversed(made):
        assert app.shutdown()
        app.window.close()
        app.window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert all(not isValid(window) for window in windows)


class ControlledPeer:
    """Release actual observer polls without a socket or wall-clock timeout."""

    def __init__(self):
        self.responses = queue.Queue()
        self.polling = threading.Event()
        self.enrollments = []

    def enroll(self, installation_id, display_name):
        self.enrollments.append((installation_id, display_name))
        return object()

    def state(self, _enrollment):
        self.polling.set()
        value = self.responses.get(timeout=2.0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def lan(monkeypatch):
    clock = [100.0]
    clients, owners = [], []
    original_init = LanRoomGuest.__init__

    def peer(*_args, **_kwargs):
        client = ControlledPeer()
        clients.append(client)
        return client

    def initialize(owner, *args, **kwargs):
        original_init(owner, *args, **kwargs, clock=lambda: clock[0])
        owners.append(owner)

    monkeypatch.setattr("services.lan_room_guest.SessionPeerClient", peer)
    monkeypatch.setattr(LanRoomGuest, "__init__", initialize)
    rig = SimpleNamespace(clock=clock, clients=clients, owners=owners)
    yield rig
    # Release a pending read before stopping; no worker may escape this test.
    for client in clients:
        client.responses.put(SessionTransferError("test peer closed"))
    for owner in owners:
        assert owner.stop()
        assert owner._thread is None or not owner._thread.is_alive()


def terminal_loss(app, qapp, lan):
    owner = app._room_participant.lan_guest
    assert owner.client.polling.wait(1.0)
    lan.clock[0] += 31.0
    owner.client.responses.put(SessionTransferError("PRIVATE-PEER-DETAIL"))
    drain(qapp, lambda: app._room_participant.can_retry_lan)
    owner._thread.join(1.0)
    assert not owner._thread.is_alive()
    assert owner._stop.is_set()
    assert owner.last_state is None
    assert owner._enrollment is None
    return owner


def block_audio(app, monkeypatch):
    app._launch_native_jamulus_for_startup = mock.Mock()
    app._start_hosted_server_for_startup = mock.Mock()
    app._configure_guest_peer = mock.Mock(side_effect=AssertionError("unexpected audio owner"))
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        mock.Mock(side_effect=AssertionError("unexpected microphone probe")),
    )


@pytest.mark.parametrize("saved_profile", ["music", "art"])
@pytest.mark.parametrize("was_connected", [False, True])
def test_terminal_lan_retry_reuses_invitation_and_accepts_only_fresh_art(
    controllers, qapp, lan, monkeypatch, saved_profile, was_connected,
):
    invite = invitation()
    app: ApplicationController = controllers(invite=invite, profile=saved_profile)
    block_audio(app, monkeypatch)
    assert app.begin_startup_journey()
    room = app._room_participant
    first = room.lan_guest
    if was_connected:
        first.client.responses.put(state(invite))
        drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    app._persistence.mark_title_borrowed("The host's room")
    app.window.session_strip.set_session_title("The host's room")
    app.window.session_canvas.set_notes("My current draft")
    context = (
        app.creator_profile.key, app._creator_profile_host_owned,
        app.settings.last_creator_profile_key, app._persistence._borrowed_title,
    )
    old_generation = room.generation
    terminal_loss(app, qapp, lan)
    assert room.lan_failed and room.state is ArtRoomState.FAILED
    assert app._session_conductor_facts().failure is FailureDisposition.RETRYABLE
    assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
    guidance = room.guidance()
    assert guidance.primary_action is SessionPrimaryAction.RETRY_SETUP
    assert guidance.action_label == "Try Again"
    old_token = app.session_conductor.token

    assert room.retry_lan_guest()
    second = room.lan_guest
    assert second is not first and second.invite is invite
    assert len(lan.owners) == 2
    assert room.generation > old_generation
    assert app.session_conductor.token != old_token
    assert room.state is ArtRoomState.STARTING
    assert room.probing and not room.probe_failed
    assert not room.lan_failed and not room.can_retry_lan
    assert app._session_conductor_facts().failure is FailureDisposition.NONE
    assert (
        app.creator_profile.key, app._creator_profile_host_owned,
        app.settings.last_creator_profile_key, app._persistence._borrowed_title,
    ) == context
    assert app.window.session_strip.current_title() == "The host's room"
    assert app.window.session_canvas.current_notes() == "My current draft"
    current_generation, current_token = room.generation, app.session_conductor.token
    assert not room.retry_lan_guest()
    assert not room.retry_lan_guest()
    assert room.generation == current_generation
    assert app.session_conductor.token == current_token
    assert len(lan.owners) == 2

    # A retired source cannot win even if it claims the new generation.
    room.receive_lan(first, current_generation, state(invite))
    room.lose_lan(first, current_generation, True)
    room.receive_lan(second, old_generation, state(invite))
    room.lose_lan(second, old_generation, True)
    assert room.state is ArtRoomState.STARTING and room.probing
    second.client.responses.put(state(invite))
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    assert app.creator_profile.key == "art"
    assert app._last_session_conductor.phase is SessionConductorPhase.CONNECTED
    assert not room.probing and not room.lan_failed and not room.can_retry_lan
    assert second._installation_id != first._installation_id
    assert len(second.client.enrollments) == 1
    assert not room.retry_lan_guest()
    app._configure_guest_peer.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    assert app.guest_peer is None
    assert app.bridge.jamulus_process is None


@pytest.mark.parametrize("saved_profile", ["music", "art"])
@pytest.mark.parametrize("stop_outcome", [False, RuntimeError("PRIVATE-STOP-DETAIL")])
def test_unproved_lan_stop_retains_owner_and_uses_room_cleanup(
    controllers, qapp, lan, monkeypatch, caplog, saved_profile, stop_outcome,
):
    app = controllers(invite=invitation(), profile=saved_profile)
    assert app.begin_startup_journey()
    room = app._room_participant
    first = terminal_loss(app, qapp, lan)
    generation, token = room.generation, app.session_conductor.token
    app._persistence.mark_title_borrowed("Borrowed room")
    app.window.session_strip.set_session_title("Borrowed room")
    app.window.session_canvas.set_notes("Private draft")
    failure = mock.Mock(
        side_effect=stop_outcome if isinstance(stop_outcome, Exception) else None,
        return_value=stop_outcome,
    )
    with monkeypatch.context() as patch:
        patch.setattr(first, "stop", failure)
        assert not room.retry_lan_guest()
        assert not room.retry_lan_guest()
    failure.assert_called_once_with()
    assert room.lan_guest is first
    assert len(lan.owners) == 1
    assert room.generation > generation
    assert app.session_conductor.token == token
    assert room.lan_failed
    assert app.audio.cleanup_retry_required and app.audio._stop_art_room
    assert not app.audio._stop_hosting
    assert not room.can_retry_lan
    assert app._persistence._borrowed_title == "Borrowed room"
    assert app.window.session_strip.current_title() == "Borrowed room"
    assert app.window.session_canvas.current_notes() == "Private draft"
    assert "PRIVATE-STOP-DETAIL" not in caplog.text
    room.receive_lan(first, generation, state(first.invite))
    assert room.probe_failed
    app.audio.retry_stop()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert room.lan_guest is None and not room.lan_failed


@pytest.mark.parametrize(
    "guard",
    ["room_stop", "audio_stop", "cleanup", "shutdown", "quit_cleanup", "quit_preflight", "host",
     "native_guest", "native_host", "native_invite", "startup", "recording",
     "nonterminal", "wrong_owner"],
)
def test_lan_retry_rejects_other_owners_and_higher_priority_work(
    controllers, qapp, lan, monkeypatch, guard,
):
    app = controllers(invite=invitation(), profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    first = terminal_loss(app, qapp, lan)
    generation, token = room.generation, app.session_conductor.token
    with monkeypatch.context() as patch:
        if guard == "room_stop":
            patch.setattr(room, "stopping", True)
        elif guard == "audio_stop":
            patch.setattr(app.audio, "stopping", True)
        elif guard == "cleanup":
            patch.setattr(app.audio, "cleanup_retry_required", True)
        elif guard == "shutdown":
            patch.setattr(app, "_shutdown", True)
        elif guard == "quit_cleanup":
            patch.setattr(app, "_shutdown_cleanup_pending", True)
        elif guard == "quit_preflight":
            patch.setattr(app, "_shutdown_in_progress", True)
        elif guard == "host":
            patch.setattr(room, "role", "host")
        elif guard == "native_guest":
            patch.setattr(app, "_remote_session", object())
        elif guard == "native_host":
            patch.setattr(app, "_remote_invite_owner", object())
        elif guard == "native_invite":
            patch.setattr(app, "_remote_invitation", object())
        elif guard == "startup":
            patch.setattr(app, "_startup_attempt", {})
        elif guard == "recording":
            patch.setattr(app, "recording", SimpleNamespace(take_in_progress=True))
        elif guard == "nonterminal":
            patch.setattr(room, "probe_failed", False)
            patch.setattr(room, "state", ArtRoomState.RECONNECTING)
        else:
            patch.setattr(room, "_lan_terminal_owner", object())
        stop = mock.Mock(wraps=first.stop)
        patch.setattr(first, "stop", stop)
        assert not room.can_retry_lan
        assert not room.retry_lan_guest()
        stop.assert_not_called()
    assert room.lan_guest is first and len(lan.owners) == 1
    assert room.generation == generation and app.session_conductor.token == token


def test_reentrant_retry_during_stop_cannot_create_a_second_observer(
    controllers, qapp, lan, monkeypatch,
):
    app = controllers(invite=invitation(), profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    first = terminal_loss(app, qapp, lan)
    stop = first.stop
    nested_results = []

    def stop_with_queued_retry():
        nested_results.append((room.can_retry_lan, room.retry_lan_guest()))
        return stop()

    with monkeypatch.context() as patch:
        patch.setattr(first, "stop", stop_with_queued_retry)
        assert room.retry_lan_guest()
    assert nested_results == [(False, False)]
    assert len(lan.owners) == 2


@pytest.mark.parametrize("superseding_action", ["end", "quit", "quit_preflight"])
def test_end_or_quit_winning_during_stop_cannot_restart_the_room(
    controllers, qapp, lan, monkeypatch, superseding_action,
):
    app = controllers(invite=invitation(), profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    first = terminal_loss(app, qapp, lan)
    stop, token = first.stop, app.session_conductor.token

    def stop_with_pending_exit():
        result = stop()
        if superseding_action == "end":
            room.generation += 1
        elif superseding_action == "quit":
            app._shutdown_cleanup_pending = True
        else:
            app._shutdown_in_progress = True
        return result

    with monkeypatch.context() as patch:
        patch.setattr(first, "stop", stop_with_pending_exit)
        assert not room.retry_lan_guest()
    app._shutdown_cleanup_pending = False
    app._shutdown_in_progress = False
    assert room.lan_guest is first and len(lan.owners) == 1
    assert app.session_conductor.token == token
    assert room.stopping and not room.can_retry_lan


@pytest.mark.parametrize("failed_step", ["constructor", "start"])
def test_replacement_start_failure_retains_a_retryable_owner_without_private_detail(
    controllers, qapp, lan, monkeypatch, caplog, failed_step,
):
    app = controllers(invite=invitation(), profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    first = terminal_loss(app, qapp, lan)
    with monkeypatch.context() as patch:
        patch.setattr(
            LanRoomGuest, "__init__" if failed_step == "constructor" else "start",
            mock.Mock(side_effect=RuntimeError("PRIVATE-START-DETAIL")),
        )
        assert not room.retry_lan_guest()
    assert room.lan_guest is not None
    assert room.lan_guest.invite is first.invite
    assert room.can_retry_lan and room.probe_failed
    assert room.guidance().primary_action is SessionPrimaryAction.RETRY_SETUP
    assert "PRIVATE-START-DETAIL" not in caplog.text


def test_retry_waits_for_authenticated_non_art_profile_before_music_handoff(
    controllers, qapp, lan, monkeypatch,
):
    invite = invitation()
    app = controllers(invite=invite, profile="art")
    assert app.begin_startup_journey()
    room = app._room_participant
    terminal_loss(app, qapp, lan)
    configure = mock.Mock(wraps=app._configure_guest_peer)
    monkeypatch.setattr(app, "_configure_guest_peer", configure)
    continuation = mock.Mock(return_value=True)
    monkeypatch.setattr(app, "begin_startup_journey", continuation)
    assert room.retry_lan_guest()
    second = room.lan_guest
    configure.assert_not_called()
    continuation.assert_not_called()
    assert app.guest_peer is None and app.creator_profile.key == "art"
    second.client.responses.put(state(invite, "music"))
    drain(qapp, lambda: continuation.call_count == 1)
    configure.assert_called_once_with(invite)
    continuation.assert_called_once_with()
    assert room.lan_guest is None and second._stop.is_set()
    assert app.guest_peer is not None
    assert app.creator_profile.key == "music"
    assert app.bridge.jamulus_process is None


@pytest.mark.parametrize("saved_profile", ["music", "art"])
@pytest.mark.parametrize("terminal", [False, True])
def test_preprofile_observer_has_a_visible_leave_that_performs_room_cleanup(
    controllers, qapp, lan, monkeypatch, saved_profile, terminal,
):
    app = controllers(invite=invitation(), profile=saved_profile)
    block_audio(app, monkeypatch)
    app.window.session_canvas.set_notes("My personal draft")
    app.window.show()
    assert app.begin_startup_journey()
    room = app._room_participant
    owner = room.lan_guest
    if terminal:
        terminal_loss(app, qapp, lan)
        assert app._last_session_conductor.phase is SessionConductorPhase.FAILED
    qapp.processEvents()
    button = app.window.session_strip._audio_button
    assert button.isVisible() and button.isEnabled()
    assert button.accessibleName() == "Leave Room"
    assert app.creator_profile.key == saved_profile
    assert not app._creator_profile_host_owned
    assert room.probing and room.active
    assert not app.audio.connected
    confirm = mock.Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", confirm)

    button.click()
    owner.client.responses.put(SessionTransferError("test peer closed"))
    drain(qapp, lambda: not app.audio.stopping)
    assert confirm.call_count == 1
    assert confirm.call_args.args[1] == "Leave Room?"
    assert app.audio._stop_art_room and not app.audio._stop_hosting
    assert room.lan_guest is None and not room.active and not room.lan_failed
    assert owner._stop.is_set() and not owner._thread.is_alive()
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == saved_profile
    assert app.window.session_canvas.current_notes() == "My personal draft"
    app._configure_guest_peer.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()


@pytest.mark.parametrize("higher_priority", ["recording", "cleanup"])
def test_terminal_lan_failure_fact_survives_disabled_retry_with_safe_precedence(
    controllers, qapp, lan, monkeypatch, higher_priority,
):
    app = controllers(invite=invitation(), profile="music")
    assert app.begin_startup_journey()
    room = app._room_participant
    terminal_loss(app, qapp, lan)
    with monkeypatch.context() as patch:
        if higher_priority == "recording":
            patch.setattr(app, "recording", SimpleNamespace(
                phase=SimpleNamespace(value="recording"),
                take_in_progress=True, is_recording_active=True,
            ))
            expected = SessionConductorPhase.RECORDING
        else:
            patch.setattr(app.audio, "cleanup_retry_required", True)
            expected = SessionConductorPhase.INDETERMINATE
        assert room.lan_failed and not room.can_retry_lan
        facts = app._session_conductor_facts()
        assert facts.failure is FailureDisposition.RETRYABLE
        assert derive_session_presentation(facts).phase is expected
        assert not room.retry_lan_guest()
    assert room.lan_failed and room.can_retry_lan

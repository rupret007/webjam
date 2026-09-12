"""A rejected invite can become one fresh Music room with its saved mix.

LAN polling, invitation parsing/acceptance, cleanup, Music discovery, Qt cards,
and registered process-bound callbacks are production. Native process/RPC
readiness and the socket are explicit synthetic boundaries; no connected or
local-roster proof flags are seeded. No live media, devices or network run.
"""

import json
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QDialog, QMessageBox

from core.jamulus_rpc_client import JamulusRpcClient, JamulusRpcMonitorIdentity
from core.meeting_companion import build_invite_message
from core.network_invite import create_invite_link
from core.session_conductor import FailureDisposition, SessionPrimaryAction
from core.session_transfer import SessionTransferError
from core.settings import AppSettings, load_settings, save_settings
from tests import test_art_lan_retry as lan_support
from tests import test_art_room_controller as room_support
from tests.test_rejected_invitation_journey import reject_current
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog

qapp = room_support.qapp
lan = lan_support.lan

PERSONAL = "https://personal.webex.com/meet/my-room"
PREVIOUS = "https://meet.google.com/abc-defg-hij"
REPLACEMENT = "https://us02web.zoom.us/j/123456789"
NOTES = "Keep the arrangement notes during invitation recovery"


@pytest.fixture(autouse=True)
def qt_errors(monkeypatch, caplog):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(kind.__name__))
    yield
    assert errors == [], f"Qt must not swallow a failed invitation/mix callback: {errors}"
    callback_errors = [
        record.getMessage()
        for phase in ("setup", "call", "teardown")
        for record in caplog.get_records(phase)
        if record.name == "webjam.ui_thread" and record.levelno >= logging.ERROR
    ]
    assert callback_errors == [], f"Queued UI callbacks must not swallow failures: {callback_errors}"


def _message(invite, meeting):
    return build_invite_message(
        join_link=create_invite_link(
            invite.host, session_id=invite.session_id, peer_port=invite.peer_port,
            invite_token=invite.invite_token, session_name="Music rehearsal",
        ),
        creator_profile_key="music", meeting_url=meeting, same_network_required=True,
    ).text


class _MemorySocket:
    def __init__(self):
        self.commands = []
        self.closed = False

    def sendall(self, raw):
        assert not self.closed
        assert raw.endswith(b"\n")
        self.commands.append(json.loads(raw))

    def close(self):
        self.closed = True

    @property
    def gains(self):
        result = {}
        for command in self.commands:
            assert command["jsonrpc"] == "2.0"
            assert isinstance(command["id"], int) and command["id"] > 0
            if command["method"] == "jamulusclient/setName":
                assert set(command["params"]) == {"name"}
                assert isinstance(command["params"]["name"], str) and command["params"]["name"]
                continue
            assert command["method"] == "jamulusclient/setFaderLevel"
            assert set(command["params"]) == {"channelIndex", "level"}
            result[command["params"]["channelIndex"]] = command["params"]["level"]
        return result


class _NativeProcess:
    pid = 4241

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        assert self.returncode is not None
        return self.returncode


class _InlineGainWorker:
    def __init__(self, *, target, **kwargs):
        self.target = target
        self.alive = False

    def start(self):
        self.alive = True
        try:
            self.target()
        finally:
            self.alive = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        assert not self.alive


class _ControlledMonitorWorker:
    """The native polling boundary is idle; tests deliver typed RPC receipts."""

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


@pytest.fixture
def composed(qapp, lan, monkeypatch, tmp_path):
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog._windows_jamulus_installer", lambda *a: None)
    monkeypatch.setattr("webjam_qt.controllers.session_persistence._persistence_home", lambda: tmp_path)
    monkeypatch.setattr("webjam_qt.controllers.mix_manager.Path.home", lambda: tmp_path)
    guards = []

    def forbid(target):
        guard = Mock(name=target, side_effect=AssertionError("No external or media action belongs to this journey"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)

    for target in (
        "PySide6.QtGui.QDesktopServices.openUrl",
        "webex_integration.open_webex_meeting",
        "core.jamulus_rpc_client.socket.create_connection",
        "webjam_qt.platform_permissions.microphone_permission_status",
        "core.session_transfer_runtime.GuestPeerSession.start",
    ):
        forbid(target)
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "takes"),
        log_file=str(tmp_path / "webjam.log"),
        webex_url=PERSONAL, host_server_enabled=False, last_creator_profile_key="music",
    )
    save_settings(settings)
    first_invite = room_support.invitation()
    door = LaunchDialog(settings)
    assert door.accept_invite(_message(first_invite, PREVIOUS))
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam", initial_title="Music invitation recovery",
    )
    app = ApplicationController(
        window, settings=settings, session_invite=door.band_invite,
        session_meeting_url=door.invitation_meeting_url,
    )
    door.deleteLater()
    for owner, name in (
        (app.bridge, "launch_webex"), (app.bridge, "launch_jamulus"),
        (app, "_play_reference_track"), (app.recording, "on_record_requested"),
        (app, "_start_hosted_server_for_startup"),
    ):
        guard = Mock(name=name, side_effect=AssertionError("Only the explicit native launch seam is permitted"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    # Native launch intent is observed at the machine boundary. Its process
    # receipt is installed later, after an intentional fresh Music join.
    launch = Mock()
    monkeypatch.setattr(app, "_launch_native_jamulus_for_startup", launch)
    saved_path = tmp_path / ".webjam_mix.json"
    saved_path.write_text(json.dumps({"participants": [
        {"channel_id": 7, "name": "WebJam Track", "fader_level": 40,
         "muted": True, "solo": False},
        {"channel_id": 8, "name": "Collaborator", "fader_level": 55,
         "muted": False, "solo": False},
    ]}))
    original_thread = threading.Thread

    def controlled_thread(*args, **kwargs):
        if kwargs.get("name") == "webjam-listening-mix":
            return _InlineGainWorker(**kwargs)
        if kwargs.get("target") == app.jamulus._monitor_loop:
            return _ControlledMonitorWorker()
        return original_thread(*args, **kwargs)

    monkeypatch.setattr("jamulus_controller.threading.Thread", controlled_thread)
    for timer in app.findChildren(QTimer):
        timer.stop()
    assert app.begin_startup_journey()
    app.window.session_canvas.set_notes(NOTES)
    window.show()
    qapp.processEvents()
    pair = SimpleNamespace(app=app, window=window, launch=launch, lan=lan,
                           saved_path=saved_path, sink=None, process=None)
    try:
        yield pair
        for guard in guards:
            guard.assert_not_called()
        assert app._reference_track is None
    finally:
        for client in lan.clients:
            client.responses.put(SessionTransferError("controlled peer closed"))
        if app.audio.cleanup_retry_required:
            app.audio.retry_stop()
            room_support.drain(qapp, lambda: not app.audio.stopping)
        assert app.shutdown()
        if pair.process is not None:
            assert pair.process.poll() is not None
            assert pair.sink.closed
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def _assert_personal_context(pair, room_meeting):
    app = pair.app
    assert app._effective_meeting_url() == room_meeting
    assert app.webex.meeting_url == room_meeting
    assert app.settings.webex_url == PERSONAL
    persisted = load_settings(app.settings.config_file)
    assert persisted.webex_url == PERSONAL
    assert persisted.last_creator_profile_key == "music"
    assert app.window.session_canvas.current_notes() == NOTES


def _join_dialog(monkeypatch, message, *, cancel=False):
    opened = []

    def execute(dialog):
        opened.append(dialog)
        if cancel:
            dialog._invite_input.setText(message)
            dialog.reject()
            return QDialog.DialogCode.Rejected
        assert dialog.accept_invite(message)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(LaunchDialog, "exec", execute)
    return opened


def _publish_native_boundary(pair, monkeypatch):
    app = pair.app
    assert pair.launch.call_count == 1
    assert not app._jamulus_connected
    assert app._jamulus_local_roster_generation == 0
    process = _NativeProcess()
    app.bridge.jamulus_process = process
    app.bridge.jamulus_state = "Running"
    app.bridge.jamulus_launch_intended = True
    app.bridge._jamulus_process_generation_counter = 1
    app.bridge._jamulus_process_generation = 1
    app.bridge._jamulus_process_started_at = time.monotonic()
    rpc = app.jamulus.rpc_client
    assert isinstance(rpc, JamulusRpcClient)
    sink = _MemorySocket()
    identity = JamulusRpcMonitorIdentity(1, 1, process.pid)

    def start_reader(*, process_generation, process_id):
        assert (process_generation, process_id) == (1, process.pid)
        rpc._monitor_epoch = rpc._sock_epoch = 1
        rpc._monitor_identity = identity
        rpc._sock = sink
        rpc._running = rpc._available = rpc._authed = True
        rpc._last_activity_at = time.monotonic()
        return identity

    # Start/Stop and callback registration remain production. Substitute only
    # the native reader, polling and meter boundaries; none supplies a roster.
    with monkeypatch.context() as native:
        native.setattr(rpc, "start", start_reader)
        native.setattr(app.jamulus.audio_engine, "start", lambda: None)
        assert app.jamulus.start(process_generation=1, process_id=process.pid) == identity
    pair.process, pair.sink = process, sink
    assert not app._jamulus_connected
    assert app._jamulus_local_roster_generation == 0
    return identity


def test_queued_readiness_after_successful_shutdown_cannot_probe_or_change_ui(
    composed, qapp, monkeypatch,
):
    pair = composed
    app = pair.app
    app._ui_invoker.invoke(app._refresh_readiness)
    for client in pair.lan.clients:
        client.responses.put(SessionTransferError("controlled peer closed"))
    assert app.shutdown()
    assert app._shutdown
    updates = []
    for owner, method in (
        (app.window.session_strip, "set_audio_state"),
        (app.window.session_strip, "set_tools_enabled"),
        (app.window.participant_grid, "set_session_state"),
        (app.window, "set_status_audio"),
    ):
        callback = Mock(name=method)
        monkeypatch.setattr(owner, method, callback)
        updates.append(callback)

    qapp.processEvents()

    for callback in updates:
        callback.assert_not_called()
    # The fixture also requires the permission guard and UI error logger to
    # remain untouched, so a swallowed exception is not successful retirement.


def test_readiness_remains_live_when_notes_save_vetoes_shutdown(composed, qapp, monkeypatch):
    app = composed.app
    owner = app._room_participant.lan_guest
    with monkeypatch.context() as save_failure:
        save_failure.setattr(app, "_save_notes", Mock(return_value=False))
        assert not app.shutdown()
    assert not app._shutdown and not app._shutdown_cleanup_pending
    assert app._room_participant.lan_guest is owner and not owner._stop.is_set()
    update = Mock(wraps=app.window.session_strip.set_audio_state)
    monkeypatch.setattr(app.window.session_strip, "set_audio_state", update)

    app._ui_invoker.invoke(app._refresh_readiness)
    qapp.processEvents()

    update.assert_any_call("Leave Room", enabled=True)
    assert app._room_participant.lan_guest is owner and not owner._stop.is_set()
    assert app.window.session_canvas.current_notes() == NOTES


@pytest.mark.parametrize("meeting", [REPLACEMENT, ""], ids=["new-conversation", "bare-invite"])
def test_rejected_invitation_fresh_music_join_restores_only_current_room_and_mix(
    composed, qapp, monkeypatch, caplog, meeting,
):
    pair = composed
    app = pair.app
    room = app._room_participant
    first = reject_current(app, qapp)
    old_generation = room.generation
    assert room.lan_invitation_rejected and not room.can_retry_lan
    assert app._session_conductor_facts().failure is FailureDisposition.BLOCKED
    assert room.guidance().primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert app.window.session_hud._action.text() == "Paste New Invite"
    _assert_personal_context(pair, PREVIOUS)
    pair.launch.assert_not_called()
    assert app.jamulus.participants == {}
    saved_bytes = pair.saved_path.read_bytes()
    replacement = room_support.invitation()
    complete = _message(replacement, meeting)

    # Cancelling the actual Join dialog leaves the rejected owner and room
    # context intact. A stale Retry signal must not launch saved Music.
    opened = _join_dialog(monkeypatch, complete, cancel=True)
    app.window.session_hud._action.click()
    assert len(opened) == 1 and room.lan_guest is first
    _assert_personal_context(pair, PREVIOUS)
    app._on_conductor_action_requested("retry")
    assert len(pair.lan.owners) == 1 and room.lan_guest is first
    pair.launch.assert_not_called()

    # An accepted replacement still waits for actual cleanup ownership. A
    # single controlled failure cannot silently consume or launch the invite.
    opened = _join_dialog(monkeypatch, complete)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    with monkeypatch.context() as failed_stop:
        failed_stop.setattr(first, "stop", Mock(return_value=False))
        app.window.session_hud._action.click()
        room_support.drain(qapp, lambda: not app.audio.stopping)
        assert len(opened) == 1
        assert app.audio.cleanup_retry_required and room.lan_guest is first
        _assert_personal_context(pair, PREVIOUS)
        pair.launch.assert_not_called()
        assert app.jamulus.participants == {}
    app.audio.retry_stop()
    room_support.drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required and room.lan_guest is None
    assert len(pair.lan.owners) == 1
    pair.launch.assert_not_called()

    # Cleanup is not acceptance: the artist explicitly opens Join again.
    app._on_conductor_action_requested("paste_invite")
    room_support.drain(qapp, lambda: room.lan_guest is not None and room.lan_guest is not first)
    second = room.lan_guest
    assert second.invite.session_id == replacement.session_id
    assert len(pair.lan.owners) == 2 and first._stop.is_set()
    _assert_personal_context(pair, meeting)
    room.receive_lan(first, room.generation, room_support.state(first.invite, "art"))
    room.lose_lan(first, room.generation, True)
    room.receive_lan(second, old_generation, room_support.state(second.invite, "art"))
    assert not room.lan_failed
    assert app.creator_profile.key == "music"
    pair.launch.assert_not_called()
    assert not app._jamulus_connected and app.jamulus.participants == {}

    assert second.client.polling.wait(1)
    second.client.responses.put(room_support.state(second.invite, "music"))
    room_support.drain(qapp, lambda: pair.launch.call_count == 1)
    assert room.lan_guest is None and second._stop.is_set()
    assert room.music_invite is second.invite and app.guest_peer is not None
    assert not app._jamulus_connected
    _assert_personal_context(pair, meeting)
    persisted_after_join = Path(app.settings.config_file).read_bytes()
    identity = _publish_native_boundary(pair, monkeypatch)
    remote_roster = [
        {"channel_id": 17, "name": "WebJam Track", "is_local": False},
        {"channel_id": 18, "name": "Collaborator", "is_local": False},
    ]
    app.jamulus._on_rpc_participants_with_source(remote_roster, identity)
    qapp.processEvents()
    assert not app._jamulus_connected
    assert app._jamulus_local_roster_generation == 0
    assert pair.sink.commands == []
    assert app.jamulus.participants[17].fader_level == 100
    _assert_personal_context(pair, meeting)

    app.jamulus._on_rpc_participants_with_source(
        [{"channel_id": 0, "name": "You", "is_local": True}, *remote_roster], identity,
    )
    room_support.drain(qapp, lambda: app._jamulus_connected)
    for timer in app.findChildren(QTimer):
        timer.stop()
    assert app._jamulus_local_roster_generation == identity.process_generation
    assert pair.sink.gains == {17: 0, 18: 43}
    cards = app.window.participant_grid._cards
    assert set(cards) == {0, 17, 18}
    assert cards[17]._fader.value() == 40 and cards[17]._mute_button.isChecked()
    assert cards[18]._fader.value() == 55 and not cards[18]._mute_button.isChecked()
    assert pair.saved_path.read_bytes() == saved_bytes
    assert Path(app.settings.config_file).read_bytes() == persisted_after_join
    _assert_personal_context(pair, meeting)
    commands = list(pair.sink.commands)
    for owner in (first, second):
        room.receive_lan(owner, room.generation, room_support.state(owner.invite, "art"))
        room.lose_lan(owner, room.generation, True)
    qapp.processEvents()
    assert app.creator_profile.key == "music" and app._jamulus_connected
    assert pair.sink.commands == commands
    _assert_personal_context(pair, meeting)
    assert "PRIVATE-REJECTION-DETAIL" not in caplog.text
    assert first.invite.invite_token not in caplog.text
    assert second.invite.invite_token not in caplog.text

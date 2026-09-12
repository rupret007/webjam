"""Saved listening controls compose with real native-room cleanup receipts.

The native host owner, primary proof checks, registered Qt roster delivery,
participant cards, mix files and gain serializer are production. Processes,
RPC socket writes and capture workers are controlled machine boundaries;
these tests start no service, device, external meeting or recording.
"""
from __future__ import annotations

import json
import logging
import time
from types import MethodType
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from core.band_check import BandCheckMode
from core.jamulus_rpc_client import JamulusRpcClient
from jamulus_controller import JamulusController
from services.bridge_service import BridgeService
from tests.test_music_effective_gain import _InlineGainWorker
from tests.test_music_gain_dispatch import MemorySocket
from tests.test_music_listening_controls_journey import set_listening_level
from tests.test_reference_track_application_integration import _primary_source_identity
from tests.test_two_session_music_host_recovery import (
    assert_context_and_source_retained,
    assert_failed_owner_cannot_publish_invitation,
    assert_fresh_music_intents_refused,
    host as _host,
    native_music as _native_music,
    no_unhandled_qt_slot_errors as _qt_errors,
    qapp as _qapp,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import RecorderPhase, RecordingCoordinator
from webjam_qt.windows.reference_track import ReferenceTrackPrimaryGate

host = _host
native_music = _native_music
qapp = _qapp
no_unhandled_qt_slot_errors = _qt_errors


@pytest.fixture(autouse=True)
def no_queued_ui_callback_errors(caplog):
    yield
    failures = [
        record.getMessage()
        for phase in ("setup", "call", "teardown")
        for record in caplog.get_records(phase)
        if record.name == "webjam.ui_thread" and record.levelno >= logging.ERROR
    ]
    assert failures == [], f"Queued UI callbacks must not swallow failures: {failures}"


class InlineComposedWorker(_InlineGainWorker):
    def __init__(self, *, target, args=(), kwargs=None, name=None, **ignored):
        assert name in {"webjam-listening-mix", "webjam-host-room-state", "webjam-session-stop"}
        super().__init__(target=lambda: target(*args, **(kwargs or {})))


class HeldBandCheckWorker:
    """Keep the real observation dialog without running a machine scan."""

    def __init__(self, target):
        self.target = Mock(wraps=target)
        self.started = False

    def start(self):
        self.started = True


@pytest.fixture
def mixed_room(native_music, qapp, monkeypatch, tmp_path):
    pair = native_music
    app: ApplicationController = pair.app
    identity = _primary_source_identity(app)
    previous_rpc = app.jamulus.rpc_client
    previous_snapshot_method = app.jamulus.rpc_monitor_snapshot_for
    rpc = JamulusRpcClient()
    sink = MemorySocket()
    rpc._running = rpc._available = rpc._authed = True
    rpc._monitor_epoch = rpc._sock_epoch = identity.monitor_epoch
    rpc._monitor_identity = identity
    rpc._last_activity_at = time.monotonic()
    rpc._sock = sink
    app.jamulus.rpc_client = rpc
    app.jamulus.rpc_monitor_snapshot_for = MethodType(
        JamulusController.rpc_monitor_snapshot_for, app.jamulus,
    )
    app.jamulus.running = True
    app.jamulus._rpc_monitor_identity = identity
    roster = [
        {"channel_id": 0, "name": "You", "is_local": True},
        {"channel_id": 7, "name": "WebJam Track", "is_local": False},
        {"channel_id": 8, "name": "Collaborator", "is_local": False},
    ]
    monkeypatch.setattr("webjam_qt.controllers.mix_manager.Path.home", lambda: tmp_path)
    app.jamulus._on_rpc_participants_with_source(roster, identity)
    qapp.processEvents()
    for timer in app.findChildren(QTimer):
        timer.stop()
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
    assert set(app.window.participant_grid._cards) == {0, 7, 8}
    assert app.jamulus.rpc_monitor_snapshot_for(
        process_generation=identity.process_generation, process_id=identity.process_id,
    ).identity == identity
    app.window.session_canvas.set_notes("Keep this rehearsal note through room recovery")
    pair.mix_socket, pair.mix_rpc = sink, rpc
    pair.primary_identity, pair.primary_roster = identity, roster
    pair.mix_path = tmp_path / ".webjam_mix.json"
    pair.held_band_checks = []

    def controlled_worker(**kwargs):
        if kwargs.get("name") == "band-check":
            worker = HeldBandCheckWorker(kwargs["target"])
            pair.held_band_checks.append(worker)
            return worker
        return InlineComposedWorker(**kwargs)

    sink.messages.clear()
    try:
        with monkeypatch.context() as workers:
            workers.setattr("jamulus_controller.threading.Thread", controlled_worker)
            yield pair
        for worker in pair.held_band_checks:
            worker.target.assert_not_called()
        assert app.window.session_canvas.current_notes() == "Keep this rehearsal note through room recovery"
        assert app.jamulus.protocol.enabled is False
    finally:
        # Restore the original fixture's controlled primary boundary before
        # its independent shutdown receipts retire the app.
        app.jamulus.rpc_client = previous_rpc
        app.jamulus.rpc_monitor_snapshot_for = previous_snapshot_method
        app.jamulus.running = False
        rpc._sock = None
        rpc._running = rpc._available = rpc._authed = False
        app._mix_dirty = False


def _gains(pair):
    assert all(message["method"] == "jamulusclient/setFaderLevel"
               for message in pair.mix_socket.messages)
    return {
        message["params"]["channelIndex"]: message["params"]["level"]
        for message in pair.mix_socket.messages
    }


def _save_solo(pair, *, muted_during_solo):
    grid = pair.app.window.participant_grid
    track, collaborator = grid._cards[7], grid._cards[8]
    set_listening_level(track, 40)
    set_listening_level(collaborator, 55)
    if not muted_during_solo:
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    if muted_during_solo:
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    pair.app.window._save_mix_shortcut.activated.emit()
    assert pair.mix_path.is_file()
    return track, collaborator


@pytest.mark.parametrize("bad_receipt", ["exception", "wrong_generation"])
@pytest.mark.parametrize("muted_during_solo", [False, True])
def test_failed_native_reset_preserves_saved_solo_controls_and_fresh_intent_fences(
    mixed_room, qapp, monkeypatch, bad_receipt, muted_during_solo,
):
    pair = mixed_room
    app, owner, process = pair.app, pair.native_owner, pair.native_process
    track, collaborator = _save_solo(pair, muted_during_solo=muted_during_solo)
    saved_mix = json.loads(pair.mix_path.read_text())
    process.close_outcome = bad_receipt
    app._reset_remote_invite()
    qapp.processEvents()
    rejected_snapshot = owner.snapshot
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert app.audio.connected and not app.audio.stopping and not app.audio.cleanup_retry_required
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
    assert_fresh_music_intents_refused(pair)

    # Make the current mix visibly different, then queue its real native roster
    # delivery before restoring the saved local choice via the registered key.
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    assert track._mute_button.isChecked()
    QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    set_listening_level(track, 95)
    queued = []
    with monkeypatch.context() as hold:
        hold.setattr(app._ui_invoker, "invoke", queued.append)
        app.jamulus._on_rpc_participants_with_source(pair.primary_roster, pair.primary_identity)
    assert len(queued) == 1
    pair.mix_socket.messages.clear()
    app.window._load_mix_shortcut.activated.emit()
    qapp.processEvents()
    assert track._fader.value() == 40 and track._solo_button.isChecked()
    assert track._mute_button.isChecked() is muted_during_solo
    assert collaborator._fader.value() == 55 and collaborator._mute_button.isChecked()
    assert _gains(pair)[7] == (0 if muted_during_solo else 31)
    assert _gains(pair)[8] == 0
    assert app.jamulus.serialize_mix() == saved_mix
    assert_context_and_source_retained(pair)
    assert_fresh_music_intents_refused(pair)

    process.close_outcome = "valid"
    app._reset_remote_invite()
    process.emit_host_connected(2)
    qapp.processEvents()
    assert owner.connection_available and owner.snapshot.generation == 2
    assert len(pair.held_band_checks) == 1 and pair.held_band_checks[0].started
    assert app._ready_check_dialog.isVisible()
    assert app._ready_check_dialog._mode is BandCheckMode.LIVE_OBSERVE
    assert not app._ready_check_dialog._start_session_when_ready
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
    pair.mix_socket.messages.clear()
    app._on_remote_session_snapshot(rejected_snapshot, source=owner)
    queued[0]()
    qapp.processEvents()
    assert app.jamulus.serialize_mix() == saved_mix
    assert track._fader.value() == 40 and track._solo_button.isChecked()
    assert track._mute_button.isChecked() is muted_during_solo
    assert pair.mix_socket.messages == []
    app.recording.on_record_requested.assert_not_called()
    app.bridge.find_reference_track_jamulus.assert_not_called()
    assert_context_and_source_retained(pair)
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    assert track._mute_button.isChecked() and not collaborator._mute_button.isChecked()
    assert _gains(pair)[7] == 0 and _gains(pair)[8] == 43


def test_saved_mix_restore_during_failed_reset_preserves_real_active_take_stop(
    mixed_room, qapp, monkeypatch,
):
    pair = mixed_room
    app, process = pair.app, pair.native_process
    _save_solo(pair, muted_during_solo=True)
    recorder = app.recording
    recorder._take_id = "33333333-3333-3333-3333-333333333333"
    recorder.phase = RecorderPhase.RECORDING
    app._recorder_armed = app._server_recording = True
    process.close_outcome = "exception"
    app._reset_remote_invite()
    qapp.processEvents()
    app.window._load_mix_shortcut.activated.emit()
    assert recorder.phase is RecorderPhase.RECORDING
    app.recording.on_record_requested.assert_not_called()
    app.bridge.find_reference_track_jamulus.assert_not_called()
    assert _gains(pair)[7] == 0
    queued = []

    class HeldRecordingWorker:
        def __init__(self, *, target, args=(), name=None, **kwargs):
            assert name == "record-toggle"
            self.args = args

        def start(self):
            queued.append(self)

    with monkeypatch.context() as stop:
        stop.setattr(recorder, "on_record_requested", MethodType(RecordingCoordinator.on_record_requested, recorder))
        stop.setattr("webjam_qt.controllers.recording_coordinator.threading.Thread", HeldRecordingWorker)
        app.window.session_strip.record_requested.emit()
    assert recorder.phase is RecorderPhase.STOPPING
    assert len(queued) == 1
    attempt = queued[0].args[0]
    assert attempt.target_armed is False and attempt.take_id == recorder._take_id
    recorder.plan_shared_track_for_next_take.assert_not_called()
    assert app._shared_track_play_after_recording == ""
    # Deliver a controlled completion receipt; never execute the capture RPC.
    recorder.on_audio_session_stopped()
    assert recorder.phase is RecorderPhase.IDLE
    assert_fresh_music_intents_refused(pair)
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)


def test_actual_primary_cleanup_keeps_cards_and_room_retry_from_claiming_recovery(
    mixed_room, qapp, monkeypatch,
):
    pair = mixed_room
    app, process = pair.app, pair.native_process
    _save_solo(pair, muted_during_solo=True)
    process.close_outcome = "exception"
    app._reset_remote_invite()
    qapp.processEvents()
    cards = dict(app.window.participant_grid._cards)
    queued = []

    class HeldPrimaryCleanup:
        def __init__(self, *, target, name, **kwargs):
            assert name == "webjam-primary-recovery-cleanup"
            self.target = target

        def start(self):
            queued.append(self.target)

    with monkeypatch.context() as hold:
        hold.setattr("webjam_qt.controllers.application_controller.threading.Thread", HeldPrimaryCleanup)
        assert app._retire_primary_after_recovery_exhaustion(unresponsive=True)
    assert len(queued) == 1 and not app.audio.connected
    pair.mix_socket.messages.clear()
    incoming = [*pair.primary_roster, {"channel_id": 12, "name": "Late collaborator", "is_local": False}]
    app.jamulus._on_rpc_participants_with_source(incoming, pair.primary_identity)
    qapp.processEvents()
    app.audio.refresh_listening_mix()
    assert app.window.participant_grid._cards == cards
    assert not app.audio.connected
    # The still-owned engine can silence a newly reported row while its stop
    # is pending. That is not UI admission, an audible gain, or a new session.
    assert _gains(pair) == {12: 0}
    with monkeypatch.context() as failure:
        failure.setattr(app.bridge, "stop_jamulus", lambda: False)
        queued[0]()
        qapp.processEvents()
    assert app.audio.cleanup_retry_required and not app.audio.connected
    close_attempts = process.close_attempts
    process.close_outcome = "valid"
    app._reset_remote_invite()
    assert process.close_attempts == close_attempts
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.SESSION_CHANGING
    app.audio.refresh_listening_mix()
    assert app.window.participant_grid._cards == cards
    assert not pair.native_owner.invitation_available
    assert_context_and_source_retained(pair, primary_connected=False)

    # Finish through real Bridge/controller/RPC shutdown. The subprocess is a
    # controlled Popen boundary, but successful UI cleanup cannot stand in for
    # actually retiring the listening dispatcher and its authenticated socket.
    assert app.bridge.jamulus_controller is app.jamulus
    assert pair.mix_rpc.monitor_snapshot().running
    assert not app.jamulus._gain_dispatch_stopped
    socket_closed = Mock(wraps=pair.mix_socket.close)
    finish_ui = app.audio._finish_session_stop_ui
    successful_finishes = []

    def process_reaped(*, timeout):
        assert timeout == 2.0
        pair.primary.poll.return_value = 0
        return 0

    def finish_after_retirement(error, **kwargs):
        if not error:
            assert app.audio.stopping  # Flags have not been cleared yet.
            assert not app.jamulus.running
            assert app.jamulus._rpc_monitor_identity is None
            assert app.jamulus._gain_dispatch_stopped
            assert app.jamulus._gain_pending == {}
            assert app.jamulus._gain_worker is None
            assert app.jamulus._gain_retiring_worker is None
            assert not pair.mix_rpc.monitor_snapshot().running
            assert not pair.mix_rpc.monitor_snapshot().available
            assert not pair.mix_rpc.monitor_snapshot().authenticated
            assert pair.mix_rpc._sock is None
            socket_closed.assert_called_once_with()
            assert app.bridge.jamulus_process is None
            successful_finishes.append(True)
        finish_ui(error, **kwargs)

    with monkeypatch.context() as finish:
        finish.setattr(app.bridge, "stop_jamulus", MethodType(BridgeService.stop_jamulus, app.bridge))
        finish.setattr(pair.primary, "wait", Mock(side_effect=process_reaped))
        finish.setattr(pair.mix_socket, "close", socket_closed)
        finish.setattr(app.audio, "_finish_session_stop_ui", finish_after_retirement)
        finish.setattr(app.bridge, "stop_hosted_server", Mock(return_value=True))
        finish.setattr(app.bridge, "hosted_server_alive", Mock(return_value=False))
        finish.setattr(app.recording, "stop_server_recording_for_shutdown", Mock(return_value=True))
        app.audio.retry_stop()
        qapp.processEvents()
        pair.primary.terminate.assert_called_once_with()
        pair.primary.wait.assert_called_once_with(timeout=2.0)
        pair.primary.kill.assert_not_called()
    assert successful_finishes == [True]
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    assert not app.audio.connected and app._remote_invite_owner is None

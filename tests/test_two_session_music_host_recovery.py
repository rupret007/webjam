"""Native Music reset receipts gate fresh work without hiding active-take Stop.

The controller, native host owner, and decoded Shared Track are production
objects. Native IPC, devices, primary RPC, and recording workers are synthetic;
no process, socket, live audio, meeting, or capture is started by these journeys.
"""

from dataclasses import replace
from types import MethodType
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from core.reference_track import ReferenceTrackState
from core.session_conductor import SessionConductorPhase
from core.session_lifecycle import SessionLifecyclePhase
from core.settings import load_settings, save_settings
from services import native_remote_transport as native
from services.remote_session_runtime import RemoteSessionErrorCode, RemoteSessionPhase
from services.transport_runtime import TransportEvent, TransportProcessError
from tests.test_native_remote_transport import FakeProcess
from tests.test_reference_track import _Backend, _context
from tests.test_shared_track_host_support_journey import (
    _settle,
    host as _host,
    no_unhandled_qt_slot_errors as _qt_errors,
    qapp as _qapp,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import RecorderPhase, RecordingCoordinator
from webjam_qt.windows.reference_track import ReferenceTrackPrimaryGate


host = _host
qapp = _qapp
no_unhandled_qt_slot_errors = _qt_errors
PERSONAL = "https://personal.webex.com/meet/personal"
ROOM = "https://meet.google.com/abc-defg-hij"


class ResetProcess(FakeProcess):
    close_outcome = "valid"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.close_attempts = 0

    def close_peer(self):
        self.close_attempts += 1
        if self.close_outcome == "exception":
            raise TransportProcessError("Synthetic local cleanup did not finish.")
        receipt = super().close_peer()
        if self.close_outcome == "wrong_generation":
            return replace(receipt, generation=receipt.generation + 1)
        return receipt

    def publish_room_state(self, state, *, generation):
        return TransportEvent(
            event_id=71, request_id=71, event_type="room_state_accepted", code="ok",
            state="connected", mode="host", profile_id="reference-local", generation=generation,
        )


@pytest.fixture
def native_music(host, qapp, monkeypatch):
    pair = host("darwin", ready=True)
    app: ApplicationController = pair.app
    for name in ("_reconnect_timer", "_level_timer", "_connection_timer"):
        getattr(app, name).stop()
    app.settings.webex_url = PERSONAL
    app.settings.server_rpc_secret_file = str(pair.source.parent / "synthetic-rpc-secret")
    save_settings(app.settings)
    app._set_session_meeting_url(ROOM)
    app.bridge.launch_webex = Mock()
    app.bridge.jamulus_state = "Running"
    # The first side-effect boundary after the production Play gate: inspect
    # whether it is reached, but never resolve or start a real audio component.
    app.bridge.find_reference_track_jamulus = Mock(return_value=None)
    monkeypatch.setattr(native, "TransportProcess", ResetProcess)
    holder = {}

    def receive(snapshot):
        if "owner" in holder:
            app._on_remote_session_snapshot(snapshot, source=holder["owner"])

    owner = native.NativeHostTransportOwner(
        target_port=22124, binary="/private/synthetic-webjam-fabric", expected_build="abc1234",
        on_snapshot=receive, schedule_callback=app._ui_invoker.invoke,
    )
    holder["owner"] = owner
    app._install_remote_invite_owner(owner)
    app._remote_session = owner
    process = FakeProcess.instances[-1]
    process.emit_host_connected(owner.snapshot.generation)
    qapp.processEvents()
    assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
    assert owner.connection_available
    assert app._reference_track_is_host()
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    assert pair.track.snapshot.can_play
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
    pair.native_owner, pair.native_process = owner, process
    yield pair
    process.close_outcome = "valid"
    assert pair.sound.streams == [] and pair.live.calls == []
    pair.prepare.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()


def assert_context_and_source_retained(pair, *, primary_connected=True):
    app = pair.app
    assert app._effective_meeting_url() == ROOM
    assert app.webex.meeting_url == ROOM
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    assert app._reference_track is pair.track
    assert pair.track._stream is pair.stream
    assert pair.track.recording_source_fingerprint() == pair.fingerprint
    assert app.bridge.jamulus_process is pair.primary
    assert app._jamulus_connected is primary_connected


def assert_failed_owner_cannot_publish_invitation(pair, monkeypatch):
    owner, process = pair.native_owner, pair.native_process
    assert owner.snapshot.phase is RemoteSessionPhase.FAILED
    assert owner.snapshot.error_code is RemoteSessionErrorCode.STOP_FAILED
    assert owner.invitation is None and not owner.invitation_available
    assert owner.room_identity is None and not owner.connection_available
    assert process.running and process.host_generations == [1]
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    pair.app._copy_band_invite()
    clipboard.setText.assert_not_called()
    process.emit_host_connected(1)
    assert not owner.connection_available


def assert_fresh_music_intents_refused(pair):
    app = pair.app
    app.recording.on_record_requested.reset_mock()
    app.recording.plan_shared_track_for_next_take.reset_mock()
    app.bridge.find_reference_track_jamulus.reset_mock()
    app._shared_track_play_after_recording = ""
    app.window.session_strip.record_requested.emit()
    app._play_reference_track()
    observed = {
        "record_started": app.recording.on_record_requested.called,
        "play_reached_component_lookup": app.bridge.find_reference_track_jamulus.called,
        "play_after_recording": app._shared_track_play_after_recording,
    }
    assert observed == {
        "record_started": False,
        "play_reached_component_lookup": False,
        "play_after_recording": "",
    }, f"Native cleanup has no valid receipt, so fresh intent must remain refused: {observed}"
    assert_context_and_source_retained(pair)


@pytest.mark.parametrize("bad_receipt", ["exception", "wrong_generation"])
def test_native_music_failed_reset_blocks_new_intent_until_valid_close(
    native_music, qapp, monkeypatch, bad_receipt,
):
    pair = native_music
    app, owner, process = pair.app, pair.native_owner, pair.native_process
    original_identity = owner.room_identity
    process.close_outcome = bad_receipt
    app._reset_remote_invite()
    # Exercise queued UI intentions before failure rendering as well as after.
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert_fresh_music_intents_refused(pair)
    qapp.processEvents()
    assert_fresh_music_intents_refused(pair)

    app._reset_remote_invite()
    assert process.close_attempts == 2
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert_fresh_music_intents_refused(pair)
    process.close_outcome = "valid"
    app._reset_remote_invite()
    qapp.processEvents()
    assert process.close_attempts == 3 and process.host_generations == [1, 2]
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert owner.invitation_available and owner.room_identity != original_identity
    assert not owner.connection_available
    assert app.session_lifecycle.snapshot.phase is not SessionLifecyclePhase.FAILED_RECOVERABLE
    assert app.session_conductor.snapshot.presentation.phase is not SessionConductorPhase.FAILED
    assert_context_and_source_retained(pair)
    app.recording.on_record_requested.assert_not_called()
    app.bridge.find_reference_track_jamulus.assert_not_called()
    assert app._shared_track_play_after_recording == ""

    # Recovery grants eligibility, never replay of a refused click. A new
    # explicit gesture can reach its controlled recording/play boundary.
    process.emit_host_connected(2)
    qapp.processEvents()
    app.window.session_strip.record_requested.emit()
    app.recording.on_record_requested.assert_called_once_with()
    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=True)
    app._play_reference_track()
    app.bridge.find_reference_track_jamulus.assert_called_once_with()


def test_native_music_failed_reset_preserves_real_active_take_stop(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, owner, process = pair.app, pair.native_owner, pair.native_process
    recorder = app.recording
    recorder._take_id = "33333333-3333-3333-3333-333333333333"
    recorder.phase = RecorderPhase.RECORDING
    app._recorder_armed = True
    app._server_recording = True
    process.close_outcome = "exception"
    app._reset_remote_invite()
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert recorder.phase is RecorderPhase.RECORDING
    queued = []

    class DeferredRecordingWorker:
        def __init__(self, *, target, args=(), kwargs=None, name=None, **ignored):
            assert name == "record-toggle", "No service worker belongs in the Stop gesture"
            self.target, self.args = target, args

        def start(self):
            queued.append(self)

    with monkeypatch.context() as stop_patch:
        stop_patch.setattr(
            recorder, "on_record_requested",
            MethodType(RecordingCoordinator.on_record_requested, recorder),
        )
        stop_patch.setattr(
            "webjam_qt.controllers.recording_coordinator.threading.Thread", DeferredRecordingWorker,
        )
        app.window.session_strip.record_requested.emit()
        assert recorder.phase is RecorderPhase.STOPPING
        assert len(queued) == 1
        attempt = queued[0].args[0]
        assert attempt.target_armed is False and attempt.take_id == recorder._take_id
        recorder.plan_shared_track_for_next_take.assert_not_called()
        assert app._shared_track_play_after_recording == ""
        assert_context_and_source_retained(pair)

    # Deliver only the controlled recorder receipt; never execute its queued RPC.
    recorder.on_audio_session_stopped()
    assert recorder.phase is RecorderPhase.IDLE
    assert not app._recorder_armed and not app._server_recording
    assert_fresh_music_intents_refused(pair)
    process.close_outcome = "valid"
    app._reset_remote_invite()
    qapp.processEvents()
    assert process.host_generations == [1, 2] and owner.invitation_available
    assert recorder.phase is RecorderPhase.IDLE
    app.recording.on_record_requested.assert_not_called()
    assert_context_and_source_retained(pair)


def test_native_music_failed_reset_keeps_track_pause_volume_and_stop(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, process = pair.app, pair.native_process
    backend = _Backend()
    monkeypatch.setattr(pair.track, "_backend", backend)
    pair.track.play(_context())
    assert pair.track.snapshot.state is ReferenceTrackState.PLAYING
    route = backend.sessions[0]
    process.close_outcome = "exception"
    app._reset_remote_invite()
    qapp.processEvents()
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert pair.track.snapshot.state is ReferenceTrackState.PLAYING
    assert route.stopped == 0

    app._open_reference_track()
    _settle(qapp, app)
    dialog = app._reference_track_dialog
    assert dialog._pause.isEnabled() and dialog._stop.isEnabled()
    assert not dialog._restart.isEnabled() and not dialog._play.isEnabled()
    assert "Reset Invite" in dialog._route_guidance.text()
    assert "Play again" in dialog._route_guidance.text()
    assert dialog._route_guidance.accessibleDescription() == dialog._route_guidance.text()
    # A concurrent local-engine outage has its own stricter controls. Room
    # recovery copy must not promise working Pause/Stop under that gate.
    for primary_gate in (
        ReferenceTrackPrimaryGate.NOT_CONNECTED,
        ReferenceTrackPrimaryGate.RECOVERING,
    ):
        dialog.set_primary_gate(primary_gate)
        assert not dialog._pause.isEnabled() and not dialog._stop.isEnabled()
        assert not dialog._play.isEnabled() and not dialog._restart.isEnabled()
        assert "Reset Invite" not in dialog._route_guidance.text()
        assert "Reset Invite" not in dialog._play.toolTip()
        assert "Reset Invite" not in dialog._restart.toolTip()
        assert "can still be paused or stopped" not in dialog._route_guidance.text()
    app._sync_reference_track_primary_gate(dialog)
    assert dialog._pause.isEnabled() and dialog._stop.isEnabled()
    assert not dialog._play.isEnabled() and not dialog._restart.isEnabled()
    assert "Reset Invite" in dialog._route_guidance.text()
    dialog._pause.click()
    _settle(qapp, app)
    assert pair.track.snapshot.state is ReferenceTrackState.PAUSED
    assert dialog._trim.isEnabled() and dialog._stop.isEnabled()
    assert not dialog._play.isEnabled() and not dialog._restart.isEnabled()
    dialog._trim.setValue(-6)
    dialog._trim.editingFinished.emit()
    _settle(qapp, app)
    assert pair.track.snapshot.trim_db == -6

    # Disabled controls must also reject delayed semantic requests. The
    # controller owns the raw signal guard; the dialog owns its button emitters.
    dialog._emit_play()
    dialog._emit_restart()
    dialog.restart_requested.emit()
    assert pair.track.snapshot.state is ReferenceTrackState.PAUSED
    assert len(backend.prepared) == 1 and route.started == 1
    dialog._stop.click()
    _settle(qapp, app)
    assert route.stopped == 1
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    assert_fresh_music_intents_refused(pair)


def test_native_music_queued_old_play_does_not_start_in_replacement_room(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, process = pair.app, pair.native_process
    backend = _Backend()
    monkeypatch.setattr(pair.track, "_backend", backend)
    app.bridge.find_reference_track_jamulus.return_value = "/private/synthetic-track-client"
    real_start = app._start_reference_track_worker
    queued = []
    with monkeypatch.context() as hold:
        hold.setattr(
            app, "_start_reference_track_worker",
            lambda operation, *, thread_name: queued.append((operation, thread_name)),
        )
        app._play_reference_track()
        assert len(queued) == 1
        assert backend.prepared == []
        process.close_outcome = "exception"
        app._reset_remote_invite()
        assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
        process.close_outcome = "valid"
        app._reset_remote_invite()
        process.emit_host_connected(2)
        qapp.processEvents()
        assert pair.native_owner.connection_available
    operation, thread_name = queued[0]
    real_start(operation, thread_name=thread_name)
    _settle(qapp, app)
    assert backend.prepared == [] and backend.sessions == []
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    assert_context_and_source_retained(pair)

    # A fresh explicit action belongs to the replacement room and reaches the
    # fake audio session. Its callback is retained, never played or captured.
    app._play_reference_track()
    _settle(qapp, app)
    assert pair.track.snapshot.state is ReferenceTrackState.PLAYING
    assert len(backend.prepared) == 1 and backend.sessions[0].started == 1
    app._request_reference_track_teardown()
    _settle(qapp, app)
    assert backend.sessions[0].stopped == 1
    assert_context_and_source_retained(pair)


def test_native_music_published_track_survives_reset_before_worker_postcheck(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, process = pair.app, pair.native_process
    backend = _Backend()
    monkeypatch.setattr(pair.track, "_backend", backend)
    app.bridge.find_reference_track_jamulus.return_value = "/private/synthetic-track-client"
    queued = []
    real_play = pair.track.play

    def publish_then_reset(context):
        result = real_play(context)
        assert result.state is ReferenceTrackState.PLAYING
        process.close_outcome = "exception"
        app._reset_remote_invite()
        assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
        return result

    # Execute the real queued operation on this controlled UI thread to place
    # Reset exactly after core publication and before the worker's postcheck.
    # No Qt control is touched from an audio worker in this simulation.
    with monkeypatch.context() as hold:
        hold.setattr(pair.track, "play", publish_then_reset)
        hold.setattr(
            app, "_start_reference_track_worker",
            lambda operation, *, thread_name: queued.append(operation),
        )
        app._play_reference_track()
        assert len(queued) == 1
        try:
            queued[0]()
        finally:
            with app._reference_track_worker_state_lock:
                app._reference_track_operation_inflight = False
                app._reference_track_operation_kind = ""
    qapp.processEvents()
    assert pair.track.snapshot.state is ReferenceTrackState.PLAYING
    assert backend.sessions[0].started == 1 and backend.sessions[0].stopped == 0
    assert_context_and_source_retained(pair)
    app._request_reference_track_teardown()
    _settle(qapp, app)
    assert backend.sessions[0].stopped == 1
    assert pair.track.snapshot.state is ReferenceTrackState.READY


def test_native_invite_recovery_preserves_independent_primary_recovery_failure(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, process = pair.app, pair.native_process
    process.close_outcome = "exception"
    app._reset_remote_invite()
    assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
    # Deliver a real exhausted-primary cleanup operation through synthetic
    # local process receipts. The loaded source remains retained by that path.
    workers = []

    class DeferredPrimaryCleanup:
        def __init__(self, *, target, name, **ignored):
            assert name == "webjam-primary-recovery-cleanup"
            self.target = target

        def start(self):
            workers.append(self.target)

    with monkeypatch.context() as hold:
        hold.setattr(
            "webjam_qt.controllers.application_controller.threading.Thread", DeferredPrimaryCleanup,
        )
        assert app._retire_primary_after_recovery_exhaustion(unresponsive=True)
        assert len(workers) == 1
    workers[0]()
    qapp.processEvents()
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.RECOVERY_FAILED
    app.bridge.stop_jamulus.assert_called_once_with()
    failure = app.session_lifecycle.snapshot
    assert failure.phase is SessionLifecyclePhase.FAILED_RECOVERABLE
    assert failure.recovery_attempt == 5
    app._render_session_conductor()
    conductor = app.session_conductor.snapshot
    assert conductor.presentation.phase is SessionConductorPhase.FAILED

    process.close_outcome = "valid"
    app._reset_remote_invite()
    assert pair.native_owner.invitation_available
    process.emit_host_connected(2)
    qapp.processEvents()
    assert pair.native_owner.connection_available
    assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.RECOVERY_FAILED
    assert app.session_lifecycle.snapshot == failure
    assert app.session_conductor.snapshot.token == conductor.token
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.FAILED
    app.bridge.find_reference_track_jamulus.reset_mock()
    app._play_reference_track()
    app.bridge.find_reference_track_jamulus.assert_not_called()
    assert_context_and_source_retained(pair, primary_connected=False)


def test_native_invite_recovery_does_not_replace_owned_startup_attempt(
    native_music, qapp, monkeypatch,
):
    pair = native_music
    app, process = pair.app, pair.native_process
    # A real explicit Start creates the production attempt and conductor token;
    # stop at its existing server-launch seam before any process can start.
    launch = Mock()
    with monkeypatch.context() as hold:
        hold.setattr(app, "_start_hosted_server_for_startup", launch)
        assert app._begin_explicit_startup_journey()
    attempt = app._startup_attempt
    assert attempt is not None and attempt["phase"] == "starting_server"
    launch.assert_called_once_with(attempt["generation"])
    token = attempt["conductor_token"]
    assert app.session_conductor.snapshot.token == token
    try:
        process.close_outcome = "exception"
        app._reset_remote_invite()
        assert_failed_owner_cannot_publish_invitation(pair, monkeypatch)
        qapp.processEvents()
        assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
        process.close_outcome = "valid"
        app._reset_remote_invite()
        process.emit_host_connected(2)
        qapp.processEvents()
        assert pair.native_owner.connection_available
        assert app._startup_attempt is attempt
        assert attempt["phase"] == "starting_server"
        assert attempt["conductor_token"] == token
        assert app.session_conductor.snapshot.token == token
        launch.assert_called_once()
        assert_context_and_source_retained(pair)
    finally:
        # The controlled server-launch handoff was never executed, so there is
        # no startup worker to finish this test-owned attempt during teardown.
        app._startup_attempt = None

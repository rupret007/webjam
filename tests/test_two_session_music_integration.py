"""Music invitation ownership survives track recovery without recording intent.

Synthetic files and controlled service receipts exercise the real controller,
recorder Stop decision, and host-to-guest transition. No live services run.
"""

from types import MethodType
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.meeting_companion import build_invite_message
from core.network_invite import create_invite_link
from core.reference_track import ReferenceTrackState
from core.settings import load_settings, save_settings
from tests.test_shared_track_host_support_journey import (
    host as _host,
    no_unhandled_qt_slot_errors as _no_unhandled_qt_slot_errors,
    qapp as _qapp,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import (
    RecorderPhase,
    RecordingCoordinator,
)
from webjam_qt.windows.reference_track import ReferenceTrackPrimaryGate


host = _host
qapp = _qapp
no_unhandled_qt_slot_errors = _no_unhandled_qt_slot_errors
PERSONAL = "https://personal.webex.com/meet/personal"
ROOM = "https://meet.google.com/abc-defg-hij"
REPLACEMENT = "https://next.webex.com/meet/rehearsal"


def _invitation(meeting: str, *, replacement: bool = False) -> str:
    return build_invite_message(
        join_link=create_invite_link(
            "192.168.1.43" if replacement else "192.168.1.42",
            session_name="Next rehearsal" if replacement else "Shared rehearsal",
            session_id=("22222222-2222-2222-2222-222222222222" if replacement
                        else "11111111-1111-1111-1111-111111111111"),
            peer_port=42001,
            invite_token=("b" if replacement else "a") * 64,
        ),
        creator_profile_key="music", meeting_url=meeting,
    ).text


def _assert_meeting(app: ApplicationController, expected: str) -> None:
    assert app._effective_meeting_url() == expected
    assert app.webex.meeting_url == expected
    assert app._effective_band_check_settings().webex_url == expected
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()


def _assert_record_refused(app: ApplicationController) -> None:
    app.recording.on_record_requested.reset_mock()
    app.recording.plan_shared_track_for_next_take.reset_mock()
    app._show_actionable_error.reset_mock()
    app._shared_track_play_after_recording = "play"
    app.window.session_strip.record_requested.emit()
    app.recording.on_record_requested.assert_not_called()
    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=False)
    assert app._shared_track_play_after_recording == ""
    app._show_actionable_error.assert_called_once()


@pytest.mark.parametrize("last_meeting", [REPLACEMENT, ""])
def test_music_track_recovery_stop_and_room_replacement_keep_separate_ownership(
    host, qapp, monkeypatch, last_meeting,
):
    pair = host("darwin", ready=True)
    app: ApplicationController = pair.app
    app.settings.webex_url = PERSONAL
    app.settings.server_rpc_secret_file = str(pair.source.parent / "synthetic-rpc-secret")
    save_settings(app.settings)
    app._set_session_meeting_url(None)
    app.bridge.launch_webex = Mock()
    app.begin_startup_journey = Mock(return_value=True)
    _assert_meeting(app, PERSONAL)
    assert app.settings.host_server_enabled

    # One selected replacement moves through queued, loading, and failed.
    # None of those transitions can replay the host's refused Record click.
    operations = []
    from core import reference_track

    decoder = reference_track.ReferenceTrackDecoder

    def fail_selected_decode(path):
        assert pair.track.snapshot.state is ReferenceTrackState.LOADING
        _assert_record_refused(app)
        _assert_meeting(app, PERSONAL)
        return decoder(path)

    with monkeypatch.context() as load_patch:
        load_patch.setattr(
            app, "_start_reference_track_worker",
            lambda operation, **kwargs: operations.append(operation),
        )
        load_patch.setattr(reference_track, "ReferenceTrackDecoder", fail_selected_decode)
        with app._reference_track_worker_state_lock:
            app._reference_track_operation_inflight = True
            app._reference_track_operation_kind = "route-check"
        try:
            app._load_reference_track(str(pair.source.parent / "missing replacement.wav"))
            assert app._reference_track_load_pending is not None
            assert pair.track.snapshot.state is ReferenceTrackState.READY
            _assert_record_refused(app)
            app._finish_reference_track_operation()
            assert app._reference_track_operation_kind == "load"
            assert len(operations) == 1
            operations.pop()()
        finally:
            app._finish_reference_track_operation()
        qapp.processEvents()
        # Presenting recovery can enqueue the existing safe capability check.
        # Complete that controlled work too, rather than strand its worker slot.
        for _ in range(3):
            if not operations:
                break
            operations.pop(0)()
            app._finish_reference_track_operation()
            qapp.processEvents()
        assert not operations and not app._reference_track_operation_inflight
    assert pair.track.snapshot.state is ReferenceTrackState.FAILED
    assert pair.track._stream is pair.stream
    assert pair.track.recording_source_fingerprint() == pair.fingerprint
    failed_snapshot = pair.track.snapshot
    _assert_record_refused(app)
    _assert_meeting(app, PERSONAL)
    app.bridge.jamulus_state = "Running"
    assert app._is_jamulus_running()

    workers = []

    class DeferredThread:
        def __init__(self, *, target, args=(), kwargs=None, name=None, **ignored):
            self.target, self.args, self.kwargs, self.name = target, args, kwargs or {}, name

        def start(self):
            if self.name in {
                "webjam-reference-track-session-stop",
                "webjam-reference-track-route-check",
                "webjam-reference-track-load",
            }:
                # Settings reconfiguration retires the retained track and
                # refreshes its fake capability. These operations are safe
                # and bounded; only actual service workers remain deferred.
                self.target(*self.args, **self.kwargs)
                return
            assert self.name in {"webjam-invite-switch", "webjam-session-stop", "record-toggle"}
            workers.append(self)

    def finish_worker(name):
        assert len(workers) == 1
        worker = workers.pop()
        assert worker.name == name
        worker.target(*worker.args, **worker.kwargs)
        qapp.processEvents()

    real_track_stop = app._stop_reference_track_for_session_end
    cleanup_receipts = iter((False, True))

    def track_cleanup(*, background):
        if not background and not next(cleanup_receipts, True):
            return False
        return real_track_stop(background=background)

    def stop_primary():
        app.bridge.jamulus_process = None
        app.bridge.jamulus_state = "Stopped"
        app._jamulus_connected = False
        return True

    with monkeypatch.context() as lifecycle_patch:
        lifecycle_patch.setattr(
            "webjam_qt.controllers.application_controller.threading.Thread", DeferredThread,
        )
        lifecycle_patch.setattr(app._ui_invoker, "invoke", lambda callback: callback())
        lifecycle_patch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes))
        lifecycle_patch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
        lifecycle_patch.setattr(app, "_stop_reference_track_for_session_end", track_cleanup)
        lifecycle_patch.setattr(app.bridge, "hosted_server_alive", Mock(return_value=False))
        lifecycle_patch.setattr(app.bridge, "hosted_server_owned", Mock(return_value=False))
        lifecycle_patch.setattr(app.bridge, "stop_hosted_server", Mock(return_value=True))
        lifecycle_patch.setattr(app.bridge, "stop_jamulus", Mock(side_effect=stop_primary))
        lifecycle_patch.setattr(app.recording, "stop_server_recording_for_shutdown", Mock(return_value=True))

        assert app.accept_invite_url(_invitation(ROOM))
        _assert_meeting(app, PERSONAL)
        finish_worker("webjam-invite-switch")
        assert app.audio.cleanup_retry_required
        assert app.settings.host_server_enabled
        assert app.bridge.jamulus_process is pair.primary
        _assert_meeting(app, PERSONAL)
        app.begin_startup_journey.assert_not_called()

        # Exercise the production recorder decision, not only a mocked Record
        # callback: the active take queues a Stop attempt while recovery blocks
        # new work. The controlled worker boundary performs no RPC or capture.
        recorder = app.recording
        lifecycle_patch.setattr(
            recorder, "on_record_requested",
            MethodType(RecordingCoordinator.on_record_requested, recorder),
        )
        recorder._take_id = "33333333-3333-3333-3333-333333333333"
        recorder.phase = RecorderPhase.RECORDING
        app._recorder_armed = True
        app._server_recording = True
        app._show_actionable_error.reset_mock()
        recorder.plan_shared_track_for_next_take.reset_mock()
        app.window.session_strip.record_requested.emit()
        assert recorder.phase is RecorderPhase.STOPPING
        assert len(workers) == 1 and workers[0].name == "record-toggle"
        attempt = workers.pop().args[0]
        assert attempt.target_armed is False
        assert attempt.take_id == recorder._take_id
        recorder.plan_shared_track_for_next_take.assert_not_called()
        app._show_actionable_error.assert_not_called()
        assert app._shared_track_play_after_recording == ""
        _assert_meeting(app, PERSONAL)

        # The synthetic receipt retires the simulated take; it does not run
        # the deferred RPC. Real session cleanup then permits a fresh invitation.
        recorder.on_audio_session_stopped()
        assert recorder.phase is RecorderPhase.IDLE
        assert not app._recorder_armed and not app._server_recording
        app.audio.retry_stop()
        finish_worker("webjam-session-stop")
        assert not app.audio.cleanup_retry_required
        _assert_meeting(app, PERSONAL)
        assert app.accept_invite_url(_invitation(ROOM))
        if workers:
            finish_worker("webjam-invite-switch")
        assert not app.settings.host_server_enabled
        assert app.creator_profile.key == "music"
        assert not app._reference_track_is_host()
        _assert_meeting(app, ROOM)

        assert app.accept_invite_url(_invitation(last_meeting, replacement=True))
        if workers:
            _assert_meeting(app, ROOM)
            finish_worker("webjam-invite-switch")
        assert not app.settings.host_server_enabled
        _assert_meeting(app, last_meeting)
        assert app._reference_track_primary_gate() is not ReferenceTrackPrimaryGate.READY
        app._on_reference_track_snapshot(failed_snapshot)
        qapp.processEvents()
        _assert_meeting(app, last_meeting)
        assert app._shared_track_play_after_recording == ""
        assert workers == []

        app.audio._begin_session_stop(False)
        _assert_meeting(app, last_meeting)
        finish_worker("webjam-session-stop")
        assert not app.audio.cleanup_retry_required
        _assert_meeting(app, PERSONAL)
        assert workers == []

    assert pair.sound.streams == [] and pair.live.calls == []
    pair.prepare.assert_not_called()
    app.bridge.launch_webex.assert_not_called()

"""Real host/dialog journeys distinguish missing routes from absent backends.

Production capability selection and a synthetic decoded WAV are real. Device
inventories and primary-process evidence are controlled; no live audio runs.
"""

from dataclasses import replace
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.reference_track import ReferenceTrackState
from core.settings import AppSettings
from services.reference_track_backend import (
    MacOSBlackHoleReferenceBackend,
    _UnavailableReferenceBackend,
)
from tests.test_reference_track import _audio_file
from tests.test_reference_track_application_integration import _set_primary_rpc
from tests.test_reference_track_backend import _LiveRouteProbe, _SoundDevice, _device, _scan
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import RecorderPhase
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.reference_track import ReferenceTrackPrimaryGate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_unhandled_qt_slot_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(
        sys, "excepthook", lambda error_type, error, traceback: errors.append(error_type.__name__),
    )
    yield
    assert errors == [], f"Qt callbacks must not hide rejected actions: {errors}"


def _settle(qapp, app):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not app._reference_track_operation_inflight:
            qapp.processEvents()
            if not app._reference_track_operation_inflight:
                return
        time.sleep(.005)
    raise AssertionError("Shared Track operation did not finish")


class TrackJourney(SimpleNamespace):
    def __repr__(self):
        return f"TrackJourney(platform={self.platform!r})"


@pytest.fixture
def host(qapp, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: True)
    external = Mock(side_effect=AssertionError("Support navigation must not launch anything"))
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", external)

    def create(platform, *, ready=False, loaded=True):
        root = tmp_path / str(len(made))
        root.mkdir()
        sound, live = _SoundDevice(), _LiveRouteProbe()
        if platform == "darwin":
            devices = (_device(),) if ready else ()
            backend = MacOSBlackHoleReferenceBackend(
                platform="darwin", scanner=lambda: _scan(*devices),
                sounddevice_module=sound, process_route_probe=live,
            )
        else:
            backend = _UnavailableReferenceBackend(platform)
        prepare = Mock(side_effect=AssertionError("This journey must not prepare audio"))
        monkeypatch.setattr(backend, "prepare", prepare)
        monkeypatch.setattr(
            "services.reference_track_backend.create_reference_audio_backend", lambda: backend,
        )
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            host_server_enabled=True, last_creator_profile_key="music",
            local_capture_choice_made=True,
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Rehearsal",
        )
        app = ApplicationController(window, settings=settings)
        made.append(app)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.window.flash_message = Mock()
        app._show_actionable_error = Mock()
        app.recording.on_record_requested = Mock()
        app.recording.plan_shared_track_for_next_take = Mock()
        app.bridge.stop_jamulus = Mock(return_value=True)
        process = MagicMock()
        process.pid = 4241
        process.poll.return_value = None
        app.bridge.jamulus_process = process
        app._jamulus_connected = True
        rpc = _set_primary_rpc(app)
        assert app._reference_track_primary_gate() is ReferenceTrackPrimaryGate.READY
        window.resize(1120, 800)
        window.show()
        window.activateWindow()
        source = _audio_file(root / "Synthetic rehearsal.wav", samplerate=48000, channels=2)
        if loaded:
            app._load_reference_track(str(source))
            _settle(qapp, app)
        track = app._reference_track_controller()
        assert track.snapshot.state is (ReferenceTrackState.READY if loaded else ReferenceTrackState.IDLE)
        assert track.snapshot.loaded is loaded
        app.window.session_strip.set_tools_enabled(True)
        return TrackJourney(
            app=app, platform=platform, backend=backend, prepare=prepare, source=source,
            track=track, stream=track._stream, fingerprint=track._source_fingerprint_sha256,
            primary=process, rpc=rpc, sound=sound, live=live,
        )

    yield create
    for app in reversed(made):
        _settle(qapp, app)
        app.bridge.jamulus_process = None
        assert app.shutdown()
        app.window.close()
        app.window.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    external.assert_not_called()


def _assert_rehearsal_unchanged(pair):
    app = pair.app
    assert app._reference_track is pair.track
    assert pair.track._stream is pair.stream
    assert pair.track._source_fingerprint_sha256 == pair.fingerprint
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    assert app.bridge.jamulus_process is pair.primary
    assert app._jamulus_connected
    assert pair.sound.streams == [] and pair.live.calls == []
    pair.prepare.assert_not_called()
    app.bridge.stop_jamulus.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "linux", "unknown-platform"])
@pytest.mark.parametrize("entry", ["support", "transport"])
def test_unsupported_host_support_and_back_preserve_track_and_rehearsal(
    host, qapp, monkeypatch, platform, entry,
):
    pair = host(platform)
    app, track = pair.app, pair.track
    dialog = app._reference_track_dialog
    assert dialog is not None and dialog.isVisible()
    assert any(word in dialog._route.text().casefold() for word in ("not available", "cannot send"))
    assert dialog._done.text() == "Back to rehearsal"
    assert not dialog._play.isEnabled()
    assert not dialog._recheck_route.isVisible() and not dialog._recheck_route.isEnabled()
    assert not dialog._blackhole_setup.isVisible()
    assert dialog._load.isEnabled() and dialog._remove.isEnabled()
    assert "Recheck Route" not in dialog._route_guidance.text()
    app.recording.on_record_requested.assert_not_called()
    refresh = Mock(wraps=track.refresh_capability)
    monkeypatch.setattr(track, "refresh_capability", refresh)
    # A stale semantic intent cannot restart the impossible setup loop.
    dialog.recheck_route_requested.emit()
    _settle(qapp, app)
    refresh.assert_not_called()

    QTest.mouseClick(dialog._done, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not dialog.isVisible()
    # Repeated unchanged snapshots must respect the host's explicit return.
    for _ in range(3):
        app._render_reference_track_snapshot(track.snapshot)
    assert not dialog.isVisible()
    strip = app.window.session_strip
    button = strip._reference_track_button if entry == "support" else strip._shared_track_transport
    assert button.isVisibleTo(app.window) and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    _settle(qapp, app)
    assert app._reference_track_dialog is dialog and dialog.isVisible()
    QTest.mouseClick(dialog._done, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not dialog.isVisible()
    _assert_rehearsal_unchanged(pair)
    app.recording.on_record_requested.assert_not_called()
    app.recording.plan_shared_track_for_next_take.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "linux", "unknown-platform"])
def test_loaded_unsupported_track_refuses_recording_without_mac_setup_loop(host, platform):
    pair = host(platform)
    app = pair.app
    app._on_record_requested()

    app.recording.on_record_requested.assert_not_called()
    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=False)
    assert app._shared_track_play_after_recording == ""
    app._show_actionable_error.assert_called_once()
    args, kwargs = app._show_actionable_error.call_args
    copy = " ".join([str(item) for item in args] + [str(item) for item in kwargs.values()])
    assert "No recorder was started" in copy
    assert "remove" in copy.casefold()
    for impossible in ("on this Mac", "BlackHole", "Recheck Route", "signed catalog"):
        assert impossible not in copy
    _assert_rehearsal_unchanged(pair)


def test_explicit_remove_allows_recording_without_discarding_the_original_file(host, qapp):
    pair = host("win32")
    app = pair.app
    source_bytes = pair.source.read_bytes()
    QTest.mouseClick(app._reference_track_dialog._remove, Qt.MouseButton.LeftButton)
    _settle(qapp, app)
    assert not pair.track.snapshot.loaded
    assert pair.source.read_bytes() == source_bytes
    app._on_record_requested()

    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=False)
    app.recording.on_record_requested.assert_called_once_with()
    app._show_actionable_error.assert_not_called()
    assert app._shared_track_play_after_recording == ""
    assert app.bridge.jamulus_process is pair.primary and app._jamulus_connected
    app.bridge.stop_jamulus.assert_not_called()
    pair.prepare.assert_not_called()


def test_mac_missing_route_retains_setup_and_refuses_recording(host, qapp):
    pair = host("darwin")
    app, dialog = pair.app, pair.app._reference_track_dialog
    assert dialog._blackhole_setup.isVisible() and dialog._blackhole_setup.isEnabled()
    assert dialog._recheck_route.isVisible() and dialog._recheck_route.isEnabled()
    assert not dialog._play.isEnabled()
    QTest.mouseClick(dialog._recheck_route, Qt.MouseButton.LeftButton)
    _settle(qapp, app)
    assert not dialog._play.isEnabled()
    app._on_record_requested()
    app.recording.on_record_requested.assert_not_called()
    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=False)
    assert "BlackHole" in app._show_actionable_error.call_args.kwargs["likely_cause"]
    assert "Recheck Route" in app._show_actionable_error.call_args.kwargs["next_action"]
    _assert_rehearsal_unchanged(pair)


@pytest.mark.parametrize("gate", ["ready", "rpc_stale", "guest", "cleanup"])
def test_mac_route_support_preserves_primary_gates_and_recording_plan(host, qapp, gate):
    pair = host("darwin", ready=True)
    app = pair.app
    app._open_reference_track()
    _settle(qapp, app)
    dialog = app._reference_track_dialog
    assert dialog._play.isEnabled()
    if gate == "rpc_stale":
        pair.rpc.last_activity_age.return_value = 60.0
    elif gate == "guest":
        app.settings.host_server_enabled = False
    elif gate == "cleanup":
        app.audio.cleanup_retry_required = True
    try:
        app._sync_reference_track_primary_gate(dialog)
        assert dialog._play.isEnabled() is (gate == "ready")
        if gate == "ready":
            # Explicit Record retains the separate-track requirement. The
            # coordinator owns when recording and reference playback start.
            app._on_record_requested()
            app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=True)
            app.recording.on_record_requested.assert_called_once_with()
            assert app._shared_track_play_after_recording == "play"
        else:
            dialog.play_requested.emit()
            app.recording.on_record_requested.assert_not_called()
        _assert_rehearsal_unchanged(pair)
    finally:
        app.audio.cleanup_retry_required = False
        app.settings.host_server_enabled = True


def _assert_record_refused(app):
    app.recording.on_record_requested.assert_not_called()
    app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=False)
    assert app._shared_track_play_after_recording == ""
    app._show_actionable_error.assert_called_once()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_failed_replacement_keeps_prior_source_required_before_recording(host, qapp, platform):
    pair = host(platform, ready=platform == "darwin")
    app = pair.app
    app._load_reference_track(str(pair.source.parent / "missing replacement.wav"))
    _settle(qapp, app)
    snapshot = pair.track.snapshot
    assert snapshot.state is ReferenceTrackState.FAILED
    assert snapshot.loaded and not snapshot.can_play
    assert pair.track._stream is pair.stream
    assert pair.track.recording_source_fingerprint() == pair.fingerprint
    app._shared_track_play_after_recording = "play"

    app.window.session_strip.record_requested.emit()

    _assert_record_refused(app)
    assert pair.track.snapshot.state is ReferenceTrackState.FAILED
    assert pair.track._stream is pair.stream
    assert pair.track.recording_source_fingerprint() == pair.fingerprint
    pair.prepare.assert_not_called()
    app.bridge.stop_jamulus.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
@pytest.mark.parametrize("replacement", [False, True])
def test_loading_source_cannot_be_silently_omitted_from_recording(
    host, qapp, monkeypatch, platform, replacement,
):
    from core import reference_track

    pair = host(platform, ready=platform == "darwin", loaded=replacement)
    app = pair.app
    decoder = reference_track.ReferenceTrackDecoder
    entered, release = threading.Event(), threading.Event()

    def held_decoder(path):
        entered.set()
        assert release.wait(3), "The test must release its bounded decoder"
        return decoder(path)

    monkeypatch.setattr(reference_track, "ReferenceTrackDecoder", held_decoder)
    app._load_reference_track(str(pair.source))
    try:
        assert entered.wait(1)
        qapp.processEvents()
        snapshot = pair.track.snapshot
        assert snapshot.state is ReferenceTrackState.LOADING
        assert snapshot.loaded is replacement
        assert not snapshot.can_play
        assert pair.track._stream is pair.stream
        assert pair.track.recording_source_fingerprint() == pair.fingerprint
        app._shared_track_play_after_recording = "play"

        app.window.session_strip.record_requested.emit()

        _assert_record_refused(app)
        assert app._reference_track_operation_inflight
    finally:
        release.set()
        _settle(qapp, app)
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    assert pair.track.snapshot.loaded
    # Completion does not replay the refused Record click or start playback.
    app.recording.on_record_requested.assert_not_called()
    assert app._shared_track_play_after_recording == ""
    assert app.bridge.jamulus_process is pair.primary and app._jamulus_connected
    pair.prepare.assert_not_called()
    assert pair.sound.streams == [] and pair.live.calls == []


@pytest.mark.parametrize("state,cleanup", [
    (ReferenceTrackState.STOPPING, False),
    (ReferenceTrackState.FAILED, True),
])
def test_retained_source_waits_for_track_cleanup_before_new_recording(
    host, monkeypatch, state, cleanup,
):
    pair = host("darwin", ready=True)
    app = pair.app
    snapshot = replace(pair.track.snapshot, state=state, cleanup_pending=cleanup)
    assert snapshot.loaded and not snapshot.can_play and snapshot.active
    # Recording consumes a bounded controller snapshot. Keep its genuinely
    # decoded source while representing an owned route awaiting cleanup.
    with monkeypatch.context() as patch:
        patch.setattr(type(pair.track), "snapshot", PropertyMock(return_value=snapshot))
        app._shared_track_play_after_recording = "restart"
        app.window.session_strip.record_requested.emit()
        _assert_record_refused(app)
    assert pair.track._stream is pair.stream
    assert pair.track.recording_source_fingerprint() == pair.fingerprint
    pair.prepare.assert_not_called()
    app.bridge.stop_jamulus.assert_not_called()


@pytest.mark.parametrize("replacement", [False, True])
def test_queued_source_load_cannot_record_old_or_missing_track(host, qapp, replacement):
    pair = host("darwin", ready=True, loaded=replacement)
    app = pair.app
    initial = pair.track.snapshot
    # Hold the existing worker slot without launching another thread. A
    # requested source remains queued while the snapshot is still READY/IDLE.
    with app._reference_track_worker_state_lock:
        app._reference_track_operation_inflight = True
        app._reference_track_operation_kind = "route-check"
    try:
        app._load_reference_track(str(pair.source))
        assert app._reference_track_load_pending is not None
        assert pair.track.snapshot.state is initial.state
        assert pair.track.snapshot.loaded is replacement
        assert pair.track._stream is pair.stream
        app._shared_track_play_after_recording = "play"

        app.window.session_strip.record_requested.emit()

        _assert_record_refused(app)
        assert app._reference_track_load_pending is not None
    finally:
        with app._reference_track_worker_state_lock:
            app._reference_track_operation_inflight = False
            app._reference_track_operation_kind = ""
        app._drain_reference_track_pending()
        _settle(qapp, app)
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    app.recording.on_record_requested.assert_not_called()
    assert app._shared_track_play_after_recording == ""
    pair.prepare.assert_not_called()
    app.bridge.stop_jamulus.assert_not_called()


@pytest.mark.parametrize("state,cleanup", [
    (ReferenceTrackState.FAILED, False),
    (ReferenceTrackState.FAILED, True),
    (ReferenceTrackState.STOPPING, False),
])
def test_active_recording_keeps_stop_when_loaded_track_needs_attention(
    host, monkeypatch, state, cleanup,
):
    pair = host("darwin", ready=True)
    app = pair.app
    snapshot = replace(pair.track.snapshot, state=state, cleanup_pending=cleanup)
    note_cleanup, teardown = Mock(), Mock()
    with monkeypatch.context() as patch:
        patch.setattr(type(pair.track), "snapshot", PropertyMock(return_value=snapshot))
        patch.setattr(app.recording, "phase", RecorderPhase.RECORDING)
        patch.setattr(app, "_recorder_armed", True)
        patch.setattr(app.recording, "note_shared_track_cleanup_requested", note_cleanup)
        patch.setattr(app, "_request_reference_track_teardown", teardown)
        app._shared_track_play_after_recording = "play"

        app.window.session_strip.record_requested.emit()

        app.recording.on_record_requested.assert_called_once_with()
        app.recording.plan_shared_track_for_next_take.assert_not_called()
        app._show_actionable_error.assert_not_called()
        assert app._shared_track_play_after_recording == ""
        assert teardown.call_count == int(snapshot.active)
        assert note_cleanup.call_count == int(snapshot.active)
    _assert_rehearsal_unchanged(pair)


@pytest.mark.parametrize("replacement", [False, True])
def test_accepted_load_worker_blocks_record_before_loading_snapshot(
    host, qapp, monkeypatch, replacement,
):
    pair = host("darwin", ready=True, loaded=replacement)
    app = pair.app
    initial = pair.track.snapshot
    start_worker = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(app, "_start_reference_track_worker", start_worker)
        app._load_reference_track(str(pair.source))
        start_worker.assert_called_once()
        assert app._reference_track_operation_inflight
        assert app._reference_track_operation_kind == "load"
        assert app._reference_track_load_pending is None
        assert pair.track.snapshot.state is initial.state
        try:
            app.window.session_strip.record_requested.emit()
            _assert_record_refused(app)
        finally:
            # Complete the captured, authorized local decode; this does not
            # start a thread, route, recorder, or audio stream.
            start_worker.call_args.args[0]()
            app._finish_reference_track_operation()
            _settle(qapp, app)
    assert pair.track.snapshot.state is ReferenceTrackState.READY
    app.recording.on_record_requested.assert_not_called()
    assert app._shared_track_play_after_recording == ""
    pair.prepare.assert_not_called()


@pytest.mark.parametrize("state", [ReferenceTrackState.ROUTING, ReferenceTrackState.PLAYING])
def test_recording_keeps_already_starting_or_playing_track_required(host, monkeypatch, state):
    pair = host("darwin", ready=True)
    app = pair.app
    snapshot = replace(pair.track.snapshot, state=state)
    assert snapshot.loaded and snapshot.active and not snapshot.can_play
    with monkeypatch.context() as patch:
        patch.setattr(type(pair.track), "snapshot", PropertyMock(return_value=snapshot))
        app.window.session_strip.record_requested.emit()

        app.recording.plan_shared_track_for_next_take.assert_called_once_with(required=True)
        app.recording.on_record_requested.assert_called_once_with()
        app._show_actionable_error.assert_not_called()
        assert app._shared_track_play_after_recording == ""
    _assert_rehearsal_unchanged(pair)

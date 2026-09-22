"""Actual Recording Setup repairs idle owners and refuses stale live changes."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.settings import load_settings, save_settings
from tests.test_art_lan_retry import controllers as _controllers_fixture
from tests.test_art_room_controller import drain, invitation, qapp as _qapp_fixture
from webjam_qt.controllers.recording_coordinator import RecorderPhase
from webjam_qt.windows.recording_setup import RecordingSetupDialog

controllers = _controllers_fixture
qapp = _qapp_fixture


@pytest.fixture
def rig(controllers, monkeypatch):
    for name in ("WEBJAM_AUDIO_SAMPLERATE", "WEBJAM_AUDIO_BLOCKSIZE", "WEBJAM_HOST_SERVER_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    app = controllers()
    old = app.settings
    saved = replace(old, audio_samplerate=44100, audio_blocksize=256, audio_input_device_index=7)
    save_settings(saved)
    app._replace_settings_object(load_settings(saved.config_file))
    app._reconfigure_services_after_settings(old)
    assert app._configure_guest_peer(invitation())
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices",
        lambda: [{"name": "Test interface", "channels": 2, "index": 7}],
    )
    return app


def open_setup(app, monkeypatch, action):
    visits, failures = [], []
    def interact(dialog):
        visits.append(dialog)
        try:
            action(dialog)
            return dialog.result()
        except Exception as error:
            # PySide reports slot exceptions to stderr instead of pytest.
            # Carry interaction assertions back across the actual button click.
            failures.append(error)
            return dialog.DialogCode.Rejected
        finally:
            dialog.deleteLater()
    monkeypatch.setattr(RecordingSetupDialog, "exec", interact)
    app.window.recording_studio._setup_btn.click()
    assert len(visits) == 1
    if failures:
        raise failures[0]


def test_idle_repair_updates_owners_invalidates_checks_and_retires_unused_plan(rig, monkeypatch):
    app = rig
    app.window.session_canvas.edit_notes("Keep this local draft during repair")
    generation = app._settings_generation
    previous = app.settings
    plan = object()
    app.recording._take_id = "retired-format-plan"
    app.recording._recording_plan_take_id = "retired-format-plan"
    app.recording._recording_plan = plan
    app.recording._recording_plan_fingerprint = "old-format"
    monkeypatch.setattr(app.jamulus.audio_engine, "start", Mock())
    monkeypatch.setattr(app, "_record_toggle_worker", Mock())

    def repair(dialog):
        assert dialog._repair_format.isEnabled()
        dialog._repair_format.setChecked(True)
        dialog._save()
        assert dialog.result() == dialog.DialogCode.Accepted
    open_setup(app, monkeypatch, repair)

    assert (app.settings.audio_samplerate, app.settings.audio_blocksize) == (48000, 0)
    assert app.settings is not previous
    assert app.bridge.settings is app.jamulus.settings is app.settings
    assert app.jamulus.audio_engine.settings is app.settings
    assert app._settings_generation > generation
    assert app.recording._recording_plan is None
    assert app.recording._take_id == ""
    assert app.guest_peer.capture_config()[1:] == (48000, 0)
    assert app.window.session_canvas.current_notes() == "Keep this local draft during repair"
    app.jamulus.audio_engine.start.assert_not_called()
    app._record_toggle_worker.assert_not_called()


@pytest.mark.parametrize("busy", [
    "audio", "meter", "monitor", "startup", "host", "stopping", "cleanup",
    "armed", "finalizing", "guest_capture", "native_owner",
])
def test_live_or_uncertain_owner_locks_only_format_repair(rig, monkeypatch, busy):
    app = rig
    with monkeypatch.context() as patch:
        if busy == "audio":
            patch.setattr(app.bridge, "jamulus_state", "Running")
        elif busy == "meter":
            patch.setattr(app.jamulus.audio_engine, "running", True)
        elif busy == "monitor":
            patch.setattr(app.jamulus, "running", True)
        elif busy == "startup":
            patch.setattr(app, "_startup_attempt", {"phase": "client_launch"})
        elif busy == "host":
            patch.setattr(app, "host_peer", SimpleNamespace(active=True))
        elif busy in {"stopping", "cleanup"}:
            patch.setattr(app.audio, "stopping" if busy == "stopping" else "cleanup_retry_required", True)
        elif busy == "armed":
            patch.setattr(app, "_recorder_armed", True)
        elif busy == "finalizing":
            patch.setattr(app.recording, "phase", RecorderPhase.FINALIZING)
        elif busy == "guest_capture":
            patch.setattr(app.guest_peer, "_active_take_id", "guest-take")
        else:
            patch.setattr(app.bridge, "_runtime_component_lifecycle_is_active", lambda: True)
        before = app.settings
        def inspect(dialog):
            assert not dialog._repair_format.isEnabled()
            assert dialog._capture.isEnabled()
            dialog.reject()
        open_setup(app, patch, inspect)
        assert app.settings is before
        assert load_settings(before.config_file).audio_samplerate == 44100


@pytest.mark.parametrize("change", ["audio", "settings"])
def test_modal_owner_change_rejects_selected_repair_before_writing(rig, monkeypatch, change):
    app = rig
    with monkeypatch.context() as patch:
        def changed(dialog):
            assert dialog._repair_format.isEnabled()
            dialog._repair_format.setChecked(True)
            if change == "audio":
                patch.setattr(app.bridge, "jamulus_state", "Running")
            else:
                app._replace_settings_object(replace(app.settings, musician_name="New session owner"))
            dialog._save()
            assert dialog.result() != dialog.DialogCode.Accepted
            assert not dialog._error.isHidden()
            assert load_settings(app.settings.config_file).audio_samplerate == 44100
            dialog.reject()
        open_setup(app, patch, changed)
        assert app.settings.audio_samplerate == 44100


def test_end_cancel_cleanup_retry_then_repair_preserves_notes_and_restarts_only_on_request(
    rig, monkeypatch, qapp,
):
    from PySide6.QtWidgets import QMessageBox
    from core import audio_engine

    app = rig
    backend = Mock()
    monkeypatch.setattr(audio_engine, "sd", backend)
    engine = app.jamulus.audio_engine
    monkeypatch.setattr(engine, "_resolve_device", lambda: 7)
    engine.start()
    app.bridge.jamulus_state = "Running"
    app.audio.connected = True
    app.window.session_canvas.edit_notes("Do not lose this verse while fixing recording")
    assert app._save_notes()
    app._update_session_hud()
    answers = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: next(answers))
    stopped = []

    def stop_owned_audio():
        stopped.append(True)
        if len(stopped) == 1:
            return False
        engine.stop()

        app.bridge.jamulus_state = "Not launched"
        return True

    monkeypatch.setattr(app.bridge, "stop_jamulus", stop_owned_audio)
    app._stop_audio()
    assert stopped == [] and engine.running
    assert app._recording_format_change_blocker()

    app._stop_audio()
    drain(qapp, lambda: app.audio.cleanup_retry_required)
    assert len(stopped) == 1 and engine.running
    assert app.settings.audio_samplerate == 44100
    assert app._recording_format_change_blocker()

    app._stop_audio()  # Existing cleanup retry route; no extra consent prompt.
    drain(qapp, lambda: not app.audio.stopping and not app.audio.cleanup_retry_required)
    assert len(stopped) == 2 and not engine.running
    assert not app._recording_format_change_blocker()
    assert app.window.session_canvas.current_notes() == "Do not lose this verse while fixing recording"

    def repair(dialog):
        assert dialog._repair_format.isEnabled()
        dialog._repair_format.setChecked(True)
        dialog._save()
        assert dialog.result() == dialog.DialogCode.Accepted
    open_setup(app, monkeypatch, repair)
    assert not engine.running
    assert backend.InputStream.call_count == 1
    engine.start()  # Explicit next owned start consumes the committed format.
    try:
        assert backend.InputStream.call_args.kwargs["samplerate"] == 48000
        assert backend.InputStream.call_args.kwargs["blocksize"] == 0
        assert engine.diagnostics().samplerate == 48000
    finally:
        engine.stop()


def test_repair_requires_a_fresh_guest_device_check_before_clearing_failure(rig, monkeypatch, qapp):
    app = rig
    query = Mock(return_value={"max_input_channels": 2})
    check = Mock()
    stream = Mock(side_effect=AssertionError("Setup must not open capture"))
    monkeypatch.setattr("sounddevice.query_devices", query)
    monkeypatch.setattr("sounddevice.check_input_settings", check)
    monkeypatch.setattr("sounddevice.InputStream", stream)
    def opt_in(dialog):
        dialog._capture.setChecked(True)
        dialog._save()
    open_setup(app, monkeypatch, opt_in)
    guest = app.guest_peer
    assert guest._current_local_original_contract() == (True, None, "", None, (), ())
    assert guest.local_capture_preflight.errors == ("unsupported_sample_rate",)
    query.assert_not_called()

    def repair(dialog):
        assert dialog._capture.isChecked()
        dialog._repair_format.setChecked(True)
        dialog._save()
    open_setup(app, monkeypatch, repair)
    assert app.settings.local_capture_enabled
    assert guest.local_capture_preflight is not None
    contract = guest._current_local_original_contract()
    assert contract[0:2] == (True, 2)
    assert guest.local_capture_preflight is None
    check.assert_called_once()
    assert check.call_args.kwargs["samplerate"] == 48000
    assert guest._capture is None and not guest.active_take_id
    stream.assert_not_called()
    qapp.processEvents()


@pytest.mark.parametrize("published", [False, True])
def test_failed_durable_format_save_keeps_runtime_old_until_explicit_retry(rig, monkeypatch, published):
    from core import file_io
    app = rig
    previous = app.settings
    original_write = file_io._atomic_write

    def failed_write(path, data, *, mode):
        if published:
            original_write(path, data, mode=mode)
        raise OSError("PRIVATE_SAVE_ERROR")

    def repair(dialog):
        dialog._repair_format.setChecked(True)
        with monkeypatch.context() as patch:
            patch.setattr(file_io, "_atomic_write", failed_write)
            dialog._save()
        assert dialog.result() != dialog.DialogCode.Accepted
        assert "PRIVATE" not in dialog._error.text()
        assert app.settings is previous
        assert app.jamulus.audio_engine.settings.audio_samplerate == 44100
        assert not app.jamulus.audio_engine.running
        assert dialog._repair_format.isChecked()
        dialog._save()
        assert dialog.result() == dialog.DialogCode.Accepted
    open_setup(app, monkeypatch, repair)
    assert (app.settings.audio_samplerate, app.settings.audio_blocksize) == (48000, 0)


def test_owner_change_after_save_reports_saved_but_unapplied_and_allows_later_retry(rig, monkeypatch):
    app = rig
    message = Mock()
    monkeypatch.setattr(app.window, "flash_message", message)
    with monkeypatch.context() as patch:
        def raced(dialog):
            dialog._repair_format.setChecked(True)
            dialog._save()
            assert dialog.result() == dialog.DialogCode.Accepted
            patch.setattr(app.bridge, "jamulus_state", "Running")
        open_setup(app, patch, raced)
        assert app.settings.audio_samplerate == 44100
        assert load_settings(app.settings.config_file).audio_samplerate == 48000
        assert "saved" in message.call_args.args[0].lower()
        assert "not applied" in message.call_args.args[0].lower()
    def retry(dialog):
        dialog._repair_format.setChecked(True)
        dialog._save()
    open_setup(app, monkeypatch, retry)
    assert app.settings.audio_samplerate == 48000

from dataclasses import replace
from pathlib import Path
import threading
from types import SimpleNamespace
from core.settings import load_settings, save_settings
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from tests.test_art_room_controller import (
    controllers as _controllers_fixture,
    qapp as _qapp_fixture,
)
from tests.test_recording_studio import _schema2_studio_take
from webjam_qt.controllers.recording_coordinator import RecorderPhase
from webjam_qt.windows.recording_setup import LocalOriginalsChoiceDialog

controllers = _controllers_fixture
qapp = _qapp_fixture


@pytest.mark.parametrize("phase", [RecorderPhase.RECORDING, RecorderPhase.STOP_FAILED])
def test_stop_observed_server_recording_does_not_ask_first_take_consent(
    controllers, qapp, monkeypatch, phase
):
    app = controllers(hosting=True)
    assert not app.settings.local_capture_enabled
    assert not app.settings.local_capture_choice_made
    app.window.show()
    app._open_take_deck()
    app.recording.on_server_state(True)
    app.recording._set_phase(phase)
    qapp.processEvents()
    studio = app.window.recording_studio
    assert studio.isVisibleTo(app.window)
    assert studio._record_btn.isVisibleTo(app.window)
    assert studio._record_btn.isEnabled()
    assert studio._record_btn.text() == (
        "■ Finish Stop" if phase is RecorderPhase.STOP_FAILED else "■ Stop Recording"
    )
    chooser = Mock(return_value=LocalOriginalsChoiceDialog.DialogCode.Rejected)
    dispatch_stop = Mock()
    monkeypatch.setattr(LocalOriginalsChoiceDialog, "exec", chooser)
    monkeypatch.setattr(app.recording, "on_record_requested", dispatch_stop)
    try:
        QTest.mouseClick(studio._record_btn, Qt.MouseButton.LeftButton)
        assert chooser.call_count == 0, (
            "Stop Recording incorrectly opened first-time Local Originals consent"
        )
        dispatch_stop.assert_called_once_with()
    finally:
        app._recorder_armed = False
        app._server_recording = False
        app.recording._set_phase(RecorderPhase.IDLE)


@pytest.mark.parametrize("choice", ["shared", "local"])
def test_first_take_choice_still_persists_and_dispatches_current_intent(
    controllers,
    qapp,
    monkeypatch,
    choice,
):
    app = controllers(hosting=True)
    app.window.show()
    app._open_take_deck()
    studio = app.window.recording_studio
    studio.set_live_participants(
        [SimpleNamespace(channel_id=4, name="Host", is_local=True)]
    )
    qapp.processEvents()
    record, setup = Mock(), Mock()
    monkeypatch.setattr(app.recording, "on_record_requested", record)
    monkeypatch.setattr(app, "_open_recording_setup", setup)

    def confirm(dialog):
        if choice == "shared":
            dialog._record_shared()
        else:
            dialog._configure_local()
        return dialog.result()

    monkeypatch.setattr(LocalOriginalsChoiceDialog, "exec", confirm)
    QTest.mouseClick(studio._record_btn, Qt.MouseButton.LeftButton)
    saved = load_settings(app.settings.config_file)
    assert saved.local_capture_choice_made
    assert not saved.local_capture_enabled
    if choice == "shared":
        record.assert_called_once_with()
        setup.assert_not_called()
    else:
        setup.assert_called_once_with()
        record.assert_not_called()


@pytest.mark.parametrize("phase", [RecorderPhase.RECORDING, RecorderPhase.STOP_FAILED])
def test_pending_export_cannot_swallow_stop_at_controller_or_coordinator(
    controllers,
    monkeypatch,
    tmp_path,
    phase,
):
    app = controllers(hosting=True)
    secret = tmp_path / "rpc-secret"
    secret.write_text("fixture-secret")
    app.settings.server_rpc_secret_file = str(secret)
    app.recording.on_server_state(True)
    app.recording._set_phase(phase)
    studio = app.window.recording_studio
    studio._exporting = True
    attempts = []
    dispatched = threading.Event()

    def capture_stop(attempt, secret_file):
        attempts.append(attempt)
        dispatched.set()

    monkeypatch.setattr(app.recording, "_run_toggle_attempt", capture_stop)
    try:
        app._on_record_requested()
        assert dispatched.wait(1), "An export must never gate Stop or Finish Stop"
        assert len(attempts) == 1
        assert attempts[0].target_armed is False
        assert app.recording.phase is RecorderPhase.STOPPING
    finally:
        studio._exporting = False
        app._recorder_armed = False
        app._server_recording = False
        app.recording._set_phase(RecorderPhase.IDLE)


@pytest.mark.parametrize("change", ["recording", "new-live-take", "arrangement"])
def test_export_confirmation_cannot_outlive_its_recording_or_take_context(
    controllers,
    qapp,
    monkeypatch,
    change,
):
    app = controllers(hosting=True)
    app.settings.local_capture_choice_made = True
    app.window.show()
    app._open_take_deck()
    studio = app.window.recording_studio
    _schema2_studio_take(Path(app.settings.takes_directory))
    monkeypatch.setattr(
        "webjam_qt.widgets.recording_studio.studio_export_supported", lambda: False
    )
    studio.reload()
    studio._take_list.setCurrentRow(0)
    studio._lanes[0]._gain.setValue(150)
    qapp.processEvents()
    assert studio._export_btn.isVisibleTo(app.window)
    assert studio._export_btn.isEnabled()

    def change_while_confirming(*args, **kwargs):
        if change == "recording":
            app.recording.on_server_state(True)
        elif change == "new-live-take":
            studio._show_live_session()
        else:
            studio._lanes[0]._gain.setValue(175)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", change_while_confirming)
    try:
        QTest.mouseClick(studio._export_btn, Qt.MouseButton.LeftButton)
        assert not studio.export_in_progress
        assert studio._export_thread is None
        assert "Studio changed" in studio._hint.text()
        if change == "recording":
            assert studio._record_btn.isEnabled()
            assert studio._record_btn.text() == "■ Stop Recording"
    finally:
        app._recorder_armed = False
        app._server_recording = False
        app.recording._set_phase(RecorderPhase.IDLE)


@pytest.mark.parametrize("change", ["settings-owner", "recorder-started"])
def test_first_take_choice_does_not_act_for_replaced_context(
    controllers, qapp, monkeypatch, change
):
    app = controllers(hosting=True)
    save_settings(app.settings)
    app.window.show()
    app._open_take_deck()
    studio = app.window.recording_studio
    studio.set_live_participants(
        [SimpleNamespace(channel_id=4, name="Host", is_local=True)]
    )
    qapp.processEvents()
    assert studio._record_btn.isVisibleTo(app.window)
    assert studio._record_btn.isEnabled()
    assert "Record Session" in studio._record_btn.text()
    dispatch = Mock()
    monkeypatch.setattr(app.recording, "on_record_requested", dispatch)

    def change_context_and_confirm(dialog):
        if change == "settings-owner":
            current = replace(
                app.settings, musician_name="Current guest", host_server_enabled=False
            )
            app._replace_settings_object(current)
            save_settings(current)
        else:
            app.recording.on_server_state(True)
        dialog._record_shared()
        return dialog.result()

    monkeypatch.setattr(LocalOriginalsChoiceDialog, "exec", change_context_and_confirm)
    try:
        QTest.mouseClick(studio._record_btn, Qt.MouseButton.LeftButton)
        if change == "settings-owner":
            saved = load_settings(app.settings.config_file)
            assert saved.musician_name == "Current guest", (
                "Stale consent overwrote the current settings owner on disk"
            )
            assert not saved.host_server_enabled
        else:
            assert app._server_recording
            assert studio._record_btn.text() == "■ Stop Recording"
        dispatch.assert_not_called()
    finally:
        app._recorder_armed = False
        app._server_recording = False
        app.recording._set_phase(RecorderPhase.IDLE)

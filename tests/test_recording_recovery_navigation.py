"""A mode change cannot retire the sole publisher of an interrupted take."""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.take_project import new_project_id
from tests.test_server_rpc_and_record_button import _make_staged_server_take
from tests.test_workspace_navigation import navigation as navigation
from webjam_qt.controllers.recording_coordinator import RecordingCoordinator


# The shared navigation fixture suppresses startup recovery to isolate its
# ordinary cases. Exercise the real method explicitly for these staged takes.
RECOVER = RecordingCoordinator.recover_interrupted_recordings


@pytest.fixture(autouse=True)
def _nonblocking_information(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", Mock())


def _staged(navigation, tmp_path):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    root = tmp_path / "takes"
    root.mkdir()
    take_id = new_project_id()
    take, native = _make_staged_server_take(
        root, "interrupted", session_id=new_project_id(), take_id=take_id,
        client_name="Synthetic musician", port=52000,
    )
    controller.settings.takes_directory = str(root)
    return controller, take, native, take_id


def test_recovery_worker_and_undrained_result_retain_workspace(navigation, tmp_path, monkeypatch):
    navigator, _mailbox, controllers, _qapp = navigation
    controller, take, native, take_id = _staged(navigation, tmp_path)
    recording = controller.recording
    entered, release = threading.Event(), threading.Event()
    original = recording._recover_staged_server_takes_worker
    callbacks = []

    def paused(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    worker = Mock(side_effect=paused)
    monkeypatch.setattr(recording, "_recover_staged_server_takes_worker", worker)
    monkeypatch.setattr(controller._ui_invoker, "invoke", callbacks.append)
    try:
        RECOVER(recording)
        recording._staged_recovery_timer.stop()  # Hold result dispatch deliberately.
        assert entered.wait(2)
        assert recording.workspace_transition_pending
        assert not navigator.return_to_launch()
        assert not controller.shutdown()  # Direct teardown cannot bypass it.
        assert navigator.controller is controller and len(controllers) == 1
        assert not controller._shutdown_cleanup_pending
        assert (take / native).is_file()
        assert (take / ".webjam-recording-staging.json").is_file()
        RECOVER(recording)
        assert worker.call_count == 1
        release.set()
        recording._staged_recovery_worker.join(timeout=3)
        assert not recording._staged_recovery_worker.is_alive()
        assert callbacks and recording.workspace_transition_pending
        assert not navigator.return_to_launch()
        callbacks.pop()()
        assert not recording.workspace_transition_pending
        result = json.loads((take / "webjam-take.json").read_text())
        assert result["take_id"] == take_id
        assert result["status"] == "needs_attention"
        assert not (take / ".webjam-recording-staging.json").exists()
        assert navigator.return_to_launch()
    finally:
        release.set()
        if recording._staged_recovery_worker is not None:
            recording._staged_recovery_worker.join(timeout=3)
        for callback in callbacks:
            callback()


def test_queued_recovery_result_cannot_touch_retired_widgets(navigation, tmp_path, monkeypatch):
    controller, _take, _native, _take_id = _staged(navigation, tmp_path)
    recording = controller.recording
    callbacks = []
    monkeypatch.setattr(controller._ui_invoker, "invoke", callbacks.append)
    RECOVER(recording)
    recording._staged_recovery_timer.stop()
    recording._staged_recovery_worker.join(timeout=3)
    assert callbacks
    finish = Mock()
    monkeypatch.setattr(recording, "_finish_staged_server_take_recovery", finish)
    controller._shutdown = True  # Simulate an already-retired QObject owner.
    try:
        callbacks.pop()()
        assert not recording.workspace_transition_pending
        finish.assert_not_called()
        RECOVER(recording)
        finish.assert_not_called()
    finally:
        controller._shutdown = False


@pytest.mark.parametrize("failure", ["worker", "delivery", "thread_start"])
def test_recovery_failures_release_gate_and_keep_evidence(
    navigation, tmp_path, monkeypatch, failure,
):
    navigator, _mailbox, _controllers, qapp = navigation
    controller, take, native, _take_id = _staged(navigation, tmp_path)
    recording = controller.recording
    original_bytes = (take / native).read_bytes()
    original_invoke = controller._ui_invoker.invoke
    monkeypatch.setattr(recording, "_recover_staged_server_takes_worker",
                        Mock(side_effect=OSError("private fixture detail")))
    if failure == "delivery":
        monkeypatch.setattr(controller._ui_invoker, "invoke",
                            Mock(side_effect=RuntimeError("delivery refused")))
    if failure == "thread_start":
        class RefusedThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("no worker")

            def is_alive(self):
                return False

        monkeypatch.setattr(threading, "Thread", RefusedThread)
    RECOVER(recording)
    until = time.monotonic() + 3
    while recording.workspace_transition_pending and time.monotonic() < until:
        qapp.processEvents()
        time.sleep(.005)
    assert not recording.workspace_transition_pending
    message = controller.window.statusBar().currentMessage()
    assert "recovery could not finish" in message
    assert "Studio" in message and "reopen" in message
    assert "private fixture detail" not in message
    assert (take / native).read_bytes() == original_bytes
    assert (take / ".webjam-recording-staging.json").is_file()
    # The injected dispatcher failure belongs only to recovery delivery, not
    # the unrelated session cleanup callbacks exercised next.
    monkeypatch.setattr(controller._ui_invoker, "invoke", original_invoke)
    assert navigator.return_to_launch()

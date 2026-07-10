"""Band-server recording lifecycle, validation, and completion feedback."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from core.take_library import (
    TakeValidationResult,
    find_changed_take,
    snapshot_take_directories,
    validate_take,
    wait_for_take_files_stable,
)

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.recording")


class RecorderPhase(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class RecorderSnapshot:
    phase: RecorderPhase
    armed: bool
    recording: bool


class RecordingCoordinator:
    """Own the same-Mac/remote recorder state machine and take verification."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller
        self.phase = RecorderPhase.IDLE
        self._before_takes: dict[Path, int] = {}
        self._expected_tracks = 0
        self.last_completed_take: Path | None = None
        self.last_validation: TakeValidationResult | None = None
        self._completion_box = None

    @property
    def snapshot(self) -> RecorderSnapshot:
        return RecorderSnapshot(
            self.phase,
            bool(self._c._recorder_armed),
            bool(self._c._server_recording),
        )

    def on_record_requested(self) -> None:
        secret_file = (self._c.settings.server_rpc_secret_file or "").strip()
        if not secret_file:
            self._c._show_actionable_error(
                "Record Button Not Set Up",
                what_failed="WebJam doesn't have access to your band server's recorder yet.",
                likely_cause="The band-server RPC hasn't been configured on this machine.",
                next_action=(
                    "One-time setup (see server/README.md in the WebJam repo):\n"
                    "1. Same Mac: install/start JamulusServer.app and use its "
                    "container secret path; no SSH tunnel is needed.\n"
                    "2. Remote Linux: copy jsonrpc.secret here and open:  ssh -N -L "
                    f"{self._c.settings.server_rpc_port}:127.0.0.1:22222 you@your-server\n"
                    "3. Set server_rpc_secret_file to that local path in "
                    "~/.webjam_config.json (or via the environment variable)."
                ),
            )
            return
        if self.phase in (RecorderPhase.STARTING, RecorderPhase.STOPPING):
            return

        target_armed = not self._c._recorder_armed
        if target_armed:
            self._before_takes = snapshot_take_directories(
                self._c.settings.takes_directory
            )
            self._expected_tracks = sum(
                1 for participant in self._c.participants.values()
                if not participant.role.startswith("Preview")
            )
            self._set_phase(RecorderPhase.STARTING)
        else:
            self._set_phase(RecorderPhase.STOPPING)
        threading.Thread(
            target=self._c._record_toggle_worker,
            args=(target_armed, secret_file),
            daemon=True,
            name="record-toggle",
        ).start()

    def toggle_worker(self, target_armed: bool, secret_file: str) -> None:
        from core.jamulus_server_rpc import (
            JamulusServerRpc,
            ServerRpcError,
            read_secret_file,
        )

        try:
            secret = read_secret_file(secret_file)
            with JamulusServerRpc(
                port=self._c.settings.server_rpc_port, secret=secret
            ) as rpc:
                acknowledged = (
                    rpc.start_recording() if target_armed else rpc.stop_recording()
                )
                if not acknowledged:
                    raise ServerRpcError("The recorder did not acknowledge the request.")
                armed = target_armed
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    status = rpc.get_recorder_status()
                    armed = bool(status["enabled"])
                    if armed == target_armed:
                        break
                    time.sleep(0.25)
                if armed != target_armed:
                    raise ServerRpcError("The recorder state did not change in time.")
            self._c._ui_invoker.invoke(
                lambda: self._c._apply_record_toggle_result(armed)
            )
        except ServerRpcError as exc:
            self._c._ui_invoker.invoke(
                lambda message=str(exc): self._c._apply_record_toggle_failure(message)
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Record toggle failed unexpectedly")
            self._c._ui_invoker.invoke(
                lambda: self._c._apply_record_toggle_failure(
                    "Unexpected error talking to the band server — see ~/.webjam.log."
                )
            )

    def apply_toggle_result(self, armed: bool) -> None:
        self._c._recorder_armed = armed
        self._c.session_health.mark_recorder(
            armed=armed, recording=self._c._server_recording
        )
        self._c.session_health.mark_rpc_result("recorder", True)
        if armed:
            # A recorderState notification normally promotes this to RECORDING.
            self._set_phase(
                RecorderPhase.RECORDING
                if self._c._server_recording else RecorderPhase.STARTING
            )
            self._c.window.session_strip.set_recording_state(True, enabled=True)
            self._c.window.flash_message(
                "Recording armed — waiting for the server's live confirmation.",
                ms=5000,
            )
            try:
                self._c.metrics.increment("metric_recording_armed")
            except Exception:  # noqa: BLE001
                LOGGER.debug("recording metric failed", exc_info=True)
        else:
            self._set_phase(RecorderPhase.IDLE)
            self._begin_take_validation()

    def apply_toggle_failure(self, message: str) -> None:
        self._c.session_health.mark_rpc_result("recorder", False, message)
        self._set_phase(RecorderPhase.ERROR)
        self._c.window.flash_message(f"Record: {message}", ms=8000)

    def on_server_state(self, recording: bool) -> None:
        if recording == self._c._server_recording:
            return
        prior_phase = self.phase
        self._c._server_recording = recording
        self._c.session_health.mark_recorder(
            armed=self._c._recorder_armed, recording=recording
        )
        self._c.window.set_status_recording(recording)
        if recording:
            self._c._recorder_armed = True
            self._set_phase(RecorderPhase.RECORDING)
            self._c.window.flash_message(
                "● Server is recording — every musician gets their own track.",
                ms=5000,
            )
        elif self.phase is not RecorderPhase.STOPPING:
            self._c._recorder_armed = False
            self._set_phase(RecorderPhase.IDLE)
            if prior_phase is RecorderPhase.RECORDING:
                self._begin_take_validation()
        if not recording:
            self._c.window.flash_message("Server recording stopped.", ms=3000)

    def _set_phase(self, phase: RecorderPhase) -> None:
        self.phase = phase
        self._c.window.session_strip.set_recording_phase(phase.value)

    def _begin_take_validation(self) -> None:
        root = self._c.settings.takes_directory
        if not root:
            self._c.window.flash_message(
                "Recording stopped, but no local Takes folder is configured.", ms=7000
            )
            return
        threading.Thread(
            target=self._validate_take_worker,
            daemon=True,
            name="take-validation",
        ).start()

    def _validate_take_worker(self) -> None:
        root = self._c.settings.takes_directory
        take_dir = None
        for _ in range(12):
            take_dir = find_changed_take(root, self._before_takes)
            if take_dir is not None:
                break
            time.sleep(0.25)
        if take_dir is None:
            result = TakeValidationResult(
                None, ("No new take folder appeared after recording stopped.",)
            )
        else:
            wait_for_take_files_stable(take_dir)
            result = validate_take(take_dir, expected_tracks=self._expected_tracks)
        self._c._ui_invoker.invoke(
            lambda: self._show_validation_result(result)
        )

    def _show_validation_result(self, result: TakeValidationResult) -> None:
        self.last_validation = result
        self.last_completed_take = result.take.path if result.take else None
        if not result.ok:
            detail = "\n".join(result.errors) or "The take could not be verified."
            self._c.window.flash_message(f"Take needs attention: {detail}", ms=10000)
        else:
            suffix = f" · {len(result.warnings)} warning(s)" if result.warnings else ""
            self._c.window.flash_message(
                f"Take saved · {result.summary}{suffix}", ms=10000
            )
        self._open_completion_box(result)

    def _open_completion_box(self, result: TakeValidationResult) -> None:
        box = QMessageBox(self._c.window)
        box.setWindowTitle("WebJam — Recording complete")
        box.setIcon(
            QMessageBox.Icon.Information if result.ok else QMessageBox.Icon.Warning
        )
        details = [f"Take saved · {result.summary}" if result.ok else "Take verification failed."]
        details.extend(result.errors)
        details.extend(f"Warning: {warning}" for warning in result.warnings)
        box.setText("\n".join(details))
        open_button = box.addButton("Open Take Deck", QMessageBox.ButtonRole.ActionRole)
        reveal_button = None
        if result.take is not None:
            reveal_button = box.addButton("Reveal in Finder", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)

        def _clicked(button) -> None:
            if button is open_button:
                self._c._open_take_deck()
            elif reveal_button is not None and button is reveal_button and result.take:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.take.path)))

        box.buttonClicked.connect(_clicked)
        box.finished.connect(lambda _result: setattr(self, "_completion_box", None))
        self._completion_box = box
        box.open()

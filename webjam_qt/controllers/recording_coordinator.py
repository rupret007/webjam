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
    write_take_manifest,
    wait_for_take_files_stable,
)

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.recording")


class RecorderPhase(str, Enum):
    IDLE = "idle"
    PREFLIGHT = "preflight"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    VALIDATING = "validating"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"
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
        self._local_capture = None

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
        if self.phase in (
            RecorderPhase.PREFLIGHT, RecorderPhase.STARTING,
            RecorderPhase.STOPPING, RecorderPhase.VALIDATING,
        ):
            return

        target_armed = not self._c._recorder_armed
        if target_armed:
            self._set_phase(RecorderPhase.PREFLIGHT)
            real_participants = [
                participant for participant in self._c.participants.values()
                if not participant.role.startswith("Preview")
            ]
            if not self._c._jamulus_connected or not real_participants:
                self._set_phase(RecorderPhase.ERROR)
                self._c._show_actionable_error(
                    "Recording Preflight Failed",
                    what_failed="No confirmed Jamulus musician is connected.",
                    likely_cause=(
                        "Audio has not connected yet, or the participant list has "
                        "not arrived from Jamulus."
                    ),
                    next_action=(
                        "Start Audio, wait for the real participant card, verify input "
                        "meters, then press Record again."
                    ),
                    retry_callback=self.on_record_requested,
                )
                return
            self._before_takes = snapshot_take_directories(
                self._c.settings.takes_directory
            )
            self._expected_tracks = len(real_participants)
            if not self._start_local_capture():
                return
            self._set_phase(RecorderPhase.STARTING)
        else:
            self._set_phase(RecorderPhase.STOPPING)
        threading.Thread(
            target=self._c._record_toggle_worker,
            args=(target_armed, secret_file),
            daemon=True,
            name="record-toggle",
        ).start()

    def _start_local_capture(self) -> bool:
        """Start isolated SSL capture on the designated host/bridge only."""
        if not self._c.settings.webex_audio_bridge_enabled:
            self._local_capture = None
            return True
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Preflight Failed",
                what_failed="No writable Takes folder is configured for isolated host tracks.",
                likely_cause="This Mac is the Webex bridge/host, so guitar and vocal isolation is required.",
                next_action="Open Settings, choose the local Jamulus recording folder, then retry.",
                retry_callback=self.on_record_requested,
            )
            return False
        try:
            from core.local_capture import LocalInputCapture

            capture = LocalInputCapture(
                root,
                device=self._c.settings.audio_input_device_index,
                samplerate=self._c.settings.audio_samplerate,
                blocksize=self._c.settings.audio_blocksize,
            )
            capture.start()
            self._local_capture = capture
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Isolated host capture preflight failed: %s", exc)
            self._local_capture = None
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Preflight Failed",
                what_failed=str(exc),
                likely_cause=(
                    "SSL 2+ is unavailable, is not selected, is not at 48 kHz, "
                    "or another application prevented two-channel capture."
                ),
                next_action=(
                    "Keep Jamulus running, verify SSL inputs 1–2 at 48 kHz in "
                    "Ready Check, then retry. No server recording was started."
                ),
                retry_callback=self.on_record_requested,
            )
            return False

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
            # Authenticated status polling confirmed the recorder is enabled;
            # do not leave the UI hanging if a notification is delayed/lost.
            self._set_phase(RecorderPhase.RECORDING)
            self._c.window.session_strip.set_recording_state(True, enabled=True)
            self._c.window.flash_message(
                "Recording confirmed by the band server.",
                ms=5000,
            )
            try:
                self._c.metrics.increment("metric_recording_armed")
            except Exception:  # noqa: BLE001
                LOGGER.debug("recording metric failed", exc_info=True)
        else:
            self._set_phase(RecorderPhase.VALIDATING)
            self._begin_take_validation()

    def apply_toggle_failure(self, message: str) -> None:
        if not self._c._recorder_armed and self._local_capture is not None:
            self._local_capture.abort()
            self._local_capture = None
        self._c.session_health.mark_rpc_result("recorder", False, message)
        self._set_phase(RecorderPhase.ERROR)
        self._c._show_actionable_error(
            "Recording Could Not Start" if not self._c._recorder_armed
            else "Recording Could Not Stop",
            what_failed=message,
            likely_cause=(
                "The recorder RPC is unavailable, the secret is incorrect, or "
                "JamulusServer is not ready."
            ),
            next_action=(
                "Run Ready Check and verify the host recorder, then retry. "
                "Do not close the server while a recording may still be active."
            ),
            retry_callback=self.on_record_requested,
        )

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
            self._set_phase(RecorderPhase.VALIDATING)
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
            if self._local_capture is not None:
                recovered = Path.home() / "Music" / "WebJam Recovered Takes" / time.strftime("%Y%m%d-%H%M%S")
                result = self._local_capture.stop_into(recovered)
                self._local_capture = None
                self._c.window.flash_message(
                    f"Server take unavailable; isolated tracks preserved in {recovered}. "
                    f"{' '.join(result.errors)}", ms=10000,
                )
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            return
        threading.Thread(
            target=self._validate_take_worker,
            daemon=True,
            name="take-validation",
        ).start()

    def _validate_take_worker(self) -> None:
        root = self._c.settings.takes_directory
        take_dir = None
        for _ in range(80):
            take_dir = find_changed_take(root, self._before_takes)
            if take_dir is not None:
                break
            time.sleep(0.25)
        required_local = 1 if self._local_capture is not None else 0
        if take_dir is None:
            recovered = Path(root).expanduser() / f"Recovered-{time.strftime('%Y%m%d-%H%M%S')}"
            capture_errors: tuple[str, ...] = ()
            started_utc = ""
            duration_s = 0.0
            if self._local_capture is not None:
                local_result = self._local_capture.stop_into(recovered)
                self._local_capture = None
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
            if recovered.is_dir():
                from webjam_qt import __version__
                result = write_take_manifest(
                    recovered,
                    expected_tracks=self._expected_tracks,
                    required_local_stems=2 if required_local else 0,
                    local_started_utc=started_utc,
                    local_duration_s=duration_s,
                    capture_errors=(
                        "No new Jamulus take folder appeared after recording stopped.",
                        *capture_errors,
                    ),
                    app_version=__version__,
                )
            else:
                result = TakeValidationResult(
                    None,
                    ("No new Jamulus take folder appeared after recording stopped.",),
                )
        else:
            capture_errors: tuple[str, ...] = ()
            started_utc = ""
            duration_s = 0.0
            if self._local_capture is not None:
                local_result = self._local_capture.stop_into(take_dir)
                self._local_capture = None
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
            stable = wait_for_take_files_stable(take_dir, polls=20, interval_s=0.25)
            if not stable:
                capture_errors = (*capture_errors, "Take files did not become stable in time.")
            from webjam_qt import __version__
            result = write_take_manifest(
                take_dir,
                expected_tracks=self._expected_tracks,
                required_local_stems=2 if required_local else 0,
                local_started_utc=started_utc,
                local_duration_s=duration_s,
                capture_errors=capture_errors,
                app_version=__version__,
            )
        self._c._ui_invoker.invoke(
            lambda: self._show_validation_result(result)
        )

    def _show_validation_result(self, result: TakeValidationResult) -> None:
        self.last_validation = result
        self.last_completed_take = result.take.path if result.take else None
        if not result.ok:
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            detail = "\n".join(result.errors) or "The take could not be verified."
            self._c.window.flash_message(f"Take needs attention: {detail}", ms=10000)
        else:
            self._set_phase(RecorderPhase.COMPLETE)
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

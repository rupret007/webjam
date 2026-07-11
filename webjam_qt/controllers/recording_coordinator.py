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
        self._recovery_box = None
        self._local_capture = None
        # The validation worker, toggle-failure handler, and shutdown salvage
        # all hand off the capture; the lock makes the hand-off atomic so the
        # stream is finalized exactly once.
        self._capture_lock = threading.Lock()

    def _take_local_capture(self):
        """Atomically claim the active capture (or None)."""
        with self._capture_lock:
            capture = self._local_capture
            self._local_capture = None
            return capture

    def _salvage_capture(self) -> tuple[Path | None, tuple[str, ...]]:
        """Preserve an in-flight capture instead of discarding it.

        Returns the recovery folder and any capture errors, or (None, ())
        when there was no capture to claim or the salvage itself failed.
        """
        capture = self._take_local_capture()
        if capture is None:
            return None, ()
        root = (self._c.settings.takes_directory or "").strip()
        base = (
            Path(root).expanduser() if root
            else Path.home() / "Music" / "WebJam Recovered Takes"
        )
        recovered = base / f"Recovered-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            result = capture.stop_into(recovered)
            LOGGER.info("Isolated host stems preserved in %s", recovered)
            for error in result.errors:
                LOGGER.warning("Capture salvage: %s", error)
            return recovered, result.errors
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not salvage isolated host stems")
            capture.abort()
            return None, ()

    def salvage_on_shutdown(self) -> None:
        """Quitting while recording must keep the audio, not abort it away."""
        self._salvage_capture()

    @property
    def is_recording_active(self) -> bool:
        """True while a recording is armed, rolling, or being armed."""
        snap = self.snapshot
        return snap.recording or snap.armed or self.phase in (
            RecorderPhase.STARTING, RecorderPhase.RECORDING,
        )

    def confirm_quit(self) -> bool:
        """Ask before quitting mid-recording; idle quits stay frictionless."""
        if not self.is_recording_active:
            return True
        box = QMessageBox(self._c.window)
        box.setWindowTitle("Quit WebJam?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            "A recording is still running.\n\n"
            "Quitting disconnects this computer, but the band server keeps "
            "recording until someone presses ■ Stop Rec. Your isolated local "
            "tracks will be saved to a Recovered folder before WebJam quits.\n\n"
            "Quit WebJam?"
        )
        quit_button = box.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is quit_button

    def on_audio_session_stopped(self) -> None:
        """Stop Audio ends this Mac's part in any in-flight recording.

        The band server keeps recording (the Stop Audio dialog says so); this
        preserves the local isolated tracks and resets the recording UI so no
        stale REC clock or take chip survives the disconnect.
        """
        if self.phase is RecorderPhase.VALIDATING:
            # The validation worker owns the capture and will finish the take.
            return
        recovered, errors = self._salvage_capture()
        self._c._recorder_armed = False
        self._c._server_recording = False
        self._c.window.set_status_recording(False)
        self._set_phase(RecorderPhase.IDLE)
        if recovered is not None:
            self._notify_recovered(recovered, errors)

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
            with self._capture_lock:
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
        if not self._c._recorder_armed:
            capture = self._take_local_capture()
            if capture is not None:
                # The server never started recording, so the stems have no
                # matching take — abort discards only this pre-take audio.
                capture.abort()
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

    def _is_inside_takes_dir(self, folder: Path) -> bool:
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            return False
        try:
            folder.resolve().relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            return False

    def _notify_recovered(self, recovered: Path, errors: tuple[str, ...]) -> None:
        """Tell the user where rescued tracks went — persistently if the
        folder is outside the Takes folder, where the Take Deck can't list it."""
        if self._is_inside_takes_dir(recovered):
            self._c.window.flash_message(
                f"Audio stopped. Your isolated local tracks were saved to "
                f"{recovered.name} — open the Take Deck to review them.",
                ms=8000,
            )
            return
        box = QMessageBox(self._c.window)
        box.setWindowTitle("WebJam — Tracks recovered")
        box.setIcon(QMessageBox.Icon.Information)
        details = [
            "Recording stopped before a finished server take arrived, but "
            "your isolated local tracks were saved.",
            "",
            f"Saved to:\n{recovered}",
            "",
            "This folder is outside your Takes folder, so it won't appear in "
            "the Take Deck. Use Reveal in Finder to open it, and set a Takes "
            "folder in Settings so future recordings land in one place.",
        ]
        if errors:
            details.extend(["", "Problems noted:"])
            details.extend(errors)
        box.setText("\n".join(details))
        reveal_button = box.addButton(
            "Reveal in Finder", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)

        def _clicked(button) -> None:
            if button is reveal_button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(recovered)))

        box.buttonClicked.connect(_clicked)
        box.finished.connect(lambda _result: setattr(self, "_recovery_box", None))
        self._recovery_box = box
        box.open()

    def _begin_take_validation(self) -> None:
        root = self._c.settings.takes_directory
        if not root:
            self._c.window.flash_message(
                "Recording stopped, but no local Takes folder is configured.", ms=7000
            )
            recovered, errors = self._salvage_capture()
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            if recovered is not None:
                self._notify_recovered(recovered, errors)
            return
        threading.Thread(
            target=self._validate_take_worker,
            daemon=True,
            name="take-validation",
        ).start()

    def _post_validation_stage(self, text: str) -> None:
        """Update the validating chip from the worker thread."""
        self._c._ui_invoker.invoke(
            lambda: self._c.window.session_strip.set_recording_phase(
                "validating", detail=text
            )
        )

    def _validate_take_worker(self) -> None:
        root = self._c.settings.takes_directory
        take_dir = None
        self._post_validation_stage("WAITING FOR SERVER FILES…")
        for _ in range(80):
            take_dir = find_changed_take(root, self._before_takes)
            if take_dir is not None:
                break
            time.sleep(0.25)
        # Peek first (arm state), claim atomically only when attaching so a
        # concurrent shutdown salvage can still preserve the audio while we
        # are polling for the take directory.
        required_local = 1 if self._local_capture is not None else 0
        if take_dir is None:
            recovered = Path(root).expanduser() / f"Recovered-{time.strftime('%Y%m%d-%H%M%S')}"
            capture_errors: tuple[str, ...] = ()
            started_utc = ""
            duration_s = 0.0
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(recovered)
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
            if recovered.is_dir():
                from webjam_qt import __version__
                self._post_validation_stage("ALIGNING HOST TRACKS…")
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
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(take_dir)
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
            self._post_validation_stage("CHECKING TRACKS…")
            stable = wait_for_take_files_stable(take_dir, polls=20, interval_s=0.25)
            if not stable:
                capture_errors = (*capture_errors, "Take files did not become stable in time.")
            from webjam_qt import __version__
            self._post_validation_stage("ALIGNING HOST TRACKS…")
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

    @staticmethod
    def _completion_text(result: TakeValidationResult) -> tuple[str, str]:
        """Title and body for the completion box — pure, so tests can read it."""
        if result.ok:
            details = [f"Take saved · {result.summary}"]
            details.extend(f"Warning: {warning}" for warning in result.warnings)
            return "WebJam — Recording complete", "\n".join(details)
        title = "WebJam — Take needs attention"
        if result.take is not None:
            details = [
                "Your recording was preserved, but it did not pass every check.",
                "",
                f"Saved to: {result.take.path}",
                "",
                "What needs attention:",
            ]
            details.extend(f"• {error}" for error in result.errors)
            if result.warnings:
                details.extend(["", "Warnings:"])
                details.extend(f"• {warning}" for warning in result.warnings)
            details.extend([
                "",
                "The recorded tracks are still playable. Open the Take Deck to "
                "hear what was captured, fix the issue above, then record a "
                "short test take.",
            ])
        else:
            details = [
                "No completed take was found on this Mac.",
                "",
                "What went wrong:",
            ]
            details.extend(f"• {error}" for error in result.errors)
            details.extend([
                "",
                "There is nothing to play back yet. Run Ready Check (F2) to "
                "verify the band server's recorder, then record a short test "
                "take.",
            ])
        return title, "\n".join(details)

    def _open_completion_box(self, result: TakeValidationResult) -> None:
        title, body = self._completion_text(result)
        box = QMessageBox(self._c.window)
        box.setWindowTitle(title)
        box.setIcon(
            QMessageBox.Icon.Information if result.ok else QMessageBox.Icon.Warning
        )
        box.setText(body)
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

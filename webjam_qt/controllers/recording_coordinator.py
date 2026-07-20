"""Band-server recording lifecycle, validation, and completion feedback."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from core.take_library import (
    EVIDENCE_ONLY_EXPORT_BLOCK_REASON,
    TakeValidationResult,
    find_changed_take,
    load_take,
    snapshot_take_directories,
    write_take_manifest,
    wait_for_take_files_stable,
)
from core.recording_manifest_journal import (
    RecordingManifestJournal,
    RecordingManifestJournalError,
)
from core.redaction import redact_text
from core.take_project import (
    HostIdentity,
    RecoveryStatus,
    SessionEvidence,
    SessionTimelineEvent,
    new_project_id,
)
from core.recording_readiness import (
    RecordingStorageStatus,
    check_recording_storage,
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
    STOP_FAILED = "stop_failed"
    ERROR = "error"


@dataclass(frozen=True)
class RecorderSnapshot:
    phase: RecorderPhase
    armed: bool
    recording: bool


@dataclass(frozen=True)
class _ToggleAttempt:
    """One recorder RPC request bound to the take that requested it.

    Jamulus recorder notifications and RPC replies arrive independently.  The
    take ID is therefore carried back to the UI thread so a late worker reply
    can never mutate a later take (or revive one that has entered validation).
    """

    take_id: str
    target_armed: bool


class RecordingCoordinator:
    """Own the same-Mac/remote recorder state machine and take verification."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller
        self.phase = RecorderPhase.IDLE
        self._before_takes: dict[Path, int] = {}
        self._expected_tracks = 0
        self._track_names: dict[int, str] = {}
        self._participant_ids: dict[int, str] = {}
        self._participant_id_by_channel: dict[int, str] = {}
        self._session_title = ""
        self._session_id = new_project_id()
        self._take_id = ""
        # ``_take_id`` is active ownership, not history.  Completed take
        # metadata lives in ``last_completed_take``/``last_validation`` so an
        # unrelated recorder notification can never reopen an old take.
        self._validation_take_id = ""
        # Workers are invoked through the controller's stable two-argument
        # compatibility wrapper. The thread-local attempt carries the request
        # identity through that wrapper without a mutable global slot that a
        # later Stop could overwrite while a Start worker is still in flight.
        self._toggle_worker_context = threading.local()
        self._local_participant_id = new_project_id()
        self.last_completed_take: Path | None = None
        self.last_validation: TakeValidationResult | None = None
        self._completion_box = None
        self._recovery_box = None
        self._local_capture = None
        self._stale_capture_scan_done = False
        # The validation worker, toggle-failure handler, and shutdown salvage
        # all hand off the capture; the lock makes the hand-off atomic so the
        # stream is finalized exactly once.
        self._capture_lock = threading.Lock()
        # One active take keeps its own bounded, privacy-safe recording facts.
        # WebJam-observed timestamps after recorder-state confirmation are
        # deliberately separate from the optional local-input capture times.
        # Jamulus does not provide a server clock in this RPC protocol.
        self._evidence_lock = threading.Lock()
        self._recording_started_utc = ""
        self._recording_ended_utc = ""
        self._recording_host = HostIdentity()
        self._recording_protocol_version = ""
        self._recording_recovery_status = RecoveryStatus.NOT_NEEDED
        self._recording_recovery_notes: list[str] = []
        self._recording_events: list[SessionTimelineEvent] = []
        self._recording_had_recovery = False
        self._recording_recovery_in_progress = False
        # A private, fsynced checkpoint exists while a recording is being
        # started or is active.  The finished take manifest replaces it only
        # after source media and its final manifest were safely published.
        self._evidence_journal: RecordingManifestJournal | None = None
        self._evidence_journal_take_id = ""
        self._evidence_journal_failed = False
        self._stale_journal_scan_done = False

    def _take_local_capture(self):
        """Atomically claim the active capture (or None)."""
        with self._capture_lock:
            capture = self._local_capture
            self._local_capture = None
            return capture

    def _start_take_validation_once(self) -> bool:
        """Hand one active take to validation exactly once.

        A ``recorderState(false)`` notification is authoritative evidence that
        the server has stopped.  Its RPC worker may still report a timeout
        afterwards, so this guard must be take-scoped rather than phase-only.
        """

        take_id = self._take_id
        if not take_id or self._validation_take_id == take_id:
            return False
        self._validation_take_id = take_id
        self._set_phase(RecorderPhase.VALIDATING)
        self._begin_take_validation(take_id)
        return True

    def _retire_active_take(self, take_id: str) -> None:
        """Forget active ownership after a terminal validation/recovery path."""

        if take_id and self._take_id == take_id:
            self._take_id = ""
        if take_id and self._validation_take_id == take_id:
            self._validation_take_id = ""

    def _toggle_callback_is_current(self, take_id: str | None) -> bool:
        """Reject a late RPC completion for a retired or validating take."""

        if take_id is None:
            # Direct controller/unit-test calls predate take-bound callbacks.
            # They remain useful for isolated state tests; production worker
            # callbacks always carry an explicit take ID.
            return True
        return bool(
            take_id
            and take_id == self._take_id
            and take_id != self._validation_take_id
        )

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _safe_evidence_detail(value: str) -> str:
        """Bound recording evidence before it reaches a durable manifest."""
        safe = redact_text(str(value or ""))
        # Diagnostics may retain a redacted URL scheme for context. A take
        # manifest needs no invitation context at all, so remove that remaining
        # shape rather than persisting even a redacted invite reference.
        safe = re.sub(
            r"(?i)\bwebjam:(?://)?\[redacted\]",
            "private invite",
            safe,
        )
        return " ".join(safe.split())[:240]

    def _append_evidence_event_locked(
        self,
        event: str,
        *,
        detail: str = "",
        occurred_utc: str = "",
    ) -> None:
        item = SessionTimelineEvent(
            event,
            occurred_utc=occurred_utc or self._utc_timestamp(),
            detail=self._safe_evidence_detail(detail),
        )
        if self._recording_events:
            previous = self._recording_events[-1]
            if previous.event == item.event and previous.detail == item.detail:
                return
        self._recording_events.append(item)
        del self._recording_events[:-100]

    def _set_recovery_locked(
        self, status: RecoveryStatus, note: str = ""
    ) -> None:
        if (
            status is RecoveryStatus.NEEDS_ATTENTION
            or self._recording_recovery_status is not RecoveryStatus.NEEDS_ATTENTION
        ):
            self._recording_recovery_status = status
        safe_note = self._safe_evidence_detail(note)
        if safe_note and safe_note not in self._recording_recovery_notes:
            self._recording_recovery_notes.append(safe_note)
            del self._recording_recovery_notes[:-20]

    def _reset_session_evidence(self) -> None:
        """Begin a new take-local evidence context before requesting record."""
        peer_control = "webjam-v2-private-peer" if self._c.host_peer.active else "none"
        # A fresh UUID is assigned immediately before this method for normal
        # recording starts.  Clear only the prior operation bookkeeping; the
        # completed take itself remains available through ``last_validation``.
        self._validation_take_id = ""
        with self._evidence_lock:
            self._recording_started_utc = ""
            self._recording_ended_utc = ""
            self._recording_host = HostIdentity(
                participant_id=self._local_participant_id,
                display_name=self._c.settings.musician_name,
            )
            self._recording_protocol_version = (
                f"jamulus-3.12.2; peer-control={peer_control}"
            )
            self._recording_recovery_status = RecoveryStatus.NOT_NEEDED
            self._recording_recovery_notes = []
            self._recording_events = []
            self._recording_had_recovery = False
            self._recording_recovery_in_progress = False
            self._evidence_journal = None
            self._evidence_journal_take_id = ""
            self._evidence_journal_failed = False
            self._append_evidence_event_locked(
                "recording_requested",
                detail="Waiting for the band server to confirm recording.",
            )

    def _current_session_evidence(self) -> SessionEvidence:
        """Return an immutable evidence snapshot for one manifest write."""
        with self._evidence_lock:
            return SessionEvidence(
                protocol_version=self._recording_protocol_version,
                started_utc=self._recording_started_utc,
                ended_utc=self._recording_ended_utc,
                host=self._recording_host,
                recovery_status=self._recording_recovery_status,
                recovery_notes=tuple(self._recording_recovery_notes),
                timeline=tuple(self._recording_events),
            )

    def _create_evidence_journal(self) -> bool:
        """Durably checkpoint a requested take before asking the server to roll."""
        root = (self._c.settings.takes_directory or "").strip()
        take_id = self._take_id
        if not root or not take_id:
            return False
        journal = RecordingManifestJournal(root)
        try:
            journal.create(take_id, self._current_session_evidence())
        except (FileExistsError, OSError, RecordingManifestJournalError, ValueError):
            # UUID take IDs make an existing entry unexpected.  Do not replace
            # it: it may be recovery evidence from a previous interrupted take.
            LOGGER.warning(
                "Could not create a private recording-evidence checkpoint."
            )
            return False
        with self._evidence_lock:
            if self._take_id != take_id:
                # A newer request took ownership while the disk write ran. The
                # just-created checkpoint remains recoverable instead of being
                # accidentally associated with the later take.
                return False
            self._evidence_journal = journal
            self._evidence_journal_take_id = take_id
            self._evidence_journal_failed = False
        return True

    def _checkpoint_evidence_journal(self) -> None:
        """Update the live checkpoint after an evidence mutation.

        A checkpoint failure never fabricates success.  The final manifest
        receives a NEEDS_ATTENTION fact if it can still be published, while
        the prior on-disk checkpoint is deliberately left intact for recovery.
        """
        with self._evidence_lock:
            journal = self._evidence_journal
            take_id = self._evidence_journal_take_id
            failed = self._evidence_journal_failed
        if not journal or not take_id or failed or take_id != self._take_id:
            return
        try:
            journal.update(take_id, self._current_session_evidence())
        except (OSError, RecordingManifestJournalError, ValueError):
            LOGGER.warning("Could not update the private recording-evidence checkpoint.")
            with self._evidence_lock:
                if self._evidence_journal is journal and self._take_id == take_id:
                    self._evidence_journal_failed = True
                    self._set_recovery_locked(
                        RecoveryStatus.NEEDS_ATTENTION,
                        "Recording evidence checkpoint could not be updated.",
                    )
                    self._append_evidence_event_locked(
                        "recording_evidence_checkpoint_failed",
                        detail="The final take needs recovery review.",
                    )

    def _remove_evidence_journal_after_manifest(self) -> None:
        """Retire the temporary checkpoint only after a manifest is published."""
        with self._evidence_lock:
            journal = self._evidence_journal
            take_id = self._evidence_journal_take_id
        if not journal or not take_id:
            return
        try:
            journal.remove(take_id)
        except (OSError, RecordingManifestJournalError, ValueError):
            # The final manifest is already durable.  Retaining the journal is
            # safer than deleting unknown recovery evidence; the next launch
            # will surface it for review without exposing private paths.
            LOGGER.warning("Could not remove the completed recording-evidence checkpoint.")
        finally:
            with self._evidence_lock:
                if self._evidence_journal is journal:
                    self._evidence_journal = None
                    self._evidence_journal_take_id = ""

    def _recover_stale_evidence_journals_once(self) -> None:
        """Surface interrupted recordings without trusting journal contents."""
        if self._stale_journal_scan_done:
            return
        self._stale_journal_scan_done = True
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            return
        try:
            scan = RecordingManifestJournal(root).list_pending()
        except (OSError, RecordingManifestJournalError, ValueError):
            LOGGER.warning("Could not scan private recording recovery evidence.")
            return
        for item in scan.journals:
            self._publish_recovered_evidence_journal(item, root)
        for issue in scan.untrusted_entries:
            # Untrusted directory entries are valid recovery cues, not recoverable
            # payload, so we keep the signal and continue. A dedicated project
            # is published from trusted or fallback evidence paths when available.
            LOGGER.warning("Ignoring untrusted recording-evidence entry: %s", issue.error)

        pending = len(scan.journals) + len(scan.untrusted_entries)
        if not pending:
            return
        noun = "recording" if pending == 1 else "recordings"
        self._c.window.flash_message(
            "WebJam found interrupted "
            f"{noun}. Open Studio and review the saved recovery evidence.",
            ms=10000,
        )
        try:
            self._c.window.recording_studio.set_takes_directory(root)
            self._c.window.recording_studio.reload()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Could not refresh Studio recovery inventory", exc_info=True)

    def _publish_recovered_evidence_journal(self, item, root: str) -> None:
        """Publish interrupt-only evidence as a review-only recovery project."""
        take_id = str(getattr(item, "take_id", "") or "").strip()
        if not take_id or not root:
            return

        recovery_dir = (
            Path(root).expanduser() / f"Recovered-{take_id}"
        )
        manifest_path = recovery_dir / "webjam-take.json"

        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    isinstance(existing_manifest, dict)
                    and existing_manifest.get("schema_version") == 2
                ):
                    return
            except (OSError, ValueError):
                pass

        evidence = getattr(item, "evidence", None)
        if not isinstance(evidence, SessionEvidence):
            evidence = SessionEvidence()

        evidence_note = (
            "Recording evidence was recovered from an interrupted session; "
            f"media was not preserved. {EVIDENCE_ONLY_EXPORT_BLOCK_REASON}"
        )
        if not bool(getattr(item, "trusted", True)):
            evidence_note = (
                "Recording evidence could not be safely read. "
                f"{EVIDENCE_ONLY_EXPORT_BLOCK_REASON}"
            )

        merged_notes = tuple(dict.fromkeys((*evidence.recovery_notes, evidence_note)))
        merged_timeline = tuple(
            dict.fromkeys(
                (*evidence.timeline, SessionTimelineEvent("recording_evidence_recovered", detail=evidence_note))
            ).keys()
        )
        evidence = replace(
            evidence,
            recovery_status=RecoveryStatus.NEEDS_ATTENTION,
            recovery_notes=tuple(merged_notes),
            timeline=merged_timeline,
        )

        try:
            from webjam_qt import __version__
            result = write_take_manifest(
                recovery_dir,
                expected_tracks=0,
                required_local_stems=0,
                local_started_utc=evidence.started_utc,
                local_duration_s=0.0,
                capture_errors=(evidence_note,),
                app_version=__version__,
                participant_names={},
                session_title="Recovered recording evidence",
                session_id=take_id,
                take_id=take_id,
                local_participant_id=evidence.host.participant_id,
                local_participant_name=evidence.host.display_name or "Recovered host",
                capture_device=None,
                capture_gaps=(),
                local_total_frames=0,
                local_durable_frames=0,
                session_evidence=evidence,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not publish evidence-only recovery project.")
            return

        if result.manifest_path:
            try:
                RecordingManifestJournal(root).remove(take_id)
            except (OSError, RecordingManifestJournalError, ValueError):
                LOGGER.warning(
                    "Could not retire recovered evidence checkpoint after manifest publish."
                )

    def recover_interrupted_recordings(self) -> None:
        """Run the bounded local-audio and evidence recovery discovery once."""
        self._recover_stale_captures_once()
        self._recover_stale_evidence_journals_once()

    def _confirmed_recording_started(self) -> tuple[str, bool]:
        """Record when WebJam observed a confirmed recorder start.

        The authenticated recorder response confirms state, not a server-clock
        timestamp, so this deliberately records WebJam's UTC observation time.
        """
        if not self._take_id:
            return "", False
        with self._evidence_lock:
            if self._recording_started_utc:
                return self._recording_started_utc, False
            started_utc = self._utc_timestamp()
            self._recording_started_utc = started_utc
            self._append_evidence_event_locked(
                "recording_started",
                detail="WebJam observed the band server confirm recording.",
                occurred_utc=started_utc,
            )
        self._checkpoint_evidence_journal()
        return started_utc, True

    def _confirmed_recording_stopped(
        self, *, unexpected: bool = False, detail: str = ""
    ) -> tuple[str, bool]:
        """Record when WebJam observed a confirmed recorder stop.

        As for start, the stored timestamp is WebJam's UTC observation time;
        it is not represented as a clock reading returned by the band server.
        """
        if not self._take_id:
            return "", False
        with self._evidence_lock:
            if self._recording_ended_utc:
                return self._recording_ended_utc, False
            stopped_utc = self._utc_timestamp()
            self._recording_ended_utc = stopped_utc
            if not self._recording_started_utc:
                self._set_recovery_locked(
                    RecoveryStatus.NEEDS_ATTENTION,
                    "WebJam observed a confirmed server stop, but not a start.",
                )
            if self._recording_recovery_in_progress:
                self._set_recovery_locked(
                    RecoveryStatus.NEEDS_ATTENTION,
                    "Recording stopped while connection recovery was incomplete.",
                )
            if unexpected:
                self._set_recovery_locked(
                    RecoveryStatus.NEEDS_ATTENTION,
                    detail or "The band server stopped recording unexpectedly.",
                )
            self._append_evidence_event_locked(
                "recording_stopped_unexpectedly" if unexpected else "recording_stopped",
                detail=(
                    detail
                    or (
                        "The band server stopped before WebJam requested it."
                        if unexpected
                        else "WebJam observed the band server confirm stop."
                    )
                ),
                occurred_utc=stopped_utc,
            )
        self._checkpoint_evidence_journal()
        return stopped_utc, True

    def _mark_recording_recovery(
        self, status: RecoveryStatus, note: str, *, event: str = "recovery"
    ) -> None:
        if not self._take_id:
            return
        with self._evidence_lock:
            self._set_recovery_locked(status, note)
            self._append_evidence_event_locked(event, detail=note)
        self._checkpoint_evidence_journal()

    def record_lifecycle_event(
        self,
        phase: object,
        *,
        reason: str = "",
        recovery_attempt: int | None = None,
    ) -> None:
        """Keep relevant, already-redacted session recovery facts with a take."""
        if not self._take_id:
            return
        phase_value = str(getattr(phase, "value", phase) or "").strip().lower()
        if not phase_value:
            return
        with self._evidence_lock:
            # The recorder cannot claim a lifecycle event belongs to a take
            # until the server actually confirmed that take started.
            if not self._recording_started_utc:
                return
            detail = self._safe_evidence_detail(reason)
            if recovery_attempt is not None:
                attempt = max(0, int(recovery_attempt))
                detail = f"{detail} (attempt {attempt}).".strip()
            self._append_evidence_event_locked(
                f"lifecycle:{phase_value}", detail=detail
            )
            if phase_value in {"degraded", "reconnecting"}:
                self._recording_had_recovery = True
                self._recording_recovery_in_progress = True
            elif phase_value == "connected" and self._recording_had_recovery:
                self._recording_recovery_in_progress = False
                self._set_recovery_locked(
                    RecoveryStatus.RECOVERED,
                    "Connection recovered while recording.",
                )
            elif phase_value in {"failed_recoverable", "failed_final"}:
                self._recording_recovery_in_progress = False
                self._set_recovery_locked(
                    RecoveryStatus.NEEDS_ATTENTION,
                    detail or "Connection recovery did not complete while recording.",
                )
        self._checkpoint_evidence_journal()

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
            actual = Path(getattr(result, "recovery_dir", None) or recovered)
            # A stalled writer promotes itself later. Its hidden work folder is
            # not a Finder-safe destination and must never be shown as if it
            # were already a finished recovery folder.
            if actual.name.startswith(".webjam-capture-"):
                LOGGER.warning(
                    "Isolated host recovery is waiting for the writer to release."
                )
                for error in result.errors:
                    LOGGER.warning("Capture salvage: %s", error)
                return None, result.errors
            published = self._publish_local_result_recovery(
                result,
                str(base),
                actual,
            )
            if published is not None:
                actual = published
            LOGGER.info("Isolated host stems preserved in %s", actual)
            for error in result.errors:
                LOGGER.warning("Capture salvage: %s", error)
            return actual, result.errors
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not salvage isolated host stems")
            capture.abort()
            return None, ()

    def salvage_on_shutdown(self) -> None:
        """Quitting while recording must keep the audio, not abort it away."""
        self._mark_recording_recovery(
            RecoveryStatus.NEEDS_ATTENTION,
            "WebJam closed before normal take validation finished.",
            event="recording_interrupted_by_shutdown",
        )
        self._salvage_capture()

    @property
    def is_recording_active(self) -> bool:
        """True while a recording is armed, rolling, or being armed."""
        snap = self.snapshot
        return snap.recording or snap.armed or self.phase in (
            RecorderPhase.STARTING,
            RecorderPhase.RECORDING,
            RecorderPhase.STOP_FAILED,
        )

    @property
    def take_in_progress(self) -> bool:
        """True until a requested take has either finished validation or failed."""
        return self.is_recording_active or self.phase in (
            RecorderPhase.STOPPING,
            RecorderPhase.VALIDATING,
        )

    def _hosting_server(self) -> bool:
        """True only when WebJam owns the server it will stop on quit."""
        return bool(
            getattr(self._c.settings, "host_server_enabled", False)
            and self._c.bridge.hosted_server_owned()
        )

    def confirm_quit(self) -> bool:
        """Ask before quitting mid-recording; idle quits stay frictionless."""
        if not self.is_recording_active:
            return True
        if self._hosting_server():
            body = (
                "A recording is still running, and this Mac is hosting the "
                "band server.\n\n"
                "Quitting stops the recording AND ends the session for every "
                "connected musician. WebJam will stop the recording cleanly "
                "and save your isolated local tracks before it quits.\n\n"
                "Quit WebJam?"
            )
        else:
            body = (
                "A recording is still running.\n\n"
                "Quitting disconnects this computer, but the band server keeps "
                "recording until someone presses ■ Stop Rec. Your isolated local "
                "tracks will be saved to a Recovered folder before WebJam quits.\n\n"
                "Quit WebJam?"
            )
        box = QMessageBox(self._c.window)
        box.setWindowTitle("Quit WebJam?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(body)
        quit_button = box.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is quit_button

    def stop_server_recording_for_shutdown(self) -> bool:
        """Synchronously stop recording and report whether tracks finalized.

        Quitting a hosting Mac takes the server down with it; stopping the
        recording first lets the server finalize every musician's track
        instead of truncating the take mid-write. Bounded by the RPC client's
        short timeouts so shutdown can never hang.
        """
        if not (self._c._server_recording or self._c._recorder_armed):
            return True
        secret_file = (self._c.settings.server_rpc_secret_file or "").strip()
        if not secret_file:
            LOGGER.error("Hosted recording is active but no recorder secret is configured")
            return False
        try:
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file
            secret = read_secret_file(secret_file)
            rpc = JamulusServerRpc(
                port=self._c.settings.server_rpc_port, secret=secret
            )
            rpc.CONNECT_TIMEOUT_S = 0.75
            rpc.CALL_TIMEOUT_S = 1.5
            with rpc:
                if not rpc.stop_recording():
                    LOGGER.error("Hosted recorder did not acknowledge stop")
                    return False
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if not bool(rpc.get_recorder_status()["enabled"]):
                        self._c._recorder_armed = False
                        self._c._server_recording = False
                        self._c.window.set_status_recording(False)
                        if self._take_id:
                            stopped_utc, newly_confirmed = (
                                self._confirmed_recording_stopped(
                                    unexpected=True,
                                    detail=(
                                        "WebJam stopped the recorder while the "
                                        "host was shutting down."
                                    ),
                                )
                            )
                            if newly_confirmed:
                                self._c.signal_peer_recording_stopped(
                                    self._take_id,
                                    stopped_utc=stopped_utc,
                                    needs_attention=True,
                                    message=(
                                        "Host shutdown interrupted normal take "
                                        "validation."
                                    ),
                                )
                        LOGGER.info(
                            "Hosted-server recording stopped and confirmed"
                        )
                        return True
                    time.sleep(0.1)
            LOGGER.error("Hosted recorder stayed enabled after stop request")
            return False
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not stop hosted recording on shutdown")
            return False

    def on_audio_session_stopped(self) -> None:
        """Stop Audio ends this Mac's part in any in-flight recording.

        The band server keeps recording — Stop Audio never stops the server,
        even when this Mac hosts it (the Stop Audio dialog says so); this
        preserves the local isolated tracks and resets the recording UI so no
        stale REC clock or take chip survives the disconnect.
        """
        if self.phase is RecorderPhase.VALIDATING:
            # The validation worker owns the capture and will finish the take.
            return
        active_take_id = self._take_id
        recovered, errors = self._salvage_capture()
        self._mark_recording_recovery(
            RecoveryStatus.NEEDS_ATTENTION,
            "This Mac stopped audio before normal take validation finished.",
            event="recording_interrupted_by_audio_stop",
        )
        self._c._recorder_armed = False
        self._c._server_recording = False
        self._c.window.set_status_recording(False)
        self._set_phase(RecorderPhase.IDLE)
        if recovered is not None:
            self._notify_recovered(recovered, errors)
        self._retire_active_take(active_take_id)

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
                "Recording Is Available On The Host",
                what_failed="This Mac is not the band's recording host.",
                likely_cause=(
                    "The host owns the synchronized multitrack files so every "
                    "musician is captured in one take."
                ),
                next_action=(
                    "Ask the host to press Record Take in Studio. Your audio "
                    "will appear there automatically as its own track."
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
                        "Start Session, wait for the musician track to appear, "
                        "then press Record Take again."
                    ),
                    retry_callback=self.on_record_requested,
                )
                return
            storage = check_recording_storage(
                self._c.settings.takes_directory,
                expected_server_tracks=len(real_participants),
                local_originals_enabled=bool(
                    self._c.settings.local_capture_enabled
                ),
            )
            if not storage.can_start:
                self._set_phase(RecorderPhase.ERROR)
                self._c._show_actionable_error(
                    "Recording Storage Needs Attention",
                    what_failed=(
                        "WebJam can't safely start this take with the available "
                        "recording storage."
                    ),
                    likely_cause=storage.detail,
                    next_action=(
                        "Free up space, or end this session and choose another Takes "
                        "folder in Recording Setup before starting again. No recording "
                        "was started."
                    ),
                    retry_callback=self.on_record_requested,
                )
                return
            if storage.status is RecordingStorageStatus.WARNING:
                self._c.window.flash_message(storage.detail, ms=8000)
            self._before_takes = snapshot_take_directories(
                self._c.settings.takes_directory
            )
            self._expected_tracks = len(real_participants)
            self._track_names = {
                int(getattr(participant, "channel_id", index)): str(
                    getattr(participant, "name", None)
                    or getattr(participant, "role", None)
                    or f"Musician {index + 1}"
                )
                for index, participant in enumerate(real_participants)
            }
            self._participant_ids = {}
            if self._c.host_peer.active:
                self._session_id = self._c.host_peer.session_id
                if self._c.host_peer.host_enrollment is not None:
                    self._local_participant_id = (
                        self._c.host_peer.host_enrollment.participant_id
                    )
            for index, participant in enumerate(real_participants):
                channel_id = int(getattr(participant, "channel_id", index))
                durable = str(getattr(participant, "participant_id", "") or "")
                if not durable:
                    durable = self._c.peer_participant_id_for_channel(channel_id)
                if not durable:
                    durable = self._participant_id_by_channel.get(channel_id, "")
                if not durable:
                    durable = new_project_id()
                self._participant_id_by_channel[channel_id] = durable
                self._participant_ids[channel_id] = durable
            self._take_id = new_project_id()
            self._session_title = self._c.window.session_strip.current_title()
            self._reset_session_evidence()
            if not self._start_local_capture():
                return
            if not self._create_evidence_journal():
                # A server take is not allowed to begin without a durable,
                # privacy-safe recovery checkpoint.  Preserve any local input
                # that was already opened instead of aborting it away.
                recovered, errors = self._salvage_capture()
                self._set_phase(RecorderPhase.ERROR)
                self._c._show_actionable_error(
                    "Recording Recovery Setup Failed",
                    what_failed=(
                        "WebJam couldn't prepare the private recovery record for "
                        "this take."
                    ),
                    likely_cause=(
                        "The selected Takes folder is no longer writable or could "
                        "not safely store recording recovery evidence."
                    ),
                    next_action=(
                        "Choose a writable Takes folder in Recording Setup, then "
                        "try Record Take again. No server recording was started."
                    ),
                    retry_callback=self.on_record_requested,
                )
                if recovered is not None:
                    self._notify_recovered(recovered, errors)
                return
            self._set_phase(RecorderPhase.STARTING)
        else:
            self._set_phase(RecorderPhase.STOPPING)
        attempt = _ToggleAttempt(
            take_id=self._take_id,
            target_armed=target_armed,
        )
        threading.Thread(
            target=self._run_toggle_attempt,
            args=(attempt, secret_file),
            daemon=True,
            name="record-toggle",
        ).start()

    def _start_local_capture(self) -> bool:
        """Start optional isolated local-input capture independently of Webex."""
        self.recover_interrupted_recordings()
        if not self._c.settings.local_capture_enabled:
            self._local_capture = None
            return True
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Preflight Failed",
                what_failed="No writable Takes folder is configured for isolated host tracks.",
                likely_cause=(
                    "Local input-stem recording is enabled, so WebJam needs a "
                    "destination for the isolated tracks."
                ),
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
                take_id=self._take_id,
                session_id=self._session_id,
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
                what_failed="WebJam couldn't open the selected two-channel input.",
                likely_cause=(
                    "The selected interface is unavailable, is not at 48 kHz, "
                    "or another application prevented two-channel capture."
                ),
                next_action=(
                    "Keep Jamulus running, verify the selected interface inputs "
                    "1–2 at 48 kHz in Band Check, then retry. No server recording "
                    "was started."
                ),
                retry_callback=self.on_record_requested,
            )
            return False

    def _recover_stale_captures_once(self) -> None:
        if self._stale_capture_scan_done:
            return
        self._stale_capture_scan_done = True
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            return
        try:
            from core.local_capture import recover_stale_local_captures

            recovered = recover_stale_local_captures(root)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not scan for abandoned local captures")
            return
        if not recovered:
            return
        for item in recovered:
            LOGGER.warning(
                "Recovered abandoned local capture in %s", item.recovery_dir
            )
            self._publish_recovered_local_capture(item, root)
        self._c.window.flash_message(
            "WebJam recovered unfinished local audio from an earlier session. "
            "Open Studio to review it.",
            ms=9000,
        )
        try:
            self._c.window.recording_studio.set_takes_directory(root)
            self._c.window.recording_studio.reload()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Could not refresh Studio recovery inventory", exc_info=True)

    def _publish_recovered_local_capture(self, item, root: str) -> None:
        """Turn recovered local media into a durable, review-only project.

        A crash can leave valid local PCM while the Jamulus server folder is
        absent or incomplete.  Do not leave that media as anonymous files: a
        recovery project binds it to the opaque take/session IDs checkpointed
        at Record time, carries any safe session evidence, and deliberately
        remains ``needs_attention``.  It is never presented as a completed
        multitrack take or timing-ready track export.
        """
        if not getattr(item, "files", ()):
            return
        recovery_dir = Path(item.recovery_dir)
        manifest_path = recovery_dir / "webjam-take.json"
        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_manifest = None
            if isinstance(existing_manifest, dict) and existing_manifest.get("schema_version") == 2:
                return

        take_id = str(getattr(item, "take_id", "") or "")
        session_id = str(getattr(item, "session_id", "") or "")
        journal = RecordingManifestJournal(root)
        journal_result = None
        if take_id:
            try:
                journal_result = journal.load(take_id)
            except (OSError, RecordingManifestJournalError, ValueError):
                LOGGER.warning("Could not read recovery evidence for local capture.")

        if journal_result is not None:
            evidence = journal_result.evidence
        else:
            evidence = SessionEvidence()
        if journal_result is not None and not journal_result.trusted:
            recovery_note = (
                "Recording evidence could not be read safely; the recovered local "
                "audio needs manual review."
            )
        else:
            recovery_note = (
                "Local isolated audio was recovered after an interrupted recording; "
                "it is not verified as a completed multitrack take."
            )
        notes = tuple(dict.fromkeys((*evidence.recovery_notes, recovery_note)))
        timeline = tuple(
            dict.fromkeys(
                (*evidence.timeline, SessionTimelineEvent(
                    "local_capture_recovered",
                    detail=recovery_note,
                ))
            )
        )
        evidence = replace(
            evidence,
            recovery_status=RecoveryStatus.NEEDS_ATTENTION,
            recovery_notes=notes,
            timeline=timeline,
        )
        try:
            from webjam_qt import __version__

            frames = max(0, int(getattr(item, "total_frames", 0) or 0))
            durable_frames = min(
                frames,
                max(0, int(getattr(item, "durable_frames", 0) or 0)),
            )
            device = getattr(item, "capture_device", None)
            sample_rate = int(
                getattr(device, "sample_rate", 0)
                or getattr(item, "sample_rate", 0)
                or 0
            )
            duration_s = frames / sample_rate if frames and sample_rate else 0.0
            result = write_take_manifest(
                recovery_dir,
                expected_tracks=0,
                required_local_stems=0,
                local_started_utc=str(getattr(item, "started_utc", "") or ""),
                local_duration_s=duration_s,
                capture_errors=(
                    recovery_note,
                    *tuple(getattr(item, "errors", ()) or ()),
                ),
                app_version=__version__,
                participant_names={},
                session_title="Recovered local audio",
                session_id=session_id,
                take_id=take_id,
                local_participant_id=evidence.host.participant_id,
                local_participant_name=evidence.host.display_name or "Recovered host",
                capture_device=device,
                capture_gaps=tuple(getattr(item, "gaps", ()) or ()),
                local_total_frames=frames,
                local_durable_frames=durable_frames,
                session_evidence=evidence,
            )
        except Exception:  # noqa: BLE001 - media stays visible even if manifest fails
            LOGGER.exception("Could not publish recovered local capture manifest")
            return

        if result.take is not None and journal_result is not None and journal_result.trusted:
            try:
                journal.remove(take_id)
            except (OSError, RecordingManifestJournalError, ValueError):
                LOGGER.warning("Could not retire recovery evidence after manifest publish.")

    def _publish_local_result_recovery(self, result, root: str, fallback_dir: Path) -> Path | None:
        """Publish a visible local-result recovery without inventing media.

        ``LocalInputCapture`` may already have promoted a partial capture to a
        visible recovery folder. A normal interrupted stop writes finished WAVs
        directly into ``fallback_dir``. Both cases need one recovery-only
        project, while a hidden writer directory must stay untouched until its
        deferred promotion completes.
        """
        recovery_dir = Path(getattr(result, "recovery_dir", None) or fallback_dir)
        if recovery_dir.name.startswith(".webjam-capture-") or not recovery_dir.is_dir():
            return None
        files = tuple(
            path
            for path in tuple(getattr(result, "files", ()) or ())
            if Path(path).is_file() and not Path(path).is_symlink()
        )
        if not files:
            try:
                files = tuple(
                    path
                    for path in sorted(recovery_dir.glob("*.recovered-partial.wav"))
                    if path.is_file() and not path.is_symlink()
                )
            except OSError:
                files = ()
        if not files:
            return recovery_dir
        from core.local_capture import RecoveredLocalCapture

        capture_device = getattr(result, "capture_device", None)
        self._publish_recovered_local_capture(
            RecoveredLocalCapture(
                source_dir=recovery_dir,
                recovery_dir=recovery_dir,
                files=tuple(Path(path) for path in files),
                errors=tuple(getattr(result, "errors", ()) or ()),
                take_id=self._take_id,
                session_id=self._session_id,
                started_utc=str(getattr(result, "started_utc", "") or ""),
                total_frames=max(0, int(getattr(result, "total_frames", 0) or 0)),
                durable_frames=max(
                    0, int(getattr(result, "durable_frames", 0) or 0)
                ),
                sample_rate=max(0, int(getattr(capture_device, "sample_rate", 0) or 0)),
                gaps=tuple(getattr(result, "gaps", ()) or ()),
                capture_device=capture_device,
            ),
            root,
        )
        return recovery_dir

    def _run_toggle_attempt(self, attempt: _ToggleAttempt, secret_file: str) -> None:
        """Carry one request's take identity through the legacy worker hook."""

        self._toggle_worker_context.attempt = attempt
        try:
            self._c._record_toggle_worker(attempt.target_armed, secret_file)
        finally:
            try:
                del self._toggle_worker_context.attempt
            except AttributeError:
                pass

    def toggle_worker(self, target_armed: bool, secret_file: str) -> None:
        from core.jamulus_server_rpc import (
            JamulusServerRpc,
            ServerRpcError,
            read_secret_file,
        )

        attempt = getattr(self._toggle_worker_context, "attempt", None)
        # Retain the originating ID even when it was retired before this
        # worker got CPU time. ``apply_*`` rejects that callback instead of
        # falling back to the legacy unbound path and mutating a newer take.
        callback_take_id = (
            attempt.take_id
            if isinstance(attempt, _ToggleAttempt)
            and attempt.target_armed is target_armed
            and attempt.take_id
            else None
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
            if callback_take_id is None:
                self._c._ui_invoker.invoke(
                    lambda: self._c._apply_record_toggle_result(armed)
                )
            else:
                self._c._ui_invoker.invoke(
                    lambda take_id=callback_take_id: self.apply_toggle_result(
                        armed, take_id=take_id
                    )
                )
        except ServerRpcError as exc:
            if callback_take_id is None:
                self._c._ui_invoker.invoke(
                    lambda message=str(exc): self._c._apply_record_toggle_failure(message)
                )
            else:
                self._c._ui_invoker.invoke(
                    lambda message=str(exc), take_id=callback_take_id: self.apply_toggle_failure(
                        message, take_id=take_id
                    )
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Record toggle failed unexpectedly")
            if callback_take_id is None:
                self._c._ui_invoker.invoke(
                    lambda: self._c._apply_record_toggle_failure(
                        "WebJam couldn't confirm the band server's recording state."
                    )
                )
            else:
                self._c._ui_invoker.invoke(
                    lambda take_id=callback_take_id: self.apply_toggle_failure(
                        "WebJam couldn't confirm the band server's recording state.",
                        take_id=take_id,
                    )
                )

    def apply_toggle_result(self, armed: bool, *, take_id: str | None = None) -> None:
        if not self._toggle_callback_is_current(take_id):
            LOGGER.debug("Ignoring stale recorder RPC result for a retired take")
            return
        self._c._recorder_armed = armed
        self._c.session_health.mark_recorder(
            armed=armed, recording=self._c._server_recording
        )
        self._c.session_health.mark_rpc_result("recorder", True)
        if armed:
            # Authenticated status polling confirmed the recorder is enabled;
            # do not leave the UI hanging if a notification is delayed/lost.
            self._set_phase(RecorderPhase.RECORDING)
            if self._take_id:
                started_utc, newly_confirmed = self._confirmed_recording_started()
                if newly_confirmed:
                    self._c.signal_peer_recording_started(
                        self._take_id, started_utc=started_utc
                    )
            self._c.window.flash_message(
                "Recording confirmed by the band server.",
                ms=5000,
            )
            try:
                self._c.metrics.increment("metric_recording_armed")
            except Exception:  # noqa: BLE001
                LOGGER.debug("recording metric failed", exc_info=True)
        else:
            if self._take_id:
                stopped_utc, newly_confirmed = self._confirmed_recording_stopped()
                if newly_confirmed:
                    self._c.signal_peer_recording_stopped(
                        self._take_id, stopped_utc=stopped_utc
                    )
                self._start_take_validation_once()
            else:
                self._set_phase(RecorderPhase.IDLE)
                self._c.window.flash_message("Recording stopped.", ms=3000)

    def apply_toggle_failure(self, message: str, *, take_id: str | None = None) -> None:
        if not self._toggle_callback_is_current(take_id):
            LOGGER.debug("Ignoring stale recorder RPC failure for a retired take")
            return
        ambiguous_start = (
            self.phase is RecorderPhase.STARTING
            and not self._c._recorder_armed
        )
        if ambiguous_start:
            # A timeout can arrive after JamulusServer accepted startRecording
            # but before WebJam received the acknowledgement or status reply.
            # Never delete local stems or offer another start in that state.
            # Fail safe toward "possibly armed" so the retry is an idempotent
            # stop; normal stop confirmation then validates the server take and
            # finalizes this still-running local capture.
            self._c._recorder_armed = True
            self._mark_recording_recovery(
                RecoveryStatus.NEEDS_ATTENTION,
                "WebJam could not confirm whether recording started.",
                event="recording_start_unconfirmed",
            )
        still_armed = bool(self._c._recorder_armed or self._c._server_recording)
        self._c.session_health.mark_rpc_result("recorder", False, message)
        self._set_phase(
            RecorderPhase.STOP_FAILED if still_armed else RecorderPhase.ERROR
        )
        self._c._show_actionable_error(
            "Recording Could Not Start" if not self._c._recorder_armed
            else "Recording Could Not Stop",
            what_failed=(
                "WebJam couldn't confirm that recording started."
                if not self._c._recorder_armed
                else "WebJam couldn't confirm that recording stopped."
            ),
            likely_cause=(
                "The recorder RPC is unavailable, the secret is incorrect, or "
                "JamulusServer is not ready."
            ),
            next_action=(
                "The server may still be recording. Try Stop Again now."
                if still_armed else
                "End the session, start it again, and retry. WebJam will rebuild the "
                "recording connection automatically."
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
            if self._take_id:
                started_utc, newly_confirmed = self._confirmed_recording_started()
                if newly_confirmed:
                    self._c.signal_peer_recording_started(
                        self._take_id, started_utc=started_utc
                    )
            self._c.window.flash_message(
                "● Server is recording — every musician gets their own track.",
                ms=5000,
            )
        else:
            if self._take_id:
                stopped_utc, newly_confirmed = self._confirmed_recording_stopped(
                    unexpected=prior_phase is not RecorderPhase.STOPPING,
                    detail=(
                        "The band server stopped recording before WebJam requested it."
                        if prior_phase is not RecorderPhase.STOPPING
                        else "WebJam observed server confirmation after Stop Rec."
                    ),
                )
                if newly_confirmed:
                    self._c.signal_peer_recording_stopped(
                        self._take_id,
                        stopped_utc=stopped_utc,
                        needs_attention=prior_phase is not RecorderPhase.STOPPING,
                        message=(
                            "The band server stopped recording unexpectedly."
                            if prior_phase is not RecorderPhase.STOPPING
                            else ""
                        ),
                    )
            # The state notification is authoritative even if the stop RPC
            # subsequently times out.  Do not wait for that worker before
            # finalizing a take: it can otherwise leave real local media in a
            # STOP_FAILED/VALIDATING limbo indefinitely.
            self._c._recorder_armed = False
            self._c.session_health.mark_recorder(armed=False, recording=False)
            if self._take_id:
                if self._validation_take_id == self._take_id:
                    self._set_phase(RecorderPhase.VALIDATING)
                else:
                    self._start_take_validation_once()
            elif self.phase in {
                RecorderPhase.STARTING,
                RecorderPhase.RECORDING,
                RecorderPhase.STOPPING,
                RecorderPhase.STOP_FAILED,
            }:
                # WebJam can still display a recorder it did not start, but a
                # manual/external stop must never manufacture a project from a
                # previously completed take.
                self._set_phase(RecorderPhase.IDLE)
        if not recording:
            self._c.window.flash_message("Server recording stopped.", ms=3000)

    def _set_phase(self, phase: RecorderPhase) -> None:
        self.phase = phase
        self._c.window.session_strip.set_recording_phase(phase.value)
        self._c.window.recording_studio.set_recording_phase(phase.value)
        changed = getattr(self._c, "_on_recorder_phase_changed", None)
        if callable(changed):
            changed(phase)

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
        """Offer a safe route to rescued tracks without rendering local paths."""
        for error in errors:
            LOGGER.warning("Recovered local capture needs review: %s", error)
        if self._is_inside_takes_dir(recovered):
            self._c.window.flash_message(
                "Audio stopped. Your isolated local tracks were saved. "
                "Open Studio to review them.",
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
            "This folder is outside your Takes folder, so it won't appear in "
            "Studio. Use Reveal in Finder to open it, and set a Takes "
            "folder in Settings so future recordings land in one place.",
        ]
        if errors:
            details.extend(
                [
                    "",
                    "Some local tracks need review. Listen to each file before "
                    "using it.",
                ]
            )
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

    def _begin_take_validation(self, take_id: str | None = None) -> None:
        """Start validation for one already-stopped, locally owned take."""

        active_take_id = self._take_id if take_id is None else take_id
        root = self._c.settings.takes_directory
        if not root:
            self._mark_recording_recovery(
                RecoveryStatus.NEEDS_ATTENTION,
                "No local Takes folder was configured when recording stopped.",
                event="takes_folder_missing",
            )
            self._c.window.flash_message(
                "Recording stopped, but no local Takes folder is configured.", ms=7000
            )
            recovered, errors = self._salvage_capture()
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            if recovered is not None:
                self._notify_recovered(recovered, errors)
            self._retire_active_take(active_take_id)
            return
        threading.Thread(
            target=self._validate_take_worker,
            args=(active_take_id,),
            daemon=True,
            name="take-validation",
        ).start()

    def _post_validation_stage(self, text: str) -> None:
        """Update the validating chip from the worker thread."""
        self._c._ui_invoker.invoke(
            lambda: (
                self._c.window.session_strip.set_recording_phase(
                    "validating", detail=text
                ),
                self._c.window.recording_studio.set_recording_phase(
                    "validating", detail=text
                ),
            )
        )

    def _validate_take_worker(self, take_id: str | None = None) -> None:
        """Never leave the recorder UI stuck if validation itself fails."""
        try:
            result = self._build_take_validation(take_id=take_id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Take validation failed unexpectedly")
            candidate = find_changed_take(
                self._c.settings.takes_directory, self._before_takes
            )
            recovered, capture_errors = self._salvage_capture()
            candidate = candidate or recovered
            take = load_take(candidate) if candidate is not None else None
            result = TakeValidationResult(
                take,
                (
                    "WebJam couldn't finish verifying this take. The source audio "
                    "was preserved; check free disk space and folder access, then "
                    "review the take before using it.",
                    *capture_errors,
                ),
            )
        self._c._ui_invoker.invoke(
            lambda: self._show_validation_result(result, take_id=take_id)
        )

    def _build_take_validation(
        self, *, take_id: str | None = None
    ) -> TakeValidationResult:
        active_take_id = self._take_id if take_id is None else take_id
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
            self._mark_recording_recovery(
                RecoveryStatus.NEEDS_ATTENTION,
                "No new Jamulus take folder appeared after recording stopped.",
                event="server_take_missing",
            )
            recovered: Path | None = (
                Path(root).expanduser() / f"Recovered-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            capture_errors: tuple[str, ...] = ()
            started_utc = ""
            duration_s = 0.0
            capture_gaps: tuple[object, ...] = ()
            local_total_frames = 0
            local_durable_frames: int | None = None
            capture_device = None
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(recovered)
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
                capture_gaps = tuple(getattr(local_result, "gaps", ()) or ())
                local_total_frames = int(
                    getattr(local_result, "total_frames", 0) or 0
                )
                local_durable_frames = getattr(
                    local_result, "durable_frames", None
                )
                capture_device = getattr(local_result, "capture_device", None)
                actual_recovery_dir = getattr(local_result, "recovery_dir", None)
                if actual_recovery_dir is not None:
                    candidate = Path(actual_recovery_dir)
                    # Deferred local recovery still owns hidden ``.part``
                    # media. Leave it untouched until its writer promotes a
                    # visible folder that startup reconciliation can publish.
                    recovered = (
                        candidate
                        if not candidate.name.startswith(".webjam-capture-")
                        else None
                    )
            if recovered is not None and recovered.is_dir():
                from webjam_qt import __version__
                self._post_validation_stage("ALIGNING HOST TRACKS…")
                self._checkpoint_evidence_journal()
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
                    participant_names=self._track_names,
                    session_title=self._session_title,
                    session_id=self._session_id,
                    take_id=active_take_id,
                    participant_ids=self._participant_ids,
                    local_participant_id=self._local_participant_id,
                    local_participant_name=self._c.settings.musician_name,
                    capture_device=capture_device,
                    capture_gaps=capture_gaps,
                    local_total_frames=local_total_frames,
                    local_durable_frames=local_durable_frames,
                    session_evidence=self._current_session_evidence(),
                )
                if result.take is not None:
                    self._remove_evidence_journal_after_manifest()
            else:
                result = TakeValidationResult(
                    None,
                    ("No new Jamulus take folder appeared after recording stopped.",),
                )
        else:
            capture_errors: tuple[str, ...] = ()
            started_utc = ""
            duration_s = 0.0
            capture_gaps: tuple[object, ...] = ()
            local_total_frames = 0
            local_durable_frames: int | None = None
            capture_device = None
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(take_dir)
                capture_errors = local_result.errors
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
                capture_gaps = tuple(getattr(local_result, "gaps", ()) or ())
                local_total_frames = int(
                    getattr(local_result, "total_frames", 0) or 0
                )
                local_durable_frames = getattr(
                    local_result, "durable_frames", None
                )
                capture_device = getattr(local_result, "capture_device", None)
            self._post_validation_stage("CHECKING TRACKS…")
            stable = wait_for_take_files_stable(take_dir, polls=20, interval_s=0.25)
            if not stable:
                capture_errors = (*capture_errors, "Take files did not become stable in time.")
                self._mark_recording_recovery(
                    RecoveryStatus.NEEDS_ATTENTION,
                    "Take files did not become stable in time.",
                    event="take_files_unstable",
                )
            from webjam_qt import __version__
            self._post_validation_stage("ALIGNING HOST TRACKS…")
            self._checkpoint_evidence_journal()
            result = write_take_manifest(
                take_dir,
                expected_tracks=self._expected_tracks,
                required_local_stems=2 if required_local else 0,
                local_started_utc=started_utc,
                local_duration_s=duration_s,
                capture_errors=capture_errors,
                app_version=__version__,
                participant_names=self._track_names,
                session_title=self._session_title,
                session_id=self._session_id,
                take_id=active_take_id,
                participant_ids=self._participant_ids,
                local_participant_id=self._local_participant_id,
                local_participant_name=self._c.settings.musician_name,
                capture_device=capture_device,
                capture_gaps=capture_gaps,
                local_total_frames=local_total_frames,
                local_durable_frames=local_durable_frames,
                session_evidence=self._current_session_evidence(),
            )
            if result.take is not None:
                self._remove_evidence_journal_after_manifest()
        return result

    def _show_validation_result(
        self, result: TakeValidationResult, *, take_id: str | None = None
    ) -> None:
        if take_id and take_id != self._take_id:
            LOGGER.debug("Ignoring validation result for a retired recording take")
            return
        completed_take_id = self._take_id if take_id is None else take_id
        self.last_validation = result
        self.last_completed_take = result.take.path if result.take else None
        for warning in result.warnings:
            LOGGER.warning("Take validation warning: %s", warning)
        if not result.ok:
            for error in result.errors:
                LOGGER.warning("Take validation needs attention: %s", error)
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            if result.take is None:
                message = (
                    "No completed take was found. Run Band Check, then record "
                    "a short test take."
                )
            else:
                message = (
                    "Take saved, but it needs review. Open Studio and listen "
                    "to each track before export."
                )
            self._c.window.flash_message(message, ms=10000)
        else:
            self._set_phase(RecorderPhase.COMPLETE)
            suffix = f" · {len(result.warnings)} warning(s)" if result.warnings else ""
            self._c.window.flash_message(
                f"Take saved · {result.summary}{suffix}", ms=10000
            )
        self._c.window.recording_studio.on_take_completed(
            result.take.path if result.take else None,
            result,
        )
        if result.take is not None and completed_take_id and self._c.host_peer.active:
            try:
                self._c.host_peer.register_take(completed_take_id, result.take.path)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not attach peer transfer inventory to take")
        self._retire_active_take(completed_take_id)

    @staticmethod
    def _completion_text(result: TakeValidationResult) -> tuple[str, str]:
        """Title and body for the completion box — pure, so tests can read it."""
        if result.ok:
            details = [f"Take saved · {result.summary}"]
            if result.warnings:
                details.extend(
                    [
                        "",
                        "WebJam found something to review. Open Studio and listen "
                        "to each track before export.",
                    ]
                )
            return "WebJam — Recording complete", "\n".join(details)
        title = "WebJam — Take needs attention"
        if result.take is not None:
            details = [
                "Your recording was preserved, but it did not pass every check.",
                "",
                "The recorded tracks may still be playable. Open Studio, listen "
                "to each track, then record a short test take.",
                "",
                "Use Reveal in Finder to open the saved files.",
            ]
        else:
            details = [
                "No completed take was found on this Mac.",
                "",
                "There is nothing to play back yet. Run Band Check (F2) to "
                "verify the band server's recorder, then record a short test "
                "take.",
            ]
        return title, "\n".join(details)

    def _open_completion_box(self, result: TakeValidationResult) -> None:
        title, body = self._completion_text(result)
        box = QMessageBox(self._c.window)
        box.setWindowTitle(title)
        box.setIcon(
            QMessageBox.Icon.Information if result.ok else QMessageBox.Icon.Warning
        )
        box.setText(body)
        open_button = box.addButton("Open Studio", QMessageBox.ButtonRole.ActionRole)
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

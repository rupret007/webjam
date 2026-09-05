"""Band-server recording lifecycle, validation, and completion feedback."""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from core.creative_modes import (
    canonical_creator_profile_key,
    get_creator_profile_by_key_or_default,
)
from core.jamulus_roster_identity import (
    JamulusRosterIdentityError,
    ordered_common_roster_digest,
    server_common_profile,
)
from core.jamulus_rpc_client import JamulusOrderedRosterProof
from core.local_capture import (
    LocalCapturePreflight,
    check_local_capture_preflight,
    local_capture_track_map_fingerprint,
)
from core.recording_manifest_journal import (
    RecordingManifestJournal,
    RecordingManifestJournalError,
)
from core.recording_readiness import (
    RecordingStorageCheck,
    RecordingStorageStatus,
    check_recording_storage,
)
from core.recording_readiness_presentation import (
    RecordingChannelTopology,
    RecordingReadinessPresentation,
    RecordingReadinessRecovery,
    RecordingReadinessSource,
    RecordingSourceKind,
    RecordingSourceReadiness,
    RecordingStoragePresentation,
    RecordingStorageReadiness,
    SharedTrackPresentation,
    SharedTrackReadiness,
)
from core.recording_sources import (
    RecordingSourceKind as LiveRecordingSourceKind,
)
from core.recording_sources import (
    RecordingSourcePresentation,
    RecordingSourceState,
    project_recording_sources,
    validate_exact_recording_sources,
)
from core.redaction import redact_text
from core.session_recording_plan import (
    GuestLocalOriginalBinding,
    InputMapBinding,
    SessionRecordingPlan,
    SharedTrackBinding,
    configured_input_map_bindings,
    resolve_capture_tracks,
)
from core.session_transfer_runtime import PEER_TRANSFER_ERROR_PREFIX
from core.take_library import (
    EVIDENCE_ONLY_EXPORT_BLOCK_REASON,
    RecorderClientReceipt,
    RecorderRosterError,
    RecordingStagingIdentity,
    TakeValidationResult,
    find_changed_take,
    is_local_stem_name,
    load_take,
    recorder_client_observations,
    recording_staging_identity,
    snapshot_take_directories,
    wait_for_take_files_stable,
    write_take_manifest,
)
from core.take_project import (
    HostIdentity,
    RecoveryStatus,
    SessionEvidence,
    SessionTimelineEvent,
    new_project_id,
)

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.recording")
_FINAL_RECEIPT_DRAIN_TIMEOUT_S = 5.0
_SHARED_TRACK_FINALIZE_TIMEOUT_S = 5.0
_PEER_INVENTORY_FINALIZE_TIMEOUT_S = 30.0
_GUEST_CAPTURE_ARM_TIMEOUT_S = 8.0
_SHARED_TRACK_PARTICIPANT_LABEL = "participant:shared-track"
_SHARED_TRACK_RECORDER_CHANNELS = 2
_RECORDING_DIAGNOSTIC_MAX_COUNT = 1_000_000
_RECORDING_FAILURE_PRIORITIES = {
    "none": 0,
    "capture_gap": 10,
    "take_needs_attention": 20,
    "peer_inventory": 30,
    "shared_track_playback_unproven": 40,
    "shared_track_cleanup_unconfirmed": 50,
    "shared_track_dropout": 60,
    "recorder_control_failure": 70,
    "unexpected_stop": 80,
    "take_publication": 90,
}
_RECORDING_FAILURE_CATEGORIES = {
    "none": "none",
    "capture_gap": "local_capture",
    "take_needs_attention": "take_validation",
    "peer_inventory": "peer_transfer",
    "shared_track_playback_unproven": "shared_track",
    "shared_track_cleanup_unconfirmed": "shared_track",
    "shared_track_dropout": "shared_track",
    "recorder_control_failure": "recorder",
    "unexpected_stop": "recorder",
    "take_publication": "take_validation",
}


def _shared_track_participant_id(session_id: str) -> str:
    """Return the canonical Shared Track identity for one WebJam session.

    Recorder receipts need a durable participant identity that survives take
    boundaries, while still remaining scoped to the current private session.
    Deriving it from the session UUID gives every take the same opaque identity
    without weakening the separate process/socket/generation proof that binds a
    native recorder row to the owned Shared Track client.
    """

    session_namespace = uuid.UUID(str(session_id))
    return str(uuid.uuid5(session_namespace, _SHARED_TRACK_PARTICIPANT_LABEL))


class RecorderPhase(str, Enum):
    IDLE = "idle"
    PREFLIGHT = "preflight"
    STARTING = "starting"
    COUNT_IN = "count_in"
    RECORDING = "recording"
    STOPPING = "stopping"
    FINALIZING = "finalizing"
    # Compatibility alias for older tests/callers. New lifecycle evidence and
    # UI projection use the truthful public name ``finalizing``.
    VALIDATING = "finalizing"
    COMPLETE = "complete"
    READY = "complete"
    NEEDS_ATTENTION = "needs_attention"
    STOP_FAILED = "stop_failed"
    ERROR = "error"


class _PublishedTakeStatus(str, Enum):
    """Bounded recovery lookup result; uncertainty never means absence."""

    MATCH = "match"
    ABSENT = "absent"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class RecorderSnapshot:
    phase: RecorderPhase
    armed: bool
    recording: bool


@dataclass(frozen=True, repr=False)
class _ToggleAttempt:
    """One recorder RPC request bound to the take that requested it.

    Jamulus recorder notifications and RPC replies arrive independently.  The
    take ID is therefore carried back to the UI thread so a late worker reply
    can never mutate a later take (or revive one that has entered validation).
    """

    take_id: str
    target_armed: bool
    server_rpc_port: int
    server_rpc_secret_file: str
    server_rpc_secret_identity: tuple[int, int, int, int]


@dataclass(frozen=True, repr=False)
class _RosterObservationContext:
    """One take-bound, address-free roster correlation request."""

    take_id: str
    channel_bindings: tuple[tuple[int, str, str, int], ...]
    ordered_roster_proof: JamulusOrderedRosterProof | None
    recording_presence_proofs: tuple[object, ...]
    require_presence_v2: bool
    host_participant_id: str
    reference_claim: object | None
    server_rpc_port: int = 0
    server_rpc_secret_file: str = ""
    server_rpc_secret_identity: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, repr=False)
class _HostedRecordingReadinessContext:
    """Read-only facts captured before a hosted take owns any resources."""

    ordered_roster_proof: JamulusOrderedRosterProof
    recording_presence_proofs: tuple[object, ...]
    presence_authority: tuple[tuple[object, ...], ...]
    host_participant_id: str
    participant_cards: tuple[tuple[int, str], ...]
    host_peer_identity: int
    server_rpc_port: int
    server_rpc_secret_file: str
    server_rpc_secret_identity: tuple[int, int, int, int]


@dataclass(frozen=True, repr=False)
class _HostedRecordingReadiness:
    """Complete correlation result safe to consume when allocating a take."""

    context: _HostedRecordingReadinessContext
    musician_ids_by_channel: tuple[tuple[int, str], ...]
    reference_channels: tuple[int, ...]
    channel_counts_by_channel: tuple[tuple[int, int], ...]


@dataclass(frozen=True, repr=False)
class _GuestCaptureArmAttempt:
    """One immutable guest-input arm request awaiting exact start ACKs."""

    take_id: str
    plan_fingerprint: str
    arm_generation: int
    hosted_readiness: _HostedRecordingReadiness
    host_peer: object


def _presence_authority_snapshot(
    proofs: tuple[object, ...],
) -> tuple[tuple[object, ...], ...] | None:
    """Reduce lease-specific proofs to stable peer-correlation claims."""

    reduced: list[tuple[object, ...]] = []
    ordinals: set[int] = set()
    participants: set[str] = set()
    topology_epochs: set[int] = set()
    try:
        for proof in proofs:
            if getattr(proof, "recorder_eligible", False) is not True:
                return None
            participant_id = str(uuid.UUID(str(proof.participant_id)))
            ordinal = int(proof.self_ordinal)
            topology_epoch = int(proof.topology_epoch)
            process_generation = int(proof.process_generation)
            rpc_generation = int(proof.rpc_connection_generation)
            audio_generation = int(proof.audio_connection_generation)
            roster_count = int(proof.roster_count)
            if (
                ordinal < 0
                or topology_epoch <= 0
                or process_generation <= 0
                or rpc_generation <= 0
                or audio_generation <= 0
                or roster_count <= 0
                or ordinal >= roster_count
                or ordinal in ordinals
                or participant_id in participants
            ):
                return None
            ordinals.add(ordinal)
            participants.add(participant_id)
            topology_epochs.add(topology_epoch)
            reduced.append(
                (
                    ordinal,
                    participant_id,
                    " ".join(str(proof.display_name or "").split())[:120],
                    str(proof.ordered_roster_digest),
                    roster_count,
                    process_generation,
                    rpc_generation,
                    audio_generation,
                    topology_epoch,
                    bool(proof.capture_enabled),
                )
            )
    except (AttributeError, TypeError, ValueError):
        return None
    if len(topology_epochs) > 1:
        return None
    return tuple(sorted(reduced))


def _is_proven_newer_lifecycle(
    prior: tuple[object, ...] | None,
    current: tuple[object, ...] | None,
    *,
    require_client_transition: bool,
) -> bool:
    """Return whether private evidence proves a recorder lifecycle boundary."""

    if prior is None or current is None or prior[:1] != current[:1]:
        return False
    if current[0] == "musician":
        try:
            newer_topology = int(current[1]) > int(prior[1])
        except (IndexError, TypeError, ValueError):
            return False
        return bool(
            newer_topology
            and (not require_client_transition or current[2:] != prior[2:])
        )
    if current[0] == "reference_track":
        # Each claim is captured before and after server RPC, proves an exact
        # PID-owned UDP socket, and remains memory-only. A changed private
        # process generation or exact socket therefore proves a new Reference
        # Track segment without relying on its copyable display name.
        return current[1:] != prior[1:]
    return False


def _private_secret_file_identity(path_value: object) -> tuple[int, int, int, int]:
    """Return a memory-only identity for the configured recorder secret file."""

    path = Path(str(path_value or "").strip()).expanduser()
    if not str(path_value or "").strip():
        raise ValueError("recorder secret file is unavailable")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("recorder secret file is unavailable")
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _read_exact_secret_file(
    path: str,
    expected_identity: tuple[int, int, int, int],
) -> str:
    """Read one captured secret without exposing its path on failure."""

    from core.jamulus_server_rpc import ServerRpcError, read_secret_file

    try:
        if _private_secret_file_identity(path) != expected_identity:
            raise OSError("secret identity changed")
        secret = read_secret_file(path)
        if _private_secret_file_identity(path) != expected_identity:
            raise OSError("secret identity changed")
    except Exception:  # noqa: BLE001 - replace path-bearing detail
        raise ServerRpcError(
            "The captured recorder configuration is no longer available."
        ) from None
    return secret


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
        self._staged_take_scan_done = False
        self._staged_media_take_ids: set[str] = set()
        # The validation worker, toggle-failure handler, and shutdown salvage
        # all hand off the capture; the lock makes the hand-off atomic so the
        # stream is finalized exactly once.
        self._capture_lock = threading.Lock()
        self._local_capture_track_count = 0
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
        # Native recorder identity is captured only from the authenticated
        # server RPC. Raw addresses are reduced to recorder-key digests inside
        # the worker and never retained, logged, or serialized.
        self._receipt_lock = threading.RLock()
        self._receipt_condition = threading.Condition(self._receipt_lock)
        self._receipt_observation_lock = threading.RLock()
        self._recording_receipts: dict[tuple[str, int], RecorderClientReceipt] = {}
        self._recording_conflicted_keys: set[str] = set()
        self._recording_unproven_keys: set[str] = set()
        self._recording_digest_by_channel: dict[int, str] = {}
        self._recording_channel_by_digest: dict[str, int] = {}
        self._recording_owner_by_channel: dict[int, str] = {}
        self._recording_lifecycle_by_channel: dict[int, tuple[object, ...]] = {}
        self._recording_owner_by_digest: dict[str, str] = {}
        self._recording_lifecycle_by_digest: dict[str, tuple[object, ...]] = {}
        self._recording_identity_errors: list[str] = []
        self._recording_identity_invalid = False
        self._recording_presence_retry_pending = False
        self._reference_participant_id = _shared_track_participant_id(self._session_id)
        # The recorder endpoint and secret-file identity are captured once per
        # take. Workers use this immutable binding instead of mutable Settings.
        self._recording_rpc_take_id = ""
        self._recording_rpc_port = 0
        # One authoritative SessionRecordingPlan is bound before any recorder
        # starts and cleared with the take. Guarded by the evidence lock with
        # its fingerprint.
        self._recording_plan: SessionRecordingPlan | None = None
        self._recording_plan_take_id = ""
        self._recording_plan_fingerprint = ""
        self._guest_capture_arm_take_id = ""
        self._guest_capture_arm_generation = 0
        self._recording_creator_profile_key = "music"
        self._recording_rpc_secret_file = ""
        self._recording_rpc_secret_identity: tuple[int, int, int, int] | None = None
        self._roster_poll_inflight = False
        self._roster_poll_pending: _RosterObservationContext | None = None
        self._recording_receipts_finalizing_take_id = ""
        self._recording_receipts_frozen_take_id = ""
        self._hosted_preflight_generation = 0
        # Session teardown may ask from either a worker or the UI thread. Once
        # recorder stop is confirmed, keep the server/application alive until
        # the ordinary take-validation owner has durably published the media.
        self._shutdown_stop_lock = threading.Lock()
        self._shutdown_validation_pending_take_id = ""
        self._shutdown_validation_dispatch_take_id = ""
        # A Record Session may include one host-owned Shared Track.  The
        # pending choice is frozen only when a take ID is allocated; playback
        # and route teardown then become conjunctive take truth rather than
        # unrelated UI operations that can finish after a false success.
        self._shared_track_condition = threading.Condition(threading.RLock())
        self._pending_shared_track_required = False
        self._shared_track_take_id = ""
        self._shared_track_required = False
        self._shared_track_playback_proven = False
        self._shared_track_recorder_active = False
        self._shared_track_underrun_baseline = 0
        self._shared_track_underrun_peak = 0
        self._shared_track_cleanup_requested = False
        self._shared_track_cleanup_confirmed = False
        self._initial_peer_inventory_take_id = ""
        # Peer manifest callbacks are normally marshalled onto Qt's UI thread,
        # but the lock also preserves this seam for direct tests/alternate
        # invokers. A callback racing terminal validation is latched, never
        # discarded, and reloaded immediately before terminal publication.
        self._peer_reconcile_lock = threading.Lock()
        self._pending_peer_reconciliations: dict[str, Path] = {}
        # This deliberately contains only bounded counters, fixed categories,
        # and opaque UUIDs. It is safe for diagnostics/support-bundle callers;
        # source names, paths, device names, and raw validation text stay out.
        self._recording_diagnostics_lock = threading.Lock()
        self._recording_generation = 0
        self._diagnostic_current_take_id = ""
        self._diagnostic_last_take_id = ""
        self._recording_dropout_gap_count = 0
        self._recording_failure_reason_code = "none"

    def _begin_recording_diagnostics(self, take_id: str) -> None:
        try:
            canonical_take = str(uuid.UUID(str(take_id)))
        except (AttributeError, TypeError, ValueError):
            canonical_take = ""
        with self._recording_diagnostics_lock:
            self._recording_generation = min(
                (1 << 63) - 1,
                self._recording_generation + 1,
            )
            self._diagnostic_current_take_id = canonical_take
            self._recording_dropout_gap_count = 0
            self._recording_failure_reason_code = "none"

    def _record_diagnostic_failure(self, reason_code: str) -> None:
        code = str(reason_code or "").strip().lower()
        if code not in _RECORDING_FAILURE_PRIORITIES:
            code = "take_needs_attention"
        with self._recording_diagnostics_lock:
            current = self._recording_failure_reason_code
            if (
                _RECORDING_FAILURE_PRIORITIES[code]
                >= _RECORDING_FAILURE_PRIORITIES[current]
            ):
                self._recording_failure_reason_code = code

    def _record_dropout_gaps(self, count: int) -> None:
        try:
            bounded = max(0, min(_RECORDING_DIAGNOSTIC_MAX_COUNT, int(count)))
        except (TypeError, ValueError):
            bounded = 0
        if not bounded:
            return
        with self._recording_diagnostics_lock:
            self._recording_dropout_gap_count = min(
                _RECORDING_DIAGNOSTIC_MAX_COUNT,
                self._recording_dropout_gap_count + bounded,
            )
        self._record_diagnostic_failure("capture_gap")

    def public_diagnostics(self) -> dict[str, object]:
        """Return path-free, bounded Record Session lifecycle diagnostics."""

        with self._recording_diagnostics_lock:
            generation = self._recording_generation
            current_take_id = self._diagnostic_current_take_id
            last_take_id = self._diagnostic_last_take_id
            dropout_gap_count = self._recording_dropout_gap_count
            failure_reason_code = self._recording_failure_reason_code
        with self._shared_track_condition:
            cleanup_pending = bool(
                self._shared_track_required
                and not self._shared_track_cleanup_confirmed
                and (
                    self._shared_track_cleanup_requested
                    or self.phase
                    in {
                        RecorderPhase.STOPPING,
                        RecorderPhase.STOP_FAILED,
                        RecorderPhase.FINALIZING,
                    }
                )
            )
        return {
            "generation": generation,
            "current_take_id": current_take_id,
            "last_take_id": last_take_id,
            "dropout_gap_count": dropout_gap_count,
            "cleanup_pending": cleanup_pending,
            "failure_reason_code": failure_reason_code,
            "failure_category": _RECORDING_FAILURE_CATEGORIES[failure_reason_code],
        }

    def plan_shared_track_for_next_take(self, *, required: bool) -> None:
        """Freeze the host's path-free Shared Track intent at take allocation."""

        with self._shared_track_condition:
            self._pending_shared_track_required = bool(required)

    def _begin_shared_track_transaction(self, take_id: str) -> None:
        with self._shared_track_condition:
            self._shared_track_take_id = str(take_id or "")
            self._shared_track_required = bool(
                self._pending_shared_track_required and take_id
            )
            self._pending_shared_track_required = False
            self._shared_track_playback_proven = False
            self._shared_track_recorder_active = False
            self._shared_track_underrun_baseline = 0
            self._shared_track_underrun_peak = 0
            self._shared_track_cleanup_requested = False
            self._shared_track_cleanup_confirmed = False
            self._shared_track_condition.notify_all()

    @staticmethod
    def _shared_track_underrun_frames(snapshot: object) -> int:
        try:
            return max(
                0,
                min((1 << 63) - 1, int(getattr(snapshot, "underrun_frames", 0))),
            )
        except (AttributeError, TypeError, ValueError):
            return 0

    def _begin_shared_track_recording_window(self, take_id: str) -> None:
        """Open underrun/playback evidence only after recorder confirmation."""

        controller = getattr(self._c, "_reference_track", None)
        snapshot = getattr(controller, "snapshot", None)
        baseline = self._shared_track_underrun_frames(snapshot)
        with self._shared_track_condition:
            if not (
                take_id
                and self._shared_track_required
                and self._shared_track_take_id == take_id
                and self._take_id == take_id
            ):
                return
            self._shared_track_recorder_active = True
            self._shared_track_underrun_baseline = baseline
            self._shared_track_underrun_peak = baseline
            self._shared_track_condition.notify_all()

    def _finish_shared_track_recording_window(self, take_id: str) -> int:
        """Close recorder overlap and return only this take's underrun delta."""

        controller = getattr(self._c, "_reference_track", None)
        snapshot = getattr(controller, "snapshot", None)
        if snapshot is not None:
            # Capture the final recorder-overlap sample while the window is
            # still active. Playback after this point belongs to route cleanup,
            # not to the recorded take.
            self.observe_shared_track_snapshot(snapshot)
        with self._shared_track_condition:
            if not (
                take_id
                and self._shared_track_required
                and self._shared_track_take_id == take_id
            ):
                return 0
            self._shared_track_recorder_active = False
            delta = max(
                0,
                self._shared_track_underrun_peak - self._shared_track_underrun_baseline,
            )
            self._shared_track_condition.notify_all()
        if delta:
            self._record_dropout_gaps(1)
            self._record_diagnostic_failure("shared_track_dropout")
        return delta

    @property
    def shared_track_required_for_active_take(self) -> bool:
        with self._shared_track_condition:
            return bool(
                self._shared_track_required
                and self._shared_track_take_id
                and self._shared_track_take_id == self._take_id
            )

    def observe_shared_track_snapshot(self, snapshot: object) -> None:
        """Reduce a local Shared Track snapshot to take-scoped lifecycle truth."""

        next_phase: RecorderPhase | None = None
        with self._shared_track_condition:
            if not (
                self._shared_track_required
                and self._shared_track_take_id
                and self._shared_track_take_id == self._take_id
            ):
                return
            state = str(
                getattr(getattr(snapshot, "state", None), "value", "") or ""
            ).lower()
            if self._shared_track_recorder_active and state == "playing":
                self._shared_track_playback_proven = True
            if self._shared_track_recorder_active and self.phase in {
                RecorderPhase.RECORDING,
                RecorderPhase.COUNT_IN,
            }:
                next_phase = (
                    RecorderPhase.COUNT_IN
                    if bool(getattr(snapshot, "count_in_active", False))
                    else RecorderPhase.RECORDING
                )
            if self._shared_track_recorder_active:
                self._shared_track_underrun_peak = max(
                    self._shared_track_underrun_peak,
                    self._shared_track_underrun_frames(snapshot),
                )
            if self._shared_track_cleanup_requested:
                active = bool(getattr(snapshot, "active", False))
                cleanup_pending = bool(getattr(snapshot, "cleanup_pending", False))
                if (
                    not active
                    and not cleanup_pending
                    and state in {"ready", "unavailable", "idle", "closed"}
                ):
                    self._shared_track_cleanup_confirmed = True
            self._shared_track_condition.notify_all()
        if (
            next_phase is not None
            and self.phase in {RecorderPhase.RECORDING, RecorderPhase.COUNT_IN}
            and self.phase is not next_phase
        ):
            self._set_phase(next_phase)

    def _confirmed_active_recording_phase(self) -> RecorderPhase:
        """Return the exact active phase visible after recorder confirmation."""

        controller = getattr(self._c, "_reference_track", None)
        snapshot = getattr(controller, "snapshot", None)
        with self._shared_track_condition:
            shared_required = bool(
                self._shared_track_required
                and self._shared_track_take_id
                and self._shared_track_take_id == self._take_id
            )
        if shared_required and bool(getattr(snapshot, "count_in_active", False)):
            return RecorderPhase.COUNT_IN
        return RecorderPhase.RECORDING

    def note_shared_track_cleanup_requested(self) -> None:
        """Join the host route's Stop acknowledgement to the active take."""

        with self._shared_track_condition:
            if not (
                self._shared_track_required
                and self._shared_track_take_id
                and self._shared_track_take_id == self._take_id
            ):
                return
            self._shared_track_cleanup_requested = True
            self._shared_track_condition.notify_all()
        controller = getattr(self._c, "_reference_track", None)
        snapshot = getattr(controller, "snapshot", None)
        if snapshot is not None:
            self.observe_shared_track_snapshot(snapshot)

    def _await_shared_track_transaction_errors(
        self,
        take_id: str,
    ) -> tuple[str, ...]:
        """Wait off the UI thread for required playback and route retirement."""

        with self._shared_track_condition:
            required = bool(
                take_id
                and self._shared_track_required
                and self._shared_track_take_id == take_id
            )
        if not required:
            return ()

        deadline = time.monotonic() + _SHARED_TRACK_FINALIZE_TIMEOUT_S
        while True:
            controller = getattr(self._c, "_reference_track", None)
            snapshot = getattr(controller, "snapshot", None)
            if snapshot is not None:
                self.observe_shared_track_snapshot(snapshot)
            with self._shared_track_condition:
                if self._shared_track_take_id != take_id:
                    return (
                        ("Shared Track recording evidence changed before the take "
                        "was finalized. The take was preserved for review."),
                    )
                settled = bool(
                    self._shared_track_playback_proven
                    and self._shared_track_cleanup_requested
                    and self._shared_track_cleanup_confirmed
                )
                if settled or time.monotonic() >= deadline:
                    playback_proven = self._shared_track_playback_proven
                    cleanup_requested = self._shared_track_cleanup_requested
                    cleanup_confirmed = self._shared_track_cleanup_confirmed
                    break
                self._shared_track_condition.wait(
                    timeout=min(0.1, max(0.0, deadline - time.monotonic()))
                )

        errors: list[str] = []
        if not playback_proven:
            self._record_diagnostic_failure("shared_track_playback_unproven")
            errors.append(
                "Shared Track playback was required for this Record Session, "
                "but its owned route never reached confirmed playback. The take "
                "was preserved for review."
            )
        if not cleanup_requested or not cleanup_confirmed:
            self._record_diagnostic_failure("shared_track_cleanup_unconfirmed")
            errors.append(
                "Shared Track cleanup was not confirmed before take publication. "
                "The take was preserved for review."
            )
        return tuple(errors)

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
        self._set_phase(RecorderPhase.FINALIZING)
        self._begin_take_validation(take_id)
        return True

    def _retire_active_take(self, take_id: str) -> None:
        """Forget active ownership after a terminal validation/recovery path."""

        host_peer = getattr(self._c, "host_peer", None)
        cancel_guest_arm = getattr(host_peer, "cancel_capture_arm", None)
        if take_id and callable(cancel_guest_arm):
            try:
                cancel_guest_arm(take_id)
            except Exception:  # noqa: BLE001 - retirement stays idempotent
                LOGGER.warning("Could not cancel a pending guest capture arm.")
        discard_guest_plan = getattr(
            host_peer,
            "discard_prepared_local_original_obligations",
            None,
        )
        if take_id and callable(discard_guest_plan):
            try:
                discard_guest_plan(take_id)
            except Exception:  # noqa: BLE001 - retirement stays idempotent
                LOGGER.warning("Could not retire an unused guest inventory plan.")
        with self._recording_diagnostics_lock:
            if take_id and self._diagnostic_current_take_id == take_id:
                self._diagnostic_last_take_id = take_id
                self._diagnostic_current_take_id = ""
        with self._peer_reconcile_lock:
            if take_id:
                self._pending_peer_reconciliations.pop(take_id, None)
        if take_id and self._shutdown_validation_pending_take_id == take_id:
            self._shutdown_validation_pending_take_id = ""
        if take_id and self._shutdown_validation_dispatch_take_id == take_id:
            self._shutdown_validation_dispatch_take_id = ""
        if take_id and self._take_id == take_id:
            self._take_id = ""
        if take_id and self._validation_take_id == take_id:
            self._validation_take_id = ""
        with self._receipt_lock:
            if take_id and self._recording_rpc_take_id == take_id:
                self._recording_rpc_take_id = ""
                self._recording_rpc_port = 0
                self._recording_rpc_secret_file = ""
                self._recording_rpc_secret_identity = None
        with self._evidence_lock:
            if take_id and self._recording_plan_take_id == take_id:
                self._recording_plan = None
                self._recording_plan_take_id = ""
                self._recording_plan_fingerprint = ""
            if take_id and self._guest_capture_arm_take_id == take_id:
                self._guest_capture_arm_take_id = ""
                self._guest_capture_arm_generation = 0
        with self._shared_track_condition:
            if take_id and self._shared_track_take_id == take_id:
                self._shared_track_take_id = ""
                self._shared_track_required = False
                self._shared_track_playback_proven = False
                self._shared_track_recorder_active = False
                self._shared_track_underrun_baseline = 0
                self._shared_track_underrun_peak = 0
                self._shared_track_cleanup_requested = False
                self._shared_track_cleanup_confirmed = False
                self._shared_track_condition.notify_all()

    def _recording_rpc_binding_for_take(
        self,
        take_id: str,
    ) -> tuple[int, str, tuple[int, int, int, int]] | None:
        """Return the immutable, memory-only recorder binding for one take."""

        with self._receipt_lock:
            if (
                not take_id
                or self._recording_rpc_take_id != take_id
                or self._recording_rpc_port <= 0
                or not self._recording_rpc_secret_file
                or self._recording_rpc_secret_identity is None
            ):
                return None
            return (
                self._recording_rpc_port,
                self._recording_rpc_secret_file,
                self._recording_rpc_secret_identity,
            )

    def _bind_recording_rpc_configuration(
        self,
        take_id: str,
        port: int,
        secret_file: str,
        secret_identity: tuple[int, int, int, int],
    ) -> None:
        """Install an atomic take-scoped RPC binding after preflight."""

        with self._receipt_lock:
            if not take_id or take_id != self._take_id:
                raise RuntimeError("recording take changed")
            self._recording_rpc_take_id = take_id
            self._recording_rpc_port = int(port)
            self._recording_rpc_secret_file = str(secret_file)
            self._recording_rpc_secret_identity = secret_identity

    def _secret_for_bound_context(
        self,
        context: _RosterObservationContext,
    ) -> tuple[int, str]:
        """Revalidate and read the exact RPC secret captured for this take."""

        identity = context.server_rpc_secret_identity
        expected = (
            context.server_rpc_port,
            context.server_rpc_secret_file,
            identity,
        )
        if (
            identity is None
            or self._recording_rpc_binding_for_take(context.take_id) != expected
        ):
            raise RuntimeError("recording RPC binding is unavailable")
        return (
            context.server_rpc_port,
            _read_exact_secret_file(context.server_rpc_secret_file, identity),
        )

    def _toggle_callback_is_current(self, take_id: str | None) -> bool:
        """Reject a late RPC completion for a retired or validating take."""

        if take_id is None:
            # Direct controller/unit-test calls predate take-bound callbacks.
            # They remain useful for isolated state tests; production worker
            # callbacks always carry an explicit take ID.
            return True
        return bool(
            take_id and take_id == self._take_id and take_id != self._validation_take_id
        )

    @staticmethod
    def _utc_timestamp() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
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

    def _set_recovery_locked(self, status: RecoveryStatus, note: str = "") -> None:
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
        self._shutdown_validation_pending_take_id = ""
        self._shutdown_validation_dispatch_take_id = ""
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
            self._recording_plan = None
            self._recording_plan_take_id = ""
            self._recording_plan_fingerprint = ""
            active_profile = getattr(
                getattr(self._c, "creator_profile", None),
                "key",
                None,
            )
            self._recording_creator_profile_key = (
                canonical_creator_profile_key(active_profile)
                or canonical_creator_profile_key(
                    getattr(
                        self._c.settings,
                        "last_creator_profile_key",
                        "music",
                    )
                )
                or "music"
            )
            self._append_evidence_event_locked(
                "recording_requested",
                detail="Waiting for the band server to confirm recording.",
            )
        with self._receipt_lock:
            self._recording_receipts = {}
            self._recording_conflicted_keys = set()
            self._recording_unproven_keys = set()
            self._recording_digest_by_channel = {}
            self._recording_channel_by_digest = {}
            self._recording_owner_by_channel = {}
            self._recording_lifecycle_by_channel = {}
            self._recording_owner_by_digest = {}
            self._recording_lifecycle_by_digest = {}
            self._recording_identity_errors = []
            self._recording_identity_invalid = False
            self._recording_presence_retry_pending = False
            # The Shared Track is one durable session participant, not a new
            # musician on every take. Exact recorder authorization still comes
            # from the current owned process/socket/generation evidence below.
            self._reference_participant_id = _shared_track_participant_id(
                self._session_id
            )
            self._recording_rpc_take_id = ""
            self._recording_rpc_port = 0
            self._recording_rpc_secret_file = ""
            self._recording_rpc_secret_identity = None
            self._roster_poll_pending = None
            self._recording_receipts_finalizing_take_id = ""
            self._recording_receipts_frozen_take_id = ""

    @staticmethod
    def _normalized_roster_name(value: object) -> str:
        return " ".join(str(value or "").split())[:120]

    def _reference_recording_claim(self):
        controller = getattr(self._c, "_reference_track", None)
        reader = getattr(controller, "recording_ownership_claim", None)
        if not callable(reader):
            return None
        try:
            return reader()
        except Exception:  # noqa: BLE001 - private evidence fails absent
            return None

    def _invalidate_recording_identity(
        self,
        note: str,
        *,
        take_id: str | None = None,
    ) -> None:
        """Fail one take closed without retaining an unsafe attribution."""

        expected_take = str(take_id or self._take_id or "")
        if not expected_take:
            return
        with self._receipt_lock:
            if (
                expected_take != self._take_id
                or expected_take == self._recording_receipts_frozen_take_id
            ):
                return
            self._recording_identity_invalid = True
            self._recording_receipts.clear()
            self._recording_unproven_keys.clear()
            safe_note = " ".join(str(note or "").split())[:240]
            if safe_note and safe_note not in self._recording_identity_errors:
                self._recording_identity_errors.append(safe_note)

    def _roster_observation_context(
        self,
        *,
        capture_reference_claim: bool = True,
    ) -> _RosterObservationContext | None:
        take_id = str(self._take_id or "")
        if not take_id:
            return None
        try:
            presentations = list(self._c.participants.values())
        except (AttributeError, RuntimeError):
            presentations = []
        bindings: list[tuple[int, str, str, int]] = []
        host_peer = getattr(self._c, "host_peer", None)
        authenticated_host_registry = bool(
            host_peer is not None and getattr(host_peer, "active", False)
        )
        ordered_proof = getattr(
            self._c,
            "_primary_ordered_roster_proof",
            None,
        )
        if not isinstance(ordered_proof, JamulusOrderedRosterProof) or not ordered_proof.identity.is_process_bound:
            ordered_proof = None
        else:
            try:
                current_proof = self._c.jamulus.ordered_roster_proof_for(
                    ordered_proof.identity
                )
            except Exception:  # noqa: BLE001 - evidence fails absent
                current_proof = None
            if (
                not isinstance(current_proof, JamulusOrderedRosterProof)
                or current_proof.authority_key != ordered_proof.authority_key
            ):
                ordered_proof = None
            else:
                ordered_proof = current_proof

        recording_presence_proofs: tuple[object, ...] = ()
        host_participant_id = ""
        rpc_binding = self._recording_rpc_binding_for_take(take_id)
        if authenticated_host_registry and ordered_proof is not None:
            enrollment = getattr(host_peer, "host_enrollment", None)
            try:
                host_participant_id = str(
                    uuid.UUID(str(enrollment.participant_id))
                )
            except (AttributeError, TypeError, ValueError):
                host_participant_id = ""
            snapshot = getattr(host_peer, "recording_presence_snapshot", None)
            if callable(snapshot):
                try:
                    recording_presence_proofs = tuple(
                        snapshot(
                            ordered_roster_digest=ordered_proof.common_digest,
                            roster_count=ordered_proof.roster_size,
                        )
                    )
                except Exception:  # noqa: BLE001 - evidence fails absent
                    recording_presence_proofs = ()
        for item in presentations:
            try:
                channel_id = int(item.channel_id)
            except (TypeError, ValueError, AttributeError):
                continue
            name = self._normalized_roster_name(
                getattr(item, "name", None) or getattr(item, "role", None)
            )
            # Only the private peer registry binds a Jamulus channel to a
            # durable participant. ParticipantPresentation and the historical
            # recording-start maps may contain generated or stale IDs after a
            # channel is reused, so they are never identity evidence here.
            durable = ""
            generation = 0
            if not authenticated_host_registry:
                # Compatibility seam for isolated tests and legacy extensions.
                # Production hosted recordings always take the authenticated
                # binding-and-generation branch above.
                try:
                    durable = str(
                        self._c.peer_participant_id_for_channel(channel_id) or ""
                    )
                    durable = str(uuid.UUID(durable)) if durable else ""
                except (TypeError, ValueError, AttributeError):
                    durable = ""
                except Exception:  # noqa: BLE001 - optional peer evidence
                    durable = ""
            bindings.append((channel_id, name, durable, generation))
        return _RosterObservationContext(
            take_id=take_id,
            channel_bindings=tuple(bindings),
            ordered_roster_proof=ordered_proof,
            recording_presence_proofs=recording_presence_proofs,
            require_presence_v2=authenticated_host_registry,
            host_participant_id=host_participant_id,
            reference_claim=(
                self._reference_recording_claim() if capture_reference_claim else None
            ),
            server_rpc_port=rpc_binding[0] if rpc_binding is not None else 0,
            server_rpc_secret_file=(rpc_binding[1] if rpc_binding is not None else ""),
            server_rpc_secret_identity=(
                rpc_binding[2] if rpc_binding is not None else None
            ),
        )

    def _hosted_recording_readiness_context(
        self,
        participants: list[object],
    ) -> _HostedRecordingReadinessContext | None:
        """Capture fresh hosted correlation facts without mutating take state."""

        host_peer = getattr(self._c, "host_peer", None)
        if host_peer is None or not getattr(host_peer, "active", False):
            return None
        proof = getattr(self._c, "_primary_ordered_roster_proof", None)
        if not isinstance(proof, JamulusOrderedRosterProof):
            return None
        try:
            current = self._c.jamulus.ordered_roster_proof_for(proof.identity)
        except Exception:  # noqa: BLE001 - readiness fails absent
            return None
        if (
            not isinstance(current, JamulusOrderedRosterProof)
            or current.authority_key != proof.authority_key
        ):
            return None
        enrollment = getattr(host_peer, "host_enrollment", None)
        try:
            host_participant_id = str(
                uuid.UUID(str(enrollment.participant_id))
            )
            proofs = tuple(
                host_peer.recording_presence_snapshot(
                    ordered_roster_digest=current.common_digest,
                    roster_count=current.roster_size,
                )
            )
        except Exception:  # noqa: BLE001 - readiness fails absent
            return None
        authority = _presence_authority_snapshot(proofs)
        if authority is None:
            return None
        try:
            server_rpc_port = int(self._c.settings.server_rpc_port)
            server_rpc_secret_file = str(
                self._c.settings.server_rpc_secret_file or ""
            ).strip()
            secret_identity = _private_secret_file_identity(server_rpc_secret_file)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        cards: list[tuple[int, str]] = []
        seen_channels: set[int] = set()
        for participant in participants:
            try:
                channel_id = int(participant.channel_id)
            except (AttributeError, TypeError, ValueError):
                return None
            name = self._normalized_roster_name(
                getattr(participant, "name", None) or getattr(participant, "role", None)
            )
            if channel_id < 0 or channel_id in seen_channels or not name:
                return None
            seen_channels.add(channel_id)
            cards.append((channel_id, name))
        if not cards:
            return None
        return _HostedRecordingReadinessContext(
            ordered_roster_proof=current,
            recording_presence_proofs=proofs,
            presence_authority=authority,
            host_participant_id=host_participant_id,
            participant_cards=tuple(sorted(cards)),
            host_peer_identity=id(host_peer),
            server_rpc_port=server_rpc_port,
            server_rpc_secret_file=server_rpc_secret_file,
            server_rpc_secret_identity=secret_identity,
        )

    @staticmethod
    def _hosted_presence_by_ordinal(
        context: _HostedRecordingReadinessContext,
    ) -> dict[int, object] | None:
        """Validate current peer claims without treating names as identity."""

        proof = context.ordered_roster_proof
        result: dict[int, object] = {}
        participants: set[str] = set()
        topology_epochs: set[int] = set()
        for claim in context.recording_presence_proofs:
            try:
                ordinal = int(claim.self_ordinal)
                participant_id = str(uuid.UUID(str(claim.participant_id)))
                topology_epoch = int(claim.topology_epoch)
                process_generation = int(claim.process_generation)
                rpc_generation = int(claim.rpc_connection_generation)
                audio_generation = int(claim.audio_connection_generation)
                roster_count = int(claim.roster_count)
            except (AttributeError, TypeError, ValueError):
                return None
            if (
                getattr(claim, "recorder_eligible", False) is not True
                or ordinal < 0
                or ordinal >= proof.roster_size
                or ordinal in result
                or participant_id in participants
                or topology_epoch <= 0
                or process_generation <= 0
                or rpc_generation <= 0
                or audio_generation <= 0
                or roster_count != proof.roster_size
                or claim.ordered_roster_digest != proof.common_digest
                or (
                    ordinal in proof.ambiguous_ordinals and ordinal != proof.own_ordinal
                )
            ):
                return None
            result[ordinal] = claim
            participants.add(participant_id)
            topology_epochs.add(topology_epoch)
        host_claim = result.get(proof.own_ordinal)
        try:
            host_matches = bool(
                host_claim is not None
                and str(uuid.UUID(str(host_claim.participant_id)))
                == context.host_participant_id
                and host_claim.process_generation == proof.identity.process_generation
                and host_claim.rpc_connection_generation
                == proof.rpc_connection_generation
                and host_claim.audio_connection_generation
                == proof.audio_connection_generation
            )
        except (AttributeError, TypeError, ValueError):
            host_matches = False
        if len(topology_epochs) != 1 or not host_matches:
            return None
        return result

    def _evaluate_hosted_recording_readiness(
        self,
        payload: object,
        context: _HostedRecordingReadinessContext,
        *,
        reference_before: object | None,
        reference_after: object | None,
    ) -> _HostedRecordingReadiness | None:
        """Correlate client, server, peer, and exact Reference Track facts."""

        from core.reference_track import ReferenceTrackOwnershipClaim

        try:
            current = self._c.jamulus.ordered_roster_proof_for(
                context.ordered_roster_proof.identity
            )
            host_peer = self._c.host_peer
            current_proofs = tuple(
                host_peer.recording_presence_snapshot(
                    ordered_roster_digest=context.ordered_roster_proof.common_digest,
                    roster_count=context.ordered_roster_proof.roster_size,
                )
            )
        except Exception:  # noqa: BLE001 - readiness fails absent
            return None
        if (
            not isinstance(current, JamulusOrderedRosterProof)
            or current.authority_key != context.ordered_roster_proof.authority_key
            or not getattr(host_peer, "active", False)
            or id(host_peer) != context.host_peer_identity
            or _presence_authority_snapshot(current_proofs)
            != context.presence_authority
        ):
            return None
        presence_by_ordinal = self._hosted_presence_by_ordinal(context)
        if presence_by_ordinal is None:
            return None
        stable_reference = (
            reference_before
            if isinstance(reference_before, ReferenceTrackOwnershipClaim)
            and reference_before == reference_after
            else None
        )
        try:
            if not isinstance(payload, dict):
                return None
            raw_rows = payload.get("clients")
            if not isinstance(raw_rows, list) or not raw_rows:
                return None
            server_ids: list[int] = []
            profiles = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, dict):
                    return None
                server_id = raw_row.get("id")
                if (
                    isinstance(server_id, bool)
                    or not isinstance(server_id, int)
                    or server_id < 0
                ):
                    return None
                server_ids.append(server_id)
                profiles.append(server_common_profile(raw_row))
            if any(
                later <= earlier for earlier, later in itertools.pairwise(server_ids)
            ):
                return None
            proof = context.ordered_roster_proof
            if (
                len(raw_rows) != proof.roster_size
                or ordered_common_roster_digest(tuple(profiles)) != proof.common_digest
            ):
                return None
            observations = recorder_client_observations(
                payload,
                owned_reference_udp_port=(
                    stable_reference.udp_port if stable_reference is not None else None
                ),
            )
        except (JamulusRosterIdentityError, RecorderRosterError):
            return None
        if len(observations) != context.ordered_roster_proof.roster_size:
            return None
        rows_by_local_id = {
            row.client_local_channel_id: row
            for row in context.ordered_roster_proof.rows
        }
        if set(rows_by_local_id) != {
            channel_id for channel_id, _name in context.participant_cards
        }:
            return None
        channel_by_ordinal: dict[int, int] = {}
        for channel_id, card_name in context.participant_cards:
            row = rows_by_local_id.get(channel_id)
            if row is None or card_name.casefold() != row.profile.name.casefold():
                return None
            channel_by_ordinal[row.ordinal] = channel_id
        musician_ids: list[tuple[int, str]] = []
        reference_channels: list[int] = []
        channel_counts: list[tuple[int, int]] = []
        for ordinal, observation in enumerate(observations):
            channel_id = channel_by_ordinal.get(ordinal)
            if channel_id is None:
                return None
            channel_counts.append((channel_id, observation.channels))
            if observation.matches_owned_reference and stable_reference is not None:
                reference_channels.append(channel_id)
                continue
            claim = presence_by_ordinal.get(ordinal)
            if claim is None:
                return None
            if (
                self._normalized_roster_name(claim.display_name).casefold()
                != observation.display_name.casefold()
            ):
                return None
            try:
                participant_id = str(uuid.UUID(str(claim.participant_id)))
            except (AttributeError, TypeError, ValueError):
                return None
            musician_ids.append((channel_id, participant_id))
        return _HostedRecordingReadiness(
            context=context,
            musician_ids_by_channel=tuple(sorted(musician_ids)),
            reference_channels=tuple(sorted(reference_channels)),
            channel_counts_by_channel=tuple(sorted(channel_counts)),
        )

    def retry_pending_authenticated_roster_observation(self) -> None:
        """Retry provisional v2 correlation from the periodic renewal tick."""

        with self._receipt_lock:
            pending = bool(
                self._recording_presence_retry_pending
                and self._take_id
                and self._recording_receipts_frozen_take_id != self._take_id
            )
        if pending:
            self.request_authenticated_roster_observation()

    def request_authenticated_roster_observation(
        self,
        *,
        exact_process_update: bool = False,
    ) -> None:
        """Coalesce a take-scoped authenticated server-roster receipt."""

        # Process-owned socket inspection is deliberately deferred to the
        # receipt worker. Participant roster updates originate on the UI
        # thread, and native process inspection must never stall that thread.
        context = self._roster_observation_context(capture_reference_claim=False)
        if context is None:
            return
        with self._receipt_lock:
            if context.take_id in {
                self._recording_receipts_finalizing_take_id,
                self._recording_receipts_frozen_take_id,
            }:
                return
            self._roster_poll_pending = context
            if self._roster_poll_inflight:
                if exact_process_update:
                    self._invalidate_recording_identity(
                        "WebJam could not verify every Jamulus roster transition. "
                        "Source audio was preserved for review.",
                        take_id=context.take_id,
                    )
                return
            self._roster_poll_inflight = True
        threading.Thread(
            target=self._roster_observation_worker,
            daemon=True,
            name="recording-roster-receipt",
        ).start()

    def _roster_observation_worker(self) -> None:
        while True:
            with self._receipt_lock:
                context = self._roster_poll_pending
                self._roster_poll_pending = None
            if context is None:
                with self._receipt_lock:
                    if self._roster_poll_pending is None:
                        self._roster_poll_inflight = False
                        self._receipt_condition.notify_all()
                        return
                continue
            try:
                from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

                context = replace(
                    context,
                    reference_claim=self._reference_recording_claim(),
                )
                if context.server_rpc_secret_identity is not None:
                    rpc_port, secret = self._secret_for_bound_context(context)
                else:
                    # Compatibility seam for direct legacy/unit-test takes.
                    secret_file = str(
                        self._c.settings.server_rpc_secret_file or ""
                    ).strip()
                    secret = read_secret_file(secret_file)
                    rpc_port = int(self._c.settings.server_rpc_port)
                with JamulusServerRpc(
                    port=rpc_port,
                    secret=secret,
                ) as rpc:
                    payload = rpc.get_clients()
                self._consume_authenticated_roster(payload, context)
            except Exception:  # noqa: BLE001 - path-free fail-closed boundary
                # Exception strings may include local paths or endpoints.
                LOGGER.debug("Authenticated recording roster was unavailable")
                self._invalidate_recording_identity(
                    "An authenticated Jamulus recording roster check failed. "
                    "Source audio was preserved for review.",
                    take_id=context.take_id,
                )

    def _consume_authenticated_roster(
        self,
        payload: object,
        context: _RosterObservationContext | None = None,
        *,
        allow_new_receipts: bool = True,
    ) -> None:
        """Reduce one authenticated roster to address-free take receipts."""

        with self._receipt_observation_lock:
            self._consume_authenticated_roster_serial(
                payload,
                context,
                allow_new_receipts=allow_new_receipts,
            )

    def _consume_authenticated_roster_serial(
        self,
        payload: object,
        context: _RosterObservationContext | None,
        *,
        allow_new_receipts: bool = True,
    ) -> None:
        """Serialized implementation used by workers and finalization."""

        context = context or self._roster_observation_context()
        if context is None or context.take_id != self._take_id:
            return
        with self._receipt_lock:
            if context.take_id == self._recording_receipts_frozen_take_id:
                return
        from core.reference_track import (
            REFERENCE_PARTICIPANT_NAME,
            ReferenceTrackOwnershipClaim,
        )

        before = context.reference_claim
        after = self._reference_recording_claim()
        stable_reference = (
            before
            if isinstance(before, ReferenceTrackOwnershipClaim) and before == after
            else None
        )
        owned_port = stable_reference.udp_port if stable_reference is not None else None
        try:
            if not isinstance(payload, dict):
                raise RecorderRosterError("server roster is invalid")
            observations = recorder_client_observations(
                payload,
                owned_reference_udp_port=owned_port,
            )
        except RecorderRosterError as exc:
            with self._receipt_lock:
                if context.take_id != self._take_id:
                    return
                if exc.conflicted_keys:
                    self._recording_conflicted_keys.update(exc.conflicted_keys)
                    for digest in exc.conflicted_keys:
                        for key in tuple(self._recording_receipts):
                            if key[0] == digest:
                                self._recording_receipts.pop(key, None)
                        self._recording_unproven_keys.discard(digest)
                else:
                    self._recording_identity_invalid = True
                    self._recording_receipts.clear()
                    self._recording_unproven_keys.clear()
                note = (
                    "Authenticated Jamulus recording identity evidence was "
                    "ambiguous. Source audio was preserved for review."
                )
                if note not in self._recording_identity_errors:
                    self._recording_identity_errors.append(note)
            return

        server_roster_digest = ""
        server_roster_count = 0
        server_order_proven = False
        if context.require_presence_v2:
            try:
                raw_server_rows = payload.get("clients")
                if not isinstance(raw_server_rows, list) or not raw_server_rows:
                    raise JamulusRosterIdentityError("server roster is empty")
                server_ids: list[int] = []
                server_profiles = []
                for raw_row in raw_server_rows:
                    if not isinstance(raw_row, dict):
                        raise JamulusRosterIdentityError("server roster row is invalid")
                    server_id = raw_row.get("id")
                    if (
                        isinstance(server_id, bool)
                        or not isinstance(server_id, int)
                        or server_id < 0
                    ):
                        raise JamulusRosterIdentityError("server roster id is invalid")
                    server_ids.append(server_id)
                    server_profiles.append(server_common_profile(raw_row))
                if any(
                    later <= earlier
                    for earlier, later in itertools.pairwise(server_ids)
                ):
                    raise JamulusRosterIdentityError("server roster order is invalid")
                if len(observations) != len(raw_server_rows):
                    raise JamulusRosterIdentityError(
                        "server roster observation is incomplete"
                    )
                server_roster_count = len(raw_server_rows)
                server_roster_digest = ordered_common_roster_digest(
                    tuple(server_profiles)
                )
                server_order_proven = True
            except JamulusRosterIdentityError:
                server_order_proven = False

        # Legacy/test callers lack a topology epoch, so any recorder-key move
        # remains permanently ambiguous. Production v2 evaluates transitions
        # only after it has resolved the current ordinal to a durable owner and
        # exact new lifecycle below.
        if not context.require_presence_v2:
            topology_conflicts: set[str] = set()
            with self._receipt_lock:
                if context.take_id != self._take_id:
                    return
                for observation in observations:
                    channel_id = observation.server_channel_id
                    digest = observation.recorder_key_sha256
                    prior_digest = self._recording_digest_by_channel.get(channel_id)
                    prior_channel = self._recording_channel_by_digest.get(digest)
                    if prior_digest and prior_digest != digest:
                        topology_conflicts.update((prior_digest, digest))
                    if prior_channel is not None and prior_channel != channel_id:
                        topology_conflicts.add(digest)
                        destination_digest = self._recording_digest_by_channel.get(
                            channel_id
                        )
                        if destination_digest:
                            topology_conflicts.add(destination_digest)
                if topology_conflicts:
                    self._recording_conflicted_keys.update(topology_conflicts)
                    for key in tuple(self._recording_receipts):
                        if key[0] in topology_conflicts:
                            self._recording_receipts.pop(key, None)
                    self._recording_unproven_keys.difference_update(topology_conflicts)
                    note = (
                        "Jamulus recording-correlation evidence "
                        "conflicted. Source audio was preserved for review."
                    )
                    if note not in self._recording_identity_errors:
                        self._recording_identity_errors.append(note)
                for observation in observations:
                    digest = observation.recorder_key_sha256
                    if digest in self._recording_conflicted_keys:
                        continue
                    self._recording_digest_by_channel.setdefault(
                        observation.server_channel_id, digest
                    )
                    self._recording_channel_by_digest.setdefault(
                        digest, observation.server_channel_id
                    )

        after_context = self._roster_observation_context()
        if after_context is None or after_context.take_id != context.take_id:
            return
        before_bindings = {
            channel_id: (name, participant_id, generation)
            for channel_id, name, participant_id, generation in context.channel_bindings
        }
        after_bindings = {
            channel_id: (name, participant_id, generation)
            for channel_id, name, participant_id, generation in after_context.channel_bindings
        }
        # Legacy bindings are retained only for isolated, non-hosted tests and
        # extensions. A hosted recorder can never translate a client-local
        # mixer ID into a server ID through this compatibility seam.
        stable_legacy_bindings = {
            channel_id: value
            for channel_id, value in before_bindings.items()
            if (
                not context.require_presence_v2
                and value == after_bindings.get(channel_id)
                and value[1]
            )
        }

        before_proof = context.ordered_roster_proof
        after_proof = after_context.ordered_roster_proof
        before_presence_authority = _presence_authority_snapshot(
            context.recording_presence_proofs
        )
        after_presence_authority = _presence_authority_snapshot(
            after_context.recording_presence_proofs
        )
        stable_ordered_roster = bool(
            context.require_presence_v2
            and isinstance(before_proof, JamulusOrderedRosterProof)
            and isinstance(after_proof, JamulusOrderedRosterProof)
            and before_proof.authority_key == after_proof.authority_key
            and server_order_proven
            and server_roster_count == before_proof.roster_size
            and server_roster_digest == before_proof.common_digest
            and before_presence_authority is not None
            and before_presence_authority == after_presence_authority
            and context.host_participant_id == after_context.host_participant_id
            and bool(context.host_participant_id)
        )
        presence_by_ordinal: dict[int, object] = {}
        if stable_ordered_roster:
            for proof in context.recording_presence_proofs:
                try:
                    ordinal = int(proof.self_ordinal)
                    if (
                        getattr(proof, "recorder_eligible", False) is not True
                        or getattr(proof, "ordered_roster_digest", "")
                        != before_proof.common_digest
                        or int(proof.roster_count)
                        != before_proof.roster_size
                        or ordinal < 0
                        or ordinal >= before_proof.roster_size
                        or (
                            ordinal in before_proof.ambiguous_ordinals
                            and ordinal != before_proof.own_ordinal
                        )
                        or ordinal in presence_by_ordinal
                    ):
                        stable_ordered_roster = False
                        presence_by_ordinal.clear()
                        break
                    presence_by_ordinal[ordinal] = proof
                except (AttributeError, TypeError, ValueError):
                    stable_ordered_roster = False
                    presence_by_ordinal.clear()
                    break
        if stable_ordered_roster:
            host_presence = presence_by_ordinal.get(before_proof.own_ordinal)
            try:
                stable_ordered_roster = bool(
                    host_presence is not None
                    and str(uuid.UUID(str(host_presence.participant_id)))
                    == context.host_participant_id
                    and host_presence.process_generation
                    == before_proof.identity.process_generation
                    and host_presence.rpc_connection_generation
                    == before_proof.rpc_connection_generation
                    and host_presence.audio_connection_generation
                    == before_proof.audio_connection_generation
                )
            except (AttributeError, TypeError, ValueError):
                stable_ordered_roster = False
            if not stable_ordered_roster:
                presence_by_ordinal.clear()
        if context.require_presence_v2:
            provisional = not stable_ordered_roster
            if not provisional:
                for ordinal, observation in enumerate(observations):
                    if (
                        observation.matches_owned_reference
                        and stable_reference is not None
                    ):
                        continue
                    presence = presence_by_ordinal.get(ordinal)
                    try:
                        candidate_id = str(uuid.UUID(str(presence.participant_id)))
                        binding_name = self._normalized_roster_name(
                            presence.display_name
                        )
                    except (AttributeError, TypeError, ValueError):
                        candidate_id = ""
                        binding_name = ""
                    if (
                        not candidate_id
                        or binding_name.casefold()
                        != observation.display_name.casefold()
                    ):
                        provisional = True
                        break
            if provisional:
                # Join/reconnect timing can briefly expose a complete native
                # roster before the enrolled WebJam peer has renewed its v2
                # claim. Keep already-proven receipts untouched and let the
                # periodic ordered-presence refresh retry asynchronously.
                with self._receipt_lock:
                    if (
                        context.take_id == self._take_id
                        and not self._recording_identity_invalid
                    ):
                        self._recording_presence_retry_pending = True
                return
            with self._receipt_lock:
                if context.take_id == self._take_id:
                    self._recording_presence_retry_pending = False
        receipts: list[RecorderClientReceipt] = []
        receipt_lifecycle_by_digest: dict[str, tuple[object, ...]] = {}
        unproven_keys: set[str] = set()
        for ordinal, observation in enumerate(observations):
            source_fingerprint = ""
            playback_generation = 0
            if observation.matches_owned_reference and stable_reference is not None:
                participant_id = self._reference_participant_id
                display_name = REFERENCE_PARTICIPANT_NAME
                source_kind = "reference_track"
                source_fingerprint = stable_reference.source_fingerprint_sha256
                playback_generation = stable_reference.playback_generation
                receipt_lifecycle_by_digest[observation.recorder_key_sha256] = (
                    "reference_track",
                    stable_reference.process_id,
                    stable_reference.generation,
                    stable_reference.udp_port,
                    source_fingerprint,
                    playback_generation,
                )
            elif context.require_presence_v2:
                presence = (
                    presence_by_ordinal.get(ordinal) if stable_ordered_roster else None
                )
                candidate_id = getattr(presence, "participant_id", "")
                binding_name = self._normalized_roster_name(
                    getattr(presence, "display_name", "")
                )
                participant_id = ""
                if (
                    binding_name.casefold() == observation.display_name.casefold()
                    and candidate_id
                ):
                    try:
                        participant_id = str(uuid.UUID(str(candidate_id)))
                    except (TypeError, ValueError, AttributeError):
                        participant_id = ""
                if not participant_id:
                    unproven_keys.add(observation.recorder_key_sha256)
                    continue
                display_name = observation.display_name
                source_kind = "musician"
                try:
                    receipt_lifecycle_by_digest[observation.recorder_key_sha256] = (
                        "musician",
                        int(presence.topology_epoch),
                        int(presence.process_generation),
                        int(presence.rpc_connection_generation),
                        int(presence.audio_connection_generation),
                    )
                except (AttributeError, TypeError, ValueError):
                    unproven_keys.add(observation.recorder_key_sha256)
                    continue
            else:
                binding_name, candidate_id, _generation = stable_legacy_bindings.get(
                    observation.server_channel_id, ("", "", 0)
                )
                participant_id = ""
                if (
                    self._normalized_roster_name(binding_name).casefold()
                    == observation.display_name.casefold()
                ):
                    try:
                        participant_id = str(uuid.UUID(str(candidate_id)))
                    except (TypeError, ValueError, AttributeError):
                        participant_id = ""
                if not participant_id:
                    unproven_keys.add(observation.recorder_key_sha256)
                    continue
                display_name = observation.display_name
                source_kind = "musician"
            receipts.append(
                RecorderClientReceipt(
                    server_channel_id=observation.server_channel_id,
                    display_name=display_name,
                    participant_id=participant_id,
                    recorder_key_sha256=observation.recorder_key_sha256,
                    channels=observation.channels,
                    source_kind=source_kind,
                    source_fingerprint_sha256=source_fingerprint,
                    playback_generation=playback_generation,
                )
            )

        with self._receipt_lock:
            if context.take_id != self._take_id or self._recording_identity_invalid:
                return
            if context.require_presence_v2:
                transition_conflicts: set[str] = set()
                for receipt in receipts:
                    digest = receipt.recorder_key_sha256
                    channel_id = receipt.server_channel_id
                    current_lifecycle = receipt_lifecycle_by_digest.get(digest)
                    prior_digest = self._recording_digest_by_channel.get(channel_id)
                    if prior_digest and prior_digest != digest:
                        prior_owner = self._recording_owner_by_channel.get(
                            channel_id, ""
                        )
                        same_owner = prior_owner == receipt.participant_id
                        if not _is_proven_newer_lifecycle(
                            self._recording_lifecycle_by_channel.get(channel_id),
                            current_lifecycle,
                            require_client_transition=same_owner,
                        ):
                            transition_conflicts.update((prior_digest, digest))
                    prior_channel = self._recording_channel_by_digest.get(digest)
                    if prior_channel is not None and prior_channel != channel_id:
                        prior_owner = self._recording_owner_by_digest.get(digest, "")
                        if (
                            prior_owner != receipt.participant_id
                            or not _is_proven_newer_lifecycle(
                                self._recording_lifecycle_by_digest.get(digest),
                                current_lifecycle,
                                require_client_transition=True,
                            )
                        ):
                            transition_conflicts.add(digest)
                            destination_digest = self._recording_digest_by_channel.get(
                                channel_id
                            )
                            if destination_digest:
                                transition_conflicts.add(destination_digest)
                if transition_conflicts:
                    self._recording_conflicted_keys.update(transition_conflicts)
                    for key in tuple(self._recording_receipts):
                        if key[0] in transition_conflicts:
                            self._recording_receipts.pop(key, None)
                    self._recording_unproven_keys.difference_update(
                        transition_conflicts
                    )
                    note = (
                        "Jamulus recording-correlation evidence "
                        "conflicted. Source audio was preserved for review."
                    )
                    if note not in self._recording_identity_errors:
                        self._recording_identity_errors.append(note)
            for digest in unproven_keys:
                existing_keys = tuple(
                    key for key in self._recording_receipts if key[0] == digest
                )
                if existing_keys:
                    # Evidence that once resolved this recorder key no longer
                    # has the same stable owner is a conflict, not permission
                    # to keep the earlier attribution.
                    self._recording_conflicted_keys.add(digest)
                    for key in existing_keys:
                        self._recording_receipts.pop(key, None)
                    self._recording_unproven_keys.discard(digest)
                    note = (
                        "Authenticated Jamulus recording identity evidence "
                        "conflicted. Source audio was preserved for review."
                    )
                    if note not in self._recording_identity_errors:
                        self._recording_identity_errors.append(note)
                elif (
                    allow_new_receipts and digest not in self._recording_conflicted_keys
                ):
                    self._recording_unproven_keys.add(digest)
            for receipt in receipts:
                digest = receipt.recorder_key_sha256
                if digest in self._recording_conflicted_keys:
                    continue
                same_digest = tuple(
                    value
                    for key, value in self._recording_receipts.items()
                    if key[0] == digest
                )
                receipt_key = (digest, receipt.channels)
                if digest in self._recording_unproven_keys:
                    # Filenames do not contain the Jamulus channel ID. A later
                    # stable binding therefore cannot retroactively prove who
                    # owned media created while this recorder key was unbound.
                    self._recording_conflicted_keys.add(digest)
                    for key in tuple(self._recording_receipts):
                        if key[0] == digest:
                            self._recording_receipts.pop(key, None)
                    self._recording_unproven_keys.discard(digest)
                    note = (
                        "Authenticated Jamulus recording identity evidence "
                        "conflicted. Source audio was preserved for review."
                    )
                    if note not in self._recording_identity_errors:
                        self._recording_identity_errors.append(note)
                    continue
                if any(
                    existing.participant_id != receipt.participant_id
                    or existing.source_kind != receipt.source_kind
                    or existing.source_fingerprint_sha256
                    != receipt.source_fingerprint_sha256
                    or existing.playback_generation != receipt.playback_generation
                    for existing in same_digest
                ):
                    self._recording_conflicted_keys.add(digest)
                    for key in tuple(self._recording_receipts):
                        if key[0] == digest:
                            self._recording_receipts.pop(key, None)
                    self._recording_unproven_keys.discard(digest)
                    note = (
                        "Authenticated Jamulus recording identity evidence "
                        "conflicted. Source audio was preserved for review."
                    )
                    if note not in self._recording_identity_errors:
                        self._recording_identity_errors.append(note)
                    continue
                if (
                    not allow_new_receipts
                    and receipt_key not in self._recording_receipts
                ):
                    if same_digest:
                        self._recording_conflicted_keys.add(digest)
                        for key in tuple(self._recording_receipts):
                            if key[0] == digest:
                                self._recording_receipts.pop(key, None)
                        note = (
                            "Authenticated Jamulus recording identity evidence "
                            "conflicted. Source audio was preserved for review."
                        )
                        if note not in self._recording_identity_errors:
                            self._recording_identity_errors.append(note)
                    # A source first seen only after Stop cannot retroactively
                    # own finished media, but its mere presence also does not
                    # taint an otherwise verified take. Any matching WAV still
                    # has no receipt and is marked unverified by the manifest.
                    continue
                self._recording_receipts[receipt_key] = receipt
                self._recording_unproven_keys.discard(digest)
                if context.require_presence_v2:
                    lifecycle = receipt_lifecycle_by_digest.get(digest)
                    if lifecycle is not None:
                        self._recording_digest_by_channel[receipt.server_channel_id] = (
                            digest
                        )
                        self._recording_channel_by_digest[digest] = (
                            receipt.server_channel_id
                        )
                        self._recording_owner_by_channel[receipt.server_channel_id] = (
                            receipt.participant_id
                        )
                        self._recording_lifecycle_by_channel[
                            receipt.server_channel_id
                        ] = lifecycle
                        self._recording_owner_by_digest[digest] = receipt.participant_id
                        self._recording_lifecycle_by_digest[digest] = lifecycle

    def _recording_receipt_snapshot(
        self,
    ) -> tuple[tuple[RecorderClientReceipt, ...], tuple[str, ...]]:
        with self._receipt_lock:
            receipts = (
                ()
                if self._recording_identity_invalid
                else tuple(self._recording_receipts.values())
            )
            errors = list(self._recording_identity_errors)
            if self._recording_unproven_keys:
                errors.append(
                    "WebJam could not prove every Jamulus recording source. "
                    "Source audio was preserved for review."
                )
            return receipts, tuple(errors)

    def recording_source_presentations(
        self,
    ) -> tuple[RecordingSourcePresentation, ...]:
        """One complete, exact, plan-bound source snapshot for the workspace.

        Receipt evidence continues to own Jamulus source state. The immutable
        recording plan supplies stable logical IDs, exact mono/stereo widths,
        and the Local Original inventory. If either side is absent or the
        combined snapshot is ambiguous, return no rows rather than falling
        back to roster order, filenames, or guessed topology.
        """

        with self._evidence_lock:
            take_id = str(self._take_id or "")
            session_id = str(self._session_id or "")
            plan = (
                self._recording_plan
                if self._recording_plan is not None
                and self._recording_plan_take_id == take_id
                and self._recording_plan.take_id == take_id
                and self._recording_plan.session_id == session_id
                else None
            )
        if plan is None or not plan.server_topology_exact:
            return ()

        with self._receipt_lock:
            receipts = (
                ()
                if self._recording_identity_invalid
                else tuple(self._recording_receipts.values())
            )
            conflicted = tuple(self._recording_conflicted_keys)
            frozen = bool(
                self._take_id
                and self._recording_receipts_frozen_take_id == self._take_id
            )
            roster = tuple(
                (
                    self._participant_ids.get(channel_id, ""),
                    self._track_names.get(channel_id, ""),
                )
                for channel_id in sorted(self._participant_ids)
            )
            channel_by_participant = {
                participant_id: channel_id
                for channel_id, participant_id in self._participant_ids.items()
                if participant_id
            }
        projected = project_recording_sources(
            phase=getattr(self.phase, "value", str(self.phase or "")),
            roster=roster,
            receipts=receipts,
            conflicted_keys=conflicted,
            receipts_frozen=frozen,
            shared_track_planned=plan.shared_track_planned,
        )
        if not projected:
            return ()

        phase = str(getattr(self.phase, "value", self.phase) or "").lower()
        if phase in {"preflight", "starting"}:
            local_state = RecordingSourceState.ARMED
        elif phase in {"count_in", "recording"}:
            local_state = RecordingSourceState.RECORDING
        elif phase == "stopping":
            local_state = RecordingSourceState.STOPPING
        elif phase == "complete":
            local_state = RecordingSourceState.FINALIZED
        elif phase in {"finalizing", "validating", "needs_attention"}:
            # Until the manifest and guest reconciliation have committed, the
            # UI must not call an intended Local Original finalized or missing.
            local_state = RecordingSourceState.WAITING
        else:
            return ()

        roster_names = dict(plan.roster)
        server_states = {
            row.participant_id: row.state
            for row in projected
            if row.kind == "musician" and row.participant_id
        }
        shared_state = next(
            (row.state for row in projected if row.kind == "shared_track"),
            local_state,
        )
        exact: list[RecordingSourcePresentation] = []
        for participant_id, channels, logical_source_id in zip(
            plan.expected_server_stems,
            plan.server_channel_counts,
            plan.server_logical_source_ids,
            strict=True,
        ):
            if participant_id == self._reference_participant_id:
                exact.append(
                    RecordingSourcePresentation(
                        participant_id="",
                        display_name="Shared Track",
                        kind="shared_track",
                        state=shared_state,
                        channels=channels,
                        logical_source_id=logical_source_id,
                        source_kind=LiveRecordingSourceKind.SHARED_TRACK,
                        channel_id=-1,
                    )
                )
                continue
            channel_id = channel_by_participant.get(participant_id)
            if channel_id is None:
                return ()
            exact.append(
                RecordingSourcePresentation(
                    participant_id=participant_id,
                    display_name=roster_names.get(participant_id, "Participant"),
                    kind="musician",
                    state=server_states.get(participant_id, local_state),
                    channels=channels,
                    logical_source_id=logical_source_id,
                    source_kind=LiveRecordingSourceKind.JAMULUS_SERVER,
                    channel_id=channel_id,
                )
            )

        local_participant_id = str(self._local_participant_id or "")
        active_local_names = {
            logical_source_id: binding.track_name
            for binding, logical_source_id in zip(
                plan.input_maps,
                plan.input_map_logical_source_ids,
                strict=True,
            )
            if binding.enabled and binding.local_original_enabled
        }
        for track in plan.resolved_capture_tracks():
            if not local_participant_id:
                return ()
            exact.append(
                RecordingSourcePresentation(
                    participant_id=local_participant_id,
                    display_name=active_local_names.get(
                        track.logical_source_id, track.stem
                    ),
                    kind="local_original",
                    state=local_state,
                    channels=track.channel_count,
                    logical_source_id=track.logical_source_id,
                    source_kind=LiveRecordingSourceKind.LOCAL_ORIGINAL,
                    channel_id=-1,
                )
            )

        for guest in plan.guest_local_originals:
            guest_name = roster_names.get(guest.participant_id, "Participant")
            for ordinal, (channels, logical_source_id) in enumerate(
                zip(
                    guest.channel_counts,
                    guest.logical_source_ids,
                    strict=True,
                ),
                start=1,
            ):
                exact.append(
                    RecordingSourcePresentation(
                        participant_id=guest.participant_id,
                        display_name=f"{guest_name} · Local Original {ordinal}",
                        kind="local_original",
                        state=local_state,
                        channels=channels,
                        logical_source_id=logical_source_id,
                        source_kind=LiveRecordingSourceKind.LOCAL_ORIGINAL,
                        channel_id=-1,
                    )
                )
        try:
            return validate_exact_recording_sources(exact)
        except Exception:  # noqa: BLE001 - presentation fails absent
            LOGGER.warning("Exact recording source presentation was unavailable")
            return ()

    def _final_recording_receipt_snapshot(
        self,
    ) -> tuple[tuple[RecorderClientReceipt, ...], tuple[str, ...]]:
        """Drain, refresh, and freeze take-scoped identity before publication.

        A recorder-state notification can start validation while an earlier
        roster RPC or the stop worker is still in flight. Publication therefore
        closes admission for new polls, drains the coalesced worker, performs
        one final authenticated observation, and rejects every later result for
        this take. Timeout/failure degrades to NEEDS_ATTENTION rather than
        publishing an attribution from incomplete evidence.
        """

        take_id = str(self._take_id or "")
        if not take_id:
            return self._recording_receipt_snapshot()
        timed_out = False
        deadline = time.monotonic() + _FINAL_RECEIPT_DRAIN_TIMEOUT_S
        with self._receipt_condition:
            if self._recording_receipts_frozen_take_id == take_id:
                return self._recording_receipt_snapshot()
            self._recording_receipts_finalizing_take_id = take_id
            while self._roster_poll_inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    timed_out = True
                    break
                self._receipt_condition.wait(timeout=remaining)

        with self._receipt_observation_lock:
            if timed_out:
                self._invalidate_recording_identity(
                    "WebJam could not finish authenticated recording identity "
                    "verification in time. Source audio was preserved for review.",
                    take_id=take_id,
                )
            else:
                try:
                    from core.jamulus_server_rpc import (
                        JamulusServerRpc,
                        read_secret_file,
                    )

                    context = self._roster_observation_context()
                    if (
                        context is not None
                        and context.server_rpc_secret_identity is not None
                    ):
                        rpc_port, secret = self._secret_for_bound_context(context)
                    else:
                        # Compatibility seam for direct legacy/unit-test takes.
                        secret = read_secret_file(
                            str(self._c.settings.server_rpc_secret_file or "").strip()
                        )
                        rpc_port = int(self._c.settings.server_rpc_port)
                    with JamulusServerRpc(
                        port=rpc_port,
                        secret=secret,
                    ) as rpc:
                        payload = rpc.get_clients()
                    self._consume_authenticated_roster_serial(
                        payload,
                        context,
                        allow_new_receipts=False,
                    )
                    with self._receipt_lock:
                        presence_still_pending = self._recording_presence_retry_pending
                    if presence_still_pending:
                        self._invalidate_recording_identity(
                            "WebJam could not prove a complete current WebJam "
                            "musician roster before finalizing. Source audio "
                            "was preserved for review.",
                            take_id=take_id,
                        )
                except Exception:  # noqa: BLE001 - path-free fail-closed boundary
                    self._invalidate_recording_identity(
                        "WebJam could not complete the final authenticated "
                        "recording roster check. Source audio was preserved "
                        "for review.",
                        take_id=take_id,
                    )
            with self._receipt_lock:
                self._recording_receipts_frozen_take_id = take_id
                self._recording_receipts_finalizing_take_id = ""
        return self._recording_receipt_snapshot()

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
                recording_plan_fingerprint=self._recording_plan_fingerprint,
                creator_profile_key=self._recording_creator_profile_key,
            )

    def _recording_plan_validation_errors(
        self,
        take_id: str,
        receipts: tuple[RecorderClientReceipt, ...],
        *,
        required_local_count: int,
    ) -> tuple[str, ...]:
        """Compare frozen finalization evidence with the immutable start plan."""

        with self._evidence_lock:
            plan = (
                self._recording_plan
                if self._recording_plan_take_id == take_id
                else None
            )
            bound_fingerprint = self._recording_plan_fingerprint
        if plan is None:
            return (
                ("The immutable recording plan was unavailable at finalization. "
                "The take was preserved for review."),
            )
        fingerprint = plan.plan_fingerprint()
        if (
            plan.take_id != take_id
            or not bound_fingerprint
            or fingerprint != bound_fingerprint
            or self._current_session_evidence().recording_plan_fingerprint
            != fingerprint
        ):
            return (
                ("The recording plan identity changed before finalization. The "
                "take was preserved for review."),
            )

        errors: list[str] = []
        expected_ids = set(plan.expected_server_stems)
        observed_ids = {receipt.participant_id for receipt in receipts}
        if observed_ids != expected_ids:
            errors.append(
                "The finalized band-server sources did not exactly match the "
                "immutable recording plan. The take was preserved for review."
            )
        if not plan.server_topology_exact or any(
            plan.channel_count_for_server(receipt.participant_id) != receipt.channels
            for receipt in receipts
        ):
            errors.append(
                "The finalized band-server mono/stereo layout did not exactly "
                "match the immutable recording plan. The take was preserved "
                "for review."
            )

        expected_local_count = sum(
            1
            for item in plan.input_maps
            if item.enabled and item.local_original_enabled
        )
        if expected_local_count != max(0, int(required_local_count)):
            errors.append(
                "The finalized Local Original source count did not match the "
                "immutable recording plan. The take was preserved for review."
            )

        frozen_guest_reader = getattr(
            getattr(self._c, "host_peer", None),
            "local_original_obligations_for_take",
            None,
        )
        try:
            frozen_guest_obligations = (
                tuple(frozen_guest_reader(take_id))
                if callable(frozen_guest_reader)
                else ()
            )
            planned_guest_key = tuple(
                sorted(
                    (
                        item.participant_id,
                        item.track_count,
                        item.map_fingerprint_sha256,
                        item.presence_generation,
                        item.channel_counts,
                        item.logical_source_ids,
                    )
                    for item in plan.guest_local_originals
                )
            )
            frozen_guest_key = tuple(
                sorted(
                    (
                        item.participant_id,
                        item.track_count,
                        item.map_fingerprint,
                        item.presence_generation,
                        tuple(item.channel_counts),
                        tuple(item.logical_source_ids),
                    )
                    for item in frozen_guest_obligations
                )
            )
        except Exception:  # noqa: BLE001 - private guest facts stay redacted
            planned_guest_key = ()
            frozen_guest_key = (("unproven", -1, "", -1),)
        if planned_guest_key != frozen_guest_key:
            errors.append(
                "The finalized guest Local Original obligations did not match "
                "the immutable recording plan. The take was preserved for review."
            )

        reference_receipts = tuple(
            receipt for receipt in receipts if receipt.source_kind == "reference_track"
        )
        binding = plan.shared_track
        if plan.shared_track_planned:
            shared_track_matches = bool(
                binding is not None
                and reference_receipts
                and all(
                    receipt.participant_id == self._reference_participant_id
                    and receipt.source_fingerprint_sha256
                    == binding.source_fingerprint_sha256
                    and receipt.playback_generation == binding.playback_generation
                    for receipt in reference_receipts
                )
            )
            if not shared_track_matches:
                errors.append(
                    "The finalized Shared Track did not match the exact source "
                    "and playback generation in the recording plan. The take "
                    "was preserved for review."
                )
        elif reference_receipts:
            errors.append(
                "An unplanned Shared Track source appeared in the finalized "
                "recording. The take was preserved for review."
            )
        return tuple(dict.fromkeys(errors))

    def _local_capture_plan_validation_errors(
        self,
        take_id: str,
        observed_tracks: object,
        *,
        required_local_count: int,
    ) -> tuple[str, ...]:
        """Require the captured logical mono/stereo topology from the plan."""

        with self._evidence_lock:
            plan = (
                self._recording_plan
                if self._recording_plan_take_id == take_id
                else None
            )
        if plan is None:
            return ()  # The primary plan gate already reports this condition.
        expected = plan.resolved_capture_tracks()
        try:
            observed = tuple(observed_tracks)
        except TypeError:
            observed = ()
        expected_count = max(0, int(required_local_count))
        if len(expected) != expected_count:
            return (
                ("The Local Original topology in the immutable recording plan "
                "was inconsistent. The take was preserved for review."),
            )
        if not expected:
            return (
                ()
                if not observed
                else (
                    ("Unplanned Local Original media appeared in the take. The take "
                    "was preserved for review."),
                )
            )
        if not observed:
            return (
                ("The captured Local Original topology could not be proven. The "
                "take was preserved for review."),
            )
        try:
            matches = len(observed) == len(
                expected
            ) and local_capture_track_map_fingerprint(
                observed
            ) == local_capture_track_map_fingerprint(expected)
        except Exception:  # noqa: BLE001 - private map facts stay redacted
            matches = False
        if matches:
            return ()
        return (
            ("The captured Local Original mono/stereo map did not match the "
            "immutable recording plan. The take was preserved for review."),
        )

    def _create_evidence_journal(self) -> bool:
        """Durably checkpoint a requested take before asking the server to roll."""
        root = (self._c.settings.takes_directory or "").strip()
        take_id = self._take_id
        if not root or not take_id:
            return False
        journal = RecordingManifestJournal(root)
        with self._evidence_lock:
            plan = (
                self._recording_plan
                if self._recording_plan_take_id == take_id
                else None
            )
        try:
            journal.create(
                take_id,
                self._current_session_evidence(),
                plan=plan,
            )
        except (FileExistsError, OSError, RecordingManifestJournalError, ValueError):
            # UUID take IDs make an existing entry unexpected.  Do not replace
            # it: it may be recovery evidence from a previous interrupted take.
            LOGGER.warning("Could not create a private recording-evidence checkpoint.")
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
            plan = (
                self._recording_plan
                if self._recording_plan_take_id == take_id
                else None
            )
        if not journal or not take_id or failed or take_id != self._take_id:
            return
        try:
            journal.update(
                take_id,
                self._current_session_evidence(),
                plan=plan,
            )
        except (OSError, RecordingManifestJournalError, ValueError):
            LOGGER.warning(
                "Could not update the private recording-evidence checkpoint."
            )
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
            LOGGER.warning(
                "Could not remove the completed recording-evidence checkpoint."
            )
        finally:
            with self._evidence_lock:
                if self._evidence_journal is journal:
                    self._evidence_journal = None
                    self._evidence_journal_take_id = ""

    def _retire_journal_for_exact_publication(
        self, take_id: str
    ) -> _PublishedTakeStatus:
        """Retire evidence only after an exact durable schema-v2 publication."""

        root = str(self._c.settings.takes_directory or "").strip()
        if not root:
            return _PublishedTakeStatus.INDETERMINATE
        status = self._published_take_has_id(root, take_id)
        if status is _PublishedTakeStatus.MATCH:
            self._remove_evidence_journal_after_manifest()
        return status

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
        active_take_ids = {
            str(value) for value in (self._take_id, self._validation_take_id) if value
        }
        pending = len(scan.untrusted_entries)
        for item in scan.journals:
            if item.take_id in active_take_ids:
                continue
            published_take = self._published_take_has_id(root, item.take_id)
            if published_take is _PublishedTakeStatus.INDETERMINATE:
                # An incomplete or changing bounded scan cannot prove the
                # media is absent. Keep the checkpoint instead of publishing
                # a contradictory zero-track recovery project.
                pending += 1
                LOGGER.warning(
                    "Published-take recovery evidence could not be safely "
                    "reconciled yet."
                )
                continue
            if published_take is _PublishedTakeStatus.MATCH:
                # A staged-media recovery may already have published the real
                # take. Never create a contradictory zero-media project. A
                # trusted linked checkpoint can be retired; an untrusted one
                # remains as an explicit recovery cue without being parsed.
                if item.trusted:
                    try:
                        RecordingManifestJournal(root).remove(item.take_id)
                    except (OSError, RecordingManifestJournalError, ValueError):
                        pending += 1
                        LOGGER.warning(
                            "Could not retire evidence already linked to a "
                            "published take."
                        )
                else:
                    pending += 1
                continue
            if item.take_id in self._staged_media_take_ids:
                # A media-bearing staged folder still owns this journal, even
                # when ambiguity or mutation made automatic recovery fail.
                # Leave both intact instead of falsely claiming no media was
                # preserved in a second project.
                pending += 1
                continue
            self._publish_recovered_evidence_journal(item, root)
            pending += 1
        for issue in scan.untrusted_entries:
            # Untrusted directory entries are valid recovery cues, not recoverable
            # payload, so we keep the signal and continue. A dedicated project
            # is published from trusted or fallback evidence paths when available.
            LOGGER.warning(
                "Ignoring untrusted recording-evidence entry: %s", issue.error
            )

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
            LOGGER.debug("Could not refresh Studio recovery inventory")

    @staticmethod
    def _published_take_has_id(root: str, take_id: str) -> _PublishedTakeStatus:
        """Boundedly find a stable immediate-child schema-v2 project.

        Malformed children are individually invalid and do not hide a later
        valid project. I/O errors, mutation, or a truncated inventory are
        different: they make absence unprovable, so callers retain the private
        evidence journal and try again on a later launch.
        """

        maximum_bytes = 1024 * 1024

        def fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                int(value.st_dev),
                int(value.st_ino),
                int(value.st_mode),
                int(value.st_size),
                int(value.st_mtime_ns),
            )

        try:
            canonical_id = str(uuid.UUID(str(take_id)))
            root_path = Path(root).expanduser()
        except (TypeError, ValueError, OSError):
            return _PublishedTakeStatus.INDETERMINATE

        uncertain = False
        try:
            for index, child in enumerate(root_path.iterdir()):
                if index >= 512:
                    return _PublishedTakeStatus.INDETERMINATE
                try:
                    child_before = child.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    uncertain = True
                    continue
                if stat.S_ISLNK(child_before.st_mode) or not stat.S_ISDIR(
                    child_before.st_mode
                ):
                    continue

                manifest = child / "webjam-take.json"
                try:
                    manifest_before = manifest.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    uncertain = True
                    continue
                if stat.S_ISLNK(manifest_before.st_mode) or not stat.S_ISREG(
                    manifest_before.st_mode
                ):
                    continue
                if manifest_before.st_size > maximum_bytes:
                    continue

                descriptor = -1
                raw = b""
                try:
                    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                    flags |= getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(manifest, flags)
                    opened_before = os.fstat(descriptor)
                    if not stat.S_ISREG(opened_before.st_mode) or fingerprint(
                        opened_before
                    ) != fingerprint(manifest_before):
                        uncertain = True
                        continue
                    chunks: list[bytes] = []
                    remaining = maximum_bytes + 1
                    while remaining:
                        chunk = os.read(descriptor, min(64 * 1024, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    raw = b"".join(chunks)
                    opened_after = os.fstat(descriptor)
                    if fingerprint(opened_after) != fingerprint(opened_before):
                        uncertain = True
                        continue
                except FileNotFoundError:
                    uncertain = True
                    continue
                except OSError:
                    uncertain = True
                    continue
                finally:
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            uncertain = True

                if len(raw) > maximum_bytes:
                    uncertain = True
                    continue
                try:
                    manifest_after = manifest.lstat()
                    child_after = child.lstat()
                except OSError:
                    uncertain = True
                    continue
                if fingerprint(manifest_after) != fingerprint(
                    manifest_before
                ) or fingerprint(child_after) != fingerprint(child_before):
                    uncertain = True
                    continue

                try:
                    payload = json.loads(raw.decode("utf-8"))
                    if (
                        not isinstance(payload, dict)
                        or payload.get("schema_version") != 2
                    ):
                        continue
                    from core.take_project import TakeProject

                    project = TakeProject.from_dict(payload)
                except (UnicodeError, ValueError, TypeError):
                    continue
                if project.take_id == canonical_id:
                    return _PublishedTakeStatus.MATCH
        except OSError:
            return _PublishedTakeStatus.INDETERMINATE
        return (
            _PublishedTakeStatus.INDETERMINATE
            if uncertain
            else _PublishedTakeStatus.ABSENT
        )

    def _publish_recovered_evidence_journal(self, item, root: str) -> None:
        """Publish interrupt-only evidence as a review-only recovery project."""
        take_id = str(getattr(item, "take_id", "") or "").strip()
        if not take_id or not root:
            return

        recovery_dir = Path(root).expanduser() / f"Recovered-{take_id}"
        manifest_path = recovery_dir / "webjam-take.json"

        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                existing_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
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
                (
                    *evidence.timeline,
                    SessionTimelineEvent(
                        "recording_evidence_recovered", detail=evidence_note
                    ),
                )
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
        except Exception:  # noqa: BLE001 - recovery errors can contain private paths
            LOGGER.error("Could not publish evidence-only recovery project.")
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
        if self._recover_staged_server_takes_once():
            # The staged-media worker owns ordering: it links and retires the
            # exact private journal before ordinary journal recovery can
            # publish a contradictory evidence-only project.
            return
        self._recover_stale_captures_once()
        self._recover_stale_evidence_journals_once()

    @staticmethod
    def _recovery_path_fingerprint(
        path: Path, *, directory: bool
    ) -> tuple[int, int, int, int]:
        info = path.lstat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode):
            raise OSError("unsafe recovery entry")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def _recover_staged_server_takes_once(self) -> bool:
        """Finish address-free publication for interrupted Jamulus takes.

        A process loss can happen after the private staging receipt is durable
        but before every native recorder filename has been replaced.  Scan
        immediate take folders once at startup, then hash and publish on a
        worker so a long take cannot freeze the UI. The resulting project is
        deliberately ``NEEDS_ATTENTION`` rather than guessing musician
        identity.
        """
        if self._staged_take_scan_done:
            return False
        self._staged_take_scan_done = True
        root_text = (self._c.settings.takes_directory or "").strip()
        if not root_text:
            return False
        root = Path(root_text).expanduser()
        try:
            if not root.is_dir():
                return False
            candidates: list[
                tuple[
                    Path,
                    tuple[int, int, int, int],
                    tuple[int, int, int, int],
                    RecordingStagingIdentity | None,
                ]
            ] = []
            for index, child in enumerate(root.iterdir()):
                if index >= 512:
                    LOGGER.warning(
                        "The interrupted-take recovery scan reached its safety limit."
                    )
                    break
                if child.is_symlink():
                    continue
                marker = child / ".webjam-recording-staging.json"
                try:
                    child_fingerprint = self._recovery_path_fingerprint(
                        child, directory=True
                    )
                    marker_fingerprint = self._recovery_path_fingerprint(
                        marker, directory=False
                    )
                except OSError:
                    continue
                candidates.append(
                    (
                        child,
                        child_fingerprint,
                        marker_fingerprint,
                        recording_staging_identity(child),
                    )
                )
        except OSError:
            LOGGER.warning("Could not scan for interrupted take publication.")
            return False

        if not candidates:
            return False
        identity_counts: dict[str, int] = {}
        for _child, _directory, _marker, identity in candidates:
            if identity is not None:
                identity_counts[identity.take_id] = (
                    identity_counts.get(identity.take_id, 0) + 1
                )
        conflicted_take_ids = {
            take_id for take_id, count in identity_counts.items() if count > 1
        }
        self._staged_media_take_ids = set(identity_counts)
        try:
            threading.Thread(
                target=self._recover_staged_server_takes_worker,
                args=(tuple(candidates), root_text, frozenset(conflicted_take_ids)),
                daemon=True,
                name="take-publication-recovery",
            ).start()
        except Exception:  # noqa: BLE001 - generic, path-free boundary
            LOGGER.warning("Could not start interrupted take recovery.")
            return False
        return True

    def _recover_staged_server_takes_worker(
        self,
        candidates: tuple[
            tuple[
                Path,
                tuple[int, int, int, int],
                tuple[int, int, int, int],
                RecordingStagingIdentity | None,
            ],
            ...,
        ],
        root_text: str,
        conflicted_take_ids: frozenset[str],
    ) -> None:
        """Reconcile staged media off the UI thread, then resume startup."""

        journal = RecordingManifestJournal(root_text)
        recovered = 0
        for (
            candidate,
            directory_fingerprint,
            marker_fingerprint,
            staged_identity,
        ) in candidates:
            try:
                if (
                    staged_identity is not None
                    and staged_identity.take_id in conflicted_take_ids
                ):
                    continue
                marker = candidate / ".webjam-recording-staging.json"
                if (
                    self._recovery_path_fingerprint(candidate, directory=True)
                    != directory_fingerprint
                    or self._recovery_path_fingerprint(marker, directory=False)
                    != marker_fingerprint
                ):
                    raise OSError("recovery entry changed")
                wavs: list[Path] = []
                for index, item in enumerate(candidate.iterdir()):
                    if index >= 512:
                        raise OSError("bounded inventory exceeded")
                    if item.suffix.lower() == ".wav":
                        self._recovery_path_fingerprint(item, directory=False)
                        wavs.append(item)
                server_tracks = sum(not is_local_stem_name(item.name) for item in wavs)
                local_stems = len(wavs) - server_tracks
                if not wavs:
                    continue

                from webjam_qt import __version__

                staging_identity = recording_staging_identity(candidate)
                if staging_identity != staged_identity:
                    raise OSError("recovery identity changed")
                journal_result = (
                    journal.load(staging_identity.take_id)
                    if staging_identity is not None
                    else None
                )
                recovery_note = (
                    "Recording media publication resumed after an interrupted "
                    "session; live musician identity could not be reauthenticated."
                )
                if journal_result is not None and journal_result.trusted:
                    evidence = journal_result.evidence
                else:
                    evidence = SessionEvidence()
                    if journal_result is not None:
                        recovery_note = (
                            "Recording media was recovered, but its private "
                            "session checkpoint could not be safely read."
                        )
                evidence = replace(
                    evidence,
                    recovery_status=RecoveryStatus.NEEDS_ATTENTION,
                    recovery_notes=tuple(
                        dict.fromkeys((*evidence.recovery_notes, recovery_note))
                    ),
                    timeline=tuple(
                        dict.fromkeys(
                            (
                                *evidence.timeline,
                                SessionTimelineEvent(
                                    "recording_media_publication_recovered",
                                    detail=recovery_note,
                                ),
                            )
                        ).keys()
                    ),
                )
                result = write_take_manifest(
                    candidate,
                    expected_tracks=server_tracks,
                    required_local_stems=local_stems,
                    local_started_utc=evidence.started_utc,
                    capture_errors=("Interrupted recording evidence recovered.",),
                    recording_receipts=(),
                    app_version=__version__,
                    session_id=(
                        staging_identity.session_id
                        if staging_identity is not None
                        else ""
                    ),
                    take_id=(
                        staging_identity.take_id if staging_identity is not None else ""
                    ),
                    local_participant_id=evidence.host.participant_id,
                    local_participant_name=(
                        evidence.host.display_name or "Recovered host"
                    ),
                    session_evidence=evidence,
                )
                durable = bool(
                    result.manifest_path is not None
                    and result.take is not None
                    and not marker.exists()
                    and (
                        staging_identity is None
                        or result.take.take_id == staging_identity.take_id
                    )
                )
                if durable:
                    recovered += 1
                    if (
                        staging_identity is not None
                        and journal_result is not None
                        and journal_result.trusted
                    ):
                        try:
                            journal.remove(staging_identity.take_id)
                        except (
                            OSError,
                            RecordingManifestJournalError,
                            ValueError,
                        ):
                            LOGGER.warning(
                                "Could not retire linked recording evidence after "
                                "media recovery."
                            )
            except Exception:  # noqa: BLE001 - path-free recovery boundary
                LOGGER.warning(
                    "An interrupted take could not be reconciled automatically."
                )

        self._c._ui_invoker.invoke(
            lambda: self._finish_staged_server_take_recovery(
                len(candidates), recovered, root_text
            )
        )

    def _finish_staged_server_take_recovery(
        self, candidate_count: int, recovered: int, root_text: str
    ) -> None:
        """Report worker results and continue ordered startup recovery."""

        noun = "recording" if candidate_count == 1 else "recordings"
        if recovered:
            message = (
                f"WebJam recovered interrupted {noun} without guessing musician "
                "identity. Open Studio and review the preserved audio."
            )
        else:
            message = (
                f"WebJam found interrupted {noun} that still need review. "
                "The source audio was left unchanged."
            )
        self._c.window.flash_message(message, ms=10000)
        try:
            self._c.window.recording_studio.set_takes_directory(root_text)
            self._c.window.recording_studio.reload()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Could not refresh Studio interrupted-take inventory")
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
        self._begin_shared_track_recording_window(self._take_id)
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
        active_take_id = self._take_id
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
        underrun_delta = self._finish_shared_track_recording_window(active_take_id)
        if underrun_delta:
            with self._evidence_lock:
                self._set_recovery_locked(
                    RecoveryStatus.NEEDS_ATTENTION,
                    "Shared Track playback had an underrun during the confirmed "
                    "recording window.",
                )
                self._append_evidence_event_locked(
                    "shared_track_dropout",
                    detail=(
                        "Shared Track playback reported "
                        f"{underrun_delta} take-local underrun frame(s)."
                    ),
                    occurred_utc=stopped_utc,
                )
        if unexpected:
            self._record_diagnostic_failure("unexpected_stop")
        self._checkpoint_evidence_journal()
        return stopped_utc, True

    def _signal_peer_recording_finalizing(
        self,
        take_id: str,
        *,
        stopped_utc: str,
        message: str = "",
    ) -> None:
        """Publish recorder stop without claiming that the take is ready yet."""

        if not take_id or not self._c.host_peer.active:
            return
        try:
            snapshot = self._c.host_peer.begin_take_finalization(
                take_id,
                stopped_utc=stopped_utc,
                message=" ".join(str(message).split())[:240],
            )
            if snapshot is None:
                raise RuntimeError("peer finalization service is unavailable")
            return
        except Exception:  # noqa: BLE001 - peer failures may contain private detail
            pass
        with self._evidence_lock:
            arm_generation = (
                self._guest_capture_arm_generation
                if self._guest_capture_arm_take_id == take_id
                else 0
            )
        fallback = getattr(
            self._c.host_peer,
            "begin_armed_take_finalization",
            None,
        )
        if arm_generation > 0 and callable(fallback):
            try:
                snapshot = fallback(
                    take_id,
                    arm_generation=arm_generation,
                    stopped_utc=stopped_utc,
                    message=" ".join(str(message).split())[:240],
                )
                if snapshot is not None:
                    return
            except Exception:  # noqa: BLE001 - peer facts stay private
                pass
        LOGGER.error("Could not publish recording finalization state")

    def _signal_peer_validation_outcome(
        self,
        take_id: str,
        *,
        needs_attention: bool,
        message: str,
    ) -> None:
        """Publish one terminal guest state only after host finalization truth."""

        if not take_id:
            return
        with self._evidence_lock:
            stopped_utc = self._recording_ended_utc
        self._c.signal_peer_recording_stopped(
            take_id,
            stopped_utc=stopped_utc,
            needs_attention=bool(needs_attention),
            message=" ".join(str(message).split())[:240],
        )

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
            Path(root).expanduser()
            if root
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
                if result.errors:
                    LOGGER.warning(
                        "Isolated host recovery reported %d capture issue%s.",
                        len(result.errors),
                        "" if len(result.errors) == 1 else "s",
                    )
                return None, result.errors
            published = self._publish_local_result_recovery(
                result,
                str(base),
                actual,
            )
            if published is not None:
                actual = published
            LOGGER.info("Isolated host stems were preserved for review.")
            if result.errors:
                LOGGER.warning(
                    "Isolated host recovery reported %d capture issue%s.",
                    len(result.errors),
                    "" if len(result.errors) == 1 else "s",
                )
            return actual, result.errors
        except Exception:  # noqa: BLE001 - capture errors can contain private paths
            LOGGER.error("Could not salvage isolated host stems")
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
        return (
            snap.recording
            or snap.armed
            or self.phase
            in (
                RecorderPhase.STARTING,
                RecorderPhase.COUNT_IN,
                RecorderPhase.RECORDING,
                RecorderPhase.STOP_FAILED,
            )
        )

    @property
    def take_in_progress(self) -> bool:
        """True until a requested take has either finished validation or failed."""
        return self.is_recording_active or self.phase in (
            RecorderPhase.STOPPING,
            RecorderPhase.FINALIZING,
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
        profile = get_creator_profile_by_key_or_default(
            getattr(
                self,
                "_recording_creator_profile_key",
                getattr(self._c.settings, "last_creator_profile_key", "music"),
            )
        )
        hosting = self._hosting_server()
        if profile.key == "podcast_voice":
            if hosting:
                body = (
                    "A recording is still running, and this Mac is hosting the "
                    "recording session.\n\n"
                    "Quitting stops the recording AND ends the recording session "
                    "for every connected speaker. WebJam will stop the recording "
                    "cleanly and preserve any completed source files before it "
                    "quits.\n\nQuit WebJam?"
                )
            else:
                body = (
                    "A recording is still running.\n\n"
                    "Quitting disconnects this computer, but the recording session "
                    "continues until someone presses ■ Stop Rec. WebJam will "
                    "preserve any local source files it captured in a Recovered "
                    "folder before it quits.\n\nQuit WebJam?"
                )
        elif profile.key == "review_rehearsal":
            if hosting:
                body = (
                    "A recording is still running in this Preview review session, "
                    "and this Mac is hosting it.\n\n"
                    "Quitting stops the recording AND ends the Preview review "
                    "session for every connected participant. WebJam will stop the "
                    "recording cleanly and preserve any completed source files "
                    "before it quits.\n\nQuit WebJam?"
                )
            else:
                body = (
                    "A recording is still running in this Preview review session.\n\n"
                    "Quitting disconnects this computer, but the review session "
                    "continues until someone presses ■ Stop Rec. WebJam will "
                    "preserve any local source files it captured in a Recovered "
                    "folder before it quits.\n\nQuit WebJam?"
                )
        elif hosting:
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
        """Stop the recorder, then report only durable take finalization.

        Quitting a hosting Mac takes the server down with it; stopping the
        recording first lets the server finalize every musician's track
        instead of truncating the take mid-write. A confirmed stop queues the
        ordinary asynchronous take validator and returns ``False`` until that
        owner retires the take, keeping the server alive in the meantime. The
        synchronous portion is bounded by the RPC client's short timeouts.
        """
        active_take_id = str(self._take_id or "")
        if active_take_id and active_take_id == self._validation_take_id:
            return False
        if (
            active_take_id
            and active_take_id == self._shutdown_validation_dispatch_take_id
        ):
            return False
        if (
            active_take_id
            and active_take_id == self._shutdown_validation_pending_take_id
        ):
            self._request_shutdown_take_validation(active_take_id)
            return False
        if not (self._c._server_recording or self._c._recorder_armed):
            return True
        if not self._shutdown_stop_lock.acquire(blocking=False):
            return False
        try:
            # Another teardown request may have completed while this caller
            # was approaching the ownership boundary.
            active_take_id = str(self._take_id or "")
            if active_take_id and active_take_id == self._validation_take_id:
                return False
            if (
                active_take_id
                and active_take_id == self._shutdown_validation_dispatch_take_id
            ):
                return False
            if (
                active_take_id
                and active_take_id == self._shutdown_validation_pending_take_id
            ):
                self._request_shutdown_take_validation(active_take_id)
                return False
            if not (self._c._server_recording or self._c._recorder_armed):
                return True
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

            rpc_binding = self._recording_rpc_binding_for_take(active_take_id)
            if rpc_binding is not None:
                rpc_port, secret_file, secret_identity = rpc_binding
                secret = _read_exact_secret_file(secret_file, secret_identity)
            else:
                # Compatibility seam for legacy recordings started before a
                # take-scoped configuration could be captured.
                secret_file = (self._c.settings.server_rpc_secret_file or "").strip()
                if not secret_file:
                    LOGGER.error(
                        "Hosted recording is active but no recorder secret is configured"
                    )
                    return False
                secret = read_secret_file(secret_file)
                rpc_port = int(self._c.settings.server_rpc_port)
            rpc = JamulusServerRpc(port=rpc_port, secret=secret)
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
                        active_take_id = str(self._take_id or "")
                        if active_take_id:
                            stopped_utc, newly_confirmed = (
                                self._confirmed_recording_stopped(
                                    unexpected=False,
                                    detail=(
                                        "WebJam stopped the recorder while the "
                                        "host was shutting down."
                                    ),
                                )
                            )
                            if newly_confirmed:
                                self._signal_peer_recording_finalizing(
                                    active_take_id,
                                    stopped_utc=stopped_utc,
                                    message="The host is finalizing the recorded take.",
                                )
                            self._shutdown_validation_pending_take_id = active_take_id
                            self._request_shutdown_take_validation(active_take_id)
                        LOGGER.info("Hosted-server recording stopped and confirmed")
                        # With an owned take, recorder stop is only the first
                        # half of finalization. A retry may tear down services
                        # after the normal validation path retires ownership.
                        return not bool(active_take_id)
                    time.sleep(0.1)
            LOGGER.error("Hosted recorder stayed enabled after stop request")
            return False
        except Exception:  # noqa: BLE001 - RPC errors can contain paths/endpoints
            LOGGER.error("Could not stop hosted recording on shutdown")
            return False
        finally:
            self._shutdown_stop_lock.release()

    def _request_shutdown_take_validation(self, take_id: str) -> None:
        """Queue one retryable UI-thread validation lease for a stopped take."""

        if (
            not take_id
            or take_id != self._take_id
            or self._validation_take_id == take_id
            or self._shutdown_validation_dispatch_take_id == take_id
        ):
            return
        self._shutdown_validation_dispatch_take_id = take_id
        try:
            self._c._ui_invoker.invoke(
                lambda expected_take_id=take_id: (
                    self._finish_shutdown_recorder_stop_on_ui(expected_take_id)
                )
            )
        except Exception:  # noqa: BLE001 - UI dispatch detail may be private
            if self._shutdown_validation_dispatch_take_id == take_id:
                self._shutdown_validation_dispatch_take_id = ""
            LOGGER.error("Could not queue hosted take validation")

    def _finish_shutdown_recorder_stop_on_ui(self, take_id: str) -> None:
        """Hand a confirmed shutdown stop to the normal UI validation path."""

        if not take_id or take_id != self._take_id:
            if self._shutdown_validation_dispatch_take_id == take_id:
                self._shutdown_validation_dispatch_take_id = ""
            if self._shutdown_validation_pending_take_id == take_id:
                self._shutdown_validation_pending_take_id = ""
            return
        self._c.window.set_status_recording(False)
        try:
            if self._validation_take_id == take_id:
                self._set_phase(RecorderPhase.FINALIZING)
                return
            self._start_take_validation_once()
        finally:
            if self._shutdown_validation_dispatch_take_id == take_id:
                self._shutdown_validation_dispatch_take_id = ""

    def on_audio_session_stopped(self) -> None:
        """Stop Audio ends this Mac's part in any in-flight recording.

        The band server keeps recording — Stop Audio never stops the server,
        even when this Mac hosts it (the Stop Audio dialog says so); this
        preserves the local isolated tracks and resets the recording UI so no
        stale REC clock or take chip survives the disconnect.
        """
        if self.phase is RecorderPhase.FINALIZING:
            # The validation worker owns the capture and will finish the take.
            return
        prior_phase = self.phase
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
        if prior_phase in {
            RecorderPhase.IDLE,
            RecorderPhase.COMPLETE,
            RecorderPhase.NEEDS_ATTENTION,
            RecorderPhase.ERROR,
        }:
            # No in-flight capture was interrupted. Retire any old Ready or
            # attention chip instead of letting class-reused/queued UI state
            # present it as belonging to the next jam.
            self._c.window.session_strip.clear_recording_session_status()
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

    def _request_hosted_readiness_refresh(
        self,
        context: _HostedRecordingReadinessContext | None = None,
    ) -> None:
        proof = (
            context.ordered_roster_proof
            if context is not None
            else getattr(self._c, "_primary_ordered_roster_proof", None)
        )
        identity = (
            proof.identity
            if isinstance(proof, JamulusOrderedRosterProof)
            else getattr(
                self._c,
                "_primary_ordered_roster_refresh_identity",
                None,
            )
        )
        if identity is None:
            return
        try:
            self._c.jamulus.request_ordered_roster_refresh(identity)
        except Exception:  # noqa: BLE001 - readiness remains fail closed
            return

    def _fail_hosted_recording_readiness(
        self,
        context: _HostedRecordingReadinessContext | None,
    ) -> None:
        """Leave recording untouched and explain the exact-correlation gate."""

        self._request_hosted_readiness_refresh(context)
        self._set_phase(RecorderPhase.ERROR)
        self._c._show_actionable_error(
            "Recording Roster Is Still Syncing",
            what_failed=(
                "WebJam cannot yet prove a current recording identity for "
                "every connected musician. No recording was started."
            ),
            likely_cause=(
                "Someone may still be joining or reconnecting, may have joined "
                "Jamulus outside WebJam, or two musicians may have identical "
                "full Jamulus profiles."
            ),
            next_action=(
                "Ask every musician to join through this WebJam session. Wait "
                "for every musician card to settle, then retry. If two people "
                "share the same name, instrument, city, and skill level, change "
                "at least one of those profile fields so the full profiles are "
                "unique."
            ),
            retry_callback=self._c._on_record_requested,
        )

    def _run_hosted_recording_readiness(
        self,
        generation: int,
        context: _HostedRecordingReadinessContext,
        secret_file: str,
    ) -> None:
        """Perform the read-only authenticated server half off the UI thread."""

        readiness = None
        try:
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

            if (
                secret_file != context.server_rpc_secret_file
                or _private_secret_file_identity(secret_file)
                != context.server_rpc_secret_identity
            ):
                raise RuntimeError("recorder configuration changed")
            reference_before = self._reference_recording_claim()
            secret = read_secret_file(secret_file)
            with JamulusServerRpc(
                port=context.server_rpc_port,
                secret=secret,
            ) as rpc:
                payload = rpc.get_clients()
            if (
                _private_secret_file_identity(secret_file)
                != context.server_rpc_secret_identity
            ):
                raise RuntimeError("recorder configuration changed")
            reference_after = self._reference_recording_claim()
            readiness = self._evaluate_hosted_recording_readiness(
                payload,
                context,
                reference_before=reference_before,
                reference_after=reference_after,
            )
        except Exception:  # noqa: BLE001 - private details stay out of UI/logs
            LOGGER.debug("Hosted recording readiness was unavailable")
        self._c._ui_invoker.invoke(
            lambda: self._apply_hosted_recording_readiness(
                generation,
                context,
                readiness,
                secret_file,
            )
        )

    def _apply_hosted_recording_readiness(
        self,
        generation: int,
        context: _HostedRecordingReadinessContext,
        readiness: _HostedRecordingReadiness | None,
        secret_file: str,
    ) -> None:
        """Recheck the read-only result before allocating recording resources."""

        if (
            generation != self._hosted_preflight_generation
            or self.phase is not RecorderPhase.PREFLIGHT
        ):
            return
        participants = [
            participant
            for participant in self._c.participants.values()
            if not str(getattr(participant, "role", "")).startswith("Preview")
        ]
        current = self._hosted_recording_readiness_context(participants)
        if (
            readiness is None
            or current is None
            or current.host_peer_identity != context.host_peer_identity
            or current.ordered_roster_proof.authority_key
            != context.ordered_roster_proof.authority_key
            or current.presence_authority != context.presence_authority
            or current.participant_cards != context.participant_cards
            or current.server_rpc_port != context.server_rpc_port
            or current.server_rpc_secret_file != context.server_rpc_secret_file
            or current.server_rpc_secret_identity != context.server_rpc_secret_identity
        ):
            self._fail_hosted_recording_readiness(context)
            return
        self._begin_recording_start(
            participants,
            context.server_rpc_secret_file,
            hosted_readiness=readiness,
        )

    def _bind_session_recording_plan(
        self,
        storage: object,
        *,
        planned_shared_track: bool,
        server_channel_counts: tuple[int, ...],
        guest_local_originals: tuple[GuestLocalOriginalBinding, ...] = (),
    ) -> bool:
        """Bind the immutable take plan before any recorder starts.

        A recording that cannot state its exact participant, local-input,
        storage, and Shared Track obligations is not safe to begin. The
        private plan is subsequently checkpointed with every journal update
        and checked again at finalization.
        """

        try:
            roster: list[tuple[str, str]] = []
            seen: set[str] = set()
            for channel_id in sorted(self._participant_ids):
                durable = str(self._participant_ids.get(channel_id, "") or "")
                if not durable or durable in seen:
                    continue
                seen.add(durable)
                name = str(self._track_names.get(channel_id, "") or "Musician")
                roster.append((durable, name))
            if isinstance(storage, RecordingStorageCheck):
                bound_storage = storage
            else:
                # Some callers/tests provide a duck-typed check; coerce the
                # facts without weakening the action-needed rule.
                status = str(getattr(storage, "status", "") or "")
                bound_storage = RecordingStorageCheck(
                    status=(
                        RecordingStorageStatus.WARNING
                        if status == RecordingStorageStatus.WARNING.value
                        else RecordingStorageStatus.READY
                    ),
                    detail=str(getattr(storage, "detail", "") or "coerced"),
                    free_bytes=None,
                    required_bytes=max(
                        0, int(getattr(storage, "required_bytes", 0) or 0)
                    ),
                )
            resolved_capture_tracks = resolve_capture_tracks(self._c.settings)
            configured_maps = (
                configured_input_map_bindings(self._c.settings)
                if bool(getattr(self._c.settings, "local_capture_enabled", False))
                else ()
            )
            if configured_maps:
                input_maps = configured_maps
            elif resolved_capture_tracks:
                # Preserve the explicit historical two-input default as
                # immutable plan facts. A malformed or opted-out configured
                # map resolves to no tracks and never reaches this branch.
                input_maps = tuple(
                    InputMapBinding(
                        track_name=track.stem,
                        channel_count=track.channel_count,
                        local_original_enabled=True,
                    )
                    for track in resolved_capture_tracks
                )
            else:
                input_maps = ()

            expected_server_stems = list(
                dict.fromkeys(
                    durable
                    for _channel, durable in sorted(self._participant_ids.items())
                    if durable
                )
            )
            shared_binding = None
            count_in_frames = 0
            pre_roll_frames = 0
            if planned_shared_track:
                reference_controller = getattr(self._c, "_reference_track", None)
                snapshot = getattr(reference_controller, "snapshot", None)
                fingerprint_reader = getattr(
                    reference_controller,
                    "recording_source_fingerprint",
                    None,
                )
                if snapshot is None or not callable(fingerprint_reader):
                    raise ValueError("Shared Track planning evidence is unavailable")
                fingerprint = str(fingerprint_reader() or "")
                state = str(
                    getattr(getattr(snapshot, "state", None), "value", "") or ""
                ).lower()
                current_generation = int(getattr(snapshot, "playback_generation", 0))
                playback_generation = (
                    current_generation + 1
                    if state in {"ready", "paused"}
                    else current_generation
                )
                shared_binding = SharedTrackBinding(
                    source_fingerprint_sha256=fingerprint,
                    playback_generation=playback_generation,
                )
                if self._reference_participant_id not in expected_server_stems:
                    expected_server_stems.append(self._reference_participant_id)
                if state in {"ready", "paused"}:
                    beats = int(getattr(snapshot, "count_in_beats", 0) or 0)
                    bpm = float(getattr(snapshot, "count_in_bpm", 120.0) or 120.0)
                    frames_per_beat = round(48_000 * 60.0 / bpm)
                    count_in_frames = frames_per_beat * beats
                    pre_roll_frames = count_in_frames

            if not expected_server_stems:
                raise ValueError("No exact server source was available to plan")
            plan = SessionRecordingPlan(
                session_id=str(self._session_id or "") or "unbound-session",
                take_id=self._take_id,
                plan_generation=max(1, int(self._recording_generation)),
                roster=tuple(roster),
                expected_server_stems=tuple(expected_server_stems),
                count_in_frames=count_in_frames,
                pre_roll_frames=pre_roll_frames,
                storage=bound_storage,
                expected_source_count=(
                    len(expected_server_stems)
                    + len(resolved_capture_tracks)
                    + sum(item.track_count for item in guest_local_originals)
                ),
                created_at_utc=(
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                ),
                shared_track=shared_binding,
                shared_track_planned=bool(planned_shared_track),
                input_maps=input_maps,
                creator_profile_key=self._recording_creator_profile_key,
                guest_local_originals=guest_local_originals,
                server_channel_counts=server_channel_counts,
            )
            if not plan.server_topology_exact or any(
                not item.exact_topology for item in plan.guest_local_originals
            ):
                raise ValueError("The recording source topology is not exact.")
        except Exception:  # noqa: BLE001 - private plan details stay redacted
            LOGGER.error("Session recording plan could not be bound safely.")
            with self._evidence_lock:
                self._recording_plan = None
                self._recording_plan_take_id = ""
                self._recording_plan_fingerprint = ""
            return False
        with self._evidence_lock:
            self._recording_plan = plan
            self._recording_plan_take_id = self._take_id
            self._recording_plan_fingerprint = plan.plan_fingerprint()
        return True

    @staticmethod
    def _readiness_topology(channel_count: int) -> RecordingChannelTopology:
        if channel_count == 1:
            return RecordingChannelTopology.MONO
        if channel_count == 2:
            return RecordingChannelTopology.STEREO
        raise ValueError("A readiness source must be mono or stereo.")

    @staticmethod
    def _bounded_meter_percent(value: object) -> int | None:
        try:
            level = float(value)
        except (TypeError, ValueError):
            return None
        if not 0.0 <= level <= 1.0:
            return None
        return max(0, min(100, round(level * 100.0)))

    def _build_recording_readiness_presentation(
        self,
        plan: SessionRecordingPlan,
        *,
        local_preflight: LocalCapturePreflight | None,
    ) -> RecordingReadinessPresentation:
        """Project one exact private plan into bounded, path-free UI facts."""

        roster_names = dict(plan.roster)
        participants_by_channel: dict[int, object] = {}
        for participant in self._c.participants.values():
            try:
                participants_by_channel[int(participant.channel_id)] = participant
            except (AttributeError, TypeError, ValueError):
                continue
        meter_by_participant: dict[str, int | None] = {}
        for channel_id, participant_id in self._participant_ids.items():
            participant = participants_by_channel.get(channel_id)
            meter_by_participant[participant_id] = self._bounded_meter_percent(
                getattr(participant, "audio_level", None)
            )

        sources: list[RecordingReadinessSource] = []
        for participant_id, channel_count, logical_source_id in zip(
            plan.expected_server_stems,
            plan.server_channel_counts,
            plan.server_logical_source_ids,
            strict=True,
        ):
            shared = participant_id == self._reference_participant_id
            sources.append(
                RecordingReadinessSource(
                    source_id=logical_source_id,
                    participant_label=(
                        "Shared Track"
                        if shared
                        else roster_names.get(participant_id, "Participant")
                    ),
                    source_label=("Shared Track" if shared else "WebJam server track"),
                    kind=(
                        RecordingSourceKind.SHARED_TRACK
                        if shared
                        else RecordingSourceKind.SERVER
                    ),
                    topology=self._readiness_topology(channel_count),
                    required=True,
                    readiness=RecordingSourceReadiness.READY,
                    detail=(
                        "Exact source, route, and playback generation are bound."
                        if shared
                        else "Authenticated participant identity and recorder width are bound."
                    ),
                    meter_percent=meter_by_participant.get(participant_id),
                )
            )

        local_tracks = plan.resolved_capture_tracks()
        active_local_by_id = {track.logical_source_id: track for track in local_tracks}
        local_ready = local_preflight is None or local_preflight.ready
        local_error_detail = (
            "Selected input device and exact 48 kHz mono/stereo map are ready."
            if local_ready
            else (
                "Check the selected input device, mapped channels, and 48 kHz "
                "format before recording."
            )
        )
        for binding, logical_source_id in zip(
            plan.input_maps,
            plan.input_map_logical_source_ids,
            strict=True,
        ):
            required = bool(binding.enabled and binding.local_original_enabled)
            sources.append(
                RecordingReadinessSource(
                    source_id=logical_source_id,
                    participant_label=(
                        str(self._c.settings.musician_name or "This Mac")
                    ),
                    source_label=binding.track_name,
                    kind=RecordingSourceKind.LOCAL_ORIGINAL,
                    topology=self._readiness_topology(binding.channel_count),
                    required=required,
                    readiness=(
                        RecordingSourceReadiness.READY
                        if (not required or logical_source_id in active_local_by_id)
                        and (not required or local_ready)
                        else RecordingSourceReadiness.ACTION_NEEDED
                    ),
                    detail=(
                        local_error_detail
                        if required
                        else "Optional input is not armed for this take."
                    ),
                    meter_percent=None,
                )
            )

        for guest in plan.guest_local_originals:
            guest_label = roster_names.get(guest.participant_id, "Guest")
            for ordinal, (channel_count, logical_source_id) in enumerate(
                zip(
                    guest.channel_counts,
                    guest.logical_source_ids,
                    strict=True,
                )
            ):
                sources.append(
                    RecordingReadinessSource(
                        source_id=logical_source_id,
                        participant_label=guest_label,
                        source_label=f"Local Original {ordinal + 1}",
                        kind=RecordingSourceKind.LOCAL_ORIGINAL,
                        topology=self._readiness_topology(channel_count),
                        required=True,
                        readiness=RecordingSourceReadiness.READY,
                        detail=(
                            "Authenticated guest inventory and exact logical "
                            "track topology are bound."
                        ),
                        meter_percent=None,
                    )
                )

        storage_readiness = {
            RecordingStorageStatus.READY: RecordingStorageReadiness.READY,
            RecordingStorageStatus.WARNING: RecordingStorageReadiness.WARNING,
            RecordingStorageStatus.ACTION_NEEDED: (
                RecordingStorageReadiness.ACTION_NEEDED
            ),
        }[plan.storage.status]
        storage = RecordingStoragePresentation(
            readiness=storage_readiness,
            summary=(
                "Storage checked"
                if plan.storage.status is RecordingStorageStatus.READY
                else "Storage is low"
                if plan.storage.status is RecordingStorageStatus.WARNING
                else "Storage needs attention"
            ),
            detail=plan.storage.detail,
        )
        if plan.shared_track_planned:
            shared_track = SharedTrackPresentation(
                readiness=SharedTrackReadiness.READY,
                required=True,
                summary="Included in this take",
                detail="Exact content checksum and playback generation are bound.",
            )
        else:
            shared_track = SharedTrackPresentation(
                readiness=SharedTrackReadiness.NOT_INCLUDED,
                required=False,
                summary="Not included",
                detail="This take will not include a Shared Track.",
            )
        profile = get_creator_profile_by_key_or_default(plan.creator_profile_key)
        return RecordingReadinessPresentation(
            profile_label=profile.label,
            sources=tuple(sources),
            storage=storage,
            shared_track=shared_track,
            recovery=(RecordingReadinessRecovery.OPEN_RECORDING_SETUP if not local_ready
                      else RecordingReadinessRecovery.NONE),
        )

    def _resolve_readiness_decision(self, plan, presentation, decision) -> bool:
        # Nested Qt events can replace the plan while the sheet is open.
        # A stale cancellation is no more authoritative than a stale Start.
        with self._evidence_lock:
            if (self.phase is not RecorderPhase.PREFLIGHT
                    or self._take_id != plan.take_id
                    or self._recording_plan_take_id != plan.take_id
                    or self._recording_plan is not plan
                    or self._recording_plan_fingerprint != plan.plan_fingerprint()):
                return False
        if decision is True and presentation is not None and presentation.can_start:
            return True
        setup = (isinstance(presentation, RecordingReadinessPresentation)
                 and not presentation.can_start
                 and presentation.recovery is RecordingReadinessRecovery.OPEN_RECORDING_SETUP
                 and decision is RecordingReadinessRecovery.OPEN_RECORDING_SETUP)
        canceled_take_id = self._take_id
        self._retire_active_take(canceled_take_id)
        self._set_phase(RecorderPhase.IDLE)
        if setup:
            self._c.window.flash_message(
                "Recording has not started. Fix the selected inputs, then choose Record Session again.",
                ms=5000,
            )
            self._c._open_recording_setup()
        else:
            self._c.window.flash_message(
                "Recording was not started. Review the source readiness items, then try again.",
                ms=5000,
            )
        return False

    def _readiness_authority_still_matches(
        self,
        plan: SessionRecordingPlan,
        *,
        hosted_readiness: _HostedRecordingReadiness,
        planned_shared_track: bool,
    ) -> bool:
        """Revalidate mutable authority after the user accepts the snapshot."""

        with self._evidence_lock:
            if (
                self.phase is not RecorderPhase.PREFLIGHT
                or self._take_id != plan.take_id
                or self._recording_plan_take_id != plan.take_id
                or self._recording_plan is not plan
                or self._recording_plan_fingerprint != plan.plan_fingerprint()
            ):
                return False
        current_context = self._hosted_recording_readiness_context(
            [
                participant
                for participant in self._c.participants.values()
                if not str(getattr(participant, "role", "")).startswith("Preview")
            ]
        )
        expected_context = hosted_readiness.context
        if (
            current_context is None
            or current_context.host_peer_identity != expected_context.host_peer_identity
            or current_context.ordered_roster_proof.authority_key
            != expected_context.ordered_roster_proof.authority_key
            or current_context.presence_authority != expected_context.presence_authority
            or current_context.participant_cards != expected_context.participant_cards
            or current_context.server_rpc_port != expected_context.server_rpc_port
            or current_context.server_rpc_secret_file
            != expected_context.server_rpc_secret_file
            or current_context.server_rpc_secret_identity
            != expected_context.server_rpc_secret_identity
        ):
            return False
        if not self._revalidate_hosted_recording_readiness(hosted_readiness):
            return False
        current_tracks = resolve_capture_tracks(self._c.settings)
        configured_maps = (
            configured_input_map_bindings(self._c.settings)
            if bool(getattr(self._c.settings, "local_capture_enabled", False))
            else ()
        )
        if configured_maps:
            current_input_maps = configured_maps
        elif current_tracks:
            current_input_maps = tuple(
                InputMapBinding(
                    track_name=track.stem,
                    channel_count=track.channel_count,
                    local_original_enabled=True,
                )
                for track in current_tracks
            )
        else:
            current_input_maps = ()
        planned_tracks = plan.resolved_capture_tracks()
        if current_input_maps != plan.input_maps:
            return False
        if planned_tracks:
            current_local = check_local_capture_preflight(
                tracks=planned_tracks,
                device=self._c.settings.audio_input_device_index,
                samplerate=self._c.settings.audio_samplerate,
                blocksize=self._c.settings.audio_blocksize,
            )
            if not current_local.ready:
                return False
        current_storage = check_recording_storage(
            self._c.settings.takes_directory,
            expected_server_tracks=len(plan.expected_server_stems),
            local_originals_enabled=bool(
                planned_tracks
                or any(item.track_count for item in plan.guest_local_originals)
            ),
            local_original_tracks=(
                sum(track.channel_count for track in planned_tracks)
                + sum(sum(item.channel_counts) for item in plan.guest_local_originals)
            ),
        )
        if (
            not current_storage.can_start
            or current_storage.required_bytes != plan.storage.required_bytes
        ):
            return False
        frozen_reader = getattr(
            getattr(self._c, "host_peer", None),
            "local_original_obligations_for_take",
            None,
        )
        try:
            frozen = tuple(frozen_reader(plan.take_id))
            frozen_key = tuple(
                (
                    item.participant_id,
                    item.track_count,
                    item.map_fingerprint,
                    item.presence_generation,
                    tuple(item.channel_counts),
                    tuple(item.logical_source_ids),
                )
                for item in frozen
            )
            plan_key = tuple(
                (
                    item.participant_id,
                    item.track_count,
                    item.map_fingerprint_sha256,
                    item.presence_generation,
                    item.channel_counts,
                    item.logical_source_ids,
                )
                for item in plan.guest_local_originals
            )
        except Exception:  # noqa: BLE001 - private guest facts stay redacted
            return False
        if frozen_key != plan_key:
            return False
        reference_controller = getattr(self._c, "_reference_track", None)
        snapshot = getattr(reference_controller, "snapshot", None)
        if bool(planned_shared_track) != bool(plan.shared_track_planned):
            return False
        if plan.shared_track_planned:
            fingerprint_reader = getattr(
                reference_controller,
                "recording_source_fingerprint",
                None,
            )
            if snapshot is None or not callable(fingerprint_reader):
                return False
            state = str(
                getattr(getattr(snapshot, "state", None), "value", "") or ""
            ).lower()
            current_generation = int(getattr(snapshot, "playback_generation", 0) or 0)
            expected_generation = (
                current_generation + 1
                if state in {"ready", "paused"}
                else current_generation
            )
            if (
                not getattr(snapshot, "loaded", False)
                or state not in {"ready", "paused", "routing", "playing"}
                or (
                    state in {"ready", "paused"}
                    and not bool(getattr(snapshot, "can_play", False))
                )
                or plan.shared_track is None
                or str(fingerprint_reader() or "")
                != plan.shared_track.source_fingerprint_sha256
                or expected_generation != plan.shared_track.playback_generation
            ):
                return False
        return True

    def _revalidate_hosted_recording_readiness(
        self,
        expected: _HostedRecordingReadiness,
    ) -> bool:
        """Repeat the authenticated server observation after user consent.

        Participant cards and Presence-v2 leases can remain momentarily stale
        while Jamulus has already added, removed, or reconfigured a recorder
        row. The first observation is therefore insufficient after a modal
        readiness sheet has been open. Re-read the exact captured RPC secret,
        query the same server, and require byte-for-byte equivalent correlated
        facts before either recorder is armed.
        """

        context = expected.context
        try:
            from core.jamulus_server_rpc import JamulusServerRpc

            reference_before = self._reference_recording_claim()
            secret = _read_exact_secret_file(
                context.server_rpc_secret_file,
                context.server_rpc_secret_identity,
            )
            with JamulusServerRpc(
                port=context.server_rpc_port,
                secret=secret,
            ) as rpc:
                payload = rpc.get_clients()
            reference_after = self._reference_recording_claim()
            refreshed = self._evaluate_hosted_recording_readiness(
                payload,
                context,
                reference_before=reference_before,
                reference_after=reference_after,
            )
        except Exception:  # noqa: BLE001 - private server facts stay redacted
            LOGGER.debug("Final recording readiness recheck was unavailable")
            return False
        return refreshed == expected

    def _prepare_guest_local_original_bindings(
        self,
        take_id: str,
    ) -> tuple[tuple[GuestLocalOriginalBinding, ...], tuple[str, ...]]:
        """Freeze authenticated guest inventories before any recorder starts."""

        host_peer = getattr(self._c, "host_peer", None)
        if not bool(getattr(host_peer, "active", False)):
            return (), ()
        prepare = getattr(host_peer, "prepare_local_original_obligations", None)
        if not callable(prepare):
            return (), (
                "The host could not prepare exact guest Local Original inventories.",
            )
        try:
            obligations, issues = prepare(take_id)
            if issues:
                return (), tuple(str(item) for item in issues if str(item))
            bindings = tuple(
                GuestLocalOriginalBinding(
                    participant_id=obligation.participant_id,
                    track_count=obligation.track_count,
                    map_fingerprint_sha256=obligation.map_fingerprint,
                    presence_generation=obligation.presence_generation,
                    channel_counts=tuple(obligation.channel_counts),
                    logical_source_ids=tuple(obligation.logical_source_ids),
                )
                for obligation in obligations
            )
            if any(not item.exact_topology for item in bindings):
                return (), (
                    "A guest did not publish an exact Local Original topology.",
                )
        except Exception:  # noqa: BLE001 - guest facts stay private
            return (), ("A guest Local Original inventory could not be bound safely.",)
        return bindings, ()

    def _fail_guest_capture_arm(
        self,
        attempt: _GuestCaptureArmAttempt,
        *,
        readiness_changed: bool,
    ) -> None:
        """Cancel one uncommitted guest arm and fail before server recording."""

        cancel = getattr(attempt.host_peer, "cancel_capture_arm", None)
        if callable(cancel):
            try:
                cancel(
                    attempt.take_id,
                    arm_generation=attempt.arm_generation,
                )
            except Exception:  # noqa: BLE001 - arm facts stay private
                LOGGER.warning("Could not cancel a failed guest capture arm.")
        if attempt.take_id != self._take_id:
            return
        self._set_phase(RecorderPhase.ERROR)
        self._retire_active_take(attempt.take_id)
        if readiness_changed:
            self._c._show_actionable_error(
                "Recording Readiness Changed",
                what_failed=(
                    "A participant, input, storage, or Shared Track fact changed "
                    "while guest inputs were opening. No recorder was started."
                ),
                likely_cause=(
                    "A guest may have disconnected, changed an input map, or lost "
                    "its audio device after the readiness sheet was accepted."
                ),
                next_action=(
                    "Reconnect the affected input, wait for every participant and "
                    "source to become Ready, then try Record Session again."
                ),
                retry_callback=self._c._on_record_requested,
            )
            return
        self._c._show_actionable_error(
            "Guest Inputs Did Not Arm",
            what_failed=(
                "WebJam could not confirm that every required guest Local Original "
                "stream was open. No server recorder was started."
            ),
            likely_cause=(
                "A guest input device may have disconnected, become busy, or failed "
                "to open before the bounded readiness timeout."
            ),
            next_action=(
                "Ask each affected guest to reconnect their input and keep WebJam "
                "open, then retry Record Session."
            ),
            retry_callback=self._c._on_record_requested,
        )

    def _wait_for_guest_capture_arm(
        self,
        attempt: _GuestCaptureArmAttempt,
    ) -> None:
        """Wait off the UI thread, then return only bounded truth to the UI."""

        ready = False
        wait = getattr(
            attempt.host_peer,
            "wait_for_capture_arm_acknowledgements",
            None,
        )
        try:
            if callable(wait):
                ready = bool(
                    wait(
                        attempt.take_id,
                        arm_generation=attempt.arm_generation,
                        timeout_s=_GUEST_CAPTURE_ARM_TIMEOUT_S,
                    )
                )
        except Exception:  # noqa: BLE001 - guest/device details stay private
            LOGGER.debug("Guest capture arm acknowledgement wait failed")
        try:
            self._c._ui_invoker.invoke(
                lambda: self._apply_guest_capture_arm_result(attempt, ready)
            )
        except Exception:  # noqa: BLE001 - shutdown can remove the UI invoker
            cancel = getattr(attempt.host_peer, "cancel_capture_arm", None)
            if callable(cancel):
                try:
                    cancel(
                        attempt.take_id,
                        arm_generation=attempt.arm_generation,
                    )
                except Exception:  # noqa: BLE001 - best-effort shutdown cleanup
                    pass
            LOGGER.error("Could not queue the guest capture-arm result.")

    def _apply_guest_capture_arm_result(
        self,
        attempt: _GuestCaptureArmAttempt,
        ready: bool,
    ) -> None:
        """Commit an exact arm only after a second authority revalidation."""

        with self._evidence_lock:
            plan = (
                self._recording_plan
                if (
                    self.phase is RecorderPhase.PREFLIGHT
                    and self._take_id == attempt.take_id
                    and self._recording_plan_take_id == attempt.take_id
                    and self._recording_plan_fingerprint == attempt.plan_fingerprint
                )
                else None
            )
        if plan is None or plan.plan_fingerprint() != attempt.plan_fingerprint:
            cancel = getattr(attempt.host_peer, "cancel_capture_arm", None)
            if callable(cancel):
                try:
                    cancel(
                        attempt.take_id,
                        arm_generation=attempt.arm_generation,
                    )
                except Exception:  # noqa: BLE001 - stale callbacks stay harmless
                    pass
            return
        if ready:
            arm_ready = getattr(attempt.host_peer, "capture_arm_ready", None)
            try:
                ready = bool(
                    callable(arm_ready)
                    and arm_ready(
                        attempt.take_id,
                        arm_generation=attempt.arm_generation,
                    )
                )
            except Exception:  # noqa: BLE001 - guest/device details stay private
                ready = False
        if not ready:
            self._fail_guest_capture_arm(attempt, readiness_changed=False)
            return
        if not self._readiness_authority_still_matches(
            plan,
            hosted_readiness=attempt.hosted_readiness,
            planned_shared_track=plan.shared_track_planned,
        ):
            self._fail_guest_capture_arm(attempt, readiness_changed=True)
            return
        self._continue_recording_start(plan)

    def _arm_guest_capture_before_server_start(
        self,
        plan: SessionRecordingPlan,
        hosted_readiness: _HostedRecordingReadiness,
    ) -> bool:
        """Publish a take-scoped arm and wait for every opted-in guest ACK."""

        host_peer = getattr(self._c, "host_peer", None)
        publish = getattr(host_peer, "publish_capture_arm", None)
        wait = getattr(host_peer, "wait_for_capture_arm_acknowledgements", None)
        if not callable(publish) or not callable(wait):
            attempt = _GuestCaptureArmAttempt(
                plan.take_id,
                plan.plan_fingerprint(),
                0,
                hosted_readiness,
                host_peer,
            )
            self._fail_guest_capture_arm(attempt, readiness_changed=False)
            return False
        try:
            arm = publish(
                plan.take_id,
                recording_plan_fingerprint=plan.plan_fingerprint(),
            )
            arm_generation = int(arm.arm_generation)
            if arm_generation <= 0:
                raise ValueError("capture arm generation is unavailable")
            with self._evidence_lock:
                if (
                    self.phase is not RecorderPhase.PREFLIGHT
                    or self._take_id != plan.take_id
                    or self._recording_plan is not plan
                    or self._recording_plan_fingerprint != plan.plan_fingerprint()
                ):
                    raise ValueError("the recording plan changed during capture arm")
                self._guest_capture_arm_take_id = plan.take_id
                self._guest_capture_arm_generation = arm_generation
        except Exception:  # noqa: BLE001 - guest/device details stay private
            attempt = _GuestCaptureArmAttempt(
                plan.take_id,
                plan.plan_fingerprint(),
                0,
                hosted_readiness,
                host_peer,
            )
            self._fail_guest_capture_arm(attempt, readiness_changed=False)
            return False
        attempt = _GuestCaptureArmAttempt(
            plan.take_id,
            plan.plan_fingerprint(),
            arm_generation,
            hosted_readiness,
            host_peer,
        )
        self._c.window.flash_message(
            "Opening each required guest Local Original before the server recorder starts…",
            ms=9000,
        )
        threading.Thread(
            target=self._wait_for_guest_capture_arm,
            args=(attempt,),
            daemon=True,
            name="guest-capture-arm",
        ).start()
        return True

    def _continue_recording_start(self, plan: SessionRecordingPlan) -> None:
        """Open host recovery state, then start the take-bound server recorder."""

        with self._evidence_lock:
            if (
                self.phase is not RecorderPhase.PREFLIGHT
                or self._take_id != plan.take_id
                or self._recording_plan_take_id != plan.take_id
                or self._recording_plan is not plan
                or self._recording_plan_fingerprint != plan.plan_fingerprint()
            ):
                return
        rpc_binding = self._recording_rpc_binding_for_take(plan.take_id)
        if rpc_binding is None:
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(plan.take_id)
            self._c._show_actionable_error(
                "Recording Connection Needs Attention",
                what_failed=(
                    "The take-bound recorder connection changed before recording. "
                    "No recorder was started."
                ),
                likely_cause=(
                    "The host recorder configuration or private secret changed "
                    "during preflight."
                ),
                next_action=(
                    "Verify the host recording setup, then retry Record Session."
                ),
                retry_callback=self._c._on_record_requested,
            )
            return
        self.request_authenticated_roster_observation()
        if not self._start_local_capture():
            return
        if not self._create_evidence_journal():
            failed_take_id = self._take_id
            recovered, errors = self._salvage_capture()
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(failed_take_id)
            self._c._show_actionable_error(
                "Recording Recovery Setup Failed",
                what_failed=(
                    "WebJam couldn't prepare the private recovery record for this take."
                ),
                likely_cause=(
                    "The selected Takes folder is no longer writable or could "
                    "not safely store recording recovery evidence."
                ),
                next_action=(
                    "Choose a writable Takes folder in Recording Setup, then "
                    "try Record Session again. No server recording was started."
                ),
                retry_callback=self._c._on_record_requested,
            )
            if recovered is not None:
                self._notify_recovered(recovered, errors)
            return
        self._set_phase(RecorderPhase.STARTING)
        attempt = _ToggleAttempt(
            take_id=plan.take_id,
            target_armed=True,
            server_rpc_port=rpc_binding[0],
            server_rpc_secret_file=rpc_binding[1],
            server_rpc_secret_identity=rpc_binding[2],
        )
        threading.Thread(
            target=self._run_toggle_attempt,
            args=(attempt,),
            daemon=True,
            name="record-toggle",
        ).start()

    def _begin_recording_start(
        self,
        real_participants: list[object],
        secret_file: str,
        *,
        hosted_readiness: _HostedRecordingReadiness | None,
    ) -> None:
        """Allocate take resources only after hosted readiness is complete."""

        with self._shared_track_condition:
            pending_shared_track = bool(self._pending_shared_track_required)
        reference_controller = getattr(self._c, "_reference_track", None)
        reference_snapshot = getattr(reference_controller, "snapshot", None)
        reference_state = str(
            getattr(getattr(reference_snapshot, "state", None), "value", "") or ""
        ).lower()
        planned_shared_track = bool(
            pending_shared_track
            and getattr(reference_snapshot, "loaded", False)
            and (
                reference_state in {"routing", "playing"}
                or (
                    reference_state in {"ready", "paused"}
                    and bool(getattr(reference_snapshot, "can_play", False))
                )
            )
        )
        if pending_shared_track and not planned_shared_track:
            with self._shared_track_condition:
                self._pending_shared_track_required = False
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Shared Track Needs Attention",
                what_failed=(
                    "The Shared Track chosen for this take changed or became "
                    "unavailable during recording preflight. No recorder was "
                    "started."
                ),
                likely_cause=(
                    "The source may have been removed, stopped, or lost its "
                    "owned audio route while WebJam was proving the session."
                ),
                next_action=(
                    "Restore the intended Shared Track and try Record Session "
                    "again, or remove it before recording if this take should "
                    "not include it."
                ),
            )
            return
        recording_rpc_port = 0
        recording_rpc_secret_file = ""
        recording_rpc_secret_identity: tuple[int, int, int, int] | None = None
        if hosted_readiness is not None:
            expected = hosted_readiness.context
            try:
                current_port = int(self._c.settings.server_rpc_port)
                current_secret_file = str(
                    self._c.settings.server_rpc_secret_file or ""
                ).strip()
                current_secret_identity = _private_secret_file_identity(
                    current_secret_file
                )
            except (AttributeError, OSError, TypeError, ValueError):
                self._fail_hosted_recording_readiness(expected)
                return
            if (
                current_port != expected.server_rpc_port
                or current_secret_file != expected.server_rpc_secret_file
                or current_secret_identity != expected.server_rpc_secret_identity
            ):
                self._fail_hosted_recording_readiness(expected)
                return
            recording_rpc_port = expected.server_rpc_port
            recording_rpc_secret_file = expected.server_rpc_secret_file
            recording_rpc_secret_identity = expected.server_rpc_secret_identity
        else:
            try:
                recording_rpc_port = int(self._c.settings.server_rpc_port)
                recording_rpc_secret_file = str(
                    self._c.settings.server_rpc_secret_file or ""
                ).strip()
                if recording_rpc_secret_file != str(secret_file or "").strip():
                    raise ValueError("recorder configuration changed")
                recording_rpc_secret_identity = _private_secret_file_identity(
                    recording_rpc_secret_file
                )
            except (AttributeError, OSError, TypeError, ValueError):
                self._set_phase(RecorderPhase.ERROR)
                self._c._show_actionable_error(
                    "Recording Connection Needs Attention",
                    what_failed=(
                        "WebJam couldn't lock this take to the configured band "
                        "server. No recording was started."
                    ),
                    likely_cause=(
                        "The recorder secret file or server configuration changed."
                    ),
                    next_action=(
                        "Verify the host recording setup, then retry Record Session."
                    ),
                    retry_callback=self._c._on_record_requested,
                )
                return
        resolved_capture_tracks = resolve_capture_tracks(self._c.settings)
        host_local_channel_count = sum(
            track.channel_count for track in resolved_capture_tracks
        )
        storage = check_recording_storage(
            self._c.settings.takes_directory,
            # A ready Shared Track joins only after the server recorder starts,
            # so reserve its server stem even when it is not in preflight's
            # current roster yet. A paused/active route may be counted twice
            # here; conservative storage estimation is intentional.
            expected_server_tracks=(len(real_participants) + int(planned_shared_track)),
            local_originals_enabled=bool(self._c.settings.local_capture_enabled),
            local_original_tracks=host_local_channel_count,
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
                retry_callback=self._c._on_record_requested,
            )
            return
        if storage.status is RecordingStorageStatus.WARNING:
            self._c.window.flash_message(storage.detail, ms=8000)

        hosted_musicians = (
            dict(hosted_readiness.musician_ids_by_channel)
            if hosted_readiness is not None
            else {}
        )
        hosted_references = (
            set(hosted_readiness.reference_channels)
            if hosted_readiness is not None
            else set()
        )
        hosted_channel_counts = (
            dict(hosted_readiness.channel_counts_by_channel)
            if hosted_readiness is not None
            else {}
        )
        participant_channels: list[int] = []
        for index, participant in enumerate(real_participants):
            try:
                channel_id = int(participant.channel_id)
            except (AttributeError, TypeError, ValueError):
                channel_id = index
            participant_channels.append(channel_id)
        if hosted_readiness is not None and (
            set(participant_channels) != set(hosted_musicians) | hosted_references
            or any(not participant_id for participant_id in hosted_musicians.values())
            or set(participant_channels) != set(hosted_channel_counts)
            or any(width not in {1, 2} for width in hosted_channel_counts.values())
        ):
            self._fail_hosted_recording_readiness(hosted_readiness.context)
            return

        # Resolve every durable server-source identity before allocating a
        # take.  A generated UUID would make the start look exact while
        # guaranteeing that recorder receipts, repeat-take lanes, and guest
        # evidence can never prove the same logical source.  Hosted readiness
        # already supplies authenticated identities; non-hosted sessions must
        # have learned the same durable mapping from their participant state.
        durable_ids_by_channel: dict[int, str] = {}
        for index, participant in enumerate(real_participants):
            channel_id = participant_channels[index]
            if hosted_readiness is not None:
                durable = hosted_musicians.get(channel_id, "")
                if channel_id in hosted_references:
                    durable = self._reference_participant_id
            else:
                durable = str(getattr(participant, "participant_id", "") or "")
                if not durable:
                    durable = self._c.peer_participant_id_for_channel(channel_id)
                if not durable:
                    durable = self._participant_id_by_channel.get(channel_id, "")
            if not durable:
                self._set_phase(RecorderPhase.ERROR)
                self._c._show_actionable_error(
                    "Recording Identity Needs Attention",
                    what_failed=(
                        "WebJam couldn't prove one connected participant's "
                        "durable recording identity. No recorder was started."
                    ),
                    likely_cause=(
                        "The participant may still be joining, or the latest "
                        "authenticated roster observation has not arrived yet."
                    ),
                    next_action=(
                        "Wait for the participant list to settle, then try "
                        "Record Session again."
                    ),
                    retry_callback=self._c._on_record_requested,
                )
                return
            durable_ids_by_channel[channel_id] = durable

        if hosted_readiness is None:
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Topology Needs Attention",
                what_failed=(
                    "WebJam couldn't prove the exact mono/stereo layout of every "
                    "band-server source before recording. No recorder was started."
                ),
                likely_cause=(
                    "This recording host is not attached to the authenticated "
                    "hosted-session roster needed by the v0.26 recording plan."
                ),
                next_action=(
                    "Start or rejoin the hosted WebJam session, wait for every "
                    "source to appear, then try Record Session again."
                ),
                retry_callback=self._c._on_record_requested,
            )
            return

        # Every operation below this line owns take-scoped state. Hosted mode
        # reaches it only with a complete read-only correlation result.
        self._before_takes = snapshot_take_directories(self._c.settings.takes_directory)
        self._expected_tracks = len(real_participants) + int(
            planned_shared_track and not hosted_references
        )
        if self._c.host_peer.active:
            self._session_id = self._c.host_peer.session_id
            if self._c.host_peer.host_enrollment is not None:
                self._local_participant_id = (
                    self._c.host_peer.host_enrollment.participant_id
                )
        self._take_id = new_project_id()
        self._begin_recording_diagnostics(self._take_id)
        self._session_title = self._c.window.session_strip.current_title()
        self._reset_session_evidence()
        self._begin_shared_track_transaction(self._take_id)
        if recording_rpc_secret_identity is None:
            raise RuntimeError("recording RPC identity is unavailable")
        self._bind_recording_rpc_configuration(
            self._take_id,
            recording_rpc_port,
            recording_rpc_secret_file,
            recording_rpc_secret_identity,
        )
        self._track_names = {}
        self._participant_ids = {}
        for index, participant in enumerate(real_participants):
            channel_id = participant_channels[index]
            self._track_names[channel_id] = str(
                getattr(participant, "name", None)
                or getattr(participant, "role", None)
                or f"Musician {index + 1}"
            )
            durable = durable_ids_by_channel[channel_id]
            self._participant_id_by_channel[channel_id] = durable
            self._participant_ids[channel_id] = durable

        server_width_by_participant = {
            durable_ids_by_channel[channel_id]: hosted_channel_counts[channel_id]
            for channel_id in participant_channels
        }
        planned_server_ids = list(
            dict.fromkeys(
                durable
                for _channel, durable in sorted(self._participant_ids.items())
                if durable
            )
        )
        if (
            planned_shared_track
            and self._reference_participant_id not in planned_server_ids
        ):
            planned_server_ids.append(self._reference_participant_id)
            # The owned Shared Track companion is pinned to a stereo Jamulus
            # route. Its source file may be mono, but the recorder-facing
            # client contract is always the verified two-channel route.
            server_width_by_participant[self._reference_participant_id] = (
                _SHARED_TRACK_RECORDER_CHANNELS
            )
        try:
            server_channel_counts = tuple(
                server_width_by_participant[participant_id]
                for participant_id in planned_server_ids
            )
        except KeyError:
            self._fail_hosted_recording_readiness(hosted_readiness.context)
            return

        guest_local_originals, guest_plan_issues = (
            self._prepare_guest_local_original_bindings(self._take_id)
        )
        if guest_plan_issues:
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Guest Recording Plan Needs Attention",
                what_failed=(
                    "WebJam couldn't prove every connected guest's exact "
                    "Local Original choice before recording. No recorder was started."
                ),
                likely_cause=(
                    "A guest may still be joining, may use an older WebJam build, "
                    "or may have changed their input map during preflight."
                ),
                next_action=(
                    "Ask every guest to finish joining with the latest WebJam, "
                    "wait for the participant list to settle, then retry."
                ),
                retry_callback=self._c._on_record_requested,
            )
            self._retire_active_take(failed_take_id)
            return

        exact_local_track_count = len(resolved_capture_tracks) + sum(
            item.track_count for item in guest_local_originals
        )
        exact_local_channel_count = host_local_channel_count + sum(
            sum(item.channel_counts) for item in guest_local_originals
        )
        exact_storage = check_recording_storage(
            self._c.settings.takes_directory,
            expected_server_tracks=self._expected_tracks,
            local_originals_enabled=bool(exact_local_track_count),
            local_original_tracks=exact_local_channel_count,
        )
        if not exact_storage.can_start:
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Storage Needs Attention",
                what_failed=(
                    "WebJam can't reserve storage for the exact server and "
                    "Local Original plan. No recorder was started."
                ),
                likely_cause=exact_storage.detail,
                next_action=(
                    "Free up space or choose another Takes folder, then retry "
                    "without changing the participant or input plan."
                ),
                retry_callback=self._c._on_record_requested,
            )
            self._retire_active_take(failed_take_id)
            return
        if exact_storage.status is RecordingStorageStatus.WARNING:
            self._c.window.flash_message(exact_storage.detail, ms=8000)
        storage = exact_storage
        if not self._bind_session_recording_plan(
            storage,
            planned_shared_track=planned_shared_track,
            server_channel_counts=server_channel_counts,
            guest_local_originals=guest_local_originals,
        ):
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._c._show_actionable_error(
                "Recording Plan Needs Attention",
                what_failed=(
                    "WebJam couldn't bind this take to its exact musicians, "
                    "inputs, and Shared Track before recording. No recording "
                    "was started."
                ),
                likely_cause=(
                    "The session roster or recording setup changed while the "
                    "take was being prepared."
                ),
                next_action=(
                    "Wait for the participant list and Shared Track to settle, "
                    "then try Record Session again."
                ),
                retry_callback=self._c._on_record_requested,
            )
            self._retire_active_take(failed_take_id)
            return
        with self._evidence_lock:
            plan = (
                self._recording_plan
                if self._recording_plan_take_id == self._take_id
                else None
            )
        if plan is None:
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(failed_take_id)
            return
        plan_capture_tracks = plan.resolved_capture_tracks()
        local_preflight = (
            check_local_capture_preflight(
                tracks=plan_capture_tracks,
                device=self._c.settings.audio_input_device_index,
                samplerate=self._c.settings.audio_samplerate,
                blocksize=self._c.settings.audio_blocksize,
            )
            if plan_capture_tracks
            else None
        )
        try:
            presentation = self._build_recording_readiness_presentation(
                plan,
                local_preflight=local_preflight,
            )
        except Exception:  # noqa: BLE001 - presentation details stay private
            presentation = None
        confirm = getattr(self._c, "_confirm_recording_readiness", None)
        decision = confirm(presentation) if presentation is not None and callable(confirm) else False
        if not self._resolve_readiness_decision(plan, presentation, decision):
            return
        if not self._readiness_authority_still_matches(
            plan,
            hosted_readiness=hosted_readiness,
            planned_shared_track=planned_shared_track,
        ):
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(failed_take_id)
            self._c._show_actionable_error(
                "Recording Readiness Changed",
                what_failed=(
                    "A participant, input, storage, or Shared Track fact changed "
                    "after the readiness check. No recorder was started."
                ),
                likely_cause=(
                    "The session roster or recording setup changed while the "
                    "source-readiness sheet was open."
                ),
                next_action=(
                    "Wait for the session to settle, review the refreshed source "
                    "list, then try Record Session again."
                ),
                retry_callback=self._c._on_record_requested,
            )
            return
        if any(item.track_count for item in plan.guest_local_originals):
            self._arm_guest_capture_before_server_start(plan, hosted_readiness)
            return
        self._continue_recording_start(plan)

    def on_record_requested(self) -> None:
        studio = getattr(getattr(self._c, "window", None), "recording_studio", None)
        if bool(getattr(studio, "export_in_progress", False)):
            self._c.window.flash_message(
                "Wait for the Studio export to finish before starting a new take. "
                "The current recordings are safe.",
                ms=6000,
            )
            return
        pending_shutdown_take = str(self._shutdown_validation_pending_take_id or "")
        if pending_shutdown_take and pending_shutdown_take == self._take_id:
            self._request_shutdown_take_validation(pending_shutdown_take)
            self._c.window.flash_message(
                "Finish publishing the previous take before starting another "
                "recording.",
                ms=7000,
            )
            return
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
                    "Ask the host to press Record Session in Studio. Your audio "
                    "will appear there automatically as its own track."
                ),
            )
            return
        if self.phase in (
            RecorderPhase.PREFLIGHT,
            RecorderPhase.STARTING,
            RecorderPhase.STOPPING,
            RecorderPhase.FINALIZING,
        ):
            return

        target_armed = not self._c._recorder_armed
        if target_armed:
            self._set_phase(RecorderPhase.PREFLIGHT)
            real_participants = [
                participant
                for participant in self._c.participants.values()
                if not str(getattr(participant, "role", "")).startswith("Preview")
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
                        "then press Record Session again."
                    ),
                    retry_callback=self._c._on_record_requested,
                )
                return
            if getattr(self._c.host_peer, "active", False):
                context = self._hosted_recording_readiness_context(real_participants)
                if context is None:
                    self._fail_hosted_recording_readiness(None)
                    return
                self._hosted_preflight_generation += 1
                generation = self._hosted_preflight_generation
                threading.Thread(
                    target=self._run_hosted_recording_readiness,
                    args=(generation, context, secret_file),
                    daemon=True,
                    name="recording-readiness",
                ).start()
                return
            self._begin_recording_start(
                real_participants,
                secret_file,
                hosted_readiness=None,
            )
            return
        self._set_phase(RecorderPhase.STOPPING)
        rpc_binding = self._recording_rpc_binding_for_take(self._take_id)
        attempt = _ToggleAttempt(
            take_id=self._take_id,
            target_armed=False,
            server_rpc_port=(rpc_binding[0] if rpc_binding is not None else 0),
            server_rpc_secret_file=(rpc_binding[1] if rpc_binding is not None else ""),
            server_rpc_secret_identity=(
                rpc_binding[2] if rpc_binding is not None else None
            ),
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
            with self._capture_lock:
                self._local_capture = None
                self._local_capture_track_count = 0
            return True
        root = (self._c.settings.takes_directory or "").strip()
        if not root:
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(failed_take_id)
            self._c._show_actionable_error(
                "Recording Preflight Failed",
                what_failed="No writable Takes folder is configured for isolated host tracks.",
                likely_cause=(
                    "Local input-stem recording is enabled, so WebJam needs a "
                    "destination for the isolated tracks."
                ),
                next_action="Open Settings, choose the local Jamulus recording folder, then retry.",
                retry_callback=self._c._on_record_requested,
            )
            return False
        try:
            from core.local_capture import LocalInputCapture

            with self._evidence_lock:
                plan = (
                    self._recording_plan
                    if self._recording_plan_take_id == self._take_id
                    else None
                )
            if plan is None or plan.take_id != self._take_id:
                raise ValueError("the immutable recording plan is unavailable")
            capture_tracks = plan.resolved_capture_tracks()
            if not capture_tracks:
                # A valid map may intentionally opt every row out. Do not
                # translate that consent into LocalInputCapture's legacy pair.
                with self._capture_lock:
                    self._local_capture = None
                    self._local_capture_track_count = 0
                return True
            capture = LocalInputCapture(
                root,
                device=self._c.settings.audio_input_device_index,
                samplerate=self._c.settings.audio_samplerate,
                blocksize=self._c.settings.audio_blocksize,
                take_id=self._take_id,
                session_id=self._session_id,
                tracks=capture_tracks,
            )
            capture.start()
            with self._capture_lock:
                self._local_capture = capture
                self._local_capture_track_count = len(capture_tracks)
            return True
        except Exception:  # noqa: BLE001 - device errors can contain private paths
            LOGGER.warning("Isolated host capture preflight failed.")
            self._local_capture = None
            failed_take_id = self._take_id
            self._set_phase(RecorderPhase.ERROR)
            self._retire_active_take(failed_take_id)
            self._c._show_actionable_error(
                "Recording Preflight Failed",
                what_failed="WebJam couldn't open the selected local input map.",
                likely_cause=(
                    "The selected interface is unavailable, is not at 48 kHz, "
                    "does not provide every mapped channel, or another "
                    "application prevented capture."
                ),
                next_action=(
                    "Keep Jamulus running, verify the selected interface and "
                    "mapped inputs at 48 kHz, then retry. No server recording "
                    "was started."
                ),
                retry_callback=self._c._on_record_requested,
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
        except Exception:  # noqa: BLE001 - recovery errors can contain private paths
            LOGGER.error("Could not scan for abandoned local captures")
            return
        if not recovered:
            return
        for item in recovered:
            self._publish_recovered_local_capture(item, root)
        LOGGER.warning(
            "Recovered %d abandoned local capture%s for review.",
            len(recovered),
            "" if len(recovered) == 1 else "s",
        )
        self._c.window.flash_message(
            "WebJam recovered unfinished local audio from an earlier session. "
            "Open Studio to review it.",
            ms=9000,
        )
        try:
            self._c.window.recording_studio.set_takes_directory(root)
            self._c.window.recording_studio.reload()
        except Exception:  # noqa: BLE001 - UI errors can contain private paths
            LOGGER.debug("Could not refresh Studio recovery inventory")

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
                existing_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                existing_manifest = None
            if (
                isinstance(existing_manifest, dict)
                and existing_manifest.get("schema_version") == 2
            ):
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
                (
                    *evidence.timeline,
                    SessionTimelineEvent(
                        "local_capture_recovered",
                        detail=recovery_note,
                    ),
                )
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
            recovered_tracks = tuple(getattr(item, "tracks", ()) or ())
            result = write_take_manifest(
                recovery_dir,
                expected_tracks=0,
                required_local_stems=len(recovered_tracks),
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
                local_capture_tracks=(recovered_tracks or None),
                local_total_frames=frames,
                local_durable_frames=durable_frames,
                session_evidence=evidence,
            )
        except Exception:  # noqa: BLE001 - media stays visible; hide private paths
            LOGGER.error("Could not publish recovered local capture manifest")
            return

        if (
            result.take is not None
            and journal_result is not None
            and journal_result.trusted
        ):
            try:
                journal.remove(take_id)
            except (OSError, RecordingManifestJournalError, ValueError):
                LOGGER.warning(
                    "Could not retire recovery evidence after manifest publish."
                )

    def _publish_local_result_recovery(
        self, result, root: str, fallback_dir: Path
    ) -> Path | None:
        """Publish a visible local-result recovery without inventing media.

        ``LocalInputCapture`` may already have promoted a partial capture to a
        visible recovery folder. A normal interrupted stop writes finished WAVs
        directly into ``fallback_dir``. Both cases need one recovery-only
        project, while a hidden writer directory must stay untouched until its
        deferred promotion completes.
        """
        recovery_dir = Path(getattr(result, "recovery_dir", None) or fallback_dir)
        if (
            recovery_dir.name.startswith(".webjam-capture-")
            or not recovery_dir.is_dir()
        ):
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
                durable_frames=max(0, int(getattr(result, "durable_frames", 0) or 0)),
                sample_rate=max(0, int(getattr(capture_device, "sample_rate", 0) or 0)),
                gaps=tuple(getattr(result, "gaps", ()) or ()),
                capture_device=capture_device,
                tracks=tuple(getattr(result, "tracks", ()) or ()),
            ),
            root,
        )
        return recovery_dir

    def _run_toggle_attempt(
        self,
        attempt: _ToggleAttempt,
        secret_file: str = "",
    ) -> None:
        """Carry one request's take identity through the legacy worker hook."""

        self._toggle_worker_context.attempt = attempt
        try:
            selected_secret_file = attempt.server_rpc_secret_file or str(
                secret_file or ""
            )
            self._c._record_toggle_worker(
                attempt.target_armed,
                selected_secret_file,
            )
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
            if isinstance(attempt, _ToggleAttempt):
                expected_binding = (
                    attempt.server_rpc_port,
                    attempt.server_rpc_secret_file,
                    attempt.server_rpc_secret_identity,
                )
                if (
                    attempt.server_rpc_port <= 0
                    or not attempt.server_rpc_secret_file
                    or secret_file != attempt.server_rpc_secret_file
                    or self._recording_rpc_binding_for_take(attempt.take_id)
                    != expected_binding
                ):
                    raise ServerRpcError(
                        "The captured recorder configuration is no longer available."
                    )
                secret = _read_exact_secret_file(
                    attempt.server_rpc_secret_file,
                    attempt.server_rpc_secret_identity,
                )
                rpc_port = attempt.server_rpc_port
            else:
                # Compatibility seam for direct legacy/unit-test worker calls.
                secret = read_secret_file(secret_file)
                rpc_port = int(self._c.settings.server_rpc_port)
            with JamulusServerRpc(port=rpc_port, secret=secret) as rpc:
                receipt_context = self._roster_observation_context()
                try:
                    self._consume_authenticated_roster(
                        rpc.get_clients(), receipt_context
                    )
                except Exception:  # noqa: BLE001 - recorder control stays usable
                    LOGGER.debug("Initial recording roster receipt was unavailable")
                    self._invalidate_recording_identity(
                        "An authenticated Jamulus recording roster check failed. "
                        "Source audio was preserved for review.",
                        take_id=(receipt_context.take_id if receipt_context else None),
                    )
                acknowledged = (
                    rpc.start_recording() if target_armed else rpc.stop_recording()
                )
                if not acknowledged:
                    raise ServerRpcError(
                        "The recorder did not acknowledge the request."
                    )
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
                final_receipt_context = self._roster_observation_context()
                try:
                    self._consume_authenticated_roster(
                        rpc.get_clients(),
                        final_receipt_context,
                        # The pre-stop observation above may bind sources that
                        # were actually present while recording. Once Stop is
                        # acknowledged, a newly appearing recorder key cannot
                        # retroactively own media from the finished take.
                        allow_new_receipts=target_armed,
                    )
                except Exception:  # noqa: BLE001 - finalizer will mark missing proof
                    LOGGER.debug("Final recording roster receipt was unavailable")
                    self._invalidate_recording_identity(
                        "An authenticated Jamulus recording roster check failed. "
                        "Source audio was preserved for review.",
                        take_id=(
                            final_receipt_context.take_id
                            if final_receipt_context
                            else None
                        ),
                    )
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
            self._invalidate_recording_identity(
                "The Jamulus recorder control check failed before recording "
                "identity could be finalized. Source audio was preserved for review.",
                take_id=callback_take_id,
            )
            if callback_take_id is None:
                self._c._ui_invoker.invoke(
                    lambda message=str(exc): self._c._apply_record_toggle_failure(
                        message
                    )
                )
            else:
                self._c._ui_invoker.invoke(
                    lambda message=str(exc), take_id=callback_take_id: (
                        self.apply_toggle_failure(message, take_id=take_id)
                    )
                )
        except Exception:  # noqa: BLE001
            # Secret-file and socket exceptions can contain private paths or
            # endpoints. Keep the log path-free at this boundary.
            LOGGER.error("Record toggle failed unexpectedly")
            self._invalidate_recording_identity(
                "The Jamulus recorder control check failed before recording "
                "identity could be finalized. Source audio was preserved for review.",
                take_id=callback_take_id,
            )
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
            self._set_phase(self._confirmed_active_recording_phase())
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
            self.request_authenticated_roster_observation()
            try:
                self._c.metrics.increment("metric_recording_armed")
            except Exception:  # noqa: BLE001
                LOGGER.debug("Recording metric update failed")
        else:
            if self._take_id:
                stopped_utc, newly_confirmed = self._confirmed_recording_stopped()
                if newly_confirmed:
                    self._signal_peer_recording_finalizing(
                        self._take_id,
                        stopped_utc=stopped_utc,
                        message="The host is finalizing the recorded take.",
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
            self.phase is RecorderPhase.STARTING and not self._c._recorder_armed
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
        self._record_diagnostic_failure("recorder_control_failure")
        still_armed = bool(self._c._recorder_armed or self._c._server_recording)
        self._c.session_health.mark_rpc_result("recorder", False, message)
        self._set_phase(
            RecorderPhase.STOP_FAILED if still_armed else RecorderPhase.ERROR
        )
        self._c._show_actionable_error(
            "Recording Could Not Start"
            if not self._c._recorder_armed
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
                if still_armed
                else "End the session, start it again, and retry. WebJam will rebuild the "
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
            self._set_phase(self._confirmed_active_recording_phase())
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
            self.request_authenticated_roster_observation()
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
                    self._signal_peer_recording_finalizing(
                        self._take_id,
                        stopped_utc=stopped_utc,
                        message=(
                            "The band server stopped unexpectedly; the host is "
                            "finalizing the take."
                            if prior_phase is not RecorderPhase.STOPPING
                            else "The host is finalizing the recorded take."
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
                    self._set_phase(RecorderPhase.FINALIZING)
                else:
                    self._start_take_validation_once()
            elif self.phase in {
                RecorderPhase.STARTING,
                RecorderPhase.COUNT_IN,
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
        if errors:
            LOGGER.warning(
                "Recovered local capture needs review after %d issue%s.",
                len(errors),
                "" if len(errors) == 1 else "s",
            )
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
            ("Recording stopped before a finished server take arrived, but "
            "your isolated local tracks were saved."),
            "",
            ("This folder is outside your Takes folder, so it won't appear in "
            "Studio. Use Reveal in Finder to open it, and set a Takes "
            "folder in Settings so future recordings land in one place."),
        ]
        if errors:
            details.extend(
                [
                    "",
                    ("Some local tracks need review. Listen to each file before "
                    "using it."),
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
            self._record_diagnostic_failure("take_publication")
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
            self._signal_peer_validation_outcome(
                active_take_id,
                needs_attention=True,
                message="The take could not be finalized and needs host review.",
            )
            if (
                active_take_id
                and active_take_id == self._shutdown_validation_pending_take_id
            ):
                if self._validation_take_id == active_take_id:
                    self._validation_take_id = ""
                LOGGER.warning(
                    "Hosted take publication remains unconfirmed because no "
                    "Takes folder is configured; teardown is retained."
                )
                return
            self._retire_active_take(active_take_id)
            return
        threading.Thread(
            target=self._validate_take_worker,
            args=(active_take_id,),
            daemon=True,
            name="take-validation",
        ).start()

    def _post_validation_stage(self, text: str) -> None:
        """Update the Finalizing chip from the worker thread."""
        self._c._ui_invoker.invoke(
            lambda: (
                self._c.window.session_strip.set_recording_phase(
                    "finalizing", detail=text
                ),
                self._c.window.recording_studio.set_recording_phase(
                    "finalizing", detail=text
                ),
            )
        )

    def _validate_take_worker(self, take_id: str | None = None) -> None:
        """Never leave the recorder UI stuck if validation itself fails."""
        try:
            result = self._build_take_validation(take_id=take_id)
        except Exception:  # noqa: BLE001 - validation errors can contain private paths
            LOGGER.error("Take validation failed unexpectedly")
            self._record_diagnostic_failure("take_publication")
            candidate = find_changed_take(
                self._c.settings.takes_directory, self._before_takes
            )
            recovered, capture_errors = self._salvage_capture()
            candidate = candidate or recovered
            take = load_take(candidate) if candidate is not None else None
            result = TakeValidationResult(
                take,
                (
                    ("WebJam couldn't finish verifying this take. The source audio "
                    "was preserved; check free disk space and folder access, then "
                    "review the take before using it."),
                    *capture_errors,
                ),
            )
        result = self._reconcile_initial_peer_inventory(
            result,
            take_id=take_id,
        )
        publication_status: _PublishedTakeStatus | None = None
        if (
            take_id
            and take_id == self._take_id
            and take_id == self._shutdown_validation_pending_take_id
        ):
            root = str(self._c.settings.takes_directory or "").strip()
            publication_status = (
                self._published_take_has_id(root, take_id)
                if root
                else _PublishedTakeStatus.INDETERMINATE
            )
        self._c._ui_invoker.invoke(
            lambda: self._show_validation_result(
                result,
                take_id=take_id,
                publication_status=publication_status,
            )
        )

    @staticmethod
    def _publish_take_attention(take_dir: Path, message: str) -> bool:
        """CAS one fixed attention reason into a schema-v2 take manifest."""

        from core.take_project import (
            ProjectStatus,
            load_take_project,
            replace_take_project_manifest_if_unchanged,
        )

        manifest = Path(take_dir) / "webjam-take.json"
        for _attempt in range(3):
            try:
                prior = manifest.read_bytes()
                payload = json.loads(prior)
                project = load_take_project(take_dir)
            except Exception:  # noqa: BLE001 - fixed path-free failure boundary
                return False
            if not isinstance(payload, dict) or project.take_id != payload.get(
                "take_id"
            ):
                return False
            errors = tuple(dict.fromkeys((*project.errors, str(message))))
            payload["status"] = ProjectStatus.NEEDS_ATTENTION.value
            payload["errors"] = list(errors)
            payload["revision"] = max(
                int(payload.get("revision", 0) or 0) + 1,
                project.revision + 1,
            )
            if replace_take_project_manifest_if_unchanged(
                take_dir,
                expected_bytes=prior,
                payload=payload,
            ):
                return True
        return False

    def _reconcile_initial_peer_inventory(
        self,
        result: TakeValidationResult,
        *,
        take_id: str | None,
    ) -> TakeValidationResult:
        """Keep Finalizing until the first expected Local Original inventory."""

        if result.take is None or not take_id:
            return result
        host_peer = getattr(self._c, "host_peer", None)
        if not bool(getattr(host_peer, "active", False)):
            return result
        take_path = result.take.path
        inventory_error = (
            f"{PEER_TRANSFER_ERROR_PREFIX}Guest Local Original inventory could "
            "not be finalized before take publication. The take was preserved "
            "for review."
        )
        marker_valid = False
        try:
            self._post_validation_stage("WAITING FOR LOCAL ORIGINALS…")
            waiter = getattr(host_peer, "wait_for_initial_take_inventory", None)
            if callable(waiter):
                waiter(
                    take_id,
                    timeout_s=_PEER_INVENTORY_FINALIZE_TIMEOUT_S,
                )
            host_peer.register_take(take_id, take_path)
            host_peer.reconcile_take(take_id, take_path)
            payload = json.loads(
                (Path(take_path) / "webjam-take.json").read_text(encoding="utf-8")
            )
            peer_transfers = (
                payload.get("peer_transfers") if isinstance(payload, dict) else None
            )
            marker_valid = bool(
                isinstance(peer_transfers, dict)
                and peer_transfers.get("status") in {"complete", "needs_attention"}
            )
        except Exception:  # noqa: BLE001 - private transfer facts stay hidden
            LOGGER.error("Initial peer recording inventory could not be finalized")
        if not marker_valid:
            self._record_diagnostic_failure("peer_inventory")
            self._publish_take_attention(take_path, inventory_error)
        else:
            self._initial_peer_inventory_take_id = take_id
        loaded = load_take(take_path)
        if loaded is None:
            return TakeValidationResult(
                None,
                tuple(dict.fromkeys((*result.errors, inventory_error))),
                result.warnings,
                result.manifest_path,
            )
        errors = tuple(loaded.manifest_errors)
        if not marker_valid and inventory_error not in errors:
            errors = tuple(dict.fromkeys((*errors, inventory_error)))
        return TakeValidationResult(
            loaded,
            errors,
            tuple(loaded.manifest_warnings),
            loaded.manifest_path,
        )

    def _build_take_validation(
        self, *, take_id: str | None = None
    ) -> TakeValidationResult:
        active_take_id = self._take_id if take_id is None else take_id
        with self._evidence_lock:
            recording_plan = (
                self._recording_plan
                if self._recording_plan_take_id == active_take_id
                and self._recording_plan is not None
                and self._recording_plan.take_id == active_take_id
                and self._recording_plan.session_id == self._session_id
                else None
            )
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
        with self._capture_lock:
            required_local_count = (
                int(getattr(self, "_local_capture_track_count", 0) or 0)
                if required_local
                else 0
            )
        recording_receipts, recording_identity_errors = (
            self._final_recording_receipt_snapshot()
        )
        terminal_errors = list(
            self._recording_plan_validation_errors(
                active_take_id,
                recording_receipts,
                required_local_count=required_local_count,
            )
        )
        terminal_errors.extend(
            self._await_shared_track_transaction_errors(active_take_id)
        )
        if (
            self._current_session_evidence().recovery_status
            is RecoveryStatus.NEEDS_ATTENTION
        ):
            terminal_errors.append(
                "Recording recovery or an unexpected server stop needs host "
                "review before this take can be treated as complete."
            )
        terminal_errors = list(dict.fromkeys(terminal_errors))
        if take_dir is None:
            self._record_diagnostic_failure("take_publication")
            self._mark_recording_recovery(
                RecoveryStatus.NEEDS_ATTENTION,
                "No new Jamulus take folder appeared after recording stopped.",
                event="server_take_missing",
            )
            recovered: Path | None = (
                Path(root).expanduser() / f"Recovered-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            capture_errors: tuple[str, ...] = tuple(terminal_errors)
            started_utc = ""
            duration_s = 0.0
            capture_gaps: tuple[object, ...] = ()
            local_capture_tracks: object = ()
            local_total_frames = 0
            local_durable_frames: int | None = None
            capture_device = None
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(recovered)
                capture_errors = tuple(
                    dict.fromkeys((*capture_errors, *local_result.errors))
                )
                capture_errors = tuple(
                    dict.fromkeys(
                        (
                            *capture_errors,
                            *self._local_capture_plan_validation_errors(
                                active_take_id,
                                getattr(local_result, "tracks", ()),
                                required_local_count=required_local_count,
                            ),
                        )
                    )
                )
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
                capture_gaps = tuple(getattr(local_result, "gaps", ()) or ())
                local_capture_tracks = tuple(getattr(local_result, "tracks", ()) or ())
                self._record_dropout_gaps(len(capture_gaps))
                local_total_frames = int(getattr(local_result, "total_frames", 0) or 0)
                local_durable_frames = getattr(local_result, "durable_frames", None)
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
                    required_local_stems=required_local_count,
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
                    # Bind the manifest to what the capture writer actually
                    # finalized. The immutable plan is supplied separately and
                    # rejects a missing/substituted topology; never fill absent
                    # result evidence with the topology we merely intended.
                    local_capture_tracks=local_capture_tracks,
                    local_total_frames=local_total_frames,
                    local_durable_frames=local_durable_frames,
                    session_evidence=self._current_session_evidence(),
                    recording_receipts=recording_receipts,
                    recording_identity_errors=recording_identity_errors,
                    required_reference_track=self.shared_track_required_for_active_take,
                    recording_plan=recording_plan,
                )
                if result.take is not None:
                    self._retire_journal_for_exact_publication(active_take_id)
            else:
                result = TakeValidationResult(
                    None,
                    ("No new Jamulus take folder appeared after recording stopped.",),
                )
        else:
            capture_errors: tuple[str, ...] = tuple(terminal_errors)
            started_utc = ""
            duration_s = 0.0
            capture_gaps: tuple[object, ...] = ()
            local_capture_tracks: object = ()
            local_total_frames = 0
            local_durable_frames: int | None = None
            capture_device = None
            capture = self._take_local_capture()
            if capture is not None:
                local_result = capture.stop_into(take_dir)
                capture_errors = tuple(
                    dict.fromkeys((*capture_errors, *local_result.errors))
                )
                capture_errors = tuple(
                    dict.fromkeys(
                        (
                            *capture_errors,
                            *self._local_capture_plan_validation_errors(
                                active_take_id,
                                getattr(local_result, "tracks", ()),
                                required_local_count=required_local_count,
                            ),
                        )
                    )
                )
                started_utc = local_result.started_utc
                duration_s = local_result.duration_s
                capture_gaps = tuple(getattr(local_result, "gaps", ()) or ())
                local_capture_tracks = tuple(getattr(local_result, "tracks", ()) or ())
                self._record_dropout_gaps(len(capture_gaps))
                local_total_frames = int(getattr(local_result, "total_frames", 0) or 0)
                local_durable_frames = getattr(local_result, "durable_frames", None)
                capture_device = getattr(local_result, "capture_device", None)
            self._post_validation_stage("CHECKING TRACKS…")
            stable = wait_for_take_files_stable(take_dir, polls=20, interval_s=0.25)
            if not stable:
                capture_errors = (
                    *capture_errors,
                    "Take files did not become stable in time.",
                )
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
                required_local_stems=required_local_count,
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
                # Bind the manifest to what the capture writer actually
                # finalized. The plan below remains the independent expected
                # topology used by the fail-closed publication check.
                local_capture_tracks=local_capture_tracks,
                local_total_frames=local_total_frames,
                local_durable_frames=local_durable_frames,
                session_evidence=self._current_session_evidence(),
                recording_receipts=recording_receipts,
                recording_identity_errors=recording_identity_errors,
                required_reference_track=self.shared_track_required_for_active_take,
                recording_plan=recording_plan,
            )
            if result.take is not None:
                self._retire_journal_for_exact_publication(active_take_id)
        return result

    def _show_validation_result(
        self,
        result: TakeValidationResult,
        *,
        take_id: str | None = None,
        publication_status: _PublishedTakeStatus | None = None,
    ) -> None:
        if take_id and take_id != self._take_id:
            LOGGER.debug("Ignoring validation result for a retired recording take")
            return
        completed_take_id = self._take_id if take_id is None else take_id
        with self._peer_reconcile_lock:
            pending_peer_path = self._pending_peer_reconciliations.pop(
                str(completed_take_id or ""),
                None,
            )
        if pending_peer_path is not None:
            refreshed = load_take(pending_peer_path)
            if refreshed is not None and refreshed.take_id == completed_take_id:
                result = TakeValidationResult(
                    refreshed,
                    tuple(refreshed.manifest_errors),
                    tuple(refreshed.manifest_warnings),
                    refreshed.manifest_path,
                )
            else:
                reload_error = (
                    f"{PEER_TRANSFER_ERROR_PREFIX}A committed Local Original "
                    "update could not be reloaded before take publication."
                )
                result = TakeValidationResult(
                    result.take,
                    tuple(dict.fromkeys((*result.errors, reload_error))),
                    result.warnings,
                    result.manifest_path,
                )
        shutdown_pending = bool(
            completed_take_id
            and completed_take_id == self._shutdown_validation_pending_take_id
        )
        if shutdown_pending and publication_status is None:
            root = str(self._c.settings.takes_directory or "").strip()
            publication_status = (
                self._published_take_has_id(root, completed_take_id)
                if root
                else _PublishedTakeStatus.INDETERMINATE
            )
        durable_shutdown_publication = bool(
            not shutdown_pending or publication_status is _PublishedTakeStatus.MATCH
        )
        take_status = str(
            getattr(result.take, "validation_status", "complete")
            if result.take is not None
            else ""
        )
        take_manifest_errors = tuple(
            getattr(result.take, "manifest_errors", ()) or ()
            if result.take is not None
            else ()
        )
        effective_needs_attention = bool(
            not result.ok
            or result.take is None
            or take_status != "complete"
            or bool(take_manifest_errors)
            or self._current_session_evidence().recovery_status
            is RecoveryStatus.NEEDS_ATTENTION
        )
        host_peer = getattr(self._c, "host_peer", None)
        if (
            result.take is not None
            and completed_take_id
            and durable_shutdown_publication
            and bool(getattr(host_peer, "active", False))
            and self._initial_peer_inventory_take_id != completed_take_id
        ):
            try:
                host_peer.register_take(completed_take_id, result.take.path)
            except Exception:  # noqa: BLE001 - private transfer facts stay hidden
                LOGGER.error("Could not queue peer transfer inventory for take")
            # Real TakeInfo objects carry their durable take ID. Production
            # may never publish Complete without the worker's initial
            # reconciliation marker. Simple test doubles keep the older seam.
            if bool(getattr(result.take, "take_id", "")):
                effective_needs_attention = True
        self.last_validation = result
        self.last_completed_take = (
            result.take.path
            if result.take is not None and durable_shutdown_publication
            else None
        )
        if result.warnings:
            LOGGER.warning(
                "Take validation completed with %d warning%s.",
                len(result.warnings),
                "" if len(result.warnings) == 1 else "s",
            )
        if effective_needs_attention:
            self._record_diagnostic_failure("take_needs_attention")
            if result.errors:
                LOGGER.warning(
                    "Take validation needs attention after %d issue%s.",
                    len(result.errors),
                    "" if len(result.errors) == 1 else "s",
                )
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
        if result.take is not None and durable_shutdown_publication:
            open_studio = getattr(self._c, "_on_rail_view_changed", None)
            if callable(open_studio):
                open_studio("takes")
        peer_needs_attention = bool(
            effective_needs_attention or not durable_shutdown_publication
        )
        self._signal_peer_validation_outcome(
            completed_take_id,
            needs_attention=peer_needs_attention,
            message=(
                "The take needs host review before it is ready."
                if peer_needs_attention
                else "The take is finalized and ready."
            ),
        )
        if shutdown_pending and not durable_shutdown_publication:
            # Raw media may be loadable even though no exact schema-v2
            # publication exists. Keep the take/journal/server ownership and
            # release only this validation lease so Try Quit/End can retry.
            if self._validation_take_id == completed_take_id:
                self._validation_take_id = ""
            self._set_phase(RecorderPhase.NEEDS_ATTENTION)
            self._c.window.flash_message(
                "Recording audio was preserved, but take publication is not "
                "confirmed yet. Try finishing the session again.",
                ms=10000,
            )
            LOGGER.warning(
                "Hosted take publication remains unconfirmed; teardown is retained."
            )
            return
        if shutdown_pending:
            self._remove_evidence_journal_after_manifest()
        self._retire_active_take(completed_take_id)

    def on_peer_take_reconciled(self, take_id: str, take_dir: Path) -> None:
        """Republish terminal truth when a late Local Original changes a take."""

        if self.phase is RecorderPhase.FINALIZING:
            if self._take_id and self._take_id != take_id:
                return
            with self._peer_reconcile_lock:
                self._pending_peer_reconciliations[str(take_id)] = Path(take_dir)
            return
        if self._take_id and self._take_id != take_id:
            return
        previous = self.last_validation
        previous_take = previous.take if previous is not None else None
        if previous_take is None or previous_take.take_id != take_id:
            return
        refreshed = load_take(Path(take_dir))
        if refreshed is None or refreshed.take_id != take_id:
            return
        result = TakeValidationResult(
            refreshed,
            tuple(refreshed.manifest_errors),
            tuple(refreshed.manifest_warnings),
            refreshed.manifest_path,
        )
        previous_attention = bool(
            not previous.ok
            or previous_take.validation_status != "complete"
            or previous_take.manifest_errors
        )
        needs_attention = bool(
            not result.ok
            or refreshed.validation_status != "complete"
            or refreshed.manifest_errors
        )
        self.last_validation = result
        self.last_completed_take = refreshed.path
        if needs_attention != previous_attention:
            self._set_phase(
                RecorderPhase.NEEDS_ATTENTION
                if needs_attention
                else RecorderPhase.COMPLETE
            )
            self._c.window.flash_message(
                (
                    "A late Local Original changed this take. Review it in Studio."
                    if needs_attention
                    else "All expected Local Originals arrived. The take is ready."
                ),
                ms=7000,
            )
        self._signal_peer_validation_outcome(
            take_id,
            needs_attention=needs_attention,
            message=(
                "The take needs host review before it is ready."
                if needs_attention
                else "The take is finalized and ready."
            ),
        )

    @staticmethod
    def _completion_text(result: TakeValidationResult) -> tuple[str, str]:
        """Title and body for the completion box — pure, so tests can read it."""
        if result.ok:
            details = [f"Take saved · {result.summary}"]
            if result.warnings:
                details.extend(
                    [
                        "",
                        ("WebJam found something to review. Open Studio and listen "
                        "to each track before export."),
                    ]
                )
            return "WebJam — Recording complete", "\n".join(details)
        title = "WebJam — Take needs attention"
        if result.take is not None:
            details = [
                "Your recording was preserved, but it did not pass every check.",
                "",
                ("The recorded tracks may still be playable. Open Studio, listen "
                "to each track, then record a short test take."),
                "",
                "Use Reveal in Finder to open the saved files.",
            ]
        else:
            details = [
                "No completed take was found on this Mac.",
                "",
                ("There is nothing to play back yet. Run Band Check (F2) to "
                "verify the band server's recorder, then record a short test "
                "take."),
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
            reveal_button = box.addButton(
                "Reveal in Finder", QMessageBox.ButtonRole.ActionRole
            )
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

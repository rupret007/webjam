"""Band-server recording lifecycle, validation, and completion feedback."""

from __future__ import annotations

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

from core.take_library import (
    EVIDENCE_ONLY_EXPORT_BLOCK_REASON,
    RecorderClientReceipt,
    RecorderRosterError,
    RecordingStagingIdentity,
    TakeValidationResult,
    find_changed_take,
    is_local_stem_name,
    load_take,
    recording_staging_identity,
    recorder_client_observations,
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
_FINAL_RECEIPT_DRAIN_TIMEOUT_S = 5.0


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


@dataclass(frozen=True)
class _ToggleAttempt:
    """One recorder RPC request bound to the take that requested it.

    Jamulus recorder notifications and RPC replies arrive independently.  The
    take ID is therefore carried back to the UI thread so a late worker reply
    can never mutate a later take (or revive one that has entered validation).
    """

    take_id: str
    target_armed: bool


@dataclass(frozen=True)
class _RosterObservationContext:
    """One take-bound, address-free roster correlation request."""

    take_id: str
    channel_bindings: tuple[tuple[int, str, str, int], ...]
    reference_claim: object | None


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
        self._recording_receipts: dict[
            tuple[str, int], RecorderClientReceipt
        ] = {}
        self._recording_conflicted_keys: set[str] = set()
        self._recording_unproven_keys: set[str] = set()
        self._recording_digest_by_channel: dict[int, str] = {}
        self._recording_channel_by_digest: dict[str, int] = {}
        self._recording_identity_errors: list[str] = []
        self._recording_identity_invalid = False
        self._reference_participant_id = new_project_id()
        self._roster_poll_inflight = False
        self._roster_poll_pending: _RosterObservationContext | None = None
        self._recording_receipts_finalizing_take_id = ""
        self._recording_receipts_frozen_take_id = ""
        # Session teardown may ask from either a worker or the UI thread. Once
        # recorder stop is confirmed, keep the server/application alive until
        # the ordinary take-validation owner has durably published the media.
        self._shutdown_stop_lock = threading.Lock()
        self._shutdown_validation_pending_take_id = ""
        self._shutdown_validation_dispatch_take_id = ""

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

        if take_id and self._shutdown_validation_pending_take_id == take_id:
            self._shutdown_validation_pending_take_id = ""
        if take_id and self._shutdown_validation_dispatch_take_id == take_id:
            self._shutdown_validation_dispatch_take_id = ""
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
            self._recording_identity_errors = []
            self._recording_identity_invalid = False
            self._reference_participant_id = new_project_id()
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
        for item in presentations:
            try:
                channel_id = int(getattr(item, "channel_id"))
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
            if authenticated_host_registry:
                try:
                    presence = host_peer.presence_for_channel(channel_id)
                    if presence is not None and int(presence.generation) > 0:
                        durable = str(uuid.UUID(str(presence.participant_id)))
                        generation = int(presence.generation)
                        name = self._normalized_roster_name(presence.display_name)
                except Exception:  # noqa: BLE001 - optional peer evidence
                    durable = ""
                    generation = 0
            else:
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
            reference_claim=(
                self._reference_recording_claim()
                if capture_reference_claim
                else None
            ),
        )

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
                secret_file = str(
                    self._c.settings.server_rpc_secret_file or ""
                ).strip()
                secret = read_secret_file(secret_file)
                with JamulusServerRpc(
                    port=self._c.settings.server_rpc_port,
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
            if isinstance(before, ReferenceTrackOwnershipClaim)
            and before == after
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

        # Recorder filenames identify a source by an address-derived digest,
        # not by the transient Jamulus channel.  Bind both directions for this
        # take so channel reuse and digest migration cannot silently inherit a
        # previously proved musician.  Any transition permanently conflicts
        # every involved digest and removes its earlier receipts.
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
                    "Authenticated Jamulus recording identity evidence "
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

        before_bindings = {
            channel_id: (name, participant_id, generation)
            for channel_id, name, participant_id, generation in context.channel_bindings
        }
        after_context = self._roster_observation_context()
        if after_context is None or after_context.take_id != context.take_id:
            return
        after_bindings = {
            channel_id: (name, participant_id, generation)
            for channel_id, name, participant_id, generation in after_context.channel_bindings
        }
        # A durable binding must agree on both sides of the authenticated
        # getClients response. This closes the same-name channel-reuse race and
        # also rejects a UI card whose old participant_id was never cleared.
        stable_bindings = {
            channel_id: value
            for channel_id, value in before_bindings.items()
            if value == after_bindings.get(channel_id) and value[1]
        }
        receipts: list[RecorderClientReceipt] = []
        unproven_keys: set[str] = set()
        for observation in observations:
            if observation.matches_owned_reference and stable_reference is not None:
                participant_id = self._reference_participant_id
                display_name = REFERENCE_PARTICIPANT_NAME
                source_kind = "reference_track"
            else:
                binding_name, candidate_id, _generation = stable_bindings.get(
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
            receipts.append(RecorderClientReceipt(
                server_channel_id=observation.server_channel_id,
                display_name=display_name,
                participant_id=participant_id,
                recorder_key_sha256=observation.recorder_key_sha256,
                channels=observation.channels,
                source_kind=source_kind,
            ))

        with self._receipt_lock:
            if context.take_id != self._take_id or self._recording_identity_invalid:
                return
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
                    allow_new_receipts
                    and digest not in self._recording_conflicted_keys
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
                if not allow_new_receipts and receipt_key not in self._recording_receipts:
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
                    secret = read_secret_file(
                        str(self._c.settings.server_rpc_secret_file or "").strip()
                    )
                    with JamulusServerRpc(
                        port=self._c.settings.server_rpc_port,
                        secret=secret,
                    ) as rpc:
                        payload = rpc.get_clients()
                    self._consume_authenticated_roster_serial(
                        payload,
                        context,
                        allow_new_receipts=False,
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
        if not journal or not take_id or failed or take_id != self._take_id:
            return
        try:
            journal.update(take_id, self._current_session_evidence())
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
            str(value)
            for value in (self._take_id, self._validation_take_id)
            if value
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
                    if (
                        not stat.S_ISREG(opened_before.st_mode)
                        or fingerprint(opened_before) != fingerprint(manifest_before)
                    ):
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
                if (
                    fingerprint(manifest_after) != fingerprint(manifest_before)
                    or fingerprint(child_after) != fingerprint(child_before)
                ):
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
                if self._recovery_path_fingerprint(
                    candidate, directory=True
                ) != directory_fingerprint or self._recovery_path_fingerprint(
                    marker, directory=False
                ) != marker_fingerprint:
                    raise OSError("recovery entry changed")
                wavs: list[Path] = []
                for index, item in enumerate(candidate.iterdir()):
                    if index >= 512:
                        raise OSError("bounded inventory exceeded")
                    if item.suffix.lower() == ".wav":
                        self._recovery_path_fingerprint(item, directory=False)
                        wavs.append(item)
                server_tracks = sum(
                    not is_local_stem_name(item.name) for item in wavs
                )
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
                    recovery_notes=tuple(dict.fromkeys(
                        (*evidence.recovery_notes, recovery_note)
                    )),
                    timeline=tuple(dict.fromkeys((
                        *evidence.timeline,
                        SessionTimelineEvent(
                            "recording_media_publication_recovered",
                            detail=recovery_note,
                        ),
                    )).keys()),
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
                        staging_identity.take_id
                        if staging_identity is not None
                        else ""
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
                RecorderPhase.RECORDING,
                RecorderPhase.STOP_FAILED,
            )
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
        if active_take_id and active_take_id == self._shutdown_validation_dispatch_take_id:
            return False
        if active_take_id and active_take_id == self._shutdown_validation_pending_take_id:
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
            if active_take_id and active_take_id == self._shutdown_validation_dispatch_take_id:
                return False
            if active_take_id and active_take_id == self._shutdown_validation_pending_take_id:
                self._request_shutdown_take_validation(active_take_id)
                return False
            if not (self._c._server_recording or self._c._recorder_armed):
                return True
            secret_file = (self._c.settings.server_rpc_secret_file or "").strip()
            if not secret_file:
                LOGGER.error(
                    "Hosted recording is active but no recorder secret is configured"
                )
                return False
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

            secret = read_secret_file(secret_file)
            rpc = JamulusServerRpc(port=self._c.settings.server_rpc_port, secret=secret)
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
                                self._c.signal_peer_recording_stopped(
                                    active_take_id,
                                    stopped_utc=stopped_utc,
                                )
                            self._shutdown_validation_pending_take_id = (
                                active_take_id
                            )
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
                self._set_phase(RecorderPhase.VALIDATING)
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
        studio = getattr(getattr(self._c, "window", None), "recording_studio", None)
        if bool(getattr(studio, "export_in_progress", False)):
            self._c.window.flash_message(
                "Wait for the Studio export to finish before starting a new take. "
                "The current recordings are safe.",
                ms=6000,
            )
            return
        pending_shutdown_take = str(
            self._shutdown_validation_pending_take_id or ""
        )
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
                    "Ask the host to press Record Take in Studio. Your audio "
                    "will appear there automatically as its own track."
                ),
            )
            return
        if self.phase in (
            RecorderPhase.PREFLIGHT,
            RecorderPhase.STARTING,
            RecorderPhase.STOPPING,
            RecorderPhase.VALIDATING,
        ):
            return

        target_armed = not self._c._recorder_armed
        if target_armed:
            self._set_phase(RecorderPhase.PREFLIGHT)
            real_participants = [
                participant
                for participant in self._c.participants.values()
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
                local_originals_enabled=bool(self._c.settings.local_capture_enabled),
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
            self.request_authenticated_roster_observation()
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
        except Exception:  # noqa: BLE001 - device errors can contain private paths
            LOGGER.warning("Isolated host capture preflight failed.")
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
                        take_id=(
                            receipt_context.take_id if receipt_context else None
                        ),
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
            self.request_authenticated_roster_observation()
            try:
                self._c.metrics.increment("metric_recording_armed")
            except Exception:  # noqa: BLE001
                LOGGER.debug("Recording metric update failed")
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
        except Exception:  # noqa: BLE001 - validation errors can contain private paths
            LOGGER.error("Take validation failed unexpectedly")
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
        recording_receipts, recording_identity_errors = (
            self._final_recording_receipt_snapshot()
        )
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
                    recording_receipts=recording_receipts,
                    recording_identity_errors=recording_identity_errors,
                )
                if result.take is not None:
                    self._retire_journal_for_exact_publication(active_take_id)
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
                recording_receipts=recording_receipts,
                recording_identity_errors=recording_identity_errors,
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
            not shutdown_pending
            or publication_status is _PublishedTakeStatus.MATCH
        )
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
        if not result.ok:
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
        if (
            result.take is not None
            and completed_take_id
            and durable_shutdown_publication
            and self._c.host_peer.active
        ):
            try:
                self._c.host_peer.register_take(completed_take_id, result.take.path)
            except Exception:  # noqa: BLE001 - transfer errors can contain private paths
                LOGGER.error("Could not attach peer transfer inventory to take")
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

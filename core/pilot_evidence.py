"""Bounded, local-only evidence for a private WebJam pilot.

This module is deliberately a *ledger*, not telemetry.  A pilot coordinator
can record a small allowlisted observation after it has real local evidence,
then reopen the same opaque run after an app restart.  It never accepts
free-form log text, device names, file paths, invitations, addresses, audio,
or credentials.

The persisted JSON contains a hash-linked event chain.  The chain is useful
for detecting accidental edits, truncation, reordering, and stale writes; it
does **not** claim to be a signed or remotely witnessed audit log.  Saving a
loaded ledger verifies that the on-disk events are an exact immutable prefix
of the replacement, so a later successful retry can be appended but cannot
erase an earlier failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from core.file_io import atomic_write_text
from core.redaction import redact_text

PILOT_EVIDENCE_SCHEMA_VERSION = 1
"""Independent schema version for private pilot evidence records."""

PILOT_EVIDENCE_DIRECTORY = ".webjam-pilot-evidence"
"""Hidden directory under a caller-provided local storage root."""

MAX_PILOT_EVENTS = 256
"""Hard cap: full ledgers fail safely instead of discarding early failures."""

MAX_LIMITATIONS_PER_EVENT = 8
MAX_PILOT_LEDGER_BYTES = 256 * 1024
MAX_PILOT_LEDGER_FILES = 64
_CHAIN_ORIGIN = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:[-+][a-z0-9.-]{1,32})?$", re.IGNORECASE)
_PACKAGE_ARTIFACT_RE = re.compile(
    r"^webjam-v\d+(?:\.\d+){1,3}(?:-[a-z0-9]+){1,8}(?:\.zip)?$", re.IGNORECASE
)


class PilotEvidenceError(ValueError):
    """Raised when a pilot evidence record is unsafe, malformed, or stale."""


class PilotRole(str, Enum):
    """The only roles that can be reported without personal identity data."""

    HOST = "host"
    GUEST = "guest"


class EvidenceOutcome(str, Enum):
    """Truthful results for automatic and explicitly human observations."""

    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT RUN"
    INDETERMINATE = "INDETERMINATE"
    NOT_AVAILABLE = "NOT AVAILABLE"


class PilotSessionState(str, Enum):
    """Safe state labels; no provider details or raw process state is stored."""

    IDLE = "idle"
    CONFIRMING_IDENTITY_AND_SOUND = "confirming_identity_and_sound"
    BAND_CHECK_REQUIRED = "band_check_required"
    BAND_CHECK_IN_PROGRESS = "band_check_in_progress"
    READY_TO_START = "ready_to_start"
    STARTING_HOST = "starting_host"
    WAITING_FOR_HOST_READINESS = "waiting_for_host_readiness"
    INVITE_READY = "invite_ready"
    JOINING = "joining"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    LIVE = "live"
    RECORDING_STARTING = "recording_starting"
    RECORDING = "recording"
    RECORDING_STOPPING = "recording_stopping"
    TAKE_VALIDATING = "take_validating"
    GUEST_MEDIA_TRANSFERRING = "guest_media_transferring"
    TAKE_READY = "take_ready"
    TAKE_NEEDS_ATTENTION = "take_needs_attention"
    REVIEWING = "reviewing"
    EXPORTING = "exporting"
    ENDING = "ending"
    ENDED = "ended"
    BLOCKED = "blocked"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class PilotObservationClass(str, Enum):
    """Bounded classes of facts that a private pilot is allowed to retain."""

    APP_LAUNCHED = "app_launched"
    PACKAGE_IDENTITY = "package_identity"
    BAND_CHECK = "band_check"
    ROUTE_FINGERPRINT = "route_fingerprint"
    SERVER_AUTHENTICATION = "server_authentication"
    EXPECTED_LISTENER = "expected_listener"
    INVITE_AVAILABILITY = "invite_availability"
    DEEP_LINK_PARSE = "deep_link_parse"
    ENROLLMENT = "enrollment"
    PARTICIPANT_PRESENCE = "participant_presence"
    CONNECTION = "connection"
    RECONNECTION = "reconnection"
    RECORDING_REQUEST = "recording_request"
    RECORDER_CONFIRMATION = "recorder_confirmation"
    RECORDING_STOP = "recording_stop"
    TAKE_VALIDATION = "take_validation"
    GUEST_CAPTURE = "guest_capture"
    TRANSFER_RESUME = "transfer_resume"
    TRANSFER_HASH = "transfer_hash"
    TRACK_INVENTORY = "track_inventory"
    GAP_INVENTORY = "gap_inventory"
    RECOVERY_PROJECT = "recovery_project"
    STUDIO_SIDECAR = "studio_sidecar"
    SOURCE_HASH = "source_hash"
    TRACK_EXPORT = "track_export"
    STEM_ANALYSIS = "stem_analysis"
    CHECKSUM_VERIFICATION = "checksum_verification"
    OWNED_PROCESS_CLEANUP = "owned_process_cleanup"
    PILOT_PAUSED = "pilot_paused"
    PILOT_RESUMED = "pilot_resumed"
    PILOT_ABANDONED = "pilot_abandoned"
    STEP_MARKED_BLOCKED = "step_marked_blocked"
    HUMAN_HOST_HEARD_BANDMATE = "human_host_heard_bandmate"
    HUMAN_BANDMATE_HEARD_HOST = "human_bandmate_heard_host"
    HUMAN_SESSION_PLAYABLE = "human_session_playable"
    HUMAN_HEADPHONES_CORRECT = "human_headphones_correct"
    HUMAN_CLIPPING_OR_ECHO = "human_clipping_or_echo"
    HUMAN_STUDIO_PLAYBACK = "human_studio_playback"
    HUMAN_STUDIO_ALIGNMENT = "human_studio_alignment"
    HUMAN_REHEARSAL_USEFUL = "human_rehearsal_useful"


class EvidenceReference(str, Enum):
    """An allowlisted *kind* of evidence, never a file name or a path."""

    NONE = "none"
    PACKAGE_METADATA = "package_metadata"
    BAND_CHECK_RESULT = "band_check_result"
    SESSION_STATE = "session_state"
    RECORDER_STATE = "recorder_state"
    TAKE_MANIFEST = "take_manifest"
    TRANSFER_RECEIPT = "transfer_receipt"
    STUDIO_STATE = "studio_state"
    EXPORT_MANIFEST = "export_manifest"
    CHECKSUM_MANIFEST = "checksum_manifest"
    PROCESS_CLEANUP = "process_cleanup"
    HUMAN_CONFIRMATION = "human_confirmation"


class EvidenceLimitation(str, Enum):
    """Safe reasons why an observation is not a broader claim."""

    NONE = "none"
    HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
    SECOND_MAC_UNAVAILABLE = "second_mac_unavailable"
    AUDIO_INTERFACE_UNAVAILABLE = "audio_interface_unavailable"
    HEADPHONES_UNAVAILABLE = "headphones_unavailable"
    EXTERNAL_EDITOR_UNAVAILABLE = "external_editor_unavailable"
    NETWORK_UNAVAILABLE = "network_unavailable"
    PARTIAL_EVIDENCE = "partial_evidence"
    RECOVERY_REQUIRED = "recovery_required"
    PROCESS_OUTCOME_UNKNOWN = "process_outcome_unknown"
    HARDWARE_NOT_EXERCISED = "hardware_not_exercised"


_HUMAN_OBSERVATIONS = frozenset(
    {
        PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
        PilotObservationClass.HUMAN_BANDMATE_HEARD_HOST,
        PilotObservationClass.HUMAN_SESSION_PLAYABLE,
        PilotObservationClass.HUMAN_HEADPHONES_CORRECT,
        PilotObservationClass.HUMAN_CLIPPING_OR_ECHO,
        PilotObservationClass.HUMAN_STUDIO_PLAYBACK,
        PilotObservationClass.HUMAN_STUDIO_ALIGNMENT,
        PilotObservationClass.HUMAN_REHEARSAL_USEFUL,
    }
)

_EnumValue = TypeVar("_EnumValue", bound=Enum)


def _enum_value(
    enum_type: type[_EnumValue], value: _EnumValue | str, field_name: str
) -> _EnumValue:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise PilotEvidenceError(f"{field_name} must be an allowlisted value.")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise PilotEvidenceError(f"{field_name} must be an allowlisted value.") from exc


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PilotEvidenceError(f"{field_name} must be an opaque UUID.") from exc
    if parsed.version != 4:
        raise PilotEvidenceError(f"{field_name} must be an opaque UUID.")
    return str(parsed)


def _canonical_version(value: object) -> str:
    if not isinstance(value, str):
        raise PilotEvidenceError("app_version must be a safe version label.")
    candidate = value.strip()
    if candidate == "not_available":
        return candidate
    if not _VERSION_RE.fullmatch(candidate):
        raise PilotEvidenceError("app_version must be a safe version label.")
    return candidate.lower()


def _canonical_build_commit(value: object) -> str:
    if not isinstance(value, str):
        raise PilotEvidenceError("build_commit must be a commit hash or not_available.")
    candidate = value.strip().lower()
    if candidate == "not_available":
        return candidate
    if not _COMMIT_RE.fullmatch(candidate):
        raise PilotEvidenceError("build_commit must be a commit hash or not_available.")
    return candidate


def _canonical_artifact_identity(value: object) -> str:
    if not isinstance(value, str):
        raise PilotEvidenceError(
            "artifact_identity must be a safe package label or hash."
        )
    candidate = value.strip().lower()
    if candidate == "not_available":
        return candidate
    if candidate.startswith("sha256:") and _HASH_RE.fullmatch(candidate[7:]):
        return candidate
    if _PACKAGE_ARTIFACT_RE.fullmatch(candidate):
        return candidate
    raise PilotEvidenceError("artifact_identity must be a safe package label or hash.")


def _canonical_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value.lower()):
        raise PilotEvidenceError(f"{field_name} must be a SHA-256 value.")
    return value.lower()


def _timestamp(value: datetime | None = None) -> str:
    current = datetime.now(timezone.utc) if value is None else value
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise PilotEvidenceError("timestamp must be a timezone-aware UTC time.")
    current = current.astimezone(timezone.utc).replace(microsecond=0)
    if not 2020 <= current.year <= 2100:
        raise PilotEvidenceError("timestamp is outside the bounded pilot range.")
    return current.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PilotEvidenceError(f"{field_name} must be a UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PilotEvidenceError(f"{field_name} must be a UTC timestamp.") from exc
    canonical = _timestamp(parsed)
    if canonical != value:
        raise PilotEvidenceError(
            f"{field_name} must be a bounded whole-second UTC timestamp."
        )
    return canonical


def _limitations(
    value: Iterable[EvidenceLimitation | str] | object,
) -> tuple[EvidenceLimitation, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise PilotEvidenceError("limitations must be an allowlisted sequence.")
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise PilotEvidenceError(
            "limitations must be an allowlisted sequence."
        ) from exc
    if len(items) > MAX_LIMITATIONS_PER_EVENT:
        raise PilotEvidenceError("An observation has too many limitations.")
    normalized = tuple(
        _enum_value(EvidenceLimitation, item, "limitation") for item in items
    )
    if len(set(normalized)) != len(normalized):
        raise PilotEvidenceError("An observation cannot repeat a limitation.")
    return tuple(sorted(normalized, key=lambda item: item.value))


def _event_hash_payload(
    *,
    event_id: str,
    sequence: int,
    run_id: str,
    app_version: str,
    build_commit: str,
    artifact_identity: str,
    role: PilotRole,
    timestamp: str,
    state_before: PilotSessionState,
    state_after: PilotSessionState,
    observation_class: PilotObservationClass,
    result: EvidenceOutcome,
    evidence_reference: EvidenceReference,
    limitations: tuple[EvidenceLimitation, ...],
    previous_hash: str,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "run_id": run_id,
        "app_version": app_version,
        "build_commit": build_commit,
        "artifact_identity": artifact_identity,
        "role": role.value,
        "timestamp_utc": timestamp,
        "state_before": state_before.value,
        "state_after": state_after.value,
        "observation_class": observation_class.value,
        "result": result.value,
        "evidence_reference": evidence_reference.value,
        "limitations": [item.value for item in limitations],
        "previous_event_sha256": previous_hash,
    }


def _event_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PilotEvidenceEvent:
    """One immutable, fully allowlisted pilot observation."""

    event_id: str
    sequence: int
    run_id: str
    app_version: str
    build_commit: str
    artifact_identity: str
    role: PilotRole
    timestamp_utc: str
    state_before: PilotSessionState
    state_after: PilotSessionState
    observation_class: PilotObservationClass
    result: EvidenceOutcome
    evidence_reference: EvidenceReference
    limitations: tuple[EvidenceLimitation, ...]
    previous_event_sha256: str
    event_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _canonical_uuid(self.event_id, "event_id"))
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise PilotEvidenceError("sequence must be a positive integer.")
        object.__setattr__(self, "run_id", _canonical_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "app_version", _canonical_version(self.app_version))
        object.__setattr__(
            self, "build_commit", _canonical_build_commit(self.build_commit)
        )
        object.__setattr__(
            self,
            "artifact_identity",
            _canonical_artifact_identity(self.artifact_identity),
        )
        object.__setattr__(self, "role", _enum_value(PilotRole, self.role, "role"))
        object.__setattr__(
            self, "timestamp_utc", _parse_timestamp(self.timestamp_utc, "timestamp_utc")
        )
        object.__setattr__(
            self,
            "state_before",
            _enum_value(PilotSessionState, self.state_before, "state_before"),
        )
        object.__setattr__(
            self,
            "state_after",
            _enum_value(PilotSessionState, self.state_after, "state_after"),
        )
        object.__setattr__(
            self,
            "observation_class",
            _enum_value(
                PilotObservationClass, self.observation_class, "observation_class"
            ),
        )
        object.__setattr__(
            self, "result", _enum_value(EvidenceOutcome, self.result, "result")
        )
        object.__setattr__(
            self,
            "evidence_reference",
            _enum_value(
                EvidenceReference, self.evidence_reference, "evidence_reference"
            ),
        )
        object.__setattr__(self, "limitations", _limitations(self.limitations))
        object.__setattr__(
            self,
            "previous_event_sha256",
            _canonical_hash(self.previous_event_sha256, "previous_event_sha256"),
        )
        object.__setattr__(
            self, "event_sha256", _canonical_hash(self.event_sha256, "event_sha256")
        )
        if self.event_sha256 != _event_hash(self._hash_payload()):
            raise PilotEvidenceError("Pilot evidence event integrity check failed.")

    def _hash_payload(self) -> dict[str, object]:
        return _event_hash_payload(
            event_id=self.event_id,
            sequence=self.sequence,
            run_id=self.run_id,
            app_version=self.app_version,
            build_commit=self.build_commit,
            artifact_identity=self.artifact_identity,
            role=self.role,
            timestamp=self.timestamp_utc,
            state_before=self.state_before,
            state_after=self.state_after,
            observation_class=self.observation_class,
            result=self.result,
            evidence_reference=self.evidence_reference,
            limitations=self.limitations,
            previous_hash=self.previous_event_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        payload = self._hash_payload()
        payload["event_sha256"] = self.event_sha256
        return payload

    def to_report_dict(self) -> dict[str, object]:
        """Return only the useful, already-sanitized event fields for a report."""

        return {
            "sequence": self.sequence,
            "timestamp_utc": self.timestamp_utc,
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "observation_class": self.observation_class.value,
            "result": self.result.value,
            "evidence_reference": self.evidence_reference.value,
            "limitations": [item.value for item in self.limitations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PilotEvidenceEvent:
        expected = {
            "event_id",
            "sequence",
            "run_id",
            "app_version",
            "build_commit",
            "artifact_identity",
            "role",
            "timestamp_utc",
            "state_before",
            "state_after",
            "observation_class",
            "result",
            "evidence_reference",
            "limitations",
            "previous_event_sha256",
            "event_sha256",
        }
        if set(value) != expected:
            raise PilotEvidenceError(
                "Pilot evidence event contains unsupported fields."
            )
        raw_limitations = value.get("limitations")
        if not isinstance(raw_limitations, list):
            raise PilotEvidenceError("Pilot evidence event limitations must be a list.")
        return cls(
            event_id=value.get("event_id", ""),
            sequence=value.get("sequence", 0),
            run_id=value.get("run_id", ""),
            app_version=value.get("app_version", ""),
            build_commit=value.get("build_commit", ""),
            artifact_identity=value.get("artifact_identity", ""),
            role=value.get("role", ""),
            timestamp_utc=value.get("timestamp_utc", ""),
            state_before=value.get("state_before", ""),
            state_after=value.get("state_after", ""),
            observation_class=value.get("observation_class", ""),
            result=value.get("result", ""),
            evidence_reference=value.get("evidence_reference", ""),
            limitations=tuple(raw_limitations),
            previous_event_sha256=value.get("previous_event_sha256", ""),
            event_sha256=value.get("event_sha256", ""),
        )


def _new_event(
    *,
    sequence: int,
    run_id: str,
    app_version: str,
    build_commit: str,
    artifact_identity: str,
    role: PilotRole,
    timestamp_utc: str,
    state_before: PilotSessionState,
    state_after: PilotSessionState,
    observation_class: PilotObservationClass,
    result: EvidenceOutcome,
    evidence_reference: EvidenceReference,
    limitations: tuple[EvidenceLimitation, ...],
    previous_event_sha256: str,
) -> PilotEvidenceEvent:
    event_id = str(uuid.uuid4())
    payload = _event_hash_payload(
        event_id=event_id,
        sequence=sequence,
        run_id=run_id,
        app_version=app_version,
        build_commit=build_commit,
        artifact_identity=artifact_identity,
        role=role,
        timestamp=timestamp_utc,
        state_before=state_before,
        state_after=state_after,
        observation_class=observation_class,
        result=result,
        evidence_reference=evidence_reference,
        limitations=limitations,
        previous_hash=previous_event_sha256,
    )
    return PilotEvidenceEvent(
        event_id=event_id,
        sequence=sequence,
        run_id=run_id,
        app_version=app_version,
        build_commit=build_commit,
        artifact_identity=artifact_identity,
        role=role,
        timestamp_utc=timestamp_utc,
        state_before=state_before,
        state_after=state_after,
        observation_class=observation_class,
        result=result,
        evidence_reference=evidence_reference,
        limitations=limitations,
        previous_event_sha256=previous_event_sha256,
        event_sha256=_event_hash(payload),
    )


@dataclass(frozen=True)
class PilotEvidenceLedger:
    """The complete bounded evidence for one opaque, local pilot run."""

    run_id: str
    app_version: str
    build_commit: str
    artifact_identity: str
    role: PilotRole
    created_at_utc: str
    updated_at_utc: str
    events: tuple[PilotEvidenceEvent, ...] = ()
    event_chain_head_sha256: str = _CHAIN_ORIGIN

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _canonical_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "app_version", _canonical_version(self.app_version))
        object.__setattr__(
            self, "build_commit", _canonical_build_commit(self.build_commit)
        )
        object.__setattr__(
            self,
            "artifact_identity",
            _canonical_artifact_identity(self.artifact_identity),
        )
        object.__setattr__(self, "role", _enum_value(PilotRole, self.role, "role"))
        object.__setattr__(
            self,
            "created_at_utc",
            _parse_timestamp(self.created_at_utc, "created_at_utc"),
        )
        object.__setattr__(
            self,
            "updated_at_utc",
            _parse_timestamp(self.updated_at_utc, "updated_at_utc"),
        )
        events = tuple(self.events)
        if len(events) > MAX_PILOT_EVENTS:
            raise PilotEvidenceError("Pilot evidence record is full; start a new run.")
        if any(not isinstance(event, PilotEvidenceEvent) for event in events):
            raise PilotEvidenceError("Pilot evidence events must be typed records.")
        previous_hash = _CHAIN_ORIGIN
        previous_timestamp = self.created_at_utc
        for index, event in enumerate(events, start=1):
            if event.sequence != index:
                raise PilotEvidenceError("Pilot evidence sequences must be contiguous.")
            if (
                event.run_id != self.run_id
                or event.app_version != self.app_version
                or event.build_commit != self.build_commit
                or event.artifact_identity != self.artifact_identity
                or event.role != self.role
            ):
                raise PilotEvidenceError(
                    "Pilot evidence event identity does not match its run."
                )
            if event.previous_event_sha256 != previous_hash:
                raise PilotEvidenceError(
                    "Pilot evidence event chain is not append-only."
                )
            if event.timestamp_utc < previous_timestamp:
                raise PilotEvidenceError(
                    "Pilot evidence event timestamps are out of order."
                )
            previous_hash = event.event_sha256
            previous_timestamp = event.timestamp_utc
        if self.updated_at_utc < previous_timestamp:
            raise PilotEvidenceError("Pilot evidence update time is out of order.")
        object.__setattr__(self, "events", events)
        object.__setattr__(
            self,
            "event_chain_head_sha256",
            _canonical_hash(self.event_chain_head_sha256, "event_chain_head_sha256"),
        )
        if self.event_chain_head_sha256 != previous_hash:
            raise PilotEvidenceError("Pilot evidence chain head is invalid.")

    @classmethod
    def create(
        cls,
        *,
        app_version: str,
        build_commit: str,
        artifact_identity: str,
        role: PilotRole | str,
        now: datetime | None = None,
    ) -> PilotEvidenceLedger:
        """Create an unsaved opaque run.  :func:`save_pilot_ledger` persists it."""

        timestamp = _timestamp(now)
        return cls(
            run_id=str(uuid.uuid4()),
            app_version=app_version,
            build_commit=build_commit,
            artifact_identity=artifact_identity,
            role=_enum_value(PilotRole, role, "role"),
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
        )

    def record_observation(
        self,
        observation_class: PilotObservationClass | str,
        result: EvidenceOutcome | str,
        *,
        state_before: PilotSessionState | str,
        state_after: PilotSessionState | str,
        evidence_reference: EvidenceReference | str,
        limitations: Iterable[EvidenceLimitation | str] = (),
        occurred_at: datetime | None = None,
    ) -> PilotEvidenceLedger:
        """Append one automatic observation with no free-form payload.

        Human outcomes intentionally require :meth:`record_human_observation`
        so automated callbacks cannot accidentally imply audibility, playback,
        an external editor, or musical usefulness.
        """

        observation = _enum_value(
            PilotObservationClass, observation_class, "observation_class"
        )
        if observation in _HUMAN_OBSERVATIONS:
            raise PilotEvidenceError(
                "Human outcomes require record_human_observation; do not infer them."
            )
        return self._append(
            observation_class=observation,
            result=_enum_value(EvidenceOutcome, result, "result"),
            state_before=_enum_value(PilotSessionState, state_before, "state_before"),
            state_after=_enum_value(PilotSessionState, state_after, "state_after"),
            evidence_reference=_enum_value(
                EvidenceReference, evidence_reference, "evidence_reference"
            ),
            limitations=_limitations(limitations),
            occurred_at=occurred_at,
        )

    def record_human_observation(
        self,
        observation_class: PilotObservationClass | str,
        result: EvidenceOutcome | str,
        *,
        state_before: PilotSessionState | str,
        state_after: PilotSessionState | str,
        limitations: Iterable[EvidenceLimitation | str] = (),
        occurred_at: datetime | None = None,
    ) -> PilotEvidenceLedger:
        """Append an explicit human answer without retaining names or notes."""

        observation = _enum_value(
            PilotObservationClass, observation_class, "observation_class"
        )
        if observation not in _HUMAN_OBSERVATIONS:
            raise PilotEvidenceError(
                "Only explicitly human observation classes belong in this method."
            )
        return self._append(
            observation_class=observation,
            result=_enum_value(EvidenceOutcome, result, "result"),
            state_before=_enum_value(PilotSessionState, state_before, "state_before"),
            state_after=_enum_value(PilotSessionState, state_after, "state_after"),
            evidence_reference=EvidenceReference.HUMAN_CONFIRMATION,
            limitations=_limitations(limitations),
            occurred_at=occurred_at,
        )

    def _append(
        self,
        *,
        observation_class: PilotObservationClass,
        result: EvidenceOutcome,
        state_before: PilotSessionState,
        state_after: PilotSessionState,
        evidence_reference: EvidenceReference,
        limitations: tuple[EvidenceLimitation, ...],
        occurred_at: datetime | None,
    ) -> PilotEvidenceLedger:
        if len(self.events) >= MAX_PILOT_EVENTS:
            raise PilotEvidenceError("Pilot evidence record is full; start a new run.")
        timestamp = _timestamp(occurred_at)
        # A clock correction must not make the append-only order ambiguous.
        timestamp = max(timestamp, self.updated_at_utc)
        event = _new_event(
            sequence=len(self.events) + 1,
            run_id=self.run_id,
            app_version=self.app_version,
            build_commit=self.build_commit,
            artifact_identity=self.artifact_identity,
            role=self.role,
            timestamp_utc=timestamp,
            state_before=state_before,
            state_after=state_after,
            observation_class=observation_class,
            result=result,
            evidence_reference=evidence_reference,
            limitations=limitations,
            previous_event_sha256=self.event_chain_head_sha256,
        )
        return PilotEvidenceLedger(
            run_id=self.run_id,
            app_version=self.app_version,
            build_commit=self.build_commit,
            artifact_identity=self.artifact_identity,
            role=self.role,
            created_at_utc=self.created_at_utc,
            updated_at_utc=timestamp,
            events=self.events + (event,),
            event_chain_head_sha256=event.event_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the strict local format; it deliberately has no path fields."""

        return {
            "schema_version": PILOT_EVIDENCE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "app_version": self.app_version,
            "build_commit": self.build_commit,
            "artifact_identity": self.artifact_identity,
            "role": self.role.value,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "event_chain_head_sha256": self.event_chain_head_sha256,
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PilotEvidenceLedger:
        expected = {
            "schema_version",
            "run_id",
            "app_version",
            "build_commit",
            "artifact_identity",
            "role",
            "created_at_utc",
            "updated_at_utc",
            "event_chain_head_sha256",
            "events",
        }
        if (
            set(value) != expected
            or value.get("schema_version") != PILOT_EVIDENCE_SCHEMA_VERSION
        ):
            raise PilotEvidenceError("Pilot evidence record has an unsupported schema.")
        raw_events = value.get("events")
        if not isinstance(raw_events, list) or len(raw_events) > MAX_PILOT_EVENTS:
            raise PilotEvidenceError("Pilot evidence record has invalid events.")
        if any(not isinstance(item, Mapping) for item in raw_events):
            raise PilotEvidenceError("Pilot evidence record contains an invalid event.")
        return cls(
            run_id=value.get("run_id", ""),
            app_version=value.get("app_version", ""),
            build_commit=value.get("build_commit", ""),
            artifact_identity=value.get("artifact_identity", ""),
            role=value.get("role", ""),
            created_at_utc=value.get("created_at_utc", ""),
            updated_at_utc=value.get("updated_at_utc", ""),
            events=tuple(PilotEvidenceEvent.from_dict(item) for item in raw_events),
            event_chain_head_sha256=value.get("event_chain_head_sha256", ""),
        )

    def sanitized_report(self) -> dict[str, object]:
        """Build a local, export-safe report with no secrets or raw identifiers."""

        counts = {outcome.value: 0 for outcome in EvidenceOutcome}
        for event in self.events:
            counts[event.result.value] += 1
        return {
            "schema_version": PILOT_EVIDENCE_SCHEMA_VERSION,
            "report_kind": "webjam_private_pilot_evidence",
            "privacy": {
                "storage": "local_only",
                "collection": "allowlist_only",
                "audio_included": False,
                "invites_included": False,
                "credentials_included": False,
                "network_addresses_included": False,
                "device_identifiers_included": False,
                "paths_included": False,
                "names_or_notes_included": False,
            },
            "run": {
                "run_id": self.run_id,
                "role": self.role.value,
                "app_version": self.app_version,
                "build_commit": self.build_commit,
                "artifact_identity": self.artifact_identity,
                "created_at_utc": self.created_at_utc,
                "updated_at_utc": self.updated_at_utc,
            },
            "summary": {
                "event_count": len(self.events),
                "outcome_counts": counts,
                "ever_failed": counts[EvidenceOutcome.FAILED.value] > 0,
                "has_blocked": counts[EvidenceOutcome.BLOCKED.value] > 0,
                "has_indeterminate": counts[EvidenceOutcome.INDETERMINATE.value] > 0,
                "has_not_run": counts[EvidenceOutcome.NOT_RUN.value] > 0,
                "event_chain_valid": True,
                "event_chain_head_sha256": self.event_chain_head_sha256,
            },
            "events": [event.to_report_dict() for event in self.events],
        }

    def render_sanitized_summary(self) -> str:
        """Render a concise human-readable summary from :meth:`sanitized_report`."""

        report = self.sanitized_report()
        run = report["run"]
        summary = report["summary"]
        assert isinstance(run, dict) and isinstance(summary, dict)
        counts = summary["outcome_counts"]
        assert isinstance(counts, dict)
        lines = [
            "WebJam private pilot evidence (local-only)",
            f"Run: {run['run_id']} ({run['role']})",
            f"Package: {run['app_version']} / {run['build_commit']} / {run['artifact_identity']}",
            f"Events: {summary['event_count']}",
            "Results: "
            + ", ".join(
                f"{outcome.value}={counts[outcome.value]}"
                for outcome in EvidenceOutcome
            ),
            "Earlier failures preserved: "
            + ("yes" if summary["ever_failed"] else "no"),
            "No audio, invites, credentials, addresses, device IDs, paths, names, or notes are included.",
        ]
        # The report is allowlist-only already.  Reusing the shared redactor
        # keeps this text surface safe if a future report line gains a value.
        return "\n".join(redact_text(line) for line in lines) + "\n"


def create_pilot_ledger(
    *,
    app_version: str,
    build_commit: str,
    artifact_identity: str,
    role: PilotRole | str,
    now: datetime | None = None,
) -> PilotEvidenceLedger:
    """Convenience wrapper for :meth:`PilotEvidenceLedger.create`."""

    return PilotEvidenceLedger.create(
        app_version=app_version,
        build_commit=build_commit,
        artifact_identity=artifact_identity,
        role=role,
        now=now,
    )


def pilot_ledger_path(storage_dir: str | Path, run_id: str) -> Path:
    """Return the deterministic local path for an opaque run ID.

    The path is intentionally never embedded in a ledger or its report.
    """

    canonical_run_id = _canonical_uuid(run_id, "run_id")
    return (
        Path(storage_dir).expanduser()
        / PILOT_EVIDENCE_DIRECTORY
        / f"{canonical_run_id}.json"
    )


def _prepare_ledger_directory(storage_dir: str | Path) -> Path:
    base = Path(storage_dir).expanduser()
    try:
        base.lstat()
    except FileNotFoundError:
        try:
            base.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise PilotEvidenceError(
                "Could not create local pilot evidence storage."
            ) from exc
    except OSError as exc:
        raise PilotEvidenceError(
            "Could not inspect local pilot evidence storage."
        ) from exc
    if base.is_symlink() or not base.is_dir():
        raise PilotEvidenceError("Local pilot evidence storage must be a directory.")
    directory = base / PILOT_EVIDENCE_DIRECTORY
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PilotEvidenceError(
            "Could not create local pilot evidence storage."
        ) from exc
    if directory.is_symlink() or not directory.is_dir():
        raise PilotEvidenceError("Local pilot evidence storage must be a directory.")
    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise PilotEvidenceError(
            "Could not protect local pilot evidence storage."
        ) from exc
    return directory


def _read_ledger_file(path: Path) -> PilotEvidenceLedger:
    if path.is_symlink():
        raise PilotEvidenceError("Pilot evidence record must not be a symbolic link.")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise PilotEvidenceError("Pilot evidence record must be a regular file.")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PilotEvidenceError(
                "Pilot evidence record permissions are not private."
            )
        with path.open("rb") as handle:
            payload = handle.read(MAX_PILOT_LEDGER_BYTES + 1)
    except OSError as exc:
        raise PilotEvidenceError("Could not read local pilot evidence.") from exc
    if len(payload) > MAX_PILOT_LEDGER_BYTES:
        raise PilotEvidenceError("Pilot evidence record is too large.")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PilotEvidenceError("Pilot evidence record is not valid JSON.") from exc
    if not isinstance(decoded, Mapping):
        raise PilotEvidenceError("Pilot evidence record root must be an object.")
    return PilotEvidenceLedger.from_dict(decoded)


def _assert_append_only(
    existing: PilotEvidenceLedger, replacement: PilotEvidenceLedger
) -> None:
    if (
        existing.run_id != replacement.run_id
        or existing.app_version != replacement.app_version
        or existing.build_commit != replacement.build_commit
        or existing.artifact_identity != replacement.artifact_identity
        or existing.role != replacement.role
        or existing.created_at_utc != replacement.created_at_utc
    ):
        raise PilotEvidenceError(
            "Pilot evidence run identity cannot change after creation."
        )
    if len(replacement.events) < len(existing.events):
        raise PilotEvidenceError("Pilot evidence events cannot be removed.")
    if replacement.events[: len(existing.events)] != existing.events:
        raise PilotEvidenceError(
            "Pilot evidence events must append without rewriting history."
        )
    if len(replacement.events) == len(existing.events) and replacement != existing:
        raise PilotEvidenceError(
            "Pilot evidence record cannot be rewritten without an event."
        )


def save_pilot_ledger(storage_dir: str | Path, ledger: PilotEvidenceLedger) -> Path:
    """Atomically persist a ledger with mode ``0600`` and append-only checks."""

    if not isinstance(ledger, PilotEvidenceLedger):
        raise PilotEvidenceError("ledger must be a PilotEvidenceLedger value.")
    directory = _prepare_ledger_directory(storage_dir)
    path = directory / f"{ledger.run_id}.json"
    try:
        path.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise PilotEvidenceError("Could not inspect local pilot evidence.") from exc
    else:
        existing = _read_ledger_file(path)
    if existing is not None:
        _assert_append_only(existing, ledger)
    payload = json.dumps(ledger.to_dict(), indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, payload, mode=0o600)
    return path


def load_pilot_ledger(storage_dir: str | Path, run_id: str) -> PilotEvidenceLedger:
    """Resume an opaque local run after an app restart without rewriting it."""

    directory = _prepare_ledger_directory(storage_dir)
    path = directory / f"{_canonical_uuid(run_id, 'run_id')}.json"
    return _read_ledger_file(path)


def resume_pilot_ledger(storage_dir: str | Path, run_id: str) -> PilotEvidenceLedger:
    """Named alias for callers that are restoring a paused pilot run."""

    return load_pilot_ledger(storage_dir, run_id)


def list_pilot_ledgers(storage_dir: str | Path) -> tuple[PilotEvidenceLedger, ...]:
    """Return verified local pilot runs newest first for an operator resume UI.

    Enumeration remains bounded and validates every candidate with the same
    private-file, schema, and hash-chain checks used for an explicit resume.
    A malformed entry is surfaced as a safe error rather than skipped, so a
    corrupted run cannot silently disappear from a closed-pilot record.
    """

    directory = _prepare_ledger_directory(storage_dir)
    try:
        paths = tuple(sorted(directory.glob("*.json")))
    except OSError as exc:
        raise PilotEvidenceError("Could not inspect local pilot evidence.") from exc
    if len(paths) > MAX_PILOT_LEDGER_FILES:
        raise PilotEvidenceError("Too many local pilot evidence records to resume.")
    ledgers: list[PilotEvidenceLedger] = []
    for path in paths:
        if path.stem != _canonical_uuid(path.stem, "run_id"):
            raise PilotEvidenceError("Pilot evidence record has an invalid name.")
        ledgers.append(_read_ledger_file(path))
    return tuple(
        sorted(
            ledgers,
            key=lambda ledger: (ledger.updated_at_utc, ledger.run_id),
            reverse=True,
        )
    )


def build_sanitized_pilot_report(ledger: PilotEvidenceLedger) -> dict[str, object]:
    """Return the same allowlisted report used by a future export surface."""

    if not isinstance(ledger, PilotEvidenceLedger):
        raise PilotEvidenceError("ledger must be a PilotEvidenceLedger value.")
    return ledger.sanitized_report()


__all__ = [
    "MAX_LIMITATIONS_PER_EVENT",
    "MAX_PILOT_EVENTS",
    "MAX_PILOT_LEDGER_BYTES",
    "MAX_PILOT_LEDGER_FILES",
    "PILOT_EVIDENCE_DIRECTORY",
    "PILOT_EVIDENCE_SCHEMA_VERSION",
    "EvidenceLimitation",
    "EvidenceOutcome",
    "EvidenceReference",
    "PilotEvidenceError",
    "PilotEvidenceEvent",
    "PilotEvidenceLedger",
    "PilotObservationClass",
    "PilotRole",
    "PilotSessionState",
    "build_sanitized_pilot_report",
    "create_pilot_ledger",
    "list_pilot_ledgers",
    "load_pilot_ledger",
    "pilot_ledger_path",
    "resume_pilot_ledger",
    "save_pilot_ledger",
]

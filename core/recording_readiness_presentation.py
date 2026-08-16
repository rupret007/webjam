"""Immutable, path-free presentation model for Record Session readiness.

Recorder authority lives elsewhere.  This module deliberately contains only the
bounded facts a pre-record surface may display: exact logical sources, their
channel topology and obligation, storage truth, Shared Track truth, and safe
musician-facing blockers.  It does not carry paths, device identifiers, RPC
credentials, recording-plan fingerprints, or mutable service objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable

from core.redaction import REDACTED_PATH, redact_log_text


MAX_READINESS_SOURCES = 512
MAX_READINESS_BLOCKERS = 32
_MAX_ID_CHARS = 128
_MAX_LABEL_CHARS = 120
_MAX_SUMMARY_CHARS = 180
_MAX_DETAIL_CHARS = 320
_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class RecordingReadinessModelError(ValueError):
    """Raised when a UI snapshot is ambiguous, unsafe, or unbounded."""


class RecordingSourceKind(str, Enum):
    SERVER = "server"
    LOCAL_ORIGINAL = "local_original"
    SHARED_TRACK = "shared_track"

    @property
    def label(self) -> str:
        return {
            RecordingSourceKind.SERVER: "Server track",
            RecordingSourceKind.LOCAL_ORIGINAL: "Local Original",
            RecordingSourceKind.SHARED_TRACK: "Shared Track",
        }[self]


class RecordingChannelTopology(str, Enum):
    MONO = "mono"
    STEREO = "stereo"

    @property
    def channels(self) -> int:
        return 1 if self is RecordingChannelTopology.MONO else 2

    @property
    def label(self) -> str:
        return "Mono" if self is RecordingChannelTopology.MONO else "Stereo"


class RecordingSourceReadiness(str, Enum):
    READY = "ready"
    CHECKING = "checking"
    ACTION_NEEDED = "action_needed"

    @property
    def label(self) -> str:
        return {
            RecordingSourceReadiness.READY: "Ready",
            RecordingSourceReadiness.CHECKING: "Checking…",
            RecordingSourceReadiness.ACTION_NEEDED: "Action needed",
        }[self]


class RecordingStorageReadiness(str, Enum):
    READY = "ready"
    WARNING = "warning"
    CHECKING = "checking"
    ACTION_NEEDED = "action_needed"

    @property
    def label(self) -> str:
        return {
            RecordingStorageReadiness.READY: "Ready",
            RecordingStorageReadiness.WARNING: "Low storage",
            RecordingStorageReadiness.CHECKING: "Checking…",
            RecordingStorageReadiness.ACTION_NEEDED: "Action needed",
        }[self]


class SharedTrackReadiness(str, Enum):
    NOT_INCLUDED = "not_included"
    READY = "ready"
    CHECKING = "checking"
    ACTION_NEEDED = "action_needed"

    @property
    def label(self) -> str:
        return {
            SharedTrackReadiness.NOT_INCLUDED: "Not included",
            SharedTrackReadiness.READY: "Ready",
            SharedTrackReadiness.CHECKING: "Checking…",
            SharedTrackReadiness.ACTION_NEEDED: "Action needed",
        }[self]


def _enum_value(value: object, enum_type: type[Enum], field: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise RecordingReadinessModelError(f"{field} is unsupported.") from exc


def _safe_text(
    value: object,
    *,
    fallback: str,
    limit: int,
) -> str:
    if not isinstance(value, str):
        raise TypeError("readiness presentation text must be a string")
    normalized = " ".join(value.split())
    safe = redact_log_text(normalized)
    if not safe or safe == REDACTED_PATH:
        safe = fallback
    return safe[:limit]


@dataclass(frozen=True, repr=False)
class RecordingReadinessSource:
    """One exact logical source row safe to render before recording."""

    source_id: str
    participant_label: str
    source_label: str
    kind: RecordingSourceKind
    topology: RecordingChannelTopology
    required: bool
    readiness: RecordingSourceReadiness
    detail: str = ""
    meter_percent: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str):
            raise TypeError("source_id must be a string")
        source_id = self.source_id
        if len(source_id) > _MAX_ID_CHARS or _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise RecordingReadinessModelError(
                "source_id must be a bounded opaque identifier."
            )
        if not isinstance(self.required, bool):
            raise TypeError("required must be true or false")
        if self.meter_percent is not None and (
            isinstance(self.meter_percent, bool)
            or not isinstance(self.meter_percent, int)
            or not 0 <= self.meter_percent <= 100
        ):
            raise RecordingReadinessModelError(
                "meter_percent must be an integer from 0 through 100."
            )
        kind = _enum_value(self.kind, RecordingSourceKind, "source kind")
        topology = _enum_value(
            self.topology,
            RecordingChannelTopology,
            "source topology",
        )
        readiness = _enum_value(
            self.readiness,
            RecordingSourceReadiness,
            "source readiness",
        )
        fallback_detail = {
            RecordingSourceReadiness.READY: "Source is ready.",
            RecordingSourceReadiness.CHECKING: "Source readiness is being checked.",
            RecordingSourceReadiness.ACTION_NEEDED: "This source needs attention.",
        }[readiness]
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(
            self,
            "participant_label",
            _safe_text(
                self.participant_label,
                fallback="Participant",
                limit=_MAX_LABEL_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "source_label",
            _safe_text(
                self.source_label,
                fallback="Recording source",
                limit=_MAX_LABEL_CHARS,
            ),
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self,
            "detail",
            _safe_text(
                self.detail,
                fallback=fallback_detail,
                limit=_MAX_DETAIL_CHARS,
            ),
        )

    @property
    def blocks_start(self) -> bool:
        return bool(
            self.required and self.readiness is not RecordingSourceReadiness.READY
        )

    @property
    def obligation_label(self) -> str:
        return "Required" if self.required else "Optional"

    @property
    def accessible_description(self) -> str:
        meter = (
            f"Meter {self.meter_percent} percent."
            if self.meter_percent is not None
            else "Meter unavailable."
        )
        return (
            f"{self.participant_label}; {self.source_label}. {self.kind.label}. "
            f"{self.topology.label}. {self.obligation_label}. "
            f"{self.readiness.label}. {meter} {self.detail}"
        )

    def __repr__(self) -> str:
        return "RecordingReadinessSource(private=[redacted])"


@dataclass(frozen=True, repr=False)
class RecordingStoragePresentation:
    readiness: RecordingStorageReadiness
    summary: str
    detail: str = ""

    def __post_init__(self) -> None:
        readiness = _enum_value(
            self.readiness,
            RecordingStorageReadiness,
            "storage readiness",
        )
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self,
            "summary",
            _safe_text(
                self.summary,
                fallback="Recording storage",
                limit=_MAX_SUMMARY_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "detail",
            _safe_text(
                self.detail,
                fallback="Storage readiness is unavailable.",
                limit=_MAX_DETAIL_CHARS,
            ),
        )

    @property
    def blocks_start(self) -> bool:
        return self.readiness in {
            RecordingStorageReadiness.CHECKING,
            RecordingStorageReadiness.ACTION_NEEDED,
        }

    def __repr__(self) -> str:
        return "RecordingStoragePresentation(private=[redacted])"


@dataclass(frozen=True, repr=False)
class SharedTrackPresentation:
    readiness: SharedTrackReadiness
    required: bool
    summary: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise TypeError("required must be true or false")
        readiness = _enum_value(
            self.readiness,
            SharedTrackReadiness,
            "Shared Track readiness",
        )
        if self.required and readiness is SharedTrackReadiness.NOT_INCLUDED:
            raise RecordingReadinessModelError(
                "a required Shared Track cannot be marked not included."
            )
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self,
            "summary",
            _safe_text(
                self.summary,
                fallback="Shared Track",
                limit=_MAX_SUMMARY_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "detail",
            _safe_text(
                self.detail,
                fallback="Shared Track readiness is unavailable.",
                limit=_MAX_DETAIL_CHARS,
            ),
        )

    @property
    def blocks_start(self) -> bool:
        return bool(self.required and self.readiness is not SharedTrackReadiness.READY)

    def __repr__(self) -> str:
        return "SharedTrackPresentation(private=[redacted])"


def _deduplicated(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
        if len(result) >= MAX_READINESS_BLOCKERS:
            break
    return tuple(result)


@dataclass(frozen=True, repr=False)
class RecordingReadinessPresentation:
    """One immutable pre-record UI snapshot.

    ``can_start`` is derived exclusively from this snapshot.  A controller must
    still compare its private plan/generation when Start is accepted; this
    presentation is not recording authority.
    """

    profile_label: str
    sources: tuple[RecordingReadinessSource, ...]
    storage: RecordingStoragePresentation
    shared_track: SharedTrackPresentation
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        if len(sources) > MAX_READINESS_SOURCES:
            raise RecordingReadinessModelError("too many readiness sources")
        if any(not isinstance(source, RecordingReadinessSource) for source in sources):
            raise TypeError("sources must contain RecordingReadinessSource values")
        source_ids = tuple(source.source_id for source in sources)
        if len(source_ids) != len(set(source_ids)):
            raise RecordingReadinessModelError("source_id values must be unique")
        if not isinstance(self.storage, RecordingStoragePresentation):
            raise TypeError("storage must be a RecordingStoragePresentation")
        if not isinstance(self.shared_track, SharedTrackPresentation):
            raise TypeError("shared_track must be a SharedTrackPresentation")
        blockers = tuple(self.blockers)
        if len(blockers) > MAX_READINESS_BLOCKERS:
            raise RecordingReadinessModelError("too many readiness blockers")
        safe_blockers = tuple(
            _safe_text(
                value,
                fallback="Recording readiness needs attention.",
                limit=_MAX_DETAIL_CHARS,
            )
            for value in blockers
        )
        object.__setattr__(
            self,
            "profile_label",
            _safe_text(
                self.profile_label,
                fallback="Creator",
                limit=_MAX_LABEL_CHARS,
            ),
        )
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "blockers", _deduplicated(safe_blockers))

    @property
    def effective_blockers(self) -> tuple[str, ...]:
        derived: list[str] = list(self.blockers)
        if not self.sources:
            derived.append("No recording sources are available for this take.")
        for source in self.sources:
            if source.blocks_start:
                derived.append(
                    f"{source.participant_label} · {source.source_label}: "
                    f"{source.detail}"
                )
        if self.storage.blocks_start:
            derived.append(self.storage.detail)
        if self.shared_track.blocks_start:
            derived.append(self.shared_track.detail)
        return _deduplicated(derived)

    @property
    def can_start(self) -> bool:
        return not self.effective_blockers

    @property
    def ready_source_count(self) -> int:
        return sum(
            source.readiness is RecordingSourceReadiness.READY
            for source in self.sources
        )

    @property
    def accessible_description(self) -> str:
        status = (
            "Ready to start recording."
            if self.can_start
            else f"Recording is blocked by {len(self.effective_blockers)} item(s)."
        )
        return (
            f"{self.profile_label} Record Session readiness. "
            f"{self.ready_source_count} of {len(self.sources)} sources ready. "
            f"Storage {self.storage.readiness.label}. Shared Track "
            f"{self.shared_track.readiness.label}. {status}"
        )

    def __repr__(self) -> str:
        return (
            "RecordingReadinessPresentation("
            f"sources={len(self.sources)}, blockers={len(self.effective_blockers)}, "
            "private=[redacted])"
        )


__all__ = [
    "MAX_READINESS_BLOCKERS",
    "MAX_READINESS_SOURCES",
    "RecordingChannelTopology",
    "RecordingReadinessModelError",
    "RecordingReadinessPresentation",
    "RecordingReadinessSource",
    "RecordingSourceKind",
    "RecordingSourceReadiness",
    "RecordingStoragePresentation",
    "RecordingStorageReadiness",
    "SharedTrackPresentation",
    "SharedTrackReadiness",
]

"""Versioned, non-destructive project truth for recorded WebJam takes.

The v1 manifest was a useful post-recording receipt, but filenames and mutable
display names were doing too many jobs.  This module gives recording,
reconnect, Studio, and export one dependency-light model with durable IDs,
explicit media segments, disclosed gaps, and separate automatic/manual
alignment metadata.

Nothing here edits source audio.  Loading a legacy manifest is intentionally
read-only; callers decide when to publish a migrated schema-v2 manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Iterable, Mapping

from core.redaction import redact_text


PROJECT_SCHEMA_VERSION = 2
_MIGRATION_NAMESPACE = uuid.UUID("f1203a8a-b035-4fe0-8a48-1c5b23d78d33")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_LOCKS_GUARD = threading.Lock()
_MANIFEST_LOCKS: dict[Path, threading.RLock] = {}


class TakeProjectError(ValueError):
    """Raised when a project manifest cannot be trusted or represented."""


class TakeProjectConflict(TakeProjectError):
    """Raised when a writer tries to replace a newer take-project revision."""


def _take_project_manifest_path(take_dir: str | Path) -> Path:
    """Return one stable, process-local lock identity for a project manifest."""

    return Path(take_dir).expanduser().resolve() / "webjam-take.json"


@contextmanager
def take_project_manifest_lock(take_dir: str | Path) -> Iterator[Path]:
    """Serialize short in-process project-manifest writes for one take.

    This deliberately protects only metadata publication. Callers must perform
    media hashing, copying, and timing analysis before acquiring the lock.
    Atomic replacement still protects readers; the lock prevents two WebJam
    writers from silently winning a read-modify-write race in this process.
    """

    manifest = _take_project_manifest_path(take_dir)
    with _MANIFEST_LOCKS_GUARD:
        lock = _MANIFEST_LOCKS.setdefault(manifest, threading.RLock())
    with lock:
        yield manifest


def replace_take_project_manifest_if_unchanged(
    take_dir: str | Path,
    *,
    expected_bytes: bytes,
    payload: Mapping[str, Any],
) -> bool:
    """Atomically replace a manifest only when its exact snapshot still wins.

    The caller supplies a payload derived from ``expected_bytes``. A false
    result means another cooperative WebJam writer published newer project
    truth, so the caller must reload and merge/retry rather than overwrite it.
    """

    from core.file_io import atomic_write_text

    serialized = json.dumps(dict(payload), indent=2, sort_keys=False) + "\n"
    with take_project_manifest_lock(take_dir) as manifest:
        try:
            current = manifest.read_bytes()
        except OSError:
            return False
        if current != bytes(expected_bytes):
            return False
        atomic_write_text(manifest, serialized, mode=0o600)
    return True


class ProjectStatus(str, Enum):
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"
    RECOVERED = "recovered"


class SourceType(str, Enum):
    LOCAL_ISOLATED = "local_isolated"
    JAMULUS_SERVER = "jamulus_server"
    LIVE_REFERENCE = "live_reference"
    STUDIO_MIX = "studio_mix"
    PROCESSED_STEM = "processed_stem"
    UNKNOWN = "unknown"


class MediaStatus(str, Enum):
    AVAILABLE = "available"
    TRANSFERRING = "transferring"
    MISSING = "missing"
    DAMAGED = "damaged"
    PARTIAL = "partial"
    RECOVERED = "recovered"
    TRANSFER_FAILED = "transfer_failed"


class SourceQuality(str, Enum):
    VERIFIED_ISOLATED = "verified_isolated"
    NETWORK_TRACK = "network_track"
    REFERENCE = "reference"
    PROCESSED = "processed"
    UNVERIFIED = "unverified"


class RecoveryStatus(str, Enum):
    """Whether session evidence says a take needed recovery attention."""

    NOT_NEEDED = "not_needed"
    RECOVERED = "recovered"
    NEEDS_ATTENTION = "needs_attention"


def new_project_id() -> str:
    """Return a canonical opaque ID suitable for any project entity."""
    return str(uuid.uuid4())


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TakeProjectError(f"{field_name} must be a UUID.") from exc
    return str(parsed)


def _finite_float(value: object, field_name: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TakeProjectError(f"{field_name} must be a number.") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        qualifier = f" at least {minimum:g}" if minimum is not None else " finite"
        raise TakeProjectError(f"{field_name} must be{qualifier}.")
    return result


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise TakeProjectError(f"{field_name} must be a non-negative integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TakeProjectError(f"{field_name} must be a non-negative integer.") from exc
    if result < 0 or result != value:
        raise TakeProjectError(f"{field_name} must be a non-negative integer.")
    return result


def _relative_media_path(value: object) -> str:
    text = str(value or "").strip()
    if not text or "\\" in text:
        raise TakeProjectError("segment path must be a relative POSIX media path.")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TakeProjectError("segment path must stay inside the take directory.")
    return path.as_posix()


def _enum_value(enum_type, value: object, field_name: str):
    try:
        return enum_type(str(value))
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise TakeProjectError(f"{field_name} must be one of: {choices}.") from exc


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _safe_session_text(value: object, limit: int) -> str:
    """Bound and redact the free-text fields retained with a take."""
    text = redact_text(str(value or ""))
    # A recording manifest needs no invite context, even in redacted form.
    text = re.sub(r"(?i)\bwebjam:(?://)?\[redacted\]", "private invite", text)
    return " ".join(text.split())[:limit]


@dataclass(frozen=True)
class GapInterval:
    """A half-open source-frame interval represented by silence or absence."""

    start_frame: int
    frame_count: int
    reason: str
    channels: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "start_frame", _nonnegative_int(self.start_frame, "gap.start_frame")
        )
        object.__setattr__(
            self, "frame_count", _nonnegative_int(self.frame_count, "gap.frame_count")
        )
        if self.frame_count <= 0:
            raise TakeProjectError("gap.frame_count must be greater than zero.")
        reason = str(self.reason or "").strip()
        if not reason:
            raise TakeProjectError("gap.reason is required.")
        object.__setattr__(self, "reason", reason[:120])
        clean_channels = tuple(
            _nonnegative_int(item, "gap.channels") for item in self.channels
        )
        object.__setattr__(self, "channels", clean_channels)

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_frame": self.start_frame,
            "frame_count": self.frame_count,
            "reason": self.reason,
            "channels": list(self.channels),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GapInterval":
        return cls(
            start_frame=value.get("start_frame", -1),
            frame_count=value.get("frame_count", 0),
            reason=value.get("reason", ""),
            channels=tuple(value.get("channels", ()))
            if isinstance(value.get("channels", ()), (list, tuple))
            else (),
        )


@dataclass(frozen=True)
class CaptureDevice:
    """The local hardware/backend configuration that produced a segment."""

    device_id: str
    display_name: str
    backend: str
    sample_rate: int
    channel_indices: tuple[int, ...]
    channel_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        device_id = str(self.device_id or "").strip()
        if not device_id:
            raise TakeProjectError("device.device_id is required.")
        object.__setattr__(self, "device_id", device_id[:256])
        object.__setattr__(self, "display_name", str(self.display_name or "").strip()[:160])
        object.__setattr__(self, "backend", str(self.backend or "").strip()[:80])
        sample_rate = _nonnegative_int(self.sample_rate, "device.sample_rate")
        if sample_rate <= 0:
            raise TakeProjectError("device.sample_rate must be greater than zero.")
        object.__setattr__(self, "sample_rate", sample_rate)
        indices = tuple(
            _nonnegative_int(item, "device.channel_indices")
            for item in self.channel_indices
        )
        if not indices:
            raise TakeProjectError("device.channel_indices must identify a source channel.")
        object.__setattr__(self, "channel_indices", indices)
        labels = tuple(str(item).strip()[:80] for item in self.channel_labels)
        if labels and len(labels) != len(indices):
            raise TakeProjectError(
                "device.channel_labels must match device.channel_indices."
            )
        object.__setattr__(self, "channel_labels", labels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "display_name": self.display_name,
            "backend": self.backend,
            "sample_rate": self.sample_rate,
            "channel_indices": list(self.channel_indices),
            "channel_labels": list(self.channel_labels),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaptureDevice":
        return cls(
            device_id=value.get("device_id", ""),
            display_name=value.get("display_name", ""),
            backend=value.get("backend", ""),
            sample_rate=value.get("sample_rate", 0),
            channel_indices=tuple(value.get("channel_indices", ()))
            if isinstance(value.get("channel_indices", ()), (list, tuple))
            else (),
            channel_labels=tuple(value.get("channel_labels", ()))
            if isinstance(value.get("channel_labels", ()), (list, tuple))
            else (),
        )


@dataclass(frozen=True)
class AlignmentAnchor:
    source_time_s: float
    project_time_s: float
    residual_ms: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_time_s",
            _finite_float(self.source_time_s, "alignment.anchor.source_time_s", minimum=0),
        )
        object.__setattr__(
            self,
            "project_time_s",
            _finite_float(self.project_time_s, "alignment.anchor.project_time_s", minimum=0),
        )
        object.__setattr__(
            self,
            "residual_ms",
            _finite_float(self.residual_ms, "alignment.anchor.residual_ms"),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "source_time_s": self.source_time_s,
            "project_time_s": self.project_time_s,
            "residual_ms": self.residual_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AlignmentAnchor":
        return cls(
            source_time_s=value.get("source_time_s", 0.0),
            project_time_s=value.get("project_time_s", 0.0),
            residual_ms=value.get("residual_ms", 0.0),
        )


@dataclass(frozen=True)
class AlignmentState:
    """Non-destructive alignment result plus an independent manual nudge."""

    automatic_offset_s: float = 0.0
    manual_nudge_s: float = 0.0
    drift_ppm: float = 0.0
    confidence: float = 0.0
    method: str = "unverified"
    residual_ms: float = 0.0
    anchors: tuple[AlignmentAnchor, ...] = ()
    # A peer original may rely on a different musician's immutable server
    # capture only when these fields identify the exact same-participant
    # reference track and its declared segment fingerprint. A LIVE_REFERENCE
    # track uses the fingerprint alone for the exact uploaded source bytes so
    # Studio never treats a replacement song as another take lane. Empty
    # values retain manifest readability; provenance-sensitive operations use
    # them as a fail-closed gate.
    reference_track_id: str = ""
    reference_fingerprint_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "automatic_offset_s",
            _finite_float(self.automatic_offset_s, "alignment.automatic_offset_s"),
        )
        object.__setattr__(
            self,
            "manual_nudge_s",
            _finite_float(self.manual_nudge_s, "alignment.manual_nudge_s"),
        )
        object.__setattr__(
            self, "drift_ppm", _finite_float(self.drift_ppm, "alignment.drift_ppm")
        )
        confidence = _finite_float(self.confidence, "alignment.confidence", minimum=0)
        if confidence > 1.0:
            raise TakeProjectError("alignment.confidence cannot exceed 1.0.")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "method", str(self.method or "unverified").strip()[:120])
        object.__setattr__(
            self,
            "residual_ms",
            _finite_float(self.residual_ms, "alignment.residual_ms", minimum=0),
        )
        object.__setattr__(self, "anchors", tuple(self.anchors))
        reference_track_id = str(self.reference_track_id or "").strip()
        if reference_track_id:
            reference_track_id = _canonical_uuid(
                reference_track_id, "alignment.reference_track_id"
            )
        object.__setattr__(self, "reference_track_id", reference_track_id)
        reference_fingerprint = str(
            self.reference_fingerprint_sha256 or ""
        ).strip().lower()
        if reference_fingerprint and not _SHA256_RE.fullmatch(reference_fingerprint):
            raise TakeProjectError(
                "alignment.reference_fingerprint_sha256 must be a SHA-256 digest."
            )
        object.__setattr__(
            self,
            "reference_fingerprint_sha256",
            reference_fingerprint,
        )

    @property
    def effective_offset_s(self) -> float:
        return self.automatic_offset_s + self.manual_nudge_s

    def with_manual_nudge(self, seconds: float) -> "AlignmentState":
        return replace(
            self,
            manual_nudge_s=_finite_float(seconds, "alignment.manual_nudge_s"),
        )

    def restore_automatic(self) -> "AlignmentState":
        return replace(self, manual_nudge_s=0.0)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "automatic_offset_s": self.automatic_offset_s,
            "manual_nudge_s": self.manual_nudge_s,
            "effective_offset_s": self.effective_offset_s,
            "drift_ppm": self.drift_ppm,
            "confidence": self.confidence,
            "method": self.method,
            "residual_ms": self.residual_ms,
            "anchors": [item.to_dict() for item in self.anchors],
        }
        if self.reference_track_id:
            payload["reference_track_id"] = self.reference_track_id
        if self.reference_fingerprint_sha256:
            payload["reference_fingerprint_sha256"] = (
                self.reference_fingerprint_sha256
            )
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AlignmentState":
        raw_anchors = value.get("anchors", ())
        anchors = tuple(
            AlignmentAnchor.from_dict(item)
            for item in raw_anchors
            if isinstance(item, Mapping)
        ) if isinstance(raw_anchors, (list, tuple)) else ()
        return cls(
            automatic_offset_s=value.get("automatic_offset_s", value.get("offset_s", 0.0)),
            manual_nudge_s=value.get("manual_nudge_s", 0.0),
            drift_ppm=value.get("drift_ppm", 0.0),
            confidence=value.get("confidence", 0.0),
            method=value.get("method", "unverified"),
            residual_ms=value.get("residual_ms", 0.0),
            anchors=anchors,
            reference_track_id=value.get("reference_track_id", ""),
            reference_fingerprint_sha256=value.get(
                "reference_fingerprint_sha256", ""
            ),
        )


@dataclass(frozen=True)
class MediaSegment:
    """One immutable file/configuration interval belonging to a source."""

    segment_id: str
    path: str
    project_start_frame: int
    frame_count: int
    sample_rate: int
    channels: int
    sample_format: str
    media_status: MediaStatus = MediaStatus.AVAILABLE
    sha256: str = ""
    device_id: str = ""
    gaps: tuple[GapInterval, ...] = ()
    size_bytes: int = 0
    has_signal: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "segment_id", _canonical_uuid(self.segment_id, "segment.segment_id")
        )
        object.__setattr__(self, "path", _relative_media_path(self.path))
        object.__setattr__(
            self,
            "project_start_frame",
            _nonnegative_int(self.project_start_frame, "segment.project_start_frame"),
        )
        object.__setattr__(
            self, "frame_count", _nonnegative_int(self.frame_count, "segment.frame_count")
        )
        sample_rate = _nonnegative_int(self.sample_rate, "segment.sample_rate")
        if sample_rate <= 0:
            raise TakeProjectError("segment.sample_rate must be greater than zero.")
        object.__setattr__(self, "sample_rate", sample_rate)
        channels = _nonnegative_int(self.channels, "segment.channels")
        if channels <= 0:
            raise TakeProjectError("segment.channels must be greater than zero.")
        object.__setattr__(self, "channels", channels)
        sample_format = str(self.sample_format or "").strip().upper()
        if not sample_format:
            raise TakeProjectError("segment.sample_format is required.")
        object.__setattr__(self, "sample_format", sample_format[:40])
        status = self.media_status
        if not isinstance(status, MediaStatus):
            status = _enum_value(MediaStatus, status, "segment.media_status")
        object.__setattr__(self, "media_status", status)
        checksum = str(self.sha256 or "").strip().lower()
        if checksum and not _SHA256_RE.fullmatch(checksum):
            raise TakeProjectError("segment.sha256 must be a lowercase SHA-256 digest.")
        object.__setattr__(self, "sha256", checksum)
        object.__setattr__(self, "device_id", str(self.device_id or "").strip()[:256])
        object.__setattr__(
            self, "size_bytes", _nonnegative_int(self.size_bytes, "segment.size_bytes")
        )
        if self.has_signal is not None and not isinstance(self.has_signal, bool):
            raise TakeProjectError("segment.has_signal must be true, false, or null.")
        gaps = tuple(self.gaps)
        for gap in gaps:
            if gap.end_frame > self.frame_count:
                raise TakeProjectError("segment gap extends beyond segment.frame_count.")
            if any(channel >= channels for channel in gap.channels):
                raise TakeProjectError("segment gap references an unavailable channel.")
        object.__setattr__(self, "gaps", gaps)

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.sample_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "path": self.path,
            "project_start_frame": self.project_start_frame,
            "frame_count": self.frame_count,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_format": self.sample_format,
            "media_status": self.media_status.value,
            "sha256": self.sha256,
            "device_id": self.device_id,
            "gaps": [gap.to_dict() for gap in self.gaps],
            "size_bytes": self.size_bytes,
            "has_signal": self.has_signal,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MediaSegment":
        raw_gaps = value.get("gaps", ())
        gaps = tuple(
            GapInterval.from_dict(item)
            for item in raw_gaps
            if isinstance(item, Mapping)
        ) if isinstance(raw_gaps, (list, tuple)) else ()
        return cls(
            segment_id=value.get("segment_id", ""),
            path=value.get("path", value.get("filename", "")),
            project_start_frame=value.get("project_start_frame", 0),
            frame_count=value.get("frame_count", 0),
            sample_rate=value.get("sample_rate", 0),
            channels=value.get("channels", 1),
            sample_format=value.get("sample_format", "PCM_24"),
            media_status=_enum_value(
                MediaStatus,
                value.get("media_status", MediaStatus.AVAILABLE.value),
                "segment.media_status",
            ),
            sha256=value.get("sha256", ""),
            device_id=value.get("device_id", ""),
            gaps=gaps,
            size_bytes=value.get("size_bytes", 0),
            has_signal=(
                value.get("has_signal")
                if isinstance(value.get("has_signal"), bool)
                or value.get("has_signal") is None
                else None
            ),
        )


@dataclass(frozen=True)
class Participant:
    participant_id: str
    display_name: str
    instrument: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "participant_id",
            _canonical_uuid(self.participant_id, "participant.participant_id"),
        )
        name = " ".join(str(self.display_name or "").split())[:120]
        if not name:
            raise TakeProjectError("participant.display_name is required.")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(
            self, "instrument", " ".join(str(self.instrument or "").split())[:120]
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "participant_id": self.participant_id,
            "display_name": self.display_name,
            "instrument": self.instrument,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Participant":
        return cls(
            participant_id=value.get("participant_id", ""),
            display_name=value.get("display_name", ""),
            instrument=value.get("instrument", ""),
        )


@dataclass(frozen=True)
class ProjectTrack:
    track_id: str
    source_id: str
    participant_id: str | None
    name: str
    instrument: str
    source_type: SourceType
    quality: SourceQuality
    media_status: MediaStatus
    order: int
    segments: tuple[MediaSegment, ...]
    alignment: AlignmentState = field(default_factory=AlignmentState)
    selected_for_export: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", _canonical_uuid(self.track_id, "track.track_id"))
        object.__setattr__(
            self, "source_id", _canonical_uuid(self.source_id, "track.source_id")
        )
        if self.participant_id:
            object.__setattr__(
                self,
                "participant_id",
                _canonical_uuid(self.participant_id, "track.participant_id"),
            )
        name = " ".join(str(self.name or "").split())[:160]
        if not name:
            raise TakeProjectError("track.name is required.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "instrument", " ".join(str(self.instrument or "").split())[:120])
        if not isinstance(self.source_type, SourceType):
            object.__setattr__(
                self,
                "source_type",
                _enum_value(SourceType, self.source_type, "track.source_type"),
            )
        if not isinstance(self.quality, SourceQuality):
            object.__setattr__(
                self,
                "quality",
                _enum_value(SourceQuality, self.quality, "track.quality"),
            )
        if not isinstance(self.media_status, MediaStatus):
            object.__setattr__(
                self,
                "media_status",
                _enum_value(MediaStatus, self.media_status, "track.media_status"),
            )
        object.__setattr__(self, "order", _nonnegative_int(self.order, "track.order"))
        segments = tuple(self.segments)
        if not segments:
            raise TakeProjectError("track.segments must retain expected media inventory.")
        if len({item.segment_id for item in segments}) != len(segments):
            raise TakeProjectError("track contains duplicate segment IDs.")
        object.__setattr__(self, "segments", segments)

    @property
    def primary_segment(self) -> MediaSegment:
        return self.segments[0]

    @property
    def duration_s(self) -> float:
        """Compatibility duration when segment starts use the primary rate.

        Schema-v2 project serialization uses :meth:`duration_at_project_rate`
        because ``project_start_frame`` belongs to the project clock, not an
        individual segment's potentially different source rate.
        """
        rate = self.primary_segment.sample_rate
        return self.duration_at_project_rate(rate)

    def duration_at_project_rate(self, project_sample_rate: int) -> float:
        if project_sample_rate <= 0:
            raise TakeProjectError("project_sample_rate must be greater than zero.")
        return max(
            (
                item.project_start_frame / project_sample_rate + item.duration_s
                for item in self.segments
            ),
            default=0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        primary = self.primary_segment
        # Keep the v1 flat evidence fields so older read-only tools degrade
        # safely while schema-v2 consumers use ``segments`` and ``alignment``.
        return {
            "track_id": self.track_id,
            "source_id": self.source_id,
            "participant_id": self.participant_id,
            "filename": primary.path,
            "name": self.name,
            "instrument": self.instrument,
            "source": self.source_type.value,
            "quality": self.quality.value,
            "media_status": self.media_status.value,
            "order": self.order,
            "sample_rate": primary.sample_rate,
            "duration_s": self.duration_s,
            "offset_s": self.alignment.effective_offset_s,
            "selected_for_export": self.selected_for_export,
            "segments": [item.to_dict() for item in self.segments],
            "alignment": self.alignment.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProjectTrack":
        raw_segments = value.get("segments", ())
        segments = tuple(
            MediaSegment.from_dict(item)
            for item in raw_segments
            if isinstance(item, Mapping)
        ) if isinstance(raw_segments, (list, tuple)) else ()
        if not segments and value.get("filename"):
            rate = _nonnegative_int(value.get("sample_rate", 0), "track.sample_rate")
            duration = _finite_float(value.get("duration_s", 0), "track.duration_s", minimum=0)
            frames = int(round(duration * rate)) if rate else 0
            segments = (
                MediaSegment(
                    segment_id=value.get("segment_id", new_project_id()),
                    path=value.get("filename", ""),
                    project_start_frame=0,
                    frame_count=frames,
                    sample_rate=rate,
                    channels=value.get("channels", 1),
                    sample_format=value.get("sample_format", "PCM_24"),
                    media_status=value.get("media_status", MediaStatus.AVAILABLE.value),
                    sha256=value.get("sha256", ""),
                ),
            )
        alignment_value = value.get("alignment", {})
        if not isinstance(alignment_value, Mapping):
            alignment_value = {}
        if "automatic_offset_s" not in alignment_value and "offset_s" in value:
            alignment_value = dict(alignment_value)
            alignment_value["automatic_offset_s"] = value.get("offset_s", 0.0)
        source_text = str(value.get("source_type", value.get("source", "unknown")))
        if source_text == "local_ssl":
            source_text = SourceType.LOCAL_ISOLATED.value
        default_quality = (
            SourceQuality.NETWORK_TRACK.value
            if source_text == SourceType.JAMULUS_SERVER.value
            else SourceQuality.UNVERIFIED.value
        )
        return cls(
            track_id=value.get("track_id", ""),
            source_id=value.get("source_id", ""),
            participant_id=value.get("participant_id") or None,
            name=value.get("name", ""),
            instrument=value.get("instrument", ""),
            source_type=_enum_value(SourceType, source_text, "track.source_type"),
            quality=_enum_value(
                SourceQuality, value.get("quality", default_quality), "track.quality"
            ),
            media_status=_enum_value(
                MediaStatus,
                value.get("media_status", MediaStatus.AVAILABLE.value),
                "track.media_status",
            ),
            order=value.get("order", 0),
            segments=segments,
            alignment=AlignmentState.from_dict(alignment_value),
            selected_for_export=bool(value.get("selected_for_export", True)),
        )


@dataclass(frozen=True)
class ProjectMarker:
    marker_id: str
    position_s: float
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "marker_id", _canonical_uuid(self.marker_id, "marker.marker_id"))
        object.__setattr__(
            self,
            "position_s",
            _finite_float(self.position_s, "marker.position_s", minimum=0),
        )
        label = " ".join(str(self.label or "").split())[:160]
        if not label:
            raise TakeProjectError("marker.label is required.")
        object.__setattr__(self, "label", label)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "position_s": self.position_s,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProjectMarker":
        return cls(
            marker_id=value.get("marker_id", ""),
            position_s=value.get("position_s", 0.0),
            label=value.get("label", ""),
        )


@dataclass(frozen=True)
class HostIdentity:
    """Optional durable host identity carried with one recorded session."""

    participant_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        participant_id = str(self.participant_id or "").strip()
        if participant_id:
            participant_id = _canonical_uuid(participant_id, "session.host.participant_id")
        # Host identity is a musician-facing name, but it remains free text
        # supplied by a local setting. Apply the same invite/address/secret
        # boundary as every other session-evidence string before it reaches a
        # journal, take manifest, or track export.
        name = _safe_session_text(self.display_name, 160)
        if "[redacted" in name or "$HOME" in name:
            # A partially redacted display name is not useful session
            # provenance and can still hint at a private value's shape. Keep
            # the durable participant UUID and use a neutral label instead.
            name = "Private host"
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "display_name", name)

    @property
    def is_empty(self) -> bool:
        return not self.participant_id and not self.display_name

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if self.participant_id:
            payload["participant_id"] = self.participant_id
        if self.display_name:
            payload["display_name"] = self.display_name
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HostIdentity":
        return cls(
            participant_id=value.get("participant_id", ""),
            display_name=value.get("display_name", ""),
        )


@dataclass(frozen=True)
class SessionTimelineEvent:
    """One bounded, non-secret recording-session event."""

    event: str
    occurred_utc: str = ""
    at_s: float | None = None
    participant_id: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        event = _safe_session_text(self.event, 80)
        if not event:
            raise TakeProjectError("session timeline event is required.")
        occurred_utc = str(self.occurred_utc or "").strip()[:40]
        participant_id = str(self.participant_id or "").strip()
        if participant_id:
            participant_id = _canonical_uuid(
                participant_id, "session.timeline.participant_id"
            )
        at_s = self.at_s
        if at_s is not None:
            at_s = _finite_float(at_s, "session.timeline.at_s", minimum=0)
        detail = _safe_session_text(self.detail, 240)
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "occurred_utc", occurred_utc)
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "at_s", at_s)
        object.__setattr__(self, "detail", detail)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"event": self.event}
        if self.occurred_utc:
            payload["occurred_utc"] = self.occurred_utc
        if self.at_s is not None:
            payload["at_s"] = self.at_s
        if self.participant_id:
            payload["participant_id"] = self.participant_id
        if self.detail:
            payload["detail"] = self.detail
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionTimelineEvent":
        return cls(
            event=value.get("event", ""),
            occurred_utc=value.get("occurred_utc", ""),
            at_s=value.get("at_s"),
            participant_id=value.get("participant_id", ""),
            detail=value.get("detail", ""),
        )


@dataclass(frozen=True)
class SessionEvidence:
    """Optional recorded-session proof that does not alter source audio."""

    protocol_version: str = ""
    started_utc: str = ""
    ended_utc: str = ""
    host: HostIdentity = field(default_factory=HostIdentity)
    recovery_status: RecoveryStatus = RecoveryStatus.NOT_NEEDED
    recovery_notes: tuple[str, ...] = ()
    timeline: tuple[SessionTimelineEvent, ...] = ()
    # The SessionRecordingPlan digest bound at record start; empty when no
    # plan was constructed (older takes, or a binding failure that is
    # reported separately). Additive and optional for backward compat.
    recording_plan_fingerprint: str = ""

    def __post_init__(self) -> None:
        protocol_version = _safe_session_text(self.protocol_version, 80)
        started_utc = str(self.started_utc or "").strip()[:40]
        ended_utc = str(self.ended_utc or "").strip()[:40]
        host = self.host
        if isinstance(host, Mapping):
            host = HostIdentity.from_dict(host)
        if not isinstance(host, HostIdentity):
            raise TakeProjectError("session.host must be a host identity.")
        status = self.recovery_status
        if not isinstance(status, RecoveryStatus):
            try:
                status = RecoveryStatus(str(status))
            except ValueError:
                status = RecoveryStatus.NEEDS_ATTENTION
        notes = tuple(
            _safe_session_text(item, 240)
            for item in self.recovery_notes
            if _safe_session_text(item, 240)
        )
        if (
            str(self.recovery_status or "")
            and not isinstance(self.recovery_status, RecoveryStatus)
            and status is RecoveryStatus.NEEDS_ATTENTION
            and str(self.recovery_status) != RecoveryStatus.NEEDS_ATTENTION.value
        ):
            notes = (*notes, "Session recovery state was unreadable.")
        timeline: list[SessionTimelineEvent] = []
        for item in self.timeline:
            if isinstance(item, SessionTimelineEvent):
                timeline.append(item)
            elif isinstance(item, Mapping):
                timeline.append(SessionTimelineEvent.from_dict(item))
            else:
                raise TakeProjectError("session.timeline entries must be events.")
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "started_utc", started_utc)
        object.__setattr__(self, "ended_utc", ended_utc)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "recovery_status", status)
        object.__setattr__(self, "recovery_notes", tuple(dict.fromkeys(notes)))
        object.__setattr__(self, "timeline", tuple(timeline))

    @property
    def is_empty(self) -> bool:
        return (
            not self.protocol_version
            and not self.started_utc
            and not self.ended_utc
            and self.host.is_empty
            and self.recovery_status is RecoveryStatus.NOT_NEEDED
            and not self.recovery_notes
            and not self.timeline
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.protocol_version:
            payload["protocol_version"] = self.protocol_version
        if self.started_utc:
            payload["started_utc"] = self.started_utc
        if self.ended_utc:
            payload["ended_utc"] = self.ended_utc
        host = self.host.to_dict()
        if host:
            payload["host"] = host
        payload["recovery_status"] = self.recovery_status.value
        if self.recovery_notes:
            payload["recovery_notes"] = list(self.recovery_notes)
        if self.timeline:
            payload["timeline"] = [item.to_dict() for item in self.timeline]
        if self.recording_plan_fingerprint:
            payload["recording_plan_fingerprint"] = (
                self.recording_plan_fingerprint
            )
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionEvidence":
        host_value = value.get("host", {})
        host = HostIdentity.from_dict(host_value) if isinstance(host_value, Mapping) else HostIdentity()
        timeline_value = value.get("timeline", ())
        timeline = (
            tuple(
                SessionTimelineEvent.from_dict(item)
                for item in timeline_value
                if isinstance(item, Mapping)
            )
            if isinstance(timeline_value, (list, tuple))
            else ()
        )
        return cls(
            protocol_version=value.get("protocol_version", ""),
            started_utc=value.get("started_utc", ""),
            ended_utc=value.get("ended_utc", ""),
            host=host,
            recovery_status=value.get("recovery_status", RecoveryStatus.NOT_NEEDED.value),
            recovery_notes=_string_tuple(value.get("recovery_notes")),
            timeline=timeline,
            recording_plan_fingerprint=str(
                value.get("recording_plan_fingerprint", "") or ""
            ),
        )


@dataclass(frozen=True)
class TakeProject:
    session_id: str
    take_id: str
    session_title: str
    take_name: str
    status: ProjectStatus
    project_sample_rate: int
    participants: tuple[Participant, ...]
    tracks: tuple[ProjectTrack, ...]
    app_version: str = ""
    created_utc: str = ""
    tempo_bpm: float = 120.0
    time_signature_numerator: int = 4
    time_signature_denominator: int = 4
    devices: tuple[CaptureDevice, ...] = ()
    markers: tuple[ProjectMarker, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    session_evidence: SessionEvidence = field(default_factory=SessionEvidence)
    revision: int = 1
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_SCHEMA_VERSION:
            raise TakeProjectError(
                f"Unsupported project schema {self.schema_version}; expected "
                f"{PROJECT_SCHEMA_VERSION}."
            )
        object.__setattr__(self, "session_id", _canonical_uuid(self.session_id, "session_id"))
        object.__setattr__(self, "take_id", _canonical_uuid(self.take_id, "take_id"))
        object.__setattr__(
            self, "session_title", " ".join(str(self.session_title or "").split())[:160]
        )
        take_name = " ".join(str(self.take_name or "").split())[:160]
        if not take_name:
            raise TakeProjectError("take_name is required.")
        object.__setattr__(self, "take_name", take_name)
        if not isinstance(self.status, ProjectStatus):
            object.__setattr__(
                self, "status", _enum_value(ProjectStatus, self.status, "status")
            )
        rate = _nonnegative_int(self.project_sample_rate, "project_sample_rate")
        if rate <= 0:
            raise TakeProjectError("project_sample_rate must be greater than zero.")
        object.__setattr__(self, "project_sample_rate", rate)
        object.__setattr__(
            self, "tempo_bpm", _finite_float(self.tempo_bpm, "tempo_bpm", minimum=1)
        )
        numerator = _nonnegative_int(
            self.time_signature_numerator, "time_signature_numerator"
        )
        denominator = _nonnegative_int(
            self.time_signature_denominator, "time_signature_denominator"
        )
        if numerator <= 0 or denominator <= 0 or denominator & (denominator - 1):
            raise TakeProjectError("time signature must use a positive power-of-two denominator.")
        object.__setattr__(self, "time_signature_numerator", numerator)
        object.__setattr__(self, "time_signature_denominator", denominator)
        object.__setattr__(self, "revision", _nonnegative_int(self.revision, "revision"))
        if self.revision <= 0:
            raise TakeProjectError("revision must be greater than zero.")
        object.__setattr__(self, "app_version", str(self.app_version or "").strip()[:80])
        object.__setattr__(self, "created_utc", str(self.created_utc or "").strip()[:40])

        participants = tuple(self.participants)
        tracks = tuple(self.tracks)
        devices = tuple(self.devices)
        markers = tuple(self.markers)
        _require_unique((item.participant_id for item in participants), "participant IDs")
        _require_unique((item.track_id for item in tracks), "track IDs")
        _require_unique((item.source_id for item in tracks), "source IDs")
        _require_unique((item.device_id for item in devices), "device IDs")
        _require_unique((item.marker_id for item in markers), "marker IDs")
        _require_unique(
            (segment.segment_id for track in tracks for segment in track.segments),
            "segment IDs",
        )
        participant_ids = {item.participant_id for item in participants}
        device_ids = {item.device_id for item in devices}
        for track in tracks:
            if track.participant_id and track.participant_id not in participant_ids:
                raise TakeProjectError(
                    f"track {track.track_id} references an unknown participant."
                )
            for segment in track.segments:
                if segment.device_id and segment.device_id not in device_ids:
                    raise TakeProjectError(
                        f"segment {segment.segment_id} references an unknown device."
                    )
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "tracks", tracks)
        object.__setattr__(self, "devices", devices)
        object.__setattr__(self, "markers", markers)
        object.__setattr__(self, "errors", tuple(str(item) for item in self.errors))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))
        evidence = self.session_evidence
        if isinstance(evidence, Mapping):
            evidence = SessionEvidence.from_dict(evidence)
        if not isinstance(evidence, SessionEvidence):
            raise TakeProjectError("session_evidence must be session evidence.")
        if evidence.host.participant_id and evidence.host.participant_id not in participant_ids:
            raise TakeProjectError("session host references an unknown participant.")
        for event in evidence.timeline:
            if event.participant_id and event.participant_id not in participant_ids:
                raise TakeProjectError(
                    "session timeline references an unknown participant."
                )
        object.__setattr__(self, "session_evidence", evidence)

    @property
    def has_blocking_media(self) -> bool:
        blocking = {
            MediaStatus.MISSING,
            MediaStatus.DAMAGED,
            MediaStatus.PARTIAL,
            MediaStatus.TRANSFER_FAILED,
        }
        return any(
            track.media_status in blocking
            or any(segment.media_status in blocking for segment in track.segments)
            for track in self.tracks
        )

    @property
    def effective_status(self) -> ProjectStatus:
        if (
            self.errors
            or self.has_blocking_media
            or self.session_evidence.recovery_status
            is RecoveryStatus.NEEDS_ATTENTION
        ):
            return ProjectStatus.NEEDS_ATTENTION
        return self.status

    def to_dict(self) -> dict[str, Any]:
        tracks = []
        for item in sorted(self.tracks, key=lambda track: track.order):
            value = item.to_dict()
            value["duration_s"] = item.duration_at_project_rate(
                self.project_sample_rate
            )
            tracks.append(value)
        payload = {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "app_version": self.app_version,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "session_title": self.session_title,
            "take_name": self.take_name,
            "created_utc": self.created_utc,
            "status": self.effective_status.value,
            "project_sample_rate": self.project_sample_rate,
            "tempo_bpm": self.tempo_bpm,
            "time_signature": {
                "numerator": self.time_signature_numerator,
                "denominator": self.time_signature_denominator,
            },
            "participants": [item.to_dict() for item in self.participants],
            "devices": [item.to_dict() for item in self.devices],
            "tracks": tracks,
            "markers": [item.to_dict() for item in self.markers],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
        if not self.session_evidence.is_empty:
            payload["session"] = self.session_evidence.to_dict()
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TakeProject":
        schema = value.get("schema_version")
        if schema != PROJECT_SCHEMA_VERSION:
            raise TakeProjectError(f"Unsupported project schema: {schema!r}.")
        signature = value.get("time_signature", {})
        if not isinstance(signature, Mapping):
            signature = {}
        session_value = value.get("session", {})
        if isinstance(session_value, Mapping):
            evidence = SessionEvidence.from_dict(session_value)
        elif session_value in (None, ""):
            evidence = SessionEvidence()
        else:
            evidence = SessionEvidence(
                recovery_status=RecoveryStatus.NEEDS_ATTENTION,
                recovery_notes=("Session evidence was unreadable.",),
            )
        return cls(
            session_id=value.get("session_id", ""),
            take_id=value.get("take_id", ""),
            session_title=value.get("session_title", ""),
            take_name=value.get("take_name", ""),
            status=_enum_value(ProjectStatus, value.get("status", ""), "status"),
            project_sample_rate=value.get("project_sample_rate", 0),
            participants=_mapping_tuple(value.get("participants"), Participant.from_dict),
            tracks=_mapping_tuple(value.get("tracks"), ProjectTrack.from_dict),
            app_version=value.get("app_version", ""),
            created_utc=value.get("created_utc", ""),
            tempo_bpm=value.get("tempo_bpm", 120.0),
            time_signature_numerator=signature.get("numerator", 4),
            time_signature_denominator=signature.get("denominator", 4),
            devices=_mapping_tuple(value.get("devices"), CaptureDevice.from_dict),
            markers=_mapping_tuple(value.get("markers"), ProjectMarker.from_dict),
            errors=_string_tuple(value.get("errors")),
            warnings=_string_tuple(value.get("warnings")),
            session_evidence=evidence,
            revision=value.get("revision", 1),
            schema_version=schema,
        )


def _mapping_tuple(value: object, factory) -> tuple:
    if not isinstance(value, (list, tuple)):
        return ()
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TakeProjectError("project arrays may contain only objects.")
        result.append(factory(item))
    return tuple(result)


def _require_unique(values: Iterable[str], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise TakeProjectError(f"Project contains duplicate {label}.")


def _legacy_id(kind: str, seed: str) -> str:
    return str(uuid.uuid5(_MIGRATION_NAMESPACE, f"{kind}:{seed}"))


def migrate_v1_manifest(take_dir: str | Path, value: Mapping[str, Any]) -> TakeProject:
    """Represent a schema-v1 receipt as schema-v2 without changing the disk.

    Legacy receipts cannot prove identity across sessions.  Deterministic IDs
    keep repeated reads/copies stable, and the migration warning states the
    limit instead of pretending mutable names became enrollment identities.
    """
    path = Path(take_dir)
    if value.get("schema_version", 1) != 1:
        raise TakeProjectError("migrate_v1_manifest accepts schema 1 only.")
    title = str(value.get("session_title") or "").strip()
    local_capture = value.get("local_capture", {})
    if not isinstance(local_capture, Mapping):
        local_capture = {}
    started = str(local_capture.get("started_utc") or "")
    inventory = value.get("tracks", [])
    if not isinstance(inventory, list):
        inventory = []
    canonical_inventory = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    session_seed = f"{title}|{path.name}|{started}"
    session_id = _legacy_id("session", session_seed)
    take_id = _legacy_id(
        "take", f"{session_id}|{hashlib.sha256(canonical_inventory.encode()).hexdigest()}"
    )

    participants: list[Participant] = []
    tracks: list[ProjectTrack] = []
    participant_ids: set[str] = set()
    migration_errors = list(_string_tuple(value.get("errors")))
    for order, raw in enumerate(inventory):
        if not isinstance(raw, Mapping):
            continue
        filename = str(raw.get("filename") or "").strip()
        if not filename:
            continue
        safe_filename = _relative_media_path(filename)
        source_text = str(raw.get("source") or SourceType.JAMULUS_SERVER.value)
        if source_text == "local_ssl":
            source_type = SourceType.LOCAL_ISOLATED
            identity_seed = f"{session_id}|legacy-local-host"
        else:
            try:
                source_type = SourceType(source_text)
            except ValueError:
                source_type = SourceType.UNKNOWN
            # Filename/channel evidence deliberately participates: two people
            # with the same display name remain distinct in a legacy take.
            identity_seed = f"{session_id}|{source_text}|{safe_filename}"
        participant_id = _legacy_id("participant", identity_seed)
        name = str(raw.get("name") or Path(safe_filename).stem).strip() or "Musician"
        if participant_id not in participant_ids:
            participants.append(Participant(participant_id, name, str(raw.get("instrument") or "")))
            participant_ids.add(participant_id)

        media_path = path / safe_filename
        media_status = MediaStatus.AVAILABLE if media_path.is_file() else MediaStatus.MISSING
        if media_status is MediaStatus.MISSING:
            migration_errors.append(f"{name} is missing from this take ({safe_filename}).")
        rate_raw = raw.get("sample_rate", 48000)
        try:
            rate = int(rate_raw)
        except (TypeError, ValueError):
            rate = 48000
        if rate <= 0:
            rate = 48000
        duration = _finite_float(raw.get("duration_s", 0.0), "track.duration_s", minimum=0)
        frame_count = _nonnegative_int(
            raw.get("frame_count", int(round(duration * rate))), "segment.frame_count"
        )
        segment_id = _legacy_id("segment", f"{take_id}|{safe_filename}|0")
        source_id = _legacy_id("source", f"{take_id}|{safe_filename}")
        track_id = _legacy_id("track", f"{take_id}|{order}|{safe_filename}")
        offset = _finite_float(raw.get("offset_s", 0.0), "track.offset_s")
        quality = (
            SourceQuality.NETWORK_TRACK
            if source_type is SourceType.JAMULUS_SERVER
            else SourceQuality.UNVERIFIED
        )
        tracks.append(ProjectTrack(
            track_id=track_id,
            source_id=source_id,
            participant_id=participant_id,
            name=name,
            instrument=str(raw.get("instrument") or ""),
            source_type=source_type,
            quality=quality,
            media_status=media_status,
            order=order,
            segments=(MediaSegment(
                segment_id=segment_id,
                path=safe_filename,
                project_start_frame=0,
                frame_count=frame_count,
                sample_rate=rate,
                channels=int(raw.get("channels", 1) or 1),
                sample_format=str(raw.get("sample_format") or "PCM_24"),
                media_status=media_status,
                sha256=str(raw.get("sha256") or ""),
            ),),
            alignment=AlignmentState(
                automatic_offset_s=offset,
                confidence=float(local_capture.get("alignment_confidence", 0.0) or 0.0)
                if source_type is SourceType.LOCAL_ISOLATED else 0.0,
                method=str(local_capture.get("alignment_method") or "legacy-manifest"),
            ),
        ))

    status_text = str(value.get("status") or ProjectStatus.NEEDS_ATTENTION.value)
    try:
        status = ProjectStatus(status_text)
    except ValueError:
        status = ProjectStatus.NEEDS_ATTENTION
    warnings = list(_string_tuple(value.get("warnings")))
    warnings.append(
        "Migrated from schema 1: participant IDs are deterministic inventory "
        "identifiers, not proof of identity across reconnects."
    )
    project_rate = next(
        (segment.sample_rate for track in tracks for segment in track.segments),
        48000,
    )
    return TakeProject(
        session_id=session_id,
        take_id=take_id,
        session_title=title,
        take_name=path.name or "Take",
        status=status,
        project_sample_rate=project_rate,
        participants=tuple(participants),
        tracks=tuple(tracks),
        app_version=str(value.get("app_version") or ""),
        created_utc=started,
        errors=tuple(dict.fromkeys(migration_errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def load_take_project(take_dir: str | Path) -> TakeProject:
    """Load schema 2, or read-only migrate schema 1, from a take folder."""
    path = Path(take_dir)
    manifest = path / "webjam-take.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TakeProjectError(f"Could not read {manifest.name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TakeProjectError("Project manifest root must be an object.")
    schema = value.get("schema_version", 1)
    if schema == 1:
        return migrate_v1_manifest(path, value)
    if schema == PROJECT_SCHEMA_VERSION:
        return TakeProject.from_dict(value)
    raise TakeProjectError(f"Unsupported project schema: {schema!r}.")


def write_take_project(
    take_dir: str | Path,
    project: TakeProject,
    *,
    expected_revision: int | None = None,
) -> Path:
    """Atomically publish project metadata without replacing newer truth.

    New projects and schema-v1 migration may write revision one. Once a
    schema-v2 manifest exists, a changed payload must be exactly the next
    revision from the version it read. This makes ordinary read-modify-write
    callers fail closed instead of silently erasing a peer reconciliation that
    finished just before their write.
    """

    from core.file_io import atomic_write_text

    path = Path(take_dir)
    path.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(project.to_dict(), indent=2, sort_keys=False) + "\n"
    with take_project_manifest_lock(path) as manifest:
        try:
            current_bytes = manifest.read_bytes()
            current_payload = json.loads(current_bytes)
        except (OSError, ValueError, TypeError):
            current_payload = None
            current_bytes = b""
        if isinstance(current_payload, Mapping) and current_payload.get(
            "schema_version"
        ) == PROJECT_SCHEMA_VERSION:
            try:
                current_revision = int(current_payload.get("revision", 0) or 0)
            except (TypeError, ValueError):
                current_revision = 0
            if expected_revision is not None and current_revision != int(
                expected_revision
            ):
                raise TakeProjectConflict(
                    "The take project changed before this update could be saved."
                )
            if current_bytes != payload and project.revision != current_revision + 1:
                raise TakeProjectConflict(
                    "The take project has a newer revision; reload it before saving."
                )
        elif expected_revision is not None:
            raise TakeProjectConflict(
                "The take project is not at the revision required for this update."
            )
        atomic_write_text(manifest, payload, mode=0o600)
    return manifest

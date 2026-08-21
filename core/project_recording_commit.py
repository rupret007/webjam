"""Crash-recoverable publication of Reference Studio recording results.

The physical recorder intentionally publishes WAVs outside a song bundle.
This module is the only bridge that collects those immutable WAVs into the
current :class:`~core.song_project.SongProject`, adds schema-3 regions/take
lanes, and preserves exact dropout/alignment evidence.

Project, Studio, and evidence files cannot be replaced in one filesystem
rename.  Publication is therefore an all-or-explicit-recovery transaction:
every member is individually atomic, while a bounded path-free journal stays
durable until all three primary documents are committed.  Before the project
manifest changes, a failed transaction removes only exact newly copied media.
After that point, recovery is preserved and must be completed explicitly.

No absolute recording path is serialized, and this module never discovers,
reads, or changes Jamulus configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from core.file_io import atomic_write_bytes
from core.project_audio import (
    PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
    PROJECT_AUDIO_SAMPLE_RATE,
)
from core.project_recording import (
    ProjectRecorderState,
    ProjectRecordingDropout,
    ProjectRecordingResult,
    ProjectRecordingSchedule,
    ProjectRecordingSegment,
    ProjectTrackRecording,
)
from core.song_project import (
    MediaImportMethod,
    MediaProvenance,
    SongProject,
    SongProjectError,
)
from core.song_project_store import (
    SongProjectConflict,
    SongProjectStoreError,
    import_project_media,
    load_project_bundle,
    project_store_lock,
    save_project_bundle,
    verify_project_media,
)
from core.song_studio_store import (
    SongStudioConflict,
    SongStudioStoreError,
    load_song_studio_document,
    save_song_studio_document,
)
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
    StudioRegion,
    StudioTakeLane,
    StudioTrackKind,
    studio_document_from_dict,
)

RECORDING_EVIDENCE_FILENAME = ".webjam-recording-evidence.json"
RECORDING_EVIDENCE_BACKUP_FILENAME = ".webjam-recording-evidence.json.bak"
RECORDING_COMMIT_JOURNAL_FILENAME = ".webjam-recording-commit.json"
RECORDING_COMMIT_LOCK_FILENAME = ".webjam-recording-commit.lock"
RECORDING_EVIDENCE_SCHEMA_VERSION = 1
RECORDING_COMMIT_JOURNAL_SCHEMA_VERSION = 1
MAX_RECORDING_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_RECORDING_COMMIT_JOURNAL_BYTES = 32 * 1024 * 1024
MAX_RECORDING_COMMITS = 20_000
MAX_EVIDENCE_TRACKS = 32
MAX_EVIDENCE_PASSES = 1_024
MAX_EVIDENCE_DROPOUTS = 100_000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORDING_NAMESPACE = uuid.UUID("e179e5e9-b9f9-479e-a63c-72f92cc54d53")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class ProjectRecordingCommitError(RuntimeError):
    """A path-free recording-publication error suitable for the Studio UI."""


class ProjectRecordingCommitRecoveryRequired(ProjectRecordingCommitError):
    """Raised when durable project state requires an explicit resume."""

    def __init__(self, commit_id: str, message: str) -> None:
        super().__init__(message)
        self.commit_id = _canonical_uuid(commit_id, "commit_id")
        self.recovery_available = True


class ProjectRecordingCommitState(str, Enum):
    COMMITTED = "committed"
    RECOVERED = "recovered"
    ROLLED_BACK = "rolled_back"


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProjectRecordingCommitError(f"{label} must be a UUID.")
    try:
        canonical = str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ProjectRecordingCommitError(f"{label} must be a UUID.") from None
    if value != canonical:
        raise ProjectRecordingCommitError(
            f"{label} must use canonical lowercase UUID text."
        )
    return canonical


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectRecordingCommitError(f"{label} must be an integer.")
    if not minimum <= value <= maximum:
        raise ProjectRecordingCommitError(
            f"{label} must be between {minimum} and {maximum}."
        )
    return value


def _signed_integer(value: object, label: str) -> int:
    return _integer(
        value,
        label,
        minimum=-PROJECT_AUDIO_SAMPLE_RATE * 10,
        maximum=PROJECT_AUDIO_SAMPLE_RATE * 10,
    )


def _path_free_text(
    value: object,
    label: str,
    *,
    maximum: int = 160,
) -> str:
    if not isinstance(value, str):
        raise ProjectRecordingCommitError(f"{label} must be text.")
    result = " ".join(value.split())
    if (
        not result
        or len(result) > maximum
        or "/" in result
        or "\\" in result
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
    ):
        raise ProjectRecordingCommitError(
            f"{label} must be a short path-free label."
        )
    return result


def _strict_keys(
    value: Mapping[str, Any],
    *,
    fields: set[str],
    label: str,
) -> None:
    if set(value) != fields:
        raise ProjectRecordingCommitError(
            f"{label} contains unsupported or missing fields."
        )


@dataclass(frozen=True, slots=True)
class RecordingDropoutEvidence:
    output_start_frame: int
    frame_count: int
    channels: tuple[int, ...]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output_start_frame",
            _integer(self.output_start_frame, "dropout.output_start_frame"),
        )
        object.__setattr__(
            self,
            "frame_count",
            _integer(self.frame_count, "dropout.frame_count", minimum=1),
        )
        if (
            not isinstance(self.channels, tuple)
            or not self.channels
            or len(self.channels) > 64
            or any(
                isinstance(channel, bool)
                or not isinstance(channel, int)
                or not 0 <= channel < 64
                for channel in self.channels
            )
            or len(set(self.channels)) != len(self.channels)
        ):
            raise ProjectRecordingCommitError(
                "dropout.channels must contain distinct recorded channel indexes."
            )
        object.__setattr__(
            self,
            "reason",
            _path_free_text(self.reason, "dropout.reason"),
        )

    @property
    def output_end_frame(self) -> int:
        return self.output_start_frame + self.frame_count

    def to_dict(self) -> dict[str, object]:
        return {
            "output_start_frame": self.output_start_frame,
            "frame_count": self.frame_count,
            "channels": list(self.channels),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecordingDropoutEvidence:
        _strict_keys(
            value,
            fields={
                "output_start_frame",
                "frame_count",
                "channels",
                "reason",
            },
            label="dropout evidence",
        )
        channels = value["channels"]
        if not isinstance(channels, list):
            raise ProjectRecordingCommitError(
                "dropout.channels must be a list."
            )
        return cls(
            output_start_frame=value["output_start_frame"],
            frame_count=value["frame_count"],
            channels=tuple(channels),
            reason=value["reason"],
        )


@dataclass(frozen=True, slots=True)
class RecordingPassEvidence:
    cycle_index: int
    region_id: str
    lane_id: str | None
    source_start_frame: int
    frame_count: int
    timeline_start_frame: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cycle_index",
            _integer(
                self.cycle_index,
                "pass.cycle_index",
                maximum=MAX_EVIDENCE_PASSES - 1,
            ),
        )
        object.__setattr__(
            self,
            "region_id",
            _canonical_uuid(self.region_id, "pass.region_id"),
        )
        if self.lane_id is not None:
            object.__setattr__(
                self,
                "lane_id",
                _canonical_uuid(self.lane_id, "pass.lane_id"),
            )
        object.__setattr__(
            self,
            "source_start_frame",
            _integer(self.source_start_frame, "pass.source_start_frame"),
        )
        object.__setattr__(
            self,
            "frame_count",
            _integer(self.frame_count, "pass.frame_count", minimum=1),
        )
        object.__setattr__(
            self,
            "timeline_start_frame",
            _integer(
                self.timeline_start_frame,
                "pass.timeline_start_frame",
                minimum=-PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cycle_index": self.cycle_index,
            "region_id": self.region_id,
            "lane_id": self.lane_id,
            "source_start_frame": self.source_start_frame,
            "frame_count": self.frame_count,
            "timeline_start_frame": self.timeline_start_frame,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecordingPassEvidence:
        _strict_keys(
            value,
            fields={
                "cycle_index",
                "region_id",
                "lane_id",
                "source_start_frame",
                "frame_count",
                "timeline_start_frame",
            },
            label="recording pass evidence",
        )
        lane_id = value["lane_id"]
        if lane_id is not None and not isinstance(lane_id, str):
            raise ProjectRecordingCommitError("pass.lane_id must be a UUID or null.")
        return cls(
            cycle_index=value["cycle_index"],
            region_id=value["region_id"],
            lane_id=lane_id,
            source_start_frame=value["source_start_frame"],
            frame_count=value["frame_count"],
            timeline_start_frame=value["timeline_start_frame"],
        )


@dataclass(frozen=True, slots=True)
class RecordingTrackEvidence:
    track_id: str
    media_id: str
    input_channels: tuple[int, ...]
    latency_compensation_frames: int
    frame_count: int
    overflow_frames: int
    recovered: bool
    dropouts: tuple[RecordingDropoutEvidence, ...]
    passes: tuple[RecordingPassEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "track_id",
            _canonical_uuid(self.track_id, "recording track_id"),
        )
        object.__setattr__(
            self,
            "media_id",
            _canonical_uuid(self.media_id, "recording media_id"),
        )
        if (
            not isinstance(self.input_channels, tuple)
            or len(self.input_channels) not in (1, 2)
            or any(
                isinstance(channel, bool)
                or not isinstance(channel, int)
                or not 0 <= channel < 64
                for channel in self.input_channels
            )
            or len(set(self.input_channels)) != len(self.input_channels)
        ):
            raise ProjectRecordingCommitError(
                "recording input_channels must contain one or two distinct indexes."
            )
        object.__setattr__(
            self,
            "latency_compensation_frames",
            _signed_integer(
                self.latency_compensation_frames,
                "recording latency_compensation_frames",
            ),
        )
        frames = _integer(
            self.frame_count,
            "recording frame_count",
            minimum=1,
        )
        object.__setattr__(self, "frame_count", frames)
        object.__setattr__(
            self,
            "overflow_frames",
            _integer(self.overflow_frames, "recording overflow_frames"),
        )
        if not isinstance(self.recovered, bool):
            raise ProjectRecordingCommitError("recording recovered must be boolean.")
        if (
            not isinstance(self.dropouts, tuple)
            or len(self.dropouts) > MAX_EVIDENCE_DROPOUTS
            or any(
                not isinstance(item, RecordingDropoutEvidence)
                for item in self.dropouts
            )
        ):
            raise ProjectRecordingCommitError("recording dropouts are invalid.")
        ordered_dropouts = sorted(
            self.dropouts,
            key=lambda item: (item.output_start_frame, item.output_end_frame),
        )
        for index, dropout in enumerate(ordered_dropouts):
            if dropout.output_end_frame > frames:
                raise ProjectRecordingCommitError(
                    "recording dropout extends beyond recorded media."
                )
            if (
                index
                and dropout.output_start_frame
                < ordered_dropouts[index - 1].output_end_frame
            ):
                raise ProjectRecordingCommitError(
                    "recording dropout intervals overlap."
                )
            if max(dropout.channels) >= len(self.input_channels):
                raise ProjectRecordingCommitError(
                    "recording dropout references an unavailable channel."
                )
        if (
            not isinstance(self.passes, tuple)
            or not 1 <= len(self.passes) <= MAX_EVIDENCE_PASSES
            or any(not isinstance(item, RecordingPassEvidence) for item in self.passes)
        ):
            raise ProjectRecordingCommitError("recording passes are invalid.")
        cursor = 0
        for item in self.passes:
            if item.source_start_frame != cursor:
                raise ProjectRecordingCommitError(
                    "recording passes must cover the source without gaps."
                )
            cursor += item.frame_count
        if cursor != frames:
            raise ProjectRecordingCommitError(
                "recording passes do not cover the complete source."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "media_id": self.media_id,
            "input_channels": list(self.input_channels),
            "latency_compensation_frames": self.latency_compensation_frames,
            "frame_count": self.frame_count,
            "overflow_frames": self.overflow_frames,
            "recovered": self.recovered,
            "dropouts": [item.to_dict() for item in self.dropouts],
            "passes": [item.to_dict() for item in self.passes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecordingTrackEvidence:
        _strict_keys(
            value,
            fields={
                "track_id",
                "media_id",
                "input_channels",
                "latency_compensation_frames",
                "frame_count",
                "overflow_frames",
                "recovered",
                "dropouts",
                "passes",
            },
            label="recording track evidence",
        )
        channels = value["input_channels"]
        dropouts = value["dropouts"]
        passes = value["passes"]
        if not isinstance(channels, list):
            raise ProjectRecordingCommitError(
                "recording input_channels must be a list."
            )
        if not isinstance(dropouts, list) or not all(
            isinstance(item, Mapping) for item in dropouts
        ):
            raise ProjectRecordingCommitError(
                "recording dropouts must be a list of objects."
            )
        if not isinstance(passes, list) or not all(
            isinstance(item, Mapping) for item in passes
        ):
            raise ProjectRecordingCommitError(
                "recording passes must be a list of objects."
            )
        return cls(
            track_id=value["track_id"],
            media_id=value["media_id"],
            input_channels=tuple(channels),
            latency_compensation_frames=value["latency_compensation_frames"],
            frame_count=value["frame_count"],
            overflow_frames=value["overflow_frames"],
            recovered=value["recovered"],
            dropouts=tuple(
                RecordingDropoutEvidence.from_dict(item) for item in dropouts
            ),
            passes=tuple(RecordingPassEvidence.from_dict(item) for item in passes),
        )


@dataclass(frozen=True, slots=True)
class RecordingCommitEvidence:
    commit_id: str
    generation: int
    punch_in_frame: int
    punch_out_frame: int
    count_in_frames: int
    pre_roll_frames: int
    cycle_start_frame: int | None
    cycle_end_frame: int | None
    cycle_count: int
    tracks: tuple[RecordingTrackEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "commit_id",
            _canonical_uuid(self.commit_id, "recording commit_id"),
        )
        object.__setattr__(
            self,
            "generation",
            _integer(
                self.generation,
                "recording generation",
                minimum=1,
                maximum=(1 << 63) - 1,
            ),
        )
        # Reusing the recorder's immutable schedule validator keeps count-in,
        # punch, cycle, and length invariants identical at capture and commit.
        schedule = ProjectRecordingSchedule(
            punch_in_frame=self.punch_in_frame,
            punch_out_frame=self.punch_out_frame,
            count_in_frames=self.count_in_frames,
            pre_roll_frames=self.pre_roll_frames,
            cycle_start_frame=self.cycle_start_frame,
            cycle_end_frame=self.cycle_end_frame,
            cycle_count=self.cycle_count,
        )
        for name in (
            "punch_in_frame",
            "punch_out_frame",
            "count_in_frames",
            "pre_roll_frames",
            "cycle_start_frame",
            "cycle_end_frame",
            "cycle_count",
        ):
            object.__setattr__(self, name, getattr(schedule, name))
        if (
            not isinstance(self.tracks, tuple)
            or not 1 <= len(self.tracks) <= MAX_EVIDENCE_TRACKS
            or any(not isinstance(item, RecordingTrackEvidence) for item in self.tracks)
        ):
            raise ProjectRecordingCommitError("recording evidence tracks are invalid.")
        if len({item.track_id for item in self.tracks}) != len(self.tracks):
            raise ProjectRecordingCommitError(
                "recording evidence contains duplicate tracks."
            )
        if len({item.media_id for item in self.tracks}) != len(self.tracks):
            raise ProjectRecordingCommitError(
                "recording evidence contains duplicate media."
            )

    @property
    def schedule(self) -> ProjectRecordingSchedule:
        return ProjectRecordingSchedule(
            punch_in_frame=self.punch_in_frame,
            punch_out_frame=self.punch_out_frame,
            count_in_frames=self.count_in_frames,
            pre_roll_frames=self.pre_roll_frames,
            cycle_start_frame=self.cycle_start_frame,
            cycle_end_frame=self.cycle_end_frame,
            cycle_count=self.cycle_count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "commit_id": self.commit_id,
            "generation": self.generation,
            "schedule": {
                "punch_in_frame": self.punch_in_frame,
                "punch_out_frame": self.punch_out_frame,
                "count_in_frames": self.count_in_frames,
                "pre_roll_frames": self.pre_roll_frames,
                "cycle_start_frame": self.cycle_start_frame,
                "cycle_end_frame": self.cycle_end_frame,
                "cycle_count": self.cycle_count,
            },
            "tracks": [item.to_dict() for item in self.tracks],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecordingCommitEvidence:
        _strict_keys(
            value,
            fields={"commit_id", "generation", "schedule", "tracks"},
            label="recording commit evidence",
        )
        schedule = value["schedule"]
        tracks = value["tracks"]
        if not isinstance(schedule, Mapping):
            raise ProjectRecordingCommitError(
                "recording evidence schedule must be an object."
            )
        _strict_keys(
            schedule,
            fields={
                "punch_in_frame",
                "punch_out_frame",
                "count_in_frames",
                "pre_roll_frames",
                "cycle_start_frame",
                "cycle_end_frame",
                "cycle_count",
            },
            label="recording evidence schedule",
        )
        if not isinstance(tracks, list) or not all(
            isinstance(item, Mapping) for item in tracks
        ):
            raise ProjectRecordingCommitError(
                "recording evidence tracks must be a list of objects."
            )
        return cls(
            commit_id=value["commit_id"],
            generation=value["generation"],
            punch_in_frame=schedule["punch_in_frame"],
            punch_out_frame=schedule["punch_out_frame"],
            count_in_frames=schedule["count_in_frames"],
            pre_roll_frames=schedule["pre_roll_frames"],
            cycle_start_frame=schedule["cycle_start_frame"],
            cycle_end_frame=schedule["cycle_end_frame"],
            cycle_count=schedule["cycle_count"],
            tracks=tuple(RecordingTrackEvidence.from_dict(item) for item in tracks),
        )


@dataclass(frozen=True, slots=True)
class RecordingEvidenceLedger:
    project_id: str
    commits: tuple[RecordingCommitEvidence, ...] = ()
    schema_version: int = RECORDING_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECORDING_EVIDENCE_SCHEMA_VERSION:
            raise ProjectRecordingCommitError(
                "Unsupported recording evidence schema."
            )
        object.__setattr__(
            self,
            "project_id",
            _canonical_uuid(self.project_id, "evidence project_id"),
        )
        if (
            not isinstance(self.commits, tuple)
            or len(self.commits) > MAX_RECORDING_COMMITS
            or any(not isinstance(item, RecordingCommitEvidence) for item in self.commits)
        ):
            raise ProjectRecordingCommitError("recording evidence commits are invalid.")
        if len({item.commit_id for item in self.commits}) != len(self.commits):
            raise ProjectRecordingCommitError(
                "recording evidence contains duplicate commit IDs."
            )

    def append(self, entry: RecordingCommitEvidence) -> RecordingEvidenceLedger:
        if not isinstance(entry, RecordingCommitEvidence):
            raise ProjectRecordingCommitError(
                "entry must be RecordingCommitEvidence."
            )
        for existing in self.commits:
            if existing.commit_id == entry.commit_id:
                if existing != entry:
                    raise ProjectRecordingCommitError(
                        "Recording evidence commit ID has different content."
                    )
                return self
        if len(self.commits) >= MAX_RECORDING_COMMITS:
            raise ProjectRecordingCommitError(
                "Recording evidence ledger limit reached."
            )
        return replace(self, commits=(*self.commits, entry))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "commits": [item.to_dict() for item in self.commits],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecordingEvidenceLedger:
        _strict_keys(
            value,
            fields={"schema_version", "project_id", "commits"},
            label="recording evidence ledger",
        )
        commits = value["commits"]
        if not isinstance(commits, list) or not all(
            isinstance(item, Mapping) for item in commits
        ):
            raise ProjectRecordingCommitError(
                "recording evidence commits must be a list of objects."
            )
        return cls(
            schema_version=value["schema_version"],
            project_id=value["project_id"],
            commits=tuple(RecordingCommitEvidence.from_dict(item) for item in commits),
        )


@dataclass(frozen=True, slots=True)
class ProjectRecordingCommitResult:
    state: ProjectRecordingCommitState
    commit_id: str
    project: SongProject
    document: StudioDocument
    project_token: str
    studio_token: str | None
    evidence: RecordingCommitEvidence | None
    imported_media_ids: tuple[str, ...]
    region_ids: tuple[str, ...]
    lane_ids: tuple[str, ...]
    skipped_track_ids: tuple[str, ...] = ()
    notice: str = ""


@dataclass(frozen=True, slots=True)
class ProjectRecordingRecoveryCandidate:
    commit_id: str
    stage: str
    can_resume: bool
    notice: str


@dataclass(frozen=True, slots=True)
class _SourcePlan:
    recording: ProjectTrackRecording
    media_id: str
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.recording.track.track_id,
            "media_id": self.media_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _json_bytes(value: Mapping[str, object], maximum: int, label: str) -> bytes:
    try:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ProjectRecordingCommitError(f"{label} could not be serialized.") from None
    if len(payload) > maximum:
        raise ProjectRecordingCommitError(f"{label} is too large.")
    return payload


def _bundle(bundle_path: str | Path, project: SongProject | None = None) -> Path:
    try:
        loaded = load_project_bundle(bundle_path)
    except SongProjectStoreError:
        raise ProjectRecordingCommitError(
            "The song project bundle could not be verified."
        ) from None
    if project is not None and loaded.project.project_id != project.project_id:
        raise ProjectRecordingCommitError(
            "The recording belongs to a different song project."
        )
    return loaded.bundle_path


def _safe_target(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise ProjectRecordingCommitError(f"Could not inspect {label}.") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProjectRecordingCommitError(
            f"{label.capitalize()} must be one regular file."
        )


@contextmanager
def _recording_commit_lock(folder: Path):
    """Serialize commit/recovery independently from the project-store lock."""

    with _LOCKS_GUARD:
        process_lock = _LOCKS.setdefault(folder, threading.RLock())
    with process_lock:
        path = folder / RECORDING_COMMIT_LOCK_FILENAME
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        handle = None
        locked = False
        try:
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            current = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise ProjectRecordingCommitError(
                    "Recording commit lock must be one stable regular file."
                )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            descriptor = -1
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            elif os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            yield
        except ProjectRecordingCommitError:
            raise
        except OSError:
            raise ProjectRecordingCommitError(
                "Could not acquire the recording commit lock."
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if handle is not None:
                try:
                    if locked and os.name == "posix":
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    elif locked and os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    handle.close()


def _read_member(path: Path, maximum: int, label: str) -> bytes | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or info.st_size > maximum
        ):
            raise ProjectRecordingCommitError(
                f"{label.capitalize()} is not a safe bounded regular file."
            )
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise ProjectRecordingCommitError(
                    f"{label.capitalize()} changed while it was read."
                )
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        published = path.lstat()
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            or (published.st_dev, published.st_ino)
            != (info.st_dev, info.st_ino)
        ):
            raise ProjectRecordingCommitError(
                f"{label.capitalize()} changed while it was read."
            )
        return b"".join(chunks)
    except ProjectRecordingCommitError:
        raise
    except OSError:
        raise ProjectRecordingCommitError(f"Could not read {label}.") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_json(data: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProjectRecordingCommitError(
            f"{label.capitalize()} is not valid UTF-8 JSON."
        ) from None
    if not isinstance(value, Mapping):
        raise ProjectRecordingCommitError(
            f"{label.capitalize()} root must be an object."
        )
    return value


def _write_member(
    folder: Path,
    filename: str,
    payload: bytes,
    label: str,
) -> None:
    path = folder / filename
    try:
        with project_store_lock(folder):
            _safe_target(path, label)
            atomic_write_bytes(path, payload, mode=0o600)
    except (OSError, SongProjectStoreError, ProjectRecordingCommitError):
        raise ProjectRecordingCommitError(f"Could not save {label}.") from None


def _remove_member(folder: Path, filename: str, label: str) -> None:
    path = folder / filename
    try:
        with project_store_lock(folder):
            _safe_target(path, label)
            try:
                path.unlink()
            except FileNotFoundError:
                return
            if os.name == "posix":
                descriptor = os.open(
                    folder,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
    except (OSError, SongProjectStoreError, ProjectRecordingCommitError):
        raise ProjectRecordingCommitError(f"Could not remove {label}.") from None


def load_recording_evidence(
    bundle_path: str | Path,
    project: SongProject,
) -> RecordingEvidenceLedger:
    """Load strict, path-free recording evidence or return an empty ledger."""

    folder = _bundle(bundle_path, project)
    data = _read_member(
        folder / RECORDING_EVIDENCE_FILENAME,
        MAX_RECORDING_EVIDENCE_BYTES,
        "recording evidence",
    )
    if data is None:
        return RecordingEvidenceLedger(project_id=project.project_id)
    ledger = RecordingEvidenceLedger.from_dict(
        _decode_json(data, "recording evidence")
    )
    if ledger.project_id != project.project_id:
        raise ProjectRecordingCommitError(
            "Recording evidence belongs to a different project."
        )
    return ledger


def copy_recording_evidence_for_project_copy(
    source_bundle_path: str | Path,
    source_project: SongProject,
    destination_bundle_path: str | Path,
    destination_project: SongProject,
    *,
    expected_source_token: str,
    expected_destination_token: str,
) -> RecordingEvidenceLedger:
    """Carry path-free recording evidence through an explicit Project Save As.

    The ordinary bundle copier preserves track/media IDs while assigning a new
    project ID.  Call this after that project copy succeeds (and before the UI
    treats Save As as complete) so recording/dropout lineage follows those
    preserved IDs without copying stale transaction recovery data.
    """

    source_folder = _bundle(source_bundle_path, source_project)
    destination_folder = _bundle(destination_bundle_path, destination_project)
    if (
        source_folder == destination_folder
        or source_project.project_id == destination_project.project_id
    ):
        raise ProjectRecordingCommitError(
            "Recording evidence copy requires a new project destination."
        )
    with _recording_commit_lock(source_folder):
        if _load_journal(source_folder) is not None:
            raise ProjectRecordingCommitError(
                "Resolve recording commit recovery before copying the project."
            )
        source_loaded = load_project_bundle(source_folder)
        if (
            source_loaded.token != expected_source_token
            or source_loaded.project != source_project
        ):
            raise ProjectRecordingCommitError(
                "Source project changed before recording evidence was copied."
            )
        source_ledger = load_recording_evidence(
            source_folder,
            source_project,
        )

    destination_track_ids = {item.track_id for item in destination_project.tracks}
    destination_media_ids = {item.media_id for item in destination_project.media}
    for entry in source_ledger.commits:
        for item in entry.tracks:
            if (
                item.track_id not in destination_track_ids
                or item.media_id not in destination_media_ids
            ):
                raise ProjectRecordingCommitError(
                    "Project copy does not preserve recorded track/media lineage."
                )
    copied = RecordingEvidenceLedger(
        project_id=destination_project.project_id,
        commits=source_ledger.commits,
    )
    with _recording_commit_lock(destination_folder):
        if _load_journal(destination_folder) is not None:
            raise ProjectRecordingCommitError(
                "Destination recording commit recovery must be resolved first."
            )
        destination_loaded = load_project_bundle(destination_folder)
        if (
            destination_loaded.token != expected_destination_token
            or destination_loaded.project != destination_project
        ):
            raise ProjectRecordingCommitError(
                "Destination project changed before recording evidence was copied."
            )
        existing = load_recording_evidence(
            destination_folder,
            destination_project,
        )
        if existing.commits and existing != copied:
            raise ProjectRecordingCommitError(
                "Destination already contains different recording evidence."
            )
        if source_ledger.commits and existing != copied:
            _save_recording_evidence(destination_folder, copied)
    return copied


def _save_recording_evidence(
    folder: Path,
    ledger: RecordingEvidenceLedger,
) -> None:
    payload = _json_bytes(
        ledger.to_dict(),
        MAX_RECORDING_EVIDENCE_BYTES,
        "Recording evidence",
    )
    primary = folder / RECORDING_EVIDENCE_FILENAME
    previous = _read_member(
        primary,
        MAX_RECORDING_EVIDENCE_BYTES,
        "recording evidence",
    )
    if previous is not None:
        # A last-known-good ledger is useful even after the commit journal has
        # been cleared. Validate it before preserving it as the backup.
        RecordingEvidenceLedger.from_dict(
            _decode_json(previous, "recording evidence")
        )
        _write_member(
            folder,
            RECORDING_EVIDENCE_BACKUP_FILENAME,
            previous,
            "recording evidence backup",
        )
    _write_member(
        folder,
        RECORDING_EVIDENCE_FILENAME,
        payload,
        "recording evidence",
    )


def _hash_regular_file(path: Path) -> tuple[str, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            or before.st_size <= 0
        ):
            raise ProjectRecordingCommitError(
                "A project recording source is not one stable regular file."
            )
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        published = path.lstat()
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or (published.st_dev, published.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise ProjectRecordingCommitError(
                "A project recording source changed before collection."
            )
        return digest.hexdigest(), before.st_size
    except ProjectRecordingCommitError:
        raise
    except OSError:
        raise ProjectRecordingCommitError(
            "A project recording source could not be verified."
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _inspect_source(recording: ProjectTrackRecording) -> tuple[str, int]:
    if recording.file is None:
        raise ProjectRecordingCommitError(
            "A successful project recording track has no WAV file."
        )
    path = Path(recording.file)
    digest, size_bytes = _hash_regular_file(path)
    try:
        import soundfile as sf  # type: ignore
    except Exception:
        raise ProjectRecordingCommitError(
            "Project recording verification is unavailable in this build."
        ) from None
    try:
        before = path.lstat()
        info = sf.info(str(path))
        after = path.lstat()
    except Exception:
        raise ProjectRecordingCommitError(
            "A project recording WAV could not be inspected."
        ) from None
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or int(info.samplerate) != PROJECT_AUDIO_SAMPLE_RATE
        or int(info.channels) != recording.track.channels
        or int(info.frames) != recording.frame_count
        or recording.frame_count <= 0
    ):
        raise ProjectRecordingCommitError(
            "A project recording WAV does not match its capture metadata."
        )
    return digest, size_bytes


def _segments_for(
    schedule: ProjectRecordingSchedule,
    frame_count: int,
) -> tuple[ProjectRecordingSegment, ...]:
    remaining = _integer(frame_count, "recording frame_count", minimum=1)
    output_start = 0
    cycle_index = 0
    result: list[ProjectRecordingSegment] = []
    while remaining and cycle_index < schedule.cycle_count:
        amount = min(remaining, schedule.punch_frames)
        result.append(
            ProjectRecordingSegment(
                output_start_frame=output_start,
                project_start_frame=schedule.punch_in_frame,
                frame_count=amount,
                cycle_index=cycle_index,
            )
        )
        output_start += amount
        remaining -= amount
        cycle_index += 1
    if remaining:
        raise ProjectRecordingCommitError(
            "Recorded media exceeds the configured punch/cycle schedule."
        )
    return tuple(result)


def _dropout_evidence(
    recording: ProjectTrackRecording,
) -> tuple[RecordingDropoutEvidence, ...]:
    values: list[RecordingDropoutEvidence] = []
    for dropout in recording.dropouts:
        if not isinstance(dropout, ProjectRecordingDropout):
            raise ProjectRecordingCommitError(
                "Project recording dropout metadata is invalid."
            )
        if dropout.track_id != recording.track.track_id:
            raise ProjectRecordingCommitError(
                "Project recording dropout belongs to another track."
            )
        values.append(
            RecordingDropoutEvidence(
                output_start_frame=dropout.output_start_frame,
                frame_count=dropout.frame_count,
                channels=tuple(dropout.channels),
                reason=dropout.reason,
            )
        )
    return tuple(values)


def _validate_current(
    folder: Path,
    project: SongProject,
    document: StudioDocument,
    *,
    expected_project_token: str | None,
    expected_studio_token: str | None,
) -> RecordingEvidenceLedger:
    if not isinstance(project, SongProject):
        raise ProjectRecordingCommitError("project must be a SongProject.")
    if project.project_sample_rate != PROJECT_AUDIO_SAMPLE_RATE:
        raise ProjectRecordingCommitError(
            "Studio recording commit requires a 48 kHz song project."
        )
    if (
        not isinstance(document, StudioDocument)
        or document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION
        or document.project_id != project.project_id
        or document.project_sample_rate != project.project_sample_rate
    ):
        raise ProjectRecordingCommitError(
            "Studio recording state does not match this song project."
        )
    try:
        loaded_project = load_project_bundle(folder)
        loaded_studio = load_song_studio_document(folder, loaded_project.project)
    except (SongProjectStoreError, SongStudioStoreError):
        raise ProjectRecordingCommitError(
            "Current project state could not be verified."
        ) from None
    if (
        loaded_project.token != expected_project_token
        or loaded_project.project != project
    ):
        raise ProjectRecordingCommitError(
            "Project changed before the recording could be committed."
        )
    if (
        loaded_studio.token != expected_studio_token
        or loaded_studio.document != document
    ):
        raise ProjectRecordingCommitError(
            "Studio arrangement changed before the recording could be committed."
        )
    return load_recording_evidence(folder, project)


def _successful_recordings(
    result: ProjectRecordingResult,
    *,
    allow_recovered: bool,
) -> tuple[tuple[ProjectTrackRecording, ...], tuple[str, ...]]:
    if not isinstance(result, ProjectRecordingResult):
        raise ProjectRecordingCommitError(
            "result must be a ProjectRecordingResult."
        )
    if not isinstance(result.schedule, ProjectRecordingSchedule):
        raise ProjectRecordingCommitError(
            "Project recording schedule metadata is invalid."
        )
    _integer(
        result.generation,
        "recording generation",
        minimum=1,
        maximum=(1 << 63) - 1,
    )
    _integer(
        result.input_frames_seen,
        "recording input_frames_seen",
    )
    output_frames = _integer(
        result.output_frames,
        "recording output_frames",
        maximum=result.schedule.scheduled_output_frames,
    )
    if not isinstance(result.tracks, tuple) or any(
        not isinstance(item, ProjectTrackRecording) for item in result.tracks
    ):
        raise ProjectRecordingCommitError(
            "Project recording track results are invalid."
        )
    if result.state is ProjectRecorderState.COMPLETED and result.published:
        candidates = tuple(item for item in result.tracks if item.file is not None)
        if output_frames <= 0 or any(
            item.frame_count != output_frames for item in candidates
        ):
            raise ProjectRecordingCommitError(
                "Published project recording track lengths do not agree."
            )
        expected_segments = _segments_for(result.schedule, output_frames)
        if result.segments != expected_segments:
            raise ProjectRecordingCommitError(
                "Published project recording segment metadata is inconsistent."
            )
    elif allow_recovered and result.state is ProjectRecorderState.FAILED:
        candidates = tuple(
            item
            for item in result.tracks
            if item.file is not None and item.recovered and item.frame_count > 0
        )
    else:
        raise ProjectRecordingCommitError(
            "Only a published recording or explicitly accepted recovery can be committed."
        )
    if not candidates:
        raise ProjectRecordingCommitError(
            "The recording contains no successful track WAVs."
        )
    if len({item.track.track_id for item in candidates}) != len(candidates):
        raise ProjectRecordingCommitError(
            "The recording contains duplicate track results."
        )
    skipped = tuple(
        item.track.track_id
        for item in result.tracks
        if item not in candidates
    )
    return candidates, skipped


def _build_source_plans(
    result: ProjectRecordingResult,
    recordings: tuple[ProjectTrackRecording, ...],
    commit_id: str,
) -> tuple[_SourcePlan, ...]:
    plans: list[_SourcePlan] = []
    for recording in recordings:
        track_id = _canonical_uuid(recording.track.track_id, "recording track_id")
        digest, size_bytes = _inspect_source(recording)
        media_id = str(
            uuid.uuid5(
                _RECORDING_NAMESPACE,
                f"{commit_id}:media:{track_id}",
            )
        )
        plans.append(
            _SourcePlan(
                recording=recording,
                media_id=media_id,
                relative_path=f"Media/{media_id}.wav",
                sha256=digest,
                size_bytes=size_bytes,
            )
        )
    return tuple(plans)


def _build_document_and_evidence(
    project: SongProject,
    document: StudioDocument,
    result: ProjectRecordingResult,
    plans: tuple[_SourcePlan, ...],
    commit_id: str,
) -> tuple[StudioDocument, RecordingCommitEvidence]:
    project_tracks = {item.track_id: item for item in project.tracks}
    studio_tracks = {item.track_id: item for item in document.tracks}
    lane_region_ids = {
        region_id for lane in document.take_lanes if not lane.deleted for region_id in lane.region_ids
    }
    regions = list(document.regions)
    lanes = list(document.take_lanes)
    evidence_tracks: list[RecordingTrackEvidence] = []

    for plan in plans:
        recording = plan.recording
        track_id = _canonical_uuid(recording.track.track_id, "recording track_id")
        project_track = project_tracks.get(track_id)
        studio_track = studio_tracks.get(track_id)
        if project_track is None or studio_track is None:
            raise ProjectRecordingCommitError(
                "An armed recording track is no longer in the project."
            )
        if (
            studio_track.kind is not StudioTrackKind.AUDIO
            or studio_track.channel_count != recording.track.channels
        ):
            raise ProjectRecordingCommitError(
                "A recorded channel mapping no longer matches its Studio track."
            )
        if (
            project_track.input_mapping is not None
            and len(project_track.input_mapping.channels) != recording.track.channels
        ):
            raise ProjectRecordingCommitError(
                "A recorded channel mapping no longer matches its project track."
            )

        segments = _segments_for(result.schedule, recording.frame_count)
        compensated_start = (
            result.schedule.punch_in_frame
            - recording.track.latency_compensation_frames
        )
        compensated_end = compensated_start + max(
            segment.frame_count for segment in segments
        )
        has_base = any(
            item.track_id == track_id
            and item.region_id not in lane_region_ids
            and item.enabled
            and not item.deleted
            and item.timeline_start_frame < compensated_end
            and item.timeline_end_frame > compensated_start
            for item in document.regions
        )
        next_lane_order = (
            max(
                (
                    item.order
                    for item in document.take_lanes
                    if item.track_id == track_id
                ),
                default=-1,
            )
            + 1
        )
        pass_evidence: list[RecordingPassEvidence] = []
        for segment in segments:
            region_id = str(
                uuid.uuid5(
                    _RECORDING_NAMESPACE,
                    f"{commit_id}:region:{track_id}:{segment.cycle_index}",
                )
            )
            timeline_start = (
                segment.project_start_frame
                - recording.track.latency_compensation_frames
            )
            lane_id: str | None = None
            if has_base:
                lane_id = str(
                    uuid.uuid5(
                        _RECORDING_NAMESPACE,
                        f"{commit_id}:lane:{track_id}:{segment.cycle_index}",
                    )
                )
            region = StudioRegion(
                region_id=region_id,
                track_id=track_id,
                source_media_id=plan.media_id,
                source_start_frame=segment.output_start_frame,
                source_frame_count=segment.frame_count,
                timeline_start_frame=timeline_start,
                timeline_frame_count=segment.frame_count,
            )
            regions.append(region)
            if lane_id is not None:
                lanes.append(
                    StudioTakeLane(
                        lane_id=lane_id,
                        track_id=track_id,
                        source_media_id=plan.media_id,
                        name=f"Take {next_lane_order + 1}",
                        order=next_lane_order,
                        region_ids=(region_id,),
                    )
                )
                next_lane_order += 1
            else:
                has_base = True
            pass_evidence.append(
                RecordingPassEvidence(
                    cycle_index=segment.cycle_index,
                    region_id=region_id,
                    lane_id=lane_id,
                    source_start_frame=segment.output_start_frame,
                    frame_count=segment.frame_count,
                    timeline_start_frame=timeline_start,
                )
            )
        evidence_tracks.append(
            RecordingTrackEvidence(
                track_id=track_id,
                media_id=plan.media_id,
                input_channels=tuple(recording.track.channel_map),
                latency_compensation_frames=(
                    recording.track.latency_compensation_frames
                ),
                frame_count=recording.frame_count,
                overflow_frames=recording.overflow_frames,
                recovered=recording.recovered,
                dropouts=_dropout_evidence(recording),
                passes=tuple(pass_evidence),
            )
        )

    try:
        updated_document = replace(
            document,
            revision=document.revision + 1,
            regions=tuple(regions),
            take_lanes=tuple(lanes),
        )
    except StudioProjectError:
        raise ProjectRecordingCommitError(
            "Recorded regions could not be added to the Studio arrangement."
        ) from None
    schedule = result.schedule
    evidence = RecordingCommitEvidence(
        commit_id=commit_id,
        generation=result.generation,
        punch_in_frame=schedule.punch_in_frame,
        punch_out_frame=schedule.punch_out_frame,
        count_in_frames=schedule.count_in_frames,
        pre_roll_frames=schedule.pre_roll_frames,
        cycle_start_frame=schedule.cycle_start_frame,
        cycle_end_frame=schedule.cycle_end_frame,
        cycle_count=schedule.cycle_count,
        tracks=tuple(evidence_tracks),
    )
    return updated_document, evidence


def _journal_payload(
    *,
    commit_id: str,
    stage: str,
    project_id: str,
    expected_project_token: str | None,
    expected_studio_token: str | None,
    plans: tuple[_SourcePlan, ...],
    project: SongProject | None = None,
    document: StudioDocument | None = None,
    evidence: RecordingCommitEvidence | None = None,
) -> dict[str, object]:
    return {
        "schema_version": RECORDING_COMMIT_JOURNAL_SCHEMA_VERSION,
        "commit_id": commit_id,
        "stage": stage,
        "project_id": project_id,
        "expected_project_token": expected_project_token,
        "expected_studio_token": expected_studio_token,
        "media": [item.to_dict() for item in plans],
        "project": project.to_dict() if project is not None else None,
        "document": document.to_dict() if document is not None else None,
        "evidence": evidence.to_dict() if evidence is not None else None,
    }


def _write_journal(folder: Path, value: Mapping[str, object]) -> None:
    _write_member(
        folder,
        RECORDING_COMMIT_JOURNAL_FILENAME,
        _json_bytes(
            value,
            MAX_RECORDING_COMMIT_JOURNAL_BYTES,
            "Recording commit recovery data",
        ),
        "recording commit recovery data",
    )


def _load_journal(folder: Path) -> Mapping[str, Any] | None:
    data = _read_member(
        folder / RECORDING_COMMIT_JOURNAL_FILENAME,
        MAX_RECORDING_COMMIT_JOURNAL_BYTES,
        "recording commit recovery data",
    )
    if data is None:
        return None
    value = _decode_json(data, "recording commit recovery data")
    _strict_keys(
        value,
        fields={
            "schema_version",
            "commit_id",
            "stage",
            "project_id",
            "expected_project_token",
            "expected_studio_token",
            "media",
            "project",
            "document",
            "evidence",
        },
        label="recording commit recovery data",
    )
    if value["schema_version"] != RECORDING_COMMIT_JOURNAL_SCHEMA_VERSION:
        raise ProjectRecordingCommitError(
            "Unsupported recording commit recovery schema."
        )
    _canonical_uuid(value["commit_id"], "journal commit_id")
    _canonical_uuid(value["project_id"], "journal project_id")
    if value["stage"] not in {"prepared", "ready"}:
        raise ProjectRecordingCommitError(
            "Recording commit recovery stage is invalid."
        )
    for token_name in ("expected_project_token", "expected_studio_token"):
        token = value[token_name]
        if token is not None and (
            not isinstance(token, str) or not _SHA256_RE.fullmatch(token)
        ):
            raise ProjectRecordingCommitError(
                "Recording commit recovery token is invalid."
            )
    media = value["media"]
    if not isinstance(media, list) or not 1 <= len(media) <= MAX_EVIDENCE_TRACKS:
        raise ProjectRecordingCommitError(
            "Recording commit recovery media inventory is invalid."
        )
    required_media = {
        "track_id",
        "media_id",
        "relative_path",
        "sha256",
        "size_bytes",
    }
    for item in media:
        if not isinstance(item, Mapping):
            raise ProjectRecordingCommitError(
                "Recording commit recovery media inventory is invalid."
            )
        _strict_keys(item, fields=required_media, label="journal media")
        _canonical_uuid(item["track_id"], "journal track_id")
        media_id = _canonical_uuid(item["media_id"], "journal media_id")
        if item["relative_path"] != f"Media/{media_id}.wav":
            raise ProjectRecordingCommitError(
                "Recording commit recovery media path is invalid."
            )
        if not isinstance(item["sha256"], str) or not _SHA256_RE.fullmatch(
            item["sha256"]
        ):
            raise ProjectRecordingCommitError(
                "Recording commit recovery media checksum is invalid."
            )
        _integer(
            item["size_bytes"],
            "journal media size",
            minimum=1,
            maximum=512 * 1024 * 1024 * 1024,
        )
    if value["stage"] == "prepared":
        if any(value[name] is not None for name in ("project", "document", "evidence")):
            raise ProjectRecordingCommitError(
                "Prepared recording recovery data contains committed state."
            )
    else:
        if not isinstance(value["project"], Mapping):
            raise ProjectRecordingCommitError(
                "Ready recording recovery data has no project."
            )
        if not isinstance(value["document"], Mapping):
            raise ProjectRecordingCommitError(
                "Ready recording recovery data has no Studio document."
            )
        if not isinstance(value["evidence"], Mapping):
            raise ProjectRecordingCommitError(
                "Ready recording recovery data has no evidence."
            )
    return value


def _file_matches(path: Path, sha256: str, size_bytes: int) -> bool:
    try:
        digest, size = _hash_regular_file(path)
    except ProjectRecordingCommitError:
        return False
    return digest == sha256 and size == size_bytes


def _cleanup_media(
    folder: Path,
    media_values: list[Mapping[str, Any]],
    current: SongProject,
) -> bool:
    declared = {item.media_id for item in current.media}
    succeeded = True
    removed = False
    for item in media_values:
        if item["media_id"] in declared:
            succeeded = False
            continue
        path = folder / item["relative_path"]
        try:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                succeeded = False
                continue
            if not _file_matches(path, item["sha256"], item["size_bytes"]):
                succeeded = False
                continue
            path.unlink()
            removed = True
        except OSError:
            succeeded = False
    if removed and os.name == "posix":
        descriptor = -1
        try:
            descriptor = os.open(
                folder / "Media",
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            os.fsync(descriptor)
        except OSError:
            succeeded = False
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return succeeded


def _inspect_project_recording_recovery_locked(
    bundle_path: str | Path,
) -> ProjectRecordingRecoveryCandidate | None:
    """Report a pending transaction without mutating it."""

    folder = _bundle(bundle_path)
    journal = _load_journal(folder)
    if journal is None:
        return None
    loaded = load_project_bundle(folder)
    can_resume = (
        journal["stage"] == "ready"
        and (
            loaded.token == journal["expected_project_token"]
            or loaded.project.to_dict() == journal["project"]
        )
    )
    return ProjectRecordingRecoveryCandidate(
        commit_id=journal["commit_id"],
        stage=journal["stage"],
        can_resume=can_resume,
        notice=(
            "A Studio recording commit can be resumed."
            if can_resume
            else "A Studio recording commit needs explicit recovery review."
        ),
    )


def inspect_project_recording_recovery(
    bundle_path: str | Path,
) -> ProjectRecordingRecoveryCandidate | None:
    """Report a pending transaction without racing an active commit."""

    folder = _bundle(bundle_path)
    with _recording_commit_lock(folder):
        return _inspect_project_recording_recovery_locked(folder)


def _result(
    state: ProjectRecordingCommitState,
    commit_id: str,
    project: SongProject,
    document: StudioDocument,
    project_token: str,
    studio_token: str | None,
    evidence: RecordingCommitEvidence | None,
    *,
    skipped: tuple[str, ...] = (),
    notice: str = "",
) -> ProjectRecordingCommitResult:
    tracks = evidence.tracks if evidence is not None else ()
    return ProjectRecordingCommitResult(
        state=state,
        commit_id=commit_id,
        project=project,
        document=document,
        project_token=project_token,
        studio_token=studio_token,
        evidence=evidence,
        imported_media_ids=tuple(item.media_id for item in tracks),
        region_ids=tuple(
            item.region_id for track in tracks for item in track.passes
        ),
        lane_ids=tuple(
            item.lane_id
            for track in tracks
            for item in track.passes
            if item.lane_id is not None
        ),
        skipped_track_ids=skipped,
        notice=notice,
    )


def _commit_project_recording_locked(
    bundle_path: str | Path,
    project: SongProject,
    document: StudioDocument,
    result: ProjectRecordingResult,
    *,
    expected_project_token: str | None,
    expected_studio_token: str | None,
    commit_id: str | None = None,
    allow_recovered: bool = False,
) -> ProjectRecordingCommitResult:
    """Collect one recording and atomically publish its durable Studio state.

    ``allow_recovered`` must be explicitly true to collect validated partial
    WAVs from a failed recorder result.  Positive latency compensation moves
    the region earlier on the project timeline; the untouched source samples,
    the signed compensation, count-in/pre-roll, cycles, and zero-filled
    dropout intervals remain in durable evidence.
    """

    folder = _bundle(bundle_path, project)
    ledger = _validate_current(
        folder,
        project,
        document,
        expected_project_token=expected_project_token,
        expected_studio_token=expected_studio_token,
    )
    if _load_journal(folder) is not None:
        raise ProjectRecordingCommitError(
            "Another Studio recording commit requires recovery first."
        )
    identifier = (
        _canonical_uuid(commit_id, "commit_id")
        if commit_id is not None
        else str(uuid.uuid4())
    )
    recordings, skipped = _successful_recordings(
        result,
        allow_recovered=allow_recovered,
    )
    plans = _build_source_plans(result, recordings, identifier)
    prepared = _journal_payload(
        commit_id=identifier,
        stage="prepared",
        project_id=project.project_id,
        expected_project_token=expected_project_token,
        expected_studio_token=expected_studio_token,
        plans=plans,
    )
    _write_journal(folder, prepared)

    updated_project = project
    try:
        for plan in plans:
            imported = import_project_media(
                folder,
                updated_project,
                plan.recording.file,
                provenance=MediaProvenance.LOCAL_RECORDING,
                import_method=MediaImportMethod.RECORDING,
                provenance_detail=f"recording {identifier}",
                media_id=plan.media_id,
            )
            if (
                imported.media.sha256 != plan.sha256
                or imported.media.size_bytes != plan.size_bytes
                or imported.media.sample_rate != PROJECT_AUDIO_SAMPLE_RATE
                or imported.media.channels != plan.recording.track.channels
                or imported.media.frame_count != plan.recording.frame_count
                or imported.media.path != plan.relative_path
            ):
                raise ProjectRecordingCommitError(
                    "A recording changed while it was collected."
                )
            updated_project = imported.project
        updated_document, evidence = _build_document_and_evidence(
            updated_project,
            document,
            result,
            plans,
            identifier,
        )
        updated_ledger = ledger.append(evidence)
        ready = _journal_payload(
            commit_id=identifier,
            stage="ready",
            project_id=project.project_id,
            expected_project_token=expected_project_token,
            expected_studio_token=expected_studio_token,
            plans=plans,
            project=updated_project,
            document=updated_document,
            evidence=evidence,
        )
        # Ensure evidence serialization is bounded before making the project
        # manifest reference the newly collected media.
        _json_bytes(
            updated_ledger.to_dict(),
            MAX_RECORDING_EVIDENCE_BYTES,
            "Recording evidence",
        )
        _write_journal(folder, ready)

        saved_project = save_project_bundle(
            folder,
            updated_project,
            expected_token=expected_project_token,
        )
        saved_studio = save_song_studio_document(
            folder,
            saved_project.project,
            updated_document,
            expected_token=expected_studio_token,
        )
        _save_recording_evidence(folder, updated_ledger)
        _remove_member(
            folder,
            RECORDING_COMMIT_JOURNAL_FILENAME,
            "recording commit recovery data",
        )
        return _result(
            ProjectRecordingCommitState.COMMITTED,
            identifier,
            saved_project.project,
            saved_studio.document,
            saved_project.token,
            saved_studio.token,
            evidence,
            skipped=skipped,
            notice=(
                "Recovered partial recording committed."
                if any(item.recovered for item in recordings)
                else "Recording committed."
            ),
        )
    except Exception as exc:
        try:
            current = load_project_bundle(folder)
            rolled_back = (
                current.token == expected_project_token
                and current.project == project
                and _cleanup_media(
                    folder,
                    list(prepared["media"]),  # type: ignore[arg-type]
                    current.project,
                )
            )
            if rolled_back:
                _remove_member(
                    folder,
                    RECORDING_COMMIT_JOURNAL_FILENAME,
                    "recording commit recovery data",
                )
        except Exception:
            rolled_back = False
        if rolled_back:
            if isinstance(exc, ProjectRecordingCommitError):
                raise
            raise ProjectRecordingCommitError(
                "The recording commit failed before project state changed."
            ) from None
        raise ProjectRecordingCommitRecoveryRequired(
            identifier,
            "The recording is protected and requires explicit commit recovery.",
        ) from None


def commit_project_recording(
    bundle_path: str | Path,
    project: SongProject,
    document: StudioDocument,
    result: ProjectRecordingResult,
    *,
    expected_project_token: str | None,
    expected_studio_token: str | None,
    commit_id: str | None = None,
    allow_recovered: bool = False,
) -> ProjectRecordingCommitResult:
    """Serialize one all-or-recoverable Studio recording transaction."""

    folder = _bundle(bundle_path, project)
    with _recording_commit_lock(folder):
        return _commit_project_recording_locked(
            folder,
            project,
            document,
            result,
            expected_project_token=expected_project_token,
            expected_studio_token=expected_studio_token,
            commit_id=commit_id,
            allow_recovered=allow_recovered,
        )


def _recover_project_recording_commit_locked(
    bundle_path: str | Path,
) -> ProjectRecordingCommitResult:
    """Resume a ready transaction or safely roll back an uncommitted prepare."""

    folder = _bundle(bundle_path)
    journal = _load_journal(folder)
    if journal is None:
        raise ProjectRecordingCommitError(
            "No Studio recording commit requires recovery."
        )
    commit_id = journal["commit_id"]
    current = load_project_bundle(folder)
    if current.project.project_id != journal["project_id"]:
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Recording recovery belongs to a different project.",
        )
    media_values = list(journal["media"])
    if journal["stage"] == "prepared":
        if current.token != journal["expected_project_token"]:
            raise ProjectRecordingCommitRecoveryRequired(
                commit_id,
                "Project changed while a recording prepare was pending.",
            )
        if not _cleanup_media(folder, media_values, current.project):
            raise ProjectRecordingCommitRecoveryRequired(
                commit_id,
                "Prepared recording media needs explicit recovery review.",
            )
        _remove_member(
            folder,
            RECORDING_COMMIT_JOURNAL_FILENAME,
            "recording commit recovery data",
        )
        studio = load_song_studio_document(folder, current.project)
        if current.token is None:
            raise ProjectRecordingCommitError(
                "Rolled-back recovery could not identify saved project state."
            )
        return _result(
            ProjectRecordingCommitState.ROLLED_BACK,
            commit_id,
            current.project,
            studio.document,
            current.token,
            studio.token,
            None,
            notice="Uncommitted recording media was safely rolled back.",
        )

    try:
        planned_project = SongProject.from_dict(journal["project"])
        planned_document = studio_document_from_dict(journal["document"])
        evidence = RecordingCommitEvidence.from_dict(journal["evidence"])
    except (SongProjectError, StudioProjectError, ProjectRecordingCommitError):
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Recording recovery data is not valid.",
        ) from None
    if planned_project.project_id != current.project.project_id:
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Recording recovery project identity does not match.",
        )

    if current.project == planned_project:
        saved_project = current
    elif current.token == journal["expected_project_token"]:
        try:
            verify_project_media(folder, planned_project)
            saved_project = save_project_bundle(
                folder,
                planned_project,
                expected_token=current.token,
            )
        except (SongProjectStoreError, SongProjectConflict):
            raise ProjectRecordingCommitRecoveryRequired(
                commit_id,
                "Collected recording media could not be resumed safely.",
            ) from None
    else:
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Project changed after recording recovery was prepared.",
        )

    try:
        studio = load_song_studio_document(folder, saved_project.project)
        if studio.document == planned_document:
            saved_studio_document = studio.document
            studio_token = studio.token
        elif studio.token == journal["expected_studio_token"]:
            saved_studio = save_song_studio_document(
                folder,
                saved_project.project,
                planned_document,
                expected_token=studio.token,
            )
            saved_studio_document = saved_studio.document
            studio_token = saved_studio.token
        else:
            raise ProjectRecordingCommitRecoveryRequired(
                commit_id,
                "Studio arrangement changed after recording recovery was prepared.",
            )
        if studio_token is None:
            raise ProjectRecordingCommitRecoveryRequired(
                commit_id,
                "Studio recovery did not publish a primary arrangement.",
            )
        ledger = load_recording_evidence(folder, saved_project.project).append(evidence)
        _save_recording_evidence(folder, ledger)
        _remove_member(
            folder,
            RECORDING_COMMIT_JOURNAL_FILENAME,
            "recording commit recovery data",
        )
    except ProjectRecordingCommitRecoveryRequired:
        raise
    except (SongStudioStoreError, SongStudioConflict, ProjectRecordingCommitError):
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Recording recovery remains protected and can be retried.",
        ) from None
    if saved_project.token is None:
        raise ProjectRecordingCommitRecoveryRequired(
            commit_id,
            "Project recovery did not publish a primary manifest.",
        )
    return _result(
        ProjectRecordingCommitState.RECOVERED,
        commit_id,
        saved_project.project,
        saved_studio_document,
        saved_project.token,
        studio_token,
        evidence,
        notice="Recording commit recovery completed.",
    )


def recover_project_recording_commit(
    bundle_path: str | Path,
) -> ProjectRecordingCommitResult:
    """Explicitly resume/roll back one serialized recording transaction."""

    folder = _bundle(bundle_path)
    with _recording_commit_lock(folder):
        return _recover_project_recording_commit_locked(folder)


__all__ = [
    "MAX_RECORDING_COMMIT_JOURNAL_BYTES",
    "MAX_RECORDING_EVIDENCE_BYTES",
    "RECORDING_COMMIT_JOURNAL_FILENAME",
    "RECORDING_COMMIT_LOCK_FILENAME",
    "RECORDING_EVIDENCE_BACKUP_FILENAME",
    "RECORDING_EVIDENCE_FILENAME",
    "ProjectRecordingCommitError",
    "ProjectRecordingCommitRecoveryRequired",
    "ProjectRecordingCommitResult",
    "ProjectRecordingCommitState",
    "ProjectRecordingRecoveryCandidate",
    "RecordingCommitEvidence",
    "RecordingDropoutEvidence",
    "RecordingEvidenceLedger",
    "RecordingPassEvidence",
    "RecordingTrackEvidence",
    "commit_project_recording",
    "copy_recording_evidence_for_project_copy",
    "inspect_project_recording_recovery",
    "load_recording_evidence",
    "recover_project_recording_commit",
]

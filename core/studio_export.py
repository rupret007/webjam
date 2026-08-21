"""Transactional, editor-neutral exports of a Studio arrangement.

Every audio sample in an export comes from :mod:`core.studio_renderer`.
This module supplies only package policy: selected tracks, equal-length PCM24
files, evidence, cancellation, disk preflight, and atomic publication.  The
recording manifest, Studio document, and recorder media remain read-only.
"""

from __future__ import annotations

import csv
import ctypes
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

import numpy as np

from core.studio_project import (
    StudioDocument,
    StudioProjectError,
    default_studio_document,
    studio_document_from_dict,
)
from core.studio_renderer import (
    DEFAULT_RENDER_BLOCK_FRAMES,
    MAX_RENDER_BLOCK_FRAMES,
    StudioRenderer,
    StudioRenderError,
    studio_delivery_block,
)
from core.studio_source_catalog import (
    StudioSourceCatalog,
    StudioSourceCatalogError,
    StudioSourceKey,
)
from core.studio_store import STUDIO_STATE_FILENAME
from core.take_export import TakeExportError, validated_project_export_tracks
from core.take_project import (
    PROJECT_SCHEMA_VERSION,
    MediaSegment,
    ProjectTrack,
    TakeProject,
)

STUDIO_EXPORT_SCHEMA_VERSION = 1
DEFAULT_DISK_RESERVE_BYTES = 64 * 1024 * 1024
MAX_EXPORT_MANIFEST_BYTES = 16 * 1024 * 1024
_METADATA_ALLOWANCE_BYTES = 2 * 1024 * 1024
_HASH_BLOCK_BYTES = 1024 * 1024
_MAX_SIMULTANEOUS_STEM_WRITERS = 32
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._() -]+")
_EXPORT_LOCK_FILENAME = ".webjam-studio-export.lock"
_EXPORT_LOCKS_GUARD = threading.Lock()
_EXPORT_LOCKS: dict[Path, threading.RLock] = {}
_SECURE_DIR_FD_SUPPORTED = all(
    item in os.supports_dir_fd
    for item in (os.open, os.mkdir, os.stat, os.unlink, os.rmdir)
)
_SECURE_EXPORT_PLATFORM_SUPPORTED = os.name == "posix" and (
    sys.platform == "darwin" or sys.platform.startswith("linux")
)


class CancellationSignal(Protocol):
    def is_set(self) -> bool:
        """Return true when the caller wants the export cancelled."""


class StudioExportError(RuntimeError):
    """Raised when an authoritative Studio export cannot be completed."""


class StudioExportCancelled(StudioExportError):
    """Raised after cancellation has removed the unpublished package."""


class StudioExportPublishedError(StudioExportError):
    """Raised when a package was published but directory durability is unknown."""

    def __init__(self, folder: Path) -> None:
        super().__init__(
            "Studio export was published, but its directory sync could not be "
            "confirmed. Keep the package and verify it before relying on it."
        )
        self.folder = folder


def studio_export_supported() -> bool:
    """Return whether this runtime can publish descriptor-bound Studio exports."""

    return bool(_SECURE_EXPORT_PLATFORM_SUPPORTED and _SECURE_DIR_FD_SUPPORTED)


@dataclass(frozen=True)
class StudioExportResult:
    folder: Path
    edited_stems: tuple[Path, ...]
    original_stems: tuple[Path, ...]
    rough_mix: Path
    markers_csv: Path
    provenance: Path
    checksums: Path
    instructions: Path
    studio_document: Path
    source_manifest: Path
    source_manifests: tuple[Path, ...]
    sample_rate: int
    frames: int


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class _SourceSnapshot:
    take_id: str
    track_id: str
    source_id: str
    segment: MediaSegment
    path: Path
    sha256: str
    size_bytes: int

    @property
    def key(self) -> StudioSourceKey:
        return (self.take_id, self.track_id, self.segment.segment_id)


@dataclass(frozen=True)
class _TakeSnapshot:
    take_id: str
    root: Path
    root_identity: tuple[int, int, int]
    project: TakeProject
    manifest: _FileSnapshot


@dataclass(frozen=True)
class _OriginalStemPlan:
    """One immutable source track rendered on its manifest alignment timeline."""

    take_id: str
    track: ProjectTrack
    primary_compatible_name: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.take_id, self.track.track_id)


def _integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioExportError(f"{field_name} must be an integer.")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and {maximum}" if maximum is not None else ""
        raise StudioExportError(f"{field_name} must be between {minimum}{upper}.")
    return value


def _check_cancelled(signal: CancellationSignal | None) -> None:
    if signal is None:
        return
    checker = getattr(signal, "is_set", None)
    if not callable(checker):
        raise StudioExportError("cancel_event must provide is_set().")
    try:
        cancelled = checker()
    except Exception as exc:
        raise StudioExportError(
            "Could not read the export cancellation state."
        ) from exc
    if not isinstance(cancelled, bool):
        raise StudioExportError("cancel_event.is_set() must return true or false.")
    if cancelled:
        raise StudioExportCancelled("Studio export was cancelled.")


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("-", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return (cleaned or fallback)[:64]


def _open_regular_readonly(path: Path, label: str) -> tuple[int, os.stat_result]:
    """Open one exact regular inode without following a final symlink."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if not nofollow:
        try:
            before = path.lstat()
        except FileNotFoundError as exc:
            raise StudioExportError(f"The {label} is missing.") from exc
        except OSError as exc:
            raise StudioExportError(f"The {label} could not be inspected.") from exc
        if stat.S_ISLNK(before.st_mode):
            raise StudioExportError(f"The {label} must not be a symbolic link.")
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        if nofollow:
            try:
                if stat.S_ISLNK(path.lstat().st_mode):
                    raise StudioExportError(
                        f"The {label} must not be a symbolic link."
                    ) from exc
            except FileNotFoundError:
                pass
        raise StudioExportError(f"The {label} is missing or unreadable.") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StudioExportError(f"The {label} must be a regular file.")
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise StudioExportError(f"The {label} changed while it was opened.")
        if before is not None and (before.st_dev, before.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise StudioExportError(f"The {label} changed while it was opened.")
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _require_open_path_identity(
    path: Path,
    label: str,
    handle: BinaryIO,
) -> os.stat_result:
    """Confirm a bounded read still belongs to the file's published name."""

    try:
        info = os.fstat(handle.fileno())
        current = path.lstat()
    except OSError as exc:
        raise StudioExportError(f"The {label} changed while it was read.") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
    ):
        raise StudioExportError(f"The {label} changed while it was read.")
    return info


def _read_regular(path: Path, label: str, maximum_bytes: int) -> bytes:
    descriptor, info = _open_regular_readonly(path, label)
    try:
        if info.st_size > maximum_bytes:
            raise StudioExportError(f"The {label} is too large.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(maximum_bytes + 1)
            final_info = _require_open_path_identity(path, label, handle)
        if len(data) > maximum_bytes or final_info.st_size > maximum_bytes:
            raise StudioExportError(f"The {label} is too large.")
        return data
    except StudioExportError:
        raise
    except OSError as exc:
        raise StudioExportError(f"The {label} could not be read.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_file(
    path: Path,
    cancel_event: CancellationSignal | None = None,
    *,
    label: str = "export evidence file",
) -> str:
    digest = hashlib.sha256()
    descriptor, _info = _open_regular_readonly(path, label)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while True:
                _check_cancelled(cancel_event)
                block = handle.read(_HASH_BLOCK_BYTES)
                if not block:
                    break
                digest.update(block)
            _require_open_path_identity(path, label, handle)
    except StudioExportError:
        raise
    except OSError as exc:
        raise StudioExportError(f"The {label} could not be read.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _snapshot_file(path: Path, label: str) -> _FileSnapshot:
    data = _read_regular(path, label, MAX_EXPORT_MANIFEST_BYTES)
    return _FileSnapshot(
        path=path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _take_manifest_snapshot(
    take_root: Path,
    project: TakeProject,
) -> _FileSnapshot:
    path = take_root / "webjam-take.json"
    data = _read_regular(path, "take manifest", MAX_EXPORT_MANIFEST_BYTES)
    snapshot = _FileSnapshot(
        path=path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StudioExportError("The take manifest is not valid JSON.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PROJECT_SCHEMA_VERSION
    ):
        raise StudioExportError("A schema-v2 take manifest is required for export.")
    try:
        loaded = TakeProject.from_dict(payload)
    except Exception as exc:
        raise StudioExportError("The take manifest could not be trusted.") from exc
    if loaded.to_dict() != project.to_dict():
        raise StudioExportError(
            "The supplied take project does not match its manifest snapshot."
        )
    return snapshot


def _take_root_identity(path: Path) -> tuple[int, int, int]:
    """Return one stable regular-directory identity without following a symlink."""

    try:
        info = path.lstat()
    except OSError as exc:
        raise StudioExportError("A Studio source take folder is missing.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StudioExportError(
            "Every Studio source take root must be a real directory."
        )
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


def _snapshot_take(
    take_id: str,
    root: Path,
    project: TakeProject,
) -> _TakeSnapshot:
    """Bind one exact take root, manifest, and parsed immutable project."""

    before = _take_root_identity(root)
    manifest = _take_manifest_snapshot(root, project)
    after = _take_root_identity(root)
    if after != before:
        raise StudioExportError(
            "A Studio source take root changed while it was snapshotted."
        )
    return _TakeSnapshot(
        take_id=take_id,
        root=root,
        root_identity=after,
        project=project,
        manifest=manifest,
    )


def _verify_take_snapshot(snapshot: _TakeSnapshot) -> None:
    """Require one source take root and manifest to remain exactly published."""

    if _take_root_identity(snapshot.root) != snapshot.root_identity:
        raise StudioExportError("A Studio source take root changed during export.")
    _verify_snapshot(snapshot.manifest, "take manifest")
    if _take_root_identity(snapshot.root) != snapshot.root_identity:
        raise StudioExportError("A Studio source take root changed during export.")


def _optional_state_snapshot(
    take_root: Path,
    document: StudioDocument,
) -> _FileSnapshot | None:
    path = take_root / STUDIO_STATE_FILENAME
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StudioExportError(
            "The Studio state file could not be inspected."
        ) from exc
    data = _read_regular(path, "Studio state file", MAX_EXPORT_MANIFEST_BYTES)
    try:
        payload = json.loads(data.decode("utf-8"))
        loaded = studio_document_from_dict(payload)
    except (UnicodeDecodeError, ValueError, StudioProjectError) as exc:
        raise StudioExportError("The Studio state file could not be trusted.") from exc
    if loaded.to_dict() != document.to_dict():
        raise StudioExportError(
            "The supplied Studio document does not match its saved state."
        )
    return _FileSnapshot(
        path=path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _verify_snapshot(snapshot: _FileSnapshot, label: str) -> None:
    current = _snapshot_file(snapshot.path, label)
    if current.size_bytes != snapshot.size_bytes or current.sha256 != snapshot.sha256:
        raise StudioExportError(f"The {label} changed during export.")


def _verified_snapshot_bytes(snapshot: _FileSnapshot, label: str) -> bytes:
    data = _read_regular(snapshot.path, label, MAX_EXPORT_MANIFEST_BYTES)
    if (
        len(data) != snapshot.size_bytes
        or hashlib.sha256(data).hexdigest() != snapshot.sha256
    ):
        raise StudioExportError(f"The {label} changed during export.")
    return data


def _verify_optional_state_snapshot(
    take_root: Path,
    snapshot: _FileSnapshot | None,
) -> None:
    path = take_root / STUDIO_STATE_FILENAME
    if snapshot is not None:
        _verify_snapshot(snapshot, "Studio state file")
        return
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StudioExportError("The Studio state file changed during export.") from exc
    raise StudioExportError("The Studio state file changed during export.")


def _retained_source_track_keys(
    document: StudioDocument,
    selected_track_ids: set[str],
) -> set[tuple[str, str]]:
    """Return playback-owned and document-owned active source recordings.

    An enabled, nondeleted lane owns its durable source track even when every
    attached region is disabled or tombstoned and no comp swipe exists.
    Ordinary active regions not attached to any lane form the base playback
    arrangement. Disabled and deleted lanes remain inventory only.
    """

    lane_by_region = {
        region_id: lane for lane in document.take_lanes for region_id in lane.region_ids
    }
    # A live lane wins over a historical tombstone if both retain the same
    # region ID in document history.
    lane_by_region.update(
        {
            region_id: lane
            for lane in document.take_lanes
            if not lane.deleted
            for region_id in lane.region_ids
        }
    )
    keys = {
        (lane.source_take_id, lane.source_track_id)
        for lane in document.take_lanes
        if not lane.deleted
        and lane.enabled
        and lane.track_id in selected_track_ids
        and lane.source_take_id
        and lane.source_track_id
    }
    for region in document.regions:
        if (
            region.deleted
            or not region.enabled
            or region.track_id not in selected_track_ids
        ):
            continue
        lane = lane_by_region.get(region.region_id)
        if lane is not None:
            continue
        keys.add((region.source_take_id, region.source_track_id))
    return keys


def _primary_original_source_track_keys(
    project: TakeProject,
    selected_track_ids: set[str],
) -> set[tuple[str, str]]:
    """Return primary tracks required by backward-compatible originals."""

    return {
        (project.take_id, track.track_id)
        for track in project.tracks
        if track.track_id in selected_track_ids
    }


def _original_stem_plans(
    project: TakeProject,
    selected_tracks,
    contexts: dict[str, tuple[TakeProject, Path]],
    retained_source_track_keys: set[tuple[str, str]],
) -> tuple[_OriginalStemPlan, ...]:
    """Plan one collision-free original for every contributing source track.

    Primary selected tracks retain their historical one-to-one ordering and
    filename shape. Any additional source track is appended by durable take
    and track ID, including repeated-take comp sources.
    """

    primary_keys = tuple((project.take_id, item.track_id) for item in selected_tracks)
    seen = set(primary_keys)
    ordered_keys = (
        *primary_keys,
        *sorted(retained_source_track_keys.difference(seen)),
    )
    plans: list[_OriginalStemPlan] = []
    for index, (take_id, track_id) in enumerate(ordered_keys):
        try:
            source_project = contexts[take_id][0]
        except KeyError as exc:
            raise StudioExportError(
                "An original source take is missing from the trusted export context."
            ) from exc
        matches = tuple(
            item for item in source_project.tracks if item.track_id == track_id
        )
        if len(matches) != 1:
            raise StudioExportError(
                "An original source track is missing from its trusted take manifest."
            )
        plans.append(
            _OriginalStemPlan(
                take_id=take_id,
                track=matches[0],
                primary_compatible_name=index < len(primary_keys),
            )
        )
    return tuple(plans)


def _original_source_keys(
    plans: Sequence[_OriginalStemPlan],
) -> set[StudioSourceKey]:
    """Return every immutable segment represented by planned originals."""

    return {
        (plan.take_id, plan.track.track_id, segment.segment_id)
        for plan in plans
        for segment in plan.track.segments
        if segment.frame_count > 0
    }


def _source_take_contexts(
    project: TakeProject,
    take_root: Path,
    source_track_keys: set[tuple[str, str]],
    source_catalog: StudioSourceCatalog | None,
) -> dict[str, tuple[TakeProject, Path]]:
    """Resolve every required take to its exact manifest project and root."""

    contexts: dict[str, tuple[TakeProject, Path]] = {}
    for take_id in sorted({key[0] for key in source_track_keys}):
        if take_id == project.take_id:
            contexts[take_id] = (project, take_root)
            continue
        if source_catalog is None:
            raise StudioExportError(
                "The Studio arrangement references another take, but no trusted "
                "source catalog was supplied."
            )
        try:
            contexts[take_id] = (
                source_catalog.project_for_take(take_id),
                source_catalog.root_for_take(take_id),
            )
        except StudioSourceCatalogError as exc:
            raise StudioExportError(str(exc)) from exc
    return contexts


def _validate_source_take_policies(
    contexts: dict[str, tuple[TakeProject, Path]],
    source_track_keys: set[tuple[str, str]],
    *,
    primary_take_id: str,
) -> None:
    """Apply the established evidence gate to every referenced source track."""

    for take_id in sorted(contexts):
        source_project, source_root = contexts[take_id]
        requested = {key[1] for key in source_track_keys if key[0] == take_id}
        known = {track.track_id: track for track in source_project.tracks}
        if not requested.issubset(known):
            raise StudioExportError(
                "The Studio arrangement references an unknown source track."
            )
        disabled = [
            known[track_id].name
            for track_id in requested
            if not known[track_id].selected_for_export
        ]
        if disabled:
            if take_id == primary_take_id:
                raise StudioExportError(
                    "A Studio track is disabled by the take's recording evidence. "
                    "Deselect it before export."
                )
            raise StudioExportError(
                "A repeated-take source track is disabled by its recording evidence: "
                + ", ".join(sorted(disabled))
                + "."
            )
        try:
            policy_tracks = validated_project_export_tracks(
                source_project,
                source_root,
                selected_track_ids=requested,
            )
        except TakeExportError as exc:
            raise StudioExportError(str(exc)) from exc
        if {track.track_id for track in policy_tracks} != requested:
            raise StudioExportError(
                "A Studio source track is disabled by its recording evidence."
            )


def _selected_render_document(
    document: StudioDocument,
    selected_track_ids: set[str],
) -> StudioDocument:
    """Return a render-only view whose inventory exactly matches selection."""

    regions = tuple(
        item for item in document.regions if item.track_id in selected_track_ids
    )
    region_ids = {item.region_id for item in regions}
    return replace(
        document,
        tracks=tuple(
            item for item in document.tracks if item.track_id in selected_track_ids
        ),
        regions=regions,
        take_lanes=tuple(
            item for item in document.take_lanes if item.track_id in selected_track_ids
        ),
        comp_ranges=tuple(
            item for item in document.comp_ranges if item.track_id in selected_track_ids
        ),
        crossfades=tuple(
            item
            for item in document.crossfades
            if item.left_region_id in region_ids and item.right_region_id in region_ids
        ),
    )


def _snapshot_sources(
    contexts: dict[str, tuple[TakeProject, Path]],
    source_keys: set[StudioSourceKey],
    cancel_event: CancellationSignal | None,
    *,
    source_catalog: StudioSourceCatalog | None,
) -> tuple[_SourceSnapshot, ...]:
    snapshots: list[_SourceSnapshot] = []
    for source_key in sorted(source_keys):
        take_id, track_id, segment_id = source_key
        source_project, take_root = contexts[take_id]
        if source_catalog is not None:
            try:
                catalog_source = source_catalog.resolve(*source_key)
            except StudioSourceCatalogError as exc:
                raise StudioExportError(str(exc)) from exc
            track = catalog_source.track
            segment = catalog_source.segment
            path = catalog_source.path
        else:
            matches = [
                (track, segment)
                for track in source_project.tracks
                if track.track_id == track_id
                for segment in track.segments
                if segment.segment_id == segment_id
            ]
            if len(matches) != 1:
                raise StudioExportError(
                    "The arrangement references an unknown source segment."
                )
            track, segment = matches[0]
            path = take_root / segment.path
            try:
                path.resolve().relative_to(take_root)
            except ValueError as exc:
                raise StudioExportError(
                    "A rendered source escapes the take folder."
                ) from exc
        if track.track_id != track_id or segment.segment_id != segment_id:
            raise StudioExportError(
                "The arrangement source does not match its trusted catalog key."
            )
        if not segment.sha256:
            raise StudioExportError(
                "Every rendered source requires a declared SHA-256 digest."
            )
        digest = _hash_file(
            path,
            cancel_event,
            label="rendered source",
        )
        try:
            info = path.stat()
        except OSError as exc:
            raise StudioExportError("A rendered source is missing.") from exc
        if segment.size_bytes and info.st_size != segment.size_bytes:
            raise StudioExportError("A rendered source changed size.")
        if digest != segment.sha256:
            raise StudioExportError("A rendered source changed after validation.")
        snapshots.append(
            _SourceSnapshot(
                take_id=take_id,
                track_id=track.track_id,
                source_id=track.source_id,
                segment=segment,
                path=path,
                sha256=digest,
                size_bytes=info.st_size,
            )
        )
    return tuple(snapshots)


def _verify_sources(
    snapshots: Sequence[_SourceSnapshot],
    cancel_event: CancellationSignal | None,
) -> None:
    for snapshot in snapshots:
        _check_cancelled(cancel_event)
        try:
            info = snapshot.path.stat()
        except OSError as exc:
            raise StudioExportError(
                "A rendered source disappeared during export."
            ) from exc
        if snapshot.segment.size_bytes and info.st_size != snapshot.segment.size_bytes:
            raise StudioExportError("A rendered source changed size during export.")
        if (
            _hash_file(
                snapshot.path,
                cancel_event,
                label="rendered source",
            )
            != snapshot.sha256
        ):
            raise StudioExportError("A rendered source changed during export.")


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise StudioExportError("The export destination is unavailable.")
    return candidate


def _canonical_destination_path(value: str | Path) -> Path:
    """Resolve only the existing parent, preserving the destination leaf."""

    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        raise StudioExportError(
            "The export destination must not contain parent-directory traversal."
        )
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    parent = candidate.parent
    missing: list[str] = []
    while not parent.exists() and parent != parent.parent:
        missing.append(parent.name)
        parent = parent.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StudioExportError("The export destination is unavailable.") from exc
    return resolved_parent.joinpath(*reversed(missing), candidate.name)


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


def _export_root_identity(path: Path) -> tuple[int, int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StudioExportError("The export destination is unavailable.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StudioExportError(
            "The export destination must be a real folder, not a symbolic link."
        )
    return _directory_identity(info)


@dataclass
class _BoundExportRoot:
    path: Path
    identity: tuple[int, int, int]
    created: bool
    descriptor: int | None = None
    parent_descriptor: int | None = None
    entry_name: str | None = None

    def close(self) -> None:
        descriptor = self.descriptor
        parent_descriptor = self.parent_descriptor
        self.descriptor = None
        self.parent_descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


@dataclass
class _BoundTemporaryDirectory:
    path: Path
    name: str
    identity: tuple[int, int, int]
    descriptor: int | None = None

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = None
        if descriptor is not None:
            os.close(descriptor)


def _secure_directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise StudioExportError(
            "This system cannot securely bind the Studio export destination."
        )
    if not _SECURE_DIR_FD_SUPPORTED:
        raise StudioExportError(
            "This system cannot securely bind the Studio export destination."
        )
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _fsync_created_parent(parent_descriptor: int) -> None:
    try:
        os.fsync(parent_descriptor)
    except OSError as exc:
        raise StudioExportError(
            "Could not make the new export destination durable."
        ) from exc


def _open_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    create: bool,
) -> tuple[int, tuple[int, int, int], bool]:
    created = False
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise StudioExportError(
                "The export destination could not be created safely."
            ) from exc
    try:
        entry_info = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise StudioExportError("The export destination is unavailable.") from exc
    if stat.S_ISLNK(entry_info.st_mode):
        raise StudioExportError(
            "The export destination must be a real folder, not a symbolic link."
        )
    if not stat.S_ISDIR(entry_info.st_mode):
        raise StudioExportError("The export destination must be a real folder.")
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            _secure_directory_flags(),
            dir_fd=parent_descriptor,
        )
        opened_info = os.fstat(descriptor)
        identity = _directory_identity(opened_info)
        if not stat.S_ISDIR(opened_info.st_mode) or identity != _directory_identity(
            entry_info
        ):
            raise StudioExportError(
                "The export destination changed while it was opened."
            )
        if created:
            os.fchmod(descriptor, 0o700)
            if _directory_identity(os.fstat(descriptor)) != identity:
                raise StudioExportError(
                    "The export destination changed while it was created."
                )
            _fsync_created_parent(parent_descriptor)
        return descriptor, identity, created
    except StudioExportError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise StudioExportError(
            "The export destination could not be bound safely."
        ) from exc


def _bind_posix_export_root(path: Path) -> _BoundExportRoot:
    flags = _secure_directory_flags()
    try:
        current_descriptor = os.open(path.anchor or os.sep, flags)
    except OSError as exc:
        raise StudioExportError("The export destination is unavailable.") from exc
    components = path.parts[1:]
    if not components:
        info = os.fstat(current_descriptor)
        return _BoundExportRoot(
            path=path,
            identity=_directory_identity(info),
            created=False,
            descriptor=current_descriptor,
        )
    try:
        for index, name in enumerate(components):
            is_final = index == len(components) - 1
            child_descriptor, identity, created = _open_directory_at(
                current_descriptor,
                name,
                create=True,
            )
            if is_final:
                return _BoundExportRoot(
                    path=path,
                    identity=identity,
                    created=created,
                    descriptor=child_descriptor,
                    parent_descriptor=current_descriptor,
                    entry_name=name,
                )
            os.close(current_descriptor)
            current_descriptor = child_descriptor
    except Exception:
        os.close(current_descriptor)
        raise
    raise StudioExportError("The export destination is unavailable.")


def _bind_export_root(path: Path) -> _BoundExportRoot:
    if _SECURE_EXPORT_PLATFORM_SUPPORTED:
        return _bind_posix_export_root(path)
    raise StudioExportError(
        "Secure Studio export publication is unavailable on this platform."
    )


def _require_export_root_identity(
    root: _BoundExportRoot,
) -> None:
    if root.descriptor is not None:
        try:
            descriptor_info = os.fstat(root.descriptor)
            if (
                not stat.S_ISDIR(descriptor_info.st_mode)
                or _directory_identity(descriptor_info) != root.identity
            ):
                raise StudioExportError("The export destination changed during export.")
            if root.parent_descriptor is not None and root.entry_name is not None:
                entry_info = os.stat(
                    root.entry_name,
                    dir_fd=root.parent_descriptor,
                    follow_symlinks=False,
                )
                if _directory_identity(entry_info) != root.identity:
                    raise StudioExportError(
                        "The export destination changed during export."
                    )
        except StudioExportError:
            raise
        except OSError as exc:
            raise StudioExportError(
                "The export destination changed during export."
            ) from exc
    if _export_root_identity(root.path) != root.identity:
        raise StudioExportError("The export destination changed during export.")


def _create_temporary_directory(
    root: _BoundExportRoot,
) -> _BoundTemporaryDirectory:
    name = f".webjam-studio-export-{uuid.uuid4().hex}"
    path = root.path / name
    if root.descriptor is None:
        raise StudioExportError(
            "Studio export requires a descriptor-bound destination."
        )
    descriptor, identity, created = _open_directory_at(
        root.descriptor,
        name,
        create=True,
    )
    if not created:
        os.close(descriptor)
        raise StudioExportError("Could not reserve a private Studio export folder.")
    return _BoundTemporaryDirectory(
        path=path,
        name=name,
        identity=identity,
        descriptor=descriptor,
    )


def _require_temporary_identity(
    root: _BoundExportRoot,
    temporary: _BoundTemporaryDirectory,
) -> None:
    if root.descriptor is None or temporary.descriptor is None:
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        )
    try:
        descriptor_info = os.fstat(temporary.descriptor)
        entry_info = os.stat(
            temporary.name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        ) from exc
    if (
        not stat.S_ISDIR(descriptor_info.st_mode)
        or not stat.S_ISDIR(entry_info.st_mode)
        or _directory_identity(descriptor_info) != temporary.identity
        or _directory_identity(entry_info) != temporary.identity
    ):
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        )


@dataclass(frozen=True)
class _BoundPackageDirectory:
    relative: Path
    parent_relative: Path
    name: str
    identity: tuple[int, int, int]
    descriptor: int


@dataclass(frozen=True)
class _BoundPackageFile:
    relative: Path
    identity: tuple[int, int, int]
    descriptor: int


@dataclass
class _BoundPackage:
    temporary: _BoundTemporaryDirectory
    directories: dict[Path, _BoundPackageDirectory]
    files: dict[Path, _BoundPackageFile]

    def close(self) -> None:
        failure: OSError | None = None
        for package_file in self.files.values():
            try:
                os.close(package_file.descriptor)
            except OSError as exc:
                failure = failure or exc
        self.files.clear()
        for directory in sorted(
            self.directories.values(),
            key=lambda item: len(item.relative.parts),
            reverse=True,
        ):
            try:
                os.close(directory.descriptor)
            except OSError as exc:
                failure = failure or exc
        self.directories.clear()
        try:
            self.temporary.close()
        except OSError as exc:
            failure = failure or exc
        if failure is not None:
            raise failure


def _new_bound_package(temporary: _BoundTemporaryDirectory) -> _BoundPackage:
    if temporary.descriptor is None:
        raise StudioExportError(
            "Studio export requires descriptor-relative package creation."
        )
    return _BoundPackage(temporary=temporary, directories={}, files={})


def _validated_package_relative(value: Path) -> Path:
    relative = Path(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise StudioExportError("The Studio package contains an unsafe path.")
    return relative


def _package_directory_descriptor(
    package: _BoundPackage,
    relative: Path,
) -> int:
    if relative == Path("."):
        descriptor = package.temporary.descriptor
        if descriptor is None:
            raise StudioExportError("The Studio package is already closed.")
        return descriptor
    directory = package.directories.get(relative)
    if directory is None:
        raise StudioExportError("The Studio package parent folder is unavailable.")
    return directory.descriptor


def _create_package_directory(package: _BoundPackage, value: Path) -> None:
    relative = _validated_package_relative(value)
    if relative in package.directories or relative in package.files:
        raise StudioExportError("The Studio package contains a duplicate path.")
    parent_relative = relative.parent
    parent_descriptor = _package_directory_descriptor(package, parent_relative)
    descriptor, identity, created = _open_directory_at(
        parent_descriptor,
        relative.name,
        create=True,
    )
    if not created:
        os.close(descriptor)
        raise StudioExportError("The Studio package path was claimed unexpectedly.")
    package.directories[relative] = _BoundPackageDirectory(
        relative=relative,
        parent_relative=parent_relative,
        name=relative.name,
        identity=identity,
        descriptor=descriptor,
    )


def _package_file_flags(*, writable: bool) -> int:
    flags = os.O_RDWR if writable else os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise StudioExportError(
            "This system cannot securely open Studio package files."
        )
    return flags | nofollow


def _create_package_file(package: _BoundPackage, value: Path) -> int:
    relative = _validated_package_relative(value)
    if relative in package.files or relative in package.directories:
        raise StudioExportError("The Studio package contains a duplicate path.")
    parent_descriptor = _package_directory_descriptor(package, relative.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            relative.name,
            _package_file_flags(writable=True) | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StudioExportError("A Studio package leaf is not a regular file.")
        identity = _directory_identity(info)
        current = os.stat(
            relative.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or _directory_identity(current) != identity
        ):
            raise StudioExportError(
                "A Studio package leaf changed while it was created."
            )
        package.files[relative] = _BoundPackageFile(
            relative,
            identity,
            descriptor,
        )
        return descriptor
    except StudioExportError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise StudioExportError(
            "A Studio package file could not be created safely."
        ) from exc


def _validate_package_file_descriptor(
    package: _BoundPackage,
    relative: Path,
    descriptor: int,
) -> None:
    expected = package.files.get(relative)
    if expected is None:
        raise StudioExportError("The Studio package leaf is not allowlisted.")
    parent_descriptor = _package_directory_descriptor(package, relative.parent)
    try:
        opened = os.fstat(descriptor)
        current = os.stat(
            relative.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise StudioExportError("A Studio package leaf changed during export.") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or _directory_identity(opened) != expected.identity
        or _directory_identity(current) != expected.identity
    ):
        raise StudioExportError("A Studio package leaf changed during export.")


def _open_package_file_readonly(package: _BoundPackage, value: Path) -> int:
    relative = _validated_package_relative(value)
    expected = package.files.get(relative)
    if expected is None:
        raise StudioExportError("The Studio package leaf is not allowlisted.")
    parent_descriptor = _package_directory_descriptor(package, relative.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            relative.name,
            _package_file_flags(writable=False),
            dir_fd=parent_descriptor,
        )
        _validate_package_file_descriptor(package, relative, descriptor)
        return descriptor
    except StudioExportError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise StudioExportError("A Studio package leaf could not be opened.") from exc


def _finish_package_file(
    package: _BoundPackage,
    relative: Path,
    descriptor: int,
) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise StudioExportError("A Studio package file could not be synced.") from exc
    _validate_package_file_descriptor(package, relative, descriptor)


def _write_package_bytes(
    package: _BoundPackage,
    relative: Path,
    data: bytes,
) -> None:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    normalized = _validated_package_relative(relative)
    descriptor = _create_package_file(package, normalized)
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(descriptor, remaining)
        except OSError as exc:
            raise StudioExportError(
                "A Studio package file could not be written."
            ) from exc
        if written <= 0:
            raise StudioExportError("A Studio package file could not be written.")
        remaining = remaining[written:]
    _finish_package_file(package, normalized, descriptor)


def _write_package_text(
    package: _BoundPackage,
    relative: Path,
    text: str,
) -> None:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    _write_package_bytes(package, relative, text.encode("utf-8"))


def _hash_package_file(
    package: _BoundPackage,
    relative: Path,
    cancel_event: CancellationSignal | None,
) -> str:
    normalized = _validated_package_relative(relative)
    descriptor = _open_package_file_readonly(package, normalized)
    digest = hashlib.sha256()
    try:
        while True:
            _check_cancelled(cancel_event)
            try:
                block = os.read(descriptor, _HASH_BLOCK_BYTES)
            except OSError as exc:
                raise StudioExportError(
                    "A Studio package file could not be read."
                ) from exc
            if not block:
                break
            digest.update(block)
        _validate_package_file_descriptor(package, normalized, descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _validate_package_tree(
    root: _BoundExportRoot,
    package: _BoundPackage,
    entry_name: str,
) -> None:
    root_descriptor = root.descriptor
    package_descriptor = package.temporary.descriptor
    if root_descriptor is None or package_descriptor is None:
        raise StudioExportError("The Studio package is not descriptor-bound.")
    try:
        package_info = os.fstat(package_descriptor)
        root_entry = os.stat(
            entry_name,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise StudioExportError(
            "The Studio package tree changed during export."
        ) from exc
    if (
        not stat.S_ISDIR(package_info.st_mode)
        or not stat.S_ISDIR(root_entry.st_mode)
        or _directory_identity(package_info) != package.temporary.identity
        or _directory_identity(root_entry) != package.temporary.identity
    ):
        raise StudioExportError("The Studio package tree changed during export.")

    expected_children: dict[Path, set[str]] = {Path("."): set()}
    for relative, directory in package.directories.items():
        expected_children.setdefault(relative, set())
        expected_children.setdefault(directory.parent_relative, set()).add(
            directory.name
        )
        parent_descriptor = _package_directory_descriptor(
            package,
            directory.parent_relative,
        )
        try:
            opened = os.fstat(directory.descriptor)
            current = os.stat(
                directory.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise StudioExportError(
                "The Studio package tree changed during export."
            ) from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or _directory_identity(opened) != directory.identity
            or _directory_identity(current) != directory.identity
        ):
            raise StudioExportError("The Studio package tree changed during export.")
    for relative, package_file in package.files.items():
        expected_children.setdefault(relative.parent, set()).add(relative.name)
        _validate_package_file_descriptor(
            package,
            relative,
            package_file.descriptor,
        )
        descriptor = _open_package_file_readonly(package, relative)
        os.close(descriptor)
    for relative, expected in expected_children.items():
        descriptor = _package_directory_descriptor(package, relative)
        try:
            observed = set(os.listdir(descriptor))
        except OSError as exc:
            raise StudioExportError(
                "The Studio package tree could not be inspected."
            ) from exc
        if observed != expected:
            raise StudioExportError(
                "The Studio package tree contains an unexpected entry."
            )


def _fsync_package_tree(package: _BoundPackage) -> None:
    for directory in sorted(
        package.directories.values(),
        key=lambda item: len(item.relative.parts),
        reverse=True,
    ):
        try:
            os.fsync(directory.descriptor)
        except OSError as exc:
            raise StudioExportError(
                "A Studio package directory could not be synced."
            ) from exc
    descriptor = package.temporary.descriptor
    if descriptor is None:
        raise StudioExportError("The Studio package is already closed.")
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise StudioExportError("The Studio package could not be synced.") from exc


def _empty_bound_package(package: _BoundPackage) -> None:
    failure: StudioExportError | None = None
    for package_file in package.files.values():
        try:
            info = os.fstat(package_file.descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or _directory_identity(info) != package_file.identity
            ):
                raise StudioExportError(
                    "Studio could not verify a relocated package file."
                )
            os.ftruncate(package_file.descriptor, 0)
            os.fsync(package_file.descriptor)
        except StudioExportError as exc:
            failure = failure or exc
        except OSError as exc:
            if failure is None:
                failure = StudioExportError(
                    "Studio could not scrub a relocated package file."
                )
                failure.__cause__ = exc
    for directory in sorted(
        package.directories.values(),
        key=lambda item: len(item.relative.parts),
        reverse=True,
    ):
        try:
            _remove_tree_contents_fd(directory.descriptor)
        except StudioExportError as exc:
            failure = failure or exc
    if failure is not None:
        raise failure


def _remove_bound_package(
    root: _BoundExportRoot,
    package: _BoundPackage,
) -> None:
    failure: StudioExportError | None = None
    try:
        _empty_bound_package(package)
    except StudioExportError as exc:
        failure = exc
    try:
        _remove_temporary_directory(root, package.temporary)
    except StudioExportError as exc:
        failure = failure or exc
    if failure is not None:
        raise failure


def _preflight_disk(
    destination_root: Path,
    *,
    frames: int,
    audio_file_count: int,
    reserve_bytes: int,
    metadata_bytes: int,
) -> int:
    # PCM24 stereo payload plus conservative WAV headers and bounded metadata.
    required = (
        frames * 2 * 3 * audio_file_count + audio_file_count * 4_096 + metadata_bytes
    )
    try:
        free = shutil.disk_usage(_existing_ancestor(destination_root)).free
    except OSError as exc:
        raise StudioExportError("Could not inspect export disk space.") from exc
    if free < required + reserve_bytes:
        raise StudioExportError(
            "Not enough free disk space for the Studio export and reserve."
        )
    return required


def _child_exists(root: _BoundExportRoot, name: str) -> bool:
    if root.descriptor is None:
        raise StudioExportError(
            "Could not inspect an unbound Studio export destination."
        )
    try:
        os.stat(name, dir_fd=root.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StudioExportError(
            "Could not inspect the Studio export destination."
        ) from exc
    return True


def _next_folder_name(root: _BoundExportRoot) -> str:
    candidate = "Studio Export"
    number = 2
    while _child_exists(root, candidate):
        candidate = f"Studio Export {number}"
        number += 1
    return candidate


def _publish_directory_no_replace(
    root: _BoundExportRoot,
    source_name: str,
    destination_name: str,
) -> None:
    """Atomically rename a package while refusing to replace any destination."""

    try:
        if sys.platform == "darwin":
            if root.descriptor is None:
                raise StudioExportError(
                    "This system cannot publish Studio exports without overwrite risk."
                )
            rename_exclusive = getattr(
                ctypes.CDLL(None, use_errno=True),
                "renameatx_np",
                None,
            )
            if rename_exclusive is None:
                raise StudioExportError(
                    "This system cannot publish Studio exports without overwrite risk."
                )
            rename_exclusive.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename_exclusive.restype = ctypes.c_int
            result = rename_exclusive(
                root.descriptor,
                os.fsencode(source_name),
                root.descriptor,
                os.fsencode(destination_name),
                0x00000004,  # RENAME_EXCL from Darwin's sys/stdio.h.
            )
            if result:
                error_number = ctypes.get_errno()
                raise OSError(
                    error_number,
                    os.strerror(error_number),
                    root.path / destination_name,
                )
        elif sys.platform.startswith("linux"):
            if root.descriptor is None:
                raise StudioExportError(
                    "This system cannot publish Studio exports without overwrite risk."
                )
            rename_no_replace = getattr(
                ctypes.CDLL(None, use_errno=True),
                "renameat2",
                None,
            )
            if rename_no_replace is None:
                raise StudioExportError(
                    "This system cannot publish Studio exports without overwrite risk."
                )
            rename_no_replace.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename_no_replace.restype = ctypes.c_int
            result = rename_no_replace(
                root.descriptor,
                os.fsencode(source_name),
                root.descriptor,
                os.fsencode(destination_name),
                1,  # RENAME_NOREPLACE from Linux's stdio.h.
            )
            if result:
                error_number = ctypes.get_errno()
                raise OSError(
                    error_number,
                    os.strerror(error_number),
                    root.path / destination_name,
                )
        else:
            raise StudioExportError(
                "This system cannot publish Studio exports without overwrite risk."
            )
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise StudioExportError(
                "The Studio export name was claimed before publication."
            ) from exc
        raise StudioExportError(
            "The Studio package could not be published atomically."
        ) from exc


@contextmanager
def _export_publication_lock(root: _BoundExportRoot) -> Iterator[None]:
    """Serialize final-name allocation and publication across processes."""

    with _EXPORT_LOCKS_GUARD:
        process_lock = _EXPORT_LOCKS.setdefault(root.path, threading.RLock())
    with process_lock:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            if root.descriptor is None:
                raise StudioExportError(
                    "The Studio export lock requires a descriptor-bound folder."
                )
            descriptor = os.open(
                _EXPORT_LOCK_FILENAME,
                flags,
                0o600,
                dir_fd=root.descriptor,
            )
            current = os.stat(
                _EXPORT_LOCK_FILENAME,
                dir_fd=root.descriptor,
                follow_symlinks=False,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise StudioExportError(
                    "The Studio export lock must be one stable regular file."
                )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            descriptor = -1
        except StudioExportError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise StudioExportError("Could not open the Studio export lock.") from exc
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise StudioExportError("Could not lock the Studio export folder.") from exc
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                # Closing the descriptor below also releases the advisory lock.
                pass
            try:
                handle.close()
            except OSError:
                pass


def _fsync_export_root(root: _BoundExportRoot) -> None:
    if not _SECURE_EXPORT_PLATFORM_SUPPORTED or root.descriptor is None:
        raise StudioExportError(
            "The Studio export destination is not descriptor-bound."
        )
    os.fsync(root.descriptor)


def _remove_tree_contents_fd(directory_descriptor: int) -> None:
    """Remove a private tree without ever following a symbolic link."""

    try:
        names = os.listdir(directory_descriptor)
    except OSError as exc:
        raise StudioExportError(
            "Studio could not inspect its unpublished export transaction."
        ) from exc
    for name in names:
        if name in {".", ".."}:
            raise StudioExportError(
                "Studio could not inspect its unpublished export transaction."
            )
        try:
            entry_info = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StudioExportError(
                "Studio could not inspect its unpublished export transaction."
            ) from exc
        if stat.S_ISDIR(entry_info.st_mode):
            child_descriptor = -1
            try:
                child_descriptor = os.open(
                    name,
                    _secure_directory_flags(),
                    dir_fd=directory_descriptor,
                )
                if _directory_identity(os.fstat(child_descriptor)) != (
                    _directory_identity(entry_info)
                ):
                    raise StudioExportError(
                        "Studio could not verify its unpublished export transaction."
                    )
                _remove_tree_contents_fd(child_descriptor)
            except StudioExportError:
                raise
            except OSError as exc:
                raise StudioExportError(
                    "Studio could not remove its unpublished export transaction."
                ) from exc
            finally:
                if child_descriptor >= 0:
                    os.close(child_descriptor)
            try:
                current = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if _directory_identity(current) != _directory_identity(entry_info):
                    raise StudioExportError(
                        "Studio could not verify its unpublished export transaction."
                    )
                os.rmdir(name, dir_fd=directory_descriptor)
            except StudioExportError:
                raise
            except OSError as exc:
                raise StudioExportError(
                    "Studio could not remove its unpublished export transaction."
                ) from exc
        else:
            try:
                os.unlink(name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StudioExportError(
                    "Studio could not remove its unpublished export transaction."
                ) from exc


def _remove_temporary_directory(
    root: _BoundExportRoot,
    temporary: _BoundTemporaryDirectory,
) -> None:
    if root.descriptor is None or temporary.descriptor is None:
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        )

    try:
        descriptor_info = os.fstat(temporary.descriptor)
    except OSError as exc:
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        ) from exc
    if (
        not stat.S_ISDIR(descriptor_info.st_mode)
        or _directory_identity(descriptor_info) != temporary.identity
    ):
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        )
    _remove_tree_contents_fd(temporary.descriptor)
    removed = False
    try:
        current = os.stat(
            temporary.name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        current = None
    except OSError as exc:
        raise StudioExportError(
            "Studio could not inspect its unpublished export transaction."
        ) from exc
    if current is not None and (
        stat.S_ISDIR(current.st_mode)
        and _directory_identity(current) == temporary.identity
    ):
        try:
            os.rmdir(temporary.name, dir_fd=root.descriptor)
        except OSError as exc:
            raise StudioExportError(
                "Studio could not remove its unpublished export transaction."
            ) from exc
        removed = True
    elif current is not None and stat.S_ISLNK(current.st_mode):
        try:
            os.unlink(temporary.name, dir_fd=root.descriptor)
        except OSError as exc:
            raise StudioExportError(
                "Studio could not remove its unpublished export transaction."
            ) from exc
    if not removed:
        try:
            for child_name in os.listdir(root.descriptor):
                try:
                    child_info = os.stat(
                        child_name,
                        dir_fd=root.descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                if (
                    stat.S_ISDIR(child_info.st_mode)
                    and _directory_identity(child_info) == temporary.identity
                ):
                    os.rmdir(child_name, dir_fd=root.descriptor)
                    removed = True
                    break
        except OSError as exc:
            raise StudioExportError(
                "Studio could not remove its unpublished export transaction."
            ) from exc
    if not removed:
        raise StudioExportError(
            "Studio could not verify its unpublished export transaction."
        )


def _remove_created_root_if_empty(root: _BoundExportRoot) -> None:
    if not root.created:
        return
    try:
        if root.parent_descriptor is not None and root.entry_name is not None:
            if root.descriptor is None:
                return
            descriptor_info = os.fstat(root.descriptor)
            entry_info = os.stat(
                root.entry_name,
                dir_fd=root.parent_descriptor,
                follow_symlinks=False,
            )
            if (
                _directory_identity(descriptor_info) != root.identity
                or _directory_identity(entry_info) != root.identity
            ):
                return
            os.rmdir(root.entry_name, dir_fd=root.parent_descriptor)
        else:
            _require_export_root_identity(root)
            root.path.rmdir()
    except (OSError, StudioExportError):
        pass


def _raise_publication_identity_failure(
    root: _BoundExportRoot,
    package: _BoundPackage,
    cause: BaseException,
) -> None:
    cleanup_failed = False
    try:
        _remove_bound_package(root, package)
    except (OSError, StudioExportError):
        cleanup_failed = True
    try:
        _fsync_export_root(root)
    except (OSError, StudioExportError):
        cleanup_failed = True
    try:
        package.close()
    except OSError:
        cleanup_failed = True
    if cleanup_failed:
        raise StudioExportError(
            "The published Studio package lost its verified name and could not "
            "be safely withdrawn."
        ) from cause
    raise StudioExportError(
        "The published Studio package lost its verified name and was withdrawn."
    ) from cause


def _delivery_audio_block(
    block: np.ndarray,
    frame_count: int,
) -> tuple[np.ndarray, int]:
    if block.shape != (frame_count, 2) or block.dtype != np.float32:
        raise StudioExportError("Studio renderer returned an invalid export block.")
    if not np.all(np.isfinite(block)):
        raise StudioExportError("Studio renderer returned non-finite audio.")
    try:
        return studio_delivery_block(block)
    except StudioRenderError as exc:
        raise StudioExportError(
            "Studio audio could not be prepared for PCM24."
        ) from exc


@contextmanager
def _package_sound_writer(
    package: _BoundPackage,
    relative: Path,
    *,
    sample_rate: int,
) -> Iterator[object]:
    try:
        import soundfile as sf  # type: ignore
    except ImportError as exc:  # pragma: no cover - packaged dependency
        raise StudioExportError("Studio audio export support is unavailable.") from exc

    normalized = _validated_package_relative(relative)
    descriptor = _create_package_file(package, normalized)
    writer = None
    completed = False
    try:
        try:
            writer = sf.SoundFile(
                descriptor,
                mode="w",
                samplerate=sample_rate,
                channels=2,
                format="WAV",
                subtype="PCM_24",
                closefd=False,
            )
            yield writer
            completed = True
        finally:
            if writer is not None:
                writer.close()
        if completed:
            _finish_package_file(package, normalized, descriptor)
    except StudioExportError:
        raise
    except (OSError, RuntimeError) as exc:
        raise StudioExportError("Could not write a Studio WAV file.") from exc


def _write_renderer_wav(
    renderer: StudioRenderer,
    package: _BoundPackage,
    destination: Path,
    *,
    frames: int,
    block_frames: int,
    cancel_event: CancellationSignal | None,
) -> int:
    written = 0
    clipped_samples = 0
    try:
        with _package_sound_writer(
            package,
            destination,
            sample_rate=renderer.sample_rate,
        ) as writer, renderer.open(
            start_frame=0,
            end_frame=frames,
            cancel_check=lambda: _check_cancelled(cancel_event),
        ) as stream:
            while written < frames:
                _check_cancelled(cancel_event)
                count = min(block_frames, frames - written)
                block = stream.read(count)
                delivered, clipped = _delivery_audio_block(block, count)
                clipped_samples += clipped
                writer.write(delivered)
                written += count
                _check_cancelled(cancel_event)
    except StudioExportError:
        raise
    except (OSError, RuntimeError) as exc:
        raise StudioExportError("Could not write a Studio WAV file.") from exc
    if written != frames:
        raise StudioExportError("Studio WAV output ended before the common timeline.")
    return clipped_samples


def _write_track_wavs(
    renderer: StudioRenderer,
    package: _BoundPackage,
    destinations: Sequence[tuple[str, Path]],
    *,
    frames: int,
    block_frames: int,
    cancel_event: CancellationSignal | None,
) -> dict[str, int]:
    """Write processed stems in bounded passes through one prepared renderer."""

    known = set(renderer.track_ids)
    requested = [track_id for track_id, _path in destinations]
    if len(requested) != len(set(requested)) or not set(requested).issubset(known):
        raise StudioExportError("Studio stem destinations do not match the renderer.")
    clipped_samples = {track_id: 0 for track_id in requested}

    for offset in range(0, len(destinations), _MAX_SIMULTANEOUS_STEM_WRITERS):
        batch = tuple(destinations[offset : offset + _MAX_SIMULTANEOUS_STEM_WRITERS])
        written = 0
        try:
            with ExitStack() as stack:
                writers = {
                    track_id: stack.enter_context(
                        _package_sound_writer(
                            package,
                            path,
                            sample_rate=renderer.sample_rate,
                        )
                    )
                    for track_id, path in batch
                }
                with renderer.open(
                    start_frame=0,
                    end_frame=frames,
                    cancel_check=lambda: _check_cancelled(cancel_event),
                ) as stream:
                    # A stem is independent of another track's solo switch. Mute,
                    # trim, fader, pan, regions, fades, and comp choices still apply.
                    for track_id in renderer.track_ids:
                        stream.set_track_mix(track_id, solo=False)
                    while written < frames:
                        _check_cancelled(cancel_event)
                        count = min(block_frames, frames - written)
                        _mix, tracks = stream.read_with_tracks(count)
                        for track_id, writer in writers.items():
                            block = tracks.get(track_id)
                            if block is None:
                                raise StudioExportError(
                                    "Studio renderer omitted an export stem."
                                )
                            delivered, clipped = _delivery_audio_block(block, count)
                            clipped_samples[track_id] += clipped
                            writer.write(delivered)
                        written += count
                        _check_cancelled(cancel_event)
        except StudioExportError:
            raise
        except (OSError, RuntimeError) as exc:
            raise StudioExportError("Could not write Studio stem WAV files.") from exc
        if written != frames:
            raise StudioExportError(
                "Studio stem output ended before the common timeline."
            )
    return clipped_samples


def _write_text(package: _BoundPackage, path: Path, text: str) -> None:
    _write_package_text(package, path, text)


def _markers_csv(document: StudioDocument, sample_rate: int) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "marker_id",
            "kind",
            "label",
            "start_frame",
            "end_frame",
            "start_seconds",
            "end_seconds",
        )
    )
    for marker in sorted(
        (item for item in document.markers if not item.deleted),
        key=lambda item: (item.start_frame, item.marker_id),
    ):
        end_frame = "" if marker.end_frame is None else str(marker.end_frame)
        end_seconds = (
            "" if marker.end_frame is None else f"{marker.end_frame / sample_rate:.9f}"
        )
        writer.writerow(
            (
                marker.marker_id,
                marker.kind.value,
                marker.label,
                marker.start_frame,
                end_frame,
                f"{marker.start_frame / sample_rate:.9f}",
                end_seconds,
            )
        )
    return output.getvalue()


def _instructions(sample_rate: int, frames: int) -> str:
    duration = frames / sample_rate
    return f"""# WebJam Studio Export

All audio files are stereo, {sample_rate} Hz, 24-bit PCM WAV files with the
same {frames}-frame ({duration:.6f} s) timeline beginning at 0:00.

## Any multitrack editor

Create a project at {sample_rate} Hz. Import the desired edited stems together
at 0:00, or import `rough-mix.wav` as a listening reference. The `original-stems`
folder contains a separate manifest-aligned unity render for every retained
source recording, including active take lanes; do not stack an original and
edited version of the same performance unintentionally.

## Logic Pro

Set the project sample rate to {sample_rate} Hz, choose **File > Import > Audio
File**, select the desired stems together, and place every file at the project
start. WebJam does not create or alter a `.logicx` project.

External-editor import validation: **NOT RUN**. This package records render
evidence only and does not claim that an external editor import was tested.
"""


def _canonical_document_hash(document: StudioDocument) -> str:
    data = json.dumps(document.to_dict(), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(data).hexdigest()


def _take_manifest_relative(
    take_id: str,
    *,
    primary_take_id: str,
) -> Path:
    if take_id == primary_take_id:
        return Path("source-take-manifest.json")
    return Path("source-take-manifests") / f"{take_id}.json"


def _original_stem_relative(index: int, plan: _OriginalStemPlan) -> Path:
    """Return a deterministic, identity-bearing original-stem package path."""

    stem_name = _safe_name(plan.track.name, f"Track {index}")
    if plan.primary_compatible_name:
        filename = f"{index:02d} {stem_name} - aligned unity.wav"
    else:
        filename = (
            f"{index:02d} {stem_name} - take {plan.take_id} - "
            f"track {plan.track.track_id} - source {plan.track.source_id} - "
            "aligned unity.wav"
        )
    return Path("original-stems") / filename


def _original_stem_provenance(
    plans: Sequence[_OriginalStemPlan],
    relatives: Sequence[Path],
    source_snapshots: Sequence[_SourceSnapshot],
    output_hashes: dict[str, str],
) -> list[dict[str, object]]:
    """Describe immutable inputs and exact output identity for each original."""

    snapshots_by_track: dict[tuple[str, str], list[_SourceSnapshot]] = {}
    for snapshot in source_snapshots:
        snapshots_by_track.setdefault((snapshot.take_id, snapshot.track_id), []).append(
            snapshot
        )
    values: list[dict[str, object]] = []
    for plan, relative in zip(plans, relatives, strict=True):
        sources = sorted(
            snapshots_by_track.get(plan.key, ()),
            key=lambda item: item.segment.segment_id,
        )
        expected_segment_ids = {
            item.segment_id for item in plan.track.segments if item.frame_count > 0
        }
        if {item.segment.segment_id for item in sources} != expected_segment_ids:
            raise StudioExportError(
                "Original-stem provenance does not cover its immutable source track."
            )
        relative_text = relative.as_posix()
        values.append(
            {
                "take_id": plan.take_id,
                "track_id": plan.track.track_id,
                "source_id": plan.track.source_id,
                "name": plan.track.name,
                "relative_path": relative_text,
                "sha256": output_hashes[relative_text],
                "primary_compatible_name": plan.primary_compatible_name,
                "alignment": plan.track.alignment.to_dict(),
                "source_keys": [
                    {
                        "take_id": item.take_id,
                        "track_id": item.track_id,
                        "segment_id": item.segment.segment_id,
                    }
                    for item in sources
                ],
                "source_segments": [
                    {
                        "segment_id": item.segment.segment_id,
                        "relative_path": item.segment.path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "project_start_frame": item.segment.project_start_frame,
                        "frame_count": item.segment.frame_count,
                        "sample_rate": item.segment.sample_rate,
                        "channels": item.segment.channels,
                        "gaps": [gap.to_dict() for gap in item.segment.gaps],
                    }
                    for item in sources
                ],
                "render": {
                    "origin_frame": 0,
                    "arrangement_edits_applied": False,
                    "track_trim_fader_pan_applied": False,
                    "master_processing_applied": False,
                    "manifest_alignment_applied": True,
                },
            }
        )
    return values


def _track_provenance(
    project: TakeProject,
    document: StudioDocument,
    selected_tracks,
    edited_relatives: Sequence[Path],
    original_relatives: Sequence[Path],
) -> list[dict[str, object]]:
    project_tracks = {item.track_id: item for item in project.tracks}
    values: list[dict[str, object]] = []
    for state, edited, original in zip(
        selected_tracks, edited_relatives, original_relatives
    ):
        source_track = project_tracks[state.track_id]
        regions = tuple(
            item
            for item in document.regions
            if item.track_id == state.track_id and item.enabled and not item.deleted
        )
        comps = tuple(
            item
            for item in document.comp_ranges
            if item.track_id == state.track_id and item.enabled and not item.deleted
        )
        source_keys = sorted(
            {
                (
                    item.source_take_id,
                    item.source_track_id,
                    item.source_segment_id,
                )
                for item in regions
            }
        )
        values.append(
            {
                "track_id": state.track_id,
                "source_id": source_track.source_id,
                "name": source_track.name,
                "order": state.order,
                "edited_stem": edited.as_posix(),
                "original_stem": original.as_posix(),
                "mix": {
                    "trim_gain": state.trim_gain,
                    "fader_gain": state.fader_gain,
                    "pan": state.pan,
                    "muted": state.muted,
                    "solo": state.solo,
                },
                "region_ids": [item.region_id for item in regions],
                "source_segment_ids": sorted(
                    {item.source_segment_id for item in regions}
                ),
                "source_keys": [
                    {
                        "take_id": take_id,
                        "track_id": track_id,
                        "segment_id": segment_id,
                    }
                    for take_id, track_id, segment_id in source_keys
                ],
                "comp_range_ids": [item.comp_range_id for item in comps],
            }
        )
    return values


def _producing_application_version() -> str:
    """Read the canonical application version without importing any Qt code.

    ``webjam_qt/__init__.py`` holds the single release-audited version
    string and imports nothing.  The guard keeps export usable in stripped
    deployments where the UI package is absent; evidence then says so
    explicitly instead of failing the export.
    """

    try:
        from webjam_qt import __version__
    except Exception:  # noqa: BLE001 - identity evidence must not block export
        return "unknown"
    return str(__version__)


def _provenance_payload(
    *,
    project: TakeProject,
    document: StudioDocument,
    manifest_snapshot: _FileSnapshot,
    take_snapshots: Sequence[_TakeSnapshot],
    state_snapshot: _FileSnapshot | None,
    source_snapshots: Sequence[_SourceSnapshot],
    selected_tracks,
    edited_relatives: Sequence[Path],
    original_relatives: Sequence[Path],
    original_plans: Sequence[_OriginalStemPlan],
    output_hashes: dict[str, str],
    output_clip_counts: dict[str, int],
    embedded_document_hash: str,
    sample_rate: int,
    frames: int,
    estimated_bytes: int,
    disk_reserve_bytes: int,
) -> dict[str, object]:
    selected_ids = {item.track_id for item in selected_tracks}
    selected_regions = tuple(
        item for item in document.regions if item.track_id in selected_ids
    )
    active_regions = tuple(
        item for item in selected_regions if item.enabled and not item.deleted
    )
    selected_lanes = tuple(
        item for item in document.take_lanes if item.track_id in selected_ids
    )
    selected_comps = tuple(
        item for item in document.comp_ranges if item.track_id in selected_ids
    )
    active_comps = tuple(
        item for item in selected_comps if item.enabled and not item.deleted
    )
    selected_region_ids = {item.region_id for item in selected_regions}
    selected_crossfades = tuple(
        item
        for item in document.crossfades
        if item.left_region_id in selected_region_ids
        and item.right_region_id in selected_region_ids
    )
    evidence_manifests = [
        {
            "take_id": item.take_id,
            "relative_path": _take_manifest_relative(
                item.take_id,
                primary_take_id=project.take_id,
            ).as_posix(),
            "sha256": item.manifest.sha256,
            "size_bytes": item.manifest.size_bytes,
        }
        for item in sorted(take_snapshots, key=lambda value: value.take_id)
    ]
    source_keys = sorted(item.key for item in source_snapshots)
    return {
        "schema_version": STUDIO_EXPORT_SCHEMA_VERSION,
        "export_type": "webjam_studio_arrangement",
        # A technician receiving an export package must be able to tell
        # which WebJam build produced it and when, from the package alone.
        "produced_by": {
            "application": "WebJam",
            "version": _producing_application_version(),
            "exported_at_utc": (
                datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
        },
        "session_id": project.session_id,
        "take_id": project.take_id,
        "take_project_revision": project.revision,
        "studio_document_revision": document.revision,
        "studio_document_schema_version": document.schema_version,
        "studio_document_sha256": _canonical_document_hash(document),
        "embedded_evidence": {
            "source_manifest": {
                "relative_path": "source-take-manifest.json",
                "sha256": manifest_snapshot.sha256,
            },
            "source_manifests": evidence_manifests,
            "studio_document": {
                "relative_path": "studio-document.json",
                "sha256": embedded_document_hash,
            },
        },
        "take_manifest": {
            "filename": manifest_snapshot.path.name,
            "sha256": manifest_snapshot.sha256,
            "size_bytes": manifest_snapshot.size_bytes,
        },
        "take_manifests": evidence_manifests,
        "studio_state_file": (
            None
            if state_snapshot is None
            else {
                "filename": state_snapshot.path.name,
                "sha256": state_snapshot.sha256,
                "size_bytes": state_snapshot.size_bytes,
            }
        ),
        "timeline": {
            "origin_frame": 0,
            "frame_count": frames,
            "sample_rate": sample_rate,
            "duration_seconds": frames / sample_rate,
        },
        "audio_format": {
            "container": "WAV",
            "encoding": "PCM_24",
            "channels": 2,
        },
        "disk_preflight": {
            "estimated_package_bytes": estimated_bytes,
            "required_reserve_bytes": disk_reserve_bytes,
        },
        "render_policy": {
            "edited_stems": "arrangement and track processing; master excluded",
            "rough_mix": "selected arrangement tracks plus master processing",
            "original_stems": (
                "one manifest-aligned immutable source track per retained recording; "
                "arrangement, track mix, and master excluded"
            ),
            "stem_solo_scope": "each edited stem is rendered independently",
            "pcm24_overload": (
                "deterministic hard saturation shared with Studio playback output"
            ),
        },
        "selection": {
            "export_included_track_ids": [item.track_id for item in selected_tracks],
            "region_ids": [item.region_id for item in selected_regions],
            "active_region_ids": [item.region_id for item in active_regions],
            "source_segment_ids": sorted(
                {item.segment.segment_id for item in source_snapshots}
            ),
            "source_keys": [
                {
                    "take_id": take_id,
                    "track_id": track_id,
                    "segment_id": segment_id,
                }
                for take_id, track_id, segment_id in source_keys
            ],
            "take_lane_ids": [item.lane_id for item in selected_lanes],
            "comp_range_ids": [item.comp_range_id for item in selected_comps],
            "active_comp_range_ids": [item.comp_range_id for item in active_comps],
            "crossfade_ids": [item.crossfade_id for item in selected_crossfades],
            "marker_ids": [item.marker_id for item in document.markers],
        },
        "document_inventory": {
            "track_ids": [item.track_id for item in document.tracks],
            "region_ids": [item.region_id for item in document.regions],
            "source_keys": [
                {
                    "take_id": take_id,
                    "track_id": track_id,
                    "segment_id": segment_id,
                }
                for take_id, track_id, segment_id in sorted(
                    {
                        (
                            item.source_take_id,
                            item.source_track_id,
                            item.source_segment_id,
                        )
                        for item in document.regions
                    }
                )
            ],
            "take_lane_ids": [item.lane_id for item in document.take_lanes],
            "comp_range_ids": [item.comp_range_id for item in document.comp_ranges],
            "crossfade_ids": [item.crossfade_id for item in document.crossfades],
            "marker_ids": [item.marker_id for item in document.markers],
        },
        "tracks": _track_provenance(
            project,
            document,
            selected_tracks,
            edited_relatives,
            original_relatives[: len(selected_tracks)],
        ),
        "original_stems": _original_stem_provenance(
            original_plans,
            original_relatives,
            source_snapshots,
            output_hashes,
        ),
        "sources": [
            {
                "take_id": item.take_id,
                "track_id": item.track_id,
                "source_id": item.source_id,
                "segment_id": item.segment.segment_id,
                "source_key": {
                    "take_id": item.take_id,
                    "track_id": item.track_id,
                    "segment_id": item.segment.segment_id,
                },
                "relative_path": item.segment.path,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "sample_rate": item.segment.sample_rate,
                "channels": item.segment.channels,
                "frame_count": item.segment.frame_count,
                "gap_count": len(item.segment.gaps),
            }
            for item in source_snapshots
        ],
        "outputs": [
            {
                "relative_path": path,
                "sha256": digest,
                "clipped_sample_count": output_clip_counts.get(path, 0),
            }
            for path, digest in sorted(output_hashes.items())
        ],
        "external_editor_validation": {
            "status": "NOT RUN",
            "tested": False,
            "editor": None,
        },
    }


def _checksum_manifest(
    package: _BoundPackage,
    relatives: Sequence[Path],
    cancel_event: CancellationSignal | None,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> str:
    lines = []
    for relative in sorted(relatives, key=lambda item: item.as_posix()):
        digest = _hash_package_file(package, relative, cancel_event)
        expected = (expected_hashes or {}).get(relative.as_posix())
        if expected is not None and digest != expected:
            raise StudioExportError("A rendered output changed before publication.")
        lines.append(f"{digest}  {relative.as_posix()}")
    return "\n".join(lines) + "\n"


def export_studio_arrangement(
    project: TakeProject,
    document: StudioDocument,
    take_root: str | Path,
    *,
    destination_root: str | Path | None = None,
    source_catalog: StudioSourceCatalog | None = None,
    block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES,
    disk_reserve_bytes: int = DEFAULT_DISK_RESERVE_BYTES,
    cancel_event: CancellationSignal | None = None,
) -> StudioExportResult:
    """Render and atomically publish one evidence-rich Studio package."""

    if not isinstance(project, TakeProject):
        raise StudioExportError("Studio export requires a TakeProject.")
    if not isinstance(document, StudioDocument):
        raise StudioExportError("Studio export requires a StudioDocument.")
    if not studio_export_supported():
        raise StudioExportError(
            "Secure Studio export publication is unavailable on this platform."
        )
    if source_catalog is not None and type(source_catalog) is not StudioSourceCatalog:
        raise StudioExportError("source_catalog must be a trusted StudioSourceCatalog.")
    block_count = _integer(
        block_frames,
        "block_frames",
        minimum=1,
        maximum=MAX_RENDER_BLOCK_FRAMES,
    )
    reserve = _integer(disk_reserve_bytes, "disk_reserve_bytes", minimum=0)
    _check_cancelled(cancel_event)

    take_folder = Path(take_root).expanduser().resolve()
    if not take_folder.is_dir():
        raise StudioExportError("The take folder is missing.")
    if source_catalog is not None:
        try:
            source_catalog.require_primary(project, take_folder)
            source_catalog.assert_current(lambda: _check_cancelled(cancel_event))
        except StudioSourceCatalogError as exc:
            raise StudioExportError(str(exc)) from exc
    export_root = _canonical_destination_path(
        destination_root
        if destination_root is not None
        else take_folder / "Studio Exports"
    )

    selected_tracks = tuple(
        item
        for item in sorted(
            document.tracks, key=lambda state: (state.order, state.track_id)
        )
        if item.export_included
    )
    if not selected_tracks:
        raise StudioExportError("No Studio tracks are selected for export.")
    selected_ids = {item.track_id for item in selected_tracks}
    retained_source_track_keys = _retained_source_track_keys(document, selected_ids)
    primary_original_track_keys = _primary_original_source_track_keys(
        project,
        selected_ids,
    )
    required_source_track_keys = (
        retained_source_track_keys | primary_original_track_keys
    )
    source_contexts = _source_take_contexts(
        project,
        take_folder,
        required_source_track_keys,
        source_catalog,
    )
    original_plans = _original_stem_plans(
        project,
        selected_tracks,
        source_contexts,
        retained_source_track_keys,
    )
    original_source_keys = _original_source_keys(original_plans)
    _validate_source_take_policies(
        source_contexts,
        required_source_track_keys,
        primary_take_id=project.take_id,
    )

    plans_by_take: dict[str, list[_OriginalStemPlan]] = {}
    for plan in original_plans:
        plans_by_take.setdefault(plan.take_id, []).append(plan)
    original_renderers: dict[str, StudioRenderer] = {}
    try:
        render_document = _selected_render_document(document, selected_ids)
        arrangement_renderer = StudioRenderer(
            project,
            render_document,
            take_folder,
            block_frames=block_count,
            respect_export_included=True,
            apply_master=True,
            source_catalog=source_catalog,
        )
        for take_id, plans in plans_by_take.items():
            source_project, source_root = source_contexts[take_id]
            original_renderers[take_id] = StudioRenderer(
                source_project,
                default_studio_document(source_project),
                source_root,
                block_frames=block_count,
                track_ids=tuple(plan.track.track_id for plan in plans),
                apply_master=False,
                source_catalog=(source_catalog if take_id == project.take_id else None),
            )
    except (StudioProjectError, StudioRenderError, ValueError) as exc:
        raise StudioExportError(
            "The Studio arrangement or an immutable original failed export preflight."
        ) from exc

    arrangement_render_source_keys = set(arrangement_renderer._required_source_keys)

    frames = max(
        0,
        arrangement_renderer.timeline_end_frame,
        *(renderer.timeline_end_frame for renderer in original_renderers.values()),
    )
    if frames <= 0:
        raise StudioExportError("No audio remains on the export timeline.")
    take_snapshots = tuple(
        _snapshot_take(take_id, root, source_project)
        for take_id, (source_project, root) in sorted(source_contexts.items())
    )
    manifest_snapshot = next(
        item.manifest for item in take_snapshots if item.take_id == project.take_id
    )
    state_snapshot = _optional_state_snapshot(take_folder, document)
    audio_file_count = len(selected_tracks) + len(original_plans) + 1
    metadata_bytes = max(
        _METADATA_ALLOWANCE_BYTES,
        len(json.dumps(document.to_dict(), sort_keys=True).encode("utf-8")) * 2
        + sum(item.manifest.size_bytes for item in take_snapshots)
        + sum(
            len(json.dumps(item.project.to_dict(), sort_keys=True).encode("utf-8"))
            for item in take_snapshots
        ),
    )
    estimated_bytes = _preflight_disk(
        export_root,
        frames=frames,
        audio_file_count=audio_file_count,
        reserve_bytes=reserve,
        metadata_bytes=metadata_bytes,
    )
    _check_cancelled(cancel_event)

    source_snapshots = _snapshot_sources(
        source_contexts,
        original_source_keys,
        cancel_event,
        source_catalog=source_catalog,
    )

    def cancel_check() -> None:
        _check_cancelled(cancel_event)

    try:
        # The primary original renderer shares one catalog authority with the
        # arrangement renderer, so exact descriptor receipts can be reused.
        # Alternate-take originals bind their own take roots independently.
        primary_original_renderer = original_renderers[project.take_id]
        primary_renderer_keys = set(primary_original_renderer._required_source_keys)
        if arrangement_render_source_keys.issubset(primary_renderer_keys):
            primary_original_renderer.validate_media(cancel_check)
            arrangement_renderer.reuse_media_validation(
                primary_original_renderer,
                cancel_check,
            )
        elif primary_renderer_keys.issubset(arrangement_render_source_keys):
            arrangement_renderer.validate_media(cancel_check)
            primary_original_renderer.reuse_media_validation(
                arrangement_renderer,
                cancel_check,
            )
        else:
            arrangement_renderer.validate_media(cancel_check)
            primary_original_renderer.validate_media(cancel_check)
        for take_id, renderer in original_renderers.items():
            if take_id != project.take_id:
                renderer.validate_media(cancel_check)
    except StudioRenderError as exc:
        raise StudioExportError(
            "The Studio source catalog changed during export preflight."
        ) from exc
    _check_cancelled(cancel_event)

    export_root_binding: _BoundExportRoot | None = None
    package_binding: _BoundPackage | None = None
    final_folder: Path | None = None
    try:
        export_root_binding = _bind_export_root(export_root)
        _require_export_root_identity(export_root_binding)
        package_binding = _new_bound_package(
            _create_temporary_directory(export_root_binding)
        )
        _require_export_root_identity(export_root_binding)
        _require_temporary_identity(
            export_root_binding,
            package_binding.temporary,
        )
        _create_package_directory(package_binding, Path("edited-stems"))
        _create_package_directory(package_binding, Path("original-stems"))
        manifest_relatives = tuple(
            (
                snapshot,
                _take_manifest_relative(
                    snapshot.take_id,
                    primary_take_id=project.take_id,
                ),
            )
            for snapshot in take_snapshots
        )
        if any(
            relative.parent != Path(".") for _snapshot, relative in manifest_relatives
        ):
            _create_package_directory(
                package_binding,
                Path("source-take-manifests"),
            )

        edited_relatives: list[Path] = []
        original_relatives: list[Path] = []
        edited_destinations: list[tuple[str, Path]] = []
        for index, track in enumerate(selected_tracks, start=1):
            project_track = next(
                item for item in project.tracks if item.track_id == track.track_id
            )
            stem_name = _safe_name(project_track.name, f"Track {index}")
            relative = Path("edited-stems") / f"{index:02d} {stem_name} - edited.wav"
            edited_relatives.append(relative)
            edited_destinations.append((track.track_id, relative))
        edited_clip_counts = _write_track_wavs(
            arrangement_renderer,
            package_binding,
            edited_destinations,
            frames=frames,
            block_frames=block_count,
            cancel_event=cancel_event,
        )

        original_relatives.extend(
            _original_stem_relative(index, plan)
            for index, plan in enumerate(original_plans, start=1)
        )
        if len({item.as_posix().casefold() for item in original_relatives}) != len(
            original_relatives
        ):
            raise StudioExportError("Original-stem filenames are not collision-free.")
        original_relative_by_key = {
            plan.key: relative
            for plan, relative in zip(original_plans, original_relatives, strict=True)
        }
        original_clip_counts: dict[str, int] = {}
        for take_id, plans in plans_by_take.items():
            counts = _write_track_wavs(
                original_renderers[take_id],
                package_binding,
                [
                    (plan.track.track_id, original_relative_by_key[plan.key])
                    for plan in plans
                ],
                frames=frames,
                block_frames=block_count,
                cancel_event=cancel_event,
            )
            original_clip_counts.update(
                {
                    original_relative_by_key[plan.key].as_posix(): counts[
                        plan.track.track_id
                    ]
                    for plan in plans
                }
            )

        rough_mix_relative = Path("rough-mix.wav")
        rough_mix_clip_count = _write_renderer_wav(
            arrangement_renderer,
            package_binding,
            rough_mix_relative,
            frames=frames,
            block_frames=block_count,
            cancel_event=cancel_event,
        )

        _check_cancelled(cancel_event)
        markers_relative = Path("markers-and-sections.csv")
        instructions_relative = Path("IMPORT_INSTRUCTIONS.md")
        provenance_relative = Path("provenance.json")
        checksums_relative = Path("SHA256SUMS.txt")
        document_relative = Path("studio-document.json")
        source_manifest_relative = _take_manifest_relative(
            project.take_id,
            primary_take_id=project.take_id,
        )
        _write_text(
            package_binding,
            markers_relative,
            _markers_csv(document, project.project_sample_rate),
        )
        _write_text(
            package_binding,
            instructions_relative,
            _instructions(project.project_sample_rate, frames),
        )
        document_bytes = (
            json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _write_package_bytes(
            package_binding,
            document_relative,
            document_bytes,
        )
        for take_snapshot, relative in manifest_relatives:
            _write_package_bytes(
                package_binding,
                relative,
                _verified_snapshot_bytes(
                    take_snapshot.manifest,
                    f"take manifest {take_snapshot.take_id}",
                ),
            )

        audio_relatives = [
            *edited_relatives,
            *original_relatives,
            rough_mix_relative,
        ]
        output_hashes = {
            relative.as_posix(): _hash_package_file(
                package_binding,
                relative,
                cancel_event,
            )
            for relative in audio_relatives
        }
        output_clip_counts = {
            relative.as_posix(): edited_clip_counts[track.track_id]
            for track, relative in zip(selected_tracks, edited_relatives)
        }
        output_clip_counts.update(
            {
                relative.as_posix(): original_clip_counts[relative.as_posix()]
                for relative in original_relatives
            }
        )
        output_clip_counts[rough_mix_relative.as_posix()] = rough_mix_clip_count
        provenance = _provenance_payload(
            project=project,
            document=document,
            manifest_snapshot=manifest_snapshot,
            take_snapshots=take_snapshots,
            state_snapshot=state_snapshot,
            source_snapshots=source_snapshots,
            selected_tracks=selected_tracks,
            edited_relatives=edited_relatives,
            original_relatives=original_relatives,
            original_plans=original_plans,
            output_hashes=output_hashes,
            output_clip_counts=output_clip_counts,
            embedded_document_hash=hashlib.sha256(document_bytes).hexdigest(),
            sample_rate=project.project_sample_rate,
            frames=frames,
            estimated_bytes=estimated_bytes,
            disk_reserve_bytes=reserve,
        )
        _write_text(
            package_binding,
            provenance_relative,
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        )
        checksummed_relatives = [
            *audio_relatives,
            markers_relative,
            document_relative,
            *(relative for _snapshot, relative in manifest_relatives),
            provenance_relative,
            instructions_relative,
        ]
        _write_text(
            package_binding,
            checksums_relative,
            _checksum_manifest(
                package_binding,
                checksummed_relatives,
                cancel_event,
                expected_hashes=output_hashes,
            ),
        )

        _verify_sources(source_snapshots, cancel_event)
        for take_snapshot in take_snapshots:
            _verify_take_snapshot(take_snapshot)
        if source_catalog is not None:
            try:
                source_catalog.assert_current(lambda: _check_cancelled(cancel_event))
            except StudioSourceCatalogError as exc:
                raise StudioExportError(str(exc)) from exc
        _verify_optional_state_snapshot(take_folder, state_snapshot)
        _check_cancelled(cancel_event)
        _validate_package_tree(
            export_root_binding,
            package_binding,
            package_binding.temporary.name,
        )
        _fsync_package_tree(package_binding)
        _require_export_root_identity(export_root_binding)
        _validate_package_tree(
            export_root_binding,
            package_binding,
            package_binding.temporary.name,
        )
        with _export_publication_lock(export_root_binding):
            _require_export_root_identity(export_root_binding)
            _validate_package_tree(
                export_root_binding,
                package_binding,
                package_binding.temporary.name,
            )
            _check_cancelled(cancel_event)
            final_name = _next_folder_name(export_root_binding)
            final_folder = export_root / final_name
            _check_cancelled(cancel_event)
            _validate_package_tree(
                export_root_binding,
                package_binding,
                package_binding.temporary.name,
            )
            _check_cancelled(cancel_event)
            _publish_directory_no_replace(
                export_root_binding,
                package_binding.temporary.name,
                final_name,
            )
            package_binding.temporary.name = final_name
            package_binding.temporary.path = final_folder
            try:
                _require_export_root_identity(export_root_binding)
                _validate_package_tree(
                    export_root_binding,
                    package_binding,
                    final_name,
                )
            except StudioExportError as exc:
                failed_package = package_binding
                package_binding = None
                _raise_publication_identity_failure(
                    export_root_binding,
                    failed_package,
                    exc,
                )
            try:
                _fsync_export_root(export_root_binding)
            except OSError as exc:
                try:
                    _require_export_root_identity(export_root_binding)
                    _validate_package_tree(
                        export_root_binding,
                        package_binding,
                        final_name,
                    )
                except StudioExportError as identity_exc:
                    failed_package = package_binding
                    package_binding = None
                    _raise_publication_identity_failure(
                        export_root_binding,
                        failed_package,
                        identity_exc,
                    )
                try:
                    package_binding.close()
                except OSError:
                    pass
                package_binding = None
                raise StudioExportPublishedError(final_folder) from exc
            try:
                _require_export_root_identity(export_root_binding)
                _validate_package_tree(
                    export_root_binding,
                    package_binding,
                    final_name,
                )
            except StudioExportError as exc:
                failed_package = package_binding
                package_binding = None
                _raise_publication_identity_failure(
                    export_root_binding,
                    failed_package,
                    exc,
                )
            try:
                package_binding.close()
            except OSError:
                pass
            package_binding = None

        return StudioExportResult(
            folder=final_folder,
            edited_stems=tuple(final_folder / item for item in edited_relatives),
            original_stems=tuple(final_folder / item for item in original_relatives),
            rough_mix=final_folder / rough_mix_relative,
            markers_csv=final_folder / markers_relative,
            provenance=final_folder / provenance_relative,
            checksums=final_folder / checksums_relative,
            instructions=final_folder / instructions_relative,
            studio_document=final_folder / document_relative,
            source_manifest=final_folder / source_manifest_relative,
            source_manifests=tuple(
                final_folder / relative for _snapshot, relative in manifest_relatives
            ),
            sample_rate=project.project_sample_rate,
            frames=frames,
        )
    except StudioExportCancelled:
        raise
    except StudioExportError:
        raise
    except (OSError, StudioRenderError, ValueError) as exc:
        raise StudioExportError("Studio export could not be completed safely.") from exc
    finally:
        try:
            if package_binding is not None:
                if export_root_binding is None:
                    raise StudioExportError(
                        "Studio could not verify its unpublished export transaction."
                    )
                _remove_bound_package(
                    export_root_binding,
                    package_binding,
                )
        finally:
            if package_binding is not None:
                package_binding.close()
            if export_root_binding is not None:
                try:
                    _remove_created_root_if_empty(export_root_binding)
                finally:
                    export_root_binding.close()


__all__ = [
    "DEFAULT_DISK_RESERVE_BYTES",
    "STUDIO_EXPORT_SCHEMA_VERSION",
    "StudioExportCancelled",
    "StudioExportError",
    "StudioExportPublishedError",
    "StudioExportResult",
    "export_studio_arrangement",
    "studio_export_supported",
]

"""Trusted, immutable source inventory for Studio comping across takes.

Studio documents reference recorder media only by durable
``(take_id, track_id, segment_id)`` tuples.  This module is the authority that
turns those tuples into source facts for repeated-take comping.  Callers may
name take *folders*, but never individual media paths: every media path comes
from a descriptor-read, schema-v2 ``webjam-take.json`` manifest and remains
contained by that take folder.

The catalog retains exact manifest and directory identity receipts.  A
renderer can therefore reject a manifest/root replacement before opening any
audio, while its own descriptor-bound media checks continue to protect each
source file during validation and rendering.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from core.take_project import (
    PROJECT_SCHEMA_VERSION,
    MediaSegment,
    ProjectTrack,
    TakeProject,
    TakeProjectError,
)


MAX_STUDIO_SOURCE_TAKES = 128
MAX_STUDIO_SOURCE_MANIFEST_BYTES = 16 * 1024 * 1024

StudioSourceKey = tuple[str, str, str]
_CATALOG_AUTHORITY = object()


class StudioSourceCatalogError(ValueError):
    """Raised when a repeated-take source catalog cannot be trusted."""


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


@dataclass(frozen=True)
class StudioCatalogSource:
    """One manifest-derived source addressed by durable IDs."""

    take_id: str
    track_id: str
    segment_id: str
    take_root: Path
    take_root_identity: tuple[int, int, int]
    track: ProjectTrack
    segment: MediaSegment

    @property
    def key(self) -> StudioSourceKey:
        return (self.take_id, self.track_id, self.segment_id)

    @property
    def path(self) -> Path:
        """Return a checked convenience path beneath the trusted take root.

        The renderer does not rely on this pathname: it opens the manifest's
        relative components through a bound root descriptor. Other read-only
        consumers still receive a path that rejects an already-swapped root or
        intermediate symbolic link and verifies component identity around
        canonicalization.
        """

        parts = tuple(self.segment.path.split("/"))
        directory_paths = [self.take_root]
        for index in range(1, len(parts)):
            directory_paths.append(self.take_root.joinpath(*parts[:index]))
        try:
            before = tuple(path.lstat() for path in directory_paths)
            for info in before:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise StudioSourceCatalogError(
                        "Studio source path contains a symbolic link or non-directory."
                    )
            if _directory_identity(before[0]) != self.take_root_identity:
                raise StudioSourceCatalogError(
                    "Studio source take root changed after catalog load."
                )
            resolved_parent = directory_paths[-1].resolve(strict=True)
            resolved_parent.relative_to(self.take_root)
            after = tuple(path.lstat() for path in directory_paths)
        except StudioSourceCatalogError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise StudioSourceCatalogError(
                "Studio source path is missing, changed, or escapes its take root."
            ) from exc
        if tuple(_directory_identity(info) for info in before) != tuple(
            _directory_identity(info) for info in after
        ):
            raise StudioSourceCatalogError(
                "Studio source path changed while it was being inspected."
            )
        for info in after:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise StudioSourceCatalogError(
                    "Studio source path contains a symbolic link or non-directory."
                )
        return resolved_parent / parts[-1]


@dataclass(frozen=True)
class _FileReceipt:
    fingerprint: tuple[int, ...]
    sha256: str


@dataclass(frozen=True)
class _CatalogTake:
    root: Path
    root_identity: tuple[int, int, int]
    manifest_path: Path
    manifest_receipt: _FileReceipt
    project: TakeProject


def _file_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    left_id = (int(getattr(left, "st_dev", 0)), int(getattr(left, "st_ino", 0)))
    right_id = (
        int(getattr(right, "st_dev", 0)),
        int(getattr(right, "st_ino", 0)),
    )
    if left_id[1] or right_id[1]:
        return left_id == right_id
    return _file_fingerprint(left) == _file_fingerprint(right)


def _trusted_take_root(value: str | Path) -> tuple[Path, tuple[int, int, int]]:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        before = path.lstat()
    except OSError as exc:
        raise StudioSourceCatalogError("Studio take root is missing.") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StudioSourceCatalogError("Studio take root must not be a symbolic link.")
    if not stat.S_ISDIR(before.st_mode):
        raise StudioSourceCatalogError("Studio take root must be a directory.")
    try:
        resolved = path.resolve(strict=True)
        current = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise StudioSourceCatalogError("Studio take root could not be opened.") from exc
    if not stat.S_ISDIR(current.st_mode) or not _same_inode(before, current):
        raise StudioSourceCatalogError(
            "Studio take root changed while it was being opened."
        )
    identity = _directory_identity(current)
    return resolved, identity


def _open_regular_readonly(path: Path) -> tuple[int, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise StudioSourceCatalogError("Studio take manifest is missing.") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StudioSourceCatalogError(
            "Studio take manifest must not be a symbolic link."
        )
    if not stat.S_ISREG(before.st_mode):
        raise StudioSourceCatalogError("Studio take manifest must be a regular file.")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        try:
            current = path.lstat()
        except OSError:
            current = None
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise StudioSourceCatalogError(
                "Studio take manifest must not be a symbolic link."
            ) from exc
        raise StudioSourceCatalogError(
            "Studio take manifest could not be opened."
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StudioSourceCatalogError(
                "Studio take manifest must be a regular file."
            )
        current = path.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or not _same_inode(before, info)
            or not _same_inode(current, info)
        ):
            raise StudioSourceCatalogError(
                "Studio take manifest changed while it was being opened."
            )
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _read_manifest(
    path: Path,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[bytes, _FileReceipt]:
    descriptor = -1
    try:
        descriptor, info = _open_regular_readonly(path)
        if info.st_size > MAX_STUDIO_SOURCE_MANIFEST_BYTES:
            raise StudioSourceCatalogError("Studio take manifest is too large.")
        chunks: list[bytes] = []
        remaining = MAX_STUDIO_SOURCE_MANIFEST_BYTES + 1
        while remaining:
            if cancel_check is not None:
                cancel_check()
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b"".join(chunks)
        final_info = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError as exc:
            raise StudioSourceCatalogError(
                "Studio take manifest changed while it was being read."
            ) from exc
        if (
            len(data) > MAX_STUDIO_SOURCE_MANIFEST_BYTES
            or _file_fingerprint(final_info) != _file_fingerprint(info)
            or stat.S_ISLNK(current.st_mode)
            or not _same_inode(current, final_info)
        ):
            raise StudioSourceCatalogError(
                "Studio take manifest changed while it was being read."
            )
        return data, _FileReceipt(
            fingerprint=_file_fingerprint(final_info),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    except OSError as exc:
        raise StudioSourceCatalogError(
            "Studio take manifest could not be read."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_take(value: str | Path) -> _CatalogTake:
    root, root_identity = _trusted_take_root(value)
    manifest = root / "webjam-take.json"
    data, receipt = _read_manifest(manifest)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StudioSourceCatalogError(
            "Studio take manifest is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise StudioSourceCatalogError("Studio take manifest root must be an object.")
    if payload.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise StudioSourceCatalogError(
            "Repeated-take comping requires a schema-v2 take manifest."
        )
    try:
        project = TakeProject.from_dict(payload)
    except TakeProjectError as exc:
        raise StudioSourceCatalogError(
            "Studio take manifest could not be trusted."
        ) from exc
    return _CatalogTake(
        root=root,
        root_identity=root_identity,
        manifest_path=manifest,
        manifest_receipt=receipt,
        project=project,
    )


class StudioSourceCatalog:
    """Read-only manifest authority for one session's repeated takes.

    Build catalogs with :meth:`load`.  The constructor intentionally rejects
    direct caller-created mappings so a renderer never accepts arbitrary media
    paths disguised as catalog entries.
    """

    __slots__ = (
        "_primary_take_id",
        "_session_id",
        "_project_sample_rate",
        "_takes",
        "_sources",
    )

    def __init__(
        self,
        *,
        primary_take_id: str,
        session_id: str,
        project_sample_rate: int,
        takes: Mapping[str, _CatalogTake],
        sources: Mapping[StudioSourceKey, StudioCatalogSource],
        _authority: object,
    ) -> None:
        if _authority is not _CATALOG_AUTHORITY:
            raise StudioSourceCatalogError(
                "Studio source catalogs must be loaded from take manifests."
            )
        self._primary_take_id = primary_take_id
        self._session_id = session_id
        self._project_sample_rate = project_sample_rate
        self._takes = MappingProxyType(dict(takes))
        self._sources = MappingProxyType(dict(sources))

    @classmethod
    def load(
        cls,
        primary_project: TakeProject,
        primary_take_root: str | Path,
        *,
        additional_take_roots: Iterable[str | Path] = (),
    ) -> "StudioSourceCatalog":
        """Load exact schema-v2 manifests for a primary and alternate takes."""

        if not isinstance(primary_project, TakeProject):
            raise StudioSourceCatalogError(
                "A Studio source catalog requires a TakeProject."
            )
        if isinstance(additional_take_roots, (str, bytes, Path)):
            raise StudioSourceCatalogError(
                "additional_take_roots must be an iterable of take folders."
            )
        try:
            additional_roots = iter(additional_take_roots)
        except TypeError as exc:
            raise StudioSourceCatalogError(
                "additional_take_roots must be an iterable of take folders."
            ) from exc
        roots: list[str | Path] = [primary_take_root]
        for root in additional_roots:
            if len(roots) >= MAX_STUDIO_SOURCE_TAKES:
                raise StudioSourceCatalogError(
                    "A Studio source catalog supports at most "
                    f"{MAX_STUDIO_SOURCE_TAKES} takes."
                )
            roots.append(root)

        loaded = tuple(_load_take(root) for root in roots)
        primary = loaded[0]
        if primary.project.take_id != primary_project.take_id:
            raise StudioSourceCatalogError(
                "The primary take root belongs to a different take."
            )
        if primary.project.to_dict() != primary_project.to_dict():
            raise StudioSourceCatalogError(
                "The supplied primary project does not match its manifest."
            )

        takes: dict[str, _CatalogTake] = {}
        roots_seen: set[Path] = set()
        sources: dict[StudioSourceKey, StudioCatalogSource] = {}
        for take in loaded:
            project = take.project
            if take.root in roots_seen:
                raise StudioSourceCatalogError(
                    "A take root appears more than once in the Studio source catalog."
                )
            roots_seen.add(take.root)
            if project.take_id in takes:
                raise StudioSourceCatalogError(
                    "A take ID appears more than once in the Studio source catalog."
                )
            if project.session_id != primary_project.session_id:
                raise StudioSourceCatalogError(
                    "Every Studio source take must belong to the same session."
                )
            if project.project_sample_rate != primary_project.project_sample_rate:
                raise StudioSourceCatalogError(
                    "Every Studio source take must use the same project sample rate."
                )
            takes[project.take_id] = take
            for track in project.tracks:
                for segment in track.segments:
                    source = StudioCatalogSource(
                        take_id=project.take_id,
                        track_id=track.track_id,
                        segment_id=segment.segment_id,
                        take_root=take.root,
                        take_root_identity=take.root_identity,
                        track=track,
                        segment=segment,
                    )
                    if source.key in sources:
                        raise StudioSourceCatalogError(
                            "A Studio source key appears more than once."
                        )
                    sources[source.key] = source

        return cls(
            primary_take_id=primary_project.take_id,
            session_id=primary_project.session_id,
            project_sample_rate=primary_project.project_sample_rate,
            takes=takes,
            sources=sources,
            _authority=_CATALOG_AUTHORITY,
        )

    @property
    def primary_take_id(self) -> str:
        return self._primary_take_id

    @property
    def primary_take_root(self) -> Path:
        return self._takes[self._primary_take_id].root

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def project_sample_rate(self) -> int:
        return self._project_sample_rate

    @property
    def source_keys(self) -> tuple[StudioSourceKey, ...]:
        return tuple(sorted(self._sources))

    @property
    def take_ids(self) -> tuple[str, ...]:
        return tuple(self._takes)

    def project_for_take(self, take_id: str) -> TakeProject:
        """Return the immutable manifest project for one cataloged take."""

        try:
            return self._takes[str(take_id)].project
        except KeyError as exc:
            raise StudioSourceCatalogError(
                "Studio take is not present in the trusted source catalog."
            ) from exc

    def root_for_take(self, take_id: str) -> Path:
        """Return the identity-checked root for one cataloged take."""

        try:
            return self._takes[str(take_id)].root
        except KeyError as exc:
            raise StudioSourceCatalogError(
                "Studio take is not present in the trusted source catalog."
            ) from exc

    def __len__(self) -> int:
        return len(self._sources)

    def resolve(
        self, take_id: str, track_id: str, segment_id: str
    ) -> StudioCatalogSource:
        key = (str(take_id), str(track_id), str(segment_id))
        try:
            return self._sources[key]
        except KeyError as exc:
            raise StudioSourceCatalogError(
                "Studio source is not present in the trusted take catalog."
            ) from exc

    def require_primary(self, project: TakeProject, take_root: str | Path) -> None:
        """Require renderer inputs to name this catalog's exact primary take."""

        if not isinstance(project, TakeProject):
            raise StudioSourceCatalogError(
                "Studio source catalog primary project is invalid."
            )
        try:
            supplied_root = Path(take_root).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise StudioSourceCatalogError(
                "Studio source catalog primary root could not be resolved."
            ) from exc
        primary = self._takes[self._primary_take_id]
        if (
            project.session_id != self._session_id
            or project.take_id != self._primary_take_id
            or project.project_sample_rate != self._project_sample_rate
            or supplied_root != primary.root
            or project.to_dict() != primary.project.to_dict()
        ):
            raise StudioSourceCatalogError(
                "Studio source catalog does not match the renderer's primary take."
            )

    def assert_current(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        """Fail if a take root or exact manifest changed after catalog load."""

        if cancel_check is not None and not callable(cancel_check):
            raise StudioSourceCatalogError("cancel_check must be callable or null.")
        for take_id in self._takes:
            if cancel_check is not None:
                cancel_check()
            take = self._takes[take_id]
            try:
                info = take.root.lstat()
            except OSError as exc:
                raise StudioSourceCatalogError(
                    "Studio take root changed after catalog load."
                ) from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise StudioSourceCatalogError(
                    "Studio take root changed after catalog load."
                )
            identity = _directory_identity(info)
            if identity != take.root_identity:
                raise StudioSourceCatalogError(
                    "Studio take root changed after catalog load."
                )
            _data, receipt = _read_manifest(
                take.manifest_path,
                cancel_check=cancel_check,
            )
            if receipt != take.manifest_receipt:
                raise StudioSourceCatalogError(
                    "Studio take manifest changed after catalog load."
                )

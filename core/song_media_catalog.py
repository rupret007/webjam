"""Sealed media authority for standalone Reference Studio projects.

Song project documents name collected media only by durable IDs and safe
``Media/<name>`` members.  This module binds those declarations to one exact
bundle, manifest revision, directory identity, and verified regular-file
identity.  Playback, waveforms, and renderers can therefore accept a
``SongMediaCatalog`` instead of accepting caller-chosen paths.

Catalog construction performs full checksum and audio-metadata verification.
The cheaper :meth:`SongMediaCatalog.assert_current` guard detects replacement
of the bundle, manifest, Media directory, or any cataloged member before a
worker starts.  Consumers that cross a long-running trust boundary may request
another full content verification.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.song_project import SongMedia, SongProject
from core.song_project_store import (
    MAX_PROJECT_MANIFEST_BYTES,
    PROJECT_MANIFEST_FILENAME,
    SongProjectStoreError,
    load_project_bundle,
    resolve_project_media,
    verify_project_media,
)

_CATALOG_AUTHORITY = object()


class SongMediaCatalogError(ValueError):
    """Raised when collected project media cannot be trusted."""


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    left_inode = (
        int(getattr(left, "st_dev", 0)),
        int(getattr(left, "st_ino", 0)),
    )
    right_inode = (
        int(getattr(right, "st_dev", 0)),
        int(getattr(right, "st_ino", 0)),
    )
    if left_inode[1] or right_inode[1]:
        return left_inode == right_inode
    return _file_identity(left) == _file_identity(right)


def _trusted_directory(
    value: str | Path,
    *,
    label: str,
) -> tuple[Path, tuple[int, int, int]]:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise OSError
        resolved = path.resolve(strict=True)
        after = resolved.stat()
    except (OSError, RuntimeError):
        raise SongMediaCatalogError(f"{label} must be one real directory.") from None
    if not _same_file(before, after):
        raise SongMediaCatalogError(f"{label} changed while it was opened.")
    return resolved, _directory_identity(after)


def _open_regular_readonly(
    path: Path,
    *,
    label: str,
) -> tuple[int, os.stat_result]:
    descriptor = -1
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not _same_file(before, opened)
            or not _same_file(current, opened)
        ):
            raise OSError
        return descriptor, opened
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise SongMediaCatalogError(
            f"{label} must be one stable regular file."
        ) from None


def _read_receipt(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[tuple[int, int, int, int, int, int], str]:
    descriptor = -1
    try:
        descriptor, opened = _open_regular_readonly(path, label=label)
        if opened.st_size > maximum_bytes:
            raise SongMediaCatalogError(f"{label} exceeds its safe size limit.")
        digest = hashlib.sha256()
        total = 0
        while True:
            if cancel_check is not None:
                cancel_check()
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SongMediaCatalogError(f"{label} exceeds its safe size limit.")
            digest.update(chunk)
        final = os.fstat(descriptor)
        current = path.lstat()
        if (
            _file_identity(opened) != _file_identity(final)
            or stat.S_ISLNK(current.st_mode)
            or not _same_file(current, final)
            or total != final.st_size
        ):
            raise SongMediaCatalogError(f"{label} changed while it was read.")
        return _file_identity(final), digest.hexdigest()
    except SongMediaCatalogError:
        raise
    except OSError:
        raise SongMediaCatalogError(f"{label} could not be read safely.") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class SongCatalogSource:
    """One descriptor-backed project media declaration."""

    project_id: str
    media: SongMedia
    bundle_root: Path
    bundle_identity: tuple[int, int, int]
    media_root: Path
    media_root_identity: tuple[int, int, int]
    member_path: Path
    member_identity: tuple[int, int, int, int, int, int]

    @property
    def media_id(self) -> str:
        return self.media.media_id

    @property
    def path(self) -> Path:
        """Return the member path only while every bound identity is current."""

        try:
            bundle_info = self.bundle_root.lstat()
            media_info = self.media_root.lstat()
            member_info = self.member_path.lstat()
        except OSError:
            raise SongMediaCatalogError(
                "Collected project media changed or became unavailable."
            ) from None
        if (
            stat.S_ISLNK(bundle_info.st_mode)
            or not stat.S_ISDIR(bundle_info.st_mode)
            or _directory_identity(bundle_info) != self.bundle_identity
            or stat.S_ISLNK(media_info.st_mode)
            or not stat.S_ISDIR(media_info.st_mode)
            or _directory_identity(media_info) != self.media_root_identity
            or stat.S_ISLNK(member_info.st_mode)
            or not stat.S_ISREG(member_info.st_mode)
            or _file_identity(member_info) != self.member_identity
        ):
            raise SongMediaCatalogError(
                "Collected project media changed or became unavailable."
            )
        return self.member_path


class SongMediaCatalog:
    """Read-only verified media inventory for one exact song project bundle."""

    __slots__ = (
        "_bundle_identity",
        "_bundle_root",
        "_manifest_identity",
        "_manifest_sha256",
        "_media_root",
        "_media_root_identity",
        "_project",
        "_sources",
    )

    def __init__(
        self,
        *,
        project: SongProject,
        bundle_root: Path,
        bundle_identity: tuple[int, int, int],
        media_root: Path,
        media_root_identity: tuple[int, int, int],
        manifest_identity: tuple[int, int, int, int, int, int],
        manifest_sha256: str,
        sources: Mapping[str, SongCatalogSource],
        _authority: object,
    ) -> None:
        if _authority is not _CATALOG_AUTHORITY:
            raise SongMediaCatalogError(
                "Song media catalogs must be loaded from a saved project bundle."
            )
        self._project = project
        self._bundle_root = bundle_root
        self._bundle_identity = bundle_identity
        self._media_root = media_root
        self._media_root_identity = media_root_identity
        self._manifest_identity = manifest_identity
        self._manifest_sha256 = manifest_sha256
        self._sources = MappingProxyType(dict(sources))

    @classmethod
    def load(
        cls,
        project: SongProject,
        bundle_path: str | Path,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> SongMediaCatalog:
        """Seal the exact saved manifest and fully verified collected media."""

        if not isinstance(project, SongProject):
            raise SongMediaCatalogError("A song media catalog requires a SongProject.")
        if cancel_check is not None and not callable(cancel_check):
            raise SongMediaCatalogError("cancel_check must be callable or null.")
        bundle_root, bundle_identity = _trusted_directory(
            bundle_path,
            label="Project bundle",
        )
        if cancel_check is not None:
            cancel_check()
        try:
            loaded = load_project_bundle(bundle_root)
        except SongProjectStoreError:
            raise SongMediaCatalogError(
                "The saved project manifest could not be trusted."
            ) from None
        if loaded.project.to_dict() != project.to_dict():
            raise SongMediaCatalogError(
                "The open project does not match its saved project manifest."
            )
        manifest_path = bundle_root / PROJECT_MANIFEST_FILENAME
        manifest_identity, manifest_sha256 = _read_receipt(
            manifest_path,
            label="Project manifest",
            maximum_bytes=MAX_PROJECT_MANIFEST_BYTES,
            cancel_check=cancel_check,
        )
        if loaded.token != manifest_sha256:
            raise SongMediaCatalogError(
                "The saved project manifest changed while it was opened."
            )

        media_root, media_root_identity = _trusted_directory(
            bundle_root / "Media",
            label="Project Media directory",
        )
        try:
            verified_paths = verify_project_media(bundle_root, project)
        except SongProjectStoreError:
            raise SongMediaCatalogError(
                "Collected project media failed verification."
            ) from None
        path_by_media = dict(zip(project.media, verified_paths, strict=True))
        sources: dict[str, SongCatalogSource] = {}
        for media in project.media:
            if cancel_check is not None:
                cancel_check()
            try:
                member = resolve_project_media(bundle_root, media)
                member.relative_to(media_root)
            except (SongProjectStoreError, ValueError):
                raise SongMediaCatalogError(
                    "Collected project media escaped its project bundle."
                ) from None
            if member != path_by_media[media]:
                raise SongMediaCatalogError(
                    "Collected project media changed during verification."
                )
            descriptor, info = _open_regular_readonly(
                member,
                label="Collected project media",
            )
            os.close(descriptor)
            source = SongCatalogSource(
                project_id=project.project_id,
                media=media,
                bundle_root=bundle_root,
                bundle_identity=bundle_identity,
                media_root=media_root,
                media_root_identity=media_root_identity,
                member_path=member,
                member_identity=_file_identity(info),
            )
            if media.media_id in sources:
                raise SongMediaCatalogError(
                    "Project contains duplicate durable media IDs."
                )
            sources[media.media_id] = source
        return cls(
            project=project,
            bundle_root=bundle_root,
            bundle_identity=bundle_identity,
            media_root=media_root,
            media_root_identity=media_root_identity,
            manifest_identity=manifest_identity,
            manifest_sha256=manifest_sha256,
            sources=sources,
            _authority=_CATALOG_AUTHORITY,
        )

    @property
    def project_id(self) -> str:
        return self._project.project_id

    @property
    def project(self) -> SongProject:
        return self._project

    @property
    def bundle_root(self) -> Path:
        return self._bundle_root

    @property
    def media_ids(self) -> tuple[str, ...]:
        return tuple(media.media_id for media in self._project.media)

    def __len__(self) -> int:
        return len(self._sources)

    def resolve(self, media_id: str) -> SongCatalogSource:
        try:
            return self._sources[str(media_id)]
        except KeyError:
            raise SongMediaCatalogError(
                "Media is not present in the trusted project catalog."
            ) from None

    def require_project(
        self,
        project: SongProject,
        bundle_path: str | Path,
    ) -> None:
        if not isinstance(project, SongProject):
            raise SongMediaCatalogError("Song media catalog project is invalid.")
        try:
            supplied, identity = _trusted_directory(
                bundle_path,
                label="Project bundle",
            )
        except SongMediaCatalogError:
            raise
        if (
            supplied != self._bundle_root
            or identity != self._bundle_identity
            or project.to_dict() != self._project.to_dict()
        ):
            raise SongMediaCatalogError(
                "Song media catalog does not match the open project."
            )

    def assert_current(
        self,
        *,
        verify_content: bool = False,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        """Reject replacements, with optional full digest/metadata verification."""

        if cancel_check is not None and not callable(cancel_check):
            raise SongMediaCatalogError("cancel_check must be callable or null.")
        try:
            bundle_info = self._bundle_root.lstat()
            media_info = self._media_root.lstat()
        except OSError:
            raise SongMediaCatalogError(
                "The project bundle changed after media verification."
            ) from None
        if (
            stat.S_ISLNK(bundle_info.st_mode)
            or not stat.S_ISDIR(bundle_info.st_mode)
            or _directory_identity(bundle_info) != self._bundle_identity
            or stat.S_ISLNK(media_info.st_mode)
            or not stat.S_ISDIR(media_info.st_mode)
            or _directory_identity(media_info) != self._media_root_identity
        ):
            raise SongMediaCatalogError(
                "The project bundle changed after media verification."
            )
        manifest_identity, manifest_sha256 = _read_receipt(
            self._bundle_root / PROJECT_MANIFEST_FILENAME,
            label="Project manifest",
            maximum_bytes=MAX_PROJECT_MANIFEST_BYTES,
            cancel_check=cancel_check,
        )
        if (
            manifest_identity != self._manifest_identity
            or manifest_sha256 != self._manifest_sha256
        ):
            raise SongMediaCatalogError(
                "The project manifest changed after media verification."
            )
        for media_id in self.media_ids:
            if cancel_check is not None:
                cancel_check()
            self.resolve(media_id).path
        if verify_content:
            try:
                verify_project_media(self._bundle_root, self._project)
            except SongProjectStoreError:
                raise SongMediaCatalogError(
                    "Collected project media failed verification."
                ) from None


__all__ = [
    "SongCatalogSource",
    "SongMediaCatalog",
    "SongMediaCatalogError",
]

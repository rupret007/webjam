"""Crash-safe portable bundle storage for :mod:`core.song_project`.

A schema-1 bundle has a deliberately small contract::

    My Song.webjam/
      webjam-project.json
      Media/
        <durable-media-id>.<extension>

Primary saves are exact-byte compare-and-swap operations.  Valid prior bytes
become ``.bak`` before an atomic, fsynced replacement.  Autosave is a separate
strict envelope and is never silently promoted; load reports a recovery
candidate which the caller must explicitly recover.

External file paths are accepted only as transient function arguments.  Media
is copied through an already-open regular-file descriptor, checksummed while
copying, probed from the project-owned copy, and represented in the manifest by
``Media/...`` plus the source basename only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from core.file_io import atomic_write_bytes
from core.song_project import (
    DEFAULT_PROJECT_SAMPLE_RATE,
    MAX_MEDIA_FILE_BYTES,
    MediaImportMethod,
    MediaProvenance,
    SongMedia,
    SongProject,
    SongProjectError,
    TimeSignature,
)


PROJECT_MANIFEST_FILENAME = "webjam-project.json"
PROJECT_BACKUP_FILENAME = ".webjam-project.json.bak"
PROJECT_AUTOSAVE_FILENAME = ".webjam-project.autosave.json"
PROJECT_LOCK_FILENAME = ".webjam-project.lock"
PROJECT_AUTOSAVE_SCHEMA_VERSION = 1
RECENT_PROJECTS_SCHEMA_VERSION = 1
MAX_PROJECT_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_AUTOSAVE_BYTES = 8 * 1024 * 1024
MAX_RECENT_PROJECTS_BYTES = 64 * 1024
MAX_RECENT_PROJECTS = 20
MAX_RECENT_PATH_BYTES = 4_096
COPY_CHUNK_BYTES = 1024 * 1024

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class SongProjectStoreError(ValueError):
    """Raised when bundle state cannot be safely trusted or persisted."""


class SongProjectConflict(SongProjectStoreError):
    """Raised when exact primary bytes changed since the caller loaded them."""


class _UnsafeBundlePath(SongProjectStoreError):
    """A path redirect or non-regular object which must not be followed."""


class _OversizedBundleFile(SongProjectStoreError):
    """An invalid file that must not be silently replaced through recovery."""


class ProjectLoadOrigin(str, Enum):
    PRIMARY = "primary"
    BACKUP = "backup"


@dataclass(frozen=True)
class ProjectRecoveryCandidate:
    project: SongProject
    autosave_token: str
    base_primary_token: str | None
    path: Path


@dataclass(frozen=True)
class ProjectLoadResult:
    project: SongProject
    bundle_path: Path
    token: str | None
    origin: ProjectLoadOrigin
    recovery_candidate: ProjectRecoveryCandidate | None = None
    recovery_notice: str = ""


@dataclass(frozen=True)
class ProjectSaveResult:
    project: SongProject
    bundle_path: Path
    manifest_path: Path
    token: str
    backup_path: Path | None = None
    preserved_corrupt_path: Path | None = None
    autosave_cleared: bool = True


@dataclass(frozen=True)
class MediaImportResult:
    project: SongProject
    media: SongMedia
    path: Path


@dataclass(frozen=True)
class MediaRelinkResult:
    project: SongProject
    media: SongMedia
    path: Path


@dataclass(frozen=True)
class RecentProjects:
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class _CopiedFile:
    sha256: str
    size_bytes: int
    path: Path


def _token(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_token(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SongProjectStoreError(f"{label} must be a lowercase SHA-256 or null.")
    return value


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SongProjectStoreError(f"Could not inspect {path.name}.") from exc


def _bundle_root(bundle_path: str | Path, *, create: bool = False) -> Path:
    raw = Path(bundle_path).expanduser()
    if _exists(raw):
        try:
            info = raw.lstat()
        except OSError as exc:
            raise SongProjectStoreError("Could not inspect the project bundle.") from exc
        if stat.S_ISLNK(info.st_mode):
            raise _UnsafeBundlePath("Project bundle must not be a symbolic link.")
        if not stat.S_ISDIR(info.st_mode):
            raise _UnsafeBundlePath("Project bundle must be a directory.")
    elif create:
        try:
            raw.mkdir(parents=True)
        except OSError as exc:
            raise SongProjectStoreError("Could not create the project bundle.") from exc
        info = raw.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _UnsafeBundlePath("Project bundle must be a real directory.")
    else:
        raise SongProjectStoreError("Project bundle does not exist.")
    try:
        return raw.resolve(strict=True)
    except OSError as exc:
        raise SongProjectStoreError("Could not resolve the project bundle.") from exc


def project_manifest_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / PROJECT_MANIFEST_FILENAME


def project_backup_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / PROJECT_BACKUP_FILENAME


def project_autosave_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / PROJECT_AUTOSAVE_FILENAME


def _open_regular_readonly(path: Path, label: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if not nofollow:
        try:
            before = path.lstat()
        except OSError as exc:
            raise SongProjectStoreError(f"Could not inspect {label}.") from exc
        if stat.S_ISLNK(before.st_mode):
            raise _UnsafeBundlePath(f"{label} must not be a symbolic link.")
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        try:
            current = path.lstat()
        except OSError:
            current = None
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise _UnsafeBundlePath(f"{label} must not be a symbolic link.") from exc
        raise SongProjectStoreError(f"Could not open {label}.") from exc
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            or (
                before is not None
                and (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
            )
        ):
            raise _UnsafeBundlePath(f"{label} must be one stable regular file.")
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_bounded(path: Path, label: str, maximum_bytes: int) -> bytes:
    descriptor, info = _open_regular_readonly(path, label)
    try:
        if info.st_size > maximum_bytes:
            raise _OversizedBundleFile(f"{label} is too large.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(maximum_bytes + 1)
            final = os.fstat(handle.fileno())
        if len(data) > maximum_bytes or final.st_size > maximum_bytes:
            raise _OversizedBundleFile(f"{label} is too large.")
        if (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino):
            raise _UnsafeBundlePath(f"{label} changed while it was read.")
        return data
    except OSError as exc:
        raise SongProjectStoreError(f"Could not read {label}.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_project(data: bytes, label: str) -> SongProject:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SongProjectStoreError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, Mapping):
        raise SongProjectStoreError(f"{label} root must be an object.")
    try:
        return SongProject.from_dict(value)
    except SongProjectError as exc:
        raise SongProjectStoreError(f"{label} is not a valid project: {exc}") from exc


def _serialize_project(project: SongProject) -> bytes:
    if not isinstance(project, SongProject):
        raise SongProjectStoreError("project must be a SongProject value.")
    payload = (
        json.dumps(
            project.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_PROJECT_MANIFEST_BYTES:
        raise SongProjectStoreError("Project manifest is too large to save safely.")
    return payload


def _require_safe_write_target(path: Path, label: str) -> None:
    if not _exists(path):
        return
    try:
        info = path.lstat()
    except OSError as exc:
        raise SongProjectStoreError(f"Could not inspect {label}.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise _UnsafeBundlePath(f"{label} must not be a symbolic link.")
    if not stat.S_ISREG(info.st_mode):
        raise _UnsafeBundlePath(f"{label} must be a regular file.")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def project_store_lock(bundle_path: str | Path) -> Iterator[Path]:
    """Serialize bundle metadata and Media publication across processes."""

    folder = _bundle_root(bundle_path)
    with _LOCKS_GUARD:
        process_lock = _LOCKS.setdefault(folder, threading.RLock())
    with process_lock:
        lock_path = folder / PROJECT_LOCK_FILENAME
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
            descriptor = os.open(lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            current = lock_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise _UnsafeBundlePath(
                    "Project store lock must be one stable regular file."
                )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            descriptor = -1
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            if _exists(lock_path) and stat.S_ISLNK(lock_path.lstat().st_mode):
                raise _UnsafeBundlePath(
                    "Project store lock must not be a symbolic link."
                ) from exc
            raise SongProjectStoreError("Could not open the project store lock.") from exc
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            yield lock_path
        finally:
            try:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()


def _ensure_media_directory(folder: Path) -> Path:
    media_dir = folder / "Media"
    if _exists(media_dir):
        info = media_dir.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _UnsafeBundlePath("Media must be a real directory.")
    else:
        try:
            media_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise SongProjectStoreError("Could not create the Media directory.") from exc
    return media_dir


def resolve_project_media(
    bundle_path: str | Path,
    media: SongMedia,
    *,
    require_exists: bool = True,
) -> Path:
    """Resolve a declared bundle member without following a media symlink."""

    if not isinstance(media, SongMedia):
        raise SongProjectStoreError("media must be a SongMedia value.")
    folder = _bundle_root(bundle_path)
    media_dir = folder / "Media"
    if not _exists(media_dir):
        if require_exists:
            raise SongProjectStoreError("Project Media directory is missing.")
        return folder / media.path
    info = media_dir.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _UnsafeBundlePath("Project Media directory must be a real directory.")
    target = folder / media.path
    if not _exists(target):
        if require_exists:
            raise SongProjectStoreError(
                f"Project media is missing: {media.original_basename}"
            )
        return target
    target_info = target.lstat()
    if stat.S_ISLNK(target_info.st_mode):
        raise _UnsafeBundlePath("Project media must not be a symbolic link.")
    if not stat.S_ISREG(target_info.st_mode):
        raise _UnsafeBundlePath("Project media must be a regular file.")
    return target


def _validate_declared_media_paths(folder: Path, project: SongProject) -> None:
    media_dir = folder / "Media"
    if _exists(media_dir):
        info = media_dir.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _UnsafeBundlePath("Project Media directory must be a real directory.")
    for media in project.media:
        target = folder / media.path
        if not _exists(target):
            continue
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise _UnsafeBundlePath("Project media must not be a symbolic link.")
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeBundlePath("Project media must be a regular file.")


def _decode_autosave(
    data: bytes,
    *,
    primary_project: SongProject,
    primary_token: str | None,
    path: Path,
) -> ProjectRecoveryCandidate | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SongProjectStoreError("Project autosave is not valid UTF-8 JSON.") from exc
    if not isinstance(value, Mapping):
        raise SongProjectStoreError("Project autosave root must be an object.")
    allowed = {"schema_version", "base_primary_token", "project"}
    unknown = set(value).difference(allowed)
    missing = allowed.difference(value)
    if unknown or missing:
        raise SongProjectStoreError("Project autosave has an unsupported shape.")
    if value["schema_version"] != PROJECT_AUTOSAVE_SCHEMA_VERSION:
        raise SongProjectStoreError("Project autosave has an unsupported schema.")
    base_token = _validate_token(value["base_primary_token"], "autosave base token")
    raw_project = value["project"]
    if not isinstance(raw_project, Mapping):
        raise SongProjectStoreError("Project autosave document must be an object.")
    try:
        candidate = SongProject.from_dict(raw_project)
    except SongProjectError as exc:
        raise SongProjectStoreError("Project autosave document is invalid.") from exc
    if candidate.project_id != primary_project.project_id:
        raise SongProjectStoreError("Project autosave belongs to another project.")
    if base_token != primary_token:
        return None
    candidate_payload = _serialize_project(candidate)
    primary_payload = _serialize_project(primary_project)
    if candidate_payload == primary_payload:
        return None
    return ProjectRecoveryCandidate(
        project=candidate,
        autosave_token=_token(data),
        base_primary_token=base_token,
        path=path,
    )


def _recovery_candidate(
    folder: Path,
    project: SongProject,
    primary_token: str | None,
) -> tuple[ProjectRecoveryCandidate | None, str]:
    autosave = folder / PROJECT_AUTOSAVE_FILENAME
    if not _exists(autosave):
        return None, ""
    try:
        data = _read_regular_bounded(
            autosave,
            "project autosave",
            MAX_AUTOSAVE_BYTES,
        )
        candidate = _decode_autosave(
            data,
            primary_project=project,
            primary_token=primary_token,
            path=autosave,
        )
    except _UnsafeBundlePath:
        raise
    except SongProjectStoreError:
        return None, (
            "A damaged crash-recovery autosave was ignored; the explicit "
            "project save remains available."
        )
    return candidate, ""


def load_project_bundle(bundle_path: str | Path) -> ProjectLoadResult:
    """Load the primary, or a last-known-good backup, without writing anything."""

    folder = _bundle_root(bundle_path)
    primary = folder / PROJECT_MANIFEST_FILENAME
    backup = folder / PROJECT_BACKUP_FILENAME
    primary_data: bytes | None = None
    primary_token: str | None = None
    primary_error: SongProjectStoreError | None = None

    if _exists(primary):
        try:
            primary_data = _read_regular_bounded(
                primary,
                "project manifest",
                MAX_PROJECT_MANIFEST_BYTES,
            )
            primary_token = _token(primary_data)
            project = _decode_project(primary_data, "Project manifest")
            _validate_declared_media_paths(folder, project)
            recovery, autosave_notice = _recovery_candidate(
                folder, project, primary_token
            )
            return ProjectLoadResult(
                project=project,
                bundle_path=folder,
                token=primary_token,
                origin=ProjectLoadOrigin.PRIMARY,
                recovery_candidate=recovery,
                recovery_notice=(
                    "A newer crash-recovery autosave is available."
                    if recovery is not None
                    else autosave_notice
                ),
            )
        except _UnsafeBundlePath:
            raise
        except _OversizedBundleFile:
            raise
        except SongProjectStoreError as exc:
            primary_error = exc
    else:
        primary_error = SongProjectStoreError("Project manifest is missing.")

    if _exists(backup):
        try:
            backup_data = _read_regular_bounded(
                backup,
                "project backup",
                MAX_PROJECT_MANIFEST_BYTES,
            )
            project = _decode_project(backup_data, "Project backup")
            _validate_declared_media_paths(folder, project)
            recovery, autosave_notice = _recovery_candidate(
                folder, project, primary_token
            )
        except _UnsafeBundlePath:
            raise
        except SongProjectStoreError as backup_error:
            raise SongProjectStoreError(
                "Project manifest and last-known-good backup are invalid."
            ) from primary_error or backup_error
        return ProjectLoadResult(
            project=project,
            bundle_path=folder,
            token=primary_token,
            origin=ProjectLoadOrigin.BACKUP,
            recovery_candidate=recovery,
            recovery_notice=(
                "Recovered the last-known-good project. The damaged primary "
                "will be preserved on explicit save."
                + (
                    " A newer crash-recovery autosave is also available."
                    if recovery is not None
                    else (f" {autosave_notice}" if autosave_notice else "")
                )
            ),
        )
    assert primary_error is not None
    raise primary_error


def _autosave_bytes(project: SongProject, base_primary_token: str | None) -> bytes:
    base = _validate_token(base_primary_token, "base_primary_token")
    value = {
        "schema_version": PROJECT_AUTOSAVE_SCHEMA_VERSION,
        "base_primary_token": base,
        "project": project.to_dict(),
    }
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
    if len(payload) > MAX_AUTOSAVE_BYTES:
        raise SongProjectStoreError("Project autosave is too large.")
    return payload


def write_project_autosave(
    bundle_path: str | Path,
    project: SongProject,
    *,
    base_primary_token: str | None,
) -> ProjectRecoveryCandidate:
    """CAS-write a recovery snapshot without replacing the explicit save."""

    if not isinstance(project, SongProject):
        raise SongProjectStoreError("project must be a SongProject value.")
    expected = _validate_token(base_primary_token, "base_primary_token")
    folder = _bundle_root(bundle_path)
    with project_store_lock(folder):
        primary = folder / PROJECT_MANIFEST_FILENAME
        if not _exists(primary):
            current_token = None
            primary_project_id = project.project_id
        else:
            current = _read_regular_bounded(
                primary,
                "project manifest",
                MAX_PROJECT_MANIFEST_BYTES,
            )
            current_token = _token(current)
            primary_project_id = _decode_project(
                current, "Project manifest"
            ).project_id
        if current_token != expected:
            raise SongProjectConflict(
                "Project changed after autosave began; reload before autosaving."
            )
        if project.project_id != primary_project_id:
            raise SongProjectStoreError("Autosave belongs to another project.")
        autosave = folder / PROJECT_AUTOSAVE_FILENAME
        _require_safe_write_target(autosave, "project autosave")
        payload = _autosave_bytes(project, expected)
        try:
            atomic_write_bytes(autosave, payload, mode=0o600)
        except OSError as exc:
            raise SongProjectStoreError(
                "Could not atomically write project autosave."
            ) from exc
        return ProjectRecoveryCandidate(
            project=project,
            autosave_token=_token(payload),
            base_primary_token=expected,
            path=autosave,
        )


def _remove_autosave(folder: Path) -> None:
    autosave = folder / PROJECT_AUTOSAVE_FILENAME
    if not _exists(autosave):
        return
    info = autosave.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise _UnsafeBundlePath("Project autosave must not be a symbolic link.")
    if not stat.S_ISREG(info.st_mode):
        raise _UnsafeBundlePath("Project autosave must be a regular file.")
    try:
        autosave.unlink()
        _fsync_directory(folder)
    except OSError as exc:
        raise SongProjectStoreError("Could not remove project autosave.") from exc


def discard_project_autosave(bundle_path: str | Path) -> None:
    folder = _bundle_root(bundle_path)
    with project_store_lock(folder):
        _remove_autosave(folder)


def _preserve_corrupt_primary(folder: Path, data: bytes) -> Path:
    target = folder / f".webjam-project.corrupt-{_token(data)}.json"
    if _exists(target):
        existing = _read_regular_bounded(
            target,
            "preserved corrupt project",
            MAX_PROJECT_MANIFEST_BYTES,
        )
        if existing != data:
            raise SongProjectStoreError(
                "Preserved corrupt-project path contains different bytes."
            )
        return target
    try:
        atomic_write_bytes(target, data, mode=0o600)
    except OSError as exc:
        raise SongProjectStoreError(
            "Could not preserve the damaged project manifest."
        ) from exc
    return target


def save_project_bundle(
    bundle_path: str | Path,
    project: SongProject,
    *,
    expected_token: str | None,
) -> ProjectSaveResult:
    """Explicitly save with exact-byte CAS and last-known-good preservation."""

    if not isinstance(project, SongProject):
        raise SongProjectStoreError("project must be a SongProject value.")
    expected = _validate_token(expected_token, "expected_token")
    folder = _bundle_root(bundle_path)
    payload = _serialize_project(project)
    with project_store_lock(folder):
        _validate_declared_media_paths(folder, project)
        primary = folder / PROJECT_MANIFEST_FILENAME
        backup = folder / PROJECT_BACKUP_FILENAME
        autosave = folder / PROJECT_AUTOSAVE_FILENAME
        _require_safe_write_target(primary, "project manifest")
        _require_safe_write_target(backup, "project backup")
        _require_safe_write_target(autosave, "project autosave")
        current: bytes | None = None
        current_token: str | None = None
        if _exists(primary):
            current = _read_regular_bounded(
                primary,
                "project manifest",
                MAX_PROJECT_MANIFEST_BYTES,
            )
            current_token = _token(current)
        if current_token != expected:
            raise SongProjectConflict(
                "Project changed after it was loaded; reload and merge your edits."
            )

        backup_written: Path | None = None
        corrupt_preserved: Path | None = None
        if current is not None:
            try:
                current_project = _decode_project(current, "Project manifest")
            except SongProjectStoreError:
                corrupt_preserved = _preserve_corrupt_primary(folder, current)
            else:
                if current_project.project_id != project.project_id:
                    raise SongProjectStoreError(
                        "Cannot replace a bundle with a different project identity."
                    )
                try:
                    atomic_write_bytes(backup, current, mode=0o600)
                except OSError as exc:
                    raise SongProjectStoreError(
                        "Could not preserve the last-known-good project."
                    ) from exc
                backup_written = backup

        try:
            atomic_write_bytes(primary, payload, mode=0o600)
        except OSError as exc:
            raise SongProjectStoreError(
                "Could not atomically save the project manifest."
            ) from exc
        autosave_cleared = True
        try:
            _remove_autosave(folder)
        except SongProjectStoreError:
            # The primary commit already succeeded. A stale autosave is bound
            # to the old exact token and therefore cannot be offered as a
            # candidate; report cleanup status without claiming save failure.
            autosave_cleared = False
        return ProjectSaveResult(
            project=project,
            bundle_path=folder,
            manifest_path=primary,
            token=_token(payload),
            backup_path=backup_written,
            preserved_corrupt_path=corrupt_preserved,
            autosave_cleared=autosave_cleared,
        )


def recover_project_autosave(
    bundle_path: str | Path,
    *,
    expected_token: str | None,
) -> ProjectSaveResult:
    loaded = load_project_bundle(bundle_path)
    expected = _validate_token(expected_token, "expected_token")
    if loaded.token != expected:
        raise SongProjectConflict("Project changed before autosave recovery.")
    if loaded.recovery_candidate is None:
        raise SongProjectStoreError("No applicable crash-recovery autosave exists.")
    return save_project_bundle(
        loaded.bundle_path,
        loaded.recovery_candidate.project,
        expected_token=expected,
    )


def create_project_bundle(
    bundle_path: str | Path,
    name: str,
    *,
    project_sample_rate: int = DEFAULT_PROJECT_SAMPLE_RATE,
    tempo_bpm: float = 120.0,
    time_signature: TimeSignature | None = None,
    project_id: str | None = None,
) -> ProjectSaveResult:
    """Create a new independent project in an absent or empty directory."""

    raw = Path(bundle_path).expanduser()
    existed = _exists(raw)
    folder = _bundle_root(raw, create=True)
    if existed and any(folder.iterdir()):
        raise SongProjectStoreError("New project bundle must be empty.")
    _ensure_media_directory(folder)
    project = SongProject.new(
        name,
        project_sample_rate=project_sample_rate,
        tempo_bpm=tempo_bpm,
        time_signature=time_signature,
        project_id=project_id,
    )
    return save_project_bundle(folder, project, expected_token=None)


def _open_copy_source(path: Path) -> tuple[int, os.stat_result]:
    if not _exists(path):
        raise SongProjectStoreError("Media source does not exist.")
    return _open_regular_readonly(path, "media source")


def _publish_descriptor_copy(
    source_path: str | Path,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> _CopiedFile:
    """Copy one stable source descriptor to an exclusively published file."""

    source = Path(source_path).expanduser()
    descriptor, before = _open_copy_source(source)
    if before.st_size <= 0 or before.st_size > MAX_MEDIA_FILE_BYTES:
        os.close(descriptor)
        raise SongProjectStoreError("Media source size is outside project limits.")
    if _exists(destination):
        os.close(descriptor)
        raise SongProjectStoreError("Project media destination is already occupied.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent_info = destination.parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        os.close(descriptor)
        raise _UnsafeBundlePath("Media destination parent must be a real directory.")
    temp_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".copying",
        dir=str(destination.parent),
    )
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    size = 0
    published = False
    completed = False
    try:
        with os.fdopen(descriptor, "rb") as source_handle, os.fdopen(
            temp_descriptor, "wb"
        ) as destination_handle:
            descriptor = -1
            temp_descriptor = -1
            while True:
                chunk = source_handle.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_MEDIA_FILE_BYTES:
                    raise SongProjectStoreError(
                        "Media source grew beyond the project file-size limit."
                    )
                digest.update(chunk)
                destination_handle.write(chunk)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
            after = os.fstat(source_handle.fileno())
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise SongProjectStoreError("Media source changed while it was copied.")
        actual_digest = digest.hexdigest()
        if size != before.st_size:
            raise SongProjectStoreError("Media source size changed while it was copied.")
        if expected_size is not None and size != expected_size:
            raise SongProjectStoreError("Media source byte size does not match project.")
        if expected_sha256 is not None and actual_digest != expected_sha256:
            raise SongProjectStoreError("Media source checksum does not match project.")
        try:
            os.link(temp_path, destination, follow_symlinks=False)
        except TypeError:
            try:
                os.link(temp_path, destination)
            except OSError as exc:
                raise SongProjectStoreError(
                    "Could not exclusively publish the collected media copy."
                ) from exc
        except OSError as exc:
            raise SongProjectStoreError(
                "Could not exclusively publish the collected media copy."
            ) from exc
        published = True
        try:
            temp_path.unlink()
        except OSError:
            # Both names refer to the same fully fsynced inode. The finally
            # block makes another best-effort cleanup without reporting a
            # false copy failure after publication has succeeded.
            pass
        _fsync_directory(destination.parent)
        result = _CopiedFile(
            sha256=actual_digest,
            size_bytes=size,
            path=destination,
        )
        completed = True
        return result
    except OSError as exc:
        raise SongProjectStoreError("Could not copy media into the project.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_descriptor >= 0:
            os.close(temp_descriptor)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        if published and not completed:
            try:
                _remove_published_media(destination)
            except OSError:
                pass


def _remove_published_media(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        path.unlink()
        _fsync_directory(path.parent)


def _audio_metadata(path: Path) -> tuple[int, int, int, str]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise SongProjectStoreError("Could not inspect collected audio.") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise _UnsafeBundlePath("Collected audio must be a regular file.")
    try:
        import soundfile as sf

        info = sf.info(str(path))
    except Exception as exc:
        raise SongProjectStoreError(
            "Media is not a supported, readable audio file."
        ) from exc
    try:
        sample_rate = int(info.samplerate)
        channels = int(info.channels)
        frame_count = int(info.frames)
        media_format = str(info.format).upper()
    except (AttributeError, TypeError, ValueError) as exc:
        raise SongProjectStoreError("Audio metadata is incomplete.") from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise SongProjectStoreError(
            "Collected audio changed while metadata was read."
        ) from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise _UnsafeBundlePath(
            "Collected audio changed while metadata was read."
        )
    return sample_rate, channels, frame_count, media_format


def _destination_suffix(source: Path) -> str:
    suffix = source.suffix
    return suffix.lower() if _SAFE_SUFFIX.fullmatch(suffix) else ".audio"


def import_project_media(
    bundle_path: str | Path,
    project: SongProject,
    source_path: str | Path,
    *,
    designate_backing: bool = False,
    provenance: MediaProvenance = MediaProvenance.LOCAL_FILE,
    import_method: MediaImportMethod = MediaImportMethod.COPY,
    provenance_detail: str = "",
    media_id: str | None = None,
) -> MediaImportResult:
    """Collect one external audio file without changing or retaining its path."""

    if not isinstance(project, SongProject):
        raise SongProjectStoreError("project must be a SongProject value.")
    folder = _bundle_root(bundle_path)
    loaded = load_project_bundle(folder)
    if loaded.project.project_id != project.project_id:
        raise SongProjectStoreError("Project object belongs to another bundle.")
    identifier = media_id or str(uuid.uuid4())
    try:
        canonical_id = str(uuid.UUID(identifier))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SongProjectStoreError("media_id must be a UUID.") from exc
    source = Path(source_path).expanduser()
    relative = f"Media/{canonical_id}{_destination_suffix(source)}"
    destination = folder / relative
    with project_store_lock(folder):
        media_dir = _ensure_media_directory(folder)
        if destination.parent != media_dir:
            raise SongProjectStoreError("Media destination escaped the bundle.")
        copied = _publish_descriptor_copy(source, destination)
        try:
            sample_rate, channels, frames, media_format = _audio_metadata(destination)
            media = SongMedia(
                media_id=canonical_id,
                path=relative,
                sha256=copied.sha256,
                size_bytes=copied.size_bytes,
                sample_rate=sample_rate,
                channels=channels,
                frame_count=frames,
                format=media_format,
                original_basename=source.name,
                provenance=provenance,
                import_method=import_method,
                provenance_detail=provenance_detail,
                original_read_only=True,
            )
            updated = project.add_media(
                media,
                designate_backing=designate_backing,
            )
        except (SongProjectError, SongProjectStoreError):
            _remove_published_media(destination)
            raise
    return MediaImportResult(project=updated, media=media, path=destination)


def relink_project_media(
    bundle_path: str | Path,
    project: SongProject,
    media_id: str,
    source_path: str | Path,
) -> MediaRelinkResult:
    """Restore one missing bundle member only after exact identity validation."""

    if not isinstance(project, SongProject):
        raise SongProjectStoreError("project must be a SongProject value.")
    try:
        media = project.media_by_id(media_id)
    except SongProjectError as exc:
        raise SongProjectStoreError(str(exc)) from exc
    folder = _bundle_root(bundle_path)
    loaded = load_project_bundle(folder)
    if loaded.project.project_id != project.project_id:
        raise SongProjectStoreError("Project object belongs to another bundle.")
    destination = resolve_project_media(folder, media, require_exists=False)
    if _exists(destination):
        raise SongProjectStoreError(
            "Relink is allowed only when the project media copy is missing."
        )
    with project_store_lock(folder):
        _ensure_media_directory(folder)
        copied = _publish_descriptor_copy(
            source_path,
            destination,
            expected_sha256=media.sha256,
            expected_size=media.size_bytes,
        )
        try:
            sample_rate, channels, frames, media_format = _audio_metadata(destination)
            if (
                sample_rate != media.sample_rate
                or channels != media.channels
                or frames != media.frame_count
                or media_format != media.format
            ):
                raise SongProjectStoreError(
                    "Relink audio metadata does not match the project descriptor."
                )
        except SongProjectStoreError:
            _remove_published_media(destination)
            raise
    return MediaRelinkResult(project=project, media=media, path=copied.path)


def verify_project_media(
    bundle_path: str | Path,
    project: SongProject,
) -> tuple[Path, ...]:
    """Verify every collected member by descriptor, size, digest, and metadata."""

    verified: list[Path] = []
    for media in project.media:
        path = resolve_project_media(bundle_path, media)
        descriptor, info = _open_regular_readonly(path, "project media")
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                while True:
                    chunk = handle.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                final = os.fstat(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            size != media.size_bytes
            or info.st_size != media.size_bytes
            or final.st_size != media.size_bytes
            or digest.hexdigest() != media.sha256
        ):
            raise SongProjectStoreError(
                f"Project media verification failed: {media.original_basename}"
            )
        sample_rate, channels, frames, media_format = _audio_metadata(path)
        if (
            sample_rate != media.sample_rate
            or channels != media.channels
            or frames != media.frame_count
            or media_format != media.format
        ):
            raise SongProjectStoreError(
                f"Project media metadata changed: {media.original_basename}"
            )
        verified.append(path)
    return tuple(verified)


def _copy_declared_media(
    source_folder: Path,
    destination_folder: Path,
    media: SongMedia,
) -> None:
    source = resolve_project_media(source_folder, media)
    destination = destination_folder / media.path
    _ensure_media_directory(destination_folder)
    copied = _publish_descriptor_copy(
        source,
        destination,
        expected_sha256=media.sha256,
        expected_size=media.size_bytes,
    )
    sample_rate, channels, frames, media_format = _audio_metadata(copied.path)
    if (
        sample_rate != media.sample_rate
        or channels != media.channels
        or frames != media.frame_count
        or media_format != media.format
    ):
        _remove_published_media(copied.path)
        raise SongProjectStoreError(
            f"Project media metadata changed: {media.original_basename}"
        )


def save_project_as(
    source_bundle_path: str | Path,
    destination_bundle_path: str | Path,
    project: SongProject | None = None,
    *,
    expected_token: str | None = None,
    new_project_id: str | None = None,
) -> ProjectSaveResult:
    """Clone a portable project under a new independent project identity.

    Track and media IDs are preserved because they identify the same musical
    objects.  ``project_id`` changes because future saves, recents, autosaves,
    and conflict handling must treat Save As as an independent document.
    """

    source_folder = _bundle_root(source_bundle_path)
    loaded = load_project_bundle(source_folder)
    if expected_token is not None and loaded.token != _validate_token(
        expected_token, "expected_token"
    ):
        raise SongProjectConflict("Source project changed before Save As.")
    source_project = project or loaded.project
    if (
        not isinstance(source_project, SongProject)
        or source_project.project_id != loaded.project.project_id
    ):
        raise SongProjectStoreError("Save As project does not match its source bundle.")
    verify_project_media(source_folder, source_project)
    identifier = new_project_id or str(uuid.uuid4())
    try:
        canonical_id = str(uuid.UUID(identifier))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SongProjectStoreError("new_project_id must be a UUID.") from exc
    cloned = replace(source_project, project_id=canonical_id)

    destination = Path(destination_bundle_path).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if _exists(destination):
        raise SongProjectStoreError("Save As destination already exists.")
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve(strict=True)
    except OSError as exc:
        raise SongProjectStoreError("Could not create Save As parent folder.") from exc
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".saving", dir=parent)
    )
    published = False
    try:
        _ensure_media_directory(stage)
        for media in cloned.media:
            _copy_declared_media(source_folder, stage, media)
        payload = _serialize_project(cloned)
        atomic_write_bytes(stage / PROJECT_MANIFEST_FILENAME, payload, mode=0o600)
        _fsync_directory(stage)
        try:
            os.rename(stage, destination)
        except OSError as exc:
            raise SongProjectStoreError(
                "Could not publish the Save As project bundle."
            ) from exc
        published = True
        _fsync_directory(parent)
        final = _bundle_root(destination)
        return ProjectSaveResult(
            project=cloned,
            bundle_path=final,
            manifest_path=final / PROJECT_MANIFEST_FILENAME,
            token=_token(payload),
        )
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _sanitize_recent_path(value: object) -> Path:
    if isinstance(value, Path):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise SongProjectStoreError("Recent project path must be text.")
    if (
        not text
        or "\x00" in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
        or len(text.encode("utf-8")) > MAX_RECENT_PATH_BYTES
    ):
        raise SongProjectStoreError("Recent project path is not safe.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise SongProjectStoreError("Recent project path must be absolute.")
    return path.resolve(strict=False)


def _decode_recent_projects(data: bytes) -> tuple[Path, ...]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SongProjectStoreError(
            "Recent project index is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "projects"}:
        raise SongProjectStoreError("Recent project index has an unsupported shape.")
    if value["schema_version"] != RECENT_PROJECTS_SCHEMA_VERSION:
        raise SongProjectStoreError("Recent project index has an unsupported schema.")
    raw_paths = value["projects"]
    if not isinstance(raw_paths, list) or len(raw_paths) > MAX_RECENT_PROJECTS:
        raise SongProjectStoreError("Recent project index exceeds its entry limit.")
    paths = tuple(_sanitize_recent_path(item) for item in raw_paths)
    if len(paths) != len(set(paths)):
        raise SongProjectStoreError("Recent project index contains duplicate paths.")
    return paths


def load_recent_projects(index_path: str | Path) -> RecentProjects:
    path = Path(index_path).expanduser()
    if not _exists(path):
        return RecentProjects(paths=())
    data = _read_regular_bounded(
        path,
        "recent project index",
        MAX_RECENT_PROJECTS_BYTES,
    )
    return RecentProjects(paths=_decode_recent_projects(data))


def write_recent_projects(
    index_path: str | Path,
    paths: Sequence[str | Path],
) -> RecentProjects:
    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
        raise SongProjectStoreError("Recent projects must be a sequence of paths.")
    if len(paths) > MAX_RECENT_PROJECTS:
        raise SongProjectStoreError("Recent project index exceeds its entry limit.")
    normalized = tuple(_sanitize_recent_path(item) for item in paths)
    if len(normalized) != len(set(normalized)):
        raise SongProjectStoreError("Recent project index contains duplicate paths.")
    value = {
        "schema_version": RECENT_PROJECTS_SCHEMA_VERSION,
        "projects": [str(path) for path in normalized],
    }
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_RECENT_PROJECTS_BYTES:
        raise SongProjectStoreError("Recent project index is too large.")
    target = Path(index_path).expanduser()
    _require_safe_write_target(target, "recent project index")
    try:
        atomic_write_bytes(target, payload, mode=0o600)
    except OSError as exc:
        raise SongProjectStoreError(
            "Could not atomically save recent projects."
        ) from exc
    return RecentProjects(paths=normalized)


def record_recent_project(
    index_path: str | Path,
    bundle_path: str | Path,
) -> RecentProjects:
    current = load_recent_projects(index_path).paths
    bundle = _sanitize_recent_path(_bundle_root(bundle_path))
    updated = (bundle, *(path for path in current if path != bundle))
    return write_recent_projects(index_path, updated[:MAX_RECENT_PROJECTS])


# Short aliases keep UI/controller call sites readable while the explicit
# ``*_bundle`` names remain discoverable to persistence code.
load_song_project = load_project_bundle
save_song_project = save_project_bundle
create_song_project = create_project_bundle
import_media = import_project_media
relink_media = relink_project_media


__all__ = [
    "MAX_AUTOSAVE_BYTES",
    "MAX_PROJECT_MANIFEST_BYTES",
    "MAX_RECENT_PROJECTS",
    "MAX_RECENT_PROJECTS_BYTES",
    "MediaImportResult",
    "MediaRelinkResult",
    "PROJECT_AUTOSAVE_FILENAME",
    "PROJECT_BACKUP_FILENAME",
    "PROJECT_LOCK_FILENAME",
    "PROJECT_MANIFEST_FILENAME",
    "ProjectLoadOrigin",
    "ProjectLoadResult",
    "ProjectRecoveryCandidate",
    "ProjectSaveResult",
    "RecentProjects",
    "SongProjectConflict",
    "SongProjectStoreError",
    "create_project_bundle",
    "create_song_project",
    "discard_project_autosave",
    "import_media",
    "import_project_media",
    "load_project_bundle",
    "load_recent_projects",
    "load_song_project",
    "project_autosave_path",
    "project_backup_path",
    "project_manifest_path",
    "project_store_lock",
    "record_recent_project",
    "recover_project_autosave",
    "relink_media",
    "relink_project_media",
    "resolve_project_media",
    "save_project_as",
    "save_project_bundle",
    "save_song_project",
    "verify_project_media",
    "write_project_autosave",
    "write_recent_projects",
]

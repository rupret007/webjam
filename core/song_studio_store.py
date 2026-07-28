"""Crash-safe schema-3 Studio persistence inside a ``.webjam`` bundle.

This module is intentionally separate from :mod:`core.studio_store`: recorded
take sidecars remain schema 2, while standalone song projects store one
schema-3 :class:`~core.studio_project.StudioDocument` under fixed bundle-local
names.  Studio snapshots contain media IDs and frame mappings only; the song
manifest remains the sole media inventory.

Primary saves are exact-byte compare-and-swap operations.  A valid previous
primary becomes the last-known-good backup, an invalid regular primary is
preserved under a content-derived name, and all publication uses atomic,
fsynced replacement.  Autosave is a bounded envelope which is reported as an
explicit recovery candidate and is never promoted during load.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Mapping

from core.file_io import atomic_write_bytes
from core.song_project import SongProject
from core.song_project_store import (
    SongProjectStoreError,
    load_project_bundle,
    project_store_lock,
)
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
    default_song_studio_document,
    studio_document_from_dict,
)


SONG_STUDIO_FILENAME = ".webjam-song-studio.json"
SONG_STUDIO_BACKUP_FILENAME = ".webjam-song-studio.json.bak"
SONG_STUDIO_AUTOSAVE_FILENAME = ".webjam-song-studio.autosave.json"
SONG_STUDIO_AUTOSAVE_SCHEMA_VERSION = 1
MAX_SONG_STUDIO_BYTES = 8 * 1024 * 1024
MAX_SONG_STUDIO_AUTOSAVE_BYTES = 8 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SongStudioStoreError(ValueError):
    """Raised when song Studio state cannot be safely trusted or persisted."""


class SongStudioConflict(SongStudioStoreError):
    """Raised when primary or recovery bytes changed after they were read."""


class _UnsafeSongStudioPath(SongStudioStoreError):
    """A redirect or non-regular bundle member which must never be followed."""


class _OversizedSongStudioState(SongStudioStoreError):
    """A stable oversized primary which can be preserved without reading it."""

    def __init__(self, token: str, message: str) -> None:
        super().__init__(message)
        self.token = token


class SongStudioLoadOrigin(str, Enum):
    DEFAULT = "default"
    PRIMARY = "primary"
    BACKUP = "backup"


@dataclass(frozen=True)
class SongStudioRecoveryCandidate:
    document: StudioDocument
    autosave_token: str
    base_primary_token: str | None


@dataclass(frozen=True)
class SongStudioLoadResult:
    document: StudioDocument
    bundle_path: Path
    token: str | None
    origin: SongStudioLoadOrigin
    needs_save: bool = False
    recovery_candidate: SongStudioRecoveryCandidate | None = None
    recovery_notice: str = ""
    autosave_requires_discard: bool = False


@dataclass(frozen=True)
class SongStudioSaveResult:
    document: StudioDocument
    path: Path
    token: str
    backup_path: Path | None = None
    preserved_corrupt_path: Path | None = None
    autosave_cleared: bool = True


@dataclass(frozen=True)
class SongStudioAutosaveResult:
    document: StudioDocument
    token: str
    base_primary_token: str | None


def song_studio_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / SONG_STUDIO_FILENAME


def song_studio_backup_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / SONG_STUDIO_BACKUP_FILENAME


def song_studio_autosave_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path).expanduser() / SONG_STUDIO_AUTOSAVE_FILENAME


def _token(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_token(
    value: str | None,
    label: str,
    *,
    allow_none: bool = True,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        suffix = " or null" if allow_none else ""
        raise SongStudioStoreError(f"{label} must be a lowercase SHA-256{suffix}.")
    return value


def _exists(path: Path, label: str) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SongStudioStoreError(f"Could not inspect {label}.") from exc


def _verified_bundle(
    bundle_path: str | Path,
    project: SongProject,
) -> Path:
    if not isinstance(project, SongProject):
        raise SongStudioStoreError("Song Studio requires a SongProject value.")
    try:
        loaded = load_project_bundle(bundle_path)
    except SongProjectStoreError as exc:
        raise SongStudioStoreError("Could not verify the song project bundle.") from exc
    if (
        loaded.project.project_id != project.project_id
        or loaded.project.project_sample_rate != project.project_sample_rate
    ):
        raise SongStudioStoreError(
            "Song Studio project identity does not match this bundle."
        )
    return loaded.bundle_path


def _oversized_token(info: os.stat_result) -> str:
    identity = (
        f"song-studio-oversized-v1\0{info.st_dev}\0{info.st_ino}\0"
        f"{info.st_size}\0{info.st_mtime_ns}"
    ).encode("ascii")
    return _token(identity)


def _open_regular_readonly(
    path: Path,
    label: str,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if not nofollow:
        try:
            before = path.lstat()
        except OSError as exc:
            raise SongStudioStoreError(f"Could not inspect {label}.") from exc
        if stat.S_ISLNK(before.st_mode):
            raise _UnsafeSongStudioPath(
                f"{label.capitalize()} must not be a symbolic link."
            )
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        try:
            current = path.lstat()
        except OSError:
            current = None
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise _UnsafeSongStudioPath(
                f"{label.capitalize()} must not be a symbolic link."
            ) from exc
        raise SongStudioStoreError(f"Could not open {label}.") from exc
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
            raise _UnsafeSongStudioPath(
                f"{label.capitalize()} must be one stable regular file."
            )
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_bounded(
    path: Path,
    label: str,
    maximum_bytes: int,
    *,
    oversized_token: bool = False,
) -> bytes:
    descriptor, info = _open_regular_readonly(path, label)
    try:
        if info.st_size > maximum_bytes:
            if oversized_token:
                raise _OversizedSongStudioState(
                    _oversized_token(info),
                    f"{label.capitalize()} is too large.",
                )
            raise SongStudioStoreError(f"{label.capitalize()} is too large.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(maximum_bytes + 1)
            final = os.fstat(handle.fileno())
        if len(data) > maximum_bytes or final.st_size > maximum_bytes:
            if oversized_token:
                raise _OversizedSongStudioState(
                    _oversized_token(final),
                    f"{label.capitalize()} is too large.",
                )
            raise SongStudioStoreError(f"{label.capitalize()} is too large.")
        if (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino):
            raise _UnsafeSongStudioPath(
                f"{label.capitalize()} changed while it was read."
            )
        return data
    except OSError as exc:
        raise SongStudioStoreError(f"Could not read {label}.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_document(project: SongProject, document: StudioDocument) -> None:
    if not isinstance(document, StudioDocument):
        raise SongStudioStoreError("Song Studio state must be a StudioDocument value.")
    if document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION:
        raise SongStudioStoreError(
            "Song Studio accepts only schema-3 Studio documents."
        )
    if (
        document.project_id != project.project_id
        or document.project_sample_rate != project.project_sample_rate
    ):
        raise SongStudioStoreError(
            "Song Studio document identity does not match the project."
        )


def _decode_document(project: SongProject, data: bytes) -> StudioDocument:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SongStudioStoreError(
            "Song Studio state is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(value, Mapping):
        raise SongStudioStoreError("Song Studio state root must be an object.")
    if value.get("schema_version") != STUDIO_SONG_PROJECT_SCHEMA_VERSION:
        raise SongStudioStoreError(
            "Song Studio state must use schema 3; migration is not implicit."
        )
    try:
        document = studio_document_from_dict(value)
    except StudioProjectError as exc:
        raise SongStudioStoreError("Song Studio arrangement is not valid.") from exc
    _validate_document(project, document)
    return document


def _serialize_document(
    project: SongProject,
    document: StudioDocument,
) -> bytes:
    _validate_document(project, document)
    try:
        payload = (
            json.dumps(
                document.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SongStudioStoreError(
            "Song Studio arrangement could not be serialized."
        ) from exc
    if len(payload) > MAX_SONG_STUDIO_BYTES:
        raise SongStudioStoreError(
            "Song Studio arrangement is too large to save safely."
        )
    return payload


def _require_safe_write_target(path: Path, label: str) -> None:
    if not _exists(path, label):
        return
    try:
        info = path.lstat()
    except OSError as exc:
        raise SongStudioStoreError(f"Could not inspect {label}.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise _UnsafeSongStudioPath(
            f"{label.capitalize()} must not be a symbolic link."
        )
    if not stat.S_ISREG(info.st_mode):
        raise _UnsafeSongStudioPath(f"{label.capitalize()} must be a regular file.")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise SongStudioStoreError(
            "Could not durably update Song Studio metadata."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_unlink(path: Path, label: str) -> bool:
    if not _exists(path, label):
        return True
    _require_safe_write_target(path, label)
    try:
        path.unlink()
        _fsync_directory(path.parent)
        return True
    except (OSError, SongStudioStoreError):
        return False


def _current_primary(
    folder: Path,
) -> tuple[bytes | None, str | None, _OversizedSongStudioState | None]:
    primary = folder / SONG_STUDIO_FILENAME
    if not _exists(primary, "song Studio state"):
        return None, None, None
    try:
        data = _read_regular_bounded(
            primary,
            "song Studio state",
            MAX_SONG_STUDIO_BYTES,
            oversized_token=True,
        )
    except _OversizedSongStudioState as exc:
        return None, exc.token, exc
    return data, _token(data), None


def _decode_autosave(
    project: SongProject,
    data: bytes,
) -> tuple[StudioDocument, str | None]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SongStudioStoreError(
            "Song Studio autosave is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(value, Mapping):
        raise SongStudioStoreError("Song Studio autosave root must be an object.")
    expected = {
        "schema_version",
        "project_id",
        "project_sample_rate",
        "base_primary_token",
        "document",
    }
    if set(value) != expected:
        raise SongStudioStoreError("Song Studio autosave has an unsupported shape.")
    if value["schema_version"] != SONG_STUDIO_AUTOSAVE_SCHEMA_VERSION:
        raise SongStudioStoreError("Song Studio autosave has an unsupported schema.")
    if (
        value["project_id"] != project.project_id
        or value["project_sample_rate"] != project.project_sample_rate
    ):
        raise SongStudioStoreError(
            "Song Studio autosave belongs to a different project."
        )
    base_token = _validate_token(
        value["base_primary_token"],
        "Song Studio autosave base token",
    )
    raw_document = value["document"]
    if not isinstance(raw_document, Mapping):
        raise SongStudioStoreError("Song Studio autosave document must be an object.")
    try:
        document = studio_document_from_dict(raw_document)
    except StudioProjectError as exc:
        raise SongStudioStoreError(
            "Song Studio autosave arrangement is not valid."
        ) from exc
    _validate_document(project, document)
    return document, base_token


def _autosave_bytes(
    project: SongProject,
    document: StudioDocument,
    base_primary_token: str | None,
) -> bytes:
    _validate_document(project, document)
    base = _validate_token(
        base_primary_token,
        "Song Studio autosave base token",
    )
    value = {
        "schema_version": SONG_STUDIO_AUTOSAVE_SCHEMA_VERSION,
        "project_id": project.project_id,
        "project_sample_rate": project.project_sample_rate,
        "base_primary_token": base,
        "document": document.to_dict(),
    }
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
    except (TypeError, ValueError, OverflowError) as exc:
        raise SongStudioStoreError(
            "Song Studio autosave could not be serialized."
        ) from exc
    if len(payload) > MAX_SONG_STUDIO_AUTOSAVE_BYTES:
        raise SongStudioStoreError("Song Studio autosave is too large.")
    return payload


def _recovery_candidate(
    folder: Path,
    project: SongProject,
    document: StudioDocument,
    primary_token: str | None,
) -> tuple[SongStudioRecoveryCandidate | None, str, bool]:
    autosave = folder / SONG_STUDIO_AUTOSAVE_FILENAME
    if not _exists(autosave, "song Studio autosave"):
        return None, "", False
    try:
        data = _read_regular_bounded(
            autosave,
            "song Studio autosave",
            MAX_SONG_STUDIO_AUTOSAVE_BYTES,
        )
        recovered, base_token = _decode_autosave(project, data)
    except _UnsafeSongStudioPath:
        raise
    except SongStudioStoreError:
        return (
            None,
            "A damaged Studio autosave was preserved and was not recovered.",
            True,
        )
    if base_token != primary_token:
        return (
            None,
            "A stale Studio autosave was preserved and was not recovered.",
            True,
        )
    if recovered == document:
        return (
            None,
            "A redundant Studio autosave is available to discard.",
            True,
        )
    return (
        SongStudioRecoveryCandidate(
            document=recovered,
            autosave_token=_token(data),
            base_primary_token=base_token,
        ),
        "A newer Studio autosave is available for explicit recovery.",
        False,
    )


def _load_locked(
    folder: Path,
    project: SongProject,
) -> SongStudioLoadResult:
    primary = folder / SONG_STUDIO_FILENAME
    backup = folder / SONG_STUDIO_BACKUP_FILENAME
    primary_data: bytes | None = None
    primary_token: str | None = None
    primary_error: SongStudioStoreError | None = None

    if _exists(primary, "song Studio state"):
        try:
            primary_data = _read_regular_bounded(
                primary,
                "song Studio state",
                MAX_SONG_STUDIO_BYTES,
                oversized_token=True,
            )
            primary_token = _token(primary_data)
            document = _decode_document(project, primary_data)
        except _UnsafeSongStudioPath:
            raise
        except _OversizedSongStudioState as exc:
            primary_error = exc
            primary_token = exc.token
        except SongStudioStoreError as exc:
            primary_error = exc
        else:
            candidate, notice, requires_discard = _recovery_candidate(
                folder,
                project,
                document,
                primary_token,
            )
            return SongStudioLoadResult(
                document=replace(document, _store_token=primary_token),
                bundle_path=folder,
                token=primary_token,
                origin=SongStudioLoadOrigin.PRIMARY,
                recovery_candidate=candidate,
                recovery_notice=notice,
                autosave_requires_discard=requires_discard,
            )

    if _exists(backup, "song Studio backup"):
        try:
            backup_data = _read_regular_bounded(
                backup,
                "song Studio backup",
                MAX_SONG_STUDIO_BYTES,
            )
            document = _decode_document(project, backup_data)
        except _UnsafeSongStudioPath:
            raise
        except SongStudioStoreError as backup_error:
            if primary_error is not None:
                raise SongStudioStoreError(
                    "Song Studio state and its valid backup are unavailable."
                ) from primary_error
            raise SongStudioStoreError(
                "Song Studio backup is not valid."
            ) from backup_error
        candidate, autosave_notice, requires_discard = _recovery_candidate(
            folder,
            project,
            document,
            primary_token,
        )
        notices = [
            "Recovered the last-known-good Studio backup; the primary was preserved."
            if primary_error is not None
            else "Recovered the last-known-good Studio backup.",
            autosave_notice,
        ]
        return SongStudioLoadResult(
            document=replace(document, _store_token=primary_token),
            bundle_path=folder,
            token=primary_token,
            origin=SongStudioLoadOrigin.BACKUP,
            needs_save=True,
            recovery_candidate=candidate,
            recovery_notice=" ".join(item for item in notices if item),
            autosave_requires_discard=requires_discard,
        )

    if primary_error is not None:
        raise primary_error
    try:
        document = default_song_studio_document(project)
    except StudioProjectError as exc:
        raise SongStudioStoreError(
            "Could not create default Song Studio state."
        ) from exc
    candidate, notice, requires_discard = _recovery_candidate(
        folder,
        project,
        document,
        None,
    )
    return SongStudioLoadResult(
        document=document,
        bundle_path=folder,
        token=None,
        origin=SongStudioLoadOrigin.DEFAULT,
        recovery_candidate=candidate,
        recovery_notice=notice,
        autosave_requires_discard=requires_discard,
    )


def load_song_studio_document(
    bundle_path: str | Path,
    project: SongProject,
) -> SongStudioLoadResult:
    """Load primary/backup/default state and report autosave explicitly."""

    folder = _verified_bundle(bundle_path, project)
    try:
        with project_store_lock(folder):
            folder = _verified_bundle(folder, project)
            return _load_locked(folder, project)
    except SongProjectStoreError as exc:
        raise SongStudioStoreError("Could not lock the song project bundle.") from exc


def _preserve_corrupt_primary(folder: Path, data: bytes) -> Path:
    digest = _token(data)
    target = folder / f".webjam-song-studio.corrupt-{digest}.json"
    if _exists(target, "corrupt Studio recovery copy"):
        existing = _read_regular_bounded(
            target,
            "corrupt Studio recovery copy",
            MAX_SONG_STUDIO_BYTES,
        )
        if existing != data:
            raise SongStudioStoreError(
                "Studio corrupt recovery destination is occupied."
            )
        return target
    _require_safe_write_target(target, "corrupt Studio recovery copy")
    try:
        atomic_write_bytes(target, data, mode=0o600)
    except OSError as exc:
        raise SongStudioStoreError("Could not preserve damaged Studio state.") from exc
    return target


def _quarantine_oversized_primary(
    folder: Path,
    primary: Path,
    token: str,
) -> Path:
    target = folder / f".webjam-song-studio.corrupt-oversized-{token}.json"
    if _exists(target, "oversized Studio recovery copy"):
        raise SongStudioStoreError("Studio oversized recovery destination is occupied.")
    linked = False
    try:
        os.link(primary, target, follow_symlinks=False)
        linked = True
        source_info = primary.lstat()
        target_info = target.lstat()
        if (
            not stat.S_ISREG(source_info.st_mode)
            or not stat.S_ISREG(target_info.st_mode)
            or (source_info.st_dev, source_info.st_ino)
            != (target_info.st_dev, target_info.st_ino)
            or _oversized_token(target_info) != token
        ):
            raise SongStudioStoreError(
                "Oversized Studio state changed before preservation."
            )
        primary.unlink()
        _fsync_directory(folder)
        return target
    except OSError as exc:
        raise SongStudioStoreError(
            "Could not preserve oversized Studio state."
        ) from exc
    finally:
        if linked and primary.exists() and target.exists():
            try:
                target.unlink()
            except OSError:
                pass


def _autosave_token(folder: Path) -> str | None:
    autosave = folder / SONG_STUDIO_AUTOSAVE_FILENAME
    if not _exists(autosave, "song Studio autosave"):
        return None
    data = _read_regular_bounded(
        autosave,
        "song Studio autosave",
        MAX_SONG_STUDIO_AUTOSAVE_BYTES,
    )
    return _token(data)


def save_song_studio_document(
    bundle_path: str | Path,
    project: SongProject,
    document: StudioDocument,
    *,
    expected_token: str | None,
    expected_recovery_token: str | None = None,
) -> SongStudioSaveResult:
    """CAS-save one schema-3 document and preserve all displaced bytes."""

    expected = _validate_token(expected_token, "Song Studio expected token")
    recovery_token = (
        _validate_token(
            expected_recovery_token,
            "Song Studio recovery token",
            allow_none=False,
        )
        if expected_recovery_token is not None
        else None
    )
    payload = _serialize_document(project, document)
    folder = _verified_bundle(bundle_path, project)
    try:
        with project_store_lock(folder):
            folder = _verified_bundle(folder, project)
            primary = folder / SONG_STUDIO_FILENAME
            backup = folder / SONG_STUDIO_BACKUP_FILENAME
            autosave = folder / SONG_STUDIO_AUTOSAVE_FILENAME
            current, current_token, oversized = _current_primary(folder)
            if current_token != expected:
                raise SongStudioConflict(
                    "Song Studio state changed after it was loaded."
                )
            if recovery_token is not None and _autosave_token(folder) != recovery_token:
                raise SongStudioConflict(
                    "Song Studio recovery data changed after it was offered."
                )
            # Reject unsafe cleanup/backup targets before changing any bytes.
            if _exists(autosave, "song Studio autosave"):
                _require_safe_write_target(autosave, "song Studio autosave")
            _require_safe_write_target(backup, "song Studio backup")
            _require_safe_write_target(primary, "song Studio state")

            backup_written: Path | None = None
            preserved: Path | None = None
            if oversized is not None:
                preserved = _quarantine_oversized_primary(
                    folder,
                    primary,
                    oversized.token,
                )
            elif current is not None:
                try:
                    _decode_document(project, current)
                except SongStudioStoreError:
                    preserved = _preserve_corrupt_primary(folder, current)
                else:
                    try:
                        atomic_write_bytes(backup, current, mode=0o600)
                    except OSError as exc:
                        raise SongStudioStoreError(
                            "Could not preserve the valid Studio backup."
                        ) from exc
                    backup_written = backup

            try:
                atomic_write_bytes(primary, payload, mode=0o600)
            except OSError as exc:
                raise SongStudioStoreError(
                    "Could not atomically save Song Studio state."
                ) from exc
            saved_token = _token(payload)
            try:
                cleared = _safe_unlink(autosave, "song Studio autosave")
            except SongStudioStoreError:
                # The primary commit is already durable. Never report it as
                # failed merely because a raced autosave could not be removed.
                cleared = False
            return SongStudioSaveResult(
                document=replace(document, _store_token=saved_token),
                path=primary,
                token=saved_token,
                backup_path=backup_written,
                preserved_corrupt_path=preserved,
                autosave_cleared=cleared,
            )
    except SongProjectStoreError as exc:
        raise SongStudioStoreError("Could not lock the song project bundle.") from exc


def write_song_studio_autosave(
    bundle_path: str | Path,
    project: SongProject,
    document: StudioDocument,
    *,
    base_primary_token: str | None,
) -> SongStudioAutosaveResult:
    """Atomically publish a recovery envelope without changing the primary."""

    base = _validate_token(
        base_primary_token,
        "Song Studio autosave base token",
    )
    payload = _autosave_bytes(project, document, base)
    folder = _verified_bundle(bundle_path, project)
    try:
        with project_store_lock(folder):
            folder = _verified_bundle(folder, project)
            _current, current_token, _oversized = _current_primary(folder)
            if current_token != base:
                raise SongStudioConflict("Song Studio state changed before autosave.")
            autosave = folder / SONG_STUDIO_AUTOSAVE_FILENAME
            _require_safe_write_target(autosave, "song Studio autosave")
            try:
                atomic_write_bytes(autosave, payload, mode=0o600)
            except OSError as exc:
                raise SongStudioStoreError(
                    "Could not atomically write Song Studio autosave."
                ) from exc
            return SongStudioAutosaveResult(
                document=document,
                token=_token(payload),
                base_primary_token=base,
            )
    except SongProjectStoreError as exc:
        raise SongStudioStoreError("Could not lock the song project bundle.") from exc


def discard_song_studio_autosave(
    bundle_path: str | Path,
    project: SongProject,
    *,
    expected_token: str | None = None,
) -> None:
    """Explicitly discard only the fixed regular Studio autosave member."""

    expected = (
        _validate_token(
            expected_token,
            "Song Studio autosave token",
            allow_none=False,
        )
        if expected_token is not None
        else None
    )
    folder = _verified_bundle(bundle_path, project)
    try:
        with project_store_lock(folder):
            folder = _verified_bundle(folder, project)
            if expected is not None:
                current = _autosave_token(folder)
                if current != expected:
                    raise SongStudioConflict(
                        "Song Studio autosave changed before it was discarded."
                    )
            autosave = folder / SONG_STUDIO_AUTOSAVE_FILENAME
            if not _safe_unlink(autosave, "song Studio autosave"):
                raise SongStudioStoreError("Could not discard Song Studio autosave.")
    except SongProjectStoreError as exc:
        raise SongStudioStoreError("Could not lock the song project bundle.") from exc


def recover_song_studio_autosave(
    bundle_path: str | Path,
    project: SongProject,
    *,
    expected_autosave_token: str,
) -> SongStudioSaveResult:
    """Explicitly promote the currently offered recovery candidate."""

    expected = _validate_token(
        expected_autosave_token,
        "Song Studio autosave token",
        allow_none=False,
    )
    loaded = load_song_studio_document(bundle_path, project)
    candidate = loaded.recovery_candidate
    if candidate is None or candidate.autosave_token != expected:
        raise SongStudioConflict(
            "Song Studio recovery data changed or is no longer applicable."
        )
    return save_song_studio_document(
        loaded.bundle_path,
        project,
        candidate.document,
        expected_token=loaded.token,
        expected_recovery_token=expected,
    )


__all__ = [
    "MAX_SONG_STUDIO_AUTOSAVE_BYTES",
    "MAX_SONG_STUDIO_BYTES",
    "SONG_STUDIO_AUTOSAVE_FILENAME",
    "SONG_STUDIO_AUTOSAVE_SCHEMA_VERSION",
    "SONG_STUDIO_BACKUP_FILENAME",
    "SONG_STUDIO_FILENAME",
    "SongStudioAutosaveResult",
    "SongStudioConflict",
    "SongStudioLoadOrigin",
    "SongStudioLoadResult",
    "SongStudioRecoveryCandidate",
    "SongStudioSaveResult",
    "SongStudioStoreError",
    "discard_song_studio_autosave",
    "load_song_studio_document",
    "recover_song_studio_autosave",
    "save_song_studio_document",
    "song_studio_autosave_path",
    "song_studio_backup_path",
    "song_studio_path",
    "write_song_studio_autosave",
]

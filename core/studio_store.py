"""Crash-safe persistence for WebJam Studio arrangement documents.

The recording manifest and source media are evidence and remain immutable.
Studio writes only ``.webjam-studio-state.json`` plus private recovery files
beside it.  Loads are bounded and identity-checked; saves use an exact-byte
compare-and-swap token, a process-local lock, a cross-process file lock,
last-known-good backup, and atomic fsynced replacement.

Schema-1 mixer sidecars are migrated in memory only.  Their exact bytes are
preserved on the first explicit schema-2 save.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping

from core.file_io import atomic_write_bytes
from core.studio_project import (
    StudioDocument,
    StudioProjectError,
    default_studio_document,
    reconcile_studio_document,
    studio_document_from_dict,
)
from core.take_project import PROJECT_SCHEMA_VERSION, TakeProject, TakeProjectError


STUDIO_STATE_FILENAME = ".webjam-studio-state.json"
STUDIO_STATE_BACKUP_FILENAME = ".webjam-studio-state.json.bak"
STUDIO_STATE_V1_BACKUP_FILENAME = ".webjam-studio-state.v1.json.bak"
STUDIO_STATE_LOCK_FILENAME = ".webjam-studio-state.lock"
STUDIO_STATE_SCHEMA_VERSION = 2
LEGACY_STUDIO_STATE_SCHEMA_VERSION = 1
MAX_STUDIO_STATE_BYTES = 8 * 1024 * 1024
MAX_TAKE_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_LEGACY_TRACKS = 512

_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[Path, threading.RLock] = {}


class StudioStoreError(ValueError):
    """Raised when Studio metadata cannot be trusted, loaded, or saved."""


class StudioStoreConflict(StudioStoreError):
    """Raised when another writer replaced Studio state after it was loaded."""


class _OversizedStudioState(StudioStoreError):
    """Internal signal retaining a stable token for a regular oversized file."""

    def __init__(self, token: str) -> None:
        super().__init__("Studio state sidecar is too large.")
        self.token = token


class _UnsafeStudioPath(StudioStoreError):
    """Internal signal for paths that must never fall back to another file."""


class StudioLoadOrigin(str, Enum):
    DEFAULT = "default"
    PRIMARY_V2 = "primary_v2"
    MIGRATED_V1 = "migrated_v1"
    BACKUP = "backup"


@dataclass(frozen=True)
class StudioLoadResult:
    document: StudioDocument
    token: str | None
    origin: StudioLoadOrigin
    needs_save: bool = False
    recovery_notice: str = ""


@dataclass(frozen=True)
class StudioSaveResult:
    document: StudioDocument
    path: Path
    token: str
    backup_path: Path | None = None


def studio_state_path(take_dir: str | Path) -> Path:
    return Path(take_dir).expanduser() / STUDIO_STATE_FILENAME


def studio_state_backup_path(take_dir: str | Path) -> Path:
    return Path(take_dir).expanduser() / STUDIO_STATE_BACKUP_FILENAME


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioStoreError(f"{field_name} must be a UUID.") from exc


def _token(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _oversized_token(info: os.stat_result) -> str:
    identity = (
        f"studio-oversized-v1\0{info.st_dev}\0{info.st_ino}\0{info.st_size}\0"
        f"{info.st_mtime_ns}"
    ).encode("ascii")
    return _token(identity)


def _open_regular_readonly(path: Path, label: str) -> tuple[int, os.stat_result]:
    """Open one regular file without following a final-component redirect."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if not nofollow:
        try:
            before = path.lstat()
        except OSError as exc:
            raise StudioStoreError(f"Could not inspect Studio {label}.") from exc
        if stat.S_ISLNK(before.st_mode):
            raise _UnsafeStudioPath(f"Studio {label} must not be a symbolic link.")
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        if nofollow:
            try:
                if stat.S_ISLNK(path.lstat().st_mode):
                    raise _UnsafeStudioPath(
                        f"Studio {label} must not be a symbolic link."
                    ) from exc
            except FileNotFoundError:
                pass
        raise StudioStoreError(f"Could not open Studio {label}.") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeStudioPath(f"Studio {label} is not a regular file.")
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or (
            current.st_dev,
            current.st_ino,
        ) != (info.st_dev, info.st_ino):
            raise _UnsafeStudioPath(
                f"Studio {label} changed while it was being opened."
            )
        if before is not None and (before.st_dev, before.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise _UnsafeStudioPath(
                f"Studio {label} changed while it was being opened."
            )
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_bounded(
    path: Path,
    label: str,
    *,
    maximum_bytes: int,
    oversized_token: bool = False,
) -> bytes:
    descriptor, info = _open_regular_readonly(path, label)
    try:
        if info.st_size > maximum_bytes:
            if oversized_token:
                raise _OversizedStudioState(_oversized_token(info))
            raise StudioStoreError(f"Studio {label} is too large.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(maximum_bytes + 1)
            final_info = os.fstat(handle.fileno())
        if len(data) > maximum_bytes or final_info.st_size > maximum_bytes:
            if oversized_token:
                raise _OversizedStudioState(_oversized_token(final_info))
            raise StudioStoreError(f"Studio {label} is too large.")
        return data
    except OSError as exc:
        raise StudioStoreError(f"Could not read Studio {label}.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_project(take_dir: str | Path) -> tuple[Path, TakeProject]:
    folder = Path(take_dir).expanduser().resolve()
    manifest = folder / "webjam-take.json"
    try:
        manifest_bytes = _read_regular_bounded(
            manifest,
            "take manifest",
            maximum_bytes=MAX_TAKE_MANIFEST_BYTES,
        )
        raw = json.loads(manifest_bytes.decode("utf-8"))
    except (StudioStoreError, UnicodeDecodeError, ValueError) as exc:
        raise StudioStoreError(
            "Could not read this take's schema-v2 manifest."
        ) from exc
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != PROJECT_SCHEMA_VERSION
    ):
        raise StudioStoreError("Studio state is available only for schema-v2 takes.")
    try:
        return folder, TakeProject.from_dict(raw)
    except TakeProjectError as exc:
        raise StudioStoreError("This take's schema-v2 manifest is not valid.") from exc


def _read_bounded(path: Path, label: str) -> bytes:
    return _read_regular_bounded(
        path,
        label,
        maximum_bytes=MAX_STUDIO_STATE_BYTES,
        oversized_token=True,
    )


def _require_exact_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise StudioStoreError(
            f"{context} contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
            + "."
        )


def _migrate_schema_v1(
    project: TakeProject, value: Mapping[str, Any]
) -> StudioDocument:
    _require_exact_keys(
        value,
        {"schema_version", "session_id", "take_id", "tracks"},
        "Studio schema-1 sidecar",
    )
    if _canonical_uuid(value.get("session_id"), "session_id") != project.session_id:
        raise StudioStoreError("Studio state belongs to a different take.")
    if _canonical_uuid(value.get("take_id"), "take_id") != project.take_id:
        raise StudioStoreError("Studio state belongs to a different take.")
    raw_tracks = value.get("tracks")
    if not isinstance(raw_tracks, list):
        raise StudioStoreError("Studio schema-1 tracks must be a list.")
    if len(raw_tracks) > MAX_LEGACY_TRACKS:
        raise StudioStoreError("Studio schema-1 sidecar has too many tracks.")

    document = default_studio_document(project)
    current_ids = {track.track_id for track in document.tracks}
    seen: set[str] = set()
    for raw in raw_tracks:
        if not isinstance(raw, Mapping):
            raise StudioStoreError("Studio schema-1 sidecar contains an invalid track.")
        _require_exact_keys(
            raw,
            {"track_id", "gain", "pan", "muted", "solo", "export_included"},
            "Studio schema-1 track",
        )
        track_id = _canonical_uuid(raw.get("track_id"), "track_id")
        if track_id in seen:
            raise StudioStoreError("Studio schema-1 sidecar has duplicate track IDs.")
        seen.add(track_id)
        if track_id not in current_ids:
            # Schema 1 historically reconciled away lanes no longer present in
            # the take manifest. Preserve that safe durable-ID behavior.
            continue
        try:
            document = document.update_track(
                track_id,
                gain=raw.get("gain", 1.0),
                pan=raw.get("pan", 0.0),
                muted=raw.get("muted", False),
                solo=raw.get("solo", False),
                export_included=raw.get("export_included", True),
            )
        except StudioProjectError as exc:
            raise StudioStoreError("Studio schema-1 track is not valid.") from exc
    return document


def _decode_document(
    project: TakeProject,
    data: bytes,
) -> tuple[StudioDocument, StudioLoadOrigin, bool]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StudioStoreError("Studio state is not valid UTF-8 JSON.") from exc
    if not isinstance(value, Mapping):
        raise StudioStoreError("Studio state root must be an object.")
    schema = value.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise StudioStoreError("Studio state schema must be an integer.")
    try:
        if schema == STUDIO_STATE_SCHEMA_VERSION:
            document = studio_document_from_dict(value)
            reconciled = reconcile_studio_document(project, document)
            return (
                reconciled,
                StudioLoadOrigin.PRIMARY_V2,
                reconciled != document,
            )
        if schema == LEGACY_STUDIO_STATE_SCHEMA_VERSION:
            return (
                _migrate_schema_v1(project, value),
                StudioLoadOrigin.MIGRATED_V1,
                True,
            )
    except StudioProjectError as exc:
        raise StudioStoreError("Studio arrangement is not valid.") from exc
    raise StudioStoreError("Studio state has an unsupported schema.")


def _exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StudioStoreError("Could not inspect Studio state path.") from exc
    return True


@contextmanager
def studio_store_lock(take_dir: str | Path) -> Iterator[Path]:
    """Serialize Studio metadata writes in this process and across processes."""
    folder = Path(take_dir).expanduser().resolve()
    with _STORE_LOCKS_GUARD:
        process_lock = _STORE_LOCKS.setdefault(folder, threading.RLock())
    with process_lock:
        lock_path = folder / STUDIO_STATE_LOCK_FILENAME
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
                raise _UnsafeStudioPath(
                    "Studio state lock must be one stable regular file."
                )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            descriptor = -1
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            if _exists_without_following(lock_path):
                try:
                    if stat.S_ISLNK(lock_path.lstat().st_mode):
                        raise StudioStoreError(
                            "Studio state lock must not be a symbolic link."
                        ) from exc
                except OSError:
                    pass
            raise StudioStoreError("Could not open the Studio state lock.") from exc
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


def load_studio_document(take_dir: str | Path) -> StudioLoadResult:
    """Load primary, migrated legacy, recovered backup, or deterministic defaults.

    Recovery never rewrites either file. Callers receive the exact current
    primary token so an explicit save can safely replace or preserve it.
    """
    folder, project = _read_project(take_dir)
    primary = folder / STUDIO_STATE_FILENAME
    backup = folder / STUDIO_STATE_BACKUP_FILENAME

    primary_data: bytes | None = None
    primary_token: str | None = None
    primary_error: StudioStoreError | None = None
    if _exists_without_following(primary):
        try:
            primary_data = _read_bounded(primary, "state sidecar")
            primary_token = _token(primary_data)
            document, origin, reconciled = _decode_document(project, primary_data)
            return StudioLoadResult(
                document=replace(document, _store_token=primary_token),
                token=primary_token,
                origin=origin,
                needs_save=(origin is StudioLoadOrigin.MIGRATED_V1 or reconciled),
            )
        except _UnsafeStudioPath:
            raise
        except _OversizedStudioState as exc:
            primary_error = exc
            primary_token = exc.token
        except StudioStoreError as exc:
            primary_error = exc

    if _exists_without_following(backup):
        try:
            backup_data = _read_bounded(backup, "state backup")
            document, _backup_schema, _reconciled = _decode_document(
                project, backup_data
            )
        except StudioStoreError as backup_error:
            if primary_error is not None:
                raise StudioStoreError(
                    "Studio state and its last-known-good backup are invalid."
                ) from primary_error
            raise StudioStoreError("Studio state backup is invalid.") from backup_error
        return StudioLoadResult(
            document=replace(document, _store_token=primary_token),
            token=primary_token,
            origin=StudioLoadOrigin.BACKUP,
            needs_save=True,
            recovery_notice=(
                "Recovered the last-known-good Studio arrangement. The damaged "
                "primary is preserved until you explicitly save."
                if primary_error is not None
                else "Recovered the last-known-good Studio arrangement backup."
            ),
        )

    if primary_error is not None:
        raise primary_error
    return StudioLoadResult(
        document=default_studio_document(project),
        token=None,
        origin=StudioLoadOrigin.DEFAULT,
        needs_save=False,
    )


def _preserve_corrupt_primary(folder: Path, data: bytes) -> Path:
    digest = _token(data)
    target = folder / f".webjam-studio-state.corrupt-{digest}.json"
    if _exists_without_following(target):
        existing = _read_bounded(target, "corrupt-state recovery copy")
        if existing != data:
            raise StudioStoreError("Studio corrupt-state recovery path is occupied.")
        return target
    atomic_write_bytes(target, data, mode=0o600)
    return target


def _quarantine_oversized_primary(
    folder: Path,
    primary: Path,
    token: str,
) -> Path:
    """Move a stable oversized regular primary aside without loading it."""

    target = folder / f".webjam-studio-state.corrupt-oversized-{token}.json"
    if _exists_without_following(target):
        raise StudioStoreError(
            "Studio oversized-state recovery path is already occupied."
        )
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
            raise StudioStoreError(
                "Studio oversized state changed before it could be preserved."
            )
        primary.unlink()
        return target
    except OSError as exc:
        raise StudioStoreError(
            "Could not preserve oversized Studio state before saving."
        ) from exc
    finally:
        if linked and primary.exists() and target.exists():
            try:
                target.unlink()
            except OSError:
                pass


def save_studio_document(
    take_dir: str | Path,
    document: StudioDocument,
    *,
    expected_token: str | None,
) -> StudioSaveResult:
    """CAS-save one arrangement while preserving valid or corrupt prior bytes."""
    if not isinstance(document, StudioDocument):
        raise StudioStoreError("Studio state must be a StudioDocument value.")
    if expected_token is not None:
        if (
            not isinstance(expected_token, str)
            or len(expected_token) != 64
            or any(character not in "0123456789abcdef" for character in expected_token)
        ):
            raise StudioStoreError("Studio state token must be a lowercase SHA-256.")

    folder, _project = _read_project(take_dir)
    with studio_store_lock(folder):
        # Re-read recording truth after the cross-process lock is held.
        _folder, project = _read_project(folder)
        try:
            reconciled = reconcile_studio_document(project, document)
        except StudioProjectError as exc:
            raise StudioStoreError(str(exc)) from exc

        primary = folder / STUDIO_STATE_FILENAME
        backup = folder / STUDIO_STATE_BACKUP_FILENAME
        migration_backup = folder / STUDIO_STATE_V1_BACKUP_FILENAME
        current: bytes | None = None
        oversized: _OversizedStudioState | None = None
        if _exists_without_following(primary):
            try:
                current = _read_bounded(primary, "state sidecar")
            except _OversizedStudioState as exc:
                oversized = exc
        current_token = (
            oversized.token
            if oversized is not None
            else (_token(current) if current is not None else None)
        )
        if current_token != expected_token:
            if oversized is not None and expected_token is None:
                raise oversized
            raise StudioStoreConflict(
                "Studio state changed after it was loaded; reload and merge your edits."
            )

        backup_written: Path | None = None
        if oversized is not None:
            backup_written = _quarantine_oversized_primary(
                folder,
                primary,
                oversized.token,
            )
        elif current is not None:
            try:
                _old_document, old_origin, _reconciled = _decode_document(
                    project, current
                )
            except StudioStoreError:
                try:
                    backup_written = _preserve_corrupt_primary(folder, current)
                except OSError as exc:
                    raise StudioStoreError(
                        "Could not preserve damaged Studio state before saving."
                    ) from exc
            else:
                try:
                    if (
                        old_origin is StudioLoadOrigin.MIGRATED_V1
                        and _exists_without_following(migration_backup)
                    ):
                        preserved_v1 = _read_bounded(
                            migration_backup,
                            "schema-1 migration backup",
                        )
                        if preserved_v1 != current:
                            raise StudioStoreError(
                                "Existing Studio schema-1 backup does not match "
                                "the exact legacy sidecar."
                            )
                    atomic_write_bytes(backup, current, mode=0o600)
                    backup_written = backup
                    if (
                        old_origin is StudioLoadOrigin.MIGRATED_V1
                        and not _exists_without_following(migration_backup)
                    ):
                        atomic_write_bytes(migration_backup, current, mode=0o600)
                except OSError as exc:
                    raise StudioStoreError(
                        "Could not preserve the last-known-good Studio state."
                    ) from exc

        payload = (
            json.dumps(reconciled.to_dict(), indent=2, sort_keys=False) + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_STUDIO_STATE_BYTES:
            raise StudioStoreError("Studio arrangement is too large to save safely.")
        try:
            atomic_write_bytes(primary, payload, mode=0o600)
        except OSError as exc:
            raise StudioStoreError("Could not atomically save Studio state.") from exc
        saved_token = _token(payload)
        saved_document = replace(reconciled, _store_token=saved_token)
        return StudioSaveResult(
            document=saved_document,
            path=primary,
            token=saved_token,
            backup_path=backup_written,
        )


__all__ = [
    "LEGACY_STUDIO_STATE_SCHEMA_VERSION",
    "MAX_STUDIO_STATE_BYTES",
    "STUDIO_STATE_BACKUP_FILENAME",
    "STUDIO_STATE_FILENAME",
    "STUDIO_STATE_LOCK_FILENAME",
    "STUDIO_STATE_SCHEMA_VERSION",
    "STUDIO_STATE_V1_BACKUP_FILENAME",
    "StudioLoadOrigin",
    "StudioLoadResult",
    "StudioSaveResult",
    "StudioStoreConflict",
    "StudioStoreError",
    "load_studio_document",
    "save_studio_document",
    "studio_state_backup_path",
    "studio_state_path",
    "studio_store_lock",
]

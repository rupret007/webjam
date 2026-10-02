"""Explicit local workspace metadata snapshots; never media or live ownership.

This format contains the Session library record, including historical links.
It does not copy, open, fetch, or verify referenced media, Studio review/edit
files, or export receipts. Checksums detect changed metadata, not authorship.
Preview retains validated bytes, independently of later edits to the file.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from core.art_workspace import normalize_art_workspace
from core.rehearsal_plan import RehearsalPlan
from core.session_library import (
    MAX_IMPORT_HISTORY,
    MAX_SESSION_RECORD_BYTES,
    SessionLibraryError,
    SessionLibrary,
    SessionRecord,
    decode_session_record,
    encode_session_record,
)

MAX_WORKSPACE_BACKUP_BYTES = MAX_SESSION_RECORD_BYTES + 1024
_FORMAT = "webjam.workspace-backup"
_FIELDS = frozenset(("format", "version", "content", "payload", "payload_sha256"))
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
METADATA_ONLY_LIMITS = (
    "Workspace metadata only; audio, artwork and other referenced files are not included.",
    "Take links are historical references, not proof that recordings are available or complete here.",
    "Studio review/edit files and export receipts are not included or reverified.",
    "Private notes and reference locations remain literal; this is not a secret-scrubbing format.",
)
_TAKE_FIELDS = frozenset(("take_id", "take_path", "path", "title", "source_identity",
                          "run_id", "recording_session_id", "validated", "status",
                          "historical_origins"))
_RECAP_FIELDS = frozenset(("run_id", "ended_at", "duration_seconds", "summary", "take_ids"))
_OWNERSHIP_FIELDS = ("recording_session_id", "run_id", "validated", "status")


class WorkspaceBackupError(SessionLibraryError):
    """A backup cannot be read, published, or imported without losing evidence."""


def _portable(record: SessionRecord) -> None:
    """Reject unsupported structure rather than silently dropping user metadata.

    Consumer normalizers are used only for comparison/validation. Their repaired
    output is never substituted for the exact source metadata.
    """
    for reference in record.take_links:
        if not set(reference) <= _TAKE_FIELDS:
            raise WorkspaceBackupError("Workspace take link contains unsupported fields.")
        if "historical_origins" in reference and not record.import_provenance:
            raise WorkspaceBackupError("Legacy historical take fields have no supported provenance; preserve the original.")
        if "validated" in reference and type(reference["validated"]) is not bool:
            raise WorkspaceBackupError("Workspace take validation must be boolean.")
    for recap in record.recaps:
        if not set(recap) <= _RECAP_FIELDS:
            raise WorkspaceBackupError("Workspace recap contains unsupported fields.")
        for key in ("run_id", "ended_at", "summary"):
            if key in recap and not isinstance(recap[key], str):
                raise WorkspaceBackupError("Workspace recap text is invalid.")
        if "duration_seconds" in recap and (type(recap["duration_seconds"]) is not int or recap["duration_seconds"] < 0):
            raise WorkspaceBackupError("Workspace recap duration is invalid.")
    try:
        if record.art and normalize_art_workspace(record.art) != record.art:
            raise ValueError("Art metadata requires repair or contains unsupported fields.")
        if record.rehearsal:
            plan = record.rehearsal
            if type(plan.get("version")) is not int or plan["version"] != 1:
                raise ValueError("Rehearsal version is unsupported.")
            if RehearsalPlan.from_payload(plan).payload() != plan:
                raise ValueError("Rehearsal metadata requires repair or contains unsupported fields.")
            for song in plan["songs"]:
                if type(song["completed"]) is not bool or (song["tempo"] is not None and type(song["tempo"]) is not int):
                    raise ValueError("Rehearsal completion or tempo is invalid.")
                for bookmark in song["bookmarks"]:
                    position = bookmark["position_seconds"]
                    if position is not None and type(position) not in {int, float}:
                        raise ValueError("Rehearsal moment position is invalid.")
                ids = [bookmark["id"] for bookmark in song["bookmarks"]]
                if len(set(ids)) != len(ids):
                    raise ValueError("Rehearsal moment identities must be unique within a song.")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise WorkspaceBackupError("Workspace Art or rehearsal metadata is unsupported; preserve the original and repair it before backup.") from exc


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise WorkspaceBackupError("Workspace backup metadata is invalid.") from exc


def _record_bytes(record: SessionRecord) -> bytes:
    try:
        return _canonical(json.loads(encode_session_record(record)))
    except SessionLibraryError as exc:
        raise WorkspaceBackupError(str(exc)) from exc


def _record(data: bytes) -> SessionRecord:
    try:
        return decode_session_record(data)
    except SessionLibraryError as exc:
        raise WorkspaceBackupError(str(exc)) from exc


@dataclass(frozen=True)
class WorkspaceBackupPreview:
    """An immutable snapshot; each record access returns independent metadata."""

    _payload_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        record = _record(self._payload_bytes)
        _portable(record)
        if _record_bytes(record) != self._payload_bytes:
            raise WorkspaceBackupError("Workspace preview must use canonical validated metadata.")

    @property
    def record(self) -> SessionRecord:
        return _record(self._payload_bytes)

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self._payload_bytes).hexdigest()

    @property
    def source_id(self) -> str:
        return self.record.id

    @property
    def limitations(self) -> tuple[str, ...]:
        return METADATA_ONLY_LIMITS

    def matching_import_ids(self, library: SessionLibrary) -> tuple[str, ...]:
        """Report prior imports of these exact bytes without deduplicating them."""
        records = library.list()
        if library.warnings:
            raise WorkspaceBackupError("Some workspaces could not be checked for prior imports; review library warnings.")
        source_id, checksum = self.source_id, self.payload_sha256
        return tuple(record.id for record in records if record.import_provenance
                     and record.import_provenance[-1]["source_workspace_id"] == source_id
                     and record.import_provenance[-1]["payload_sha256"] == checksum)


def import_workspace_backup(library: SessionLibrary, preview: WorkspaceBackupPreview) -> SessionRecord:
    """Create a fresh local workspace from the reviewed bytes, never activate it.

    Recording ownership and validation become explicitly historical evidence.
    A SessionLibraryImportUnconfirmed exposes the intended identity and byte
    checksum after an uncertain publication; reconcile that identity before any
    explicit retry. A successful repeat import deliberately gets a different ID.
    """
    if not isinstance(preview, WorkspaceBackupPreview):
        raise WorkspaceBackupError("Import requires a validated backup preview.")
    source = preview.record
    _portable(source)
    if len(source.import_provenance) >= MAX_IMPORT_HISTORY:
        raise WorkspaceBackupError("Workspace import history is full; no history was discarded.")
    hop = {"source_workspace_id": source.id, "profile": source.profile,
           "revision": source.revision, "created_at": source.created_at,
           "updated_at": source.updated_at, "source_key": source.source_key,
           "payload_sha256": preview.payload_sha256}
    references = []
    for reference in source.take_links:
        historical = {key: reference[key] for key in _OWNERSHIP_FIELDS if key in reference}
        if historical:
            origins = reference.get("historical_origins", [])
            if len(origins) >= MAX_IMPORT_HISTORY:
                raise WorkspaceBackupError("Historical take origins are full; no history was discarded.")
            reference["historical_origins"] = [*origins, {"source_workspace_id": source.id, **historical}]
            for key in historical:
                del reference[key]
        references.append(reference)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    imported = replace(source, id=uuid4().hex, revision=1, created_at=now, updated_at=now,
                       take_links=tuple(references), import_provenance=(*source.import_provenance, hop),
                       recovered=False, _store_token=None)
    return library.create_imported(imported)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceBackupError("Workspace backup contains duplicate fields.")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise WorkspaceBackupError("Workspace backup contains a non-finite number.")


def _decode(data: bytes) -> WorkspaceBackupPreview:
    if len(data) > MAX_WORKSPACE_BACKUP_BYTES:
        raise WorkspaceBackupError("Workspace backup is too large.")
    try:
        envelope = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, WorkspaceBackupError):
            raise
        raise WorkspaceBackupError("Workspace backup could not be decoded.") from exc
    if (not isinstance(envelope, dict) or set(envelope) != _FIELDS
            or envelope["format"] != _FORMAT or type(envelope["version"]) is not int
            or envelope["version"] != 1 or envelope["content"] != "metadata-only"):
        raise WorkspaceBackupError("Workspace backup format is unsupported.")
    checksum = envelope["payload_sha256"]
    if not isinstance(checksum, str) or not _DIGEST.fullmatch(checksum):
        raise WorkspaceBackupError("Workspace backup checksum is invalid.")
    payload = _canonical(envelope["payload"])
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise WorkspaceBackupError("Workspace backup checksum does not match its metadata.")
    return WorkspaceBackupPreview(payload)


def _read_regular(path: Path) -> bytes:
    """Bound reads and reject links, special files, and replacements during read."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise WorkspaceBackupError("Choose a regular backup file without symbolic links.")
    if before.st_size > MAX_WORKSPACE_BACKUP_BYTES:
        raise WorkspaceBackupError("Workspace backup is too large.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        with stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                before.st_dev, before.st_ino
            ) != (opened.st_dev, opened.st_ino):
                raise WorkspaceBackupError("Workspace backup changed while being read.")
            data = stream.read(MAX_WORKSPACE_BACKUP_BYTES + 1)
            after = os.fstat(stream.fileno())
            current = path.lstat()
            def identity(value):
                return (value.st_dev, value.st_ino, value.st_size,
                        value.st_mtime_ns, value.st_ctime_ns)
            if not stat.S_ISREG(current.st_mode) or not (
                identity(before) == identity(opened) == identity(after) == identity(current)
            ):
                raise WorkspaceBackupError("Workspace backup changed while being read.")
        if len(data) > MAX_WORKSPACE_BACKUP_BYTES:
            raise WorkspaceBackupError("Workspace backup is too large.")
        return data
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def preview_workspace_backup(path: str | Path) -> WorkspaceBackupPreview:
    """Read once without writes or media access; preview binds validated bytes."""
    try:
        return _decode(_read_regular(Path(path)))
    except (OSError, ValueError) as exc:
        if isinstance(exc, WorkspaceBackupError):
            raise
        raise WorkspaceBackupError("Workspace backup could not be read safely.") from exc


def _same_inode(path: Path, expected: os.stat_result) -> bool:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


def _new_stage(parent: Path) -> tuple[int, str]:
    if os.name != "nt":
        return tempfile.mkstemp(prefix=".webjam-backup-", suffix=".tmp", dir=parent)

    # The normal Windows CRT temporary-file handle denies delete sharing.
    # Explicit sharing allows ownership-checked unlink while this descriptor
    # still pins the inode. No delete-on-close flag or DELETE access is used:
    # ordinary readback of the published hard link must remain compatible.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    name = str(parent / f".webjam-backup-{uuid4().hex}.tmp")
    # GENERIC_READ | GENERIC_WRITE; FILE_SHARE_READ | WRITE | DELETE;
    # CREATE_NEW; FILE_ATTRIBUTE_NORMAL. Null security means non-inheritable.
    handle = create(name, 0x80000000 | 0x40000000, 1 | 2 | 4, None, 1, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise
    # Ownership is now transferred to the CRT descriptor, then to fdopen.
    return descriptor, name


def export_workspace_backup(record: SessionRecord, destination: str | Path) -> WorkspaceBackupPreview:
    """Publish a complete private backup to a NEW filename; never replace files.

    A fsynced temporary file is linked exclusively into place. Existing files,
    directories and links are refused, including ones created during export.
    A post-publication durability failure can leave the complete new backup;
    it is reported as an error, never silently retried or called successful.
    """
    preview = WorkspaceBackupPreview(_record_bytes(record))
    data = _canonical({"format": _FORMAT, "version": 1, "content": "metadata-only",
                       "payload": json.loads(preview._payload_bytes),
                       "payload_sha256": preview.payload_sha256}) + b"\n"
    if len(data) > MAX_WORKSPACE_BACKUP_BYTES:
        raise WorkspaceBackupError("Workspace backup is too large.")
    temporary = None
    owned = None
    stream = None
    try:
        target = Path(destination)
        # Resolve the selected existing directory once, so ordinary platform
        # aliases are supported without creating directories as a side effect.
        parent = target.parent.resolve(strict=True)
        target = parent / target.name
        if target.exists() or target.is_symlink():
            raise WorkspaceBackupError("Choose a new backup filename; existing files are preserved.")
        descriptor, name = _new_stage(parent)
        temporary = Path(name)
        try:
            stream = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        # Keep this descriptor open until ownership-checked cleanup finishes.
        # Otherwise an unlinked stage's inode number can be reused, making a
        # dev/inode comparison mistake another writer's file for our own.
        owned = os.fstat(stream.fileno())
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        if not _same_inode(temporary, owned):
            raise WorkspaceBackupError("Workspace backup staging file changed; publication was stopped.")
        os.link(temporary, target, follow_symlinks=False)
        if not _same_inode(target, owned) or _read_regular(target) != data:
            raise WorkspaceBackupError("Workspace backup publication changed; the destination was not confirmed saved.")
        if not _same_inode(temporary, owned):
            raise WorkspaceBackupError("Workspace backup staging file changed; the destination was not confirmed saved.")
        temporary.unlink()
        temporary = None
        if os.name == "posix":
            directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return preview
    except (OSError, ValueError) as exc:
        if isinstance(exc, WorkspaceBackupError):
            raise
        raise WorkspaceBackupError("Workspace backup was not confirmed saved; preserve any completed file and choose a new writable filename.") from exc
    finally:
        try:
            if temporary is not None and owned is not None:
                try:
                    if _same_inode(temporary, owned):
                        temporary.unlink()
                except OSError:
                    # Preserve the original failure. This private temporary
                    # file is never reported as a completed backup.
                    pass
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError as exc:
                    raise WorkspaceBackupError("Workspace backup close was not confirmed; preserve any completed file.") from exc

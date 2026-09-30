"""Private, revision-safe named workspaces; referenced media stays in place.

One bounded JSON document owns each workspace. Reads never repair or replace
files. A damaged primary can expose its last valid backup with ``recovered``
set; the next explicit save preserves the damaged bytes before publication.
The caller must retain the record returned by save, including its store token.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Iterator
from uuid import UUID, uuid4, uuid5

from core.component_lock import ComponentLockError, InterProcessComponentLock
from core.creative_modes import CREATOR_PROFILES
from core.file_io import atomic_write_bytes
from core.notes_recovery import MAX_RECOVERY_DRAFT_BYTES

MAX_SESSION_RECORD_BYTES = 8 * 1024 * 1024
MAX_LIBRARY_SCAN_BYTES = 64 * 1024 * 1024
MAX_LIBRARY_RECORDS = 1000
_PROFILES = frozenset(profile.key for profile in CREATOR_PROFILES)
_ID = re.compile(r"[0-9a-f]{32}\Z")
_IMPORT_NAMESPACE = UUID("d1dbac72-a7ca-47c3-b071-b097ee73c198")
_CONTENT_FIELDS = (
    "notes", "mode_key", "decisions", "actions", "blockers", "recaps",
    "take_links", "rehearsal", "art", "source_key",
)
_FIELDS = frozenset((
    "version", "id", "profile", "title", "revision", "created_at", "updated_at",
    *_CONTENT_FIELDS,
))


class SessionLibraryError(ValueError):
    """A workspace cannot be read or saved safely."""


class SessionLibraryConflict(SessionLibraryError):
    """A newer or externally changed workspace must be reloaded first."""


class _UnsafeSessionPath(SessionLibraryError):
    pass


@dataclass(frozen=True)
class SessionRecord:
    id: str
    profile: str
    title: str
    revision: int
    created_at: str
    updated_at: str
    notes: str = ""
    mode_key: str = ""
    decisions: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    recaps: tuple[dict, ...] = ()
    take_links: tuple[dict, ...] = ()
    rehearsal: dict = field(default_factory=dict)
    art: dict = field(default_factory=dict)
    source_key: str = ""
    recovered: bool = field(default=False, compare=False)
    _store_token: str | None = field(default=None, repr=False, compare=False)


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise SessionLibraryError(f"{label} must be text.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SessionLibraryError(f"{label} must be valid UTF-8.") from exc
    if len(encoded) > maximum or "\0" in value or (required and not value.strip()):
        raise SessionLibraryError(f"{label} is empty, invalid, or too large.")
    return value


def _profile(value: object) -> str:
    if not isinstance(value, str) or value not in _PROFILES:
        raise SessionLibraryError("Workspace profile is unsupported.")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SessionLibraryError("Workspace identifier is invalid.")
    return value


def _timestamp(value: object) -> str:
    value = _text(value, "Workspace timestamp", 40, required=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionLibraryError("Workspace timestamp is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SessionLibraryError("Workspace timestamp must use UTC.")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json_value(value: object, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [50000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise SessionLibraryError("Workspace details are too deeply nested or numerous.")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 2**63 - 1:
            raise SessionLibraryError("Workspace number is too large.")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SessionLibraryError("Workspace number must be finite.")
        return
    if isinstance(value, str):
        _text(value, "Workspace detail", MAX_RECOVERY_DRAFT_BYTES)
        return
    if isinstance(value, (tuple, list)):
        if len(value) > 2000:
            raise SessionLibraryError("Workspace list is too large.")
        for child in value:
            _json_value(child, depth=depth + 1, budget=budget)
        return
    if isinstance(value, dict):
        if len(value) > 2000:
            raise SessionLibraryError("Workspace object is too large.")
        for key, child in value.items():
            _text(key, "Workspace field name", 256, required=True)
            _json_value(child, depth=depth + 1, budget=budget)
        return
    raise SessionLibraryError("Workspace details must contain only JSON values.")


def _payload(record: SessionRecord) -> dict:
    if not isinstance(record, SessionRecord):
        raise SessionLibraryError("Expected a workspace record.")
    _identifier(record.id)
    _profile(record.profile)
    _text(record.title, "Workspace title", 512, required=True)
    _text(record.notes, "Workspace notes", MAX_RECOVERY_DRAFT_BYTES)
    _text(record.mode_key, "Workspace mode", 128)
    _text(record.source_key, "Workspace import source", 512)
    if type(record.revision) is not int or not 1 <= record.revision <= 2**63 - 1:
        raise SessionLibraryError("Workspace revision is invalid.")
    _timestamp(record.created_at)
    _timestamp(record.updated_at)
    if datetime.fromisoformat(record.updated_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        record.created_at.replace("Z", "+00:00")
    ):
        raise SessionLibraryError("Workspace update predates its creation.")
    for name in ("decisions", "actions", "blockers"):
        values = getattr(record, name)
        if not isinstance(values, (list, tuple)) or len(values) > 2000:
            raise SessionLibraryError(f"Workspace {name} list is invalid.")
        for value in values:
            _text(value, f"Workspace {name}", 65536)
    for name in ("recaps", "take_links"):
        values = getattr(record, name)
        if not isinstance(values, (list, tuple)) or len(values) > 2000:
            raise SessionLibraryError(f"Workspace {name} list is invalid.")
        if any(not isinstance(value, dict) for value in values):
            raise SessionLibraryError(f"Workspace {name} must contain objects.")
    if not isinstance(record.rehearsal, dict) or not isinstance(record.art, dict):
        raise SessionLibraryError("Workspace rehearsal and Art details must be objects.")
    result = {
        "version": 1, "id": record.id, "profile": record.profile,
        "title": record.title, "revision": record.revision,
        "created_at": record.created_at, "updated_at": record.updated_at,
        **{name: getattr(record, name) for name in _CONTENT_FIELDS},
    }
    _json_value(result)
    return result


def _encode(record: SessionRecord) -> bytes:
    try:
        data = (json.dumps(_payload(record), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, OverflowError, RecursionError) as exc:
        raise SessionLibraryError("Workspace details could not be encoded.") from exc
    if len(data) > MAX_SESSION_RECORD_BYTES:
        raise SessionLibraryError("Workspace document is too large; nothing was saved.")
    return data


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SessionLibraryError("Workspace document has duplicate fields.")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise SessionLibraryError("Workspace document contains a non-finite number.")


def _decode(data: bytes, expected_id: str) -> SessionRecord:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(value, dict) or set(value) != _FIELDS or type(value["version"]) is not int or value["version"] != 1:
            raise SessionLibraryError("Workspace document schema is unsupported.")
        raw = {name: content for name, content in value.items() if name != "version"}
        for name in ("decisions", "actions", "blockers", "recaps", "take_links"):
            if not isinstance(raw[name], list):
                raise SessionLibraryError("Workspace document list is invalid.")
            raw[name] = tuple(raw[name])
        record = SessionRecord(**raw)
        _payload(record)
        if record.id != expected_id:
            raise SessionLibraryError("Workspace filename and identity do not match.")
        return record
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, SessionLibraryError):
            raise
        raise SessionLibraryError("Workspace document could not be decoded.") from exc


def _read(path: Path) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise _UnsafeSessionPath("Workspace storage must use regular files without symbolic links.")
    if before.st_size > MAX_SESSION_RECORD_BYTES:
        raise SessionLibraryError("Workspace file is too large.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode) or (
            (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _UnsafeSessionPath("Workspace changed while being read.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(MAX_SESSION_RECORD_BYTES + 1)
        if len(data) > MAX_SESSION_RECORD_BYTES:
            raise SessionLibraryError("Workspace file is too large.")
        return data
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _token(data: bytes | None) -> str:
    return "missing" if data is None else hashlib.sha256(data).hexdigest()


class SessionLibrary:
    """Named local sessions. Use ``replace(record, ...)`` then ``save(record)``.

    References in ``art``, ``rehearsal``, and ``take_links`` are metadata only:
    missing or moved files do not prevent reopening a workspace. Callers own
    explicit relinking; the store never follows, relocates, or deletes media.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else Path.home() / ".webjam_sessions"
        self.warnings: tuple[str, ...] = ()

    def _root_exists(self, *, create: bool = False) -> bool:
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            if not create:
                return False
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise _UnsafeSessionPath("Session library must be a directory without symbolic links.")
        return True

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._root_exists(create=True)
        try:
            with InterProcessComponentLock(self.root / ".library.lock", timeout=2):
                yield
        except ComponentLockError as exc:
            raise SessionLibraryError("Session library is busy or could not be locked; retry saving.") from exc

    def _path(self, session_id: str) -> Path:
        return self.root / f"{_identifier(session_id)}.json"

    def _load(self, session_id: str, profile: str | None = None) -> SessionRecord:
        path = self._path(session_id)
        data = _read(path)
        backup = None
        try:
            if data is None:
                raise SessionLibraryError("Workspace was not found.")
            record = _decode(data, session_id)
        except SessionLibraryError as primary_error:
            backup = _read(path.with_suffix(".json.bak"))
            if backup is None:
                raise primary_error
            record = replace(_decode(backup, session_id), recovered=True)
        if profile is not None and record.profile != _profile(profile):
            raise SessionLibraryError("Workspace belongs to a different creator profile.")
        token = _token(data)
        if record.recovered:
            # Recovery depends on both files. An externally edited backup
            # must not be overwritten merely because the damaged primary is
            # unchanged and the edit kept the same revision number.
            token = f"recovered:{token}:{_token(backup)}"
        return replace(record, _store_token=token)

    def load(self, session_id: str, *, profile: str | None = None) -> SessionRecord:
        """Read without writing; ``recovered`` identifies a valid backup fallback."""
        self._root_exists()
        return self._load(session_id, profile)

    def _create(self, profile: str, title: str, session_id: str, payload: dict) -> SessionRecord:
        now = _now()
        try:
            record = SessionRecord(session_id, profile, title, 1, now, now, **payload)
        except TypeError as exc:
            raise SessionLibraryError("Workspace fields are unsupported.") from exc
        data = _encode(record)
        path = self._path(session_id)
        if _read(path) is not None or _read(path.with_suffix(".json.bak")) is not None:
            raise SessionLibraryConflict("Workspace already exists.")
        atomic_write_bytes(path, data, mode=0o600)
        return replace(_decode(data, session_id), _store_token=_token(data))

    def create(self, profile: str, title: str, **payload: object) -> SessionRecord:
        """Create a named session; notes and payloads are never truncated."""
        with self._locked():
            return self._create(profile, title, uuid4().hex, payload)

    def save(self, record: SessionRecord) -> SessionRecord:
        """Publish only if both the revision and exact previously read bytes match.

        An I/O exception can occur after atomic publication (directory fsync).
        Reload to determine the current state; never retry a stale record blind.
        """
        _encode(record)
        with self._locked():
            current = self._load(record.id, record.profile)
            if record._store_token is None or record._store_token != current._store_token or record.revision != current.revision:
                raise SessionLibraryConflict("Workspace changed since it was opened. Reload before saving.")
            if record.created_at != current.created_at or record.source_key != current.source_key:
                raise SessionLibraryConflict("Workspace creation or import identity changed.")
            # A backwards wall clock must not make newly saved work sort older.
            updated = max(_now(), current.updated_at)
            saved = replace(record, revision=current.revision + 1, updated_at=updated, recovered=False)
            data = _encode(saved)
            path = self._path(record.id)
            previous = _read(path)
            primary_token = current._store_token.split(":")[1] if current.recovered else current._store_token
            if _token(previous) != primary_token:
                raise SessionLibraryConflict("Workspace changed during save.")
            if current.recovered:
                if previous is not None:
                    preserved = self.root / f"{record.id}.damaged-{_token(previous)}"
                    atomic_write_bytes(preserved, previous, mode=0o600)
            elif previous is not None:
                backup = path.with_suffix(".json.bak")
                _read(backup)  # Never replace an unsafe link or special file.
                atomic_write_bytes(backup, previous, mode=0o600)
            atomic_write_bytes(path, data, mode=0o600)
            return replace(_decode(data, record.id), _store_token=_token(data))

    def import_snapshot(
        self, profile: str, title: str, notes: str, *,
        source_key: str = "legacy-profile-notes", **payload: object,
    ) -> SessionRecord:
        """Copy one legacy snapshot once per profile/source; never read originals.

        Existing imported work is returned unchanged, even if the caller later
        supplies a different snapshot. No source file is modified or deleted.
        """
        _profile(profile)
        _text(source_key, "Workspace import source", 512, required=True)
        session_id = uuid5(_IMPORT_NAMESPACE, profile + "\0" + source_key).hex
        with self._locked():
            path = self._path(session_id)
            if _read(path) is not None or _read(path.with_suffix(".json.bak")) is not None:
                return self._load(session_id, profile)
            return self._create(profile, title, session_id, {**payload, "notes": notes, "source_key": source_key})

    def list(self, query: str = "", profile: str | None = None) -> list[SessionRecord]:
        """Recent first; search all saved content without dereferencing media.

        One damaged document does not hide the rest. ``warnings`` exposes every
        skipped document, backup fallback, and scan limit encountered this call.
        """
        self.warnings = ()
        _text(query, "Workspace search", 4096)
        if profile is not None:
            _profile(profile)
        if not self._root_exists():
            return []
        warnings = []
        records = []
        total_bytes = 0
        scanned = 0
        seen = set()
        needle = query.casefold().strip()
        with os.scandir(self.root) as entries:
            for entry in entries:
                if entry.name.endswith(".json.bak"):
                    session_id = entry.name[:-9]
                elif entry.name.endswith(".json"):
                    session_id = entry.name[:-5]
                else:
                    continue
                if not _ID.fullmatch(session_id) or session_id in seen:
                    continue
                seen.add(session_id)
                scanned += 1
                if scanned > MAX_LIBRARY_RECORDS:
                    warnings.append("Session library scan limit reached; some workspaces were not listed.")
                    break
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > MAX_SESSION_RECORD_BYTES:
                        raise SessionLibraryError("Workspace file is too large.")
                    total_bytes += size
                    if total_bytes > MAX_LIBRARY_SCAN_BYTES:
                        warnings.append("Session library size limit reached; some workspaces were not listed.")
                        break
                    record = self._load(session_id)
                    if profile is not None and record.profile != profile:
                        continue
                    if record.recovered:
                        warnings.append(f"Workspace {record.id} was read from its last valid backup; review before saving.")
                    searchable = json.dumps(_payload(record), ensure_ascii=False).casefold()
                    if not needle or needle in searchable:
                        records.append(record)
                except (OSError, SessionLibraryError) as exc:
                    warnings.append(f"Workspace {session_id} could not be opened ({type(exc).__name__}); its files were preserved.")
        self.warnings = tuple(warnings)
        return sorted(records, key=lambda record: (record.updated_at, record.id), reverse=True)


__all__ = [
    "MAX_SESSION_RECORD_BYTES", "SessionLibrary", "SessionLibraryConflict",
    "SessionLibraryError", "SessionRecord",
]

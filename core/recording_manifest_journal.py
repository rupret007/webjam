"""Crash-safe, private evidence journals for in-progress recordings.

The final ``webjam-take.json`` manifest is written only after a take is
stopped.  This module provides a deliberately narrow checkpoint while a
recording is live: its payload is only :class:`SessionEvidence`, bound to a
canonical take UUID.  It never persists settings, invite links, credentials,
or raw diagnostic output.

Journals are kept below the caller-supplied takes root in a private directory.
Writes first fsync a private temporary file and then use an atomic filesystem
operation; directory metadata is fsynced before success is returned.  A bad
or untrusted journal is never interpreted as a clean take: ``load`` returns a
result marked untrusted with ``RecoveryStatus.NEEDS_ATTENTION``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping
import uuid

from core.take_project import RecoveryStatus, SessionEvidence


JOURNAL_SCHEMA_VERSION = 1
JOURNAL_DIRECTORY_NAME = ".webjam-recording-evidence"
JOURNAL_FILE_SUFFIX = ".json"
JOURNAL_FILE_MODE = 0o600
JOURNAL_DIRECTORY_MODE = 0o700
MAX_JOURNAL_BYTES = 64 * 1024
MAX_RECOVERY_NOTES = 32
MAX_TIMELINE_EVENTS = 256


class RecordingManifestJournalError(RuntimeError):
    """A journal could not be safely addressed or made durable."""


@dataclass(frozen=True, slots=True)
class JournalLoadResult:
    """The result of loading one recording-evidence checkpoint.

    ``trusted`` is false for malformed, oversized, incorrectly-permissioned,
    or otherwise untrusted on-disk content.  ``error`` is intentionally a
    stable, non-sensitive code; raw file contents and OS errors are never
    copied into a take manifest or surfaced by this module.
    """

    take_id: str
    evidence: SessionEvidence
    trusted: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class JournalDirectoryIssue:
    """A non-sensitive issue discovered while scanning the private directory."""

    error: str


@dataclass(frozen=True, slots=True)
class PendingJournalScan:
    """All canonical journal results plus safe reports for stray entries.

    A canonical UUID filename with malformed content is represented in
    ``journals`` as a ``JournalLoadResult`` with ``trusted=False`` so a
    recovery caller still knows which take needs attention.  Non-canonical
    filenames are never surfaced verbatim because they are untrusted input;
    they are represented by a generic ``JournalDirectoryIssue`` instead.
    """

    journals: tuple[JournalLoadResult, ...] = ()
    untrusted_entries: tuple[JournalDirectoryIssue, ...] = ()


class RecordingManifestJournal:
    """Own crash-safe SessionEvidence checkpoints below one takes root."""

    def __init__(self, takes_root: str | Path) -> None:
        self.takes_root = Path(takes_root).expanduser().resolve(strict=False)
        self._directory = self.takes_root / JOURNAL_DIRECTORY_NAME

    @property
    def directory(self) -> Path:
        """The private journal directory, without creating it."""

        return self._directory

    def path_for(self, take_id: str) -> Path:
        """Return the canonical, non-traversable journal path for ``take_id``."""

        return self._directory / f"{_canonical_take_id(take_id)}{JOURNAL_FILE_SUFFIX}"

    def create(self, take_id: str, evidence: SessionEvidence) -> Path:
        """Create a new journal without ever exposing a partial file.

        A journal is intentionally immutable at creation time.  Repeated
        create calls fail rather than silently replacing evidence from an
        earlier recording.
        """

        canonical_take_id = _canonical_take_id(take_id)
        payload = _serialized_payload(canonical_take_id, evidence)
        directory = self._journal_directory(create=True)
        path = directory / f"{canonical_take_id}{JOURNAL_FILE_SUFFIX}"
        temporary = _write_private_temporary(directory, payload)
        try:
            try:
                # Linking a fully-fsynced sibling creates the destination
                # atomically and refuses to overwrite an existing journal.
                os.link(temporary, path)
            except FileExistsError:
                raise
            except OSError as exc:
                raise RecordingManifestJournalError(
                    "Could not atomically create the recording journal."
                ) from exc
            _fsync_directory(directory)
            return path
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def update(self, take_id: str, evidence: SessionEvidence) -> Path:
        """Atomically replace an existing journal with typed evidence."""

        canonical_take_id = _canonical_take_id(take_id)
        payload = _serialized_payload(canonical_take_id, evidence)
        directory = self._journal_directory(create=True)
        path = directory / f"{canonical_take_id}{JOURNAL_FILE_SUFFIX}"
        _require_regular_file(path)
        temporary = _write_private_temporary(directory, payload)
        try:
            try:
                os.replace(temporary, path)
            except OSError as exc:
                raise RecordingManifestJournalError(
                    "Could not atomically update the recording journal."
                ) from exc
            _fsync_directory(directory)
            return path
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def load(self, take_id: str) -> JournalLoadResult | None:
        """Load one checkpoint, failing closed if its on-disk form is unsafe.

        ``None`` means no journal exists.  A non-``None`` result with
        ``trusted=False`` must be treated as a recovery-needed recording; it
        deliberately supplies no stale host, timing, or protocol facts.
        """

        canonical_take_id = _canonical_take_id(take_id)
        directory = self._journal_directory(create=False)
        if directory is None:
            return None
        path = directory / f"{canonical_take_id}{JOURNAL_FILE_SUFFIX}"
        try:
            payload = _read_private_file(path)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, ValueError, RecordingManifestJournalError):
            return _untrusted_result(canonical_take_id)

        try:
            decoded = json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
            evidence = _parse_payload(decoded, canonical_take_id)
        except (TypeError, ValueError, RecordingManifestJournalError):
            return _untrusted_result(canonical_take_id)
        return JournalLoadResult(canonical_take_id, evidence, trusted=True)

    def remove(self, take_id: str) -> bool:
        """Atomically remove a journal entry; return false when it is absent."""

        canonical_take_id = _canonical_take_id(take_id)
        directory = self._journal_directory(create=False)
        if directory is None:
            return False
        path = directory / f"{canonical_take_id}{JOURNAL_FILE_SUFFIX}"
        try:
            _require_regular_file(path)
        except FileNotFoundError:
            return False
        try:
            path.unlink()
            _fsync_directory(directory)
        except OSError as exc:
            raise RecordingManifestJournalError(
                "Could not remove the recording journal."
            ) from exc
        return True

    def list_pending(self) -> PendingJournalScan:
        """Scan pending journals without recursing or trusting directory names.

        Only exactly canonical UUID ``.json`` names are loaded.  Their
        ordinary load result carries malformed or permission failures as
        ``trusted=False``.  Every other immediate directory entry is counted
        as a generic untrusted issue rather than being silently ignored or
        copied into a manifest.
        """

        directory = self._journal_directory(create=False)
        if directory is None:
            return PendingJournalScan()
        try:
            paths = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
        except OSError as exc:
            raise RecordingManifestJournalError(
                "Could not scan the recording journal directory."
            ) from exc

        journals: list[JournalLoadResult] = []
        untrusted_entries: list[JournalDirectoryIssue] = []
        for path in paths:
            take_id = _take_id_from_journal_name(path.name)
            if not take_id:
                untrusted_entries.append(JournalDirectoryIssue("journal_untrusted_name"))
                continue
            result = self.load(take_id)
            if result is None:
                # A concurrent delete is not recovery evidence.  It is safe
                # to omit because the caller cannot recover a missing file.
                continue
            journals.append(result)
        return PendingJournalScan(tuple(journals), tuple(untrusted_entries))

    def pending_take_ids(self) -> tuple[str, ...]:
        """Return only trusted canonical UUID checkpoints ready for recovery."""

        return tuple(
            result.take_id
            for result in self.list_pending().journals
            if result.trusted
        )

    def _journal_directory(self, *, create: bool) -> Path | None:
        directory = self._directory
        if create:
            try:
                self.takes_root.mkdir(parents=True, exist_ok=True)
                directory.mkdir(mode=JOURNAL_DIRECTORY_MODE, exist_ok=True)
            except OSError as exc:
                raise RecordingManifestJournalError(
                    "Could not create the recording journal directory."
                ) from exc
        elif not directory.exists() and not directory.is_symlink():
            return None

        try:
            directory_stat = directory.lstat()
            resolved_directory = directory.resolve(strict=True)
        except OSError as exc:
            raise RecordingManifestJournalError(
                "The recording journal directory is not safe to use."
            ) from exc
        if not stat.S_ISDIR(directory_stat.st_mode) or resolved_directory != directory:
            raise RecordingManifestJournalError(
                "The recording journal directory is not safe to use."
            )
        try:
            os.chmod(directory, JOURNAL_DIRECTORY_MODE)
        except OSError as exc:
            raise RecordingManifestJournalError(
                "Could not secure the recording journal directory."
            ) from exc
        return directory


def _canonical_take_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecordingManifestJournalError(
            "Recording journals require a valid take UUID."
        ) from exc


def _take_id_from_journal_name(name: str) -> str:
    if not name.endswith(JOURNAL_FILE_SUFFIX):
        return ""
    stem = name[: -len(JOURNAL_FILE_SUFFIX)]
    try:
        canonical = _canonical_take_id(stem)
    except RecordingManifestJournalError:
        return ""
    return canonical if stem == canonical else ""


def _serialized_payload(take_id: str, evidence: SessionEvidence) -> bytes:
    if not isinstance(evidence, SessionEvidence):
        raise TypeError("Recording journal evidence must be a SessionEvidence instance.")
    if len(evidence.recovery_notes) > MAX_RECOVERY_NOTES:
        raise RecordingManifestJournalError("Too many recording recovery notes.")
    if len(evidence.timeline) > MAX_TIMELINE_EVENTS:
        raise RecordingManifestJournalError("Too many recording timeline events.")
    payload = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "take_id": take_id,
        "session": evidence.to_dict(),
    }
    try:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecordingManifestJournalError(
            "Recording evidence could not be serialized safely."
        ) from exc
    if len(encoded) > MAX_JOURNAL_BYTES:
        raise RecordingManifestJournalError("Recording evidence journal is too large.")
    return encoded


def _write_private_temporary(directory: Path, payload: bytes) -> Path:
    fd, temporary_name = tempfile.mkstemp(
        prefix=".recording-evidence-", suffix=".tmp", dir=str(directory)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, JOURNAL_FILE_MODE)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return temporary


def _read_private_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RecordingManifestJournalError("Recording journal is not a regular file.")
        if file_stat.st_mode & 0o777 != JOURNAL_FILE_MODE:
            raise RecordingManifestJournalError(
                "Recording journal permissions are not private."
            )
        if file_stat.st_size > MAX_JOURNAL_BYTES:
            raise RecordingManifestJournalError("Recording journal is too large.")
        chunks: list[bytes] = []
        remaining = MAX_JOURNAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_JOURNAL_BYTES:
            raise RecordingManifestJournalError("Recording journal is too large.")
        return data.decode("utf-8")
    finally:
        os.close(descriptor)


def _require_regular_file(path: Path) -> None:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise RecordingManifestJournalError("Recording journal is not a regular file.")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise RecordingManifestJournalError(
            "Could not make the recording journal durable."
        ) from exc
    finally:
        os.close(descriptor)


def _parse_payload(value: Any, expected_take_id: str) -> SessionEvidence:
    if not isinstance(value, Mapping):
        raise ValueError("Journal is not an object.")
    _require_keys(value, {"schema_version", "take_id", "session"})
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != JOURNAL_SCHEMA_VERSION:
        raise ValueError("Unsupported journal schema.")
    stored_take_id = value.get("take_id")
    if not isinstance(stored_take_id, str) or _canonical_take_id(stored_take_id) != expected_take_id:
        raise ValueError("Journal take identity does not match.")
    session = value.get("session")
    _validate_session_shape(session)
    try:
        return SessionEvidence.from_dict(session)
    except Exception as exc:
        raise ValueError("Journal session evidence is invalid.") from exc


def _validate_session_shape(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("Journal session is not an object.")
    allowed = {
        "protocol_version",
        "started_utc",
        "ended_utc",
        "host",
        "recovery_status",
        "recovery_notes",
        "timeline",
    }
    _require_known_keys(value, allowed)
    recovery_status = value.get("recovery_status")
    if not isinstance(recovery_status, str) or recovery_status not in {
        status.value for status in RecoveryStatus
    }:
        raise ValueError("Journal recovery state is missing.")
    for key in ("protocol_version", "started_utc", "ended_utc"):
        if key in value and not isinstance(value[key], str):
            raise ValueError("Journal text field is invalid.")
    host = value.get("host")
    if host is not None:
        if not isinstance(host, Mapping):
            raise ValueError("Journal host is invalid.")
        _require_known_keys(host, {"participant_id", "display_name"})
        if any(not isinstance(item, str) for item in host.values()):
            raise ValueError("Journal host field is invalid.")
    notes = value.get("recovery_notes")
    if notes is not None:
        if (
            not isinstance(notes, list)
            or len(notes) > MAX_RECOVERY_NOTES
            or any(not isinstance(item, str) for item in notes)
        ):
            raise ValueError("Journal recovery notes are invalid.")
    timeline = value.get("timeline")
    if timeline is not None:
        if not isinstance(timeline, list) or len(timeline) > MAX_TIMELINE_EVENTS:
            raise ValueError("Journal timeline is invalid.")
        for item in timeline:
            _validate_timeline_item(item)


def _validate_timeline_item(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("Journal timeline item is invalid.")
    _require_known_keys(
        value, {"event", "occurred_utc", "at_s", "participant_id", "detail"}
    )
    if not isinstance(value.get("event"), str):
        raise ValueError("Journal timeline event is invalid.")
    for key in ("occurred_utc", "participant_id", "detail"):
        if key in value and not isinstance(value[key], str):
            raise ValueError("Journal timeline text is invalid.")
    if "at_s" in value and (
        isinstance(value["at_s"], bool)
        or not isinstance(value["at_s"], (int, float))
    ):
        raise ValueError("Journal timeline position is invalid.")


def _require_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("Journal fields do not match the schema.")


def _require_known_keys(value: Mapping[str, Any], allowed: set[str]) -> None:
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise ValueError("Journal contains an untrusted field.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Journal contains duplicate fields.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Journal contains a non-finite number.")


def _untrusted_result(take_id: str) -> JournalLoadResult:
    evidence = SessionEvidence(
        recovery_status=RecoveryStatus.NEEDS_ATTENTION,
        recovery_notes=(
            "Recording evidence journal is unreadable; preserve local media and review recovery.",
        ),
    )
    return JournalLoadResult(
        take_id=take_id,
        evidence=evidence,
        trusted=False,
        error="journal_untrusted",
    )

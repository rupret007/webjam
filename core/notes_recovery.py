"""Bounded local Notes checkpoints; callers own recovery and save decisions.

The journal contains only canonical workspace keys, exact draft text, and an
optional fingerprint of the original notes. Reading a checkpoint never writes
an original, and writing one does not acknowledge a Notes save. A write checks
the caller's previous decoded snapshot before publishing a private atomic file.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from core.creative_modes import CREATOR_PROFILES
from core.file_io import atomic_write_bytes

MAX_RECOVERY_DRAFT_BYTES = 2 * 1024 * 1024
MAX_RECOVERY_FILE_BYTES = 16 * 1024 * 1024
_PROFILE_KEYS = tuple(profile.key for profile in CREATOR_PROFILES)
_SCHEMA_VERSION = 1


class NotesRecoveryError(ValueError):
    """The checkpoint or requested replacement cannot be used safely."""


class NotesRecoveryConflict(NotesRecoveryError):
    """The current checkpoint differs from the caller's expected drafts."""


def _text_bytes(text: str) -> bytes:
    if not isinstance(text, str):
        raise NotesRecoveryError("Notes recovery text must be a string.")
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NotesRecoveryError("Notes recovery text must be valid UTF-8.") from exc


def notes_fingerprint(text: str | None) -> str:
    """Fingerprint exact text, keeping a missing original distinct from empty."""
    value = b"missing" if text is None else b"text\0" + _text_bytes(text)
    return hashlib.sha256(b"webjam-notes-v1\0" + value).hexdigest()


@dataclass(frozen=True)
class NotesRecoveryDraft:
    text: str
    baseline_fingerprint: str | None

    def __post_init__(self) -> None:
        if len(_text_bytes(self.text)) > MAX_RECOVERY_DRAFT_BYTES:
            raise NotesRecoveryError("Notes recovery draft is too large.")
        fingerprint = self.baseline_fingerprint
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise NotesRecoveryError("Notes recovery fingerprint must be a lowercase SHA-256.")


def _validated_drafts(drafts: Mapping[str, NotesRecoveryDraft]) -> dict[str, NotesRecoveryDraft]:
    if not isinstance(drafts, Mapping) or len(drafts) > len(_PROFILE_KEYS):
        raise NotesRecoveryError("Notes recovery workspaces are invalid.")
    for key, draft in drafts.items():
        if not isinstance(key, str) or key not in _PROFILE_KEYS:
            raise NotesRecoveryError("Notes recovery workspace is unsupported.")
        if not isinstance(draft, NotesRecoveryDraft):
            raise NotesRecoveryError("Notes recovery entry must be a draft.")
        # Validate again at the persistence boundary, including values supplied
        # by callers that bypassed a frozen dataclass's normal construction.
        NotesRecoveryDraft(draft.text, draft.baseline_fingerprint)
    return {key: drafts[key] for key in _PROFILE_KEYS if key in drafts}


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NotesRecoveryError("Notes recovery contains duplicate fields.")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise NotesRecoveryError("Notes recovery contains a non-finite value.")


def _read_regular_bytes(path: Path) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise NotesRecoveryError("Notes recovery must be a regular file without symbolic links.")
    if before.st_size > MAX_RECOVERY_FILE_BYTES:
        raise NotesRecoveryError("Notes recovery file is too large.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise NotesRecoveryError("Notes recovery changed while it was being opened.")
        if info.st_size > MAX_RECOVERY_FILE_BYTES:
            raise NotesRecoveryError("Notes recovery file is too large.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(MAX_RECOVERY_FILE_BYTES + 1)
            final_size = os.fstat(handle.fileno()).st_size
        if len(data) > MAX_RECOVERY_FILE_BYTES or final_size > MAX_RECOVERY_FILE_BYTES:
            raise NotesRecoveryError("Notes recovery file is too large.")
        return data
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_notes_recovery(path: Path) -> dict[str, NotesRecoveryDraft]:
    """Read one strict checkpoint; missing means no retained drafts."""
    data = _read_regular_bytes(Path(path))
    if data is None:
        return {}
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise NotesRecoveryError("Notes recovery could not be decoded.") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "profiles"}
        or type(value["version"]) is not int
        or value["version"] != _SCHEMA_VERSION
        or not isinstance(value["profiles"], dict)
        or len(value["profiles"]) > len(_PROFILE_KEYS)
    ):
        raise NotesRecoveryError("Notes recovery schema is unsupported.")
    drafts = {}
    for key, record in value["profiles"].items():
        if key not in _PROFILE_KEYS:
            raise NotesRecoveryError("Notes recovery workspace is unsupported.")
        if not isinstance(record, dict) or set(record) != {"text", "baseline_fingerprint"}:
            raise NotesRecoveryError("Notes recovery draft fields are invalid.")
        drafts[key] = NotesRecoveryDraft(record["text"], record["baseline_fingerprint"])
    return _validated_drafts(drafts)


def write_notes_recovery(
    path: Path,
    drafts: Mapping[str, NotesRecoveryDraft],
    *,
    expected: Mapping[str, NotesRecoveryDraft],
) -> None:
    """Replace only the expected decoded checkpoint, without truncating drafts.

    I/O errors propagate unchanged. In particular, a failed directory fsync can
    occur after publication; callers must not treat that exception as proof
    that the previous checkpoint remains on disk.
    """
    validated = _validated_drafts(drafts)
    previous = _validated_drafts(expected)
    payload = {
        "version": _SCHEMA_VERSION,
        "profiles": {
            key: {"text": draft.text, "baseline_fingerprint": draft.baseline_fingerprint}
            for key, draft in validated.items()
        },
    }
    data = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_RECOVERY_FILE_BYTES:
        raise NotesRecoveryError("Notes recovery file is too large.")
    target = Path(path)
    if read_notes_recovery(target) != previous:
        raise NotesRecoveryConflict("Notes recovery changed since it was read.")
    atomic_write_bytes(target, data, mode=0o600)


__all__ = [
    "MAX_RECOVERY_DRAFT_BYTES",
    "MAX_RECOVERY_FILE_BYTES",
    "NotesRecoveryConflict",
    "NotesRecoveryDraft",
    "NotesRecoveryError",
    "notes_fingerprint",
    "read_notes_recovery",
    "write_notes_recovery",
]

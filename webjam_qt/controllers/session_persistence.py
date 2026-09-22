"""Session metadata persistence — profile-scoped title, mode, and local notes.

Owns the small bit of state that must survive an app restart:

* fixed profile note files   — free-form local session canvas notes
* ``~/.webjam_session.json`` — profile-keyed title and compatibility mode
* private recovery checkpoint — exact retained drafts for review after restart

Notes retain failed drafts per profile and report a bounded local save state.
Metadata remains best-effort. Atomic writes prevent half-written files; only
a successful save reports that notes are saved on this computer.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.creative_modes import (
    CREATOR_PROFILES,
    canonical_creator_profile_key,
    get_mode_by_key,
)
from core.file_io import atomic_write_text
from core.notes_recovery import (
    MAX_RECOVERY_DRAFT_BYTES,
    NotesRecoveryDraft,
    notes_fingerprint,
    read_notes_recovery,
    write_notes_recovery,
)

_NOTES_FILE = ".webjam_notes.md"
_PROFILE_NOTES_FILES = {
    "music": _NOTES_FILE,
    "podcast_voice": ".webjam_notes.podcast_voice.md",
    "review_rehearsal": ".webjam_notes.review_rehearsal.md",
    "art": ".webjam_notes.art.md",
}
_SESSION_FILE = ".webjam_session.json"
_NOTES_RECOVERY_FILE = ".webjam_notes.recovery.json"
_SESSION_SCHEMA_VERSION = 2
_MAX_SESSION_FILE_BYTES = 64 * 1024
_MAX_NOTES_FILE_BYTES = 1024 * 1024
_MAX_TITLE_BYTES = 512
_MAX_MODE_KEY_BYTES = 64
_PROFILE_ORDER = tuple(profile.key for profile in CREATOR_PROFILES)

if set(_PROFILE_NOTES_FILES) != set(_PROFILE_ORDER):
    # A profile without its own scratchpad path would silently write another
    # profile's notes file, so refuse to start instead.
    raise RuntimeError("Every creator profile requires a private notes file.")


def notes_save_failure_state(error: Exception) -> str:
    """Classify known filesystem failures without projecting error text or paths."""
    if not isinstance(error, OSError):
        return "failed"
    code = error.errno
    if code == errno.ENOSPC or (hasattr(errno, "EDQUOT") and code == errno.EDQUOT):
        return "disk_full"
    if code in {errno.EACCES, errno.EPERM}:
        return "permission_denied"
    if code == errno.EROFS:
        return "read_only"
    return "failed"


@dataclass(frozen=True)
class NotesOriginalSnapshot:
    """One readable original, bound to the workspace and reviewed draft."""

    profile_key: str
    text: str
    fingerprint: str
    draft_fingerprint: str


def _persistence_home() -> Path:
    """Return the trusted root for profile notes and session metadata."""

    return Path.home()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Session metadata contains duplicate fields.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Session metadata contains a non-finite value.")


def _read_bounded_json(path: Path) -> object | None:
    """Read one fixed metadata path without following links or unbounded input."""

    if path.is_symlink():
        raise ValueError("Session metadata cannot be a symbolic link.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SESSION_FILE_BYTES:
            raise ValueError("Session metadata is not a bounded regular file.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_MAX_SESSION_FILE_BYTES + 1)
        if len(raw) > _MAX_SESSION_FILE_BYTES:
            raise ValueError("Session metadata is too large.")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_bounded_notes(path: Path) -> str | None:
    """Read one fixed local-notes file without links or unbounded input."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_NOTES_FILE_BYTES:
        raise ValueError("Session notes are not a bounded regular file.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(current.st_mode)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
                or info.st_size > _MAX_NOTES_FILE_BYTES):
            raise ValueError("Session notes are not a bounded regular file.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_MAX_NOTES_FILE_BYTES + 1)
        if len(raw) > _MAX_NOTES_FILE_BYTES:
            raise ValueError("Session notes are too large.")
        return raw.decode("utf-8")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _bounded_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Session title must be text.")
    title = " ".join(value.split())
    if len(title.encode("utf-8")) > _MAX_TITLE_BYTES:
        raise ValueError("Session title is too long.")
    return title


def _bounded_mode_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Session mode must be text.")
    key = value.strip()
    if (
        not key
        or len(key.encode("utf-8")) > _MAX_MODE_KEY_BYTES
        or get_mode_by_key(key) is None
    ):
        raise ValueError("Session mode is unsupported.")
    return key


def _profile_record(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value or not set(value) <= {
        "title",
        "mode",
    }:
        raise ValueError("Creator session metadata fields are invalid.")
    record: dict[str, str] = {}
    if "title" in value:
        title = _bounded_title(value["title"])
        if title:
            record["title"] = title
    if "mode" in value:
        record["mode"] = _bounded_mode_key(value["mode"])
    if not record:
        raise ValueError("Creator session metadata is empty.")
    return record


def _profile_records(value: object) -> dict[str, dict[str, str]]:
    """Decode current metadata or migrate the exact legacy single-profile shape."""

    if not isinstance(value, dict) or not value:
        raise ValueError("Session metadata root is invalid.")
    keys = set(value)
    if keys <= {"title", "mode"}:
        # v1 had one global title/mode. It represented the only then-supported
        # Music workflow and must never bleed into Podcast or Review.
        return {"music": _profile_record(value)}
    if keys != {"schema_version", "profiles"}:
        raise ValueError("Session metadata fields do not match the schema.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("Session metadata schema is unsupported.")
    raw_profiles = value["profiles"]
    if not isinstance(raw_profiles, dict) or len(raw_profiles) > len(_PROFILE_ORDER):
        raise ValueError("Creator session profiles are invalid.")
    records: dict[str, dict[str, str]] = {}
    for raw_key, raw_record in raw_profiles.items():
        canonical = canonical_creator_profile_key(raw_key)
        if canonical is None or canonical in records:
            raise ValueError("Creator session profile is unsupported.")
        records[canonical] = _profile_record(raw_record)
    return records


def _load_profile_records(path: Path) -> dict[str, dict[str, str]]:
    value = _read_bounded_json(path)
    return {} if value is None else _profile_records(value)


class SessionPersistence:
    """Loads and saves the session title, mode, and notes for a single window."""

    def __init__(
        self,
        session_strip,
        session_canvas,
        logger: logging.Logger | None = None,
        *,
        creator_profile_key: object = "music",
    ) -> None:
        self._strip = session_strip
        self._canvas = session_canvas
        self._creator_profile_key = "music"
        self.set_profile_key(creator_profile_key)
        # The fallback stays inside the ``webjam`` namespace so session
        # titles never reach an unredacted root-logger handler.
        self._log = logger or logging.getLogger(
            "webjam.qt.session_persistence"
        )
        # A joined session shows the name whoever sent the invitation chose.
        # That is not the musician's own title, so it must never overwrite
        # the one on disk -- otherwise a guest session's name follows them
        # into every later jam, including one they host themselves.
        self._borrowed_title: str | None = None
        self._pending_notes: dict[str, str] = {}
        self._settled_notes: dict[str, str] = {}
        # A failed atomic write may already have replaced the destination
        # before directory fsync failed. Even Undo to the old saved text must
        # be written again before it can be acknowledged as saved.
        self._unconfirmed_notes: set[str] = set()
        # An atomic write can publish before its directory sync fails. Keep
        # only the before/attempted identities, never trust a later reread as
        # proof that an external edit was ours.
        self._notes_write_fingerprints: dict[str, frozenset[str]] = {}
        self._notes_save_failures: dict[str, str] = {}
        self._unreadable_notes: set[str] = set()
        self._exported_profiles: set[str] = set()
        self._notes_save_state = "saved"
        # Original identity is independent of settled/exported text: exporting
        # a draft deliberately does not replace the original notes file.
        self._notes_baselines: dict[str, str | None] = {}
        self._recovered_notes: set[str] = set()
        self._recovery_conflicts: set[str] = set()
        self._recovery_loaded = False
        self._recovery_checkpoint: dict[str, NotesRecoveryDraft] = {}
        self._checkpoint_confirmed = True
        self._checkpoint_blocked = False
        set_export_handler = getattr(self._canvas, "set_notes_export_handler", None)
        if callable(set_export_handler):
            set_export_handler(self.export_notes_copy)

    @property
    def profile_key(self) -> str:
        """Return the canonical profile whose title/mode this instance owns."""

        return self._creator_profile_key

    def set_profile_key(self, creator_profile_key: object) -> str:
        """Select one canonical metadata namespace, safely defaulting to Music."""

        canonical = canonical_creator_profile_key(creator_profile_key) or "music"
        self._creator_profile_key = canonical
        return canonical

    def switch_profile_key(self, creator_profile_key: object) -> str:
        """Retain any unsaved draft before loading the target profile's notes.

        A guest must still adopt the host's profile when a disk is full. Its
        previous notes stay local in this bounded set of profile namespaces.
        """

        canonical = canonical_creator_profile_key(creator_profile_key) or "music"
        if canonical == self._creator_profile_key:
            return canonical
        self._save_notes_only()
        self._creator_profile_key = canonical
        self._load_notes_only(clear_missing=True)
        return canonical

    def _notes_path(self) -> Path:
        return _persistence_home() / _PROFILE_NOTES_FILES[self._creator_profile_key]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Restore notes + session metadata from disk (best-effort)."""
        self._load_notes_only()
        self._load_session_metadata()

    def save(self) -> None:
        """Persist notes + session metadata to disk (best-effort)."""
        self._save_notes_only()
        self._save_session_metadata()

    def mark_title_borrowed(self, title: str) -> None:
        """Note that the visible title came from an invitation, not the user."""

        self._borrowed_title = " ".join(str(title or "").split()) or None

    def clear_borrowed_title(self) -> None:
        """The musician has made the visible title their own."""

        self._borrowed_title = None

    def save_title_and_mode(self) -> None:
        """Persist only the session metadata (title + mode)."""
        self._save_session_metadata()

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------
    def _load_notes_recovery(self) -> None:
        """Offer every retained workspace after restart without applying it."""
        if self._recovery_loaded:
            return
        self._recovery_loaded = True
        try:
            checkpoint = read_notes_recovery(_persistence_home() / _NOTES_RECOVERY_FILE)
        except (OSError, ValueError) as exc:
            # An unreadable/unsupported recovery file may hold the only copy.
            # Preserve it, while still allowing ordinary notes saves to work.
            self._checkpoint_blocked = True
            self._checkpoint_confirmed = False
            self._log.debug("Could not load notes recovery; error_type=%s", type(exc).__name__)
            return
        self._recovery_checkpoint = checkpoint
        for profile, draft in checkpoint.items():
            self._notes_baselines[profile] = draft.baseline_fingerprint
            try:
                original = _read_bounded_notes(_persistence_home() / _PROFILE_NOTES_FILES[profile])
                self._settled_notes[profile] = original or ""
                if original == draft.text:
                    # A primary save may have succeeded just before recovery
                    # cleanup failed. Its exact bytes are already present.
                    self._notes_baselines[profile] = notes_fingerprint(original)
                    continue
                if notes_fingerprint(original) != draft.baseline_fingerprint:
                    self._recovery_conflicts.add(profile)
            except (OSError, ValueError):
                self._unreadable_notes.add(profile)
                self._settled_notes[profile] = ""
            self._pending_notes[profile] = draft.text
            self._recovered_notes.add(profile)

    def _capture_notes_baseline(self, profile: str) -> None:
        if profile in self._notes_baselines:
            return
        try:
            original = _read_bounded_notes(_persistence_home() / _PROFILE_NOTES_FILES[profile])
            self._notes_baselines[profile] = notes_fingerprint(original)
        except (OSError, ValueError):
            self._notes_baselines[profile] = None
            self._unreadable_notes.add(profile)

    def _checkpoint_notes(self) -> None:
        """Checkpoint on save boundaries; never claim an unconfirmed copy."""
        if self._checkpoint_blocked:
            return
        desired = {}
        for profile, text in self._pending_notes.items():
            self._capture_notes_baseline(profile)
            if len(text.encode("utf-8")) > MAX_RECOVERY_DRAFT_BYTES:
                # Keep the previous good revision rather than truncate it or
                # erase another workspace's recovery because this one is long.
                if profile in self._recovery_checkpoint:
                    desired[profile] = self._recovery_checkpoint[profile]
                continue
            desired[profile] = NotesRecoveryDraft(text, self._notes_baselines[profile])
        if desired == self._recovery_checkpoint and self._checkpoint_confirmed:
            return
        path = _persistence_home() / _NOTES_RECOVERY_FILE
        previous = self._recovery_checkpoint
        try:
            write_notes_recovery(path, desired, expected=previous)
        except (OSError, ValueError) as exc:
            self._checkpoint_confirmed = False
            # Like a primary save, publication may precede a failed directory
            # sync. Recognize our own published bytes but rewrite on retry;
            # observing them does not prove the failed sync succeeded.
            try:
                observed = read_notes_recovery(path)
                if observed == previous or observed == desired:
                    self._recovery_checkpoint = observed
                else:
                    self._checkpoint_blocked = True
            except (OSError, ValueError):
                self._checkpoint_blocked = True
            self._log.debug("Could not checkpoint notes; error_type=%s", type(exc).__name__)
            return
        self._recovery_checkpoint = desired
        self._checkpoint_confirmed = True

    def notes_restart_recovery_state(self, profile: str) -> str:
        """Whether the current exact draft has a confirmed restart checkpoint."""
        draft = self._recovery_checkpoint.get(profile)
        if (not self._checkpoint_blocked and self._checkpoint_confirmed
                and draft is not None and profile in self._pending_notes
                and draft.text == self._pending_notes[profile]):
            return "confirmed"
        return "unconfirmed"

    def notes_recovery_requires_review(self, profile: str) -> bool:
        return profile in self._recovered_notes

    def _load_notes_only(self, *, clear_missing: bool = False) -> None:
        self._load_notes_recovery()
        profile = self._creator_profile_key
        if profile in self._pending_notes:
            self._canvas.restore_notes(self._pending_notes[profile])
        elif profile in self._exported_profiles:
            self._canvas.restore_notes(self._settled_notes[profile])
        else:
            try:
                text = _read_bounded_notes(self._notes_path())
                self._unreadable_notes.discard(profile)
                self._settled_notes[profile] = text or ""
                self._notes_baselines[profile] = notes_fingerprint(text)
                if text is not None or clear_missing:
                    self._canvas.restore_notes(text or "")
            except Exception as exc:  # noqa: BLE001 - preserve rejected originals
                self._unreadable_notes.add(profile)
                self._settled_notes[profile] = ""
                self._notes_baselines[profile] = None
                if clear_missing:
                    self._canvas.restore_notes("")
                else:
                    self._retain_notes(profile, self._canvas.current_notes())
                self._log.debug("Could not load notes; error_type=%s", type(exc).__name__)
        self._refresh_notes_state()

    @property
    def unreadable_notes_profile(self) -> str | None:
        """The active unavailable original that has no draft to replace."""
        profile = self._creator_profile_key
        if (profile in self._unreadable_notes and profile not in self._pending_notes
                and not self._canvas.current_notes()):
            return profile
        return None

    def reload_unreadable_notes(self, profile: str) -> bool:
        """Reopen an unavailable original only while its active editor is empty."""
        def still_empty() -> bool:
            return profile in _PROFILE_NOTES_FILES and profile == self.unreadable_notes_profile

        if not still_empty():
            return False
        try:
            text = _read_bounded_notes(_persistence_home() / _PROFILE_NOTES_FILES[profile])
        except (OSError, ValueError) as exc:
            self._log.debug("Could not recheck notes; error_type=%s", type(exc).__name__)
            return False
        # Missing notes are still unavailable, not a confirmed empty original.
        # Recheck context after IO so a later edit or workspace change wins.
        if text is None or not still_empty():
            return False
        self._unreadable_notes.discard(profile)
        self._settled_notes[profile] = text
        self._notes_baselines[profile] = notes_fingerprint(text)
        self._canvas.restore_notes(text)
        self._refresh_notes_state()
        return True

    @property
    def has_unsaved_notes(self) -> bool:
        return bool(self._pending_notes)

    @property
    def notes_save_state(self) -> str:
        return self._notes_save_state

    @property
    def unsaved_notes(self) -> tuple[tuple[str, str], ...]:
        """Local drafts only; never part of session or invitation projections."""
        return tuple(self._pending_notes.items())

    @property
    def notes_recovery_summary(self) -> tuple[tuple[str, str], ...]:
        """Stable workspace/reason pairs for local UI; no draft bytes or paths."""
        summary = []
        for profile in _PROFILE_ORDER:
            if profile not in self._pending_notes:
                continue
            state = self.notes_recovery_state(profile)
            if (state == "failed" and profile not in self._notes_save_failures
                    and profile not in self._unconfirmed_notes):
                state = "pending"
            summary.append((profile, state))
        return tuple(summary)

    def _notify_notes_state(self, state: str) -> None:
        self._notes_save_state = state
        set_unavailable = getattr(self._canvas, "set_notes_original_unavailable", None)
        if callable(set_unavailable):
            set_unavailable(self.unreadable_notes_profile)
        set_context = getattr(self._canvas, "set_notes_recovery_context", None)
        if callable(set_context):
            set_context(self._creator_profile_key, self.notes_recovery_summary)
        setter = getattr(self._canvas, "set_notes_save_state", None)
        if callable(setter):
            setter(state)

    def _refresh_notes_state(self) -> None:
        if any(profile in self._unreadable_notes for profile in self._pending_notes):
            state = "protected_original"
        elif self._recovery_conflicts & self._pending_notes.keys():
            state = "recovery_conflict"
        elif any(len(text.encode("utf-8")) > _MAX_NOTES_FILE_BYTES
               for text in self._pending_notes.values()):
            state = "too_large"
        elif self._recovered_notes & self._pending_notes.keys():
            state = "recovered"
        elif self._pending_notes:
            state = "failed"
        elif self._creator_profile_key in self._exported_profiles:
            state = "exported"
        elif self._creator_profile_key in self._unreadable_notes:
            state = "unreadable"
        elif self._checkpoint_blocked:
            state = "recovery_unavailable"
        else:
            state = "saved"
        self._notify_notes_state(state)

    def notes_recovery_state(self, profile: str) -> str:
        """Return the bounded reason for one retained workspace draft."""
        if profile in self._unreadable_notes:
            return "protected_original"
        if profile in self._recovery_conflicts:
            return "recovery_conflict"
        text = self._pending_notes.get(profile, "")
        if len(text.encode("utf-8")) > _MAX_NOTES_FILE_BYTES:
            return "too_large"
        if profile in self._recovered_notes:
            return self._notes_save_failures.get(profile, "recovered")
        return self._notes_save_failures.get(profile, "failed")

    def _retain_notes(self, profile: str, text: str) -> None:
        if (profile not in self._unconfirmed_notes and profile not in self._recovered_notes
                and profile not in self._recovery_conflicts
                and text == self._settled_notes.get(profile, "")):
            self._pending_notes.pop(profile, None)
            self._notes_save_failures.pop(profile, None)
        else:
            self._pending_notes[profile] = text

    def notes_changed(self, text: str) -> None:
        """Keep the current draft before an eventual debounced disk write."""
        self._load_notes_recovery()
        self._retain_notes(self._creator_profile_key, text)
        self._notify_notes_state("pending")

    def _save_notes_only(self) -> bool:
        """Save changed drafts; preserve rejected originals and failed writes."""
        self._load_notes_recovery()
        self._retain_notes(self._creator_profile_key, self._canvas.current_notes())
        self._checkpoint_notes()
        for profile, text in tuple(self._pending_notes.items()):
            try:
                # A blocked recovery journal must not bypass original checks.
                self._capture_notes_baseline(profile)
                if (profile in self._unreadable_notes or profile in self._recovered_notes
                        or profile in self._recovery_conflicts
                        or len(text.encode("utf-8")) > _MAX_NOTES_FILE_BYTES):
                    continue
                path = _persistence_home() / _PROFILE_NOTES_FILES[profile]
                try:
                    original = _read_bounded_notes(path)
                except ValueError:
                    self._unreadable_notes.add(profile)
                    raise
                observed = notes_fingerprint(original)
                allowed = self._notes_write_fingerprints.get(profile, frozenset())
                if observed != self._notes_baselines[profile] and observed not in allowed:
                    self._recovery_conflicts.add(profile)
                    continue
                self._notes_write_fingerprints[profile] = frozenset((
                    observed, notes_fingerprint(text),
                ))
                self._unconfirmed_notes.add(profile)
                atomic_write_text(path, text, mode=0o600)
                self._unconfirmed_notes.discard(profile)
                self._notes_write_fingerprints.pop(profile, None)
                self._notes_save_failures.pop(profile, None)
                self._settled_notes[profile] = text
                self._notes_baselines[profile] = notes_fingerprint(text)
                self._pending_notes.pop(profile, None)
                self._exported_profiles.discard(profile)
            except Exception as exc:  # noqa: BLE001 - keep the draft for retry
                self._notes_save_failures[profile] = notes_save_failure_state(exc)
                self._log.debug("Could not save notes; error_type=%s", type(exc).__name__)
        self._checkpoint_notes()
        self._refresh_notes_state()
        return not self._pending_notes

    def save_recovered_notes(self, profile: str, expected: str) -> bool:
        """Explicitly save a reviewed draft while its original still matches."""
        if profile not in self._recovered_notes or self._pending_notes.get(profile) != expected:
            return False
        if len(expected.encode("utf-8")) > _MAX_NOTES_FILE_BYTES:
            return False
        self._checkpoint_notes()
        if self._pending_notes.get(profile) != expected:
            return False
        path = _persistence_home() / _PROFILE_NOTES_FILES[profile]
        try:
            original = _read_bounded_notes(path)
            observed = notes_fingerprint(original)
            allowed = self._notes_write_fingerprints.get(profile, frozenset())
            if (observed != self._notes_baselines.get(profile)
                    and original != expected and observed not in allowed):
                self._recovery_conflicts.add(profile)
                self._refresh_notes_state()
                return False
            self._notes_write_fingerprints[profile] = frozenset((
                observed, notes_fingerprint(expected),
            ))
            self._unconfirmed_notes.add(profile)
            atomic_write_text(path, expected, mode=0o600)
        except (OSError, ValueError) as exc:
            self._notes_save_failures[profile] = notes_save_failure_state(exc)
            self._refresh_notes_state()
            return False
        self._notes_baselines[profile] = notes_fingerprint(expected)
        self._settled_notes[profile] = expected
        self._unconfirmed_notes.discard(profile)
        self._notes_write_fingerprints.pop(profile, None)
        if self._pending_notes.get(profile) != expected:
            self._refresh_notes_state()
            return False
        self._pending_notes.pop(profile)
        self._recovered_notes.discard(profile)
        self._recovery_conflicts.discard(profile)
        self._unreadable_notes.discard(profile)
        self._notes_save_failures.pop(profile, None)
        self._exported_profiles.discard(profile)
        self._checkpoint_notes()
        self._refresh_notes_state()
        return True

    def revise_pending_notes(self, profile: str, expected: str, text: str) -> bool:
        """Edit a retained draft without switching the active session profile."""
        if self._pending_notes.get(profile) != expected:
            return False
        self._retain_notes(profile, text)
        if profile == self._creator_profile_key:
            self._canvas.restore_notes(text)
        self._checkpoint_notes()
        self._refresh_notes_state()
        return True

    def recheck_notes_original(self, profile: str, expected: str) -> NotesOriginalSnapshot | None:
        """Read a protected original for review without changing either copy."""
        if (profile not in _PROFILE_NOTES_FILES
                or self._pending_notes.get(profile) != expected
                or profile not in self._unreadable_notes | self._recovery_conflicts):
            return None
        try:
            text = _read_bounded_notes(_persistence_home() / _PROFILE_NOTES_FILES[profile])
        except (OSError, ValueError):
            return None
        if text is None or self._pending_notes.get(profile) != expected:
            return None
        return NotesOriginalSnapshot(
            profile, text, notes_fingerprint(text), notes_fingerprint(expected),
        )

    def export_draft_and_use_original(
        self, profile: str, expected: str, original: NotesOriginalSnapshot, path: str,
    ) -> bool:
        """Keep a durable draft copy before adopting an unchanged saved original."""
        if (not path or not isinstance(original, NotesOriginalSnapshot)
                or profile not in _PROFILE_NOTES_FILES
                or original.profile_key != profile
                or original.draft_fingerprint != notes_fingerprint(expected)
                or original.fingerprint != notes_fingerprint(original.text)
                or self._pending_notes.get(profile) != expected):
            return False
        saved_path = _persistence_home() / _PROFILE_NOTES_FILES[profile]

        def still_matches() -> bool:
            try:
                saved = _read_bounded_notes(saved_path)
            except (OSError, ValueError):
                return False
            return (
                self._pending_notes.get(profile) == expected
                and saved is not None and notes_fingerprint(saved) == original.fingerprint
            )

        if not still_matches():
            return False
        # Export without acknowledging the pending draft yet. A file picker
        # or writer may have allowed a newer draft or external original to
        # arrive; neither can be acknowledged with these older bytes.
        self.export_notes_copy(expected, path)
        if not still_matches():
            return False
        self._settled_notes[profile] = original.text
        self._notes_baselines[profile] = original.fingerprint
        self._pending_notes.pop(profile)
        self._unconfirmed_notes.discard(profile)
        self._notes_write_fingerprints.pop(profile, None)
        self._recovered_notes.discard(profile)
        self._recovery_conflicts.discard(profile)
        self._unreadable_notes.discard(profile)
        self._notes_save_failures.pop(profile, None)
        self._exported_profiles.discard(profile)
        if profile == self._creator_profile_key:
            self._canvas.restore_notes(original.text)
        self._checkpoint_notes()
        self._refresh_notes_state()
        return True

    def export_pending_notes(self, profile: str, expected: str, path: str) -> bool:
        """Acknowledge only an exact draft successfully saved to a chosen copy."""
        if self._pending_notes.get(profile) != expected:
            return False
        self.export_notes_copy(expected, path)
        self._unconfirmed_notes.discard(profile)
        # A copy does not settle an ambiguous primary publication. Remember
        # those bounded identities so the next edit can safely retry there.
        self._notes_save_failures.pop(profile, None)
        self._settled_notes[profile] = expected
        self._exported_profiles.add(profile)
        self._pending_notes.pop(profile, None)
        self._recovered_notes.discard(profile)
        self._recovery_conflicts.discard(profile)
        self._checkpoint_notes()
        self._refresh_notes_state()
        return True

    def export_notes_copy(self, text: str, path: str) -> None:
        """Export local recovery text without overwriting a profile original."""
        destination = Path(path)
        home = _persistence_home()
        names = (*_PROFILE_NOTES_FILES.values(), _NOTES_RECOVERY_FILE)

        def same_existing_file(left: Path, right: Path) -> bool:
            try:
                return left.samefile(right)
            except FileNotFoundError:
                return False

        # resolve() alone preserves case on APFS and cannot identify hardlinks.
        # Reserve this namespace even before a primary exists; trailing dots
        # and spaces may also alias a basename on Windows.
        in_notes_home = (
            destination.parent.resolve() == home.resolve()
            or same_existing_file(destination.parent, home)
        )
        reserved_name = destination.name.rstrip(" .").casefold() in {
            name.casefold() for name in names
        }
        if (destination.is_symlink() or (in_notes_home and reserved_name)
                or any(same_existing_file(destination, home / name) for name in names)):
            raise ValueError("Choose a separate notes file for this copy.")
        atomic_write_text(destination, text, mode=0o600)

    # ------------------------------------------------------------------
    # Session metadata (title + mode)
    # ------------------------------------------------------------------
    def _load_session_metadata(self) -> None:
        path = _persistence_home() / _SESSION_FILE
        try:
            record = _load_profile_records(path).get(self._creator_profile_key)
            if record is None:
                return
            title = record.get("title")
            if title:
                # Set the QLineEdit directly so we don't fire editingFinished.
                self._strip._title_input.setText(title)
            mode_key = record.get("mode")
            if mode_key:
                picker = self._strip._mode_picker
                idx = picker.findData(mode_key)
                if idx >= 0:
                    picker.setCurrentIndex(idx)
        except Exception:  # noqa: BLE001
            self._log.debug("Could not load session metadata", exc_info=True)

    def _stored_title(self) -> str:
        """Return this profile's title so a borrowed one can be left alone."""

        try:
            records = _load_profile_records(_persistence_home() / _SESSION_FILE)
        except Exception:  # noqa: BLE001 - absent or unreadable is fine
            return ""
        return records.get(self._creator_profile_key, {}).get("title", "")

    def _save_session_metadata(self) -> None:
        try:
            path = _persistence_home() / _SESSION_FILE
            try:
                records = _load_profile_records(path)
            except Exception:  # noqa: BLE001 - replace corrupt metadata safely
                records = {}
            title = _bounded_title(self._strip.current_title())
            if (
                self._borrowed_title
                and " ".join(title.split()) == self._borrowed_title
            ):
                # Keep the musician's own title rather than adopting the
                # invitation's name as their default.
                title = self._stored_title()
            raw_mode_key = self._strip.current_mode_key()
            mode_key = _bounded_mode_key(raw_mode_key) if raw_mode_key else ""
            record: dict[str, str] = {}
            if title:
                record["title"] = title
            if mode_key:
                record["mode"] = mode_key
            if not record:
                return
            records[self._creator_profile_key] = record
            ordered_profiles = {
                profile_key: records[profile_key]
                for profile_key in _PROFILE_ORDER
                if profile_key in records
            }
            payload = {
                "schema_version": _SESSION_SCHEMA_VERSION,
                "profiles": ordered_profiles,
            }
            atomic_write_text(
                path,
                json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
                mode=0o600,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Could not save session metadata", exc_info=True)

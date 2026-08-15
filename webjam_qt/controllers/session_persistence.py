"""Session metadata persistence — profile-scoped title, mode, and local notes.

Owns the small bit of state that must survive an app restart:

* fixed profile note files   — free-form local session canvas notes
* ``~/.webjam_session.json`` — profile-keyed title and compatibility mode

Best-effort: filesystem errors are logged at ``debug`` and otherwise
swallowed.  Atomic writes (via ``core.file_io.atomic_write_text``) prevent
half-written files from corrupting state on crash.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from core.creative_modes import (
    CREATOR_PROFILES,
    canonical_creator_profile_key,
    get_mode_by_key,
)
from core.file_io import atomic_write_text

_NOTES_FILE = ".webjam_notes.md"
_PROFILE_NOTES_FILES = {
    "music": _NOTES_FILE,
    "podcast_voice": ".webjam_notes.podcast_voice.md",
    "review_rehearsal": ".webjam_notes.review_rehearsal.md",
}
_SESSION_FILE = ".webjam_session.json"
_SESSION_SCHEMA_VERSION = 2
_MAX_SESSION_FILE_BYTES = 64 * 1024
_MAX_NOTES_FILE_BYTES = 1024 * 1024
_MAX_TITLE_BYTES = 512
_MAX_MODE_KEY_BYTES = 64
_PROFILE_ORDER = tuple(profile.key for profile in CREATOR_PROFILES)


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

    if path.is_symlink():
        raise ValueError("Session notes cannot be a symbolic link.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_NOTES_FILE_BYTES:
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
        logger: Optional[logging.Logger] = None,
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
        """Save the current scratchpad, then load the target profile's notes."""

        canonical = canonical_creator_profile_key(creator_profile_key) or "music"
        if canonical == self._creator_profile_key:
            return canonical
        self._save_notes_only()
        self._creator_profile_key = canonical
        self._load_notes_only(clear_missing=True)
        return canonical

    def _notes_path(self) -> Path:
        return Path.home() / _PROFILE_NOTES_FILES[self._creator_profile_key]

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
    def _load_notes_only(self, *, clear_missing: bool = False) -> None:
        notes_path = self._notes_path()
        try:
            text = _read_bounded_notes(notes_path)
            if text is None:
                if clear_missing:
                    self._canvas.set_notes("")
                return
            self._canvas.set_notes(text)
        except Exception:  # noqa: BLE001
            if clear_missing:
                # A rejected target file must never leave the previous
                # profile's scratchpad visible under the new profile key.
                self._canvas.set_notes("")
            self._log.debug("Could not load session notes", exc_info=True)

    def _save_notes_only(self) -> None:
        try:
            text = self._canvas.current_notes()
            notes_path = self._notes_path()
            # Do not create an empty scratchpad in a clean profile, but once a
            # note file exists an explicit Clear must stay cleared after restart.
            if text or notes_path.exists():
                atomic_write_text(notes_path, text, mode=0o600)
        except Exception:  # noqa: BLE001
            self._log.debug("Could not save session notes", exc_info=True)

    # ------------------------------------------------------------------
    # Session metadata (title + mode)
    # ------------------------------------------------------------------
    def _load_session_metadata(self) -> None:
        path = Path.home() / _SESSION_FILE
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
            records = _load_profile_records(Path.home() / _SESSION_FILE)
        except Exception:  # noqa: BLE001 - absent or unreadable is fine
            return ""
        return records.get(self._creator_profile_key, {}).get("title", "")

    def _save_session_metadata(self) -> None:
        try:
            path = Path.home() / _SESSION_FILE
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

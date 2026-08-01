"""Session metadata persistence — title, mode, and notes.

Owns the small bit of state that must survive an app restart:

* ``~/.webjam_notes.md``     — free-form session canvas notes
* ``~/.webjam_session.json`` — session title and last-used mode key

Best-effort: filesystem errors are logged at ``debug`` and otherwise
swallowed.  Atomic writes (via ``core.file_io.atomic_write_text``) prevent
half-written files from corrupting state on crash.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from core.file_io import atomic_write_text

_NOTES_FILE = ".webjam_notes.md"
_SESSION_FILE = ".webjam_session.json"


class SessionPersistence:
    """Loads and saves the session title, mode, and notes for a single window."""

    def __init__(self, session_strip, session_canvas, logger: Optional[logging.Logger] = None) -> None:
        self._strip = session_strip
        self._canvas = session_canvas
        self._log = logger or logging.getLogger(__name__)
        # A joined session shows the name whoever sent the invitation chose.
        # That is not the musician's own title, so it must never overwrite
        # the one on disk -- otherwise a guest session's name follows them
        # into every later jam, including one they host themselves.
        self._borrowed_title: str | None = None

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
    def _load_notes_only(self) -> None:
        notes_path = Path.home() / _NOTES_FILE
        if not notes_path.exists():
            return
        try:
            text = notes_path.read_text(encoding="utf-8")
            self._canvas.set_notes(text)
        except Exception:  # noqa: BLE001
            self._log.debug("Could not load session notes", exc_info=True)

    def _save_notes_only(self) -> None:
        try:
            text = self._canvas.current_notes()
            if text.strip():
                atomic_write_text(Path.home() / _NOTES_FILE, text)
        except Exception:  # noqa: BLE001
            self._log.debug("Could not save session notes", exc_info=True)

    # ------------------------------------------------------------------
    # Session metadata (title + mode)
    # ------------------------------------------------------------------
    def _load_session_metadata(self) -> None:
        path = Path.home() / _SESSION_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            title = data.get("title")
            if isinstance(title, str) and title.strip():
                # Set the QLineEdit directly so we don't fire editingFinished.
                self._strip._title_input.setText(title)
            mode_key = data.get("mode")
            if isinstance(mode_key, str) and mode_key:
                picker = self._strip._mode_picker
                idx = picker.findData(mode_key)
                if idx >= 0:
                    picker.setCurrentIndex(idx)
        except Exception:  # noqa: BLE001
            self._log.debug("Could not load session metadata", exc_info=True)

    def _stored_title(self) -> str:
        """The title already on disk, so a borrowed one can be left alone."""

        try:
            data = json.loads(
                (Path.home() / _SESSION_FILE).read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001 - absent or unreadable is fine
            return ""
        title = data.get("title") if isinstance(data, dict) else None
        return title if isinstance(title, str) else ""

    def _save_session_metadata(self) -> None:
        try:
            title = self._strip.current_title()
            if (
                self._borrowed_title
                and " ".join(title.split()) == self._borrowed_title
            ):
                # Keep the musician's own title rather than adopting the
                # invitation's name as their default.
                title = self._stored_title()
            mode_key = self._strip.current_mode_key()
            payload: dict = {}
            if title:
                payload["title"] = title
            if mode_key:
                payload["mode"] = mode_key
            if not payload:
                return
            atomic_write_text(
                Path.home() / _SESSION_FILE,
                json.dumps(payload, indent=2),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Could not save session metadata", exc_info=True)

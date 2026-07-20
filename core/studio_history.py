"""Bounded undo/redo history for immutable Studio documents.

Studio edits return a new :class:`~core.studio_project.StudioDocument` rather
than mutating source recording truth.  ``StudioHistory`` keeps the before and
after document snapshots needed to reverse those edits while bounding both
the number of retained commands and their compact serialized size.

The current document is never discarded to satisfy a history limit.  If one
edit is larger than the configured byte budget, the edit still succeeds and
becomes current, but no undo entry is retained for it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Callable

from core.studio_project import StudioDocument, StudioProjectError


DEFAULT_MAX_ENTRIES = 128
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MAX_HISTORY_LABEL_BYTES = 512


@dataclass(frozen=True)
class _HistoryEntry:
    """One immutable edit transition and its accounted serialized size."""

    label: str
    before: StudioDocument
    after: StudioDocument
    serialized_bytes: int
    merge_key: str | None = None


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StudioProjectError(f"{field_name} must be a positive integer.")
    return value


def _document_size(document: StudioDocument) -> int:
    """Return the deterministic compact-JSON byte size of one document."""

    try:
        payload = json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise StudioProjectError(
            "Studio document could not be serialized for history."
        ) from exc
    return len(payload)


def _validated_text(value: object, field_name: str) -> tuple[str, int]:
    """Return one bounded history string and its exact UTF-8 byte size."""

    if not isinstance(value, str):
        raise StudioProjectError(f"Studio history {field_name} must be text.")
    # Every Unicode code point needs at least one UTF-8 byte. Reject obviously
    # oversized input before allocating a second, encoded copy of it.
    if len(value) > MAX_HISTORY_LABEL_BYTES:
        raise StudioProjectError(
            f"Studio history {field_name} cannot exceed "
            f"{MAX_HISTORY_LABEL_BYTES} UTF-8 bytes."
        )
    try:
        value_bytes = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise StudioProjectError(
            f"Studio history {field_name} must be valid Unicode text."
        ) from exc
    if value_bytes > MAX_HISTORY_LABEL_BYTES:
        raise StudioProjectError(
            f"Studio history {field_name} cannot exceed "
            f"{MAX_HISTORY_LABEL_BYTES} UTF-8 bytes."
        )
    return value, value_bytes


def _validate_transition(before: StudioDocument, after: StudioDocument) -> None:
    """Reject snapshots that cannot be a forward edit of ``before``."""

    before_identity = (
        before.session_id,
        before.take_id,
        before.project_sample_rate,
    )
    after_identity = (
        after.session_id,
        after.take_id,
        after.project_sample_rate,
    )
    if after_identity != before_identity:
        raise StudioProjectError(
            "Studio history edits must preserve session, take, and sample-rate identity."
        )
    if after.revision <= before.revision:
        raise StudioProjectError(
            "Studio history edits must advance the document revision."
        )


class StudioHistory:
    """Thread-safe bounded command history for one Studio document.

    Edit callables run while the controller lock is held so two UI/controller
    threads cannot both derive a replacement from the same stale document.
    An edit exception or an invalid return value leaves the current document
    and both history stacks unchanged.
    """

    def __init__(
        self,
        document: StudioDocument,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if not isinstance(document, StudioDocument):
            raise StudioProjectError("Studio history requires a StudioDocument.")
        self._max_entries = _positive_int(max_entries, "max_entries")
        self._max_bytes = _positive_int(max_bytes, "max_bytes")
        self._document = document
        self._undo: list[_HistoryEntry] = []
        self._redo: list[_HistoryEntry] = []
        self._history_bytes = 0
        self._lock = threading.RLock()

    @property
    def document(self) -> StudioDocument:
        with self._lock:
            return self._document

    @property
    def can_undo(self) -> bool:
        with self._lock:
            return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        with self._lock:
            return bool(self._redo)

    @property
    def undo_depth(self) -> int:
        with self._lock:
            return len(self._undo)

    @property
    def redo_depth(self) -> int:
        with self._lock:
            return len(self._redo)

    def perform(
        self,
        label: str,
        edit_callable: Callable[[StudioDocument], StudioDocument],
        *,
        merge_key: str | None = None,
    ) -> StudioDocument:
        """Apply one immutable edit and retain it when history limits permit.

        No-op edits are ignored and preserve an existing redo path.  A real
        edit is divergent, so it clears redo entries before the new transition
        is appended.  Oversized transitions are immediately evicted after the
        new document becomes current; history limits can therefore never make
        a valid edit fail or discard the current state.
        """

        if not callable(edit_callable):
            raise StudioProjectError("Studio history edit must be callable.")
        command_label, label_bytes = _validated_text(label, "label")
        command_merge_key: str | None = None
        merge_key_bytes = 0
        if merge_key is not None:
            command_merge_key, merge_key_bytes = _validated_text(
                merge_key,
                "merge key",
            )

        with self._lock:
            before = self._document
            after = edit_callable(before)
            if not isinstance(after, StudioDocument):
                raise StudioProjectError(
                    "Studio history edits must return a StudioDocument."
                )
            if after == before:
                return self._document

            _validate_transition(before, after)

            serialized_bytes = (
                _document_size(before)
                + _document_size(after)
                + label_bytes
                + merge_key_bytes
            )
            entry = _HistoryEntry(
                label=command_label,
                before=before,
                after=after,
                serialized_bytes=serialized_bytes,
                merge_key=command_merge_key,
            )

            self._drop_redo_locked()
            self._document = after
            previous = self._undo[-1] if self._undo else None
            if (
                command_merge_key is not None
                and previous is not None
                and previous.merge_key == command_merge_key
                and previous.after == before
            ):
                merged_size = (
                    _document_size(previous.before)
                    + _document_size(after)
                    + label_bytes
                    + merge_key_bytes
                )
                self._undo[-1] = _HistoryEntry(
                    label=command_label,
                    before=previous.before,
                    after=after,
                    serialized_bytes=merged_size,
                    merge_key=command_merge_key,
                )
                self._history_bytes += merged_size - previous.serialized_bytes
            else:
                self._undo.append(entry)
                self._history_bytes += entry.serialized_bytes
            self._enforce_limits_locked()
            return self._document

    def undo(self) -> StudioDocument:
        """Undo the newest retained edit, or return the current document."""

        with self._lock:
            if not self._undo:
                return self._document
            entry = self._undo.pop()
            self._document = entry.before
            self._redo.append(entry)
            return self._document

    def redo(self) -> StudioDocument:
        """Redo the newest undone edit, or return the current document."""

        with self._lock:
            if not self._redo:
                return self._document
            entry = self._redo.pop()
            self._document = entry.after
            self._undo.append(entry)
            return self._document

    def clear(self) -> None:
        """Discard undo/redo snapshots without changing the current document."""

        with self._lock:
            self._undo.clear()
            self._redo.clear()
            self._history_bytes = 0

    def _drop_redo_locked(self) -> None:
        if not self._redo:
            return
        self._history_bytes -= sum(entry.serialized_bytes for entry in self._redo)
        self._redo.clear()

    def _enforce_limits_locked(self) -> None:
        while self._undo and (
            len(self._undo) + len(self._redo) > self._max_entries
            or self._history_bytes > self._max_bytes
        ):
            removed = self._undo.pop(0)
            self._history_bytes -= removed.serialized_bytes


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ENTRIES",
    "MAX_HISTORY_LABEL_BYTES",
    "StudioHistory",
]

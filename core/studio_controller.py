"""Framework-neutral coordination for one Studio arrangement.

The controller owns the mutable *workflow* around immutable Studio documents:
history, exact-byte persistence tokens, selection, autosave coalescing, and
generation cancellation.  It deliberately creates no threads or timers and
imports no UI framework.  A caller may schedule its own timer from the
``autosave_requested`` hook and later call :meth:`flush_autosave` with the
generation supplied to that hook.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from core.studio_history import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    StudioHistory,
)
from core.studio_project import StudioDocument, StudioProjectError
from core.studio_store import (
    StudioLoadResult,
    StudioStoreConflict,
    StudioStoreError,
    StudioStoreSaveUnconfirmed,
    load_studio_document,
    save_studio_document,
)

StudioEdit = Callable[[StudioDocument], StudioDocument]
AutosaveRequest = Callable[[int], None]


class StudioControllerError(RuntimeError):
    """Raised when a controller operation would discard or misbind state."""


class StudioCancellationToken:
    """Read-only cancellation state shared with asynchronous Studio work."""

    __slots__ = ("_cancelled", "_generation")

    def __init__(self, generation: int) -> None:
        self._generation = generation
        self._cancelled = threading.Event()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _cancel(self) -> None:
        self._cancelled.set()


class StudioProjectController:
    """Own one loaded Studio document without depending on a UI event loop."""

    def __init__(
        self,
        *,
        autosave_requested: AutosaveRequest | None = None,
        max_history_entries: int = DEFAULT_MAX_ENTRIES,
        max_history_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if autosave_requested is not None and not callable(autosave_requested):
            raise StudioControllerError("autosave_requested must be callable or null.")
        for value, name in (
            (max_history_entries, "max_history_entries"),
            (max_history_bytes, "max_history_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise StudioControllerError(f"{name} must be a positive integer.")

        self._autosave_requested = autosave_requested
        self._max_history_entries = max_history_entries
        self._max_history_bytes = max_history_bytes
        self._history: StudioHistory | None = None
        self._saved_document: StudioDocument | None = None
        self._take_path: Path | None = None
        self._store_token: str | None = None
        self._requires_save = False
        self._dirty = False
        self._autosave_pending = False
        self._selected_track_id: str | None = None
        self._selected_region_id: str | None = None
        self._last_error = ""
        self._conflicted = False
        self._recovery_notice = ""
        self._generation = 0
        self._task_token = StudioCancellationToken(self._generation)
        self._task_token._cancel()
        self._shutdown = False
        self._lock = threading.RLock()

    @property
    def document(self) -> StudioDocument:
        with self._lock:
            return self._document_locked()

    @property
    def take_path(self) -> Path | None:
        with self._lock:
            return self._take_path

    @property
    def store_token(self) -> str | None:
        with self._lock:
            return self._store_token

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    @property
    def autosave_pending(self) -> bool:
        with self._lock:
            return self._autosave_pending

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def conflicted(self) -> bool:
        with self._lock:
            return self._conflicted

    @property
    def recovery_notice(self) -> str:
        with self._lock:
            return self._recovery_notice

    @property
    def selected_track_id(self) -> str | None:
        with self._lock:
            return self._selected_track_id

    @property
    def selected_region_id(self) -> str | None:
        with self._lock:
            return self._selected_region_id

    @property
    def can_undo(self) -> bool:
        with self._lock:
            return bool(self._history and self._history.can_undo)

    @property
    def can_redo(self) -> bool:
        with self._lock:
            return bool(self._history and self._history.can_redo)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def task_token(self) -> StudioCancellationToken:
        with self._lock:
            self._ensure_running_locked()
            self._document_locked()
            return self._task_token

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def load(
        self,
        take_dir: str | Path,
        *,
        discard_dirty: bool = False,
    ) -> StudioDocument:
        """Load an initial take or switch takes without silently losing edits."""

        self._require_bool(discard_dirty, "discard_dirty")
        folder = Path(take_dir).expanduser().resolve()
        with self._lock:
            self._ensure_running_locked()
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Studio has unsaved edits; save or explicitly discard them before "
                    "switching takes."
                )
            prior_identity = self._identity_locked()
            same_path = self._take_path == folder
            result = self._load_result_locked(folder)
            if same_path and prior_identity is not None:
                loaded_identity = self._document_identity(result.document)
                if loaded_identity != prior_identity:
                    self._raise_retained_locked(
                        "The take at this path changed identity; the loaded Studio state "
                        "was retained."
                    )
            self._activate_load_locked(
                folder,
                result,
                preserve_selection=same_path,
            )
            return self._document_locked()

    def unload(self, *, discard_dirty: bool = False) -> None:
        """Release the active take without silently discarding unsaved edits."""

        self._require_bool(discard_dirty, "discard_dirty")
        with self._lock:
            self._ensure_running_locked()
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Studio has unsaved edits; save or explicitly discard them before "
                    "unloading."
                )

            self._task_token._cancel()
            self._generation += 1
            self._task_token = StudioCancellationToken(self._generation)
            self._task_token._cancel()
            self._history = None
            self._saved_document = None
            self._take_path = None
            self._store_token = None
            self._requires_save = False
            self._dirty = False
            self._autosave_pending = False
            self._selected_track_id = None
            self._selected_region_id = None
            self._last_error = ""
            self._conflicted = False
            self._recovery_notice = ""

    def reload(self, *, discard_dirty: bool = False) -> StudioDocument:
        """Reload the current path only when it still identifies the same take."""

        self._require_bool(discard_dirty, "discard_dirty")
        with self._lock:
            self._ensure_running_locked()
            if self._take_path is None or self._history is None:
                raise StudioControllerError("No Studio take is loaded.")
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Studio has unsaved edits; save or explicitly discard them before "
                    "reloading."
                )
            expected_identity = self._document_identity(self._document_locked())
            result = self._load_result_locked(self._take_path)
            if self._document_identity(result.document) != expected_identity:
                self._raise_retained_locked(
                    "Reloaded Studio state belongs to a different take; current state "
                    "was retained."
                )
            self._activate_load_locked(
                self._take_path,
                result,
                preserve_selection=True,
            )
            return self._document_locked()

    def perform(
        self,
        label: str,
        edit: StudioEdit,
        *,
        merge_key: str | None = None,
    ) -> StudioDocument:
        """Apply one validated history edit and coalesce an autosave request."""

        request: tuple[AutosaveRequest, int] | None
        with self._lock:
            self._ensure_editable_locked()
            assert self._history is not None
            before = self._history.document
            after = self._history.perform(label, edit, merge_key=merge_key)
            if after == before:
                return after
            request = self._document_changed_locked()
        self._emit_autosave_request(request)
        return after

    def undo(self) -> StudioDocument:
        return self._history_step("undo")

    def redo(self) -> StudioDocument:
        return self._history_step("redo")

    def select_track(self, track_id: str | None) -> None:
        with self._lock:
            self._ensure_editable_locked()
            document = self._document_locked()
            if track_id is None:
                self._selected_track_id = None
                self._selected_region_id = None
                return
            track = document.state_for(track_id)
            self._selected_track_id = track.track_id
            if self._selected_region_id is not None:
                region = document.region_for(self._selected_region_id)
                if region.track_id != track.track_id or region.deleted:
                    self._selected_region_id = None

    def select_region(self, region_id: str | None) -> None:
        with self._lock:
            self._ensure_editable_locked()
            if region_id is None:
                self._selected_region_id = None
                return
            region = self._document_locked().region_for(region_id)
            if region.deleted:
                raise StudioProjectError("A deleted Studio region cannot be selected.")
            self._selected_region_id = region.region_id
            self._selected_track_id = region.track_id

    def save(self) -> bool:
        """Save with the exact load token; retain dirty state on every failure."""

        with self._lock:
            self._ensure_editable_locked()
            self._autosave_pending = False
            return self._save_locked()

    def flush_autosave(self, generation: int) -> bool:
        """Run a scheduled autosave only if its generation is still current."""

        if isinstance(generation, bool) or not isinstance(generation, int):
            raise StudioControllerError("Autosave generation must be an integer.")
        with self._lock:
            if self._shutdown or generation != self._generation:
                return False
            self._ensure_editable_locked()
            self._autosave_pending = False
            return self._save_locked()

    def accepts_async_result(self, token: StudioCancellationToken) -> bool:
        """Return whether an async callback still belongs to the active take."""

        with self._lock:
            return (
                isinstance(token, StudioCancellationToken)
                and not self._shutdown
                and self._history is not None
                and token is self._task_token
                and token.generation == self._generation
                and not token.cancelled
            )

    def shutdown(self) -> None:
        """Cancel outstanding work and reject subsequent controller mutations."""

        with self._lock:
            if self._shutdown:
                return
            self._task_token._cancel()
            self._generation += 1
            self._task_token = StudioCancellationToken(self._generation)
            self._task_token._cancel()
            self._autosave_pending = False
            self._shutdown = True

    def _history_step(self, operation: str) -> StudioDocument:
        request: tuple[AutosaveRequest, int] | None
        with self._lock:
            self._ensure_editable_locked()
            assert self._history is not None
            before = self._history.document
            after = (
                self._history.undo() if operation == "undo" else self._history.redo()
            )
            if after == before:
                return after
            request = self._document_changed_locked()
        self._emit_autosave_request(request)
        return after

    def _document_changed_locked(self) -> tuple[AutosaveRequest, int] | None:
        self._reconcile_selection_locked()
        self._refresh_dirty_locked()
        return self._arm_autosave_locked()

    def _arm_autosave_locked(self) -> tuple[AutosaveRequest, int] | None:
        if (
            not self._dirty
            or self._autosave_pending
            or self._autosave_requested is None
        ):
            return None
        self._autosave_pending = True
        return self._autosave_requested, self._generation

    def _emit_autosave_request(
        self,
        request: tuple[AutosaveRequest, int] | None,
    ) -> None:
        if request is None:
            return
        callback, generation = request
        try:
            callback(generation)
        except Exception as exc:  # noqa: BLE001 - scheduler failures stay recoverable
            with self._lock:
                if generation == self._generation and not self._shutdown:
                    self._autosave_pending = False
                    self._last_error = f"Could not schedule Studio autosave: {exc}"
                    self._conflicted = False

    def _save_locked(self) -> bool:
        if not self._dirty:
            return True
        document = self._document_locked()
        assert self._take_path is not None
        try:
            result = save_studio_document(
                self._take_path,
                document,
                expected_token=self._store_token,
            )
        except StudioStoreConflict as exc:
            self._last_error = str(exc)
            self._conflicted = True
            self._dirty = True
            return False
        except StudioStoreSaveUnconfirmed as exc:
            self._store_token = exc.published_token
            self._requires_save = True
            self._last_error = str(exc)
            self._conflicted = False
            self._dirty = True
            return False
        except StudioStoreError as exc:
            self._last_error = str(exc)
            self._conflicted = False
            self._dirty = True
            return False

        if self._document_identity(result.document) != self._document_identity(
            document
        ):
            self._last_error = "Saved Studio state returned a different take identity."
            self._conflicted = True
            self._dirty = True
            return False
        if result.path.parent.expanduser().resolve() != self._take_path:
            self._last_error = "Saved Studio state returned an unexpected take path."
            self._conflicted = True
            self._dirty = True
            return False

        if result.document != document:
            self._history = self._new_history(result.document)
            document = result.document
        self._store_token = result.token
        self._saved_document = document
        self._requires_save = False
        self._dirty = False
        self._last_error = ""
        self._conflicted = False
        self._recovery_notice = ""
        self._reconcile_selection_locked()
        return True

    def _load_result_locked(self, folder: Path) -> StudioLoadResult:
        try:
            return load_studio_document(folder)
        except StudioStoreError as exc:
            self._last_error = str(exc)
            self._conflicted = isinstance(exc, StudioStoreConflict)
            raise StudioControllerError(str(exc)) from exc

    def _activate_load_locked(
        self,
        folder: Path,
        result: StudioLoadResult,
        *,
        preserve_selection: bool,
    ) -> None:
        previous_track = self._selected_track_id if preserve_selection else None
        previous_region = self._selected_region_id if preserve_selection else None
        self._history = self._new_history(result.document)
        self._saved_document = result.document
        self._take_path = folder
        self._store_token = result.token
        self._requires_save = result.needs_save
        self._dirty = result.needs_save
        self._autosave_pending = False
        self._selected_track_id = previous_track
        self._selected_region_id = previous_region
        self._last_error = ""
        self._conflicted = False
        self._recovery_notice = result.recovery_notice
        self._task_token._cancel()
        self._generation += 1
        self._task_token = StudioCancellationToken(self._generation)
        self._reconcile_selection_locked()

    def _new_history(self, document: StudioDocument) -> StudioHistory:
        return StudioHistory(
            document,
            max_entries=self._max_history_entries,
            max_bytes=self._max_history_bytes,
        )

    def _refresh_dirty_locked(self) -> None:
        document = self._document_locked()
        self._dirty = self._requires_save or document != self._saved_document
        if not self._dirty:
            self._autosave_pending = False

    def _reconcile_selection_locked(self) -> None:
        document = self._document_locked()
        track_ids = {item.track_id for item in document.tracks}
        if self._selected_track_id not in track_ids:
            self._selected_track_id = None
            self._selected_region_id = None
            return
        if self._selected_region_id is None:
            return
        region = next(
            (
                item
                for item in document.regions
                if item.region_id == self._selected_region_id
            ),
            None,
        )
        if (
            region is None
            or region.deleted
            or region.track_id != self._selected_track_id
        ):
            self._selected_region_id = None

    def _identity_locked(self) -> tuple[str, str, int] | None:
        if self._history is None:
            return None
        return self._document_identity(self._history.document)

    @staticmethod
    def _document_identity(document: StudioDocument) -> tuple[str, str, int]:
        return document.session_id, document.take_id, document.project_sample_rate

    def _document_locked(self) -> StudioDocument:
        if self._history is None:
            raise StudioControllerError("No Studio take is loaded.")
        return self._history.document

    def _ensure_running_locked(self) -> None:
        if self._shutdown:
            raise StudioControllerError("Studio controller has shut down.")

    def _ensure_editable_locked(self) -> None:
        self._ensure_running_locked()
        self._document_locked()

    def _raise_retained_locked(self, message: str) -> None:
        self._last_error = message
        self._conflicted = False
        raise StudioControllerError(message)

    @staticmethod
    def _require_bool(value: object, field_name: str) -> None:
        if not isinstance(value, bool):
            raise StudioControllerError(f"{field_name} must be true or false.")


__all__ = [
    "StudioCancellationToken",
    "StudioControllerError",
    "StudioProjectController",
]

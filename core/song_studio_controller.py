"""Framework-neutral workflow controller for schema-3 song Studio state.

The controller owns mutable workflow around immutable
:class:`~core.studio_project.StudioDocument` snapshots: bounded history,
exact persistence tokens, explicit autosave recovery, dirty state, close
vetoes, and cancellation generations.  It creates no timer or thread; UI and
service layers schedule the generation passed to ``autosave_requested`` and
later call :meth:`SongStudioController.flush_autosave`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from core.song_project import SongProject
from core.studio_history import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    StudioHistory,
)
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
)
from core.song_studio_store import (
    SongStudioConflict,
    SongStudioLoadResult,
    SongStudioRecoveryCandidate,
    SongStudioStoreError,
    discard_song_studio_autosave,
    load_song_studio_document,
    recover_song_studio_autosave,
    save_song_studio_document,
    write_song_studio_autosave,
)


SongStudioEdit = Callable[[StudioDocument], StudioDocument]
SongStudioAutosaveRequest = Callable[[int], None]


class SongStudioControllerError(RuntimeError):
    """Raised when an operation could discard or misbind song Studio state."""


class SongStudioCancellationToken:
    """Cancellation state for asynchronous work tied to one load generation."""

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

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise SongStudioControllerError(
                "Song Studio work was cancelled or superseded."
            )

    def _cancel(self) -> None:
        self._cancelled.set()


class SongStudioController:
    """Own one loaded song-project Studio document and its safe lifecycle."""

    def __init__(
        self,
        *,
        autosave_requested: SongStudioAutosaveRequest | None = None,
        max_history_entries: int = DEFAULT_MAX_ENTRIES,
        max_history_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if autosave_requested is not None and not callable(autosave_requested):
            raise SongStudioControllerError(
                "autosave_requested must be callable or null."
            )
        for value, label in (
            (max_history_entries, "max_history_entries"),
            (max_history_bytes, "max_history_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SongStudioControllerError(f"{label} must be a positive integer.")

        self._autosave_requested = autosave_requested
        self._max_history_entries = max_history_entries
        self._max_history_bytes = max_history_bytes
        self._history: StudioHistory | None = None
        self._saved_document: StudioDocument | None = None
        self._project: SongProject | None = None
        self._bundle_path: Path | None = None
        self._store_token: str | None = None
        self._requires_save = False
        self._dirty = False
        self._autosave_pending = False
        self._autosave_token: str | None = None
        self._recovery_candidate: SongStudioRecoveryCandidate | None = None
        self._autosave_requires_discard = False
        self._recovery_notice = ""
        self._last_error = ""
        self._conflicted = False
        self._generation = 0
        self._task_token = SongStudioCancellationToken(self._generation)
        self._task_token._cancel()
        self._shutdown = False
        self._lock = threading.RLock()

    @property
    def document(self) -> StudioDocument:
        with self._lock:
            return self._document_locked()

    @property
    def project(self) -> SongProject:
        with self._lock:
            if self._project is None:
                raise SongStudioControllerError("No song Studio project is loaded.")
            return self._project

    @property
    def bundle_path(self) -> Path | None:
        with self._lock:
            return self._bundle_path

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
    def autosave_token(self) -> str | None:
        with self._lock:
            return self._autosave_token

    @property
    def recovery_candidate(self) -> SongStudioRecoveryCandidate | None:
        with self._lock:
            return self._recovery_candidate

    @property
    def recovery_requires_discard(self) -> bool:
        with self._lock:
            return self._autosave_requires_discard

    @property
    def recovery_notice(self) -> str:
        with self._lock:
            return self._recovery_notice

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def conflicted(self) -> bool:
        with self._lock:
            return self._conflicted

    @property
    def can_undo(self) -> bool:
        with self._lock:
            return bool(self._history and self._history.can_undo)

    @property
    def can_redo(self) -> bool:
        with self._lock:
            return bool(self._history and self._history.can_redo)

    @property
    def undo_depth(self) -> int:
        with self._lock:
            return self._history.undo_depth if self._history is not None else 0

    @property
    def redo_depth(self) -> int:
        with self._lock:
            return self._history.redo_depth if self._history is not None else 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def task_token(self) -> SongStudioCancellationToken:
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
        bundle_path: str | Path,
        project: SongProject,
        *,
        discard_dirty: bool = False,
    ) -> StudioDocument:
        """Load or switch projects without silently dropping dirty snapshots."""

        self._require_bool(discard_dirty, "discard_dirty")
        if not isinstance(project, SongProject):
            raise SongStudioControllerError("Song Studio requires a SongProject value.")
        with self._lock:
            self._ensure_running_locked()
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Song Studio has unsaved edits; save or explicitly discard "
                    "them before switching projects."
                )
            unresolved_recovery = (
                self._recovery_candidate is not None
                or self._autosave_requires_discard
                or self._autosave_token is not None
            )
            if unresolved_recovery and not discard_dirty:
                self._raise_retained_locked(
                    "Resolve or explicitly discard Studio recovery data before "
                    "switching projects."
                )
            if unresolved_recovery and self._project is not None:
                self._discard_autosave_locked(require_present=False)
            result = self._load_result_locked(bundle_path, project)
            if (
                self._bundle_path == result.bundle_path
                and self._project is not None
                and self._project_identity(self._project)
                != self._project_identity(project)
            ):
                self._raise_retained_locked(
                    "The project at this bundle changed identity; current Studio "
                    "state was retained."
                )
            self._activate_load_locked(project, result)
            return self._document_locked()

    def reload(self, *, discard_dirty: bool = False) -> StudioDocument:
        self._require_bool(discard_dirty, "discard_dirty")
        with self._lock:
            self._ensure_running_locked()
            project, bundle = self._loaded_identity_locked()
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Song Studio has unsaved edits; save or explicitly discard "
                    "them before reloading."
                )
            unresolved_recovery = (
                self._recovery_candidate is not None
                or self._autosave_requires_discard
                or self._autosave_token is not None
            )
            if unresolved_recovery and not discard_dirty:
                self._raise_retained_locked(
                    "Resolve or explicitly discard Studio recovery data before "
                    "reloading."
                )
            if unresolved_recovery:
                self._discard_autosave_locked(require_present=False)
            result = self._load_result_locked(bundle, project)
            if self._document_identity(result.document) != self._project_identity(
                project
            ):
                self._raise_retained_locked(
                    "Reloaded Studio state belongs to a different song project."
                )
            self._activate_load_locked(project, result)
            return self._document_locked()

    def unload(self, *, discard_dirty: bool = False) -> None:
        self._require_bool(discard_dirty, "discard_dirty")
        with self._lock:
            self._ensure_running_locked()
            if self._dirty and not discard_dirty:
                self._raise_retained_locked(
                    "Song Studio has unsaved edits; save or explicitly discard "
                    "them before unloading."
                )
            if (
                self._recovery_candidate is not None
                or self._autosave_requires_discard
                or self._autosave_token is not None
            ) and not discard_dirty:
                self._raise_retained_locked(
                    "Resolve or explicitly discard Studio recovery data before "
                    "unloading."
                )
            if discard_dirty and self._project is not None:
                self._discard_autosave_locked(require_present=False)
            self._clear_load_locked()

    def perform(
        self,
        label: str,
        edit: SongStudioEdit,
        *,
        merge_key: str | None = None,
    ) -> StudioDocument:
        """Apply one identity-checked immutable edit and request autosave."""

        if not callable(edit):
            raise SongStudioControllerError("Song Studio edit must be callable.")
        request: tuple[SongStudioAutosaveRequest, int] | None
        with self._lock:
            self._ensure_editable_locked()
            assert self._history is not None
            before = self._history.document

            def guarded(current: StudioDocument) -> StudioDocument:
                updated = edit(current)
                if not isinstance(updated, StudioDocument):
                    raise StudioProjectError(
                        "Song Studio edits must return a StudioDocument."
                    )
                self._validate_document_locked(updated)
                return updated

            after = self._history.perform(
                label,
                guarded,
                merge_key=merge_key,
            )
            if after == before:
                return after
            request = self._document_changed_locked()
        self._emit_autosave_request(request)
        return after

    def apply_async_edit(
        self,
        token: SongStudioCancellationToken,
        label: str,
        edit: SongStudioEdit,
        *,
        merge_key: str | None = None,
    ) -> StudioDocument:
        """Apply asynchronous work only while its exact generation still owns."""

        if not callable(edit):
            raise SongStudioControllerError("Song Studio edit must be callable.")
        request: tuple[SongStudioAutosaveRequest, int] | None
        with self._lock:
            if not self._accepts_async_result_locked(token):
                raise SongStudioControllerError(
                    "Song Studio result belongs to a stale generation."
                )
            self._ensure_editable_locked()
            assert self._history is not None
            before = self._history.document

            def guarded(current: StudioDocument) -> StudioDocument:
                updated = edit(current)
                if not isinstance(updated, StudioDocument):
                    raise StudioProjectError(
                        "Song Studio edits must return a StudioDocument."
                    )
                self._validate_document_locked(updated)
                return updated

            after = self._history.perform(
                label,
                guarded,
                merge_key=merge_key,
            )
            if after == before:
                return after
            request = self._document_changed_locked()
        self._emit_autosave_request(request)
        return after

    def undo(self) -> StudioDocument:
        return self._history_step("undo")

    def redo(self) -> StudioDocument:
        return self._history_step("redo")

    def save(self) -> bool:
        """Explicitly save primary state with the exact loaded token."""

        with self._lock:
            self._ensure_editable_locked()
            self._autosave_pending = False
            return self._save_locked()

    def flush_autosave(self, generation: int) -> bool:
        """Write recovery state only for the currently loaded generation."""

        if isinstance(generation, bool) or not isinstance(generation, int):
            raise SongStudioControllerError("Autosave generation must be an integer.")
        with self._lock:
            if self._shutdown or generation != self._generation:
                return False
            self._ensure_editable_locked()
            self._autosave_pending = False
            if not self._dirty:
                return True
            project, bundle = self._loaded_identity_locked()
            try:
                result = write_song_studio_autosave(
                    bundle,
                    project,
                    self._document_locked(),
                    base_primary_token=self._store_token,
                )
            except SongStudioConflict as exc:
                self._last_error = str(exc)
                self._conflicted = True
                return False
            except SongStudioStoreError as exc:
                self._last_error = str(exc)
                self._conflicted = False
                return False
            self._autosave_token = result.token
            self._last_error = ""
            self._conflicted = False
            return True

    def recover_autosave(self) -> StudioDocument:
        """Explicitly promote the offered autosave after exact-token checks."""

        with self._lock:
            self._ensure_running_locked()
            project, bundle = self._loaded_identity_locked()
            candidate = self._recovery_candidate
            if candidate is None:
                raise SongStudioControllerError(
                    "No recoverable Studio autosave is available."
                )
            try:
                result = recover_song_studio_autosave(
                    bundle,
                    project,
                    expected_autosave_token=candidate.autosave_token,
                )
            except (SongStudioConflict, SongStudioStoreError) as exc:
                self._last_error = str(exc)
                self._conflicted = isinstance(exc, SongStudioConflict)
                raise SongStudioControllerError(str(exc)) from exc
            self._activate_saved_locked(result.document, result.token)
            if not result.autosave_cleared:
                self._autosave_requires_discard = True
                self._recovery_notice = (
                    "Recovered Studio state was saved, but its autosave still "
                    "requires explicit discard."
                )
            return self._document_locked()

    def discard_recovery(self) -> None:
        """Explicitly remove the fixed autosave, including stale/corrupt state."""

        with self._lock:
            self._ensure_running_locked()
            project, bundle = self._loaded_identity_locked()
            expected = (
                self._recovery_candidate.autosave_token
                if self._recovery_candidate is not None
                else self._autosave_token
            )
            try:
                discard_song_studio_autosave(
                    bundle,
                    project,
                    expected_token=expected,
                )
            except (SongStudioConflict, SongStudioStoreError) as exc:
                self._last_error = str(exc)
                self._conflicted = isinstance(exc, SongStudioConflict)
                raise SongStudioControllerError(str(exc)) from exc
            self._recovery_candidate = None
            self._autosave_requires_discard = False
            self._autosave_token = None
            self._recovery_notice = ""
            self._last_error = ""
            self._conflicted = False

    def accepts_async_result(self, token: SongStudioCancellationToken) -> bool:
        with self._lock:
            return self._accepts_async_result_locked(token)

    def request_close(self, *, discard_dirty: bool = False) -> bool:
        """Return ``False`` rather than closing across dirty/recovery state."""

        self._require_bool(discard_dirty, "discard_dirty")
        with self._lock:
            if self._shutdown:
                return True
            if (
                self._dirty
                or self._recovery_candidate is not None
                or self._autosave_requires_discard
                or self._autosave_token is not None
            ) and not discard_dirty:
                self._last_error = (
                    "Song Studio has unsaved or recovery state; save, recover, "
                    "or explicitly discard it before closing."
                )
                self._conflicted = False
                return False
            if discard_dirty and self._project is not None:
                try:
                    self._discard_autosave_locked(require_present=False)
                except SongStudioControllerError:
                    return False
            self._shutdown_locked()
            return True

    close = request_close

    def shutdown(self) -> None:
        """Cancel outstanding work without overriding the close-veto contract."""

        with self._lock:
            self._shutdown_locked()

    def _history_step(self, operation: str) -> StudioDocument:
        request: tuple[SongStudioAutosaveRequest, int] | None
        with self._lock:
            self._ensure_editable_locked()
            assert self._history is not None
            before = self._history.document
            after = (
                self._history.undo() if operation == "undo" else self._history.redo()
            )
            if after == before:
                return after
            self._validate_document_locked(after)
            request = self._document_changed_locked()
        self._emit_autosave_request(request)
        return after

    def _document_changed_locked(
        self,
    ) -> tuple[SongStudioAutosaveRequest, int] | None:
        self._refresh_dirty_locked()
        return self._arm_autosave_locked()

    def _arm_autosave_locked(
        self,
    ) -> tuple[SongStudioAutosaveRequest, int] | None:
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
        request: tuple[SongStudioAutosaveRequest, int] | None,
    ) -> None:
        if request is None:
            return
        callback, generation = request
        try:
            callback(generation)
        except Exception:  # noqa: BLE001 - scheduler is outside our trust boundary
            with self._lock:
                if generation == self._generation and not self._shutdown:
                    self._autosave_pending = False
                    self._last_error = "Could not schedule Song Studio autosave."
                    self._conflicted = False

    def _save_locked(self) -> bool:
        if not self._dirty:
            if self._autosave_token is not None:
                try:
                    self._discard_autosave_locked(require_present=False)
                except SongStudioControllerError:
                    return False
            return True
        project, bundle = self._loaded_identity_locked()
        document = self._document_locked()
        try:
            result = save_song_studio_document(
                bundle,
                project,
                document,
                expected_token=self._store_token,
            )
        except SongStudioConflict as exc:
            self._last_error = str(exc)
            self._conflicted = True
            self._dirty = True
            return False
        except SongStudioStoreError as exc:
            self._last_error = str(exc)
            self._conflicted = False
            self._dirty = True
            return False
        if self._document_identity(result.document) != self._project_identity(project):
            self._last_error = (
                "Saved Song Studio state returned a different project identity."
            )
            self._conflicted = True
            self._dirty = True
            return False
        if result.path.parent.resolve() != bundle.resolve():
            self._last_error = "Saved Song Studio state returned an unexpected bundle."
            self._conflicted = True
            self._dirty = True
            return False
        self._activate_saved_locked(result.document, result.token)
        if not result.autosave_cleared:
            self._autosave_requires_discard = True
            self._recovery_notice = (
                "Studio was saved, but stale autosave data still requires discard."
            )
        return True

    def _activate_saved_locked(
        self,
        document: StudioDocument,
        token: str,
    ) -> None:
        self._validate_document_locked(document)
        if self._history is None or self._history.document != document:
            self._history = self._new_history(document)
        self._saved_document = document
        self._store_token = token
        self._requires_save = False
        self._dirty = False
        self._autosave_pending = False
        self._autosave_token = None
        self._recovery_candidate = None
        self._autosave_requires_discard = False
        self._recovery_notice = ""
        self._last_error = ""
        self._conflicted = False
        self._invalidate_generation_locked()

    def _load_result_locked(
        self,
        bundle_path: str | Path,
        project: SongProject,
    ) -> SongStudioLoadResult:
        try:
            return load_song_studio_document(bundle_path, project)
        except SongStudioStoreError as exc:
            self._last_error = str(exc)
            self._conflicted = isinstance(exc, SongStudioConflict)
            raise SongStudioControllerError(str(exc)) from exc

    def _activate_load_locked(
        self,
        project: SongProject,
        result: SongStudioLoadResult,
    ) -> None:
        self._validate_document_for_project(result.document, project)
        self._project = project
        self._bundle_path = result.bundle_path
        self._history = self._new_history(result.document)
        self._saved_document = result.document
        self._store_token = result.token
        self._requires_save = result.needs_save
        self._dirty = result.needs_save
        self._autosave_pending = False
        self._autosave_token = (
            result.recovery_candidate.autosave_token
            if result.recovery_candidate is not None
            else None
        )
        self._recovery_candidate = result.recovery_candidate
        self._autosave_requires_discard = result.autosave_requires_discard
        self._recovery_notice = result.recovery_notice
        self._last_error = ""
        self._conflicted = False
        self._invalidate_generation_locked()

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

    def _loaded_identity_locked(self) -> tuple[SongProject, Path]:
        if self._project is None or self._bundle_path is None:
            raise SongStudioControllerError("No song Studio project is loaded.")
        return self._project, self._bundle_path

    def _document_locked(self) -> StudioDocument:
        if self._history is None:
            raise SongStudioControllerError("No song Studio project is loaded.")
        return self._history.document

    def _validate_document_locked(self, document: StudioDocument) -> None:
        if self._project is None:
            raise SongStudioControllerError("No song Studio project is loaded.")
        self._validate_document_for_project(document, self._project)

    @classmethod
    def _validate_document_for_project(
        cls,
        document: StudioDocument,
        project: SongProject,
    ) -> None:
        if not isinstance(document, StudioDocument):
            raise SongStudioControllerError(
                "Song Studio requires a StudioDocument snapshot."
            )
        if document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION:
            raise SongStudioControllerError(
                "Song Studio accepts only schema-3 snapshots."
            )
        if cls._document_identity(document) != cls._project_identity(project):
            raise SongStudioControllerError(
                "Song Studio edit changed project identity."
            )

    @staticmethod
    def _project_identity(project: SongProject) -> tuple[str, int]:
        return project.project_id, project.project_sample_rate

    @staticmethod
    def _document_identity(document: StudioDocument) -> tuple[str, int]:
        return document.project_id, document.project_sample_rate

    def _accepts_async_result_locked(
        self,
        token: SongStudioCancellationToken,
    ) -> bool:
        return (
            isinstance(token, SongStudioCancellationToken)
            and not self._shutdown
            and self._history is not None
            and token is self._task_token
            and token.generation == self._generation
            and not token.cancelled
        )

    def _ensure_running_locked(self) -> None:
        if self._shutdown:
            raise SongStudioControllerError("Song Studio controller has shut down.")

    def _ensure_editable_locked(self) -> None:
        self._ensure_running_locked()
        self._document_locked()
        if self._recovery_candidate is not None or self._autosave_requires_discard:
            raise SongStudioControllerError(
                "Recover or discard Studio autosave data before editing."
            )

    def _discard_autosave_locked(self, *, require_present: bool) -> None:
        project, bundle = self._loaded_identity_locked()
        if (
            require_present
            and self._recovery_candidate is None
            and not self._autosave_requires_discard
            and self._autosave_token is None
        ):
            raise SongStudioControllerError(
                "No Studio autosave is available to discard."
            )
        expected = (
            self._recovery_candidate.autosave_token
            if self._recovery_candidate is not None
            else self._autosave_token
        )
        try:
            discard_song_studio_autosave(
                bundle,
                project,
                expected_token=expected,
            )
        except (SongStudioConflict, SongStudioStoreError) as exc:
            self._last_error = str(exc)
            self._conflicted = isinstance(exc, SongStudioConflict)
            raise SongStudioControllerError(str(exc)) from exc
        self._recovery_candidate = None
        self._autosave_requires_discard = False
        self._autosave_token = None
        self._recovery_notice = ""

    def _invalidate_generation_locked(self) -> None:
        self._task_token._cancel()
        self._generation += 1
        self._task_token = SongStudioCancellationToken(self._generation)

    def _clear_load_locked(self) -> None:
        self._invalidate_generation_locked()
        self._task_token._cancel()
        self._history = None
        self._saved_document = None
        self._project = None
        self._bundle_path = None
        self._store_token = None
        self._requires_save = False
        self._dirty = False
        self._autosave_pending = False
        self._autosave_token = None
        self._recovery_candidate = None
        self._autosave_requires_discard = False
        self._recovery_notice = ""
        self._last_error = ""
        self._conflicted = False

    def _shutdown_locked(self) -> None:
        if self._shutdown:
            return
        self._task_token._cancel()
        self._generation += 1
        self._task_token = SongStudioCancellationToken(self._generation)
        self._task_token._cancel()
        self._autosave_pending = False
        self._shutdown = True

    def _raise_retained_locked(self, message: str) -> None:
        self._last_error = message
        self._conflicted = False
        raise SongStudioControllerError(message)

    @staticmethod
    def _require_bool(value: object, field_name: str) -> None:
        if not isinstance(value, bool):
            raise SongStudioControllerError(f"{field_name} must be true or false.")


__all__ = [
    "SongStudioCancellationToken",
    "SongStudioController",
    "SongStudioControllerError",
]

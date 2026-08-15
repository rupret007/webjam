"""UI-neutral lifecycle controller for standalone WebJam song projects.

The controller owns no Qt objects and no audio/Jamulus state.  It coordinates
immutable :class:`~core.song_project.SongProject` values with the crash-safe
bundle store, exposes exact dirty/token state, and provides generation leases
for worker-thread media operations.

All external-path and low-level I/O failures are translated to bounded,
musician-facing messages with no exception chaining.  Recovery autosaves are
surfaced but are never silently promoted.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from core.song_project import (
    InputMapping,
    MediaImportMethod,
    MediaProvenance,
    SongMedia,
    SongProject,
    SongProjectError,
    SongTrack,
    TimeSignature,
)
from core.song_project_store import (
    ProjectLoadOrigin,
    ProjectRecoveryCandidate,
    RecentProjects,
    SongProjectConflict,
    SongProjectStoreError,
    create_project_bundle,
    discard_project_autosave,
    import_project_media,
    load_project_bundle,
    load_recent_projects,
    record_recent_project,
    recover_project_autosave,
    relink_project_media,
    save_project_as,
    save_project_bundle,
    verify_project_media,
    write_project_autosave,
)


class SongProjectControllerError(RuntimeError):
    """A bounded musician-facing controller failure."""


class SongProjectNotOpen(SongProjectControllerError):
    """Raised when a project operation has no open project."""


class SongProjectControllerConflict(SongProjectControllerError):
    """Raised when exact primary bytes changed outside this controller."""


class MediaVerificationState(str, Enum):
    UNKNOWN = "unknown"
    VERIFIED = "verified"
    INVALID = "invalid"


@dataclass(frozen=True)
class ControllerGeneration:
    """Lease binding an async completion to one exact controller generation."""

    controller_id: str
    generation: int
    project_id: str
    bundle_identity: str


@dataclass(frozen=True)
class RecoverySnapshot:
    """Path-free recovery choice shown to the application."""

    project: SongProject
    autosave_token: str
    base_primary_token: str | None

    @classmethod
    def from_candidate(
        cls, candidate: ProjectRecoveryCandidate | None
    ) -> "RecoverySnapshot | None":
        if candidate is None:
            return None
        return cls(
            project=candidate.project,
            autosave_token=candidate.autosave_token,
            base_primary_token=candidate.base_primary_token,
        )


@dataclass(frozen=True)
class SongProjectControllerSnapshot:
    project: SongProject | None
    bundle_path: Path | None
    token: str | None
    dirty: bool
    generation: int
    recovery: RecoverySnapshot | None
    autosave_pending: bool
    autosave_error: str
    recent_projects: tuple[Path, ...]
    recent_error: str
    backing_media_verification: MediaVerificationState

    @property
    def is_open(self) -> bool:
        return self.project is not None


@dataclass(frozen=True)
class ControllerSaveResult:
    snapshot: SongProjectControllerSnapshot
    saved: bool
    stale: bool = False


@dataclass(frozen=True)
class ControllerCloseResult:
    snapshot: SongProjectControllerSnapshot
    closed: bool
    vetoed: bool


@dataclass(frozen=True)
class AutosaveFlushResult:
    snapshot: SongProjectControllerSnapshot
    written: bool
    stale: bool = False
    error: str = ""


@dataclass(frozen=True)
class MediaControllerResult:
    snapshot: SongProjectControllerSnapshot
    applied: bool
    stale: bool
    media: SongMedia | None = None
    verified: bool = False


AutosaveCallback = Callable[[], AutosaveFlushResult]
AutosaveScheduler = Callable[[AutosaveCallback], object]
CloseConfirmation = Callable[[SongProjectControllerSnapshot], bool]


class SongProjectController:
    """Lifecycle and immutable-edit coordinator for one open song project."""

    def __init__(
        self,
        *,
        recent_index_path: str | Path | None = None,
        autosave_scheduler: AutosaveScheduler | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._controller_id = str(uuid.uuid4())
        self._recent_index_path = (
            Path(recent_index_path).expanduser()
            if recent_index_path is not None
            else None
        )
        self._autosave_scheduler = autosave_scheduler
        self._project: SongProject | None = None
        self._saved_project: SongProject | None = None
        self._bundle_path: Path | None = None
        self._token: str | None = None
        self._forced_dirty = False
        self._generation = 0
        self._recovery: RecoverySnapshot | None = None
        self._autosave_pending = False
        self._autosave_error = ""
        self._recent_projects: tuple[Path, ...] = ()
        self._recent_error = ""
        self._backing_verification = MediaVerificationState.UNKNOWN
        self._load_recents_initially()

    def _load_recents_initially(self) -> None:
        if self._recent_index_path is None:
            return
        try:
            recent = load_recent_projects(self._recent_index_path)
        except SongProjectStoreError:
            self._recent_error = (
                "WebJam couldn't read the recent-project list."
            )
        else:
            self._recent_projects = recent.paths

    def _dirty_locked(self) -> bool:
        if self._project is None:
            return False
        return (
            self._forced_dirty
            or self._saved_project is None
            or self._project != self._saved_project
        )

    def _snapshot_locked(self) -> SongProjectControllerSnapshot:
        return SongProjectControllerSnapshot(
            project=self._project,
            bundle_path=self._bundle_path,
            token=self._token,
            dirty=self._dirty_locked(),
            generation=self._generation,
            recovery=self._recovery,
            autosave_pending=self._autosave_pending,
            autosave_error=self._autosave_error,
            recent_projects=self._recent_projects,
            recent_error=self._recent_error,
            backing_media_verification=self._backing_verification,
        )

    @property
    def snapshot(self) -> SongProjectControllerSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def generation_token(self) -> ControllerGeneration:
        with self._lock:
            project, folder = self._require_open_locked()
            return ControllerGeneration(
                controller_id=self._controller_id,
                generation=self._generation,
                project_id=project.project_id,
                bundle_identity=str(folder),
            )

    def _token_current_locked(self, token: ControllerGeneration) -> bool:
        return (
            isinstance(token, ControllerGeneration)
            and token.controller_id == self._controller_id
            and token.generation == self._generation
            and self._project is not None
            and token.project_id == self._project.project_id
            and self._bundle_path is not None
            and token.bundle_identity == str(self._bundle_path)
        )

    def _token_document_current_locked(self, token: ControllerGeneration) -> bool:
        """Check document identity while intentionally ignoring edit generation."""

        return (
            isinstance(token, ControllerGeneration)
            and token.controller_id == self._controller_id
            and self._project is not None
            and token.project_id == self._project.project_id
            and self._bundle_path is not None
            and token.bundle_identity == str(self._bundle_path)
        )

    def _require_open_locked(self) -> tuple[SongProject, Path]:
        if self._project is None or self._bundle_path is None:
            raise SongProjectNotOpen("Open or create a project first.")
        return self._project, self._bundle_path

    def _invalidate_locked(self) -> None:
        self._generation += 1

    def _record_recent(self, bundle_path: Path) -> None:
        if self._recent_index_path is None:
            return
        try:
            recent = record_recent_project(self._recent_index_path, bundle_path)
        except SongProjectStoreError:
            with self._lock:
                self._recent_error = (
                    "WebJam couldn't update the recent-project list."
                )
            return
        with self._lock:
            self._recent_projects = recent.paths
            self._recent_error = ""

    def refresh_recent_projects(self) -> RecentProjects:
        if self._recent_index_path is None:
            with self._lock:
                return RecentProjects(self._recent_projects)
        try:
            recent = load_recent_projects(self._recent_index_path)
        except SongProjectStoreError:
            with self._lock:
                self._recent_error = (
                    "WebJam couldn't read the recent-project list."
                )
            raise SongProjectControllerError(
                "WebJam couldn't read the recent-project list."
            ) from None
        with self._lock:
            self._recent_projects = recent.paths
            self._recent_error = ""
        return recent

    def create_project(
        self,
        bundle_path: str | Path,
        name: str,
        *,
        project_sample_rate: int = 48_000,
        tempo_bpm: float = 120.0,
        time_signature: TimeSignature | None = None,
        project_id: str | None = None,
        creator_profile_key: str = "music",
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            if self._dirty_locked():
                raise SongProjectControllerError(
                    "Save or discard the current project before creating another."
                )
            self._invalidate_locked()
        try:
            saved = create_project_bundle(
                bundle_path,
                name,
                project_sample_rate=project_sample_rate,
                tempo_bpm=tempo_bpm,
                time_signature=time_signature,
                project_id=project_id,
                creator_profile_key=creator_profile_key,
            )
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't create that project."
            ) from None
        with self._lock:
            self._project = saved.project
            self._saved_project = saved.project
            self._bundle_path = saved.bundle_path
            self._token = saved.token
            self._forced_dirty = False
            self._recovery = None
            self._autosave_pending = False
            self._autosave_error = ""
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
        self._record_recent(saved.bundle_path)
        return self.snapshot

    def open_project(
        self,
        bundle_path: str | Path,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            if self._dirty_locked():
                raise SongProjectControllerError(
                    "Save or discard the current project before opening another."
                )
            self._invalidate_locked()
        try:
            loaded = load_project_bundle(bundle_path)
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely open that project."
            ) from None
        recovered_backup = loaded.origin is ProjectLoadOrigin.BACKUP
        with self._lock:
            self._project = loaded.project
            self._saved_project = None if recovered_backup else loaded.project
            self._bundle_path = loaded.bundle_path
            self._token = loaded.token
            self._forced_dirty = recovered_backup
            self._recovery = RecoverySnapshot.from_candidate(
                loaded.recovery_candidate
            )
            self._autosave_pending = recovered_backup
            self._autosave_error = ""
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
        self._record_recent(loaded.bundle_path)
        return self.snapshot

    def close_project(
        self,
        *,
        confirm_discard: CloseConfirmation | None = None,
        discard_unsaved: bool = False,
    ) -> ControllerCloseResult:
        with self._lock:
            current = self._snapshot_locked()
            dirty = current.dirty
            folder = self._bundle_path
        allowed = discard_unsaved
        if dirty and not allowed and confirm_discard is not None:
            try:
                allowed = bool(confirm_discard(current))
            except Exception:
                allowed = False
        if dirty and not allowed:
            return ControllerCloseResult(
                snapshot=current,
                closed=False,
                vetoed=True,
            )
        if dirty and folder is not None:
            try:
                discard_project_autosave(folder)
            except SongProjectStoreError:
                with self._lock:
                    self._autosave_error = (
                        "WebJam couldn't discard the recovery autosave."
                    )
                    snapshot = self._snapshot_locked()
                return ControllerCloseResult(
                    snapshot=snapshot,
                    closed=False,
                    vetoed=True,
                )
        with self._lock:
            self._project = None
            self._saved_project = None
            self._bundle_path = None
            self._token = None
            self._forced_dirty = False
            self._recovery = None
            self._autosave_pending = False
            self._autosave_error = ""
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
            snapshot = self._snapshot_locked()
        return ControllerCloseResult(snapshot=snapshot, closed=True, vetoed=False)

    def _schedule_autosave(self, token: ControllerGeneration) -> None:
        scheduler = self._autosave_scheduler
        if scheduler is None:
            return

        def callback() -> AutosaveFlushResult:
            return self.flush_autosave(generation=token)

        try:
            scheduler(callback)
        except Exception:
            with self._lock:
                if self._token_current_locked(token):
                    self._autosave_error = (
                        "WebJam couldn't schedule automatic recovery."
                    )

    def _replace_project(
        self,
        project: SongProject,
        *,
        clear_recovery: bool = False,
        expected_generation: ControllerGeneration | None = None,
    ) -> SongProjectControllerSnapshot | None:
        with self._lock:
            current, folder = self._require_open_locked()
            if (
                expected_generation is not None
                and not self._token_current_locked(expected_generation)
            ):
                return None
            if not isinstance(project, SongProject):
                raise SongProjectControllerError(
                    "Project replacement must be an immutable SongProject."
                )
            if project.project_id != current.project_id:
                raise SongProjectControllerError(
                    "Project replacement belongs to a different project."
                )
            self._project = project
            if clear_recovery:
                self._recovery = None
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
            dirty = self._dirty_locked()
            recovery_blocks_autosave = dirty and self._recovery is not None
            self._autosave_pending = dirty and not recovery_blocks_autosave
            self._autosave_error = (
                "Choose Recover or Discard before automatic recovery can resume."
                if recovery_blocks_autosave
                else ""
            )
            token = ControllerGeneration(
                controller_id=self._controller_id,
                generation=self._generation,
                project_id=project.project_id,
                bundle_identity=str(folder),
            )
        if dirty and not recovery_blocks_autosave:
            self._schedule_autosave(token)
        elif not dirty and self._recovery is None:
            try:
                discard_project_autosave(folder)
            except SongProjectStoreError:
                with self._lock:
                    if self._token_current_locked(token):
                        self._autosave_error = (
                            "WebJam couldn't clear an obsolete recovery autosave."
                        )
        return self.snapshot

    def replace_project(
        self,
        project: SongProject,
    ) -> SongProjectControllerSnapshot:
        """Install an immutable edit; callers may retain the prior value for undo."""

        snapshot = self._replace_project(project)
        assert snapshot is not None
        return snapshot

    def save_project(self) -> ControllerSaveResult:
        with self._lock:
            project, folder = self._require_open_locked()
            if not self._dirty_locked():
                return ControllerSaveResult(
                    snapshot=self._snapshot_locked(),
                    saved=False,
                )
            expected = self._token
            operation_token = self.generation_token()
        try:
            saved = save_project_bundle(
                folder,
                project,
                expected_token=expected,
            )
        except SongProjectConflict:
            raise SongProjectControllerConflict(
                "This project changed in another window. Reopen it before saving."
            ) from None
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely save this project."
            ) from None
        reschedule: ControllerGeneration | None = None
        with self._lock:
            if not self._token_current_locked(operation_token):
                if self._token_document_current_locked(operation_token):
                    # The captured immutable revision did save successfully,
                    # while a newer in-memory revision arrived during I/O.
                    # Advance the CAS baseline without replacing the newer
                    # project, so its next save cannot conflict with our own.
                    self._saved_project = saved.project
                    self._token = saved.token
                    self._forced_dirty = False
                    self._autosave_pending = self._dirty_locked()
                    self._autosave_error = (
                        ""
                        if saved.autosave_cleared
                        else "The project saved, but an obsolete autosave remains."
                    )
                    self._invalidate_locked()
                    if self._dirty_locked():
                        current, current_folder = self._require_open_locked()
                        reschedule = ControllerGeneration(
                            controller_id=self._controller_id,
                            generation=self._generation,
                            project_id=current.project_id,
                            bundle_identity=str(current_folder),
                        )
                    snapshot = self._snapshot_locked()
                else:
                    snapshot = self._snapshot_locked()
                result = ControllerSaveResult(
                    snapshot=snapshot,
                    saved=True,
                    stale=True,
                )
            else:
                self._project = saved.project
                self._saved_project = saved.project
                self._token = saved.token
                self._forced_dirty = False
                self._autosave_pending = False
                self._autosave_error = (
                    ""
                    if saved.autosave_cleared
                    else "The project saved, but an obsolete autosave remains."
                )
                self._invalidate_locked()
                result = ControllerSaveResult(
                    snapshot=self._snapshot_locked(),
                    saved=True,
                )
        if reschedule is not None:
            self._schedule_autosave(reschedule)
            result = replace(result, snapshot=self.snapshot)
        return result

    def save_project_as(
        self,
        destination_bundle_path: str | Path,
        *,
        new_project_id: str | None = None,
    ) -> ControllerSaveResult:
        with self._lock:
            project, folder = self._require_open_locked()
            expected = self._token
            operation_token = self.generation_token()
        try:
            saved = save_project_as(
                folder,
                destination_bundle_path,
                project,
                expected_token=expected,
                new_project_id=new_project_id,
            )
        except SongProjectConflict:
            raise SongProjectControllerConflict(
                "This project changed before Save As completed."
            ) from None
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely create the project copy."
            ) from None
        with self._lock:
            if not self._token_current_locked(operation_token):
                return ControllerSaveResult(
                    snapshot=self._snapshot_locked(),
                    saved=True,
                    stale=True,
                )
            self._project = saved.project
            self._saved_project = saved.project
            self._bundle_path = saved.bundle_path
            self._token = saved.token
            self._forced_dirty = False
            self._recovery = None
            self._autosave_pending = False
            self._autosave_error = ""
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
        self._record_recent(saved.bundle_path)
        return ControllerSaveResult(snapshot=self.snapshot, saved=True)

    def flush_autosave(
        self,
        *,
        generation: ControllerGeneration | None = None,
    ) -> AutosaveFlushResult:
        with self._lock:
            project, folder = self._require_open_locked()
            operation_token = generation or self.generation_token()
            if not self._token_current_locked(operation_token):
                return AutosaveFlushResult(
                    snapshot=self._snapshot_locked(),
                    written=False,
                    stale=True,
                )
            if not self._dirty_locked():
                self._autosave_pending = False
                return AutosaveFlushResult(
                    snapshot=self._snapshot_locked(),
                    written=False,
                )
            expected = self._token
        try:
            write_project_autosave(
                folder,
                project,
                base_primary_token=expected,
            )
        except (SongProjectError, SongProjectStoreError, OSError):
            message = "WebJam couldn't update the automatic recovery copy."
            with self._lock:
                if self._token_current_locked(operation_token):
                    self._autosave_pending = True
                    self._autosave_error = message
                    snapshot = self._snapshot_locked()
                    return AutosaveFlushResult(
                        snapshot=snapshot,
                        written=False,
                        error=message,
                    )
                return AutosaveFlushResult(
                    snapshot=self._snapshot_locked(),
                    written=False,
                    stale=True,
                )
        with self._lock:
            if not self._token_current_locked(operation_token):
                return AutosaveFlushResult(
                    snapshot=self._snapshot_locked(),
                    written=True,
                    stale=True,
                )
            self._autosave_pending = False
            self._autosave_error = ""
            return AutosaveFlushResult(
                snapshot=self._snapshot_locked(),
                written=True,
            )

    def recover_autosave(self) -> SongProjectControllerSnapshot:
        with self._lock:
            _project, folder = self._require_open_locked()
            if self._recovery is None:
                raise SongProjectControllerError(
                    "No crash-recovery autosave is available."
                )
            if self._dirty_locked():
                raise SongProjectControllerError(
                    "Discard current edits before recovering the autosave."
                )
            expected = self._token
            operation_token = self.generation_token()
        try:
            saved = recover_project_autosave(folder, expected_token=expected)
        except SongProjectConflict:
            raise SongProjectControllerConflict(
                "The project changed before recovery completed."
            ) from None
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely recover the autosave."
            ) from None
        with self._lock:
            if not self._token_current_locked(operation_token):
                raise SongProjectControllerError(
                    "The project changed before recovery completed."
                ) from None
            self._project = saved.project
            self._saved_project = saved.project
            self._token = saved.token
            self._forced_dirty = False
            self._recovery = None
            self._autosave_pending = False
            self._autosave_error = ""
            self._backing_verification = MediaVerificationState.UNKNOWN
            self._invalidate_locked()
            return self._snapshot_locked()

    def discard_recovery(self) -> SongProjectControllerSnapshot:
        with self._lock:
            _project, folder = self._require_open_locked()
            if self._recovery is None:
                return self._snapshot_locked()
            operation_token = self.generation_token()
        try:
            discard_project_autosave(folder)
        except SongProjectStoreError:
            raise SongProjectControllerError(
                "WebJam couldn't discard the recovery autosave."
            ) from None
        schedule: ControllerGeneration | None = None
        with self._lock:
            if self._token_current_locked(operation_token):
                self._recovery = None
                if self._dirty_locked():
                    self._autosave_pending = True
                    self._autosave_error = ""
                    current, current_folder = self._require_open_locked()
                    schedule = ControllerGeneration(
                        controller_id=self._controller_id,
                        generation=self._generation,
                        project_id=current.project_id,
                        bundle_identity=str(current_folder),
                    )
            snapshot = self._snapshot_locked()
        if schedule is not None:
            self._schedule_autosave(schedule)
            snapshot = self.snapshot
        return snapshot

    def _edited_tracks(
        self,
        tracks: tuple[SongTrack, ...],
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = replace(
                project,
                tracks=tracks,
                revision=project.revision + 1,
            )
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    @staticmethod
    def _track_index(project: SongProject, track_id: str) -> int:
        for index, track in enumerate(project.tracks):
            if track.track_id == track_id:
                return index
        raise SongProjectControllerError("That project track was not found.")

    def add_track(
        self,
        name: str,
        *,
        input_mapping: InputMapping | None = None,
        track_id: str | None = None,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = project.add_track(
                name,
                input_mapping=input_mapping,
                track_id=track_id,
            )
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    def rename_project(self, name: str) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = project.rename(name)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    def remove_track(self, track_id: str) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = project.remove_track(track_id)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    def set_tempo(self, tempo_bpm: float) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = project.set_tempo(tempo_bpm)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    def set_time_signature(
        self,
        numerator: int,
        denominator: int,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
        try:
            edited = project.set_time_signature(numerator, denominator)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        return self._replace_project(edited)

    def rename_track(
        self,
        track_id: str,
        name: str,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
            index = self._track_index(project, track_id)
        try:
            renamed = replace(project.tracks[index], name=name)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        tracks = list(project.tracks)
        tracks[index] = renamed
        return self._edited_tracks(tuple(tracks))

    def reorder_track(
        self,
        track_id: str,
        new_index: int,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
            old_index = self._track_index(project, track_id)
        if (
            isinstance(new_index, bool)
            or not isinstance(new_index, int)
            or not 0 <= new_index < len(project.tracks)
        ):
            raise SongProjectControllerError(
                "Track position is outside the project."
            )
        tracks = list(project.tracks)
        moved = tracks.pop(old_index)
        tracks.insert(new_index, moved)
        ordered = tuple(
            replace(track, order=index) for index, track in enumerate(tracks)
        )
        return self._edited_tracks(ordered)

    def set_track_armed(
        self,
        track_id: str,
        armed: bool,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
            index = self._track_index(project, track_id)
        try:
            updated = replace(project.tracks[index], armed=armed)
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        tracks = list(project.tracks)
        tracks[index] = updated
        return self._edited_tracks(tuple(tracks))

    def set_track_input_mapping(
        self,
        track_id: str,
        input_mapping: InputMapping | None,
    ) -> SongProjectControllerSnapshot:
        with self._lock:
            project, _folder = self._require_open_locked()
            index = self._track_index(project, track_id)
        try:
            updated = replace(
                project.tracks[index],
                input_mapping=input_mapping,
            )
        except SongProjectError as exc:
            raise SongProjectControllerError(str(exc)) from None
        tracks = list(project.tracks)
        tracks[index] = updated
        return self._edited_tracks(tuple(tracks))

    def import_backing_media(
        self,
        source_path: str | Path,
        *,
        generation: ControllerGeneration | None = None,
        provenance: MediaProvenance = MediaProvenance.LOCAL_FILE,
        import_method: MediaImportMethod = MediaImportMethod.COPY,
        provenance_detail: str = "",
        media_id: str | None = None,
    ) -> MediaControllerResult:
        with self._lock:
            project, folder = self._require_open_locked()
            operation_token = generation or self.generation_token()
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                )
        try:
            imported = import_project_media(
                folder,
                project,
                source_path,
                designate_backing=True,
                provenance=provenance,
                import_method=import_method,
                provenance_detail=provenance_detail,
                media_id=media_id,
            )
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely import that audio file."
            ) from None
        with self._lock:
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                    media=imported.media,
                )
        snapshot = self._replace_project(
            imported.project,
            expected_generation=operation_token,
        )
        if snapshot is None:
            return MediaControllerResult(
                snapshot=self.snapshot,
                applied=False,
                stale=True,
                media=imported.media,
            )
        with self._lock:
            if (
                self._project == imported.project
                and self._generation == snapshot.generation
            ):
                self._backing_verification = MediaVerificationState.VERIFIED
            snapshot = self._snapshot_locked()
        return MediaControllerResult(
            snapshot=snapshot,
            applied=True,
            stale=False,
            media=imported.media,
            verified=True,
        )

    def import_media(
        self,
        source_path: str | Path,
        *,
        generation: ControllerGeneration | None = None,
        provenance: MediaProvenance = MediaProvenance.LOCAL_FILE,
        import_method: MediaImportMethod = MediaImportMethod.COPY,
        provenance_detail: str = "",
        media_id: str | None = None,
    ) -> MediaControllerResult:
        """Collect non-backing media under the same stale-result guard."""

        with self._lock:
            project, folder = self._require_open_locked()
            operation_token = generation or self.generation_token()
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                )
        try:
            imported = import_project_media(
                folder,
                project,
                source_path,
                designate_backing=False,
                provenance=provenance,
                import_method=import_method,
                provenance_detail=provenance_detail,
                media_id=media_id,
            )
        except (SongProjectError, SongProjectStoreError, OSError):
            raise SongProjectControllerError(
                "WebJam couldn't safely import that audio file."
            ) from None
        snapshot = self._replace_project(
            imported.project,
            expected_generation=operation_token,
        )
        if snapshot is None:
            return MediaControllerResult(
                snapshot=self.snapshot,
                applied=False,
                stale=True,
                media=imported.media,
            )
        return MediaControllerResult(
            snapshot=snapshot,
            applied=True,
            stale=False,
            media=imported.media,
            verified=True,
        )

    def _backing_media_locked(self) -> tuple[SongProject, Path, SongMedia]:
        project, folder = self._require_open_locked()
        if project.backing_media_id is None:
            raise SongProjectControllerError(
                "This project does not have a backing track."
            )
        try:
            media = project.media_by_id(project.backing_media_id)
        except SongProjectError:
            raise SongProjectControllerError(
                "The backing-track descriptor is invalid."
            ) from None
        return project, folder, media

    def relink_backing_media(
        self,
        source_path: str | Path,
        *,
        generation: ControllerGeneration | None = None,
    ) -> MediaControllerResult:
        with self._lock:
            project, folder, media = self._backing_media_locked()
            operation_token = generation or self.generation_token()
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                    media=media,
                )
        try:
            relinked = relink_project_media(
                folder,
                project,
                media.media_id,
                source_path,
            )
        except (SongProjectError, SongProjectStoreError, OSError):
            with self._lock:
                if self._token_current_locked(operation_token):
                    self._backing_verification = MediaVerificationState.INVALID
            raise SongProjectControllerError(
                "WebJam couldn't verify and relink that backing track."
            ) from None
        with self._lock:
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                    media=relinked.media,
                )
            self._backing_verification = MediaVerificationState.VERIFIED
            self._invalidate_locked()
            snapshot = self._snapshot_locked()
        return MediaControllerResult(
            snapshot=snapshot,
            applied=True,
            stale=False,
            media=relinked.media,
            verified=True,
        )

    def verify_backing_media(
        self,
        *,
        generation: ControllerGeneration | None = None,
    ) -> MediaControllerResult:
        with self._lock:
            project, folder, media = self._backing_media_locked()
            operation_token = generation or self.generation_token()
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                    media=media,
                )
        try:
            verify_project_media(folder, project)
        except (SongProjectError, SongProjectStoreError, OSError):
            with self._lock:
                if self._token_current_locked(operation_token):
                    self._backing_verification = MediaVerificationState.INVALID
            raise SongProjectControllerError(
                "WebJam couldn't verify the collected backing track."
            ) from None
        with self._lock:
            if not self._token_current_locked(operation_token):
                return MediaControllerResult(
                    snapshot=self._snapshot_locked(),
                    applied=False,
                    stale=True,
                    media=media,
                )
            self._backing_verification = MediaVerificationState.VERIFIED
            snapshot = self._snapshot_locked()
        return MediaControllerResult(
            snapshot=snapshot,
            applied=True,
            stale=False,
            media=media,
            verified=True,
        )

    # Concise aliases for application/controller call sites.
    create = create_project
    open = open_project
    close = close_project
    save = save_project
    save_as = save_project_as
    arm_track = set_track_armed
    map_track_input = set_track_input_mapping


__all__ = [
    "AutosaveCallback",
    "AutosaveFlushResult",
    "AutosaveScheduler",
    "CloseConfirmation",
    "ControllerCloseResult",
    "ControllerGeneration",
    "ControllerSaveResult",
    "MediaControllerResult",
    "MediaVerificationState",
    "RecoverySnapshot",
    "SongProjectController",
    "SongProjectControllerConflict",
    "SongProjectControllerError",
    "SongProjectControllerSnapshot",
    "SongProjectNotOpen",
]

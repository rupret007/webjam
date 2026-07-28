"""Safe Save-As cloning for schema-3 Reference Studio projects.

``SongProject`` Save As deliberately preserves durable media and musician
track IDs while assigning a new project ID.  Most schema-3 Studio identities
therefore survive unchanged.  The two exceptions are the deterministic
backing-track and base-backing-region IDs, whose UUIDv5 inputs include the
project ID.  This module remaps those identities and every typed dependent
reference without reinterpreting user edits.

The high-level transaction builds the complete destination in a hidden sibling
bundle, verifies both primary documents, and only then publishes the finished
bundle with one directory rename.  A Studio failure can consequently remove
the unpublished staging bundle without changing the source or leaving a
partially usable destination at the requested path.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from core.project_recording_commit import (
    RECORDING_COMMIT_JOURNAL_FILENAME,
    RECORDING_EVIDENCE_FILENAME,
    ProjectRecordingCommitError,
    copy_recording_evidence_for_project_copy,
)
from core.song_project import SongProject
from core.song_project_store import (
    PROJECT_AUTOSAVE_FILENAME,
    PROJECT_BACKUP_FILENAME,
    PROJECT_MANIFEST_FILENAME,
    ProjectLoadOrigin,
    ProjectSaveResult,
    SongProjectConflict,
    SongProjectStoreError,
    load_project_bundle,
    save_project_as,
)
from core.song_studio_store import (
    SONG_STUDIO_AUTOSAVE_FILENAME,
    SONG_STUDIO_BACKUP_FILENAME,
    SONG_STUDIO_FILENAME,
    SongStudioLoadOrigin,
    SongStudioSaveResult,
    SongStudioStoreError,
    load_song_studio_document,
    save_song_studio_document,
)
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
    StudioTrackKind,
    default_song_studio_document,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SongStudioCloneError(ValueError):
    """Raised when Save As cannot preserve Studio identity without ambiguity."""


class SongStudioSaveAsError(SongStudioCloneError):
    """Raised when the complete project-and-Studio transaction cannot publish."""


class SongStudioSaveAsConflict(SongStudioSaveAsError):
    """Raised when source bytes changed after the UI loaded them."""


@dataclass(frozen=True)
class SongStudioSaveAsResult:
    """The fully published destination and its clean exact-save tokens."""

    project: SongProject
    document: StudioDocument
    bundle_path: Path
    project_token: str
    studio_token: str
    project_save: ProjectSaveResult
    studio_save: SongStudioSaveResult


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SongStudioSaveAsError(f"{label} must be a lowercase SHA-256.")
    return value


def _optional_sha256(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, label)


def _project_lineage(project: SongProject) -> dict[str, object]:
    value = project.to_dict()
    value.pop("project_id")
    return value


def _default_backing_identities(
    project: SongProject,
) -> tuple[str, str] | None:
    if project.backing_media_id is None:
        return None
    try:
        default = default_song_studio_document(project)
    except StudioProjectError as exc:
        raise SongStudioCloneError(
            "Project backing identity cannot form a valid Studio document."
        ) from exc
    backing_tracks = tuple(
        track for track in default.tracks if track.kind is StudioTrackKind.BACKING
    )
    if len(backing_tracks) != 1 or len(default.regions) != 1:
        raise SongStudioCloneError(
            "Project backing identity is incomplete or ambiguous."
        )
    return backing_tracks[0].track_id, default.regions[0].region_id


def _validate_source_document(
    project: SongProject,
    document: StudioDocument,
) -> None:
    if not isinstance(project, SongProject):
        raise SongStudioCloneError("Source project must be a SongProject value.")
    if not isinstance(document, StudioDocument):
        raise SongStudioCloneError(
            "Source Studio state must be a StudioDocument value."
        )
    if document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION:
        raise SongStudioCloneError(
            "Save As accepts only schema-3 Song Studio documents."
        )
    if (
        document.project_id != project.project_id
        or document.project_sample_rate != project.project_sample_rate
    ):
        raise SongStudioCloneError(
            "Source Studio state belongs to a different project."
        )

    project_track_ids = {track.track_id for track in project.tracks}
    studio_tracks = {track.track_id: track for track in document.tracks}
    missing = project_track_ids.difference(studio_tracks)
    if missing:
        raise SongStudioCloneError(
            "Source Studio state is missing a durable project track."
        )
    if any(
        studio_tracks[track_id].kind is not StudioTrackKind.AUDIO
        for track_id in project_track_ids
    ):
        raise SongStudioCloneError(
            "A durable project track has an incompatible Studio track kind."
        )

    backing = _default_backing_identities(project)
    backing_track_id = backing[0] if backing is not None else None
    allowed_non_project_ids = (
        {backing_track_id} if backing_track_id is not None else set()
    )
    if any(
        track.kind is StudioTrackKind.AUDIO and track.track_id not in project_track_ids
        for track in document.tracks
    ):
        raise SongStudioCloneError(
            "A Studio audio track has no durable project-track identity."
        )
    if any(
        track.kind is StudioTrackKind.BACKING
        and track.track_id not in allowed_non_project_ids
        for track in document.tracks
    ):
        raise SongStudioCloneError(
            "Source Studio state contains an ambiguous backing track."
        )
    actual_backing = tuple(
        track for track in document.tracks if track.kind is StudioTrackKind.BACKING
    )
    if backing is None:
        if actual_backing:
            raise SongStudioCloneError(
                "Source Studio state has backing state but the project does not."
            )
    elif len(actual_backing) != 1 or actual_backing[0].track_id != backing_track_id:
        raise SongStudioCloneError(
            "Source Studio backing track does not have its deterministic identity."
        )

    media_ids = {media.media_id for media in project.media}
    if any(region.source_media_id not in media_ids for region in document.regions):
        raise SongStudioCloneError(
            "A Studio region references media outside the source project."
        )
    if any(lane.source_media_id not in media_ids for lane in document.take_lanes):
        raise SongStudioCloneError(
            "A Studio take lane references media outside the source project."
        )

    if backing is not None:
        backing_track_id, backing_region_id = backing
        matching_regions = tuple(
            region
            for region in document.regions
            if region.region_id == backing_region_id
        )
        if len(matching_regions) != 1:
            raise SongStudioCloneError(
                "Source Studio backing base region is missing or ambiguous."
            )
        base = matching_regions[0]
        if (
            base.track_id != backing_track_id
            or base.source_media_id != project.backing_media_id
        ):
            raise SongStudioCloneError(
                "Source Studio backing base region has incompatible lineage."
            )


def clone_song_studio_document(
    source_project: SongProject,
    destination_project: SongProject,
    source_document: StudioDocument,
) -> StudioDocument:
    """Clone one schema-3 arrangement to a Save-As project identity.

    The destination must be the exact project clone produced by
    :func:`core.song_project_store.save_project_as`: only ``project_id`` may
    differ.  User-created IDs and every edit remain unchanged.  The returned
    document is intentionally detached from any source-store token.
    """

    _validate_source_document(source_project, source_document)
    if not isinstance(destination_project, SongProject):
        raise SongStudioCloneError("Destination project must be a SongProject value.")
    if destination_project.project_id == source_project.project_id:
        raise SongStudioCloneError(
            "Save As destination must have a new project identity."
        )
    if _project_lineage(destination_project) != _project_lineage(source_project):
        raise SongStudioCloneError(
            "Save As destination does not preserve exact project lineage."
        )

    source_backing = _default_backing_identities(source_project)
    destination_backing = _default_backing_identities(destination_project)
    if bool(source_backing) != bool(destination_backing):
        raise SongStudioCloneError(
            "Save As destination backing lineage does not match the source."
        )

    track_id_map: dict[str, str] = {}
    region_id_map: dict[str, str] = {}
    if source_backing is not None and destination_backing is not None:
        old_track_id, old_region_id = source_backing
        new_track_id, new_region_id = destination_backing
        if any(
            track.track_id == new_track_id and track.track_id != old_track_id
            for track in source_document.tracks
        ):
            raise SongStudioCloneError(
                "Destination backing-track identity collides with Studio state."
            )
        if any(
            region.region_id == new_region_id and region.region_id != old_region_id
            for region in source_document.regions
        ):
            raise SongStudioCloneError(
                "Destination backing-region identity collides with Studio state."
            )
        track_id_map[old_track_id] = new_track_id
        region_id_map[old_region_id] = new_region_id

    def mapped_track(value: str) -> str:
        return track_id_map.get(value, value)

    def mapped_region(value: str) -> str:
        return region_id_map.get(value, value)

    def cloned_track(track):
        output_bus_id = mapped_track(track.output_bus_id) if track.output_bus_id else ""
        return replace(
            track,
            track_id=mapped_track(track.track_id),
            output_bus_id=output_bus_id,
            sends=tuple(
                replace(send, target_bus_id=mapped_track(send.target_bus_id))
                for send in track.sends
            ),
        )

    try:
        cloned = replace(
            source_document,
            project_id=destination_project.project_id,
            tracks=tuple(cloned_track(track) for track in source_document.tracks),
            regions=tuple(
                replace(
                    region,
                    region_id=mapped_region(region.region_id),
                    track_id=mapped_track(region.track_id),
                )
                for region in source_document.regions
            ),
            take_lanes=tuple(
                replace(
                    lane,
                    track_id=mapped_track(lane.track_id),
                    region_ids=tuple(
                        mapped_region(region_id) for region_id in lane.region_ids
                    ),
                )
                for lane in source_document.take_lanes
            ),
            comp_ranges=tuple(
                replace(comp, track_id=mapped_track(comp.track_id))
                for comp in source_document.comp_ranges
            ),
            crossfades=tuple(
                replace(
                    crossfade,
                    left_region_id=mapped_region(crossfade.left_region_id),
                    right_region_id=mapped_region(crossfade.right_region_id),
                )
                for crossfade in source_document.crossfades
            ),
            _store_token=None,
        )
    except StudioProjectError as exc:
        raise SongStudioCloneError(
            "Studio state cannot be remapped without losing edit relationships."
        ) from exc
    _validate_source_document(destination_project, cloned)
    return cloned


def _lstat_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not inspect the Save As destination."
        ) from exc


def _safe_remove_stage(stage: Path) -> None:
    if not _lstat_exists(stage):
        return
    try:
        info = stage.lstat()
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not inspect the unpublished Save As staging bundle."
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SongStudioSaveAsError(
            "Unpublished Save As staging state was replaced unexpectedly."
        )
    try:
        shutil.rmtree(stage)
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not remove the unpublished Save As staging bundle."
        ) from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not durably publish the Save As project."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _clean_stage_state(stage: Path) -> None:
    forbidden = (
        PROJECT_BACKUP_FILENAME,
        PROJECT_AUTOSAVE_FILENAME,
        SONG_STUDIO_BACKUP_FILENAME,
        SONG_STUDIO_AUTOSAVE_FILENAME,
    )
    if any(_lstat_exists(stage / name) for name in forbidden):
        raise SongStudioSaveAsError(
            "Save As staging contains recovery state that must not be cloned."
        )
    try:
        names = tuple(item.name for item in stage.iterdir())
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not verify the Save As staging bundle."
        ) from exc
    if any(
        name.startswith(".webjam-song-studio.corrupt-")
        or name.startswith(".webjam-project.corrupt-")
        for name in names
    ):
        raise SongStudioSaveAsError(
            "Save As staging contains unexpected corrupt recovery state."
        )


def _recheck_source_tokens(
    source: Path,
    source_project: SongProject,
    *,
    expected_project_token: str,
    expected_studio_token: str | None,
) -> None:
    """Close the copy-duration race before the completed clone is published."""

    try:
        current_project = load_project_bundle(source)
    except SongProjectStoreError as exc:
        raise SongStudioSaveAsConflict(
            "Source project became unavailable during Save As."
        ) from exc
    if (
        current_project.token != expected_project_token
        or current_project.project.project_id != source_project.project_id
    ):
        raise SongStudioSaveAsConflict("Source project changed during Save As.")
    try:
        current_studio = load_song_studio_document(source, source_project)
    except SongStudioStoreError as exc:
        raise SongStudioSaveAsConflict(
            "Source Studio state became unavailable during Save As."
        ) from exc
    if current_studio.token != expected_studio_token:
        raise SongStudioSaveAsConflict("Source Studio state changed during Save As.")


def save_song_studio_project_as(
    source_bundle_path: str | Path,
    destination_bundle_path: str | Path,
    source_project: SongProject,
    source_document: StudioDocument,
    *,
    expected_project_token: str,
    expected_studio_token: str | None,
    new_project_id: str | None = None,
) -> SongStudioSaveAsResult:
    """Crash-safely publish one independent project plus Studio arrangement.

    Dirty in-memory project/Studio values may be supplied.  The expected tokens
    still guard the two source primaries from races; neither source primary nor
    its autosave/backup state is changed.  The requested destination remains
    absent until its project manifest, media, and Studio primary all reload
    successfully from a hidden sibling staging bundle.
    """

    project_token = _require_sha256(
        expected_project_token,
        "expected_project_token",
    )
    studio_token = _optional_sha256(
        expected_studio_token,
        "expected_studio_token",
    )
    _validate_source_document(source_project, source_document)

    try:
        loaded_source = load_project_bundle(source_bundle_path)
    except SongProjectStoreError as exc:
        raise SongStudioSaveAsError(
            "Could not verify the source project for Save As."
        ) from exc
    if (
        loaded_source.token != project_token
        or loaded_source.project.project_id != source_project.project_id
    ):
        raise SongStudioSaveAsConflict("Source project changed before Save As.")
    try:
        loaded_studio = load_song_studio_document(
            loaded_source.bundle_path,
            source_project,
        )
    except SongStudioStoreError as exc:
        raise SongStudioSaveAsError(
            "Could not verify the source Studio state for Save As."
        ) from exc
    if loaded_studio.token != studio_token:
        raise SongStudioSaveAsConflict("Source Studio state changed before Save As.")

    raw_destination = Path(destination_bundle_path).expanduser()
    if not raw_destination.is_absolute():
        raw_destination = Path.cwd() / raw_destination
    source = loaded_source.bundle_path
    unresolved_destination = raw_destination.resolve(strict=False)
    if unresolved_destination == source or source in unresolved_destination.parents:
        raise SongStudioSaveAsError(
            "Save As destination must be outside the source project bundle."
        )
    try:
        raw_destination.parent.mkdir(parents=True, exist_ok=True)
        parent = raw_destination.parent.resolve(strict=True)
    except OSError as exc:
        raise SongStudioSaveAsError(
            "Could not prepare the Save As destination folder."
        ) from exc
    destination = parent / raw_destination.name
    if destination == source or source in parent.parents or parent == source:
        raise SongStudioSaveAsError(
            "Save As destination must be outside the source project bundle."
        )
    if _lstat_exists(destination):
        raise SongStudioSaveAsError("Save As destination already exists.")

    stage = parent / f".{destination.name}.{uuid.uuid4().hex}.saving"
    while _lstat_exists(stage):
        stage = parent / f".{destination.name}.{uuid.uuid4().hex}.saving"

    published = False
    staged_project_save: ProjectSaveResult | None = None
    staged_studio_save: SongStudioSaveResult | None = None
    try:
        try:
            staged_project_save = save_project_as(
                source,
                stage,
                source_project,
                expected_token=project_token,
                new_project_id=new_project_id,
            )
        except SongProjectConflict as exc:
            raise SongStudioSaveAsConflict(
                "Source project changed before Save As."
            ) from exc
        except SongProjectStoreError as exc:
            raise SongStudioSaveAsError(
                "Could not clone the source project for Save As."
            ) from exc

        cloned = clone_song_studio_document(
            source_project,
            staged_project_save.project,
            source_document,
        )
        try:
            staged_studio_save = save_song_studio_document(
                stage,
                staged_project_save.project,
                cloned,
                expected_token=None,
            )
        except SongStudioStoreError as exc:
            raise SongStudioSaveAsError(
                "Could not save Studio state into the Save As project."
            ) from exc

        if _lstat_exists(source / RECORDING_EVIDENCE_FILENAME) or _lstat_exists(
            source / RECORDING_COMMIT_JOURNAL_FILENAME
        ):
            try:
                copy_recording_evidence_for_project_copy(
                    source,
                    source_project,
                    stage,
                    staged_project_save.project,
                    expected_source_token=project_token,
                    expected_destination_token=staged_project_save.token,
                )
            except ProjectRecordingCommitError as exc:
                raise SongStudioSaveAsError(
                    "Could not preserve recording evidence in the Save As project."
                ) from exc

        verified_project = load_project_bundle(stage)
        verified_studio = load_song_studio_document(
            stage,
            verified_project.project,
        )
        if (
            verified_project.origin is not ProjectLoadOrigin.PRIMARY
            or verified_project.token != staged_project_save.token
            or verified_project.project != staged_project_save.project
            or verified_studio.origin is not SongStudioLoadOrigin.PRIMARY
            or verified_studio.token != staged_studio_save.token
            or verified_studio.document != staged_studio_save.document
            or verified_studio.recovery_candidate is not None
        ):
            raise SongStudioSaveAsError(
                "Save As staging did not reload as the exact saved project."
            )
        _clean_stage_state(stage)
        _recheck_source_tokens(
            source,
            source_project,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
        )
        if _lstat_exists(destination):
            raise SongStudioSaveAsError("Save As destination already exists.")
        try:
            os.rename(stage, destination)
        except OSError as exc:
            raise SongStudioSaveAsError(
                "Could not publish the completed Save As project."
            ) from exc
        published = True
        _fsync_directory(parent)

        final_project_save = replace(
            staged_project_save,
            bundle_path=destination,
            manifest_path=destination / PROJECT_MANIFEST_FILENAME,
        )
        final_studio_save = replace(
            staged_studio_save,
            path=destination / SONG_STUDIO_FILENAME,
        )
        return SongStudioSaveAsResult(
            project=final_project_save.project,
            document=final_studio_save.document,
            bundle_path=destination,
            project_token=final_project_save.token,
            studio_token=final_studio_save.token,
            project_save=final_project_save,
            studio_save=final_studio_save,
        )
    except SongStudioCloneError:
        raise
    except (SongProjectStoreError, SongStudioStoreError) as exc:
        raise SongStudioSaveAsError(
            "Could not verify the completed Save As project."
        ) from exc
    finally:
        if not published:
            _safe_remove_stage(stage)


__all__ = [
    "SongStudioCloneError",
    "SongStudioSaveAsConflict",
    "SongStudioSaveAsError",
    "SongStudioSaveAsResult",
    "clone_song_studio_document",
    "save_song_studio_project_as",
]

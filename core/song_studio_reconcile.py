"""Reconcile durable song-project inventory into schema-3 Studio state.

The song manifest owns project tracks and collected media.  The Studio
sidecar owns arrangement and mix decisions.  This module is the narrow bridge
between them: project-controlled fields follow durable IDs while existing
non-destructive edits, history-worthy mix values, regions, take lanes, comps,
markers, and fades remain untouched.
"""

from __future__ import annotations

from dataclasses import replace

from core.song_project import SongProject
from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
    StudioRegion,
    StudioTrack,
    StudioTrackKind,
    default_song_studio_document,
)


class SongStudioReconcileError(ValueError):
    """Raised when project inventory cannot be merged without losing edits."""


def _reconciled_track(existing: StudioTrack, desired: StudioTrack) -> StudioTrack:
    """Keep mix choices while accepting manifest-owned identity fields."""

    return replace(
        existing,
        order=desired.order,
        name=desired.name,
        kind=desired.kind,
        channel_count=desired.channel_count,
        armed=desired.armed,
        input_monitoring=desired.input_monitoring,
    )


def _reconciled_backing_region(
    existing: StudioRegion,
    desired: StudioRegion,
) -> StudioRegion:
    """Refresh immutable source mapping without erasing fades or enable state."""

    return replace(
        existing,
        track_id=desired.track_id,
        source_take_id="",
        source_track_id="",
        source_segment_id="",
        source_media_id=desired.source_media_id,
        source_start_frame=desired.source_start_frame,
        source_frame_count=desired.source_frame_count,
        timeline_start_frame=desired.timeline_start_frame,
        timeline_frame_count=desired.timeline_frame_count,
        mapping_source_start_frame=desired.mapping_source_start_frame,
        mapping_timeline_start_frame=desired.mapping_timeline_start_frame,
        mapping_source_frame_count=desired.mapping_source_frame_count,
        mapping_timeline_frame_count=desired.mapping_timeline_frame_count,
    )


def reconcile_song_studio_document(
    project: SongProject,
    document: StudioDocument,
) -> StudioDocument:
    """Merge current manifest tracks/backing media into one Studio snapshot.

    Track and media IDs are the authority.  Adding or renaming a project track
    therefore does not reset faders, pans, regions, comping, markers, or
    history.  Replacing a backing file tombstones the old backing region and
    adds the new deterministic region, so undo/recovery evidence never points
    at silently repurposed bytes.
    """

    if not isinstance(project, SongProject):
        raise SongStudioReconcileError("Song Studio requires a SongProject.")
    if not isinstance(document, StudioDocument):
        raise SongStudioReconcileError(
            "Song Studio reconciliation requires a StudioDocument."
        )
    if (
        document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION
        or document.project_id != project.project_id
        or document.project_sample_rate != project.project_sample_rate
    ):
        raise SongStudioReconcileError(
            "Song Studio state belongs to a different project."
        )

    try:
        desired = default_song_studio_document(project)
    except StudioProjectError as exc:
        raise SongStudioReconcileError(
            "Project inventory cannot form a valid Studio arrangement."
        ) from exc

    existing_tracks = {item.track_id: item for item in document.tracks}
    signal_flow_tracks = tuple(
        sorted(
            (
                item
                for item in document.tracks
                if item.kind in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
            ),
            key=lambda item: (item.order, item.track_id),
        )
    )
    signal_flow_ids = {item.track_id for item in signal_flow_tracks}
    if any(item.track_id in signal_flow_ids for item in desired.tracks):
        raise SongStudioReconcileError(
            "A project track collides with a Studio signal-flow node."
        )
    desired_ids = {item.track_id for item in desired.tracks}
    existing_source_ids = {
        item.track_id
        for item in document.tracks
        if item.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
    }
    removed_ids = existing_source_ids.difference(desired_ids)
    if removed_ids and any(
        (
            region.track_id in removed_ids
            and not region.deleted
        )
        for region in document.regions
    ):
        raise SongStudioReconcileError(
            "Remove or move active regions before removing their project track."
        )
    if removed_ids and any(
        lane.track_id in removed_ids and not lane.deleted
        for lane in document.take_lanes
    ):
        raise SongStudioReconcileError(
            "Remove active take lanes before removing their project track."
        )
    if removed_ids and any(
        comp.track_id in removed_ids and not comp.deleted
        for comp in document.comp_ranges
    ):
        raise SongStudioReconcileError(
            "Remove active comp ranges before removing their project track."
        )

    source_tracks = tuple(
        (
            _reconciled_track(existing_tracks[item.track_id], item)
            if item.track_id in existing_tracks
            else item
        )
        for item in desired.tracks
    )
    tracks = source_tracks + tuple(
        replace(track, order=len(source_tracks) + index)
        for index, track in enumerate(signal_flow_tracks)
    )

    desired_backing_track = next(
        (item for item in desired.tracks if item.kind is StudioTrackKind.BACKING),
        None,
    )
    desired_backing_region = (
        desired.regions[0] if desired_backing_track is not None else None
    )
    region_values: list[StudioRegion] = []
    found_current_backing = False
    for region in document.regions:
        if region.track_id in removed_ids:
            continue
        if (
            desired_backing_track is not None
            and region.track_id == desired_backing_track.track_id
        ):
            if (
                desired_backing_region is not None
                and region.region_id == desired_backing_region.region_id
            ):
                region_values.append(
                    _reconciled_backing_region(region, desired_backing_region)
                )
                found_current_backing = True
            else:
                region_values.append(
                    replace(region, enabled=False, deleted=True)
                    if not region.deleted
                    else region
                )
            continue
        region_values.append(region)
    if desired_backing_region is not None and not found_current_backing:
        region_values.append(desired_backing_region)

    if tracks == document.tracks and tuple(region_values) == document.regions:
        return document
    try:
        return replace(
            document,
            tracks=tracks,
            regions=tuple(region_values),
            revision=document.revision + 1,
        )
    except StudioProjectError as exc:
        raise SongStudioReconcileError(
            "Project inventory could not be reconciled without losing edits."
        ) from exc


__all__ = [
    "SongStudioReconcileError",
    "reconcile_song_studio_document",
]

"""Practical, non-destructive repeated-take comp construction.

The Studio document stores only durable IDs and frame-domain edits.  This
module turns a trusted schema-v2 repeated take into an alternate lane, applies
Logic-style quick-swipe selections, and builds temporary lane-audition views.
It never opens or mutates source media; :mod:`core.studio_source_catalog` and
:mod:`core.studio_renderer` remain the authorities for media identity and DSP.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Iterable

from core.studio_project import (
    MAX_PROJECT_FRAMES,
    StudioCompRange,
    StudioDocument,
    StudioProjectError,
    StudioRegion,
    StudioTakeLane,
)
from core.take_project import (
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
)


DEFAULT_COMP_BOUNDARY_MS = 5.0
_USABLE_MEDIA = frozenset({MediaStatus.AVAILABLE, MediaStatus.RECOVERED})
_USABLE_PROJECTS = frozenset({ProjectStatus.COMPLETE, ProjectStatus.RECOVERED})
_COMP_NAMESPACE = uuid.UUID("87729e15-3ee0-48d7-8678-82d08277d65f")


class StudioCompingError(ValueError):
    """Raised when a repeated take cannot form a truthful Studio comp."""


def _canonical_id(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioCompingError(f"{field_name} must be a UUID.") from exc


def _frame(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioCompingError(f"{field_name} must be an integer frame.")
    if value < -MAX_PROJECT_FRAMES or value > MAX_PROJECT_FRAMES:
        raise StudioCompingError(f"{field_name} is outside the Studio timeline.")
    return value


def _normalized_label(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _usable_track(track: ProjectTrack) -> bool:
    return (
        track.media_status in _USABLE_MEDIA
        and bool(track.segments)
        and all(
            item.media_status in _USABLE_MEDIA and item.frame_count > 0
            for item in track.segments
        )
    )


def shared_track_sources_match(
    destination_track: ProjectTrack,
    candidate: ProjectTrack,
) -> tuple[bool, str]:
    """Fingerprint-equality gate for cross-take Shared Track identity.

    A Shared Track participant UUID is stable for the session, so it proves
    route ownership but not that two takes used the same uploaded song.
    Identity is the path-free uploaded-source digest, never a filename:
    both sides must carry equal, nonempty fingerprints. Legacy/partial
    manifests remain readable but fail closed here, with an honest reason.
    """

    if destination_track.source_type is not SourceType.LIVE_REFERENCE:
        return True, ""
    destination_fingerprint = str(
        destination_track.alignment.reference_fingerprint_sha256 or ""
    ).lower()
    candidate_fingerprint = str(
        candidate.alignment.reference_fingerprint_sha256 or ""
    ).lower()
    if not destination_fingerprint or not candidate_fingerprint:
        return False, (
            "One of these takes has no Shared Track source evidence, so "
            "WebJam cannot prove both takes played the same song."
        )
    if destination_fingerprint != candidate_fingerprint:
        return False, (
            "The repeated take played a different Shared Track song, so "
            "its lane cannot stack with this one."
        )
    return True, ""


def _same_live_reference_source(
    destination_track: ProjectTrack,
    candidate: ProjectTrack,
) -> bool:
    matched, _reason = shared_track_sources_match(destination_track, candidate)
    return matched


def _matching_source_tracks(
    destination_track: ProjectTrack,
    source_project: TakeProject,
) -> tuple[ProjectTrack, ...]:
    """Return media-usable musician matches before the timing evidence gate."""

    usable = tuple(item for item in source_project.tracks if _usable_track(item))
    if destination_track.participant_id:
        participant_matches = tuple(
            item
            for item in usable
            if item.participant_id == destination_track.participant_id
            and item.source_type is destination_track.source_type
            and _same_live_reference_source(destination_track, item)
        )
        if participant_matches:
            return tuple(
                sorted(
                    participant_matches, key=lambda item: (item.order, item.track_id)
                )
            )
        if any(item.participant_id for item in usable):
            return ()

    label_matches = tuple(
        item
        for item in usable
        if item.source_type is destination_track.source_type
        and _same_live_reference_source(destination_track, item)
        and _normalized_label(item.name) == _normalized_label(destination_track.name)
        and (
            not destination_track.instrument
            or not item.instrument
            or _normalized_label(item.instrument)
            == _normalized_label(destination_track.instrument)
        )
    )
    if label_matches:
        return tuple(
            sorted(label_matches, key=lambda item: (item.order, item.track_id))
        )

    order_matches = tuple(
        item
        for item in usable
        if item.source_type is destination_track.source_type
        and _same_live_reference_source(destination_track, item)
        and item.order == destination_track.order
    )
    return order_matches if len(order_matches) == 1 else ()


def _metadata_timing_ready(
    track: ProjectTrack,
    source_project: TakeProject,
) -> bool:
    """Apply the aligned-export timing policy without opening source media.

    Ordinary recorder-side local captures retain the established export rule:
    positive automatic-alignment confidence is sufficient.  Peer originals
    additionally require the verified provenance, quality, residual, anchors,
    and exact same-participant server-reference fingerprint written by the
    peer-alignment pipeline.  The renderer repeats this check with the take
    root so the reference bytes are verified before audio is trusted.
    """

    if track.source_type is not SourceType.LOCAL_ISOLATED:
        return True
    confidence = float(track.alignment.confidence)
    method = str(track.alignment.method or "").strip().lower()
    if confidence <= 0.0:
        return False
    if not method.startswith("peer-local-original"):
        return True

    # Keep these facts in one policy vocabulary with aligned export.  Local
    # imports avoid making the recorder/export layer depend on Studio.
    from core.take_export import (
        _PEER_ALIGNMENT_MAX_RESIDUAL_MS,
        _PEER_ALIGNMENT_MIN_ANCHORS,
        _PEER_ALIGNMENT_MIN_CONFIDENCE,
        _PEER_VERIFIED_ALIGNMENT_PREFIX,
        _reference_fingerprint,
    )

    if (
        not method.startswith(_PEER_VERIFIED_ALIGNMENT_PREFIX)
        or track.quality is not SourceQuality.VERIFIED_ISOLATED
        or confidence < _PEER_ALIGNMENT_MIN_CONFIDENCE
        or float(track.alignment.residual_ms) > _PEER_ALIGNMENT_MAX_RESIDUAL_MS
        or len(track.alignment.anchors) < _PEER_ALIGNMENT_MIN_ANCHORS
    ):
        return False
    reference_id = str(track.alignment.reference_track_id or "")
    fingerprint = str(track.alignment.reference_fingerprint_sha256 or "").lower()
    references = tuple(
        candidate
        for candidate in source_project.tracks
        if candidate.track_id == reference_id
        and candidate.participant_id == track.participant_id
        and candidate.source_type is SourceType.JAMULUS_SERVER
        and candidate.quality is SourceQuality.NETWORK_TRACK
    )
    if len(references) != 1:
        return False
    reference = references[0]
    return bool(
        fingerprint
        and _reference_fingerprint(reference) == fingerprint
        and reference.media_status is MediaStatus.AVAILABLE
        and all(
            segment.media_status is MediaStatus.AVAILABLE and segment.sha256
            for segment in reference.segments
        )
    )


def _timing_ready_source_track(
    track: ProjectTrack,
    source_project: TakeProject,
    *,
    take_root: str | Path | None = None,
) -> bool:
    """Return whether a repeated-take source owns a trusted timeline."""

    if not _metadata_timing_ready(track, source_project):
        return False
    if track.source_type is not SourceType.LOCAL_ISOLATED or take_root is None:
        return True

    # At the renderer boundary, reuse the complete aligned-export check.  In
    # particular this binds peer provenance to the still-current reference
    # media rather than trusting optimistic manifest fields alone.
    from core.take_export import _unaligned_local_original_names

    return not _unaligned_local_original_names(
        (track,),
        all_tracks=source_project.tracks,
        take_root=Path(take_root),
    )


def compatible_source_tracks(
    destination_track: ProjectTrack,
    source_project: TakeProject,
) -> tuple[ProjectTrack, ...]:
    """Return timing-ready repeated-take counterparts in preference order.

    Durable participant identity wins. LIVE_REFERENCE lanes additionally
    require the exact uploaded-source fingerprint because their participant
    identity is stable across source replacement. Older projects without participant
    identity fall back to the same source type plus normalized name/instrument;
    an order-only fallback is accepted only when it identifies one usable
    source.  Local originals must also carry the timing evidence required by
    aligned export.  This avoids silently comping the wrong musician—or an
    unaligned performance—into a track.
    """

    if not isinstance(destination_track, ProjectTrack):
        raise StudioCompingError("destination_track must be a ProjectTrack.")
    if not isinstance(source_project, TakeProject):
        raise StudioCompingError("source_project must be a TakeProject.")
    return tuple(
        item
        for item in _matching_source_tracks(destination_track, source_project)
        if _timing_ready_source_track(item, source_project)
    )


def _require_projects(
    document: StudioDocument,
    primary_project: TakeProject,
    source_project: TakeProject,
) -> None:
    if not isinstance(document, StudioDocument):
        raise StudioCompingError("document must be a StudioDocument.")
    if not isinstance(primary_project, TakeProject) or not isinstance(
        source_project, TakeProject
    ):
        raise StudioCompingError("Comp lanes require two TakeProject catalogs.")
    if (
        document.session_id != primary_project.session_id
        or document.take_id != primary_project.take_id
        or document.project_sample_rate != primary_project.project_sample_rate
    ):
        raise StudioCompingError(
            "The Studio document does not match its primary take catalog."
        )
    if source_project.take_id == primary_project.take_id:
        raise StudioCompingError("A take cannot be added as its own alternate lane.")
    if source_project.session_id != primary_project.session_id:
        raise StudioCompingError("The repeated take belongs to a different session.")
    if source_project.project_sample_rate != primary_project.project_sample_rate:
        raise StudioCompingError(
            "The repeated take uses a different project sample rate."
        )
    if source_project.status not in _USABLE_PROJECTS:
        raise StudioCompingError(
            "The repeated take must be complete or explicitly recovered."
        )


def _lane_id(
    document: StudioDocument,
    destination_track_id: str,
    source_project: TakeProject,
    source_track: ProjectTrack,
) -> str:
    return str(
        uuid.uuid5(
            _COMP_NAMESPACE,
            ":".join(
                (
                    "lane",
                    document.take_id,
                    destination_track_id,
                    source_project.take_id,
                    source_track.track_id,
                )
            ),
        )
    )


def _lane_region(
    document: StudioDocument,
    source_project: TakeProject,
    source_track: ProjectTrack,
    destination_track_id: str,
    segment,
) -> StudioRegion:
    drift_scale = 1.0 + float(source_track.alignment.drift_ppm) / 1_000_000.0
    if not math.isfinite(drift_scale) or drift_scale <= 0.0:
        raise StudioCompingError("The repeated take has an invalid drift transform.")
    timeline_count = round(
        segment.frame_count
        / segment.sample_rate
        * drift_scale
        * document.project_sample_rate
    )
    if timeline_count <= 0:
        raise StudioCompingError("A repeated-take segment has no timeline duration.")
    timeline_start = segment.project_start_frame + round(
        source_track.alignment.effective_offset_s * document.project_sample_rate
    )
    region_id = str(
        uuid.uuid5(
            _COMP_NAMESPACE,
            ":".join(
                (
                    "region",
                    document.take_id,
                    destination_track_id,
                    source_project.take_id,
                    source_track.track_id,
                    segment.segment_id,
                )
            ),
        )
    )
    return StudioRegion(
        region_id=region_id,
        track_id=destination_track_id,
        source_take_id=source_project.take_id,
        source_track_id=source_track.track_id,
        source_segment_id=segment.segment_id,
        source_start_frame=0,
        source_frame_count=segment.frame_count,
        timeline_start_frame=timeline_start,
        timeline_frame_count=timeline_count,
    )


def add_take_lane(
    document: StudioDocument,
    primary_project: TakeProject,
    source_project: TakeProject,
    *,
    destination_track_id: str,
    source_track_id: str | None = None,
    name: str = "",
) -> StudioDocument:
    """Add or restore one complete repeated-take lane in a single revision."""

    _require_projects(document, primary_project, source_project)
    destination_id = _canonical_id(destination_track_id, "destination_track_id")
    try:
        document.state_for(destination_id)
        destination_track = next(
            item for item in primary_project.tracks if item.track_id == destination_id
        )
    except (StopIteration, StudioProjectError) as exc:
        raise StudioCompingError(
            "The destination track is not part of the primary take."
        ) from exc

    matched = _matching_source_tracks(destination_track, source_project)
    compatible = tuple(
        item for item in matched if _timing_ready_source_track(item, source_project)
    )
    if source_track_id is None:
        if len(compatible) != 1:
            if (
                matched
                and not compatible
                and any(
                    item.source_type is SourceType.LOCAL_ISOLATED for item in matched
                )
            ):
                raise StudioCompingError(
                    "The repeated-take local original has no verified timeline "
                    "alignment. Keep its Jamulus server track, or align and verify "
                    "the local original before comping it."
                )
            if destination_track.source_type is SourceType.LIVE_REFERENCE:
                for candidate in source_project.tracks:
                    if candidate.source_type is not SourceType.LIVE_REFERENCE:
                        continue
                    matched_gate, reason = shared_track_sources_match(
                        destination_track, candidate
                    )
                    if not matched_gate and reason:
                        raise StudioCompingError(reason)
            raise StudioCompingError(
                "The repeated take does not have one unambiguous matching track."
            )
        source_track = compatible[0]
    else:
        source_id = _canonical_id(source_track_id, "source_track_id")
        matched_source = next(
            (item for item in matched if item.track_id == source_id),
            None,
        )
        if (
            matched_source is not None
            and matched_source.source_type is SourceType.LOCAL_ISOLATED
            and not _timing_ready_source_track(matched_source, source_project)
        ):
            raise StudioCompingError(
                "The repeated-take local original has no verified timeline "
                "alignment. Keep its Jamulus server track, or align and verify "
                "the local original before comping it."
            )
        source_track = next(
            (item for item in compatible if item.track_id == source_id),
            None,
        )
        if source_track is None:
            raise StudioCompingError(
                "The chosen source track is not a safe match for this musician."
            )
    if not _usable_track(source_track):
        raise StudioCompingError("The repeated-take track media is unavailable.")

    lane_id = _lane_id(document, destination_id, source_project, source_track)
    for existing in document.take_lanes:
        if existing.lane_id == lane_id and not existing.deleted:
            return document
    regions = tuple(
        _lane_region(
            document,
            source_project,
            source_track,
            destination_id,
            segment,
        )
        for segment in sorted(
            source_track.segments,
            key=lambda item: (item.project_start_frame, item.segment_id),
        )
    )
    orders = [
        item.order
        for item in document.take_lanes
        if item.track_id == destination_id and not item.deleted
    ]
    clean_name = " ".join(
        str(name or source_project.take_name or "Alternate take").split()
    )
    lane = StudioTakeLane(
        lane_id=lane_id,
        track_id=destination_id,
        source_take_id=source_project.take_id,
        source_track_id=source_track.track_id,
        name=clean_name[:160],
        order=max(orders, default=-1) + 1,
        region_ids=tuple(item.region_id for item in regions),
        enabled=True,
        deleted=False,
    )
    try:
        return document.upsert_take_lane_with_regions(lane, regions)
    except StudioProjectError as exc:
        raise StudioCompingError(
            "The repeated take could not form a valid lane."
        ) from exc


def _lane_coverage(
    document: StudioDocument,
    lane: StudioTakeLane,
) -> tuple[tuple[int, int], ...]:
    regions = {item.region_id: item for item in document.regions}
    intervals = sorted(
        (
            region.timeline_start_frame,
            region.timeline_end_frame,
        )
        for region_id in lane.region_ids
        if (region := regions.get(region_id)) is not None
        and not region.deleted
        and region.enabled
    )
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return tuple(merged)


def _covered(intervals: Iterable[tuple[int, int]], start: int, end: int) -> bool:
    cursor = start
    for left, right in intervals:
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return False


def _range_id(
    lane_id: str,
    start: int,
    end: int,
    *,
    suffix: str = "",
) -> str:
    return str(
        uuid.uuid5(
            _COMP_NAMESPACE,
            f"comp:{lane_id}:{start}:{end}:{suffix}",
        )
    )


def _fades(frame_count: int, requested: int) -> tuple[int, int]:
    fade = min(max(0, requested), frame_count // 2)
    return fade, fade


def _fragment(
    original: StudioCompRange,
    start: int,
    end: int,
    *,
    boundary_fade: int,
    keep_left_edge: bool,
    keep_right_edge: bool,
    reuse_id: bool,
) -> StudioCompRange:
    count = end - start
    fade_in = original.fade_in_frames if keep_left_edge else boundary_fade
    fade_out = original.fade_out_frames if keep_right_edge else boundary_fade
    if keep_right_edge and not keep_left_edge:
        # A right fragment inherits the original outer fade. Reserve that
        # envelope first, then shorten only the newly requested swipe boundary.
        fade_out = min(fade_out, count)
        fade_in = min(fade_in, max(0, count - fade_out))
    else:
        fade_in = min(fade_in, count)
        fade_out = min(fade_out, max(0, count - fade_in))
    return StudioCompRange(
        comp_range_id=(
            original.comp_range_id
            if reuse_id
            else _range_id(original.lane_id, start, end, suffix=original.comp_range_id)
        ),
        track_id=original.track_id,
        lane_id=original.lane_id,
        timeline_start_frame=start,
        frame_count=count,
        fade_in_frames=fade_in,
        fade_out_frames=fade_out,
    )


def select_lane_range(
    document: StudioDocument,
    lane_id: str,
    start_frame: int,
    end_frame: int,
    *,
    boundary_ms: float = DEFAULT_COMP_BOUNDARY_MS,
) -> StudioDocument:
    """Quick-swipe one half-open lane interval into the persisted comp.

    The new swipe replaces only overlapping selections on the destination
    track.  Uncovered left/right fragments remain selected from their original
    lanes, and every new boundary receives a short equal-power transition via
    the base take in the authoritative renderer.
    """

    if not isinstance(document, StudioDocument):
        raise StudioCompingError("document must be a StudioDocument.")
    canonical_lane = _canonical_id(lane_id, "lane_id")
    try:
        lane = document.lane_for(canonical_lane)
    except StudioProjectError as exc:
        raise StudioCompingError("The comp lane is not part of this document.") from exc
    if lane.deleted or not lane.enabled:
        raise StudioCompingError("The comp lane is not active.")
    start = _frame(start_frame, "start_frame")
    end = _frame(end_frame, "end_frame")
    if end <= start:
        raise StudioCompingError("A comp selection must have positive duration.")
    coverage = _lane_coverage(document, lane)
    if not _covered(coverage, start, end):
        raise StudioCompingError("The comp selection extends beyond lane media.")
    if isinstance(boundary_ms, bool) or not isinstance(boundary_ms, (int, float)):
        raise StudioCompingError("boundary_ms must be a finite number.")
    boundary = float(boundary_ms)
    if not math.isfinite(boundary) or boundary < 0.0 or boundary > 100.0:
        raise StudioCompingError("boundary_ms must be between 0 and 100.")
    boundary_frames = round(boundary / 1000.0 * document.project_sample_rate)

    active = sorted(
        (
            item
            for item in document.comp_ranges
            if item.track_id == lane.track_id and not item.deleted and item.enabled
        ),
        key=lambda item: (
            item.timeline_start_frame,
            item.timeline_end_frame,
            item.comp_range_id,
        ),
    )
    same_lane_coverage = tuple(
        (item.timeline_start_frame, item.timeline_end_frame)
        for item in active
        if item.lane_id == lane.lane_id
    )
    if _covered(same_lane_coverage, start, end):
        # A nested swipe already selects the requested source. Treating it as
        # a new range would recreate the surrounding range's fades and could
        # alter samples outside the swipe (including inherited outer fades).
        return document

    # Repeated swipes on the same lane behave like one longer swipe.
    changed = True
    while changed:
        changed = False
        for item in active:
            if item.lane_id != lane.lane_id:
                continue
            if item.timeline_end_frame < start or item.timeline_start_frame > end:
                continue
            expanded_start = min(start, item.timeline_start_frame)
            expanded_end = max(end, item.timeline_end_frame)
            if (expanded_start, expanded_end) != (start, end) and _covered(
                coverage, expanded_start, expanded_end
            ):
                start, end = expanded_start, expanded_end
                changed = True

    for item in active:
        if item.timeline_end_frame <= start or item.timeline_start_frame >= end:
            continue
        fade_intervals = (
            (
                item.timeline_start_frame,
                item.timeline_start_frame + item.fade_in_frames,
            ),
            (
                item.timeline_end_frame - item.fade_out_frames,
                item.timeline_end_frame,
            ),
        )
        if any(
            fade_start < boundary_frame < fade_end
            for boundary_frame in (start, end)
            for fade_start, fade_end in fade_intervals
        ):
            raise StudioCompingError(
                "A comp selection boundary cannot cut through an existing comp fade."
            )
    retained: list[StudioCompRange] = []
    for item in active:
        left = item.timeline_start_frame
        right = item.timeline_end_frame
        if right <= start or left >= end:
            retained.append(item)
            continue
        pieces: list[tuple[int, int, bool, bool]] = []
        if left < start:
            pieces.append((left, start, True, False))
        if right > end:
            pieces.append((end, right, False, True))
        for index, (piece_start, piece_end, keep_left, keep_right) in enumerate(pieces):
            retained.append(
                _fragment(
                    item,
                    piece_start,
                    piece_end,
                    boundary_fade=boundary_frames,
                    keep_left_edge=keep_left,
                    keep_right_edge=keep_right,
                    reuse_id=index == 0,
                )
            )

    fade_in, fade_out = _fades(end - start, boundary_frames)
    retained.append(
        StudioCompRange(
            comp_range_id=_range_id(lane.lane_id, start, end),
            track_id=lane.track_id,
            lane_id=lane.lane_id,
            timeline_start_frame=start,
            frame_count=end - start,
            fade_in_frames=fade_in,
            fade_out_frames=fade_out,
        )
    )
    retained.sort(
        key=lambda item: (
            item.timeline_start_frame,
            item.timeline_end_frame,
            item.comp_range_id,
        )
    )
    try:
        return document.set_comp_ranges(lane.track_id, retained)
    except StudioProjectError as exc:
        raise StudioCompingError(
            "The comp selection is not a valid lane edit."
        ) from exc


def audition_lane_document(
    document: StudioDocument,
    lane_id: str,
) -> StudioDocument:
    """Return a temporary full-lane audition view without changing sources."""

    if not isinstance(document, StudioDocument):
        raise StudioCompingError("document must be a StudioDocument.")
    canonical_lane = _canonical_id(lane_id, "lane_id")
    try:
        lane = document.lane_for(canonical_lane)
    except StudioProjectError as exc:
        raise StudioCompingError(
            "The audition lane is not part of this document."
        ) from exc
    if lane.deleted or not lane.enabled:
        raise StudioCompingError("The audition lane is not active.")
    ranges = tuple(
        StudioCompRange(
            comp_range_id=_range_id(lane.lane_id, start, end, suffix="audition"),
            track_id=lane.track_id,
            lane_id=lane.lane_id,
            timeline_start_frame=start,
            frame_count=end - start,
        )
        for start, end in _lane_coverage(document, lane)
    )
    if not ranges:
        raise StudioCompingError("The audition lane has no playable regions.")
    try:
        return document.set_comp_ranges(lane.track_id, ranges)
    except StudioProjectError as exc:
        raise StudioCompingError(
            "The take lane could not be auditioned safely."
        ) from exc


def remove_take_lane(document: StudioDocument, lane_id: str) -> StudioDocument:
    """Tombstone a lane, its comp choices, and all owned alternate regions."""

    if not isinstance(document, StudioDocument):
        raise StudioCompingError("document must be a StudioDocument.")
    try:
        return document.remove_take_lane(_canonical_id(lane_id, "lane_id"))
    except StudioProjectError as exc:
        raise StudioCompingError("The take lane is not part of this document.") from exc


__all__ = [
    "DEFAULT_COMP_BOUNDARY_MS",
    "StudioCompingError",
    "add_take_lane",
    "audition_lane_document",
    "compatible_source_tracks",
    "remove_take_lane",
    "select_lane_range",
]

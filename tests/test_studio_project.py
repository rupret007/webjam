"""Focused invariants for the pure, non-destructive Studio arrangement model."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from core.studio_project import (
    FadeCurve,
    MarkerKind,
    SnapMode,
    StudioCompRange,
    StudioCrossfade,
    StudioCycleRange,
    StudioDocument,
    StudioMarker,
    StudioMaster,
    StudioProjectError,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
    crossfade_gains,
    default_studio_document,
    fade_gain,
    reconcile_studio_document,
    studio_document_from_dict,
)
from core.take_project import (
    AlignmentState,
    MediaSegment,
    MediaStatus,
    ProjectMarker,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _segment(
    number: int,
    path: str,
    *,
    start: int,
    frames: int,
    rate: int,
) -> MediaSegment:
    return MediaSegment(
        segment_id=_id(number),
        path=path,
        project_start_frame=start,
        frame_count=frames,
        sample_rate=rate,
        channels=1,
        sample_format="PCM_24",
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(path.encode("utf-8")).hexdigest(),
    )


def _track(
    number: int,
    name: str,
    order: int,
    segments: tuple[MediaSegment, ...],
    *,
    alignment: AlignmentState | None = None,
) -> ProjectTrack:
    return ProjectTrack(
        track_id=_id(number),
        source_id=_id(number + 100),
        participant_id=None,
        name=name,
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=order,
        segments=segments,
        alignment=alignment or AlignmentState(),
    )


@pytest.fixture
def take_and_media(tmp_path: Path) -> tuple[TakeProject, tuple[Path, ...]]:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    paths = (
        media_dir / "guitar-secret-name.wav",
        media_dir / "guitar-punch.wav",
        media_dir / "drums-secret-name.wav",
    )
    for index, path in enumerate(paths):
        path.write_bytes(f"immutable source {index}".encode("ascii"))

    guitar = _track(
        10,
        "Guitar",
        5,
        (
            _segment(
                20,
                "media/guitar-secret-name.wav",
                start=4_800,
                frames=24_000,
                rate=24_000,
            ),
            _segment(
                21,
                "media/guitar-punch.wav",
                start=96_000,
                frames=12_000,
                rate=48_000,
            ),
        ),
        alignment=AlignmentState(
            automatic_offset_s=0.2,
            manual_nudge_s=0.05,
            drift_ppm=1_000.0,
        ),
    )
    drums = _track(
        11,
        "Drums",
        1,
        (
            _segment(
                22,
                "media/drums-secret-name.wav",
                start=0,
                frames=48_000,
                rate=48_000,
            ),
        ),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Rehearsal",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(),
        tracks=(guitar, drums),
        markers=(ProjectMarker(_id(30), 0.5, "Verse"),),
    )
    return project, paths


def _source_region(document: StudioDocument, segment_id: str) -> StudioRegion:
    return next(
        region for region in document.regions if region.source_segment_id == segment_id
    )


def _media_hashes(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)


def test_track_defaults_and_gain_alias_are_small_and_immutable() -> None:
    track = StudioTrack(_id(10))

    assert track.gain == 1.0
    assert track.fader_gain == 1.0
    with pytest.raises(FrozenInstanceError):
        track.muted = True  # type: ignore[misc]

    document = StudioDocument(
        session_id=_id(1),
        take_id=_id(2),
        project_sample_rate=48_000,
        tracks=(track, StudioTrack(_id(11))),
    )
    changed = document.update_track(track.track_id, gain=0.75, muted=True)

    assert changed is not document
    assert changed.revision == document.revision + 1
    assert changed.state_for(track.track_id).gain == 0.75
    assert changed.state_for(track.track_id).muted is True
    assert document.state_for(track.track_id) == track
    with pytest.raises(StudioProjectError, match="gain or fader_gain"):
        document.update_track(track.track_id, gain=1.0, fader_gain=1.0)


@pytest.mark.parametrize(
    ("curve", "midpoint"),
    (
        (FadeCurve.LINEAR, 0.5),
        (FadeCurve.EQUAL_POWER, math.sqrt(0.5)),
        (FadeCurve.S_CURVE, 0.5),
    ),
)
def test_fade_math_has_exact_endpoints_and_deterministic_curves(
    curve: FadeCurve,
    midpoint: float,
) -> None:
    assert fade_gain(-10, 101, curve) == 0.0
    assert fade_gain(0, 101, curve) == 0.0
    assert fade_gain(100, 101, curve) == 1.0
    assert fade_gain(110, 101, curve) == 1.0
    assert fade_gain(0, 101, curve, fade_in=False) == 1.0
    assert fade_gain(100, 101, curve, fade_in=False) == 0.0
    assert fade_gain(50, 101, curve) == pytest.approx(midpoint)
    assert fade_gain(0, 1, curve) == 1.0
    assert fade_gain(0, 1, curve, fade_in=False) == 0.0

    outgoing, incoming = crossfade_gains(50, 101, curve)
    assert outgoing == pytest.approx(midpoint)
    assert incoming == pytest.approx(midpoint)
    if curve is FadeCurve.EQUAL_POWER:
        for position in (0, 1, 25, 50, 75, 99, 100):
            outgoing, incoming = crossfade_gains(position, 101, curve)
            assert outgoing**2 + incoming**2 == pytest.approx(1.0)

    with pytest.raises(StudioProjectError, match="positive|between"):
        fade_gain(0, 0, curve)
    with pytest.raises(StudioProjectError, match="integer"):
        fade_gain(0.5, 100, curve)  # type: ignore[arg-type]


def test_default_document_is_deterministic_frame_based_and_path_free(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, media_paths = take_and_media
    project_before = project.to_dict()
    media_before = _media_hashes(media_paths)

    first = default_studio_document(project)
    second = default_studio_document(project)
    renamed = replace(
        project,
        session_title="Renamed",
        tracks=tuple(
            replace(track, name=f"Renamed {track.name}") for track in project.tracks
        ),
    )
    renamed_default = default_studio_document(renamed)

    assert first == second
    assert tuple(track.track_id for track in first.tracks) == (_id(11), _id(10))
    assert tuple(track.order for track in first.tracks) == (0, 1)
    assert tuple(region.region_id for region in first.regions) == tuple(
        region.region_id for region in renamed_default.regions
    )
    assert all(uuid.UUID(region.region_id).version == 5 for region in first.regions)

    guitar_first = _source_region(first, _id(20))
    guitar_punch = _source_region(first, _id(21))
    assert guitar_first.source_start_frame == 0
    assert guitar_first.source_frame_count == 24_000
    assert guitar_first.timeline_start_frame == 16_800
    assert guitar_first.timeline_frame_count == 48_048
    assert guitar_punch.timeline_start_frame == 108_000
    assert guitar_punch.timeline_frame_count == 12_012
    assert first.markers == (StudioMarker(_id(30), 24_000, "Verse"),)

    payload = first.to_dict()
    serialized = json.dumps(payload, sort_keys=True)
    assert "path" not in serialized.lower()
    assert "secret-name.wav" not in serialized
    assert project.to_dict() == project_before
    assert _media_hashes(media_paths) == media_before


def test_strict_parser_round_trips_and_rejects_unknown_types_and_references(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, _media_paths = take_and_media
    document = default_studio_document(project)
    payload = document.to_dict()

    assert studio_document_from_dict(payload) == document

    malformed = copy.deepcopy(payload)
    malformed["unexpected"] = True
    with pytest.raises(StudioProjectError, match="unsupported fields"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["tracks"][0]["unexpected"] = True
    with pytest.raises(StudioProjectError, match="unsupported fields"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["schema_version"] = 2.0
    with pytest.raises(StudioProjectError, match="integer"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["tracks"][0]["fader_gain"] = "0.5"
    with pytest.raises(StudioProjectError, match="finite number"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["regions"][0]["timeline_start_frame"] = 1.5
    with pytest.raises(StudioProjectError, match="integer"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["regions"][0]["track_id"] = _id(999)
    with pytest.raises(StudioProjectError, match="unknown Studio track"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["regions"][1]["region_id"] = malformed["regions"][0]["region_id"]
    with pytest.raises(StudioProjectError, match="duplicate region IDs"):
        studio_document_from_dict(malformed)

    partial = copy.deepcopy(payload)
    partial.pop("regions")
    with pytest.raises(StudioProjectError, match="missing required fields: regions"):
        studio_document_from_dict(partial)

    partial = copy.deepcopy(payload)
    partial["regions"][0].pop("fade_out_curve")
    with pytest.raises(StudioProjectError, match="fade_out_curve"):
        studio_document_from_dict(partial)


def test_region_edits_preserve_sources_and_revisioned_tombstones(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, media_paths = take_and_media
    project_before = project.to_dict()
    media_before = _media_hashes(media_paths)
    original_document = default_studio_document(project)
    original_region = _source_region(original_document, _id(20))

    moved = original_document.move_region(original_region.region_id, 20_000)
    assert moved.revision == original_document.revision + 1
    assert moved.region_for(original_region.region_id).timeline_start_frame == 20_000
    assert original_document.region_for(original_region.region_id) == original_region

    trimmed = moved.trim_region(
        original_region.region_id,
        source_start_frame=120,
        source_frame_count=23_880,
        timeline_start_frame=20_240,
        timeline_frame_count=47_808,
    )
    assert trimmed.revision == moved.revision + 1
    assert trimmed.region_for(original_region.region_id).source_start_frame == 120

    faded = trimmed.set_region_fades(
        original_region.region_id,
        fade_in_frames=480,
        fade_out_frames=960,
        fade_in_curve=FadeCurve.S_CURVE,
        fade_out_curve=FadeCurve.EQUAL_POWER,
    )
    right_id = _id(80)
    split_at = 44_144
    split = faded.split_region(
        original_region.region_id,
        split_at,
        right_region_id=right_id,
    )
    left = split.region_for(original_region.region_id)
    right = split.region_for(right_id)
    assert split.revision == faded.revision + 1
    assert left.timeline_end_frame == right.timeline_start_frame == split_at
    assert left.timeline_frame_count + right.timeline_frame_count == 47_808
    assert left.source_end_frame == right.source_start_frame
    assert left.source_frame_count + right.source_frame_count == 23_880
    for timeline_frame in (
        left.timeline_start_frame,
        left.timeline_end_frame,
        right.timeline_end_frame,
    ):
        child = left if timeline_frame <= left.timeline_end_frame else right
        assert child.source_boundary_for_timeline(timeline_frame) == trimmed.region_for(
            original_region.region_id
        ).source_boundary_for_timeline(timeline_frame)
    assert left.fade_in_frames == 480
    assert left.fade_out_frames == 0
    assert right.fade_in_frames == 0
    assert right.fade_out_frames == 960

    duplicate_id = _id(81)
    duplicated = split.duplicate_region(
        right_id,
        new_region_id=duplicate_id,
        timeline_start_frame=80_000,
    )
    assert duplicated.revision == split.revision + 1
    assert duplicated.region_for(duplicate_id).source_segment_id == _id(20)
    assert duplicated.region_for(duplicate_id).timeline_start_frame == 80_000

    disabled = duplicated.set_region_enabled(duplicate_id, False)
    enabled = disabled.set_region_enabled(duplicate_id, True)
    deleted = enabled.delete_region(duplicate_id)
    tombstone = deleted.region_for(duplicate_id)
    assert disabled.revision == duplicated.revision + 1
    assert enabled.revision == disabled.revision + 1
    assert deleted.revision == enabled.revision + 1
    assert tombstone.deleted is True
    assert tombstone.enabled is False
    assert deleted.delete_region(duplicate_id) is deleted
    with pytest.raises(StudioProjectError, match="deleted region"):
        deleted.move_region(duplicate_id, 0)
    with pytest.raises(StudioProjectError, match="strictly inside"):
        split.split_region(right_id, split_at)
    with pytest.raises(StudioProjectError, match="already in use"):
        split.duplicate_region(right_id, new_region_id=right_id)
    with pytest.raises(StudioProjectError, match="affine source mapping"):
        moved.trim_region(
            original_region.region_id,
            source_start_frame=0,
            source_frame_count=12_000,
            timeline_start_frame=20_000,
            timeline_frame_count=48_048,
        )

    assert project.to_dict() == project_before
    assert _media_hashes(media_paths) == media_before


def test_fades_and_crossfades_stay_inside_active_same_track_overlaps(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, _media_paths = take_and_media
    document = default_studio_document(project)
    left = _source_region(document, _id(20))
    duplicate_id = _id(82)
    document = document.duplicate_region(
        left.region_id,
        new_region_id=duplicate_id,
        timeline_start_frame=60_000,
    )
    document = document.set_region_fades(
        left.region_id,
        fade_in_frames=100,
        fade_out_frames=200,
        fade_in_curve=FadeCurve.LINEAR,
        fade_out_curve=FadeCurve.S_CURVE,
    )
    crossfade_id = _id(83)
    blended = document.set_crossfade(
        left.region_id,
        duplicate_id,
        start_frame=60_000,
        frame_count=1_000,
        curve=FadeCurve.EQUAL_POWER,
        crossfade_id=crossfade_id,
    )

    assert blended.revision == document.revision + 1
    assert blended.crossfades == (
        StudioCrossfade(
            crossfade_id,
            left.region_id,
            duplicate_id,
            60_000,
            1_000,
        ),
    )
    with pytest.raises(StudioProjectError, match="inside.*overlap"):
        document.set_crossfade(
            left.region_id,
            duplicate_id,
            start_frame=59_999,
            frame_count=1_000,
        )

    other_track_region = _source_region(document, _id(22))
    with pytest.raises(StudioProjectError, match="share a track"):
        document.set_crossfade(
            left.region_id,
            other_track_region.region_id,
            start_frame=16_800,
            frame_count=100,
        )

    split = blended.split_region(
        duplicate_id,
        60_500,
        right_region_id=_id(84),
    )
    assert split.crossfades[0].deleted is True
    assert split.remove_crossfade(crossfade_id) is split

    disabled = blended.set_region_enabled(duplicate_id, False)
    assert disabled.crossfades[0].deleted is True


def test_markers_cycle_snap_and_master_are_validated_immutable_edits(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, _media_paths = take_and_media
    document = default_studio_document(project)
    section = StudioMarker(
        _id(90),
        48_000,
        "Chorus",
        kind=MarkerKind.SECTION,
        end_frame=96_000,
    )

    marked = document.upsert_marker(section)
    snapped = marked.set_snap_mode(SnapMode.MARKERS)
    cycled = snapped.set_cycle_range(StudioCycleRange(48_000, 96_000))
    mastered = cycled.set_master(StudioMaster(gain=0.9, limiter_enabled=False))

    assert marked.revision == document.revision + 1
    assert snapped.revision == marked.revision + 1
    assert cycled.revision == snapped.revision + 1
    assert mastered.revision == cycled.revision + 1
    assert marked.markers[-1].name == "Chorus"
    assert marked.markers[-1].position_frame == 48_000
    assert snapped.snap_mode is SnapMode.MARKERS
    assert cycled.cycle_range == StudioCycleRange(48_000, 96_000)
    assert mastered.master == StudioMaster(0.9, False)

    removed = mastered.remove_marker(section.marker_id)
    assert removed.markers[-1].deleted is True
    assert removed.set_cycle_range(None).cycle_range is None
    with pytest.raises(StudioProjectError, match="later end frame"):
        StudioMarker(
            _id(91),
            100,
            "Broken section",
            kind=MarkerKind.SECTION,
            end_frame=100,
        )
    with pytest.raises(StudioProjectError, match="later than"):
        StudioCycleRange(100, 100)


def test_take_lanes_and_comp_ranges_validate_references_and_selection_overlap(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, _media_paths = take_and_media
    document = default_studio_document(project)
    guitar_track_id = _id(10)
    guitar_regions = tuple(
        region.region_id
        for region in document.regions
        if region.track_id == guitar_track_id
    )
    lane = StudioTakeLane(
        lane_id=_id(100),
        track_id=guitar_track_id,
        source_take_id=project.take_id,
        source_track_id=guitar_track_id,
        name="Guitar main",
        region_ids=guitar_regions,
    )
    with_lane = document.upsert_take_lane(lane)
    first = StudioCompRange(_id(101), guitar_track_id, lane.lane_id, 16_800, 1_000)
    second = StudioCompRange(_id(102), guitar_track_id, lane.lane_id, 17_800, 1_000)

    selected = with_lane.select_comp_range(first).select_comp_range(second)
    assert selected.revision == with_lane.revision + 2
    assert selected.comp_ranges == (first, second)

    overlapping = StudioCompRange(
        _id(103), guitar_track_id, lane.lane_id, 17_300, 1_000
    )
    with pytest.raises(StudioProjectError, match="cannot overlap"):
        selected.select_comp_range(overlapping)

    replacement = StudioCompRange(_id(104), guitar_track_id, lane.lane_id, 20_000, 500)
    replaced = selected.set_comp_ranges(guitar_track_id, (replacement,))
    assert tuple(item.deleted for item in replaced.comp_ranges) == (True, True, False)
    assert replaced.comp_ranges[-1] == replacement

    removed_lane = replaced.remove_take_lane(lane.lane_id)
    assert removed_lane.lane_for(lane.lane_id).deleted is True
    assert all(item.deleted for item in removed_lane.comp_ranges)

    unknown_lane_range = StudioCompRange(_id(105), guitar_track_id, _id(999), 0, 100)
    with pytest.raises(StudioProjectError, match="unknown track or take lane"):
        with_lane.select_comp_range(unknown_lane_range)
    with pytest.raises(StudioProjectError, match="provided together"):
        StudioTakeLane(
            lane_id=_id(106),
            track_id=guitar_track_id,
            source_take_id=project.take_id,
        )

    empty_lane = StudioTakeLane(
        lane_id=_id(107),
        track_id=guitar_track_id,
        source_take_id=project.take_id,
        source_track_id=guitar_track_id,
    )
    empty_document = document.upsert_take_lane(empty_lane)
    with pytest.raises(StudioProjectError, match="covered by active lane regions"):
        empty_document.select_comp_range(
            StudioCompRange(_id(108), guitar_track_id, empty_lane.lane_id, 16_800, 100)
        )

    with pytest.raises(StudioProjectError, match="source IDs do not match"):
        document.upsert_take_lane(
            replace(lane, source_track_id=_id(11), lane_id=_id(109))
        )
    with pytest.raises(StudioProjectError, match="more than one active take lane"):
        with_lane.upsert_take_lane(replace(lane, lane_id=_id(110)))


def test_reconcile_preserves_durable_edits_adds_inventory_and_honors_tombstones(
    take_and_media: tuple[TakeProject, tuple[Path, ...]],
) -> None:
    project, media_paths = take_and_media
    project_before = project.to_dict()
    media_before = _media_hashes(media_paths)
    document = default_studio_document(project)
    moved_id = _source_region(document, _id(20)).region_id
    deleted_id = _source_region(document, _id(21)).region_id
    document = document.update_track(_id(10), gain=0.65, pan=-0.2)
    document = document.move_region(moved_id, 33_000)
    document = document.delete_region(deleted_id)

    new_segment = _segment(
        23,
        "media/new-guitar.wav",
        start=120_000,
        frames=4_800,
        rate=48_000,
    )
    changed_guitar = replace(
        project.tracks[0],
        segments=(*project.tracks[0].segments, new_segment),
    )
    new_track = _track(
        12,
        "Bass",
        9,
        (
            _segment(
                24,
                "media/new-bass.wav",
                start=0,
                frames=9_600,
                rate=48_000,
            ),
        ),
    )
    changed_project = replace(
        project,
        tracks=(changed_guitar, project.tracks[1], new_track),
        markers=(*project.markers, ProjectMarker(_id(31), 2.0, "Chorus")),
        revision=project.revision + 1,
    )

    reconciled = reconcile_studio_document(changed_project, document)

    assert reconciled.revision == document.revision + 1
    assert reconciled.state_for(_id(10)).gain == 0.65
    assert reconciled.state_for(_id(10)).pan == -0.2
    assert reconciled.region_for(moved_id).timeline_start_frame == 33_000
    assert reconciled.region_for(deleted_id).deleted is True
    assert (
        len(
            [
                region
                for region in reconciled.regions
                if region.source_segment_id == _id(21)
            ]
        )
        == 1
    )
    assert _source_region(reconciled, _id(23)).deleted is False
    assert reconciled.state_for(_id(12)) == StudioTrack(_id(12), order=2)
    assert _source_region(reconciled, _id(24)).track_id == _id(12)
    assert reconciled.markers[-1] == StudioMarker(_id(31), 96_000, "Chorus")
    assert reconcile_studio_document(changed_project, reconciled) is reconciled

    bounded_region = _source_region(document, _id(20))
    out_of_bounds = replace(
        document,
        regions=tuple(
            replace(
                region,
                source_start_frame=23_000,
                source_frame_count=2_000,
                timeline_start_frame=region.timeline_boundary_for_source(23_000),
                timeline_frame_count=region.timeline_boundary_for_source(25_000)
                - region.timeline_boundary_for_source(23_000),
            )
            if region.region_id == bounded_region.region_id
            else region
            for region in document.regions
        ),
    )
    with pytest.raises(StudioProjectError, match="beyond.*source segment"):
        reconcile_studio_document(project, out_of_bounds)

    wrong_take = replace(changed_project, take_id=_id(999))
    with pytest.raises(StudioProjectError, match="different take"):
        reconcile_studio_document(wrong_take, reconciled)

    assert project.to_dict() == project_before
    assert _media_hashes(media_paths) == media_before

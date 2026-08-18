"""Core-only coverage for atomic song-section ripple reordering."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.studio_project import (
    MAX_PROJECT_FRAMES,
    FadeCurve,
    MarkerKind,
    StudioCompRange,
    StudioCrossfade,
    StudioCycleRange,
    StudioDocument,
    StudioMarker,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
    default_studio_document,
)
from core.studio_renderer import StudioRenderer
from core.studio_sections import (
    StudioSectionError,
    duplicate_section,
    remove_section,
    reorder_section,
)
from core.take_project import (
    AlignmentState,
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _ids(*numbers: int):
    values = iter(_id(number) for number in numbers)
    return lambda: next(values)


def _region(
    number: int,
    track_number: int,
    start: int,
    count: int,
    *,
    source_start: int = 0,
    source_count: int | None = None,
    mapping_timeline_start: int | None = None,
    mapping_timeline_count: int | None = None,
    mapping_source_start: int | None = None,
    mapping_source_count: int | None = None,
    enabled: bool = True,
    deleted: bool = False,
    fade_in: int = 0,
    fade_out: int = 0,
) -> StudioRegion:
    source_frames = count if source_count is None else source_count
    return StudioRegion(
        region_id=_id(number),
        track_id=_id(track_number),
        source_take_id=_id(2),
        source_track_id=_id(track_number),
        source_segment_id=_id(number + 1_000),
        source_start_frame=source_start,
        source_frame_count=source_frames,
        timeline_start_frame=start,
        timeline_frame_count=count,
        mapping_source_start_frame=(
            source_start if mapping_source_start is None else mapping_source_start
        ),
        mapping_timeline_start_frame=(
            start if mapping_timeline_start is None else mapping_timeline_start
        ),
        mapping_source_frame_count=(
            source_frames if mapping_source_count is None else mapping_source_count
        ),
        mapping_timeline_frame_count=(
            count if mapping_timeline_count is None else mapping_timeline_count
        ),
        enabled=enabled,
        deleted=deleted,
        fade_in_frames=fade_in,
        fade_out_frames=fade_out,
        fade_in_curve=FadeCurve.S_CURVE,
        fade_out_curve=FadeCurve.EQUAL_POWER,
    )


def _document(
    *,
    regions: tuple[StudioRegion, ...] = (),
    markers: tuple[StudioMarker, ...] | None = None,
    take_lanes: tuple[StudioTakeLane, ...] = (),
    comp_ranges: tuple[StudioCompRange, ...] = (),
    crossfades: tuple[StudioCrossfade, ...] = (),
    cycle_range: StudioCycleRange | None = None,
    revision: int = 7,
) -> StudioDocument:
    return StudioDocument(
        session_id=_id(1),
        take_id=_id(2),
        project_sample_rate=48_000,
        tracks=(StudioTrack(_id(10)), StudioTrack(_id(11), order=1)),
        regions=regions,
        take_lanes=take_lanes,
        comp_ranges=comp_ranges,
        markers=(
            (
                StudioMarker(
                    _id(300),
                    10,
                    "Verse",
                    kind=MarkerKind.SECTION,
                    end_frame=20,
                ),
            )
            if markers is None
            else markers
        ),
        crossfades=crossfades,
        cycle_range=cycle_range,
        revision=revision,
    )


def test_reorder_section_earlier_splits_every_track_and_moves_markers() -> None:
    first = _region(100, 10, 0, 30, fade_in=2, fade_out=3)
    second = _region(101, 11, 5, 10)
    disabled = _region(102, 10, 9, 12, enabled=False)
    tombstone = _region(103, 11, 0, 30, enabled=False, deleted=True)
    markers = (
        StudioMarker(
            _id(300),
            10,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=20,
        ),
        StudioMarker(_id(301), 10, "Section entrance"),
        StudioMarker(_id(302), 5, "Intervening point"),
        StudioMarker(_id(303), 20, "Following point"),
        StudioMarker(
            _id(304),
            12,
            "Nested phrase",
            kind=MarkerKind.SECTION,
            end_frame=16,
        ),
        StudioMarker(
            _id(305),
            2,
            "Intervening phrase",
            kind=MarkerKind.SECTION,
            end_frame=10,
        ),
        StudioMarker(
            _id(306),
            20,
            "Following phrase",
            kind=MarkerKind.SECTION,
            end_frame=25,
        ),
        StudioMarker(_id(307), 5, "Deleted", deleted=True),
    )
    original = _document(
        regions=(first, second, disabled, tombstone),
        markers=markers,
    )
    original_snapshot = original.to_dict()

    moved = reorder_section(
        original,
        _id(300),
        2,
        split_id_factory=_ids(400, 401, 402, 403, 404, 405),
    )

    assert moved.revision == original.revision + 1
    assert original.to_dict() == original_snapshot
    assert tuple(item.region_id for item in moved.regions) == (
        _id(100),
        _id(400),
        _id(401),
        _id(402),
        _id(101),
        _id(403),
        _id(102),
        _id(404),
        _id(405),
        _id(103),
    )
    assert tuple(
        (item.timeline_start_frame, item.timeline_end_frame)
        for item in moved.regions[:4]
    ) == ((0, 2), (12, 20), (2, 12), (20, 30))
    assert tuple(
        (item.source_start_frame, item.source_end_frame) for item in moved.regions[:4]
    ) == ((0, 2), (2, 10), (10, 20), (20, 30))
    assert moved.regions[0].fade_in_frames == 2
    assert moved.regions[0].fade_out_frames == 0
    assert moved.regions[1].fade_in_frames == moved.regions[1].fade_out_frames == 0
    assert moved.regions[2].fade_in_frames == moved.regions[2].fade_out_frames == 0
    assert moved.regions[3].fade_in_frames == 0
    assert moved.regions[3].fade_out_frames == 3
    assert tuple(
        (item.timeline_start_frame, item.timeline_end_frame)
        for item in moved.regions[4:6]
    ) == ((15, 20), (2, 7))
    assert tuple(
        (item.timeline_start_frame, item.timeline_end_frame)
        for item in moved.regions[6:9]
    ) == ((19, 20), (2, 12), (20, 21))
    assert moved.regions[6].enabled is False
    assert moved.regions[7].enabled is False
    assert moved.regions[8].enabled is False
    assert moved.regions[-1] is tombstone
    for item in moved.regions[:9]:
        assert item.source_boundary_for_timeline(item.timeline_start_frame) == (
            item.source_start_frame
        )
        assert item.source_boundary_for_timeline(item.timeline_end_frame) == (
            item.source_end_frame
        )

    marker_map = {item.marker_id: item for item in moved.markers}
    assert (marker_map[_id(300)].start_frame, marker_map[_id(300)].end_frame) == (
        2,
        12,
    )
    assert marker_map[_id(301)].start_frame == 2
    assert marker_map[_id(302)].start_frame == 15
    assert marker_map[_id(303)].start_frame == 20
    assert (marker_map[_id(304)].start_frame, marker_map[_id(304)].end_frame) == (
        4,
        8,
    )
    assert (marker_map[_id(305)].start_frame, marker_map[_id(305)].end_frame) == (
        12,
        20,
    )
    assert (marker_map[_id(306)].start_frame, marker_map[_id(306)].end_frame) == (
        20,
        25,
    )
    assert marker_map[_id(307)] is markers[-1]


def test_reorder_section_later_uses_target_as_final_start() -> None:
    original = _document(
        regions=(_region(100, 10, 0, 15),),
        markers=(
            StudioMarker(
                _id(300),
                4,
                "Bridge",
                kind=MarkerKind.SECTION,
                end_frame=7,
            ),
            StudioMarker(_id(301), 4, "Bridge start"),
            StudioMarker(_id(302), 7, "Following start"),
            StudioMarker(_id(303), 12, "Following tail"),
            StudioMarker(_id(304), 13, "Unaffected"),
        ),
    )

    moved = reorder_section(
        original,
        _id(300),
        10,
        split_id_factory=_ids(400, 401, 402),
    )

    assert tuple(
        (item.region_id, item.timeline_start_frame, item.timeline_end_frame)
        for item in moved.regions
    ) == (
        (_id(100), 0, 4),
        (_id(400), 10, 13),
        (_id(401), 4, 10),
        (_id(402), 13, 15),
    )
    assert tuple(
        (item.source_start_frame, item.source_end_frame) for item in moved.regions
    ) == ((0, 4), (4, 7), (7, 13), (13, 15))
    markers = {item.marker_id: item for item in moved.markers}
    assert (markers[_id(300)].start_frame, markers[_id(300)].end_frame) == (
        10,
        13,
    )
    assert markers[_id(301)].start_frame == 10
    assert markers[_id(302)].start_frame == 4
    assert markers[_id(303)].start_frame == 9
    assert markers[_id(304)].start_frame == 13


def test_reorder_section_preserves_non_unit_affine_mapping_origins() -> None:
    original_region = _region(
        100,
        10,
        0,
        8,
        source_count=5,
        mapping_source_count=5,
        mapping_timeline_count=8,
    )
    original = _document(
        regions=(original_region,),
        markers=(
            StudioMarker(
                _id(300),
                3,
                "Rate-converted section",
                kind=MarkerKind.SECTION,
                end_frame=5,
            ),
        ),
    )

    moved = reorder_section(
        original,
        _id(300),
        0,
        split_id_factory=_ids(400, 401),
    )

    assert tuple(
        (
            item.timeline_start_frame,
            item.timeline_end_frame,
            item.source_start_frame,
            item.source_end_frame,
            item.mapping_timeline_start_frame,
        )
        for item in moved.regions
    ) == ((2, 5, 0, 2, 2), (0, 2, 2, 3, -3), (5, 8, 3, 5, 0))
    original_ranges = ((0, 3), (3, 5), (5, 8))
    for fragment, (old_start, old_end) in zip(moved.regions, original_ranges):
        assert fragment.source_start_frame == (
            original_region.source_boundary_for_timeline(old_start)
        )
        assert fragment.source_end_frame == (
            original_region.source_boundary_for_timeline(old_end)
        )
        assert fragment.source_boundary_for_timeline(
            fragment.timeline_start_frame
        ) == original_region.source_boundary_for_timeline(old_start)
        assert fragment.source_boundary_for_timeline(
            fragment.timeline_end_frame
        ) == original_region.source_boundary_for_timeline(old_end)


def test_reorder_section_transforms_comp_crossfade_cycle_and_lane_references() -> None:
    left = _region(100, 10, 0, 30)
    right = _region(101, 10, 8, 20)
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(left.region_id,),
    )
    comp = StudioCompRange(
        _id(501),
        _id(10),
        lane.lane_id,
        12,
        4,
    )
    deleted_comp = StudioCompRange(
        _id(502),
        _id(10),
        lane.lane_id,
        9,
        2,
        enabled=False,
        deleted=True,
    )
    crossfade = StudioCrossfade(_id(503), left.region_id, right.region_id, 12, 3)
    deleted_crossfade = StudioCrossfade(
        _id(504),
        left.region_id,
        right.region_id,
        9,
        2,
        deleted=True,
    )
    original = _document(
        regions=(left, right),
        take_lanes=(lane,),
        comp_ranges=(comp, deleted_comp),
        crossfades=(crossfade, deleted_crossfade),
        cycle_range=StudioCycleRange(3, 8),
    )

    moved = reorder_section(
        original,
        _id(300),
        2,
        split_id_factory=_ids(400, 401, 402, 403, 404),
    )

    assert moved.take_lanes[0].region_ids == (
        _id(100),
        _id(400),
        _id(401),
        _id(402),
    )
    assert moved.comp_ranges[0].timeline_start_frame == 4
    assert moved.comp_ranges[0].frame_count == 4
    assert moved.comp_ranges[1] is deleted_comp
    assert moved.crossfades[0] == replace(
        crossfade,
        left_region_id=_id(401),
        right_region_id=_id(403),
        start_frame=4,
    )
    assert moved.crossfades[1] is deleted_crossfade
    assert moved.cycle_range == StudioCycleRange(13, 18)


@pytest.mark.parametrize(
    ("hazard", "message"),
    (
        ("section", "Section marker"),
        ("comp", "comp range"),
        ("crossfade", "crossfade"),
        ("cycle", "cycle range"),
    ),
)
def test_reorder_section_fails_closed_for_intervals_crossing_a_seam(
    hazard: str,
    message: str,
) -> None:
    left = _region(100, 10, 0, 30)
    right = _region(101, 10, 0, 30)
    markers = (
        StudioMarker(
            _id(300),
            10,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=20,
        ),
    )
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(left.region_id,),
    )
    kwargs: dict[str, object] = {}
    if hazard == "section":
        markers = (
            *markers,
            StudioMarker(
                _id(301),
                9,
                "Unsafe",
                kind=MarkerKind.SECTION,
                end_frame=11,
            ),
        )
    elif hazard == "comp":
        kwargs["take_lanes"] = (lane,)
        kwargs["comp_ranges"] = (
            StudioCompRange(_id(501), _id(10), lane.lane_id, 9, 2),
        )
    elif hazard == "crossfade":
        kwargs["crossfades"] = (
            StudioCrossfade(_id(502), left.region_id, right.region_id, 9, 2),
        )
    else:
        kwargs["cycle_range"] = StudioCycleRange(9, 11, enabled=False)
    original = _document(regions=(left, right), markers=markers, **kwargs)
    snapshot = original.to_dict()
    calls = 0

    def split_id() -> str:
        nonlocal calls
        calls += 1
        return _id(600 + calls)

    with pytest.raises(StudioSectionError, match=message):
        reorder_section(
            original,
            _id(300),
            2,
            split_id_factory=split_id,
        )

    assert calls == 0
    assert original.to_dict() == snapshot


@pytest.mark.parametrize(
    "target",
    (-1, MAX_PROJECT_FRAMES + 1, True, 1.5, 11),
)
def test_reorder_section_rejects_invalid_or_inside_targets(target: object) -> None:
    with pytest.raises(StudioSectionError):
        reorder_section(_document(), _id(300), target)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "marker_id",
    ("not-a-uuid", _id(999), _id(301), _id(302)),
)
def test_reorder_section_rejects_invalid_deleted_or_point_markers(
    marker_id: str,
) -> None:
    document = _document(
        markers=(
            StudioMarker(
                _id(300),
                10,
                "Verse",
                kind=MarkerKind.SECTION,
                end_frame=20,
            ),
            StudioMarker(_id(301), 5, "Point"),
            StudioMarker(
                _id(302),
                20,
                "Deleted section",
                kind=MarkerKind.SECTION,
                end_frame=30,
                deleted=True,
            ),
        )
    )
    with pytest.raises(StudioSectionError):
        reorder_section(document, marker_id, 0)


def test_reorder_section_noop_preserves_identity_without_allocating_ids() -> None:
    original = _document(regions=(_region(100, 10, 0, 30),))
    calls = 0

    def split_id() -> str:
        nonlocal calls
        calls += 1
        return _id(400)

    assert (
        reorder_section(
            original,
            _id(300),
            10,
            split_id_factory=split_id,
        )
        is original
    )
    assert calls == 0


def test_reorder_section_rejects_unsafe_affine_split_without_allocating_ids() -> None:
    compressed = _region(
        100,
        10,
        0,
        10,
        source_count=1,
        mapping_source_count=1,
        mapping_timeline_count=10,
    )
    original = _document(
        regions=(compressed,),
        markers=(
            StudioMarker(
                _id(300),
                1,
                "Tiny",
                kind=MarkerKind.SECTION,
                end_frame=2,
            ),
        ),
    )
    calls = 0

    def split_id() -> str:
        nonlocal calls
        calls += 1
        return _id(400)

    with pytest.raises(StudioSectionError, match="source-frame boundary"):
        reorder_section(
            original,
            _id(300),
            0,
            split_id_factory=split_id,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "region",
    (
        _region(100, 10, 0, 30, fade_in=11),
        _region(100, 10, 0, 30, fade_out=11),
    ),
)
def test_reorder_section_rejects_a_seam_inside_a_region_fade(
    region: StudioRegion,
) -> None:
    original = _document(regions=(region,))
    snapshot = original.to_dict()
    calls = 0

    def split_id() -> str:
        nonlocal calls
        calls += 1
        return _id(400 + calls)

    with pytest.raises(StudioSectionError, match="region fade"):
        reorder_section(
            original,
            _id(300),
            2,
            split_id_factory=split_id,
        )

    assert calls == 0
    assert original.to_dict() == snapshot


def test_reorder_section_rejects_revision_bounds_and_bad_split_ids() -> None:
    exhausted = _document(revision=MAX_PROJECT_FRAMES)
    with pytest.raises(StudioSectionError, match="revision is exhausted"):
        reorder_section(exhausted, _id(300), 0)

    near_end = _document(
        markers=(
            StudioMarker(
                _id(300),
                MAX_PROJECT_FRAMES - 5,
                "Ending",
                kind=MarkerKind.SECTION,
                end_frame=MAX_PROJECT_FRAMES,
            ),
        )
    )
    with pytest.raises(StudioSectionError, match="project frame range"):
        reorder_section(near_end, _id(300), MAX_PROJECT_FRAMES)

    split = _document(regions=(_region(100, 10, 0, 30),))
    with pytest.raises(StudioSectionError, match="duplicate ID"):
        reorder_section(
            split,
            _id(300),
            0,
            split_id_factory=lambda: _id(100),
        )
    with pytest.raises(StudioSectionError, match="valid UUIDs"):
        reorder_section(
            split,
            _id(300),
            0,
            split_id_factory=lambda: "not-a-uuid",
        )


def test_reorder_section_renderer_preserves_exact_samples_and_source_media(
    tmp_path: Path,
) -> None:
    samples = np.arange(1, 13, dtype=np.float32) / 100.0
    source = tmp_path / "section-source.wav"
    sf.write(source, samples, 8_000, subtype="FLOAT")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    segment = MediaSegment(
        segment_id=_id(20),
        path=source.name,
        project_start_frame=0,
        frame_count=12,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        media_status=MediaStatus.AVAILABLE,
        sha256=digest,
        size_bytes=source.stat().st_size,
        gaps=(),
        has_signal=True,
    )
    track = ProjectTrack(
        track_id=_id(10),
        source_id=_id(110),
        participant_id=None,
        name="Guitar",
        instrument="guitar",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=AlignmentState(),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Section fixture",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=8_000,
        participants=(),
        tracks=(track,),
    )
    project_snapshot = project.to_dict()
    document = replace(
        default_studio_document(project),
        regions=(
            replace(
                default_studio_document(project).regions[0],
                fade_in_frames=3,
                fade_out_frames=1,
                fade_in_curve=FadeCurve.EQUAL_POWER,
                fade_out_curve=FadeCurve.S_CURVE,
            ),
        ),
        markers=(
            StudioMarker(
                _id(300),
                3,
                "Solo",
                kind=MarkerKind.SECTION,
                end_frame=6,
            ),
        ),
    )

    moved = reorder_section(
        document,
        _id(300),
        8,
        split_id_factory=_ids(400, 401, 402),
    )
    original_render = StudioRenderer(project, document, tmp_path).render_block(0, 12)
    rendered = StudioRenderer(project, moved, tmp_path).render_block(0, 12)
    order = [0, 1, 2, 6, 7, 8, 9, 10, 3, 4, 5, 11]

    np.testing.assert_allclose(rendered, original_render[order], atol=1e-7)
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])
    assert project.to_dict() == project_snapshot
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_duplicate_section_repeats_the_block_after_itself_across_every_track() -> (
    None
):
    first = _region(100, 10, 0, 30, fade_in=2, fade_out=3)
    second = _region(101, 11, 5, 10)
    disabled = _region(102, 10, 22, 5, enabled=False)
    tombstone = _region(103, 11, 0, 30, enabled=False, deleted=True)
    contained = _region(104, 11, 12, 6, fade_in=1, fade_out=1)
    original = _document(
        regions=(first, second, disabled, tombstone, contained),
    )
    original_snapshot = original.to_dict()

    doubled = duplicate_section(
        original,
        _id(300),
        id_factory=_ids(400, 401, 402, 403, 404, 405, 406),
    )

    assert doubled.revision == original.revision + 1
    assert original.to_dict() == original_snapshot
    assert tuple(item.region_id for item in doubled.regions) == (
        _id(100),
        _id(400),
        _id(401),
        _id(101),
        _id(402),
        _id(102),
        _id(103),
        _id(104),
        _id(403),
        _id(404),
        _id(405),
    )
    spans = {
        item.region_id: (item.timeline_start_frame, item.timeline_end_frame)
        for item in doubled.regions
    }
    assert spans[_id(100)] == (0, 10)
    assert spans[_id(400)] == (10, 20)
    assert spans[_id(401)] == (30, 40)
    assert spans[_id(101)] == (5, 10)
    assert spans[_id(402)] == (10, 15)
    assert spans[_id(102)] == (32, 37)
    assert spans[_id(103)] == (0, 30)
    assert spans[_id(104)] == (12, 18)
    assert spans[_id(403)] == (20, 30)
    assert spans[_id(404)] == (20, 25)
    assert spans[_id(405)] == (22, 28)
    copies = {item.region_id: item for item in doubled.regions}
    assert copies[_id(403)].source_start_frame == 10
    assert copies[_id(403)].source_end_frame == 20
    assert copies[_id(403)].fade_in_frames == 0
    assert copies[_id(403)].fade_out_frames == 0
    assert copies[_id(404)].source_start_frame == 5
    assert copies[_id(404)].source_end_frame == 10
    assert copies[_id(405)].fade_in_frames == 1
    assert copies[_id(405)].fade_out_frames == 1
    assert copies[_id(102)].enabled is False
    assert copies[_id(103)] is tombstone
    for item in doubled.regions:
        if item.deleted:
            continue
        assert item.source_boundary_for_timeline(item.timeline_start_frame) == (
            item.source_start_frame
        )
        assert item.source_boundary_for_timeline(item.timeline_end_frame) == (
            item.source_end_frame
        )

    marker_map = {item.marker_id: item for item in doubled.markers}
    assert (marker_map[_id(300)].start_frame, marker_map[_id(300)].end_frame) == (
        10,
        20,
    )
    assert (marker_map[_id(406)].start_frame, marker_map[_id(406)].end_frame) == (
        20,
        30,
    )
    assert marker_map[_id(406)].label == "Verse"
    assert marker_map[_id(406)].kind is MarkerKind.SECTION


def test_duplicate_section_copies_lane_inventory_comp_choices_and_crossfades() -> (
    None
):
    left = _region(100, 10, 5, 10)
    right = _region(101, 10, 13, 12)
    lane_region = _region(105, 10, 5, 20)
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(lane_region.region_id,),
    )
    comp = StudioCompRange(_id(501), _id(10), lane.lane_id, 6, 4)
    crossfade = StudioCrossfade(_id(502), left.region_id, right.region_id, 13, 2)
    original = _document(
        regions=(left, right, lane_region),
        markers=(
            StudioMarker(
                _id(300),
                5,
                "Verse",
                kind=MarkerKind.SECTION,
                end_frame=25,
            ),
            StudioMarker(_id(301), 7, "Inside point"),
        ),
        take_lanes=(lane,),
        comp_ranges=(comp,),
        crossfades=(crossfade,),
    )
    original_snapshot = original.to_dict()

    doubled = duplicate_section(
        original,
        _id(300),
        id_factory=_ids(400, 401, 402, 403, 404, 405, 406),
    )

    assert original.to_dict() == original_snapshot
    spans = {
        item.region_id: (item.timeline_start_frame, item.timeline_end_frame)
        for item in doubled.regions
    }
    assert spans[_id(100)] == (5, 15)
    assert spans[_id(101)] == (13, 25)
    assert spans[_id(105)] == (5, 25)
    assert spans[_id(400)] == (25, 35)
    assert spans[_id(401)] == (33, 45)
    assert spans[_id(402)] == (25, 45)

    lanes = {item.lane_id: item for item in doubled.take_lanes}
    assert lanes[_id(500)].region_ids == (_id(105), _id(402))

    markers = {item.marker_id: item for item in doubled.markers}
    assert (markers[_id(403)].start_frame, markers[_id(403)].end_frame) == (25, 45)
    assert markers[_id(403)].label == "Verse"
    assert markers[_id(404)].start_frame == 27
    assert markers[_id(404)].end_frame is None
    assert markers[_id(404)].label == "Inside point"

    comps = {item.comp_range_id: item for item in doubled.comp_ranges}
    assert comps[_id(501)].timeline_start_frame == 6
    assert comps[_id(405)].timeline_start_frame == 26
    assert comps[_id(405)].lane_id == lane.lane_id

    crossfades = {item.crossfade_id: item for item in doubled.crossfades}
    assert crossfades[_id(502)].start_frame == 13
    assert crossfades[_id(406)].start_frame == 33
    assert crossfades[_id(406)].left_region_id == _id(400)
    assert crossfades[_id(406)].right_region_id == _id(401)


def test_duplicate_section_renderer_repeats_exact_samples_and_source_media(
    tmp_path: Path,
) -> None:
    samples = np.arange(1, 13, dtype=np.float32) / 100.0
    source = tmp_path / "duplicate-source.wav"
    sf.write(source, samples, 8_000, subtype="FLOAT")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    segment = MediaSegment(
        segment_id=_id(20),
        path=source.name,
        project_start_frame=0,
        frame_count=12,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        media_status=MediaStatus.AVAILABLE,
        sha256=digest,
        size_bytes=source.stat().st_size,
        gaps=(),
        has_signal=True,
    )
    track = ProjectTrack(
        track_id=_id(10),
        source_id=_id(110),
        participant_id=None,
        name="Guitar",
        instrument="guitar",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=AlignmentState(),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Duplicate fixture",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=8_000,
        participants=(),
        tracks=(track,),
    )
    project_snapshot = project.to_dict()
    document = replace(
        default_studio_document(project),
        regions=(
            replace(
                default_studio_document(project).regions[0],
                fade_in_frames=3,
                fade_out_frames=1,
                fade_in_curve=FadeCurve.EQUAL_POWER,
                fade_out_curve=FadeCurve.S_CURVE,
            ),
        ),
        markers=(
            StudioMarker(
                _id(300),
                3,
                "Solo",
                kind=MarkerKind.SECTION,
                end_frame=6,
            ),
        ),
    )

    doubled = duplicate_section(
        document,
        _id(300),
        id_factory=_ids(400, 401, 402, 403),
    )
    original_render = StudioRenderer(project, document, tmp_path).render_block(0, 12)
    rendered = StudioRenderer(project, doubled, tmp_path).render_block(0, 15)
    order = [0, 1, 2, 3, 4, 5, 3, 4, 5, 6, 7, 8, 9, 10, 11]

    np.testing.assert_allclose(rendered, original_render[order], atol=1e-7)
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])
    assert project.to_dict() == project_snapshot
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    ("hazard", "message"),
    (
        ("section", "crosses a section-duplicate boundary"),
        ("comp", "crosses a section-duplicate boundary"),
        ("crossfade", "crosses a section-duplicate boundary"),
        ("cycle", "crosses a section-duplicate boundary"),
        ("fade", "crosses a region fade"),
    ),
)
def test_duplicate_section_fails_closed_for_intervals_crossing_its_edges(
    hazard: str,
    message: str,
) -> None:
    left = _region(100, 10, 0, 30)
    right = _region(101, 10, 0, 30)
    markers = (
        StudioMarker(
            _id(300),
            10,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=20,
        ),
    )
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(left.region_id,),
    )
    regions: tuple[StudioRegion, ...] = (left, right)
    kwargs: dict[str, object] = {}
    if hazard == "section":
        markers = (
            *markers,
            StudioMarker(
                _id(301),
                19,
                "Unsafe",
                kind=MarkerKind.SECTION,
                end_frame=21,
            ),
        )
    elif hazard == "comp":
        kwargs["take_lanes"] = (lane,)
        kwargs["comp_ranges"] = (
            StudioCompRange(_id(501), _id(10), lane.lane_id, 9, 2),
        )
    elif hazard == "crossfade":
        kwargs["crossfades"] = (
            StudioCrossfade(_id(502), left.region_id, right.region_id, 19, 2),
        )
    elif hazard == "cycle":
        kwargs["cycle_range"] = StudioCycleRange(9, 11, enabled=False)
    else:
        regions = (_region(104, 10, 8, 6, fade_in=4), right)
    original = _document(regions=regions, markers=markers, **kwargs)
    snapshot = original.to_dict()
    calls = 0

    def next_id() -> str:
        nonlocal calls
        calls += 1
        return _id(600 + calls)

    with pytest.raises(StudioSectionError, match=message):
        duplicate_section(
            original,
            _id(300),
            id_factory=next_id,
        )

    assert calls == 0
    assert original.to_dict() == snapshot


@pytest.mark.parametrize(
    "marker_id",
    ("not-a-uuid", _id(999), _id(301), _id(302)),
)
def test_duplicate_section_rejects_invalid_deleted_or_point_markers(
    marker_id: str,
) -> None:
    document = _document(
        markers=(
            StudioMarker(
                _id(300),
                10,
                "Verse",
                kind=MarkerKind.SECTION,
                end_frame=20,
            ),
            StudioMarker(_id(301), 5, "Point"),
            StudioMarker(
                _id(302),
                20,
                "Deleted section",
                kind=MarkerKind.SECTION,
                end_frame=30,
                deleted=True,
            ),
        )
    )
    with pytest.raises(StudioSectionError):
        duplicate_section(document, marker_id)


def test_duplicate_section_rejects_revision_frame_and_id_bounds() -> None:
    exhausted = _document(revision=MAX_PROJECT_FRAMES)
    with pytest.raises(StudioSectionError, match="revision is exhausted"):
        duplicate_section(exhausted, _id(300))

    near_end = _document(
        markers=(
            StudioMarker(
                _id(300),
                MAX_PROJECT_FRAMES - 10,
                "Ending",
                kind=MarkerKind.SECTION,
                end_frame=MAX_PROJECT_FRAMES,
            ),
        )
    )
    with pytest.raises(StudioSectionError, match="project frame range"):
        duplicate_section(near_end, _id(300))

    split = _document(regions=(_region(100, 10, 0, 30),))
    with pytest.raises(StudioSectionError, match="duplicate ID"):
        duplicate_section(
            split,
            _id(300),
            id_factory=lambda: _id(100),
        )
    with pytest.raises(StudioSectionError, match="valid UUIDs"):
        duplicate_section(
            split,
            _id(300),
            id_factory=lambda: "not-a-uuid",
        )
    with pytest.raises(StudioSectionError, match="id_factory must be callable"):
        duplicate_section(
            split,
            _id(300),
            id_factory="not-callable",  # type: ignore[arg-type]
        )


def test_remove_section_closes_the_gap_and_tombstones_the_block() -> None:
    first = _region(100, 10, 0, 30, fade_in=2, fade_out=3)
    second = _region(101, 11, 5, 10)
    disabled = _region(102, 10, 22, 5, enabled=False)
    tombstone = _region(103, 11, 0, 30, enabled=False, deleted=True)
    contained = _region(104, 11, 12, 6)
    markers = (
        StudioMarker(
            _id(300),
            10,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=20,
        ),
        StudioMarker(_id(301), 5, "Before point"),
        StudioMarker(_id(302), 12, "Inside point"),
        StudioMarker(_id(303), 20, "After point"),
        StudioMarker(
            _id(304),
            12,
            "Nested phrase",
            kind=MarkerKind.SECTION,
            end_frame=16,
        ),
        StudioMarker(
            _id(305),
            20,
            "Following phrase",
            kind=MarkerKind.SECTION,
            end_frame=25,
        ),
        StudioMarker(_id(306), 5, "Deleted", deleted=True),
    )
    original = _document(
        regions=(first, second, disabled, tombstone, contained),
        markers=markers,
    )
    original_snapshot = original.to_dict()

    removed = remove_section(
        original,
        _id(300),
        split_id_factory=_ids(400, 401, 402),
    )

    assert removed.revision == original.revision + 1
    assert original.to_dict() == original_snapshot
    values = {item.region_id: item for item in removed.regions}
    assert tuple(item.region_id for item in removed.regions) == (
        _id(100),
        _id(400),
        _id(401),
        _id(101),
        _id(402),
        _id(102),
        _id(103),
        _id(104),
    )
    assert (
        values[_id(100)].timeline_start_frame,
        values[_id(100)].timeline_end_frame,
    ) == (0, 10)
    assert values[_id(100)].fade_in_frames == 2
    assert values[_id(100)].fade_out_frames == 0
    assert values[_id(100)].deleted is False
    assert (
        values[_id(400)].timeline_start_frame,
        values[_id(400)].timeline_end_frame,
    ) == (10, 20)
    assert values[_id(400)].deleted is True
    assert values[_id(400)].enabled is False
    assert values[_id(400)].source_start_frame == 10
    assert values[_id(400)].source_end_frame == 20
    assert (
        values[_id(401)].timeline_start_frame,
        values[_id(401)].timeline_end_frame,
    ) == (10, 20)
    assert values[_id(401)].deleted is False
    assert values[_id(401)].source_start_frame == 20
    assert values[_id(401)].source_end_frame == 30
    assert values[_id(401)].fade_out_frames == 3
    assert (
        values[_id(101)].timeline_start_frame,
        values[_id(101)].timeline_end_frame,
    ) == (5, 10)
    assert values[_id(402)].deleted is True
    assert (
        values[_id(402)].timeline_start_frame,
        values[_id(402)].timeline_end_frame,
    ) == (10, 15)
    assert (
        values[_id(102)].timeline_start_frame,
        values[_id(102)].timeline_end_frame,
    ) == (12, 17)
    assert values[_id(102)].enabled is False
    assert values[_id(102)].deleted is False
    assert values[_id(103)] is tombstone
    assert values[_id(104)].deleted is True
    assert values[_id(104)].enabled is False
    assert (
        values[_id(104)].timeline_start_frame,
        values[_id(104)].timeline_end_frame,
    ) == (12, 18)

    marker_map = {item.marker_id: item for item in removed.markers}
    assert marker_map[_id(300)].deleted is True
    assert (marker_map[_id(300)].start_frame, marker_map[_id(300)].end_frame) == (
        10,
        20,
    )
    assert marker_map[_id(301)].start_frame == 5
    assert marker_map[_id(301)].deleted is False
    assert marker_map[_id(302)].deleted is True
    assert marker_map[_id(303)].start_frame == 10
    assert marker_map[_id(303)].deleted is False
    assert marker_map[_id(304)].deleted is True
    assert (marker_map[_id(305)].start_frame, marker_map[_id(305)].end_frame) == (
        10,
        15,
    )
    assert marker_map[_id(306)] is markers[-1]


def test_remove_section_tombstones_comp_choices_and_crossfades_in_the_block() -> (
    None
):
    left = _region(100, 10, 5, 10)
    right = _region(101, 10, 13, 12)
    later_left = _region(106, 10, 25, 5)
    later_right = _region(107, 10, 28, 6)
    lane_region = _region(105, 10, 5, 20)
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(lane_region.region_id,),
    )
    comp = StudioCompRange(_id(501), _id(10), lane.lane_id, 6, 4)
    inside_crossfade = StudioCrossfade(
        _id(502), left.region_id, right.region_id, 13, 2
    )
    later_crossfade = StudioCrossfade(
        _id(503), later_left.region_id, later_right.region_id, 28, 2
    )
    original = _document(
        regions=(left, right, later_left, later_right, lane_region),
        markers=(
            StudioMarker(
                _id(300),
                5,
                "Verse",
                kind=MarkerKind.SECTION,
                end_frame=25,
            ),
        ),
        take_lanes=(lane,),
        comp_ranges=(comp,),
        crossfades=(inside_crossfade, later_crossfade),
        cycle_range=StudioCycleRange(6, 10, enabled=False),
    )
    original_snapshot = original.to_dict()

    removed = remove_section(original, _id(300))

    assert original.to_dict() == original_snapshot
    values = {item.region_id: item for item in removed.regions}
    assert values[_id(100)].deleted is True
    assert values[_id(101)].deleted is True
    assert values[_id(105)].deleted is True
    assert (
        values[_id(106)].timeline_start_frame,
        values[_id(106)].timeline_end_frame,
    ) == (5, 10)
    assert (
        values[_id(107)].timeline_start_frame,
        values[_id(107)].timeline_end_frame,
    ) == (8, 14)

    lanes = {item.lane_id: item for item in removed.take_lanes}
    assert lanes[_id(500)].region_ids == (_id(105),)

    comps = {item.comp_range_id: item for item in removed.comp_ranges}
    assert comps[_id(501)].deleted is True
    assert comps[_id(501)].enabled is False
    assert comps[_id(501)].timeline_start_frame == 6

    crossfades = {item.crossfade_id: item for item in removed.crossfades}
    assert crossfades[_id(502)].deleted is True
    assert crossfades[_id(502)].start_frame == 13
    assert crossfades[_id(503)].deleted is False
    assert crossfades[_id(503)].start_frame == 8
    assert crossfades[_id(503)].left_region_id == _id(106)
    assert crossfades[_id(503)].right_region_id == _id(107)

    assert removed.cycle_range is None


def test_remove_section_renderer_excises_exactly_the_block(
    tmp_path: Path,
) -> None:
    samples = np.arange(1, 13, dtype=np.float32) / 100.0
    source = tmp_path / "remove-source.wav"
    sf.write(source, samples, 8_000, subtype="FLOAT")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    segment = MediaSegment(
        segment_id=_id(20),
        path=source.name,
        project_start_frame=0,
        frame_count=12,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        media_status=MediaStatus.AVAILABLE,
        sha256=digest,
        size_bytes=source.stat().st_size,
        gaps=(),
        has_signal=True,
    )
    track = ProjectTrack(
        track_id=_id(10),
        source_id=_id(110),
        participant_id=None,
        name="Guitar",
        instrument="guitar",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=AlignmentState(),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Remove fixture",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=8_000,
        participants=(),
        tracks=(track,),
    )
    project_snapshot = project.to_dict()
    document = replace(
        default_studio_document(project),
        regions=(
            replace(
                default_studio_document(project).regions[0],
                fade_in_frames=3,
                fade_out_frames=1,
                fade_in_curve=FadeCurve.EQUAL_POWER,
                fade_out_curve=FadeCurve.S_CURVE,
            ),
        ),
        markers=(
            StudioMarker(
                _id(300),
                3,
                "Solo",
                kind=MarkerKind.SECTION,
                end_frame=6,
            ),
        ),
    )

    removed = remove_section(
        document,
        _id(300),
        split_id_factory=_ids(400, 401),
    )
    original_render = StudioRenderer(project, document, tmp_path).render_block(0, 12)
    rendered = StudioRenderer(project, removed, tmp_path).render_block(0, 9)
    order = [0, 1, 2, 6, 7, 8, 9, 10, 11]

    np.testing.assert_allclose(rendered, original_render[order], atol=1e-7)
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])
    assert project.to_dict() == project_snapshot
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    ("hazard", "message"),
    (
        ("section", "crosses a section-removal boundary"),
        ("comp", "crosses a section-removal boundary"),
        ("crossfade", "crosses a section-removal boundary"),
        ("cycle", "crosses a section-removal boundary"),
        ("fade", "crosses a region fade"),
    ),
)
def test_remove_section_fails_closed_for_intervals_crossing_its_edges(
    hazard: str,
    message: str,
) -> None:
    left = _region(100, 10, 0, 30)
    right = _region(101, 10, 0, 30)
    markers = (
        StudioMarker(
            _id(300),
            10,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=20,
        ),
    )
    lane = StudioTakeLane(
        lane_id=_id(500),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        region_ids=(left.region_id,),
    )
    regions: tuple[StudioRegion, ...] = (left, right)
    kwargs: dict[str, object] = {}
    if hazard == "section":
        markers = (
            *markers,
            StudioMarker(
                _id(301),
                19,
                "Unsafe",
                kind=MarkerKind.SECTION,
                end_frame=21,
            ),
        )
    elif hazard == "comp":
        kwargs["take_lanes"] = (lane,)
        kwargs["comp_ranges"] = (
            StudioCompRange(_id(501), _id(10), lane.lane_id, 9, 2),
        )
    elif hazard == "crossfade":
        kwargs["crossfades"] = (
            StudioCrossfade(_id(502), left.region_id, right.region_id, 19, 2),
        )
    elif hazard == "cycle":
        kwargs["cycle_range"] = StudioCycleRange(9, 11, enabled=False)
    else:
        regions = (_region(104, 10, 8, 6, fade_in=4), right)
    original = _document(regions=regions, markers=markers, **kwargs)
    snapshot = original.to_dict()
    calls = 0

    def next_id() -> str:
        nonlocal calls
        calls += 1
        return _id(600 + calls)

    with pytest.raises(StudioSectionError, match=message):
        remove_section(
            original,
            _id(300),
            split_id_factory=next_id,
        )

    assert calls == 0
    assert original.to_dict() == snapshot


@pytest.mark.parametrize(
    "marker_id",
    ("not-a-uuid", _id(999), _id(301), _id(302)),
)
def test_remove_section_rejects_invalid_deleted_or_point_markers(
    marker_id: str,
) -> None:
    document = _document(
        markers=(
            StudioMarker(
                _id(300),
                10,
                "Verse",
                kind=MarkerKind.SECTION,
                end_frame=20,
            ),
            StudioMarker(_id(301), 5, "Point"),
            StudioMarker(
                _id(302),
                20,
                "Deleted section",
                kind=MarkerKind.SECTION,
                end_frame=30,
                deleted=True,
            ),
        )
    )
    with pytest.raises(StudioSectionError):
        remove_section(document, marker_id)


def test_remove_section_rejects_revision_and_id_bounds() -> None:
    exhausted = _document(revision=MAX_PROJECT_FRAMES)
    with pytest.raises(StudioSectionError, match="revision is exhausted"):
        remove_section(exhausted, _id(300))

    split = _document(regions=(_region(100, 10, 0, 30),))
    with pytest.raises(StudioSectionError, match="duplicate ID"):
        remove_section(
            split,
            _id(300),
            split_id_factory=lambda: _id(100),
        )
    with pytest.raises(StudioSectionError, match="valid UUIDs"):
        remove_section(
            split,
            _id(300),
            split_id_factory=lambda: "not-a-uuid",
        )
    with pytest.raises(StudioSectionError, match="split_id_factory must be callable"):
        remove_section(
            split,
            _id(300),
            split_id_factory="not-callable",  # type: ignore[arg-type]
        )

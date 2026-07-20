"""Focused coverage for the shared, non-destructive Studio render path."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.studio_project import (
    FadeCurve,
    StudioCompRange,
    StudioMaster,
    StudioTakeLane,
    default_studio_document,
)
from core.studio_renderer import (
    MAX_RENDER_BLOCK_FRAMES,
    StudioRenderError,
    StudioRenderer,
)
from core.take_project import (
    AlignmentState,
    GapInterval,
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _segment(
    root: Path,
    number: int,
    samples: np.ndarray,
    *,
    rate: int,
    start: int = 0,
    gaps: tuple[GapInterval, ...] = (),
) -> tuple[MediaSegment, Path]:
    path = root / f"source-{number}.wav"
    sf.write(path, np.asarray(samples, dtype=np.float32), rate, subtype="FLOAT")
    info = sf.info(path)
    segment = MediaSegment(
        segment_id=_id(number),
        path=path.name,
        project_start_frame=start,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        media_status=MediaStatus.AVAILABLE,
        sha256=_digest(path),
        size_bytes=path.stat().st_size,
        gaps=gaps,
        has_signal=True,
    )
    return segment, path


def _track(
    number: int,
    segments: tuple[MediaSegment, ...],
    *,
    order: int = 0,
    alignment: AlignmentState | None = None,
) -> ProjectTrack:
    return ProjectTrack(
        track_id=_id(number),
        source_id=_id(number + 100),
        participant_id=None,
        name=f"Track {number}",
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=order,
        segments=segments,
        alignment=alignment or AlignmentState(),
    )


def _project(
    tracks: tuple[ProjectTrack, ...],
    *,
    rate: int,
    take_id: str = _id(2),
) -> TakeProject:
    return TakeProject(
        session_id=_id(1),
        take_id=take_id,
        session_title="Renderer fixture",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=rate,
        participants=(),
        tracks=tracks,
    )


def _render(
    renderer: StudioRenderer,
    *,
    end: int | None = None,
    block_frames: int = 3,
) -> np.ndarray:
    blocks = tuple(renderer.iter_blocks(end_frame=end, block_frames=block_frames))
    if not blocks:
        return np.zeros((0, 2), dtype=np.float32)
    assert all(block.shape[0] <= block_frames for block in blocks)
    return np.concatenate(blocks)


def test_split_move_trim_duplicate_and_gaps_share_one_block_stable_mapping(
    tmp_path: Path,
) -> None:
    gap = GapInterval(2, 2, "fixture dropout")
    segment, source = _segment(
        tmp_path,
        20,
        np.arange(1, 9, dtype=np.float32) / 10.0,
        rate=8_000,
        gaps=(gap,),
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project)
    source_hash = _digest(source)
    original = document.regions[0]

    split = document.split_region(original.region_id, 4, right_region_id=_id(30))
    moved = split.move_region(_id(30), 6)
    duplicated = moved.duplicate_region(
        original.region_id,
        new_region_id=_id(31),
        timeline_start_frame=10,
    )
    edited = duplicated.trim_region(
        _id(31), timeline_start_frame=11, timeline_frame_count=2
    )
    renderer = StudioRenderer(project, edited, tmp_path, block_frames=2)

    two_frame_blocks = _render(renderer, end=13, block_frames=2)
    five_frame_blocks = _render(renderer, end=13, block_frames=5)

    assert two_frame_blocks.dtype == np.float32
    np.testing.assert_array_equal(two_frame_blocks, five_frame_blocks)
    np.testing.assert_allclose(
        two_frame_blocks[:, 0],
        np.array(
            [0.1, 0.2, 0.0, 0.0, 0.0, 0.0, 0.5, 0.6, 0.7, 0.8, 0.0, 0.2, 0.0],
            dtype=np.float32,
        ),
        atol=1e-7,
    )
    np.testing.assert_array_equal(two_frame_blocks[:, 0], two_frame_blocks[:, 1])
    assert renderer.timeline_end_frame == 13
    assert _digest(source) == source_hash


def test_affine_mapping_survives_split_and_move_with_rate_conversion(
    tmp_path: Path,
) -> None:
    segment, _source = _segment(
        tmp_path,
        20,
        np.array([0.0, 0.2, 0.4, 0.6, 0.8], dtype=np.float32),
        rate=5_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    original = default_studio_document(project)
    original_region = original.regions[0]
    assert original_region.timeline_frame_count == 8

    unsplit = StudioRenderer(project, original, tmp_path).render_block(0, 8)
    split = original.split_region(original_region.region_id, 3, right_region_id=_id(30))
    split_audio = StudioRenderer(project, split, tmp_path).render_block(0, 8)

    # A split is an edit boundary, not a new resampling slope.
    np.testing.assert_array_equal(split_audio, unsplit)

    moved = split.move_region(_id(30), 5)
    moved_audio = StudioRenderer(project, moved, tmp_path).render_block(0, 10)
    np.testing.assert_array_equal(moved_audio[:3], unsplit[:3])
    np.testing.assert_array_equal(moved_audio[3:5], np.zeros((2, 2), dtype=np.float32))
    np.testing.assert_array_equal(moved_audio[5:10], unsplit[3:8])


def test_region_fades_equal_power_crossfade_and_full_mix_controls(
    tmp_path: Path,
) -> None:
    left, _ = _segment(tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000)
    right, _ = _segment(
        tmp_path,
        21,
        np.full(4, 0.5, dtype=np.float32),
        rate=8_000,
        start=2,
    )
    backing, _ = _segment(tmp_path, 22, np.full(6, 0.75, dtype=np.float32), rate=8_000)
    lead_track = _track(10, (left, right), order=0)
    backing_track = _track(11, (backing,), order=1)
    project = _project((lead_track, backing_track), rate=8_000)
    document = default_studio_document(project)
    left_region = next(
        item for item in document.regions if item.source_segment_id == left.segment_id
    )
    right_region = next(
        item for item in document.regions if item.source_segment_id == right.segment_id
    )
    document = document.set_region_fades(
        left_region.region_id,
        fade_in_frames=2,
        fade_out_frames=0,
        fade_in_curve=FadeCurve.LINEAR,
    )
    document = document.set_region_fades(
        right_region.region_id,
        fade_in_frames=0,
        fade_out_frames=2,
        fade_out_curve=FadeCurve.LINEAR,
    )
    document = document.set_crossfade(
        left_region.region_id,
        right_region.region_id,
        start_frame=2,
        frame_count=2,
        curve=FadeCurve.EQUAL_POWER,
        crossfade_id=_id(40),
    )
    document = document.update_track(
        lead_track.track_id,
        trim_gain=0.5,
        fader_gain=0.5,
        pan=1.0,
        solo=True,
    ).set_master(StudioMaster(gain=2.0, limiter_enabled=False))

    rendered = StudioRenderer(project, document, tmp_path).render_block(0, 6)

    np.testing.assert_array_equal(rendered[:, 0], np.zeros(6, dtype=np.float32))
    np.testing.assert_allclose(
        rendered[:, 1],
        np.array([0.0, 0.125, 0.125, 0.25, 0.25, 0.0], dtype=np.float32),
        atol=1e-7,
    )

    muted_solo = document.update_track(lead_track.track_id, muted=True)
    np.testing.assert_array_equal(
        StudioRenderer(project, muted_solo, tmp_path).render_block(0, 6),
        np.zeros((6, 2), dtype=np.float32),
    )

    limited = document.update_track(
        lead_track.track_id,
        trim_gain=1.0,
        fader_gain=1.0,
        pan=0.0,
        solo=False,
    ).set_master(StudioMaster(gain=2.0, limiter_enabled=True))
    limited_audio = StudioRenderer(project, limited, tmp_path).render_block(0, 6)
    assert float(np.max(np.abs(limited_audio))) == 1.0


def test_three_frame_equal_power_crossfade_has_exact_endpoints_and_midpoint(
    tmp_path: Path,
) -> None:
    left, _ = _segment(tmp_path, 20, np.ones(5, dtype=np.float32), rate=8_000)
    right, _ = _segment(
        tmp_path,
        21,
        np.full(5, 2.0, dtype=np.float32),
        rate=8_000,
        start=2,
    )
    track = _track(10, (left, right))
    project = _project((track,), rate=8_000)
    document = default_studio_document(project)
    regions = {item.source_segment_id: item for item in document.regions}
    document = document.set_crossfade(
        regions[left.segment_id].region_id,
        regions[right.segment_id].region_id,
        start_frame=2,
        frame_count=3,
        curve=FadeCurve.EQUAL_POWER,
        crossfade_id=_id(40),
    ).set_master(StudioMaster(limiter_enabled=False))

    rendered = StudioRenderer(project, document, tmp_path).render_block(2, 3)
    expected = np.array([1.0, 3.0 / np.sqrt(2.0), 2.0], dtype=np.float32)
    np.testing.assert_allclose(rendered[:, 0], expected, atol=1e-6)
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])


def test_comp_range_replaces_base_only_when_selected_lane_fully_covers_it(
    tmp_path: Path,
) -> None:
    base, _ = _segment(tmp_path, 20, np.full(6, 0.1, dtype=np.float32), rate=8_000)
    alternate, _ = _segment(tmp_path, 21, np.full(6, 0.8, dtype=np.float32), rate=8_000)
    track = _track(10, (base, alternate))
    project = _project((track,), rate=8_000)
    document = default_studio_document(project)
    alternate_region = next(
        item
        for item in document.regions
        if item.source_segment_id == alternate.segment_id
    )
    lane = StudioTakeLane(
        lane_id=_id(30),
        track_id=track.track_id,
        source_take_id=project.take_id,
        source_track_id=track.track_id,
        name="Alternate",
        region_ids=(alternate_region.region_id,),
    )
    comp = StudioCompRange(
        comp_range_id=_id(31),
        track_id=track.track_id,
        lane_id=lane.lane_id,
        timeline_start_frame=2,
        frame_count=3,
    )
    document = document.upsert_take_lane(lane).select_comp_range(comp)

    rendered = StudioRenderer(project, document, tmp_path).render_block(0, 6)

    np.testing.assert_allclose(
        rendered[:, 0],
        np.array([0.1, 0.1, 0.8, 0.8, 0.8, 0.1], dtype=np.float32),
        atol=1e-7,
    )
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])

    disabled_lane = document.remove_comp_range(comp.comp_range_id).upsert_take_lane(
        replace(lane, enabled=False)
    )
    disabled_audio = StudioRenderer(project, disabled_lane, tmp_path).render_block(0, 6)
    np.testing.assert_allclose(
        disabled_audio[:, 0], np.full(6, 0.1, dtype=np.float32), atol=1e-7
    )


def test_missing_replaced_and_cross_take_media_fail_before_audio_is_returned(
    tmp_path: Path,
) -> None:
    segment, source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    track = _track(10, (segment,))
    project = _project((track,), rate=8_000)
    document = default_studio_document(project)

    cross_take = replace(
        document,
        regions=(replace(document.regions[0], source_take_id=_id(999)),),
    )
    with pytest.raises(StudioRenderError, match="different take"):
        StudioRenderer(project, cross_take, tmp_path)

    wrong_segment_owner = replace(
        document,
        regions=(replace(document.regions[0], source_segment_id=_id(999)),),
    )
    with pytest.raises(StudioRenderError, match="source catalog"):
        StudioRenderer(project, wrong_segment_owner, tmp_path)

    renderer = StudioRenderer(project, document, tmp_path)
    original_bytes = source.read_bytes()
    changed = bytearray(original_bytes)
    changed[-1] ^= 0x01
    source.write_bytes(changed)
    with pytest.raises(StudioRenderError, match="checksum"):
        renderer.render_block(0, 4)

    source.write_bytes(original_bytes)
    source.unlink()
    with pytest.raises(StudioRenderError, match="missing"):
        renderer.render_block(0, 4)


def test_stream_is_bounded_seekable_and_does_not_mutate_sources(
    tmp_path: Path,
) -> None:
    segment, source = _segment(
        tmp_path, 20, np.linspace(0.0, 0.9, 10, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project)
    before = source.read_bytes()
    renderer = StudioRenderer(project, document, tmp_path, block_frames=3)

    with renderer.open(start_frame=1, end_frame=9) as stream:
        assert stream.read(3).shape == (3, 2)
        assert stream.remaining_frames == 5
        assert stream.seek(2) == 2
        assert stream.read(2).shape == (2, 2)
    assert stream.closed is True
    with pytest.raises(StudioRenderError, match="closed"):
        stream.read(1)
    with pytest.raises(StudioRenderError, match="between"):
        renderer.render_block(0, MAX_RENDER_BLOCK_FRAMES + 1)
    with pytest.raises(StudioRenderError, match="between"):
        renderer.render_block(0, 0)
    assert source.read_bytes() == before

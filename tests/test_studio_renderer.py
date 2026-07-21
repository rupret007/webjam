"""Focused coverage for the shared, non-destructive Studio render path."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.studio_renderer as studio_renderer
from core.studio_project import (
    FadeCurve,
    StudioCompRange,
    StudioCycleRange,
    StudioMaster,
    StudioProjectError,
    StudioTakeLane,
    default_studio_document,
)
from core.studio_renderer import (
    MAX_RENDER_BLOCK_FRAMES,
    StudioRenderError,
    StudioRenderer,
)
from core.take_player import PlaybackError, StudioPlaybackSourceError, TakePlayer
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


class _DeferredStudioSink:
    def __init__(self) -> None:
        self.pull = None
        self.stopped = False

    def start(self, _rate, _blocksize, pull) -> None:
        self.pull = pull

    def stop(self) -> None:
        self.stopped = True


def _wait_for_studio_buffer(player: TakePlayer, minimum_frames: int = 1) -> None:
    deadline = time.monotonic() + 1.0
    while player.studio_buffered_frames < minimum_frames:
        if player.terminal_error is not None or time.monotonic() >= deadline:
            break
        time.sleep(0.001)
    assert player.studio_buffered_frames >= minimum_frames


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


def test_inactive_take_lane_source_is_not_validated_prepared_or_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, _base_path = _segment(
        tmp_path, 20, np.full(6, 0.1, dtype=np.float32), rate=8_000
    )
    alternate, _alternate_path = _segment(
        tmp_path, 21, np.full(6, 0.8, dtype=np.float32), rate=8_000
    )
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
        name="Unused alternate",
        region_ids=(alternate_region.region_id,),
    )
    document = document.upsert_take_lane(lane)
    renderer = StudioRenderer(project, document, tmp_path)
    hashes: list[int] = []
    original_hash = studio_renderer._sha256_descriptor

    def counted_hash(descriptor, cancel_check=None):
        hashes.append(descriptor)
        return original_hash(descriptor, cancel_check)

    monkeypatch.setattr(studio_renderer, "_sha256_descriptor", counted_hash)
    with renderer.open(end_frame=6, realtime_safe=True) as stream:
        assert len(stream._readers) == 1
        rendered = stream.read(6)

    assert len(hashes) == 1
    assert len(renderer._validated_sources) == 1
    assert alternate_region.source_segment_id not in {
        source_key[2] for source_key in renderer._validated_sources
    }
    np.testing.assert_allclose(rendered[:, 0], np.full(6, 0.1), atol=1e-7)

    audition = document.select_comp_range(
        StudioCompRange(
            comp_range_id=_id(31),
            track_id=track.track_id,
            lane_id=lane.lane_id,
            timeline_start_frame=0,
            frame_count=6,
        )
    )
    audition_renderer = StudioRenderer(project, audition, tmp_path)
    with audition_renderer.open(end_frame=6, realtime_safe=True) as stream:
        assert len(stream._readers) == 2
        auditioned = stream.read(6)
    np.testing.assert_allclose(auditioned[:, 0], np.full(6, 0.8), atol=1e-7)


def test_missing_replaced_and_cross_take_media_fail_before_audio_is_returned(
    tmp_path: Path,
) -> None:
    segment, source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    track = _track(10, (segment,))
    project = _project((track,), rate=8_000)
    document = default_studio_document(project)

    with pytest.raises(StudioProjectError, match="cross-take region"):
        replace(
            document,
            regions=(replace(document.regions[0], source_take_id=_id(999)),),
        )

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


def test_stream_rejects_same_shape_source_swap_after_validation(
    tmp_path: Path,
) -> None:
    segment, source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    forged = tmp_path / "forged.wav"
    sf.write(forged, np.full(4, 0.75, dtype=np.float32), 8_000, subtype="FLOAT")
    assert sf.info(forged).frames == segment.frame_count
    assert forged.stat().st_size == source.stat().st_size
    project = _project((_track(10, (segment,)),), rate=8_000)
    with pytest.warns(DeprecationWarning, match="deprecated and ignored"):
        renderer = StudioRenderer(
            project,
            default_studio_document(project),
            tmp_path,
            verify_checksums=False,
        )
    assert renderer.verify_checksums is True
    parked = tmp_path / "validated-original.wav"

    try:
        with renderer.open(end_frame=4) as stream:
            # Validation has completed but libsndfile has not opened the lazy
            # source reader. A path-based implementation would now render the
            # forged same-shape inode and could be fooled if the original were
            # restored before an export's final checksum pass.
            os.replace(source, parked)
            os.replace(forged, source)
            with pytest.raises(StudioRenderError, match="replaced after validation"):
                stream.read(4)
    finally:
        if parked.exists():
            if source.exists() or source.is_symlink():
                source.unlink()
            os.replace(parked, source)


def test_deprecated_checksum_opt_out_is_ignored(tmp_path: Path) -> None:
    segment, source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    with pytest.warns(DeprecationWarning, match="deprecated and ignored"):
        renderer = StudioRenderer(
            project,
            default_studio_document(project),
            tmp_path,
            verify_checksums=False,
        )

    changed = bytearray(source.read_bytes())
    changed[-1] ^= 0x01
    source.write_bytes(changed)

    with pytest.raises(StudioRenderError, match="checksum"):
        renderer.render_block(0, 4)


def test_final_component_source_symlink_is_rejected(tmp_path: Path) -> None:
    segment, source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    target = tmp_path / "real-source.wav"
    os.replace(source, target)
    try:
        try:
            source.symlink_to(target.name)
        except OSError as exc:  # pragma: no cover - privilege-limited Windows
            pytest.skip(f"symlinks are unavailable: {exc}")
        project = _project((_track(10, (segment,)),), rate=8_000)
        renderer = StudioRenderer(project, default_studio_document(project), tmp_path)

        with pytest.raises(StudioRenderError, match="symbolic link"):
            renderer.render_block(0, 4)
    finally:
        if source.is_symlink():
            source.unlink()
        if target.exists():
            os.replace(target, source)


def test_nested_source_descriptor_is_closed_when_child_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "source.wav").write_bytes(b"regular source fixture")
    real_open = studio_renderer.os.open
    real_fstat = studio_renderer.os.fstat
    real_close = studio_renderer.os.close
    child_descriptor: int | None = None
    closed: set[int] = set()

    def tracked_open(path, flags, *args, **kwargs):
        nonlocal child_descriptor
        descriptor = real_open(path, flags, *args, **kwargs)
        if path == "media":
            child_descriptor = descriptor
        return descriptor

    def failing_fstat(descriptor):
        if descriptor == child_descriptor:
            raise OSError("injected child fstat failure")
        return real_fstat(descriptor)

    def tracked_close(descriptor):
        closed.add(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(studio_renderer.os, "open", tracked_open)
    monkeypatch.setattr(studio_renderer.os, "fstat", failing_fstat)
    monkeypatch.setattr(studio_renderer.os, "close", tracked_close)

    with pytest.raises(StudioRenderError, match="missing|symbolic link"):
        studio_renderer._open_bound_source_dirfd(
            tmp_path,
            ("media", "source.wav"),
            None,
        )

    assert child_descriptor is not None
    assert child_descriptor in closed


def test_compatible_renderer_reuses_descriptor_bound_media_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, _source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project)
    validated = StudioRenderer(project, document, tmp_path)
    reused = StudioRenderer(
        project,
        document.update_track(project.tracks[0].track_id, pan=0.5),
        tmp_path,
    )
    validated.validate_media()

    def no_second_hash(_descriptor):
        raise AssertionError("compatible validation should not hash twice")

    monkeypatch.setattr(studio_renderer, "_sha256_descriptor", no_second_hash)
    reused.reuse_media_validation(validated)

    block = reused.render_block(0, 4)
    assert block.shape == (4, 2)


def test_media_checksum_validation_is_cooperatively_cancellable(
    tmp_path: Path,
) -> None:
    segment, _source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    renderer = StudioRenderer(project, default_studio_document(project), tmp_path)
    checks = 0

    class Cancelled(RuntimeError):
        pass

    def cancel_after_one_block() -> None:
        nonlocal checks
        checks += 1
        if checks > 1:
            raise Cancelled("stop validation")

    with pytest.raises(Cancelled, match="stop validation"):
        renderer.validate_media(cancel_after_one_block)
    assert renderer._media_validated is False
    assert renderer._validated_sources == {}


def test_prevalidated_open_rechecks_media_cooperatively(
    tmp_path: Path,
) -> None:
    segment, _source = _segment(
        tmp_path, 20, np.full(4, 0.25, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    renderer = StudioRenderer(project, default_studio_document(project), tmp_path)
    renderer.validate_media()

    class Cancelled(RuntimeError):
        pass

    checks = 0

    def cancel_now() -> None:
        nonlocal checks
        checks += 1
        raise Cancelled("stop currentness check")

    with pytest.raises(Cancelled, match="stop currentness check"):
        renderer.open(end_frame=4, cancel_check=cancel_now)
    assert checks == 1


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


def test_stream_runtime_mix_reports_track_contributions_without_reopening_sources(
    tmp_path: Path,
) -> None:
    first_segment, _first = _segment(
        tmp_path, 20, np.full(4, 0.2, dtype=np.float32), rate=8_000
    )
    second_segment, _second = _segment(
        tmp_path, 21, np.full(4, 0.3, dtype=np.float32), rate=8_000
    )
    project = _project(
        (
            _track(10, (first_segment,), order=0),
            _track(11, (second_segment,), order=1),
        ),
        rate=8_000,
    )
    document = default_studio_document(project)
    renderer = StudioRenderer(project, document, tmp_path)

    with renderer.open(end_frame=4) as stream:
        stream.set_track_mix(_id(10), gain=0.5, pan=-1.0)
        stream.set_track_mix(_id(11), muted=True)
        mix, tracks = stream.read_with_tracks(4)

    assert set(tracks) == {_id(10), _id(11)}
    np.testing.assert_allclose(tracks[_id(10)][:, 0], 0.1, atol=1e-6)
    np.testing.assert_allclose(tracks[_id(10)][:, 1], 0.0, atol=1e-6)
    np.testing.assert_array_equal(tracks[_id(11)], np.zeros((4, 2), np.float32))
    np.testing.assert_array_equal(mix, tracks[_id(10)])


def test_take_player_uses_the_shared_studio_stream_for_transport_and_live_mix(
    tmp_path: Path,
) -> None:
    class PullSink:
        pull = None

        def start(self, _rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            pass

    segment, _source = _segment(
        tmp_path, 20, np.full(8, 0.4, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project).set_region_fades(
        default_studio_document(project).regions[0].region_id,
        fade_in_frames=3,
        fade_out_frames=2,
    )
    expected_renderer = StudioRenderer(project, document, tmp_path)
    with expected_renderer.open(end_frame=8) as stream:
        stream.set_track_mix(_id(10), gain=0.5, pan=1.0)
        expected = stream.read(8)

    sink = PullSink()
    levels: dict[int, float] = {}
    stereo_levels: dict[int, tuple[float, float, bool]] = {}
    master_levels: list[tuple[float, float, bool]] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=8,
        sink=sink,
        on_levels=levels.update,
        on_stereo_levels=stereo_levels.update,
        on_master_level=master_levels.append,
    )
    player.load_studio(project, document, tmp_path)
    player.set_gain(0, 0.5)
    player.set_pan(0, 1.0)
    player.play()
    actual = sink.pull(8)
    player.drain_studio_notifications()
    player.stop()

    np.testing.assert_allclose(actual, expected, atol=1e-7)
    assert levels[0] > 0.0
    assert stereo_levels[0][0] == pytest.approx(0.0)
    assert stereo_levels[0][1] > 0.0
    assert stereo_levels[0][2] is False
    assert master_levels and master_levels[-1][1] > 0.0
    assert player.duration_s == pytest.approx(8 / 8_000)


def test_cycle_disabled_pipeline_matches_authoritative_renderer_for_varied_pulls(
    tmp_path: Path,
) -> None:
    first_values = np.linspace(-0.7, 0.8, 73, dtype=np.float32)
    second_values = np.linspace(0.6, -0.4, 51, dtype=np.float32)
    first_segment, first_source = _segment(
        tmp_path,
        38,
        first_values,
        rate=8_000,
    )
    second_segment, second_source = _segment(
        tmp_path,
        39,
        second_values,
        rate=8_000,
        start=5,
    )
    project = _project(
        (
            _track(10, (first_segment,), order=0),
            _track(11, (second_segment,), order=1),
        ),
        rate=8_000,
    )
    document = (
        default_studio_document(project)
        .update_track(_id(10), trim_gain=0.8, fader_gain=1.1, pan=-0.25)
        .update_track(_id(11), trim_gain=1.2, fader_gain=0.9, pan=0.4)
        .set_master(StudioMaster(gain=1.3, limiter_enabled=False))
    )
    renderer = StudioRenderer(project, document, tmp_path)
    with renderer.open(end_frame=renderer.total_frames) as stream:
        expected_raw = stream.read(renderer.total_frames)
    expected, _clipped = studio_renderer.studio_delivery_block(expected_raw)

    sink = _DeferredStudioSink()
    finished: list[bool] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=11,
        sink=sink,
        on_finished=lambda: finished.append(True),
    )
    player.load_studio(project, document, tmp_path)
    before = (_digest(first_source), _digest(second_source))
    player.play()
    assert sink.pull is not None

    remaining = len(expected)
    requested = (1, 7, 3, 11, 2, 9, 5)
    blocks: list[np.ndarray] = []
    index = 0
    while remaining:
        count = min(remaining, requested[index % len(requested)])
        blocks.append(sink.pull(count))
        remaining -= count
        index += 1
    actual = np.concatenate(blocks)
    player.drain_studio_notifications()

    np.testing.assert_array_equal(actual, expected)
    assert player.position_frame == len(expected)
    assert finished == [True]
    assert (_digest(first_source), _digest(second_source)) == before
    player.stop()


def test_realtime_pull_uses_no_path_or_descriptor_stat_syscalls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PullSink:
        pull = None

        def start(self, _rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            pass

    segment, _source = _segment(
        tmp_path, 20, np.full(32, 0.4, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = PullSink()
    player = TakePlayer(samplerate=8_000, sink=sink)
    player.load_studio(project, default_studio_document(project), tmp_path)
    preparation = player.prepare_studio_playback()
    assert player.install_studio_preparation(preparation)
    player.play()
    assert sink.pull is not None

    violations: list[str] = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            violations.append(name)
            raise AssertionError(f"{name} ran in realtime pull")

        return fail

    monkeypatch.setattr(studio_renderer, "_open_bound_source", forbidden("open"))
    monkeypatch.setattr(
        studio_renderer,
        "_require_bound_source_current",
        forbidden("path-stat"),
    )
    monkeypatch.setattr(studio_renderer.os, "fstat", forbidden("fstat"))
    result: list[np.ndarray] = []
    callback = threading.Thread(
        target=lambda: result.append(sink.pull(8)),
        name="test-portaudio-callback",
    )
    callback.start()
    callback.join(1.0)

    assert not callback.is_alive(), "realtime pull exceeded its one-second deadline"
    assert violations == []
    assert len(result) == 1
    np.testing.assert_allclose(result[0][:, 0], 0.4, atol=1e-7)
    player.stop()


def test_background_prefill_gives_first_pulls_without_callback_source_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, _source = _segment(
        tmp_path,
        30,
        np.full(2_000, 0.4, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    levels: list[dict[int, float]] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=64,
        sink=sink,
        on_levels=lambda value: levels.append(dict(value)),
    )
    player.load_studio(project, default_studio_document(project), tmp_path)

    callback_thread_ids: list[int] = []
    renderer_thread_ids: list[int] = []
    source_thread_ids: list[int] = []
    renderer_lock_violations: list[bool] = []
    pipeline_box: list[object] = []
    hold_future_reads = threading.Event()
    producer_waiting = threading.Event()
    release_producer = threading.Event()

    original_render_read = studio_renderer.StudioRenderStream.read_with_tracks
    original_soundfile_seek = sf.SoundFile.seek
    original_soundfile_read = sf.SoundFile.read
    original_open_bound = studio_renderer._open_bound_source
    original_require_current = studio_renderer._require_bound_source_current
    original_fstat = studio_renderer.os.fstat

    def observed_render_read(stream, frame_count):
        renderer_thread_ids.append(threading.get_ident())
        if pipeline_box:
            pipeline = pipeline_box[0]
            acquired = pipeline._condition.acquire(blocking=False)
            renderer_lock_violations.append(not acquired)
            if acquired:
                pipeline._condition.release()
        if hold_future_reads.is_set():
            producer_waiting.set()
            assert release_producer.wait(1.0)
        return original_render_read(stream, frame_count)

    def observed_seek(reader, *args, **kwargs):
        source_thread_ids.append(threading.get_ident())
        return original_soundfile_seek(reader, *args, **kwargs)

    def observed_read(reader, *args, **kwargs):
        source_thread_ids.append(threading.get_ident())
        return original_soundfile_read(reader, *args, **kwargs)

    def observed_open_bound(*args, **kwargs):
        source_thread_ids.append(threading.get_ident())
        return original_open_bound(*args, **kwargs)

    def observed_require_current(*args, **kwargs):
        source_thread_ids.append(threading.get_ident())
        return original_require_current(*args, **kwargs)

    def observed_fstat(*args, **kwargs):
        source_thread_ids.append(threading.get_ident())
        return original_fstat(*args, **kwargs)

    monkeypatch.setattr(
        studio_renderer.StudioRenderStream,
        "read_with_tracks",
        observed_render_read,
    )
    monkeypatch.setattr(sf.SoundFile, "seek", observed_seek)
    monkeypatch.setattr(sf.SoundFile, "read", observed_read)
    monkeypatch.setattr(studio_renderer, "_open_bound_source", observed_open_bound)
    monkeypatch.setattr(
        studio_renderer,
        "_require_bound_source_current",
        observed_require_current,
    )
    monkeypatch.setattr(studio_renderer.os, "fstat", observed_fstat)

    preparation = player.prepare_studio_playback()
    pipeline = preparation.pipeline
    assert pipeline is not None
    pipeline_box.append(pipeline)
    assert pipeline.buffered_frames >= pipeline.prime_frames
    assert pipeline.prime_frames >= 8_000 * 50 // 1_000
    assert pipeline.capacity_frames <= 8_000 * 100 // 1_000
    assert pipeline.buffered_frames <= pipeline.capacity_frames
    assert pipeline.buffered_bytes <= pipeline.capacity_bytes
    assert player.install_studio_preparation(preparation)

    hold_future_reads.set()
    player.play()
    assert sink.pull is not None
    assert producer_waiting.wait(1.0)
    results: list[np.ndarray] = []

    def consume_prefill() -> None:
        callback_thread_ids.append(threading.get_ident())
        for _ in range(3):
            results.append(sink.pull(64))

    callback = threading.Thread(
        target=consume_prefill,
        name="test-portaudio-callback",
    )
    callback.start()
    callback.join(1.0)
    assert not callback.is_alive()
    assert len(results) == 3
    for block in results:
        np.testing.assert_allclose(block, 0.4, atol=1e-7)
    assert levels == []
    assert player.drain_studio_notifications() == 3
    assert len(levels) == 3
    callback_id = callback_thread_ids[0]
    assert renderer_thread_ids
    assert source_thread_ids
    assert callback_id not in renderer_thread_ids
    assert callback_id not in source_thread_ids
    assert renderer_lock_violations == [False] * len(renderer_lock_violations)
    assert player.position_frame == 192
    assert player.studio_buffered_frames <= player.studio_buffer_capacity_frames
    assert player.studio_buffered_bytes <= player.studio_buffer_capacity_bytes

    readers = tuple(player._studio_stream._readers.values())
    producer_thread = pipeline._thread
    release_producer.set()
    player.stop()
    assert not producer_thread.is_alive()
    assert pipeline.is_closed
    assert all(opened.reader.closed for opened in readers)
    assert player._studio_stream is None
    assert player.studio_buffered_frames == 0


def test_callback_notification_mailbox_is_bounded_and_preserves_terminal(
    tmp_path: Path,
) -> None:
    frame_count = 385
    segment, _source = _segment(
        tmp_path,
        40,
        np.full(frame_count, 0.25, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    callback_guard = threading.Lock()
    level_callbacks: list[dict[int, float]] = []
    finished: list[bool] = []

    def on_levels(value: dict[int, float]) -> None:
        with callback_guard:
            level_callbacks.append(dict(value))

    def on_finished() -> None:
        with callback_guard:
            finished.append(True)

    player = TakePlayer(
        samplerate=8_000,
        blocksize=1,
        sink=sink,
        on_levels=on_levels,
        on_finished=on_finished,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    assert sink.pull is not None
    assert player.studio_notification_capacity < frame_count / 3

    rendered: list[np.ndarray] = []
    callback_durations: list[float] = []

    def saturate_mailbox() -> None:
        deadline = time.monotonic() + 1.0
        while len(rendered) < frame_count and time.monotonic() < deadline:
            started = time.monotonic()
            block = sink.pull(1)
            callback_durations.append(time.monotonic() - started)
            if float(np.max(np.abs(block))) > 0.0:
                rendered.append(block)

    callback_guard.acquire()
    callback = threading.Thread(
        target=saturate_mailbox,
        name="test-portaudio-notification-saturation",
    )
    callback.start()
    callback.join(0.75)
    blocked_on_ui = callback.is_alive()
    callback_guard.release()
    callback.join(1.0)

    assert not blocked_on_ui
    assert not callback.is_alive()
    assert max(callback_durations) < 0.1
    assert level_callbacks == []
    assert finished == []
    assert player.is_playing is True
    assert player.position_frame == frame_count
    assert player.studio_pending_notifications == (
        player.studio_notification_capacity + 1
    )
    assert player.studio_buffered_bytes <= player.studio_buffer_capacity_bytes
    np.testing.assert_allclose(np.concatenate(rendered), 0.25, atol=1e-7)

    assert player.drain_studio_notifications() == (
        player.studio_notification_capacity + 1
    )
    assert len(level_callbacks) == player.studio_notification_capacity
    assert finished == [True]
    assert player.is_playing is False
    assert player.studio_pending_notifications == 0
    player.stop()


def test_terminal_mailbox_preserves_current_events_and_rejects_late_old_epoch(
    tmp_path: Path,
) -> None:
    segment, _source = _segment(
        tmp_path,
        41,
        np.full(2_000, 0.2, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    finished: list[bool] = []
    errors: list[PlaybackError] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=32,
        sink=sink,
        on_finished=lambda: finished.append(True),
        on_error=errors.append,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    old_epoch = player.playback_epoch
    pipeline = player._studio_pipeline
    assert pipeline is not None
    old_generation = pipeline.generation

    player.pause()
    player.play()
    assert player.playback_epoch > old_epoch
    assert pipeline.generation > old_generation

    player._finish_studio_realtime(old_epoch, pipeline, old_generation)
    assert player.drain_studio_notifications() == 0
    assert player.is_playing is True
    assert finished == []

    stale_error = StudioPlaybackSourceError("stale producer failure")
    player._latch_terminal_error_realtime(
        stale_error,
        old_epoch,
        pipeline,
        old_generation,
    )
    assert player.drain_studio_notifications() == 0
    assert player.is_playing is True
    assert player.terminal_error is None
    assert errors == []

    current_epoch = player.playback_epoch
    current_generation = pipeline.generation
    player._finish_studio_realtime(
        current_epoch,
        pipeline,
        current_generation,
    )
    terminal_capacity = player._studio_terminal_notifications.maxlen
    assert terminal_capacity is not None
    for _index in range(terminal_capacity * 2):
        player._finish_studio_realtime(old_epoch, pipeline, old_generation)
    assert len(player._studio_terminal_notifications) == 1
    assert player.drain_studio_notifications() == 1
    assert finished == [True]
    assert player.is_playing is False

    player.play()
    error_epoch = player.playback_epoch
    error_generation = pipeline.generation
    current_error = StudioPlaybackSourceError("current producer failure")
    player._latch_terminal_error_realtime(
        current_error,
        error_epoch,
        pipeline,
        error_generation,
    )
    for _index in range(terminal_capacity * 2):
        player._finish_studio_realtime(old_epoch, pipeline, old_generation)
    assert len(player._studio_terminal_notifications) == 1
    assert player.drain_studio_notifications() == 1
    assert errors == [current_error]
    assert player.terminal_error is current_error
    assert player.is_playing is False
    assert player.drain_terminal_error() is current_error


def test_configured_8192_frame_callback_is_fully_primed(tmp_path: Path) -> None:
    callback_frames = 8_192
    segment, _source = _segment(
        tmp_path,
        42,
        np.full(callback_frames + 2_000, 0.25, dtype=np.float32),
        rate=48_000,
    )
    project = _project((_track(10, (segment,)),), rate=48_000)
    sink = _DeferredStudioSink()
    player = TakePlayer(
        samplerate=48_000,
        blocksize=callback_frames,
        sink=sink,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    assert sink.pull is not None
    assert player.studio_buffer_capacity_frames >= callback_frames
    assert player.studio_buffer_prime_frames >= callback_frames
    assert player.studio_buffered_frames >= callback_frames

    rendered = sink.pull(callback_frames)
    np.testing.assert_allclose(rendered, 0.25, atol=1e-7)
    assert player.position_frame == callback_frames
    player.stop()


def test_stalled_producer_seek_times_out_fail_closed_without_late_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, _source = _segment(
        tmp_path,
        43,
        np.full(2_000, 0.3, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    positions: list[float] = []
    levels: list[dict[int, float]] = []
    errors: list[PlaybackError] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=32,
        sink=sink,
        on_position=positions.append,
        on_levels=lambda value: levels.append(dict(value)),
        on_error=errors.append,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    preparation = player.prepare_studio_playback()
    assert player.install_studio_preparation(preparation)

    producer_entered = threading.Event()
    release_producer = threading.Event()
    original_read = studio_renderer.StudioRenderStream.read_with_tracks

    def stalled_read(stream, frame_count):
        producer_entered.set()
        assert release_producer.wait(2.0)
        return original_read(stream, frame_count)

    monkeypatch.setattr(
        studio_renderer.StudioRenderStream,
        "read_with_tracks",
        stalled_read,
    )
    player.play()
    assert sink.pull is not None
    assert producer_entered.wait(1.0)
    np.testing.assert_allclose(sink.pull(8), 0.3, atol=1e-7)
    player.drain_studio_notifications()
    positions.clear()
    levels.clear()
    pipeline = player._studio_pipeline
    assert pipeline is not None
    readers = tuple(player._studio_stream._readers.values())

    try:
        started = time.monotonic()
        with pytest.raises(PlaybackError, match="did not respond") as raised:
            player.seek_frame(100)
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert errors == [raised.value]
        assert player.terminal_error is raised.value
        assert player.is_playing is False
        assert player.position_frame == 8
        np.testing.assert_array_equal(
            sink.pull(8),
            np.zeros((8, 2), dtype=np.float32),
        )
        assert player.drain_studio_notifications() == 0
        assert positions == []
        assert levels == []
        assert player._studio_stream is not None
        assert pipeline._thread.is_alive()
        assert all(not opened.reader.closed for opened in readers)
    finally:
        release_producer.set()

    pipeline._thread.join(1.0)
    assert pipeline.is_closed
    assert all(opened.reader.closed for opened in readers)
    assert player.drain_terminal_error() is errors[0]
    assert player._studio_stream is None
    np.testing.assert_array_equal(
        sink.pull(8),
        np.zeros((8, 2), dtype=np.float32),
    )


def test_mix_and_seek_flush_prefetch_at_last_consumed_frame_with_safe_underrun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = np.arange(1, 2_001, dtype=np.float32) / np.float32(4_000.0)
    segment, _source = _segment(tmp_path, 31, samples, rate=8_000)
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    positions: list[float] = []
    levels: list[dict[int, float]] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=16,
        sink=sink,
        on_position=positions.append,
        on_levels=lambda value: levels.append(dict(value)),
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    assert sink.pull is not None

    first = sink.pull(3)
    np.testing.assert_allclose(first[:, 0], samples[:3], atol=1e-7)
    assert player.position_frame == 3
    pipeline = player._studio_pipeline
    assert pipeline is not None
    assert player._studio_stream.position_frame > player.position_frame
    old_generation = pipeline.generation

    # Even direct queue-lock contention cannot make the device callback wait.
    contended: list[np.ndarray] = []
    levels.clear()
    positions.clear()
    pipeline._condition.acquire()
    try:
        callback = threading.Thread(
            target=lambda: contended.append(sink.pull(4)),
            name="test-contended-portaudio-callback",
        )
        callback.start()
        callback.join(0.2)
        assert not callback.is_alive()
    finally:
        pipeline._condition.release()
    np.testing.assert_array_equal(contended[0], np.zeros((4, 2), np.float32))
    assert player.position_frame == 3
    assert levels == []
    assert positions == []

    original_read = studio_renderer.StudioRenderStream.read_with_tracks
    slow_enabled = threading.Event()
    producer_waiting = threading.Event()
    release_producer = threading.Event()
    blocked_once = False

    def slow_next_generation(stream, frame_count):
        nonlocal blocked_once
        if slow_enabled.is_set() and not blocked_once:
            blocked_once = True
            producer_waiting.set()
            assert release_producer.wait(1.0)
        return original_read(stream, frame_count)

    monkeypatch.setattr(
        studio_renderer.StudioRenderStream,
        "read_with_tracks",
        slow_next_generation,
    )
    slow_enabled.set()
    levels.clear()
    positions.clear()
    player.set_gain(0, 0.5)
    assert pipeline.generation > old_generation
    assert producer_waiting.wait(1.0)

    # The stale prefetched cursor is never consumed. An empty/contended queue
    # emits silence without advancing transport or inventing meter callbacks.
    np.testing.assert_array_equal(sink.pull(4), np.zeros((4, 2), np.float32))
    assert player.position_frame == 3
    assert levels == []
    assert positions == []

    release_producer.set()
    _wait_for_studio_buffer(player, 4)
    remixed = sink.pull(4)
    np.testing.assert_allclose(remixed[:, 0], samples[3:7] * 0.5, atol=1e-7)
    assert player.position_frame == 7
    player.drain_studio_notifications()
    assert levels and positions

    # Explicit seek performs descriptor identity work on the producer, discards
    # any old mix generation, and restarts at the exact requested sample. The
    # full checksum receipt comes from asynchronous preparation.
    player.seek_frame(100)
    _wait_for_studio_buffer(player, 3)
    sought = sink.pull(3)
    np.testing.assert_allclose(sought[:, 0], samples[100:103] * 0.5, atol=1e-7)
    assert player.position_frame == 103
    player.stop()


def test_old_device_callback_cannot_consume_after_studio_reload(tmp_path: Path) -> None:
    first_segment, _first_source = _segment(
        tmp_path,
        32,
        np.full(1_000, 0.2, dtype=np.float32),
        rate=8_000,
    )
    second_segment, _second_source = _segment(
        tmp_path,
        33,
        np.full(1_000, 0.7, dtype=np.float32),
        rate=8_000,
    )
    first_project = _project(
        (_track(10, (first_segment,)),),
        rate=8_000,
        take_id=_id(40),
    )
    second_project = _project(
        (_track(11, (second_segment,)),),
        rate=8_000,
        take_id=_id(41),
    )
    sink = _DeferredStudioSink()
    player = TakePlayer(samplerate=8_000, blocksize=32, sink=sink)

    player.load_studio(
        first_project,
        default_studio_document(first_project),
        tmp_path,
    )
    player.play()
    old_pull = sink.pull
    assert old_pull is not None
    np.testing.assert_allclose(old_pull(4), 0.2, atol=1e-7)
    old_epoch = player.playback_epoch

    player.load_studio(
        second_project,
        default_studio_document(second_project),
        tmp_path,
    )
    player.play()
    new_pull = sink.pull
    assert new_pull is not None
    assert player.playback_epoch > old_epoch
    np.testing.assert_array_equal(old_pull(4), np.zeros((4, 2), np.float32))
    assert player.position_frame == 0
    np.testing.assert_allclose(new_pull(4), 0.7, atol=1e-7)
    assert player.position_frame == 4
    player.stop()


def test_producer_error_is_ordered_after_prefetch_and_latched_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, _source = _segment(
        tmp_path,
        34,
        np.full(2_000, 0.35, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    errors: list[object] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=32,
        sink=sink,
        on_error=errors.append,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    preparation = player.prepare_studio_playback()
    primed_frames = preparation.pipeline.buffered_frames
    assert player.install_studio_preparation(preparation)

    def fail_future_read(_stream, _frame_count):
        raise StudioRenderError("injected producer read failure")

    monkeypatch.setattr(
        studio_renderer.StudioRenderStream,
        "read_with_tracks",
        fail_future_read,
    )
    player.play()
    assert sink.pull is not None

    audible_frames = 0
    deadline = time.monotonic() + 1.0
    while player.terminal_error is None and time.monotonic() < deadline:
        block = sink.pull(32)
        player.drain_studio_notifications()
        if float(np.max(np.abs(block))) > 0.0:
            audible_frames += 32
        time.sleep(0.001)
    assert audible_frames == primed_frames
    assert len(errors) == 1
    assert isinstance(errors[0], StudioPlaybackSourceError)
    assert player.terminal_error is errors[0]
    assert player.is_playing is False
    np.testing.assert_array_equal(sink.pull(32), np.zeros((32, 2), np.float32))
    assert player.drain_terminal_error() is errors[0]
    assert player._studio_stream is None


def test_seek_reuses_prepared_checksum_and_rejects_same_inode_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, source = _segment(
        tmp_path,
        35,
        np.full(2_000, 0.3, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    errors: list[object] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=32,
        sink=sink,
        on_error=errors.append,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    assert sink.pull is not None
    sink.pull(4)

    repeated_hashes: list[int] = []
    original_hash = studio_renderer._sha256_descriptor

    def observed_hash(*args, **kwargs):
        repeated_hashes.append(threading.get_ident())
        return original_hash(*args, **kwargs)

    monkeypatch.setattr(studio_renderer, "_sha256_descriptor", observed_hash)
    player.seek_frame(4)
    assert repeated_hashes == []

    before = source.stat()
    with source.open("r+b", buffering=0) as handle:
        handle.seek(-4, os.SEEK_END)
        original = handle.read(4)
        handle.seek(-4, os.SEEK_END)
        handle.write(bytes(value ^ 0x01 for value in original))
        os.fsync(handle.fileno())
    after = source.stat()
    assert (before.st_dev, before.st_ino, before.st_size) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
    )

    with pytest.raises(StudioPlaybackSourceError, match="changed while seeking"):
        player.seek_frame(4)
    assert len(errors) == 1
    assert player.terminal_error is errors[0]
    assert player.drain_terminal_error() is errors[0]


def test_slow_producer_stop_timeout_is_bounded_and_retains_owned_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment, _source = _segment(
        tmp_path,
        36,
        np.full(2_000, 0.25, dtype=np.float32),
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = _DeferredStudioSink()
    player = TakePlayer(samplerate=8_000, blocksize=32, sink=sink)
    player.load_studio(project, default_studio_document(project), tmp_path)
    preparation = player.prepare_studio_playback()
    pipeline = preparation.pipeline
    assert pipeline is not None
    assert player.install_studio_preparation(preparation)
    stream = player._studio_stream
    readers = tuple(stream._readers.values())

    original_read = studio_renderer.StudioRenderStream.read_with_tracks
    producer_waiting = threading.Event()
    release_producer = threading.Event()

    def held_read(render_stream, frame_count):
        producer_waiting.set()
        assert release_producer.wait(1.0)
        return original_read(render_stream, frame_count)

    monkeypatch.setattr(
        studio_renderer.StudioRenderStream,
        "read_with_tracks",
        held_read,
    )
    player.play()
    assert producer_waiting.wait(1.0)

    started = time.monotonic()
    assert pipeline.stop(timeout=0.01) is False
    assert time.monotonic() - started < 0.2
    assert pipeline.is_closed is False
    assert player._studio_stream is stream
    assert any(not opened.reader.closed for opened in readers)

    release_producer.set()
    assert pipeline.stop(timeout=1.0) is True
    player.stop()
    assert pipeline.is_closed
    assert all(opened.reader.closed for opened in readers)
    assert player._studio_stream is None


def test_take_player_reports_studio_media_failure_separately_from_device_failure(
    tmp_path: Path,
) -> None:
    class RejectUnexpectedStart:
        started = False

        def start(self, _rate, _blocksize, _pull) -> None:
            self.started = True

        def stop(self) -> None:
            pass

    segment, source = _segment(
        tmp_path, 20, np.full(8, 0.4, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project)
    sink = RejectUnexpectedStart()
    player = TakePlayer(samplerate=8_000, sink=sink)
    player.load_studio(project, document, tmp_path)
    source.unlink()

    with pytest.raises(
        StudioPlaybackSourceError,
        match="Studio arrangement source media could not be opened",
    ) as error:
        player.play()

    assert "playback device" not in str(error.value)
    assert sink.started is False
    assert player.is_playing is False

    class RemoveSourceOnStart:
        stopped = False

        def __init__(self, path: Path) -> None:
            self.path = path

        def start(self, _rate, _blocksize, pull) -> None:
            self.path.unlink()
            pull(1)

        def stop(self) -> None:
            self.stopped = True

    lazy_segment, lazy_source = _segment(
        tmp_path, 21, np.full(8, 0.4, dtype=np.float32), rate=8_000
    )
    lazy_project = _project((_track(10, (lazy_segment,)),), rate=8_000)
    lazy_sink = RemoveSourceOnStart(lazy_source)
    lazy_player = TakePlayer(samplerate=8_000, sink=lazy_sink)
    lazy_player.load_studio(
        lazy_project,
        default_studio_document(lazy_project),
        tmp_path,
    )

    # Preparation binds the validated descriptor before the output callback.
    # Removing its published name inside start() therefore cannot redirect the
    # callback to different bytes; the next UI-thread seek checkpoint reports it.
    lazy_player.play()
    with pytest.raises(StudioPlaybackSourceError, match="changed while seeking"):
        lazy_player.seek_frame(1)
    assert lazy_player.is_playing is False
    lazy_player.drain_terminal_error()
    assert lazy_sink.stopped is True


def test_take_player_latches_midstream_source_read_failure_without_callback_escape(
    tmp_path: Path,
) -> None:
    class PullSink:
        pull = None
        stopped = False

        def start(self, _rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            self.stopped = True

    segment, source = _segment(
        tmp_path, 20, np.full(2_400, 0.4, dtype=np.float32), rate=8_000
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    sink = PullSink()
    errors: list[object] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=16,
        sink=sink,
        on_error=errors.append,
    )
    player.load_studio(project, default_studio_document(project), tmp_path)
    player.play()
    sink.stopped = False
    assert sink.pull is not None
    assert float(np.max(np.abs(sink.pull(2)))) > 0.1

    del source
    opened = next(iter(player._studio_stream._readers.values()))
    opened.reader.close()

    # Already-prefetched memory remains valid. Once it is consumed, the sole
    # producer observes the reader failure and orders one terminal event; no
    # exception escapes the realtime callback.
    deadline = time.monotonic() + 1.0
    while player.terminal_error is None and time.monotonic() < deadline:
        sink.pull(16)
        player.drain_studio_notifications()
        time.sleep(0.001)
    np.testing.assert_array_equal(sink.pull(2), np.zeros((2, 2), np.float32))
    assert len(errors) == 1
    assert isinstance(errors[0], StudioPlaybackSourceError)
    assert player.terminal_error is errors[0]
    assert player.is_playing is False
    assert player._studio_stream is not None
    assert sink.stopped is False

    assert player.drain_terminal_error() is errors[0]
    assert player.terminal_error is None
    assert player._studio_stream is None
    assert player.position_frame == 0
    assert sink.stopped is True


def test_take_player_seek_translates_source_replacement_and_uses_same_latch(
    tmp_path: Path,
) -> None:
    class PullSink:
        pull = None
        stopped = False

        def start(self, _rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            self.stopped = True

    segment, source = _segment(
        tmp_path, 20, np.full(64, 0.4, dtype=np.float32), rate=48_000
    )
    project = _project((_track(10, (segment,)),), rate=48_000)
    sink = PullSink()
    errors: list[object] = []
    player = TakePlayer(samplerate=48_000, sink=sink, on_error=errors.append)
    player.load_studio(project, default_studio_document(project), tmp_path)

    player.seek_frame(27)
    assert player.position_frame == 27
    assert player.position_s == pytest.approx(27 / 48_000)
    player.play()
    sink.stopped = False
    assert sink.pull is not None
    assert float(np.max(np.abs(sink.pull(1)))) > 0.1

    replacement = tmp_path / "replacement.wav"
    parked = tmp_path / "original.wav"
    sf.write(
        replacement,
        np.full(64, 0.7, dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )
    os.replace(source, parked)
    os.replace(replacement, source)

    with pytest.raises(
        StudioPlaybackSourceError,
        match="source media changed while seeking",
    ) as error:
        player.seek_frame(27)

    assert errors == [error.value]
    assert player.terminal_error is error.value
    assert player.is_playing is False
    assert sink.stopped is False
    assert player.drain_terminal_error() is error.value
    assert player._studio_stream is None
    assert player.position_frame == 0
    assert sink.stopped is True


def test_take_player_cycles_on_exact_frames_across_device_block_boundaries(
    tmp_path: Path,
) -> None:
    segment, _source = _segment(
        tmp_path,
        20,
        np.arange(1, 9, dtype=np.float32) / 10.0,
        rate=8_000,
    )
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project).set_cycle_range(StudioCycleRange(2, 5))
    positions: list[float] = []
    finished: list[bool] = []
    sink = _DeferredStudioSink()
    player = TakePlayer(
        samplerate=8_000,
        blocksize=8,
        sink=sink,
        on_position=positions.append,
        on_finished=lambda: finished.append(True),
    )
    player.load_studio(project, document, tmp_path)
    player.play()
    assert sink.pull is not None

    first = sink.pull(8)
    np.testing.assert_allclose(
        first[:, 0],
        np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.3, 0.4, 0.5]),
        atol=1e-6,
    )
    np.testing.assert_array_equal(first[:, 0], first[:, 1])
    assert player.position_frame == 2

    _wait_for_studio_buffer(player, 7)
    second = sink.pull(7)
    player.drain_studio_notifications()
    np.testing.assert_allclose(
        second[:, 0],
        np.array([0.3, 0.4, 0.5, 0.3, 0.4, 0.5, 0.3]),
        atol=1e-6,
    )
    assert player.position_frame == 3
    assert positions[-1] == pytest.approx(3 / 8_000)
    assert finished == []
    assert player.is_playing is True
    player.stop()


def test_cycle_positions_and_meters_follow_only_each_consumed_callback(
    tmp_path: Path,
) -> None:
    samples = np.arange(1, 9, dtype=np.float32) / np.float32(10.0)
    segment, _source = _segment(tmp_path, 37, samples, rate=8_000)
    project = _project((_track(10, (segment,)),), rate=8_000)
    document = default_studio_document(project).set_cycle_range(StudioCycleRange(2, 5))
    sink = _DeferredStudioSink()
    positions: list[float] = []
    levels: list[dict[int, float]] = []
    stereo: list[dict[int, tuple[float, float, bool]]] = []
    master: list[tuple[float, float, bool]] = []
    player = TakePlayer(
        samplerate=8_000,
        blocksize=8,
        sink=sink,
        on_position=positions.append,
        on_levels=lambda value: levels.append(dict(value)),
        on_stereo_levels=lambda value: stereo.append(dict(value)),
        on_master_level=master.append,
    )
    player.load_studio(project, document, tmp_path)
    player.play()
    assert sink.pull is not None

    blocks = [sink.pull(count) for count in (3, 4, 5)]
    player.drain_studio_notifications()
    expected = (
        np.array([0.1, 0.2, 0.3], dtype=np.float32),
        np.array([0.4, 0.5, 0.3, 0.4], dtype=np.float32),
        np.array([0.5, 0.3, 0.4, 0.5, 0.3], dtype=np.float32),
    )
    for block, values in zip(blocks, expected):
        np.testing.assert_allclose(block[:, 0], values, atol=1e-7)
        np.testing.assert_array_equal(block[:, 0], block[:, 1])
    assert player.position_frame == 3
    assert positions == pytest.approx([3 / 8_000, 4 / 8_000, 3 / 8_000])
    assert len(levels) == len(stereo) == len(master) == 3
    for values, level, stereo_level, master_level in zip(
        expected,
        levels,
        stereo,
        master,
    ):
        expected_rms = min(1.0, float(np.sqrt(np.mean(np.square(values)))) * 3.0)
        expected_peak = float(np.max(np.abs(values)))
        assert level[0] == pytest.approx(expected_rms)
        assert stereo_level[0] == pytest.approx((expected_peak, expected_peak, False))
        assert master_level == pytest.approx((expected_peak, expected_peak, False))
    player.stop()


@pytest.mark.parametrize(
    "samples",
    (
        np.array([0.65], dtype=np.float32),
        np.array([0.75, -0.5], dtype=np.float32),
        np.array([0.8, -0.6, 0.35], dtype=np.float32),
    ),
)
def test_one_to_three_frame_cycles_remain_sample_exact_and_non_silent(
    tmp_path: Path,
    samples: np.ndarray,
) -> None:
    segment, _source = _segment(tmp_path, 20, samples, rate=48_000)
    project = _project((_track(10, (segment,)),), rate=48_000)
    document = default_studio_document(project).set_cycle_range(
        StudioCycleRange(0, len(samples))
    )
    frame_count = len(samples) * 4 + 1
    sink = _DeferredStudioSink()
    player = TakePlayer(
        samplerate=48_000,
        blocksize=frame_count,
        sink=sink,
    )
    player.load_studio(project, document, tmp_path)
    player.play()
    assert sink.pull is not None

    rendered = sink.pull(frame_count)[:, 0]

    np.testing.assert_allclose(
        rendered,
        np.resize(samples, frame_count),
        atol=1e-7,
    )
    assert float(np.max(np.abs(rendered[len(samples) :]))) > 0.0
    assert player.position_frame == frame_count % len(samples)
    player.stop()


def test_cycle_smoothing_spans_multiple_wraps_without_fading_initial_entry(
    tmp_path: Path,
) -> None:
    samples = np.array(
        [0.2, 0.3, 1.0, 0.8, 0.6, 0.4, 0.2, 0.1, -0.1, -0.3, -0.5, -0.7, -0.9, -1.0],
        dtype=np.float32,
    )
    segment, _source = _segment(tmp_path, 20, samples, rate=1_000)
    project = _project((_track(10, (segment,)),), rate=1_000)
    document = default_studio_document(project).set_cycle_range(StudioCycleRange(2, 14))
    sink = _DeferredStudioSink()
    player = TakePlayer(samplerate=1_000, blocksize=40, sink=sink)
    player.load_studio(project, document, tmp_path)
    player.play()
    assert sink.pull is not None

    rendered = sink.pull(40)[:, 0]

    assert len(rendered) == 40
    assert rendered[2] == pytest.approx(samples[2])  # initial entry is untouched
    assert rendered[13] == pytest.approx(0.0)  # first fade-out endpoint
    assert rendered[14] == pytest.approx(0.0)  # first wrapped fade-in endpoint
    assert rendered[19] == pytest.approx(samples[7])  # interior remains sample-exact
    assert rendered[25] == pytest.approx(0.0)
    assert rendered[26] == pytest.approx(0.0)
    assert player.position_frame == 4
    assert player.is_playing is True
    player.stop()


def test_cycle_shorter_than_fade_window_has_exact_count_and_stable_seams(
    tmp_path: Path,
) -> None:
    samples = np.array([0.2, 1.0, 0.8, -0.8, -1.0, 0.1], dtype=np.float32)
    segment, _source = _segment(tmp_path, 20, samples, rate=48_000)
    project = _project((_track(10, (segment,)),), rate=48_000)
    document = default_studio_document(project).set_cycle_range(StudioCycleRange(1, 5))
    sink = _DeferredStudioSink()
    player = TakePlayer(samplerate=48_000, blocksize=13, sink=sink)
    player.load_studio(project, document, tmp_path)
    player.play()
    assert sink.pull is not None

    rendered = sink.pull(13)[:, 0]

    expected = np.array(
        [0.2, 1.0, 0.8, -0.4, 0.0, 0.0, 0.4, -0.4, 0.0, 0.0, 0.4, -0.4, 0.0],
        dtype=np.float32,
    )
    np.testing.assert_allclose(rendered, expected, atol=1e-6)
    assert player.position_frame == 1
    assert player.is_playing is True
    player.stop()

"""Bounded and cancellation-safe waveform cache coverage for Studio."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.studio_waveform as studio_waveform
from core.studio_waveform import (
    StudioWaveformCancelled,
    StudioWaveformError,
    WaveformGap,
    WaveformSource,
    WaveformTileCache,
)
from core.studio_source_catalog import StudioSourceCatalog
from core.take_project import (
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


def _write_float_wav(path: Path, samples: np.ndarray, *, rate: int = 8_000) -> None:
    sf.write(path, np.asarray(samples, dtype=np.float32), rate, subtype="FLOAT")


def _source(
    path: Path,
    *,
    source_id: str = "source-1",
    gaps: tuple[WaveformGap, ...] = (),
) -> WaveformSource:
    info = sf.info(path)
    return WaveformSource(
        source_id=source_id,
        path=path,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        size_bytes=path.stat().st_size,
        gaps=gaps,
    )


def _write_sparse_pcm16_wav(
    path: Path,
    *,
    sample_rate: int,
    frame_count: int,
    channels: int = 1,
) -> None:
    """Write a valid zero-filled WAV whose data extent can remain sparse."""

    bytes_per_sample = 2
    block_align = channels * bytes_per_sample
    data_size = frame_count * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bytes_per_sample * 8,
        b"data",
        data_size,
    )
    with path.open("wb") as handle:
        handle.write(header)
        handle.seek(data_size - 1, os.SEEK_CUR)
        handle.write(b"\0")


def test_tile_reduces_bounded_chunks_and_declared_gaps_to_silence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stereo.wav"
    samples = np.array(
        [
            [0.1, -0.1],
            [0.2, -0.2],
            [0.3, -0.3],
            [0.4, -0.4],
        ],
        dtype=np.float32,
    )
    _write_float_wav(path, samples)
    source = _source(path, gaps=(WaveformGap(1, 2, (1,)),))
    cache = WaveformTileCache(read_chunk_frames=1, tile_peaks=4)
    token = cache.begin_generation()

    tile = cache.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )

    np.testing.assert_allclose(
        tile.minimum,
        np.array([[0.1, -0.1], [0.3, -0.4]], dtype=np.float32),
    )
    np.testing.assert_allclose(
        tile.maximum,
        np.array([[0.2, 0.0], [0.4, 0.0]], dtype=np.float32),
    )
    assert tile.minimum.shape == (2, 2)
    assert tile.maximum.shape == (2, 2)
    assert tile.key.source == source.identity
    assert str(path) not in repr(tile.key)
    assert cache.stats.max_read_frames == 1
    assert cache.stats.read_calls == 4
    with pytest.raises(ValueError):
        tile.minimum[0, 0] = 9.0
    with pytest.raises(ValueError):
        tile.minimum.setflags(write=True)

    same = cache.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )
    assert same is tile
    assert cache.stats.hits == 1
    assert cache.stats.source_opens == 1


def test_all_channel_gap_is_silence_and_segment_factory_preserves_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.wav"
    _write_float_wav(path, np.full(6, 0.75, dtype=np.float32))
    segment = MediaSegment(
        segment_id="00000000-0000-4000-8000-000000000001",
        path=path.name,
        project_start_frame=0,
        frame_count=6,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        gaps=(GapInterval(2, 2, "declared dropout"),),
        size_bytes=path.stat().st_size,
    )
    source = WaveformSource.from_media_segment(tmp_path, segment)
    cache = WaveformTileCache(read_chunk_frames=3)
    tile = cache.request_tile(
        source,
        start_frame=0,
        frame_count=6,
        frames_per_peak=2,
        token=cache.begin_generation(),
    )

    np.testing.assert_allclose(tile.minimum[:, 0], [0.75, 0.0, 0.75])
    np.testing.assert_allclose(tile.maximum[:, 0], [0.75, 0.0, 0.75])
    assert tile.key.source.source_id == segment.segment_id
    assert tile.key.source.gap_signature != "0" * 64


def test_gap_inventory_is_bounded_and_late_tile_cursor_skips_ended_gaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "gap-index.wav"
    _write_float_wav(path, np.ones(512, dtype=np.float32))
    monkeypatch.setattr(studio_waveform, "MAX_WAVEFORM_GAPS", 2)
    with pytest.raises(StudioWaveformError, match="at most 2 intervals"):
        _source(
            path,
            gaps=(
                WaveformGap(0, 1),
                WaveformGap(1, 1),
                WaveformGap(2, 1),
            ),
        )

    monkeypatch.setattr(studio_waveform, "MAX_WAVEFORM_GAPS", 512)
    spanning = WaveformGap(50, 400)
    source = _source(
        path,
        gaps=tuple(WaveformGap(index, 1) for index in range(200)) + (spanning,),
    )
    cache = WaveformTileCache(read_chunk_frames=2)
    token = cache.begin_generation()
    next_index, active = cache._initial_gap_cursor(source, 300, token)
    assert next_index == len(source.gaps)
    assert active == [spanning]

    block = np.ones((2, 1), dtype=np.float32)
    next_index, active = cache._apply_gaps(
        source,
        300,
        block,
        next_gap_index=next_index,
        active_gaps=active,
        token=token,
    )
    np.testing.assert_array_equal(block, np.zeros((2, 1), dtype=np.float32))
    assert next_index == len(source.gaps)
    assert active == [spanning]


def test_gap_overlap_walk_checks_cancellation_inside_large_active_set(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gap-cancel.wav"
    _write_float_wav(path, np.ones(512, dtype=np.float32))
    source = _source(path)

    class CancellingToken:
        def __init__(self) -> None:
            self.calls = 0

        def raise_if_cancelled(self) -> None:
            self.calls += 1
            if self.calls == 2:
                raise StudioWaveformCancelled("cancelled in gap walk")

    token = CancellingToken()
    active = [WaveformGap(0, 512)] * 300
    with pytest.raises(StudioWaveformCancelled, match="gap walk"):
        WaveformTileCache._apply_gaps(
            source,
            1,
            np.ones((2, 1), dtype=np.float32),
            next_gap_index=0,
            active_gaps=active,
            token=token,  # type: ignore[arg-type]
        )
    assert token.calls == 2


def test_catalog_factory_uses_full_durable_key_and_declared_checksum(
    tmp_path: Path,
) -> None:
    root = tmp_path / "catalog-take"
    root.mkdir()
    path = root / "source.wav"
    _write_float_wav(path, np.linspace(-0.4, 0.4, 8, dtype=np.float32))
    segment = MediaSegment(
        segment_id=_id(20),
        path=path.name,
        project_start_frame=0,
        frame_count=8,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
    )
    track = ProjectTrack(
        track_id=_id(10),
        source_id=_id(11),
        participant_id=None,
        name="Catalog track",
        instrument="",
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.VERIFIED_ISOLATED,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Waveform catalog",
        take_name="Take 1",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=8_000,
        participants=(),
        tracks=(track,),
    )
    (root / "webjam-take.json").write_text(
        json.dumps(project.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    catalog = StudioSourceCatalog.load(project, root)

    source = WaveformSource.from_catalog_source(
        catalog,
        project.take_id,
        track.track_id,
        segment.segment_id,
    )
    cache = WaveformTileCache()
    tile = cache.request_tile(
        source,
        start_frame=0,
        frame_count=8,
        frames_per_peak=2,
        token=cache.begin_generation(),
    )

    assert tile.key.source.catalog_key == (
        project.take_id,
        track.track_id,
        segment.segment_id,
    )
    assert source.trusted_root == root.resolve()


def test_fixed_grid_viewport_tiles_include_only_intersecting_source_ranges(
    tmp_path: Path,
) -> None:
    path = tmp_path / "grid.wav"
    _write_float_wav(path, np.linspace(-1.0, 1.0, 30, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(tile_peaks=4, read_chunk_frames=5)
    token = cache.begin_generation()

    tiles = cache.request_viewport(
        source,
        start_frame=7,
        frame_count=10,
        frames_per_peak=2,
        token=token,
    )

    assert [item.key.start_frame for item in tiles] == [0, 8, 16]
    assert [item.key.frame_count for item in tiles] == [8, 8, 8]
    assert all(item.peak_count == 4 for item in tiles)
    assert (
        cache.request_viewport(
            source,
            start_frame=source.frame_count,
            frame_count=10,
            frames_per_peak=2,
            token=token,
        )
        == ()
    )


def test_lru_enforces_entry_and_payload_byte_bounds(tmp_path: Path) -> None:
    path = tmp_path / "lru.wav"
    _write_float_wav(path, np.linspace(-0.8, 0.8, 16, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(
        max_entries=2,
        max_bytes=64,
        read_chunk_frames=2,
    )
    token = cache.begin_generation()

    for start in (0, 4, 8):
        tile = cache.request_tile(
            source,
            start_frame=start,
            frame_count=4,
            frames_per_peak=1,
            token=token,
        )
        assert tile.byte_size == 32
        assert cache.stats.entries <= 2
        assert cache.stats.bytes <= 64

    stats = cache.stats
    assert stats.entries == 2
    assert stats.bytes == 64
    assert stats.evictions == 1
    assert stats.misses == 3

    cache.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=1,
        token=token,
    )
    assert cache.stats.misses == 4
    assert cache.stats.evictions == 2

    tiny = WaveformTileCache(max_entries=2, max_bytes=8)
    oversized = tiny.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=1,
        token=tiny.begin_generation(),
    )
    assert oversized.byte_size == 32
    assert tiny.stats.entries == 0
    assert tiny.stats.bytes == 0


def test_cache_hit_rechecks_physical_identity_and_reloads_replaced_media(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replace.wav"
    _write_float_wav(path, np.full(4, 0.25, dtype=np.float32))
    original = _source(path)
    cache = WaveformTileCache(read_chunk_frames=2)
    token = cache.begin_generation()
    first = cache.request_tile(
        original,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )

    replacement = tmp_path / "replacement.wav"
    _write_float_wav(replacement, np.full(4, -0.5, dtype=np.float32))
    assert replacement.stat().st_size == path.stat().st_size
    os.replace(replacement, path)
    current = _source(path)
    assert current.identity == original.identity

    second = cache.request_tile(
        current,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )

    assert second is not first
    np.testing.assert_allclose(second.minimum[:, 0], [-0.5, -0.5])
    assert cache.stats.misses == 2
    assert cache.stats.source_opens == 2


def test_declared_checksum_is_verified_once_per_exact_physical_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "checksummed.wav"
    _write_float_wav(path, np.linspace(-0.5, 0.5, 8, dtype=np.float32))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    info = sf.info(path)
    source = WaveformSource(
        source_id="checksummed-source",
        path=path,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        size_bytes=path.stat().st_size,
        sha256=digest,
    )
    calls = 0
    original_hash = studio_waveform._sha256_descriptor

    def counted_hash(descriptor: int, token) -> str:
        nonlocal calls
        calls += 1
        return original_hash(descriptor, token)

    monkeypatch.setattr(studio_waveform, "_sha256_descriptor", counted_hash)
    cache = WaveformTileCache(tile_peaks=2)
    token = cache.begin_generation()
    cache.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )
    cache.request_tile(
        source,
        start_frame=4,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )
    assert calls == 1

    replacement = tmp_path / "wrong.wav"
    _write_float_wav(replacement, np.full(8, -0.75, dtype=np.float32))
    assert replacement.stat().st_size == path.stat().st_size
    os.replace(replacement, path)
    with pytest.raises(StudioWaveformError, match="checksum changed"):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=4,
            frames_per_peak=2,
            token=token,
        )
    assert calls == 2


def test_cache_hit_descriptor_check_rejects_swap_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hit-race.wav"
    replacement = tmp_path / "hit-race-replacement.wav"
    _write_float_wav(path, np.full(4, 0.25, dtype=np.float32))
    _write_float_wav(replacement, np.full(4, -0.5, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(read_chunk_frames=2)
    token = cache.begin_generation()
    cache.request_tile(
        source,
        start_frame=0,
        frame_count=4,
        frames_per_peak=2,
        token=token,
    )
    original_open = studio_waveform._open_regular_source
    swapped = False

    def open_then_swap(current_source: WaveformSource):
        nonlocal swapped
        descriptor, info = original_open(current_source)
        if not swapped:
            swapped = True
            os.replace(replacement, path)
        return descriptor, info

    monkeypatch.setattr(studio_waveform, "_open_regular_source", open_then_swap)
    with pytest.raises(StudioWaveformError, match="changed"):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=4,
            frames_per_peak=2,
            token=token,
        )


def test_trusted_source_rejects_replaced_ancestor_symlink(tmp_path: Path) -> None:
    root = tmp_path / "take"
    media = root / "media"
    outside = tmp_path / "outside"
    media.mkdir(parents=True)
    outside.mkdir()
    source_path = media / "source.wav"
    outside_path = outside / "source.wav"
    _write_float_wav(source_path, np.full(4, 0.25, dtype=np.float32))
    _write_float_wav(outside_path, np.full(4, -0.5, dtype=np.float32))
    segment = MediaSegment(
        segment_id=_id(30),
        path="media/source.wav",
        project_start_frame=0,
        frame_count=4,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        size_bytes=source_path.stat().st_size,
    )
    source = WaveformSource.from_media_segment(root, segment)
    media.rename(root / "original-media")
    media.symlink_to(outside, target_is_directory=True)
    cache = WaveformTileCache()

    with pytest.raises(StudioWaveformError, match="ancestors.*symbolic link"):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=4,
            frames_per_peak=1,
            token=cache.begin_generation(),
        )


def test_final_component_symlink_is_rejected_even_before_a_cache_read(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.wav"
    link = tmp_path / "linked.wav"
    _write_float_wav(target, np.zeros(4, dtype=np.float32))
    link.symlink_to(target.name)
    source = WaveformSource(
        source_id="linked-source",
        path=link,
        frame_count=4,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        size_bytes=target.stat().st_size,
    )
    cache = WaveformTileCache()

    with pytest.raises(StudioWaveformError, match="symbolic link"):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=4,
            frames_per_peak=1,
            token=cache.begin_generation(),
        )
    assert cache.stats.entries == 0


def test_generation_rollover_cancels_mid_read_before_stale_cache_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cancel.wav"
    _write_float_wav(path, np.linspace(-1.0, 1.0, 12, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(read_chunk_frames=2)
    stale = cache.begin_generation()
    original_record_read = cache._record_read
    rolled = False

    def roll_generation(frames: int) -> None:
        nonlocal rolled
        original_record_read(frames)
        if not rolled:
            rolled = True
            cache.begin_generation()

    monkeypatch.setattr(cache, "_record_read", roll_generation)
    with pytest.raises(StudioWaveformCancelled, match="stale generation"):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=12,
            frames_per_peak=2,
            token=stale,
        )

    assert stale.cancelled is True
    assert cache.stats.read_calls == 1
    assert cache.stats.entries == 0
    reads_after_cancel = cache.stats.read_calls
    with pytest.raises(StudioWaveformCancelled):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=12,
            frames_per_peak=2,
            token=stale,
        )
    assert cache.stats.read_calls == reads_after_cancel


def test_direct_cancellation_during_insert_rolls_back_stale_tile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cancel-insert.wav"
    _write_float_wav(path, np.linspace(-1.0, 1.0, 8, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(read_chunk_frames=2)
    token = cache.begin_generation()
    original_remove = cache._remove_entry
    cancelled = False

    def cancel_then_remove(key) -> None:
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            token.cancel()
        original_remove(key)

    monkeypatch.setattr(cache, "_remove_entry", cancel_then_remove)
    with pytest.raises(StudioWaveformCancelled):
        cache.request_tile(
            source,
            start_frame=0,
            frame_count=8,
            frames_per_peak=2,
            token=token,
        )
    assert token.cancelled is True
    assert cache.stats.entries == 0
    assert cache.stats.bytes == 0


def test_viewport_planning_rejects_unbounded_tile_work_before_io(
    tmp_path: Path,
) -> None:
    path = tmp_path / "viewport-bound.wav"
    _write_float_wav(path, np.zeros(20, dtype=np.float32))
    source = _source(path)
    cache = WaveformTileCache(tile_peaks=2, max_viewport_tiles=3)
    token = cache.begin_generation()

    with pytest.raises(StudioWaveformError, match="tile-count limit"):
        cache.plan_viewport(
            source,
            start_frame=0,
            frame_count=20,
            frames_per_peak=1,
            token=token,
        )
    assert cache.stats.source_opens == 0
    assert cache.stats.read_calls == 0

    keys = cache.plan_viewport(
        source,
        start_frame=2,
        frame_count=6,
        frames_per_peak=1,
        token=token,
    )
    assert [(item.start_frame, item.frame_count) for item in keys] == [
        (2, 2),
        (4, 2),
        (6, 2),
    ]


def test_sparse_twelve_track_sixty_minute_viewports_stay_bounded(
    tmp_path: Path,
) -> None:
    """A long-session shape exercises bounds without allocating full sources."""

    sample_rate = 100
    minutes = 60
    source_frames = sample_rate * minutes * 60
    sources: list[WaveformSource] = []
    for index in range(12):
        path = tmp_path / f"track-{index:02d}.wav"
        _write_sparse_pcm16_wav(
            path,
            sample_rate=sample_rate,
            frame_count=source_frames,
        )
        sources.append(
            WaveformSource(
                source_id=f"immutable-track-{index:02d}",
                path=path,
                frame_count=source_frames,
                sample_rate=sample_rate,
                channels=1,
                sample_format="PCM_16",
                size_bytes=path.stat().st_size,
            )
        )

    # Each 64-peak mono tile owns exactly 512 payload bytes.  The configured
    # LRU can therefore retain at most 20 regardless of session duration.
    cache = WaveformTileCache(
        max_entries=20,
        max_bytes=20 * 64 * 2 * np.dtype(np.float32).itemsize,
        read_chunk_frames=37,
        tile_peaks=64,
    )
    token = cache.begin_generation()
    viewport_start = 30 * sample_rate
    viewport_frames = 12 * sample_rate
    for source in sources:
        tiles = cache.request_viewport(
            source,
            start_frame=viewport_start,
            frame_count=viewport_frames,
            frames_per_peak=10,
            token=token,
        )
        assert len(tiles) == 3
        assert all(np.count_nonzero(item.minimum) == 0 for item in tiles)
        assert all(np.count_nonzero(item.maximum) == 0 for item in tiles)

    stats = cache.stats
    assert stats.entries == 20
    assert stats.bytes == stats.max_bytes
    assert stats.evictions == 16
    assert stats.max_read_frames <= 37
    assert stats.total_frames_read == 12 * 3 * 640
    assert stats.total_frames_read < (12 * source_frames) // 100
    assert stats.source_opens == 36

    stale = token
    current = cache.begin_generation()
    reads_before_stale_request = cache.stats.read_calls
    with pytest.raises(StudioWaveformCancelled):
        cache.request_viewport(
            sources[0],
            start_frame=0,
            frame_count=source_frames,
            frames_per_peak=10,
            token=stale,
        )
    assert cache.stats.read_calls == reads_before_stale_request
    assert cache.is_current(current)

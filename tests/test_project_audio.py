"""Safety and real-time contracts for reusable project-audio primitives."""

from __future__ import annotations

import builtins
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import threading
import tracemalloc

import numpy as np
import pytest
import soundfile as sf

import core.project_audio as project_audio
from core.project_audio import (
    CaptureBlockRing,
    GenerationGate,
    PlaybackBlockRing,
    ProjectAudioCancelled,
    ProjectAudioDecoder,
    ProjectAudioError,
    RealtimeBlockPool,
)


_FORMAT_CASES = (
    (".wav", "WAV", "FLOAT"),
    (".aif", "AIFF", "PCM_16"),
    (".aiff", "AIFF", "PCM_16"),
    (".flac", "FLAC", "PCM_16"),
    (".ogg", "OGG", "VORBIS"),
    (".mp3", "MP3", "MPEG_LAYER_III"),
)


def _write_audio(
    path: Path,
    samples: np.ndarray | None = None,
    *,
    samplerate: int = 48_000,
    channels: int = 1,
    format_name: str = "WAV",
    subtype: str = "FLOAT",
) -> Path:
    if samples is None:
        timeline = np.arange(256, dtype=np.float32) / samplerate
        mono = (0.25 * np.sin(2.0 * np.pi * 440.0 * timeline)).astype(
            np.float32
        )
        samples = (
            mono
            if channels == 1
            else np.column_stack([mono] * channels).astype(np.float32)
        )
    sf.write(
        path,
        samples,
        samplerate,
        format=format_name,
        subtype=subtype,
    )
    return path


def _assert_path_free(error: BaseException, source: Path) -> None:
    message = str(error)
    assert str(source) not in message
    assert source.name not in message
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("suffix", "format_name", "subtype"),
    _FORMAT_CASES,
)
def test_decoder_accepts_validated_formats_and_paths_with_spaces(
    tmp_path: Path,
    suffix: str,
    format_name: str,
    subtype: str,
) -> None:
    if format_name == "MP3" and not sf.check_format("MP3"):
        pytest.skip("the locked libsndfile build has no MP3 capability")
    folder = tmp_path / "folder with spaces"
    folder.mkdir()
    source = _write_audio(
        folder / f"reference track{suffix}",
        format_name=format_name,
        subtype=subtype,
    )

    with ProjectAudioDecoder(source) as decoder:
        output = np.empty((128, 2), dtype=np.float32)
        identity = id(output)
        decoded = decoder.read_into(0, output)

        assert decoder.probe.container == format_name
        assert decoder.probe.channels == 1
        assert decoder.probe.source_sample_rate == 48_000
        assert decoded == 128
        assert id(output) == identity
        np.testing.assert_allclose(output[:, 0], output[:, 1])
        assert str(tmp_path) not in repr(decoder)


def test_mp3_support_is_capability_tested_not_inferred_from_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_audio(
        tmp_path / "song.mp3",
        format_name="MP3",
        subtype="MPEG_LAYER_III",
    )
    formats = dict(sf.available_formats())
    formats.pop("MP3", None)
    monkeypatch.setattr(sf, "available_formats", lambda: formats)

    with pytest.raises(ProjectAudioError, match="MP3 decoding is unavailable") as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_decoder_rejects_wrong_container_corrupt_and_multichannel_files(
    tmp_path: Path,
) -> None:
    wrong_container = _write_audio(
        tmp_path / "flac-disguised-as-wave.wav",
        format_name="FLAC",
        subtype="PCM_16",
    )
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not a supported audio container")
    surround = _write_audio(
        tmp_path / "surround.wav",
        channels=3,
    )

    for source in (wrong_container, corrupt, surround):
        with pytest.raises(ProjectAudioError) as caught:
            ProjectAudioDecoder(source)
        _assert_path_free(caught.value, source)


def test_decoder_rejects_symlink_and_open_time_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _write_audio(tmp_path / "target.wav")
    symlink = tmp_path / "linked.wav"
    try:
        symlink.symlink_to(target)
    except OSError:
        pytest.skip("this platform cannot create a test symlink")

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(symlink)
    _assert_path_free(caught.value, symlink)

    source = _write_audio(tmp_path / "raced.wav")
    replacement = _write_audio(
        tmp_path / "replacement.wav",
        np.full(256, 0.5, dtype=np.float32),
    )
    real_open = os.open
    raced = False

    def replace_before_open(path: os.PathLike[str], flags: int) -> int:
        nonlocal raced
        if not raced:
            raced = True
            os.replace(replacement, source)
        return real_open(path, flags)

    monkeypatch.setattr(project_audio.os, "open", replace_before_open)
    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_descriptor_binding_rejects_replacement_after_probe(
    tmp_path: Path,
) -> None:
    source = _write_audio(tmp_path / "source.wav")
    replacement = _write_audio(
        tmp_path / "new-source.wav",
        np.full(256, 0.75, dtype=np.float32),
    )
    decoder = ProjectAudioDecoder(source)
    try:
        try:
            os.replace(replacement, source)
        except PermissionError:
            pytest.skip(
                "the platform prevents replacement while the descriptor is open"
            )
        output = np.empty((32, 2), dtype=np.float32)
        with pytest.raises(ProjectAudioError, match="changed or became unavailable") as caught:
            decoder.read_into(0, output)
        _assert_path_free(caught.value, source)
        assert np.count_nonzero(output) == 0
    finally:
        decoder.close()


@pytest.mark.parametrize(
    ("limit_name", "limit", "samplerate", "frames"),
    (
        ("PROJECT_AUDIO_MAX_SOURCE_RATE", 24_000, 48_000, 4),
        ("PROJECT_AUDIO_MAX_SOURCE_FRAMES", 3, 48_000, 4),
        ("PROJECT_AUDIO_MAX_OUTPUT_FRAMES", 3, 24_000, 4),
    ),
)
def test_decoder_enforces_rate_frame_and_output_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
    samplerate: int,
    frames: int,
) -> None:
    source = _write_audio(
        tmp_path / "bounded.wav",
        np.linspace(-0.5, 0.5, frames, dtype=np.float32),
        samplerate=samplerate,
    )
    monkeypatch.setattr(project_audio, limit_name, limit)

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_resampling_is_deterministic_and_holds_the_final_source_sample(
    tmp_path: Path,
) -> None:
    source_samples = np.array([0.0, 1.0, 0.5], dtype=np.float32)
    source = _write_audio(
        tmp_path / "24k.wav",
        source_samples,
        samplerate=24_000,
    )
    expected = np.array([0.0, 0.5, 1.0, 0.75, 0.5, 0.5], dtype=np.float32)

    with ProjectAudioDecoder(source) as decoder:
        whole = np.empty((6, 2), dtype=np.float32)
        first = np.empty((3, 2), dtype=np.float32)
        second = np.empty((3, 2), dtype=np.float32)
        assert decoder.read_into(0, whole) == 6
        assert decoder.read_into(0, first) == 3
        assert decoder.read_into(3, second) == 3

    np.testing.assert_array_equal(whole[:, 0], expected)
    np.testing.assert_array_equal(whole[:, 1], expected)
    np.testing.assert_array_equal(np.vstack((first, second)), whole)


def test_decoder_zeroes_unused_tail_and_rejects_unbounded_output(
    tmp_path: Path,
) -> None:
    source = _write_audio(
        tmp_path / "short.wav",
        np.array([0.25, 0.5, 0.75], dtype=np.float32),
        samplerate=24_000,
    )
    with ProjectAudioDecoder(source) as decoder:
        output = np.full((4, 2), 9.0, dtype=np.float32)
        assert decoder.read_into(5, output) == 1
        np.testing.assert_array_equal(output[1:], 0.0)

        too_large = np.empty(
            (project_audio.PROJECT_AUDIO_MAX_DECODE_FRAMES + 1, 2),
            dtype=np.float32,
        )
        with pytest.raises(ValueError, match="bounded decoder"):
            decoder.read_into(0, too_large)

        non_contiguous = np.empty((4, 4), dtype=np.float32)[:, ::2]
        with pytest.raises(ValueError, match="C-contiguous"):
            decoder.read_into(0, non_contiguous)


def test_decoder_rejects_nonfinite_samples_without_leaking_source(
    tmp_path: Path,
) -> None:
    source = _write_audio(
        tmp_path / "nonfinite.wav",
        np.array([0.0, np.nan, 0.5], dtype=np.float32),
    )
    decoder = ProjectAudioDecoder(source)
    try:
        output = np.full((3, 2), 1.0, dtype=np.float32)
        with pytest.raises(ProjectAudioError, match="invalid sample") as caught:
            decoder.read_into(0, output)
        _assert_path_free(caught.value, source)
        assert np.count_nonzero(output) == 0
    finally:
        decoder.close()


def test_generation_gate_cancels_stale_decoder_work_and_zeroes_output(
    tmp_path: Path,
) -> None:
    source = _write_audio(tmp_path / "generation.wav")
    gate = GenerationGate()
    stale = gate.issue()
    current = gate.issue()
    output = np.full((32, 2), 1.0, dtype=np.float32)

    with ProjectAudioDecoder(source) as decoder:
        with pytest.raises(ProjectAudioCancelled):
            decoder.read_into(0, output, token=stale)
        assert np.count_nonzero(output) == 0
        assert decoder.read_into(0, output, token=current) == 32
        gate.cancel()
        with pytest.raises(ProjectAudioCancelled):
            decoder.read_into(0, output, token=current)
        assert np.count_nonzero(output) == 0


def test_decoder_serializes_concurrent_workers_using_shared_scratch(
    tmp_path: Path,
) -> None:
    samples = np.linspace(-0.9, 0.9, 20_000, dtype=np.float32)
    source = _write_audio(
        tmp_path / "concurrent.wav",
        samples,
        samplerate=44_100,
    )
    starts = (0, 71, 509, 1_301, 2_777, 5_123, 8_991, 14_000)

    with ProjectAudioDecoder(source) as decoder:
        expected: dict[int, np.ndarray] = {}
        for start in starts:
            output = np.empty((512, 2), dtype=np.float32)
            assert decoder.read_into(start, output) == 512
            expected[start] = output

        def decode(start: int) -> tuple[int, np.ndarray]:
            output = np.empty((512, 2), dtype=np.float32)
            assert decoder.read_into(start, output) == 512
            return start, output

        repeated = starts * 16
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = tuple(workers.map(decode, repeated))

    for start, output in results:
        np.testing.assert_array_equal(output, expected[start])


def test_block_pool_has_fixed_storage_and_stable_slot_objects() -> None:
    pool = RealtimeBlockPool(capacity=4, block_frames=16, channels=2)
    expected_bytes = 4 * 16 * 2 * np.dtype(np.float32).itemsize
    identities = pool.buffer_identities

    assert pool.nbytes == expected_bytes
    for _ in range(10_000):
        for index, identity in enumerate(identities):
            assert id(pool.buffer(index)) == identity
    assert pool.buffer_identities == identities


def test_playback_ring_exact_delivery_underrun_overflow_and_stale_counts() -> None:
    ring = PlaybackBlockRing(capacity=1, block_frames=8)
    output = np.full((4, 2), 9.0, dtype=np.float32)

    assert ring.pull_into(output, generation=2) == 0
    np.testing.assert_array_equal(output, 0.0)
    assert ring.requested_frames == 4
    assert ring.underrun_frames == 4

    stale = np.full((8, 2), 0.25, dtype=np.float32)
    assert ring.try_push_from(stale, start_frame=20, generation=1)
    assert not ring.try_push_from(stale, start_frame=28, generation=1)
    assert ring.overflow_blocks == 1
    assert ring.overflow_frames == 8
    assert ring.pull_into(output, generation=2) == 0
    np.testing.assert_array_equal(output, 0.0)
    assert ring.stale_frames == 8
    assert ring.underrun_frames == 8

    current = np.arange(16, dtype=np.float32).reshape(8, 2)
    assert ring.try_push_from(current, start_frame=100, generation=2)
    assert ring.pull_into(output, generation=2) == 4
    np.testing.assert_array_equal(output, current[:4])
    assert ring.position_frame == 104
    assert ring.pull_into(output, generation=2) == 4
    np.testing.assert_array_equal(output, current[4:])
    assert ring.position_frame == 108
    assert ring.delivered_frames == 8
    assert ring.queued_blocks == 0


def test_capture_ring_channel_map_exact_gaps_and_bounded_ledger() -> None:
    ring = CaptureBlockRing(
        capacity=1,
        block_frames=4,
        input_channels=4,
        channel_map=(2, 0),
        gap_capacity=1,
    )
    source = np.arange(16, dtype=np.float32).reshape(4, 4)
    output = np.empty((4, 2), dtype=np.float32)

    assert ring.push_from(source, start_frame=0, generation=5)
    assert not ring.push_from(source, start_frame=4, generation=5)
    assert not ring.push_from(source, start_frame=8, generation=5)
    assert ring.overflow_blocks == 2
    assert ring.overflow_frames == 8
    assert ring.gaps()[0].start_frame == 4
    assert ring.gaps()[0].frame_count == 8
    assert ring.gaps()[0].end_frame == 12
    assert ring.gaps()[0].channels == (0, 1)

    assert ring.pop_into(output, generation=5) == 4
    np.testing.assert_array_equal(output[:, 0], source[:, 2])
    np.testing.assert_array_equal(output[:, 1], source[:, 0])
    assert ring.last_popped_start_frame == 0
    assert ring.pushed_frames == 4
    assert ring.popped_frames == 4

    assert ring.push_from(source, start_frame=12, generation=6)
    assert not ring.push_from(source, start_frame=16, generation=6)
    assert ring.gap_ledger_overflowed is True
    assert ring.unreported_gap_frames == 4


def test_capture_ring_drops_stale_generation_exactly() -> None:
    ring = CaptureBlockRing(
        capacity=2,
        block_frames=4,
        input_channels=2,
        channel_map=(0, 1),
    )
    source = np.ones((4, 2), dtype=np.float32)
    output = np.empty((4, 2), dtype=np.float32)

    assert ring.push_from(source, start_frame=40, generation=1)
    assert ring.pop_into(output, generation=2) == 0
    assert ring.stale_frames == 4
    assert ring.popped_frames == 0
    assert ring.queued_blocks == 0


def test_ring_storage_identity_and_memory_remain_bounded_for_10k_cycles() -> None:
    playback = PlaybackBlockRing(capacity=4, block_frames=8)
    capture = CaptureBlockRing(
        capacity=4,
        block_frames=8,
        input_channels=2,
        channel_map=(0, 1),
    )
    playback_ids = playback.pool.buffer_identities
    capture_ids = capture.pool.buffer_identities
    playback_source = np.ones((8, 2), dtype=np.float32)
    callback_output = np.empty((8, 2), dtype=np.float32)
    capture_output = np.empty((8, 2), dtype=np.float32)

    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    try:
        for index in range(10_000):
            assert playback.try_push_from(
                playback_source,
                start_frame=index * 8,
                generation=7,
            )
            assert playback.pull_into(callback_output, generation=7) == 8
            assert capture.push_from(
                callback_output,
                start_frame=index * 8,
                generation=7,
            )
            assert capture.pop_into(capture_output, generation=7) == 8
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert playback.pool.buffer_identities == playback_ids
    assert capture.pool.buffer_identities == capture_ids
    assert current - baseline < 128 * 1_024
    assert peak - baseline < 1_024 * 1_024
    assert playback.requested_frames == 80_000
    assert playback.underrun_frames == 0
    assert capture.pushed_frames == 80_000
    assert capture.popped_frames == 80_000


def test_ring_hot_paths_do_not_create_buffers_perform_io_log_or_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playback = PlaybackBlockRing(capacity=2, block_frames=8)
    capture = CaptureBlockRing(
        capacity=2,
        block_frames=8,
        input_channels=2,
        channel_map=(0, 1),
    )
    source = np.ones((8, 2), dtype=np.float32)
    playback_output = np.empty((8, 2), dtype=np.float32)
    capture_output = np.empty((8, 2), dtype=np.float32)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden operation reached an audio hot path")

    for name in (
        "array",
        "asarray",
        "concatenate",
        "empty",
        "ones",
        "stack",
        "zeros",
    ):
        monkeypatch.setattr(project_audio.np, name, forbidden)
    monkeypatch.setattr(project_audio.os, "open", forbidden)
    monkeypatch.setattr(project_audio.os, "read", forbidden)
    monkeypatch.setattr(project_audio.os, "write", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(logging.Logger, "_log", forbidden)
    monkeypatch.setattr(threading.Event, "wait", forbidden)
    monkeypatch.setattr(threading.Condition, "wait", forbidden)

    assert playback.try_push_from(source, start_frame=0, generation=1)
    assert playback.pull_into(playback_output, generation=1) == 8
    assert capture.push_from(source, start_frame=0, generation=1)
    assert capture.pop_into(capture_output, generation=1) == 8
    for frame in range(8):
        for channel in range(2):
            assert playback_output[frame, channel] == source[frame, channel]
            assert capture_output[frame, channel] == source[frame, channel]

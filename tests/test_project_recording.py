"""Deterministic contracts for standalone multitrack project recording."""

from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError
import logging
from pathlib import Path
import threading

import numpy as np
import pytest
import soundfile as sf

import core.project_recording as project_recording
from core.project_recording import (
    ArmedProjectTrack,
    ProjectMultitrackRecorder,
    ProjectRecorderState,
    ProjectRecordingError,
    ProjectRecordingIngress,
    ProjectRecordingSchedule,
    SoundDeviceProjectInputBackend,
)


class _InputBackend:
    def __init__(
        self,
        *,
        input_channels: int = 3,
        block_frames: int = 4,
        sample_rate: int = 48_000,
    ) -> None:
        self.sample_rate = sample_rate
        self.input_channels = input_channels
        self.block_frames = block_frames
        self.callbacks: list = []
        self.starts = 0
        self.stops = 0
        self.aborts = 0
        self.fail_start = False
        self.fail_stop = False
        self.live_jamulus_settings = {
            "server": "unchanged.example:22124",
            "channels": [7, 9],
        }

    def start(self, callback) -> None:
        self.starts += 1
        self.callbacks.append(callback)
        if self.fail_start:
            raise RuntimeError("backend start failed at /private/source")

    def stop(self) -> None:
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("backend stop failed at /private/source")

    def abort(self) -> None:
        self.aborts += 1

    def emit(
        self,
        samples: np.ndarray,
        *,
        callback_index: int = -1,
    ) -> None:
        self.callbacks[callback_index](samples)


class _SoundDeviceStatus:
    def __init__(self, *, overflow: bool = False) -> None:
        self.input_overflow = overflow

    def __bool__(self) -> bool:
        return True


class _SoundDeviceStream:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.starts = 0
        self.stops = 0
        self.aborts = 0
        self.closes = 0
        self.fail_start = False
        self.fail_stop = False
        self.fail_abort = False
        self.fail_close = False

    def start(self) -> None:
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("secret device start path")

    def stop(self) -> None:
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("secret device stop path")

    def abort(self) -> None:
        self.aborts += 1
        if self.fail_abort:
            raise RuntimeError("secret device abort path")

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            raise RuntimeError("secret device close path")

    def emit(
        self,
        samples: np.ndarray,
        *,
        frames: int | None = None,
        status=None,
    ) -> None:
        self.kwargs["callback"](
            samples,
            len(samples) if frames is None else frames,
            object(),
            status,
        )


class _SoundDeviceModule:
    def __init__(self) -> None:
        self.streams: list[_SoundDeviceStream] = []
        self.fail_next_start = False

    def InputStream(self, **kwargs):
        stream = _SoundDeviceStream(kwargs)
        stream.fail_start = self.fail_next_start
        self.streams.append(stream)
        return stream


def _read(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    assert rate == 48_000
    return samples


def _ramp(frames: int, channels: int = 3) -> np.ndarray:
    return np.arange(frames * channels, dtype=np.float32).reshape(
        frames,
        channels,
    )


def test_armed_tracks_and_schedule_are_immutable_and_exact() -> None:
    track = ArmedProjectTrack(
        "lead_guitar",
        [2, 0],  # type: ignore[arg-type]
        latency_compensation_frames=-137,
    )
    schedule = ProjectRecordingSchedule(
        punch_in_frame=120,
        punch_out_frame=160,
        count_in_frames=20,
        pre_roll_frames=20,
        cycle_start_frame=100,
        cycle_end_frame=180,
        cycle_count=3,
    )

    assert track.channel_map == (2, 0)
    assert track.channels == 2
    assert schedule.lead_in_frames == 40
    assert schedule.cue_start_frame == 60
    assert schedule.punch_frames == 40
    assert schedule.cycle_frames == 80
    assert schedule.scheduled_input_frames == 280
    assert schedule.scheduled_output_frames == 120
    with pytest.raises(FrozenInstanceError):
        track.track_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        schedule.cycle_count = 4  # type: ignore[misc]

    with pytest.raises(ValueError, match="distinct"):
        ArmedProjectTrack("bad", (1, 1))
    with pytest.raises(ValueError, match="safe"):
        ArmedProjectTrack("../escape", (0,))
    with pytest.raises(ValueError, match="safe"):
        ArmedProjectTrack("CON", (0,))
    with pytest.raises(ValueError, match="inside the cycle"):
        ProjectRecordingSchedule(
            90,
            110,
            cycle_start_frame=100,
            cycle_end_frame=120,
        )
    with pytest.raises(ValueError, match="frame zero"):
        ProjectRecordingSchedule(4, 8, count_in_frames=5)


def test_atomic_multitrack_recording_honors_count_in_preroll_and_mappings(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=3, block_frames=4)
    settings_before = {
        "server": backend.live_jamulus_settings["server"],
        "channels": list(backend.live_jamulus_settings["channels"]),
    }
    recorder = ProjectMultitrackRecorder(backend)
    schedule = ProjectRecordingSchedule(
        punch_in_frame=8,
        punch_out_frame=16,
        count_in_frames=4,
        pre_roll_frames=4,
    )
    tracks = (
        ArmedProjectTrack(
            "vocal",
            (1,),
            latency_compensation_frames=96,
        ),
        ArmedProjectTrack("keys", (2, 0)),
    )
    destination = tmp_path / "take with spaces"
    source = _ramp(16)

    generation = recorder.start(
        destination,
        schedule=schedule,
        tracks=tracks,
    )
    assert generation > 0
    assert not destination.exists()
    assert len(tuple(tmp_path.glob(".webjam-project-recording-*"))) == 1
    for block in np.split(source, 4):
        backend.emit(block)
    result = recorder.stop()

    assert result.state is ProjectRecorderState.COMPLETED
    assert result.published is True
    assert result.output_frames == 8
    assert result.input_frames_seen == 16
    assert result.segments[0].project_start_frame == 8
    assert result.segments[0].frame_count == 8
    assert result.tracks[0].track.latency_compensation_frames == 96
    assert not result.tracks[0].dropouts
    np.testing.assert_array_equal(
        _read(destination / "vocal.wav")[:, 0],
        source[8:, 1],
    )
    np.testing.assert_array_equal(
        _read(destination / "keys.wav"),
        source[8:, (2, 0)],
    )
    assert not tuple(tmp_path.glob(".webjam-project-recording-*"))
    assert not tuple(destination.glob("*.part"))
    assert backend.live_jamulus_settings == settings_before


def test_case_colliding_track_ids_are_rejected_before_file_creation(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=2)
    recorder = ProjectMultitrackRecorder(backend)

    with pytest.raises(ValueError, match="identifiers must be unique"):
        recorder.start(
            tmp_path / "collision",
            schedule=ProjectRecordingSchedule(0, 4),
            tracks=(
                ArmedProjectTrack("Mic", (0,)),
                ArmedProjectTrack("mic", (1,)),
            ),
        )
    assert not tuple(tmp_path.iterdir())


def test_backend_must_be_exact_48k_and_track_mapping_must_exist(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProjectRecordingError, match="exact 48 kHz"):
        ProjectMultitrackRecorder(
            _InputBackend(sample_rate=44_100)
        )

    recorder = ProjectMultitrackRecorder(
        _InputBackend(input_channels=1)
    )
    with pytest.raises(ValueError, match="unavailable input channel"):
        recorder.start(
            tmp_path / "bad-map",
            schedule=ProjectRecordingSchedule(0, 4),
            tracks=(ArmedProjectTrack("mic", (1,)),),
        )
    assert not tuple(tmp_path.iterdir())


def test_mixed_callback_sizes_keep_all_tracks_sample_synchronized(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=3, block_frames=8)
    recorder = ProjectMultitrackRecorder(backend)
    schedule = ProjectRecordingSchedule(
        punch_in_frame=10,
        punch_out_frame=21,
        count_in_frames=2,
        pre_roll_frames=3,
    )
    tracks = (
        ArmedProjectTrack("one", (0,)),
        ArmedProjectTrack("two", (1, 2)),
    )
    source = _ramp(schedule.scheduled_input_frames)
    recorder.start(tmp_path / "sync", schedule=schedule, tracks=tracks)

    cursor = 0
    for amount in (3, 8, 2, 3):
        backend.emit(source[cursor : cursor + amount])
        cursor += amount
    result = recorder.stop()

    assert result.output_frames == 11
    assert {track.frame_count for track in result.tracks} == {11}
    np.testing.assert_array_equal(
        _read(result.tracks[0].file)[:, 0],
        source[5:, 0],
    )
    np.testing.assert_array_equal(
        _read(result.tracks[1].file),
        source[5:, (1, 2)],
    )


def test_cycle_recording_maps_rapid_passes_to_linear_wav_segments(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(backend, ring_capacity=256)
    cycle_count = 64
    schedule = ProjectRecordingSchedule(
        punch_in_frame=11,
        punch_out_frame=13,
        cycle_start_frame=10,
        cycle_end_frame=14,
        cycle_count=cycle_count,
    )
    track = ArmedProjectTrack("loop", (0,))
    source = np.arange(
        schedule.scheduled_input_frames + 4,
        dtype=np.float32,
    ).reshape(-1, 1)

    recorder.start(
        tmp_path / "cycles",
        schedule=schedule,
        tracks=(track,),
    )
    for start in range(0, len(source), 4):
        backend.emit(source[start : start + 4])
    result = recorder.stop()

    expected_indices = tuple(
        frame
        for cycle in range(cycle_count)
        for frame in (cycle * 4 + 1, cycle * 4 + 2)
    )
    np.testing.assert_array_equal(
        _read(result.tracks[0].file)[:, 0],
        source[expected_indices, 0],
    )
    assert result.output_frames == cycle_count * 2
    assert tuple(segment.cycle_index for segment in result.segments) == tuple(
        range(cycle_count)
    )
    assert tuple(segment.output_start_frame for segment in result.segments) == tuple(
        range(0, cycle_count * 2, 2)
    )
    assert all(segment.project_start_frame == 11 for segment in result.segments)


def test_ring_overflow_is_exact_zero_filled_mid_take_with_evidence(
    tmp_path: Path,
) -> None:
    write_started = threading.Event()
    release_first = threading.Event()
    second_written = threading.Event()

    class _BlockingWriter:
        def __init__(self, path: Path, channels: int) -> None:
            self.inner = sf.SoundFile(
                path,
                mode="w",
                samplerate=48_000,
                channels=channels,
                format="WAV",
                subtype="FLOAT",
            )
            self.writes = 0

        def write(self, samples: np.ndarray) -> object:
            self.writes += 1
            if self.writes == 1:
                write_started.set()
                assert release_first.wait(timeout=5)
            result = self.inner.write(samples)
            if self.writes == 2:
                second_written.set()
            return result

        def flush(self) -> object:
            return self.inner.flush()

        def close(self) -> object:
            return self.inner.close()

    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(
        backend,
        ring_capacity=1,
        writer_factory=_BlockingWriter,
    )
    schedule = ProjectRecordingSchedule(0, 16)
    destination = tmp_path / "dropout"
    blocks = tuple(
        np.full((4, 1), value, dtype=np.float32)
        for value in (0.1, 0.2, 0.3, 0.4)
    )
    recorder.start(
        destination,
        schedule=schedule,
        tracks=(ArmedProjectTrack("mic", (0,)),),
    )

    backend.emit(blocks[0])
    assert write_started.wait(timeout=5)
    backend.emit(blocks[1])
    backend.emit(blocks[2])
    release_first.set()
    assert second_written.wait(timeout=5)
    backend.emit(blocks[3])
    result = recorder.stop()

    recorded = _read(destination / "mic.wav")[:, 0]
    np.testing.assert_allclose(recorded[:4], 0.1)
    np.testing.assert_allclose(recorded[4:8], 0.2)
    np.testing.assert_array_equal(recorded[8:12], 0.0)
    np.testing.assert_allclose(recorded[12:], 0.4)
    assert result.tracks[0].overflow_frames == 4
    assert len(result.tracks[0].dropouts) == 1
    dropout = result.tracks[0].dropouts[0]
    assert dropout.output_start_frame == 8
    assert dropout.frame_count == 4
    assert dropout.output_end_frame == 12
    assert dropout.channels == (0,)


def test_cancel_invalidates_old_callback_and_recorder_can_restart(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(backend)
    schedule = ProjectRecordingSchedule(0, 4)
    track = ArmedProjectTrack("mic", (0,))

    first_generation = recorder.start(
        tmp_path / "cancelled",
        schedule=schedule,
        tracks=(track,),
    )
    old_callback = backend.callbacks[-1]
    backend.emit(np.full((4, 1), 0.9, dtype=np.float32))
    cancelled = recorder.cancel()
    assert cancelled.state is ProjectRecorderState.CANCELLED
    assert not (tmp_path / "cancelled").exists()

    second_generation = recorder.start(
        tmp_path / "current",
        schedule=schedule,
        tracks=(track,),
    )
    old_callback(np.full((4, 1), 0.8, dtype=np.float32))
    backend.emit(np.full((4, 1), 0.2, dtype=np.float32))
    completed = recorder.stop()

    assert second_generation > first_generation
    assert backend.aborts == 1
    np.testing.assert_allclose(
        _read(completed.tracks[0].file)[:, 0],
        0.2,
    )


def test_rapid_start_cancel_cycles_leave_no_temp_media(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(backend)
    generations: list[int] = []

    for index in range(25):
        generations.append(
            recorder.start(
                tmp_path / f"cancel-{index}",
                schedule=ProjectRecordingSchedule(0, 4),
                tracks=(ArmedProjectTrack("mic", (0,)),),
            )
        )
        assert recorder.cancel().state is ProjectRecorderState.CANCELLED

    assert generations == sorted(set(generations))
    assert not tuple(tmp_path.glob(".webjam-project-recording-*"))
    assert not tuple(tmp_path.glob("cancel-*"))


def test_writer_failure_recovers_each_complete_temporary_wav(
    tmp_path: Path,
) -> None:
    class _FailSecondWrite:
        def __init__(self, path: Path, channels: int) -> None:
            self.inner = sf.SoundFile(
                path,
                mode="w",
                samplerate=48_000,
                channels=channels,
                format="WAV",
                subtype="FLOAT",
            )
            self.writes = 0

        def write(self, samples: np.ndarray) -> object:
            self.writes += 1
            if self.writes == 2:
                raise OSError(
                    "simulated failure /Users/private/song.wav"
                )
            return self.inner.write(samples)

        def flush(self) -> object:
            return self.inner.flush()

        def close(self) -> object:
            return self.inner.close()

    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(
        backend,
        ring_capacity=4,
        writer_factory=_FailSecondWrite,
    )
    destination = tmp_path / "should-not-publish"
    recorder.start(
        destination,
        schedule=ProjectRecordingSchedule(0, 8),
        tracks=(ArmedProjectTrack("mic", (0,)),),
    )
    backend.emit(np.full((4, 1), 0.25, dtype=np.float32))
    backend.emit(np.full((4, 1), 0.5, dtype=np.float32))
    result = recorder.stop()

    assert result.state is ProjectRecorderState.FAILED
    assert result.published is False
    assert not destination.exists()
    assert result.recovery_dir is not None
    assert result.recovery_dir.is_dir()
    recovered = result.tracks[0]
    assert recovered.recovered is True
    assert recovered.file is not None
    assert recovered.file.name == "mic.recovered-partial.wav"
    np.testing.assert_allclose(_read(recovered.file)[:, 0], 0.25)
    assert recovered.frame_count == 4
    assert str(tmp_path) not in " ".join(result.errors)
    assert "private" not in " ".join(result.errors)


def test_backend_start_and_stop_fail_closed_with_path_free_errors(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=1, block_frames=4)
    backend.fail_start = True
    recorder = ProjectMultitrackRecorder(backend)
    with pytest.raises(ProjectRecordingError) as caught:
        recorder.start(
            tmp_path / "start-failure",
            schedule=ProjectRecordingSchedule(0, 4),
            tracks=(ArmedProjectTrack("mic", (0,)),),
        )
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None
    assert not tuple(tmp_path.glob(".webjam-project-recording-*"))

    backend.fail_start = False
    backend.fail_stop = True
    recorder.start(
        tmp_path / "stop-failure",
        schedule=ProjectRecordingSchedule(0, 4),
        tracks=(ArmedProjectTrack("mic", (0,)),),
    )
    backend.emit(np.ones((4, 1), dtype=np.float32))
    result = recorder.stop()
    assert result.state is ProjectRecorderState.FAILED
    assert result.recovery_dir is not None
    assert backend.aborts >= 1
    assert str(tmp_path) not in " ".join(result.errors)


def test_malformed_callback_fails_without_publishing_false_success(
    tmp_path: Path,
) -> None:
    backend = _InputBackend(input_channels=1, block_frames=4)
    recorder = ProjectMultitrackRecorder(backend)
    destination = tmp_path / "malformed"
    recorder.start(
        destination,
        schedule=ProjectRecordingSchedule(0, 4),
        tracks=(ArmedProjectTrack("mic", (0,)),),
    )
    backend.emit(np.ones((4, 1), dtype=np.float64))
    result = recorder.stop()

    assert result.state is ProjectRecorderState.FAILED
    assert result.published is False
    assert not destination.exists()
    assert result.errors


def test_ingress_hot_path_has_no_buffer_creation_io_logging_or_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress = ProjectRecordingIngress(
        ProjectRecordingSchedule(0, 8),
        (ArmedProjectTrack("mono", (0,)),),
        input_channels=1,
        block_frames=8,
        ring_capacity=2,
        generation=1,
    )
    source = np.ones((8, 1), dtype=np.float32)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden operation reached the audio callback")

    with monkeypatch.context() as hot:
        for name in (
            "array",
            "asarray",
            "concatenate",
            "empty",
            "ones",
            "stack",
            "zeros",
        ):
            hot.setattr(project_recording.np, name, forbidden)
        hot.setattr(project_recording.os, "open", forbidden)
        hot.setattr(project_recording.os, "read", forbidden)
        hot.setattr(project_recording.os, "write", forbidden)
        hot.setattr(Path, "lstat", forbidden)
        hot.setattr(Path, "open", forbidden)
        hot.setattr(builtins, "open", forbidden)
        hot.setattr(logging.Logger, "_log", forbidden)
        hot.setattr(threading.Event, "wait", forbidden)
        hot.setattr(threading.Condition, "wait", forbidden)
        assert ingress.process(source) == 8

    output = np.empty((8, 1), dtype=np.float32)
    assert ingress.rings[0].pop_into(output, generation=1) == 8
    np.testing.assert_array_equal(output, source)


def test_sounddevice_input_backend_opens_exact_fixed_format_and_counts_status() -> None:
    module = _SoundDeviceModule()
    backend = SoundDeviceProjectInputBackend(
        input_channels=2,
        block_frames=128,
        device="Studio Interface",
        latency="low",
        sounddevice_module=module,
    )
    delivered = [None]

    backend.start(lambda samples: delivered.__setitem__(0, samples))
    stream = module.streams[0]
    assert stream.kwargs == {
        "samplerate": 48_000,
        "blocksize": 128,
        "channels": 2,
        "dtype": "float32",
        "device": "Studio Interface",
        "callback": stream.kwargs["callback"],
        "latency": "low",
    }
    assert stream.starts == 1
    assert backend.snapshot.running is True

    samples = np.ones((128, 2), dtype=np.float32)
    stream.emit(samples, status=_SoundDeviceStatus(overflow=True))
    assert delivered[0] is samples
    assert backend.snapshot.callback_calls == 1
    assert backend.snapshot.status_events == 1
    assert backend.snapshot.overflow_events == 1
    assert backend.snapshot.format_events == 0

    backend.stop()
    assert stream.stops == 1
    assert stream.closes == 1
    assert backend.snapshot.running is False
    backend.stop()
    assert stream.stops == 1


@pytest.mark.parametrize(
    ("samples", "frames"),
    [
        (np.ones((63, 2), dtype=np.float32), 63),
        (np.ones((64, 1), dtype=np.float32), 64),
        (np.ones((64, 2), dtype=np.float64), 64),
        (np.ones((128,), dtype=np.float32), 64),
    ],
)
def test_sounddevice_input_backend_rejects_non_exact_callback_format(
    samples: np.ndarray,
    frames: int,
) -> None:
    module = _SoundDeviceModule()
    backend = SoundDeviceProjectInputBackend(
        input_channels=2,
        block_frames=64,
        sounddevice_module=module,
    )
    delivered = [np.ones((1, 1), dtype=np.float32)]
    backend.start(lambda value: delivered.__setitem__(0, value))

    module.streams[0].emit(samples, frames=frames)

    assert delivered[0].dtype == np.float32
    assert delivered[0].shape == (0, 2)
    assert backend.snapshot.format_events == 1
    backend.abort()


def test_sounddevice_input_callback_has_no_allocation_io_logging_or_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _SoundDeviceModule()
    backend = SoundDeviceProjectInputBackend(
        input_channels=1,
        block_frames=8,
        sounddevice_module=module,
    )
    delivered = [None]
    backend.start(lambda value: delivered.__setitem__(0, value))
    samples = np.ones((8, 1), dtype=np.float32)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden operation reached the audio callback")

    with monkeypatch.context() as hot:
        for name in (
            "array",
            "asarray",
            "concatenate",
            "empty",
            "ones",
            "stack",
            "zeros",
        ):
            hot.setattr(project_recording.np, name, forbidden)
        hot.setattr(project_recording.os, "open", forbidden)
        hot.setattr(project_recording.os, "read", forbidden)
        hot.setattr(project_recording.os, "write", forbidden)
        hot.setattr(Path, "lstat", forbidden)
        hot.setattr(Path, "open", forbidden)
        hot.setattr(builtins, "open", forbidden)
        hot.setattr(logging.Logger, "_log", forbidden)
        hot.setattr(threading.Event, "wait", forbidden)
        hot.setattr(threading.Event, "set", forbidden)
        module.streams[0].emit(samples)

    assert delivered[0] is samples
    backend.abort()


def test_sounddevice_input_backend_closes_after_start_stop_and_abort_failures() -> None:
    module = _SoundDeviceModule()
    module.fail_next_start = True
    backend = SoundDeviceProjectInputBackend(
        input_channels=1,
        sounddevice_module=module,
    )
    with pytest.raises(ProjectRecordingError) as caught:
        backend.start(lambda _samples: None)
    assert "secret" not in str(caught.value)
    assert module.streams[0].closes == 1
    assert backend.snapshot.running is False

    module.fail_next_start = False
    backend.start(lambda _samples: None)
    stream = module.streams[-1]
    stream.fail_stop = True
    with pytest.raises(ProjectRecordingError, match="stop"):
        backend.stop()
    assert stream.closes == 1
    assert backend.snapshot.running is False

    backend.start(lambda _samples: None)
    stream = module.streams[-1]
    stream.fail_abort = True
    with pytest.raises(ProjectRecordingError, match="abort"):
        backend.abort()
    assert stream.closes == 1
    assert backend.snapshot.running is False

"""Jamulus-independent, sample-accurate multitrack project recording.

The input backend is injected and owns the physical stream.  This module
never discovers devices or reads/writes Jamulus settings.  Its callback path
only performs integer schedule arithmetic and copies into fixed
``CaptureBlockRing`` storage.  WAV I/O, dropout padding, recovery, and atomic
publication all belong to a dedicated writer thread.
"""

from __future__ import annotations

import operator
import os
import re
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

import numpy as np

from core.project_audio import (
    PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
    PROJECT_AUDIO_SAMPLE_RATE,
    CaptureBlockRing,
    GenerationGate,
    GenerationToken,
)

_TRACK_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_WINDOWS_RESERVED_TRACK_IDS: Final = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_MAX_TRACKS: Final = 32
_MAX_CYCLES: Final = 1_024
_MAX_CAPTURE_RING_BYTES: Final = 256 * 1_024 * 1_024
_CALLBACK_FORMAT_ERROR: Final = 1


class ProjectRecordingError(RuntimeError):
    """A path-free project-recording failure suitable for musician-facing UI."""


class ProjectRecorderState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _integer(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(operator.index(value))
    except TypeError:
        raise ValueError(f"{name} must be an integer") from None
    if result < minimum or (maximum is not None and result > maximum):
        if maximum is None:
            raise ValueError(f"{name} must be at least {minimum}")
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return result


def _signed_integer(
    value: object,
    name: str,
    *,
    magnitude: int,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(operator.index(value))
    except TypeError:
        raise ValueError(f"{name} must be an integer") from None
    if not -magnitude <= result <= magnitude:
        raise ValueError(
            f"{name} must be between {-magnitude} and {magnitude}"
        )
    return result


@dataclass(frozen=True, slots=True)
class ArmedProjectTrack:
    """One immutable mono/stereo input mapping and alignment fact."""

    track_id: str
    channel_map: tuple[int, ...]
    latency_compensation_frames: int = 0

    def __post_init__(self) -> None:
        track_id = str(self.track_id)
        mapping = tuple(self.channel_map)
        if (
            not _TRACK_ID.fullmatch(track_id)
            or track_id.casefold() in _WINDOWS_RESERVED_TRACK_IDS
        ):
            raise ValueError(
                "track_id must be a safe 1-64 character identifier"
            )
        if (
            len(mapping) not in (1, 2)
            or any(
                isinstance(channel, bool)
                or not isinstance(channel, int)
                or channel < 0
                or channel >= 64
                for channel in mapping
            )
            or len(set(mapping)) != len(mapping)
        ):
            raise ValueError(
                "channel_map must contain one or two distinct input channels"
            )
        latency = _signed_integer(
            self.latency_compensation_frames,
            "latency_compensation_frames",
            magnitude=PROJECT_AUDIO_SAMPLE_RATE * 10,
        )
        object.__setattr__(self, "track_id", track_id)
        object.__setattr__(self, "channel_map", mapping)
        object.__setattr__(
            self,
            "latency_compensation_frames",
            latency,
        )

    @property
    def channels(self) -> int:
        return len(self.channel_map)


@dataclass(frozen=True, slots=True)
class ProjectRecordingSchedule:
    """Exact project-frame count-in, pre-roll, punch, and cycle schedule.

    Without cycling, the injected stream begins
    ``count_in_frames + pre_roll_frames`` before ``punch_in_frame`` and ends
    at the punch-out boundary.  With cycling, that lead-in targets
    ``cycle_start_frame``; every cycle then records only the punch interval.
    """

    punch_in_frame: int
    punch_out_frame: int
    count_in_frames: int = 0
    pre_roll_frames: int = 0
    cycle_start_frame: int | None = None
    cycle_end_frame: int | None = None
    cycle_count: int = 1

    def __post_init__(self) -> None:
        punch_in = _integer(
            self.punch_in_frame,
            "punch_in_frame",
            minimum=0,
            maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
        )
        punch_out = _integer(
            self.punch_out_frame,
            "punch_out_frame",
            minimum=1,
            maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
        )
        if punch_out <= punch_in:
            raise ValueError("punch_out_frame must follow punch_in_frame")
        count_in = _integer(
            self.count_in_frames,
            "count_in_frames",
            minimum=0,
            maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
        )
        pre_roll = _integer(
            self.pre_roll_frames,
            "pre_roll_frames",
            minimum=0,
            maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
        )
        cycles = _integer(
            self.cycle_count,
            "cycle_count",
            minimum=1,
            maximum=_MAX_CYCLES,
        )
        start = self.cycle_start_frame
        end = self.cycle_end_frame
        if (start is None) != (end is None):
            raise ValueError(
                "cycle_start_frame and cycle_end_frame must be set together"
            )
        lead = count_in + pre_roll
        if start is None:
            if cycles != 1:
                raise ValueError("cycle_count requires a cycle range")
            if lead > punch_in:
                raise ValueError("count-in and pre-roll begin before frame zero")
        else:
            start = _integer(
                start,
                "cycle_start_frame",
                minimum=0,
                maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
            )
            end = _integer(
                end,
                "cycle_end_frame",
                minimum=1,
                maximum=PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
            )
            if not start <= punch_in < punch_out <= end:
                raise ValueError(
                    "the punch interval must be inside the cycle range"
                )
            if end <= start:
                raise ValueError(
                    "cycle_end_frame must follow cycle_start_frame"
                )
            if lead > start:
                raise ValueError("count-in and pre-roll begin before frame zero")
            output_frames = (punch_out - punch_in) * cycles
            input_frames = lead + (end - start) * cycles
            if (
                output_frames > PROJECT_AUDIO_MAX_OUTPUT_FRAMES
                or input_frames > PROJECT_AUDIO_MAX_OUTPUT_FRAMES
            ):
                raise ValueError("the cycle schedule is too long")

        object.__setattr__(self, "punch_in_frame", punch_in)
        object.__setattr__(self, "punch_out_frame", punch_out)
        object.__setattr__(self, "count_in_frames", count_in)
        object.__setattr__(self, "pre_roll_frames", pre_roll)
        object.__setattr__(self, "cycle_start_frame", start)
        object.__setattr__(self, "cycle_end_frame", end)
        object.__setattr__(self, "cycle_count", cycles)

    @property
    def lead_in_frames(self) -> int:
        return self.count_in_frames + self.pre_roll_frames

    @property
    def cue_start_frame(self) -> int:
        target = (
            self.punch_in_frame
            if self.cycle_start_frame is None
            else self.cycle_start_frame
        )
        return target - self.lead_in_frames

    @property
    def punch_frames(self) -> int:
        return self.punch_out_frame - self.punch_in_frame

    @property
    def cycle_frames(self) -> int:
        if self.cycle_start_frame is None or self.cycle_end_frame is None:
            return self.punch_frames
        return self.cycle_end_frame - self.cycle_start_frame

    @property
    def scheduled_output_frames(self) -> int:
        return self.punch_frames * self.cycle_count

    @property
    def scheduled_input_frames(self) -> int:
        return self.lead_in_frames + self.cycle_frames * self.cycle_count


@dataclass(frozen=True, slots=True)
class ProjectRecordingSegment:
    """A linear WAV interval mapped back to one project/cycle interval."""

    output_start_frame: int
    project_start_frame: int
    frame_count: int
    cycle_index: int

    @property
    def output_end_frame(self) -> int:
        return self.output_start_frame + self.frame_count


@dataclass(frozen=True, slots=True)
class ProjectRecordingDropout:
    """An exact zero-filled interval in one published track."""

    track_id: str
    output_start_frame: int
    frame_count: int
    channels: tuple[int, ...]
    reason: str = "capture_ring_overflow"

    @property
    def output_end_frame(self) -> int:
        return self.output_start_frame + self.frame_count


@dataclass(frozen=True, slots=True)
class ProjectTrackRecording:
    track: ArmedProjectTrack
    file: Path | None
    frame_count: int
    dropouts: tuple[ProjectRecordingDropout, ...] = ()
    overflow_frames: int = 0
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProjectRecordingResult:
    state: ProjectRecorderState
    generation: int
    schedule: ProjectRecordingSchedule
    input_frames_seen: int
    output_frames: int
    segments: tuple[ProjectRecordingSegment, ...]
    tracks: tuple[ProjectTrackRecording, ...]
    output_dir: Path | None = None
    recovery_dir: Path | None = None
    errors: tuple[str, ...] = ()

    @property
    def published(self) -> bool:
        return (
            self.state is ProjectRecorderState.COMPLETED
            and self.output_dir is not None
        )


@dataclass(frozen=True, slots=True)
class SoundDeviceInputSnapshot:
    """Control-plane diagnostics for the physical Studio input stream."""

    running: bool
    callback_calls: int
    status_events: int
    overflow_events: int
    format_events: int


class ProjectInputBackend(Protocol):
    """Injected 48-kHz float32 stream; implementations own all device policy."""

    sample_rate: int
    input_channels: int
    block_frames: int

    def start(self, callback: Callable[[np.ndarray], None]) -> None: ...

    def stop(self) -> None: ...

    def abort(self) -> None: ...


class ProjectWaveWriter(Protocol):
    def write(self, samples: np.ndarray) -> object: ...

    def flush(self) -> object: ...

    def close(self) -> object: ...


ProjectWaveWriterFactory = Callable[[Path, int], ProjectWaveWriter]


def _default_wave_writer(path: Path, channels: int) -> ProjectWaveWriter:
    try:
        import soundfile as sf  # type: ignore
    except Exception:
        raise ProjectRecordingError(
            "Project WAV recording is unavailable in this build."
        ) from None
    try:
        return sf.SoundFile(
            path,
            mode="w",
            samplerate=PROJECT_AUDIO_SAMPLE_RATE,
            channels=channels,
            format="WAV",
            subtype="FLOAT",
        )
    except Exception:
        raise ProjectRecordingError(
            "WebJam couldn't create the project recording files."
        ) from None


class ProjectRecordingIngress:
    """Fixed-storage, lock-free callback side of one recording generation."""

    def __init__(
        self,
        schedule: ProjectRecordingSchedule,
        tracks: tuple[ArmedProjectTrack, ...],
        *,
        input_channels: int,
        block_frames: int,
        ring_capacity: int,
        generation: int,
    ) -> None:
        self.schedule = schedule
        self.tracks = tracks
        self.input_channels = _integer(
            input_channels,
            "input_channels",
            minimum=1,
            maximum=64,
        )
        self.block_frames = _integer(
            block_frames,
            "block_frames",
            minimum=1,
            maximum=65_536,
        )
        self.generation = _integer(
            generation,
            "generation",
            minimum=1,
        )
        if (
            schedule.cycle_start_frame is not None
            and schedule.cycle_frames < self.block_frames
        ):
            raise ValueError(
                "cycle length must be at least one input callback block"
            )
        for track in tracks:
            if max(track.channel_map) >= self.input_channels:
                raise ValueError(
                    "an armed track references an unavailable input channel"
                )
        self.rings = tuple(
            CaptureBlockRing(
                ring_capacity,
                self.block_frames,
                input_channels=self.input_channels,
                channel_map=track.channel_map,
                # The writer infers an unbounded exact gap list from absolute
                # block positions. The ring ledger is diagnostic only here.
                gap_capacity=1,
            )
            for track in tracks
        )
        self.input_frames_seen = 0
        self.scheduled_frames = 0
        self.callback_error_code = 0
        self.closed = False

    @property
    def complete(self) -> bool:
        return self.input_frames_seen >= self.schedule.scheduled_input_frames

    def process(self, input_data: np.ndarray) -> int:
        """Process one backend callback without locks, waits, I/O, or growth."""

        if self.closed:
            return 0
        if (
            not isinstance(input_data, np.ndarray)
            or input_data.dtype != np.float32
            or input_data.ndim != 2
            or input_data.shape[1] < self.input_channels
            or not 1 <= input_data.shape[0] <= self.block_frames
        ):
            self.callback_error_code = _CALLBACK_FORMAT_ERROR
            self.closed = True
            return 0

        frames = int(input_data.shape[0])
        offset = 0
        captured = 0
        schedule = self.schedule
        lead = schedule.lead_in_frames
        total_input = schedule.scheduled_input_frames
        while offset < frames:
            stream_frame = self.input_frames_seen + offset
            if stream_frame >= total_input:
                offset = frames
                continue
            if stream_frame < lead:
                amount = min(frames - offset, lead - stream_frame)
                offset += amount
                continue

            active_frame = stream_frame - lead
            if schedule.cycle_start_frame is None:
                if active_frame >= schedule.punch_frames:
                    offset = frames
                    continue
                amount = min(
                    frames - offset,
                    schedule.punch_frames - active_frame,
                )
                output_start = active_frame
            else:
                cycle_frames = schedule.cycle_frames
                cycle_index = active_frame // cycle_frames
                if cycle_index >= schedule.cycle_count:
                    offset = frames
                    continue
                cycle_offset = active_frame % cycle_frames
                project_frame = schedule.cycle_start_frame + cycle_offset
                if project_frame < schedule.punch_in_frame:
                    amount = min(
                        frames - offset,
                        schedule.punch_in_frame - project_frame,
                    )
                    offset += amount
                    continue
                if project_frame >= schedule.punch_out_frame:
                    amount = min(
                        frames - offset,
                        schedule.cycle_end_frame - project_frame,
                    )
                    offset += amount
                    continue
                amount = min(
                    frames - offset,
                    schedule.punch_out_frame - project_frame,
                )
                output_start = (
                    cycle_index * schedule.punch_frames
                    + project_frame
                    - schedule.punch_in_frame
                )

            source = input_data[offset : offset + amount]
            for ring in self.rings:
                ring.push_from(
                    source,
                    start_frame=output_start,
                    generation=self.generation,
                )
            end = output_start + amount
            self.scheduled_frames = max(self.scheduled_frames, end)
            captured += amount
            offset += amount

        self.input_frames_seen += frames
        return captured


def _recording_segments(
    schedule: ProjectRecordingSchedule,
    output_frames: int,
) -> tuple[ProjectRecordingSegment, ...]:
    remaining = max(0, int(output_frames))
    segments: list[ProjectRecordingSegment] = []
    cycle_index = 0
    output_start = 0
    while remaining > 0 and cycle_index < schedule.cycle_count:
        amount = min(remaining, schedule.punch_frames)
        segments.append(
            ProjectRecordingSegment(
                output_start_frame=output_start,
                project_start_frame=schedule.punch_in_frame,
                frame_count=amount,
                cycle_index=cycle_index,
            )
        )
        output_start += amount
        remaining -= amount
        cycle_index += 1
    return tuple(segments)


def _fsync_regular_file(path: Path) -> None:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_rmtree(working_dir: Path) -> None:
    if (
        working_dir.name.startswith(".webjam-project-recording-")
        and working_dir.parent.is_dir()
        and not working_dir.is_symlink()
    ):
        shutil.rmtree(working_dir, ignore_errors=True)


class _RecordingSession:
    def __init__(
        self,
        *,
        output_dir: Path,
        working_dir: Path,
        schedule: ProjectRecordingSchedule,
        tracks: tuple[ArmedProjectTrack, ...],
        ingress: ProjectRecordingIngress,
        token: GenerationToken,
        writers: tuple[ProjectWaveWriter, ...],
        part_paths: tuple[Path, ...],
        writer_poll_s: float,
    ) -> None:
        self.output_dir = output_dir
        self.working_dir = working_dir
        self.schedule = schedule
        self.tracks = tracks
        self.ingress = ingress
        self.token = token
        self.generation = token.generation
        self.writers = writers
        self.part_paths = part_paths
        self.writer_poll_s = writer_poll_s
        self.scratch = tuple(
            np.empty(
                (ingress.block_frames, track.channels),
                dtype=np.float32,
            )
            for track in tracks
        )
        self.zeroes = tuple(
            np.zeros(
                (ingress.block_frames, track.channels),
                dtype=np.float32,
            )
            for track in tracks
        )
        self.writer_frames = [0 for _track in tracks]
        self.dropouts: list[list[ProjectRecordingDropout]] = [
            [] for _track in tracks
        ]
        self.stop_requested = False
        self.cancelled = False
        self.accepting = True
        self.backend_failed = False
        self.writer_failed = False
        self.failure_message = ""
        self.wake = threading.Event()
        self.result: ProjectRecordingResult | None = None
        self.thread = threading.Thread(
            target=self._writer_loop,
            name=f"project-recording-writer-{self.generation}",
            daemon=True,
        )

    def _append_gap(self, track_index: int, start: int, count: int) -> None:
        if count <= 0:
            return
        gaps = self.dropouts[track_index]
        if gaps and gaps[-1].output_end_frame == start:
            previous = gaps[-1]
            gaps[-1] = ProjectRecordingDropout(
                track_id=previous.track_id,
                output_start_frame=previous.output_start_frame,
                frame_count=previous.frame_count + count,
                channels=previous.channels,
                reason=previous.reason,
            )
            return
        track = self.tracks[track_index]
        gaps.append(
            ProjectRecordingDropout(
                track_id=track.track_id,
                output_start_frame=start,
                frame_count=count,
                channels=tuple(range(track.channels)),
            )
        )

    def _write_zeroes(self, track_index: int, count: int) -> None:
        remaining = count
        writer = self.writers[track_index]
        zeroes = self.zeroes[track_index]
        while remaining > 0:
            amount = min(remaining, self.ingress.block_frames)
            writer.write(zeroes[:amount])
            remaining -= amount

    def _write_block(
        self,
        track_index: int,
        start_frame: int,
        frame_count: int,
    ) -> None:
        position = self.writer_frames[track_index]
        if start_frame < position:
            raise ProjectRecordingError(
                "Project recording blocks arrived out of order."
            )
        if start_frame > position:
            gap = start_frame - position
            self._append_gap(track_index, position, gap)
            self._write_zeroes(track_index, gap)
            position = start_frame
        self.writers[track_index].write(
            self.scratch[track_index][:frame_count]
        )
        self.writer_frames[track_index] = position + frame_count

    def _drain_once(self) -> bool:
        progressed = False
        for index, ring in enumerate(self.ingress.rings):
            # One block per track per round prevents a continuously busy first
            # track from starving the other armed tracks.
            frame_count = ring.pop_into(
                self.scratch[index],
                generation=self.generation,
            )
            if frame_count <= 0:
                continue
            progressed = True
            self._write_block(
                index,
                ring.last_popped_start_frame,
                frame_count,
            )
        return progressed

    def _close_writers(self) -> bool:
        succeeded = True
        for writer in self.writers:
            try:
                writer.flush()
            except Exception:
                succeeded = False
            try:
                writer.close()
            except Exception:
                succeeded = False
        return succeeded

    def _track_results(
        self,
        *,
        files: tuple[Path | None, ...],
        recovered: bool,
    ) -> tuple[ProjectTrackRecording, ...]:
        return tuple(
            ProjectTrackRecording(
                track=track,
                file=files[index],
                frame_count=self.writer_frames[index],
                dropouts=tuple(self.dropouts[index]),
                overflow_frames=ring.overflow_frames,
                recovered=recovered and files[index] is not None,
            )
            for index, (track, ring) in enumerate(
                zip(self.tracks, self.ingress.rings)
            )
        )

    def _recovery_destination(self) -> Path:
        base = self.output_dir.parent / (
            f"Recovered-project-recording-{self.generation}"
        )
        if not base.exists():
            return base
        for suffix in range(1, 1_001):
            candidate = base.with_name(f"{base.name}-{suffix}")
            if not candidate.exists():
                return candidate
        raise ProjectRecordingError(
            "WebJam couldn't reserve a recording recovery folder."
        )

    def _recover(self) -> tuple[Path | None, tuple[Path | None, ...]]:
        if not self.working_dir.exists():
            return None, tuple(None for _track in self.tracks)
        try:
            destination = self._recovery_destination()
            os.replace(self.working_dir, destination)
        except Exception:
            return None, tuple(None for _track in self.tracks)
        recovered: list[Path | None] = []
        try:
            import soundfile as sf  # type: ignore
        except Exception:
            sf = None
        for track in self.tracks:
            part = destination / f"{track.track_id}.wav.part"
            final = destination / f"{track.track_id}.wav"
            candidate = part if part.exists() else final
            output: Path | None = None
            if (
                sf is not None
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                try:
                    info = sf.info(candidate)
                    if (
                        int(info.samplerate) == PROJECT_AUDIO_SAMPLE_RATE
                        and int(info.channels) == track.channels
                        and int(info.frames) > 0
                    ):
                        output = destination / (
                            f"{track.track_id}.recovered-partial.wav"
                        )
                        os.replace(candidate, output)
                except Exception:
                    output = None
            recovered.append(output)
        return destination, tuple(recovered)

    def _failure_result(self) -> ProjectRecordingResult:
        recovery_dir, recovered_files = self._recover()
        return ProjectRecordingResult(
            state=ProjectRecorderState.FAILED,
            generation=self.generation,
            schedule=self.schedule,
            input_frames_seen=self.ingress.input_frames_seen,
            output_frames=max(self.writer_frames, default=0),
            segments=_recording_segments(
                self.schedule,
                max(self.writer_frames, default=0),
            ),
            tracks=self._track_results(
                files=recovered_files,
                recovered=True,
            ),
            recovery_dir=recovery_dir,
            errors=(
                self.failure_message
                or "Project recording stopped before it could be published.",
            ),
        )

    def _publish(self) -> ProjectRecordingResult:
        output_frames = self.ingress.scheduled_frames
        for index, position in enumerate(self.writer_frames):
            if position < output_frames:
                gap = output_frames - position
                self._append_gap(index, position, gap)
                self._write_zeroes(index, gap)
                self.writer_frames[index] = output_frames
        if not self._close_writers():
            raise ProjectRecordingError(
                "WebJam couldn't finalize the project WAV files."
            )
        for path in self.part_paths:
            _fsync_regular_file(path)
        final_names = tuple(
            self.working_dir / f"{track.track_id}.wav"
            for track in self.tracks
        )
        for part, final in zip(self.part_paths, final_names):
            os.replace(part, final)
        if self.output_dir.exists() or self.output_dir.is_symlink():
            raise ProjectRecordingError(
                "The project recording destination already exists."
            )
        os.replace(self.working_dir, self.output_dir)
        files = tuple(
            self.output_dir / f"{track.track_id}.wav"
            for track in self.tracks
        )
        return ProjectRecordingResult(
            state=ProjectRecorderState.COMPLETED,
            generation=self.generation,
            schedule=self.schedule,
            input_frames_seen=self.ingress.input_frames_seen,
            output_frames=output_frames,
            segments=_recording_segments(self.schedule, output_frames),
            tracks=self._track_results(files=files, recovered=False),
            output_dir=self.output_dir,
        )

    def _cancel_result(self) -> ProjectRecordingResult:
        _safe_rmtree(self.working_dir)
        empty_files = tuple(None for _track in self.tracks)
        return ProjectRecordingResult(
            state=ProjectRecorderState.CANCELLED,
            generation=self.generation,
            schedule=self.schedule,
            input_frames_seen=self.ingress.input_frames_seen,
            output_frames=0,
            segments=(),
            tracks=self._track_results(
                files=empty_files,
                recovered=False,
            ),
        )

    def _writer_loop(self) -> None:
        try:
            while True:
                progressed = self._drain_once()
                if self.writer_failed:
                    break
                if self.stop_requested and all(
                    ring.queued_blocks == 0 for ring in self.ingress.rings
                ):
                    break
                if not progressed:
                    self.wake.wait(self.writer_poll_s)
                    self.wake.clear()
        except Exception:
            self.writer_failed = True
            self.accepting = False
            self.failure_message = (
                "Project recording storage failed; complete temporary WAVs "
                "were preserved for recovery."
            )

        if self.cancelled or not self.token.current:
            self._close_writers()
            self.result = self._cancel_result()
            return
        if (
            self.writer_failed
            or self.backend_failed
            or self.ingress.callback_error_code
        ):
            self._close_writers()
            if not self.failure_message:
                self.failure_message = (
                    "The project input stream stopped unexpectedly; complete "
                    "temporary WAVs were preserved for recovery."
                )
            self.result = self._failure_result()
            return
        try:
            self.result = self._publish()
        except Exception:
            self.writer_failed = True
            self.failure_message = (
                "WebJam couldn't atomically publish the project recording; "
                "complete temporary WAVs were preserved for recovery."
            )
            self._close_writers()
            self.result = self._failure_result()


class ProjectMultitrackRecorder:
    """Control-plane owner for one injected-backend recording at a time."""

    def __init__(
        self,
        input_backend: ProjectInputBackend,
        *,
        ring_capacity: int = 64,
        writer_factory: ProjectWaveWriterFactory | None = None,
        writer_poll_s: float = 0.002,
        join_timeout_s: float = 10.0,
    ) -> None:
        self._backend = input_backend
        self._sample_rate = _integer(
            getattr(input_backend, "sample_rate", 0),
            "backend sample_rate",
            minimum=1,
        )
        if self._sample_rate != PROJECT_AUDIO_SAMPLE_RATE:
            raise ProjectRecordingError(
                "Project recording requires an exact 48 kHz input backend."
            )
        self._input_channels = _integer(
            getattr(input_backend, "input_channels", 0),
            "backend input_channels",
            minimum=1,
            maximum=64,
        )
        self._block_frames = _integer(
            getattr(input_backend, "block_frames", 0),
            "backend block_frames",
            minimum=1,
            maximum=65_536,
        )
        self._ring_capacity = _integer(
            ring_capacity,
            "ring_capacity",
            minimum=1,
            maximum=4_096,
        )
        if not 0.0001 <= float(writer_poll_s) <= 1.0:
            raise ValueError("writer_poll_s must be between 0.0001 and 1")
        if not 0.1 <= float(join_timeout_s) <= 120.0:
            raise ValueError("join_timeout_s must be between 0.1 and 120")
        self._writer_poll_s = float(writer_poll_s)
        self._join_timeout_s = float(join_timeout_s)
        self._writer_factory = writer_factory or _default_wave_writer
        self._gate = GenerationGate()
        self._control_lock = threading.Lock()
        self._session: _RecordingSession | None = None
        self._active_generation = 0
        self._state = ProjectRecorderState.IDLE
        self._last_result: ProjectRecordingResult | None = None

    @property
    def state(self) -> ProjectRecorderState:
        with self._control_lock:
            return self._state

    @property
    def result(self) -> ProjectRecordingResult | None:
        with self._control_lock:
            return self._last_result

    @property
    def active_generation(self) -> int:
        return self._active_generation

    def _build_session(
        self,
        output_dir: Path,
        schedule: ProjectRecordingSchedule,
        tracks: tuple[ArmedProjectTrack, ...],
        token: GenerationToken,
    ) -> _RecordingSession:
        ring_bytes = (
            self._ring_capacity
            * self._block_frames
            * sum(track.channels for track in tracks)
            * np.dtype(np.float32).itemsize
        )
        if ring_bytes > _MAX_CAPTURE_RING_BYTES:
            raise ProjectRecordingError(
                "The armed-track recording buffer request is too large."
            )
        ingress = ProjectRecordingIngress(
            schedule,
            tracks,
            input_channels=self._input_channels,
            block_frames=self._block_frames,
            ring_capacity=self._ring_capacity,
            generation=token.generation,
        )
        parent = output_dir.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if output_dir.exists() or output_dir.is_symlink():
                raise ProjectRecordingError(
                    "The project recording destination already exists."
                )
            working_dir = Path(
                tempfile.mkdtemp(
                    prefix=".webjam-project-recording-",
                    dir=parent,
                )
            )
            os.chmod(working_dir, 0o700)
        except ProjectRecordingError:
            raise
        except Exception:
            raise ProjectRecordingError(
                "WebJam couldn't prepare the project recording folder."
            ) from None

        writers: list[ProjectWaveWriter] = []
        parts = tuple(
            working_dir / f"{track.track_id}.wav.part"
            for track in tracks
        )
        try:
            for track, part in zip(tracks, parts):
                writers.append(self._writer_factory(part, track.channels))
        except Exception:
            for writer in writers:
                try:
                    writer.close()
                except Exception:
                    pass
            _safe_rmtree(working_dir)
            raise ProjectRecordingError(
                "WebJam couldn't create the project recording files."
            ) from None
        return _RecordingSession(
            output_dir=output_dir,
            working_dir=working_dir,
            schedule=schedule,
            tracks=tracks,
            ingress=ingress,
            token=token,
            writers=tuple(writers),
            part_paths=parts,
            writer_poll_s=self._writer_poll_s,
        )

    def start(
        self,
        output_dir: str | Path,
        *,
        schedule: ProjectRecordingSchedule,
        tracks: tuple[ArmedProjectTrack, ...],
    ) -> int:
        armed = tuple(tracks)
        if not 1 <= len(armed) <= _MAX_TRACKS:
            raise ValueError("between 1 and 32 tracks must be armed")
        if not all(isinstance(track, ArmedProjectTrack) for track in armed):
            raise ValueError("tracks must contain ArmedProjectTrack values")
        if len({track.track_id.casefold() for track in armed}) != len(armed):
            raise ValueError("armed track identifiers must be unique")
        destination = Path(
            os.path.abspath(Path(output_dir).expanduser())
        )

        with self._control_lock:
            if self._session is not None:
                raise ProjectRecordingError(
                    "A project recording is already active."
                )
            token = self._gate.issue()
            try:
                session = self._build_session(
                    destination,
                    schedule,
                    armed,
                    token,
                )
            except Exception:
                self._gate.cancel()
                raise
            self._session = session
            self._active_generation = token.generation
            self._state = ProjectRecorderState.RECORDING
            self._last_result = None

        generation = token.generation

        def callback(input_data: np.ndarray) -> None:
            if (
                self._active_generation != generation
                or not session.accepting
            ):
                return
            session.ingress.process(input_data)
            if session.ingress.callback_error_code:
                session.accepting = False

        session.thread.start()
        try:
            self._backend.start(callback)
        except Exception:
            self._gate.cancel()
            self._active_generation = 0
            session.accepting = False
            session.cancelled = True
            session.stop_requested = True
            session.wake.set()
            session.thread.join(self._join_timeout_s)
            with self._control_lock:
                self._session = None
                self._state = ProjectRecorderState.FAILED
                self._last_result = session.result
            raise ProjectRecordingError(
                "WebJam couldn't start the project input stream."
            ) from None
        return generation

    def stop(self) -> ProjectRecordingResult:
        with self._control_lock:
            session = self._session
            if session is None:
                if self._last_result is not None:
                    return self._last_result
                raise ProjectRecordingError("No project recording is active.")
            if self._state is ProjectRecorderState.STOPPING:
                raise ProjectRecordingError(
                    "The project recording is already stopping."
                )
            self._state = ProjectRecorderState.STOPPING
            session.accepting = False

        try:
            self._backend.stop()
        except Exception:
            session.backend_failed = True
            try:
                self._backend.abort()
            except Exception:
                pass
        session.stop_requested = True
        session.wake.set()
        session.thread.join(self._join_timeout_s)
        if session.thread.is_alive() or session.result is None:
            session.backend_failed = True
            raise ProjectRecordingError(
                "Project recording is still stopping; its temporary WAVs "
                "remain protected."
            )

        self._gate.cancel()
        self._active_generation = 0
        with self._control_lock:
            self._session = None
            self._last_result = session.result
            self._state = session.result.state
            return session.result

    def cancel(self) -> ProjectRecordingResult:
        with self._control_lock:
            session = self._session
            if session is None:
                if self._last_result is not None:
                    return self._last_result
                raise ProjectRecordingError("No project recording is active.")
            if self._state is ProjectRecorderState.STOPPING:
                raise ProjectRecordingError(
                    "The project recording is already stopping."
                )
            session.accepting = False
            session.cancelled = True
            self._state = ProjectRecorderState.STOPPING
        self._gate.cancel()
        self._active_generation = 0
        try:
            self._backend.abort()
        except Exception:
            pass
        session.stop_requested = True
        session.wake.set()
        session.thread.join(self._join_timeout_s)
        if session.thread.is_alive() or session.result is None:
            raise ProjectRecordingError(
                "Project recording cancellation is still draining safely."
            )
        with self._control_lock:
            self._session = None
            self._last_result = session.result
            self._state = session.result.state
            return session.result


class SoundDeviceProjectInputBackend:
    """Exact-format control-plane adapter around ``sounddevice.InputStream``.

    Stream construction, start/stop, and error translation happen off the
    realtime callback.  The callback itself performs only fixed-shape checks,
    integer counter updates, and one handoff to ``ProjectMultitrackRecorder``.
    A preallocated invalid sentinel reports a backend format violation to the
    recorder without allocating, logging, waiting, or doing path I/O.
    """

    def __init__(
        self,
        *,
        input_channels: int,
        block_frames: int = 512,
        device: str | int | None = None,
        latency: str | float | None = None,
        sounddevice_module=None,
    ) -> None:
        self.sample_rate = PROJECT_AUDIO_SAMPLE_RATE
        self.input_channels = _integer(
            input_channels,
            "input_channels",
            minimum=1,
            maximum=64,
        )
        self.block_frames = _integer(
            block_frames,
            "block_frames",
            minimum=1,
            maximum=65_536,
        )
        if latency is not None and not isinstance(latency, (str, int, float)):
            raise ValueError("latency must be a sounddevice latency value")
        self.device = device
        self.latency = latency
        self._sounddevice = sounddevice_module
        self._stream = None
        self._invalid_block = np.empty(
            (0, self.input_channels),
            dtype=np.float32,
        )
        self.callback_calls = 0
        self.status_events = 0
        self.overflow_events = 0
        self.format_events = 0

    @property
    def snapshot(self) -> SoundDeviceInputSnapshot:
        return SoundDeviceInputSnapshot(
            running=self._stream is not None,
            callback_calls=int(self.callback_calls),
            status_events=int(self.status_events),
            overflow_events=int(self.overflow_events),
            format_events=int(self.format_events),
        )

    def start(self, callback: Callable[[np.ndarray], None]) -> None:
        if self._stream is not None:
            raise ProjectRecordingError("Project input is already running.")
        if not callable(callback):
            raise TypeError("callback must be callable")
        module = self._sounddevice
        if module is None:
            try:
                import sounddevice as module  # type: ignore
            except Exception:
                raise ProjectRecordingError(
                    "Project audio input is unavailable in this build."
                ) from None

        invalid_block = self._invalid_block
        expected_frames = self.block_frames
        expected_channels = self.input_channels

        def input_callback(indata, frames, _time_info, status) -> None:
            self.callback_calls += 1
            if status:
                self.status_events += 1
                if getattr(status, "input_overflow", False):
                    self.overflow_events += 1
            if (
                frames != expected_frames
                or not isinstance(indata, np.ndarray)
                or indata.dtype != np.float32
                or indata.ndim != 2
                or indata.shape[0] != expected_frames
                or indata.shape[1] != expected_channels
            ):
                self.format_events += 1
                callback(invalid_block)
                return
            callback(indata)

        kwargs = {
            "samplerate": self.sample_rate,
            "blocksize": self.block_frames,
            "channels": self.input_channels,
            "dtype": "float32",
            "device": self.device,
            "callback": input_callback,
        }
        if self.latency is not None:
            kwargs["latency"] = self.latency
        stream = None
        try:
            stream = module.InputStream(**kwargs)
            stream.start()
        except Exception:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise ProjectRecordingError(
                "WebJam couldn't open the selected Studio input device."
            ) from None
        self._stream = stream

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        failure = False
        try:
            stream.stop()
        except Exception:
            failure = True
        try:
            stream.close()
        except Exception:
            failure = True
        if failure:
            raise ProjectRecordingError(
                "WebJam couldn't stop the Studio input device cleanly."
            )

    def abort(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        failure = False
        try:
            stream.abort()
        except Exception:
            failure = True
        try:
            stream.close()
        except Exception:
            failure = True
        if failure:
            raise ProjectRecordingError(
                "WebJam couldn't abort the Studio input device cleanly."
            )


__all__ = [
    "ArmedProjectTrack",
    "ProjectInputBackend",
    "ProjectMultitrackRecorder",
    "ProjectRecorderState",
    "ProjectRecordingDropout",
    "ProjectRecordingError",
    "ProjectRecordingIngress",
    "ProjectRecordingResult",
    "ProjectRecordingSchedule",
    "ProjectRecordingSegment",
    "ProjectTrackRecording",
    "ProjectWaveWriter",
    "ProjectWaveWriterFactory",
    "SoundDeviceInputSnapshot",
    "SoundDeviceProjectInputBackend",
]

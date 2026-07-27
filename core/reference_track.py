"""Host-controlled, Jamulus-routed reference-track primitives.

This module deliberately has no Qt dependency and never persists a selected
media path.  Source decoding runs on a bounded producer thread; a real-time
audio callback only consumes already-decoded float32 blocks and emits silence
on underrun.

The platform boundary lives behind :class:`ReferenceAudioBridgeBackend`.
Production currently supplies a capability-gated macOS BlackHole backend.
Windows and Linux remain truthfully unavailable until equivalent routing
isolation can be proved there.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math
import os
from pathlib import Path
import stat
import threading
from typing import Callable, Protocol

import numpy as np


REFERENCE_SAMPLE_RATE = 48_000
REFERENCE_BLOCK_FRAMES = 1_024
REFERENCE_QUEUE_BLOCKS = 12
REFERENCE_MAX_DECODE_FRAMES = 4_096
REFERENCE_MAX_SOURCE_RATE = 384_000
REFERENCE_MIN_TRIM_DB = -60.0
REFERENCE_MAX_TRIM_DB = 12.0
REFERENCE_MAX_COUNT_IN_BEATS = 16
REFERENCE_MIN_BPM = 20.0
REFERENCE_MAX_BPM = 400.0
_SUPPORTED_EXTENSIONS = frozenset({".wav", ".aif", ".aiff", ".flac", ".mp3"})
_ROUTE_WARNING = (
    "Jamulus-routed: everyone hears this like another musician, with the "
    "session's normal buffering, jitter handling, and network latency. "
    "A server recording captures it as a separate stem."
)


class ReferenceTrackError(RuntimeError):
    """A path-free, musician-safe reference-track failure."""


class ReferenceTrackState(str, Enum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    ROUTING = "routing"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ReferenceTrackCapability:
    available: bool
    platform: str
    detail: str
    route_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "available", bool(self.available))
        for name, maximum in (("platform", 32), ("detail", 512), ("route_name", 128)):
            value = str(getattr(self, name) or "").strip()
            if len(value) > maximum:
                raise ValueError(f"{name} is too long")
            object.__setattr__(self, name, value)
        if self.available and not self.route_name:
            raise ValueError("an available route requires route_name")


@dataclass(frozen=True, slots=True)
class ReferenceTrackLaunchContext:
    """Ephemeral facts needed to launch one separately-owned Jamulus client."""

    server_address: str
    jamulus_binary: str
    primary_udp_port: int
    primary_rpc_port: int
    primary_process_id: int
    primary_input_device_name: str = ""
    primary_output_device_name: str = ""
    audience_bridge_active: bool = False

    def __post_init__(self) -> None:
        server = str(self.server_address or "").strip()
        binary = str(self.jamulus_binary or "").strip()
        if not server:
            raise ValueError("server_address must not be empty")
        if not binary:
            raise ValueError("jamulus_binary must not be empty")
        object.__setattr__(self, "server_address", server)
        object.__setattr__(self, "jamulus_binary", binary)
        for field_name in ("primary_udp_port", "primary_rpc_port"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not 1 <= int(value) <= 65_535:
                raise ValueError(f"{field_name} must be between 1 and 65535")
            object.__setattr__(self, field_name, int(value))
        if (
            isinstance(self.primary_process_id, bool)
            or int(self.primary_process_id) <= 0
        ):
            raise ValueError("primary_process_id must be a positive integer")
        object.__setattr__(
            self, "primary_process_id", int(self.primary_process_id)
        )
        for field_name in (
            "primary_input_device_name",
            "primary_output_device_name",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if (
                len(value) > 512
                or any(character in value for character in ("\0", "\r", "\n"))
            ):
                raise ValueError(f"{field_name} is invalid")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self, "audience_bridge_active", bool(self.audience_bridge_active)
        )


@dataclass(frozen=True, slots=True)
class ReferenceTrackSnapshot:
    state: ReferenceTrackState
    capability: ReferenceTrackCapability
    source_name: str = ""
    duration_s: float = 0.0
    position_s: float = 0.0
    loop_start_s: float = 0.0
    loop_end_s: float | None = None
    trim_db: float = 0.0
    count_in_beats: int = 0
    count_in_bpm: float = 120.0
    route_detail: str = ""
    error: str = ""
    warning: str = _ROUTE_WARNING

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ReferenceTrackState(self.state))
        if not isinstance(self.capability, ReferenceTrackCapability):
            raise ValueError("capability must be a ReferenceTrackCapability")
        source_name = str(self.source_name or "").strip()
        if len(source_name) > 255 or any(c in source_name for c in ("\0", "\r", "\n")):
            source_name = "Selected song"
        object.__setattr__(self, "source_name", source_name)
        for name in ("duration_s", "position_s", "loop_start_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if self.loop_end_s is not None:
            loop_end = float(self.loop_end_s)
            if not math.isfinite(loop_end) or loop_end <= self.loop_start_s:
                raise ValueError("loop_end_s must be after loop_start_s")
            object.__setattr__(self, "loop_end_s", loop_end)
        trim = float(self.trim_db)
        if not REFERENCE_MIN_TRIM_DB <= trim <= REFERENCE_MAX_TRIM_DB:
            raise ValueError("trim_db is out of range")
        object.__setattr__(self, "trim_db", trim)
        beats = int(self.count_in_beats)
        if isinstance(self.count_in_beats, bool) or not 0 <= beats <= REFERENCE_MAX_COUNT_IN_BEATS:
            raise ValueError("count_in_beats is out of range")
        object.__setattr__(self, "count_in_beats", beats)
        bpm = float(self.count_in_bpm)
        if not REFERENCE_MIN_BPM <= bpm <= REFERENCE_MAX_BPM:
            raise ValueError("count_in_bpm is out of range")
        object.__setattr__(self, "count_in_bpm", bpm)
        for name in ("route_detail", "error", "warning"):
            value = str(getattr(self, name) or "").strip()
            if len(value) > 1_024:
                raise ValueError(f"{name} is too long")
            object.__setattr__(self, name, value)

    @property
    def loaded(self) -> bool:
        return bool(self.source_name)

    @property
    def can_play(self) -> bool:
        return bool(
            self.loaded
            and self.capability.available
            and self.state
            in {
                ReferenceTrackState.READY,
                ReferenceTrackState.PAUSED,
            }
        )

    @property
    def active(self) -> bool:
        return self.state in {
            ReferenceTrackState.ROUTING,
            ReferenceTrackState.PLAYING,
            ReferenceTrackState.PAUSED,
            ReferenceTrackState.STOPPING,
        }


class ReferenceAudioBridgeSession(Protocol):
    @property
    def route_name(self) -> str: ...

    def start(self, pull: Callable[[int], np.ndarray]) -> None: ...

    def health_error(self) -> str: ...

    def stop(self) -> None: ...


class ReferenceAudioBridgeBackend(Protocol):
    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability: ...

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession: ...


@dataclass(frozen=True, slots=True)
class ReferenceTrackSourceInfo:
    name: str
    duration_s: float
    source_samplerate: int
    channels: int
    output_frames: int


class ReferenceTrackDecoder:
    """Descriptor-bound, bounded mono/stereo decoder with 48-kHz output."""

    def __init__(self, path: str | Path) -> None:
        candidate = Path(path).expanduser()
        try:
            metadata = candidate.lstat()
        except OSError:
            raise ReferenceTrackError(
                "That song is unavailable. Choose a local audio file and try again."
            ) from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or candidate.is_symlink()
            or candidate.suffix.lower() not in _SUPPORTED_EXTENSIONS
        ):
            raise ReferenceTrackError(
                "Choose a local WAV, AIFF, FLAC, or supported MP3 audio file."
            )

        descriptor = -1
        source_file = None
        try:
            import soundfile as sf  # type: ignore

            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(candidate, flags)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
            ):
                raise OSError("source changed during open")
            source_file = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
            reader = sf.SoundFile(source_file, mode="r")
        except Exception:  # noqa: BLE001 - native decoder boundary
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if source_file is not None:
                try:
                    source_file.close()
                except OSError:
                    pass
            raise ReferenceTrackError(
                "WebJam couldn't read that song. Try WAV, AIFF, FLAC, or "
                "an MP3 supported by this build."
            ) from None

        try:
            source_rate = int(reader.samplerate)
            channels = int(reader.channels)
            source_frames = int(reader.frames)
            if not 1 <= source_rate <= REFERENCE_MAX_SOURCE_RATE:
                raise ValueError("unsupported source sample rate")
            if channels not in {1, 2}:
                raise ValueError("unsupported source channel count")
            if source_frames <= 0:
                raise ValueError("empty source")
            output_frames = max(
                1, int(round(source_frames * REFERENCE_SAMPLE_RATE / source_rate))
            )
        except Exception:
            reader.close()
            try:
                source_file.close()
            except OSError:
                pass
            raise ReferenceTrackError(
                "That song needs one or two channels and a readable sample rate."
            ) from None

        safe_name = candidate.name.strip()
        if (
            not safe_name
            or len(safe_name) > 255
            or any(c in safe_name for c in ("\0", "\r", "\n"))
        ):
            safe_name = "Selected song"
        self._reader = reader
        self._source_file = source_file
        self._source_rate = source_rate
        self._source_frames = source_frames
        self._channels = channels
        self._output_frames = output_frames
        self._closed = False
        self._lock = threading.Lock()
        self.info = ReferenceTrackSourceInfo(
            name=safe_name,
            duration_s=output_frames / REFERENCE_SAMPLE_RATE,
            source_samplerate=source_rate,
            channels=channels,
            output_frames=output_frames,
        )

    def __repr__(self) -> str:
        return (
            "ReferenceTrackDecoder("
            f"rate={self._source_rate}, channels={self._channels}, "
            f"output_frames={self._output_frames})"
        )

    @property
    def output_frames(self) -> int:
        return self._output_frames

    def read_48k(self, start_frame: int, frames: int) -> np.ndarray:
        """Decode an exact bounded output window without retaining the file."""

        start = int(start_frame)
        requested = int(frames)
        if start < 0:
            raise ValueError("start_frame must be non-negative")
        if not 0 <= requested <= REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("frames exceeds the bounded decoder limit")
        output = np.zeros((requested, 2), dtype=np.float32)
        if requested == 0 or start >= self._output_frames:
            return output
        usable = min(requested, self._output_frames - start)
        ratio = self._source_rate / REFERENCE_SAMPLE_RATE
        positions = (start + np.arange(usable, dtype=np.float64)) * ratio
        first = int(math.floor(float(positions[0])))
        last = min(
            self._source_frames - 1,
            int(math.floor(float(positions[-1]))) + 1,
        )
        read_count = max(1, last - first + 1)
        with self._lock:
            if self._closed:
                raise ReferenceTrackError("The selected song was already closed.")
            try:
                self._reader.seek(first)
                source = self._reader.read(
                    read_count, dtype="float32", always_2d=True
                )
            except Exception:  # noqa: BLE001
                raise ReferenceTrackError(
                    "WebJam lost access to the selected song. Load it again."
                ) from None
        if len(source) == 0:
            return output
        source = np.asarray(source, dtype=np.float32)
        if source.shape[0] < read_count:
            source = np.pad(
                source,
                ((0, read_count - source.shape[0]), (0, 0)),
                mode="edge",
            )
        local = positions - first
        left_index = np.floor(local).astype(np.int64)
        right_index = np.minimum(left_index + 1, source.shape[0] - 1)
        fraction = (local - left_index).astype(np.float32)[:, None]
        rendered = (
            source[left_index] * (1.0 - fraction)
            + source[right_index] * fraction
        )
        if self._channels == 1:
            rendered = np.repeat(rendered, 2, axis=1)
        output[:usable] = rendered[:, :2]
        return output

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._reader.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._source_file.close()
            except Exception:  # noqa: BLE001
                pass


@dataclass(slots=True)
class _QueuedBlock:
    audio: np.ndarray
    song_start_frame: int
    song_end_frame: int
    finish_after: bool = False
    count_in: bool = False


class ReferenceTrackStream:
    """Bounded decode producer and non-blocking-ish callback consumer."""

    def __init__(
        self,
        decoder: ReferenceTrackDecoder,
        *,
        block_frames: int = REFERENCE_BLOCK_FRAMES,
        queue_blocks: int = REFERENCE_QUEUE_BLOCKS,
    ) -> None:
        block_frames = int(block_frames)
        queue_blocks = int(queue_blocks)
        if not 1 <= block_frames <= REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("block_frames is out of range")
        if not 2 <= queue_blocks <= 128:
            raise ValueError("queue_blocks is out of range")
        self._decoder = decoder
        self._block_frames = block_frames
        self._queue_blocks = queue_blocks
        self._condition = threading.Condition(threading.Lock())
        self._queue: deque[_QueuedBlock] = deque()
        self._front_offset = 0
        self._playing = False
        self._closed = False
        self._finished = False
        self._error = ""
        self._generation = 0
        self._producer_position = 0
        self._consumer_position = 0
        self._loop_start = 0
        self._loop_end: int | None = None
        self._trim_db = 0.0
        self._trim_gain = 1.0
        self._count_in_beats = 0
        self._count_in_bpm = 120.0
        self._count_in_total = 0
        self._count_in_cursor = 0
        self._thread = threading.Thread(
            target=self._run,
            name="WebJam reference-track decoder",
            daemon=True,
        )
        self._thread.start()

    @property
    def duration_s(self) -> float:
        return self._decoder.output_frames / REFERENCE_SAMPLE_RATE

    @property
    def position_s(self) -> float:
        with self._condition:
            return self._consumer_position / REFERENCE_SAMPLE_RATE

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._finished

    @property
    def error(self) -> str:
        with self._condition:
            return self._error

    def configure_loop(self, start_s: float, end_s: float | None) -> None:
        start = self._seconds_to_frame(start_s)
        end = None if end_s is None else self._seconds_to_frame(end_s)
        if end is not None and (
            end <= start or end > self._decoder.output_frames
        ):
            raise ReferenceTrackError(
                "The loop end must be after its start and inside the song."
            )
        with self._condition:
            self._loop_start = start
            self._loop_end = end
            if end is not None and self._consumer_position >= end:
                self._consumer_position = start
            self._reset_producer_locked()

    def configure_trim(self, trim_db: float) -> None:
        value = float(trim_db)
        if (
            not math.isfinite(value)
            or not REFERENCE_MIN_TRIM_DB <= value <= REFERENCE_MAX_TRIM_DB
        ):
            raise ReferenceTrackError(
                f"Song trim must be between {REFERENCE_MIN_TRIM_DB:g} and "
                f"+{REFERENCE_MAX_TRIM_DB:g} dB."
            )
        with self._condition:
            self._trim_db = value
            self._trim_gain = 10.0 ** (value / 20.0)
            self._reset_producer_locked()

    def configure_count_in(self, beats: int, bpm: float = 120.0) -> None:
        if (
            isinstance(beats, bool)
            or not 0 <= int(beats) <= REFERENCE_MAX_COUNT_IN_BEATS
        ):
            raise ReferenceTrackError(
                f"Count-in must be 0 to {REFERENCE_MAX_COUNT_IN_BEATS} beats."
            )
        tempo = float(bpm)
        if not math.isfinite(tempo) or not REFERENCE_MIN_BPM <= tempo <= REFERENCE_MAX_BPM:
            raise ReferenceTrackError(
                f"Count-in tempo must be {REFERENCE_MIN_BPM:g} to "
                f"{REFERENCE_MAX_BPM:g} BPM."
            )
        with self._condition:
            self._count_in_beats = int(beats)
            self._count_in_bpm = tempo

    def play(self, *, count_in: bool = False) -> None:
        with self._condition:
            if self._closed:
                raise ReferenceTrackError("The selected song was already closed.")
            if self._consumer_position >= self._decoder.output_frames:
                self._consumer_position = (
                    self._loop_start if self._loop_end is not None else 0
                )
            self._finished = False
            self._error = ""
            self._playing = True
            self._reset_producer_locked()
            if count_in and self._count_in_beats:
                frames_per_beat = round(
                    REFERENCE_SAMPLE_RATE * 60.0 / self._count_in_bpm
                )
                self._count_in_total = frames_per_beat * self._count_in_beats
            else:
                self._count_in_total = 0
            self._count_in_cursor = 0
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            self._playing = False
            self._count_in_total = 0
            self._count_in_cursor = 0
            self._reset_producer_locked()

    def seek(self, seconds: float) -> None:
        frame = self._seconds_to_frame(seconds)
        if frame > self._decoder.output_frames:
            frame = self._decoder.output_frames
        with self._condition:
            self._consumer_position = frame
            self._finished = False
            self._error = ""
            self._count_in_total = 0
            self._count_in_cursor = 0
            self._reset_producer_locked()

    def restart(self, *, count_in: bool = True) -> None:
        self.seek(0.0)
        self.play(count_in=count_in)

    def pull(self, frames: int) -> np.ndarray:
        """Consume prepared audio; never performs source I/O."""

        requested = int(frames)
        if requested <= 0 or requested > REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("pull frames is out of range")
        output = np.zeros((requested, 2), dtype=np.float32)
        written = 0
        with self._condition:
            if not self._playing or self._closed:
                return output
            while written < requested and self._queue:
                block = self._queue[0]
                available = block.audio.shape[0] - self._front_offset
                take = min(available, requested - written)
                begin = self._front_offset
                output[written : written + take] = block.audio[begin : begin + take]
                self._front_offset += take
                written += take
                if not block.count_in:
                    self._consumer_position = min(
                        block.song_end_frame,
                        block.song_start_frame + self._front_offset,
                    )
                if self._front_offset >= block.audio.shape[0]:
                    self._queue.popleft()
                    self._front_offset = 0
                    if block.finish_after:
                        self._finished = True
                        self._playing = False
                if self._finished:
                    break
            self._condition.notify_all()
        return output

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._playing = False
            self._queue.clear()
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        self._decoder.close()

    def _reset_producer_locked(self) -> None:
        self._generation += 1
        self._queue.clear()
        self._front_offset = 0
        self._producer_position = self._consumer_position
        self._condition.notify_all()

    def _seconds_to_frame(self, seconds: float) -> int:
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            raise ReferenceTrackError("Song position must be finite and non-negative.")
        return int(round(value * REFERENCE_SAMPLE_RATE))

    def _run(self) -> None:
        while True:
            with self._condition:
                while (
                    not self._closed
                    and (
                        not self._playing
                        or len(self._queue) >= self._queue_blocks
                        or (
                            self._producer_position >= self._decoder.output_frames
                            and self._loop_end is None
                            and self._count_in_cursor >= self._count_in_total
                        )
                    )
                ):
                    self._condition.wait(timeout=0.25)
                if self._closed:
                    return
                generation = self._generation
                count_remaining = self._count_in_total - self._count_in_cursor
                if count_remaining > 0:
                    amount = min(self._block_frames, count_remaining)
                    count_start = self._count_in_cursor
                    self._count_in_cursor += amount
                    song_start = self._producer_position
                    trim_gain = self._trim_gain
                    count_config = (
                        self._count_in_bpm,
                        self._count_in_beats,
                        self._count_in_total,
                    )
                else:
                    loop_end = self._loop_end
                    if loop_end is not None and self._producer_position >= loop_end:
                        self._producer_position = self._loop_start
                    song_start = self._producer_position
                    boundary = (
                        loop_end
                        if loop_end is not None
                        else self._decoder.output_frames
                    )
                    amount = min(self._block_frames, boundary - song_start)
                    if amount <= 0:
                        self._condition.wait(timeout=0.05)
                        continue
                    self._producer_position += amount
                    finish_after = (
                        loop_end is None
                        and self._producer_position >= self._decoder.output_frames
                    )
                    trim_gain = self._trim_gain

            if count_remaining > 0:
                audio = self._render_count_in(
                    count_start,
                    amount,
                    bpm=count_config[0],
                    total_beats=count_config[1],
                    total_frames=count_config[2],
                )
                block = _QueuedBlock(
                    # Source trim is intentionally not a count-in gain. The
                    # cue must remain audible even when a hot song is trimmed
                    # far down.
                    audio=np.clip(audio, -1.0, 1.0),
                    song_start_frame=song_start,
                    song_end_frame=song_start,
                    count_in=True,
                )
            else:
                try:
                    audio = self._decoder.read_48k(song_start, amount)
                except ReferenceTrackError as exc:
                    with self._condition:
                        if generation == self._generation:
                            self._error = str(exc)
                            self._finished = True
                            self._playing = False
                    continue
                block = _QueuedBlock(
                    audio=np.clip(audio * trim_gain, -1.0, 1.0),
                    song_start_frame=song_start,
                    song_end_frame=song_start + amount,
                    finish_after=finish_after,
                )
            with self._condition:
                if (
                    not self._closed
                    and self._playing
                    and generation == self._generation
                ):
                    self._queue.append(block)
                    self._condition.notify_all()

    @staticmethod
    def _render_count_in(
        start: int,
        frames: int,
        *,
        bpm: float,
        total_beats: int,
        total_frames: int,
    ) -> np.ndarray:
        del total_beats
        per_beat = max(1, round(REFERENCE_SAMPLE_RATE * 60.0 / bpm))
        indexes = start + np.arange(frames, dtype=np.int64)
        phase_in_beat = indexes % per_beat
        click_frames = min(per_beat, round(REFERENCE_SAMPLE_RATE * 0.04))
        active = phase_in_beat < click_frames
        beat_number = indexes // per_beat
        last_beat = max(0, (total_frames - 1) // per_beat)
        frequency = np.where(beat_number == last_beat, 1_400.0, 950.0)
        envelope = np.maximum(
            0.0, 1.0 - phase_in_beat.astype(np.float32) / max(1, click_frames)
        )
        tone = (
            np.sin(
                2.0
                * np.pi
                * frequency
                * phase_in_beat.astype(np.float64)
                / REFERENCE_SAMPLE_RATE
            )
            * envelope
            * active
            * 0.55
        ).astype(np.float32)
        return np.column_stack((tone, tone))


class ReferenceTrackController:
    """Thread-safe host authority for one ephemeral reference-track route."""

    def __init__(
        self,
        backend: ReferenceAudioBridgeBackend,
        *,
        is_host: Callable[[], bool],
        on_snapshot: Callable[[ReferenceTrackSnapshot], None] | None = None,
    ) -> None:
        if not callable(is_host):
            raise TypeError("is_host must be callable")
        if on_snapshot is not None and not callable(on_snapshot):
            raise TypeError("on_snapshot must be callable or None")
        self._backend = backend
        self._is_host = is_host
        self._on_snapshot = on_snapshot
        self._lock = threading.RLock()
        self._stream: ReferenceTrackStream | None = None
        self._session: ReferenceAudioBridgeSession | None = None
        self._state = ReferenceTrackState.IDLE
        self._source_name = ""
        self._duration_s = 0.0
        self._loop_start_s = 0.0
        self._loop_end_s: float | None = None
        self._trim_db = 0.0
        self._count_in_beats = 0
        self._count_in_bpm = 120.0
        self._route_detail = ""
        self._error = ""
        self._capability = self._safe_capability(False)
        if not self._capability.available:
            self._state = ReferenceTrackState.UNAVAILABLE

    @property
    def snapshot(self) -> ReferenceTrackSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def refresh_capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackSnapshot:
        capability = self._safe_capability(audience_bridge_active)
        with self._lock:
            active_session = self._session is not None
        stopped: ReferenceTrackSnapshot | None = None
        if active_session and not capability.available:
            # A route that becomes incompatible (most importantly because the
            # audience bridge claimed the same device) must stop before the UI
            # can publish the new capability. Merely greying out controls
            # would leave an unsafe second client alive.
            stopped = self.stop()
        with self._lock:
            self._require_open_locked()
            self._capability = capability
            if (
                stopped is not None
                and stopped.state is ReferenceTrackState.FAILED
                and self._session is not None
            ):
                # Preserve teardown truth. The capability is unavailable, but
                # the more urgent fact is that owned-process death is unproved.
                pass
            elif not capability.available and self._session is None:
                self._state = ReferenceTrackState.UNAVAILABLE
            elif capability.available and self._state is ReferenceTrackState.UNAVAILABLE:
                self._state = (
                    ReferenceTrackState.READY
                    if self._stream is not None
                    else ReferenceTrackState.IDLE
                )
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def load(self, path: str | Path) -> ReferenceTrackSnapshot:
        stopped = self.stop()
        with self._lock:
            self._require_open_locked()
            if (
                stopped.state is ReferenceTrackState.FAILED
                and self._session is not None
            ):
                # Never replace the source (or hide the failure state) while
                # an older owned Jamulus process may still be alive.
                return stopped
            self._state = ReferenceTrackState.LOADING
            self._error = ""
            loading = self._snapshot_locked()
        self._notify(loading)
        try:
            decoder = ReferenceTrackDecoder(path)
            stream = ReferenceTrackStream(decoder)
        except ReferenceTrackError as exc:
            with self._lock:
                self._state = ReferenceTrackState.FAILED
                self._error = str(exc)
                failed = self._snapshot_locked()
            self._notify(failed)
            return failed

        old_stream: ReferenceTrackStream | None
        with self._lock:
            old_stream = self._stream
            self._stream = stream
            self._source_name = decoder.info.name
            self._duration_s = decoder.info.duration_s
            self._loop_start_s = 0.0
            self._loop_end_s = None
            self._trim_db = 0.0
            self._error = ""
            self._state = (
                ReferenceTrackState.READY
                if self._capability.available
                else ReferenceTrackState.UNAVAILABLE
            )
            snapshot = self._snapshot_locked()
        if old_stream is not None:
            old_stream.close()
        self._notify(snapshot)
        return snapshot

    def play(self, context: ReferenceTrackLaunchContext) -> ReferenceTrackSnapshot:
        resume_session: ReferenceAudioBridgeSession | None = None
        with self._lock:
            self._require_open_locked()
            if not self._is_host():
                return self._fail_locked(
                    "Only the session host can control the Reference Track."
                )
            if context.audience_bridge_active:
                return self._fail_locked(
                    "Reference Track can't share BlackHole with the Webex audience "
                    "bridge. Switch Webex to talkback or video-only first."
                )
            if self._stream is None:
                return self._fail_locked("Load a song before starting Reference Track.")
            if self._state is ReferenceTrackState.PLAYING:
                return self._snapshot_locked()
            if (
                self._state is ReferenceTrackState.PAUSED
                and self._session is not None
            ):
                resume_session = self._session
        if resume_session is not None:
            checked = self.refresh_health()
            with self._lock:
                if (
                    checked.state is ReferenceTrackState.FAILED
                    or self._session is not resume_session
                ):
                    return checked
                if self._state is not ReferenceTrackState.PAUSED:
                    return self._snapshot_locked()
                self._stream.play(count_in=False)
                self._state = ReferenceTrackState.PLAYING
                self._error = ""
                resumed = self._snapshot_locked()
            self._notify(resumed)
            return resumed
        with self._lock:
            self._capability = self._safe_capability(
                context.audience_bridge_active
            )
            if not self._capability.available:
                return self._fail_locked(self._capability.detail)
            self._state = ReferenceTrackState.ROUTING
            self._error = ""
            routing = self._snapshot_locked()
        self._notify(routing)

        session: ReferenceAudioBridgeSession | None = None
        try:
            session = self._backend.prepare(context)
            session.start(self._stream.pull)
            self._stream.play(count_in=True)
        except Exception as exc:  # noqa: BLE001 - backend boundary
            teardown_error = ""
            if session is not None:
                try:
                    session.stop()
                except ReferenceTrackError as stop_exc:
                    teardown_error = str(stop_exc)
                except Exception:  # noqa: BLE001
                    teardown_error = (
                        "Reference Track couldn't confirm that its owned "
                        "Jamulus client stopped."
                    )
                if teardown_error:
                    with self._lock:
                        # Retain the exact session owner so Stop/Close/Load can
                        # retry cleanup instead of hiding a surviving client.
                        self._session = session
                        self._route_detail = str(
                            getattr(session, "route_name", "") or ""
                        )
            message = (
                teardown_error
                or (
                    str(exc)
                    if isinstance(exc, ReferenceTrackError)
                    else "WebJam couldn't prove a safe Reference Track route."
                )
            )
            with self._lock:
                return self._fail_locked(message)

        with self._lock:
            self._session = session
            self._route_detail = session.route_name
            self._state = ReferenceTrackState.PLAYING
            self._error = ""
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def pause(self) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._state is not ReferenceTrackState.PLAYING or self._stream is None:
                raise ReferenceTrackError("Reference Track is not playing.")
            self._stream.pause()
            self._state = ReferenceTrackState.PAUSED
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def restart(self) -> ReferenceTrackSnapshot:
        checked = self.refresh_health()
        if checked.state is ReferenceTrackState.FAILED:
            return checked
        with self._lock:
            self._require_open_locked()
            if self._stream is None or self._session is None:
                raise ReferenceTrackError("Start Reference Track before restarting it.")
            self._stream.restart(count_in=True)
            self._state = ReferenceTrackState.PLAYING
            self._error = ""
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def seek(self, seconds: float) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before seeking.")
            if self._state not in {
                ReferenceTrackState.PAUSED,
                ReferenceTrackState.READY,
            }:
                raise ReferenceTrackError("Pause Reference Track before seeking.")
            self._stream.seek(seconds)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_loop(
        self, start_s: float, end_s: float | None
    ) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before setting a loop.")
            self._stream.configure_loop(start_s, end_s)
            self._loop_start_s = float(start_s)
            self._loop_end_s = None if end_s is None else float(end_s)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_trim_db(self, trim_db: float) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before changing its trim.")
            self._stream.configure_trim(trim_db)
            self._trim_db = float(trim_db)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_count_in(
        self, beats: int, bpm: float = 120.0
    ) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before setting its count-in.")
            self._stream.configure_count_in(beats, bpm)
            self._count_in_beats = int(beats)
            self._count_in_bpm = float(bpm)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def refresh_health(self) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            session = self._session
            stream = self._stream
        if session is not None:
            try:
                error = session.health_error().strip()
            except Exception:  # noqa: BLE001
                error = "Reference Track couldn't verify its owned route."
            if error:
                teardown_error = self._stop_session()
                with self._lock:
                    self._state = ReferenceTrackState.FAILED
                    self._error = teardown_error or error
                    snapshot = self._snapshot_locked()
                self._notify(snapshot)
                return snapshot
        if stream is not None and stream.error:
            teardown_error = self._stop_session()
            with self._lock:
                self._state = ReferenceTrackState.FAILED
                self._error = teardown_error or stream.error
                snapshot = self._snapshot_locked()
            self._notify(snapshot)
            return snapshot
        if stream is not None and stream.finished and session is not None:
            self.stop()
        return self.snapshot

    def handle_session_end(self) -> ReferenceTrackSnapshot:
        return self.stop()

    def stop(self) -> ReferenceTrackSnapshot:
        with self._lock:
            if self._state is ReferenceTrackState.CLOSED:
                return self._snapshot_locked()
            has_active = self._session is not None
            if has_active:
                self._state = ReferenceTrackState.STOPPING
                stopping = self._snapshot_locked()
            else:
                stopping = None
            stream = self._stream
        if stopping is not None:
            self._notify(stopping)
        if stream is not None:
            stream.pause()
            stream.seek(0.0)
        teardown_error = self._stop_session()
        with self._lock:
            self._route_detail = ""
            if teardown_error:
                self._error = teardown_error
                self._state = ReferenceTrackState.FAILED
            else:
                self._error = ""
                self._state = (
                    ReferenceTrackState.READY
                    if self._stream is not None and self._capability.available
                    else (
                        ReferenceTrackState.UNAVAILABLE
                        if not self._capability.available
                        else ReferenceTrackState.IDLE
                    )
                )
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def close(self) -> ReferenceTrackSnapshot:
        stopped = self.stop()
        with self._lock:
            if (
                stopped.state is ReferenceTrackState.FAILED
                and self._session is not None
            ):
                return stopped
        with self._lock:
            stream = self._stream
            self._stream = None
            self._state = ReferenceTrackState.CLOSED
            self._source_name = ""
            self._duration_s = 0.0
            snapshot = self._snapshot_locked()
        if stream is not None:
            stream.close()
        self._notify(snapshot)
        return snapshot

    def _stop_session(self) -> str:
        with self._lock:
            session = self._session
        if session is None:
            return ""
        try:
            session.stop()
        except ReferenceTrackError as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            return (
                "Reference Track couldn't confirm that its owned Jamulus "
                "client stopped."
            )
        with self._lock:
            if self._session is session:
                self._session = None
        return ""

    def _safe_capability(self, audience_bridge_active: bool) -> ReferenceTrackCapability:
        try:
            capability = self._backend.capability(audience_bridge_active)
        except Exception:  # noqa: BLE001
            capability = ReferenceTrackCapability(
                False,
                "unknown",
                "Reference Track routing could not be inspected safely.",
            )
        if not isinstance(capability, ReferenceTrackCapability):
            return ReferenceTrackCapability(
                False,
                "unknown",
                "Reference Track routing returned invalid capability evidence.",
            )
        return capability

    def _snapshot_locked(self) -> ReferenceTrackSnapshot:
        position = self._stream.position_s if self._stream is not None else 0.0
        return ReferenceTrackSnapshot(
            state=self._state,
            capability=self._capability,
            source_name=self._source_name,
            duration_s=self._duration_s,
            position_s=position,
            loop_start_s=self._loop_start_s,
            loop_end_s=self._loop_end_s,
            trim_db=self._trim_db,
            count_in_beats=self._count_in_beats,
            count_in_bpm=self._count_in_bpm,
            route_detail=self._route_detail,
            error=self._error,
        )

    def _fail_locked(self, message: str) -> ReferenceTrackSnapshot:
        self._state = ReferenceTrackState.FAILED
        self._error = str(message or "Reference Track couldn't continue.").strip()
        snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def _require_open_locked(self) -> None:
        if self._state is ReferenceTrackState.CLOSED:
            raise ReferenceTrackError("Reference Track was already closed.")

    def _notify(self, snapshot: ReferenceTrackSnapshot) -> None:
        callback = self._on_snapshot
        if callback is None:
            return
        try:
            callback(snapshot)
        except Exception:  # noqa: BLE001 - observers cannot own transport
            pass


__all__ = [
    "REFERENCE_BLOCK_FRAMES",
    "REFERENCE_MAX_DECODE_FRAMES",
    "REFERENCE_QUEUE_BLOCKS",
    "REFERENCE_SAMPLE_RATE",
    "ReferenceAudioBridgeBackend",
    "ReferenceAudioBridgeSession",
    "ReferenceTrackCapability",
    "ReferenceTrackController",
    "ReferenceTrackDecoder",
    "ReferenceTrackError",
    "ReferenceTrackLaunchContext",
    "ReferenceTrackSnapshot",
    "ReferenceTrackSourceInfo",
    "ReferenceTrackState",
    "ReferenceTrackStream",
]

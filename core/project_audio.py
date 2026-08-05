"""Bounded audio primitives for standalone WebJam Studio projects.

This module deliberately separates worker-thread media decoding from the
PortAudio-facing data path:

* :class:`ProjectAudioDecoder` owns one descriptor-bound SoundFile reader and
  fills caller-provided float32 buffers at WebJam's 48 kHz project rate.
* :class:`PlaybackBlockRing` and :class:`CaptureBlockRing` own all NumPy
  storage up front. Their single-producer/single-consumer hot paths copy only
  between existing buffers, never perform file or log I/O, and never wait.
* :class:`GenerationGate` lets cancellable workers reject stale results before
  they publish into a newer project/audio generation.

The rings intentionally do not use locks. They are SPSC structures whose
producer and consumer must be fixed for their lifetime. Control-plane callers
must stop the audio stream before resetting or replacing a ring.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import operator
from pathlib import Path
import stat
import threading
from typing import Final

import numpy as np

from core.mp3_scan import (
    MP3_MAX_TRAILING_REPORT_FRAMES,
    Mp3Scan,
    Mp3ScanError,
    scan_mp3_descriptor,
)


PROJECT_AUDIO_SAMPLE_RATE: Final = 48_000
PROJECT_AUDIO_MAX_DECODE_FRAMES: Final = 4_096
PROJECT_AUDIO_MAX_SOURCE_RATE: Final = 384_000
PROJECT_AUDIO_MAX_SOURCE_CHANNELS: Final = 2
PROJECT_AUDIO_MAX_DURATION_SECONDS: Final = 24 * 60 * 60
PROJECT_AUDIO_MAX_OUTPUT_FRAMES: Final = (
    PROJECT_AUDIO_SAMPLE_RATE * PROJECT_AUDIO_MAX_DURATION_SECONDS
)
PROJECT_AUDIO_MAX_SOURCE_FRAMES: Final = (
    PROJECT_AUDIO_MAX_SOURCE_RATE * PROJECT_AUDIO_MAX_DURATION_SECONDS
)
PROJECT_AUDIO_MAX_MP3_FILE_BYTES: Final = (
    PROJECT_AUDIO_MAX_DURATION_SECONDS * 320_000 // 8
    + 4 * 1_024 * 1_024
    + 128
)

_FORMAT_BY_SUFFIX: Final[dict[str, frozenset[str]]] = {
    ".wav": frozenset({"WAV", "WAVEX", "RF64"}),
    ".wave": frozenset({"WAV", "WAVEX", "RF64"}),
    ".aif": frozenset({"AIFF"}),
    ".aiff": frozenset({"AIFF"}),
    ".flac": frozenset({"FLAC"}),
    ".ogg": frozenset({"OGG"}),
    ".mp3": frozenset({"MP3"}),
}


class ProjectAudioError(RuntimeError):
    """A musician-safe project-audio failure that never embeds a source path."""


class ProjectAudioCancelled(ProjectAudioError):
    """Raised when work belongs to an obsolete generation."""


def project_audio_mp3_available() -> bool:
    """Return runtime decoder truth without trusting an extension or manifest."""

    try:
        import soundfile as sf  # type: ignore

        return bool(
            "MP3" in sf.available_formats()
            and sf.check_format("MP3")
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ProjectAudioProbe:
    """Validated, path-free facts about one local audio source."""

    container: str
    subtype: str
    source_sample_rate: int
    channels: int
    source_frames: int
    output_frames: int

    @property
    def duration_s(self) -> float:
        return self.output_frames / PROJECT_AUDIO_SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class ProjectAudioGap:
    """One exact half-open interval omitted by a full capture ring."""

    start_frame: int
    frame_count: int
    generation: int
    channels: tuple[int, ...]
    reason: str = "capture_ring_overflow"

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


class GenerationToken:
    """One immutable lease issued by :class:`GenerationGate`."""

    __slots__ = ("_gate", "generation")

    def __init__(self, gate: "GenerationGate", generation: int) -> None:
        self._gate = gate
        self.generation = int(generation)

    @property
    def current(self) -> bool:
        return self._gate.is_current(self)

    def require_current(self) -> None:
        if not self.current:
            raise ProjectAudioCancelled("Project audio work was cancelled.")


class GenerationGate:
    """Thread-safe latest-generation authority for cancellable workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def issue(self) -> GenerationToken:
        with self._lock:
            self._generation += 1
            generation = self._generation
        return GenerationToken(self, generation)

    def cancel(self) -> int:
        """Invalidate every issued token and return the new generation."""

        with self._lock:
            self._generation += 1
            return self._generation

    def is_current(self, token: GenerationToken) -> bool:
        if not isinstance(token, GenerationToken) or token._gate is not self:
            return False
        with self._lock:
            return token.generation == self._generation


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", round(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", round(info.st_ctime * 1_000_000_000))),
    )


def _rounded_output_frames(source_frames: int, source_rate: int) -> int:
    # Integer half-up rounding avoids platform/libm variation and Python's
    # ties-to-even behavior for exact half-frame durations.
    return max(
        1,
        (
            int(source_frames) * PROJECT_AUDIO_SAMPLE_RATE
            + int(source_rate) // 2
        )
        // int(source_rate),
    )


def _reconcile_mp3_source_frames(
    reader: object,
    reported_frames: int,
    channels: int,
    scan: Mp3Scan,
) -> tuple[int, int]:
    """Reconcile libsndfile behavior with an authoritative physical scan."""

    sample = np.empty((1, channels), dtype=np.float32)
    tail = np.empty((576, channels), dtype=np.float32)

    def exact_seek(position: int) -> None:
        try:
            landed = reader.seek(position)  # type: ignore[attr-defined]
        except Exception:
            raise Mp3ScanError("MP3 decoder seek failed.") from None
        if int(landed) != position:
            raise Mp3ScanError("MP3 decoder seek was not exact.")

    def require_exact_boundary(boundary: int) -> None:
        tail_frames = min(576, boundary)
        tail_start = boundary - tail_frames
        exact_seek(tail_start)
        try:
            decoded_tail = reader.read(  # type: ignore[attr-defined]
                out=tail[:tail_frames]
            )
        except Exception:
            raise Mp3ScanError("MP3 decoder tail read failed.") from None
        if len(decoded_tail) != tail_frames:
            raise Mp3ScanError("MP3 decoder ended before the scanned boundary.")
        exact_seek(boundary)
        try:
            decoded_after = reader.read(  # type: ignore[attr-defined]
                out=sample
            )
        except Exception:
            raise Mp3ScanError("MP3 decoder EOF probe failed.") from None
        if len(decoded_after) != 0:
            raise Mp3ScanError("MP3 decoder continued beyond the scanned boundary.")

    try:
        gapless = scan.gapless
        if gapless is not None and reported_frames == gapless.content_frames:
            require_exact_boundary(gapless.content_frames)
            return 0, gapless.content_frames

        raw_frames = scan.raw_frames
        if (
            reported_frames < raw_frames
            or reported_frames - raw_frames
            > MP3_MAX_TRAILING_REPORT_FRAMES
        ):
            raise Mp3ScanError("MP3 decoder duration disagrees with the frame scan.")
        require_exact_boundary(raw_frames)
        if gapless is None:
            return 0, raw_frames
        return gapless.delay_frames, gapless.content_frames
    finally:
        try:
            reset = reader.seek(0)  # type: ignore[attr-defined]
        except Exception:
            raise Mp3ScanError("MP3 decoder could not reset after validation.") from None
        if int(reset) != 0:
            raise Mp3ScanError("MP3 decoder reset was not exact.")


def _close_quietly(
    reader: object,
    source_file: object,
    descriptor: int,
) -> None:
    if reader is not None:
        try:
            reader.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    if source_file is not None:
        try:
            source_file.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _bounded_int(
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


class ProjectAudioDecoder:
    """Descriptor-bound bounded decoder with deterministic 48-kHz output.

    ``read_into`` is a worker-thread API. It may seek/read the already-bound
    source and revalidate its pathname; it must never be called by an audio
    callback. The decoder serializes seek/read operations because libsndfile
    readers are stateful.
    """

    def __init__(self, path: str | Path) -> None:
        # Keep lexical path identity stable if a worker changes its current
        # directory later. ``abspath`` does not dereference a final symlink.
        candidate = Path(os.path.abspath(Path(path).expanduser()))
        suffix = candidate.suffix.casefold()
        expected_formats = _FORMAT_BY_SUFFIX.get(suffix)
        if expected_formats is None:
            raise ProjectAudioError(
                "Choose a local WAV, AIFF, FLAC, OGG, or supported MP3 file."
            )

        try:
            import soundfile as sf  # type: ignore
        except Exception:
            raise ProjectAudioError(
                "Project audio decoding is unavailable in this build."
            ) from None

        if suffix == ".mp3":
            if not project_audio_mp3_available():
                raise ProjectAudioError(
                    "MP3 decoding is unavailable in this build. Convert the "
                    "file to WAV, AIFF, FLAC, or OGG and try again."
                )

        descriptor = -1
        source_file = None
        reader = None
        mp3_scan: Mp3Scan | None = None
        try:
            before = candidate.lstat()
            if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
                raise OSError("source is not a regular file")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(candidate, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(
                before
            ):
                raise OSError("source changed during open")
            if suffix == ".mp3":
                mp3_scan = scan_mp3_descriptor(
                    descriptor,
                    expected_identity=_identity(opened),
                    max_source_frames=PROJECT_AUDIO_MAX_SOURCE_FRAMES,
                    max_duration_seconds=PROJECT_AUDIO_MAX_DURATION_SECONDS,
                    max_file_bytes=PROJECT_AUDIO_MAX_MP3_FILE_BYTES,
                )
            source_file = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
            reader = sf.SoundFile(source_file, mode="r")

            container = str(reader.format or "").upper()
            if container not in expected_formats:
                raise ValueError("container does not match extension")
            source_rate = int(reader.samplerate)
            channels = int(reader.channels)
            reported_source_frames = int(reader.frames)
            if not 1 <= source_rate <= PROJECT_AUDIO_MAX_SOURCE_RATE:
                raise ValueError("source sample rate is out of range")
            if not 1 <= channels <= PROJECT_AUDIO_MAX_SOURCE_CHANNELS:
                raise ValueError("source channel count is out of range")
            if not (
                1
                <= reported_source_frames
                <= PROJECT_AUDIO_MAX_SOURCE_FRAMES
            ):
                raise ValueError("source frame count is out of range")
            source_start_frame = 0
            source_frames = reported_source_frames
            if container == "MP3":
                if (
                    mp3_scan is None
                    or mp3_scan.source_sample_rate != source_rate
                    or mp3_scan.channels != channels
                ):
                    raise Mp3ScanError(
                        "MP3 decoder format disagrees with the frame scan."
                    )
                (
                    source_start_frame,
                    source_frames,
                ) = _reconcile_mp3_source_frames(
                    reader,
                    reported_source_frames,
                    channels,
                    mp3_scan,
                )
            output_frames = _rounded_output_frames(source_frames, source_rate)
            if output_frames > PROJECT_AUDIO_MAX_OUTPUT_FRAMES:
                raise ValueError("decoded duration is out of range")
            subtype = str(reader.subtype or "").upper()
            current_path = candidate.lstat()
            current_descriptor = os.fstat(source_file.fileno())
            if (
                stat.S_ISLNK(current_path.st_mode)
                or not stat.S_ISREG(current_path.st_mode)
                or _identity(current_path) != _identity(opened)
                or _identity(current_descriptor) != _identity(opened)
            ):
                raise OSError("source changed during decoder validation")
        except ProjectAudioError:
            raise
        except Mp3ScanError as exc:
            _close_quietly(reader, source_file, descriptor)
            # The scan reasons are fixed, bounded sentences that never carry
            # file paths; surfacing the exact one turns an opaque rejection
            # into an actionable message.
            reason = str(exc).strip().rstrip(".")
            raise ProjectAudioError(
                "WebJam couldn't validate that MP3 "
                f"({reason}). Re-export the song as a fresh MP3, or convert "
                "it to WAV, AIFF, or FLAC, and try again."
            ) from None
        except Exception:
            _close_quietly(reader, source_file, descriptor)
            raise ProjectAudioError(
                "WebJam couldn't safely read that audio file. Check that it is "
                "a valid one- or two-channel local audio file."
            ) from None

        self._path = candidate
        self._reader = reader
        self._source_file = source_file
        self._bound_identity = _identity(opened)
        self._source_rate = source_rate
        self._channels = channels
        self._source_start_frame = source_start_frame
        self._source_frames = source_frames
        self._output_frames = output_frames
        self._closed = False
        self._lock = threading.Lock()
        self.probe = ProjectAudioProbe(
            container=container,
            subtype=subtype,
            source_sample_rate=source_rate,
            channels=channels,
            source_frames=source_frames,
            output_frames=output_frames,
        )

        ratio = source_rate / PROJECT_AUDIO_SAMPLE_RATE
        maximum_source_window = min(
            source_frames,
            int(math.ceil((PROJECT_AUDIO_MAX_DECODE_FRAMES - 1) * ratio)) + 2,
        )
        self._source_scratch = np.empty(
            (max(1, maximum_source_window), channels),
            dtype=np.float32,
        )
        self._positions = np.empty(
            PROJECT_AUDIO_MAX_DECODE_FRAMES, dtype=np.float64
        )
        self._indices = np.arange(
            PROJECT_AUDIO_MAX_DECODE_FRAMES, dtype=np.float64
        )
        self._floors = np.empty(
            PROJECT_AUDIO_MAX_DECODE_FRAMES, dtype=np.float64
        )
        self._lower = np.empty(
            PROJECT_AUDIO_MAX_DECODE_FRAMES, dtype=np.int64
        )

    def __repr__(self) -> str:
        return (
            "ProjectAudioDecoder("
            f"container={self.probe.container!r}, "
            f"rate={self.probe.source_sample_rate}, "
            f"channels={self.probe.channels}, "
            f"frames={self.probe.output_frames})"
        )

    @property
    def output_frames(self) -> int:
        return self._output_frames

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _require_current(self) -> None:
        try:
            path_info = self._path.lstat()
            descriptor_info = os.fstat(self._source_file.fileno())
        except (OSError, ValueError):
            raise ProjectAudioError(
                "The imported audio changed or became unavailable. Relink it "
                "before continuing."
            ) from None
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or _identity(path_info) != self._bound_identity
            or _identity(descriptor_info) != self._bound_identity
        ):
            raise ProjectAudioError(
                "The imported audio changed or became unavailable. Relink it "
                "before continuing."
            )

    @staticmethod
    def _validate_output(output: np.ndarray) -> int:
        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != 2
            or not output.flags.c_contiguous
        ):
            raise ValueError(
                "output must be a C-contiguous float32 array with two channels"
            )
        frames = int(output.shape[0])
        if not 0 <= frames <= PROJECT_AUDIO_MAX_DECODE_FRAMES:
            raise ValueError("output exceeds the bounded decoder size")
        return frames

    def read_into(
        self,
        start_frame: int,
        output: np.ndarray,
        *,
        token: GenerationToken | None = None,
    ) -> int:
        """Fill ``output`` from one exact 48-kHz project-frame position.

        The unused tail is always zero. The return value is the number of
        decoded project frames, which can be shorter only at end-of-file.
        """

        start_frame = _bounded_int(
            start_frame,
            "start_frame",
            minimum=0,
        )
        requested = self._validate_output(output)
        output.fill(0.0)
        if token is not None:
            token.require_current()
        if requested == 0 or start_frame >= self._output_frames:
            return 0
        usable = min(requested, self._output_frames - start_frame)
        ratio = self._source_rate / PROJECT_AUDIO_SAMPLE_RATE

        # The reader and every scratch array are decoder-owned. Keep the whole
        # operation serialized so concurrent worker reads cannot overwrite
        # interpolation state after another read releases libsndfile.
        with self._lock:
            if self._closed:
                raise ProjectAudioError("The imported audio is already closed.")
            positions = self._positions[:usable]
            np.multiply(self._indices[:usable], ratio, out=positions)
            positions += start_frame * ratio
            positions += self._source_start_frame
            # Half-up duration rounding can leave the final 48-kHz frame a
            # fraction beyond the final source frame while upsampling. Holding
            # the final sample keeps the descriptor read bounded.
            source_end = self._source_start_frame + self._source_frames
            np.minimum(
                positions,
                float(source_end - 1),
                out=positions,
            )
            floors = self._floors[:usable]
            np.floor(positions, out=floors)
            lower = self._lower[:usable]
            np.copyto(lower, floors, casting="unsafe")
            first = int(lower[0])
            last = min(source_end - 1, int(lower[-1]) + 1)
            read_count = max(1, last - first + 1)
            source = self._source_scratch[:read_count]
            if token is not None:
                token.require_current()
            self._require_current()
            try:
                landed = self._reader.seek(first)
                if int(landed) != first:
                    raise OSError("decoder seek was not exact")
                decoded = self._reader.read(out=source)
            except Exception:
                raise ProjectAudioError(
                    "WebJam lost access to the imported audio. Relink it and "
                    "try again."
                ) from None
            if len(decoded) != read_count:
                raise ProjectAudioError(
                    "The imported audio ended before its validated duration."
                )
            self._require_current()
            if token is not None:
                token.require_current()

            # Interpolation is intentionally explicit: it avoids temporary
            # indexed arrays and gives identical mono/stereo behavior.
            for index in range(usable):
                absolute_left = int(lower[index])
                left = absolute_left - first
                right = min(left + 1, read_count - 1)
                fraction = float(positions[index] - absolute_left)
                inverse = 1.0 - fraction
                if self._channels == 1:
                    value = (
                        float(source[left, 0]) * inverse
                        + float(source[right, 0]) * fraction
                    )
                    output[index, 0] = value
                    output[index, 1] = value
                else:
                    output[index, 0] = (
                        float(source[left, 0]) * inverse
                        + float(source[right, 0]) * fraction
                    )
                    output[index, 1] = (
                        float(source[left, 1]) * inverse
                        + float(source[right, 1]) * fraction
                    )
            if not np.isfinite(output[:usable]).all():
                output.fill(0.0)
                raise ProjectAudioError(
                    "The imported audio contains invalid sample values."
                )
            if token is not None:
                try:
                    token.require_current()
                except ProjectAudioCancelled:
                    output.fill(0.0)
                    raise
        return usable

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._reader.close()
            except Exception:
                pass
            try:
                self._source_file.close()
            except Exception:
                pass

    def __enter__(self) -> "ProjectAudioDecoder":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class RealtimeBlockPool:
    """Fixed NumPy storage with stable per-slot array objects."""

    def __init__(self, capacity: int, block_frames: int, channels: int) -> None:
        self.capacity = _bounded_int(
            capacity,
            "capacity",
            minimum=1,
            maximum=4_096,
        )
        self.block_frames = _bounded_int(
            block_frames,
            "block_frames",
            minimum=1,
            maximum=65_536,
        )
        self.channels = _bounded_int(
            channels,
            "channels",
            minimum=1,
            maximum=64,
        )
        self._storage = np.zeros(
            (self.capacity, self.block_frames, self.channels),
            dtype=np.float32,
        )
        # NumPy indexing creates a view object. Retain those views once so a
        # hot-path buffer lookup returns the exact same object each time.
        self._buffers = tuple(self._storage[index] for index in range(self.capacity))

    @property
    def nbytes(self) -> int:
        return int(self._storage.nbytes)

    @property
    def buffer_identities(self) -> tuple[int, ...]:
        return tuple(id(item) for item in self._buffers)

    def buffer(self, index: int) -> np.ndarray:
        try:
            index = _bounded_int(
                index,
                "index",
                minimum=0,
                maximum=self.capacity - 1,
            )
        except ValueError:
            raise IndexError("block-pool index is out of range")
        return self._buffers[index]


class PlaybackBlockRing:
    """Preallocated SPSC audio-delivery ring for one output callback."""

    def __init__(self, capacity: int, block_frames: int, channels: int = 2) -> None:
        self.pool = RealtimeBlockPool(capacity, block_frames, channels)
        self.capacity = self.pool.capacity
        self.block_frames = self.pool.block_frames
        self.channels = self.pool.channels
        self._frame_counts = np.zeros(self.capacity, dtype=np.int32)
        self._start_frames = np.zeros(self.capacity, dtype=np.int64)
        self._generations = np.zeros(self.capacity, dtype=np.int64)
        self._head = 0
        self._tail = 0
        self._count = 0
        self._front_offset = 0
        self.requested_frames = 0
        self.delivered_frames = 0
        self.underrun_frames = 0
        self.overflow_blocks = 0
        self.overflow_frames = 0
        self.stale_frames = 0
        self.position_frame = 0

    @property
    def queued_blocks(self) -> int:
        return self._count

    def acquire_write_buffer(self) -> np.ndarray | None:
        """Return the stable next producer slot, or ``None`` when full."""

        if self._count >= self.capacity:
            return None
        return self.pool.buffer(self._head)

    def commit_write(
        self,
        frame_count: int,
        *,
        start_frame: int,
        generation: int,
    ) -> bool:
        """Publish the current producer slot after it has been filled."""

        frame_count = _bounded_int(
            frame_count,
            "frame_count",
            minimum=1,
            maximum=self.block_frames,
        )
        start_frame = _bounded_int(start_frame, "start_frame", minimum=0)
        generation = _bounded_int(generation, "generation", minimum=0)
        if self._count >= self.capacity:
            self.overflow_blocks += 1
            self.overflow_frames += frame_count
            return False
        slot = self._head
        self._frame_counts[slot] = frame_count
        self._start_frames[slot] = start_frame
        self._generations[slot] = generation
        self._head = (slot + 1) % self.capacity
        self._count += 1
        return True

    def try_push_from(
        self,
        source: np.ndarray,
        *,
        start_frame: int,
        generation: int,
    ) -> bool:
        if (
            not isinstance(source, np.ndarray)
            or source.dtype != np.float32
            or source.ndim != 2
            or source.shape[1] != self.channels
            or not 1 <= source.shape[0] <= self.block_frames
        ):
            raise ValueError("source does not fit the playback ring")
        start_frame = _bounded_int(start_frame, "start_frame", minimum=0)
        generation = _bounded_int(generation, "generation", minimum=0)
        target = self.acquire_write_buffer()
        if target is None:
            self.overflow_blocks += 1
            self.overflow_frames += int(source.shape[0])
            return False
        frame_count = int(source.shape[0])
        np.copyto(target[:frame_count], source)
        return self.commit_write(
            frame_count,
            start_frame=start_frame,
            generation=generation,
        )

    def pull_into(self, output: np.ndarray, *, generation: int) -> int:
        """Fill one callback-owned output buffer, returning delivered frames."""

        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != self.channels
            or output.shape[0] > self.block_frames
        ):
            raise ValueError("output does not fit the playback ring")
        generation = _bounded_int(generation, "generation", minimum=0)
        requested = int(output.shape[0])
        output.fill(0.0)
        self.requested_frames += requested
        written = 0
        while written < requested:
            if self._count <= 0:
                break
            slot = self._tail
            frame_count = int(self._frame_counts[slot])
            if int(self._generations[slot]) != generation:
                self.stale_frames += max(0, frame_count - self._front_offset)
                self._tail = (slot + 1) % self.capacity
                self._count -= 1
                self._front_offset = 0
                continue
            available = frame_count - self._front_offset
            if available <= 0:
                self._tail = (slot + 1) % self.capacity
                self._count -= 1
                self._front_offset = 0
                continue
            amount = min(requested - written, available)
            source = self.pool.buffer(slot)
            begin = self._front_offset
            np.copyto(
                output[written : written + amount],
                source[begin : begin + amount],
            )
            self.position_frame = int(self._start_frames[slot]) + begin + amount
            written += amount
            self._front_offset += amount
            if self._front_offset >= frame_count:
                self._tail = (slot + 1) % self.capacity
                self._count -= 1
                self._front_offset = 0
        self.delivered_frames += written
        self.underrun_frames += requested - written
        return written


class CaptureBlockRing:
    """Preallocated SPSC input ring with a fixed exact overflow ledger."""

    def __init__(
        self,
        capacity: int,
        block_frames: int,
        *,
        input_channels: int,
        channel_map: tuple[int, ...],
        gap_capacity: int = 1_024,
    ) -> None:
        input_channels = _bounded_int(
            input_channels,
            "input_channels",
            minimum=1,
            maximum=64,
        )
        mapping = tuple(channel_map)
        if (
            not mapping
            or len(mapping) > 64
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item < input_channels
                for item in mapping
            )
        ):
            raise ValueError("channel_map references an unavailable input")
        gap_capacity = _bounded_int(
            gap_capacity,
            "gap_capacity",
            minimum=1,
            maximum=65_536,
        )

        self.input_channels = input_channels
        self.channel_map = mapping
        self.pool = RealtimeBlockPool(capacity, block_frames, len(mapping))
        self.capacity = self.pool.capacity
        self.block_frames = self.pool.block_frames
        self.channels = self.pool.channels
        self._frame_counts = np.zeros(self.capacity, dtype=np.int32)
        self._start_frames = np.zeros(self.capacity, dtype=np.int64)
        self._generations = np.zeros(self.capacity, dtype=np.int64)
        self._head = 0
        self._tail = 0
        self._count = 0
        self.pushed_frames = 0
        self.popped_frames = 0
        self.overflow_blocks = 0
        self.overflow_frames = 0
        self.stale_frames = 0
        self.last_popped_start_frame = 0
        self.last_popped_generation = 0

        self._gap_capacity = gap_capacity
        self._gap_starts = np.zeros(self._gap_capacity, dtype=np.int64)
        self._gap_counts = np.zeros(self._gap_capacity, dtype=np.int64)
        self._gap_generations = np.zeros(self._gap_capacity, dtype=np.int64)
        self._gap_count = 0
        self.gap_ledger_overflowed = False
        self.unreported_gap_frames = 0
        self._gap_channels = tuple(range(self.channels))

    @property
    def queued_blocks(self) -> int:
        return self._count

    @property
    def gap_count(self) -> int:
        return self._gap_count

    def _record_overflow_gap(
        self, start_frame: int, frame_count: int, generation: int
    ) -> None:
        if self._gap_count:
            previous = self._gap_count - 1
            if (
                int(self._gap_generations[previous]) == int(generation)
                and int(self._gap_starts[previous])
                + int(self._gap_counts[previous])
                == int(start_frame)
            ):
                self._gap_counts[previous] += int(frame_count)
                return
        if self._gap_count >= self._gap_capacity:
            self.gap_ledger_overflowed = True
            self.unreported_gap_frames += int(frame_count)
            return
        slot = self._gap_count
        self._gap_starts[slot] = int(start_frame)
        self._gap_counts[slot] = int(frame_count)
        self._gap_generations[slot] = int(generation)
        self._gap_count += 1

    def push_from(
        self,
        input_data: np.ndarray,
        *,
        start_frame: int,
        generation: int,
    ) -> bool:
        """Copy one callback input block into a free preallocated slot."""

        if (
            not isinstance(input_data, np.ndarray)
            or input_data.dtype != np.float32
            or input_data.ndim != 2
            or input_data.shape[1] < self.input_channels
            or not 1 <= input_data.shape[0] <= self.block_frames
        ):
            raise ValueError("input_data does not fit the capture ring")
        start_frame = _bounded_int(start_frame, "start_frame", minimum=0)
        generation = _bounded_int(generation, "generation", minimum=0)
        frame_count = int(input_data.shape[0])
        if self._count >= self.capacity:
            self.overflow_blocks += 1
            self.overflow_frames += frame_count
            self._record_overflow_gap(start_frame, frame_count, generation)
            return False
        slot = self._head
        target = self.pool.buffer(slot)
        for destination, source in enumerate(self.channel_map):
            np.copyto(
                target[:frame_count, destination],
                input_data[:frame_count, source],
            )
        self._frame_counts[slot] = frame_count
        self._start_frames[slot] = int(start_frame)
        self._generations[slot] = int(generation)
        self._head = (slot + 1) % self.capacity
        self._count += 1
        self.pushed_frames += frame_count
        return True

    def pop_into(
        self,
        output: np.ndarray,
        *,
        generation: int | None = None,
    ) -> int:
        """Copy the next complete capture block into writer-owned storage."""

        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != self.channels
            or output.shape[0] < self.block_frames
        ):
            raise ValueError("output cannot hold one capture block")
        if generation is not None:
            generation = _bounded_int(generation, "generation", minimum=0)
        while self._count > 0:
            slot = self._tail
            frame_count = int(self._frame_counts[slot])
            slot_generation = int(self._generations[slot])
            if generation is not None and slot_generation != generation:
                self._tail = (slot + 1) % self.capacity
                self._count -= 1
                self.stale_frames += frame_count
                continue
            np.copyto(output[:frame_count], self.pool.buffer(slot)[:frame_count])
            # Do not publish the slot as free until the copy completes.
            # ``numpy.copyto`` may release the GIL; advancing first would let
            # the producer overwrite the slot while the writer consumes it.
            self._tail = (slot + 1) % self.capacity
            self._count -= 1
            self.last_popped_start_frame = int(self._start_frames[slot])
            self.last_popped_generation = slot_generation
            self.popped_frames += frame_count
            return frame_count
        return 0

    def gaps(self) -> tuple[ProjectAudioGap, ...]:
        """Materialize the bounded overflow ledger on a control/writer thread."""

        return tuple(
            ProjectAudioGap(
                start_frame=int(self._gap_starts[index]),
                frame_count=int(self._gap_counts[index]),
                generation=int(self._gap_generations[index]),
                channels=self._gap_channels,
            )
            for index in range(self._gap_count)
        )


__all__ = [
    "CaptureBlockRing",
    "GenerationGate",
    "GenerationToken",
    "PROJECT_AUDIO_MAX_DECODE_FRAMES",
    "PROJECT_AUDIO_MAX_DURATION_SECONDS",
    "PROJECT_AUDIO_MAX_OUTPUT_FRAMES",
    "PROJECT_AUDIO_MAX_SOURCE_CHANNELS",
    "PROJECT_AUDIO_MAX_SOURCE_FRAMES",
    "PROJECT_AUDIO_MAX_SOURCE_RATE",
    "PROJECT_AUDIO_SAMPLE_RATE",
    "PlaybackBlockRing",
    "ProjectAudioCancelled",
    "ProjectAudioDecoder",
    "ProjectAudioError",
    "ProjectAudioGap",
    "ProjectAudioProbe",
    "RealtimeBlockPool",
    "project_audio_mp3_available",
]

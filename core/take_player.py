"""
TakePlayer — multitrack playback engine for the Take Deck.

Plays back a recorded take: each track is a streaming file reader, mixed on
a numpy bus with per-track gain / mute / solo and per-track start offsets,
into a stereo output stream. This is the "review the jam" heart of Studio:
it deliberately keeps every mix move non-destructive so the verified source
files remain ready for a DAW handoff.

Testability
-----------
The audio device is behind the ``OutputSink`` protocol. Production uses
``SoundDeviceSink`` (sounddevice, imported lazily); tests use a synchronous
sink that pulls a fixed number of blocks and captures them — so the entire
mixing/transport/gain/solo path is verified headless in CI without any
audio hardware.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

import numpy as np

_logger = logging.getLogger("webjam.take_player")

DEFAULT_SAMPLERATE = 48000
DEFAULT_BLOCKSIZE = 1024
STUDIO_PLAYBACK_TARGET_BUFFER_MS = 100
STUDIO_PLAYBACK_PRIME_BUFFER_MS = 60
STUDIO_PLAYBACK_MIN_PRODUCER_BLOCK_MS = 20
STUDIO_PLAYBACK_MAX_QUEUE_BLOCKS = 128
STUDIO_PLAYBACK_MAX_BLOCK_FRAMES = 4096
STUDIO_PLAYBACK_PRIME_TIMEOUT_S = 0.25
STUDIO_PLAYBACK_COMMAND_TIMEOUT_S = 0.5
STUDIO_PLAYBACK_STOP_TIMEOUT_S = 2.0
STUDIO_PLAYBACK_NOTIFICATION_CAPACITY = 128
STUDIO_PLAYBACK_TERMINAL_NOTIFICATION_CAPACITY = 8


class PlaybackError(RuntimeError):
    """Raised when Take Deck playback cannot start safely."""


class StudioPlaybackSourceError(PlaybackError):
    """Raised when Studio cannot validate or open arrangement source media."""


class PlaybackDeviceError(PlaybackError):
    """Raised when the selected playback output cannot be started."""


class _StudioPlaybackCommandTimeout(PlaybackError):
    """Internal typed failure for a render worker that stopped responding."""


@dataclass
class StudioPlaybackPreparation:
    """Validated, descriptor-bound Studio stream awaiting UI-thread install."""

    renderer: object
    stream: object
    pipeline: object | None = None
    position_frame: int = 0
    mix: object | None = None
    _installed: bool = field(default=False, init=False, repr=False)

    def take_resources(self) -> tuple[object, object | None]:
        if self._installed:
            raise PlaybackError("Studio playback preparation was already consumed.")
        self._installed = True
        return self.stream, self.pipeline

    def close(self) -> None:
        if self._installed:
            return
        self._installed = True
        try:
            if self.pipeline is not None:
                closed = self.pipeline.stop()  # type: ignore[attr-defined]
                if not closed:
                    _logger.error(
                        "Studio preparation producer did not stop within its deadline."
                    )
            else:
                self.stream.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


@dataclass(frozen=True)
class _StudioMixSnapshot:
    """Complete stream-local mix state carried by one producer generation."""

    tracks: tuple[tuple[str, float, float, float, bool, bool], ...]
    master: object | None


@dataclass
class _StudioPipelineCommand:
    """Latest-wins control command consumed only by the render worker."""

    generation: int
    epoch: int
    position_frame: int
    wrapped: bool
    mix: _StudioMixSnapshot
    checkpoint: bool
    verify_checksum: bool
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    error: PlaybackError | None = field(default=None, repr=False)
    cancelled: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class _StudioAudioChunk:
    """One bounded, fully in-memory producer result."""

    generation: int
    epoch: int
    audio: np.ndarray
    track_audio: dict[str, np.ndarray]
    master_meter_audio: np.ndarray
    positions_after: np.ndarray
    wrapped_after: np.ndarray
    finish_offset: int | None = None

    @property
    def nbytes(self) -> int:
        return int(
            self.audio.nbytes
            + self.master_meter_audio.nbytes
            + self.positions_after.nbytes
            + self.wrapped_after.nbytes
            + sum(block.nbytes for block in self.track_audio.values())
        )


@dataclass(frozen=True)
class _StudioTerminalEvent:
    """A generation-tagged terminal condition ordered after queued audio."""

    generation: int
    epoch: int
    error: PlaybackError | None = None
    finished: bool = False


@dataclass(frozen=True)
class _StudioConsumedBlock:
    """Memory-only data returned to a device callback without waiting."""

    generation: int
    epoch: int
    audio: np.ndarray
    track_audio: dict[str, np.ndarray]
    master_meter_audio: np.ndarray
    position_frame: int | None
    wrapped: bool
    consumed_frames: int
    error: PlaybackError | None = None
    finished: bool = False


@dataclass(frozen=True)
class _StudioMeterNotification:
    """One consumed-block snapshot handed off without calling UI code."""

    pipeline: _StudioPlaybackPipeline = field(repr=False, compare=False)
    generation: int
    epoch: int
    position_frame: int
    wrapped: bool
    levels: dict[int, float]
    stereo_levels: dict[int, tuple[float, float, bool]]
    master_level: tuple[float, float, bool]


@dataclass(frozen=True)
class _StudioTerminalNotification:
    """One terminal event kept outside the lossy bounded meter mailbox."""

    pipeline: _StudioPlaybackPipeline = field(repr=False, compare=False)
    generation: int
    epoch: int
    error: PlaybackError | None = None
    finished: bool = False


class _StudioPlaybackPipeline:
    """Single-owner Studio renderer with a bounded SPSC delivery queue.

    Only ``_run`` calls the descriptor-bound ``StudioRenderStream``. The
    PortAudio-facing consumer takes the state lock with ``blocking=False``;
    contention and underruns therefore produce silence instead of waiting or
    falling through to source I/O.
    """

    def __init__(
        self,
        stream: object,
        *,
        block_frames: int,
        callback_frames: int,
        capacity_blocks: int,
        track_ids: tuple[str, ...],
        cycle_range: tuple[int, int] | None,
        smooth_cycle: Callable[..., np.ndarray],
    ) -> None:
        self._stream = stream
        self.block_frames = max(
            1,
            min(int(block_frames), STUDIO_PLAYBACK_MAX_BLOCK_FRAMES),
        )
        self.callback_frames = max(1, int(callback_frames))
        self.capacity_blocks = max(1, int(capacity_blocks))
        self._track_ids = track_ids
        self._cycle_range = cycle_range
        self._smooth_cycle = smooth_cycle
        sample_rate = max(1, int(getattr(stream, "sample_rate", DEFAULT_SAMPLERATE)))
        requested_prime = max(
            self.block_frames,
            self.callback_frames,
            int(round(sample_rate * STUDIO_PLAYBACK_PRIME_BUFFER_MS / 1000.0)),
        )
        self.prime_frames = min(self.capacity_frames, requested_prime)
        self._condition = threading.Condition(threading.Lock())
        self._items: deque[_StudioAudioChunk | _StudioTerminalEvent] = deque()
        self._front_offset = 0
        self._generation = 0
        self._epoch = 0
        self._consumer_position = 0
        self._consumer_wrapped = False
        self._pending: _StudioPipelineCommand | None = None
        self._active: _StudioPipelineCommand | None = None
        self._prime_only = False
        self._primed_paused = False
        self._stop_requested = False
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="WebJam Studio audio producer",
            daemon=True,
        )

    @property
    def worker_thread_id(self) -> int | None:
        return self._thread.ident

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def matches_realtime_snapshot(self, generation: int, epoch: int) -> bool:
        """Return a lock-free hint; the control-thread drain revalidates it."""

        return (
            not self._stop_requested
            and not self._closed
            and int(generation) == self._generation
            and int(epoch) == self._epoch
        )

    @property
    def buffered_blocks(self) -> int:
        with self._condition:
            return sum(isinstance(item, _StudioAudioChunk) for item in self._items)

    @property
    def capacity_frames(self) -> int:
        return self.block_frames * self.capacity_blocks

    @property
    def buffered_frames(self) -> int:
        with self._condition:
            total = sum(
                len(item.audio)
                for item in self._items
                if isinstance(item, _StudioAudioChunk)
            )
            if self._items and isinstance(self._items[0], _StudioAudioChunk):
                total -= self._front_offset
            return total

    @property
    def capacity_bytes(self) -> int:
        bytes_per_frame = 8 + 8 + 8 + 1 + 8 * len(self._track_ids)
        return self.capacity_frames * bytes_per_frame

    @property
    def buffered_bytes(self) -> int:
        with self._condition:
            return sum(
                item.nbytes
                for item in self._items
                if isinstance(item, _StudioAudioChunk)
            )

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    def transport_state(self) -> tuple[int, bool]:
        with self._condition:
            return self._consumer_position, self._consumer_wrapped

    def start(
        self,
        *,
        epoch: int,
        position_frame: int,
        wrapped: bool,
        mix: _StudioMixSnapshot,
        prime_only: bool = False,
    ) -> tuple[int, _StudioPipelineCommand]:
        self._prime_only = bool(prime_only)
        generation, command = self.reconfigure(
            epoch=epoch,
            position_frame=position_frame,
            wrapped=wrapped,
            mix=mix,
            checkpoint=True,
            verify_checksum=False,
        )
        self._thread.start()
        return generation, command

    def reconfigure(
        self,
        *,
        epoch: int,
        position_frame: int,
        wrapped: bool,
        mix: _StudioMixSnapshot,
        checkpoint: bool,
        verify_checksum: bool = False,
    ) -> tuple[int, _StudioPipelineCommand]:
        with self._condition:
            if self._stop_requested or self._closed:
                raise PlaybackError("Studio playback producer is closed.")
            self._generation += 1
            generation = self._generation
            command = _StudioPipelineCommand(
                generation=generation,
                epoch=int(epoch),
                position_frame=int(position_frame),
                wrapped=bool(wrapped),
                mix=mix,
                checkpoint=bool(checkpoint),
                verify_checksum=bool(verify_checksum),
            )
            if self._pending is not None:
                self._pending.cancelled = True
                self._pending.done.set()
            self._pending = command
            self._epoch = int(epoch)
            self._consumer_position = int(position_frame)
            self._consumer_wrapped = bool(wrapped)
            self._items.clear()
            self._front_offset = 0
            self._primed_paused = False
            self._condition.notify_all()
            return generation, command

    def promote_primed(
        self,
        *,
        epoch: int,
        position_frame: int,
        wrapped: bool,
        mix: _StudioMixSnapshot,
    ) -> int | None:
        """Adopt a background-prefilled generation without discarding audio."""

        with self._condition:
            active = self._active
            if (
                not self._primed_paused
                or active is None
                or active.generation != self._generation
                or self._pending is not None
                or self._consumer_position != int(position_frame)
                or self._consumer_wrapped != bool(wrapped)
                or active.mix != mix
                or not self._items
            ):
                return None
            active.epoch = int(epoch)
            self._epoch = int(epoch)
            self._items = deque(replace(item, epoch=int(epoch)) for item in self._items)
            self._primed_paused = False
            self._condition.notify_all()
            return self._generation

    def wait_for_command(
        self,
        command: _StudioPipelineCommand,
        *,
        timeout: float = STUDIO_PLAYBACK_COMMAND_TIMEOUT_S,
        fallback_position_frame: int | None = None,
        fallback_wrapped: bool | None = None,
    ) -> None:
        """Wait a bounded time and fail closed if the render worker stalls."""

        if not command.done.wait(max(0.0, float(timeout))):
            timeout_error: PlaybackError | None = None
            with self._condition:
                # Resolve the boundary race in favor of a command that really
                # completed before the condition was acquired.
                if not command.done.is_set():
                    if command.generation == self._generation:
                        timeout_error = _StudioPlaybackCommandTimeout(
                            "Studio playback producer did not respond within its "
                            "safety deadline."
                        )
                        self._generation += 1
                        self._stop_requested = True
                        self._items.clear()
                        self._front_offset = 0
                        if fallback_position_frame is not None:
                            self._consumer_position = int(fallback_position_frame)
                        if fallback_wrapped is not None:
                            self._consumer_wrapped = bool(fallback_wrapped)
                        if self._pending is command:
                            self._pending = None
                        command.error = timeout_error
                        command.done.set()
                        self._condition.notify_all()
                    else:
                        command.cancelled = True
                        command.done.set()
                        self._condition.notify_all()
            if timeout_error is not None:
                raise timeout_error
        if command.error is not None:
            raise command.error
        if command.cancelled:
            raise PlaybackError("Studio playback command was superseded.")

    def wait_until_ready(self, generation: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while not self._stop_requested and generation == self._generation:
                if any(item.generation == generation for item in self._items):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return False

    def wait_until_primed(self, generation: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while not self._stop_requested and generation == self._generation:
                if self._primed_paused:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return False

    def ready_error(self, generation: int) -> PlaybackError | None:
        with self._condition:
            for item in self._items:
                if item.generation != generation:
                    continue
                if isinstance(item, _StudioTerminalEvent) and item.error is not None:
                    return item.error
            return None

    def stop(self, timeout: float = STUDIO_PLAYBACK_STOP_TIMEOUT_S) -> bool:
        with self._condition:
            if self._stop_requested:
                thread = self._thread
            else:
                self._stop_requested = True
                if self._pending is not None:
                    self._pending.cancelled = True
                    self._pending.done.set()
                    self._pending = None
                self._items.clear()
                self._front_offset = 0
                self._condition.notify_all()
                thread = self._thread
        if thread.ident is None:
            try:
                self._stream.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            with self._condition:
                self._closed = True
                self._condition.notify_all()
            return True
        if thread.ident is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        return not thread.is_alive()

    def consume(self, frames: int, epoch: int) -> _StudioConsumedBlock:
        """Consume ready memory, returning immediately on contention/underrun."""

        requested = max(0, int(frames))
        silence = np.zeros((requested, 2), dtype=np.float32)
        if requested == 0 or not self._condition.acquire(blocking=False):
            return _StudioConsumedBlock(
                generation=self._generation,
                epoch=int(epoch),
                audio=silence,
                track_audio={},
                master_meter_audio=np.zeros((0, 2), dtype=np.float32),
                position_frame=None,
                wrapped=False,
                consumed_frames=0,
            )
        try:
            generation = self._generation
            expected_epoch = int(epoch)
            if self._stop_requested or expected_epoch != self._epoch or self._closed:
                return _StudioConsumedBlock(
                    generation=generation,
                    epoch=expected_epoch,
                    audio=silence,
                    track_audio={},
                    master_meter_audio=np.zeros((0, 2), dtype=np.float32),
                    position_frame=None,
                    wrapped=False,
                    consumed_frames=0,
                )

            output_offset = 0
            timeline_consumed = 0
            position: int | None = None
            wrapped = self._consumer_wrapped
            finished = False
            error: PlaybackError | None = None
            track_parts: dict[str, list[np.ndarray]] = {
                track_id: [] for track_id in self._track_ids
            }
            master_parts: list[np.ndarray] = []
            while output_offset < requested and self._items:
                item = self._items[0]
                if item.generation != generation or item.epoch != expected_epoch:
                    self._items.popleft()
                    self._front_offset = 0
                    continue
                if isinstance(item, _StudioTerminalEvent):
                    self._items.popleft()
                    self._front_offset = 0
                    error = item.error
                    finished = item.finished
                    break

                start = self._front_offset
                available = len(item.audio) - start
                take = min(requested - output_offset, available)
                if take <= 0:
                    self._items.popleft()
                    self._front_offset = 0
                    continue
                end = start + take
                silence[output_offset : output_offset + take] = item.audio[start:end]
                meter_end = end
                if item.finish_offset is not None:
                    meter_end = min(meter_end, item.finish_offset)
                if meter_end > start:
                    for track_id, parts in track_parts.items():
                        block = item.track_audio.get(track_id)
                        if block is not None:
                            parts.append(block[start:meter_end])
                    master_parts.append(item.master_meter_audio[start:meter_end])
                    position = int(item.positions_after[meter_end - 1])
                    wrapped = bool(item.wrapped_after[meter_end - 1])
                    timeline_consumed += meter_end - start
                output_offset += take
                self._front_offset = end
                if item.finish_offset is not None and start < item.finish_offset <= end:
                    finished = True
                    self._items.popleft()
                    self._front_offset = 0
                    break
                if self._front_offset >= len(item.audio):
                    self._items.popleft()
                    self._front_offset = 0

            if position is not None:
                self._consumer_position = position
                self._consumer_wrapped = wrapped
            self._condition.notify_all()
            track_audio = {
                track_id: (
                    np.concatenate(parts)
                    if parts
                    else np.zeros((0, 2), dtype=np.float32)
                )
                for track_id, parts in track_parts.items()
            }
            master_audio = (
                np.concatenate(master_parts)
                if master_parts
                else np.zeros((0, 2), dtype=np.float32)
            )
            return _StudioConsumedBlock(
                generation=generation,
                epoch=expected_epoch,
                audio=silence,
                track_audio=track_audio,
                master_meter_audio=master_audio,
                position_frame=position,
                wrapped=wrapped,
                consumed_frames=timeline_consumed,
                error=error,
                finished=finished,
            )
        finally:
            self._condition.release()

    @staticmethod
    def _source_error(exc: Exception, *, seeking: bool) -> StudioPlaybackSourceError:
        if seeking:
            message = "Studio arrangement source media changed while seeking."
        else:
            message = "Studio arrangement source media could not be read."
        error = StudioPlaybackSourceError(message)
        error.__cause__ = exc
        return error

    def _apply_command(self, command: _StudioPipelineCommand) -> None:
        if command.checkpoint:
            self._stream.checkpoint(  # type: ignore[attr-defined]
                command.position_frame,
                verify_checksum=command.verify_checksum,
            )
        else:
            self._stream.seek(command.position_frame)  # type: ignore[attr-defined]
        for track_id, trim, gain, pan, muted, solo in command.mix.tracks:
            self._stream.set_track_mix(  # type: ignore[attr-defined]
                track_id,
                trim_gain=trim,
                fader_gain=gain,
                pan=pan,
                muted=muted,
                solo=solo,
            )
        if command.mix.master is not None:
            self._stream.set_master(command.mix.master)  # type: ignore[attr-defined]

    def _append_rendered(
        self,
        block_parts: list[np.ndarray],
        track_parts: dict[str, list[np.ndarray]],
        position_parts: list[np.ndarray],
        wrapped_parts: list[np.ndarray],
        rendered: np.ndarray,
        contributions: dict[str, np.ndarray],
        *,
        start_frame: int,
        wrapped: bool,
        repetitions: int = 1,
    ) -> tuple[int, bool]:
        count = len(rendered)
        if count <= 0 or repetitions <= 0:
            return 0, wrapped
        cycle = self._cycle_range
        combined = rendered if repetitions == 1 else np.tile(rendered, (repetitions, 1))
        gains: np.ndarray | None = None
        if cycle is not None:
            gains = self._smooth_cycle(
                start_frame,
                count,
                cycle,
                wrapped=wrapped,
            )
            if repetitions > 1:
                wrapped_gains = self._smooth_cycle(
                    start_frame,
                    count,
                    cycle,
                    wrapped=True,
                )
                gains = np.concatenate((gains, np.tile(wrapped_gains, repetitions - 1)))
        apply_smoothing = gains is not None and bool(np.any(gains != 1.0))
        if apply_smoothing:
            combined = combined.copy()
            combined *= gains[:, np.newaxis]
        block_parts.append(combined)
        for track_id, parts in track_parts.items():
            contribution = contributions.get(track_id)
            if contribution is None or len(contribution) != count:
                contribution = np.zeros((count, 2), dtype=np.float32)
            combined_contribution = (
                contribution
                if repetitions == 1
                else np.tile(contribution, (repetitions, 1))
            )
            if apply_smoothing:
                combined_contribution = combined_contribution.copy()
                combined_contribution *= gains[:, np.newaxis]
            parts.append(combined_contribution)

        one_positions = np.arange(
            start_frame + 1,
            start_frame + count + 1,
            dtype=np.int64,
        )
        one_wrapped = np.full(count, wrapped, dtype=np.bool_)
        wrapped_after = wrapped
        if cycle is not None and count:
            cycle_start, cycle_end = cycle
            if int(one_positions[-1]) >= cycle_end:
                one_positions[-1] = cycle_start
                one_wrapped[-1] = True
                wrapped_after = True
        if repetitions == 1:
            position_parts.append(one_positions)
            wrapped_parts.append(one_wrapped)
        else:
            wrapped_positions = one_positions.copy()
            wrapped_flags = np.ones(count, dtype=np.bool_)
            position_parts.append(
                np.concatenate(
                    (one_positions, np.tile(wrapped_positions, repetitions - 1))
                )
            )
            wrapped_parts.append(
                np.concatenate((one_wrapped, np.tile(wrapped_flags, repetitions - 1)))
            )
            wrapped_after = True
        return count * repetitions, wrapped_after

    def _render_chunk(
        self,
        command: _StudioPipelineCommand,
        *,
        wrapped: bool,
    ) -> tuple[_StudioAudioChunk | _StudioTerminalEvent, bool, bool]:
        from core.studio_renderer import studio_delivery_block

        stream = self._stream
        cycle = self._cycle_range
        stream.checkpoint(  # type: ignore[attr-defined]
            int(stream.position_frame),  # type: ignore[attr-defined]
            verify_checksum=False,
        )
        if cycle is None and stream.position_frame >= stream.end_frame:  # type: ignore[attr-defined]
            return (
                _StudioTerminalEvent(
                    generation=command.generation,
                    epoch=command.epoch,
                    finished=True,
                ),
                wrapped,
                True,
            )
        remaining = self.block_frames
        block_parts: list[np.ndarray] = []
        track_parts: dict[str, list[np.ndarray]] = {
            track_id: [] for track_id in self._track_ids
        }
        position_parts: list[np.ndarray] = []
        wrapped_parts: list[np.ndarray] = []

        while remaining > 0:
            position = int(stream.position_frame)  # type: ignore[attr-defined]
            if cycle is not None and position >= cycle[1]:
                stream.seek(cycle[0])  # type: ignore[attr-defined]
                position = cycle[0]
                wrapped = True

            if cycle is not None:
                cycle_start, cycle_end = cycle
                cycle_frames = cycle_end - cycle_start
                if position == cycle_start and remaining >= cycle_frames:
                    rendered, contributions = stream.read_with_tracks(  # type: ignore[attr-defined]
                        cycle_frames
                    )
                    if len(rendered) != cycle_frames:
                        break
                    repetitions = remaining // cycle_frames
                    consumed, wrapped = self._append_rendered(
                        block_parts,
                        track_parts,
                        position_parts,
                        wrapped_parts,
                        rendered,
                        contributions,
                        start_frame=position,
                        wrapped=wrapped,
                        repetitions=repetitions,
                    )
                    remaining -= consumed
                    stream.seek(cycle_start)  # type: ignore[attr-defined]
                    wrapped = True
                    continue
                request = min(remaining, cycle_end - position)
            else:
                request = remaining
            if request <= 0:
                break
            rendered, contributions = stream.read_with_tracks(request)  # type: ignore[attr-defined]
            consumed, wrapped = self._append_rendered(
                block_parts,
                track_parts,
                position_parts,
                wrapped_parts,
                rendered,
                contributions,
                start_frame=position,
                wrapped=wrapped,
            )
            if consumed <= 0:
                break
            remaining -= consumed
            if consumed < request:
                break
            if cycle is not None and stream.position_frame >= cycle[1]:  # type: ignore[attr-defined]
                stream.seek(cycle[0])  # type: ignore[attr-defined]
                wrapped = True

        rendered_count = self.block_frames - remaining
        stream.checkpoint(  # type: ignore[attr-defined]
            int(stream.position_frame),  # type: ignore[attr-defined]
            verify_checksum=False,
        )
        if rendered_count <= 0:
            return (
                _StudioTerminalEvent(
                    generation=command.generation,
                    epoch=command.epoch,
                    finished=True,
                ),
                wrapped,
                True,
            )
        raw = np.concatenate(block_parts)
        tracks = {
            track_id: np.concatenate(parts)
            if parts
            else np.zeros((rendered_count, 2), dtype=np.float32)
            for track_id, parts in track_parts.items()
        }
        positions = np.concatenate(position_parts)
        wrapped_flags = np.concatenate(wrapped_parts)
        finished = cycle is None and int(stream.position_frame) >= int(stream.end_frame)  # type: ignore[attr-defined]
        if rendered_count < self.block_frames:
            pad = self.block_frames - rendered_count
            raw = np.pad(raw, ((0, pad), (0, 0)))
            tracks = {
                track_id: np.pad(block, ((0, pad), (0, 0)))
                for track_id, block in tracks.items()
            }
            final_position = int(stream.position_frame)  # type: ignore[attr-defined]
            positions = np.pad(positions, (0, pad), constant_values=final_position)
            wrapped_flags = np.pad(
                wrapped_flags,
                (0, pad),
                constant_values=wrapped,
            )
        master_meter = raw
        delivered, _clipped = studio_delivery_block(raw)
        return (
            _StudioAudioChunk(
                generation=command.generation,
                epoch=command.epoch,
                audio=delivered,
                track_audio=tracks,
                master_meter_audio=master_meter,
                positions_after=positions,
                wrapped_after=wrapped_flags,
                finish_offset=rendered_count if finished else None,
            ),
            wrapped,
            finished,
        )

    def _run(self) -> None:
        active: _StudioPipelineCommand | None = None
        wrapped = False
        terminal = False
        try:
            while True:
                command: _StudioPipelineCommand | None = None
                with self._condition:
                    while True:
                        if self._stop_requested:
                            return
                        if self._pending is not None:
                            command = self._pending
                            self._pending = None
                            break
                        if (
                            active is not None
                            and not terminal
                            and not self._primed_paused
                            and len(self._items) < self.capacity_blocks
                        ):
                            break
                        self._condition.wait()
                if command is not None:
                    try:
                        self._apply_command(command)
                    except Exception as exc:  # noqa: BLE001
                        command.error = self._source_error(
                            exc,
                            seeking=command.checkpoint,
                        )
                    with self._condition:
                        current = (
                            not self._stop_requested
                            and command.generation == self._generation
                        )
                        if current and command.error is None:
                            active = command
                            self._active = command
                            wrapped = command.wrapped
                            terminal = False
                            self._primed_paused = False
                        elif current and command.error is not None:
                            self._items.append(
                                _StudioTerminalEvent(
                                    generation=command.generation,
                                    epoch=command.epoch,
                                    error=command.error,
                                )
                            )
                            active = command
                            self._active = command
                            terminal = True
                        else:
                            command.cancelled = True
                        command.done.set()
                        self._condition.notify_all()
                    continue

                assert active is not None
                try:
                    item, next_wrapped, item_terminal = self._render_chunk(
                        active,
                        wrapped=wrapped,
                    )
                except Exception as exc:  # noqa: BLE001
                    item = _StudioTerminalEvent(
                        generation=active.generation,
                        epoch=active.epoch,
                        error=self._source_error(exc, seeking=False),
                    )
                    next_wrapped = wrapped
                    item_terminal = True
                with self._condition:
                    if (
                        not self._stop_requested
                        and active.generation == self._generation
                        and len(self._items) < self.capacity_blocks
                    ):
                        self._items.append(item)
                        wrapped = next_wrapped
                        terminal = item_terminal
                        primed_frames = sum(
                            len(queued.audio)
                            for queued in self._items
                            if isinstance(queued, _StudioAudioChunk)
                        )
                        if self._prime_only and (
                            terminal or primed_frames >= self.prime_frames
                        ):
                            self._prime_only = False
                            self._primed_paused = True
                    self._condition.notify_all()
        finally:
            try:
                self._stream.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            with self._condition:
                self._closed = True
                self._condition.notify_all()


@dataclass
class TrackSegmentState:
    """One immutable media interval rendered onto the playback timeline."""

    path: Path
    project_start_frame: int
    frame_count: int
    samplerate: int
    channels: int = 1
    gaps: tuple[tuple[int, int, tuple[int, ...], str], ...] = ()
    _reader: object = field(default=None, repr=False)


@dataclass
class TrackState:
    """Per-track mixer state (mutated live from the UI)."""

    channel_id: int
    name: str
    path: Path
    track_id: str = ""
    offset_s: float = 0.0
    source: str = "jamulus_server"
    gain: float = 1.0  # 0..~1.27 (fader/100)
    trim_gain: float = 1.0
    pan: float = 0.0  # -1.0 left .. 0 centre .. +1.0 right
    muted: bool = False
    solo: bool = False
    level: float = 0.0  # last block RMS, 0..1 (for meters)
    _reader: object = field(default=None, repr=False)
    _eof: bool = False
    segments: tuple[TrackSegmentState, ...] = field(default_factory=tuple, repr=False)
    drift_ppm: float = 0.0


class OutputSink(Protocol):
    """Something that consumes mixed audio blocks."""

    def start(
        self, samplerate: int, blocksize: int, pull: Callable[[int], np.ndarray]
    ) -> None:
        """Begin playback. ``pull(n)`` returns the next ``n`` stereo frames."""
        ...

    def stop(self) -> None: ...


class SoundDeviceSink:
    """Real audio output via sounddevice (imported lazily)."""

    def __init__(self, device_name: str = "") -> None:
        self._stream = None
        self.device_name = str(device_name or "")

    def start(self, samplerate, blocksize, pull) -> None:
        import sounddevice as sd  # type: ignore

        def _callback(outdata, frames, time_info, status):  # noqa: ANN001
            if status:
                _logger.debug("sounddevice status: %s", status)
            block = pull(frames)
            count = min(block.shape[0], frames)
            outdata[:] = 0.0
            if count:
                if block.ndim == 1:
                    # Backwards-compatible for a custom/legacy pull source.
                    outdata[:count, 0] = block[:count]
                    outdata[:count, 1] = block[:count]
                elif block.shape[1] == 1:
                    outdata[:count, 0] = block[:count, 0]
                    outdata[:count, 1] = block[:count, 0]
                else:
                    outdata[:count, :2] = block[:count, :2]

        self._stream = sd.OutputStream(
            samplerate=samplerate,
            blocksize=blocksize,
            channels=2,
            dtype="float32",
            callback=_callback,
            device=self.device_name or None,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                _logger.debug("sounddevice stop error: %s", exc)
            self._stream = None


class TakePlayer:
    """Transport + mix bus for one take.

    Legacy take playback serializes mixer mutations and source reads through
    ``_lock``. Studio playback transfers its prepared stream to one producer
    thread; the device callback only consumes its bounded in-memory queue.
    Level/position callbacks are invoked outside ``_lock``.
    """

    def __init__(
        self,
        samplerate: int = DEFAULT_SAMPLERATE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        sink: Optional[OutputSink] = None,
        on_position: Optional[Callable[[float], None]] = None,
        on_levels: Optional[Callable[[Dict[int, float]], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_stereo_levels: Optional[
            Callable[[Dict[int, tuple[float, float, bool]]], None]
        ] = None,
        on_master_level: Optional[Callable[[tuple[float, float, bool]], None]] = None,
        on_error: Optional[Callable[[PlaybackError], None]] = None,
    ) -> None:
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self._sink = sink or SoundDeviceSink()
        self._on_position = on_position
        self._on_levels = on_levels
        self._on_finished = on_finished
        self._on_stereo_levels = on_stereo_levels
        self._on_master_level = on_master_level
        self._on_error = on_error

        self._tracks: List[TrackState] = []
        self._lock = threading.RLock()
        self._playing = False
        self._pos_frames = 0
        self._total_frames = 0
        self._studio_renderer = None
        self._studio_stream = None
        self._studio_pipeline: _StudioPlaybackPipeline | None = None
        self._studio_track_channels: dict[str, int] = {}
        self._studio_master = None
        self._studio_cycle_range: tuple[int, int] | None = None
        self._studio_cycle_has_wrapped = False
        self._terminal_error: PlaybackError | None = None
        # Every output run owns one monotonic identity. Epoch-aware consumers
        # can reject a callback that was queued by a stopped, paused, or
        # replaced run even when a new run has already started.
        self._playback_epoch = 0
        self._on_levels_epoch: Callable[[int, Dict[int, float]], None] | None = None
        self._on_stereo_levels_epoch: (
            Callable[[int, Dict[int, tuple[float, float, bool]]], None] | None
        ) = None
        self._on_master_level_epoch: (
            Callable[[int, tuple[float, float, bool]], None] | None
        ) = None
        self._on_finished_epoch: Callable[[int], None] | None = None
        self._on_error_epoch: Callable[[int, PlaybackError], None] | None = None
        # CPython deque append/popleft are atomic and never wait on a UI lock.
        # Meter snapshots may shed their oldest value under a stalled UI, but
        # terminal state has an independent small queue and cannot be evicted by
        # meter traffic or one late callback from a superseded generation.
        self._studio_meter_notifications: deque[_StudioMeterNotification] = deque(
            maxlen=STUDIO_PLAYBACK_NOTIFICATION_CAPACITY
        )
        self._studio_terminal_notifications: deque[_StudioTerminalNotification] = deque(
            maxlen=STUDIO_PLAYBACK_TERMINAL_NOTIFICATION_CAPACITY
        )

    # -- loading ----------------------------------------------------------
    def load(self, take) -> None:
        """Load a TakeInfo (or anything with ``.tracks`` of path/name/offset).
        Resets transport to the start.

        The playback samplerate is adopted from the take's tracks (Jamulus
        records every track of a session at one rate). Without this the
        engine would stream a 44.1 kHz take through a 48 kHz device — a
        ~1.5-semitone pitch shift plus offset/duration misalignment.
        """
        self.stop()
        with self._lock:
            self._studio_renderer = None
            self._studio_stream = None
            self._studio_track_channels = {}
            self._studio_master = None
            self._studio_cycle_range = None
            self._studio_cycle_has_wrapped = False
            self._tracks = []
            # Schema-v2 owns an explicit project rate. Legacy takes adopt the
            # most common non-zero source rate. Individual files are converted
            # to this timeline while streaming, so mixed supported rates never
            # play at the wrong pitch or duration.
            _rates = [
                int(getattr(t, "samplerate", 0) or 0)
                for t in getattr(take, "tracks", [])
            ]
            _rates = [r for r in _rates if r > 0]
            project_rate = int(getattr(take, "project_samplerate", 0) or 0)
            if project_rate > 0:
                self.samplerate = project_rate
            elif _rates:
                from collections import Counter

                self.samplerate = Counter(_rates).most_common(1)[0][0]
            _distinct = set(_rates)
            if len(_distinct) > 1:
                _logger.info(
                    "take has mixed track samplerates %s; converting to %d Hz "
                    "on the non-destructive playback timeline",
                    sorted(_distinct),
                    self.samplerate,
                )
            longest = 0
            for i, t in enumerate(getattr(take, "tracks", [])):
                # Offsets are signed: a negative offset means the track's file
                # starts before the take timeline (normal for supplemental
                # host stems) and playback skips that lead-in.
                offset_s = float(getattr(t, "offset_s", 0.0) or 0.0)
                offset_frames = int(round(offset_s * self.samplerate))
                drift_ppm = float(getattr(t, "drift_ppm", 0.0) or 0.0)
                drift_scale = 1.0 + drift_ppm / 1_000_000.0
                if not np.isfinite(drift_scale) or drift_scale <= 0.0:
                    drift_scale = 1.0
                    drift_ppm = 0.0
                segment_states: list[TrackSegmentState] = []
                for segment in tuple(getattr(t, "segments", ()) or ()):
                    source_rate = int(getattr(segment, "samplerate", 0) or 0)
                    source_frames = int(getattr(segment, "frame_count", 0) or 0)
                    if source_rate <= 0 or source_frames <= 0:
                        continue
                    state = TrackSegmentState(
                        path=Path(getattr(segment, "path")),
                        project_start_frame=int(
                            getattr(segment, "project_start_frame", 0) or 0
                        ),
                        frame_count=source_frames,
                        samplerate=source_rate,
                        channels=max(1, int(getattr(segment, "channels", 1) or 1)),
                        gaps=tuple(getattr(segment, "gaps", ()) or ()),
                    )
                    segment_states.append(state)
                    rendered = int(
                        round(
                            source_frames / source_rate * drift_scale * self.samplerate
                        )
                    )
                    longest = max(
                        longest,
                        state.project_start_frame + offset_frames + rendered,
                    )
                if not segment_states:
                    source_rate = int(getattr(t, "samplerate", 0) or self.samplerate)
                    duration = float(getattr(t, "duration_s", 0.0) or 0.0)
                    source_frames = max(0, int(round(duration * source_rate)))
                    segment_states.append(
                        TrackSegmentState(
                            path=Path(getattr(t, "path")),
                            project_start_frame=0,
                            frame_count=source_frames,
                            samplerate=source_rate,
                        )
                    )
                    longest = max(
                        longest,
                        offset_frames
                        + int(round(duration * drift_scale * self.samplerate)),
                    )
                self._tracks.append(
                    TrackState(
                        channel_id=i,
                        name=getattr(t, "name", f"Track {i}"),
                        path=Path(getattr(t, "path")),
                        track_id=str(getattr(t, "track_id", "") or ""),
                        offset_s=offset_s,
                        source=str(getattr(t, "source", "jamulus_server")),
                        segments=tuple(segment_states),
                        drift_ppm=drift_ppm,
                    )
                )
            self._total_frames = longest
            self._pos_frames = 0

    def load_studio(
        self,
        project,
        document,
        take_root: str | Path,
        *,
        source_catalog=None,
    ) -> None:
        """Load an edited Studio document through the shared render engine.

        Source/arrangement DSP is delegated to ``StudioRenderer``.  The
        existing transport and sink remain in place so output-device behavior
        and UI callbacks do not fork into a second playback implementation.
        ``source_catalog`` is the same optional trusted repeated-take catalog
        accepted by export/render callers.
        """

        from core.studio_renderer import StudioRenderError, StudioRenderer

        self.stop()
        with self._lock:
            self._studio_renderer = None
            self._studio_stream = None
            self._studio_track_channels = {}
            self._studio_master = None
            self._studio_cycle_range = None
            self._studio_cycle_has_wrapped = False
            self._tracks = []
            self._total_frames = 0
            self._pos_frames = 0
        try:
            renderer = StudioRenderer(
                project,
                document,
                take_root,
                source_catalog=source_catalog,
            )
        except StudioRenderError as exc:
            raise StudioPlaybackSourceError(
                f"Studio arrangement could not be loaded: {exc}"
            ) from exc
        project_tracks = {track.track_id: track for track in project.tracks}
        root = Path(take_root).expanduser().resolve()
        with self._lock:
            self._studio_renderer = renderer
            self._studio_stream = None
            self._studio_track_channels = {}
            self._studio_master = document.master
            self.samplerate = renderer.sample_rate
            self._tracks = []
            for channel_id, state in enumerate(
                sorted(document.tracks, key=lambda item: (item.order, item.track_id))
            ):
                source = project_tracks.get(state.track_id)
                first_segment = (
                    source.segments[0] if source and source.segments else None
                )
                path = root / first_segment.path if first_segment is not None else root
                source_type = getattr(source, "source_type", "jamulus_server")
                source_label = str(getattr(source_type, "value", source_type))
                self._tracks.append(
                    TrackState(
                        channel_id=channel_id,
                        track_id=state.track_id,
                        name=getattr(source, "name", f"Track {channel_id + 1}"),
                        path=path,
                        source=source_label,
                        gain=state.fader_gain,
                        trim_gain=state.trim_gain,
                        pan=state.pan,
                        muted=state.muted,
                        solo=state.solo,
                    )
                )
                self._studio_track_channels[state.track_id] = channel_id
            self._total_frames = renderer.total_frames
            self._pos_frames = 0
            cycle = document.cycle_range
            if (
                cycle is not None
                and cycle.enabled
                and cycle.end_frame > 0
                and cycle.start_frame < self._total_frames
            ):
                cycle_start = max(0, cycle.start_frame)
                cycle_end = min(self._total_frames, cycle.end_frame)
                if cycle_end > cycle_start:
                    self._studio_cycle_range = (cycle_start, cycle_end)

    @property
    def tracks(self) -> List[TrackState]:
        return list(self._tracks)

    @property
    def duration_s(self) -> float:
        return self._total_frames / self.samplerate if self.samplerate else 0.0

    @property
    def position_s(self) -> float:
        frame = self.position_frame
        return frame / self.samplerate if self.samplerate else 0.0

    @property
    def position_frame(self) -> int:
        """Exact integer transport position on the active project timeline."""

        pipeline = self._studio_pipeline
        if pipeline is not None:
            return pipeline.transport_state()[0]
        return self._pos_frames

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def playback_epoch(self) -> int:
        """Return the identity of the current playback lifecycle."""

        with self._lock:
            return self._playback_epoch

    def _advance_playback_epoch_locked(self) -> int:
        self._playback_epoch += 1
        self._studio_meter_notifications.clear()
        self._studio_terminal_notifications.clear()
        return self._playback_epoch

    @property
    def studio_playback_prepared(self) -> bool:
        with self._lock:
            return self._studio_renderer is not None and self._studio_stream is not None

    @property
    def studio_buffered_blocks(self) -> int:
        """Current bounded Studio prefetch occupancy (diagnostic/tests)."""

        pipeline = self._studio_pipeline
        return pipeline.buffered_blocks if pipeline is not None else 0

    @property
    def studio_buffered_bytes(self) -> int:
        """Current Studio prefetch payload bytes (diagnostic/tests)."""

        pipeline = self._studio_pipeline
        return pipeline.buffered_bytes if pipeline is not None else 0

    @property
    def studio_buffered_frames(self) -> int:
        """Ready Studio frames retained by the bounded producer queue."""

        pipeline = self._studio_pipeline
        return pipeline.buffered_frames if pipeline is not None else 0

    @property
    def studio_buffer_capacity_frames(self) -> int:
        pipeline = self._studio_pipeline
        return pipeline.capacity_frames if pipeline is not None else 0

    @property
    def studio_buffer_capacity_bytes(self) -> int:
        pipeline = self._studio_pipeline
        return pipeline.capacity_bytes if pipeline is not None else 0

    @property
    def studio_buffer_prime_frames(self) -> int:
        pipeline = self._studio_pipeline
        return pipeline.prime_frames if pipeline is not None else 0

    @property
    def studio_notification_capacity(self) -> int:
        """Maximum queued meter snapshots; terminal state has its own slot."""

        return STUDIO_PLAYBACK_NOTIFICATION_CAPACITY

    @property
    def studio_pending_notifications(self) -> int:
        """Current bounded callback-to-control mailbox occupancy."""

        return len(self._studio_meter_notifications) + len(
            self._studio_terminal_notifications
        )

    @property
    def has_studio_arrangement(self) -> bool:
        with self._lock:
            return self._studio_renderer is not None

    @property
    def output_device_name(self) -> str:
        return str(getattr(self._sink, "device_name", "") or "")

    def set_output_device(self, device_name: str) -> bool:
        """Select a playback device when using WebJam's sounddevice sink.

        Returns ``False`` for injected/custom sinks so tests and integrations
        never have their output object silently replaced.
        """
        if not isinstance(self._sink, SoundDeviceSink):
            return False
        self.stop()
        self._sink.device_name = str(device_name or "")
        return True

    # -- mixer controls (live) -------------------------------------------
    def set_gain(self, channel_id: int, gain: float) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.gain = max(0.0, min(4.0, float(gain)))
                    self._update_studio_stream_mix(t, fader_gain=t.gain)

    def set_trim_gain(self, channel_id: int, gain: float) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.trim_gain = max(0.0, min(4.0, float(gain)))
                    self._update_studio_stream_mix(t, trim_gain=t.trim_gain)

    def set_muted(self, channel_id: int, muted: bool) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.muted = bool(muted)
                    self._update_studio_stream_mix(t, muted=t.muted)

    def set_pan(self, channel_id: int, pan: float) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.pan = max(-1.0, min(1.0, float(pan)))
                    self._update_studio_stream_mix(t, pan=t.pan)

    def set_solo(self, channel_id: int, solo: bool) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.solo = bool(solo)
                    self._update_studio_stream_mix(t, solo=t.solo)

    def _update_studio_stream_mix(self, track: TrackState, **changes: object) -> None:
        del changes
        pipeline = self._studio_pipeline
        if pipeline is not None:
            position, wrapped = pipeline.transport_state()
            pipeline.reconfigure(
                epoch=self._playback_epoch,
                position_frame=position,
                wrapped=wrapped,
                mix=self._studio_mix_snapshot_locked(),
                checkpoint=False,
            )
            return
        stream = self._studio_stream
        if stream is not None and track.track_id:
            stream.set_track_mix(
                track.track_id,
                trim_gain=track.trim_gain,
                fader_gain=track.gain,
                pan=track.pan,
                muted=track.muted,
                solo=track.solo,
            )

    def set_master_gain(self, gain: float) -> None:
        from core.studio_project import StudioMaster

        with self._lock:
            current = self._studio_master or StudioMaster()
            self._studio_master = StudioMaster(
                gain=max(0.0, min(4.0, float(gain))),
                limiter_enabled=current.limiter_enabled,
            )
            if self._studio_pipeline is not None:
                position, wrapped = self._studio_pipeline.transport_state()
                self._studio_pipeline.reconfigure(
                    epoch=self._playback_epoch,
                    position_frame=position,
                    wrapped=wrapped,
                    mix=self._studio_mix_snapshot_locked(),
                    checkpoint=False,
                )
            elif self._studio_stream is not None:
                self._studio_stream.set_master(self._studio_master)

    def set_limiter_enabled(self, enabled: bool) -> None:
        from core.studio_project import StudioMaster

        with self._lock:
            current = self._studio_master or StudioMaster()
            self._studio_master = StudioMaster(
                gain=current.gain,
                limiter_enabled=bool(enabled),
            )
            if self._studio_pipeline is not None:
                position, wrapped = self._studio_pipeline.transport_state()
                self._studio_pipeline.reconfigure(
                    epoch=self._playback_epoch,
                    position_frame=position,
                    wrapped=wrapped,
                    mix=self._studio_mix_snapshot_locked(),
                    checkpoint=False,
                )
            elif self._studio_stream is not None:
                self._studio_stream.set_master(self._studio_master)

    def _studio_mix_snapshot_locked(self) -> _StudioMixSnapshot:
        return _StudioMixSnapshot(
            tracks=tuple(
                (
                    track.track_id,
                    track.trim_gain,
                    track.gain,
                    track.pan,
                    track.muted,
                    track.solo,
                )
                for track in self._tracks
                if track.track_id
            ),
            master=self._studio_master,
        )

    def _new_studio_pipeline(
        self,
        stream: object,
        *,
        samplerate: int,
        blocksize: int,
        track_ids: tuple[str, ...],
        cycle_range: tuple[int, int] | None,
    ) -> _StudioPlaybackPipeline:
        target_frames = max(
            1,
            int(round(samplerate * STUDIO_PLAYBACK_TARGET_BUFFER_MS / 1000.0)),
        )
        minimum_producer_frames = max(
            1,
            int(round(samplerate * STUDIO_PLAYBACK_MIN_PRODUCER_BLOCK_MS / 1000.0)),
        )
        producer_frames = max(
            1,
            min(
                max(blocksize, minimum_producer_frames),
                STUDIO_PLAYBACK_MAX_BLOCK_FRAMES,
            ),
        )
        callback_frames = max(1, int(blocksize))
        maximum_capacity = (
            STUDIO_PLAYBACK_MAX_BLOCK_FRAMES * STUDIO_PLAYBACK_MAX_QUEUE_BLOCKS
        )
        if callback_frames > maximum_capacity:
            raise PlaybackError(
                "Studio playback block size exceeds the bounded realtime capacity."
            )
        target_blocks = max(1, target_frames // producer_frames)
        callback_blocks = max(
            1,
            (callback_frames + producer_frames - 1) // producer_frames,
        )
        capacity_blocks = min(
            max(target_blocks, callback_blocks),
            STUDIO_PLAYBACK_MAX_QUEUE_BLOCKS,
        )
        return _StudioPlaybackPipeline(
            stream,
            block_frames=producer_frames,
            callback_frames=callback_frames,
            capacity_blocks=capacity_blocks,
            track_ids=track_ids,
            cycle_range=cycle_range,
            smooth_cycle=self._cycle_smoothing_gains,
        )

    def _start_studio_pipeline(self, playback_epoch: int) -> None:
        """Promote prefetched audio or start the stream's sole render worker."""

        command: _StudioPipelineCommand | None = None
        with self._lock:
            pipeline = self._studio_pipeline
            position = self._pos_frames
            wrapped = self._studio_cycle_has_wrapped
            mix = self._studio_mix_snapshot_locked()
            if pipeline is None:
                stream = self._studio_stream
                if stream is None:
                    raise PlaybackError("Studio playback sources are not prepared.")
                pipeline = self._new_studio_pipeline(
                    stream,
                    samplerate=self.samplerate,
                    blocksize=self.blocksize,
                    track_ids=tuple(
                        track.track_id for track in self._tracks if track.track_id
                    ),
                    cycle_range=self._studio_cycle_range,
                )
                self._studio_pipeline = pipeline
                generation, command = pipeline.start(
                    epoch=playback_epoch,
                    position_frame=position,
                    wrapped=wrapped,
                    mix=mix,
                )
            else:
                position, wrapped = pipeline.transport_state()
                self._pos_frames = position
                self._studio_cycle_has_wrapped = wrapped
                generation = pipeline.promote_primed(
                    epoch=playback_epoch,
                    position_frame=position,
                    wrapped=wrapped,
                    mix=mix,
                )
                if generation is None:
                    generation, command = pipeline.reconfigure(
                        epoch=playback_epoch,
                        position_frame=position,
                        wrapped=wrapped,
                        mix=mix,
                        checkpoint=True,
                        verify_checksum=False,
                    )
        if command is not None:
            pipeline.wait_for_command(command)
        ready = pipeline.wait_until_ready(
            generation,
            STUDIO_PLAYBACK_PRIME_TIMEOUT_S,
        )
        if not ready:
            raise PlaybackError("Studio playback producer did not become ready.")
        error = pipeline.ready_error(generation)
        if error is not None:
            raise error

    # -- transport --------------------------------------------------------
    def play(self) -> None:
        with self._lock:
            if self._playing:
                return
            if self._terminal_error is not None:
                raise self._terminal_error
            # Replaying after the take finished: rewind instead of starting a
            # stream that would immediately sit at EOF emitting silence.
            cycle = self._studio_cycle_range
            if cycle is not None and self._pos_frames >= cycle[1]:
                self._pos_frames = cycle[0]
                self._studio_cycle_has_wrapped = False
                self._close_readers()
            elif self._pos_frames >= self._total_frames:
                self._pos_frames = 0
                self._studio_cycle_has_wrapped = False
                self._close_readers()
            playback_epoch = self._advance_playback_epoch_locked()
            self._playing = True
        try:
            self._open_readers()
            if self._studio_renderer is not None:
                self._start_studio_pipeline(playback_epoch)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._playing = False
                self._close_readers()
            if self._studio_renderer is not None:
                raise StudioPlaybackSourceError(
                    f"Studio arrangement source media could not be opened: {exc}"
                ) from exc
            raise PlaybackError(f"Couldn't open playback media: {exc}") from exc
        try:
            self._sink.start(
                self.samplerate,
                self.blocksize,
                lambda frames, epoch=playback_epoch: self._pull(
                    frames,
                    _callback_epoch=epoch,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            studio_render_error = False
            if self._studio_renderer is not None:
                from core.studio_renderer import StudioRenderError

                studio_render_error = isinstance(exc, StudioRenderError)
            try:
                self._sink.stop()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._playing = False
                self._close_readers()
            if studio_render_error:
                raise StudioPlaybackSourceError(
                    f"Studio arrangement source media could not be opened: {exc}"
                ) from exc
            raise PlaybackDeviceError(
                f"Couldn't open the playback device: {exc}"
            ) from exc
        if self._studio_renderer is not None:
            # A synchronous test sink may have pulled inside start(). A real
            # device normally leaves these for RecordingStudio's UI timer.
            self.drain_studio_notifications()
        terminal_error = self.terminal_error
        if terminal_error is not None:
            # A synchronous/custom sink can execute ``pull`` inside start().
            # Preserve the same typed startup behavior while the real audio
            # callback path leaves cleanup for its UI-thread error drain.
            self.drain_terminal_error()
            raise terminal_error

    def pause(self) -> None:
        with self._lock:
            pipeline = self._studio_pipeline
            if pipeline is not None:
                self._pos_frames, self._studio_cycle_has_wrapped = (
                    pipeline.transport_state()
                )
            self._playing = False
            self._advance_playback_epoch_locked()
        self._sink.stop()

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._advance_playback_epoch_locked()
        self._sink.stop()
        with self._lock:
            self._pos_frames = 0
            self._studio_cycle_has_wrapped = False
            self._close_readers()
            if self._studio_pipeline is not None:
                error = PlaybackError(
                    "Studio playback producer did not stop within its safety deadline."
                )
                self._terminal_error = error
                raise error
            self._terminal_error = None

    def seek(self, seconds: float) -> None:
        target = int(max(0.0, seconds) * self.samplerate)
        self.seek_frame(target)

    def seek_frame(self, frame: int) -> None:
        """Seek to one exact integer project frame.

        Arrange uses this path so a frame selection never makes a lossy
        round-trip through floating-point seconds.
        """

        if isinstance(frame, bool) or not isinstance(frame, int):
            raise ValueError("seek frame must be an integer")
        source_error: StudioPlaybackSourceError | None = None
        pipeline_command: _StudioPipelineCommand | None = None
        pipeline: _StudioPlaybackPipeline | None = None
        previous_position_frame: int | None = None
        previous_wrapped: bool | None = None
        with self._lock:
            target = max(0, frame)
            self._pos_frames = min(target, self._total_frames)
            self._studio_cycle_has_wrapped = False
            # Recreate readers so their next pull seeks from one consistent
            # timeline position.  Playback may already be running (Studio's
            # scrubber seeks without stopping the output stream), so leaving
            # the readers closed here would turn every subsequent block into
            # silence until the user manually stopped and pressed Play again.
            pipeline = self._studio_pipeline
            if pipeline is not None:
                previous_position_frame, previous_wrapped = pipeline.transport_state()
                _generation, pipeline_command = pipeline.reconfigure(
                    epoch=self._playback_epoch,
                    position_frame=self._pos_frames,
                    wrapped=False,
                    mix=self._studio_mix_snapshot_locked(),
                    checkpoint=True,
                    verify_checksum=False,
                )
            elif self._studio_stream is not None:
                try:
                    self._studio_stream.checkpoint(self._pos_frames)
                except Exception as exc:  # noqa: BLE001
                    if self._studio_renderer is None:
                        raise
                    from core.studio_renderer import StudioRenderError

                    if not isinstance(exc, StudioRenderError):
                        raise
                    source_error = StudioPlaybackSourceError(
                        "Studio arrangement source media changed while seeking."
                    )
            else:
                self._close_readers()
                if self._playing:
                    try:
                        self._open_readers()
                    except Exception as exc:  # noqa: BLE001
                        if self._studio_renderer is None:
                            raise
                        from core.studio_renderer import StudioRenderError

                        if isinstance(exc, StudioPlaybackSourceError):
                            source_error = exc
                        elif isinstance(exc, StudioRenderError):
                            source_error = StudioPlaybackSourceError(
                                "Studio arrangement source media changed while seeking."
                            )
                        else:
                            raise
        if pipeline is not None and pipeline_command is not None:
            try:
                pipeline.wait_for_command(
                    pipeline_command,
                    fallback_position_frame=previous_position_frame,
                    fallback_wrapped=previous_wrapped,
                )
            except StudioPlaybackSourceError as exc:
                source_error = exc
            except _StudioPlaybackCommandTimeout as exc:
                with self._lock:
                    if self._studio_pipeline is pipeline:
                        if previous_position_frame is not None:
                            self._pos_frames = previous_position_frame
                        if previous_wrapped is not None:
                            self._studio_cycle_has_wrapped = previous_wrapped
                self._latch_terminal_error(exc)
                raise
            except PlaybackError:
                raise
        if source_error is not None:
            self._latch_terminal_error(source_error)
            raise source_error
        if self._on_position:
            self._on_position(self.position_s)

    def drain_studio_notifications(self) -> int:
        """Publish bounded Studio callback snapshots on a control/UI thread.

        The device callback only appends immutable values to two lock-free
        deques. This method performs every potentially blocking application
        callback after validating the pipeline, generation, and playback epoch.
        """

        meters: list[_StudioMeterNotification] = []
        for _index in range(
            min(
                len(self._studio_meter_notifications),
                STUDIO_PLAYBACK_NOTIFICATION_CAPACITY,
            )
        ):
            try:
                meters.append(self._studio_meter_notifications.popleft())
            except IndexError:
                break
        terminals: list[_StudioTerminalNotification] = []
        for _index in range(
            min(
                len(self._studio_terminal_notifications),
                STUDIO_PLAYBACK_TERMINAL_NOTIFICATION_CAPACITY,
            )
        ):
            try:
                terminals.append(self._studio_terminal_notifications.popleft())
            except IndexError:
                break

        published = 0
        for notification in meters:
            pipeline = notification.pipeline
            with self._lock:
                if (
                    self._studio_pipeline is not pipeline
                    or notification.epoch != self._playback_epoch
                    or notification.generation != pipeline.generation
                ):
                    continue
                self._pos_frames = notification.position_frame
                self._studio_cycle_has_wrapped = notification.wrapped
                for track in self._tracks:
                    if track.channel_id in notification.levels:
                        track.level = notification.levels[track.channel_id]
                levels_callback = self._on_levels
                levels_epoch_callback = self._on_levels_epoch
                stereo_callback = self._on_stereo_levels
                stereo_epoch_callback = self._on_stereo_levels_epoch
                master_callback = self._on_master_level
                master_epoch_callback = self._on_master_level_epoch
                position_callback = self._on_position
            if levels_callback is not None:
                levels_callback(notification.levels)
            if levels_epoch_callback is not None:
                levels_epoch_callback(notification.epoch, notification.levels)
            if stereo_callback is not None:
                stereo_callback(notification.stereo_levels)
            if stereo_epoch_callback is not None:
                stereo_epoch_callback(
                    notification.epoch,
                    notification.stereo_levels,
                )
            if master_callback is not None:
                master_callback(notification.master_level)
            if master_epoch_callback is not None:
                master_epoch_callback(
                    notification.epoch,
                    notification.master_level,
                )
            if position_callback is not None:
                position_callback(notification.position_frame / self.samplerate)
            published += 1

        for notification in terminals:
            pipeline = notification.pipeline
            if notification.error is not None:
                accepted = self._latch_terminal_error(
                    notification.error,
                    notification.epoch,
                    pipeline=pipeline,
                    generation=notification.generation,
                )
            elif notification.finished:
                accepted = self._finish_if_needed(
                    notification.epoch,
                    pipeline=pipeline,
                    generation=notification.generation,
                )
            else:
                accepted = False
            if accepted:
                published += 1
        return published

    @property
    def terminal_error(self) -> PlaybackError | None:
        """Return the currently latched terminal playback error, if any."""

        with self._lock:
            return self._terminal_error

    def drain_terminal_error(self) -> PlaybackError | None:
        """Stop and release playback after an audio-thread terminal error.

        This must be called from a control/UI thread. In particular it keeps
        ``sink.stop()`` and reader teardown out of a sounddevice callback.
        """

        with self._lock:
            error = self._terminal_error
            if error is None:
                return None
            self._playing = False
            self._advance_playback_epoch_locked()
        try:
            self._sink.stop()
        except Exception as exc:  # noqa: BLE001
            _logger.debug("playback error cleanup failed to stop sink: %s", exc)
        with self._lock:
            self._close_readers()
            self._pos_frames = 0
            self._studio_cycle_has_wrapped = False
            if self._terminal_error is error and self._studio_pipeline is None:
                self._terminal_error = None
        return error

    def _latch_terminal_error(
        self,
        error: PlaybackError,
        callback_epoch: int | None = None,
        *,
        pipeline: _StudioPlaybackPipeline | None = None,
        generation: int | None = None,
    ) -> bool:
        callback: Callable[[PlaybackError], None] | None = None
        epoch_callback: Callable[[int, PlaybackError], None] | None = None
        with self._lock:
            epoch = (
                self._playback_epoch if callback_epoch is None else int(callback_epoch)
            )
            if epoch != self._playback_epoch:
                return False
            if pipeline is not None and (
                self._studio_pipeline is not pipeline
                or generation is None
                or generation != pipeline.generation
            ):
                return False
            if self._terminal_error is not None:
                return False
            self._terminal_error = error
            self._playing = False
            callback = self._on_error
            epoch_callback = self._on_error_epoch
        if callback is not None:
            try:
                callback(error)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("playback error callback failed: %s", exc)
        if epoch_callback is not None:
            try:
                epoch_callback(epoch, error)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("epoch playback error callback failed: %s", exc)
        return True

    # -- readers ----------------------------------------------------------
    def prepare_studio_playback(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> StudioPlaybackPreparation:
        """Validate, bind, and prefill Studio audio on the preparation worker."""

        if cancel_check is not None and not callable(cancel_check):
            raise PlaybackError("cancel_check must be callable or null.")
        with self._lock:
            renderer = self._studio_renderer
            if renderer is None:
                raise PlaybackError("No Studio arrangement is loaded.")
            total_frames = self._total_frames
            position_frame = self._pos_frames
            wrapped = self._studio_cycle_has_wrapped
            mix = self._studio_mix_snapshot_locked()
            epoch = self._playback_epoch
            samplerate = self.samplerate
            blocksize = self.blocksize
            track_ids = tuple(
                track.track_id for track in self._tracks if track.track_id
            )
            cycle_range = self._studio_cycle_range
        stream: object | None = None
        pipeline: _StudioPlaybackPipeline | None = None
        try:
            if cancel_check is not None:
                cancel_check()
            stream = renderer.open(
                start_frame=0,
                end_frame=total_frames,
                cancel_check=cancel_check,
                realtime_safe=True,
            )
            pipeline = self._new_studio_pipeline(
                stream,
                samplerate=samplerate,
                blocksize=blocksize,
                track_ids=track_ids,
                cycle_range=cycle_range,
            )
            generation, command = pipeline.start(
                epoch=epoch,
                position_frame=position_frame,
                wrapped=wrapped,
                mix=mix,
                prime_only=True,
            )
            pipeline.wait_for_command(command)
            while not pipeline.wait_until_primed(generation, 0.05):
                if cancel_check is not None:
                    cancel_check()
                if pipeline.is_closed:
                    raise PlaybackError(
                        "Studio playback producer closed during preparation."
                    )
            error = pipeline.ready_error(generation)
            if error is not None:
                raise error
            if cancel_check is not None:
                cancel_check()
        except Exception as exc:
            if pipeline is not None:
                pipeline.stop()
            elif stream is not None:
                stream.close()
            from core.studio_renderer import StudioRenderError

            if isinstance(exc, StudioRenderError):
                raise StudioPlaybackSourceError(
                    f"Studio arrangement source media could not be prepared: {exc}"
                ) from exc
            raise
        return StudioPlaybackPreparation(
            renderer=renderer,
            stream=stream,
            pipeline=pipeline,
            position_frame=position_frame,
            mix=mix,
        )

    def install_studio_preparation(
        self,
        preparation: StudioPlaybackPreparation,
    ) -> bool:
        """Install an exact background-prefilled generation without source I/O."""

        if not isinstance(preparation, StudioPlaybackPreparation):
            raise PlaybackError("Invalid Studio playback preparation.")
        with self._lock:
            current_mix = self._studio_mix_snapshot_locked()
            if (
                self._studio_renderer is not preparation.renderer
                or self._pos_frames != preparation.position_frame
                or current_mix != preparation.mix
            ):
                preparation.close()
                return False
            if not isinstance(preparation.pipeline, _StudioPlaybackPipeline):
                preparation.close()
                raise PlaybackError("Studio preparation did not include audio prefill.")
            self._close_readers()
            if self._studio_pipeline is not None:
                preparation.close()
                raise PlaybackError(
                    "Previous Studio playback producer is still shutting down."
                )
            stream, pipeline = preparation.take_resources()
            self._studio_stream = stream
            self._studio_pipeline = pipeline
            return True

    def _open_readers(self) -> None:
        if self._studio_renderer is not None:
            with self._lock:
                if self._studio_stream is not None:
                    return
            preparation = self.prepare_studio_playback()
            if not self.install_studio_preparation(preparation):
                raise PlaybackError("Studio arrangement changed while preparing.")
            return
        import soundfile as sf  # type: ignore

        with self._lock:
            for t in self._tracks:
                for segment in t.segments:
                    if segment._reader is not None:
                        continue
                    try:
                        segment._reader = sf.SoundFile(str(segment.path))
                    except Exception as exc:  # noqa: BLE001
                        _logger.warning("can't open %s: %s", segment.path, exc)
                        segment._reader = None
                t._reader = t.segments[0]._reader if t.segments else None
                t._eof = False

    def _close_readers(self) -> None:
        pipeline = self._studio_pipeline
        if pipeline is not None:
            if pipeline.stop():
                self._studio_pipeline = None
                self._studio_stream = None
            else:
                _logger.error(
                    "Studio playback producer did not stop within its deadline; "
                    "its stream remains owned until the worker exits."
                )
        elif self._studio_stream is not None:
            try:
                self._studio_stream.close()
            finally:
                self._studio_stream = None
        for t in self._tracks:
            for segment in t.segments:
                if segment._reader is None:
                    continue
                try:
                    segment._reader.close()
                except Exception:  # noqa: BLE001
                    pass
                segment._reader = None
            t._reader = None
            t._eof = False

    def _read_track_block(self, t: TrackState, start: int, n: int) -> np.ndarray:
        """Return n source-channel frames for absolute take-timeline
        window [start, start+n), honouring the track's offset. Silence where
        the track isn't sounding (before its offset or past its end)."""
        channels = max((segment.channels for segment in t.segments), default=1)
        out = np.zeros((n, channels), dtype=np.float32)
        offset_frames = int(round(t.offset_s * self.samplerate))
        scale = 1.0 + float(t.drift_ppm) / 1_000_000.0
        for segment in t.segments:
            reader = segment._reader
            if reader is None or segment.samplerate <= 0 or segment.frame_count <= 0:
                continue
            segment_start = segment.project_start_frame + offset_frames
            rendered_frames = int(
                round(
                    segment.frame_count / segment.samplerate * scale * self.samplerate
                )
            )
            segment_end = segment_start + max(0, rendered_frames)
            overlap_start = max(start, segment_start)
            overlap_end = min(start + n, segment_end)
            if overlap_end <= overlap_start:
                continue
            output_positions = np.arange(overlap_start, overlap_end, dtype=np.float64)
            source_positions = (
                (output_positions - segment_start)
                / self.samplerate
                / scale
                * segment.samplerate
            )
            source_positions = np.clip(
                source_positions, 0.0, max(0.0, segment.frame_count - 1.0)
            )
            first = max(0, int(np.floor(source_positions[0])) - 1)
            last = min(
                segment.frame_count,
                int(np.ceil(source_positions[-1])) + 2,
            )
            try:
                if reader.tell() != first:
                    reader.seek(first)
                source = reader.read(last - first, dtype="float32", always_2d=True)
            except Exception:  # noqa: BLE001
                continue
            if not len(source):
                continue
            local_positions = source_positions - first
            grid = np.arange(len(source), dtype=np.float64)
            rendered = np.empty(
                (len(output_positions), source.shape[1]), dtype=np.float32
            )
            for channel in range(source.shape[1]):
                rendered[:, channel] = np.interp(
                    local_positions, grid, source[:, channel]
                ).astype(np.float32)
            for gap_start, gap_count, gap_channels, _reason in segment.gaps:
                inside = (source_positions >= gap_start) & (
                    source_positions < gap_start + gap_count
                )
                if not np.any(inside):
                    continue
                targets = gap_channels or tuple(range(rendered.shape[1]))
                for channel in targets:
                    if 0 <= channel < rendered.shape[1]:
                        rendered[inside, channel] = 0.0
            destination = overlap_start - start
            out[
                destination : destination + len(rendered),
                : rendered.shape[1],
            ] = rendered
        return out

    @staticmethod
    def _pan_block(block: np.ndarray, pan: float) -> np.ndarray:
        """Return a stereo block using mono pan or stereo balance."""
        value = max(-1.0, min(1.0, float(pan)))
        if block.shape[1] == 1:
            mono = block[:, 0]
            left = mono * (1.0 - max(0.0, value))
            right = mono * (1.0 + min(0.0, value))
            return np.column_stack((left, right))
        stereo = block[:, :2].copy()
        if value < 0:
            stereo[:, 1] *= 1.0 + value
        elif value > 0:
            stereo[:, 0] *= 1.0 - value
        return stereo

    def _pull(
        self,
        frames: int,
        *,
        _callback_epoch: int | None = None,
    ) -> np.ndarray:
        """Mix the next ``frames`` frames. Called from the audio thread."""
        if self._studio_renderer is not None:
            return self._pull_studio(frames, _callback_epoch=_callback_epoch)
        with self._lock:
            callback_epoch = (
                self._playback_epoch
                if _callback_epoch is None
                else int(_callback_epoch)
            )
            if _callback_epoch is not None and (
                callback_epoch != self._playback_epoch or not self._playing
            ):
                return np.zeros((frames, 2), dtype=np.float32)
            if not self._tracks or self._pos_frames >= self._total_frames:
                self._finish_if_needed(callback_epoch)
                return np.zeros((frames, 2), dtype=np.float32)

            start = self._pos_frames
            any_solo = any(t.solo for t in self._tracks)
            mix = np.zeros((frames, 2), dtype=np.float32)
            levels: Dict[int, float] = {}
            stereo_levels: Dict[int, tuple[float, float, bool]] = {}
            for t in self._tracks:
                audible = (not t.muted) and (t.solo or not any_solo)
                block = self._read_track_block(t, start, frames)
                if audible and t.gain > 0.0:
                    contribution = self._pan_block(block, t.pan) * t.gain
                    mix += contribution
                    rms = (
                        float(np.sqrt(np.mean(np.square(contribution))))
                        if frames
                        else 0.0
                    )
                    t.level = min(1.0, rms * 3.0)  # visual scaling
                    peaks = (
                        np.max(np.abs(contribution), axis=0)
                        if contribution.size
                        else (0.0, 0.0)
                    )
                    stereo_levels[t.channel_id] = (
                        float(peaks[0]),
                        float(peaks[1]),
                        bool(np.any(np.abs(contribution) > 1.0)),
                    )
                else:
                    t.level = 0.0
                    stereo_levels[t.channel_id] = (0.0, 0.0, False)
                levels[t.channel_id] = t.level

            # Hard-limit to protect ears/speakers when many tracks stack.
            master_peaks = np.max(np.abs(mix), axis=0) if frames else (0.0, 0.0)
            master_level = (
                float(master_peaks[0]),
                float(master_peaks[1]),
                bool(np.any(np.abs(mix) > 1.0)),
            )
            np.clip(mix, -1.0, 1.0, out=mix)
            self._pos_frames += frames
            pos_s = self.position_s
            finished = self._pos_frames >= self._total_frames

        if self._on_levels:
            self._on_levels(levels)
        if self._on_levels_epoch:
            self._on_levels_epoch(callback_epoch, levels)
        if self._on_stereo_levels:
            self._on_stereo_levels(stereo_levels)
        if self._on_stereo_levels_epoch:
            self._on_stereo_levels_epoch(callback_epoch, stereo_levels)
        if self._on_master_level:
            self._on_master_level(master_level)
        if self._on_master_level_epoch:
            self._on_master_level_epoch(callback_epoch, master_level)
        if self._on_position:
            self._on_position(pos_s)
        if finished:
            self._finish_if_needed(callback_epoch)
        return mix

    def _cycle_smoothing_gains(
        self,
        start_frame: int,
        frame_count: int,
        cycle: tuple[int, int] | None,
        *,
        wrapped: bool,
    ) -> np.ndarray:
        """Return a deterministic short fade around an exact cycle boundary."""

        gains = np.ones(frame_count, dtype=np.float32)
        if cycle is None or frame_count <= 0:
            return gains
        cycle_start, cycle_end = cycle
        cycle_frames = cycle_end - cycle_start
        # Cycles shorter than four frames cannot hold distinct fade-in and
        # fade-out endpoints plus any untouched musical sample. Preserve those
        # pathological but valid loops sample-exactly instead of turning a
        # two-frame cycle into silence or a three-frame cycle into one sample.
        if cycle_frames < 4:
            return gains
        fade_frames = min(
            max(1, int(round(self.samplerate * 0.005))),
            cycle_frames // 2,
        )
        if fade_frames <= 0:
            return gains
        positions = np.arange(
            start_frame,
            start_frame + frame_count,
            dtype=np.int64,
        )
        inside = (positions >= cycle_start) & (positions < cycle_end)
        if not np.any(inside):
            return gains
        offsets = positions - cycle_start
        fade_out = inside & (offsets >= cycle_frames - fade_frames)
        if np.any(fade_out):
            gains[fade_out] *= (
                (cycle_frames - 1 - offsets[fade_out]) / float(fade_frames)
            ).astype(np.float32)
        if wrapped:
            fade_in = inside & (offsets < fade_frames)
            if np.any(fade_in):
                gains[fade_in] *= (offsets[fade_in] / float(fade_frames)).astype(
                    np.float32
                )
        return gains

    def _pull_studio(
        self,
        frames: int,
        *,
        _callback_epoch: int | None = None,
    ) -> np.ndarray:
        """Consume one ready Studio block without waiting or touching sources."""

        requested = max(0, int(frames))
        silence = np.zeros((requested, 2), dtype=np.float32)
        callback_epoch = (
            self._playback_epoch if _callback_epoch is None else int(_callback_epoch)
        )
        pipeline = self._studio_pipeline
        if (
            pipeline is None
            or callback_epoch != self._playback_epoch
            or not self._playing
            or self._terminal_error is not None
        ):
            return silence

        consumed = pipeline.consume(requested, callback_epoch)
        if (
            self._studio_pipeline is not pipeline
            or callback_epoch != self._playback_epoch
            or not pipeline.matches_realtime_snapshot(
                consumed.generation,
                callback_epoch,
            )
        ):
            return silence

        if consumed.consumed_frames:
            levels: Dict[int, float] = {}
            stereo_levels: Dict[int, tuple[float, float, bool]] = {}
            for track in self._tracks:
                contribution = consumed.track_audio.get(track.track_id)
                if contribution is None or not contribution.size:
                    level = 0.0
                    peaks = np.zeros(2, dtype=np.float32)
                    clipped = False
                else:
                    level = min(
                        1.0,
                        float(np.sqrt(np.mean(np.square(contribution)))) * 3.0,
                    )
                    peaks = np.max(np.abs(contribution), axis=0)
                    clipped = bool(np.any(np.abs(contribution) > 1.0))
                levels[track.channel_id] = level
                stereo_levels[track.channel_id] = (
                    float(peaks[0]),
                    float(peaks[1]),
                    clipped,
                )
            master_audio = consumed.master_meter_audio
            master_peaks = (
                np.max(np.abs(master_audio), axis=0)
                if master_audio.size
                else np.zeros(2, dtype=np.float32)
            )
            master_level = (
                float(master_peaks[0]),
                float(master_peaks[1]),
                bool(master_audio.size and np.any(np.abs(master_audio) > 1.0)),
            )
            if consumed.position_frame is not None:
                self._studio_meter_notifications.append(
                    _StudioMeterNotification(
                        pipeline=pipeline,
                        generation=consumed.generation,
                        epoch=callback_epoch,
                        position_frame=consumed.position_frame,
                        wrapped=consumed.wrapped,
                        levels=levels,
                        stereo_levels=stereo_levels,
                        master_level=master_level,
                    )
                )

        if consumed.error is not None:
            self._latch_terminal_error_realtime(
                consumed.error,
                callback_epoch,
                pipeline,
                consumed.generation,
            )
        elif consumed.finished:
            self._finish_studio_realtime(
                callback_epoch,
                pipeline,
                consumed.generation,
            )
        return consumed.audio

    def _latch_terminal_error_realtime(
        self,
        error: PlaybackError,
        callback_epoch: int,
        pipeline: _StudioPlaybackPipeline,
        generation: int,
    ) -> None:
        """Hand one producer error to the independent terminal mailbox."""

        if (
            self._studio_pipeline is not pipeline
            or int(callback_epoch) != self._playback_epoch
            or not pipeline.matches_realtime_snapshot(generation, callback_epoch)
        ):
            return
        self._studio_terminal_notifications.append(
            _StudioTerminalNotification(
                pipeline=pipeline,
                generation=int(generation),
                epoch=int(callback_epoch),
                error=error,
            )
        )

    def _finish_studio_realtime(
        self,
        callback_epoch: int,
        pipeline: _StudioPlaybackPipeline,
        generation: int,
    ) -> None:
        """Hand EOF to the control thread without mutating transport state."""

        if (
            self._studio_pipeline is not pipeline
            or int(callback_epoch) != self._playback_epoch
            or not pipeline.matches_realtime_snapshot(generation, callback_epoch)
        ):
            return
        self._studio_terminal_notifications.append(
            _StudioTerminalNotification(
                pipeline=pipeline,
                generation=int(generation),
                epoch=int(callback_epoch),
                finished=True,
            )
        )

    def _finish_if_needed(
        self,
        callback_epoch: int | None = None,
        *,
        pipeline: _StudioPlaybackPipeline | None = None,
        generation: int | None = None,
    ) -> bool:
        with self._lock:
            epoch = (
                self._playback_epoch if callback_epoch is None else int(callback_epoch)
            )
            if epoch != self._playback_epoch or not self._playing:
                return False
            if pipeline is not None and (
                self._studio_pipeline is not pipeline
                or generation is None
                or generation != pipeline.generation
            ):
                return False
            self._playing = False
            callback = self._on_finished
            epoch_callback = self._on_finished_epoch
        # Don't call sink.stop() from within its own pull on some backends;
        # schedule cleanup through the consumer callback instead.
        if callback:
            callback()
        if epoch_callback:
            epoch_callback(epoch)
        return True

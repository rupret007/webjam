"""Realtime-safe delivery engine for standalone Studio project rendering.

Rendering and click generation run on a producer thread.  The physical output
callback only copies from a preallocated SPSC ring (or fills silence while
paused), updates fixed counters, and returns.  It never opens media, logs,
waits, allocates NumPy buffers, or touches Jamulus configuration.
"""

from __future__ import annotations

import math
import operator
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Protocol

import numpy as np

from core.project_audio import (
    PROJECT_AUDIO_SAMPLE_RATE,
    GenerationGate,
    GenerationToken,
    PlaybackBlockRing,
)
from core.studio_renderer import (
    StudioRenderer,
    StudioRenderStream,
    studio_delivery_block,
)
from core.studio_tempo import TempoMap


class ProjectPlaybackError(RuntimeError):
    """Path-free playback failure suitable for musician-facing UI."""


class ProjectPlaybackState(str, Enum):
    EMPTY = "empty"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    FINISHED = "finished"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ProjectPlaybackSnapshot:
    state: ProjectPlaybackState
    generation: int
    position_frame: int
    timeline_end_frame: int
    loop_start_frame: int | None
    loop_end_frame: int | None
    metronome_enabled: bool
    delivered_frames: int
    underrun_frames: int
    stale_frames: int
    clipped_samples: int
    backend_status_events: int
    error: str = ""


class ProjectOutputBackend(Protocol):
    """Injected stereo 48-kHz output boundary."""

    sample_rate: int
    block_frames: int

    def start(self, callback: Callable[[np.ndarray], None]) -> None: ...

    def stop(self) -> None: ...

    def abort(self) -> None: ...


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
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _safe_error(_error: BaseException) -> str:
    return (
        "Project playback stopped safely. Verify the project media and output "
        "device, then try again."
    )


class ProjectPlaybackEngine:
    """One prepared renderer, one output backend, and one bounded producer."""

    def __init__(
        self,
        backend: ProjectOutputBackend,
        *,
        ring_capacity: int = 8,
        tempo_map: TempoMap | None = None,
    ) -> None:
        rate = _integer(
            getattr(backend, "sample_rate", 0),
            "backend.sample_rate",
            minimum=PROJECT_AUDIO_SAMPLE_RATE,
            maximum=PROJECT_AUDIO_SAMPLE_RATE,
        )
        block_frames = _integer(
            getattr(backend, "block_frames", 0),
            "backend.block_frames",
            minimum=1,
            maximum=65_536,
        )
        capacity = _integer(
            ring_capacity,
            "ring_capacity",
            minimum=2,
            maximum=4_096,
        )
        if not all(
            callable(getattr(backend, method, None))
            for method in ("start", "stop", "abort")
        ):
            raise ValueError("backend does not implement the project output contract")
        if tempo_map is not None and (
            not isinstance(tempo_map, TempoMap) or tempo_map.sample_rate != rate
        ):
            raise ValueError("tempo_map must use the project output sample rate")

        self.backend = backend
        self.sample_rate = rate
        self.block_frames = block_frames
        self.ring_capacity = capacity
        self._tempo_map = tempo_map or TempoMap.default(rate)
        self._renderer: StudioRenderer | None = None
        self._ring = PlaybackBlockRing(capacity, block_frames, 2)
        self._gate = GenerationGate()
        self._token: GenerationToken | None = None
        self._stream: StudioRenderStream | None = None
        self._producer: threading.Thread | None = None
        self._producer_wake = threading.Event()
        self._control_lock = threading.RLock()
        self._state = ProjectPlaybackState.EMPTY
        self._position_frame = 0
        self._start_frame = 0
        self._loop_start: int | None = None
        self._loop_end: int | None = None
        self._paused = False
        self._producer_eof = False
        self._callback_finished = False
        self._closed = False
        self._error = ""
        self._clipped_samples = 0
        self._backend_status_events = 0
        self._click_enabled = False
        self._click_regular, self._click_accent = self._build_clicks()

    def _build_clicks(self) -> tuple[np.ndarray, np.ndarray]:
        frames = max(16, round(self.sample_rate * 0.025))
        time = np.arange(frames, dtype=np.float32) / np.float32(self.sample_rate)
        envelope = np.exp(-time * np.float32(120.0)).astype(np.float32)

        def click(frequency: float, gain: float) -> np.ndarray:
            mono = (
                np.sin(time * np.float32(2.0 * math.pi * frequency))
                * envelope
                * np.float32(gain)
            ).astype(np.float32)
            result = np.empty((frames, 2), dtype=np.float32)
            result[:, 0] = mono
            result[:, 1] = mono
            result.setflags(write=False)
            return result

        return click(1_000.0, 0.16), click(1_500.0, 0.24)

    @property
    def state(self) -> ProjectPlaybackState:
        return self._state

    def set_renderer(
        self,
        renderer: StudioRenderer,
        *,
        tempo_map: TempoMap | None = None,
    ) -> None:
        if not isinstance(renderer, StudioRenderer):
            raise TypeError("renderer must be a StudioRenderer")
        if renderer.sample_rate != self.sample_rate:
            raise ProjectPlaybackError(
                "Project playback requires a 48-kHz Studio project."
            )
        if tempo_map is not None and (
            not isinstance(tempo_map, TempoMap)
            or tempo_map.sample_rate != self.sample_rate
        ):
            raise ValueError("tempo_map must use the project sample rate")
        with self._control_lock:
            self._require_open()
            if self._state in {
                ProjectPlaybackState.PLAYING,
                ProjectPlaybackState.PAUSED,
            }:
                raise ProjectPlaybackError(
                    "Stop project playback before replacing the arrangement."
                )
            self._renderer = renderer
            if tempo_map is not None:
                self._tempo_map = tempo_map
            self._position_frame = 0
            self._start_frame = 0
            self._loop_start = None
            self._loop_end = None
            self._error = ""
            self._state = ProjectPlaybackState.READY

    def set_metronome(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be true or false")
        with self._control_lock:
            self._require_open()
            self._click_enabled = enabled

    def set_loop(self, start_frame: int | None, end_frame: int | None) -> None:
        with self._control_lock:
            self._require_open()
            if (start_frame is None) != (end_frame is None):
                raise ValueError("loop start and end must be set together")
            if start_frame is None:
                self._loop_start = None
                self._loop_end = None
                return
            renderer = self._require_renderer()
            start = _integer(
                start_frame,
                "start_frame",
                minimum=0,
                maximum=renderer.timeline_end_frame,
            )
            end = _integer(
                end_frame,
                "end_frame",
                minimum=1,
                maximum=renderer.timeline_end_frame,
            )
            if end <= start:
                raise ValueError("loop end must follow loop start")
            if self._state in {
                ProjectPlaybackState.PLAYING,
                ProjectPlaybackState.PAUSED,
            }:
                raise ProjectPlaybackError("Stop playback before changing the loop.")
            self._loop_start = start
            self._loop_end = end

    def play(
        self,
        *,
        start_frame: int | None = None,
        allow_loop_lead_in: bool = False,
    ) -> None:
        if not isinstance(allow_loop_lead_in, bool):
            raise TypeError("allow_loop_lead_in must be true or false")
        with self._control_lock:
            self._require_open()
            renderer = self._require_renderer()
            if self._state is ProjectPlaybackState.PLAYING:
                return
            if self._state is ProjectPlaybackState.PAUSED and start_frame is None:
                self._paused = False
                self._state = ProjectPlaybackState.PLAYING
                self._producer_wake.set()
                return
            if self._producer is not None:
                self._stop_run(abort=False)
            start = (
                self._position_frame
                if start_frame is None
                else _integer(
                    start_frame,
                    "start_frame",
                    minimum=0,
                    maximum=renderer.timeline_end_frame,
                )
            )
            if self._loop_start is not None and self._loop_end is not None:
                if not (
                    allow_loop_lead_in
                    and start < self._loop_start
                ) and not self._loop_start <= start < self._loop_end:
                    start = self._loop_start
                stream_end = self._loop_end
            else:
                stream_end = renderer.timeline_end_frame
                if start >= stream_end:
                    start = 0
            self._start_frame = start
            self._position_frame = start
            self._ring = PlaybackBlockRing(
                self.ring_capacity,
                self.block_frames,
                2,
            )
            token = self._gate.issue()
            self._token = token
            self._producer_eof = False
            self._callback_finished = False
            self._paused = False
            self._error = ""
            try:
                stream = renderer.open(
                    start_frame=start,
                    end_frame=stream_end,
                    realtime_safe=True,
                )
                self._stream = stream
                # Prime the entire bounded ring before the physical stream starts.
                while self._ring.queued_blocks < self.ring_capacity:
                    if not self._produce_one(stream, token):
                        break
                self._producer = threading.Thread(
                    target=self._producer_loop,
                    args=(token,),
                    name=f"project-playback-producer-{token.generation}",
                    daemon=True,
                )
                self._producer.start()
                self.backend.start(self.process_output)
            except Exception as exc:
                self._gate.cancel()
                self._close_stream()
                self._producer = None
                self._error = _safe_error(exc)
                self._state = ProjectPlaybackState.FAILED
                try:
                    self.backend.abort()
                except Exception:
                    pass
                raise ProjectPlaybackError(self._error) from None
            self._state = ProjectPlaybackState.PLAYING

    def pause(self) -> None:
        with self._control_lock:
            self._require_open()
            if self._state is not ProjectPlaybackState.PLAYING:
                return
            self._paused = True
            self._state = ProjectPlaybackState.PAUSED

    def stop(self) -> None:
        with self._control_lock:
            self._require_open()
            self._stop_run(abort=False)
            # A clean user stop ends the failed run as well as the transport
            # thread.  Do not let a stale producer/device error overwrite a
            # later recording or arrangement success message on every poll.
            self._error = ""
            self._position_frame = 0
            self._state = (
                ProjectPlaybackState.READY
                if self._renderer is not None
                else ProjectPlaybackState.EMPTY
            )

    def seek(self, frame: int) -> int:
        with self._control_lock:
            self._require_open()
            renderer = self._require_renderer()
            target = _integer(
                frame,
                "frame",
                minimum=0,
                maximum=renderer.timeline_end_frame,
            )
            was_playing = self._state is ProjectPlaybackState.PLAYING
            was_paused = self._state is ProjectPlaybackState.PAUSED
            if was_playing or was_paused:
                self._stop_run(abort=False)
            self._position_frame = target
            self._state = ProjectPlaybackState.STOPPED
            if was_playing or was_paused:
                self.play(start_frame=target)
                if was_paused:
                    self.pause()
            return target

    def process_output(self, output: np.ndarray) -> None:
        """Physical output callback: fixed copies/counters only."""

        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != 2
            or output.shape[0] > self.block_frames
        ):
            self._backend_status_events += 1
            return
        if self._closed or self._paused or self._state not in {
            ProjectPlaybackState.PLAYING,
            ProjectPlaybackState.PAUSED,
        }:
            output.fill(0.0)
            return
        token = self._token
        if token is None:
            output.fill(0.0)
            return
        delivered = self._ring.pull_into(
            output,
            generation=token.generation,
        )
        if delivered:
            self._position_frame = self._ring.position_frame
        if (
            self._producer_eof
            and self._ring.queued_blocks == 0
            and delivered < len(output)
        ):
            self._callback_finished = True

    def poll(self) -> ProjectPlaybackSnapshot:
        """Finalize callback-reported EOF/error on a non-realtime thread."""

        with self._control_lock:
            self._require_open()
            if self._callback_finished and self._state is ProjectPlaybackState.PLAYING:
                self._stop_run(abort=False)
                self._state = ProjectPlaybackState.FINISHED
                if self._loop_start is None:
                    renderer = self._require_renderer()
                    self._position_frame = renderer.timeline_end_frame
            return self.snapshot()

    def snapshot(self) -> ProjectPlaybackSnapshot:
        renderer = self._renderer
        return ProjectPlaybackSnapshot(
            state=self._state,
            generation=self._token.generation if self._token is not None else 0,
            position_frame=int(self._position_frame),
            timeline_end_frame=(
                int(renderer.timeline_end_frame) if renderer is not None else 0
            ),
            loop_start_frame=self._loop_start,
            loop_end_frame=self._loop_end,
            metronome_enabled=self._click_enabled,
            delivered_frames=int(self._ring.delivered_frames),
            underrun_frames=int(self._ring.underrun_frames),
            stale_frames=int(self._ring.stale_frames),
            clipped_samples=int(self._clipped_samples),
            backend_status_events=int(self._backend_status_events),
            error=self._error,
        )

    def close(self) -> None:
        with self._control_lock:
            if self._closed:
                return
            self._stop_run(abort=True)
            self._renderer = None
            self._closed = True
            self._state = ProjectPlaybackState.CLOSED

    def _require_open(self) -> None:
        if self._closed:
            raise ProjectPlaybackError("Project playback is closed.")

    def _require_renderer(self) -> StudioRenderer:
        if self._renderer is None:
            raise ProjectPlaybackError("Open a Studio project before playback.")
        return self._renderer

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _stop_run(self, *, abort: bool) -> None:
        self._paused = True
        self._gate.cancel()
        self._producer_wake.set()
        try:
            if abort:
                self.backend.abort()
            else:
                self.backend.stop()
        except Exception:
            if not abort:
                try:
                    self.backend.abort()
                except Exception:
                    pass
        producer = self._producer
        if (
            producer is not None
            and producer.is_alive()
            and producer is not threading.current_thread()
        ):
            producer.join(timeout=2.0)
        self._producer = None
        self._close_stream()
        self._token = None
        self._producer_eof = False
        self._callback_finished = False
        self._paused = False

    def _producer_loop(self, token: GenerationToken) -> None:
        try:
            while token.current and not self._closed:
                stream = self._stream
                if stream is None:
                    return
                if self._ring.queued_blocks >= self.ring_capacity:
                    self._producer_wake.wait(0.002)
                    self._producer_wake.clear()
                    continue
                if not self._produce_one(stream, token):
                    if self._loop_start is not None and self._loop_end is not None:
                        stream.seek(self._loop_start)
                        continue
                    self._producer_eof = True
                    return
        except Exception as exc:
            self._error = _safe_error(exc)
            self._producer_eof = True

    def _produce_one(
        self,
        stream: StudioRenderStream,
        token: GenerationToken,
    ) -> bool:
        token.require_current()
        write_buffer = self._ring.acquire_write_buffer()
        if write_buffer is None:
            return True
        start_frame = int(stream.position_frame)
        block = stream.read(self.block_frames)
        token.require_current()
        if not len(block):
            return False
        frame_count = len(block)
        if self._click_enabled:
            self._mix_clicks(block, start_frame)
        delivered, clipped = studio_delivery_block(block)
        np.copyto(write_buffer[:frame_count], delivered)
        if frame_count < self.block_frames:
            write_buffer[frame_count:].fill(0.0)
        self._clipped_samples += clipped
        return self._ring.commit_write(
            frame_count,
            start_frame=start_frame,
            generation=token.generation,
        )

    def _mix_clicks(self, block: np.ndarray, start_frame: int) -> None:
        end_frame = start_frame + len(block)
        click_frames = len(self._click_regular)
        earliest = max(0, start_frame - click_frames + 1)
        first_beat = self._tempo_map.frame_to_beat(earliest)
        beat_number = max(0, math.floor(first_beat))
        if Fraction(beat_number, 1) < first_beat:
            beat_number += 1
        # Include the immediately preceding click tail when the block begins
        # after a beat boundary.
        beat_number = max(0, beat_number - 1)
        while True:
            click_frame = self._tempo_map.beat_to_frame(beat_number)
            if click_frame >= end_frame:
                return
            click_end = click_frame + click_frames
            if click_end > start_frame:
                try:
                    accented = (
                        self._tempo_map.frame_to_bar_position(click_frame).beat_number
                        == 1
                    )
                except Exception:
                    accented = beat_number == 0
                click = self._click_accent if accented else self._click_regular
                destination_start = max(start_frame, click_frame)
                destination_end = min(end_frame, click_end)
                source_start = destination_start - click_frame
                source_end = source_start + destination_end - destination_start
                block[
                    destination_start - start_frame : destination_end - start_frame
                ] += click[source_start:source_end]
            beat_number += 1


class SoundDeviceProjectOutputBackend:
    """Small control-plane adapter around sounddevice.OutputStream."""

    def __init__(
        self,
        *,
        block_frames: int = 512,
        device: str | int | None = None,
        sounddevice_module=None,
    ) -> None:
        self.sample_rate = PROJECT_AUDIO_SAMPLE_RATE
        self.block_frames = _integer(
            block_frames,
            "block_frames",
            minimum=1,
            maximum=65_536,
        )
        self.device = device
        self._sounddevice = sounddevice_module
        self._stream = None
        self.status_events = 0

    def start(self, callback: Callable[[np.ndarray], None]) -> None:
        if self._stream is not None:
            raise ProjectPlaybackError("Project output is already running.")
        module = self._sounddevice
        if module is None:
            try:
                import sounddevice as module  # type: ignore
            except Exception:
                raise ProjectPlaybackError(
                    "Project audio output is unavailable in this build."
                ) from None

        def output_callback(outdata, _frames, _time_info, status) -> None:
            if status:
                self.status_events += 1
            callback(outdata)

        try:
            stream = module.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_frames,
                channels=2,
                dtype="float32",
                device=self.device,
                callback=output_callback,
            )
            stream.start()
        except Exception:
            raise ProjectPlaybackError(
                "WebJam couldn't open the selected Studio output device."
            ) from None
        self._stream = stream

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    def abort(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.abort()
        finally:
            stream.close()


__all__ = [
    "ProjectOutputBackend",
    "ProjectPlaybackEngine",
    "ProjectPlaybackError",
    "ProjectPlaybackSnapshot",
    "ProjectPlaybackState",
    "SoundDeviceProjectOutputBackend",
]

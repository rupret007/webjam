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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

import numpy as np

_logger = logging.getLogger("webjam.take_player")

DEFAULT_SAMPLERATE = 48000
DEFAULT_BLOCKSIZE = 1024


class PlaybackError(RuntimeError):
    """Raised when Take Deck playback cannot start safely."""


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
    offset_s: float = 0.0
    source: str = "jamulus_server"
    gain: float = 1.0        # 0..~1.27 (fader/100)
    pan: float = 0.0         # -1.0 left .. 0 centre .. +1.0 right
    muted: bool = False
    solo: bool = False
    level: float = 0.0       # last block RMS, 0..1 (for meters)
    _reader: object = field(default=None, repr=False)
    _eof: bool = False
    segments: tuple[TrackSegmentState, ...] = field(default_factory=tuple, repr=False)
    drift_ppm: float = 0.0


class OutputSink(Protocol):
    """Something that consumes mixed audio blocks."""

    def start(self, samplerate: int, blocksize: int,
              pull: Callable[[int], np.ndarray]) -> None:
        """Begin playback. ``pull(n)`` returns the next ``n`` stereo frames."""
        ...

    def stop(self) -> None:
        ...


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
            samplerate=samplerate, blocksize=blocksize,
            channels=2, dtype="float32", callback=_callback,
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

    Thread-safety: mixer mutations (gain/mute/solo/seek) take ``_lock``; the
    pull path also takes it, so the audio callback always sees a consistent
    snapshot. Level/position callbacks are invoked outside the lock.
    """

    def __init__(
        self,
        samplerate: int = DEFAULT_SAMPLERATE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        sink: Optional[OutputSink] = None,
        on_position: Optional[Callable[[float], None]] = None,
        on_levels: Optional[Callable[[Dict[int, float]], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self._sink = sink or SoundDeviceSink()
        self._on_position = on_position
        self._on_levels = on_levels
        self._on_finished = on_finished

        self._tracks: List[TrackState] = []
        self._lock = threading.RLock()
        self._playing = False
        self._pos_frames = 0
        self._total_frames = 0

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
            self._tracks = []
            # Schema-v2 owns an explicit project rate. Legacy takes adopt the
            # most common non-zero source rate. Individual files are converted
            # to this timeline while streaming, so mixed supported rates never
            # play at the wrong pitch or duration.
            _rates = [int(getattr(t, "samplerate", 0) or 0)
                      for t in getattr(take, "tracks", [])]
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
                    sorted(_distinct), self.samplerate,
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
                            source_frames
                            / source_rate
                            * drift_scale
                            * self.samplerate
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
                        offset_frames + int(round(duration * drift_scale * self.samplerate)),
                    )
                self._tracks.append(TrackState(
                    channel_id=i,
                    name=getattr(t, "name", f"Track {i}"),
                    path=Path(getattr(t, "path")),
                    offset_s=offset_s,
                    source=str(getattr(t, "source", "jamulus_server")),
                    segments=tuple(segment_states),
                    drift_ppm=drift_ppm,
                ))
            self._total_frames = longest
            self._pos_frames = 0

    @property
    def tracks(self) -> List[TrackState]:
        return list(self._tracks)

    @property
    def duration_s(self) -> float:
        return self._total_frames / self.samplerate if self.samplerate else 0.0

    @property
    def position_s(self) -> float:
        return self._pos_frames / self.samplerate if self.samplerate else 0.0

    @property
    def is_playing(self) -> bool:
        return self._playing

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
                    t.gain = max(0.0, float(gain))

    def set_muted(self, channel_id: int, muted: bool) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.muted = bool(muted)

    def set_pan(self, channel_id: int, pan: float) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.pan = max(-1.0, min(1.0, float(pan)))

    def set_solo(self, channel_id: int, solo: bool) -> None:
        with self._lock:
            for t in self._tracks:
                if t.channel_id == channel_id:
                    t.solo = bool(solo)

    # -- transport --------------------------------------------------------
    def play(self) -> None:
        if self._playing:
            return
        with self._lock:
            # Replaying after the take finished: rewind instead of starting a
            # stream that would immediately sit at EOF emitting silence.
            if self._pos_frames >= self._total_frames:
                self._pos_frames = 0
                self._close_readers()
            self._playing = True
        self._open_readers()
        try:
            self._sink.start(self.samplerate, self.blocksize, self._pull)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._playing = False
                self._close_readers()
            raise PlaybackError(f"Couldn't open the playback device: {exc}") from exc

    def pause(self) -> None:
        self._playing = False
        self._sink.stop()

    def stop(self) -> None:
        self._playing = False
        self._sink.stop()
        with self._lock:
            self._pos_frames = 0
            self._close_readers()

    def seek(self, seconds: float) -> None:
        with self._lock:
            target = int(max(0.0, seconds) * self.samplerate)
            self._pos_frames = min(target, self._total_frames)
            # Recreate readers so their next pull seeks from one consistent
            # timeline position.  Playback may already be running (Studio's
            # scrubber seeks without stopping the output stream), so leaving
            # the readers closed here would turn every subsequent block into
            # silence until the user manually stopped and pressed Play again.
            self._close_readers()
            if self._playing:
                self._open_readers()
        if self._on_position:
            self._on_position(self.position_s)

    # -- readers ----------------------------------------------------------
    def _open_readers(self) -> None:
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
                    segment.frame_count
                    / segment.samplerate
                    * scale
                    * self.samplerate
                )
            )
            segment_end = segment_start + max(0, rendered_frames)
            overlap_start = max(start, segment_start)
            overlap_end = min(start + n, segment_end)
            if overlap_end <= overlap_start:
                continue
            output_positions = np.arange(
                overlap_start, overlap_end, dtype=np.float64
            )
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
                source = reader.read(
                    last - first, dtype="float32", always_2d=True
                )
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

    def _pull(self, frames: int) -> np.ndarray:
        """Mix the next ``frames`` frames. Called from the audio thread."""
        with self._lock:
            if not self._tracks or self._pos_frames >= self._total_frames:
                self._finish_if_needed()
                return np.zeros((frames, 2), dtype=np.float32)

            start = self._pos_frames
            any_solo = any(t.solo for t in self._tracks)
            mix = np.zeros((frames, 2), dtype=np.float32)
            levels: Dict[int, float] = {}
            for t in self._tracks:
                audible = (not t.muted) and (t.solo or not any_solo)
                block = self._read_track_block(t, start, frames)
                if audible and t.gain > 0.0:
                    contribution = self._pan_block(block, t.pan) * t.gain
                    mix += contribution
                    rms = float(np.sqrt(np.mean(np.square(contribution)))) \
                        if frames else 0.0
                    t.level = min(1.0, rms * 3.0)  # visual scaling
                else:
                    t.level = 0.0
                levels[t.channel_id] = t.level

            # Hard-limit to protect ears/speakers when many tracks stack.
            np.clip(mix, -1.0, 1.0, out=mix)
            self._pos_frames += frames
            pos_s = self.position_s
            finished = self._pos_frames >= self._total_frames

        if self._on_levels:
            self._on_levels(levels)
        if self._on_position:
            self._on_position(pos_s)
        if finished:
            self._finish_if_needed()
        return mix

    def _finish_if_needed(self) -> None:
        if self._playing:
            self._playing = False
            # Don't call sink.stop() from within its own pull on some
            # backends; schedule via callback instead.
            if self._on_finished:
                self._on_finished()

"""
TakePlayer — multitrack playback engine for the Take Deck.

Plays back a recorded take: each track is a streaming file reader, mixed on
a numpy bus with per-track gain / mute / solo and per-track start offsets,
into a single output stream. This is the "review the jam" heart of the Demo
Deck — it deliberately does NOT edit, trim, or apply effects (that's the
DAW's job; the take always keeps its Reaper-project escape hatch).

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


@dataclass
class TrackState:
    """Per-track mixer state (mutated live from the UI)."""
    channel_id: int
    name: str
    path: Path
    offset_s: float = 0.0
    gain: float = 1.0        # 0..~1.27 (fader/100)
    muted: bool = False
    solo: bool = False
    level: float = 0.0       # last block RMS, 0..1 (for meters)
    _reader: object = field(default=None, repr=False)
    _eof: bool = False


class OutputSink(Protocol):
    """Something that consumes mixed audio blocks."""

    def start(self, samplerate: int, blocksize: int,
              pull: Callable[[int], np.ndarray]) -> None:
        """Begin playback. ``pull(n)`` returns the next ``n`` mono frames as
        a float32 array (may be shorter than n at end-of-take)."""
        ...

    def stop(self) -> None:
        ...


class SoundDeviceSink:
    """Real audio output via sounddevice (imported lazily)."""

    def __init__(self) -> None:
        self._stream = None

    def start(self, samplerate, blocksize, pull) -> None:
        import sounddevice as sd  # type: ignore

        def _callback(outdata, frames, time_info, status):  # noqa: ANN001
            if status:
                _logger.debug("sounddevice status: %s", status)
            block = pull(frames)
            if block.shape[0] < frames:
                outdata[:block.shape[0], 0] = block
                outdata[block.shape[0]:, 0] = 0.0
            else:
                outdata[:, 0] = block[:frames]

        self._stream = sd.OutputStream(
            samplerate=samplerate, blocksize=blocksize,
            channels=1, dtype="float32", callback=_callback,
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
            # Adopt the take's samplerate (most common non-zero rate across
            # its tracks); keep the current default if none is known.
            _rates = [int(getattr(t, "samplerate", 0) or 0)
                      for t in getattr(take, "tracks", [])]
            _rates = [r for r in _rates if r > 0]
            if _rates:
                from collections import Counter
                self.samplerate = Counter(_rates).most_common(1)[0][0]
                _distinct = set(_rates)
                if len(_distinct) > 1:
                    _logger.warning(
                        "take has mixed track samplerates %s; playing all at "
                        "%d Hz (other-rate tracks will be off-pitch)",
                        sorted(_distinct), self.samplerate,
                    )
            longest = 0
            for i, t in enumerate(getattr(take, "tracks", [])):
                offset_frames = int(max(0.0, getattr(t, "offset_s", 0.0))
                                    * self.samplerate)
                dur = getattr(t, "duration_s", 0.0) or 0.0
                end = offset_frames + int(dur * self.samplerate)
                longest = max(longest, end)
                self._tracks.append(TrackState(
                    channel_id=i,
                    name=getattr(t, "name", f"Track {i}"),
                    path=Path(getattr(t, "path")),
                    offset_s=max(0.0, getattr(t, "offset_s", 0.0)),
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
        self._sink.start(self.samplerate, self.blocksize, self._pull)

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
            # Force readers to re-seek on next pull.
            self._close_readers()
        if self._on_position:
            self._on_position(self.position_s)

    # -- readers ----------------------------------------------------------
    def _open_readers(self) -> None:
        import soundfile as sf  # type: ignore
        with self._lock:
            for t in self._tracks:
                if t._reader is None:
                    try:
                        t._reader = sf.SoundFile(str(t.path))
                    except Exception as exc:  # noqa: BLE001
                        _logger.warning("can't open %s: %s", t.path, exc)
                        t._reader = None
                    t._eof = False

    def _close_readers(self) -> None:
        for t in self._tracks:
            if t._reader is not None:
                try:
                    t._reader.close()
                except Exception:  # noqa: BLE001
                    pass
                t._reader = None
            t._eof = False

    def _read_track_block(self, t: TrackState, start: int, n: int) -> np.ndarray:
        """Return n mono frames from track ``t`` for absolute take-timeline
        window [start, start+n), honouring the track's offset. Silence where
        the track isn't sounding (before its offset or past its end)."""
        out = np.zeros(n, dtype=np.float32)
        reader = t._reader
        if reader is None:
            return out
        offset_frames = int(t.offset_s * self.samplerate)
        track_start = start - offset_frames  # first file-frame this block wants
        # The window [track_start, track_start+n) intersected with the file.
        file_len = len(reader)
        read_from = max(0, track_start)          # file position to seek to
        if read_from >= file_len:
            return out                            # entirely past end -> silence
        dest_start = read_from - track_start      # where it lands in ``out``
        want = min(n - dest_start, file_len - read_from)
        if want <= 0:
            return out
        try:
            if reader.tell() != read_from:
                reader.seek(read_from)
            data = reader.read(want, dtype="float32", always_2d=True)
        except Exception:  # noqa: BLE001
            return out
        if data.shape[0] == 0:
            return out
        mono = data.mean(axis=1)                  # downmix channels
        out[dest_start:dest_start + mono.shape[0]] = mono
        return out

    def _pull(self, frames: int) -> np.ndarray:
        """Mix the next ``frames`` frames. Called from the audio thread."""
        with self._lock:
            if not self._tracks or self._pos_frames >= self._total_frames:
                self._finish_if_needed()
                return np.zeros(frames, dtype=np.float32)

            start = self._pos_frames
            any_solo = any(t.solo for t in self._tracks)
            mix = np.zeros(frames, dtype=np.float32)
            levels: Dict[int, float] = {}
            for t in self._tracks:
                audible = (not t.muted) and (t.solo or not any_solo)
                block = self._read_track_block(t, start, frames)
                if audible and t.gain > 0.0:
                    contribution = block * t.gain
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

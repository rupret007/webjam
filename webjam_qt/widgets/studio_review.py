"""Focused review-lane, legacy waveform, ruler, and meter widgets for Studio.

These presentation components intentionally know nothing about Studio project
persistence, playback ownership, export, or take switching.  RecordingStudio
coordinates them and the immutable arrangement/controller remains elsewhere.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import threading
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.take_library import TakeInfo
from webjam_qt.theme.tokens import Color, Space


LOGGER = logging.getLogger("webjam.qt.recording_studio")

TRACK_LANE_HEADER_WIDTH = 324
_WAVEFORM_BUCKETS = 720
_WAVEFORM_CHUNK_FRAMES = 65_536
_WAVEFORM_CACHE_ENTRIES = 64
_WaveformSourceKey = tuple[object, ...]


class _WaveformBuildCancelled(RuntimeError):
    """Internal cooperative cancellation for a no-longer-visible take."""


@dataclass(frozen=True)
class _WaveformSegmentSpec:
    path: Path
    project_start_frame: int
    frame_count: int
    samplerate: int
    channels: int
    gaps: tuple[tuple[int, int, tuple[int, ...], str], ...] = ()


@dataclass(frozen=True)
class _CompositeWaveformSpec:
    segments: tuple[_WaveformSegmentSpec, ...]
    project_samplerate: int
    offset_s: float
    drift_ppm: float
    timeline_duration_s: float


def _waveform_source_key(path: Path) -> _WaveformSourceKey:
    """Return a cheap cache key that changes when the source identity changes."""
    source = Path(path)
    stat = source.stat()
    return (
        str(source.resolve()),
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


class _WaveformPeakCache:
    """Small thread-safe LRU for bounded waveform envelopes."""

    def __init__(self, max_entries: int = _WAVEFORM_CACHE_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._items: OrderedDict[_WaveformSourceKey, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: _WaveformSourceKey) -> Optional[tuple[float, ...]]:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: _WaveformSourceKey, peaks: tuple[float, ...]) -> None:
        with self._lock:
            self._items[key] = tuple(peaks)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)


def _fmt_time(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 60}:{value % 60:02d}"


def _fmt_db(gain: float) -> str:
    """Return a compact, honest gain label for the non-destructive Studio mix."""
    value = max(0.0, float(gain))
    if value <= 0.0001:
        return "−∞ dB"
    return f"{20.0 * math.log10(value):+.1f} dB"


def _timeline_tick_positions(duration: float, width: int) -> tuple[float, ...]:
    """Return a small shared seconds-only grid; no musical tempo is implied."""
    # Eight divisions keep the waveform lanes readable at the 760 px supported
    # workspace floor while staying stable across every track in a take.
    _ = max(1, int(width))
    span = max(1.0, float(duration))
    return tuple(span * index / 8.0 for index in range(9))


def _source_key(source: object) -> str:
    """Normalize persisted source values and their enum equivalents."""

    return str(getattr(source, "value", source) or "").strip().casefold()


def _is_shared_track_source(source: object) -> bool:
    """Return whether a recorded lane is the canonical live Shared Track."""

    return _source_key(source) == "live_reference"


def _is_synchronized_source(source: object) -> bool:
    """Return whether recorder evidence places the source on the server timeline."""

    return _source_key(source) in {"jamulus_server", "live_reference"}


def _safe_source_label(source: object) -> str:
    """Return stable musician-facing source labels without exposing file paths."""

    return {
        "jamulus_server": "MUSICIAN",
        "live_reference": "SHARED TRACK",
        "local_ssl": "LOCAL ORIGINAL",
        "local_isolated": "LOCAL ORIGINAL",
    }.get(_source_key(source), "TRACK")


def _safe_source_description(source: object) -> str:
    """Describe a source truthfully without treating unknown media as local."""

    return {
        "jamulus_server": "Musician (band server track)",
        "live_reference": "Shared Track",
        "local_ssl": "Local Original",
        "local_isolated": "Local Original",
    }.get(_source_key(source), "Recorded track")


class StudioTimelineRuler(QWidget):
    """Shared elapsed-time ruler and playhead for every Studio waveform lane.

    WebJam does not infer tempo, bars, or beats from a rehearsal.  The ruler
    therefore exposes only the one thing every recorded source can truthfully
    share: elapsed project time.  Clicking or dragging it seeks review playback
    but never changes capture media or project timing.
    """

    seek_requested = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StudioRuler")
        self.setMinimumHeight(34)
        self.setMaximumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAccessibleName("Shared recording timeline")
        self.setAccessibleDescription(
            "Elapsed time shared by every track. Click or drag to seek the open take."
        )
        self._duration = 1.0
        self._playhead = 0.0
        self._seek_enabled = False
        self._dragging = False
        self._trailing_inset = 8

    def set_timeline(
        self,
        *,
        duration: float,
        playhead: float = 0.0,
        seek_enabled: bool,
    ) -> None:
        self._duration = max(1.0, float(duration))
        self._playhead = max(0.0, min(float(playhead), self._duration))
        self._seek_enabled = bool(seek_enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._seek_enabled
            else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def set_trailing_inset(self, pixels: int) -> None:
        """Reserve the track-scrollbar width so every lane stays time-aligned."""
        inset = max(8, int(pixels))
        if inset != self._trailing_inset:
            self._trailing_inset = inset
            self.update()

    def _timeline_rect(self):
        return self.rect().adjusted(8, 0, -self._trailing_inset, 0)

    def _seconds_at(self, x: float) -> float:
        rect = self._timeline_rect()
        if rect.width() <= 0:
            return 0.0
        fraction = (float(x) - rect.left()) / rect.width()
        return max(0.0, min(self._duration, fraction * self._duration))

    def _emit_seek(self, x: float) -> None:
        if self._seek_enabled:
            self.seek_requested.emit(self._seconds_at(x))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._seek_enabled:
            self._dragging = True
            self._emit_seek(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and self._seek_enabled:
            self._emit_seek(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._emit_seek(event.position().x())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor(Color.BG_INPUT))
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        timeline = self._timeline_rect()
        ticks = _timeline_tick_positions(self._duration, timeline.width())
        label_step = 2 if timeline.width() < 420 else 1
        last_label = ""
        for index, seconds in enumerate(ticks):
            x = timeline.left() + timeline.width() * seconds / self._duration
            painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
            painter.drawLine(int(x), rect.top() + 14, int(x), rect.bottom())
            label = _fmt_time(seconds)
            if (
                index % label_step == 0 or index == len(ticks) - 1
            ) and label != last_label:
                painter.setPen(QPen(QColor(Color.TEXT_MUTED), 1))
                if index == 0:
                    painter.drawText(int(x) + 2, rect.top() + 11, label)
                elif index == len(ticks) - 1:
                    width = painter.fontMetrics().horizontalAdvance(label)
                    painter.drawText(int(x) - width - 2, rect.top() + 11, label)
                else:
                    painter.drawText(int(x) + 2, rect.top() + 11, label)
                last_label = label
        playhead_x = (
            timeline.left() + timeline.width() * self._playhead / self._duration
        )
        painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 2))
        painter.drawLine(int(playhead_x), rect.top(), int(playhead_x), rect.bottom())
        painter.setBrush(QColor(Color.ACCENT_PRIMARY))
        painter.setPen(Qt.PenStyle.NoPen)
        marker = QPainterPath()
        marker.moveTo(int(playhead_x) - 4, rect.top())
        marker.lineTo(int(playhead_x) + 4, rect.top())
        marker.lineTo(int(playhead_x), rect.top() + 6)
        marker.closeSubpath()
        painter.drawPath(marker)
        painter.end()


class TrackLevelMeter(QWidget):
    """Compact stereo peaks with a latched-over-one-block clip indicator."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StudioTrackMeter")
        self.setFixedSize(38, 24)
        self.setAccessibleName("Stereo track peak level")
        self.setAccessibleDescription("Current left and right track peak levels.")
        self._left = 0.0
        self._right = 0.0
        self._level = 0.0
        self._clipped = False

    def set_level(self, level: float) -> None:
        value = max(0.0, min(1.0, float(level)))
        self.set_stereo_levels(value, value, clipped=False)

    def set_stereo_levels(
        self,
        left: float,
        right: float,
        *,
        clipped: bool,
    ) -> None:
        self._left = max(0.0, min(1.0, float(left)))
        self._right = max(0.0, min(1.0, float(right)))
        self._level = max(self._left, self._right)
        self._clipped = bool(clipped)
        clip_text = " Clip detected." if self._clipped else ""
        self.setAccessibleDescription(
            f"Left peak {round(self._left * 100)} percent; right peak "
            f"{round(self._right * 100)} percent.{clip_text}"
        )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor(Color.BG_INPUT))
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawRoundedRect(rect, 3, 3)
        fill = rect.adjusted(4, 5, -4, -3)
        bar_width = max(2, (fill.width() - 3) // 2)
        for index, level in enumerate((self._left, self._right)):
            height = int(fill.height() * level)
            if not height:
                continue
            left = fill.left() + index * (bar_width + 3)
            level_rect = fill.adjusted(0, fill.height() - height, 0, 0)
            level_rect.setLeft(left)
            level_rect.setWidth(bar_width)
            painter.fillRect(level_rect, QColor(Color.ACCENT_PRIMARY))
        clip_color = QColor(
            Color.ACCENT_DANGER if self._clipped else Color.BORDER_SUBTLE
        )
        painter.fillRect(rect.right() - 7, rect.top() + 2, 4, 2, clip_color)
        scale_color = QColor(Color.TEXT_MUTED)
        scale_color.setAlpha(88)
        painter.setPen(QPen(scale_color, 1))
        for fraction in (0.25, 0.5, 0.75):
            y = round(fill.bottom() - fill.height() * fraction)
            painter.drawLine(fill.left(), y, fill.right(), y)
        painter.end()


class _CompactComboBox(QComboBox):
    """Keep long hardware names from setting the window's minimum width."""

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(140)
        return hint

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(min(220, hint.width()))
        return hint


def _waveform_peaks(
    path: Path,
    buckets: int = _WAVEFORM_BUCKETS,
    *,
    chunk_frames: int = _WAVEFORM_CHUNK_FRAMES,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[float, ...]:
    """Build a truthful bounded envelope by streaming every source frame.

    Each output value is the maximum absolute sample in one exact, contiguous
    timeline bucket.  Unlike the former sparse sampler, this cannot miss a
    transient between probe windows.  Reads stay chunk-bounded and the
    ``SoundFile`` context closes the handle on success, failure, or cancellation.
    """
    if int(buckets) <= 0:
        raise ValueError("buckets must be positive")
    if int(chunk_frames) <= 0:
        raise ValueError("chunk_frames must be positive")
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        with sf.SoundFile(str(path)) as audio:
            total = len(audio)
            if total <= 0:
                return ()
            count = max(1, min(int(buckets), total))
            values: list[float] = []
            for index in range(count):
                if cancel_event is not None and cancel_event.is_set():
                    raise _WaveformBuildCancelled
                start = index * total // count
                end = (index + 1) * total // count
                if audio.tell() != start:
                    audio.seek(start)
                remaining = end - start
                peak = 0.0
                while remaining > 0:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _WaveformBuildCancelled
                    block = audio.read(
                        min(int(chunk_frames), remaining),
                        dtype="float32",
                        always_2d=True,
                    )
                    frames_read = int(block.shape[0])
                    if frames_read <= 0:
                        break
                    peak = max(peak, float(np.max(np.abs(block))))
                    remaining -= frames_read
                values.append(min(1.0, peak))
            return tuple(values)
    except _WaveformBuildCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Could not build waveform for %s: %s", path, exc)
        return ()


def _composite_waveform_key(spec: _CompositeWaveformSpec) -> _WaveformSourceKey:
    items: list[object] = [
        "composite-v1",
        spec.project_samplerate,
        round(spec.offset_s, 9),
        round(spec.drift_ppm, 9),
        round(spec.timeline_duration_s, 9),
    ]
    for segment in spec.segments:
        items.extend(
            (
                *_waveform_source_key(segment.path),
                segment.project_start_frame,
                segment.frame_count,
                segment.samplerate,
                segment.channels,
                segment.gaps,
            )
        )
    return tuple(items)


def _composite_waveform_peaks(
    spec: _CompositeWaveformSpec,
    buckets: int = _WAVEFORM_BUCKETS,
    *,
    chunk_frames: int = _WAVEFORM_CHUNK_FRAMES,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[float, ...]:
    """Inspect every segment frame while retaining reconnect gaps on screen."""
    if buckets <= 0 or chunk_frames <= 0:
        raise ValueError("buckets and chunk_frames must be positive")
    if spec.project_samplerate <= 0 or spec.timeline_duration_s <= 0:
        return ()
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        scale = 1.0 + spec.drift_ppm / 1_000_000.0
        if scale <= 0.0:
            return ()
        count = max(
            1,
            min(
                int(buckets),
                int(round(spec.timeline_duration_s * spec.project_samplerate)),
            ),
        )
        values = [0.0] * count
        with ExitStack() as stack:
            readers = [
                stack.enter_context(sf.SoundFile(str(segment.path)))
                for segment in spec.segments
            ]
            for bucket in range(count):
                if cancel_event is not None and cancel_event.is_set():
                    raise _WaveformBuildCancelled
                project_start_s = bucket / count * spec.timeline_duration_s
                project_end_s = (bucket + 1) / count * spec.timeline_duration_s
                peak = 0.0
                for segment, reader in zip(spec.segments, readers):
                    segment_start_s = (
                        segment.project_start_frame / spec.project_samplerate
                        + spec.offset_s
                    )
                    segment_end_s = (
                        segment_start_s
                        + segment.frame_count / segment.samplerate * scale
                    )
                    overlap_start = max(project_start_s, segment_start_s)
                    overlap_end = min(project_end_s, segment_end_s)
                    if overlap_end <= overlap_start:
                        continue
                    source_start = max(
                        0,
                        int(
                            np.floor(
                                (overlap_start - segment_start_s)
                                / scale
                                * segment.samplerate
                            )
                        ),
                    )
                    source_end = min(
                        segment.frame_count,
                        int(
                            np.ceil(
                                (overlap_end - segment_start_s)
                                / scale
                                * segment.samplerate
                            )
                        ),
                    )
                    if source_end <= source_start:
                        continue
                    reader.seek(source_start)
                    cursor = source_start
                    while cursor < source_end:
                        if cancel_event is not None and cancel_event.is_set():
                            raise _WaveformBuildCancelled
                        block = reader.read(
                            min(chunk_frames, source_end - cursor),
                            dtype="float32",
                            always_2d=True,
                        )
                        if not len(block):
                            break
                        if segment.gaps:
                            block = block.copy()
                            block_end = cursor + len(block)
                            for (
                                gap_start,
                                gap_count,
                                gap_channels,
                                _reason,
                            ) in segment.gaps:
                                gap_end = gap_start + gap_count
                                if gap_end <= cursor or gap_start >= block_end:
                                    continue
                                lo = max(cursor, gap_start) - cursor
                                hi = min(block_end, gap_end) - cursor
                                channels = gap_channels or tuple(range(block.shape[1]))
                                for channel in channels:
                                    if 0 <= channel < block.shape[1]:
                                        block[lo:hi, channel] = 0.0
                        peak = max(peak, float(np.max(np.abs(block))))
                        cursor += len(block)
                values[bucket] = min(1.0, peak)
        return tuple(values)
    except _WaveformBuildCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Could not build composite waveform: %s", exc)
        return ()


def _waveform_spec_for_track(track, take: TakeInfo) -> _CompositeWaveformSpec | None:
    raw_segments = tuple(getattr(track, "segments", ()) or ())
    if not raw_segments:
        return None
    project_rate = int(getattr(take, "project_samplerate", 0) or 0)
    if project_rate <= 0:
        project_rate = int(getattr(track, "samplerate", 0) or 0)
    segments = tuple(
        _WaveformSegmentSpec(
            path=Path(segment.path),
            project_start_frame=int(segment.project_start_frame),
            frame_count=int(segment.frame_count),
            samplerate=int(segment.samplerate),
            channels=int(segment.channels),
            gaps=tuple(segment.gaps),
        )
        for segment in raw_segments
        if int(getattr(segment, "samplerate", 0) or 0) > 0
        and int(getattr(segment, "frame_count", 0) or 0) > 0
    )
    if not segments or project_rate <= 0:
        return None
    needs_composite = (
        len(segments) > 1
        or any(segment.project_start_frame or segment.gaps for segment in segments)
        or any(segment.samplerate != project_rate for segment in segments)
        or bool(float(getattr(track, "drift_ppm", 0.0) or 0.0))
    )
    if not needs_composite:
        return None
    return _CompositeWaveformSpec(
        segments=segments,
        project_samplerate=project_rate,
        offset_s=float(getattr(track, "offset_s", 0.0) or 0.0),
        drift_ppm=float(getattr(track, "drift_ppm", 0.0) or 0.0),
        timeline_duration_s=max(0.001, float(take.duration_s)),
    )


def _timeline_gaps_for_track(
    track,
    take: TakeInfo,
) -> tuple[tuple[float, float, str], ...]:
    """Project explicit segment gaps onto the shared seconds-only timeline."""
    project_rate = int(getattr(take, "project_samplerate", 0) or 0)
    if project_rate <= 0:
        project_rate = int(getattr(track, "samplerate", 0) or 0)
    if project_rate <= 0:
        return ()
    scale = 1.0 + float(getattr(track, "drift_ppm", 0.0) or 0.0) / 1_000_000.0
    if scale <= 0.0:
        scale = 1.0
    offset_s = float(getattr(track, "offset_s", 0.0) or 0.0)
    ranges: list[tuple[float, float, str]] = []
    for segment in tuple(getattr(track, "segments", ()) or ()):
        rate = int(getattr(segment, "samplerate", 0) or 0)
        if rate <= 0:
            continue
        start = (
            int(getattr(segment, "project_start_frame", 0) or 0) / project_rate
            + offset_s
        )
        for gap_start, gap_frames, _channels, reason in tuple(
            getattr(segment, "gaps", ()) or ()
        ):
            length = max(0.0, float(gap_frames) / rate * scale)
            if length:
                ranges.append(
                    (
                        max(0.0, start + float(gap_start) / rate * scale),
                        length,
                        str(reason or "recording gap"),
                    )
                )
    return tuple(ranges)


class WaveformCanvas(QWidget):
    """Small DAW-like clip lane for a recorded file or live input history."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WaveformCanvas")
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Track waveform")
        self.setAccessibleDescription(
            "A visual overview of this track. Use the transport controls to play it."
        )
        self._peaks: tuple[float, ...] = ()
        self._history: list[float] = []
        self._offset = 0.0
        self._clip_duration = 0.0
        self._timeline_duration = 1.0
        self._playhead = 0.0
        self._live = False
        self._recording = False
        self._source = "jamulus_server"
        self._gaps: tuple[tuple[float, float, str], ...] = ()
        self._selected = False

    def set_recorded_clip(
        self,
        *,
        peaks: tuple[float, ...],
        offset: float,
        duration: float,
        timeline_duration: float,
        source: str,
        gaps: tuple[tuple[float, float, str], ...] = (),
    ) -> None:
        self._peaks = peaks
        self._history = []
        self._offset = float(offset)
        self._clip_duration = max(0.0, float(duration))
        self._timeline_duration = max(0.001, float(timeline_duration))
        self._source = str(source)
        self._gaps = tuple(
            (max(0.0, float(start)), max(0.0, float(length)), str(reason))
            for start, length, reason in gaps
            if float(length) > 0.0
        )
        self._live = False
        self._recording = False
        self.setAccessibleDescription(
            f"Recorded track waveform, {_fmt_time(duration)} long. "
            "Use the transport controls to play it."
        )
        self.update()

    def set_live(self, recording: bool) -> None:
        self._peaks = ()
        self._history = []
        self._offset = 0.0
        self._clip_duration = 0.0
        self._timeline_duration = 30.0
        self._playhead = 0.0
        self._gaps = ()
        self._live = True
        self._recording = bool(recording)
        self.setAccessibleDescription(
            "Live track waveform. "
            + ("Recording is active." if recording else "The track is armed.")
        )
        self.update()

    def set_recording(self, recording: bool) -> None:
        self._recording = bool(recording)
        self.update()

    def set_peaks(self, peaks: tuple[float, ...]) -> None:
        """Apply an asynchronously generated envelope to a recorded lane."""
        if self._live:
            return
        self._peaks = tuple(peaks)
        self.update()

    def push_level(self, level: float) -> None:
        if not self._live:
            return
        self._history.append(max(0.0, min(1.0, float(level))))
        if len(self._history) > 1200:
            del self._history[: len(self._history) - 1200]
        self.update()

    def set_playhead(self, seconds: float, duration: Optional[float] = None) -> None:
        self._playhead = max(0.0, float(seconds))
        if duration is not None:
            self._timeline_duration = max(1.0, float(duration))
        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor(Color.BG_INPUT))
        painter.setPen(
            QPen(
                QColor(Color.ACCENT_PRIMARY if self._selected else Color.BORDER_SUBTLE),
                2 if self._selected else 1,
            )
        )
        painter.drawRoundedRect(rect, 6, 6)

        # These elapsed-time divisions deliberately mirror the shared ruler.
        for seconds in _timeline_tick_positions(self._timeline_duration, rect.width())[
            1:-1
        ]:
            x = rect.left() + rect.width() * seconds / self._timeline_duration
            painter.setPen(QPen(QColor(Color.BG_CARD), 1))
            painter.drawLine(int(x), rect.top() + 1, int(x), rect.bottom() - 1)

        mid = rect.center().y()
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawLine(rect.left() + 4, mid, rect.right() - 4, mid)

        if self._live:
            values = self._history
            if values:
                width = max(1.0, rect.width() - 12.0)
                step = width / max(1, len(values) - 1)
                path = QPainterPath()
                path.moveTo(rect.left() + 6, mid)
                for index, value in enumerate(values):
                    x = rect.left() + 6 + index * step
                    y = mid - value * max(4, rect.height() * 0.38)
                    path.lineTo(x, y)
                painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 2))
                painter.drawPath(path)
            if self._recording:
                painter.setPen(QPen(QColor(Color.TEXT_PRIMARY), 1))
                painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 5, 5)
            painter.end()
            return

        start = max(0.0, self._offset)
        visible_duration = max(
            0.0,
            self._clip_duration + min(0.0, self._offset),
        )
        clip_x = rect.left() + rect.width() * start / self._timeline_duration
        clip_width = rect.width() * visible_duration / self._timeline_duration
        clip_width = max(3.0, min(float(rect.right()) - clip_x, clip_width))
        clip = rect.adjusted(0, 7, 0, -7)
        clip.setLeft(int(clip_x))
        clip.setWidth(int(clip_width))
        synchronized_source = _is_synchronized_source(self._source)
        fill = QColor(
            Color.BG_CARD_HOVER if synchronized_source else Color.BG_PANEL
        )
        fill.setAlpha(215 if synchronized_source else 235)
        painter.fillRect(clip, fill)
        # Redraw the shared seconds grid above the clip fill.  The same grid
        # underneath is useful for live lanes, but would otherwise disappear
        # under an opaque recorded clip.
        grid_color = QColor(Color.TEXT_MUTED)
        grid_color.setAlpha(72)
        painter.setPen(QPen(grid_color, 1))
        for seconds in _timeline_tick_positions(self._timeline_duration, rect.width())[
            1:-1
        ]:
            x = rect.left() + rect.width() * seconds / self._timeline_duration
            painter.drawLine(int(x), rect.top() + 1, int(x), rect.bottom() - 1)
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawLine(rect.left() + 4, mid, rect.right() - 4, mid)
        painter.setPen(
            QPen(
                QColor(
                    Color.ACCENT_PRIMARY
                    if self._selected
                    else (
                        Color.TEXT_SECONDARY
                        if synchronized_source
                        else Color.TEXT_MUTED
                    )
                ),
                2 if self._selected else 1,
            )
        )
        painter.drawRoundedRect(clip, 4, 4)

        for gap_start, gap_duration, _reason in self._gaps:
            gap_left = max(
                clip.left(),
                int(rect.left() + rect.width() * gap_start / self._timeline_duration),
            )
            gap_right = min(
                clip.right(),
                int(
                    rect.left()
                    + rect.width()
                    * (gap_start + gap_duration)
                    / self._timeline_duration
                ),
            )
            if gap_right <= gap_left:
                continue
            gap = clip.adjusted(0, 0, 0, 0)
            gap.setLeft(gap_left)
            gap.setRight(gap_right)
            warning = QColor(Color.ACCENT_PRIMARY)
            warning.setAlpha(92)
            painter.fillRect(gap, warning)
            painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 1))
            for x in range(gap.left() - gap.height(), gap.right() + 1, 6):
                painter.drawLine(x, gap.bottom(), x + gap.height(), gap.top())

        if self._peaks and clip.width() > 2:
            center = clip.center().y()
            amplitude = max(2.0, clip.height() * 0.43)
            step = clip.width() / max(1, len(self._peaks) - 1)
            top_path = QPainterPath()
            bottom_path = QPainterPath()
            top_path.moveTo(clip.left(), center)
            bottom_path.moveTo(clip.left(), center)
            for index, peak in enumerate(self._peaks):
                x = clip.left() + index * step
                top_path.lineTo(x, center - peak * amplitude)
                bottom_path.lineTo(x, center + peak * amplitude)
            painter.setPen(QPen(QColor(Color.TEXT_PRIMARY), 1))
            painter.drawPath(top_path)
            painter.drawPath(bottom_path)

        if self._timeline_duration > 0:
            x = rect.left() + rect.width() * min(
                1.0, self._playhead / self._timeline_duration
            )
            painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 2))
            painter.drawLine(int(x), rect.top() + 1, int(x), rect.bottom() - 1)
        painter.end()


class TrackLane(QFrame):
    gain_changed = Signal(int, int)
    trim_changed = Signal(int, int)
    mute_changed = Signal(int, bool)
    solo_changed = Signal(int, bool)
    pan_changed = Signal(int, int)
    export_included_changed = Signal(str, bool)
    track_selected = Signal(int)
    mix_gesture_started = Signal(int, str)
    mix_gesture_finished = Signal(int, str)

    def __init__(
        self,
        channel_id: int,
        name: str,
        detail: str,
        *,
        export_track_id: str = "",
        track_number: int = 0,
        source: str = "jamulus_server",
        source_badge_label: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.channel_id = int(channel_id)
        self._export_track_id = str(export_track_id or "").strip()
        self._selected = False
        self._live = False
        self._trim_available = True
        self._take_editing_enabled = True
        self._track_export_enabled = True
        self.setObjectName("StudioTrackLane")
        self.setFixedHeight(100)
        self.setAccessibleName(f"{name} track")
        self.setAccessibleDescription(detail.replace("·", ","))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        row.setSpacing(Space.SM)

        header = QFrame()
        header.setObjectName("StudioTrackHeader")
        header.setFixedWidth(TRACK_LANE_HEADER_WIDTH)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        header_layout.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(Space.XS)
        self._track_number = QLabel(
            f"{max(1, int(track_number or channel_id + 1)):02d}"
        )
        self._track_number.setObjectName("StudioTrackNumber")
        self._track_number.setFixedWidth(20)
        self._name = QLabel(name)
        self._name.setObjectName("StudioTrackName")
        self._name.setMinimumWidth(0)
        resolved_source_label = (
            str(source_badge_label or "").strip() or _safe_source_label(source)
        )
        self._source_badge = QLabel(resolved_source_label)
        self._source_badge.setObjectName("StudioTrackSource")
        self._source_badge.setAccessibleName(f"{resolved_source_label} source")

        # This is deliberately an ephemeral export choice. It changes neither
        # the recording nor its durable project manifest; it simply narrows the
        # next track export to tracks the musician has reviewed and wants.
        self._track_export_include = QCheckBox("Export")
        self._track_export_include.setObjectName("StudioTrackExportInclude")
        self._track_export_include.setChecked(True)
        self._track_export_include.setVisible(bool(self._export_track_id))
        self._track_export_include.setAccessibleName(f"Include {name} in track export")
        self._track_export_include.setAccessibleDescription(
            "Uncheck to leave this track out of the next track export. "
            "This does not change the recorded take."
        )
        self._track_export_include.setToolTip(
            "Uncheck to leave this track out of the next track export. "
            "The recorded take is unchanged."
        )
        name_row.addWidget(self._track_number)
        name_row.addWidget(self._name, 1)
        name_row.addWidget(self._track_export_include)
        self._detail = QLabel(detail)
        self._detail.setObjectName("StudioTrackDetail")
        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(Space.XS)
        detail_row.addWidget(self._source_badge)
        detail_row.addWidget(self._detail, 1)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(Space.XS)
        self._mute = QPushButton("M")
        self._mute.setObjectName("StudioMuteButton")
        self._mute.setCheckable(True)
        self._mute.setAccessibleName(f"Mute {name} track")
        self._solo = QPushButton("S")
        self._solo.setObjectName("StudioSoloButton")
        self._solo.setCheckable(True)
        self._solo.setAccessibleName(f"Solo {name} track")
        self._meter = TrackLevelMeter()
        self._trim = QSlider(Qt.Orientation.Horizontal)
        self._trim.setRange(0, 400)
        self._trim.setValue(100)
        self._trim.setMaximumWidth(30)
        self._trim.setAccessibleName(f"{name} input trim")
        self._trim.setToolTip("Non-destructive input trim")
        self._trim_value = QLabel(_fmt_db(1.0))
        self._trim_value.setObjectName("StudioTrimValue")
        self._trim_value.setFixedWidth(42)
        self._gain = QSlider(Qt.Orientation.Horizontal)
        self._gain.setRange(0, 400)
        self._gain.setValue(100)
        self._gain.setMaximumWidth(36)
        self._gain.setAccessibleName(f"{name} track volume")
        self._gain_value = QLabel(_fmt_db(1.0))
        self._gain_value.setObjectName("StudioGainValue")
        self._gain_value.setFixedWidth(42)
        self._pan = QSlider(Qt.Orientation.Horizontal)
        self._pan.setRange(-100, 100)
        self._pan.setValue(0)
        self._pan.setMaximumWidth(28)
        self._pan.setAccessibleName(f"{name} track pan")
        self._pan.setToolTip("Pan left or right")
        self._gain.setToolTip("Track volume")
        self._pan_value = QLabel("C")
        self._pan_value.setObjectName("StudioPanValue")
        self._pan_value.setFixedWidth(20)
        controls.addWidget(self._mute)
        controls.addWidget(self._solo)
        controls.addWidget(self._meter)
        controls.addWidget(self._trim)
        controls.addWidget(self._trim_value)
        controls.addWidget(self._gain)
        controls.addWidget(self._gain_value)
        controls.addWidget(self._pan)
        controls.addWidget(self._pan_value)
        header_layout.addLayout(name_row)
        header_layout.addLayout(detail_row)
        header_layout.addLayout(controls)
        row.addWidget(header)

        self.waveform = WaveformCanvas()
        row.addWidget(self.waveform, 1)

        self._trim.valueChanged.connect(self._on_trim_changed)
        self._gain.valueChanged.connect(self._on_gain_changed)
        self._mute.toggled.connect(
            lambda checked: self.mute_changed.emit(self.channel_id, checked)
        )
        self._solo.toggled.connect(
            lambda checked: self.solo_changed.emit(self.channel_id, checked)
        )
        self._pan.valueChanged.connect(self._on_pan_changed)
        self._track_export_include.toggled.connect(self._on_export_included_changed)
        for control in (self._mute, self._solo, self._track_export_include):
            control.clicked.connect(self._select)
        self._gain.sliderPressed.connect(self._select)
        self._trim.sliderPressed.connect(self._select)
        self._pan.sliderPressed.connect(self._select)
        for control, field_name in (
            (self._gain, "gain"),
            (self._trim, "trim_gain"),
            (self._pan, "pan"),
        ):
            control.sliderPressed.connect(
                lambda field=field_name: self.mix_gesture_started.emit(
                    self.channel_id,
                    field,
                )
            )
            control.sliderReleased.connect(
                lambda field=field_name: self.mix_gesture_finished.emit(
                    self.channel_id,
                    field,
                )
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._select()
        super().mousePressEvent(event)

    def _select(self, _checked: bool = False) -> None:
        self.track_selected.emit(self.channel_id)

    def _on_gain_changed(self, value: int) -> None:
        self._gain_value.setText(_fmt_db(value / 100.0))
        self.gain_changed.emit(self.channel_id, value)

    def _on_trim_changed(self, value: int) -> None:
        self._trim_value.setText(_fmt_db(value / 100.0))
        self.trim_changed.emit(self.channel_id, value)

    def _on_pan_changed(self, value: int) -> None:
        self._pan_value.setText(
            "C" if value == 0 else f"{'L' if value < 0 else 'R'}{abs(value)}"
        )
        self.pan_changed.emit(self.channel_id, value)

    def set_live_mode(self, live: bool) -> None:
        """Hide playback-only controls that cannot affect the live Jamulus mix."""
        self._live = bool(live)
        recorded_mix_visible = not self._live and self._take_editing_enabled
        live_or_editable = self._live or self._take_editing_enabled
        self._mute.setVisible(live_or_editable)
        self._solo.setVisible(live_or_editable)
        self._gain.setVisible(live_or_editable)
        self._pan.setVisible(recorded_mix_visible)
        self._pan_value.setVisible(recorded_mix_visible)
        self._trim.setVisible(recorded_mix_visible and self._trim_available)
        self._trim_value.setVisible(recorded_mix_visible and self._trim_available)
        self._gain_value.setVisible(recorded_mix_visible)
        self._track_export_include.setVisible(
            not self._live
            and self._take_editing_enabled
            and self._track_export_enabled
            and bool(self._export_track_id)
        )

    def set_take_review_capabilities(
        self,
        *,
        editing_enabled: bool,
        track_export_enabled: bool,
    ) -> None:
        """Apply a take's read-only/edit/export product boundary to this lane."""

        self._take_editing_enabled = bool(editing_enabled)
        self._track_export_enabled = bool(track_export_enabled)
        self.set_live_mode(self._live)
        if (
            not self._take_editing_enabled
            and "Playback and source review only"
            not in self.accessibleDescription()
        ):
            self.setAccessibleDescription(
                f"{self.accessibleDescription()} Playback and source review only; "
                "mix editing and track export are unavailable."
            )

    def set_trim_available(self, available: bool) -> None:
        """Show trim only when the active project can persist and render it."""

        self._trim_available = bool(available)
        visible = (
            not self._live
            and self._take_editing_enabled
            and self._trim_available
        )
        self._trim.setVisible(visible)
        self._trim_value.setVisible(visible)

    def set_track_export_included(self, included: bool) -> None:
        """Reflect Studio's transient per-take export selection without a signal."""
        self._track_export_include.blockSignals(True)
        self._track_export_include.setChecked(bool(included))
        self._track_export_include.blockSignals(False)

    def set_track_export_enabled(self, enabled: bool) -> None:
        self._track_export_include.setEnabled(
            bool(enabled) and self._track_export_enabled
        )

    def set_mix_state(
        self,
        *,
        gain: float,
        trim_gain: float = 1.0,
        pan: float,
        muted: bool,
        solo: bool,
    ) -> None:
        """Apply saved review-mix choices without treating loading as an edit."""
        values = (
            (self._trim, max(0, min(400, round(float(trim_gain) * 100)))),
            (self._gain, max(0, min(400, round(float(gain) * 100)))),
            (self._pan, max(-100, min(100, round(float(pan) * 100)))),
            (self._mute, bool(muted)),
            (self._solo, bool(solo)),
        )
        for control, value in values:
            control.blockSignals(True)
            control.setValue(value) if isinstance(
                control, QSlider
            ) else control.setChecked(value)
            control.blockSignals(False)
        self._gain_value.setText(_fmt_db(self._gain.value() / 100.0))
        self._trim_value.setText(_fmt_db(self._trim.value() / 100.0))
        pan_value = self._pan.value()
        self._pan_value.setText(
            "C"
            if pan_value == 0
            else f"{'L' if pan_value < 0 else 'R'}{abs(pan_value)}"
        )

    def _on_export_included_changed(self, included: bool) -> None:
        if self._export_track_id:
            self.export_included_changed.emit(self._export_track_id, bool(included))

    def set_level(self, value: float) -> None:
        self._meter.set_level(value)
        self.waveform.push_level(value)

    def set_stereo_levels(
        self,
        left: float,
        right: float,
        *,
        clipped: bool,
    ) -> None:
        self._meter.set_stereo_levels(left, right, clipped=clipped)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setProperty("selected", self._selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.waveform.set_selected(self._selected)


__all__ = [
    "TRACK_LANE_HEADER_WIDTH",
    "StudioTimelineRuler",
    "TrackLane",
    "TrackLevelMeter",
    "WaveformCanvas",
    "_CompactComboBox",
    "_CompositeWaveformSpec",
    "_WaveformBuildCancelled",
    "_WaveformPeakCache",
    "_WaveformSegmentSpec",
    "_WaveformSourceKey",
    "_composite_waveform_key",
    "_composite_waveform_peaks",
    "_fmt_db",
    "_fmt_time",
    "_timeline_gaps_for_track",
    "_timeline_tick_positions",
    "_waveform_peaks",
    "_waveform_source_key",
    "_waveform_spec_for_track",
]

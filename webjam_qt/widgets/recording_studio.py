"""Integrated multitrack recording and playback workspace.

The server recorder already captures one synchronized file per musician.  This
widget makes that capability feel like part of WebJam instead of an external
server feature: live armed lanes become recorded waveform lanes, and the same
screen provides transport plus per-track gain/mute/solo controls.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
import logging
import queue
import threading
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_output_devices
from core.take_export import (
    LogicExportResult,
    TrackMixSettings,
    export_logic_package,
)
from core.take_library import TakeInfo, TakeValidationResult, discover_takes
from core.take_player import PlaybackError, SoundDeviceSink, TakePlayer
from webjam_qt.theme.tokens import Color, Space

LOGGER = logging.getLogger("webjam.qt.recording_studio")

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
                            for gap_start, gap_count, gap_channels, _reason in segment.gaps:
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

    def set_recorded_clip(
        self,
        *,
        peaks: tuple[float, ...],
        offset: float,
        duration: float,
        timeline_duration: float,
        source: str,
    ) -> None:
        self._peaks = peaks
        self._history = []
        self._offset = float(offset)
        self._clip_duration = max(0.0, float(duration))
        self._timeline_duration = max(0.001, float(timeline_duration))
        self._source = str(source)
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

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor(Color.BG_INPUT))
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawRoundedRect(rect, 6, 6)

        # Beat/time grid: intentionally neutral because tempo is not inferred.
        for index in range(1, 8):
            x = rect.left() + rect.width() * index / 8
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
        fill = QColor(
            Color.ACCENT_PRIMARY
            if self._source == "jamulus_server"
            else Color.TEXT_MUTED
        )
        fill.setAlpha(72 if self._source == "jamulus_server" else 42)
        painter.fillRect(clip, fill)
        painter.setPen(
            QPen(
                QColor(
                    Color.ACCENT_PRIMARY
                    if self._source == "jamulus_server"
                    else Color.TEXT_MUTED
                ),
                1,
            )
        )
        painter.drawRoundedRect(clip, 4, 4)

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
    mute_changed = Signal(int, bool)
    solo_changed = Signal(int, bool)
    pan_changed = Signal(int, int)

    def __init__(
        self,
        channel_id: int,
        name: str,
        detail: str,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.channel_id = int(channel_id)
        self.setObjectName("StudioTrackLane")
        self.setFixedHeight(92)
        self.setAccessibleName(f"{name} track")
        self.setAccessibleDescription(detail.replace("·", ","))

        row = QHBoxLayout(self)
        row.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        row.setSpacing(Space.SM)

        header = QFrame()
        header.setObjectName("StudioTrackHeader")
        header.setFixedWidth(210)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        header_layout.setSpacing(2)
        self._name = QLabel(name)
        self._name.setObjectName("StudioTrackName")
        self._detail = QLabel(detail)
        self._detail.setObjectName("StudioTrackDetail")
        controls = QHBoxLayout()
        controls.setSpacing(Space.XS)
        self._mute = QPushButton("M")
        self._mute.setObjectName("StudioMuteButton")
        self._mute.setCheckable(True)
        self._mute.setAccessibleName(f"Mute {name} track")
        self._solo = QPushButton("S")
        self._solo.setObjectName("StudioSoloButton")
        self._solo.setCheckable(True)
        self._solo.setAccessibleName(f"Solo {name} track")
        self._gain = QSlider(Qt.Orientation.Horizontal)
        self._gain.setRange(0, 127)
        self._gain.setValue(100)
        self._gain.setAccessibleName(f"{name} track volume")
        self._pan = QSlider(Qt.Orientation.Horizontal)
        self._pan.setRange(-100, 100)
        self._pan.setValue(0)
        self._pan.setMaximumWidth(50)
        self._pan.setAccessibleName(f"{name} track pan")
        self._pan.setToolTip("Pan left or right")
        self._gain.setToolTip("Track volume")
        self._pan_value = QLabel("C")
        self._pan_value.setObjectName("StudioPanValue")
        self._pan_value.setFixedWidth(24)
        controls.addWidget(self._mute)
        controls.addWidget(self._solo)
        controls.addWidget(self._gain, 1)
        controls.addWidget(self._pan)
        controls.addWidget(self._pan_value)
        header_layout.addWidget(self._name)
        header_layout.addWidget(self._detail)
        header_layout.addLayout(controls)
        row.addWidget(header)

        self.waveform = WaveformCanvas()
        row.addWidget(self.waveform, 1)

        self._gain.valueChanged.connect(
            lambda value: self.gain_changed.emit(self.channel_id, value)
        )
        self._mute.toggled.connect(
            lambda checked: self.mute_changed.emit(self.channel_id, checked)
        )
        self._solo.toggled.connect(
            lambda checked: self.solo_changed.emit(self.channel_id, checked)
        )
        self._pan.valueChanged.connect(self._on_pan_changed)

    def _on_pan_changed(self, value: int) -> None:
        self._pan_value.setText(
            "C" if value == 0 else f"{'L' if value < 0 else 'R'}{abs(value)}"
        )
        self.pan_changed.emit(self.channel_id, value)

    def set_live_mode(self, live: bool) -> None:
        """Hide playback-only controls that cannot affect the live Jamulus mix."""
        self._pan.setVisible(not live)
        self._pan_value.setVisible(not live)

    def set_level(self, value: float) -> None:
        self.waveform.push_level(value)


class RecordingStudio(QWidget):
    """A single in-app home for recording, takes, waveforms, and rough mixes."""

    record_requested = Signal()
    return_live_requested = Signal()
    live_fader_changed = Signal(int, int)
    live_mute_toggled = Signal(int, bool)
    live_solo_toggled = Signal(int, bool)
    output_device_changed = Signal(str)
    recording_setup_requested = Signal()

    def __init__(
        self,
        takes_dir: str = "",
        *,
        player: Optional[TakePlayer] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RecordingStudio")
        self._takes_dir = str(takes_dir or "")
        self._takes: list[TakeInfo] = []
        self._current: Optional[TakeInfo] = None
        self._live_participants: list = []
        self._live_signature: tuple = ()
        self._lanes: dict[int, TrackLane] = {}
        self._pending_levels: dict[int, float] = {}
        self._finished_flag = False
        self._export_outcome: tuple[Optional[LogicExportResult], str] | None = None
        self._exporting = False
        self._reveal_path: Optional[Path] = None
        self._local_originals_path: Optional[Path] = None
        self._recording_elapsed = 0.0
        self._recording = False
        self._can_record = True
        self._phase_name = "idle"
        self._viewing_live = True
        self._waveform_cache = _WaveformPeakCache()
        self._waveform_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="webjam-waveform",
        )
        self._waveform_generation = 0
        self._waveform_cancel = threading.Event()
        self._waveform_futures: set[Future] = set()
        self._waveform_futures_lock = threading.Lock()
        self._waveform_results: queue.SimpleQueue[
            tuple[
                int,
                int,
                Path,
                _WaveformSourceKey,
                tuple[float, ...],
            ]
        ] = queue.SimpleQueue()
        self._waveform_shutdown = False
        self._player = player or TakePlayer(sink=SoundDeviceSink())
        self._player._on_levels = self._on_levels_bg
        self._player._on_finished = self._on_finished_bg

        self._build_ui()
        self.reload()
        self._show_live_session()

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.LG)
        root.setSpacing(Space.MD)

        top = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        eyebrow = QLabel("MULTITRACK STUDIO")
        eyebrow.setObjectName("StudioEyebrow")
        self._title = QLabel("Record the whole band")
        self._title.setObjectName("StudioTitle")
        self._subtitle = QLabel(
            "Every connected musician lands on a separate synchronized track."
        )
        self._subtitle.setObjectName("StudioSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setMinimumWidth(0)
        title_block.addWidget(eyebrow)
        title_block.addWidget(self._title)
        title_block.addWidget(self._subtitle)
        top.addLayout(title_block, 1)
        self._live_btn = QPushButton("Live")
        self._live_btn.setObjectName("GhostButton")
        self._live_btn.setAccessibleName("Return to live room")
        self._live_btn.clicked.connect(self.return_live_requested.emit)
        top.addWidget(self._live_btn)
        self._setup_btn = QPushButton("Setup")
        self._setup_btn.setObjectName("GhostButton")
        self._setup_btn.setAccessibleName("Open recording setup")
        self._setup_btn.clicked.connect(self.recording_setup_requested.emit)
        top.addWidget(self._setup_btn)
        self._record_btn = QPushButton("● Record")
        self._record_btn.setObjectName("StudioRecordButton")
        self._record_btn.setAccessibleName("Record take")
        self._record_btn.clicked.connect(self.record_requested.emit)
        top.addWidget(self._record_btn)
        root.addLayout(top)

        self._phase = QLabel("READY")
        self._phase.setObjectName("StudioPhase")
        self._phase.setAccessibleName("Multitrack recorder status")
        root.addWidget(self._phase)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        library = QFrame()
        self._library = library
        library.setObjectName("StudioLibrary")
        library.setMinimumWidth(160)
        library.setMaximumWidth(240)
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        library_layout.setSpacing(Space.SM)
        library_title = QLabel("Takes")
        library_title.setObjectName("StudioSectionTitle")
        library_layout.addWidget(library_title)
        self._take_list = QListWidget()
        self._take_list.setObjectName("StudioTakeList")
        self._take_list.currentRowChanged.connect(self._on_take_selected)
        library_layout.addWidget(self._take_list, 1)
        self._new_take_btn = QPushButton("＋ New live take")
        self._new_take_btn.setObjectName("GhostButton")
        self._new_take_btn.clicked.connect(self._show_live_session)
        library_layout.addWidget(self._new_take_btn)
        splitter.addWidget(library)

        editor = QFrame()
        editor.setObjectName("StudioEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        editor_layout.setSpacing(Space.SM)

        transport = QHBoxLayout()
        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setObjectName("AudioButton")
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setObjectName("GhostButton")
        self._stop_btn.clicked.connect(self._stop_playback)
        self._position = QLabel("0:00 / 0:00")
        self._position.setObjectName("StudioPosition")
        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, 1000)
        self._scrub.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        self._scrub.sliderReleased.connect(self._seek_from_scrub)
        self._scrubbing = False
        transport.addWidget(self._play_btn)
        transport.addWidget(self._stop_btn)
        transport.addWidget(self._position)
        transport.addWidget(self._scrub, 1)
        editor_layout.addLayout(transport)

        actions = QHBoxLayout()
        self._output_label = QLabel("Playback output")
        actions.addWidget(self._output_label)
        self._output_picker = _CompactComboBox()
        self._output_picker.setObjectName("StudioOutputPicker")
        self._output_picker.setAccessibleName("Studio playback output")
        self._output_picker.setMaximumWidth(220)
        self._populate_output_devices()
        self._output_picker.currentIndexChanged.connect(self._on_output_changed)
        actions.addWidget(self._output_picker, 1)
        actions.addStretch(1)
        self._export_btn = QPushButton("Export for Logic")
        self._export_btn.setObjectName("PrimaryButton")
        self._export_btn.setAccessibleName("Export aligned stems for Logic Pro")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_for_logic)
        actions.addWidget(self._export_btn)
        self._reveal_btn = QPushButton("Show Take")
        self._reveal_btn.setObjectName("GhostButton")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_current)
        actions.addWidget(self._reveal_btn)
        self._originals_btn = QPushButton("Show My Originals")
        self._originals_btn.setObjectName("GhostButton")
        self._originals_btn.setAccessibleName(
            "Show preserved Local Originals folder"
        )
        self._originals_btn.setToolTip(
            "Open the folder containing this Mac's preserved, unchanged recordings."
        )
        self._originals_btn.setVisible(False)
        self._originals_btn.clicked.connect(self._reveal_local_originals)
        actions.addWidget(self._originals_btn)
        editor_layout.addLayout(actions)

        self._playback_controls = (
            self._play_btn,
            self._stop_btn,
            self._position,
            self._scrub,
            self._output_label,
            self._output_picker,
            self._export_btn,
            self._reveal_btn,
        )

        self._track_scroll = QScrollArea()
        self._track_scroll.setObjectName("StudioTrackScroll")
        self._track_scroll.setWidgetResizable(True)
        self._track_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._track_container = QWidget()
        self._track_layout = QVBoxLayout(self._track_container)
        self._track_layout.setContentsMargins(0, 0, 0, 0)
        self._track_layout.setSpacing(Space.XS)
        self._track_layout.addStretch(1)
        self._track_scroll.setWidget(self._track_container)
        editor_layout.addWidget(self._track_scroll, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("StudioHint")
        self._hint.setWordWrap(True)
        editor_layout.addWidget(self._hint)
        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 900])
        root.addWidget(splitter, 1)

    def set_takes_directory(self, path: str) -> None:
        normalized = str(path or "")
        if normalized == self._takes_dir:
            return
        self._takes_dir = normalized
        self.reload()

    def set_local_originals_directory(self, path: str | Path | None) -> None:
        """Expose preserved guest media without importing or modifying it."""

        self._local_originals_path = (
            Path(path).expanduser().resolve() if path else None
        )
        available = bool(
            self._local_originals_path is not None
            and self._local_originals_path.is_dir()
        )
        self._originals_btn.setVisible(available)
        self._originals_btn.setEnabled(available)

    def refresh_take(self, path: str | Path) -> None:
        """Reload manifest truth while preserving the user's Studio context."""

        target = Path(path).resolve()
        selected = self._current.path if self._current is not None else None
        viewing_live = self._viewing_live
        if not viewing_live and selected is None:
            selected = target
        self.reload(select_path=selected)
        if viewing_live:
            self._show_live_session()

    def set_output_device(self, name: str) -> None:
        """Apply the saved Studio playback output without starting audio."""
        self._stop_playback()
        value = str(name or "")
        index = self._output_picker.findData(value)
        if index < 0 and value:
            self._output_picker.addItem(f"{value} (unavailable)", value)
            index = self._output_picker.count() - 1
        self._output_picker.blockSignals(True)
        self._output_picker.setCurrentIndex(max(0, index))
        self._output_picker.blockSignals(False)
        self._player.set_output_device(value)

    def _populate_output_devices(self) -> None:
        self._output_picker.addItem("System Default", "")
        for device in list_output_devices():
            name = str(device.get("name") or "").strip()
            if name and self._output_picker.findData(name) < 0:
                self._output_picker.addItem(name, name)

    def _on_output_changed(self, _index: int) -> None:
        self._stop_playback()
        name = str(self._output_picker.currentData() or "")
        self._player.set_output_device(name)
        self.output_device_changed.emit(name)

    def set_can_record(self, enabled: bool, reason: str = "") -> None:
        self._can_record = bool(enabled)
        if not self._recording:
            self._record_btn.setEnabled(
                self._can_record and bool(self._live_participants)
            )
        if reason and not enabled:
            self._hint.setText(reason)

    def set_live_participants(self, participants: Iterable) -> None:
        incoming = list(participants)
        signature = tuple(
            (
                int(getattr(item, "channel_id", -1)),
                str(getattr(item, "name", "")),
                bool(getattr(item, "is_local", False)),
            )
            for item in incoming
        )
        changed = signature != self._live_signature
        self._live_participants = incoming
        self._live_signature = signature
        if self._phase_name not in {
            "preflight", "starting", "stopping", "validating"
        }:
            self._record_btn.setEnabled(
                self._recording
                or (self._can_record and bool(self._live_participants))
            )
        if self._viewing_live and changed:
            self._populate_live_lanes()

    def set_live_levels(self, levels: dict[int, float]) -> None:
        if not self._viewing_live:
            return
        for channel_id, value in levels.items():
            lane = self._lanes.get(int(channel_id))
            if lane is not None:
                lane.set_level(value)

    def set_recording_phase(self, phase: str, detail: str = "") -> None:
        phase = str(phase or "idle")
        self._phase_name = phase
        labels = {
            "idle": "READY · start audio, then record a take",
            "preflight": "CHECKING THE BAND…",
            "starting": "ARMING TRACKS…",
            "recording": "● RECORDING · one track per musician",
            "stopping": "SAVING TRACKS…",
            "validating": detail or "VERIFYING THE TAKE…",
            "complete": "TAKE SAVED · ready to play",
            "needs_attention": "TAKE SAVED · review recommended",
            "stop_failed": "● STILL RECORDING · stop was not confirmed",
            "error": "RECORDING NEEDS ATTENTION",
        }
        self._phase.setText(labels.get(phase, phase.upper()))
        self._recording = phase in {"recording", "stop_failed"}
        if self._recording:
            if phase == "recording":
                self._recording_elapsed = 0.0
            self._show_live_session()
            self._record_btn.setText(
                "■ Try Stop" if phase == "stop_failed" else "■ Stop"
            )
            self._record_btn.setEnabled(True)
        elif phase in {"preflight", "starting", "stopping", "validating"}:
            self._record_btn.setText("Working…")
            self._record_btn.setEnabled(False)
        else:
            self._record_btn.setText("● Record")
            self._record_btn.setEnabled(
                self._can_record and bool(self._live_participants)
            )
        for lane in self._lanes.values():
            lane.waveform.set_recording(self._recording)

    def reload(self, select_path: Optional[Path] = None) -> None:
        self._takes = discover_takes(self._takes_dir) if self._takes_dir else []
        self._library.setVisible(bool(self._takes))
        self._take_list.blockSignals(True)
        self._take_list.clear()
        for take in self._takes:
            status = "✓" if take.validation_status == "complete" else "•"
            label = (
                f"{status} {take.display_name}\n"
                f"   {take.track_count} tracks · {_fmt_time(take.duration_s)}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(take.path))
            self._take_list.addItem(item)
        self._take_list.blockSignals(False)
        if select_path is not None:
            wanted = str(Path(select_path))
            for row in range(self._take_list.count()):
                if self._take_list.item(row).data(Qt.ItemDataRole.UserRole) == wanted:
                    self._take_list.setCurrentRow(row)
                    self._on_take_selected(row)
                    return
        if not self._takes and not self._live_participants:
            self._hint.setText(
                "Start Session to bring musicians into the studio. Press Record and "
                "WebJam will create one synchronized track for everyone automatically."
            )
        if self._current is None:
            self._export_btn.setEnabled(False)
            self._reveal_btn.setEnabled(False)

    def on_take_completed(
        self,
        path: Optional[Path],
        validation: Optional[TakeValidationResult] = None,
    ) -> None:
        self.reload(select_path=path)
        if validation is not None:
            messages = list(validation.errors)
            messages.extend(f"Warning: {item}" for item in validation.warnings)
            self._hint.setText("\n".join(messages))

    def _cancel_waveform_jobs(self) -> None:
        """Cancel current work and discard results for lanes being replaced."""
        self._waveform_cancel.set()
        with self._waveform_futures_lock:
            futures = tuple(self._waveform_futures)
            self._waveform_futures.clear()
        for future in futures:
            future.cancel()
        while True:
            try:
                self._waveform_results.get_nowait()
            except queue.Empty:
                break

    def _begin_waveform_batch(self) -> tuple[int, threading.Event]:
        self._waveform_generation += 1
        self._waveform_cancel = threading.Event()
        return self._waveform_generation, self._waveform_cancel

    def _schedule_waveform(
        self,
        *,
        generation: int,
        cancel_event: threading.Event,
        channel_id: int,
        path: Path,
    ) -> None:
        """Apply a cached envelope or build one away from the Qt thread."""
        if self._waveform_shutdown or cancel_event.is_set():
            return
        source = Path(path)
        try:
            key = _waveform_source_key(source)
        except OSError as exc:
            LOGGER.debug("Could not identify waveform source %s: %s", source, exc)
            return

        cached = self._waveform_cache.get(key)
        if cached is not None:
            if generation == self._waveform_generation and not self._viewing_live:
                lane = self._lanes.get(int(channel_id))
                if lane is not None:
                    lane.waveform.set_peaks(cached)
            return

        def build() -> tuple[float, ...]:
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            existing = self._waveform_cache.get(key)
            if existing is not None:
                return existing
            peaks = _waveform_peaks(source, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            self._waveform_cache.put(key, peaks)
            return peaks

        try:
            future = self._waveform_executor.submit(build)
        except RuntimeError:
            # Executor shutdown raced an application/window close.
            return
        with self._waveform_futures_lock:
            self._waveform_futures.add(future)

        def completed(done: Future) -> None:
            with self._waveform_futures_lock:
                self._waveform_futures.discard(done)
            try:
                peaks = done.result()
            except (CancelledError, _WaveformBuildCancelled):
                return
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Waveform worker failed for %s: %s", source, exc)
                return
            if self._waveform_shutdown or cancel_event.is_set():
                return
            self._waveform_results.put(
                (generation, int(channel_id), source, key, tuple(peaks))
            )

        future.add_done_callback(completed)

    def _schedule_composite_waveform(
        self,
        *,
        generation: int,
        cancel_event: threading.Event,
        channel_id: int,
        spec: _CompositeWaveformSpec,
    ) -> None:
        if self._waveform_shutdown or cancel_event.is_set():
            return
        try:
            key = _composite_waveform_key(spec)
        except OSError as exc:
            LOGGER.debug("Could not identify composite waveform: %s", exc)
            return
        cached = self._waveform_cache.get(key)
        if cached is not None:
            if generation == self._waveform_generation and not self._viewing_live:
                lane = self._lanes.get(int(channel_id))
                if lane is not None:
                    lane.waveform.set_peaks(cached)
            return

        def build() -> tuple[float, ...]:
            existing = self._waveform_cache.get(key)
            if existing is not None:
                return existing
            peaks = _composite_waveform_peaks(spec, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            self._waveform_cache.put(key, peaks)
            return peaks

        try:
            future = self._waveform_executor.submit(build)
        except RuntimeError:
            return
        with self._waveform_futures_lock:
            self._waveform_futures.add(future)

        def completed(done: Future) -> None:
            with self._waveform_futures_lock:
                self._waveform_futures.discard(done)
            try:
                peaks = done.result()
            except (CancelledError, _WaveformBuildCancelled):
                return
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Composite waveform worker failed: %s", exc)
                return
            if self._waveform_shutdown or cancel_event.is_set():
                return
            self._waveform_results.put(
                (generation, int(channel_id), spec, key, tuple(peaks))
            )

        future.add_done_callback(completed)

    def _drain_waveform_results(self) -> None:
        """Apply current worker results; stale take/source results are ignored."""
        while True:
            try:
                generation, channel_id, source, key, peaks = (
                    self._waveform_results.get_nowait()
                )
            except queue.Empty:
                return
            if generation != self._waveform_generation or self._viewing_live:
                continue
            lane = self._lanes.get(channel_id)
            if lane is None:
                continue
            try:
                current_key = (
                    _composite_waveform_key(source)
                    if isinstance(source, _CompositeWaveformSpec)
                    else _waveform_source_key(source)
                )
            except OSError:
                continue
            if current_key != key:
                # The file changed while it was being scanned.  Never paint
                # stale peaks; queue one build for the new source identity.
                if isinstance(source, _CompositeWaveformSpec):
                    self._schedule_composite_waveform(
                        generation=generation,
                        cancel_event=self._waveform_cancel,
                        channel_id=channel_id,
                        spec=source,
                    )
                else:
                    self._schedule_waveform(
                        generation=generation,
                        cancel_event=self._waveform_cancel,
                        channel_id=channel_id,
                        path=source,
                    )
                continue
            lane.waveform.set_peaks(peaks)

    def _clear_lanes(self) -> None:
        self._cancel_waveform_jobs()
        self._lanes.clear()
        while self._track_layout.count() > 1:
            item = self._track_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_lane(self, lane: TrackLane, *, live: bool) -> None:
        self._lanes[lane.channel_id] = lane
        lane.set_live_mode(live)
        if live:
            lane.gain_changed.connect(self.live_fader_changed.emit)
            lane.mute_changed.connect(self.live_mute_toggled.emit)
            lane.solo_changed.connect(self.live_solo_toggled.emit)
        else:
            lane.gain_changed.connect(
                lambda cid, value: self._player.set_gain(cid, value / 100.0)
            )
            lane.mute_changed.connect(self._player.set_muted)
            lane.solo_changed.connect(self._player.set_solo)
            lane.pan_changed.connect(
                lambda cid, value: self._player.set_pan(cid, value / 100.0)
            )
        self._track_layout.insertWidget(self._track_layout.count() - 1, lane)

    def _set_playback_controls_visible(self, visible: bool) -> None:
        for widget in self._playback_controls:
            widget.setVisible(visible)

    def _populate_live_lanes(self) -> None:
        self._clear_lanes()
        self._set_playback_controls_visible(False)
        self._live_btn.setVisible(False)
        self._new_take_btn.setVisible(False)
        self._title.setText("Live multitrack session")
        self._subtitle.setText(
            "Each connected musician is armed automatically—no track setup required."
        )
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._scrub.setEnabled(False)
        self._position.setText(
            f"REC {_fmt_time(self._recording_elapsed)}" if self._recording else "0:00"
        )
        for participant in self._live_participants:
            channel_id = int(getattr(participant, "channel_id", -1))
            if channel_id < 0:
                continue
            detail = "ARMED · live musician"
            if getattr(participant, "is_local", False):
                detail = "ARMED · you"
            lane = TrackLane(channel_id, getattr(participant, "name", "Musician"), detail)
            lane.waveform.set_live(self._recording)
            self._add_lane(lane, live=True)
        if self._live_participants:
            self._hint.setText(
                f"{len(self._live_participants)} track"
                f"{'s' if len(self._live_participants) != 1 else ''} armed. "
                "Press Record when the band is ready."
            )
        else:
            self._hint.setText("Start Session and your musicians will appear here as tracks.")

    def _show_live_session(self) -> None:
        self._player.stop()
        self._current = None
        self._reveal_path = None
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.setText("Show Take")
        self._export_btn.setEnabled(False)
        self._viewing_live = True
        self._take_list.clearSelection()
        self._populate_live_lanes()

    def _on_take_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._takes):
            return
        self._viewing_live = False
        take = self._takes[row]
        self._current = take
        self._player.load(take)
        self._clear_lanes()
        waveform_generation, waveform_cancel = self._begin_waveform_batch()
        self._set_playback_controls_visible(True)
        self._live_btn.setVisible(True)
        self._new_take_btn.setVisible(True)
        self._title.setText(take.display_name)
        blocked_statuses = {"missing", "damaged", "transfer_failed", "transferring"}
        missing_count = sum(
            getattr(track, "media_status", "available") in blocked_statuses
            for track in take.tracks
        )
        if missing_count:
            self._subtitle.setText(
                f"{take.track_count} tracks · {missing_count} missing · "
                f"{_fmt_time(take.duration_s)}"
            )
        else:
            self._subtitle.setText(
                f"{take.track_count} synchronized tracks · {_fmt_time(take.duration_s)}"
            )
        playable = any(
            getattr(track, "media_status", "available") not in blocked_statuses
            and float(getattr(track, "duration_s", 0.0) or 0.0) > 0.0
            for track in take.tracks
        )
        self._play_btn.setEnabled(playable)
        self._stop_btn.setEnabled(playable)
        self._scrub.setEnabled(playable)
        verified = take.validation_status == "complete" and not take.manifest_errors
        self._export_btn.setEnabled(
            bool(self._player.tracks) and verified and not self._exporting
        )
        self._reveal_path = take.path
        self._reveal_btn.setText("Show Take")
        self._reveal_btn.setEnabled(True)
        self._scrub.setValue(0)
        self._position.setText(f"0:00 / {_fmt_time(self._player.duration_s)}")
        info_by_path = {Path(track.path): track for track in take.tracks}
        for track in self._player.tracks:
            source_info = info_by_path.get(Path(track.path))
            duration = float(getattr(source_info, "duration_s", 0.0) or 0.0)
            media_status = str(
                getattr(source_info, "media_status", "available") or "available"
            )
            if media_status in blocked_statuses:
                label = {
                    "missing": "MISSING MEDIA",
                    "damaged": "DAMAGED MEDIA",
                    "transfer_failed": "TRANSFER FAILED",
                    "transferring": "TRANSFER IN PROGRESS",
                }.get(media_status, "MEDIA NEEDS ATTENTION")
                detail = f"{label} · restore or finish this track to continue"
            elif media_status == "partial":
                detail = "PARTIAL TRACK · listen and review before export"
            elif media_status == "recovered":
                detail = "RECOVERED TRACK · listen and review before export"
            else:
                detail = (
                    "BAND TRACK" if track.source == "jamulus_server" else "LOCAL TRACK"
                )
            lane = TrackLane(track.channel_id, track.name, detail)
            composite_spec = (
                _waveform_spec_for_track(source_info, take)
                if source_info is not None
                else None
            )
            lane.waveform.set_recorded_clip(
                peaks=(),
                offset=0.0 if composite_spec is not None else track.offset_s,
                duration=(
                    max(0.001, float(take.duration_s))
                    if composite_spec is not None
                    else duration
                ),
                timeline_duration=max(1.0, take.duration_s),
                source=track.source,
            )
            self._add_lane(lane, live=False)
            if media_status not in blocked_statuses:
                if composite_spec is not None:
                    self._schedule_composite_waveform(
                        generation=waveform_generation,
                        cancel_event=waveform_cancel,
                        channel_id=track.channel_id,
                        spec=composite_spec,
                    )
                else:
                    self._schedule_waveform(
                        generation=waveform_generation,
                        cancel_event=waveform_cancel,
                        channel_id=track.channel_id,
                        path=track.path,
                    )
        messages = list(take.manifest_errors)
        messages.extend(f"Warning: {item}" for item in take.manifest_warnings)
        if messages:
            self._hint.setText("\n".join(messages))
        elif verified:
            self._hint.setText("Take verified and ready to mix or export.")
        else:
            self._hint.setText(
                "Unverified take. Playback is available, but Logic export stays "
                "locked until WebJam verifies the recording."
            )

    def _export_for_logic(self) -> None:
        take = self._current
        if (
            take is None
            or self._exporting
            or take.validation_status != "complete"
            or bool(take.manifest_errors)
        ):
            return
        self._stop_playback()
        states = {
            track.channel_id: TrackMixSettings(
                gain=track.gain,
                pan=track.pan,
                muted=track.muted,
                solo=track.solo,
            )
            for track in self._player.tracks
        }
        self._exporting = True
        self._export_outcome = None
        self._take_list.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting…")
        self._hint.setText(
            "Preparing aligned 24-bit stems and a stereo rough mix. "
            "The original take will not be changed."
        )

        def worker() -> None:
            try:
                result = export_logic_package(take, mix_settings=states)
                self._export_outcome = (result, "")
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Logic export failed for %s", take.path)
                self._export_outcome = (None, str(exc))

        threading.Thread(
            target=worker,
            daemon=True,
            name="logic-export",
        ).start()

    def _finish_export(self, result: Optional[LogicExportResult], error: str) -> None:
        self._exporting = False
        self._take_list.setEnabled(True)
        self._export_btn.setText("Export for Logic")
        self._export_btn.setEnabled(
            self._current is not None
            and self._current.validation_status == "complete"
            and not self._current.manifest_errors
        )
        if result is None:
            LOGGER.error("Logic export did not complete: %s", error or "unknown error")
            self._hint.setText(
                "Logic export couldn't be completed. The original take is safe. "
                "Check available disk space and folder access, then try again."
            )
            return
        self._reveal_path = result.folder
        self._reveal_btn.setText("Show Logic Export")
        self._reveal_btn.setEnabled(True)
        self._hint.setText(
            f"Logic export ready · {len(result.stems)} aligned 24-bit stems · "
            f"{result.samplerate / 1000:g} kHz. Drag the numbered WAVs into "
            "Logic together at 0:00."
        )

    def _reveal_current(self) -> None:
        if self._reveal_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._reveal_path)))

    def _reveal_local_originals(self) -> None:
        path = self._local_originals_path
        if path is not None and path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _toggle_play(self) -> None:
        if self._current is None:
            return
        if self._player.is_playing:
            self._player.pause()
            self._play_btn.setText("▶ Play")
            return
        try:
            self._player.play()
            self._play_btn.setText("⏸ Pause")
        except PlaybackError as exc:
            self._hint.setText(str(exc))

    def _stop_playback(self) -> None:
        self._player.stop()
        self._play_btn.setText("▶ Play")
        self._scrub.setValue(0)

    def _seek_from_scrub(self) -> None:
        self._scrubbing = False
        if self._player.duration_s > 0:
            self._player.seek(self._scrub.value() / 1000.0 * self._player.duration_s)

    def _on_levels_bg(self, levels: dict[int, float]) -> None:
        self._pending_levels = dict(levels)

    def _on_finished_bg(self) -> None:
        self._finished_flag = True

    def _tick(self) -> None:
        if self._recording:
            self._recording_elapsed += self._timer.interval() / 1000.0
            self._position.setText(f"REC {_fmt_time(self._recording_elapsed)}")
            for lane in self._lanes.values():
                lane.waveform.set_playhead(
                    self._recording_elapsed, max(30.0, self._recording_elapsed)
                )
        elif not self._viewing_live:
            pos = self._player.position_s
            duration = self._player.duration_s
            if not self._scrubbing and duration > 0:
                self._scrub.setValue(int(pos / duration * 1000))
            self._position.setText(f"{_fmt_time(pos)} / {_fmt_time(duration)}")
            for lane in self._lanes.values():
                lane.waveform.set_playhead(pos, duration)
        pending, self._pending_levels = self._pending_levels, {}
        for channel_id, level in pending.items():
            lane = self._lanes.get(int(channel_id))
            if lane is not None:
                lane.set_level(level)
        self._drain_waveform_results()
        if self._finished_flag:
            self._finished_flag = False
            self._player.stop()
            self._play_btn.setText("▶ Play")
            self._scrub.setValue(0)
        if self._export_outcome is not None:
            outcome, self._export_outcome = self._export_outcome, None
            self._finish_export(*outcome)

    def shutdown(self) -> None:
        if self._waveform_shutdown:
            return
        self._waveform_shutdown = True
        self._cancel_waveform_jobs()
        self._waveform_executor.shutdown(wait=False, cancel_futures=True)
        self._timer.stop()
        self._player.stop()

    def hideEvent(self, event) -> None:  # noqa: N802
        """Release playback when the integrated Studio workspace is left.

        Studio lives in a stacked workspace rather than a separate closeable
        window.  A stack switch emits a hide event, so this is the lifecycle
        boundary that must stop the output stream and close source readers.
        """
        self._stop_playback()
        super().hideEvent(event)

    @property
    def export_in_progress(self) -> bool:
        return self._exporting

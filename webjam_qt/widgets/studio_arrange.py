"""Frame-domain Arrange surface for immutable Studio documents.

The widget deliberately owns no persistence, audio engine, or edit history.  It
renders one :class:`~core.studio_project.StudioDocument` and translates mouse
and keyboard input into semantic Qt signals.  A controller applies those
requests to the immutable document and supplies the resulting snapshot back
through :meth:`StudioArrange.set_document`.
"""

from __future__ import annotations

import bisect
import math
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QHBoxLayout, QSizePolicy, QWidget

from core.studio_project import (
    MarkerKind,
    SnapMode,
    StudioDocument,
    StudioMarker,
    StudioRegion,
    crossfade_gains,
    fade_gain,
)
from core.studio_waveform import WaveformTile, WaveformTileKey
from webjam_qt.theme.tokens import Color


TRACK_HEADER_WIDTH = 168
RULER_HEIGHT = 42
TRACK_HEIGHT = 72
TAKE_LANE_HEIGHT = 30
MIN_PIXELS_PER_SECOND = 8.0
MAX_PIXELS_PER_SECOND = 1_600.0
DEFAULT_PIXELS_PER_SECOND = 72.0
MAX_ARRANGE_WAVEFORM_BINDINGS = 4_096
MAX_ARRANGE_WAVEFORM_BYTES = 32 * 1024 * 1024

_EDGE_HIT_PIXELS = 7.0
_MARKER_SNAP_PIXELS = 12.0
_MAX_SCROLL_UNITS = 1_000_000_000
_MAX_TRACK_NAME_CHARACTERS = 160


@dataclass(frozen=True)
class _LaneLayout:
    lane_id: str
    top: int
    name: str


@dataclass(frozen=True)
class _TrackLayout:
    track_id: str
    top: int
    height: int
    lanes: tuple[_LaneLayout, ...]


@dataclass
class _Gesture:
    kind: str
    region_id: str
    lane_id: str
    press_frame: int
    original_start: int
    original_end: int


@dataclass(frozen=True)
class _PaintStats:
    tracks: int = 0
    lanes: int = 0
    regions: int = 0
    markers: int = 0
    comp_ranges: int = 0
    crossfades: int = 0


def _require_frame(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer frame.")
    return value


def _format_frame_time(frame: int, sample_rate: int) -> str:
    seconds = abs(frame) / max(1, sample_rate)
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    sign = "−" if frame < 0 else ""
    if seconds < 10:
        return f"{sign}{minutes}:{remainder:04.1f}"
    return f"{sign}{minutes}:{int(remainder):02d}"


def _grid_step_frames(sample_rate: int, pixels_per_second: float) -> int:
    """Choose a 1/2/5 elapsed-time grid with roughly 72 px spacing."""

    target_seconds = 72.0 / max(MIN_PIXELS_PER_SECOND, pixels_per_second)
    power = 10.0 ** math.floor(math.log10(max(0.001, target_seconds)))
    normalized = target_seconds / power
    multiplier = 1.0 if normalized <= 1.0 else 2.0 if normalized <= 2.0 else 5.0
    return max(1, round(multiplier * power * sample_rate))


def _nearest_step(frame: int, step: int) -> int:
    """Round a signed frame to the nearest grid point, with stable half ties."""

    if frame >= 0:
        return ((frame + step // 2) // step) * step
    return -(((-frame + step // 2) // step) * step)


class _TrackHeaders(QWidget):
    """Fixed-width names aligned with the canvas' vertically scrolled rows."""

    def __init__(self, arrange: StudioArrange) -> None:
        super().__init__(arrange)
        self._arrange = arrange
        self.setObjectName("StudioArrangeTrackHeaders")
        self.setFixedWidth(TRACK_HEADER_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Arrange track headers")
        self.setAccessibleDescription(
            "Fixed track and take-lane names. Use the arrow keys or pointer to "
            "select a track."
        )
        self.setToolTip("Track headers remain fixed while the timeline scrolls.")

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Color.BG_PANEL))
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        painter.fillRect(
            QRectF(0, 0, self.width(), RULER_HEIGHT), QColor(Color.BG_INPUT)
        )
        painter.setPen(QColor(Color.TEXT_MUTED))
        painter.drawText(
            QRectF(12, 0, self.width() - 24, RULER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "TRACKS",
        )

        canvas = self._arrange._canvas
        vertical_offset = canvas.verticalScrollBar().value()
        painter.save()
        painter.setClipRect(QRectF(0, RULER_HEIGHT, self.width(), self.height()))
        for layout in canvas._track_layouts:
            top = RULER_HEIGHT + layout.top - vertical_offset
            bottom = top + layout.height
            if bottom <= RULER_HEIGHT or top >= self.height():
                continue
            selected = layout.track_id == self._arrange._selected_track_id
            main_rect = QRectF(0, top, self.width(), TRACK_HEIGHT)
            painter.fillRect(
                main_rect,
                QColor(Color.BG_CARD_HOVER if selected else Color.BG_CARD),
            )
            if selected:
                painter.fillRect(
                    QRectF(0, top, 3, TRACK_HEIGHT), QColor(Color.ACCENT_PRIMARY)
                )
            painter.setPen(QColor(Color.TEXT_PRIMARY))
            name = self._arrange._track_name(layout.track_id)
            painter.drawText(
                main_rect.adjusted(12, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                name,
            )
            painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
            painter.drawLine(
                0, int(top + TRACK_HEIGHT), self.width(), int(top + TRACK_HEIGHT)
            )

            for lane in layout.lanes:
                lane_top = RULER_HEIGHT + lane.top - vertical_offset
                lane_rect = QRectF(0, lane_top, self.width(), TAKE_LANE_HEIGHT)
                if (
                    lane_rect.bottom() <= RULER_HEIGHT
                    or lane_rect.top() >= self.height()
                ):
                    continue
                selected_lane = lane.lane_id == self._arrange._selected_lane_id
                auditioning = lane.lane_id == self._arrange._audition_lane_id
                painter.fillRect(
                    lane_rect,
                    QColor(Color.BG_CARD_HOVER if selected_lane else Color.BG_INPUT),
                )
                if selected_lane or auditioning:
                    painter.fillRect(
                        QRectF(0, lane_top, 3, TAKE_LANE_HEIGHT),
                        QColor(Color.ACCENT_PRIMARY),
                    )
                painter.setPen(
                    QColor(
                        Color.ACCENT_PRIMARY
                        if auditioning
                        else Color.TEXT_SECONDARY
                        if selected_lane
                        else Color.TEXT_MUTED
                    )
                )
                painter.drawText(
                    lane_rect.adjusted(20, 0, -6, 0),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    (
                        f"▶ {lane.name or 'Alternate take'}"
                        if auditioning
                        else lane.name or "Alternate take"
                    ),
                )
                painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
                painter.drawLine(
                    12,
                    int(lane_rect.bottom()),
                    self.width(),
                    int(lane_rect.bottom()),
                )
        painter.restore()
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            layout = self._arrange._canvas._track_at_y(event.position().y())
            if layout is not None:
                row_id = self._arrange._canvas._row_at_y(layout, event.position().y())
                if row_id in self._arrange._canvas._lane_layouts:
                    self._arrange._user_select_lane(layout.track_id, row_id)
                else:
                    self._arrange._user_select_track(layout.track_id)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            layout = self._arrange._canvas._track_at_y(event.position().y())
            if layout is not None:
                row_id = self._arrange._canvas._row_at_y(layout, event.position().y())
                if row_id in self._arrange._canvas._lane_layouts:
                    self._arrange._user_select_lane(layout.track_id, row_id)
                    self._arrange.lane_audition_requested.emit(row_id)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)


class _TimelineViewport(QWidget):
    """Paint and input target owned by :class:`_ArrangeScrollArea`."""

    def __init__(self, canvas: _ArrangeScrollArea) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self.setObjectName("StudioArrangeCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Arrange timeline canvas")
        self.setAccessibleDescription(
            "Frame-accurate regions, markers, take lanes, fades, and playhead. "
            "Drag clips to move them, drag an edge to trim, or Option-drag an "
            "alternate lane to select it into the comp."
        )
        self.setToolTip(
            "Drag a clip to move · drag an edge to trim · Ctrl+E split · "
            "Ctrl+D duplicate · Delete remove · Shift+Delete enable or disable · "
            "Option/Alt-drag a take lane to comp · double-click its name to audition · "
            "Alt+1/2/3 snap off/time/markers"
        )


class _ArrangeScrollArea(QAbstractScrollArea):
    """A bounded scroll model whose paint coordinates remain frame based."""

    def __init__(self, arrange: StudioArrange) -> None:
        super().__init__(arrange)
        self._arrange = arrange
        self._document: StudioDocument | None = None
        self._pixels_per_second = DEFAULT_PIXELS_PER_SECOND
        self._minimum_frame = 0
        self._maximum_frame = 48_000
        self._frames_per_scroll_unit = 1.0
        self._scrollable_frames = 0.0
        self._track_layouts: tuple[_TrackLayout, ...] = ()
        self._layout_by_track: dict[str, _TrackLayout] = {}
        self._lane_by_region: dict[str, str] = {}
        self._lane_layouts: dict[str, _LaneLayout] = {}
        self._regions_by_row: dict[str, tuple[StudioRegion, ...]] = {}
        self._region_starts_by_row: dict[str, tuple[int, ...]] = {}
        self._gesture: _Gesture | None = None
        self._preview_range: tuple[str, int, int] | None = None
        self._comp_preview: tuple[str, int, int] | None = None
        self._section_preview: tuple[str, int, int] | None = None
        self._last_paint_stats = _PaintStats()

        self.setObjectName("StudioArrangeScrollArea")
        self.setFrameStyle(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setViewport(_TimelineViewport(self))
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self.horizontalScrollBar().valueChanged.connect(self._horizontal_scroll_changed)
        self.verticalScrollBar().valueChanged.connect(self._scroll_changed)

    @property
    def pixels_per_second(self) -> float:
        return self._pixels_per_second

    @property
    def pixels_per_frame(self) -> float:
        document = self._document
        rate = document.project_sample_rate if document is not None else 48_000
        return self._pixels_per_second / rate

    def set_document(self, document: StudioDocument | None) -> None:
        previous = self._document
        previous_start = self._visible_start_frame()
        same_timeline = (
            previous is not None
            and document is not None
            and (
                previous.session_id,
                previous.take_id,
                previous.project_sample_rate,
            )
            == (
                document.session_id,
                document.take_id,
                document.project_sample_rate,
            )
        )
        self._document = document
        self._gesture = None
        self._preview_range = None
        self._comp_preview = None
        self._section_preview = None
        self._rebuild_layouts()
        self._rebuild_timeline_bounds()
        self._update_scrollbars(
            scroll_start=(
                previous_start if same_timeline else float(self._minimum_frame)
            )
        )
        self.viewport().update()

    def set_zoom(
        self, pixels_per_second: float, *, anchor_x: float | None = None
    ) -> None:
        if isinstance(pixels_per_second, bool) or not isinstance(
            pixels_per_second, (int, float)
        ):
            raise TypeError("pixels_per_second must be a finite number.")
        zoom = float(pixels_per_second)
        if not math.isfinite(zoom):
            raise ValueError("pixels_per_second must be a finite number.")
        zoom = max(MIN_PIXELS_PER_SECOND, min(MAX_PIXELS_PER_SECOND, zoom))
        if math.isclose(zoom, self._pixels_per_second):
            return
        anchor = self.viewport().width() / 2.0 if anchor_x is None else float(anchor_x)
        anchor = max(0.0, min(float(self.viewport().width()), anchor))
        anchor_frame = self.frame_at_x(anchor)
        self._pixels_per_second = zoom
        self._update_scrollbars()
        desired_start = anchor_frame - anchor / self.pixels_per_frame
        self._set_scroll_start(desired_start)
        self.viewport().update()
        self._arrange.viewport_changed.emit()

    def scroll_to_frame(self, frame: int, *, center: bool) -> None:
        value = _require_frame(frame, "frame")
        if center:
            value -= round(self.viewport().width() / (2.0 * self.pixels_per_frame))
        self._set_scroll_start(float(value))

    def visible_frame_range(self) -> tuple[int, int]:
        start = self._visible_start_frame()
        end = start + self.viewport().width() / self.pixels_per_frame
        return math.floor(start), math.ceil(end)

    def frame_at_x(self, x: float) -> int:
        frame = self._visible_start_frame() + float(x) / self.pixels_per_frame
        return round(frame)

    def x_for_frame(self, frame: int) -> float:
        return (frame - self._visible_start_frame()) * self.pixels_per_frame

    def _visible_start_frame(self) -> float:
        return self._minimum_frame + (
            self.horizontalScrollBar().value() * self._frames_per_scroll_unit
        )

    def _set_scroll_start(self, frame: float) -> None:
        offset = max(
            0.0,
            min(self._scrollable_frames, float(frame) - self._minimum_frame),
        )
        value = round(offset / self._frames_per_scroll_unit)
        self.horizontalScrollBar().setValue(value)
        self.viewport().update()

    def _scroll_changed(self, _value: int) -> None:
        self.viewport().update()
        self._arrange._headers.update()
        self._arrange.viewport_changed.emit()

    def _horizontal_scroll_changed(self, _value: int) -> None:
        self._arrange.viewport_changed.emit()

    def _rebuild_layouts(self) -> None:
        document = self._document
        if document is None:
            self._track_layouts = ()
            self._layout_by_track = {}
            self._lane_by_region = {}
            self._lane_layouts = {}
            self._regions_by_row = {}
            self._region_starts_by_row = {}
            return

        lanes_by_track: dict[str, list[object]] = {}
        lane_by_region: dict[str, str] = {}
        for lane in document.take_lanes:
            if lane.deleted:
                continue
            lanes_by_track.setdefault(lane.track_id, []).append(lane)
            for region_id in lane.region_ids:
                lane_by_region[region_id] = lane.lane_id

        layouts: list[_TrackLayout] = []
        lane_layouts: dict[str, _LaneLayout] = {}
        top = 0
        for track in sorted(
            document.tracks, key=lambda item: (item.order, item.track_id)
        ):
            track_lanes = sorted(
                lanes_by_track.get(track.track_id, ()),
                key=lambda item: (item.order, item.lane_id),
            )
            lane_values: list[_LaneLayout] = []
            for index, lane in enumerate(track_lanes):
                lane_layout = _LaneLayout(
                    lane_id=lane.lane_id,
                    top=top + TRACK_HEIGHT + index * TAKE_LANE_HEIGHT,
                    name=lane.name,
                )
                lane_values.append(lane_layout)
                lane_layouts[lane.lane_id] = lane_layout
            height = TRACK_HEIGHT + len(lane_values) * TAKE_LANE_HEIGHT
            layouts.append(
                _TrackLayout(
                    track_id=track.track_id,
                    top=top,
                    height=height,
                    lanes=tuple(lane_values),
                )
            )
            top += height

        regions_by_row: dict[str, list[StudioRegion]] = {}
        for region in document.regions:
            if region.deleted:
                continue
            row_id = lane_by_region.get(region.region_id, region.track_id)
            regions_by_row.setdefault(row_id, []).append(region)
        ordered_regions: dict[str, tuple[StudioRegion, ...]] = {}
        region_starts: dict[str, tuple[int, ...]] = {}
        for row_id, regions in regions_by_row.items():
            ordered = tuple(
                sorted(
                    regions,
                    key=lambda item: (
                        item.timeline_start_frame,
                        item.timeline_end_frame,
                        item.region_id,
                    ),
                )
            )
            ordered_regions[row_id] = ordered
            region_starts[row_id] = tuple(item.timeline_start_frame for item in ordered)

        self._track_layouts = tuple(layouts)
        self._layout_by_track = {item.track_id: item for item in layouts}
        self._lane_by_region = lane_by_region
        self._lane_layouts = lane_layouts
        self._regions_by_row = ordered_regions
        self._region_starts_by_row = region_starts

    def _rebuild_timeline_bounds(self) -> None:
        document = self._document
        if document is None:
            self._minimum_frame = 0
            self._maximum_frame = 48_000
            return
        rate = document.project_sample_rate
        starts = [0]
        ends = [rate * 5]
        for region in document.regions:
            if not region.deleted:
                starts.append(region.timeline_start_frame)
                ends.append(region.timeline_end_frame)
        for marker in document.markers:
            if marker.deleted:
                continue
            starts.append(marker.start_frame)
            ends.append(
                marker.end_frame if marker.end_frame is not None else marker.start_frame
            )
        for comp_range in document.comp_ranges:
            if not comp_range.deleted:
                starts.append(comp_range.timeline_start_frame)
                ends.append(comp_range.timeline_end_frame)
        for crossfade in document.crossfades:
            if not crossfade.deleted:
                starts.append(crossfade.start_frame)
                ends.append(crossfade.end_frame)
        if document.cycle_range is not None:
            starts.append(document.cycle_range.start_frame)
            ends.append(document.cycle_range.end_frame)
        starts.append(self._arrange._playhead_frame)
        ends.append(self._arrange._playhead_frame)
        padding = max(1, rate // 2)
        minimum = min(starts)
        maximum = max(ends)
        self._minimum_frame = minimum - padding if minimum < 0 else 0
        self._maximum_frame = max(self._minimum_frame + rate, maximum + padding)

    def _update_scrollbars(self, *, scroll_start: float | None = None) -> None:
        previous_start = (
            self._visible_start_frame() if scroll_start is None else scroll_start
        )
        ppf = self.pixels_per_frame
        visible_frames = max(1.0, self.viewport().width() / ppf)
        total_frames = max(1.0, self._maximum_frame - self._minimum_frame)
        scrollable = max(0.0, total_frames - visible_frames)
        unit = max(1.0 / ppf, scrollable / _MAX_SCROLL_UNITS)
        maximum = min(_MAX_SCROLL_UNITS, math.ceil(scrollable / unit))
        self._frames_per_scroll_unit = unit
        self._scrollable_frames = scrollable
        horizontal = self.horizontalScrollBar()
        horizontal.setRange(0, maximum)
        horizontal.setPageStep(max(1, min(maximum + 1, round(visible_frames / unit))))
        horizontal.setSingleStep(max(1, round(24.0 / (ppf * unit))))

        track_height = (
            self._track_layouts[-1].top + self._track_layouts[-1].height
            if self._track_layouts
            else 0
        )
        visible_track_height = max(0, self.viewport().height() - RULER_HEIGHT)
        vertical = self.verticalScrollBar()
        vertical.setRange(0, max(0, track_height - visible_track_height))
        vertical.setPageStep(max(1, visible_track_height))
        vertical.setSingleStep(TAKE_LANE_HEIGHT)
        self._set_scroll_start(previous_start)
        self._arrange._headers.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        start = self._visible_start_frame()
        super().resizeEvent(event)
        self._update_scrollbars(scroll_start=start)
        self._arrange.viewport_changed.emit()

    # QAbstractScrollArea remaps viewport events onto these handlers.  Keeping
    # the handlers here (and painting explicitly onto ``viewport()``) also
    # makes synthetic QTest input behave exactly like native mouse input.
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        self._paint_viewport(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._mouse_release(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self._wheel(event)

    def _track_at_y(self, y: float) -> _TrackLayout | None:
        content_y = float(y) - RULER_HEIGHT + self.verticalScrollBar().value()
        if content_y < 0:
            return None
        for layout in self._track_layouts:
            if layout.top <= content_y < layout.top + layout.height:
                return layout
        return None

    def _row_at_y(self, layout: _TrackLayout, y: float) -> str:
        content_y = float(y) - RULER_HEIGHT + self.verticalScrollBar().value()
        if content_y < layout.top + TRACK_HEIGHT:
            return layout.track_id
        for lane in layout.lanes:
            if lane.top <= content_y < lane.top + TAKE_LANE_HEIGHT:
                return lane.lane_id
        return layout.track_id

    def _row_rect(self, row_id: str) -> QRectF | None:
        vertical_offset = self.verticalScrollBar().value()
        layout = self._layout_by_track.get(row_id)
        if layout is not None:
            return QRectF(
                0,
                RULER_HEIGHT + layout.top - vertical_offset,
                self.viewport().width(),
                TRACK_HEIGHT,
            )
        lane = self._lane_layouts.get(row_id)
        if lane is None:
            return None
        return QRectF(
            0,
            RULER_HEIGHT + lane.top - vertical_offset,
            self.viewport().width(),
            TAKE_LANE_HEIGHT,
        )

    def _visible_regions_for_row(
        self, row_id: str, start_frame: int, end_frame: int
    ) -> tuple[StudioRegion, ...]:
        values = self._regions_by_row.get(row_id, ())
        starts = self._region_starts_by_row.get(row_id, ())
        stop = bisect.bisect_right(starts, end_frame)
        return tuple(
            region
            for region in values[:stop]
            if region.timeline_end_frame >= start_frame
        )

    def _region_at(self, x: float, y: float) -> StudioRegion | None:
        document = self._document
        layout = self._track_at_y(y)
        if document is None or layout is None:
            return None
        row_id = self._row_at_y(layout, y)
        frame = self.frame_at_x(x)
        values = self._regions_by_row.get(row_id, ())
        for region in reversed(values):
            if region.timeline_start_frame <= frame <= region.timeline_end_frame:
                return region
        return None

    def _section_at(self, x: float, y: float) -> StudioMarker | None:
        """Return the topmost section block under the ruler's arranger bar."""

        document = self._document
        if document is None or not 17.0 <= float(y) <= 27.0:
            return None
        frame = self.frame_at_x(x)
        sections = tuple(
            marker
            for marker in document.markers
            if not marker.deleted
            and marker.kind is MarkerKind.SECTION
            and marker.end_frame is not None
            and marker.start_frame <= frame < marker.end_frame
        )
        return sections[-1] if sections else None

    def _snap_frame(self, frame: int) -> int:
        document = self._document
        if document is None or document.snap_mode is SnapMode.OFF:
            return frame
        if document.snap_mode is SnapMode.TIME:
            step = max(1, document.project_sample_rate // 10)
            return _nearest_step(frame, step)

        candidates: list[int] = []
        for marker in document.markers:
            if marker.deleted:
                continue
            candidates.append(marker.start_frame)
            if marker.end_frame is not None:
                candidates.append(marker.end_frame)
        if not candidates:
            return frame
        nearest = min(candidates, key=lambda item: (abs(item - frame), item))
        tolerance = max(1, round(_MARKER_SNAP_PIXELS / self.pixels_per_frame))
        return nearest if abs(nearest - frame) <= tolerance else frame

    def _paint_viewport(self, _event: QPaintEvent) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.viewport().rect(), QColor(Color.BG_INPUT))
        document = self._document
        if document is None:
            painter.setPen(QColor(Color.TEXT_MUTED))
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Open a take to arrange it",
            )
            painter.end()
            self._last_paint_stats = _PaintStats()
            return

        visible_start, visible_end = self.visible_frame_range()
        visible_layouts = tuple(
            layout for layout in self._track_layouts if self._layout_visible(layout)
        )
        visible_track_ids = {item.track_id for item in visible_layouts}
        visible_lane_ids = {
            lane.lane_id
            for layout in visible_layouts
            for lane in layout.lanes
            if self._lane_visible(lane)
        }

        self._draw_track_backgrounds(painter, visible_layouts, visible_lane_ids)
        self._draw_time_grid(painter, visible_start, visible_end)
        marker_count = self._draw_sections_and_cycle(
            painter, document, visible_start, visible_end
        )
        region_count = self._draw_regions(
            painter,
            visible_layouts,
            visible_lane_ids,
            visible_start,
            visible_end,
        )
        comp_count = self._draw_comp_ranges(
            painter,
            document,
            visible_track_ids,
            visible_lane_ids,
            visible_start,
            visible_end,
        )
        self._draw_comp_preview(painter)
        crossfade_count = self._draw_crossfades(
            painter,
            document,
            visible_track_ids,
            visible_lane_ids,
            visible_start,
            visible_end,
        )
        marker_count += self._draw_marker_lines(
            painter, document, visible_start, visible_end
        )
        self._draw_ruler(painter, document, visible_start, visible_end)
        self._draw_playhead(painter)
        painter.end()
        self._last_paint_stats = _PaintStats(
            tracks=len(visible_layouts),
            lanes=len(visible_lane_ids),
            regions=region_count,
            markers=marker_count,
            comp_ranges=comp_count,
            crossfades=crossfade_count,
        )

    def _layout_visible(self, layout: _TrackLayout) -> bool:
        top = RULER_HEIGHT + layout.top - self.verticalScrollBar().value()
        return top < self.viewport().height() and top + layout.height > RULER_HEIGHT

    def _lane_visible(self, lane: _LaneLayout) -> bool:
        top = RULER_HEIGHT + lane.top - self.verticalScrollBar().value()
        return top < self.viewport().height() and top + TAKE_LANE_HEIGHT > RULER_HEIGHT

    def _draw_track_backgrounds(
        self,
        painter: QPainter,
        layouts: tuple[_TrackLayout, ...],
        visible_lane_ids: set[str],
    ) -> None:
        for layout in layouts:
            row = self._row_rect(layout.track_id)
            if row is None:
                continue
            painter.fillRect(row, QColor(Color.BG_CARD))
            if layout.track_id == self._arrange._selected_track_id:
                selected = QColor(Color.ACCENT_PRIMARY)
                selected.setAlpha(25)
                painter.fillRect(row, selected)
            painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
            painter.drawLine(
                int(row.left()), int(row.bottom()), int(row.right()), int(row.bottom())
            )
            for lane in layout.lanes:
                if lane.lane_id not in visible_lane_ids:
                    continue
                lane_rect = self._row_rect(lane.lane_id)
                if lane_rect is None:
                    continue
                painter.fillRect(lane_rect, QColor(Color.BG_PANEL))
                painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
                painter.drawLine(
                    int(lane_rect.left()),
                    int(lane_rect.bottom()),
                    int(lane_rect.right()),
                    int(lane_rect.bottom()),
                )

    def _draw_time_grid(
        self, painter: QPainter, visible_start: int, visible_end: int
    ) -> None:
        document = self._document
        assert document is not None
        step = _grid_step_frames(document.project_sample_rate, self._pixels_per_second)
        first = math.floor(visible_start / step) * step
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        frame = first
        maximum_ticks = max(4, self.viewport().width() // 24 + 4)
        for _index in range(maximum_ticks):
            if frame > visible_end:
                break
            x = self.x_for_frame(frame)
            painter.drawLine(int(x), RULER_HEIGHT, int(x), self.viewport().height())
            frame += step

    def _draw_sections_and_cycle(
        self,
        painter: QPainter,
        document: StudioDocument,
        visible_start: int,
        visible_end: int,
    ) -> int:
        count = 0
        for marker in document.markers:
            if (
                marker.deleted
                or marker.kind is not MarkerKind.SECTION
                or marker.end_frame is None
                or marker.end_frame < visible_start
                or marker.start_frame > visible_end
            ):
                continue
            left = max(0.0, self.x_for_frame(marker.start_frame))
            right = min(
                float(self.viewport().width()), self.x_for_frame(marker.end_frame)
            )
            section = QColor(Color.TEXT_SECONDARY)
            section.setAlpha(13)
            painter.fillRect(
                QRectF(
                    left,
                    RULER_HEIGHT,
                    max(0.0, right - left),
                    self.viewport().height() - RULER_HEIGHT,
                ),
                section,
            )
            count += 1

        cycle = document.cycle_range
        if (
            cycle is not None
            and cycle.enabled
            and cycle.end_frame >= visible_start
            and cycle.start_frame <= visible_end
        ):
            left = max(0.0, self.x_for_frame(cycle.start_frame))
            right = min(
                float(self.viewport().width()), self.x_for_frame(cycle.end_frame)
            )
            fill = QColor(Color.ACCENT_PRIMARY)
            fill.setAlpha(15)
            painter.fillRect(
                QRectF(
                    left,
                    RULER_HEIGHT,
                    max(0.0, right - left),
                    self.viewport().height() - RULER_HEIGHT,
                ),
                fill,
            )
        return count

    def _draw_regions(
        self,
        painter: QPainter,
        layouts: tuple[_TrackLayout, ...],
        visible_lane_ids: set[str],
        visible_start: int,
        visible_end: int,
    ) -> int:
        count = 0
        for layout in layouts:
            row_ids = (layout.track_id, *(lane.lane_id for lane in layout.lanes))
            for row_id in row_ids:
                if row_id != layout.track_id and row_id not in visible_lane_ids:
                    continue
                row = self._row_rect(row_id)
                if row is None:
                    continue
                for region in self._visible_regions_for_row(
                    row_id, visible_start, visible_end
                ):
                    self._draw_region(painter, region, row, row_id != layout.track_id)
                    count += 1
        return count

    def _draw_region(
        self,
        painter: QPainter,
        region: StudioRegion,
        row: QRectF,
        in_take_lane: bool,
    ) -> None:
        start = region.timeline_start_frame
        end = region.timeline_end_frame
        if (
            self._preview_range is not None
            and self._preview_range[0] == region.region_id
        ):
            _region_id, start, end = self._preview_range
        left = max(-2.0, self.x_for_frame(start))
        right = min(float(self.viewport().width()) + 2.0, self.x_for_frame(end))
        vertical_margin = 4.0 if in_take_lane else 9.0
        rect = QRectF(
            left,
            row.top() + vertical_margin,
            max(1.0, right - left),
            max(4.0, row.height() - vertical_margin * 2.0),
        )
        fill = QColor(Color.BG_CARD_HOVER if in_take_lane else Color.BG_PANEL)
        fill.setAlpha(150 if not region.enabled else 240)
        painter.fillRect(rect, fill)

        self._draw_region_waveform(
            painter,
            region,
            rect,
            timeline_start=start,
            timeline_end=end,
        )

        selected = region.region_id == self._arrange._selected_region_id
        painter.setPen(
            QPen(
                QColor(Color.ACCENT_PRIMARY if selected else Color.TEXT_MUTED),
                2 if selected else 1,
            )
        )
        painter.drawRoundedRect(rect, 4, 4)
        if not region.enabled:
            painter.setPen(QPen(QColor(Color.TEXT_MUTED), 1))
            step = 8
            for x in range(
                int(rect.left()) - int(rect.height()), int(rect.right()) + 1, step
            ):
                painter.drawLine(
                    x, int(rect.bottom()), x + int(rect.height()), int(rect.top())
                )

        self._draw_region_fade(
            painter,
            region,
            rect,
            start,
            end,
            fade_in=True,
        )
        self._draw_region_fade(
            painter,
            region,
            rect,
            start,
            end,
            fade_in=False,
        )

    def _draw_region_waveform(
        self,
        painter: QPainter,
        region: StudioRegion,
        rect: QRectF,
        *,
        timeline_start: int,
        timeline_end: int,
    ) -> None:
        tiles = self._arrange._waveform_tiles.get(region.region_id, {})
        if not tiles or timeline_end <= timeline_start:
            return
        source_start = region.source_start_frame
        source_end = region.source_end_frame
        source_span = source_end - source_start
        if source_span <= 0:
            return
        center = rect.center().y()
        amplitude = max(1.0, rect.height() * 0.42)
        color = QColor(Color.ACCENT_PRIMARY)
        color.setAlpha(150 if region.enabled else 75)
        painter.save()
        painter.setClipRect(rect.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(color, 1))
        for tile in sorted(
            tiles.values(),
            key=lambda item: (
                item.key.start_frame,
                item.key.frames_per_peak,
            ),
        ):
            tile_start = tile.key.start_frame
            resolution = tile.key.frames_per_peak
            for index in range(tile.peak_count):
                bucket_start = tile_start + index * resolution
                bucket_end = min(
                    tile_start + tile.key.frame_count,
                    bucket_start + resolution,
                )
                if bucket_end <= source_start or bucket_start >= source_end:
                    continue
                source_midpoint = (
                    max(source_start, bucket_start) + min(source_end, bucket_end)
                ) / 2.0
                proportion = (source_midpoint - source_start) / source_span
                frame = timeline_start + proportion * (timeline_end - timeline_start)
                x = self.x_for_frame(round(frame))
                if x < rect.left() - 1 or x > rect.right() + 1:
                    continue
                low = float(tile.minimum[index].min())
                high = float(tile.maximum[index].max())
                low = max(-1.0, min(1.0, low))
                high = max(-1.0, min(1.0, high))
                painter.drawLine(
                    round(x),
                    round(center - high * amplitude),
                    round(x),
                    round(center - low * amplitude),
                )
        painter.restore()

    def _draw_region_fade(
        self,
        painter: QPainter,
        region: StudioRegion,
        rect: QRectF,
        start_frame: int,
        end_frame: int,
        *,
        fade_in: bool,
    ) -> None:
        length = region.fade_in_frames if fade_in else region.fade_out_frames
        if length <= 0:
            return
        fade_start = start_frame if fade_in else end_frame - length
        fade_end = start_frame + length if fade_in else end_frame
        if (
            fade_end < self.visible_frame_range()[0]
            or fade_start > self.visible_frame_range()[1]
        ):
            return
        curve = region.fade_in_curve if fade_in else region.fade_out_curve
        path = QPainterPath()
        points = 12
        for index in range(points + 1):
            position = round(index * length / points)
            frame = fade_start + position
            gain = fade_gain(position, length, curve, fade_in=fade_in)
            x = self.x_for_frame(frame)
            y = rect.bottom() - gain * rect.height()
            if index == 0:
                path.moveTo(QPointF(x, y))
            else:
                path.lineTo(QPointF(x, y))
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor(Color.TEXT_SECONDARY), 1))
        painter.drawPath(path)
        painter.restore()

    def _draw_comp_ranges(
        self,
        painter: QPainter,
        document: StudioDocument,
        visible_track_ids: set[str],
        visible_lane_ids: set[str],
        visible_start: int,
        visible_end: int,
    ) -> int:
        count = 0
        for comp_range in document.comp_ranges:
            if (
                comp_range.deleted
                or not comp_range.enabled
                or comp_range.track_id not in visible_track_ids
                or comp_range.lane_id not in visible_lane_ids
                or comp_range.timeline_end_frame < visible_start
                or comp_range.timeline_start_frame > visible_end
            ):
                continue
            row = self._row_rect(comp_range.lane_id)
            if row is None:
                continue
            left = max(0.0, self.x_for_frame(comp_range.timeline_start_frame))
            right = min(
                float(self.viewport().width()),
                self.x_for_frame(comp_range.timeline_end_frame),
            )
            overlay = QRectF(
                left,
                row.bottom() - 7,
                max(1.0, right - left),
                6,
            )
            fill = QColor(Color.ACCENT_PRIMARY)
            fill.setAlpha(190)
            painter.fillRect(overlay, fill)
            painter.setPen(QPen(QColor(Color.TEXT_PRIMARY), 1))
            if overlay.width() >= 34:
                painter.drawText(
                    overlay.adjusted(3, -12, -2, 0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "COMP",
                )
            count += 1
        return count

    def _draw_comp_preview(self, painter: QPainter) -> None:
        preview = self._comp_preview
        if preview is None:
            return
        lane_id, start, end = preview
        row = self._row_rect(lane_id)
        if row is None:
            return
        left = max(0.0, self.x_for_frame(start))
        right = min(float(self.viewport().width()), self.x_for_frame(end))
        if right <= left:
            return
        overlay = QRectF(left, row.top() + 2, right - left, row.height() - 4)
        fill = QColor(Color.ACCENT_PRIMARY)
        fill.setAlpha(70)
        painter.fillRect(overlay, fill)
        painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 2, Qt.PenStyle.DashLine))
        painter.drawRect(overlay)

    def _draw_crossfades(
        self,
        painter: QPainter,
        document: StudioDocument,
        visible_track_ids: set[str],
        visible_lane_ids: set[str],
        visible_start: int,
        visible_end: int,
    ) -> int:
        regions = {item.region_id: item for item in document.regions}
        count = 0
        for crossfade in document.crossfades:
            if (
                crossfade.deleted
                or crossfade.end_frame < visible_start
                or crossfade.start_frame > visible_end
            ):
                continue
            left_region = regions.get(crossfade.left_region_id)
            right_region = regions.get(crossfade.right_region_id)
            if left_region is None or right_region is None:
                continue
            left_lane = self._lane_by_region.get(left_region.region_id)
            right_lane = self._lane_by_region.get(right_region.region_id)
            row_id = (
                left_lane
                if left_lane and left_lane == right_lane
                else left_region.track_id
            )
            if row_id not in visible_track_ids and row_id not in visible_lane_ids:
                continue
            row = self._row_rect(row_id)
            if row is None:
                continue
            left = max(0.0, self.x_for_frame(crossfade.start_frame))
            right = min(
                float(self.viewport().width()), self.x_for_frame(crossfade.end_frame)
            )
            rect = QRectF(
                left,
                row.top() + (4 if row_id in visible_lane_ids else 9),
                max(1.0, right - left),
                row.height() - (8 if row_id in visible_lane_ids else 18),
            )
            fill = QColor(Color.ACCENT_PRIMARY)
            fill.setAlpha(36)
            painter.fillRect(rect, fill)
            outgoing = QPainterPath()
            incoming = QPainterPath()
            points = 16
            for index in range(points + 1):
                position = round(index * crossfade.frame_count / points)
                out_gain, in_gain = crossfade_gains(
                    position, crossfade.frame_count, crossfade.curve
                )
                x = self.x_for_frame(crossfade.start_frame + position)
                out_y = rect.bottom() - out_gain * rect.height()
                in_y = rect.bottom() - in_gain * rect.height()
                if index == 0:
                    outgoing.moveTo(QPointF(x, out_y))
                    incoming.moveTo(QPointF(x, in_y))
                else:
                    outgoing.lineTo(QPointF(x, out_y))
                    incoming.lineTo(QPointF(x, in_y))
            painter.save()
            painter.setClipRect(rect)
            painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 1))
            painter.drawPath(outgoing)
            painter.drawPath(incoming)
            painter.restore()
            count += 1
        return count

    def _draw_marker_lines(
        self,
        painter: QPainter,
        document: StudioDocument,
        visible_start: int,
        visible_end: int,
    ) -> int:
        count = 0
        for marker in document.markers:
            if (
                marker.deleted
                or marker.start_frame < visible_start
                or marker.start_frame > visible_end
            ):
                continue
            x = self.x_for_frame(marker.start_frame)
            painter.setPen(QPen(QColor(Color.TEXT_SECONDARY), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(x), RULER_HEIGHT, int(x), self.viewport().height())
            if marker.kind is MarkerKind.MARKER:
                count += 1
        return count

    def _draw_ruler(
        self,
        painter: QPainter,
        document: StudioDocument,
        visible_start: int,
        visible_end: int,
    ) -> None:
        ruler = QRectF(0, 0, self.viewport().width(), RULER_HEIGHT)
        painter.fillRect(ruler, QColor(Color.BG_INPUT))
        painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
        painter.drawLine(0, RULER_HEIGHT - 1, self.viewport().width(), RULER_HEIGHT - 1)
        step = _grid_step_frames(document.project_sample_rate, self._pixels_per_second)
        frame = math.floor(visible_start / step) * step
        maximum_ticks = max(4, self.viewport().width() // 24 + 4)
        for _index in range(maximum_ticks):
            if frame > visible_end:
                break
            x = self.x_for_frame(frame)
            painter.setPen(QPen(QColor(Color.BORDER_SUBTLE), 1))
            painter.drawLine(int(x), 24, int(x), RULER_HEIGHT - 1)
            painter.setPen(QColor(Color.TEXT_MUTED))
            painter.drawText(
                QRectF(x + 3, 2, 68, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                _format_frame_time(frame, document.project_sample_rate),
            )
            frame += step

        cycle = document.cycle_range
        if (
            cycle is not None
            and cycle.enabled
            and cycle.end_frame >= visible_start
            and cycle.start_frame <= visible_end
        ):
            left = max(0.0, self.x_for_frame(cycle.start_frame))
            right = min(
                float(self.viewport().width()), self.x_for_frame(cycle.end_frame)
            )
            cycle_rect = QRectF(left, RULER_HEIGHT - 8, max(1.0, right - left), 6)
            painter.fillRect(cycle_rect, QColor(Color.ACCENT_PRIMARY))
            if cycle_rect.width() > 42:
                painter.setPen(QColor(Color.TEXT_PRIMARY))
                painter.drawText(
                    QRectF(cycle_rect.left() + 3, 20, 60, 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "CYCLE",
                )

        for marker in document.markers:
            end = (
                marker.end_frame if marker.end_frame is not None else marker.start_frame
            )
            if (
                marker.deleted
                or end < visible_start
                or marker.start_frame > visible_end
            ):
                continue
            x = max(0.0, self.x_for_frame(marker.start_frame))
            painter.setPen(QPen(QColor(Color.TEXT_SECONDARY), 1))
            painter.drawLine(int(x), 18, int(x), RULER_HEIGHT - 1)
            label_width = min(
                180, max(42, painter.fontMetrics().horizontalAdvance(marker.label) + 12)
            )
            label_rect = QRectF(x + 3, 2, label_width, 18)
            if marker.kind is MarkerKind.SECTION and marker.end_frame is not None:
                section_right = min(
                    float(self.viewport().width()), self.x_for_frame(marker.end_frame)
                )
                section_header = QColor(Color.TEXT_SECONDARY)
                section_header.setAlpha(35)
                painter.fillRect(
                    QRectF(x, 20, max(1.0, section_right - x), 4), section_header
                )
            painter.setPen(QColor(Color.TEXT_PRIMARY))
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    marker.label,
                    Qt.TextElideMode.ElideRight,
                    label_width,
                ),
            )

        preview = self._section_preview
        if preview is not None:
            marker_id, start_frame, end_frame = preview
            marker = next(
                (
                    item
                    for item in document.markers
                    if item.marker_id == marker_id and not item.deleted
                ),
                None,
            )
            if marker is not None:
                left = self.x_for_frame(start_frame)
                right = self.x_for_frame(end_frame)
                preview_color = QColor(Color.ACCENT_PRIMARY)
                preview_color.setAlpha(115)
                painter.fillRect(
                    QRectF(left, 19, max(1.0, right - left), 6),
                    preview_color,
                )
                painter.setPen(QColor(Color.ACCENT_PRIMARY))
                painter.drawText(
                    QRectF(left + 3, 2, max(42.0, right - left - 6), 17),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    marker.label,
                )

        snap_text = f"SNAP {document.snap_mode.value.upper()}"
        snap_width = painter.fontMetrics().horizontalAdvance(snap_text) + 14
        snap_rect = QRectF(
            max(0, self.viewport().width() - snap_width - 5),
            2,
            snap_width,
            18,
        )
        painter.fillRect(snap_rect, QColor(Color.BG_PANEL))
        painter.setPen(QColor(Color.TEXT_SECONDARY))
        painter.drawText(
            snap_rect,
            Qt.AlignmentFlag.AlignCenter,
            snap_text,
        )

    def _draw_playhead(self, painter: QPainter) -> None:
        x = self.x_for_frame(self._arrange._playhead_frame)
        if x < -8 or x > self.viewport().width() + 8:
            return
        painter.setPen(QPen(QColor(Color.ACCENT_PRIMARY), 2))
        painter.drawLine(int(x), 0, int(x), self.viewport().height())
        painter.setBrush(QColor(Color.ACCENT_PRIMARY))
        painter.setPen(Qt.PenStyle.NoPen)
        marker = QPainterPath()
        marker.moveTo(x - 5, 0)
        marker.lineTo(x + 5, 0)
        marker.lineTo(x, 7)
        marker.closeSubpath()
        painter.drawPath(marker)

    def _mouse_press(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.viewport().setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        if point.y() < RULER_HEIGHT:
            section = self._section_at(point.x(), point.y())
            if section is not None and section.end_frame is not None:
                press_frame = self.frame_at_x(point.x())
                self._gesture = _Gesture(
                    kind="section_move",
                    region_id=section.marker_id,
                    lane_id="",
                    press_frame=press_frame,
                    original_start=section.start_frame,
                    original_end=section.end_frame,
                )
                self._section_preview = (
                    section.marker_id,
                    section.start_frame,
                    section.end_frame,
                )
                event.accept()
                return
            self._gesture = _Gesture(
                kind="scrub",
                region_id="",
                lane_id="",
                press_frame=self.frame_at_x(point.x()),
                original_start=0,
                original_end=0,
            )
            self._emit_scrub(point.x())
            event.accept()
            return

        layout = self._track_at_y(point.y())
        if layout is None:
            event.ignore()
            return
        region = self._region_at(point.x(), point.y())
        if region is None:
            self._arrange._user_select_track(layout.track_id)
            self._emit_scrub(point.x())
            event.accept()
            return

        self._arrange._user_select_region(region)
        frame = self.frame_at_x(point.x())
        row_id = self._row_at_y(layout, point.y())
        if (
            row_id in self._lane_layouts
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            frame = max(
                region.timeline_start_frame,
                min(region.timeline_end_frame, frame),
            )
            self._gesture = _Gesture(
                kind="comp",
                region_id=region.region_id,
                lane_id=row_id,
                press_frame=frame,
                original_start=region.timeline_start_frame,
                original_end=region.timeline_end_frame,
            )
            self._comp_preview = (row_id, frame, frame)
            event.accept()
            return
        left_distance = abs(point.x() - self.x_for_frame(region.timeline_start_frame))
        right_distance = abs(point.x() - self.x_for_frame(region.timeline_end_frame))
        if min(left_distance, right_distance) <= _EDGE_HIT_PIXELS:
            kind = "trim_start" if left_distance <= right_distance else "trim_end"
        else:
            kind = "move"
        self._gesture = _Gesture(
            kind=kind,
            region_id=region.region_id,
            lane_id="",
            press_frame=frame,
            original_start=region.timeline_start_frame,
            original_end=region.timeline_end_frame,
        )
        event.accept()

    def _mouse_move(self, event: QMouseEvent) -> None:
        point = event.position()
        gesture = self._gesture
        if gesture is None:
            self._update_hover_cursor(point.x(), point.y())
            event.ignore()
            return
        if gesture.kind == "scrub":
            self._emit_scrub(point.x())
            event.accept()
            return
        if gesture.kind == "comp":
            self._emit_comp_gesture(
                gesture,
                self.frame_at_x(point.x()),
                commit=False,
            )
            event.accept()
            return
        if gesture.kind == "section_move":
            self._emit_section_gesture(
                gesture,
                self.frame_at_x(point.x()),
                commit=False,
            )
            event.accept()
            return
        self._emit_region_gesture(
            gesture,
            self.frame_at_x(point.x()),
            commit=False,
        )
        event.accept()

    def _mouse_release(self, event: QMouseEvent) -> None:
        gesture = self._gesture
        if event.button() != Qt.MouseButton.LeftButton or gesture is None:
            event.ignore()
            return
        if gesture.kind == "scrub":
            self._emit_scrub(event.position().x())
        elif gesture.kind == "comp":
            self._emit_comp_gesture(
                gesture,
                self.frame_at_x(event.position().x()),
                commit=True,
            )
        elif gesture.kind == "section_move":
            self._emit_section_gesture(
                gesture,
                self.frame_at_x(event.position().x()),
                commit=True,
            )
        else:
            self._emit_region_gesture(
                gesture,
                self.frame_at_x(event.position().x()),
                commit=True,
            )
        self._gesture = None
        self._preview_range = None
        self._comp_preview = None
        self._section_preview = None
        self.viewport().unsetCursor()
        self.viewport().update()
        event.accept()

    def _emit_scrub(self, x: float) -> None:
        frame = max(0, self._snap_frame(self.frame_at_x(x)))
        self._arrange.scrub_requested.emit(frame)

    def _emit_section_gesture(
        self,
        gesture: _Gesture,
        current_frame: int,
        *,
        commit: bool,
    ) -> None:
        length = gesture.original_end - gesture.original_start
        target = max(
            0,
            self._snap_frame(
                gesture.original_start + current_frame - gesture.press_frame
            ),
        )
        if gesture.original_start < target < gesture.original_end:
            target = gesture.original_start
        self._section_preview = (
            gesture.region_id,
            target,
            target + length,
        )
        self.viewport().update()
        if commit and target != gesture.original_start:
            self._arrange.section_move_requested.emit(gesture.region_id, target)

    def _emit_region_gesture(
        self,
        gesture: _Gesture,
        current_frame: int,
        *,
        commit: bool,
    ) -> None:
        if gesture.kind == "move":
            target = self._snap_frame(
                gesture.original_start + current_frame - gesture.press_frame
            )
            original_target = gesture.original_start
            preview_start = target
            preview_end = target + gesture.original_end - gesture.original_start
        elif gesture.kind == "trim_start":
            target = min(
                gesture.original_end - 1,
                self._snap_frame(
                    gesture.original_start + current_frame - gesture.press_frame
                ),
            )
            original_target = gesture.original_start
            preview_start = target
            preview_end = gesture.original_end
        else:
            target = max(
                gesture.original_start + 1,
                self._snap_frame(
                    gesture.original_end + current_frame - gesture.press_frame
                ),
            )
            original_target = gesture.original_end
            preview_start = gesture.original_start
            preview_end = target
        self._preview_range = (gesture.region_id, preview_start, preview_end)
        self.viewport().update()
        if not commit or target == original_target:
            return
        if gesture.kind == "move":
            self._arrange.region_move_requested.emit(gesture.region_id, target)
        else:
            edge = "start" if gesture.kind == "trim_start" else "end"
            self._arrange.region_trim_requested.emit(gesture.region_id, edge, target)

    def _emit_comp_gesture(
        self,
        gesture: _Gesture,
        current_frame: int,
        *,
        commit: bool,
    ) -> None:
        current = max(
            gesture.original_start,
            min(gesture.original_end, self._snap_frame(current_frame)),
        )
        pressed = max(
            gesture.original_start,
            min(gesture.original_end, self._snap_frame(gesture.press_frame)),
        )
        start, end = sorted((pressed, current))
        self._comp_preview = (gesture.lane_id, start, end)
        self.viewport().update()
        if commit and end > start:
            self._arrange.comp_range_requested.emit(gesture.lane_id, start, end)

    def _update_hover_cursor(self, x: float, y: float) -> None:
        if y < RULER_HEIGHT:
            self.viewport().setCursor(
                Qt.CursorShape.SizeAllCursor
                if self._section_at(x, y) is not None
                else Qt.CursorShape.SplitHCursor
            )
            return
        region = self._region_at(x, y)
        if region is None:
            self.viewport().unsetCursor()
            return
        left = abs(x - self.x_for_frame(region.timeline_start_frame))
        right = abs(x - self.x_for_frame(region.timeline_end_frame))
        self.viewport().setCursor(
            Qt.CursorShape.SizeHorCursor
            if min(left, right) <= _EDGE_HIT_PIXELS
            else Qt.CursorShape.SizeAllCursor
        )

    def _wheel(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = delta / 120.0
            self.set_zoom(
                self._pixels_per_second * (1.2**steps),
                anchor_x=event.position().x(),
            )
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(
                bar.value() - round(delta / 120.0 * max(1, bar.singleStep() * 3))
            )
        else:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - round(delta / 120.0 * TAKE_LANE_HEIGHT))
        event.accept()


class StudioArrange(QWidget):
    """Scrollable Arrange editor surface driven only by semantic signals.

    Move requests carry the target timeline start.  Trim requests carry
    ``"start"`` or ``"end"`` plus the target half-open boundary.  Duplicate
    requests include a suggested adjacent start frame, and snap-mode requests
    carry the persisted :class:`SnapMode` string value.  Every frame argument
    remains a Python integer so long projects are not truncated by Qt.
    """

    # Qt's ``int`` metatype is 32-bit on supported platforms, while Studio
    # frames are explicitly 64-bit.  Object-typed frame arguments preserve the
    # exact Python integer instead of silently wrapping long arrangements.
    scrub_requested = Signal(object)
    track_selected = Signal(str)
    region_selected = Signal(str)
    region_move_requested = Signal(str, object)
    region_trim_requested = Signal(str, str, object)
    split_region_requested = Signal(str, object)
    duplicate_region_requested = Signal(str, object)
    region_enabled_requested = Signal(str, bool)
    delete_region_requested = Signal(str)
    lane_selected = Signal(str)
    lane_audition_requested = Signal(str)
    comp_range_requested = Signal(str, object, object)
    section_move_requested = Signal(str, object)
    viewport_changed = Signal()
    snap_mode_requested = Signal(str)
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: StudioDocument | None = None
        self._playhead_frame = 0
        self._track_names: dict[str, str] = {}
        self._selected_track_id: str | None = None
        self._selected_region_id: str | None = None
        self._selected_lane_id: str | None = None
        self._audition_lane_id: str | None = None
        self._region_by_id: dict[str, StudioRegion] = {}
        self._waveform_tiles: dict[str, dict[WaveformTileKey, WaveformTile]] = {}
        self._waveform_bindings: OrderedDict[
            tuple[str, WaveformTileKey], WaveformTile
        ] = OrderedDict()
        self._waveform_retained_bytes = 0
        self._waveform_generation: int | None = None
        self._shortcuts: list[QShortcut] = []
        self._shortcut_keys: set[str] = set()

        self.setObjectName("StudioArrange")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 280)
        self.setAccessibleName("Studio Arrange editor")
        self.setAccessibleDescription(
            "Non-destructive frame-domain arrangement editor. No source audio is "
            "changed by this surface."
        )
        self.setToolTip(
            "Ctrl+Z undo · Ctrl+Shift+Z redo · Ctrl+E split · Ctrl+D duplicate · "
            "Delete remove · arrows select tracks/regions · Alt+←/→ nudge · "
            "drag a named section bar to rearrange the song · "
            "Option/Alt-drag lane to comp · double-click lane to audition · "
            "Ctrl++/Ctrl+- zoom"
        )

        self._headers = _TrackHeaders(self)
        self._canvas = _ArrangeScrollArea(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._headers)
        layout.addWidget(self._canvas, 1)
        self._install_shortcuts()

    @property
    def selected_track_id(self) -> str | None:
        return self._selected_track_id

    @property
    def selected_region_id(self) -> str | None:
        return self._selected_region_id

    @property
    def selected_lane_id(self) -> str | None:
        return self._selected_lane_id

    @property
    def audition_lane_id(self) -> str | None:
        return self._audition_lane_id

    @property
    def playhead_frame(self) -> int:
        """Return the current non-negative project-frame playhead."""

        return self._playhead_frame

    @property
    def pixels_per_second(self) -> float:
        return self._canvas.pixels_per_second

    @property
    def pixels_per_frame(self) -> float:
        return self._canvas.pixels_per_frame

    @property
    def horizontal_scroll_frame(self) -> int:
        return round(self._canvas._visible_start_frame())

    @property
    def waveform_retained_entries(self) -> int:
        """Return the number of disposable waveform bindings held for painting."""

        return len(self._waveform_bindings)

    @property
    def waveform_retained_bytes(self) -> int:
        """Return the exact NumPy payload bytes held by waveform bindings."""

        return self._waveform_retained_bytes

    def set_document(self, document: StudioDocument | None) -> None:
        """Display an immutable snapshot, preserving only still-valid selection."""

        if document is not None and not isinstance(document, StudioDocument):
            raise TypeError("document must be a StudioDocument or None.")
        previous = self._document
        self._document = document
        previous_sources = (
            {
                item.region_id: (
                    item.source_take_id,
                    item.source_track_id,
                    item.source_segment_id,
                    item.source_start_frame,
                    item.source_frame_count,
                )
                for item in previous.regions
                if not item.deleted
            }
            if previous is not None
            else {}
        )
        if document is None:
            self._region_by_id = {}
            self._selected_track_id = None
            self._selected_region_id = None
            self._selected_lane_id = None
            self._audition_lane_id = None
        else:
            track_ids = {item.track_id for item in document.tracks}
            if self._selected_track_id not in track_ids:
                self._selected_track_id = None
            active_regions = {
                item.region_id: item for item in document.regions if not item.deleted
            }
            self._region_by_id = active_regions
            selected_region = active_regions.get(self._selected_region_id or "")
            if selected_region is None:
                self._selected_region_id = None
            else:
                self._selected_track_id = selected_region.track_id
            lane_ids = {
                item.lane_id for item in document.take_lanes if not item.deleted
            }
            if self._selected_lane_id not in lane_ids:
                self._selected_lane_id = None
            if self._audition_lane_id not in lane_ids:
                self._audition_lane_id = None
            if selected_region is not None:
                self._selected_lane_id = next(
                    (
                        item.lane_id
                        for item in document.take_lanes
                        if not item.deleted
                        and selected_region.region_id in item.region_ids
                    ),
                    None,
                )
            current_sources = {
                item.region_id: (
                    item.source_take_id,
                    item.source_track_id,
                    item.source_segment_id,
                    item.source_start_frame,
                    item.source_frame_count,
                )
                for item in document.regions
                if not item.deleted
            }
            same_document_identity = previous is not None and (
                previous.session_id,
                previous.take_id,
                previous.project_sample_rate,
            ) == (
                document.session_id,
                document.take_id,
                document.project_sample_rate,
            )
            if same_document_identity:
                self._waveform_tiles = {
                    region_id: tiles
                    for region_id, tiles in self._waveform_tiles.items()
                    if previous_sources.get(region_id) == current_sources.get(region_id)
                }
                self._rebuild_waveform_bindings()
            else:
                self._clear_waveform_storage()
                self._waveform_generation = None
        if document is None:
            self._clear_waveform_storage()
            self._waveform_generation = None
        self._canvas.set_document(document)
        self._refresh_accessible_state()
        self._headers.update()
        self.viewport_changed.emit()

    def set_selection(
        self,
        *,
        track_id: str | None = None,
        region_id: str | None = None,
    ) -> None:
        """Synchronize controller-owned selection without emitting user signals."""

        document = self._document
        if document is None:
            if track_id is not None or region_id is not None:
                raise ValueError("No Studio arrangement is open.")
            self._selected_track_id = None
            self._selected_region_id = None
            self._selected_lane_id = None
        elif region_id is not None:
            region = next(
                (
                    item
                    for item in document.regions
                    if item.region_id == region_id and not item.deleted
                ),
                None,
            )
            if region is None:
                raise ValueError("region_id is not an active Studio region.")
            if track_id is not None and track_id != region.track_id:
                raise ValueError("track_id does not own region_id.")
            self._selected_track_id = region.track_id
            self._selected_region_id = region.region_id
            self._selected_lane_id = next(
                (
                    item.lane_id
                    for item in document.take_lanes
                    if not item.deleted and region.region_id in item.region_ids
                ),
                None,
            )
        elif track_id is not None:
            if track_id not in {item.track_id for item in document.tracks}:
                raise ValueError("track_id is not part of this Studio arrangement.")
            self._selected_track_id = track_id
            self._selected_region_id = None
            self._selected_lane_id = None
        else:
            self._selected_track_id = None
            self._selected_region_id = None
            self._selected_lane_id = None
        self._refresh_accessible_state()
        self._headers.update()
        self._canvas.viewport().update()

    def set_audition_lane(self, lane_id: str | None) -> None:
        """Show which temporary lane audition currently owns the transport."""

        if lane_id is not None:
            document = self._document
            if document is None or lane_id not in {
                item.lane_id for item in document.take_lanes if not item.deleted
            }:
                raise ValueError("lane_id is not an active Studio take lane.")
        self._audition_lane_id = lane_id
        self._refresh_accessible_state()
        self._headers.update()

    def set_playhead(self, frame: int) -> None:
        """Set the non-negative project-frame playhead without initiating audio."""

        value = max(0, _require_frame(frame, "playhead"))
        if value == self._playhead_frame:
            return
        outside = (
            value < self._canvas._minimum_frame or value > self._canvas._maximum_frame
        )
        self._playhead_frame = value
        if outside:
            start = self._canvas._visible_start_frame()
            self._canvas._rebuild_timeline_bounds()
            self._canvas._update_scrollbars(scroll_start=start)
        self._canvas.viewport().update()

    def set_track_names(self, names: Mapping[str, str]) -> None:
        """Set presentation-only names keyed by durable Studio track ID."""

        if not isinstance(names, Mapping):
            raise TypeError("track names must be a mapping of track IDs to text.")
        if len(names) > 512:
            raise ValueError("track names cannot contain more than 512 entries.")
        validated: dict[str, str] = {}
        for track_id, name in names.items():
            if not isinstance(track_id, str) or not isinstance(name, str):
                raise TypeError("track names must map text IDs to text names.")
            cleaned = name.strip()
            if len(cleaned) > _MAX_TRACK_NAME_CHARACTERS:
                raise ValueError(
                    f"track names cannot exceed {_MAX_TRACK_NAME_CHARACTERS} characters."
                )
            validated[track_id] = cleaned
        self._track_names = validated
        self._headers.update()

    def set_zoom(self, pixels_per_second: float) -> None:
        """Set bounded elapsed-time zoom while keeping the viewport center stable."""

        self._canvas.set_zoom(pixels_per_second)

    def zoom_in(self) -> None:
        self.set_zoom(self.pixels_per_second * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.pixels_per_second / 1.25)

    def scroll_to_frame(self, frame: int, *, center: bool = True) -> None:
        """Scroll so ``frame`` is centered, or at the left edge when requested."""

        if not isinstance(center, bool):
            raise TypeError("center must be true or false.")
        self._canvas.scroll_to_frame(frame, center=center)

    def visible_frame_range(self) -> tuple[int, int]:
        return self._canvas.visible_frame_range()

    def visible_region_ids(self) -> frozenset[str]:
        """Return regions intersecting both the horizontal and vertical viewport."""

        start, end = self.visible_frame_range()
        values: set[str] = set()
        for layout in self._canvas._track_layouts:
            if not self._canvas._layout_visible(layout):
                continue
            row_ids = (layout.track_id, *(lane.lane_id for lane in layout.lanes))
            for row_id in row_ids:
                if (
                    row_id in self._canvas._lane_layouts
                    and not self._canvas._lane_visible(
                        self._canvas._lane_layouts[row_id]
                    )
                ):
                    continue
                values.update(
                    item.region_id
                    for item in self._canvas._visible_regions_for_row(
                        row_id, start, end
                    )
                )
        return frozenset(values)

    def add_region_waveform_tile(
        self,
        region_id: str,
        tile: WaveformTile,
        *,
        generation: int | None = None,
    ) -> None:
        """Publish one immutable worker-produced tile onto the UI thread."""

        if not isinstance(tile, WaveformTile):
            raise TypeError("tile must be a WaveformTile.")
        if generation is not None:
            if isinstance(generation, bool) or not isinstance(generation, int):
                raise TypeError("generation must be a non-negative integer.")
            if generation < 0:
                raise ValueError("generation must be a non-negative integer.")
            if generation != self._waveform_generation:
                return
        region = self._region_by_id.get(region_id)
        if region is None:
            return
        catalog_key = tile.key.source.catalog_key
        if catalog_key and catalog_key != (
            region.source_take_id,
            region.source_track_id,
            region.source_segment_id,
        ):
            raise ValueError("Waveform tile belongs to a different Studio source.")
        tile_end = tile.key.start_frame + tile.key.frame_count
        if (
            tile_end <= region.source_start_frame
            or tile.key.start_frame >= region.source_end_frame
        ):
            return
        binding = (region.region_id, tile.key)
        previous = self._waveform_bindings.pop(binding, None)
        if previous is not None:
            self._waveform_retained_bytes -= previous.byte_size
        self._waveform_tiles.setdefault(region.region_id, {})[tile.key] = tile
        self._waveform_bindings[binding] = tile
        self._waveform_retained_bytes += tile.byte_size
        self._evict_waveform_bindings()
        self._canvas.viewport().update()

    def begin_waveform_generation(self, generation: int) -> None:
        """Accept only tiles from ``generation`` and discard the prior view."""

        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("generation must be a non-negative integer.")
        if generation < 0:
            raise ValueError("generation must be a non-negative integer.")
        if generation == self._waveform_generation:
            return
        self._waveform_generation = generation
        self._clear_waveform_storage()
        self._canvas.viewport().update()

    def clear_waveforms(self) -> None:
        """Discard all disposable waveform view tiles."""

        had_tiles = bool(self._waveform_tiles)
        self._waveform_generation = None
        self._clear_waveform_storage()
        if not had_tiles:
            return
        self._canvas.viewport().update()

    def _clear_waveform_storage(self) -> None:
        self._waveform_tiles.clear()
        self._waveform_bindings.clear()
        self._waveform_retained_bytes = 0

    def _rebuild_waveform_bindings(self) -> None:
        retained = self._waveform_tiles
        self._waveform_bindings.clear()
        self._waveform_retained_bytes = 0
        for region_id, tiles in retained.items():
            for key, tile in tiles.items():
                self._waveform_bindings[(region_id, key)] = tile
                self._waveform_retained_bytes += tile.byte_size
        self._evict_waveform_bindings()

    def _evict_waveform_bindings(self) -> None:
        while self._waveform_bindings and (
            len(self._waveform_bindings) > MAX_ARRANGE_WAVEFORM_BINDINGS
            or self._waveform_retained_bytes > MAX_ARRANGE_WAVEFORM_BYTES
        ):
            (region_id, key), tile = self._waveform_bindings.popitem(last=False)
            region_tiles = self._waveform_tiles.get(region_id)
            if region_tiles is not None:
                region_tiles.pop(key, None)
                if not region_tiles:
                    self._waveform_tiles.pop(region_id, None)
            self._waveform_retained_bytes -= tile.byte_size

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(760, 600)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # The standalone editor starts at 480x280.  RecordingStudio lowers
        # that floor for its embedded Arrange/mixer split, and Qt's splitter
        # must receive the active minimum rather than this original constant.
        return QSize(self.minimumWidth(), self.minimumHeight())

    def _track_name(self, track_id: str) -> str:
        value = self._track_names.get(track_id, "")
        if value:
            return value
        document = self._document
        if document is None:
            return "Track"
        ordered = sorted(document.tracks, key=lambda item: (item.order, item.track_id))
        for index, track in enumerate(ordered, 1):
            if track.track_id == track_id:
                return f"Track {index}"
        return "Track"

    def _user_select_track(self, track_id: str) -> None:
        self._selected_track_id = track_id
        self._selected_region_id = None
        self._selected_lane_id = None
        self._refresh_accessible_state()
        self._headers.update()
        self._canvas.viewport().update()
        self.track_selected.emit(track_id)

    def _user_select_lane(self, track_id: str, lane_id: str) -> None:
        self._selected_track_id = track_id
        self._selected_region_id = None
        self._selected_lane_id = lane_id
        self._refresh_accessible_state()
        self._headers.update()
        self._canvas.viewport().update()
        self.track_selected.emit(track_id)
        self.lane_selected.emit(lane_id)

    def _user_select_region(self, region: StudioRegion) -> None:
        self._selected_track_id = region.track_id
        self._selected_region_id = region.region_id
        self._selected_lane_id = self._canvas._lane_by_region.get(region.region_id)
        self._refresh_accessible_state()
        self._headers.update()
        self._canvas.viewport().update()
        self.track_selected.emit(region.track_id)
        self.region_selected.emit(region.region_id)

    def _refresh_accessible_state(self) -> None:
        document = self._document
        if document is None:
            self._canvas.viewport().setAccessibleDescription(
                "No Studio arrangement is open."
            )
            self._headers.setAccessibleDescription(
                "Fixed track and take-lane names. No take is open."
            )
            return

        selection = "No track or region is selected."
        region = self._selected_region()
        if region is not None:
            row = "alternate take lane" if self._selected_lane_id else "main track"
            selection = (
                f"Selected region on {self._track_name(region.track_id)} {row}, "
                f"frames {region.timeline_start_frame} through "
                f"{region.timeline_end_frame}."
            )
        elif self._selected_lane_id is not None:
            lane = next(
                (
                    item
                    for item in document.take_lanes
                    if item.lane_id == self._selected_lane_id and not item.deleted
                ),
                None,
            )
            if lane is not None:
                selection = (
                    f"Selected alternate take lane {lane.name or 'Alternate take'} "
                    f"on {self._track_name(lane.track_id)}."
                )
        elif self._selected_track_id is not None:
            selection = f"Selected track {self._track_name(self._selected_track_id)}."

        audition = (
            " An alternate lane is being auditioned."
            if self._audition_lane_id is not None
            else ""
        )
        self._canvas.viewport().setAccessibleDescription(
            "Frame-accurate regions, markers, take lanes, fades, and playhead. "
            f"{len(document.tracks)} tracks; {document.snap_mode.value} snapping. "
            f"{selection}{audition} Arrow keys select rows and regions; Alt plus "
            "Left or Right nudges; Control plus left or right bracket trims to "
            "the playhead; Control Alt C comps a selected lane region; Control "
            "Alt A auditions its lane; Control Alt Left or Right moves the named "
            "section at the playhead."
        )
        self._headers.setAccessibleDescription(
            "Fixed track and take-lane names. Use arrow keys or pointer to "
            "select a row. "
            f"{len(document.tracks)} tracks are available. {selection}"
        )

    def _ordered_rows(self) -> tuple[tuple[str, str], ...]:
        """Return keyboard-selectable ``(track_id, row_id)`` pairs in paint order."""

        values: list[tuple[str, str]] = []
        for layout in self._canvas._track_layouts:
            values.append((layout.track_id, layout.track_id))
            values.extend((layout.track_id, lane.lane_id) for lane in layout.lanes)
        return tuple(values)

    def _request_row_selection(self, direction: int) -> None:
        rows = self._ordered_rows()
        if not rows:
            return
        current_row = self._selected_lane_id or self._selected_track_id
        current_index = next(
            (
                index
                for index, (_track_id, row_id) in enumerate(rows)
                if row_id == current_row
            ),
            -1 if direction > 0 else len(rows),
        )
        target_index = max(0, min(len(rows) - 1, current_index + direction))
        track_id, row_id = rows[target_index]
        if row_id == track_id:
            self._user_select_track(track_id)
        else:
            self._user_select_lane(track_id, row_id)
        self._scroll_row_into_view(row_id)

    def _scroll_row_into_view(self, row_id: str) -> None:
        rect = self._canvas._row_rect(row_id)
        if rect is None:
            return
        bar = self._canvas.verticalScrollBar()
        if rect.top() < RULER_HEIGHT:
            bar.setValue(max(0, bar.value() + round(rect.top() - RULER_HEIGHT)))
        elif rect.bottom() > self._canvas.viewport().height():
            bar.setValue(
                min(
                    bar.maximum(),
                    bar.value()
                    + round(rect.bottom() - self._canvas.viewport().height()),
                )
            )

    def _request_region_selection(self, direction: int) -> None:
        rows = self._ordered_rows()
        if not rows:
            return
        if self._selected_track_id is None:
            self._request_row_selection(1)
        row_id = self._selected_lane_id or self._selected_track_id
        if row_id is None:
            return
        regions = self._canvas._regions_by_row.get(row_id, ())
        if not regions:
            return
        current_index = next(
            (
                index
                for index, region in enumerate(regions)
                if region.region_id == self._selected_region_id
            ),
            -1 if direction > 0 else len(regions),
        )
        target_index = max(0, min(len(regions) - 1, current_index + direction))
        region = regions[target_index]
        self._user_select_region(region)
        start, end = self.visible_frame_range()
        if not start <= region.timeline_start_frame < end:
            self.scroll_to_frame(region.timeline_start_frame)

    def _keyboard_step(self, frame: int, direction: int) -> int:
        document = self._document
        if document is None:
            return frame
        if document.snap_mode is SnapMode.TIME:
            return frame + direction * max(1, document.project_sample_rate // 10)
        if document.snap_mode is SnapMode.MARKERS:
            boundaries = sorted(
                {
                    boundary
                    for marker in document.markers
                    if not marker.deleted
                    for boundary in (marker.start_frame, marker.end_frame)
                    if boundary is not None
                }
            )
            candidates = (
                [item for item in boundaries if item > frame]
                if direction > 0
                else [item for item in boundaries if item < frame]
            )
            if candidates:
                return candidates[0] if direction > 0 else candidates[-1]
        return frame + direction

    def _request_nudge(self, direction: int) -> None:
        region = self._selected_region()
        if region is None:
            return
        target = self._keyboard_step(region.timeline_start_frame, direction)
        if target != region.timeline_start_frame:
            self.region_move_requested.emit(region.region_id, target)

    def _request_trim_to_playhead(self, edge: str) -> None:
        region = self._selected_region()
        if region is None:
            return
        frame = self._canvas._snap_frame(self._playhead_frame)
        if (
            edge == "start"
            and region.timeline_start_frame < frame < region.timeline_end_frame
        ):
            self.region_trim_requested.emit(region.region_id, edge, frame)
        elif (
            edge == "end"
            and region.timeline_start_frame < frame < region.timeline_end_frame
        ):
            self.region_trim_requested.emit(region.region_id, edge, frame)

    def _request_selected_lane_audition(self) -> None:
        if self._selected_lane_id is not None:
            self.lane_audition_requested.emit(self._selected_lane_id)

    def _request_selected_lane_comp(self) -> None:
        region = self._selected_region()
        if region is None or self._selected_lane_id is None:
            return
        self.comp_range_requested.emit(
            self._selected_lane_id,
            region.timeline_start_frame,
            region.timeline_end_frame,
        )

    def _request_section_move(self, direction: int) -> None:
        document = self._document
        if document is None:
            return
        sections = tuple(
            sorted(
                (
                    marker
                    for marker in document.markers
                    if not marker.deleted
                    and marker.kind is MarkerKind.SECTION
                    and marker.end_frame is not None
                ),
                key=lambda marker: (marker.start_frame, marker.marker_id),
            )
        )
        if not sections:
            return
        current_index = next(
            (
                index
                for index, marker in enumerate(sections)
                if marker.start_frame <= self._playhead_frame < int(marker.end_frame)
            ),
            -1,
        )
        if current_index < 0:
            return
        target_index = current_index + direction
        if not 0 <= target_index < len(sections):
            return
        target = sections[target_index].start_frame
        self.section_move_requested.emit(sections[current_index].marker_id, target)

    def _selected_region(self) -> StudioRegion | None:
        document = self._document
        if document is None or self._selected_region_id is None:
            return None
        for region in document.regions:
            if region.region_id == self._selected_region_id and not region.deleted:
                return region
        return None

    def _request_split(self) -> None:
        region = self._selected_region()
        if region is None:
            return
        frame = self._canvas._snap_frame(self._playhead_frame)
        if region.timeline_start_frame < frame < region.timeline_end_frame:
            self.split_region_requested.emit(region.region_id, frame)

    def _request_duplicate(self) -> None:
        region = self._selected_region()
        if region is not None:
            self.duplicate_region_requested.emit(
                region.region_id, region.timeline_end_frame
            )

    def _request_toggle_enabled(self) -> None:
        region = self._selected_region()
        if region is not None:
            self.region_enabled_requested.emit(region.region_id, not region.enabled)

    def _request_delete(self) -> None:
        region = self._selected_region()
        if region is not None:
            self.delete_region_requested.emit(region.region_id)

    def _request_snap_mode(self, mode: SnapMode) -> None:
        self.snap_mode_requested.emit(mode.value)

    def _install_shortcuts(self) -> None:
        self._add_shortcut(QKeySequence.StandardKey.Undo, self.undo_requested.emit)
        self._add_shortcut("Ctrl+Z", self.undo_requested.emit)
        self._add_shortcut(QKeySequence.StandardKey.Redo, self.redo_requested.emit)
        self._add_shortcut("Ctrl+Shift+Z", self.redo_requested.emit)
        self._add_shortcut("Ctrl+Y", self.redo_requested.emit)
        self._add_shortcut("Ctrl+E", self._request_split)
        self._add_shortcut("Ctrl+D", self._request_duplicate)
        self._add_shortcut("Shift+Delete", self._request_toggle_enabled)
        self._add_shortcut("Delete", self._request_delete)
        self._add_shortcut("Ctrl++", self.zoom_in)
        self._add_shortcut("Ctrl+=", self.zoom_in)
        self._add_shortcut("Ctrl+-", self.zoom_out)
        self._add_shortcut("Up", lambda: self._request_row_selection(-1))
        self._add_shortcut("Down", lambda: self._request_row_selection(1))
        self._add_shortcut("Left", lambda: self._request_region_selection(-1))
        self._add_shortcut("Right", lambda: self._request_region_selection(1))
        self._add_shortcut("Alt+Left", lambda: self._request_nudge(-1))
        self._add_shortcut("Alt+Right", lambda: self._request_nudge(1))
        self._add_shortcut("Ctrl+[", lambda: self._request_trim_to_playhead("start"))
        self._add_shortcut("Ctrl+]", lambda: self._request_trim_to_playhead("end"))
        self._add_shortcut("Ctrl+Alt+A", self._request_selected_lane_audition)
        self._add_shortcut("Ctrl+Alt+C", self._request_selected_lane_comp)
        self._add_shortcut("Ctrl+Alt+Left", lambda: self._request_section_move(-1))
        self._add_shortcut("Ctrl+Alt+Right", lambda: self._request_section_move(1))
        self._add_shortcut("Alt+1", lambda: self._request_snap_mode(SnapMode.OFF))
        self._add_shortcut("Alt+2", lambda: self._request_snap_mode(SnapMode.TIME))
        self._add_shortcut("Alt+3", lambda: self._request_snap_mode(SnapMode.MARKERS))

    def _add_shortcut(
        self,
        sequence: QKeySequence.StandardKey | str,
        callback: Callable[[], None],
    ) -> None:
        key_sequence = (
            QKeySequence(sequence)
            if isinstance(sequence, QKeySequence.StandardKey)
            else QKeySequence(sequence)
        )
        portable_key = key_sequence.toString(QKeySequence.SequenceFormat.PortableText)
        if portable_key in self._shortcut_keys:
            return
        shortcut = QShortcut(key_sequence, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)
        self._shortcut_keys.add(portable_key)


__all__ = [
    "DEFAULT_PIXELS_PER_SECOND",
    "MAX_ARRANGE_WAVEFORM_BINDINGS",
    "MAX_ARRANGE_WAVEFORM_BYTES",
    "MAX_PIXELS_PER_SECOND",
    "MIN_PIXELS_PER_SECOND",
    "RULER_HEIGHT",
    "STUDIO_ARRANGE_TRACK_HEADER_WIDTH",
    "StudioArrange",
    "TAKE_LANE_HEIGHT",
    "TRACK_HEIGHT",
]

# Descriptive public alias without exposing the implementation class.
STUDIO_ARRANGE_TRACK_HEADER_WIDTH = TRACK_HEADER_WIDTH

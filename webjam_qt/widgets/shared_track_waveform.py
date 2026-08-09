"""Reusable, path-free waveform presentation for WebJam's Shared Track."""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

from webjam_qt.theme.tokens import Color


def _clock_text(seconds: float) -> str:
    bounded = max(0, int(float(seconds or 0.0)))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class SharedTrackWaveform(QFrame):
    """Render fixed-bin source peaks, playhead, loop, and analysis progress."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._peaks: tuple[float, ...] = ()
        self._progress = 0.0
        self._position = 0.0
        self._duration = 0.0
        self._loop_start = 0.0
        self._loop_end: float | None = None
        self.setObjectName(
            "SharedTrackWaveformCompact" if compact else "SharedTrackWaveform"
        )
        self.setAccessibleName("Shared Track waveform")
        self.setAccessibleDescription("No Shared Track loaded")
        self.setMinimumHeight(28 if compact else 58)
        if compact:
            self.setFixedWidth(76)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed if compact else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def set_snapshot(self, snapshot: object) -> None:
        peaks = tuple(getattr(snapshot, "waveform_peaks", ()) or ())
        self._peaks = tuple(
            min(1.0, max(0.0, float(value))) for value in peaks
        )
        self._progress = min(
            1.0,
            max(0.0, float(getattr(snapshot, "waveform_progress", 0.0) or 0.0)),
        )
        self._duration = max(
            0.0, float(getattr(snapshot, "duration_s", 0.0) or 0.0)
        )
        self._position = min(
            self._duration,
            max(0.0, float(getattr(snapshot, "position_s", 0.0) or 0.0)),
        )
        self._loop_start = max(
            0.0, float(getattr(snapshot, "loop_start_s", 0.0) or 0.0)
        )
        loop_end = getattr(snapshot, "loop_end_s", None)
        self._loop_end = None if loop_end is None else max(0.0, float(loop_end))
        source_name = str(getattr(snapshot, "source_name", "") or "")
        description = (
            f"Waveform for {source_name}; {_clock_text(self._position)} of "
            f"{_clock_text(self._duration)}."
            if source_name
            else "No Shared Track loaded"
        )
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.update()

    def clear(self, description: str = "No Shared Track loaded") -> None:
        self._peaks = ()
        self._progress = 0.0
        self._position = 0.0
        self._duration = 0.0
        self._loop_start = 0.0
        self._loop_end = None
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bounds = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(bounds, QColor(Color.BG_INPUT))
        painter.setPen(QColor(Color.BORDER_SUBTLE))
        painter.drawRect(bounds)
        plot = bounds.adjusted(4, 4, -4, -4)
        center = plot.center().y()
        if not self._peaks or self._duration <= 0.0:
            painter.setPen(QColor(Color.BORDER_STRONG))
            painter.drawLine(plot.left(), center, plot.right(), center)
            return

        if self._loop_end is not None and self._loop_end > self._loop_start:
            loop_color = QColor(Color.ACCENT_PRIMARY)
            loop_color.setAlpha(42)
            left = plot.left() + round(
                plot.width() * min(1.0, self._loop_start / self._duration)
            )
            right = plot.left() + round(
                plot.width() * min(1.0, self._loop_end / self._duration)
            )
            painter.fillRect(
                left,
                plot.top(),
                max(1, right - left),
                plot.height(),
                loop_color,
            )

        played = self._position / self._duration
        count = len(self._peaks)
        for index, peak in enumerate(self._peaks):
            x = plot.left() + round(
                index * max(1, plot.width() - 1) / max(1, count - 1)
            )
            half = max(
                1,
                round(float(peak) * max(1, plot.height() // 2 - 1)),
            )
            painter.setPen(
                QColor(Color.ACCENT_PRIMARY)
                if index / max(1, count - 1) <= played
                else QColor(Color.TEXT_SECONDARY)
            )
            painter.drawLine(x, center - half, x, center + half)

        if self._progress < 1.0:
            progress_x = plot.left() + round(plot.width() * self._progress)
            painter.setPen(QColor(Color.TEXT_MUTED))
            painter.drawLine(progress_x, plot.top(), progress_x, plot.bottom())


__all__ = ["SharedTrackWaveform"]

"""LevelMeter — a horizontal audio level bar in the WebJam palette.

Decay is driven externally by ApplicationController via :meth:`tick_decay`.

When constructed with ``external_tick=True`` (the default), the meter does
*not* create its own QTimer; a single application-wide timer ticks every
meter at 25 Hz.  This keeps a 20-participant grid down to one timer
instead of twenty (a ~95% reduction in timer events/sec).

For backwards-compat (existing tests, standalone widget demos), passing
``external_tick=False`` restores the legacy self-driving behavior with a
per-instance QTimer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from webjam_qt.theme.tokens import Color


class LevelMeter(QWidget):
    """
    Paints a level bar from 0..1.

    The fill moves from neutral gray to burnt orange to white. The meter's
    length carries the level, so its meaning never depends on hue alone.

    Decay is driven externally by ApplicationController via :meth:`tick_decay`,
    unless ``external_tick=False`` is passed (legacy self-driving mode).
    """

    DECAY_PER_TICK = 0.03
    TICK_MS = 40

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        height: int = 4,
        external_tick: bool = True,
    ) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._peak = 0.0
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAccessibleName("Audio level meter")
        self.setAccessibleDescription("Audio level: quiet")
        self._accessible_band = "quiet"

        self._external_tick = external_tick
        if external_tick:
            # No per-instance timer — ApplicationController ticks all meters.
            self._decay_timer: QTimer | None = None
        else:
            self._decay_timer = QTimer(self)
            self._decay_timer.timeout.connect(self._decay)
            self._decay_timer.start(self.TICK_MS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        self._level = level
        self._peak = max(self._peak, level)
        band = "high" if level >= 0.85 else "signal present" if level >= 0.05 else "quiet"
        if band != self._accessible_band:
            self._accessible_band = band
            self.setAccessibleDescription(f"Audio level: {band}")
        self.update()

    def tick_decay(self) -> None:
        """Apply one decay step.  Called by the global tick driver."""
        self._decay()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _decay(self) -> None:
        changed = False
        if self._level > 0.0:
            self._level = max(0.0, self._level - self.DECAY_PER_TICK)
            changed = True
        if self._peak > self._level:
            self._peak = max(self._level, self._peak - self.DECAY_PER_TICK * 0.6)
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()

        # Background track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(Color.BG_INPUT))
        painter.drawRoundedRect(rect, 2, 2)

        # Filled portion
        if self._level > 0.0:
            filled_width = int(rect.width() * self._level)
            gradient = QLinearGradient(0, 0, rect.width(), 0)
            gradient.setColorAt(0.0, QColor(Color.METER_GREEN))
            gradient.setColorAt(0.7, QColor(Color.METER_GREEN))
            gradient.setColorAt(0.85, QColor(Color.METER_YELLOW))
            gradient.setColorAt(1.0, QColor(Color.METER_RED))
            painter.setBrush(gradient)
            fill_rect = rect.adjusted(0, 0, filled_width - rect.width(), 0)
            painter.drawRoundedRect(fill_rect, 2, 2)

        # Peak marker
        if self._peak > 0.0:
            peak_x = int(rect.width() * self._peak) - 1
            painter.setPen(QColor(Color.TEXT_PRIMARY))
            painter.drawLine(peak_x, rect.top(), peak_x, rect.bottom())

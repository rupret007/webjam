"""LevelMeter — a horizontal audio level bar with green→yellow→red gradient."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QLinearGradient, QPainter, QColor, QPaintEvent
from PySide6.QtWidgets import QWidget

from webjam_qt.theme.tokens import Color


class LevelMeter(QWidget):
    """
    Paints a level bar from 0..1.

    - Green up to ~0.7
    - Yellow 0.7..0.9
    - Red above 0.9

    Uses a decay timer so peaks fall off naturally.
    """

    DECAY_PER_TICK = 0.03
    TICK_MS = 40

    def __init__(self, parent: QWidget | None = None, *, height: int = 4) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._peak = 0.0
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAccessibleName("Audio level meter")
        self.setAccessibleDescription("Shows real-time audio level from 0 to 100 percent")

        self._decay_timer = QTimer(self)
        self._decay_timer.timeout.connect(self._decay)
        self._decay_timer.start(self.TICK_MS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        self._level = level
        if level > self._peak:
            self._peak = level
        self.update()

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

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
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

"""Launch-scale artwork for WebJam's Host/Join moment.

The first screen uses the exact same native trinity knot as the header and
application icon.  A faint, static signal line sits behind it only to give the
otherwise quiet launch surface a musical cue.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from webjam_qt.theme.brand import draw_brand_mark
from webjam_qt.theme.tokens import Color


class JamSignalGraphic(QWidget):
    """Scalable, static launch artwork based on the WebJam trinity mark."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("JamSignalGraphic")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(104)
        self.setMaximumHeight(164)
        self.setAccessibleName("WebJam three-loop mark")
        self.setAccessibleDescription(
            "A decorative three-loop WebJam mark. It does not contain controls."
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self) -> QSize:
        return QSize(320, 144)

    def minimumSizeHint(self) -> QSize:
        return QSize(220, 104)

    def _draw_signal_line(self, painter: QPainter, bounds: QRectF) -> None:
        """Add a deliberately subdued, non-animated audio signal cue."""
        center = bounds.center()
        span = min(bounds.width() * 0.80, 328.0)
        step = max(8.0, span / 24.0)
        bars = (4, 6, 9, 14, 22, 32, 42, 31, 22, 15, 10, 7, 5)
        accent = QColor(Color.ACCENT_PRIMARY)
        accent.setAlpha(74)
        pen = QPen(accent, max(1.0, min(2.0, bounds.width() / 220.0)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for offset, height in enumerate(bars):
            for direction in (-1, 1):
                index = offset * direction
                x = center.x() + (index * step)
                if x < bounds.left() or x > bounds.right():
                    continue
                half_height = min(height / 2.0, bounds.height() * 0.31)
                painter.drawLine(
                    QPointF(x, center.y() - half_height),
                    QPointF(x, center.y() + half_height),
                )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(12.0, 6.0, -12.0, -6.0)
        if bounds.width() <= 0 or bounds.height() <= 0:
            painter.end()
            return

        self._draw_signal_line(painter, bounds)
        mark_size = min(bounds.height() * 0.98, bounds.width() * 0.54, 148.0)
        mark = QRectF(
            bounds.center().x() - mark_size / 2.0,
            bounds.center().y() - mark_size / 2.0,
            mark_size,
            mark_size,
        )
        draw_brand_mark(painter, mark)
        painter.end()

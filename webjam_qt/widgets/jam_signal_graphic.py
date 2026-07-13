"""A small, native-drawn mark for WebJam's Host/Join moment.

The graphic is intentionally static.  It suggests three musicians sharing one
signal without adding an animation that could distract people or conflict with
the operating system's Reduce Motion preference.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from webjam_qt.theme.tokens import Color


class JamSignalGraphic(QWidget):
    """Scalable, dependency-free artwork built from the product palette."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("JamSignalGraphic")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(104)
        self.setMaximumHeight(164)
        self.setAccessibleName("Three musicians connected by one audio signal")
        self.setAccessibleDescription(
            "A decorative WebJam illustration. It does not contain controls."
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 144)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, 104)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bounds = QRectF(self.rect()).adjusted(12.0, 8.0, -12.0, -8.0)
        if bounds.width() <= 0 or bounds.height() <= 0:
            return

        center = bounds.center()
        span = min(bounds.width() * 0.70, 300.0)
        left_x = center.x() - span / 2.0
        right_x = center.x() + span / 2.0
        wave_height = min(bounds.height() * 0.34, 42.0)

        # A quiet halo gives the mark depth while remaining the exact same
        # brand orange, only with transparency.
        halo = QColor(Color.ACCENT_PRIMARY)
        halo.setAlpha(34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        halo_size = min(bounds.height() * 0.84, 112.0)
        painter.drawEllipse(
            QRectF(
                center.x() - halo_size / 2.0,
                center.y() - halo_size / 2.0,
                halo_size,
                halo_size,
            )
        )

        # One continuous signal joins all three points.  The curve is original
        # to WebJam and is drawn by Qt, so it stays sharp at every scale.
        signal = QPainterPath(QPointF(left_x, center.y()))
        signal.cubicTo(
            QPointF(center.x() - span * 0.30, center.y()),
            QPointF(center.x() - span * 0.27, center.y() - wave_height),
            QPointF(center.x() - span * 0.14, center.y() - wave_height),
        )
        signal.cubicTo(
            QPointF(center.x() - span * 0.04, center.y() - wave_height),
            QPointF(center.x() - span * 0.07, center.y() + wave_height),
            QPointF(center.x(), center.y() + wave_height),
        )
        signal.cubicTo(
            QPointF(center.x() + span * 0.07, center.y() + wave_height),
            QPointF(center.x() + span * 0.04, center.y() - wave_height),
            QPointF(center.x() + span * 0.14, center.y() - wave_height),
        )
        signal.cubicTo(
            QPointF(center.x() + span * 0.27, center.y() - wave_height),
            QPointF(center.x() + span * 0.30, center.y()),
            QPointF(right_x, center.y()),
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(Color.ACCENT_PRIMARY),
                max(3.0, min(5.0, bounds.width() / 90.0)),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(signal)

        # Outer musicians are neutral; the shared center is the only filled
        # accent.  This keeps the composition readable in grayscale too.
        outer_radius = max(9.0, min(13.0, bounds.width() / 26.0))
        painter.setPen(QPen(QColor(Color.TEXT_PRIMARY), 2.0))
        painter.setBrush(QColor(Color.BG_CARD))
        painter.drawEllipse(QPointF(left_x, center.y()), outer_radius, outer_radius)
        painter.drawEllipse(QPointF(right_x, center.y()), outer_radius, outer_radius)

        center_radius = outer_radius + 7.0
        painter.setPen(QPen(QColor(Color.TEXT_PRIMARY), 2.0))
        painter.setBrush(QColor(Color.ACCENT_PRIMARY))
        painter.drawEllipse(center, center_radius, center_radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(Color.TEXT_PRIMARY))
        painter.drawEllipse(center, 4.0, 4.0)

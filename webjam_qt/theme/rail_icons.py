"""QPainter-drawn icons for the side rail.

Icons are drawn at runtime from theme tokens, so they recolor with the
palette and need no bundled image assets. Each icon carries two pixmaps:
the normal (secondary text) color and the checked/active accent color,
selected automatically by Qt through the button's checked state.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

from webjam_qt.theme.tokens import Color

_ICON_PX = 40  # drawn at 2x for crisp rendering at 20px logical size


def _canvas() -> QPixmap:
    pixmap = QPixmap(_ICON_PX, _ICON_PX)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def _painter(pixmap: QPixmap, color: str, width: float = 1.8) -> QPainter:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    return painter


def _draw_live(pixmap: QPixmap, color: str) -> None:
    """Broadcast dot inside a ring."""
    painter = _painter(pixmap, color)
    painter.drawEllipse(QRectF(3.5, 3.5, 13.0, 13.0))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(7.75, 7.75, 4.5, 4.5))
    painter.end()


def _draw_notes(pixmap: QPixmap, color: str) -> None:
    """Three note lines, the last one short."""
    painter = _painter(pixmap, color)
    painter.drawLine(QPointF(4.0, 6.0), QPointF(16.0, 6.0))
    painter.drawLine(QPointF(4.0, 10.0), QPointF(16.0, 10.0))
    painter.drawLine(QPointF(4.0, 14.0), QPointF(11.0, 14.0))
    painter.end()


def _draw_takes(pixmap: QPixmap, color: str) -> None:
    """Play triangle inside a circle."""
    painter = _painter(pixmap, color)
    painter.drawEllipse(QRectF(3.0, 3.0, 14.0, 14.0))
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(QPolygonF([
        QPointF(8.4, 6.9),
        QPointF(13.4, 10.0),
        QPointF(8.4, 13.1),
    ]))
    painter.end()


def _draw_settings(pixmap: QPixmap, color: str) -> None:
    """Gear: hub circle plus six radial ticks."""
    painter = _painter(pixmap, color)
    painter.drawEllipse(QRectF(6.0, 6.0, 8.0, 8.0))
    center = QPointF(10.0, 10.0)
    from math import cos, radians, sin
    for step in range(6):
        angle = radians(step * 60.0)
        inner = QPointF(center.x() + 5.4 * cos(angle), center.y() + 5.4 * sin(angle))
        outer = QPointF(center.x() + 8.0 * cos(angle), center.y() + 8.0 * sin(angle))
        painter.drawLine(inner, outer)
    painter.end()


_DRAWERS = {
    "stage": _draw_live,
    "canvas": _draw_notes,
    "takes": _draw_takes,
    "settings": _draw_settings,
}


def make_rail_icon(
    key: str,
    color: str = Color.TEXT_SECONDARY,
    active_color: str = Color.ACCENT_VIDEO,
) -> QIcon:
    """Two-state icon for a rail key; unknown keys get an empty icon."""
    drawer = _DRAWERS.get(key)
    icon = QIcon()
    if drawer is None:
        return icon
    normal = _canvas()
    drawer(normal, color)
    icon.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
    active = _canvas()
    drawer(active, active_color)
    icon.addPixmap(active, QIcon.Mode.Normal, QIcon.State.On)
    return icon

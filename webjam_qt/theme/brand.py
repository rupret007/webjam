"""Native QPainter rendering for WebJam's three-loop trinity mark.

The mark deliberately belongs to WebJam alone: three warm linked loops and
three nodes for musicians playing together.  It is drawn by Qt rather than
scaled from a bitmap, so the small session-header mark, the launch artwork,
and every application-icon resolution remain crisp on Retina displays.

``assets/webjam-mark.svg`` is a matching, portable vector companion for
documentation and packages.  The live application does not depend on it; a
damaged optional asset can never turn the in-app identity into a broken-image
placeholder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from webjam_qt.theme.tokens import Color


BRAND_NAME: Final = "WebJam"
BRAND_DESCRIPTION: Final = (
    "WebJam symbol: three linked loops for musicians playing together."
)
BRAND_MARK_PATH: Final = Path(__file__).resolve().parent / "assets" / "webjam-mark.svg"

# A deliberately restrained black-and-orange identity palette. The mark is a
# silhouette first; it should stay recognizable in a 16px header and never
# depend on an elaborate rendered effect.
_INK: Final = QColor("#0A0A0A")
_BURNT_ORANGE: Final = QColor(Color.ACCENT_PRIMARY)
_ORANGE: Final = QColor("#F06A00")
_HIGHLIGHT_ORANGE: Final = QColor("#F28A00")


def _trinity_paths() -> tuple[QPainterPath, QPainterPath, QPainterPath]:
    """Return the supplied trinity reference as three linked ribbon loops.

    The earlier mathematical trefoil was technically a three-loop knot but
    looked busy at header size.  These three rounded loops follow the supplied
    trinity reference literally: one loop for each musician, with the rings
    joining them at the outer points.  The geometry stays calm at 16 px.
    """
    top = QPainterPath(QPointF(0.50, 0.15))
    top.cubicTo(
        QPointF(0.31, 0.36),
        QPointF(0.31, 0.64),
        QPointF(0.50, 0.85),
    )
    top.cubicTo(
        QPointF(0.69, 0.64),
        QPointF(0.69, 0.36),
        QPointF(0.50, 0.15),
    )

    left = QPainterPath(QPointF(0.154, 0.75))
    left.cubicTo(
        QPointF(0.443, 0.803),
        QPointF(0.661, 0.677),
        QPointF(0.760, 0.400),
    )
    left.cubicTo(
        QPointF(0.471, 0.347),
        QPointF(0.253, 0.473),
        QPointF(0.154, 0.750),
    )

    right = QPainterPath(QPointF(0.846, 0.75))
    right.cubicTo(
        QPointF(0.747, 0.473),
        QPointF(0.529, 0.347),
        QPointF(0.240, 0.400),
    )
    right.cubicTo(
        QPointF(0.339, 0.677),
        QPointF(0.557, 0.803),
        QPointF(0.846, 0.750),
    )
    return top, left, right


_TOP_LOOP_PATH, _LEFT_LOOP_PATH, _RIGHT_LOOP_PATH = _trinity_paths()
_NODES: Final = (
    QPointF(0.50, 0.15),
    QPointF(0.846, 0.75),
    QPointF(0.154, 0.75),
)


def _resolved_color(color_name: str) -> QColor:
    color = QColor(color_name)
    return color if color.isValid() else QColor(_BURNT_ORANGE)


def _mark_color(color_name: str) -> QColor:
    """Return a simple, high-contrast ribbon color for this context."""

    base = _resolved_color(color_name)
    # Default app surfaces use the bright orange mark shown at launch while
    # explicit monochrome callers retain the requested color.
    return QColor(_ORANGE if base.name().upper() == _BURNT_ORANGE.name().upper() else base)


def _mark_brush(color_name: str) -> QBrush:
    """Use the warm reference ribbon without losing monochrome callers."""

    base = _resolved_color(color_name)
    if base.name().upper() != _BURNT_ORANGE.name().upper():
        return QBrush(_mark_color(color_name))
    gradient = QLinearGradient(QPointF(0.15, 0.12), QPointF(0.86, 0.88))
    gradient.setColorAt(0.0, _HIGHLIGHT_ORANGE)
    gradient.setColorAt(0.34, _ORANGE)
    gradient.setColorAt(0.72, QColor(Color.ACCENT_PRIMARY))
    gradient.setColorAt(1.0, _ORANGE)
    return QBrush(gradient)


def draw_brand_mark(
    painter: QPainter,
    bounds: QRectF,
    *,
    color: str = Color.ACCENT_PRIMARY,
) -> None:
    """Draw the trinity knot into ``bounds`` without raster scaling.

    The composition is square and is centered inside any supplied rectangle.
    Its one orange ribbon and three nodes are intentionally free of shadows,
    gradients, and animation so a musician sees the same mark everywhere.
    """
    side = min(bounds.width(), bounds.height())
    if side <= 0:
        return
    rect = QRectF(
        bounds.center().x() - side / 2.0,
        bounds.center().y() - side / 2.0,
        side,
        side,
    )

    painter.save()
    painter.translate(rect.left(), rect.top())
    painter.scale(side, side)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # One simple ribbon weight keeps the mark readable at both 16 px and
    # launch scale. The three deliberate loops mirror the supplied mark
    # without shadows, animation, or a fragile bitmap crop.
    stroke = max(0.076, 1.62 / side)

    mark_brush = _mark_brush(color)
    ribbon = QPen(mark_brush, stroke)
    ribbon.setWidthF(stroke)
    ribbon.setCapStyle(Qt.PenCapStyle.RoundCap)
    ribbon.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(ribbon)
    painter.drawPath(_TOP_LOOP_PATH)
    painter.drawPath(_LEFT_LOOP_PATH)
    painter.drawPath(_RIGHT_LOOP_PATH)

    # Three circular nodes remain visible even at 16 px.  The dark center
    # keeps them legible on both the app's black tile and a light OS surface.
    node_radius = max(0.066, 1.50 / side)
    inner_radius = node_radius * 0.53
    for node in _NODES:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mark_brush)
        painter.drawEllipse(node, node_radius, node_radius)
        painter.setBrush(_INK)
        painter.drawEllipse(node, inner_radius, inner_radius)

    painter.restore()


def render_brand_pixmap(
    logical_size: int,
    *,
    color: str = Color.ACCENT_PRIMARY,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """Render the vector knot at an exact logical size and device scale."""
    if logical_size <= 0 or device_pixel_ratio <= 0:
        return QPixmap()
    pixels = max(1, round(logical_size * device_pixel_ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_brand_mark(painter, QRectF(0, 0, pixels, pixels), color=color)
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def render_application_icon_pixmap(size: int) -> QPixmap:
    """Render the knot on a quiet black tile for OS chrome and launchers."""
    if size <= 0:
        return QPixmap()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = max(1.0, size * 0.035)
    radius = max(2.0, size * 0.22)
    tile = QRectF(inset, inset, size - (2 * inset), size - (2 * inset))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(Color.BG_PANEL))
    painter.drawRoundedRect(tile, radius, radius)
    mark_inset = max(1.0, size * 0.12)
    draw_brand_mark(painter, tile.adjusted(mark_inset, mark_inset, -mark_inset, -mark_inset))
    painter.end()
    return pixmap


def make_brand_icon() -> QIcon:
    """Return one multi-resolution icon shared by all application windows."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        pixmap = render_application_icon_pixmap(size)
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon


class BrandMark(QLabel):
    """Scalable, non-interactive native vector brand graphic."""

    def __init__(
        self,
        logical_size: int = 28,
        *,
        color: str = Color.ACCENT_PRIMARY,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._logical_size = max(16, int(logical_size))
        self._color = color
        self.setAccessibleName(BRAND_NAME)
        self.setAccessibleDescription(BRAND_DESCRIPTION)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setText("")
        self.setFixedSize(self._logical_size, self._logical_size)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._logical_size, self._logical_size)

    def has_vector_mark(self) -> bool:
        """The mark is native QPainter geometry, with no runtime asset risk."""
        return True

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        draw_brand_mark(painter, QRectF(self.contentsRect()), color=self._color)
        painter.end()


__all__ = [
    "BRAND_DESCRIPTION",
    "BRAND_MARK_PATH",
    "BRAND_NAME",
    "BrandMark",
    "draw_brand_mark",
    "make_brand_icon",
    "render_application_icon_pixmap",
    "render_brand_pixmap",
]

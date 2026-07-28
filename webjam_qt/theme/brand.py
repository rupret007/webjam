"""Native QPainter rendering for WebJam's continuous trefoil identity.

The mark deliberately belongs to WebJam alone: one unbroken warm ribbon forms
three linked loops, with three nodes for musicians playing together. It is
drawn by Qt rather than scaled from a bitmap, so the small session-header mark,
launch artwork, and every application-icon resolution remain crisp on Retina
displays.

``assets/webjam-mark.svg`` is a matching, portable vector companion for
documentation, packages, and companion-app assets. The live application does
not depend on it; a
damaged optional asset can never turn the in-app identity into a broken-image
placeholder.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Final, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from webjam_qt.theme.tokens import Color


BRAND_NAME: Final = "WebJam"
BRAND_DESCRIPTION: Final = (
    "WebJam symbol: one continuous three-loop trefoil for musicians playing together."
)
BRAND_MARK_PATH: Final = Path(__file__).resolve().parent / "assets" / "webjam-mark.svg"

# A deliberately restrained black-and-orange identity palette. The mark is a
# silhouette first; it should stay recognizable in a 16px header and never
# depend on an elaborate rendered effect.
_INK: Final = QColor("#0A0A0A")
_BURNT_ORANGE: Final = QColor(Color.ACCENT_PRIMARY)
_ORANGE: Final = QColor("#F06A00")
_TRINITY_EXTENT: Final = 0.355
_TRINITY_NATIVE_SAMPLES: Final = 240


def trinity_points(samples: int = _TRINITY_NATIVE_SAMPLES) -> tuple[QPointF, ...]:
    """Return the canonical continuous trefoil in a 0..1 coordinate space.

    The analytic curve is the single source for the live vector mark, SVG,
    desktop icon containers, and Pocket Stage artwork. ``samples`` only
    controls tessellation; it never changes the trefoil's geometry.
    """
    if samples < 3:
        raise ValueError("The WebJam trefoil requires at least three samples")
    raw_points: list[tuple[float, float]] = []
    for index in range(samples + 1):
        angle = (2.0 * math.pi * index) / samples
        raw_points.append(
            (
                math.sin(angle) + (2.0 * math.sin(2.0 * angle)),
                math.cos(angle) - (2.0 * math.cos(2.0 * angle)),
            )
        )
    max_x = max(abs(x) for x, _ in raw_points)
    max_y = max(abs(y) for _, y in raw_points)
    return tuple(
        QPointF(
            0.5 + ((x / max_x) * _TRINITY_EXTENT),
            0.5 + ((y / max_y) * _TRINITY_EXTENT),
        )
        for x, y in raw_points
    )


def trinity_svg_path_data(*, samples: int = 72, canvas_size: float = 64.0) -> str:
    """Return deterministic SVG path data from the canonical trefoil curve."""

    commands = []
    for index, point in enumerate(trinity_points(samples)):
        command = "M" if index == 0 else "L"
        commands.append(
            f"{command}{point.x() * canvas_size:.2f} {point.y() * canvas_size:.2f}"
        )
    return " ".join(commands) + " Z"


def _trinity_path() -> QPainterPath:
    path = QPainterPath()
    for index, point in enumerate(trinity_points()):
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


_TRINITY_PATH: Final = _trinity_path()
_NODES: Final = (
    QPointF(0.5, 0.145),
    QPointF(0.826, 0.693),
    QPointF(0.174, 0.693),
)


def _resolved_color(color_name: str) -> QColor:
    color = QColor(color_name)
    return color if color.isValid() else QColor(_BURNT_ORANGE)


def _mark_color(color_name: str) -> QColor:
    """Return a simple, high-contrast ribbon color for this context."""

    base = _resolved_color(color_name)
    # Default app surfaces use the bright orange mark shown at launch while
    # explicit monochrome callers retain the requested color.
    return QColor(
        _ORANGE if base.name().upper() == _BURNT_ORANGE.name().upper() else base
    )


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
    # launch scale. The loops naturally layer at their crossings without a
    # dark outline or decorative glow.
    stroke = max(0.076, 1.62 / side)

    mark_color = _mark_color(color)
    ribbon = QPen(mark_color)
    ribbon.setWidthF(stroke)
    ribbon.setCapStyle(Qt.PenCapStyle.RoundCap)
    ribbon.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(ribbon)
    painter.drawPath(_TRINITY_PATH)

    # Three circular nodes remain visible even at 16 px.  The dark center
    # keeps them legible on both the app's black tile and a light OS surface.
    node_radius = max(0.066, 1.50 / side)
    inner_radius = node_radius * 0.53
    for node in _NODES:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mark_color)
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
    draw_brand_mark(
        painter, tile.adjusted(mark_inset, mark_inset, -mark_inset, -mark_inset)
    )
    painter.end()
    return pixmap


def render_ios_application_icon_pixmap(size: int) -> QPixmap:
    """Render an opaque, unmasked Pocket Stage icon for Apple's asset catalog."""

    if size <= 0:
        return QPixmap()
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(Color.BG_PANEL))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    mark_inset = max(1.0, size * 0.16)
    draw_brand_mark(
        painter,
        QRectF(0, 0, size, size).adjusted(
            mark_inset,
            mark_inset,
            -mark_inset,
            -mark_inset,
        ),
    )
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
    "render_ios_application_icon_pixmap",
    "trinity_points",
    "trinity_svg_path_data",
]

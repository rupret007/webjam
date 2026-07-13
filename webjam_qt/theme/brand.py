"""Central rendering for WebJam's original three-path brand mark.

The SVG is the canonical source.  This module keeps every UI surface and the
runtime application icon on that same asset, while retaining a plain-text
fallback if a damaged package ever omits the SVG.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Optional

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from webjam_qt.theme.tokens import Color


BRAND_NAME: Final = "WebJam"
BRAND_DESCRIPTION: Final = (
    "WebJam symbol: three connected paths for conversation, live music, "
    "and production."
)
BRAND_MARK_PATH: Final = Path(__file__).resolve().parent / "assets" / "webjam-mark.svg"
_SOURCE_COLOR: Final = b"#BF5700"


@lru_cache(maxsize=8)
def _brand_svg_data(color_name: str = Color.ACCENT_PRIMARY) -> bytes:
    """Return the canonical SVG recolored for a one-color use."""
    try:
        source = BRAND_MARK_PATH.read_bytes()
    except OSError:
        return b""
    replacement = QColor(color_name).name().upper().encode("ascii")
    return source.replace(_SOURCE_COLOR, replacement)


def _renderer(color: str, svg_data: Optional[bytes] = None) -> QSvgRenderer:
    data = _brand_svg_data(color) if svg_data is None else bytes(svg_data)
    return QSvgRenderer(QByteArray(data))


def render_brand_pixmap(
    logical_size: int,
    *,
    color: str = Color.ACCENT_PRIMARY,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """Render the one-color mark without raster upscaling.

    ``logical_size`` is the UI size.  A caller can request a higher device
    pixel ratio for a Retina pixmap while keeping the same logical dimensions.
    An invalid/missing source returns a null pixmap so callers can fall back
    honestly instead of displaying a broken-image placeholder.
    """
    if logical_size <= 0 or device_pixel_ratio <= 0:
        return QPixmap()
    renderer = _renderer(color)
    if not renderer.isValid():
        return QPixmap()

    pixels = max(1, round(logical_size * device_pixel_ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def render_application_icon_pixmap(size: int) -> QPixmap:
    """Render the mark on a quiet black tile for OS chrome and launchers."""
    if size <= 0:
        return QPixmap()
    mark = render_brand_pixmap(max(1, round(size * 0.68)))
    if mark.isNull():
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
    mark_size = mark.width()
    offset = (size - mark_size) // 2
    painter.drawPixmap(offset, offset, mark)
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
    """Scalable, non-interactive brand graphic with an accessible fallback."""

    def __init__(
        self,
        logical_size: int = 28,
        *,
        color: str = Color.ACCENT_PRIMARY,
        parent: Optional[QWidget] = None,
        _svg_data: Optional[bytes] = None,
    ) -> None:
        super().__init__(parent)
        self._logical_size = max(16, int(logical_size))
        self._color = color
        self._renderer = _renderer(color, _svg_data)
        self.setAccessibleName(BRAND_NAME)
        self.setAccessibleDescription(BRAND_DESCRIPTION)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        if self._renderer.isValid():
            self.setText("")
            self.setFixedSize(self._logical_size, self._logical_size)
        else:
            # A word is more useful than a blank/broken glyph if packaging
            # ever loses the SVG. The accessible name remains the same in
            # either state, so screen readers never depend on the artwork.
            self.setText(BRAND_NAME)
            self.setStyleSheet(f"color: {QColor(color).name()}; background: transparent;")
            self.setFixedHeight(self._logical_size)
            self.setMinimumWidth(self._logical_size)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if self._renderer.isValid():
            return QSize(self._logical_size, self._logical_size)
        return super().sizeHint()

    def has_vector_mark(self) -> bool:
        """Whether the canonical asset loaded; useful for package diagnostics."""
        return self._renderer.isValid()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._renderer.isValid():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._renderer.render(painter, QRectF(self.contentsRect()))
        painter.end()


__all__ = [
    "BRAND_DESCRIPTION",
    "BRAND_MARK_PATH",
    "BRAND_NAME",
    "BrandMark",
    "make_brand_icon",
    "render_application_icon_pixmap",
    "render_brand_pixmap",
]

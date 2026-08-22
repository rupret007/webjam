"""Geometry for sitting WebJam and the Webex meeting side by side.

WebJam shipped with a flat ``resize(1440, 900)`` and no screen awareness at
all, so on any larger display it opened as a floating window with Webex
landing on top of it.  Getting the two usable together meant dragging them
by hand every session.

The split is computed here as pure geometry -- no widgets, no platform
calls -- so it can be proven headlessly and reused by the Webex-side
placement that follows.  Two rules the tests pin:

* **Gapless.**  The two rectangles tile the usable area exactly: no desktop
  strip between them, no overlap, and their widths sum to the full width.
* **Honest about small screens.**  When the display cannot hold both
  minimums, WebJam takes the whole area and the Webex rectangle comes back
  empty, so a caller places nothing rather than something unusable.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize

# WebJam owns the mixer, transport, and take list; the meeting only needs to
# show faces, so it gets the narrower share.
DEFAULT_WEBJAM_FRACTION = 0.62

# Below these the panes stop being usable rather than merely cramped.
MINIMUM_WEBJAM_WIDTH = 720
MINIMUM_WEBEX_WIDTH = 380


@dataclass(frozen=True, slots=True)
class SessionLayout:
    """Where each window goes on one display."""

    webjam: QRect
    webex: QRect

    @property
    def places_webex(self) -> bool:
        """False when the display is too small to seat the meeting."""

        return not self.webex.isEmpty()


def split_screen(
    available: QRect,
    *,
    webjam_fraction: float = DEFAULT_WEBJAM_FRACTION,
    minimum_webjam_width: int = MINIMUM_WEBJAM_WIDTH,
    minimum_webex_width: int = MINIMUM_WEBEX_WIDTH,
) -> SessionLayout:
    """Tile ``available`` into a WebJam pane and a Webex pane.

    ``available`` should be the screen's *available* geometry so the menu bar
    and Dock are already excluded.
    """

    if available.isEmpty():
        return SessionLayout(QRect(), QRect())

    width = available.width()
    height = available.height()
    top = available.y()
    left = available.x()

    # One pane whenever both cannot be usable at once.  Returning a cramped
    # pair here would be worse than telling the caller to place nothing.
    if width < minimum_webjam_width + minimum_webex_width:
        return SessionLayout(QRect(left, top, width, height), QRect())

    fraction = min(max(float(webjam_fraction), 0.0), 1.0)
    webjam_width = round(width * fraction)

    # Clamp so neither pane can be squeezed below usable by the fraction.
    webjam_width = max(webjam_width, minimum_webjam_width)
    webjam_width = min(webjam_width, width - minimum_webex_width)

    # The Webex pane takes the exact remainder, which is what keeps the two
    # gapless under rounding.
    webex_width = width - webjam_width

    return SessionLayout(
        QRect(left, top, webjam_width, height),
        QRect(left + webjam_width, top, webex_width, height),
    )


def centered_window_rect(available: QRect, requested: QSize) -> QRect:
    """Return a visible, centered top-level window rectangle.

    Native macOS sheets follow their parent, including when a stale saved
    window position leaves that parent outside every connected display. Help
    and About use this pure geometry helper to remain entirely inside the
    chosen screen's available area.
    """

    if available.isEmpty():
        return QRect()
    width = min(available.width(), max(1, int(requested.width())))
    height = min(available.height(), max(1, int(requested.height())))
    left = available.x() + (available.width() - width) // 2
    top = available.y() + (available.height() - height) // 2
    return QRect(left, top, width, height)

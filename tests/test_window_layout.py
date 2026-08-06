"""Geometry proofs for the WebJam/Webex side-by-side split.

Pure QRect maths -- no widgets and no platform plugin -- so these run
anywhere, including headless CI.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, QSize

from webjam_qt.controllers.window_layout import (
    DEFAULT_WEBJAM_FRACTION,
    MINIMUM_WEBEX_WIDTH,
    MINIMUM_WEBJAM_WIDTH,
    centered_window_rect,
    split_screen,
)


def _assert_tiles_exactly(layout, available: QRect) -> None:
    """The pair must cover the area with no gap and no overlap."""

    assert layout.webjam.y() == available.y()
    assert layout.webjam.height() == available.height()
    assert layout.webjam.x() == available.x()

    if not layout.places_webex:
        assert layout.webjam.width() == available.width()
        return

    assert layout.webex.y() == available.y()
    assert layout.webex.height() == available.height()
    # Gapless: Webex starts on the very next pixel column.
    assert layout.webex.x() == layout.webjam.right() + 1
    # Exact: the two widths account for every pixel of the usable width.
    assert layout.webjam.width() + layout.webex.width() == available.width()
    assert not layout.webjam.intersects(layout.webex)


def test_split_tiles_a_1440p_display_without_a_gap() -> None:
    available = QRect(0, 25, 2560, 1415)

    layout = split_screen(available)

    _assert_tiles_exactly(layout, available)
    assert layout.places_webex is True
    assert layout.webjam.width() == round(2560 * DEFAULT_WEBJAM_FRACTION)


def test_split_honours_the_offset_of_a_secondary_display() -> None:
    """A display to the right of the primary keeps its own origin."""

    available = QRect(2560, 0, 1920, 1080)

    layout = split_screen(available)

    _assert_tiles_exactly(layout, available)
    assert layout.webjam.x() == 2560


def test_narrow_display_never_starves_the_meeting_pane() -> None:
    """The fraction must not push Webex below usable width."""

    # 62% of 1200 would leave Webex only 456px here; the clamp protects it.
    available = QRect(0, 0, 1200, 800)

    layout = split_screen(available, webjam_fraction=0.95)

    _assert_tiles_exactly(layout, available)
    assert layout.webex.width() >= MINIMUM_WEBEX_WIDTH
    assert layout.webjam.width() >= MINIMUM_WEBJAM_WIDTH


def test_tiny_display_gives_webjam_everything_and_places_no_meeting() -> None:
    """Better one usable window than two unusable ones."""

    available = QRect(0, 0, MINIMUM_WEBJAM_WIDTH + MINIMUM_WEBEX_WIDTH - 1, 700)

    layout = split_screen(available)

    assert layout.places_webex is False
    assert layout.webex.isEmpty()
    assert layout.webjam.width() == available.width()
    _assert_tiles_exactly(layout, available)


def test_empty_available_area_places_nothing() -> None:
    layout = split_screen(QRect())

    assert layout.webjam.isEmpty()
    assert layout.places_webex is False


def test_centered_window_rect_stays_inside_offset_available_geometry() -> None:
    available = QRect(1920, 24, 1440, 876)

    placed = centered_window_rect(available, QSize(620, 540))

    assert placed == QRect(2330, 192, 620, 540)
    assert available.contains(placed)


def test_centered_window_rect_bounds_an_oversized_dialog() -> None:
    available = QRect(-1280, 0, 1280, 720)

    placed = centered_window_rect(available, QSize(2000, 900))

    assert placed == available


@pytest.mark.parametrize(
    "width",
    [
        MINIMUM_WEBJAM_WIDTH + MINIMUM_WEBEX_WIDTH,
        1101,
        1280,
        1440,
        1512,
        1728,
        1920,
        2056,
        2560,
        3008,
        3440,
        5120,
        6016,
    ],
)
def test_split_is_gapless_at_every_real_display_width(width: int) -> None:
    """Rounding must never leak a desktop strip between the two windows."""

    available = QRect(0, 25, width, 1000)

    layout = split_screen(available)

    _assert_tiles_exactly(layout, available)


@pytest.mark.parametrize("fraction", [0.0, 0.25, 0.5, 0.62, 0.75, 1.0, -3.0, 4.0])
def test_out_of_range_fractions_still_tile_exactly(fraction: float) -> None:
    """Clamping keeps both panes usable whatever fraction is asked for."""

    available = QRect(0, 0, 2560, 1400)

    layout = split_screen(available, webjam_fraction=fraction)

    _assert_tiles_exactly(layout, available)
    assert layout.webjam.width() >= MINIMUM_WEBJAM_WIDTH
    assert layout.webex.width() >= MINIMUM_WEBEX_WIDTH

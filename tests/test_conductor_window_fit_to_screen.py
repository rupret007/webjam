"""The window must size itself from the display, not a hardcoded 1440x900.

v0.22.2 opened at a flat 1440x900 on every machine, so on a larger display
WebJam floated with the desktop showing around it and Webex landing on top.
These prove the window now takes the share the layout computes for it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.window_layout import split_screen
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app):
    win = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Fit to screen",
    )
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()


def test_fit_to_screen_claims_the_layout_share_of_the_display(window) -> None:
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        pytest.skip("no screen available on this host")
    expected = split_screen(screen.availableGeometry())

    applied = window.fit_to_screen()

    assert applied is not None
    assert applied.webjam == expected.webjam
    assert applied.webex == expected.webex


def test_fit_to_screen_keeps_the_window_inside_the_usable_area(window) -> None:
    """The title bar must not push the frame off the bottom of the screen."""

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        pytest.skip("no screen available on this host")
    available = screen.availableGeometry()

    applied = window.fit_to_screen()
    if applied is None:
        pytest.skip("display too small to tile")

    frame = window.frameGeometry()
    assert frame.width() <= available.width()
    assert frame.height() <= available.height()


def test_fit_to_screen_never_shrinks_below_the_usable_minimum(window) -> None:
    applied = window.fit_to_screen()
    if applied is None:
        pytest.skip("display too small to tile")

    assert window.width() >= window.minimumWidth()
    assert window.height() >= window.minimumHeight()


def test_fit_to_screen_is_idempotent(window) -> None:
    """Pressing the shortcut twice must not creep the window."""

    if window.fit_to_screen() is None:
        pytest.skip("display too small to tile")
    first = window.geometry()

    window.fit_to_screen()

    assert window.geometry() == first


def test_fit_shortcut_is_bound(window) -> None:
    assert window._fit_shortcut.key().toString() in {"Ctrl+Shift+F", "Meta+Shift+F"}


@pytest.mark.parametrize("font_size", [14, 20])
@pytest.mark.parametrize("notes_visible", [False, True])
def test_browser_invitation_controls_fit_the_compact_music_window(window, qt_app, font_size, notes_visible):
    from webjam_qt.theme import load_stylesheet

    window.setStyleSheet(load_stylesheet() + f"\nQWidget {{ font-size: {font_size}px; }}")
    panel = window.web_guest_panel
    panel.render("ready", "Ready for one browser guest. Copy the link and share it privately. Headphones required.")
    window.resize(800, 600)
    window.session_canvas.setVisible(notes_visible)
    if notes_visible:
        window.center_splitter.setSizes([520, 280])
    window.show()
    for _ in range(4):
        qt_app.processEvents()
    assert (window.width(), window.height()) == (800, 600)
    for widget in (panel.primary, panel.stop_button, panel.status):
        assert widget.isVisibleTo(window)
        assert window.rect().contains(QRect(widget.mapTo(window, QPoint()), widget.size()))
        assert widget.visibleRegion().contains(widget.rect())
    assert panel.primary.width() >= panel.primary.minimumSizeHint().width()
    assert panel.stop_button.width() >= panel.stop_button.minimumSizeHint().width()
    assert panel.status.height() >= panel.status.heightForWidth(panel.status.width())


def test_browser_invitation_stays_out_of_art_and_offline_studio(window):
    from core.creative_modes import get_creator_profile_by_key_or_default

    window.set_creator_profile(get_creator_profile_by_key_or_default("art"))
    assert window.web_guest_panel.isHidden()
    window.set_creator_profile(get_creator_profile_by_key_or_default("music"))
    assert not window.web_guest_panel.isHidden()
    window.show_reference_studio_only()
    window.set_creator_profile(get_creator_profile_by_key_or_default("music"))
    assert window.web_guest_panel.isHidden()

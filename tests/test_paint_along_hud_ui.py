"""Paint along owns Art's guidance while the embedded surface is open."""

import pytest

from PySide6.QtWidgets import QApplication

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.reference_video import ReferenceVideoDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    window = ConductorWindow(
        mode_entries=[(profile.key, profile.label) for profile in CREATOR_PROFILES],
        initial_mode_key="art",
        initial_title="Art",
    )
    window.set_creator_profile(get_creator_profile_by_key("art"))
    yield window
    window.hide()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("hosting", [True, False], ids=["host", "guest"])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_art_paint_along_hides_hud_and_restores_current_guidance(
    window, qapp, hosting, size,
):
    panel = ReferenceVideoDialog(hosting=hosting, parent=window)
    panel.return_requested.connect(lambda: window.hide_paint_along(panel))
    window.resize(*size)
    window.show()
    qapp.processEvents()
    assert window.session_hud.isVisible()

    window.show_paint_along(panel)
    qapp.processEvents()
    assert panel.isVisible()
    assert not window.session_hud.isVisible()
    assert "Preview" in window.windowTitle()
    assert window.session_controls.isVisible()

    # Normal status refreshes must not bring the HUD back over the video.
    window.session_hud.set_state(
        "Waiting for the room", "Return to the room to reconnect.",
        action_text="Try Again", action_kind="retry", action_visible=True,
    )
    assert not window.session_hud.isVisible()
    assert not window.session_hud._action.isVisible()
    panel._back_button.click()
    qapp.processEvents()
    assert window.workspace_stack.currentWidget() is window.center_splitter
    assert window.session_hud.isVisible()
    assert window.session_hud._status.text() == "Waiting for the room"
    assert window.session_hud._action.isVisible()

    # Room/Notes navigation may switch the stack without closing the panel.
    window.show_paint_along(panel)
    assert not window.session_hud.isVisible()
    window.workspace_stack.setCurrentWidget(window.center_splitter)
    assert window.session_hud.isVisible()
    window.show_paint_along(panel)
    assert not window.session_hud.isVisible()
    window._handle_escape()
    assert window.session_hud.isVisible()

    window.show_paint_along(panel)
    replacement = ReferenceVideoDialog(hosting=hosting, parent=window)
    window.show_paint_along(replacement)
    assert replacement.isVisible()
    assert not window.session_hud.isVisible()
    window.release_paint_along(replacement)
    assert window.session_hud.isVisible()
    panel.deleteLater()
    replacement.deleteLater()


def test_music_hud_keeps_its_status_and_action_across_profile_changes(window, qapp):
    window.set_creator_profile(get_creator_profile_by_key("music"))
    window.session_hud.set_state(
        "Starting your jam…", "WebJam is getting the music ready.",
        action_text="Try Again", action_kind="retry", action_visible=True,
    )
    actions = []
    window.session_hud.action_requested.connect(actions.append)
    window.show()
    qapp.processEvents()
    panel = ReferenceVideoDialog(hosting=True, parent=window)
    window.show_paint_along(panel)
    assert window.session_hud.isVisible()
    window.set_creator_profile(get_creator_profile_by_key("art"))
    assert not window.session_hud.isVisible()
    window.set_creator_profile(get_creator_profile_by_key("music"))
    assert window.session_hud.isVisible()
    assert window.session_hud._status.text() == "Starting your jam…"
    assert window.session_hud._detail.text() == "WebJam is getting the music ready."
    window.session_hud._action.click()
    assert actions == ["retry"]
    window.release_paint_along(panel)
    assert window.session_hud.isVisible()
    panel.deleteLater()


def test_leaving_paint_along_for_offline_studio_keeps_hud_hidden(window, qapp):
    panel = ReferenceVideoDialog(hosting=True, parent=window)
    window.show_paint_along(panel)
    window.show()
    qapp.processEvents()
    assert not window.session_hud.isVisible()
    window.show_reference_studio_only()
    window.set_creator_profile(get_creator_profile_by_key("music"))
    window.release_paint_along(panel)
    assert window.workspace_stack.currentWidget() is window.reference_studio
    assert not window.session_hud.isVisible()
    panel.deleteLater()

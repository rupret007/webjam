"""The idle stage welcomes any musicians without renaming their template."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from core.creative_modes import CREATIVE_MODES, get_creator_profile_by_key
from webjam_qt.session_state import SessionUiState
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.participant_grid import ParticipantGrid
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
@pytest.mark.parametrize("hosting,message", [
    (False, "Start the session to play together."),
    (True, "Start the session and invite others."),
])
def test_music_idle_stage_is_inclusive_and_keeps_template(qapp, size, hosting, message):
    profile = get_creator_profile_by_key("music")
    window = ConductorWindow(
        mode_entries=[(mode.key, mode.label) for mode in CREATIVE_MODES],
        initial_mode_key="music_jam",
        initial_title=profile.default_template,
    )
    try:
        window.setStyleSheet(load_stylesheet())
        window.set_creator_profile(profile)
        window.resize(*size)
        window.show()
        grid = window.participant_grid
        grid.set_session_state(SessionUiState.idle(hosting=hosting))
        qapp.processEvents()

        assert window.session_strip.current_title() == "Band Rehearsal"
        assert grid._empty_message.text() == message
        assert grid._empty_message.isVisibleTo(window)
        assert grid._empty_message.visibleRegion().contains(grid._empty_message.rect())
        assert grid._empty_primary.text() == "Start Session"
        assert grid._empty_primary.accessibleDescription() == message
        assert grid._empty_primary.toolTip() == message
        assert grid._empty_primary.isEnabled()
        assert grid._empty_primary.isVisibleTo(window)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("profile,guest_message,host_message", [
    ("art", "Start the session to join the room.",
     "Start the room and invite other artists."),
    ("podcast_voice", "Start the session to join the recording.",
     "Start the recording session and invite speakers."),
    ("review_rehearsal", "Start the session to join the review.",
     "Start the review session and invite participants."),
])
@pytest.mark.parametrize("hosting", [False, True])
def test_other_profiles_keep_idle_vocabulary(qapp, profile, guest_message, host_message, hosting):
    grid = ParticipantGrid()
    try:
        grid.set_creator_profile(get_creator_profile_by_key(profile))
        grid.set_session_state(SessionUiState.idle(hosting=hosting))
        assert grid._empty_message.text() == (host_message if hosting else guest_message)
        # Switching profiles reprojects the same idle state without stale copy.
        grid.set_creator_profile(get_creator_profile_by_key("music"))
        assert grid._empty_message.text() == (
            "Start the session and invite others." if hosting
            else "Start the session to play together."
        )
    finally:
        grid.close()
        grid.deleteLater()

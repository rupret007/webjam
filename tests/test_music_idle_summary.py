"""Music idle has one visible summary beside its one startup action."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QPushButton

from core.creative_modes import get_creator_profile_by_key_or_default
from core.session_conductor import SessionConductorFacts, SessionRole
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.session_state import SessionUiState
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def room(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Rehearsal",
    )
    window.setStyleSheet(load_stylesheet())
    controller = ApplicationController(
        window, settings=AppSettings(config_file=str(tmp_path / "settings.json"))
    )
    try:
        yield controller, window
    finally:
        controller.shutdown()
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
@pytest.mark.parametrize("role", [SessionRole.HOST, SessionRole.GUEST])
def test_music_idle_shows_one_summary_and_one_start_action(room, qapp, monkeypatch, size, role):
    controller, window = room
    facts = SessionConductorFacts(role=role)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._render_session_conductor()
    window.resize(*size)
    window.show()
    qapp.processEvents()

    hud = window.session_hud
    grid = window.participant_grid
    for _ in range(2):
        # Count only the live HUD and stage, not Notes/guidance copies that a
        # splitter may still report as visible under Linux offscreen Qt.
        assert hud._status.isVisibleTo(window)
        assert hud._status.text() == "Ready when you are"
        assert hud._detail.isVisibleTo(window)
        assert hud._detail.text() == "Choose Host or Join to begin a rehearsal."
        assert grid._empty_title.isHidden()
        assert grid._empty_message.isHidden()
        assert grid._empty_eyebrow.isVisibleTo(window)
        assert grid._empty_eyebrow.text() == "NOT CONNECTED"
        assert grid._empty_hint.text() in (
            "Recording is optional. Set it up when you choose Record Session.",
            "Your host can choose Record Session when everyone is ready.",
        )
        starts = [button for button in window.findChildren(QPushButton)
                  if button.isVisibleTo(window) and button.text() == "Start Session"]
        assert starts == [hud._action]
        assert hud._action.isEnabled()
        assert hud._action.accessibleName() == "Start Session"
        assert "Ready when you are" in hud.accessibleDescription()
        action_rect = QRect(hud._action.mapTo(window, QPoint()), hud._action.size())
        assert window.rect().contains(action_rect)

        # A recovery render must restore both lines, and idle must hide them again.
        grid.set_session_state(SessionUiState.connection_failed())
        qapp.processEvents()
        assert grid._empty_title.isVisible()
        assert grid._empty_message.isVisible()
        assert grid._empty_title.text() == "Couldn’t reach the jam"
        assert "try again" in grid._empty_message.text()
        controller._render_session_conductor()
        qapp.processEvents()


def test_music_setup_guidance_is_not_hidden_as_idle(room, qapp, monkeypatch):
    controller, window = room
    facts = SessionConductorFacts(role=SessionRole.HOST)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._render_session_conductor()
    window.show()
    qapp.processEvents()
    assert window.participant_grid._empty_title.isHidden()

    facts = replace(facts, setup_requested=True)
    controller._render_session_conductor()
    qapp.processEvents()
    grid = window.participant_grid
    assert grid._empty_title.isVisible()
    assert grid._empty_message.isVisible()
    assert grid._empty_title.text() == window.session_hud._status.text()
    assert grid._empty_message.text() == window.session_hud._detail.text()


@pytest.mark.parametrize("profile_key", ["art", "podcast_voice", "review_rehearsal"])
def test_other_profile_idle_summaries_stay_visible(room, qapp, monkeypatch, profile_key):
    controller, window = room
    profile = get_creator_profile_by_key_or_default(profile_key)
    monkeypatch.setattr(type(controller), "creator_profile", property(lambda self: profile))
    window.participant_grid.set_creator_profile(profile)
    facts = SessionConductorFacts(creator_profile_key=profile_key)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._render_session_conductor()
    # Art may hide the whole mixer; its summary must remain available inside it.
    assert not window.participant_grid._empty_title.isHidden()
    assert not window.participant_grid._empty_message.isHidden()

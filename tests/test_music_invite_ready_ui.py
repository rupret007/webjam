"""Music's invite-ready screen has one headline and one next action."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.creative_modes import get_creator_profile_by_key_or_default
from core.session_conductor import (
    EvidenceState,
    ProcessState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionRole,
    derive_session_conductor,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _invite_screen(size, larger_text=False):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    window.set_creator_profile(get_creator_profile_by_key_or_default("music"))
    window.session_strip.set_recording_available(False)
    window.session_strip.set_audio_state("End Session")
    window.setStyleSheet(
        load_stylesheet() + ("\nQWidget { font-size: 20px; }" if larger_text else "")
    )
    window.resize(*size)
    # Exercise the production renderer and widgets without starting services.
    controller = ApplicationController.__new__(ApplicationController)
    controller.window = window
    controller.audio = SimpleNamespace(connected=False)
    facts = SessionConductorFacts(
        role=SessionRole.HOST,
        setup_requested=True,
        identity=EvidenceState.NOT_REQUIRED,
        sound=EvidenceState.NOT_REQUIRED,
        band_check=EvidenceState.NOT_REQUIRED,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
    )
    controller._session_conductor_facts = Mock(return_value=facts)
    controller._observe_session_conductor_facts = lambda facts, **_: SimpleNamespace(
        presentation=derive_session_conductor(facts)
    )
    for name in (
        "_record_pilot_conductor_presentation", "_publish_musician_guidance",
        "_sync_art_room_overview", "_focus_initial_hud_action",
        "_guest_recording_setup_context", "_studio_save_retry_context",
    ):
        setattr(controller, name, Mock(return_value=None))
    window.show()
    controller._render_session_conductor()
    return controller, window


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
@pytest.mark.parametrize("larger_text", [False, True])
def test_invite_ready_has_one_headline_and_an_explanatory_stage(qapp, size, larger_text):
    controller, window = _invite_screen(size, larger_text)
    try:
        qapp.processEvents()
        hud = window.session_hud
        grid = window.participant_grid
        assert controller._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
        assert hud._status.text() == "Copy the invite"
        assert hud._action.text() == "Copy Invite"
        assert hud._action.isVisibleTo(window) and hud._action.isEnabled()
        assert grid._empty_eyebrow.text() == "NOT CONNECTED"
        assert grid._empty_title.text() == "Waiting for your band."
        assert grid._empty_message.text() == ""
        assert grid._empty_message.isHidden()
        assert grid._empty_state.accessibleDescription() == "Waiting for your band."
        assert [
            label for label in window.findChildren(QLabel)
            if label.isVisibleTo(window) and label.text() == "Copy the invite"
        ] == [hud._status]
        assert not any(
            button.isVisibleTo(window)
            for button in grid._empty_state.findChildren(QPushButton)
        )
        for label in (grid._empty_eyebrow, grid._empty_title):
            assert label.isVisibleTo(window)
            assert grid._empty_state.rect().contains(
                QRect(label.mapTo(grid._empty_state, QPoint()), label.size())
            )
            if label.wordWrap():
                assert label.height() >= label.heightForWidth(label.width())
        assert grid.viewport().rect().contains(grid._empty_state.geometry())
        assert window.rect().contains(QRect(hud._action.mapTo(window, QPoint()), hud._action.size()))

        # Losing invite evidence must restore the preparing state on both surfaces.
        controller._session_conductor_facts.return_value = replace(
            controller._session_conductor_facts.return_value,
            host_listener=EvidenceState.UNKNOWN,
            invite=EvidenceState.UNKNOWN,
        )
        controller._render_session_conductor()
        assert hud._status.text() == "Preparing the invite"
        assert grid._empty_title.text() == "Preparing the invite"
        assert grid._empty_message.text() == controller._last_session_conductor.message
        assert not grid._empty_message.isHidden()
        assert hud._action.isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()

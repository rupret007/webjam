"""W12: Music HUD, participant empty state, and Session chip share conductor phase."""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.session_conductor import (
    CleanupState,
    EvidenceState,
    MusicPathState,
    ProcessState,
    ReviewState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionRole,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.session_state import SessionPhase
from webjam_qt.windows.conductor_window import ConductorWindow

APP = QApplication.instance() or QApplication([])


def _live_host_facts() -> SessionConductorFacts:
    return SessionConductorFacts(
        role=SessionRole.HOST,
        setup_requested=True,
        identity=EvidenceState.VERIFIED,
        sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=EvidenceState.VERIFIED,
        participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
    )


def test_conductor_stage_phase_maps_live_and_connected_to_connected_stage() -> None:
    assert (
        ApplicationController._conductor_stage_phase(SessionConductorPhase.LIVE)
        is SessionPhase.CONNECTED
    )
    assert (
        ApplicationController._conductor_stage_phase(SessionConductorPhase.CONNECTED)
        is SessionPhase.CONNECTED
    )
    assert (
        ApplicationController._conductor_stage_phase(SessionConductorPhase.IDLE)
        is SessionPhase.NOT_CONNECTED
    )
    offline_review = SessionConductorFacts(
        role=SessionRole.HOST,
        setup_requested=False,
        studio=ReviewState.REVIEWING,
    )
    assert (
        ApplicationController._conductor_stage_phase(
            SessionConductorPhase.REVIEWING,
            offline_review,
        )
        is SessionPhase.NOT_CONNECTED
    )
    assert (
        ApplicationController._conductor_stage_phase(
            SessionConductorPhase.REVIEWING,
            _live_host_facts(),
        )
        is SessionPhase.CONNECTED
    )


def test_render_session_conductor_aligns_grid_eyebrow_and_session_chip(tmp_path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="W12 grid phase",
    )
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(window, settings=None)
    try:
        controller._jamulus_connected = False
        controller.audio.connected = False
        controller._session_conductor_facts = mock.Mock(return_value=_live_host_facts())
        controller._render_session_conductor()

        assert window.session_hud._status.text() == "Band connected"
        assert window.participant_grid._empty_title.text() == "Band connected"
        assert window.participant_grid._empty_eyebrow.text() == "CONNECTED"
        assert "NOT CONNECTED" not in window.participant_grid._empty_eyebrow.text()
        assert window._status_latency.text() == "Session: Band connected"
    finally:
        controller.shutdown()
        window.close()


def _reconnecting_after_live_facts() -> SessionConductorFacts:
    facts = _live_host_facts()
    return SessionConductorFacts(
        role=facts.role,
        setup_requested=facts.setup_requested,
        identity=facts.identity,
        sound=facts.sound,
        band_check=facts.band_check,
        host_server_process=facts.host_server_process,
        host_server_rpc=facts.host_server_rpc,
        host_listener=facts.host_listener,
        invite=facts.invite,
        music_path=MusicPathState.RECONNECTING,
        local_participant=EvidenceState.UNKNOWN,
        remote_participant=EvidenceState.UNKNOWN,
        participant_identity=EvidenceState.UNKNOWN,
        had_authenticated_connection=True,
    )


def test_session_chip_tracks_reconnecting_and_ended_after_live(tmp_path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="W12 chip transitions",
    )
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(window, settings=None)
    facts_mock = mock.Mock(side_effect=[_live_host_facts(), _reconnecting_after_live_facts()])
    try:
        controller._jamulus_connected = False
        controller.audio.connected = False
        controller._session_conductor_facts = facts_mock
        controller._render_session_conductor()
        assert window._status_latency.text() == "Session: Band connected"

        controller._render_session_conductor()
        assert window.session_hud._status.text() == "Reconnecting"
        assert "NEEDS ATTENTION" in window.participant_grid._empty_eyebrow.text()
        assert window._status_latency.text() == "Session: Reconnecting"
        assert "Band connected" not in window._status_latency.text()

        ended_facts = SessionConductorFacts(
            role=SessionRole.HOST,
            setup_requested=False,
            cleanup=CleanupState.COMPLETE,
        )
        facts_mock.side_effect = [ended_facts]
        controller._render_session_conductor()
        assert window.session_hud._status.text() == "Safe to end session"
        assert window._status_latency.text() == "Session: Safe to end session"
    finally:
        controller.shutdown()
        window.close()


def test_offline_studio_review_does_not_claim_connected(tmp_path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="W12 offline review",
    )
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(window, settings=None)
    try:
        controller._jamulus_connected = False
        controller.audio.connected = False
        controller._conductor_studio_reviewing = True
        controller._conductor_setup_requested = False
        controller._render_session_conductor()

        assert window.session_hud._status.text() == "No takes yet"
        eyebrow = window.participant_grid._empty_eyebrow.text()
        assert "NOT CONNECTED" in eyebrow
        assert eyebrow != "CONNECTED"
        assert "Band connected" not in window._status_latency.text()
        assert window._status_latency.text() == "Session: No takes yet"
    finally:
        controller.shutdown()
        window.close()

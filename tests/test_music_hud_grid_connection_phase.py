"""W12: Music HUD, participant empty state, and Session chip share conductor phase."""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.session_conductor import (
    EvidenceState,
    MusicPathState,
    ProcessState,
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

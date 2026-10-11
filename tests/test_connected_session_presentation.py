"""Conductor connection truth reaches the empty stage and footer together."""
from dataclasses import replace
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from core.session_conductor import (
    EvidenceState, MusicPathState, SessionConductorFacts, SessionConductorPhase,
    SessionRole,
)
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status", lambda: "granted",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam", initial_title="Connection truth",
    )
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(window, settings=settings)
    try:
        yield controller, window
    finally:
        controller.shutdown()
        window.close()


def connected_facts(role, remote):
    return SessionConductorFacts(
        role=role, setup_requested=True,
        identity=EvidenceState.VERIFIED, sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=remote,
        participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
    )


@pytest.mark.parametrize("role", [SessionRole.HOST, SessionRole.GUEST])
@pytest.mark.parametrize("remote, phase", [
    (EvidenceState.UNKNOWN, SessionConductorPhase.CONNECTED),
    (EvidenceState.VERIFIED, SessionConductorPhase.LIVE),
])
@pytest.mark.parametrize("surface", ["stage", "chips"])
def test_connected_hud_grid_and_chips_agree(room, monkeypatch, role, remote, phase, surface):
    controller, window = room
    facts = connected_facts(role, remote)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._update_session_hud()

    assert controller._last_session_conductor.phase is phase
    assert not window.participant_grid.cards()
    if phase is SessionConductorPhase.LIVE:
        assert window.session_hud._status.text() == "Band connected"
    if surface == "stage":
        grid = window.participant_grid
        assert grid._empty_eyebrow.text() == "CONNECTED"
        assert grid._empty_title.text() == window.session_hud._status.text()
        assert grid._empty_primary.isHidden()
    else:
        assert window._status_audio.text() == "Audio: Connected"
        assert window._status_latency.text() == "Session: Connected"
    # Connection evidence must not turn into a claim of verified audibility.
    assert facts.human_two_way_audibility is not EvidenceState.VERIFIED
    assert "heard" in controller._last_session_conductor.evidence_limit or (
        "audibility" in controller._last_session_conductor.evidence_limit
    )


def test_connection_loss_and_return_clear_stale_connected_copy(room, monkeypatch):
    controller, window = room
    live = connected_facts(SessionRole.GUEST, EvidenceState.VERIFIED)
    facts = live
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._update_session_hud()
    facts = replace(live, music_path=MusicPathState.DISCONNECTED)
    controller._update_session_hud()
    assert controller._last_session_conductor.phase is SessionConductorPhase.RECONNECTING
    assert window.participant_grid._empty_eyebrow.text() != "CONNECTED"
    assert window._status_audio.text() == "Audio: Reconnecting…"
    assert window._status_latency.text() == "Session: Reconnecting"
    facts = live
    controller._update_session_hud()
    assert window.participant_grid._empty_eyebrow.text() == "CONNECTED"
    assert window._status_latency.text() == "Session: Connected"
    facts = SessionConductorFacts(role=SessionRole.GUEST)
    controller._update_session_hud()
    assert window.participant_grid._empty_eyebrow.text() == "NOT CONNECTED"
    assert window._status_latency.text() == "Session: Not connected"
    assert window._status_audio.text() != "Audio: Connected"


@pytest.mark.parametrize("path, expected_phase, chip", [
    (MusicPathState.AUTHENTICATING, SessionConductorPhase.JOINING, "Connecting…"),
    (MusicPathState.FAILED, SessionConductorPhase.FAILED, "Needs attention"),
])
def test_lost_connection_evidence_replaces_connected_chips(
    room, monkeypatch, path, expected_phase, chip,
):
    controller, window = room
    facts = connected_facts(SessionRole.GUEST, EvidenceState.VERIFIED)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._update_session_hud()
    facts = replace(facts, music_path=path, local_participant=EvidenceState.UNKNOWN)
    controller._update_session_hud()
    assert controller._last_session_conductor.phase is expected_phase
    assert window.participant_grid._empty_eyebrow.text() != "CONNECTED"
    assert window._status_audio.text() == f"Audio: {chip}"
    assert window._status_latency.text() == f"Session: {chip}"

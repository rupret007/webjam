"""Music's empty stage and footer follow the same accepted facts as its HUD."""
from dataclasses import replace
from unittest import mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.session_conductor import (
    EvidenceState,
    MusicPathState,
    ProcessState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionRole,
)
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        lambda: "authorized",
    )
    qapp = QApplication.instance() or QApplication([])
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Connected empty stage",
    )
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "takes"),
        last_creator_profile_key="music",
    )
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        app = ApplicationController(window, settings=settings)
    try:
        yield app
    finally:
        app.shutdown()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def _facts(role, remote):
    return SessionConductorFacts(
        creator_profile_key="music", role=role, setup_requested=True,
        identity=EvidenceState.VERIFIED, sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED, invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=remote, participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
        human_two_way_audibility=EvidenceState.UNKNOWN,
    )


@pytest.mark.parametrize("surface", ["stage", "footer"])
@pytest.mark.parametrize("role,remote,phase", [
    (SessionRole.HOST, EvidenceState.VERIFIED, SessionConductorPhase.LIVE),
    (SessionRole.GUEST, EvidenceState.VERIFIED, SessionConductorPhase.LIVE),
    (SessionRole.GUEST, EvidenceState.UNKNOWN, SessionConductorPhase.CONNECTED),
])
def test_connected_empty_surfaces_match_hud(controller, monkeypatch, role, remote, phase, surface):
    facts = _facts(role, remote)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    window = controller.window
    audio_before = window._status_audio.text()
    for _ in range(2):  # An unchanged guidance snapshot must still refresh the footer.
        window.set_status_latency("Not connected")
        controller._update_session_hud()
        assert controller._last_session_conductor.phase is phase
        assert not window.participant_grid.cards()
        assert window.participant_grid._empty_title.text() == window.session_hud._status.text()
        if surface == "stage":
            assert window.participant_grid._empty_eyebrow.text() == "CONNECTED"
        else:
            assert window._status_latency.text() == f"Session: {window.session_hud._status.text()}"
        assert window.participant_grid._empty_primary.isHidden()
        assert window._status_audio.text() == audio_before
        assert facts.human_two_way_audibility is EvidenceState.UNKNOWN


def test_empty_connection_loss_replaces_connected_copy(controller, monkeypatch):
    facts = _facts(SessionRole.GUEST, EvidenceState.VERIFIED)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._update_session_hud()
    facts = replace(facts, music_path=MusicPathState.DISCONNECTED)
    controller._update_session_hud()
    assert controller._last_session_conductor.phase is SessionConductorPhase.RECONNECTING
    window = controller.window
    assert window.participant_grid._empty_eyebrow.text() == "NEEDS ATTENTION"
    assert window._status_latency.text() == f"Session: {window.session_hud._status.text()}"
    assert "Band connected" not in window._status_latency.text()


def test_running_process_does_not_make_empty_stage_connected(controller, monkeypatch):
    facts = SessionConductorFacts(host_server_process=ProcessState.RUNNING)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    controller._update_session_hud()
    assert controller._last_session_conductor.phase is SessionConductorPhase.IDLE
    assert controller.window.participant_grid._empty_eyebrow.text() == "NOT CONNECTED"


def test_populated_roster_keeps_its_participant_count(controller, monkeypatch):
    from webjam_qt.widgets.participant_card import ParticipantPresentation

    facts = _facts(SessionRole.GUEST, EvidenceState.VERIFIED)
    monkeypatch.setattr(controller, "_session_conductor_facts", lambda: facts)
    window = controller.window
    window.participant_grid.set_participants([
        ParticipantPresentation(channel_id=1, name="Bandmate"),
    ])
    window.set_status_latency("1 participant · waiting for others")
    controller._update_session_hud()
    assert window._status_latency.text() == "Session: 1 participant · waiting for others"
    assert window.participant_grid._empty_state.isHidden()


def test_empty_footer_keeps_microphone_recovery_override(controller, monkeypatch):
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        lambda: "denied",
    )
    controller._update_session_hud()
    window = controller.window
    assert window.session_hud._status.text() == "Microphone access is off"
    assert window._status_latency.text() == "Session: Microphone access is off"
    assert window.participant_grid._empty_eyebrow.text() != "CONNECTED"

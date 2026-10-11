"""Room-switch wording through the public invitation entry and failure callbacks."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.creative_modes import get_creator_profile_by_key_or_default
from core.network_invite import BandInvite
from webjam_qt.controllers.application_controller import ApplicationController


@pytest.fixture
def switch_controller(monkeypatch):
    def create(profile, active, hosting):
        controller = SimpleNamespace(
            creator_profile=get_creator_profile_by_key_or_default(profile),
            _room_participant=SimpleNamespace(active=active),
            settings=SimpleNamespace(host_server_enabled=hosting),
            recording=SimpleNamespace(
                is_recording_active=False, take_in_progress=False,
                stop_server_recording_for_shutdown=Mock(return_value=True),
            ),
            audio=SimpleNamespace(stopping=False, cleanup_retry_required=False),
            bridge=SimpleNamespace(
                hosted_server_alive=Mock(return_value=False),
                hosted_server_owned=Mock(return_value=hosting),
                stop_jamulus=Mock(return_value=True),
            ),
            window=SimpleNamespace(
                session_strip=Mock(), session_hud=Mock(),
                participant_grid=Mock(), flash_message=Mock(),
            ),
            _invite_switch_generation=0,
            _ui_invoker=SimpleNamespace(invoke=lambda callback: callback()),
            _validated_session_meeting_url=lambda url: url,
            _shutdown_cleanup_blocks_action=lambda: False,
            _is_jamulus_running=lambda: not active,
            _room_is_busy_for_invitation=lambda: active,
            _sync_reference_track_primary_gate=Mock(),
            _prepare_pocket_stage_for_session_end=Mock(),
            _complete_pocket_stage_session_end=Mock(),
            _stop_reference_track_for_session_end=Mock(return_value=True),
            _stop_pocket_stage_for_session_end=Mock(return_value=True),
            _stop_session_peer=Mock(return_value=True),
            _clear_remote_invite_owner=Mock(return_value=True),
            _stop_remote_transport=Mock(return_value=True),
        )
        for name in ("_art_room_active", "_accept_band_invitation"):
            setattr(controller, name, MethodType(getattr(ApplicationController, name), controller))
        return controller

    # Retain the real worker/callback code, but schedule it deterministically.
    thread = Mock()
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("webjam_qt.controllers.application_controller.threading.Thread", thread)
    monkeypatch.setattr(QMessageBox, "question", question)
    return create, thread, question


@pytest.mark.parametrize("hosting", [False, True], ids=["guest", "host"])
@pytest.mark.parametrize(
    "profile,active,noun",
    [("art", False, "room"), ("music", True, "room"), ("music", False, "jam")],
    ids=["art-profile", "active-art-room", "music"],
)
def test_accept_invitation_switch_confirmation_uses_room_vocabulary(
    switch_controller, hosting, profile, active, noun,
):
    create, thread, question = switch_controller
    controller = create(profile, active, hosting)
    question.return_value = QMessageBox.StandardButton.No

    assert not ApplicationController.accept_invitation(controller, BandInvite("192.168.1.20"))

    assert question.call_args.args[1:3] == (
        f"Join this {noun}?",
        f"WebJam will safely end your current {noun}, then join the new one.",
    )
    assert question.call_args.args[4] == QMessageBox.StandardButton.No
    thread.assert_not_called()
    controller._stop_session_peer.assert_not_called()


@pytest.mark.parametrize("hosting", [False, True], ids=["guest", "host"])
@pytest.mark.parametrize("cleanup_unresolved", [False, True], ids=["apply-failed", "cleanup-failed"])
@pytest.mark.parametrize(
    "profile,active,noun,connection",
    [("art", False, "room", "room"), ("music", True, "room", "room"),
     ("music", False, "jam", "music")],
    ids=["art-profile", "active-art-room", "music"],
)
def test_show_switch_failure_keeps_original_room_vocabulary(
    switch_controller, monkeypatch, hosting, cleanup_unresolved, profile, active, noun, connection,
):
    create, thread, _question = switch_controller
    controller = create(profile, active, hosting)
    controller._stop_reference_track_for_session_end.return_value = not cleanup_unresolved
    # A failed settings read reaches the actual _show_switch_failure closure
    # after confirmed cleanup, without creating files or starting a connection.
    monkeypatch.setattr("core.settings.load_settings", Mock(side_effect=OSError("test read failure")))
    controller.settings.config_file = "unused-test-settings.json"

    assert ApplicationController.accept_invitation(controller, BandInvite("192.168.1.20"))
    joining_copy = controller.window.session_hud.set_state.call_args.args
    # Teardown may retire Art before its queued result reaches the UI.
    controller._room_participant.active = False
    controller.creator_profile = get_creator_profile_by_key_or_default("music")
    thread.call_args.kwargs["target"]()

    assert controller.window.session_hud.set_state.call_args.args == (
        f"WebJam couldn’t open the new {noun} safely",
        "Some previous session services still need to stop. Try "
        "ending or leaving again before opening the invitation."
        if cleanup_unresolved else
        f"The previous {connection} connection was stopped, but the "
        "new invitation could not be applied safely. Quit and "
        "reopen WebJam, then open the invitation again.",
    )
    controller.window.flash_message.assert_called_once_with(
        f"The {noun} switch did not finish safely. Try ending or "
        "leaving again, then reopen the invitation."
        if cleanup_unresolved else
        f"The {noun} switch did not finish. Quit and reopen "
        "WebJam, then open the invitation again.",
        ms=8000,
    )
    assert controller.audio.cleanup_retry_required is cleanup_unresolved
    assert controller._invite_switch_in_flight is False
    controller.window.session_strip.set_audio_state.assert_called_with(
        ("Try End Session" if hosting else "Try Leave Jam") if cleanup_unresolved else "Start Session",
        enabled=cleanup_unresolved,
    )
    assert joining_copy == (
        f"Joining your {noun}…",
        "WebJam is switching the room connection safely." if noun == "room"
        else "WebJam is switching the band connection safely.",
    )

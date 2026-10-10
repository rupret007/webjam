"""Invitation switching keeps Art's room vocabulary through failed cleanup."""

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.network_invite import create_invite_link
from tests.test_art_invitation_flow import controllers, qapp  # noqa: F401


@pytest.fixture
def switching(controllers, monkeypatch):  # noqa: F811 - imported pytest fixture
    def create(profile, hosting):
        app = controllers(profile=profile, hosting=hosting)
        monkeypatch.setattr(app, "_room_is_busy_for_invitation", lambda: True)
        monkeypatch.setattr(app, "_is_jamulus_running", lambda: False)
        monkeypatch.setattr(app.bridge, "hosted_server_alive", lambda: False)
        monkeypatch.setattr(app.bridge, "hosted_server_owned", lambda: False)
        for name in (
            "_stop_reference_track_for_session_end", "_stop_pocket_stage_for_session_end",
            "_stop_session_peer", "_clear_remote_invite_owner", "_stop_remote_transport",
        ):
            monkeypatch.setattr(app, name, Mock(return_value=True))
        monkeypatch.setattr(app.bridge, "stop_jamulus", Mock(return_value=True))
        monkeypatch.setattr(app, "begin_startup_journey", Mock())
        monkeypatch.setattr(app.window, "flash_message", Mock())
        monkeypatch.setattr(app._ui_invoker, "invoke", lambda callback: callback())
        thread = Mock()
        monkeypatch.setattr(
            "webjam_qt.controllers.application_controller.threading.Thread", thread,
        )
        question = Mock(return_value=QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, "question", question)
        return app, question, thread

    return create


@pytest.mark.parametrize("profile, noun", [("art", "room"), ("music", "jam")])
def test_switch_progress_uses_active_profile(switching, profile, noun):
    app, question, thread = switching(profile, False)
    assert app.accept_invite_url(create_invite_link("192.168.1.42"))
    assert app.window.session_hud._status.text() == f"Joining your {noun}…"
    assert app.window.session_hud._detail.text() == (
        "WebJam is switching the room connection safely."
        if profile == "art" else "WebJam is switching the band connection safely."
    )


@pytest.mark.parametrize("profile, noun", [("art", "room"), ("music", "jam")])
@pytest.mark.parametrize("hosting", [False, True], ids=["guest", "host"])
def test_switch_confirmation_can_cancel_without_cleanup(switching, profile, noun, hosting):
    app, question, thread = switching(profile, hosting)
    question.return_value = QMessageBox.StandardButton.No

    assert not app.accept_invite_url(create_invite_link("192.168.1.42"))

    assert question.call_args.args[1:3] == (
        f"Join this {noun}?",
        f"WebJam will safely end your current {noun}, then join the new one.",
    )
    assert question.call_args.args[4] == QMessageBox.StandardButton.No
    thread.assert_not_called()
    app._stop_session_peer.assert_not_called()
    app.begin_startup_journey.assert_not_called()


@pytest.mark.parametrize("profile, noun", [("art", "room"), ("music", "jam")])
@pytest.mark.parametrize("hosting", [False, True], ids=["guest", "host"])
@pytest.mark.parametrize("cleanup_unresolved", [False, True], ids=["apply-failed", "stop-failed"])
def test_switch_failure_keeps_room_copy_and_recovery_truth(
    switching, monkeypatch, profile, noun, hosting, cleanup_unresolved,
):
    app, question, thread = switching(profile, hosting)
    app._stop_reference_track_for_session_end.return_value = not cleanup_unresolved
    monkeypatch.setattr("core.settings.load_settings", Mock(side_effect=OSError("fixture")))

    assert app.accept_invite_url(create_invite_link("192.168.1.42"))
    # creator_profile is a property backed by this key. Simulate a profile
    # change while the worker is pending and prove the live property changed.
    assert app.creator_profile.key == profile
    app._active_creator_profile_key = "music"
    assert app.creator_profile.key == "music"
    thread.call_args.kwargs["target"]()

    assert app.window.session_hud._status.text() == f"WebJam couldn’t open the new {noun} safely"
    detail = app.window.session_hud._detail.text()
    if cleanup_unresolved:
        assert detail == (
            "Some previous session services still need to stop. Try "
            "ending or leaving again before opening the invitation."
        )
        label = (
            ("Try End Room" if hosting else "Try Leave Room")
            if profile == "art" else ("Try End Session" if hosting else "Try Leave Jam")
        )
        assert app.window.flash_message.call_args.args[0] == (
            f"The {noun} switch did not finish safely. Try ending or "
            "leaving again, then reopen the invitation."
        )
    else:
        connection = "room" if profile == "art" else "music"
        assert detail == (
            f"The previous {connection} connection was stopped, but the "
            "new invitation could not be applied safely. Quit and "
            "reopen WebJam, then open the invitation again."
        )
        label = "Start Session"
        assert app.window.flash_message.call_args.args[0] == (
            f"The {noun} switch did not finish. Quit and reopen "
            "WebJam, then open the invitation again."
        )
    assert app.window.session_strip._audio_button.text() == label
    assert app.window.session_strip._audio_button.isEnabled() is cleanup_unresolved
    assert app.audio.cleanup_retry_required is cleanup_unresolved
    assert app.audio._stop_hosting is hosting
    assert not app._invite_switch_in_flight
    app.begin_startup_journey.assert_not_called()


@pytest.mark.parametrize("hosting", [False, True], ids=["guest", "host"])
def test_art_apply_failure_reuses_ready_button_copy(switching, monkeypatch, hosting):
    app, question, thread = switching("art", hosting)
    assert not app._room_participant.active
    app._refresh_readiness()
    ready_label = app.window.session_strip._audio_button.text()
    assert ready_label == "Start Session"
    assert app.window.session_strip._audio_button.isEnabled()
    monkeypatch.setattr("core.settings.load_settings", Mock(side_effect=OSError("fixture")))

    assert app.accept_invite_url(create_invite_link("192.168.1.42"))
    thread.call_args.kwargs["target"]()

    assert app.window.session_strip._audio_button.text() == ready_label
    assert not app.window.session_strip._audio_button.isEnabled()
    assert not app.audio.cleanup_retry_required
    app.begin_startup_journey.assert_not_called()

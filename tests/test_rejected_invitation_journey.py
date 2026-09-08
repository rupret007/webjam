"""Rejected LAN invitations reach fresh Join through the real room actions."""
from __future__ import annotations

from unittest import mock

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from core.session_conductor import ArtRoomState, FailureDisposition, SessionPrimaryAction
from core.session_transfer import TransferAuthenticationError
from core.settings import save_settings
from tests.test_art_invitation_flow import _replace_join_dialog
from tests.test_art_lan_retry import (
    block_audio,
    controllers as _controllers_fixture,
    lan as _lan_fixture,
)
from tests.test_art_room_controller import drain, invitation, qapp as _qapp_fixture, state
from webjam_qt.controllers.application_controller import ApplicationController

controllers = _controllers_fixture
lan = _lan_fixture
qapp = _qapp_fixture


def reject_current(app, qapp):
    owner = app._room_participant.lan_guest
    assert owner.client.polling.wait(1)
    owner.client.responses.put(TransferAuthenticationError("PRIVATE-REJECTION-DETAIL"))
    drain(qapp, lambda: app._room_participant.lan_failed)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    return owner


@pytest.mark.parametrize("saved_profile", ["music", "art"])
@pytest.mark.parametrize("was_connected", [False, True])
@pytest.mark.parametrize("door_outcome", ["cancel", "empty"])
def test_rejected_invitation_primary_action_and_cancel_keep_notes_and_rejection(
    controllers, qapp, lan, monkeypatch, caplog, saved_profile, was_connected, door_outcome,
):
    invite = invitation()
    app: ApplicationController = controllers(invite=invite, profile=saved_profile)
    block_audio(app, monkeypatch)
    assert app.begin_startup_journey()
    room = app._room_participant
    first = room.lan_guest
    if was_connected:
        first.client.responses.put(state(invite))
        drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    app.window.session_canvas.set_notes("My mountain study — keep this draft")
    saved_meeting = app.settings.webex_url
    app.window.show()
    generation = room.generation
    reject_current(app, qapp)
    assert not room.can_retry_lan
    assert app._session_conductor_facts().failure is FailureDisposition.BLOCKED
    guidance = room.guidance()
    assert guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    app._update_session_hud()
    button = app.window.session_hud._action
    assert button.isVisibleTo(app.window) and button.isEnabled()
    assert button.text() == "Paste New Invite"
    assert not app.window.session_hud._secondary_action.isVisibleTo(app.window)
    assert app.window.session_canvas._current_guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    app._refresh_pocket_projection()
    assert app._get_pocket_projection().primary_action is SessionPrimaryAction.PASTE_NEW_INVITE

    opened = _replace_join_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Rejected if door_outcome == "cancel" else QDialog.DialogCode.Accepted,
    )
    button.click()
    assert len(opened) == 1
    assert room.lan_guest is first and room.generation == generation
    assert room.lan_failed and not room.can_retry_lan
    assert app.window.session_canvas.current_notes() == "My mountain study — keep this draft"
    assert app.settings.last_creator_profile_key == saved_profile
    assert app.settings.webex_url == saved_meeting
    assert app.window.session_hud._action.text() == "Paste New Invite"
    # Stale generic retry signals must not recreate the rejected observer or
    # fall through to the saved Music profile's launch path.
    for action in ("retry", "retry_startup", "try_reconnect", "check_session"):
        app._on_conductor_action_requested(action)
    assert len(lan.owners) == 1 and room.lan_guest is first
    room.receive_lan(first, generation, state(invite))
    room.lose_lan(first, generation, False)
    assert room.lan_failed and not room.can_retry_lan
    assert app._session_conductor_facts().failure is FailureDisposition.BLOCKED
    assert app.window.session_hud._action.text() == "Paste New Invite"
    assert "PRIVATE-REJECTION-DETAIL" not in caplog.text
    app._configure_guest_peer.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()


def test_replacement_after_rejection_waits_for_owned_cleanup(
    controllers, qapp, lan, monkeypatch,
):
    app: ApplicationController = controllers(invite=invitation(), profile="art")
    block_audio(app, monkeypatch)
    assert app.begin_startup_journey()
    first = reject_current(app, qapp)
    app.window.session_canvas.set_notes("Keep the sketch while cleanup waits")
    replacement = invitation()
    _replace_join_dialog(monkeypatch, result=QDialog.DialogCode.Accepted, value=replacement)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    with monkeypatch.context() as patch:
        patch.setattr(first, "stop", mock.Mock(return_value=False))
        app._on_conductor_action_requested("paste_invite")
        drain(qapp, lambda: not app.audio.stopping)
        assert app.audio.cleanup_retry_required
        assert app._room_participant.lan_guest is first
        assert len(lan.owners) == 1
        assert app.window.session_canvas.current_notes() == "Keep the sketch while cleanup waits"
    app.audio.retry_stop()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._room_participant.lan_guest is None
    assert len(lan.owners) == 1
    app._configure_guest_peer.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()


def test_fresh_invitation_action_connects_a_new_art_owner_without_replaying_old_state(
    controllers, qapp, lan, monkeypatch,
):
    app: ApplicationController = controllers(invite=invitation(), profile="art")
    # The real replacement path reloads the user's existing configuration.
    # Give it an actual isolated file rather than the default home location.
    save_settings(app.settings)
    block_audio(app, monkeypatch)
    assert app.begin_startup_journey()
    first = reject_current(app, qapp)
    room = app._room_participant
    previous_generation = room.generation
    app.window.session_canvas.set_notes("Mountains after a fresh invitation")
    replacement = invitation()
    _replace_join_dialog(monkeypatch, result=QDialog.DialogCode.Accepted, value=replacement)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    app.window.session_hud._action.click()
    drain(qapp, lambda: room.lan_guest is not None and room.lan_guest is not first)
    second = room.lan_guest
    assert second.invite is replacement
    assert len(lan.owners) == 2 and first._stop.is_set()
    room.lose_lan(first, room.generation, True)
    room.receive_lan(first, room.generation, state(first.invite))
    room.lose_lan(second, previous_generation, True)
    assert not room.lan_failed
    second.client.responses.put(state(replacement))
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    assert not room.can_retry_lan
    assert app.window.session_canvas.current_notes() == "Mountains after a fresh invitation"
    app._configure_guest_peer.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()

"""Actual invitation-copy and recovery actions for Art and Music guests."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog

from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.session_conductor import SessionPrimaryAction
from core.session_transfer import SessionCredentials
from core.settings import AppSettings, load_settings, save_settings
from tests.test_art_room_controller import arm_lan, invitation
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.invitation_ingress import InvitationSource, parse_invitation_at_ingress
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controllers(qapp, tmp_path):
    made = []

    def create(*, profile="art", hosting=False, invite=None):
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"),
            last_creator_profile_key=profile,
            host_server_enabled=hosting,
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Making room",
        )
        app = ApplicationController(window, settings=settings, session_invite=invite)
        made.append(app)
        return app

    yield create
    for app in reversed(made):
        app.shutdown()
        window = app.window
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(window)


def _lan_link():
    credentials = SessionCredentials.create()
    return create_invite_link(
        "192.168.1.20", session_id=credentials.session_id,
        peer_port=22125, invite_token=credentials.invite_token,
    )


@pytest.mark.parametrize(
    "meeting", ["", "https://example.webex.com/meet/artist"], ids=["without-meeting", "with-meeting"],
)
@pytest.mark.parametrize("profile", ["art", "music"])
def test_actual_host_copy_leads_to_explicit_join_with_the_complete_invitation(
    controllers, monkeypatch, tmp_path, caplog, meeting, profile,
):
    app = controllers(profile=profile, hosting=True)
    app.settings.webex_url = meeting
    link = _lan_link()
    readiness = SimpleNamespace(shareable=True, address="192.168.1.20")
    app._host_share_readiness = Mock(return_value=readiness)
    app._current_invite_url = Mock(return_value=link)
    app.window.flash_message = Mock()
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    monkeypatch.setattr(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer", lambda settings: None,
    )
    qt_errors = []
    monkeypatch.setattr(
        sys, "excepthook", lambda error_type, _error, _traceback: qt_errors.append(error_type),
    )
    effects = [Mock(name=name) for name in ("browser", "process", "provider", "startup", "audio")]
    monkeypatch.setattr("webbrowser.open", effects[0])
    monkeypatch.setattr("subprocess.Popen", effects[1])
    monkeypatch.setattr(QDesktopServices, "openUrl", effects[2])
    monkeypatch.setattr(app, "begin_startup_journey", effects[3])
    monkeypatch.setattr(app, "_launch_native_jamulus_for_startup", effects[4])

    app._copy_band_invite()

    assert clipboard.setText.call_count == 1
    message = clipboard.setText.call_args.args[0]
    # Assertion diagnostics must not include the private clipboard payload.
    visible_copy = message.replace(link, "[private invitation]")
    if meeting:
        visible_copy = visible_copy.replace(meeting, "[optional meeting]")
    link_count = message.splitlines().count(link)
    assert link_count == 1
    assert "same Wi-Fi or local network" in visible_copy
    assert "choose Join" in visible_copy and "paste this full invitation" in visible_copy
    assert "host needs to keep this room open" in visible_copy
    assert "Open the link in WebJam" not in visible_copy
    parsed = parse_invitation_at_ingress(message, source=InvitationSource.PASTE)
    expected = parse_invitation_at_ingress(link, source=InvitationSource.PASTE)
    assert parsed == expected
    meeting_preserved = (meeting in message) if meeting else ("Optional" not in message)
    assert meeting_preserved
    feedback = app.window.flash_message.call_args.args[0]
    feedback_exposes_invitation = link in feedback or expected.invite_token in feedback
    assert not feedback_exposes_invitation
    visible_feedback = feedback.replace(link, "[private invitation]")
    visible_feedback = visible_feedback.replace(expected.invite_token, "[private token]")
    if meeting:
        visible_feedback = visible_feedback.replace(meeting, "[optional meeting]")
    assert "same Wi-Fi or local network" in visible_feedback
    assert "whole message" in visible_feedback and "Keep this room open" in visible_feedback
    assert app._last_shared_lan_address == readiness.address

    personal_meeting = "https://zoom.us/j/987654321"
    guest_settings = AppSettings(
        config_file=str(tmp_path / "guest-settings.json"),
        musician_name="Guest",
        last_creator_profile_key="music",
        webex_url=personal_meeting,
    )
    save_settings(guest_settings)
    door = LaunchDialog(load_settings(guest_settings.config_file))
    try:
        door.show_join()
        door._invite_input.setText(message)
        door._join_button_primary.click()
        assert not qt_errors
        assert door.result() == QDialog.DialogCode.Accepted
        assert door.selected_role == "join"
        paste_empty = door._invite_input.text() == ""
        assert paste_empty
        assert door.band_invite == expected
        assert door.remote_invitation is None
        persisted = load_settings(guest_settings.config_file)
        assert persisted.webex_url == personal_meeting
        assert persisted.last_creator_profile_key == "music"
        assert guest_settings.webex_url == personal_meeting
        # This door check stops before application adoption. The composed
        # Music journey separately proves the room's Conversation handoff.
        for effect in effects:
            assert effect.call_count == 0
        assert not any(
            record.name == "webjam.ui_thread" and record.levelno >= logging.ERROR
            for record in caplog.records
        )
    finally:
        door.deleteLater()
        QCoreApplication.sendPostedEvents(door, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(door)


@pytest.mark.parametrize("profile", ["art", "music"])
def test_native_owner_does_not_inherit_lan_or_public_reachability_claim(
    controllers, monkeypatch, profile,
):
    app = controllers(profile=profile, hosting=True)
    issued = issue_remote_invitation(
        "reference-local", allowed_profiles={"reference-local"},
        host_spki_sha256=bytes.fromhex("44" * 32),
    )
    # Native owner supplies an opaque private link. Scope is derived from
    # this owner path, not by reparsing or guessing from the URL.
    owner = SimpleNamespace(copy_for_clipboard=Mock(return_value=issued.private_link.reveal_for_clipboard()))
    app._remote_invite_owner = owner
    app.window.flash_message = Mock()
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    try:
        app._copy_band_invite()
    finally:
        app._remote_invite_owner = None
    message = clipboard.setText.call_args.args[0]
    visible_copy = message.replace(issued.private_link.reveal_for_clipboard(), "[private invitation]")
    assert "choose Join" in visible_copy and "paste this full invitation" in visible_copy
    assert "same Wi-Fi" not in visible_copy
    assert "public" not in visible_copy.casefold()
    assert "anywhere" not in visible_copy.casefold()
    feedback = app.window.flash_message.call_args.args[0]
    private_link = issued.private_link.reveal_for_clipboard()
    feedback_exposes_invitation = private_link in feedback
    assert not feedback_exposes_invitation
    visible_feedback = feedback.replace(private_link, "[private invitation]")
    assert "same Wi-Fi" not in visible_feedback
    assert "public" not in visible_feedback.casefold()
    assert "anywhere" not in visible_feedback.casefold()
    parsed = parse_invitation_at_ingress(
        message, source=InvitationSource.PASTE,
        allowed_remote_profiles=frozenset({"reference-local"}),
    )
    preserved = (
        parsed.profile_id == issued.invitation.profile_id
        and parsed.session_reference == issued.invitation.session_reference
        and parsed.invite_reference == issued.invitation.invite_reference
        and parsed.host_spki_sha256 == issued.invitation.host_spki_sha256
        and parsed.capability_for_enrollment() == issued.invitation.capability_for_enrollment()
    )
    assert preserved


@pytest.mark.parametrize("profile", ["art", "music"])
def test_failed_lan_share_does_not_copy_unusable_invitation(controllers, monkeypatch, profile):
    app = controllers(profile=profile, hosting=True)
    app._host_share_readiness = Mock(
        return_value=SimpleNamespace(shareable=False, address="")
    )
    app._current_invite_url = Mock(return_value="")
    app._update_session_hud = Mock()
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    app._copy_band_invite()
    assert clipboard.setText.call_count == 0


def _replace_join_dialog(monkeypatch, *, result, value=None):
    opened = []

    class Door:
        def __init__(self, *args, **kwargs):
            self.band_invite = value
            opened.append(self)

        def show_join(self):
            pass

        def exec(self):
            return result

        def take_remote_invitation(self):
            return None

        def deleteLater(self):
            pass

    monkeypatch.setattr("webjam_qt.windows.launch_dialog.LaunchDialog", Door)
    return opened


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("outcome", ["cancelled", "empty", "rejected"])
def test_lan_replacement_door_restores_real_retry_policy(
    controllers, monkeypatch, profile, outcome,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(profile=profile, invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    room.lose_lan(room.lan_guest, room.generation, True)
    assert room.can_retry_lan
    before = room.lan_guest
    app._render_remote_fresh_invitation_hud = Mock()
    _replace_join_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Rejected if outcome == "cancelled" else QDialog.DialogCode.Accepted,
        value=invitation() if outcome == "rejected" else None,
    )
    if outcome == "rejected":
        app.accept_invitation = Mock(return_value=False)

    app._paste_new_invitation()

    assert room.lan_guest is before and room.can_retry_lan
    assert not app._remote_invitation_requires_replacement
    guidance = app._last_guidance_display_override
    assert guidance.primary_action is SessionPrimaryAction.RETRY_SETUP
    assert guidance.action_label == "Try Again"
    assert "network" in guidance.message.casefold()
    app._render_remote_fresh_invitation_hud.assert_not_called()


@pytest.mark.parametrize("profile", ["music", "art"])
def test_cancelling_native_replacement_preserves_fresh_invite_requirement(
    controllers, monkeypatch, profile,
):
    app = controllers(profile=profile)
    app._remote_invitation_requires_replacement = True
    app._remote_fresh_invitation_detail = Mock(
        return_value="Ask the host for a fresh invitation, then paste it here."
    )
    _replace_join_dialog(monkeypatch, result=QDialog.DialogCode.Rejected)
    app._paste_new_invitation()
    assert app._remote_invitation_requires_replacement
    guidance = app._last_guidance_display_override
    assert guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert "fresh invitation" in guidance.message
    assert not app._room_participant.can_retry_lan


@pytest.mark.parametrize("action", ["retry_startup", "retry", "try_reconnect", "check_session"])
@pytest.mark.parametrize("profile", ["music", "art"])
def test_repeated_retry_action_stays_with_lan_owner_before_host_profile(
    controllers, monkeypatch, action, profile,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(profile=profile, invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    old = room.lan_guest
    room.lose_lan(old, room.generation, True)
    app._retry_startup_journey = Mock()
    app._retry_session = Mock()
    app._begin_remote_join = Mock()
    app._launch_native_jamulus_for_startup = Mock()
    begin_attempt = Mock(wraps=app._start_session_conductor_attempt)
    app._start_session_conductor_attempt = begin_attempt

    app._on_conductor_action_requested(action)
    replacement = room.lan_guest
    assert replacement is not old
    assert replacement.invite is invite
    assert room.probing and not room.can_retry_lan
    app._on_conductor_action_requested(action)

    assert room.lan_guest is replacement
    begin_attempt.assert_called_once_with("guest")
    app._retry_startup_journey.assert_not_called()
    app._retry_session.assert_not_called()
    app._begin_remote_join.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()


@pytest.mark.parametrize("profile", ["music", "art"])
def test_terminal_lan_guest_can_open_replacement_from_visible_secondary_action(
    controllers, monkeypatch, profile,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(profile=profile, invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    room.lose_lan(room.lan_guest, room.generation, True)
    app.window.show()
    replacement = app.window.session_hud._secondary_action
    assert replacement.isVisibleTo(app.window)
    assert replacement.text() == "Use Another Invite"
    assert app.window.session_hud._action.text() == "Try Again"
    opened = _replace_join_dialog(monkeypatch, result=QDialog.DialogCode.Rejected)

    replacement.click()

    assert len(opened) == 1
    assert room.can_retry_lan
    assert replacement.isVisibleTo(app.window)
    assert not app._remote_invitation_requires_replacement
    # A stale secondary command after retry has begun must not open another
    # invitation or interrupt its now-current observer.
    app._on_conductor_action_requested("retry_startup")
    assert not room.can_retry_lan
    assert not replacement.isVisibleTo(app.window)
    app._on_conductor_secondary_action_requested("replace_lan_invite")
    assert len(opened) == 1


@pytest.mark.parametrize("profile", ["music", "art"])
def test_native_replacement_requirement_cannot_enter_lan_secondary_route(
    controllers, monkeypatch, profile,
):
    app = controllers(profile=profile)
    app._remote_invitation_requires_replacement = True
    app._update_session_hud()
    opened = _replace_join_dialog(monkeypatch, result=QDialog.DialogCode.Rejected)
    app._on_conductor_secondary_action_requested("replace_lan_invite")
    assert not opened
    assert not app.window.session_hud._secondary_action.isVisibleTo(app.window)
    assert app._remote_invitation_requires_replacement
    assert app._last_guidance_display_override.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE


@pytest.mark.parametrize("was_connected", [False, True])
def test_art_failure_distinguishes_first_join_from_a_lost_room(
    controllers, monkeypatch, qapp, was_connected,
):
    from core.session_conductor import ArtRoomState
    from tests.test_art_room_controller import drain

    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(profile="art", invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    if was_connected:
        owner.poll_once()
        drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    room.lose_lan(owner, generation, True)

    overview = app.window.art_room_overview._overview
    assert overview.phase == "failed"
    assert overview.phase_label == ("Connection lost" if was_connected else "Room not reached")
    assert overview.connection_label == (
        "Room connection is unavailable" if was_connected else "No room connection confirmed"
    )
    assert not overview.activity_enabled
    assert room.can_retry_lan
    assert room.retry_lan_guest()
    assert app.window.art_room_overview._overview.phase == "opening"
    new_owner = room.lan_guest
    new_owner.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    assert app.window.art_room_overview._overview.connection_label == "Connected to the host"

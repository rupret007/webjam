"""Conversation follows accepted room ownership through real controller paths."""

from types import MethodType
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.meeting_companion import build_invite_message
from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.settings import load_settings
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import RoomBackend, arm_lan, drain
from tests.test_invitation_conversation_journey import (
    PERSONAL,
    ROOM,
    apps as _apps,
    qapp as _qapp,
)
from webjam_qt.controllers.application_controller import ApplicationController


apps = _apps
qapp = _qapp


def _remote_message(meeting=ROOM):
    issue = issue_remote_invitation(
        "reference-local", allowed_profiles={"reference-local"},
        host_spki_sha256=b"p" * 32,
    )
    return build_invite_message(
        join_link=issue.private_link.reveal_for_clipboard(),
        creator_profile_key="art", meeting_url=meeting,
    ).text


def _start_lan(app, qapp, monkeypatch, *, profile="art"):
    invite = app._guest_invite
    arm_lan(monkeypatch, invite, profile)
    app.begin_startup_journey = MethodType(ApplicationController.begin_startup_journey, app)
    app._launch_native_jamulus_for_startup = Mock()
    app._start_hosted_server_for_startup = Mock()
    assert app.begin_startup_journey()
    owner = app._room_participant.lan_guest
    owner.poll_once()
    if profile == "art":
        drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    else:
        drain(qapp, lambda: app._launch_native_jamulus_for_startup.call_count == 1)
    return owner


def test_native_transport_consumption_and_late_state_do_not_lose_or_revive_meeting(
    apps, qapp, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = apps()
    assert app.accept_invite_url(_remote_message())
    invitation = app._remote_invitation
    assert invitation is not None
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    source = app._remote_session
    backend.emit(RoomState(1, "art", "paint_along"))
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    assert app._remote_invitation is None
    assert app._effective_meeting_url() == ROOM
    assert app.webex.meeting_url == ROOM
    app.bridge.launch_webex.assert_not_called()

    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._remote_session is None
    assert app._effective_meeting_url() == PERSONAL
    backend.emit(RoomState(2, "art", "paint_along"))
    app._on_remote_session_snapshot(source.snapshot, source=source)
    qapp.processEvents()
    assert app._effective_meeting_url() == PERSONAL
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()


def test_lan_music_discovery_replaces_observer_without_dropping_conversation(
    apps, qapp, monkeypatch,
):
    app = apps(cold=True)
    invitation = app._guest_invite
    owner = _start_lan(app, qapp, monkeypatch, profile="music")
    assert app.creator_profile.key == "music"
    assert owner._stop.is_set()
    assert app._room_participant.lan_guest is None
    assert app.guest_peer is not None
    assert app._guest_invite is invitation
    assert app._effective_meeting_url() == ROOM
    assert app.webex.meeting_url == ROOM
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()


def test_native_music_proxy_activation_keeps_conversation_after_consuming_invite(
    apps, qapp, monkeypatch,
):
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app = apps()
    assert app.accept_invite_url(_remote_message())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    RoomBackend.instances[-1].emit(RoomState(1, "music"))
    drain(qapp, lambda: app._remote_invitation is None)
    assert app.creator_profile.key == "music"
    assert app._remote_route_generation > 0
    assert app.settings.jamulus_server == "127.0.0.1"
    assert app.settings.jamulus_port == 43123
    assert app._effective_meeting_url() == ROOM
    assert app.webex.meeting_url == ROOM
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()


@pytest.mark.parametrize("fail_first", (False, True))
def test_actual_lan_leave_restores_personal_meeting_only_after_cleanup_receipt(
    apps, qapp, monkeypatch, fail_first,
):
    app = apps(cold=True)
    owner = _start_lan(app, qapp, monkeypatch)
    generation = app._room_participant.generation
    host_state = owner.last_state
    real_stop = owner.stop
    if fail_first:
        owner.stop = Mock(return_value=False)
    app.bridge.webex_state = "Opened externally"

    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    if fail_first:
        assert app.audio.cleanup_retry_required
        assert app._effective_meeting_url() == ROOM
        assert app._room_participant.lan_guest is owner
        owner.stop = real_stop
        app.audio.retry_stop()
        drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._room_participant.lan_guest is None
    assert app._guest_invite is None
    assert app._effective_meeting_url() == PERSONAL
    assert app.webex.meeting_url == PERSONAL
    assert app.bridge.webex_state == "Not opened"
    # A receipt queued by the retired guest cannot bring its meeting back.
    app._room_participant.receive_lan(owner, generation, host_state)
    assert app._effective_meeting_url() == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()


@pytest.mark.parametrize("native", (False, True))
@pytest.mark.parametrize("last_meeting", ("https://last.webex.com/meet/last", ""))
def test_queued_latest_invitation_applies_its_own_conversation_after_switch(
    apps, monkeypatch, native, last_meeting,
):
    app = apps(cold=True)
    workers = []

    class DeferredThread:
        def __init__(self, *, target, name=None, **kwargs):
            self.target, self.name = target, name

        def start(self):
            if self.name == "webjam-invite-switch":
                workers.append(self.target)

    def message(host, title, meeting):
        return build_invite_message(
            join_link=create_invite_link(host, session_name=title),
            creator_profile_key="art", meeting_url=meeting,
        ).text

    app.bridge.jamulus_state = "Running"
    app.bridge.hosted_server_alive = Mock(return_value=False)
    app.bridge.hosted_server_owned = Mock(return_value=False)

    def stop_music():
        app.bridge.jamulus_state = "Stopped"
        return True

    app.bridge.stop_jamulus = Mock(side_effect=stop_music)
    app._begin_remote_join = Mock()
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes))
    monkeypatch.setattr("webjam_qt.controllers.application_controller.threading.Thread", DeferredThread)
    monkeypatch.setattr(app._ui_invoker, "invoke", lambda callback: callback())
    first = message("192.168.1.42", "First", "https://first.webex.com/meet/first")
    last = (_remote_message(last_meeting) if native
            else message("192.168.1.43", "Last", last_meeting))

    assert app.accept_invite_url(first)
    assert app.accept_invite_url(last)
    assert len(workers) == 1
    assert app._effective_meeting_url() == ROOM
    workers.pop()()

    assert not app._invite_switch_in_flight
    assert app._pending_invitation is None
    assert app._pending_invitation_meeting_url == ""
    assert app._effective_meeting_url() == last_meeting
    assert app.webex.meeting_url == last_meeting
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    app.bridge.launch_webex.assert_not_called()
    if native:
        assert app._remote_invitation is not None
        app._begin_remote_join.assert_called_once_with()
    else:
        assert app.settings.jamulus_server == "192.168.1.43"
        assert app.window.session_strip.current_title() == "Last"
        app.begin_startup_journey.assert_called_once_with()

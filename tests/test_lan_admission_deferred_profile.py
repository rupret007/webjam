"""LAN admission waits for Library work, without replaying stale receipts."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.session_conductor import ArtRoomState, SessionConductorPhase
from services.lan_room_guest import LanRoomTerminalReason
from tests.test_art_room_controller import (
    arm_lan,
    controllers as _controllers_fixture,
    drain,
    invitation,
    qapp as _qapp_fixture,
    state,
)

qapp = _qapp_fixture
controllers = _controllers_fixture


@pytest.mark.parametrize("profile,host_profile", [("music", "art"), ("art", "music")])
def test_lan_admission_waits_for_library_then_retries_once(
    qapp, controllers, monkeypatch, profile, host_profile,
):
    invite = invitation()
    arm_lan(monkeypatch, invite, host_profile)
    app = controllers(invite=invite, profile=profile)
    assert app.begin_startup_journey()
    room = app._room_participant
    observer, generation = room.lan_guest, room.generation
    launch = mock.Mock()
    monkeypatch.setattr(app, "begin_startup_journey", launch)
    observe = mock.Mock(wraps=room.observe_creative_state)
    monkeypatch.setattr(room, "observe_creative_state", observe)
    set_profile = mock.Mock(wraps=app.window.set_creator_profile)
    monkeypatch.setattr(app.window, "set_creator_profile", set_profile)
    app.window.recording_studio._workspace_review_active = True
    try:
        room.receive_lan(observer, generation, state(invite, host_profile))
        assert app.creator_profile.key == profile
        assert room.state is ArtRoomState.STARTING
        assert room.probing and room.lan_guest is observer
        assert not observer._stop.is_set()
        assert app.guest_peer is None
        assert app._last_session_conductor.phase is not SessionConductorPhase.CONNECTED
        assert room.guidance().title == "Contacting the host"
        launch.assert_not_called()
        observe.assert_not_called()
    finally:
        app.window.recording_studio._workspace_review_active = False

    # No new network receipt: the scheduled retry must complete admission.
    drain(qapp, lambda: not room.probing)
    assert app.creator_profile.key == host_profile
    assert app.settings.last_creator_profile_key == profile
    # Music handoff must not restore the personal profile between adoption
    # and audio startup. Both directions perform exactly one visible switch.
    set_profile.assert_called_once()
    assert app.bridge.jamulus_process is None
    if host_profile == "art":
        assert room.state is ArtRoomState.CONNECTED
        assert app._last_session_conductor.title == "You’re in"
        observe.assert_called_once()
        launch.assert_not_called()
    else:
        assert room.lan_guest is None and observer._stop.is_set()
        assert app.guest_peer is not None
        launch.assert_called_once_with()
        # A queued discovery receipt cannot start audio twice.
        room.receive_lan(observer, generation, state(invite, host_profile))
        launch.assert_called_once_with()
    assert app._stop_session_peer(clear_invite=True)
    assert app.creator_profile.key == profile


@pytest.mark.parametrize("host_profile", ["art", "music"])
@pytest.mark.parametrize("retirement", [
    "stop", "replacement", "terminal", "transient", "terminal_before_callback", "stopping",
])
def test_deferred_lan_receipt_cannot_outlive_its_connection(
    controllers, monkeypatch, host_profile, retirement,
):
    invite = invitation()
    profile = "music" if host_profile == "art" else "art"
    arm_lan(monkeypatch, invite, host_profile)
    app = controllers(invite=invite, profile=profile)
    assert app.begin_startup_journey()
    room = app._room_participant
    observer, generation = room.lan_guest, room.generation
    pending = []
    monkeypatch.setattr("webjam_qt.controllers.room_participant.QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: pending.append(callback),
    ))
    launch = mock.Mock()
    monkeypatch.setattr(app, "begin_startup_journey", launch)
    app.window.recording_studio._workspace_review_active = True
    try:
        room.receive_lan(observer, generation, state(invite, host_profile))
        assert len(pending) == 1
        if retirement in {"stop", "replacement"}:
            assert app._stop_session_peer(clear_invite=True)
            if retirement == "replacement":
                next_invite = invitation()
                app._guest_invite = next_invite
                assert room.start_lan_guest(next_invite)
        elif retirement == "terminal_before_callback":
            observer._terminal_reason = LanRoomTerminalReason.UNAVAILABLE
        elif retirement == "stopping":
            app.audio.stopping = True
        else:
            room.lose_lan(observer, generation, retirement == "terminal")
    finally:
        app.window.recording_studio._workspace_review_active = False
    for retry in pending[:]:
        retry()
    assert len(pending) == 1
    assert app.creator_profile.key == profile
    assert room.state is not ArtRoomState.CONNECTED
    assert app.guest_peer is None
    launch.assert_not_called()
    app.audio.stopping = False


def test_lan_retry_expires_and_new_receipt_can_resume_admission(controllers, monkeypatch):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    observer, generation = room.lan_guest, room.generation
    pending, now = [], [100.0]
    monkeypatch.setattr("webjam_qt.controllers.room_participant.QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: pending.append((delay, callback)),
    ))
    monkeypatch.setattr("webjam_qt.controllers.room_participant.time", SimpleNamespace(
        monotonic=lambda: now[0],
    ))
    apply = mock.Mock(wraps=app._apply_creator_profile_key)
    monkeypatch.setattr(app, "_apply_creator_profile_key", apply)
    app.window.recording_studio._workspace_review_active = True
    try:
        room.receive_lan(observer, generation, state(invite))
        assert len(pending) == 1 and pending[0][0] == 200
        now[0] += 5.0
        pending.pop()[1]()
        assert not pending
        assert apply.call_count == 1
        assert room.probing and room.state is ArtRoomState.STARTING
    finally:
        app.window.recording_studio._workspace_review_active = False
    room.receive_lan(observer, generation, state(invite))
    assert room.state is ArtRoomState.CONNECTED
    assert app.creator_profile.key == "art"


def test_new_lan_receipt_supersedes_deferred_profile(controllers, monkeypatch):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite)
    assert app.begin_startup_journey()
    room = app._room_participant
    observer, generation = room.lan_guest, room.generation
    pending = []
    monkeypatch.setattr("webjam_qt.controllers.room_participant.QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: pending.append(callback),
    ))
    app.window.recording_studio._workspace_review_active = True
    try:
        room.receive_lan(observer, generation, state(invite, "art"))
        assert len(pending) == 1
        launch = mock.Mock()
        monkeypatch.setattr(app, "begin_startup_journey", launch)
        room.receive_lan(observer, generation, state(invite, "music"))
    finally:
        app.window.recording_studio._workspace_review_active = False
    pending[0]()
    assert app.creator_profile.key == "music"
    launch.assert_called_once_with()
    assert room.state is not ArtRoomState.CONNECTED

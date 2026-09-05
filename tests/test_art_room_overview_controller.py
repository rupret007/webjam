"""Real Art room events drive the stage; Music and personal work stay intact."""

from __future__ import annotations

from dataclasses import replace
from unittest import mock

import pytest

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.session_transfer import SharedCanvasSessionSnapshot
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import (
    RoomBackend,
    arm_lan,
    controllers as _controllers_fixture,
    drain,
    invitation,
    qapp as _qapp_fixture,
    remote,
    state,
)
from webjam_qt.controllers.application_controller import ApplicationController

controllers = _controllers_fixture
qapp = _qapp_fixture


@pytest.fixture(autouse=True)
def accept_explicit_room_exit(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )


def test_cold_guest_room_loss_and_leave_never_show_music_roster(qapp, controllers, monkeypatch):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app: ApplicationController = controllers(invite=invite)
    app.window.resize(720, 560)
    app.window.show()
    assert app.begin_startup_journey()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    owner.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    panel = app.window.art_room_overview
    assert panel.isVisibleTo(app.window)
    assert not app.window.participant_grid.isVisibleTo(app.window)
    assert panel._overview.connection_label == "Connected to the host"
    assert "0 artists" not in panel.accessibleDescription()
    assert panel._overview.activity_label == "Bring your own tools"

    app._on_rail_view_changed("canvas")
    qapp.processEvents()
    assert app.window.session_canvas.isVisibleTo(app.window)
    assert not panel.isVisibleTo(app.window)
    room.lose_lan(owner, generation, False)
    assert panel._overview.phase == "reconnecting"
    assert panel._overview.conversation_enabled
    assert not panel.isVisibleTo(app.window)
    app._on_rail_view_changed("stage")
    qapp.processEvents()
    assert panel.isVisibleTo(app.window)
    assert not app.window.participant_grid.isVisibleTo(app.window)

    room.lose_lan(owner, generation, True)
    assert panel._overview.phase == "failed"
    owner.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    assert panel._overview.phase == "connected"

    app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == "music"
    assert app.window.participant_grid.isVisibleTo(app.window)
    assert not panel.isVisibleTo(app.window)
    room.receive_lan(owner, generation, state(invite))
    assert app.creator_profile.key == "music"
    assert not panel.isVisibleTo(app.window)


@pytest.mark.parametrize("hosting", [False, True])
def test_idle_role_and_failed_probe_do_not_claim_an_open_room(controllers, hosting):
    app: ApplicationController = controllers(profile="art", hosting=hosting)
    app._refresh_readiness()
    panel = app.window.art_room_overview
    assert panel._overview.role_label == ("Host" if hosting else "Guest")
    assert panel._overview.phase == "idle"
    app._room_participant.probing = True
    app._room_participant.probe_failed = True
    app._update_session_hud()
    assert panel._overview.phase == "failed"
    assert not panel._overview.activity_enabled
    app._room_participant.probing = False
    app._room_participant.probe_failed = False


def test_native_shared_canvas_routes_existing_panel_and_rechecks_owner(
    qapp, controllers, monkeypatch,
):
    launcher = mock.Mock()
    launcher.available.return_value = True
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    app: ApplicationController = controllers()
    app.bridge.launch_webex = mock.Mock()
    app.settings.webex_url = "https://example.webex.com/meet/room"
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    backend.emit(RoomState(1, "art", "talk_and_make", shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True, join_url="drawpile://example.com/room?password=private-canvas",
        server_label="Private server", session_label="Private canvas title",
    )))
    drain(qapp, lambda: app.creator_profile.key == "art")
    panel = app.window.art_room_overview
    assert panel._overview.activity_action == "canvas"
    assert "private-canvas" not in repr(panel._overview)
    assert "Private canvas title" not in panel.accessibleDescription()
    launcher.open_canvas.assert_not_called()

    panel.activity_requested.emit("canvas")
    assert app._shared_canvas_dialog is not None
    launcher.open_canvas.assert_not_called()
    panel.conversation_requested.emit()
    assert app.window.webex_embed.isVisibleTo(app.window)
    app.bridge.launch_webex.assert_not_called()
    assert app._room_participant.state is ArtRoomState.CONNECTED

    # A queued click may outlive its rendered snapshot. Revalidate the room,
    # not merely the old enabled button, before opening either panel.
    app._open_shared_canvas = mock.Mock()
    app._show_webex_conversation = mock.Mock()
    source = app._remote_session
    failed = replace(source.snapshot, phase=RemoteSessionPhase.FAILED)
    app._on_remote_session_snapshot(failed, source=source)
    assert panel._overview.phase == "failed"
    panel.activity_requested.emit("canvas")
    panel.conversation_requested.emit()
    app._open_shared_canvas.assert_not_called()
    app._show_webex_conversation.assert_called_once_with()
    assert not panel._overview.activity_enabled

    app._show_webex_conversation.reset_mock()
    app.audio.stopping = True
    try:
        panel.activity_requested.emit("canvas")
        panel.conversation_requested.emit()
        app._open_shared_canvas.assert_not_called()
        app._show_webex_conversation.assert_not_called()
    finally:
        app.audio.stopping = False


def test_ended_native_host_can_reopen_with_current_room_truth(qapp, controllers, monkeypatch):
    from core.room_state import RoomIdentity
    from core.session_transport import SessionRole
    from services.remote_session_runtime import RemoteSessionSnapshot

    owners = []

    class HostOwner:
        def __init__(self, **kwargs):
            self.room_identity = RoomIdentity(f"room-{len(owners)}", "room-key")
            self.snapshot = RemoteSessionSnapshot(
                RemoteSessionPhase.IDLE, SessionRole.HOST, 1,
            )
            self.connection_available = False
            self.invitation_available = True
            self.publish_room_state = mock.Mock(return_value=True)
            self.stop = mock.Mock(return_value=True)
            owners.append(self)

    monkeypatch.setattr("services.native_remote_transport.NativeHostTransportOwner", HostOwner)
    monkeypatch.setattr("services.native_remote_transport.reference_local_host_requested", lambda: True)
    app: ApplicationController = controllers(profile="art", hosting=True)
    app.bridge.enable_remote_host_mode = mock.Mock()
    app.settings.last_creator_start_key = "paint_along"
    panel = app.window.art_room_overview
    try:
        assert app.begin_startup_journey()
        drain(qapp, lambda: not app._remote_host_preparing)
        assert panel._overview.phase == "waiting"
        assert panel._overview.activity_action == "video"
        first = app._remote_session
        app.window.session_strip.launch_audio_requested.emit()
        drain(qapp, lambda: not app.audio.stopping)
        assert not app.audio.cleanup_retry_required
        assert app.audio.ended_by_user
        assert panel._overview.phase == "ended"
        assert not panel._overview.activity_enabled

        app.window.session_strip.launch_audio_requested.emit()
        drain(qapp, lambda: not app._remote_host_preparing)
        assert app._remote_session is not first
        assert panel._overview.phase == "waiting"
        assert panel._overview.activity_action == "video"
        assert panel._overview.activity_enabled
        current = app._remote_session
        current.connection_available = True
        current.snapshot = replace(current.snapshot, phase=RemoteSessionPhase.CONNECTED)
        app._on_remote_session_snapshot(current.snapshot, source=current)
        assert panel._overview.phase == "connected"
        assert "no longer" not in panel._overview.connection_label
        assert panel._overview.activity_enabled
    finally:
        drain(qapp, lambda: not app._remote_host_preparing)


def test_failed_quit_keeps_unproved_native_owner_visible(qapp, controllers, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from core.room_state import RoomIdentity
    from core.session_transport import SessionRole
    from services.remote_session_runtime import RemoteSessionSnapshot

    app: ApplicationController = controllers(profile="art", hosting=True)
    owner = SimpleNamespace(
        room_identity=RoomIdentity("owned-room", "private-key"),
        snapshot=RemoteSessionSnapshot(RemoteSessionPhase.CONNECTED, SessionRole.GUEST, 1),
        stop=mock.Mock(return_value=False),
    )
    app._remote_session = owner
    app._room_participant.role = "guest"
    app._room_participant.state = ArtRoomState.CONNECTED
    app._refresh_readiness()
    panel = app.window.art_room_overview
    assert panel._overview.phase == "connected"
    monkeypatch.setattr(QMessageBox, "information", mock.Mock())
    try:
        assert not app.shutdown()
        assert app._remote_session is owner
        assert app._room_participant.state is ArtRoomState.NONE
        assert app._shutdown_cleanup_pending
        assert panel._overview.phase == "cleanup_required"
        assert panel._overview.role_label == "Guest"
        assert "Quit again" in panel._overview.connection_detail
        assert not panel._overview.activity_enabled
        assert not panel._overview.conversation_enabled
        app._open_reference_video = mock.Mock()
        app._show_webex_conversation = mock.Mock()
        panel.activity_requested.emit("video")
        panel.conversation_requested.emit()
        app._open_reference_video.assert_not_called()
        app._show_webex_conversation.assert_not_called()
    finally:
        owner.stop.return_value = True
        assert app.shutdown()

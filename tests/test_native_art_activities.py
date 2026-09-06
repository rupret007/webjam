"""Both host-offered Art activities remain reachable through native room UI."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from shiboken6 import isValid

from core.reference_video import (
    ReferenceVideoFollowState,
    load_reference_video_source,
    session_identity_signer,
)
from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoSessionSnapshot, SharedCanvasSessionSnapshot
from core.settings import AppSettings
from core.shared_canvas import SharedCanvasFollowState
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import RoomBackend, drain, remote
from tests.test_paint_along_guest_journey import JourneyPlayer
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

_CANVAS_PASSWORD = "PRIVATE_ART_CANVAS_PASSWORD"
_CANVAS_CODE = "PRIVATEARTCANVASCODE"
_CANVAS_URL = f"drawpile://studio.example/lesson:{_CANVAS_CODE}?p={_CANVAS_PASSWORD}"
_VIDEO_NAME = "PRIVATE_ART_REFERENCE.mp4"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def native_room(qapp, monkeypatch, tmp_path):
    made = []
    players = []
    launcher = FakeLauncher()
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    def player_factory(parent=None):
        player = JourneyPlayer(parent)
        players.append(player)
        return player

    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player",
        player_factory,
    )

    def create(*, profile="music", installed=True):
        launcher.installed = installed
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            last_creator_profile_key=profile, last_creator_start_key="talk_and_make",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="My work",
        )
        app = ApplicationController(window, settings=settings)
        made.append(app)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.bridge.launch_webex = Mock()
        app.window.flash_message = Mock()
        app.host_peer.publish_reference_video_state = Mock()
        app.host_peer.publish_shared_canvas_state = Mock()
        assert app.accept_invitation(remote())
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        backend = RoomBackend.instances[-1]
        copy = root / _VIDEO_NAME
        copy.write_bytes(b"one controlled local reference; no codec used")
        identity = app._remote_session.room_identity
        signer = session_identity_signer(
            session_id=identity.session_id, session_key=identity.session_key,
        )
        offered = RoomState(
            1, "art", "paint_along",
            reference_video=ReferenceVideoSessionSnapshot(
                generation=1, playback_generation=1, state="paused", shared=True,
                source_display_name=copy.name,
                identity_digest=signer(load_reference_video_source(copy).content_sha256),
                position_s=25.0, duration_s=300.0,
            ),
            shared_canvas=SharedCanvasSessionSnapshot(
                generation=1, shared=True, join_url=_CANVAS_URL,
                server_label="studio.example", session_label="lesson",
            ),
        )
        backend.emit(offered)
        drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
        window.resize(760, 600)
        window.show()
        app._tick_creator_start()
        room = app._room_participant
        return SimpleNamespace(
            app=app, backend=backend, offered=offered, copy=copy,
            launcher=launcher, players=players, saved_profile=profile,
            saved_start="talk_and_make", source=room.native_source,
            room_generation=room.generation, native_generation=room.native_generation,
            identity=identity,
        )

    yield create
    for app in reversed(made):
        qapp.processEvents()
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for player in players:
        assert not isValid(player.surface)


def _show_room(pair, qapp):
    app = pair.app
    if app._shared_canvas_dialog is not None:
        app._shared_canvas_dialog.hide()
    app._on_rail_view_changed("stage")
    app._tick_creator_start()
    qapp.processEvents()
    panel = app.window.art_room_overview
    assert panel.isVisibleTo(app.window)
    return panel


def _button(panel, action):
    overview = panel._overview
    if overview.activity_action == action:
        return panel.activity_button()
    assert overview.secondary_activity_action == action
    return panel.secondary_activity_button()


def _open_activity(pair, qapp, action):
    panel = _show_room(pair, qapp)
    assert action in panel._overview.activity_actions
    button = _button(panel, action)
    assert button.isVisibleTo(pair.app.window) and button.isEnabled()
    panel.ensureWidgetVisible(button)
    qapp.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _assert_private_and_stable(pair, caplog):
    app, room = pair.app, pair.app._room_participant
    assert room.state is ArtRoomState.CONNECTED
    assert room.native_source is pair.source
    assert room.generation == pair.room_generation
    assert room.native_generation == pair.native_generation
    assert app._remote_session.room_identity == pair.identity
    assert app.settings.last_creator_profile_key == pair.saved_profile
    assert app.settings.last_creator_start_key == pair.saved_start
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    app.host_peer.publish_shared_canvas_state.assert_not_called()
    panel = app.window.art_room_overview
    public = repr(app.art_room_state()) + repr(panel._overview) + panel.accessibleDescription()
    for button in (panel.activity_button(), panel.secondary_activity_button()):
        public += button.accessibleName() + button.accessibleDescription()
    public += str(app.window.flash_message.call_args_list) + caplog.text
    for marker in (_CANVAS_PASSWORD, _CANVAS_CODE, _VIDEO_NAME, pair.identity.session_key):
        assert marker not in public


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
def test_native_guest_can_reach_each_offered_activity_without_starting_it(
    native_room, qapp, caplog, profile, installed, hidden,
):
    pair = native_room(profile=profile, installed=installed)
    app = pair.app
    video = app._reference_video
    assert video.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    if hidden:
        _open_activity(pair, qapp, "video")
        assert app._reference_video_dialog._hide_action.isVisible()
        app._reference_video_dialog._hide_action.trigger()
        assert video.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    panel = _show_room(pair, qapp)
    assert set(panel._overview.activity_actions) == {"video", "canvas"}

    _open_activity(pair, qapp, "video")
    video_panel = app._reference_video_dialog
    assert video_panel.isVisibleTo(app.window)
    assert not video_panel._hosting
    assert not video_panel._position.isEnabled()
    _open_activity(pair, qapp, "canvas")
    canvas_panel = app._shared_canvas_dialog
    assert canvas_panel.isVisible()
    assert app._shared_canvas.follow_snapshot.state is (
        SharedCanvasFollowState.READY if installed else SharedCanvasFollowState.NEEDS_DRAWPILE
    )
    assert canvas_panel._chip.isEnabled()
    assert canvas_panel._chip.property("action") == ("open" if installed else "install")
    _open_activity(pair, qapp, "video")
    assert app._reference_video_dialog is video_panel
    assert app._shared_canvas_dialog is canvas_panel
    assert pair.players == []
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    _assert_private_and_stable(pair, caplog)


def test_navigation_preserves_one_explicitly_opened_copy_and_its_local_hide_state(
    native_room, qapp, monkeypatch, caplog,
):
    pair = native_room()
    app = pair.app
    _open_activity(pair, qapp, "video")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(pair.copy), ""))
    QTest.mouseClick(app._reference_video_dialog._open_button, Qt.MouseButton.LeftButton)
    app._tick_reference_video()
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    player = pair.players[0]
    assert player.loads == [pair.copy] and player.muted
    QTest.mouseClick(app._reference_video_dialog._hide_button, Qt.MouseButton.LeftButton)
    app._tick_reference_video()
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    # The host is paused, so this copy has sought once without ever playing.
    assert player.state == "ready" and player.position == 25.0
    stopped_transport = (player.state, player.position, tuple(player.seeks))

    _open_activity(pair, qapp, "canvas")
    assert pair.launcher.joined == []
    QTest.mouseClick(app._shared_canvas_dialog._chip, Qt.MouseButton.LeftButton)
    assert pair.launcher.joined == [_CANVAS_URL]
    _open_activity(pair, qapp, "video")
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    assert len(pair.players) == 1 and player.loads == [pair.copy]
    assert app._reference_video_dialog._attached_surface is None
    assert (player.state, player.position, tuple(player.seeks)) == stopped_transport
    _assert_private_and_stable(pair, caplog)


@pytest.mark.parametrize("withdrawn", ["video", "canvas"])
def test_withdrawal_removes_only_that_route_and_rejects_its_stale_intent(
    native_room, qapp, monkeypatch, caplog, withdrawn,
):
    pair = native_room()
    app = pair.app
    panel = _show_room(pair, qapp)
    assert set(panel._overview.activity_actions) == {"video", "canvas"}
    changed = replace(pair.offered, revision=2, **{
        "reference_video" if withdrawn == "video" else "shared_canvas": (
            ReferenceVideoSessionSnapshot(generation=2) if withdrawn == "video"
            else SharedCanvasSessionSnapshot(generation=2)
        ),
    })
    pair.backend.emit(changed)
    drain(qapp, lambda: app._room_participant.native_state.revision == 2)
    panel = _show_room(pair, qapp)
    remaining = "canvas" if withdrawn == "video" else "video"
    assert panel._overview.activity_actions == (remaining,)
    blocked = Mock()
    monkeypatch.setattr(app, "_open_reference_video" if withdrawn == "video" else "_open_shared_canvas", blocked)
    panel.activity_requested.emit(withdrawn)
    blocked.assert_not_called()
    _open_activity(pair, qapp, remaining)
    assert pair.players == [] and pair.launcher.joined == []
    _assert_private_and_stable(pair, caplog)


def test_native_loss_and_leave_reject_stale_actions_for_both_activities(
    native_room, qapp, monkeypatch,
):
    pair = native_room(profile="art")
    app = pair.app
    panel = _show_room(pair, qapp)
    assert set(panel._overview.activity_actions) == {"video", "canvas"}
    video_open, canvas_open = Mock(), Mock()
    monkeypatch.setattr(app, "_open_reference_video", video_open)
    monkeypatch.setattr(app, "_open_shared_canvas", canvas_open)
    source = app._remote_session
    source.mark_connection_lost(expected_generation=source.snapshot.generation)
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.FAILED)
    assert panel._overview.activity_actions == ()
    for action in ("video", "canvas"):
        panel.activity_requested.emit(action)
    video_open.assert_not_called()
    canvas_open.assert_not_called()

    app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._reference_video_identity() == ("", "", "")
    # Neither a retired native receipt nor a queued activity may reopen a panel.
    pair.backend.emit(replace(pair.offered, revision=3))
    qapp.processEvents()
    for action in ("video", "canvas"):
        panel.activity_requested.emit(action)
    video_open.assert_not_called()
    canvas_open.assert_not_called()
    assert pair.players == [] and pair.launcher.joined == []

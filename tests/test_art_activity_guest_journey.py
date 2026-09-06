"""LAN artists can return to either actual room activity without losing their work."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.art_companion import ArtCompanionProjection
from core.reference_video import ReferenceVideoFollowState
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoSessionSnapshot, SharedCanvasSessionSnapshot
from tests.test_paint_along_guest_journey import (
    _choose,
    _observe,
    _video,
    journey as _journey_fixture,
    qapp as _qapp_fixture,
)
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.theme import load_stylesheet

journey = _journey_fixture
qapp = _qapp_fixture


@pytest.fixture(autouse=True)
def activity_theme(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    yield
    qapp.setStyleSheet(previous)


def _poll(app, qapp, offer):
    owner = app._room_participant.lan_guest
    owner.client.state = lambda *_: offer
    owner.poll_once()
    qapp.processEvents()
    app._tick_creator_start()


def _room_button(app, action):
    panel = app.window.art_room_overview
    view = panel._overview
    assert action in view.activity_actions
    return (panel.activity_button() if action == view.activity_action
            else panel.secondary_activity_button())


def _click(widget, qapp):
    assert widget.isVisible() and widget.isEnabled()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _back_to_room(app, qapp):
    dialog = app._reference_video_dialog
    if dialog is not None and dialog.isVisible():
        _click(dialog._back_button, qapp)
    else:
        app._on_rail_view_changed("stage")
        qapp.processEvents()
    app._tick_creator_start()
    assert app.window.art_room_overview.isVisibleTo(app.window)


def _private_and_local(app, caplog):
    projection = app.art_room_state()
    assert ArtCompanionProjection(**projection.to_public_dict()) == projection
    text = (repr(projection) + repr(app.window.art_room_overview._overview)
            + app.window.art_room_overview.accessibleDescription() + caplog.text)
    for private in ("PRIVATE_INVITATION", "PRIVATE_VIDEO", "PRIVATE_MEDIA_DETAIL"):
        assert private not in text
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    assert app.creator_profile.key == "art"
    assert app.settings.last_creator_profile_key == "music"


@pytest.fixture
def guest(journey, monkeypatch, qapp):
    def create(*, installed=True, video_state="hidden"):
        launcher = FakeLauncher(installed=installed)
        monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
        app, invite, root, players = journey("music")
        path = _video(root / "PRIVATE_VIDEO.mp4", b"synthetic process video")
        coordinator = _observe(app, invite, path)
        if video_state != "needs_file":
            _choose(app, monkeypatch, path)
        if video_state == "hidden":
            _click(app._reference_video_dialog._hide_button, qapp)
            assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
        elif video_state == "attention":
            players[0].fail_on.add("position")
            app._tick_reference_video()
            assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
        offer = replace(app._room_participant.lan_guest.last_state,
            shared_canvas=SharedCanvasSessionSnapshot(
                generation=1, shared=True,
                join_url="drawpile://example.com/lesson?v1&p=PRIVATE_INVITATION",
                server_label="example.com", session_label="lesson",
            ),
        )
        _poll(app, qapp, offer)
        _back_to_room(app, qapp)
        app.window.resize(720, 560)
        qapp.processEvents()
        return app, coordinator, launcher, players, offer
    return create


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("video_state", ["hidden", "needs_file", "attention"])
def test_lan_guest_can_visit_both_offered_panels_without_starting_an_activity(
    guest, qapp, caplog, installed, video_state,
):
    app, coordinator, launcher, players, _ = guest(installed=installed, video_state=video_state)
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    before_loads = sum(len(player.loads) for player in players)
    before_players = len(players)
    before_state = coordinator.follow_snapshot.state
    assert set(app.window.art_room_overview._overview.activity_actions) == {"canvas", "video"}

    _click(_room_button(app, "canvas"), qapp)
    canvas = app._shared_canvas_dialog
    assert canvas is not None and canvas.isVisible()
    assert not launcher.joined and not launcher.host_pages
    canvas.close()
    qapp.processEvents()
    _click(_room_button(app, "video"), qapp)
    assert app._reference_video_dialog.isVisible()
    assert coordinator.follow_snapshot.state is before_state
    assert sum(len(player.loads) for player in players) == before_loads
    assert len(players) == before_players
    if video_state == "hidden":
        assert app._reference_video_dialog._hide_button.text() == "Show video"
        _click(app._reference_video_dialog._hide_button, qapp)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
        assert sum(len(player.loads) for player in players) == before_loads
        assert all(player.muted for player in players)
    _back_to_room(app, qapp)
    assert set(app.window.art_room_overview._overview.activity_actions) == {"canvas", "video"}
    assert room.lan_guest is owner and room.generation == generation
    assert room.state is ArtRoomState.CONNECTED
    _private_and_local(app, caplog)


@pytest.mark.parametrize("withdrawn", ["canvas", "video"])
def test_lan_withdrawal_removes_only_that_route_and_rejects_its_queued_click(
    guest, qapp, caplog, withdrawn,
):
    app, _, launcher, _, offer = guest()
    if withdrawn == "canvas":
        offer = replace(offer, shared_canvas=SharedCanvasSessionSnapshot(generation=2))
    else:
        offer = replace(offer, reference_video=ReferenceVideoSessionSnapshot(generation=2))
    _poll(app, qapp, offer)
    view = app.window.art_room_overview._overview
    remaining = "video" if withdrawn == "canvas" else "canvas"
    assert view.activity_actions == (remaining,)
    assert not app.window.art_room_overview.secondary_activity_button().isVisible()
    video_open = Mock(wraps=app._open_reference_video)
    canvas_open = Mock(wraps=app._open_shared_canvas)
    app._open_reference_video, app._open_shared_canvas = video_open, canvas_open
    app.window.art_room_overview.activity_requested.emit(withdrawn)
    assert video_open.call_count == canvas_open.call_count == 0
    _click(_room_button(app, remaining), qapp)
    assert (video_open.call_count, canvas_open.call_count) == ((1, 0) if remaining == "video" else (0, 1))
    assert not launcher.joined
    _private_and_local(app, caplog)


@pytest.mark.parametrize("terminal", [False, True])
def test_lan_loss_retires_both_routes_until_a_current_room_receipt_returns(
    guest, qapp, caplog, terminal,
):
    app, _, _, _, offer = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    room.lose_lan(owner, generation, terminal)
    view = app.window.art_room_overview._overview
    assert view.phase == ("failed" if terminal else "reconnecting")
    assert view.activity_actions == () and view.conversation_enabled
    app._open_reference_video = Mock()
    app._open_shared_canvas = Mock()
    for action in ("canvas", "video"):
        app.window.art_room_overview.activity_requested.emit(action)
    app._open_reference_video.assert_not_called()
    app._open_shared_canvas.assert_not_called()
    _poll(app, qapp, offer)
    assert set(app.window.art_room_overview._overview.activity_actions) == {"canvas", "video"}
    assert room.lan_guest is owner and room.generation == generation
    _click(_room_button(app, "video"), qapp)
    app._open_reference_video.assert_called_once_with()
    _private_and_local(app, caplog)

"""Actual ApplicationController Paint along actions follow current room evidence."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.reference_video import ReferenceVideoFollowState
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoPlaybackState, ReferenceVideoSessionSnapshot
from tests.test_art_activity_guest_journey import (
    _poll,
    guest as _guest_fixture,
    journey as _journey_fixture,
    qapp as _qapp_fixture,
)
from tests.test_art_room_controller import drain
from tests.test_native_art_activities import native_room as _native_room_fixture

qapp = _qapp_fixture
journey = _journey_fixture
guest = _guest_fixture
native_room = _native_room_fixture


def _open_paint_along(app, qapp):
    app._open_reference_video()
    qapp.processEvents()
    panel = app._reference_video_dialog
    assert panel is not None and panel.isVisible()
    return panel


def _click(widget, qapp):
    assert widget.isVisible() and widget.isEnabled()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _assert_private_and_local(app, caplog):
    public = (
        repr(app.art_room_state())
        + repr(app.window.art_room_overview._overview)
        + str(app.window.flash_message.call_args_list)
        + caplog.text
    )
    panel = getattr(app, "_reference_video_dialog", None)
    if panel is not None and getattr(panel, "_return_button", None) is not None:
        public += panel._return_button.text()
        public += panel._return_button.accessibleName()
        public += panel._return_button.accessibleDescription()
    for marker in (
        "PRIVATE_INVITATION", "PRIVATE_REPLACEMENT", "PRIVATE_VIDEO",
        "PRIVATE_MEDIA_DETAIL", "PRIVATE_LOCAL_CANVAS_NOTES",
    ):
        assert marker not in public
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()


def _assert_waiting_for_room(panel):
    assert panel._headline.text() == "Waiting for the room"
    assert "cannot confirm" in panel._status.text().casefold()
    assert panel._return_button.isVisibleTo(panel) and panel._return_button.isEnabled()
    assert panel._return_button.text() == "Return to room"
    assert not panel._open_button.isVisibleTo(panel)
    assert not panel._hide_button.isVisibleTo(panel)
    assert not panel._close_action.isVisible()
    assert not panel._hide_action.isVisible()
    assert not panel._clock.isVisibleTo(panel)
    assert not panel._position.isVisibleTo(panel)


@pytest.mark.parametrize("terminal", [False, True])
def test_open_lan_paint_along_rejects_queued_follow_after_room_loss(
    guest, qapp, caplog, terminal,
):
    app, coordinator, _, players, _ = guest()
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    panel = _open_paint_along(app, qapp)
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    assert panel._hide_button.text() == "Show video"
    loads = tuple(player.loads for player in players)
    room.lose_lan(owner, generation, terminal)

    panel.hide_requested.emit(False)
    panel.open_local_copy_requested.emit("/tmp/PRIVATE_MEDIA_DETAIL.mp4")
    panel.close_local_copy_requested.emit()

    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    assert coordinator.hidden is True
    assert tuple(player.loads for player in players) == loads
    assert panel is app._reference_video_dialog and panel.isVisible()
    _assert_waiting_for_room(panel)
    assert room.state is (ArtRoomState.FAILED if terminal else ArtRoomState.RECONNECTING)
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("already_following", [False, True])
def test_lan_paint_along_return_preserves_local_copy_and_room_owner(
    guest, qapp, caplog, already_following,
):
    app, coordinator, _, players, _ = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    app.window.session_canvas.set_notes("PRIVATE_LOCAL_CANVAS_NOTES: keep the glaze notes")
    notes = app.window.session_canvas.current_notes()
    panel = _open_paint_along(app, qapp)
    if already_following:
        _click(panel._hide_button, qapp)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    loads = tuple(player.loads for player in players)
    hidden = coordinator.hidden
    room.lose_lan(owner, generation, False)
    _assert_waiting_for_room(panel)

    _click(panel._return_button, qapp)

    assert not panel.isVisible()
    assert app.window.art_room_overview.isVisibleTo(app.window)
    assert app._last_content_key == "stage"
    assert app.window.art_room_overview._overview.phase == "reconnecting"
    assert coordinator.follow_snapshot.state is (
        ReferenceVideoFollowState.FOLLOWING if already_following
        else ReferenceVideoFollowState.HIDDEN
    )
    assert coordinator.hidden is hidden
    assert tuple(player.loads for player in players) == loads
    assert app.window.session_canvas.current_notes() == notes
    assert room.lan_guest is owner and room.generation == generation
    assert room.state is ArtRoomState.RECONNECTING
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("receipt", ["same", "changed", "withdrawn"])
def test_lan_paint_along_panel_uses_only_the_latest_confirmed_room_offer(
    guest, qapp, caplog, receipt,
):
    app, coordinator, _, players, offer = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    panel = _open_paint_along(app, qapp)
    if receipt == "changed":
        _click(panel._hide_button, qapp)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    loads = tuple(player.loads for player in players)
    room.lose_lan(owner, generation, False)
    _assert_waiting_for_room(panel)
    panel.hide_requested.emit(not coordinator.hidden)
    assert coordinator.follow_snapshot.state is (
        ReferenceVideoFollowState.FOLLOWING if receipt == "changed"
        else ReferenceVideoFollowState.HIDDEN
    )
    if receipt == "changed":
        current = replace(offer, reference_video=ReferenceVideoSessionSnapshot(
            generation=2, playback_generation=2, shared=True,
            state=ReferenceVideoPlaybackState.PLAYING,
            source_display_name="PRIVATE_REPLACEMENT.mp4",
            identity_digest="b" * 64,
            position_s=10.0, duration_s=120.0,
        ))
    elif receipt == "withdrawn":
        current = replace(
            offer, reference_video=ReferenceVideoSessionSnapshot(generation=2),
        )
    else:
        current = offer

    _poll(app, qapp, current)

    assert room.state is ArtRoomState.CONNECTED
    assert app._reference_video is coordinator and app._reference_video_dialog is panel
    assert room.lan_guest is owner and room.generation == generation
    assert tuple(player.loads for player in players) == loads
    if receipt == "withdrawn":
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        assert not panel._return_button.isVisibleTo(panel)
        assert not panel._open_button.isVisibleTo(panel)
        assert not panel._hide_button.isVisibleTo(panel)
        panel.hide_requested.emit(False)
        panel.open_local_copy_requested.emit("/tmp/PRIVATE_MEDIA_DETAIL.mp4")
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    elif receipt == "changed":
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
        assert panel._open_button.isVisibleTo(panel) and panel._open_button.isEnabled()
        assert not panel._return_button.isVisibleTo(panel)
        panel.hide_requested.emit(True)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    else:
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
        assert panel._hide_button.isVisibleTo(panel) and panel._hide_button.isEnabled()
        assert panel._hide_button.text() == "Show video"
        assert not panel._return_button.isVisibleTo(panel)
        _click(panel._hide_button, qapp)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    _assert_private_and_local(app, caplog)


def test_reopening_paint_along_during_lan_loss_keeps_recovery_action(
    guest, qapp, caplog,
):
    app, coordinator, _, _, _ = guest()
    room = app._room_participant
    panel = _open_paint_along(app, qapp)
    app._return_to_art_room(panel)
    qapp.processEvents()
    room.lose_lan(room.lan_guest, room.generation, False)

    reopened = _open_paint_along(app, qapp)

    assert reopened is panel
    _assert_waiting_for_room(panel)
    panel.hide_requested.emit(False)
    assert coordinator.hidden is True
    _assert_private_and_local(app, caplog)


def test_native_failed_runtime_rejects_queued_paint_along_follow_before_ui_repaint(
    native_room, qapp, caplog,
):
    pair = native_room(profile="art")
    app = pair.app
    panel = _open_paint_along(app, qapp)
    source = app._remote_session
    generation = app._room_participant.generation
    coordinator = app._reference_video
    before = coordinator.follow_snapshot.state

    assert source.mark_connection_lost(expected_generation=source.snapshot.generation)
    panel.hide_requested.emit(True)
    panel.open_local_copy_requested.emit(str(pair.copy))
    assert coordinator.follow_snapshot.state is before
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.FAILED)
    assert app._remote_session is source
    assert app._room_participant.generation == generation
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


def test_lan_paint_along_follow_checks_expired_observer_before_room_loss_callback(
    guest, qapp, caplog,
):
    app, coordinator, _, players, _ = guest()
    room = app._room_participant
    owner = room.lan_guest
    panel = _open_paint_along(app, qapp)
    loads = tuple(player.loads for player in players)
    assert owner.connection_available
    owner._clock = lambda: owner._last_seen + 5.0
    assert not owner.connection_available
    assert room.state is ArtRoomState.CONNECTED

    panel.hide_requested.emit(False)
    panel.open_local_copy_requested.emit("/tmp/PRIVATE_MEDIA_DETAIL.mp4")

    assert coordinator.hidden is True
    assert tuple(player.loads for player in players) == loads
    _assert_waiting_for_room(panel)
    for _ in range(3):
        app._tick_creator_start()
        app._update_session_hud()
        _assert_waiting_for_room(panel)
    assert room.lan_guest is owner
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("receipt", ["changed", "withdrawn"])
def test_queued_new_lan_video_cannot_use_previous_follow_before_ui_observes_it(
    guest, qapp, caplog, receipt,
):
    app, coordinator, _, players, offer = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    panel = _open_paint_along(app, qapp)
    if receipt == "changed":
        _click(panel._hide_button, qapp)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    loads = tuple(player.loads for player in players)
    current = replace(offer, reference_video=(
        ReferenceVideoSessionSnapshot(
            generation=2, playback_generation=2, shared=True,
            state=ReferenceVideoPlaybackState.PLAYING,
            source_display_name="PRIVATE_REPLACEMENT.mp4",
            identity_digest="b" * 64,
            position_s=10.0, duration_s=120.0,
        ) if receipt == "changed" else ReferenceVideoSessionSnapshot(generation=2)
    ))
    owner.client.state = lambda *_: current
    owner.poll_once()
    assert owner.last_state is current
    hidden = coordinator.hidden
    panel.hide_requested.emit(not hidden)
    panel.open_local_copy_requested.emit("/tmp/PRIVATE_MEDIA_DETAIL.mp4")
    assert coordinator.hidden is hidden
    assert tuple(player.loads for player in players) == loads
    qapp.processEvents()
    assert room.state is ArtRoomState.CONNECTED
    assert room.lan_guest is owner and room.generation == generation
    if receipt == "changed":
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
        assert panel._open_button.isVisibleTo(panel)
        assert not panel._return_button.isVisibleTo(panel)
    else:
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
        assert not panel._open_button.isVisibleTo(panel)
        assert not panel._hide_button.isVisibleTo(panel)
        panel.hide_requested.emit(False)
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    _assert_private_and_local(app, caplog)


def test_replaced_paint_along_panel_cannot_hide_or_return_the_current_panel(
    guest, qapp, caplog,
):
    from shiboken6 import isValid

    app, _, _, _, offer = guest()
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    previous = _open_paint_along(app, qapp)
    app._release_reference_video()
    current_coordinator = app._reference_video_coordinator()
    current_coordinator.observe_host_state(offer)
    app._open_reference_video()
    current = app._reference_video_dialog
    assert current is not previous
    if isValid(previous):
        previous.hide_requested.emit(True)
        previous.return_requested.emit()
        previous.open_local_copy_requested.emit("/tmp/PRIVATE_MEDIA_DETAIL.mp4")
    qapp.processEvents()
    assert app._reference_video_dialog is current and current.isVisible()
    assert app._reference_video is current_coordinator
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    assert current_coordinator.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert current._open_button.isVisibleTo(current) and current._open_button.isEnabled()
    _assert_private_and_local(app, caplog)


def test_paint_along_connection_recovery_stays_readable_and_keyboard_reachable_at_760(
    guest, qapp, caplog,
):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QLabel
    from webjam_qt.theme import load_stylesheet

    app, coordinator, _, _, _ = guest()
    app.window.setStyleSheet(load_stylesheet())
    app.window.resize(760, 600)
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    panel = _open_paint_along(app, qapp)
    panel.setStyleSheet(load_stylesheet())
    room.lose_lan(owner, generation, False)
    qapp.processEvents()
    qapp.processEvents()
    before = (
        panel._headline.text(), panel._status.text(), panel._return_button.text(),
    )
    for _ in range(3):
        app._tick_creator_start()
        app._update_session_hud()
    assert (
        panel._headline.text(), panel._status.text(), panel._return_button.text(),
    ) == before
    _assert_waiting_for_room(panel)
    assert panel.width() <= 760 and panel.height() <= 600
    button_rect = QRect(
        panel._return_button.mapTo(panel, QPoint()), panel._return_button.size(),
    )
    assert panel.rect().contains(button_rect)
    assert not panel._return_button.visibleRegion().isEmpty()
    assert panel._return_button.height() >= 48
    for label in panel.findChildren(QLabel):
        if label.isVisibleTo(panel) and label.text():
            assert label.height() >= label.heightForWidth(label.width())
    panel._return_button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(panel._return_button, Qt.Key.Key_Space)
    qapp.processEvents()
    assert not panel.isVisible()
    assert app.window.art_room_overview.isVisibleTo(app.window)
    assert coordinator.hidden is True
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    _assert_private_and_local(app, caplog)


def test_paint_along_does_not_auto_open_while_the_room_is_unconfirmed(
    guest, qapp, caplog,
):
    from core.reference_video import ReferenceVideoFollowSnapshot

    app, _, _, _, _ = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    panel = app._reference_video_dialog
    if panel is not None and panel.isVisible():
        app._return_to_art_room(panel)
        qapp.processEvents()
    room.lose_lan(owner, generation, False)
    app._announced_creator_start = ()
    app._maybe_open_paint_along(
        ReferenceVideoFollowSnapshot(state=ReferenceVideoFollowState.NEEDS_FILE),
    )
    qapp.processEvents()
    current = app._reference_video_dialog
    assert current is None or not current.isVisible()
    assert room.lan_guest is owner and room.generation == generation
    _assert_private_and_local(app, caplog)

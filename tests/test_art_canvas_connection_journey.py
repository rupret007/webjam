"""Actual ApplicationController canvas actions follow current room evidence."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.session_conductor import ArtRoomState
from core.session_transfer import SharedCanvasSessionSnapshot
from tests.test_art_activity_guest_journey import (
    _poll,
    guest as _guest_fixture,
    journey as _journey_fixture,
    qapp as _qapp_fixture,
)
from tests.test_native_art_activities import native_room as _native_room_fixture
from tests.test_art_room_controller import drain

qapp = _qapp_fixture
journey = _journey_fixture
guest = _guest_fixture
native_room = _native_room_fixture


def _open_canvas_panel(app, qapp):
    app._open_shared_canvas()
    qapp.processEvents()
    panel = app._shared_canvas_dialog
    assert panel is not None and panel.isVisible()
    assert panel._chip.isVisibleTo(panel) and panel._chip.isEnabled()
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
    for marker in (
        "PRIVATE_INVITATION", "PRIVATE_REPLACEMENT", "PRIVATE_VIDEO",
        "PRIVATE_MEDIA_DETAIL", "PRIVATE_LOCAL_CANVAS_NOTES",
    ):
        assert marker not in public
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()


@pytest.mark.parametrize("terminal", [False, True])
def test_open_lan_canvas_panel_rejects_queued_open_after_room_loss(
    guest, qapp, caplog, terminal,
):
    app, _, launcher, _, _ = guest()
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    panel = _open_canvas_panel(app, qapp)
    assert panel._chip.text() == "Open shared canvas"
    room.lose_lan(owner, generation, terminal)

    # A queued signal bypasses button enablement; the dispatcher must check
    # the owner and room receipt again before a real external launch.
    panel.open_canvas_requested.emit()
    assert launcher.joined == []
    assert panel is app._shared_canvas_dialog and panel.isVisible()
    assert panel._chip.text() == "Return to room"
    assert panel._chip.isVisibleTo(panel) and panel._chip.isEnabled()
    assert "cannot confirm" in panel._status.text().casefold()
    assert room.state is (ArtRoomState.FAILED if terminal else ArtRoomState.RECONNECTING)
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("already_opened", [False, True])
def test_lan_canvas_return_action_preserves_existing_work_and_room_owner(
    guest, qapp, caplog, already_opened,
):
    app, _, launcher, _, _ = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    app.window.session_canvas.set_notes("PRIVATE_LOCAL_CANVAS_NOTES: keep shaping the clay")
    notes = app.window.session_canvas.current_notes()
    panel = _open_canvas_panel(app, qapp)
    if already_opened:
        _click(panel._chip, qapp)
        assert len(launcher.joined) == 1
    launches = tuple(launcher.joined)
    room.lose_lan(owner, generation, False)
    assert panel._chip.text() == "Return to room"

    _click(panel._chip, qapp)

    assert not panel.isVisible()
    assert app.window.art_room_overview.isVisibleTo(app.window)
    assert app._last_content_key == "stage"
    assert app.window.art_room_overview._overview.phase == "reconnecting"
    assert tuple(launcher.joined) == launches
    assert app.window.session_canvas.current_notes() == notes
    assert room.lan_guest is owner and room.generation == generation
    assert room.state is ArtRoomState.RECONNECTING
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("receipt", ["same", "changed", "withdrawn"])
def test_lan_canvas_panel_uses_only_the_latest_confirmed_room_offer(
    guest, qapp, caplog, receipt,
):
    app, _, launcher, _, offer = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    panel = _open_canvas_panel(app, qapp)
    coordinator = app._shared_canvas
    room.lose_lan(owner, generation, False)
    assert panel._chip.text() == "Return to room"
    panel.open_canvas_requested.emit()
    assert launcher.joined == []
    if receipt == "changed":
        current = replace(offer, shared_canvas=SharedCanvasSessionSnapshot(
            generation=2, shared=True,
            join_url="drawpile://example.com/new-sculpture?v1&p=PRIVATE_REPLACEMENT",
            server_label="example.com", session_label="new-sculpture",
        ))
    elif receipt == "withdrawn":
        current = replace(offer, shared_canvas=SharedCanvasSessionSnapshot(generation=2))
    else:
        current = offer

    _poll(app, qapp, current)

    assert room.state is ArtRoomState.CONNECTED
    assert app._shared_canvas is coordinator and app._shared_canvas_dialog is panel
    assert room.lan_guest is owner and room.generation == generation
    if receipt == "withdrawn":
        assert not panel._chip.isVisibleTo(panel)
        panel.open_canvas_requested.emit()
        assert launcher.joined == []
    else:
        assert panel._chip.text() == "Open shared canvas"
        _click(panel._chip, qapp)
        assert len(launcher.joined) == 1
        copied_current_offer = launcher.joined[-1] == current.shared_canvas.join_url
        assert copied_current_offer
    _assert_private_and_local(app, caplog)


def test_reopening_canvas_panel_during_lan_loss_keeps_recovery_action(
    guest, qapp, caplog,
):
    app, _, launcher, _, _ = guest()
    room = app._room_participant
    panel = _open_canvas_panel(app, qapp)
    panel.close()
    room.lose_lan(room.lan_guest, room.generation, False)

    reopened = _open_canvas_panel(app, qapp)

    assert reopened is panel
    assert panel._chip.text() == "Return to room"
    panel.open_canvas_requested.emit()
    assert launcher.joined == []
    _assert_private_and_local(app, caplog)


def test_native_failed_runtime_rejects_queued_canvas_open_before_ui_repaint(
    native_room, qapp, caplog,
):
    pair = native_room(profile="art")
    app = pair.app
    panel = _open_canvas_panel(app, qapp)
    source = app._remote_session
    generation = app._room_participant.generation
    assert panel._chip.text() == "Open shared canvas"

    # The owner changes its authoritative state before its queued Qt snapshot
    # retires the old panel. A last click in that window cannot launch it.
    assert source.mark_connection_lost(expected_generation=source.snapshot.generation)
    panel.open_canvas_requested.emit()
    assert pair.launcher.joined == []
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.FAILED)
    assert app._remote_session is source
    assert app._room_participant.generation == generation
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


def test_lan_canvas_open_checks_expired_observer_before_room_loss_callback(
    guest, qapp, caplog,
):
    app, _, launcher, _, offer = guest()
    room = app._room_participant
    owner = room.lan_guest
    panel = _open_canvas_panel(app, qapp)
    assert owner.connection_available
    owner._clock = lambda: owner._last_seen + 5.0
    assert not owner.connection_available
    assert room.state is ArtRoomState.CONNECTED

    panel.open_canvas_requested.emit()

    assert launcher.joined == []
    assert panel._chip.text() == "Return to room"
    for _ in range(3):
        app._tick_creator_start()
        app._update_session_hud()
        assert panel._chip.text() == "Return to room"
    assert room.lan_guest is owner
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


@pytest.mark.parametrize("receipt", ["changed", "withdrawn"])
def test_queued_new_lan_offer_cannot_open_previous_canvas_before_ui_observes_it(
    guest, qapp, caplog, receipt,
):
    app, _, launcher, _, offer = guest()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation
    panel = _open_canvas_panel(app, qapp)
    current = replace(offer, shared_canvas=(
        SharedCanvasSessionSnapshot(
            generation=2, shared=True,
            join_url="drawpile://example.com/current?v1&p=PRIVATE_REPLACEMENT",
            server_label="example.com", session_label="current",
        ) if receipt == "changed" else SharedCanvasSessionSnapshot(generation=2)
    ))
    owner.client.state = lambda *_: current
    owner.poll_once()
    assert owner.last_state is current
    # The authenticated observer already owns the latest receipt. Its queued
    # callback has not yet replaced the follower's previous canvas invitation.
    panel.open_canvas_requested.emit()
    assert launcher.joined == []
    qapp.processEvents()
    assert room.state is ArtRoomState.CONNECTED
    assert room.lan_guest is owner and room.generation == generation
    if receipt == "changed":
        assert panel._chip.text() == "Open shared canvas"
        _click(panel._chip, qapp)
        assert len(launcher.joined) == 1
        opened_current_offer = launcher.joined[-1] == current.shared_canvas.join_url
        assert opened_current_offer
    else:
        assert not panel._chip.isVisibleTo(panel)
        panel.open_canvas_requested.emit()
        assert launcher.joined == []
    _assert_private_and_local(app, caplog)


def test_native_canvas_click_observes_real_backend_loss_during_availability_check(
    native_room, qapp, caplog,
):
    from services.remote_session_runtime import RemoteSessionPhase

    pair = native_room(profile="art")
    app = pair.app
    panel = _open_canvas_panel(app, qapp)
    source = app._remote_session
    assert source.snapshot.phase is RemoteSessionPhase.CONNECTED
    pair.backend.connection_available = False
    assert app._room_participant.state is ArtRoomState.CONNECTED

    panel.open_canvas_requested.emit()

    assert pair.launcher.joined == []
    assert source.snapshot.phase is RemoteSessionPhase.FAILED
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.FAILED)
    assert app._remote_session is source
    assert not app.audio.stopping and not app.audio.cleanup_retry_required
    _assert_private_and_local(app, caplog)


def test_replaced_canvas_panel_cannot_open_or_hide_the_current_panel(
    guest, qapp, caplog,
):
    from shiboken6 import isValid

    app, _, launcher, _, offer = guest()
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    previous = _open_canvas_panel(app, qapp)
    app._release_shared_canvas()
    current_coordinator = app._shared_canvas_coordinator()
    current_coordinator.observe_host_state(offer)
    app._open_shared_canvas()
    current = app._shared_canvas_dialog
    assert current is not previous
    # A retired QObject may have queued intents until deferred deletion runs.
    # If Qt already deleted it, those intents are already inert by ownership.
    if isValid(previous):
        previous.open_canvas_requested.emit()
        previous.return_requested.emit()
    assert launcher.joined == []
    qapp.processEvents()
    assert app._shared_canvas_dialog is current and current.isVisible()
    assert app._shared_canvas is current_coordinator
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    _click(current._chip, qapp)
    assert len(launcher.joined) == 1
    _assert_private_and_local(app, caplog)


def test_canvas_connection_recovery_stays_readable_and_keyboard_reachable_at_760(
    guest, qapp, caplog,
):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QLabel
    from webjam_qt.theme import load_stylesheet

    app, _, launcher, _, _ = guest()
    app.window.setStyleSheet(load_stylesheet())
    app.window.resize(760, 600)
    room = app._room_participant
    owner, generation, token = room.lan_guest, room.generation, app.session_conductor.token
    panel = _open_canvas_panel(app, qapp)
    panel.setStyleSheet(load_stylesheet())
    panel.resize(460, panel.sizeHint().height())
    room.lose_lan(owner, generation, False)
    qapp.processEvents()
    qapp.processEvents()
    before = (panel._headline.text(), panel._status.text(), panel._chip.text())
    for _ in range(3):
        app._tick_creator_start()
        app._update_session_hud()
    assert (panel._headline.text(), panel._status.text(), panel._chip.text()) == before
    assert before[-1] == "Return to room"
    assert panel.width() <= 760 and panel.height() <= 600
    button_rect = QRect(panel._chip.mapTo(panel, QPoint()), panel._chip.size())
    assert panel.rect().contains(button_rect)
    assert not panel._chip.visibleRegion().isEmpty()
    assert panel._chip.height() >= 48
    for label in panel.findChildren(QLabel):
        if label.isVisibleTo(panel) and label.text():
            assert label.height() >= label.heightForWidth(label.width())
    assert not panel._room_clock.isVisibleTo(panel)
    panel._chip.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(panel._chip, Qt.Key.Key_Space)
    qapp.processEvents()
    assert not panel.isVisible()
    assert app.window.art_room_overview.isVisibleTo(app.window)
    assert launcher.joined == []
    assert room.lan_guest is owner and room.generation == generation
    assert app.session_conductor.token == token
    _assert_private_and_local(app, caplog)

"""Guest recovery uses real Qt/controller/worker paths with an in-memory peer."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.session_transfer import SessionTransferError
from tests.test_art_lesson_request_journey import no_swallowed_ui_errors  # noqa: F401
from tests.test_art_notes_conversation_journey import (
    qapp as _qapp_fixture,
    room as _room_fixture,
)
from tests.test_art_shared_lesson_journey import _assert_no_handoff, _paint_along
from tests.test_lan_lesson_request_worker import Clock, MemoryPeer

qapp = _qapp_fixture
room = _room_fixture


@pytest.fixture
def guest(room, qapp, monkeypatch):
    person = room(role="lan", profile="art", configured=True)
    app = person.app
    owner = app._room_participant.lan_guest
    clock = Clock()
    peer = MemoryPeer(clock, owner.last_state)
    owner.client = peer
    monkeypatch.setattr(owner, "_clock", clock)
    meeting, browser = Mock(), Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)

    def poll():
        owner.poll_once()
        qapp.processEvents()

    poll()
    dialog = _paint_along(person, qapp)
    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    poll()
    assert owner.lesson_request_state.can_submit
    yield SimpleNamespace(app=app, owner=owner, peer=peer, clock=clock, poll=poll)
    _assert_no_handoff(person, (meeting, browser))


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
@pytest.mark.parametrize("queue_during_poll", [False, True])
def test_brief_poll_failure_names_restart_and_requires_fresh_intent(
    guest, qapp, size, queue_during_poll,
):
    app, owner, peer = guest.app, guest.owner, guest.peer
    room, panel = app._room_participant, app.window.webex_embed
    app.window.resize(*size)
    qapp.processEvents()
    binding = room._lesson_binding
    old_submit = room._lesson_slots[0][1]
    pending = []
    assert room._lesson_current(binding)
    assert not panel.lesson_handoff.restart_button.isVisibleTo(app.window)

    def fail_get(_view):
        # Queue after the worker's send phase: this intent has never been sent.
        if queue_during_poll:
            assert owner.queue_lesson_request("pause")
            pending.append(owner.lesson_request_state)
        raise SessionTransferError("synthetic poll failure")

    peer.get_hook = fail_get
    with pytest.raises(SessionTransferError):
        owner.poll_once()
    assert owner.connection_available  # No room loss or new binding needed.
    qapp.processEvents()
    peer.get_hook = None
    for _ in range(3):
        guest.clock.advance(0.5)
        guest.poll()
        assert not owner.lesson_requests_enabled
        assert not owner.lesson_request_state.can_submit
        assert room._lesson_binding is None

    restart = panel.lesson_handoff.restart_button
    assert restart.isVisibleTo(app.window) and restart.isEnabled()
    assert restart.text() == "Restart pause requests"
    assert not panel._lesson_pause_button.isEnabled()
    assert not panel._lesson_ready_button.isEnabled()
    assert not panel._lesson_retry_button.isEnabled()
    status = panel._lesson_request_guest_status
    assert status.text() == "Restart pause requests, or ask the host aloud."
    assert status.accessibleDescription() == status.text()
    assert peer.commands == []

    # A keyboard click takes the same explicit restart path as the mouse.
    generation = panel.lesson_handoff.generation
    restart.setFocus()
    qapp.processEvents()
    QTest.keyClick(restart, Qt.Key.Key_Space)
    fresh_binding = room._lesson_binding
    assert fresh_binding is not None and fresh_binding is not binding
    assert owner.lesson_requests_enabled
    assert not owner.lesson_request_state.can_submit  # Await a fresh host poll.
    assert not restart.isVisibleTo(app.window)
    guest.poll()
    assert owner.lesson_request_state.can_submit
    assert owner.lesson_request_state.status == "available"
    assert panel._lesson_pause_button.isEnabled()
    assert panel._lesson_ready_button.isEnabled()

    app.follow_along.restart_requests(generation)
    old_submit("pause")
    for state in pending:
        room.receive_lesson_request(owner, room.generation, state)
    guest.poll()
    assert room._lesson_binding is fresh_binding
    assert peer.commands == [] and peer.store.host_notices() == ()
    assert not owner.retry_lesson_request()

    QTest.mouseClick(panel._lesson_ready_button, Qt.MouseButton.LeftButton)
    guest.poll()
    assert len(peer.commands) == 1
    assert peer.commands[0].intent.value == "ready"
    if pending:
        assert peer.commands[0].revision > pending[0].command.revision
    assert owner.lesson_request_state.status == "accepted"


def test_unsupported_room_keeps_aloud_fallback_without_restart(room, qapp):
    person = room(role="native", profile="art", configured=True)
    dialog = _paint_along(person, qapp)
    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    panel = person.app.window.webex_embed
    assert not panel.lesson_handoff.restart_button.isVisibleTo(person.app.window)
    assert panel._lesson_request_guest_status.text() == "Ask the host aloud."
    assert not panel._lesson_pause_button.isEnabled()
    assert not panel._lesson_ready_button.isEnabled()

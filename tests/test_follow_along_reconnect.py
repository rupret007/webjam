"""Guest drop and rejoin during an active shared lesson."""

from __future__ import annotations

import logging
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.session_conductor import ArtRoomState
from tests.support.follow_along_lesson_rig import pause_guest
from tests.test_art_room_controller import drain, state as lan_state
from tests.test_art_shared_lesson_journey import (
    qapp as _qapp_fixture,
    room as _room_fixture,
)

pytest_plugins = ("tests.support.follow_along_lesson_rig",)

qapp = _qapp_fixture
room = _room_fixture
pytestmark = pytest.mark.requires_local_socket


@pytest.fixture(autouse=True)
def no_swallowed_ui_errors(monkeypatch, caplog):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args[0].__name__))
    yield
    assert errors == []
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_guest_rejoin_gets_fresh_admission_without_stale_acknowledgement(lesson_pair, qapp):
    rig = lesson_pair
    rig.enter(rig.host, choose=True)
    rig.enter(rig.guests[0])
    rig.poll()
    lesson_url = rig.host.app.follow_along.panel.lesson_url
    notice = pause_guest(rig, 0, qapp)
    guest_app = rig.guests[0].app
    room = guest_app._room_participant
    owner, generation = rig.owners[0], room.generation
    binding = room._lesson_binding
    old_admission = notice.admission_id

    room.lose_lan(owner, generation, False)
    assert room.state is ArtRoomState.RECONNECTING
    room.receive_lan(owner, generation, lan_state(owner.invite))
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
    rig.clock[0] += 5.1
    rig.poll(owner)
    rig.enter(rig.guests[0])
    rig.poll(owner)
    assert room._lesson_binding is not binding
    assert rig.host.app.follow_along.panel.lesson_url == lesson_url
    assert owner.lesson_request_state.status != "acknowledged"
    assert "acknowledged" not in guest_app.window.webex_embed._lesson_request_guest_status.text().lower()
    view = owner.lesson_request_state.view
    assert view.admission_id != old_admission
    host_notices = rig.server.lesson_request_notices()
    assert all(item.admission_id != old_admission or item.state == "expired" for item in host_notices)
    panel = rig.host.app.window.webex_embed
    key = (notice.context_id, notice.admission_id, notice.revision)
    if key in panel._lesson_request_rows:
        panel.lesson_request_acknowledge.emit(*key)
        rig.poll()
        assert owner.lesson_request_state.status != "acknowledged"

    QTest.mouseClick(guest_app.window.webex_embed._lesson_pause_button, Qt.MouseButton.LeftButton)
    rig.poll(owner)
    assert owner.lesson_request_state.status == "accepted"
    fresh = next(
        item for item in rig.server.lesson_request_notices()
        if item.participant_id == owner._enrollment.participant_id
    )
    assert fresh.admission_id == view.admission_id
    assert fresh.revision >= notice.revision

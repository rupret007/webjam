"""Host with multiple guests: separate pause requests and presence expiry."""

from __future__ import annotations

import logging
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.support.follow_along_lesson_rig import pause_guest
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


def _ready(rig, index, qapp):
    guest = rig.guests[index]
    rig.clock[0] += 2.1
    button = guest.app.window.webex_embed._lesson_ready_button
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    rig.poll(rig.owners[index])
    participant = rig.owners[index]._enrollment.participant_id
    for notice in rig.server.lesson_request_notices():
        if notice.participant_id == participant:
            return notice
    raise AssertionError("ready notice missing")


def test_host_tracks_each_guest_pause_and_acknowledgement_isolated(lesson_multi, qapp):
    rig = lesson_multi
    rig.enter(rig.host, choose=True)
    rig.enter(rig.guests[0])
    rig.enter(rig.guests[1])
    rig.poll()
    pause_a = pause_guest(rig, 0, qapp)
    ready_b = _ready(rig, 1, qapp)
    panel = rig.host.app.window.webex_embed
    assert len(rig.server.lesson_request_notices()) == 2
    assert len(panel._lesson_request_rows) == 2
    key_b = (ready_b.context_id, ready_b.admission_id, ready_b.revision)
    assert key_b in panel._lesson_request_rows
    panel.lesson_request_acknowledge.emit(*key_b)
    rig.poll()
    assert rig.owners[1].lesson_request_state.status == "acknowledged"
    assert rig.owners[0].lesson_request_state.status == "accepted"
    assert pause_a.admission_id != ready_b.admission_id
    key_a = (pause_a.context_id, pause_a.admission_id, pause_a.revision)
    assert key_a in panel._lesson_request_rows
    assert rig.host.app._room_participant.acknowledge_lesson_request(
        rig.host.app._room_participant._lesson_binding, *key_a,
    )


def test_late_guest_gets_current_lesson_without_host_rechoosing(lesson_multi, qapp):
    rig = lesson_multi
    rig.enter(rig.host, choose=True)
    rig.enter(rig.guests[0])
    rig.poll()
    lesson_url = rig.host.app.follow_along.panel.lesson_url
    assert lesson_url
    late, late_owner = rig.join_late()
    assert late.app.follow_along.panel.lesson_url == ""
    assert "YouTube" in late.app.window.webex_embed._mode_label.text()
    assert rig.host.app.follow_along.panel.lesson_url == lesson_url
    assert late_owner.lesson_request_state.can_submit
    assert rig.host.app._room_participant._lesson_binding is not None


def test_departing_guest_presence_expiry_removes_only_that_notice(lesson_multi, qapp):
    rig = lesson_multi
    rig.enter(rig.host, choose=True)
    rig.enter(rig.guests[0])
    rig.enter(rig.guests[1])
    rig.poll()
    pause_a = pause_guest(rig, 0, qapp)
    _ready(rig, 1, qapp)
    owner_a, owner_b = rig.owners[0], rig.owners[1]
    participant_a = owner_a._enrollment.participant_id
    participant_b = owner_b._enrollment.participant_id
    for _ in range(3):
        rig.clock[0] += 2.0
        rig.poll(owner_b)
    notices = rig.server.lesson_request_notices()
    assert participant_a not in {item.participant_id for item in notices}
    assert any(item.participant_id == participant_b for item in notices)
    panel = rig.host.app.window.webex_embed
    assert all(
        key[1] != pause_a.admission_id for key in panel._lesson_request_rows
    )
    assert owner_a.lesson_request_state.status in {"accepted", "inactive", "expired"}

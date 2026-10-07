"""Shared authenticated LAN Art lesson-request rigs for follow-along session tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QInputDialog

from core.session_transfer import (
    EnrollmentRegistry,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionControlState,
    TransferStore,
)
from tests import test_art_notes_conversation_journey as notes_journey
from tests.test_art_shared_lesson_journey import _paint_along

LESSON = "https://www.youtube.com/watch?v=M7lc1UVf-VE"


def wire_server(host, guest_invite, tmp_path, monkeypatch):
    credentials = SessionCredentials(guest_invite.session_id, guest_invite.invite_token)
    host.app.host_peer.credentials = credentials
    root = tmp_path / "authenticated-lesson"
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=EnrollmentRegistry(root, credentials),
        control=SessionControlState(root, credentials.session_id, creator_profile_key="art"),
        transfers=TransferStore(root, credentials.session_id),
    )
    local_address = server.address
    monkeypatch.setattr(
        SessionPeerServer,
        "address",
        property(lambda self: ("192.168.1.20", self._httpd.server_address[1])),
    )
    host.app.host_peer.server = server
    server.start()
    return server, credentials, local_address


def attach_guest(guest, credentials, local_address, clock, monkeypatch):
    owner = guest.app._room_participant.lan_guest
    owner.client = SessionPeerClient(*local_address, credentials=credentials)
    owner._enrollment = None
    monkeypatch.setattr(owner, "_clock", lambda: clock[0])
    return owner


@pytest.fixture
def lesson_pair(room, qapp, monkeypatch, tmp_path):
    """Host plus one guest on a real localhost SessionPeerServer."""
    guest = room(role="lan", profile="art", configured=True)
    host = room(role="host", profile="art", configured=True)
    server, credentials, local_address = wire_server(host, guest.invite, tmp_path, monkeypatch)
    clock = [100.0]
    owner = attach_guest(guest, credentials, local_address, clock, monkeypatch)
    monkeypatch.setattr(server, "_room_poll_clock", lambda: clock[0])
    monkeypatch.setattr(owner, "_clock", lambda: clock[0])
    meeting, browser = Mock(), Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    rig = SimpleNamespace(
        host=host, guests=(guest,), owners=(owner,), server=server, clock=clock,
        credentials=credentials, local_address=local_address,
    )

    def poll(*only_owners):
        targets = only_owners or rig.owners
        for item in targets:
            item.poll_once()
        qapp.processEvents()
        host.app._room_participant.tick()
        qapp.processEvents()

    def enter(person, *, choose=False):
        dialog = _paint_along(person, qapp)
        QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        if choose and person is host:
            monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (LESSON, True))
            app = host.app
            app.follow_along.choose_lesson(app.follow_along.panel.generation)
            qapp.processEvents()

    rig.poll, rig.enter = poll, enter
    try:
        poll()
        yield rig
    finally:
        server.stop()


@pytest.fixture
def lesson_multi(room, qapp, monkeypatch, tmp_path):
    """Host plus two guests sharing one invite; third guest can join late."""
    guest_a = room(role="lan", profile="art", configured=True)
    monkeypatch.setattr(notes_journey, "invitation", lambda: guest_a.invite)
    guest_b = room(role="lan", profile="art", configured=True)
    host = room(role="host", profile="art", configured=True)
    server, credentials, local_address = wire_server(host, guest_a.invite, tmp_path, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(server, "_room_poll_clock", lambda: clock[0])
    owner_a = attach_guest(guest_a, credentials, local_address, clock, monkeypatch)
    owner_b = attach_guest(guest_b, credentials, local_address, clock, monkeypatch)
    meeting, browser = Mock(), Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    rig = SimpleNamespace(
        host=host,
        guests=(guest_a, guest_b),
        owners=(owner_a, owner_b),
        server=server,
        clock=clock,
        credentials=credentials,
        local_address=local_address,
    )

    def poll(*only_owners):
        targets = only_owners or rig.owners
        for item in targets:
            item.poll_once()
        qapp.processEvents()
        host.app._room_participant.tick()
        qapp.processEvents()

    def enter(person, *, choose=False):
        dialog = _paint_along(person, qapp)
        QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        if choose and person is host:
            monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (LESSON, True))
            app = host.app
            app.follow_along.choose_lesson(app.follow_along.panel.generation)
            qapp.processEvents()

    def join_late():
        monkeypatch.setattr(notes_journey, "invitation", lambda: guest_a.invite)
        late = room(role="lan", profile="art", configured=True)
        owner = attach_guest(late, credentials, local_address, clock, monkeypatch)
        rig.guests = (*rig.guests, late)
        rig.owners = (*rig.owners, owner)
        poll(owner)
        enter(late)
        poll(owner)
        return late, owner

    rig.poll, rig.enter, rig.join_late = poll, enter, join_late
    try:
        poll()
        yield rig
    finally:
        server.stop()


def pause_guest(rig, guest_index, qapp):
    guest = rig.guests[guest_index]
    button = guest.app.window.webex_embed._lesson_pause_button
    assert button.isVisibleTo(guest.app.window) and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    owner = rig.owners[guest_index]
    assert owner.lesson_request_state.status == "sending"
    rig.poll(owner)
    assert owner.lesson_request_state.status == "accepted"
    notices = rig.server.lesson_request_notices()
    participant = owner._enrollment.participant_id
    match = [item for item in notices if item.participant_id == participant]
    assert len(match) == 1
    return match[0]

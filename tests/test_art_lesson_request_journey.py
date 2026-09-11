"""Real ApplicationController Art journeys and authenticated LAN requests, without live media."""

from __future__ import annotations

import logging
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from core.lesson_request import LessonRequestCommand, LessonRequestError
from core.session_transfer import (
    EnrollmentRegistry,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    TransferStore,
)
from tests.test_art_conversation_link_journey import (
    _button,
    _drive_room_meeting,
    _raise_dialog_failures,
    _room_meeting_field,
)
from tests.test_art_notes_conversation_journey import (
    _make_notes,
    _notes_state,
    qapp as _qapp_fixture,
    room as _room_fixture,
)
from tests.test_art_shared_lesson_journey import _assert_no_handoff, _paint_along

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


@pytest.fixture
def pair(room, qapp, monkeypatch, tmp_path):
    """Only socket routing and media/device effects are controlled.

    Host/guest Qt controllers, the guest poll method, HTTP authentication,
    domain state and both widgets execute their production paths. The UI sees
    a private address while the actual listener is confined to localhost.
    """
    host = room(role="host", profile="art", configured=True)
    guest = room(role="lan", profile="art", configured=True)
    credentials = SessionCredentials(guest.invite.session_id, guest.invite.invite_token)
    host.app.host_peer.credentials = credentials
    root = tmp_path / "authenticated-lesson"
    server = SessionPeerServer(
        "127.0.0.1", 0,
        registry=EnrollmentRegistry(root, credentials),
        control=SessionControlState(root, credentials.session_id, creator_profile_key="art"),
        transfers=TransferStore(root, credentials.session_id),
    )
    local_address = server.address
    monkeypatch.setattr(SessionPeerServer, "address", property(
        lambda self: ("192.168.1.20", self._httpd.server_address[1]),
    ))
    host.app.host_peer.server = server
    owner = guest.app._room_participant.lan_guest
    owner.client = SessionPeerClient(*local_address, credentials=credentials)
    owner._enrollment = None
    clock = [100.0]
    monkeypatch.setattr(server, "_room_poll_clock", lambda: clock[0])
    monkeypatch.setattr(owner, "_clock", lambda: clock[0])
    meeting, browser = Mock(), Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    server.start()
    rig = SimpleNamespace(host=host, guest=guest, server=server, owner=owner, clock=clock)

    def poll():
        owner.poll_once()
        qapp.processEvents()
        host.app._room_participant.tick()
        qapp.processEvents()

    def enter(person):
        dialog = _paint_along(person, qapp)
        QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

    rig.poll, rig.enter = poll, enter
    try:
        poll()
        enter(host)
        enter(guest)
        poll()
        assert host.app._room_participant._lesson_binding is not None
        assert owner.lesson_request_state.can_submit
        yield rig
        _assert_no_handoff(host, (meeting, browser))
        _assert_no_handoff(guest, (meeting, browser))
    finally:
        # Stop our controlled local listener before the parent room fixture
        # shuts down each real controller. No remote device or media is used.
        server.stop()


def _pause(rig, qapp):
    button = rig.guest.app.window.webex_embed._lesson_pause_button
    assert button.isVisibleTo(rig.guest.app.window) and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert rig.owner.lesson_request_state.status == "sending"
    rig.poll()
    assert rig.owner.lesson_request_state.status == "accepted"
    assert len(rig.server.lesson_request_notices()) == 1
    return rig.server.lesson_request_notices()[0]


def test_guest_pause_host_ack_and_ready_are_human_requests_only(pair, qapp):
    rig = pair
    notes = _make_notes(rig.guest)
    focus = rig.guest.app.window.focusWidget()
    notice = _pause(rig, qapp)
    panel = rig.host.app.window.webex_embed
    key = (notice.context_id, notice.admission_id, notice.revision)
    assert key in panel._lesson_request_rows
    # The actual panel action goes through captured host ownership and the
    # real server. It never changes an external browser or local player.
    panel.lesson_request_acknowledge.emit(*key)
    rig.poll()
    assert rig.owner.lesson_request_state.status == "acknowledged"
    assert "Host acknowledged" in rig.guest.app.window.webex_embed._lesson_request_guest_status.text()
    assert _notes_state(rig.guest.app.window.session_canvas) == notes
    # Choosing Pause may focus that button; passive receipt delivery must not
    # move focus away from the guest's own current control.
    assert focus is not None
    focused_after_pause = rig.guest.app.window.focusWidget()
    rig.poll()
    assert rig.guest.app.window.focusWidget() is focused_after_pause
    rig.clock[0] += 2.1
    QTest.mouseClick(rig.guest.app.window.webex_embed._lesson_ready_button, Qt.MouseButton.LeftButton)
    rig.poll()
    ready = rig.server.lesson_request_notices()[0]
    assert ready.intent.value == "ready" and ready.revision > notice.revision
    assert ready.state == "accepted"
    assert not rig.host.app._room_participant.acknowledge_lesson_request(
        rig.host.app._room_participant._lesson_binding, *key,
    )
    assert rig.server.lesson_request_notices()[0].state == "accepted"


@pytest.mark.parametrize("destination", ["notes", "paint_along", "conversation", "meeting", "same_meeting"])
def test_host_navigation_or_meeting_replacement_retires_old_request_and_ack(pair, qapp, destination):
    notice = _pause(pair, qapp)
    app = pair.host.app
    room = app._room_participant
    binding = room._lesson_binding
    if destination == "notes":
        app.window.side_rail.trigger("canvas")
    elif destination == "paint_along":
        app._open_reference_video()
    elif destination == "conversation":
        app._show_webex_conversation()
    else:
        value = app._effective_meeting_url() if destination == "same_meeting" else "https://studio.webex.com/meet/other"
        app._set_session_meeting_url(value)
    assert room._lesson_binding is None
    assert pair.server.lesson_request_notices() == ()
    if destination in {"meeting", "same_meeting"}:
        panel = app.window.webex_embed
        assert panel._shared_lesson_hosting is True
        assert panel.isVisibleTo(app.window)
        assert room._lesson_slots == []
        assert panel._lesson_request_rows == {}
        assert panel._lesson_request_notices == {}
        pair.poll()
        assert room._lesson_binding is None  # Rendering cannot create a new context.
    assert not room.acknowledge_lesson_request(
        binding, notice.context_id, notice.admission_id, notice.revision,
    )
    with pytest.raises(LessonRequestError, match="context_stale"):
        pair.owner.client.post_lesson_request(pair.owner._enrollment, LessonRequestCommand(
            notice.context_id, notice.admission_id, notice.revision, "pause",
        ))
    pair.enter(pair.host)
    assert room._lesson_binding.context_id != notice.context_id
    pair.poll()
    assert pair.owner.lesson_request_state.view.own_receipt is None


def test_guest_meeting_edit_keeps_guidance_but_retires_only_local_pending_intent(
    pair, qapp, monkeypatch,
):
    notice = _pause(pair, qapp)
    app = pair.guest.app
    room, panel = app._room_participant, app.window.webex_embed
    notes = _make_notes(pair.guest)
    personal_meeting = app.settings.webex_url
    room_generation, room_identity = room.generation, app._reference_video_identity()
    host_binding = pair.host.app._room_participant._lesson_binding
    binding = room._lesson_binding
    identity = (notice.context_id, notice.admission_id)
    old_submit = room._lesson_slots[0][1]
    pair.clock[0] += 2.1
    pair.poll()
    QTest.mouseClick(panel._lesson_ready_button, Qt.MouseButton.LeftButton)
    pending = pair.owner.lesson_request_state
    assert pending.status == "sending" and pending.command.intent.value == "ready"

    replacement = "https://studio.webex.com/meet/replacement-lesson"

    def edit_meeting(modal):
        field = _room_meeting_field(modal)
        field.selectAll()
        QTest.keyClicks(field, replacement)
        QTest.mouseClick(_button(modal, "OK"), Qt.MouseButton.LeftButton)

    observed = _drive_room_meeting(monkeypatch, edit_meeting)
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    assert app._effective_meeting_url() == replacement
    assert panel._shared_lesson_hosting is False and panel.isVisibleTo(app.window)
    assert room._lesson_binding is None and room._lesson_slots == []
    assert pair.owner.lesson_request_state.command is None
    assert not pair.owner.lesson_request_state.can_submit
    assert not panel._lesson_pause_button.isEnabled()
    assert not panel._lesson_ready_button.isEnabled()
    assert not panel._lesson_retry_button.isEnabled()
    assert room.generation == room_generation
    assert app._reference_video_identity() == room_identity
    assert app.settings.webex_url == personal_meeting
    assert _notes_state(app.window.session_canvas) == notes

    # A guest's local edit must not erase the host's accepted pause request,
    # send the abandoned Ready intent, or restore its captured control slots.
    assert not room.submit_lesson_request(binding, identity, "ready")
    old_submit("ready")
    room.receive_lesson_request(pair.owner, room.generation, pending)
    panel.lesson_request_intent.emit("ready")
    pair.poll()
    assert room._lesson_binding is None and room._lesson_slots == []
    assert not pair.owner.lesson_request_state.can_submit
    assert pair.host.app._room_participant._lesson_binding is host_binding
    current = pair.server.lesson_request_notices()
    assert len(current) == 1
    assert current[0].context_id == notice.context_id
    assert current[0].revision == notice.revision and current[0].intent.value == "pause"

    pair.enter(pair.guest)
    pair.poll()
    assert room._lesson_binding is not binding
    assert pair.owner.lesson_request_state.can_submit
    assert pair.server.lesson_request_notices()[0].revision == notice.revision
    QTest.mouseClick(panel._lesson_ready_button, Qt.MouseButton.LeftButton)
    pair.poll()
    fresh = pair.server.lesson_request_notices()[0]
    assert fresh.intent.value == "ready" and fresh.revision > pending.command.revision


def test_host_os_hiding_keeps_notice_without_attention_or_playback_claim(pair, qapp):
    notice = _pause(pair, qapp)
    app = pair.host.app
    binding = app._room_participant._lesson_binding
    app.window.hide()
    pair.poll()
    assert app._room_participant._lesson_binding is binding
    assert pair.server.lesson_request_notices()[0].revision == notice.revision
    assert pair.owner.lesson_request_state.status == "accepted"
    app.window.show()


def test_guest_navigation_cancels_local_intent_and_old_callbacks_without_retiring_host(pair, qapp):
    app = pair.guest.app
    room = app._room_participant
    binding = room._lesson_binding
    identity = (pair.owner.lesson_request_state.view.context_id,
                pair.owner.lesson_request_state.view.admission_id)
    old_submit = room._lesson_slots[0][1]
    QTest.mouseClick(app.window.webex_embed._lesson_pause_button, Qt.MouseButton.LeftButton)
    pending = pair.owner.lesson_request_state
    app.window.side_rail.trigger("canvas")
    assert room._lesson_binding is None
    pair.poll()
    assert pair.server.lesson_request_notices() == ()
    pair.enter(pair.guest)
    pair.poll()
    assert pair.owner.lesson_request_state.can_submit
    assert not room.submit_lesson_request(binding, identity, "pause")
    old_submit("pause")
    room.receive_lesson_request(pair.owner, room.generation, pending)
    pair.poll()
    assert pair.server.lesson_request_notices() == ()
    assert pair.host.app._room_participant._lesson_binding.context_id == identity[0]
    fresh = _pause(pair, qapp)
    assert fresh.revision > pending.command.revision


@pytest.mark.parametrize("role", ["host", "guest"])
def test_failed_leave_retires_request_authority_before_cleanup_wait(pair, qapp, monkeypatch, role):
    notice = _pause(pair, qapp)
    app = getattr(pair, role).app
    room = app._room_participant
    bound = room._lesson_binding
    observations = []

    def hold_stop(*args, **kwargs):
        observations.append(room._lesson_binding)
        if role == "host":
            assert pair.server.lesson_request_notices() == ()
        else:
            assert not pair.owner.lesson_request_state.can_submit
        return False

    owner = app.host_peer if role == "host" else pair.owner
    original_stop = owner.stop
    monkeypatch.setattr(owner, "stop", hold_stop)
    assert not app._stop_session_peer(clear_invite=True)
    assert observations == [None]
    assert room._lesson_binding is None
    if role == "host":
        assert not room.acknowledge_lesson_request(bound, notice.context_id,
                                                notice.admission_id, notice.revision)
    else:
        assert len(pair.server.lesson_request_notices()) == 1
    monkeypatch.setattr(owner, "stop", original_stop)


def test_end_action_retires_host_requests_before_the_cleanup_worker(pair, monkeypatch):
    app = pair.host.app
    observed = []

    class HeldStop:
        def __init__(self, **kwargs):
            observed.append(kwargs["target"])

        def start(self):
            assert app._room_participant._lesson_binding is None
            assert pair.server.lesson_request_notices() == ()

    monkeypatch.setattr("webjam_qt.controllers.audio_coordinator.threading.Thread", HeldStop)
    app.audio._begin_session_stop(True, art_room=True)
    assert len(observed) == 1
    # No worker was run. Restore the explicit cleanup latch so the fixture can
    # perform normal real-controller shutdown rather than pretending End passed.
    app.audio.stopping = False


@pytest.mark.parametrize("scoped_guest", [False, True])
def test_saved_meeting_change_retires_only_effective_host_context(pair, monkeypatch, scoped_guest):
    person = pair.guest if scoped_guest else pair.host
    app = person.app
    if not scoped_guest:
        app._set_session_meeting_url(None)
        pair.enter(person)
    binding = app._room_participant._lesson_binding
    old = replace(app.settings)
    app.settings = replace(app.settings, webex_url="https://studio.webex.com/meet/new-saved")
    monkeypatch.setattr(app, "_start_routing_scan", lambda: None)
    app._reconfigure_services_after_settings(old)
    assert (app._room_participant._lesson_binding is binding) is scoped_guest
    if scoped_guest:
        assert pair.host.app._room_participant._lesson_binding is not None
    else:
        assert pair.server.lesson_request_notices() == ()


def test_route_loss_drops_requests_and_recovery_requires_explicit_host_entry(pair, qapp, monkeypatch):
    _pause(pair, qapp)
    app = pair.host.app
    binding = app._room_participant._lesson_binding
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: "")
    app._room_participant.tick()
    assert app._room_participant._lesson_binding is None
    assert pair.server.lesson_request_notices() == ()
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: "192.168.1.20")
    pair.poll()
    assert app._room_participant._lesson_binding is None
    pair.enter(pair.host)
    assert app._room_participant._lesson_binding.context_id != binding.context_id


def test_expiry_clears_host_action_without_turning_guest_ready_or_paused(pair, qapp):
    notice = _pause(pair, qapp)
    room = pair.host.app._room_participant
    binding = room._lesson_binding
    for _ in range(16):
        pair.clock[0] += 2.0
        pair.poll()
    assert pair.owner.lesson_request_state.status == "expired"
    assert pair.server.lesson_request_notices() == ()
    assert pair.host.app.window.webex_embed._lesson_request_rows == {}
    assert "expired" in pair.guest.app.window.webex_embed._lesson_request_guest_status.text()
    assert not room.acknowledge_lesson_request(
        binding, notice.context_id, notice.admission_id, notice.revision,
    )
    assert pair.owner.lesson_request_state.can_submit


def test_shutdown_retires_request_authority_before_any_blocking_cleanup(pair, monkeypatch):
    app = pair.host.app
    checks = []
    original_close = app._quiesce_startup_for_shutdown

    def close(*args, **kwargs):
        checks.append(app._room_participant._lesson_binding)
        assert pair.server.lesson_request_notices() == ()
        return original_close(*args, **kwargs)

    monkeypatch.setattr(app, "_quiesce_startup_for_shutdown", close)
    assert app.shutdown()
    assert checks == [None]

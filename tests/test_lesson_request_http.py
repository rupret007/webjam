"""Actual loopback HTTP authentication and ephemeral request receipt boundaries."""
from __future__ import annotations

import http.client
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

import pytest

from core.lesson_request import LessonRequestCommand, LessonRequestError
from core.network_invite import BandInvite
from core.session_transfer import (
    EnrollmentRegistry, SessionControlState, SessionCredentials, SessionPeerClient,
    SessionPeerServer, SessionStateSnapshot, SessionTransferError,
    TransferAuthenticationError, TransferStore,
)
from tests.test_lesson_request_model import Clock
from services.lan_room_guest import LanRoomGuest, LanRoomTerminalReason

pytestmark = pytest.mark.requires_local_socket


@pytest.fixture
def peer(tmp_path, request):
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    control = SessionControlState(tmp_path, credentials.session_id,
                                  creator_profile_key=getattr(request, "param", "art"))
    server = SessionPeerServer("127.0.0.1", 0, registry=registry, control=control,
                               transfers=TransferStore(tmp_path, credentials.session_id))
    clock = Clock()
    server._room_poll_clock = clock
    server.start()
    client = SessionPeerClient(*server.address, credentials=credentials)
    enrollment = client.enroll(str(uuid.uuid4()), "<b>Potter</b>")
    try:
        yield server, client, enrollment, clock, tmp_path
    finally:
        server.stop()
        assert server.active_handler_count == 0


def admitted(peer):
    server, client, enrollment, _clock, _path = peer
    server.activate_lesson_requests()
    result = client.state_with_lesson_requests(enrollment)
    view = result.lesson_requests
    return LessonRequestCommand(view.context_id, view.admission_id, 1, "pause")


def raw_post(peer, body, *, enrollment=None, token=None, content_type="application/json"):
    server, _client, default, *_ = peer
    enrollment = enrollment or default
    conn = http.client.HTTPConnection(*server.address, timeout=2)
    try:
        conn.request("POST", "/v1/lesson-requests", body=body, headers={
            "Authorization": "Bearer " + (enrollment.participant_token if token is None else token),
            "X-WebJam-Participant": enrollment.participant_id,
            "Content-Type": content_type,
        })
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_real_authenticated_request_ack_and_scoped_get_never_persist(peer):
    server, client, enrollment, _clock, path = peer
    before = {file.name: file.read_bytes() for file in path.iterdir() if file.is_file()}
    inactive = client.state_with_lesson_requests(enrollment)
    assert type(inactive.snapshot) is SessionStateSnapshot
    assert inactive.lesson_requests.reason == "inactive"
    command = admitted(peer)
    accepted = client.post_lesson_request(enrollment, command)
    assert accepted.own_receipt.state == "accepted"
    notices = server.lesson_request_notices()
    assert len(notices) == 1 and notices[0].participant_id == enrollment.participant_id
    names = server.lesson_request_names(frozenset({enrollment.participant_id}))
    assert names == {enrollment.participant_id: "<b>Potter</b>"}
    server.acknowledge_lesson_request(command.context_id, command.admission_id, 1)
    acknowledged = client.state_with_lesson_requests(enrollment)
    assert acknowledged.lesson_requests.own_receipt.state == "acknowledged"
    assert type(client.state(enrollment)) is SessionStateSnapshot
    assert {file.name: file.read_bytes() for file in path.iterdir() if file.is_file()} == before


def test_enrollment_without_fresh_state_has_no_admission(peer):
    server, client, enrollment, _clock, _path = peer
    context = server.activate_lesson_requests()
    command = LessonRequestCommand(context, "0" * 32, 1, "pause")
    with pytest.raises(LessonRequestError, match="admission_stale"):
        client.post_lesson_request(enrollment, command)
    assert server.room_participants() == frozenset()
    assert server.lesson_request_notices() == ()


def test_other_guest_cannot_use_admission_or_see_own_receipt(peer):
    server, client, first, _clock, _path = peer
    command = admitted(peer)
    client.post_lesson_request(first, command)
    other = client.enroll(str(uuid.uuid4()), "Another guest")
    result = client.state_with_lesson_requests(other)
    assert result.lesson_requests.own_receipt is None
    assert first.participant_id not in json.dumps(result.lesson_requests.to_mapping())
    assert command.admission_id not in json.dumps(result.lesson_requests.to_mapping())
    with pytest.raises(LessonRequestError, match="admission_stale"):
        client.post_lesson_request(other, command)
    with pytest.raises(TransferAuthenticationError):
        client.post_lesson_request(replace(first, participant_token=other.participant_token), command)
    assert len(server.lesson_request_notices()) == 1


@pytest.mark.parametrize("token", ["", "secret-invalid-bearer"])
def test_missing_wrong_auth_is_terminal_safe_and_does_not_mutate(peer, token):
    server, _client, _enrollment, *_ = peer
    command = admitted(peer)
    status, payload = raw_post(peer, json.dumps(command.to_mapping()), token=token)
    assert status == 401
    assert payload == {"error": "unauthorized", "message": "Participant authentication failed."}
    assert server.lesson_request_notices() == ()


@pytest.mark.parametrize("body,content_type", [
    ("[1]", "application/json"),
    ("x" * 513, "application/json"),
    ('{"version":1,"version":1}', "application/json"),
    ("{", "application/json"),
    (b"\xff", "application/json"),
    ("{}", "text/plain"),
])
def test_bad_body_is_fixed_safe_400_without_notice(peer, body, content_type):
    server = peer[0]
    admitted(peer)
    status, payload = raw_post(peer, body, content_type=content_type)
    assert status == 400 and payload == {"version": 1, "error": "invalid_request"}
    assert server.lesson_request_notices() == ()


@pytest.mark.parametrize("boundary,code", [("retire", "context_stale"), ("freshness", "presence_stale")])
def test_auth_body_arrival_does_not_bypass_retirement_or_freshness_at_commit(peer, monkeypatch, boundary, code):
    server, client, enrollment, clock, _path = peer
    command = admitted(peer)
    entered, release = threading.Event(), threading.Event()
    original = server._submit_lesson_request

    def held(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(server, "_submit_lesson_request", held)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(client.post_lesson_request, enrollment, command)
        assert entered.wait(1)
        if boundary == "retire":
            server.retire_lesson_requests()
        else:
            clock.now += 5
        release.set()
        with pytest.raises(LessonRequestError, match=code):
            future.result(timeout=2)
    assert server.lesson_request_notices() == ()


def test_accepted_reply_lost_then_get_reconciles_without_resend(peer, monkeypatch):
    server, client, enrollment, _clock, _path = peer
    command = admitted(peer)
    request = client._request
    post_count = 0

    def lose_reply(method, path, **kwargs):
        nonlocal post_count
        result = request(method, path, **kwargs)
        if method == "POST":
            post_count += 1
            raise SessionTransferError("Controlled lost reply.")
        return result

    monkeypatch.setattr(client, "_request", lose_reply)
    with pytest.raises(SessionTransferError, match="Controlled lost reply"):
        client.post_lesson_request(enrollment, command)
    reconciled = client.state_with_lesson_requests(enrollment).lesson_requests
    assert reconciled.own_receipt.revision == 1
    assert reconciled.own_receipt.intent == "pause"
    assert reconciled.own_receipt.state == "accepted"
    assert post_count == 1 and len(server.lesson_request_notices()) == 1


def test_http_rate_limit_is_typed_and_does_not_reject_base_room(peer):
    _server, client, enrollment, _clock, _path = peer
    command = admitted(peer)
    client.post_lesson_request(enrollment, command)
    with pytest.raises(LessonRequestError) as error:
        client.post_lesson_request(enrollment, replace(command, revision=2, intent="ready"))
    assert error.value.code == "rate_limited" and error.value.retry_after_ms == 2000
    assert client.state(enrollment).creator_profile_key == "art"
    assert client.state_with_lesson_requests(enrollment).lesson_requests.next_revision == 2


def test_stop_retires_authority_before_waiting_for_handlers(peer, monkeypatch):
    server, _client, enrollment, *_ = peer
    command = admitted(peer)
    real_begin = server._httpd.begin_shutdown
    observed = []

    def inspect_then_begin():
        observed.append(server._lesson_requests.current_view(enrollment.participant_id).availability)
        with pytest.raises(LessonRequestError, match="context_stale"):
            server._lesson_requests.submit(enrollment.participant_id, command)
        real_begin()

    monkeypatch.setattr(server._httpd, "begin_shutdown", inspect_then_begin)
    server.stop()
    assert observed == ["unavailable"]
    with pytest.raises(LessonRequestError, match="context_stale"):
        server.activate_lesson_requests()


def test_name_lookup_never_waits_or_returns_stale_names(peer):
    server, client, enrollment, clock, _path = peer
    command = admitted(peer)
    client.post_lesson_request(enrollment, command)
    held, release = threading.Event(), threading.Event()

    def hold_registry_write_lock():
        with server.registry._lock:
            held.set()
            assert release.wait(2)

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(hold_registry_write_lock)
        assert held.wait(1)
        assert server.lesson_request_names(frozenset({enrollment.participant_id})) is None
        release.set()
        future.result(timeout=2)
    clock.now += 5
    assert server.lesson_request_names(frozenset({enrollment.participant_id})) == {}


def test_client_old_unknown_and_malformed_extension_keep_snapshot_contract(peer, monkeypatch):
    _server, client, enrollment, *_ = peer
    request = client._request
    payload = request("GET", "/v1/state", token=enrollment.participant_token,
                      participant_id=enrollment.participant_id)
    for optional in [None, {"version": 2}, {"version": 1, "availability": []},
                     {"version": 1, "availability": "active", "secret": "private"}]:
        response = {**payload, "lesson_requests": optional}
        fake = Mock(return_value=response)
        monkeypatch.setattr(client, "_request", fake)
        result = client.state_with_lesson_requests(enrollment)
        assert type(result.snapshot) is SessionStateSnapshot
        assert result.lesson_requests is None
        assert fake.call_count == 1
    monkeypatch.setattr(client, "_request", Mock(return_value={**payload, "session_id": str(uuid.uuid4())}))
    with pytest.raises(SessionTransferError, match="different room"):
        client.state_with_lesson_requests(enrollment)


@pytest.mark.parametrize("peer", ["music"], indirect=True)
def test_music_owner_never_advertises_or_activates(peer):
    server, client, enrollment, *_ = peer
    with pytest.raises(LessonRequestError, match="context_stale"):
        server.activate_lesson_requests()
    result = client.state_with_lesson_requests(enrollment)
    assert result.snapshot.creator_profile_key == "music" and result.lesson_requests is None
    assert server.lesson_request_notices() == ()


def test_host_local_reads_do_not_wait_for_control_state_and_replacement_retires(peer, monkeypatch):
    server, client, enrollment, _clock, path = peer
    command = admitted(peer)
    client.post_lesson_request(enrollment, command)
    snapshot = Mock(side_effect=AssertionError("Host tick waited for durable state."))
    monkeypatch.setattr(server.control, "snapshot", snapshot)
    assert len(server.lesson_request_notices()) == 1
    server.acknowledge_lesson_request(command.context_id, command.admission_id, 1)
    snapshot.assert_not_called()
    server.control = SessionControlState(path / "replacement", client.credentials.session_id,
                                         creator_profile_key="art")
    assert server.lesson_request_notices() == ()
    with pytest.raises(LessonRequestError, match="context_stale"):
        server.acknowledge_lesson_request(command.context_id, command.admission_id, 1)
    assert server._lesson_requests.current_view(enrollment.participant_id).reason == "inactive"


def test_actual_old_host_404_is_feature_only(peer, monkeypatch):
    server, client, enrollment, *_ = peer
    command = admitted(peer)

    def old_host(handler):
        handler._error(404, "not_found", "Unknown WebJam route.")

    monkeypatch.setattr(server._httpd.RequestHandlerClass, "do_POST", old_host)
    with pytest.raises(LessonRequestError, match="unsupported"):
        client.post_lesson_request(enrollment, command)
    assert client.state(enrollment).creator_profile_key == "art"


@pytest.mark.parametrize("receipt", [{}, {"version": 2}, {"version": 1, "availability": "unavailable", "reason": "inactive"}])
def test_malformed_success_receipt_stays_delivery_unconfirmed(peer, monkeypatch, receipt):
    _server, client, enrollment, *_ = peer
    command = admitted(peer)
    monkeypatch.setattr(client, "_request", Mock(return_value=receipt))
    with pytest.raises(SessionTransferError, match="receipt"):
        client.post_lesson_request(enrollment, command)


def response_handler(status, body):
    def respond(handler):
        handler.send_response(status)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    return respond


@pytest.mark.parametrize("status,body", [
    (401, b"not JSON"), (401, b"x" * (65536 + 1)), (404, b"<html>missing</html>"),
])
def test_terminal_auth_and_old_host_fallback_do_not_depend_on_response_body(peer, monkeypatch, status, body):
    server, client, enrollment, *_ = peer
    command = admitted(peer)
    monkeypatch.setattr(server._httpd.RequestHandlerClass, "do_POST", response_handler(status, body))
    if status == 401:
        with pytest.raises(TransferAuthenticationError, match="Participant authentication failed"):
            client.post_lesson_request(enrollment, command)
    else:
        with pytest.raises(LessonRequestError, match="unsupported"):
            client.post_lesson_request(enrollment, command)


@pytest.mark.parametrize("status,payload", [
    (400, {"version": 2, "error": "invalid_request"}),
    (400, {"version": 1, "error": "context_stale"}),
    (409, {"version": 1, "error": "context_stale", "secret": "private"}),
    (409, {"version": True, "error": "context_stale"}),
    (409, {"error": "context_stale"}),
    (409, {"version": 1, "error": "unknown"}),
    (409, {"version": 1, "error": "rate_limited"}),
    (429, {"version": 1, "error": "rate_limited"}),
    (429, {"version": 1, "error": "rate_limited", "retry_after_ms": True}),
    (429, {"version": 1, "error": "rate_limited", "retry_after_ms": 10001}),
])
def test_malformed_feature_refusal_is_unconfirmed_and_never_echoes_body(peer, monkeypatch, status, payload):
    server, client, enrollment, *_ = peer
    command = admitted(peer)
    monkeypatch.setattr(server._httpd.RequestHandlerClass, "do_POST",
                        response_handler(status, json.dumps(payload).encode()))
    with pytest.raises(SessionTransferError) as error:
        client.post_lesson_request(enrollment, command)
    assert str(error.value) == "The lesson request could not be confirmed."
    assert server.lesson_request_notices() == ()


@pytest.mark.parametrize("body", [b"not JSON", b"x" * (65536 + 1)])
def test_new_state_wrapper_classifies_actual_401_before_unreadable_body(peer, monkeypatch, body):
    server, client, enrollment, *_ = peer
    monkeypatch.setattr(server._httpd.RequestHandlerClass, "do_GET", response_handler(401, body))
    with pytest.raises(TransferAuthenticationError) as error:
        client.state_with_lesson_requests(enrollment)
    assert str(error.value) == "Participant authentication failed."


def test_real_guest_observer_stops_on_unreadable_authenticated_get_rejection(peer, monkeypatch):
    server, client, _enrollment, *_ = peer
    monkeypatch.setattr(server._httpd.RequestHandlerClass, "do_GET", response_handler(401, b"not JSON"))
    # Only address construction is adapted: the observer uses the real HTTP
    # client/enrollment/parser, while the socket remains strictly loopback.
    monkeypatch.setattr("services.lan_room_guest.SessionPeerClient", lambda *a, **k: client)
    credentials = client.credentials
    invitation = BandInvite("192.168.1.20", session_id=credentials.session_id,
                            peer_port=server.address[1], invite_token=credentials.invite_token)
    state_received, lost = Mock(), Mock()
    owner = LanRoomGuest(invitation, display_name="Synthetic artist",
                         on_state=state_received, on_loss=lost)
    try:
        owner.set_lesson_requests_enabled(True)
        owner._run()
        assert owner.terminal_reason is LanRoomTerminalReason.INVITATION_REJECTED
        assert not owner.connection_available
        assert not owner.lesson_request_state.can_submit
        state_received.assert_not_called()
        lost.assert_called_once_with(owner, True)
    finally:
        assert owner.stop()

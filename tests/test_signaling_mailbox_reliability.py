"""Deadline and network recovery coverage without external signaling services."""

from __future__ import annotations

import json
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from core import signaling_mailbox as mailbox
from core.remote_invitation import issue_remote_invitation


SIGNAL = b"WJSE" + bytes(range(12))


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


@pytest.fixture
def invitation():
    return issue_remote_invitation(
        "direct-stun",
        allowed_profiles={"direct-stun"},
        host_spki_sha256=bytes(range(32)),
    ).invitation


def answer_body(invitation):
    keys = mailbox.keys_from_invitation(invitation)
    return {
        "payload": mailbox.seal_slot(
            keys["key"], SIGNAL, kind="answer",
            session_reference=keys["session_reference"],
            host_spki_sha256=keys["host_spki_sha256"],
        ),
    }


class Responses:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    def request(self, method, path):
        assert method == "GET"
        self.calls += 1
        return next(self.responses)


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_host_poll_recovers_from_temporary_server_failure(invitation, status):
    clock = Clock()
    transport = Responses((status, {}), (204, {}), (200, answer_body(invitation)))

    result = mailbox.poll_answer(
        invitation, transport=transport, timeout_seconds=60,
        now=clock.now, sleeper=clock.sleep,
    )

    assert result == SIGNAL
    assert transport.calls == 3
    assert clock.sleeps == [10.0, 15.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
def test_terminal_mailbox_responses_are_not_retried(invitation, status):
    clock = Clock()
    transport = Responses((status, {}))

    with pytest.raises(mailbox.MailboxUnavailable):
        mailbox.poll_answer(
            invitation, transport=transport, timeout_seconds=90,
            now=clock.now, sleeper=clock.sleep,
        )

    assert transport.calls == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [204, 503])
def test_poll_never_starts_another_request_at_the_deadline(invitation, status):
    clock = Clock()
    transport = Responses(*[(status, {})] * 4)

    with pytest.raises(mailbox.MailboxUnavailable, match="timed out"):
        mailbox.poll_answer(
            invitation, transport=transport, timeout_seconds=26,
            now=clock.now, sleeper=clock.sleep,
        )

    assert transport.calls == 3
    assert clock.sleeps == [10.0, 15.0, 1.0]
    assert clock.elapsed == 26.0


class HttpResponse:
    status = 200

    def __init__(self, body):
        self.body = body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")

    def close(self):
        self.closed = True


def test_host_poll_recovers_after_http_connection_interruption(invitation, monkeypatch):
    clock = Clock()
    transport = mailbox._HttpTransport("https://mailbox.example.test")
    requests = []

    def urlopen(request, *, timeout):
        requests.append((request.get_method(), timeout, clock.elapsed))
        if len(requests) == 1:
            clock.elapsed += 2.0
            raise URLError("connection reset")
        return HttpResponse(answer_body(invitation))

    def unavailable_websocket(*_args, **_kwargs):
        raise mailbox.MailboxUnavailable("websocket blocked")

    monkeypatch.setattr(mailbox, "urlopen", urlopen)
    monkeypatch.setattr(transport, "_await_ready", unavailable_websocket)

    assert mailbox.poll_answer(
        invitation, transport=transport, timeout_seconds=30,
        now=clock.now, sleeper=clock.sleep,
    ) == SIGNAL
    assert requests == [("GET", 5.0, 0.0), ("GET", 5.0, 12.0)]
    assert clock.sleeps == [10.0]


def test_http_request_timeout_uses_remaining_join_budget(invitation, monkeypatch):
    clock = Clock()
    timeouts = []

    def urlopen(_request, *, timeout):
        timeouts.append(timeout)
        clock.elapsed += timeout
        raise URLError("timed out")

    monkeypatch.setattr(mailbox, "urlopen", urlopen)
    with pytest.raises(mailbox.MailboxUnavailable, match="timed out"):
        mailbox.poll_answer(
            invitation,
            transport=mailbox._HttpTransport("https://mailbox.example.test"),
            timeout_seconds=0.3, now=clock.now, sleeper=clock.sleep,
        )

    assert timeouts == [0.3]
    assert clock.elapsed == 0.3
    assert clock.sleeps == []


@pytest.mark.parametrize("http_error", [False, True])
@pytest.mark.parametrize("failure", [IncompleteRead(b"partial"), URLError("connection reset")])
def test_interrupted_response_body_can_recover(invitation, monkeypatch, http_error, failure):
    clock = Clock()
    transport = mailbox._HttpTransport("https://mailbox.example.test")
    attempts = 0
    interrupted_responses = []

    class InterruptedResponse(HttpResponse):
        def read(self):
            raise failure

    def urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            response = InterruptedResponse({})
            interrupted_responses.append(response)
            if http_error:
                raise HTTPError(request.full_url, 503, "unavailable", {}, response)
            return response
        return HttpResponse(answer_body(invitation))

    def unavailable_websocket(*_args, **_kwargs):
        raise mailbox.MailboxUnavailable("websocket blocked")

    monkeypatch.setattr(mailbox, "urlopen", urlopen)
    monkeypatch.setattr(transport, "_await_ready", unavailable_websocket)
    assert mailbox.poll_answer(
        invitation, transport=transport, timeout_seconds=30,
        now=clock.now, sleeper=clock.sleep,
    ) == SIGNAL
    assert attempts == 2
    assert clock.sleeps == [10.0]
    assert interrupted_responses[0].closed


def test_malformed_http_json_is_terminal(invitation, monkeypatch):
    clock = Clock()
    attempts = 0

    class InvalidResponse(HttpResponse):
        def read(self):
            return b"not-json"

    def urlopen(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        return InvalidResponse({})

    monkeypatch.setattr(mailbox, "urlopen", urlopen)
    with pytest.raises(mailbox.MailboxUnavailable, match="response is not valid"):
        mailbox.poll_answer(
            invitation,
            transport=mailbox._HttpTransport("https://mailbox.example.test"),
            timeout_seconds=30, now=clock.now, sleeper=clock.sleep,
        )
    assert attempts == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
@pytest.mark.parametrize("failure", [IncompleteRead(b"partial"), URLError("connection reset")])
def test_interrupted_error_body_preserves_terminal_status(invitation, monkeypatch, status, failure):
    clock = Clock()
    attempts = 0

    class InterruptedResponse(HttpResponse):
        def read(self):
            raise failure

    response = InterruptedResponse({})

    def urlopen(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, status, "rejected", {}, response)

    monkeypatch.setattr(mailbox, "urlopen", urlopen)
    with pytest.raises(mailbox.MailboxUnavailable):
        mailbox.poll_answer(
            invitation,
            transport=mailbox._HttpTransport("https://mailbox.example.test"),
            timeout_seconds=30, now=clock.now, sleeper=clock.sleep,
        )

    assert attempts == 1
    assert clock.sleeps == []
    assert response.closed


def test_cancellation_during_network_request_discards_the_answer(invitation):
    clock = Clock()
    stopped = False

    class CancelledTransport:
        def request(self, _method, _path):
            nonlocal stopped
            stopped = True
            return 200, answer_body(invitation)

    with pytest.raises(mailbox.MailboxUnavailable, match="stopped"):
        mailbox.poll_answer(
            invitation, transport=CancelledTransport(), should_stop=lambda: stopped,
            now=clock.now, sleeper=clock.sleep,
        )
    assert clock.sleeps == []


def test_cancellation_interrupts_poll_backoff(invitation):
    clock = Clock()
    transport = Responses((204, {}))

    with pytest.raises(mailbox.MailboxUnavailable, match="stopped"):
        mailbox.poll_answer(
            invitation, transport=transport, timeout_seconds=90,
            should_stop=lambda: clock.elapsed >= 0.25,
            now=clock.now, sleeper=clock.sleep,
        )

    assert transport.calls == 1
    assert clock.elapsed == 0.25


@pytest.mark.parametrize("spent, expected_sleeps", [(4.0, [6.0]), (10.0, [])])
def test_websocket_failure_sleeps_only_the_unused_budget(monkeypatch, spent, expected_sleeps):
    clock = Clock()
    transport = mailbox._HttpTransport("https://mailbox.example.test")

    def interrupted(*_args, **_kwargs):
        clock.elapsed += spent
        raise mailbox.MailboxUnavailable("connection dropped")

    monkeypatch.setattr(transport, "_await_ready", interrupted)
    transport.wait_for_slot("/mailbox/test/answer", 10.0, now=clock.now, sleeper=clock.sleep)

    assert clock.elapsed == 10.0
    assert clock.sleeps == expected_sleeps


@pytest.mark.parametrize("cancel", [False, True])
def test_websocket_connect_and_receive_share_one_cancellable_budget(monkeypatch, cancel):
    from websockets.sync import client

    clock = Clock()
    connect_options = {}
    receive_timeouts = []
    closed = False

    class Socket:
        def __enter__(self):
            clock.elapsed += 4.0
            return self

        def __exit__(self, *_args):
            nonlocal closed
            closed = True

        def recv(self, *, timeout):
            receive_timeouts.append(timeout)
            clock.elapsed += timeout
            raise TimeoutError

    def connect(_url, **kwargs):
        connect_options.update(kwargs)
        return Socket()

    monkeypatch.setattr(client, "connect", connect)
    transport = mailbox._HttpTransport("https://mailbox.example.test")
    transport.wait_for_slot(
        "/mailbox/test/answer", 10.0, now=clock.now, sleeper=clock.sleep,
        should_stop=(lambda: clock.elapsed >= 4.25) if cancel else None,
    )

    assert connect_options["open_timeout"] == 5.0
    assert connect_options["close_timeout"] == 0
    assert clock.elapsed == (4.25 if cancel else 10.0)
    assert receive_timeouts and max(receive_timeouts) <= 0.25
    assert clock.sleeps == []
    assert closed


def test_malformed_answer_still_fails_closed_without_retry(invitation):
    clock = Clock()
    transport = Responses((200, {"payload": "invalid"}))

    with pytest.raises(ValueError):
        mailbox.poll_answer(
            invitation, transport=transport, timeout_seconds=90,
            now=clock.now, sleeper=clock.sleep,
        )

    assert transport.calls == 1
    assert clock.sleeps == []

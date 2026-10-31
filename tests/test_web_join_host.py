import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from services.transport_runtime import (
    parse_transport_event,
    TransportProtocolError,
    TransportProcess,
)
from services.web_join_host import (
    WebJoinHost,
    WebJoinMailbox,
    WebJoinUnavailable,
    encode_chunks,
    decode_chunk,
    mailbox_chunk_id,
)

ENVELOPE = b"WJWS1" + bytes(range(256)) * 25


def test_mailbox_matches_browser_crypto_vector():
    vector = json.loads(
        (Path(__file__).resolve().parents[1] / "web/join/test/vectors.json").read_text()
    )
    assert (
        mailbox_chunk_id(
            bytes.fromhex(vector["capability_hex"]),
            bytes.fromhex(vector["session"]),
            "offer",
            0,
        )
        == vector["mailbox_offer_0"]
    )


def test_chunk_boundaries_and_tampering():
    chunks = encode_chunks(ENVELOPE)
    assert len(chunks) == 3
    assert (
        b"".join(decode_chunk(chunk, i, 3)[1] for i, chunk in enumerate(chunks))
        == ENVELOPE
    )
    for bad in (chunks[0] + "=", "!", "AAE", chunks[1]):
        with pytest.raises(WebJoinUnavailable):
            decode_chunk(bad, 0)
    with pytest.raises(TransportProtocolError):
        encode_chunks(b"WJWS1" + b"x" * 8192)


class NoWait:
    def wait(self, _seconds):
        return False

    def is_set(self):
        return False


def test_mailbox_publishes_commit_chunk_last_and_assembles_answer():
    calls = []
    chunks = encode_chunks(ENVELOPE)
    session, capability = b"s" * 32, b"k" * 32
    indices = {mailbox_chunk_id(capability, session, "answer", i): i for i in range(3)}

    def request(method, path, body):
        calls.append((method, path, body))
        if method == "POST":
            return 201, None
        index = indices[path.split("/")[2]]
        return 200, {"payload": chunks[index]}

    mailbox = WebJoinMailbox(capability, session, request=request, cancel=NoWait())
    mailbox.publish_offer(ENVELOPE)
    assert [
        base64.urlsafe_b64decode(body["payload"] + "=" * (-len(body["payload"]) % 4))[0]
        for _, _, body in calls
    ] == [1, 2, 0]
    assert mailbox.poll_answer() == ENVELOPE
    assert all("?client=web" in path for _, path, _ in calls)
    assert all(capability.hex() not in path for _, path, _ in calls)


@pytest.mark.parametrize("status", [301, 403, 429, 503])
def test_mailbox_denial_fails_closed(status):
    mailbox = WebJoinMailbox(
        b"k" * 32, b"s" * 32, request=lambda *_: (status, None), cancel=NoWait()
    )
    with pytest.raises(WebJoinUnavailable):
        mailbox.publish_offer(ENVELOPE)


def test_transport_web_events_remain_fixed_shape():
    raw = {
        "version": 1,
        "id": 1,
        "type": "web_join_offer",
        "code": "ok",
        "state": "host_waiting",
        "generation": 2,
        "expires_at_unix": 1700000300,
        "signal": base64.urlsafe_b64encode(ENVELOPE).rstrip(b"=").decode(),
    }
    event = parse_transport_event(json.dumps(raw).encode() + b"\n")
    assert event.signal == ENVELOPE
    assert event.expires_at_unix == 1700000300
    assert "WJWS1" not in repr(event)
    for key, value in (
        ("url", "https://invalid"),
        ("text", "leak"),
        ("state", "connected"),
        ("generation", True),
        ("id", 0),
        ("expires_at_unix", True),
        ("expires_at_unix", 0),
        ("expires_at_unix", -1),
        ("expires_at_unix", 2**63),
        ("expires_at_unix", "1700000300"),
    ):
        with pytest.raises(TransportProtocolError):
            parse_transport_event(json.dumps({**raw, key: value}).encode() + b"\n")
    del raw["expires_at_unix"]
    with pytest.raises(TransportProtocolError):
        parse_transport_event(json.dumps(raw).encode() + b"\n")


@pytest.mark.parametrize("event", [
    {"id": 1, "type": "web_join_answer_applied", "state": "connecting", "generation": 1},
    {"id": 1, "type": "web_join_closed", "state": "closed", "generation": 1},
    {"id": 0, "type": "web_join_connected", "state": "connected", "generation": 1},
    {"id": 0, "type": "ready", "state": "idle", "build": "dev"},
])
def test_offer_expiry_is_rejected_on_other_event_shapes(event):
    raw = {"version": 1, "code": "ok", **event}
    parse_transport_event(json.dumps(raw).encode() + b"\n")
    for expiry in (0, 1700000090):
        with pytest.raises(TransportProtocolError):
            parse_transport_event(json.dumps({**raw, "expires_at_unix": expiry}).encode() + b"\n")


class FakeBridge:
    port = 23456
    token = b"b" * 32

    def __init__(self, port):
        self.target = port
        self.closed = threading.Event()
        self.started = False

    def start(self):
        self.started = True

    def close(self):
        self.closed.set()


class FakeProcess:
    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.open_args = None

    def start(self):
        self.running = True

    def open_web_join(self, **kwargs):
        self.open_args = kwargs
        return SimpleNamespace(signal=ENVELOPE, expires_at_unix=kwargs["expires_at_unix"])

    def apply_web_join_answer(self, answer, **kwargs):
        assert answer == ENVELOPE

    def stop(self):
        self.running = False


class FakeMailbox:
    def __init__(self, *_args, cancel):
        self.cancel = cancel

    def publish_offer(self, offer):
        assert offer == ENVELOPE

    def poll_answer(self, **kwargs):
        self.cancel.wait(2)
        raise WebJoinUnavailable()


def test_host_is_off_until_opt_in_and_kick_revokes_invite():
    processes, bridges = [], []

    def process_factory(callback):
        process = FakeProcess(callback)
        processes.append(process)
        return process

    def bridge_factory(port):
        bridge = FakeBridge(port)
        bridges.append(bridge)
        return bridge

    host = WebJoinHost(
        process_factory=process_factory,
        bridge_factory=bridge_factory,
        mailbox_factory=FakeMailbox,
    )
    assert host.state == "disabled" and not processes and not bridges
    invite = host.allow(22124, generation=5)
    assert invite.startswith("https://stalemate.band/join/#k=")
    assert "?" not in invite and "&g=5&" in invite
    assert "k=" not in repr(host)
    assert processes[0].open_args["bridge_port"] == bridges[0].port
    host.set_generation(6)
    assert host.state == "closed" and not host.invite
    assert not processes[0].running and bridges[0].closed.is_set()
    with pytest.raises(WebJoinUnavailable):
        host.allow(22124, generation=6)


def test_private_package_invite_uses_its_build_target_and_normal_stop(tmp_path, monkeypatch):
    from core import web_join_target as target

    build = "a" * 40
    manifest = tmp_path / target.MANIFEST_NAME
    manifest.write_bytes(target.build_target_document("private-beta", build))
    monkeypatch.setattr(target.sys, "frozen", True, raising=False)
    monkeypatch.setattr(target, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(target, "build_id", lambda: build)
    process = FakeProcess(None)
    host = WebJoinHost(
        process_factory=lambda _callback: process,
        bridge_factory=FakeBridge,
        mailbox_factory=FakeMailbox,
    )
    invite = host.allow(22124, generation=8)
    parsed = urlsplit(invite)
    assert parsed.scheme == "https"
    assert parsed.netloc == "stalemate-hangout-beta.jeffstory007.workers.dev"
    assert parsed.path == "/join/" and not parsed.query
    assert set(parse_qs(parsed.fragment)) == {"k", "s", "g", "e"}
    assert parse_qs(parsed.fragment)["g"] == ["8"]
    assert invite not in repr(host)
    host.stop()
    assert not host.invite and not process.running


def test_invalid_package_target_cannot_start_bridge_transport_or_capability(tmp_path, monkeypatch):
    from core import web_join_target as target

    monkeypatch.setattr(target.sys, "frozen", True, raising=False)
    monkeypatch.setattr(target, "_manifest_path", lambda: tmp_path / target.MANIFEST_NAME)
    monkeypatch.setattr(target, "build_id", lambda: "a" * 40)

    def unexpected_start(*_args, **_kwargs):
        pytest.fail("invalid routing metadata must fail before a resource is allocated")

    monkeypatch.setattr("services.web_join_host.secrets.token_bytes", unexpected_start)
    host = WebJoinHost(
        process_factory=unexpected_start,
        bridge_factory=unexpected_start,
        mailbox_factory=unexpected_start,
    )
    with pytest.raises(WebJoinUnavailable):
        host.allow(22124)
    assert not host.invite and host.state == "closed"


def test_prepare_failure_closes_owned_bridge_and_sidecar():
    process = FakeProcess(None)

    def fail(**_kwargs):
        raise RuntimeError("private failure")

    process.open_web_join = fail
    bridge = FakeBridge(22124)
    host = WebJoinHost(
        process_factory=lambda _: process, bridge_factory=lambda _: bridge
    )
    with pytest.raises(WebJoinUnavailable, match="unavailable") as error:
        host.allow(22124)
    assert "private failure" not in str(error.value)
    assert not process.running and bridge.closed.is_set() and not host.invite


def test_host_invite_uses_credential_clamped_offer_expiry(monkeypatch):
    monkeypatch.setattr("services.web_join_host.time.time", lambda: 1700000000)
    process, bridge = FakeProcess(None), FakeBridge(22124)

    def open_web_join(**kwargs):
        assert kwargs["expires_at_unix"] == 1700000600
        # The sidecar sealed the offer with the shorter Worker credential TTL.
        return SimpleNamespace(signal=ENVELOPE, expires_at_unix=1700000090)

    process.open_web_join = open_web_join
    host = WebJoinHost(
        process_factory=lambda _: process,
        bridge_factory=lambda _: bridge,
        mailbox_factory=FakeMailbox,
    )
    try:
        invite = host.allow(22124)
        assert parse_qs(urlsplit(invite).fragment)["e"] == ["1700000090"]
    finally:
        host.stop()


@pytest.mark.parametrize("expiry", [None, True, 0, -1, "1700000090", 1700000090.0, 1699999999, 1700000000, 1700000601])
def test_host_rejects_invalid_offer_expiry_before_publication(monkeypatch, expiry):
    monkeypatch.setattr("services.web_join_host.time.time", lambda: 1700000000)
    process, bridge = FakeProcess(None), FakeBridge(22124)
    process.open_web_join = lambda **_: SimpleNamespace(signal=ENVELOPE, expires_at_unix=expiry)
    mailboxes = []

    def mailbox_factory(*args, **kwargs):
        mailbox = FakeMailbox(*args, **kwargs)
        mailboxes.append(mailbox)
        return mailbox

    host = WebJoinHost(
        process_factory=lambda _: process,
        bridge_factory=lambda _: bridge,
        mailbox_factory=mailbox_factory,
    )
    try:
        with pytest.raises(WebJoinUnavailable):
            host.allow(22124)
        assert not mailboxes
        assert not host.invite and not process.running and bridge.closed.is_set()
    finally:
        host.stop()


def test_transport_open_serializes_no_network_authority(monkeypatch):
    monkeypatch.setattr("services.transport_runtime.time.time", lambda: 1700000000)
    process = TransportProcess("/unused", expected_build="dev")
    recorded = {}

    def request(kind, **kwargs):
        recorded.update(kind=kind, **kwargs)
        return SimpleNamespace(event_type="web_join_offer", generation=1, expires_at_unix=1700000090)

    monkeypatch.setattr(process, "_request", request)
    process.open_web_join(
        session_id=b"s" * 32,
        capability=b"c" * 32,
        bridge_port=22125,
        bridge_token=b"t" * 32,
        generation=1,
        expires_at_unix=1700000300,
    )
    assert set(recorded) == {
        "kind",
        "timeout",
        "session_id",
        "capability",
        "bridge_port",
        "bridge_token",
        "generation",
        "expires_at_unix",
    }


@pytest.mark.parametrize("expiry", [True, 0, 1699999999, 1700000000, 1700000301])
def test_transport_open_rejects_expired_or_extended_offer(monkeypatch, expiry):
    monkeypatch.setattr("services.transport_runtime.time.time", lambda: 1700000000)
    process = TransportProcess("/unused", expected_build="dev")
    monkeypatch.setattr(process, "_request", lambda *_args, **_kwargs: SimpleNamespace(
        event_type="web_join_offer", generation=1, expires_at_unix=expiry,
    ))
    with pytest.raises(TransportProtocolError):
        process.open_web_join(
            session_id=b"s" * 32, capability=b"c" * 32,
            bridge_port=22125, bridge_token=b"t" * 32,
            generation=1, expires_at_unix=1700000300,
        )


@pytest.mark.parametrize("failing", ["process", "bridge", "both"])
def test_cleanup_failure_retains_each_failed_owner_until_retry(failing):
    process, bridge = FakeProcess(None), FakeBridge(22124)
    process.running = True
    calls = []
    fail = {"process": failing in {"process", "both"}, "bridge": failing in {"bridge", "both"}}

    def stop_process():
        calls.append("process")
        if fail["process"]:
            raise RuntimeError("private process details")
        process.running = False

    def close_bridge():
        calls.append("bridge")
        if fail["bridge"]:
            raise RuntimeError("private bridge details")
        bridge.closed.set()

    process.stop, bridge.close = stop_process, close_bridge
    host = WebJoinHost()
    host._process, host._bridge = process, bridge
    host._invite = "https://invalid/#private-invite"
    with pytest.raises(WebJoinUnavailable, match="could not stop") as failure:
        host.stop()
    assert "private" not in str(failure.value)
    assert host.state == "cleanup_pending" and not host.invite
    assert calls == ["process", "bridge"]  # Both cleanup attempts must run.
    assert (host._process is process) == fail["process"]
    assert (host._bridge is bridge) == fail["bridge"]
    fail.update(process=False, bridge=False)
    host.stop()
    assert host.state == "closed" and host._process is None and host._bridge is None
    assert not process.running and bridge.closed.is_set()


def test_cleanup_attempts_are_serialized():
    entered, release = threading.Event(), threading.Event()
    process = FakeProcess(None)
    calls = []

    def blocking_stop():
        calls.append(True)
        entered.set()
        assert release.wait(2)

    process.stop = blocking_stop
    host = WebJoinHost()
    host._process = process
    first = threading.Thread(target=host.stop)
    second = threading.Thread(target=host.stop)
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert calls == [True] and host.state == "closed"


def test_prepare_failure_preserves_owner_when_cleanup_fails():
    process, bridge = FakeProcess(None), FakeBridge(22124)
    process.open_web_join = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private invite"))
    process.stop = lambda: (_ for _ in ()).throw(RuntimeError("private stop"))
    host = WebJoinHost(process_factory=lambda _: process, bridge_factory=lambda _: bridge)
    with pytest.raises(WebJoinUnavailable, match="could not stop"):
        host.allow(22124)
    assert host.state == "cleanup_pending" and host._process is process
    assert host._bridge is None and bridge.closed.is_set() and not host.invite
    process.stop = lambda: setattr(process, "running", False)
    host.stop()
    assert host.state == "closed"


def test_cancellation_during_bridge_preparation_cannot_launch_transport():
    entered, release = threading.Event(), threading.Event()
    process, bridge = FakeProcess(None), FakeBridge(22124)
    launches, errors = [], []
    process.start = lambda: launches.append(True)

    def prepare_bridge():
        entered.set()
        assert release.wait(2)

    bridge.start = prepare_bridge
    host = WebJoinHost(process_factory=lambda _: process, bridge_factory=lambda _: bridge)

    def prepare():
        try:
            host.allow(22124)
        except WebJoinUnavailable:
            errors.append(True)

    worker = threading.Thread(target=prepare)
    worker.start()
    assert entered.wait(2)
    cleanup = threading.Thread(target=host.stop)
    cleanup.start()
    assert host._stop.wait(2)
    release.set()
    worker.join(2)
    cleanup.join(2)
    assert not worker.is_alive() and not cleanup.is_alive()
    assert errors == [True] and launches == []
    assert bridge.closed.is_set() and host.state == "closed"


def test_late_connected_event_cannot_resurrect_stopped_state():
    host = WebJoinHost()
    host.stop()
    host._on_event(SimpleNamespace(event_type="web_join_connected"))
    assert host.state == "closed" and not host.invite


def test_mailbox_https_uses_packaged_roots_and_rejects_redirects(monkeypatch):
    import ssl
    from urllib.request import HTTPSHandler
    import services.web_join_host as module

    captured = []

    class Response:
        status = 204
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def read(self, _limit):
            return b""

    def opener(*handlers):
        captured.extend(handlers)
        return SimpleNamespace(open=lambda *_args, **_kwargs: Response())

    monkeypatch.setattr(module, "build_opener", opener)
    mailbox = WebJoinMailbox(b"k" * 32, b"s" * 32)
    assert mailbox._http("GET", "/mailbox/test/offer", None) == (204, None)
    https = next(handler for handler in captured if isinstance(handler, HTTPSHandler))
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.check_hostname
    assert https._context.cert_store_stats()["x509_ca"] > 0
    redirect_type = next(handler for handler in captured if handler is module._NoRedirect)
    assert redirect_type().redirect_request(None, None, 302, "", {}, "https://invalid") is None


def test_mailbox_missing_packaged_roots_never_falls_back(monkeypatch):
    import services.web_join_host as module
    calls = []
    monkeypatch.setattr(module.ssl, "create_default_context", lambda **_kwargs: (_ for _ in ()).throw(OSError("private CA path")))
    monkeypatch.setattr(module, "build_opener", lambda *_args: calls.append(True))
    mailbox = WebJoinMailbox(b"k" * 32, b"s" * 32)
    with pytest.raises(WebJoinUnavailable) as error:
        mailbox._http("GET", "/mailbox/test/offer", None)
    assert "private" not in str(error.value) and calls == []


def test_process_factory_failure_closes_published_bridge_owner():
    bridge = FakeBridge(22124)
    def failed_factory(_callback):
        raise RuntimeError("private process factory details")
    host = WebJoinHost(process_factory=failed_factory, bridge_factory=lambda _: bridge)
    with pytest.raises(WebJoinUnavailable):
        host.allow(22124)
    assert bridge.closed.is_set() and host.state == "closed"
    assert host._bridge is None

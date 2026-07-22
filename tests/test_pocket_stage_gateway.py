"""Security and command-flow tests for the dedicated Pocket Stage gateway."""

from __future__ import annotations

import json
import hashlib
import ssl
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.pocket_stage import (
    MAX_WIRE_MESSAGE_BYTES,
    MobileParticipant,
    MobileParticipantState,
    MobileRecordingState,
    MobileSection,
    MobileSessionProjection,
    PairingCapabilityRegistry,
    PairingCapabilityState,
    PairingClaim,
    PairingScope,
    PocketCommand,
    PocketCommandReceipt,
    PocketCommandRejectionReason,
    PocketCommandRequest,
    PocketCommandStatus,
    PocketStageEnvelope,
    PocketStageMessageKind,
)
from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)
from services.pocket_stage_gateway import (
    PocketStageGateway,
    PocketStageGatewayError,
    PocketStagePairingOffer,
)
from services.pocket_stage_tls import PocketStageTlsError, PocketStageTlsIdentity
from services.pocket_stage_packaged_smoke import (
    SUCCESS_MARKER,
    run_frozen_pocket_stage_smoke,
)


HOST = "127.0.0.1"
PORT = 18443
HOST_HEADER = f"{HOST}:{PORT}"


@dataclass
class _Clock:
    value: float = 1_800_000_000.0

    def __call__(self) -> float:
        return self.value


def _projection(
    *,
    generation: int = 3,
    revision: int = 9,
) -> MobileSessionProjection:
    return MobileSessionProjection(
        generation=generation,
        revision=revision,
        role=SessionRole.HOST,
        phase=SessionConductorPhase.RECORDING,
        primary_action=SessionPrimaryAction.STOP_RECORDING,
        primary_enabled=True,
        recording_state=MobileRecordingState.RECORDING,
        participants=(
            MobileParticipant(
                slot=1,
                label="Guitar",
                fader_level=76,
                pan=50,
                muted=False,
                solo=False,
                is_local=True,
                connection_state=MobileParticipantState.READY,
            ),
        ),
        sections=(
            MobileSection(ordinal=1, label="Verse", start_ms=0, end_ms=20_000),
            MobileSection(
                ordinal=2,
                label="Chorus",
                start_ms=20_000,
                end_ms=40_000,
            ),
        ),
        current_section_ordinal=1,
        cue="Chorus in four bars",
    )


class _GatewayHarness:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.registry = PairingCapabilityRegistry(clock=self.clock)
        self.current = _projection()
        self.handled: list[PocketCommandRequest] = []
        self.gateway = PocketStageGateway(
            snapshot_provider=lambda: self.current,
            command_handler=self._handle,
            host=HOST,
            port=PORT,
            allow_loopback_for_tests=True,
            clock=self.clock,
            pairing_registry=self.registry,
        )

        # Exercise the ASGI application directly.  TLS listener lifecycle has
        # separate tests; these values model state established by start().
        self.gateway._host = HOST
        self.gateway._port = PORT
        self.gateway._session_id = str(uuid.uuid4())
        self.gateway._running = True
        self.gateway._connection_epoch = 1
        self.app = self.gateway._create_app(FastAPI)
        self.client = TestClient(self.app, base_url=f"http://{HOST_HEADER}")

    def close(self) -> None:
        self.gateway._running = False
        self.gateway._connection_epoch += 1
        self.client.close()

    def _handle(
        self,
        request: PocketCommandRequest,
        _scopes: tuple[PairingScope, ...],
        _epoch: int,
        _lease_id: str,
    ) -> PocketCommandReceipt:
        self.handled.append(request)
        return PocketCommandReceipt(
            command_id=request.command_id,
            status=PocketCommandStatus.CONFIRMED,
            generation=self.current.generation,
            revision=self.current.revision + 1,
        )

    def issue(self, *scopes: PairingScope, ttl_seconds: int = 60):
        return self.registry.issue(scopes=scopes, ttl_seconds=ttl_seconds)

    def connect(self):
        return self.client.websocket_connect(
            "/v1/pocket",
            headers={"host": HOST_HEADER},
        )


@pytest.fixture
def harness() -> Iterator[_GatewayHarness]:
    value = _GatewayHarness()
    try:
        yield value
    finally:
        value.close()


def _envelope(
    body: PairingClaim | PocketCommandRequest,
    *,
    sequence: int,
) -> PocketStageEnvelope:
    is_pair = isinstance(body, PairingClaim)
    return PocketStageEnvelope(
        kind=(
            PocketStageMessageKind.PAIR
            if is_pair
            else PocketStageMessageKind.COMMAND
        ),
        message_id=str(uuid.uuid4()),
        generation=0 if is_pair else body.generation,
        sequence=sequence,
        sent_at_unix_ms=1_800_000_000_000,
        body=body,
    )


def _pair_envelope(capability_token: str, *, claim_id: str | None = None) -> str:
    return _envelope(
        PairingClaim(
            capability_token=capability_token,
            claim_id=claim_id or str(uuid.uuid4()),
        ),
        sequence=0,
    ).to_json()


@contextmanager
def _paired(
    harness: _GatewayHarness,
    *scopes: PairingScope,
):
    capability = harness.issue(*scopes)
    with harness.connect() as socket:
        socket.send_text(_pair_envelope(capability.reveal_for_pairing()))
        first = PocketStageEnvelope.from_json(socket.receive_text())
        assert first.kind is PocketStageMessageKind.SNAPSHOT
        yield socket, first


def _mix_request(
    *,
    generation: int = 3,
    revision: int = 9,
    command_id: str | None = None,
) -> PocketCommandRequest:
    return PocketCommandRequest(
        command_id=command_id or str(uuid.uuid4()),
        command=PocketCommand.SET_PARTICIPANT_FADER,
        generation=generation,
        expected_revision=revision,
        arguments={"slot": 1, "fader_level": 64},
    )


def _receive_receipt(socket) -> tuple[PocketStageEnvelope, PocketCommandReceipt]:
    envelope = PocketStageEnvelope.from_json(socket.receive_text())
    assert envelope.kind is PocketStageMessageKind.RECEIPT
    assert isinstance(envelope.body, PocketCommandReceipt)
    return envelope, envelope.body


def _assert_pairing_rejected(harness: _GatewayHarness, raw: str) -> WebSocketDisconnect:
    with harness.connect() as socket:
        socket.send_text(raw)
        with pytest.raises(WebSocketDisconnect) as caught:
            socket.receive_text()
    assert caught.value.code == 1008
    assert caught.value.reason == "Pairing rejected"
    return caught.value


def test_gateway_exposes_only_the_websocket_route(harness: _GatewayHarness) -> None:
    for path in (
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/participants",
        "/diagnostics",
        "/control",
    ):
        response = harness.client.get(path)
        assert response.status_code == 404, path


def test_gateway_refuses_offer_that_would_outlive_ephemeral_certificate() -> None:
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda *_args: (_ for _ in ()).throw(
            AssertionError("no command expected")
        ),
        host=HOST,
        allow_loopback_for_tests=True,
    )
    with gateway._state_lock:
        gateway._running = True
        gateway._host = HOST
        gateway._port = PORT
        gateway._session_id = str(uuid.uuid4())
        gateway._connection_epoch = 1
        gateway._identity = SimpleNamespace(
            fingerprint_sha256="a" * 64,
            not_after_unix=time.time() + 30,
        )

    with pytest.raises(PocketStageGatewayError, match="identity expired"):
        gateway.issue_pairing_offer(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=60,
        )

    assert gateway._offer_capability_ids == set()


def test_gateway_rejects_clock_rollback_extended_pairing_lifetime() -> None:
    clock = _Clock(2_000.0)
    registry = PairingCapabilityRegistry(clock=clock)
    earlier = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=1)
    registry.revoke(earlier.capability_id)
    clock.value = 1_000.0
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda *_args: (_ for _ in ()).throw(
            AssertionError("no command expected")
        ),
        host=HOST,
        allow_loopback_for_tests=True,
        clock=clock,
        pairing_registry=registry,
    )
    with gateway._state_lock:
        gateway._running = True
        gateway._host = HOST
        gateway._port = PORT
        gateway._session_id = str(uuid.uuid4())
        gateway._connection_epoch = 1
        gateway._identity = SimpleNamespace(
            fingerprint_sha256="a" * 64,
            not_after_unix=3_000.0,
        )

    with pytest.raises(PocketStageGatewayError, match="clock changed"):
        gateway.issue_pairing_offer(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=120,
        )

    records = tuple(registry._by_id.values())
    assert len(records) == 2
    assert all(record.state is PairingCapabilityState.REVOKED for record in records)


def test_bound_route_check_retires_changed_or_missing_private_address() -> None:
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda *_args: (_ for _ in ()).throw(
            AssertionError("no command expected")
        ),
    )
    with gateway._state_lock:
        gateway._running = True
        gateway._host = "192.168.4.8"

    with patch(
        "services.pocket_stage_gateway.discover_private_lan_ipv4",
        return_value="192.168.4.8",
    ):
        assert gateway.bound_route_is_current() is True
    with patch(
        "services.pocket_stage_gateway.discover_private_lan_ipv4",
        return_value="192.168.4.9",
    ):
        assert gateway.bound_route_is_current() is False
    with patch(
        "services.pocket_stage_gateway.discover_private_lan_ipv4",
        side_effect=PocketStageTlsError("private route unavailable"),
    ):
        assert gateway.bound_route_is_current() is False


def test_frozen_runtime_path_works_without_console_streams() -> None:
    with tempfile.TemporaryDirectory(prefix="webjam-pocket-smoke-") as directory:
        result_path = Path(directory) / "result.txt"
        with (
            patch.object(sys, "stdout", None),
            patch.object(sys, "stderr", None),
        ):
            assert run_frozen_pocket_stage_smoke(result_path=result_path) == 0
        assert result_path.read_text(encoding="utf-8") == SUCCESS_MARKER + "\n"


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example"},
        {"host": HOST_HEADER, "origin": "https://evil.example"},
    ],
)
def test_websocket_rejects_wrong_host_or_any_browser_origin(
    harness: _GatewayHarness,
    headers: dict[str, str],
) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with harness.client.websocket_connect("/v1/pocket", headers=headers):
            pass
    assert caught.value.code == 1008
    assert caught.value.reason == "Pocket Stage connection rejected"


@pytest.mark.parametrize(
    ("peer", "allow_loopback", "allowed"),
    [
        ("10.1.2.3", False, True),
        ("172.16.2.3", False, True),
        ("192.168.2.3", False, True),
        ("127.0.0.1", False, False),
        ("127.0.0.1", True, True),
        ("0.0.0.0", False, False),
        ("255.255.255.255", False, False),
        ("192.0.2.10", False, False),
        ("198.51.100.10", False, False),
        ("203.0.113.10", False, False),
    ],
)
def test_handshake_peer_must_be_rfc1918_or_explicit_test_loopback(
    peer: str,
    allow_loopback: bool,
    allowed: bool,
) -> None:
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda _request, _scopes, _epoch, _lease: (
            _ for _ in ()
        ).throw(AssertionError("no command expected")),
        host=HOST,
        port=PORT,
        allow_loopback_for_tests=allow_loopback,
    )
    gateway._host = HOST
    gateway._port = PORT
    websocket = SimpleNamespace(
        headers={"host": HOST_HEADER},
        client=SimpleNamespace(host=peer),
    )

    assert gateway._handshake_allowed(websocket) is allowed


def test_invalid_pairing_capability_is_rejected_without_echo(
    harness: _GatewayHarness,
) -> None:
    unknown_secret = "A" * 43
    rejection = _assert_pairing_rejected(
        harness,
        _pair_envelope(unknown_secret),
    )
    assert unknown_secret not in str(rejection)


def test_expired_pairing_capability_is_rejected(harness: _GatewayHarness) -> None:
    capability = harness.issue(PairingScope.OBSERVE, ttl_seconds=1)
    secret = capability.reveal_for_pairing()
    harness.clock.value += 2

    _assert_pairing_rejected(harness, _pair_envelope(secret))

    snapshot = harness.registry.snapshot(capability.capability_id)
    assert snapshot.state is PairingCapabilityState.EXPIRED


def test_one_time_pairing_rejects_replayed_claim(harness: _GatewayHarness) -> None:
    capability = harness.issue(PairingScope.OBSERVE)
    secret = capability.reveal_for_pairing()
    claim_id = str(uuid.uuid4())
    raw = _pair_envelope(secret, claim_id=claim_id)

    with harness.connect() as socket:
        socket.send_text(raw)
        snapshot = PocketStageEnvelope.from_json(socket.receive_text())
        assert snapshot.kind is PocketStageMessageKind.SNAPSHOT

    _assert_pairing_rejected(harness, raw)
    assert (
        harness.registry.snapshot(capability.capability_id).state
        is PairingCapabilityState.CONSUMED
    )


def test_successful_pair_immediately_receives_authoritative_snapshot(
    harness: _GatewayHarness,
) -> None:
    with _paired(harness, PairingScope.OBSERVE) as (_socket, envelope):
        assert envelope.sequence == 1
        assert envelope.generation == harness.current.generation
        assert envelope.body == harness.current
        assert envelope.body.to_dict()["participants"][0]["slot"] == 1
        assert envelope.body.to_dict()["participants"][0]["label"] == "Guitar"
        assert "name" not in json.dumps(envelope.body.to_dict())


def test_pending_handshakes_are_bounded_and_reservation_is_released(
    harness: _GatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.pocket_stage_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "_PAIRING_TIMEOUT_SECONDS", 0.01)
    with harness.connect() as first:
        with pytest.raises(WebSocketDisconnect) as timed_out:
            first.receive_text()
    assert timed_out.value.reason == "Pairing timed out"
    assert harness.gateway._reserved_connections == 0

    harness.gateway._reserved_connections = 1
    with pytest.raises(WebSocketDisconnect) as full:
        with harness.connect():
            pass
    assert full.value.reason == "Pocket Stage connection rejected"


def test_connection_reservation_registers_socket_in_same_transition(
    harness: _GatewayHarness,
) -> None:
    websocket = object()
    event_loop = object()

    epoch = harness.gateway._reserve_connection(  # type: ignore[arg-type]
        websocket,
        event_loop,
    )

    assert epoch == harness.gateway._connection_epoch
    assert harness.gateway._reserved_connections == 1
    assert harness.gateway._active_websockets[id(websocket)] is websocket
    assert harness.gateway._event_loop is event_loop


def test_scope_denial_returns_bounded_receipt_without_dispatch(
    harness: _GatewayHarness,
) -> None:
    with _paired(harness, PairingScope.OBSERVE) as (socket, _snapshot):
        request = _mix_request()
        socket.send_text(_envelope(request, sequence=1).to_json())
        envelope, receipt = _receive_receipt(socket)

    assert envelope.sequence == 2
    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.UNAUTHORIZED
    assert receipt.command_id == request.command_id
    assert harness.handled == []


@pytest.mark.parametrize(
    ("generation", "revision", "reason"),
    [
        (2, 9, PocketCommandRejectionReason.STALE_GENERATION),
        (3, 8, PocketCommandRejectionReason.STALE_REVISION),
    ],
)
def test_stale_generation_or_revision_is_rejected_before_dispatch(
    harness: _GatewayHarness,
    generation: int,
    revision: int,
    reason: PocketCommandRejectionReason,
) -> None:
    with _paired(harness, PairingScope.MIX) as (socket, _snapshot):
        request = _mix_request(generation=generation, revision=revision)
        socket.send_text(_envelope(request, sequence=1).to_json())
        _envelope_value, receipt = _receive_receipt(socket)

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is reason
    assert receipt.generation == harness.current.generation
    assert receipt.revision == harness.current.revision
    assert harness.handled == []


def test_command_receipt_and_identical_duplicate_are_idempotent(
    harness: _GatewayHarness,
) -> None:
    request = _mix_request()
    with _paired(harness, PairingScope.MIX) as (socket, _snapshot):
        socket.send_text(_envelope(request, sequence=1).to_json())
        first_envelope, first = _receive_receipt(socket)

        socket.send_text(_envelope(request, sequence=2).to_json())
        second_envelope, second = _receive_receipt(socket)

    assert first.status is PocketCommandStatus.CONFIRMED
    assert first == second
    assert first_envelope.sequence == 2
    assert second_envelope.sequence == 3
    assert harness.handled == [request]


def test_late_owner_completion_replaces_pending_receipt_without_replay(
    harness: _GatewayHarness,
) -> None:
    request = _mix_request()

    def pending_handler(_request, _scopes, _epoch, _lease_id):
        return PocketCommandReceipt(
            command_id=request.command_id,
            status=PocketCommandStatus.PENDING,
            generation=harness.current.generation,
            revision=harness.current.revision,
        )

    harness.gateway._command_handler = pending_handler
    with _paired(harness, PairingScope.MIX) as (socket, _snapshot):
        socket.send_text(_envelope(request, sequence=1).to_json())
        _pending_envelope, pending = _receive_receipt(socket)
        assert pending.status is PocketCommandStatus.PENDING

        terminal = PocketCommandReceipt(
            command_id=request.command_id,
            status=PocketCommandStatus.ACCEPTED,
            generation=harness.current.generation,
            revision=harness.current.revision,
        )
        harness.gateway.complete_pending_command(terminal)
        _terminal_envelope, received = _receive_receipt(socket)
        assert received == terminal

        socket.send_text(_envelope(request, sequence=2).to_json())
        _duplicate_envelope, duplicate = _receive_receipt(socket)
        assert duplicate == terminal


def test_rate_limited_command_id_stays_terminal_after_window(
    harness: _GatewayHarness,
) -> None:
    with _paired(harness, PairingScope.MIX) as (socket, _snapshot):
        rejected_request = None
        rejected_receipt = None
        for sequence in range(1, 22):
            request = _mix_request()
            socket.send_text(_envelope(request, sequence=sequence).to_json())
            _receipt_envelope, receipt = _receive_receipt(socket)
            if sequence == 21:
                rejected_request = request
                rejected_receipt = receipt

        assert rejected_request is not None
        assert rejected_receipt is not None
        assert rejected_receipt.status is PocketCommandStatus.REJECTED
        assert rejected_receipt.reason is PocketCommandRejectionReason.RATE_LIMITED
        handled_before_retry = len(harness.handled)
        harness.clock.value += 2
        socket.send_text(_envelope(rejected_request, sequence=22).to_json())
        _retry_envelope, retry = _receive_receipt(socket)

    assert retry == rejected_receipt
    assert len(harness.handled) == handled_before_retry


def test_command_sequence_gap_closes_the_connection(harness: _GatewayHarness) -> None:
    with _paired(harness, PairingScope.MIX) as (socket, _snapshot):
        socket.send_text(_envelope(_mix_request(), sequence=2).to_json())
        with pytest.raises(WebSocketDisconnect) as caught:
            socket.receive_text()

    assert caught.value.code == 1008
    assert caught.value.reason == "Sequence mismatch"
    assert harness.handled == []


@pytest.mark.parametrize("malformed_kind", ["oversize", "duplicate-key"])
def test_oversize_and_duplicate_key_json_are_rejected_at_pairing(
    harness: _GatewayHarness,
    malformed_kind: str,
) -> None:
    if malformed_kind == "oversize":
        raw = "{" + ("x" * MAX_WIRE_MESSAGE_BYTES) + "}"
    else:
        capability = harness.issue(PairingScope.OBSERVE)
        raw = _pair_envelope(capability.reveal_for_pairing())
        raw = raw.replace('"version":1', '"version":1,"version":1', 1)

    _assert_pairing_rejected(harness, raw)


def test_pairing_offer_repr_is_redacted_and_qr_has_only_pairing_fields() -> None:
    secret = "S" * 43
    capability_id = "55555555-5555-4555-8555-555555555555"
    offer = PocketStagePairingOffer(
        session_id="66666666-6666-4666-8666-666666666666",
        endpoint="wss://192.168.1.10:18443/v1/pocket",
        certificate_fingerprint_sha256="ab" * 32,
        capability_id=capability_id,
        capability_token=secret,
        expires_at_unix=1_800_000_120.0,
        display_name="Jeff's WebJam",
        scopes=(PairingScope.OBSERVE, PairingScope.MIX),
    )

    for rendered in (repr(offer), str(offer)):
        assert secret not in rendered
        assert offer.endpoint not in rendered
        assert offer.session_id not in rendered
        assert "redacted" in rendered or "private" in rendered

    parsed = urlparse(offer.qr_code_text)
    query = parse_qs(parsed.query, strict_parsing=True)
    assert parsed.scheme == "pocketstage"
    assert parsed.netloc == "pair"
    assert set(query) == {
        "v",
        "session",
        "endpoint",
        "token",
        "fingerprint",
        "expires",
        "name",
    }
    assert query["token"] == [secret]
    assert query["endpoint"] == [offer.endpoint]
    assert query["fingerprint"] == [offer.certificate_fingerprint_sha256]
    assert capability_id not in offer.qr_code_text


def test_pairing_offer_name_is_bounded_by_utf8_bytes_for_swift_parser(
    harness: _GatewayHarness,
) -> None:
    harness.gateway._identity = SimpleNamespace(  # type: ignore[assignment]
        fingerprint_sha256="ab" * 32
    )
    offer = harness.gateway.issue_pairing_offer(display_name="🎸" * 40)
    parsed_name = parse_qs(urlparse(offer.qr_code_text).query)["name"][0]

    assert parsed_name == "🎸" * 16
    assert len(parsed_name.encode("utf-8")) == 64


def test_offer_issued_during_stop_is_revoked_before_it_can_escape(
    harness: _GatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.gateway._identity = SimpleNamespace(  # type: ignore[assignment]
        fingerprint_sha256="ab" * 32
    )
    entered = threading.Event()
    release = threading.Event()
    issued = []
    original_issue = harness.registry.issue

    def blocked_issue(*, scopes, ttl_seconds):
        capability = original_issue(scopes=scopes, ttl_seconds=ttl_seconds)
        issued.append(capability)
        entered.set()
        assert release.wait(timeout=2)
        return capability

    monkeypatch.setattr(harness.registry, "issue", blocked_issue)
    result: dict[str, object] = {}

    def issue_offer() -> None:
        try:
            result["offer"] = harness.gateway.issue_pairing_offer()
        except Exception as exc:  # noqa: BLE001 - asserted below
            result["error"] = exc

    worker = threading.Thread(target=issue_offer)
    worker.start()
    assert entered.wait(timeout=2)
    harness.gateway.stop()
    release.set()
    worker.join(timeout=2)

    assert isinstance(result.get("error"), PocketStageGatewayError)
    assert "offer" not in result
    assert len(issued) == 1
    assert (
        harness.registry.snapshot(issued[0].capability_id).state
        is PairingCapabilityState.REVOKED
    )


def test_concurrent_offer_requests_leave_only_newest_capability_live(
    harness: _GatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.gateway._identity = SimpleNamespace(  # type: ignore[assignment]
        fingerprint_sha256="ab" * 32
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    original_issue = harness.registry.issue
    issue_count = 0

    def controlled_issue(*, scopes, ttl_seconds):
        nonlocal issue_count
        issue_count += 1
        if issue_count == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        return original_issue(scopes=scopes, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(harness.registry, "issue", controlled_issue)
    offers: dict[str, PocketStagePairingOffer] = {}

    def issue_offer(name: str) -> None:
        offers[name] = harness.gateway.issue_pairing_offer()

    first = threading.Thread(target=issue_offer, args=("first",))
    second = threading.Thread(target=issue_offer, args=("second",))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    time.sleep(0.02)
    assert issue_count == 1
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(offers) == 2
    assert (
        harness.registry.snapshot(offers["first"].capability_id).state
        is PairingCapabilityState.REVOKED
    )
    assert (
        harness.registry.snapshot(offers["second"].capability_id).state
        is PairingCapabilityState.ISSUED
    )


def test_failed_start_retains_live_thread_and_tls_identity() -> None:
    class StuckThread:
        joined = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float) -> None:
            assert timeout == 1
            self.joined = True

    class Listener:
        closed = False

        def close(self) -> None:
            self.closed = True

    class Identity:
        cleaned = False

        def cleanup(self) -> None:
            self.cleaned = True

    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda _request, _scopes, _epoch, _lease: (
            _ for _ in ()
        ).throw(AssertionError("no command expected")),
        host=HOST,
        allow_loopback_for_tests=True,
    )
    server = SimpleNamespace(should_exit=False)
    thread = StuckThread()
    listener = Listener()
    identity = Identity()
    gateway._server = server
    gateway._thread = thread  # type: ignore[assignment]
    gateway._listener = listener  # type: ignore[assignment]
    gateway._identity = identity  # type: ignore[assignment]
    gateway._connection_epoch = 4
    gateway._starting = True

    gateway._cleanup_failed_start(  # type: ignore[arg-type]
        listener,
        identity,
        start_epoch=4,
    )

    assert server.should_exit is True
    assert thread.joined is True
    assert listener.closed is True
    assert identity.cleaned is False
    assert gateway._thread is thread
    assert gateway._identity is identity
    with pytest.raises(PocketStageGatewayError, match="still closing"):
        gateway.start()


def test_stop_cancels_start_before_listener_is_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = PocketStageTlsIdentity.create(HOST)
    entered = threading.Event()
    release = threading.Event()

    def blocked_identity(_host: str) -> PocketStageTlsIdentity:
        entered.set()
        assert release.wait(timeout=2)
        return identity

    monkeypatch.setattr(PocketStageTlsIdentity, "create", blocked_identity)
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda _request, _scopes, _epoch, _lease: (
            _ for _ in ()
        ).throw(AssertionError("no command expected")),
        host=HOST,
        port=0,
        allow_loopback_for_tests=True,
    )
    result: dict[str, object] = {}

    def start_gateway() -> None:
        try:
            result["started"] = gateway.start()
        except Exception as exc:  # noqa: BLE001 - asserted below
            result["error"] = exc

    worker = threading.Thread(target=start_gateway)
    worker.start()
    assert entered.wait(timeout=2)
    gateway.stop()
    release.set()
    worker.join(timeout=3)

    assert worker.is_alive() is False
    assert "started" not in result
    assert isinstance(result.get("error"), PocketStageGatewayError)
    assert gateway.running is False
    assert gateway._server is None
    assert gateway._thread is None
    assert gateway._listener is None
    assert identity.directory.exists() is False


def test_claim_consumed_during_stop_never_receives_a_snapshot(
    harness: _GatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = harness.issue(PairingScope.OBSERVE)
    original_consume = harness.registry.consume

    def consume_then_stop(token: str, *, claim_id: str):
        acceptance = original_consume(token, claim_id=claim_id)
        with harness.gateway._state_lock:
            harness.gateway._running = False
            harness.gateway._connection_epoch += 1
        return acceptance

    monkeypatch.setattr(harness.registry, "consume", consume_then_stop)
    with harness.connect() as socket:
        socket.send_text(_pair_envelope(capability.reveal_for_pairing()))
        with pytest.raises(WebSocketDisconnect) as caught:
            socket.receive_text()

    assert caught.value.code == 1001
    assert harness.gateway.connected_clients == 0


def test_live_wss_text_pairing_and_stop_destroy_runtime_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("cryptography")
    websockets = pytest.importorskip("websockets.sync.client")
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda _request, _scopes, _epoch, _lease_id: (
            _ for _ in ()
        ).throw(
            AssertionError("no command expected")
        ),
        host=HOST,
        port=0,
        allow_loopback_for_tests=True,
    )
    connection = None
    runtime_thread = None
    identity_directory = None
    try:
        assert gateway.start() is True
        offer = gateway.issue_pairing_offer(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=60,
        )
        identity_directory = gateway._identity.directory
        runtime_thread = gateway._thread
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = websockets.connect(
            offer.endpoint,
            ssl=context,
            proxy=None,
            open_timeout=3,
        )
        peer_der = connection.socket.getpeercert(binary_form=True)
        assert hashlib.sha256(peer_der).hexdigest() == (
            offer.certificate_fingerprint_sha256
        )
        claim = PairingClaim(
            capability_token=offer.capability_token,
            claim_id=str(uuid.uuid4()),
        )
        pair = PocketStageEnvelope(
            kind=PocketStageMessageKind.PAIR,
            message_id=str(uuid.uuid4()),
            generation=0,
            sequence=0,
            sent_at_unix_ms=1,
            body=claim,
        )
        connection.send(pair.to_json())
        snapshot = PocketStageEnvelope.from_json(connection.recv(timeout=3))
        assert snapshot.kind is PocketStageMessageKind.SNAPSHOT
        heartbeat = PocketStageEnvelope.from_json(connection.recv(timeout=3))
        assert heartbeat.kind is PocketStageMessageKind.SNAPSHOT
        assert heartbeat.sequence == snapshot.sequence + 1
        assert heartbeat.body == snapshot.body

        gateway.stop()

        assert runtime_thread is not None and not runtime_thread.is_alive()
        assert gateway.running is False
        assert gateway._server is None
        assert identity_directory is not None and not identity_directory.exists()
        assert not any(
            "stopped unexpectedly" in record.getMessage()
            for record in caplog.records
        )
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            gateway.stop()

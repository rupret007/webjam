from __future__ import annotations

import json

import pytest

from webjam_reference.config import ServiceConfig
from webjam_reference.protocol import (
    DatagramKind,
    ProtocolError,
    RelayFrame,
    Role,
    derive_relay_key,
    encode_relay,
    parse_relay,
    verify_relay,
)
from webjam_reference.state import SessionRegistry


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


SESSION = b"s" * 32
HOST = b"h" * 32
ENROLLMENT = b"e" * 32
GUEST = b"g" * 32


def registry(clock: Clock | None = None, **changes: object) -> SessionRegistry:
    values = {
        "min_session_ttl_seconds": 1,
        "max_session_ttl_seconds": 20,
        "idle_timeout_seconds": 10,
        "tombstone_ttl_seconds": 20,
    }
    values.update(changes)
    return SessionRegistry(ServiceConfig(**values), clock=clock or Clock())


def register(target: SessionRegistry, session: bytes = SESSION) -> None:
    target.register(session, HOST, ENROLLMENT, 1, 20)


def enroll(target: SessionRegistry, session: bytes = SESSION) -> None:
    target.enroll(session, ENROLLMENT, GUEST)


def test_registration_is_opaque_bounded_and_conflict_safe() -> None:
    target = registry(max_sessions=1)
    register(target)
    with pytest.raises(ProtocolError, match="session_conflict"):
        register(target)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, b"t" * 32)
    diagnostics = target.diagnostics()
    encoded = json.dumps(diagnostics)
    assert diagnostics["sessions"]["active"] == 1
    assert SESSION.hex() not in encoded
    assert HOST.hex() not in encoded
    assert ENROLLMENT.hex() not in encoded


def test_registration_rate_is_globally_bounded() -> None:
    target = registry(registrations_per_second=1, registration_burst=1)
    register(target)
    with pytest.raises(ProtocolError, match="overloaded"):
        target.register(b"t" * 32, b"i" * 32, b"j" * 32, 1, 20)


def test_role_and_enrollment_credentials_cannot_cross_sessions() -> None:
    target = registry()
    register(target)
    with pytest.raises(ProtocolError, match="malformed"):
        target.register(b"t" * 32, HOST, b"j" * 32, 1, 20)
    with pytest.raises(ProtocolError, match="malformed"):
        target.register(b"u" * 32, b"i" * 32, ENROLLMENT, 1, 20)


def test_enrollment_is_short_lived_one_use_and_one_guest_only() -> None:
    target = registry()
    register(target)
    assert target.enroll(SESSION, ENROLLMENT, GUEST) <= 20
    with pytest.raises(ProtocolError, match="enrollment_used"):
        target.enroll(SESSION, ENROLLMENT, b"2" * 32)
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(b"x" * 32, ENROLLMENT, b"2" * 32)


def test_bad_enrollment_does_not_consume_valid_enrollment() -> None:
    target = registry()
    register(target)
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(SESSION, b"x" * 32, GUEST)
    enroll(target)


def test_enrollment_token_cannot_be_reused_as_guest_role_token() -> None:
    target = registry()
    register(target)
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(SESSION, ENROLLMENT, ENROLLMENT)
    enroll(target)


def test_sealed_signaling_is_opaque_directional_bounded_and_replay_safe() -> None:
    target = registry(max_signals_per_recipient=1)
    register(target)
    enroll(target)
    sealed = b"\x00opaque-aead-tag"  # service never interprets this payload
    target.publish_signal(SESSION, Role.HOST, HOST, 1, 1, sealed)
    with pytest.raises(ProtocolError, match="queue_full"):
        target.publish_signal(SESSION, Role.HOST, HOST, 1, 2, b"another-sealed-tag")
    assert target.poll_signals(SESSION, Role.GUEST, GUEST, 1, 1) == (sealed,)
    assert target.poll_signals(SESSION, Role.HOST, HOST, 1, 3) == ()
    with pytest.raises(ProtocolError, match="replay"):
        target.poll_signals(SESSION, Role.GUEST, GUEST, 1, 1)


def test_global_signal_memory_is_hard_bounded() -> None:
    target = registry(
        max_signal_bytes_global=16,
        max_signal_bytes_per_session=32,
        max_signal_bytes=16,
    )
    register(target)
    enroll(target)
    target.publish_signal(SESSION, Role.HOST, HOST, 1, 1, b"x" * 16)
    with pytest.raises(ProtocolError, match="overloaded"):
        target.publish_signal(SESSION, Role.GUEST, GUEST, 1, 1, b"y" * 16)


def test_authenticated_operations_reject_wrong_role_token_and_generation() -> None:
    target = registry()
    register(target)
    enroll(target)
    for token, generation in ((GUEST, 1), (HOST, 2)):
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.publish_signal(
                SESSION, Role.HOST, token, generation, 1, b"opaque-aead-tag!"
            )


def _handle(
    target: SessionRegistry,
    frame: RelayFrame,
    token: bytes,
    endpoint: tuple[str, int],
):
    packet = encode_relay(frame, derive_relay_key(token))
    decoded, body, tag = parse_relay(packet, 1_420)
    return target.handle_datagram(decoded, body, tag, endpoint)


def test_relay_forwards_only_between_exact_bound_session_peers() -> None:
    target = registry()
    register(target)
    enroll(target)
    host_endpoint = ("198.51.100.7", 5000)
    guest_endpoint = ("203.0.113.8", 6000)
    assert _handle(
        target,
        RelayFrame(Role.HOST, DatagramKind.BIND, SESSION, 1, 1),
        HOST,
        host_endpoint,
    ).accepted
    assert _handle(
        target,
        RelayFrame(Role.GUEST, DatagramKind.BIND, SESSION, 1, 1),
        GUEST,
        guest_endpoint,
    ).accepted
    result = _handle(
        target,
        RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 1, 2, b"ciphertext"),
        HOST,
        host_endpoint,
    )
    assert result.destination == guest_endpoint
    assert result.datagram is not None
    delivered, body, tag = parse_relay(result.datagram, 1_420, allow_delivery=True)
    assert delivered.payload == b"ciphertext"
    assert delivered.role is Role.HOST
    assert verify_relay(body, tag, derive_relay_key(GUEST))


def test_relay_rejects_endpoint_change_replay_bad_auth_and_unready_peer() -> None:
    target = registry()
    register(target)
    host_frame = RelayFrame(Role.HOST, DatagramKind.BIND, SESSION, 1, 1)
    assert _handle(target, host_frame, HOST, ("127.0.0.1", 5000)).accepted
    assert not _handle(target, host_frame, HOST, ("127.0.0.1", 5000)).accepted
    moved = RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 1, 2, b"cipher")
    assert not _handle(target, moved, HOST, ("127.0.0.1", 5001)).accepted
    forged = RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 1, 3, b"cipher")
    assert not _handle(target, forged, GUEST, ("127.0.0.1", 5000)).accepted
    no_guest = RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 1, 4, b"cipher")
    assert not _handle(target, no_guest, HOST, ("127.0.0.1", 5000)).accepted


def test_datagram_rate_and_bandwidth_are_bounded() -> None:
    target = registry(datagram_burst=1, datagrams_per_second=1)
    register(target)
    first = RelayFrame(Role.HOST, DatagramKind.BIND, SESSION, 1, 1)
    second = RelayFrame(Role.HOST, DatagramKind.KEEPALIVE, SESSION, 1, 2)
    assert _handle(target, first, HOST, ("127.0.0.1", 1)).accepted
    assert not _handle(target, second, HOST, ("127.0.0.1", 1)).accepted
    assert target.diagnostics()["traffic"]["drops"]["rate"] == 1

    bandwidth_target = registry(
        bandwidth_bytes_per_second=1, bandwidth_burst_bytes=100
    )
    register(bandwidth_target)
    assert _handle(
        bandwidth_target,
        RelayFrame(Role.HOST, DatagramKind.BIND, SESSION, 1, 1),
        HOST,
        ("127.0.0.1", 1),
    ).accepted
    assert not _handle(
        bandwidth_target,
        RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 1, 2, b"x" * 16),
        HOST,
        ("127.0.0.1", 1),
    ).accepted
    assert bandwidth_target.diagnostics()["traffic"]["drops"]["bandwidth"] == 1


def test_expiry_wipes_session_and_tombstone_rejects_registration_replay() -> None:
    clock = Clock()
    target = registry(clock)
    register(target)
    clock.advance(11)
    assert target.cleanup() == 1
    assert target.session_count == 0
    with pytest.raises(ProtocolError, match="session_replayed"):
        register(target)
    clock.advance(21)
    target.cleanup()
    register(target)


def test_absolute_ttl_expires_even_when_idle_window_is_longer() -> None:
    clock = Clock()
    target = registry(clock, idle_timeout_seconds=100)
    target.register(SESSION, HOST, ENROLLMENT, 1, 2)
    clock.advance(2)
    assert target.cleanup() == 1
    assert target.session_count == 0


def test_only_host_can_close_and_close_is_replay_protected() -> None:
    target = registry()
    register(target)
    enroll(target)
    with pytest.raises(ProtocolError, match="unauthorized"):
        target.close_session(SESSION, Role.GUEST, GUEST, 1, 1)
    target.close_session(SESSION, Role.HOST, HOST, 1, 1)
    assert target.session_count == 0

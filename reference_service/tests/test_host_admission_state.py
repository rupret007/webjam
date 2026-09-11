from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
)
from webjam_reference.state import SessionRegistry


HOST_A = "a" * 32
HOST_B = "b" * 32


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def admission_config(**changes: object) -> ServiceConfig:
    # ServiceConfig validates policy configuration; only the server loads files.
    values = {
        "tls_cert_path": Path("server-cert.pem"),
        "tls_key_path": Path("server-key.pem"),
        "host_client_ca_path": Path("host-ca.pem"),
        "host_admission_path": Path("host-policy.json"),
        "min_session_ttl_seconds": 1,
        "max_session_ttl_seconds": 20,
        "max_active_session_seconds": 60,
        "idle_timeout_seconds": 10,
    }
    values.update(changes)
    return ServiceConfig(**values)


def registry(clock: Clock | None = None, **changes: object) -> SessionRegistry:
    return SessionRegistry(
        admission_config(**changes),
        clock=clock or Clock(),
        host_principals=(HOST_A, HOST_B),
    )


def credentials(number: int) -> tuple[bytes, bytes, bytes, bytes]:
    return tuple(
        hashlib.sha256(f"synthetic-{number}-{role}".encode()).digest()
        for role in ("session", "host", "enrollment", "guest")
    )


def register(target: SessionRegistry, number: int, principal: str | None = HOST_A) -> None:
    session, host, enrollment, _ = credentials(number)
    assert target.register(
        session, host, enrollment, 1, 20, host_principal=principal
    ) == 20


def enroll(target: SessionRegistry, number: int) -> None:
    session, _, enrollment, guest = credentials(number)
    assert 0 <= target.enroll(session, enrollment, guest) <= 20


def keep_active(target: SessionRegistry, number: int, sequence: int) -> None:
    session, host, _, _ = credentials(number)
    assert target.poll_signals(session, Role.HOST, host, 1, sequence) == ()


def close_room(target: SessionRegistry, number: int, sequence: int = 1) -> None:
    session, host, _, _ = credentials(number)
    target.close_session(session, Role.HOST, host, 1, sequence)


def bind(target: SessionRegistry, number: int, role: Role, port: int) -> None:
    session, host, _, guest = credentials(number)
    token = host if role is Role.HOST else guest
    packet = encode_relay(
        RelayFrame(role, DatagramKind.BIND, session, 1, 1), derive_relay_key(token)
    )
    frame, body, tag = parse_relay(packet, 1_420)
    assert target.handle_datagram(frame, body, tag, ("192.0.2.1", port)).accepted


@pytest.mark.parametrize(
    "principals",
    [
        (), None, [HOST_A], (HOST_A, HOST_A), ("A" * 32,), ("a" * 31,),
        ("g" * 32,), (True,), (b"a" * 32,), ([],),
        tuple(f"{index:032x}" for index in range(129)),
    ],
)
def test_admission_registry_requires_fixed_valid_bounded_principals(principals) -> None:
    with pytest.raises(ValueError, match="configured admission policy"):
        SessionRegistry(admission_config(), host_principals=principals)


def test_full_approved_principal_set_is_bounded_without_dynamic_identity_entries() -> None:
    principals = tuple(f"{index:032x}" for index in range(128))
    target = SessionRegistry(admission_config(), host_principals=principals, clock=Clock())
    assert set(target._host_session_counts) == set(principals)
    assert set(target._host_registration_buckets) == set(principals)
    assert target.session_count == 0


def test_default_lab_keeps_anonymous_api_and_lower_global_capacity() -> None:
    target = SessionRegistry(ServiceConfig(max_sessions=1), clock=Clock())
    session, host, enrollment, _ = credentials(1)
    assert target.register(session, host, enrollment, 1, 600) == 600
    other, other_host, other_enrollment, _ = credentials(2)
    with pytest.raises(ProtocolError, match="overloaded"):
        target.register(other, other_host, other_enrollment, 1, 600)
    assert not target._host_session_counts
    assert not target._host_registration_buckets
    with pytest.raises(ValueError, match="configured admission policy"):
        SessionRegistry(ServiceConfig(), host_principals=(HOST_A,))
    with pytest.raises(ProtocolError, match="unauthorized"):
        target.register(other, other_host, other_enrollment, 1, 600, host_principal=HOST_A)


def test_unknown_principals_cannot_spend_admitted_or_global_allocation_budget() -> None:
    clock = Clock()
    target = registry(clock, registration_burst=1, registrations_per_second=1)
    session, host, enrollment, _ = credentials(1)
    invalid = [None, "c" * 32, "PRIVATE_UNKNOWN_PRINCIPAL", True, [], {}, b"a" * 32]
    for principal in invalid * 8:
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.register(session, host, enrollment, 1, 20, host_principal=principal)
    assert target.session_count == 0
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}
    assert set(target._host_registration_buckets) == {HOST_A, HOST_B}
    register(target, 1)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 2, HOST_B)
    clock.advance(1)
    register(target, 2, HOST_B)
    assert target.session_count == 2


def test_waiting_and_enrolled_rooms_share_host_cap_without_blocking_another_host() -> None:
    target = registry(max_sessions=8, max_sessions_per_host=2)
    register(target, 1)
    enroll(target, 1)
    register(target, 2)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 3)
    register(target, 4, HOST_B)
    assert target._host_session_counts == {HOST_A: 2, HOST_B: 1}
    assert target.diagnostics()["sessions"]["enrolled"] == 1
    assert credentials(3)[0] not in target._sessions


def test_per_host_cap_does_not_override_a_lower_global_limit() -> None:
    target = registry(max_sessions=1)
    register(target, 1)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 2, HOST_B)
    close_room(target, 1)
    register(target, 2, HOST_B)
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 1}


def test_host_rate_survives_new_sessions_and_close_without_spending_other_host_budget() -> None:
    clock = Clock()
    target = registry(clock, max_sessions_per_host=16, registration_burst=5)
    for number in range(1, 5):
        register(target, number)
    # Registration owns no TCP connection: a new connection/session cannot reset this bucket.
    for number in range(5, 25):
        with pytest.raises(ProtocolError, match="overloaded"):
            register(target, number)
    register(target, 25, HOST_B)
    close_room(target, 1)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 5)
    clock.advance(0.5)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 5)
    clock.advance(0.5)
    register(target, 5)
    assert target._host_session_counts == {HOST_A: 4, HOST_B: 1}
    assert set(target._host_registration_buckets) == {HOST_A, HOST_B}


def test_invalid_conflicting_and_replayed_registration_never_reserve_extra_host_slots() -> None:
    clock = Clock()
    target = registry(clock, max_sessions_per_host=2)
    register(target, 1)
    one, one_host, _, _ = credentials(1)
    two, _, two_enrollment, _ = credentials(2)
    with pytest.raises(ProtocolError, match="malformed"):
        target.register(two, one_host, two_enrollment, 1, 20, host_principal=HOST_A)
    with pytest.raises(ProtocolError, match="session_conflict"):
        register(target, 1)
    three, three_host, three_enrollment, _ = credentials(3)
    with pytest.raises(ProtocolError, match="invalid_ttl"):
        target.register(three, three_host, three_enrollment, 1, 0, host_principal=HOST_A)
    assert target._host_session_counts == {HOST_A: 1, HOST_B: 0}
    close_room(target, 1)
    clock.advance(1)
    with pytest.raises(ProtocolError, match="session_replayed"):
        register(target, 1)
    assert one not in target._sessions
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}
    clock.advance(1)
    register(target, 2)
    assert target._host_session_counts[HOST_A] == 1


def test_host_allocation_identity_never_replaces_room_role_authentication() -> None:
    clock = Clock()
    target = registry(clock, max_sessions_per_host=1)
    register(target, 1)
    register(target, 2, HOST_B)
    enroll(target, 1)
    session, host, _, guest = credentials(1)
    other_session, other_host, _, _ = credentials(2)
    room = target._sessions[session]
    clock.advance(9)
    for current, role, token, generation in (
        (session, Role.HOST, other_host, 1),
        (session, Role.HOST, host, 2),
        (session, Role.GUEST, guest, 1),
        (other_session, Role.HOST, host, 1),
    ):
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.close_session(current, role, token, generation, 1)
    assert room.last_activity == 100.0
    assert target._host_session_counts == {HOST_A: 1, HOST_B: 1}
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 3)
    clock.advance(1)
    assert target.cleanup() == 2
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}


def test_authenticated_close_releases_one_slot_and_wipes_owned_room_state_once() -> None:
    target = registry(max_sessions_per_host=1)
    register(target, 1)
    enroll(target, 1)
    session, host, _, guest = credentials(1)
    room = target._sessions[session]
    bind(target, 1, Role.HOST, 4001)
    bind(target, 1, Role.GUEST, 4002)
    target.publish_signal(session, Role.HOST, host, 1, 1, b"host-sealed-body!")
    target.publish_signal(session, Role.GUEST, guest, 1, 1, b"guest-sealed-body")
    close_room(target, 1, sequence=2)
    with pytest.raises(ProtocolError, match="unauthorized"):
        close_room(target, 1, sequence=3)
    assert target.cleanup() == target.cleanup() == 0
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}
    assert target._signal_bytes == 0
    assert room.signal_bytes == 0 and room.enrollment_hash is None
    assert not any(room.host.relay_key) and room.host.endpoint is None
    assert room.guest is not None and not any(room.guest.relay_key)
    assert room.guest.endpoint is None and room.host_principal is None
    assert not any(room.signals.values())
    assert target.diagnostics()["totals"]["sessions_closed"] == 1
    register(target, 2)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 3)
    assert target._host_session_counts[HOST_A] == 1


def test_replayed_control_cannot_extend_idle_lifetime_or_release_a_host_slot() -> None:
    clock = Clock()
    target = registry(clock, max_sessions_per_host=1)
    register(target, 1)
    enroll(target, 1)
    keep_active(target, 1, 1)
    room = target._sessions[credentials(1)[0]]
    clock.advance(9)
    with pytest.raises(ProtocolError, match="replay"):
        keep_active(target, 1, 1)
    assert room.last_activity == 100.0
    assert target._host_session_counts[HOST_A] == 1
    clock.advance(1)
    assert target.cleanup() == 1
    assert target._host_session_counts[HOST_A] == 0
    register(target, 2)


@pytest.mark.parametrize("expiry", ["waiting_idle", "enrolled_idle", "admission", "active"])
def test_every_existing_expiry_releases_quota_once_and_preserves_replay_tombstone(expiry) -> None:
    clock = Clock()
    target = registry(clock, max_sessions_per_host=1)
    register(target, 1)
    if expiry in {"enrolled_idle", "active"}:
        enroll(target, 1)
    room = target._sessions[credentials(1)[0]]
    if expiry in {"waiting_idle", "enrolled_idle"}:
        clock.advance(10)
    else:
        deadline = 20 if expiry == "admission" else 60
        for sequence, elapsed in enumerate(range(9, deadline, 9), 1):
            clock.advance(9)
            keep_active(target, 1, sequence)
        clock.advance(deadline - elapsed)
    assert target.cleanup() == 1
    assert target.cleanup() == 0
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}
    assert target.diagnostics()["totals"]["sessions_expired"] == 1
    assert room.host_principal is None and not any(room.host.relay_key)
    with pytest.raises(ProtocolError, match="session_replayed"):
        register(target, 1)
    register(target, 2)
    with pytest.raises(ProtocolError, match="overloaded"):
        register(target, 3)
    assert target._host_session_counts[HOST_A] == 1


def test_shutdown_wipes_all_hosts_without_identity_leaks_or_double_release() -> None:
    target = registry()
    register(target, 1)
    register(target, 2)
    register(target, 3, HOST_B)
    enroll(target, 2)
    rooms = tuple(target._sessions.values())
    before = json.dumps(target.diagnostics())
    assert HOST_A not in before and HOST_B not in before
    assert all(HOST_A not in repr(room) and HOST_B not in repr(room) for room in rooms)
    target.close()
    target.close()
    assert target.session_count == 0
    assert target._host_session_counts == {HOST_A: 0, HOST_B: 0}
    assert not target._tombstones
    assert all(room.host_principal is None and not any(room.host.relay_key) for room in rooms)
    after = json.dumps(target.diagnostics())
    assert HOST_A not in after and HOST_B not in after
    assert target.diagnostics()["totals"]["sessions_closed"] == 3

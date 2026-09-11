"""Fake-clock evidence for admission, enrolled retention, and revocation bounds."""

from __future__ import annotations

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


SESSION, HOST, ENROLLMENT, GUEST = (bytes([n]) * 32 for n in range(1, 5))
ENDPOINTS = {Role.HOST: ("127.0.0.1", 5000), Role.GUEST: ("127.0.0.1", 6000)}
TOKENS = {Role.HOST: HOST, Role.GUEST: GUEST}


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def datagram(
    target: SessionRegistry,
    role: Role,
    sequence: int,
    kind: DatagramKind = DatagramKind.KEEPALIVE,
    *,
    token: bytes | None = None,
    endpoint: tuple[str, int] | None = None,
    generation: int = 1,
):
    payload = b"opaque-ciphertext" if kind is DatagramKind.DATA else b""
    packet = encode_relay(
        RelayFrame(role, kind, SESSION, generation, sequence, payload),
        derive_relay_key(TOKENS[role] if token is None else token),
    )
    frame, body, tag = parse_relay(packet, 1420)
    return target.handle_datagram(frame, body, tag, endpoint or ENDPOINTS[role])


def register(clock: Clock, **config_changes: object) -> SessionRegistry:
    target = SessionRegistry(ServiceConfig(**config_changes), clock=clock)
    assert target.register(SESSION, HOST, ENROLLMENT, 1, 600) == 600
    assert datagram(target, Role.HOST, 1, DatagramKind.BIND).accepted
    return target


def keep_host_until_last_admission_second(clock: Clock, target: SessionRegistry) -> None:
    for sequence in range(2, 11):
        clock.advance(60)
        assert datagram(target, Role.HOST, sequence).accepted
    clock.advance(59)
    assert datagram(target, Role.HOST, 11).accepted


@pytest.mark.parametrize("kind", [DatagramKind.DATA, DatagramKind.KEEPALIVE])
@pytest.mark.parametrize("final_second_fraction", [0.0, 0.75])
def test_enrolled_peers_use_authenticated_relay_after_original_admission_deadline(
    kind, final_second_fraction
) -> None:
    clock = Clock()
    target = register(clock)
    keep_host_until_last_admission_second(clock, target)
    # The late enrollment retains the original wire TTL, not the active cap.
    clock.advance(final_second_fraction)
    assert target.enroll(SESSION, ENROLLMENT, GUEST) == int(1 - final_second_fraction)
    assert datagram(target, Role.GUEST, 1, DatagramKind.BIND).accepted
    clock.advance(1 - final_second_fraction)
    assert target.cleanup() == 0
    for role in Role:
        result = datagram(target, role, 12, kind)
        assert result.accepted
        if kind is DatagramKind.DATA:
            assert result.destination == ENDPOINTS[role.opposite]
            delivered, body, tag = parse_relay(result.datagram, 1420, allow_delivery=True)
            assert delivered.payload == b"opaque-ciphertext"
            assert delivered.role is role
            assert verify_relay(body, tag, derive_relay_key(TOKENS[role.opposite]))
        else:
            assert result.destination is None and result.datagram is None
    assert target.session_count == 1
    with pytest.raises(ProtocolError, match="enrollment_used"):
        target.enroll(SESSION, ENROLLMENT, b"x" * 32)


@pytest.mark.parametrize("expiry_path", ["cleanup", "datagram", "enrollment"])
def test_unenrolled_room_expires_at_admission_boundary_despite_host_activity(expiry_path) -> None:
    clock = Clock()
    target = register(clock)
    keep_host_until_last_admission_second(clock, target)
    clock.advance(1)
    if expiry_path == "cleanup":
        assert target.cleanup() == 1
    elif expiry_path == "datagram":
        assert not datagram(target, Role.HOST, 12).accepted
    else:
        with pytest.raises(ProtocolError, match="invalid_enrollment"):
            target.enroll(SESSION, ENROLLMENT, GUEST)
    assert target.session_count == 0
    assert target.diagnostics()["totals"]["sessions_expired"] == 1
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(SESSION, ENROLLMENT, GUEST)


@pytest.mark.parametrize("active_limit", [1200, 28_800])
@pytest.mark.parametrize("expiry_path", ["cleanup", "datagram", "control"])
def test_active_hard_cap_is_from_registration_and_cannot_be_renewed(active_limit, expiry_path) -> None:
    clock = Clock()
    registered_at = clock.now
    target = register(clock, max_active_session_seconds=active_limit)
    keep_host_until_last_admission_second(clock, target)
    assert target.enroll(SESSION, ENROLLMENT, GUEST) == 1
    assert datagram(target, Role.GUEST, 1, DatagramKind.BIND).accepted
    sequence = 12
    while clock.now + 60 < registered_at + active_limit:
        clock.advance(60)
        assert datagram(target, Role.HOST, sequence, DatagramKind.DATA).accepted
        assert datagram(target, Role.GUEST, sequence).accepted
        sequence += 1
    clock.now = registered_at + active_limit - 1
    assert datagram(target, Role.HOST, sequence).accepted
    assert target.cleanup() == 0
    clock.advance(1)
    if expiry_path == "cleanup":
        assert target.cleanup() == 1
    elif expiry_path == "datagram":
        assert not datagram(target, Role.HOST, sequence + 1).accepted
    else:
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.poll_signals(SESSION, Role.HOST, HOST, 1, 1)
    assert target.session_count == 0
    assert target.cleanup() == 0
    assert target.diagnostics()["totals"]["sessions_expired"] == 1
    with pytest.raises(ProtocolError, match="session_replayed"):
        target.register(SESSION, HOST, ENROLLMENT, 1, 600)


@pytest.mark.parametrize(
    "rejected_traffic",
    ["relay_token", "relay_replay", "endpoint", "generation", "control_token", "control_replay"],
)
def test_rejected_traffic_cannot_extend_enrolled_idle_lifetime(rejected_traffic) -> None:
    clock = Clock()
    target = register(clock)
    assert target.enroll(SESSION, ENROLLMENT, GUEST) == 600
    assert datagram(target, Role.GUEST, 1, DatagramKind.BIND).accepted
    # Keep a valid session beyond admission, then withhold all valid activity.
    for sequence in range(2, 13):
        clock.advance(60)
        assert datagram(target, Role.HOST, sequence).accepted
    assert target.poll_signals(SESSION, Role.HOST, HOST, 1, 1) == ()
    clock.advance(89)
    if rejected_traffic == "control_token":
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.poll_signals(SESSION, Role.HOST, GUEST, 1, 2)
    elif rejected_traffic == "control_replay":
        with pytest.raises(ProtocolError, match="replay"):
            target.poll_signals(SESSION, Role.HOST, HOST, 1, 1)
    else:
        changes = {
            "relay_token": {"token": GUEST},
            "relay_replay": {},
            "endpoint": {"endpoint": ("127.0.0.1", 5001)},
            "generation": {"generation": 2},
        }[rejected_traffic]
        sequence = 12 if rejected_traffic == "relay_replay" else 13
        assert not datagram(target, Role.HOST, sequence, **changes).accepted
    assert target.cleanup() == 0
    clock.advance(1)
    assert target.cleanup() == 1
    assert not datagram(target, Role.GUEST, 2).accepted


@pytest.mark.parametrize("ending", ["close", "idle", "active_cap"])
def test_enrolled_removal_wipes_keys_endpoints_queues_and_releases_capacity_once(ending) -> None:
    clock = Clock()
    registered_at = clock.now
    target = register(clock, max_sessions=1, max_active_session_seconds=1200)
    target.enroll(SESSION, ENROLLMENT, GUEST)
    assert datagram(target, Role.GUEST, 1, DatagramKind.BIND).accepted
    for role in Role:
        target.publish_signal(SESSION, role, TOKENS[role], 1, 1, b"opaque-ciphertext")
    # Retain references only to verify erasure after removal, not to drive expiry.
    session = target._sessions[SESSION]
    host_key, guest_key = session.host.relay_key, session.guest.relay_key
    assert any(host_key) and any(guest_key)
    assert target.diagnostics()["signal_queue"]["items"] == 2
    for sequence in range(2, 13):
        clock.advance(60)
        assert datagram(target, Role.HOST, sequence).accepted
    with pytest.raises(ProtocolError, match="overloaded"):
        target.register(b"q" * 32, b"r" * 32, b"t" * 32, 1, 600)
    if ending == "close":
        with pytest.raises(ProtocolError, match="unauthorized"):
            target.close_session(SESSION, Role.GUEST, GUEST, 1, 2)
        assert target.session_count == 1
        target.close_session(SESSION, Role.HOST, HOST, 1, 2)
    elif ending == "idle":
        clock.advance(90)
        assert target.cleanup() == 1
    else:
        sequence = 13
        while clock.now + 60 < registered_at + 1200:
            clock.advance(60)
            assert datagram(target, Role.HOST, sequence).accepted
            sequence += 1
        clock.now = registered_at + 1200
        assert target.cleanup() == 1
    assert target.session_count == 0
    assert not any(host_key) and not any(guest_key)
    assert session.host.endpoint is None and session.guest.endpoint is None
    assert session.enrollment_hash is None
    assert not any(session.signals.values())
    assert session.signal_bytes == 0
    diagnostics = target.diagnostics()
    assert diagnostics["signal_queue"]["bytes"] == 0
    assert diagnostics["signal_queue"]["items"] == 0
    assert diagnostics["totals"]["sessions_closed"] == int(ending == "close")
    assert diagnostics["totals"]["sessions_expired"] == int(ending != "close")
    assert target.cleanup() == 0
    with pytest.raises(ProtocolError, match="session_replayed"):
        target.register(SESSION, HOST, ENROLLMENT, 1, 600)
    assert target.register(b"q" * 32, b"r" * 32, b"t" * 32, 1, 600) == 600


def test_failed_enrollment_does_not_promote_room_or_extend_admission() -> None:
    clock = Clock()
    target = register(clock)
    keep_host_until_last_admission_second(clock, target)
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(SESSION, b"x" * 32, GUEST)
    clock.advance(1)
    assert target.cleanup() == 1
    with pytest.raises(ProtocolError, match="invalid_enrollment"):
        target.enroll(SESSION, ENROLLMENT, GUEST)

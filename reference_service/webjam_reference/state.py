"""Bounded in-memory state for opaque rendezvous and exact-peer relaying."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

from .config import ServiceConfig
from .protocol import (
    DatagramKind,
    ProtocolError,
    RelayFrame,
    ReplayWindow,
    Role,
    SESSION_BYTES,
    TOKEN_BYTES,
    derive_relay_key,
    encode_relay,
    token_digest,
    verify_relay,
)

Endpoint = tuple[object, ...]


class TokenBucket:
    __slots__ = ("_capacity", "_clock", "_rate", "_then", "_tokens")

    def __init__(self, rate: int, capacity: int, clock: Callable[[], float]) -> None:
        self._rate = float(rate)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._then = clock()

    def allow(self, cost: int = 1) -> bool:
        now = self._clock()
        elapsed = max(0.0, now - self._then)
        self._then = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if cost < 0 or self._tokens < cost:
            return False
        self._tokens -= cost
        return True


@dataclass(slots=True)
class Peer:
    token_hash: bytes
    relay_key: bytearray
    control_replay: ReplayWindow
    datagram_replay: ReplayWindow
    endpoint: Endpoint | None = None

    def authenticate(self, token: bytes) -> bool:
        return hmac.compare_digest(self.token_hash, token_digest(token))

    def wipe(self) -> None:
        for index in range(len(self.relay_key)):
            self.relay_key[index] = 0
        self.endpoint = None


@dataclass(slots=True)
class Session:
    session_id: bytes
    generation: int
    enrollment_hash: bytes | None
    host: Peer
    created_at: float
    expires_at: float
    last_activity: float
    datagram_bucket: TokenBucket
    bandwidth_bucket: TokenBucket
    guest: Peer | None = None
    signals: dict[Role, deque[bytes]] = field(
        default_factory=lambda: {Role.HOST: deque(), Role.GUEST: deque()}
    )
    signal_bytes: int = 0

    def peer(self, role: Role) -> Peer | None:
        return self.host if role is Role.HOST else self.guest

    def wipe(self) -> int:
        self.host.wipe()
        if self.guest is not None:
            self.guest.wipe()
        released = self.signal_bytes
        self.signals[Role.HOST].clear()
        self.signals[Role.GUEST].clear()
        self.signal_bytes = 0
        self.enrollment_hash = None
        return released


@dataclass(frozen=True, slots=True)
class RelayResult:
    destination: Endpoint | None = None
    datagram: bytes | None = None
    accepted: bool = False


class SessionRegistry:
    """Single-event-loop registry; every collection has a configured bound."""

    _KNOWN_DROPS = frozenset(
        {
            "auth",
            "bandwidth",
            "endpoint_mismatch",
            "malformed",
            "not_ready",
            "rate",
            "replay",
            "session",
            "version",
        }
    )

    def __init__(
        self, config: ServiceConfig, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.config = config
        self._clock = clock
        self._started_at = clock()
        self._sessions: dict[bytes, Session] = {}
        self._tombstones: OrderedDict[bytes, float] = OrderedDict()
        self._signal_bytes = 0
        self._counters: Counter[str] = Counter()
        self._registration_bucket = TokenBucket(
            config.registrations_per_second, config.registration_burst, clock
        )
        self._global_datagram_bucket = TokenBucket(
            config.global_datagrams_per_second, config.global_datagram_burst, clock
        )

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def register(
        self,
        session_id: bytes,
        host_token: bytes,
        enrollment_token: bytes,
        generation: int,
        ttl_seconds: int,
    ) -> int:
        self.cleanup()
        if (
            len(session_id) != SESSION_BYTES
            or len(host_token) != TOKEN_BYTES
            or len(enrollment_token) != TOKEN_BYTES
        ):
            raise ProtocolError("malformed")
        if not self._registration_bucket.allow():
            self._counters["registrations_rejected_rate"] += 1
            raise ProtocolError("overloaded")
        if session_id in self._sessions:
            raise ProtocolError("session_conflict")
        tombstone = self._session_digest(session_id)
        if tombstone in self._tombstones:
            raise ProtocolError("session_replayed")
        if len(self._sessions) >= self.config.max_sessions:
            self._counters["registrations_rejected_capacity"] += 1
            raise ProtocolError("overloaded")
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise ProtocolError("malformed")
        if not 1 <= generation <= 0xFFFF_FFFF:
            raise ProtocolError("malformed")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise ProtocolError("malformed")
        if not self.config.min_session_ttl_seconds <= ttl_seconds <= (
            self.config.max_session_ttl_seconds
        ):
            raise ProtocolError("invalid_ttl")
        host_hash = token_digest(host_token)
        enrollment_hash = hashlib.sha256(
            b"webjam-reference-enrollment-v3\x00" + enrollment_token
        ).digest()
        if hmac.compare_digest(host_hash, token_digest(enrollment_token)):
            raise ProtocolError("malformed")
        if self._credential_in_use(host_token) or self._credential_in_use(
            enrollment_token
        ):
            raise ProtocolError("malformed")
        now = self._clock()
        peer = self._new_peer(host_token)
        self._sessions[session_id] = Session(
            session_id=session_id,
            generation=generation,
            enrollment_hash=enrollment_hash,
            host=peer,
            created_at=now,
            expires_at=now + ttl_seconds,
            last_activity=now,
            datagram_bucket=TokenBucket(
                self.config.datagrams_per_second,
                self.config.datagram_burst,
                self._clock,
            ),
            bandwidth_bucket=TokenBucket(
                self.config.bandwidth_bytes_per_second,
                self.config.bandwidth_burst_bytes,
                self._clock,
            ),
        )
        self._counters["sessions_registered"] += 1
        return ttl_seconds

    def enroll(
        self, session_id: bytes, enrollment_token: bytes, guest_token: bytes
    ) -> int:
        if (
            len(session_id) != SESSION_BYTES
            or len(enrollment_token) != TOKEN_BYTES
            or len(guest_token) != TOKEN_BYTES
        ):
            raise ProtocolError("malformed")
        session = self._live_session(session_id, public_error="invalid_enrollment")
        if session.guest is not None or session.enrollment_hash is None:
            raise ProtocolError("enrollment_used")
        candidate = hashlib.sha256(
            b"webjam-reference-enrollment-v3\x00" + enrollment_token
        ).digest()
        if not hmac.compare_digest(candidate, session.enrollment_hash):
            self._counters["enrollments_rejected"] += 1
            raise ProtocolError("invalid_enrollment")
        if session.host.authenticate(guest_token):
            raise ProtocolError("invalid_enrollment")
        if hmac.compare_digest(enrollment_token, guest_token):
            raise ProtocolError("invalid_enrollment")
        if self._credential_in_use(guest_token):
            raise ProtocolError("invalid_enrollment")
        session.guest = self._new_peer(guest_token)
        session.enrollment_hash = None
        session.last_activity = self._clock()
        self._counters["guests_enrolled"] += 1
        return max(0, int(session.expires_at - self._clock()))

    def publish_signal(
        self,
        session_id: bytes,
        role: Role,
        token: bytes,
        generation: int,
        sequence: int,
        sealed_payload: bytes,
    ) -> None:
        if not 16 <= len(sealed_payload) <= self.config.max_signal_bytes:
            raise ProtocolError("malformed")
        session, peer = self._authenticate(
            session_id, role, token, generation, sequence
        )
        recipient = role.opposite
        queue = session.signals[recipient]
        if len(queue) >= self.config.max_signals_per_recipient:
            raise ProtocolError("queue_full")
        projected_session = session.signal_bytes + len(sealed_payload)
        projected_global = self._signal_bytes + len(sealed_payload)
        if projected_session > self.config.max_signal_bytes_per_session:
            raise ProtocolError("queue_full")
        if projected_global > self.config.max_signal_bytes_global:
            raise ProtocolError("overloaded")
        queue.append(sealed_payload)
        session.signal_bytes = projected_session
        self._signal_bytes = projected_global
        session.last_activity = self._clock()
        self._counters["signals_published"] += 1
        del peer

    def poll_signals(
        self,
        session_id: bytes,
        role: Role,
        token: bytes,
        generation: int,
        sequence: int,
    ) -> tuple[bytes, ...]:
        session, _ = self._authenticate(
            session_id, role, token, generation, sequence
        )
        queue = session.signals[role]
        # One item keeps every response below the configured control-frame bound.
        values = (queue.popleft(),) if queue else ()
        released = sum(map(len, values))
        session.signal_bytes -= released
        self._signal_bytes -= released
        session.last_activity = self._clock()
        self._counters["signals_polled"] += len(values)
        return values

    def close_session(
        self,
        session_id: bytes,
        role: Role,
        token: bytes,
        generation: int,
        sequence: int,
    ) -> None:
        self._authenticate(session_id, role, token, generation, sequence)
        if role is not Role.HOST:
            raise ProtocolError("unauthorized")
        self._remove(session_id, "closed")

    def handle_datagram(
        self,
        frame: RelayFrame,
        signed_body: bytes,
        tag: bytes,
        source: Endpoint,
    ) -> RelayResult:
        if not self._global_datagram_bucket.allow():
            return self._drop("rate")
        session = self._sessions.get(frame.session_id)
        if session is None or self._is_expired(session):
            if session is not None:
                self._remove(frame.session_id, "expired")
            return self._drop("session")
        if frame.generation != session.generation:
            return self._drop("session")
        peer = session.peer(frame.role)
        if peer is None:
            return self._drop("not_ready")
        relay_key = bytes(peer.relay_key)
        if not verify_relay(signed_body, tag, relay_key):
            return self._drop("auth")
        if frame.kind in (DatagramKind.BIND, DatagramKind.KEEPALIVE) and frame.payload:
            return self._drop("malformed")
        if len(frame.payload) > self.config.max_relay_payload_bytes:
            return self._drop("malformed")
        if not session.datagram_bucket.allow():
            return self._drop("rate")
        network_cost = len(signed_body) + len(tag)
        if frame.kind is DatagramKind.DATA:
            network_cost *= 2
        if not session.bandwidth_bucket.allow(network_cost):
            return self._drop("bandwidth")
        if not peer.datagram_replay.accept(frame.sequence):
            return self._drop("replay")

        if frame.kind is DatagramKind.BIND:
            if peer.endpoint is None:
                peer.endpoint = source
            elif peer.endpoint != source:
                return self._drop("endpoint_mismatch")
            session.last_activity = self._clock()
            self._counters["datagrams_bound"] += 1
            return RelayResult(accepted=True)
        if peer.endpoint is None or peer.endpoint != source:
            return self._drop("endpoint_mismatch")
        session.last_activity = self._clock()
        if frame.kind is DatagramKind.KEEPALIVE:
            self._counters["datagrams_keepalive"] += 1
            return RelayResult(accepted=True)
        if frame.kind is not DatagramKind.DATA:
            return self._drop("malformed")

        recipient = session.peer(frame.role.opposite)
        if recipient is None or recipient.endpoint is None:
            return self._drop("not_ready")
        delivery = encode_relay(
            RelayFrame(
                role=frame.role,
                kind=DatagramKind.DELIVERY,
                session_id=frame.session_id,
                generation=frame.generation,
                sequence=frame.sequence,
                payload=frame.payload,
            ),
            bytes(recipient.relay_key),
        )
        self._counters["datagrams_forwarded"] += 1
        self._counters["bytes_forwarded"] += len(frame.payload)
        return RelayResult(recipient.endpoint, delivery, True)

    def record_datagram_drop(self, reason: str) -> None:
        self._drop(reason)

    def cleanup(self) -> int:
        now = self._clock()
        expired = [
            key
            for key, session in self._sessions.items()
            if now >= session.expires_at
            or now - session.last_activity >= self.config.idle_timeout_seconds
        ]
        for key in expired:
            self._remove(key, "expired")
        while self._tombstones:
            _, expires_at = next(iter(self._tombstones.items()))
            if expires_at > now:
                break
            self._tombstones.popitem(last=False)
        return len(expired)

    def diagnostics(self) -> dict[str, object]:
        self.cleanup()
        enrolled = sum(session.guest is not None for session in self._sessions.values())
        bound = sum(
            int(session.host.endpoint is not None)
            + int(session.guest is not None and session.guest.endpoint is not None)
            for session in self._sessions.values()
        )
        capacity_degraded = (
            len(self._sessions) >= self.config.max_sessions
            or self._signal_bytes >= self.config.max_signal_bytes_global
        )
        return {
            "v": self.config.protocol_version,
            "status": "degraded" if capacity_degraded else "ok",
            "uptime_seconds": max(0, int(self._clock() - self._started_at)),
            "sessions": {
                "active": len(self._sessions),
                "capacity": self.config.max_sessions,
                "enrolled": enrolled,
                "peer_endpoints_bound": bound,
            },
            "signal_queue": {
                "bytes": self._signal_bytes,
                "byte_capacity": self.config.max_signal_bytes_global,
                "items": sum(
                    len(queue)
                    for session in self._sessions.values()
                    for queue in session.signals.values()
                ),
            },
            "traffic": {
                "bytes_forwarded": self._counters["bytes_forwarded"],
                "datagrams_forwarded": self._counters["datagrams_forwarded"],
                "drops": {
                    reason: self._counters[f"drop_{reason}"]
                    for reason in sorted(self._KNOWN_DROPS)
                },
            },
            "totals": {
                key: self._counters[key]
                for key in (
                    "sessions_registered",
                    "sessions_closed",
                    "sessions_expired",
                    "guests_enrolled",
                    "signals_published",
                    "signals_polled",
                    "registrations_rejected_capacity",
                    "registrations_rejected_rate",
                )
            },
        }

    def close(self) -> None:
        for key in tuple(self._sessions):
            self._remove(key, "closed", tombstone=False)
        self._tombstones.clear()

    def _new_peer(self, token: bytes) -> Peer:
        return Peer(
            token_hash=token_digest(token),
            relay_key=bytearray(derive_relay_key(token)),
            control_replay=ReplayWindow(self.config.replay_window_size),
            datagram_replay=ReplayWindow(self.config.replay_window_size),
        )

    def _authenticate(
        self,
        session_id: bytes,
        role: Role,
        token: bytes,
        generation: int,
        sequence: int,
    ) -> tuple[Session, Peer]:
        if len(session_id) != SESSION_BYTES or len(token) != TOKEN_BYTES:
            raise ProtocolError("unauthorized")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or not 1 <= generation <= 0xFFFF_FFFF
        ):
            raise ProtocolError("unauthorized")
        session = self._live_session(session_id)
        if generation != session.generation:
            raise ProtocolError("unauthorized")
        peer = session.peer(role)
        if peer is None or not peer.authenticate(token):
            raise ProtocolError("unauthorized")
        if not peer.control_replay.accept(sequence):
            raise ProtocolError("replay")
        return session, peer

    def _live_session(self, session_id: bytes, public_error: str = "unauthorized") -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise ProtocolError(public_error)
        if self._is_expired(session):
            self._remove(session_id, "expired")
            raise ProtocolError(public_error)
        return session

    def _is_expired(self, session: Session) -> bool:
        now = self._clock()
        return now >= session.expires_at or (
            now - session.last_activity >= self.config.idle_timeout_seconds
        )

    def _remove(self, session_id: bytes, reason: str, *, tombstone: bool = True) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        self._signal_bytes -= session.wipe()
        if tombstone:
            digest = self._session_digest(session_id)
            self._tombstones[digest] = self._clock() + self.config.tombstone_ttl_seconds
            self._tombstones.move_to_end(digest)
            while len(self._tombstones) > self.config.max_tombstones:
                self._tombstones.popitem(last=False)
        self._counters[f"sessions_{reason}"] += 1

    def _drop(self, reason: str) -> RelayResult:
        safe_reason = reason if reason in self._KNOWN_DROPS else "malformed"
        self._counters[f"drop_{safe_reason}"] += 1
        return RelayResult()

    @staticmethod
    def _session_digest(session_id: bytes) -> bytes:
        return hashlib.sha256(b"webjam-reference-tombstone-v3\x00" + session_id).digest()

    def _credential_in_use(self, candidate: bytes) -> bool:
        role_hash = token_digest(candidate)
        enrollment_hash = hashlib.sha256(
            b"webjam-reference-enrollment-v3\x00" + candidate
        ).digest()
        for session in self._sessions.values():
            if hmac.compare_digest(session.host.token_hash, role_hash):
                return True
            if session.guest is not None and hmac.compare_digest(
                session.guest.token_hash, role_hash
            ):
                return True
            if session.enrollment_hash is not None and hmac.compare_digest(
                session.enrollment_hash, enrollment_hash
            ):
                return True
        return False

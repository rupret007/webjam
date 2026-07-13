"""Typed, UI-neutral session-transport state.

The live packet implementation belongs to the owned transport sidecar.  This
module is the desktop-side seam: immutable evidence enters here, deterministic
policy chooses a path, and Qt consumes snapshots without learning addresses,
credentials, candidates, or protocol internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
import math
import time
from typing import Callable, Iterable


MAX_CONNECTION_TIMELINE = 64
MAX_EVIDENCE_SAMPLES = 64


class SessionRole(str, Enum):
    HOST = "host"
    GUEST = "guest"


class TransportPath(str, Enum):
    LAN_DIRECT = "lan_direct"
    INTERNET_DIRECT = "internet_direct"
    SECURE_RELAY = "secure_relay"

    @property
    def musician_label(self) -> str:
        if self is TransportPath.SECURE_RELAY:
            return "Using a secure relay"
        return "Connected directly"


class TransportPhase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    PROBING = "probing"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    EXHAUSTED = "exhausted"
    STOPPED = "stopped"

    @property
    def musician_label(self) -> str:
        return {
            TransportPhase.IDLE: "Preparing your jam",
            TransportPhase.PREPARING: "Preparing your jam",
            TransportPhase.PROBING: "Finding the fastest path",
            TransportPhase.CONNECTED: "Your audio session is connected",
            TransportPhase.DEGRADED: "Connection may be difficult for live playing",
            TransportPhase.RECONNECTING: "Reconnecting",
            TransportPhase.EXHAUSTED: "The host is temporarily unreachable",
            TransportPhase.STOPPED: "Audio session ended",
        }[self]


class ConnectionQuality(str, Enum):
    UNKNOWN = "unknown"
    PLAYABLE = "playable"
    DIFFICULT = "difficult"
    UNUSABLE = "unusable"

    @property
    def musician_label(self) -> str:
        return {
            ConnectionQuality.UNKNOWN: "Checking the connection",
            ConnectionQuality.PLAYABLE: "Connection looks playable",
            ConnectionQuality.DIFFICULT: (
                "Connection may be difficult for live playing"
            ),
            ConnectionQuality.UNUSABLE: "The audio connection needs attention",
        }[self]


class TransportEventCode(str, Enum):
    PREPARE = "prepare"
    PROBE = "probe"
    PATH_SELECTED = "path_selected"
    PATH_DEGRADED = "path_degraded"
    MIGRATION_DEFERRED = "migration_deferred"
    NETWORK_CHANGED = "network_changed"
    RECONNECT = "reconnect"
    RETRIES_EXHAUSTED = "retries_exhausted"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class TransportHealth:
    """One bounded, non-content transport observation.

    A healthy socket or received datagram is not proof of human audibility.
    `audio_datagrams_received` means only that encrypted Jamulus-plane traffic
    crossed the selected transport.
    """

    path: TransportPath
    generation: int
    observed_monotonic: float
    control_reachable: bool
    handshake_ms: float | None = None
    round_trip_ms: float | None = None
    jitter_ms: float | None = None
    loss_percent: float | None = None
    reorder_percent: float | None = None
    recent_outages: int = 0
    backpressure_bytes: int = 0
    reconnect_count: int = 0
    audio_datagrams_sent: int = 0
    audio_datagrams_received: int = 0
    sample_count: int = 0

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("transport generation must be positive")
        if not math.isfinite(self.observed_monotonic):
            raise ValueError("observation time must be finite")
        for name in ("handshake_ms", "round_trip_ms", "jitter_ms"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("loss_percent", "reorder_percent"):
            value = getattr(self, name)
            if value is not None and (
                not math.isfinite(value) or not 0 <= value <= 100
            ):
                raise ValueError(f"{name} must be between zero and one hundred")
        for name in (
            "recent_outages",
            "backpressure_bytes",
            "reconnect_count",
            "audio_datagrams_sent",
            "audio_datagrams_received",
            "sample_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    @property
    def has_audio_transport_evidence(self) -> bool:
        return self.audio_datagrams_received > 0

    @property
    def complete_probe(self) -> bool:
        return bool(
            self.control_reachable
            and self.round_trip_ms is not None
            and self.jitter_ms is not None
            and self.loss_percent is not None
            and self.sample_count > 0
        )

    @property
    def quality(self) -> ConnectionQuality:
        if not self.complete_probe:
            return ConnectionQuality.UNKNOWN
        assert self.round_trip_ms is not None
        assert self.jitter_ms is not None
        assert self.loss_percent is not None
        if (
            self.round_trip_ms <= 45
            and self.jitter_ms <= 8
            and self.loss_percent <= 1
            and self.recent_outages == 0
        ):
            return ConnectionQuality.PLAYABLE
        if (
            self.round_trip_ms <= 100
            and self.jitter_ms <= 25
            and self.loss_percent <= 5
            and self.recent_outages <= 2
        ):
            return ConnectionQuality.DIFFICULT
        return ConnectionQuality.UNUSABLE

    def age_seconds(self, *, now: float | None = None) -> float:
        stamp = time.monotonic() if now is None else float(now)
        return max(0.0, stamp - self.observed_monotonic)


@dataclass(frozen=True, slots=True)
class TransportEvidence:
    """Bounded observations for one path and connection generation."""

    path: TransportPath
    generation: int
    samples: tuple[TransportHealth, ...] = ()

    def append(self, sample: TransportHealth) -> TransportEvidence:
        if sample.path is not self.path or sample.generation != self.generation:
            raise ValueError("transport evidence cannot cross path or generation")
        return replace(
            self,
            samples=(*self.samples, sample)[-MAX_EVIDENCE_SAMPLES:],
        )

    @property
    def latest(self) -> TransportHealth | None:
        return self.samples[-1] if self.samples else None


@dataclass(frozen=True, slots=True)
class ConnectionPathDecision:
    selected: TransportPath | None
    quality: ConnectionQuality
    reason: str
    score: float | None = None
    deferred_until_safe_boundary: bool = False

    def __post_init__(self) -> None:
        if self.reason not in {
            "best_viable_path",
            "keep_stable_path",
            "cooldown",
            "recording_boundary",
            "no_viable_path",
        }:
            raise ValueError("unknown path-decision reason")
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("path score must be finite")


@dataclass(frozen=True, slots=True)
class TransportTimelineEvent:
    sequence: int
    at_monotonic: float
    code: TransportEventCode
    phase: TransportPhase
    generation: int
    path: TransportPath | None = None


@dataclass(frozen=True, slots=True)
class SessionTransportSnapshot:
    protocol_version: int
    role: SessionRole
    phase: TransportPhase
    generation: int
    selected_path: TransportPath | None
    quality: ConnectionQuality
    evidence: tuple[TransportEvidence, ...]
    decision: ConnectionPathDecision | None
    timeline: tuple[TransportTimelineEvent, ...]
    reconnect_count: int
    retries_remaining: int

    @property
    def musician_status(self) -> str:
        if self.phase in {
            TransportPhase.IDLE,
            TransportPhase.PREPARING,
            TransportPhase.PROBING,
            TransportPhase.RECONNECTING,
            TransportPhase.EXHAUSTED,
            TransportPhase.STOPPED,
        }:
            return self.phase.musician_label
        if self.quality in {ConnectionQuality.DIFFICULT, ConnectionQuality.UNUSABLE}:
            return self.quality.musician_label
        if self.selected_path is not None:
            return self.selected_path.musician_label
        return self.phase.musician_label


@dataclass(frozen=True, slots=True)
class TransportStartRequest:
    protocol_version: int
    role: SessionRole
    generation: int


class SessionTransport(ABC):
    """Owned transport implementation behind the coordinator seam."""

    @property
    @abstractmethod
    def path(self) -> TransportPath:
        raise NotImplementedError

    @abstractmethod
    def start(self, request: TransportStartRequest) -> None:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> TransportHealth:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError


class SessionTransportCoordinator:
    """Deterministic connection state; never mutates UI or recording models."""

    def __init__(
        self,
        *,
        protocol_version: int = 3,
        retry_budget: int = 5,
        clock: Callable[[], float] = time.monotonic,
        policy: object | None = None,
    ) -> None:
        if protocol_version < 1:
            raise ValueError("protocol version must be positive")
        if retry_budget < 0:
            raise ValueError("retry budget must be non-negative")
        if policy is None:
            from core.transport_decision import ConnectionPathPolicy

            policy = ConnectionPathPolicy()
        self._policy = policy
        self._protocol_version = protocol_version
        self._retry_budget = retry_budget
        self._clock = clock
        self._role = SessionRole.HOST
        self._phase = TransportPhase.IDLE
        self._generation = 1
        self._selected: TransportPath | None = None
        self._quality = ConnectionQuality.UNKNOWN
        self._evidence: dict[TransportPath, TransportEvidence] = {}
        self._decision: ConnectionPathDecision | None = None
        self._timeline: list[TransportTimelineEvent] = []
        self._sequence = 0
        self._reconnect_count = 0
        self._retries_remaining = retry_budget
        self._last_switch_monotonic: float | None = None

    def begin(self, role: SessionRole) -> SessionTransportSnapshot:
        if self._phase not in {
            TransportPhase.IDLE,
            TransportPhase.STOPPED,
            TransportPhase.EXHAUSTED,
        }:
            raise RuntimeError("session transport is already active")
        self._role = SessionRole(role)
        self._phase = TransportPhase.PREPARING
        self._generation = 1
        self._selected = None
        self._quality = ConnectionQuality.UNKNOWN
        self._evidence.clear()
        self._decision = None
        self._timeline.clear()
        self._sequence = 0
        self._reconnect_count = 0
        self._retries_remaining = self._retry_budget
        self._last_switch_monotonic = None
        self._event(TransportEventCode.PREPARE)
        return self.snapshot()

    def begin_probing(self) -> SessionTransportSnapshot:
        if self._phase not in {
            TransportPhase.PREPARING,
            TransportPhase.RECONNECTING,
        }:
            raise RuntimeError("transport is not ready to probe")
        self._phase = TransportPhase.PROBING
        self._event(TransportEventCode.PROBE)
        return self.snapshot()

    def observe(self, health: TransportHealth) -> SessionTransportSnapshot:
        if self._phase in {
            TransportPhase.IDLE,
            TransportPhase.STOPPED,
            TransportPhase.EXHAUSTED,
        }:
            raise RuntimeError("transport is not accepting evidence")
        if health.generation != self._generation:
            raise ValueError("stale or future transport generation")
        evidence = self._evidence.get(health.path)
        if evidence is None:
            evidence = TransportEvidence(health.path, health.generation)
        self._evidence[health.path] = evidence.append(health)
        return self.snapshot()

    def choose_path(self, *, recording_active: bool = False) -> SessionTransportSnapshot:
        if self._phase not in {
            TransportPhase.PROBING,
            TransportPhase.CONNECTED,
            TransportPhase.DEGRADED,
        }:
            raise RuntimeError("transport is not ready for path selection")
        now = self._clock()
        decision = self._policy.choose(
            tuple(self._evidence.values()),
            current=self._selected,
            now=now,
            last_switch_monotonic=self._last_switch_monotonic,
            recording_active=recording_active,
        )
        if not isinstance(decision, ConnectionPathDecision):
            raise TypeError("path policy returned an invalid decision")
        self._decision = decision
        if decision.selected is None:
            self._quality = ConnectionQuality.UNUSABLE
            self._phase = TransportPhase.DEGRADED
            self._event(TransportEventCode.PATH_DEGRADED)
            return self.snapshot()
        if decision.deferred_until_safe_boundary:
            self._event(TransportEventCode.MIGRATION_DEFERRED, decision.selected)
            return self.snapshot()
        changed = decision.selected is not self._selected
        self._selected = decision.selected
        self._quality = decision.quality
        self._phase = (
            TransportPhase.CONNECTED
            if decision.quality is ConnectionQuality.PLAYABLE
            else TransportPhase.DEGRADED
        )
        if changed:
            self._last_switch_monotonic = now
        self._event(
            TransportEventCode.PATH_SELECTED
            if self._phase is TransportPhase.CONNECTED
            else TransportEventCode.PATH_DEGRADED,
            self._selected,
        )
        return self.snapshot()

    def network_changed(self) -> SessionTransportSnapshot:
        self._require_active()
        self._generation += 1
        self._selected = None
        self._quality = ConnectionQuality.UNKNOWN
        self._evidence.clear()
        self._decision = None
        self._phase = TransportPhase.PROBING
        self._event(TransportEventCode.NETWORK_CHANGED)
        return self.snapshot()

    def path_failed(self) -> SessionTransportSnapshot:
        self._require_active()
        if self._retries_remaining <= 0:
            self._phase = TransportPhase.EXHAUSTED
            self._selected = None
            self._quality = ConnectionQuality.UNUSABLE
            self._event(TransportEventCode.RETRIES_EXHAUSTED)
            return self.snapshot()
        self._retries_remaining -= 1
        self._reconnect_count += 1
        self._generation += 1
        self._selected = None
        self._quality = ConnectionQuality.UNKNOWN
        self._evidence.clear()
        self._decision = None
        self._phase = TransportPhase.RECONNECTING
        self._event(TransportEventCode.RECONNECT)
        return self.snapshot()

    def stop(self) -> SessionTransportSnapshot:
        if self._phase is TransportPhase.STOPPED:
            return self.snapshot()
        self._selected = None
        self._quality = ConnectionQuality.UNKNOWN
        self._phase = TransportPhase.STOPPED
        self._event(TransportEventCode.STOP)
        return self.snapshot()

    def snapshot(self) -> SessionTransportSnapshot:
        evidence = tuple(
            self._evidence[path]
            for path in TransportPath
            if path in self._evidence
        )
        return SessionTransportSnapshot(
            protocol_version=self._protocol_version,
            role=self._role,
            phase=self._phase,
            generation=self._generation,
            selected_path=self._selected,
            quality=self._quality,
            evidence=evidence,
            decision=self._decision,
            timeline=tuple(self._timeline),
            reconnect_count=self._reconnect_count,
            retries_remaining=self._retries_remaining,
        )

    def latest_health(self, path: TransportPath) -> TransportHealth | None:
        evidence = self._evidence.get(path)
        return evidence.latest if evidence is not None else None

    def _require_active(self) -> None:
        if self._phase in {
            TransportPhase.IDLE,
            TransportPhase.STOPPED,
            TransportPhase.EXHAUSTED,
        }:
            raise RuntimeError("session transport is not active")

    def _event(
        self,
        code: TransportEventCode,
        path: TransportPath | None = None,
    ) -> None:
        self._sequence += 1
        self._timeline.append(
            TransportTimelineEvent(
                sequence=self._sequence,
                at_monotonic=self._clock(),
                code=code,
                phase=self._phase,
                generation=self._generation,
                path=path,
            )
        )
        del self._timeline[:-MAX_CONNECTION_TIMELINE]


def latest_health_by_path(
    evidence: Iterable[TransportEvidence],
) -> dict[TransportPath, TransportHealth]:
    return {
        item.path: item.latest
        for item in evidence
        if item.latest is not None
    }

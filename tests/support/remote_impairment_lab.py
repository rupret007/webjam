"""Deterministic, rootless remote-network impairment support.

The pure :class:`ImpairmentChannel` scheduler is suitable for fast unit tests
with :class:`VirtualClock`.  :class:`RemoteImpairmentLab` additionally owns
real loopback UDP proxies and child processes so the same fault model can sit
between the WebJam sidecar and a real Jamulus process in opt-in tests.  Seeded
decisions and queue order are reproducible; live delivery timing remains
subject to the host scheduler.  The live proxy deliberately models one local
client and one exact loopback target.  It does not simulate a kernel NAT,
Internet routing, PMTU discovery, or physical network hardware.

Reports intentionally contain only fixed enums, counters, relative monotonic
times, and lab-issued endpoint tokens.  Packet bytes and network addresses are
never admitted to the report model.
"""

from __future__ import annotations

import heapq
import ipaddress
import json
import math
import os
import random
import re
import signal
import socket
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Protocol, Sequence

MAX_UDP_PAYLOAD_BYTES = 65_507
MAX_QUEUE_CAPACITY = 8_192
MAX_REPORT_EVENT_CAPACITY = 4_096
MAX_LAB_PROXIES = 16
MAX_LAB_PROCESSES = 8
MAX_LAB_CHANNELS = 32
MAX_LAB_ENDPOINTS = 32
MAX_CHANNEL_ENDPOINTS = 16

_SAFE_TOKEN = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_SAFE_TOKEN_SCHEMA_PATTERN = "^[a-z][a-z0-9._-]{0,63}$"


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class PathKind(_StringEnum):
    DIRECT = "direct"
    RELAY = "relay"


class TrafficClass(_StringEnum):
    CONTROL = "control"
    JAMULUS_DATAGRAM = "jamulus_datagram"


class Direction(_StringEnum):
    TO_TARGET = "to_target"
    FROM_TARGET = "from_target"


class EventCategory(_StringEnum):
    INJECTED_OUTAGE = "injected_outage"
    UNEXPECTED_TRANSPORT_LOSS = "unexpected_transport_loss"
    CONTROL_LOSS = "control_loss"
    JAMULUS_DATAGRAM_LOSS = "jamulus_datagram_loss"
    RECONNECT = "reconnect"
    MEDIA_RECOVERY = "media_recovery"
    CLEANUP = "cleanup"


class ReasonCode(_StringEnum):
    SEEDED_LOSS = "seeded_loss"
    BLACKHOLE = "blackhole"
    DIRECT_REJECTED = "direct_rejected"
    RELAY_FAILED = "relay_failed"
    RELAY_RESTARTED = "relay_restarted"
    ADDRESS_CHANGED = "address_changed"
    QUEUE_OVERFLOW = "queue_overflow"
    OVERSIZED_DATAGRAM = "oversized_datagram"
    STALE_GENERATION = "stale_generation"
    SOCKET_RECEIVE_ERROR = "socket_receive_error"
    SOCKET_SEND_ERROR = "socket_send_error"
    CLIENT_CHANGED = "client_changed"
    MANUAL_RECONNECT = "manual_reconnect"
    PACKET_DELIVERED = "packet_delivered"
    CLEAN_SHUTDOWN = "clean_shutdown"
    CLEANUP_TIMEOUT = "cleanup_timeout"


class Metric(_StringEnum):
    PACKETS_SUBMITTED = "packets_submitted"
    PACKETS_SCHEDULED = "packets_scheduled"
    PACKETS_DELIVERED = "packets_delivered"
    PACKETS_DROPPED = "packets_dropped"
    PACKETS_DUPLICATED = "packets_duplicated"
    PACKETS_REORDERED = "packets_reordered"
    BYTES_SUBMITTED = "bytes_submitted"
    BYTES_DELIVERED = "bytes_delivered"
    QUEUE_HIGH_WATER = "queue_high_water"
    ADDRESS_CHANGES = "address_changes"
    RELAY_RESTARTS = "relay_restarts"
    RESOURCES_CLEANED = "resources_cleaned"
    CLEANUP_FAILURES = "cleanup_failures"


class Clock(Protocol):
    def __call__(self) -> float: ...


class RealClock:
    """Monotonic clock marker used by live UDP proxies."""

    is_real_time = True

    def __call__(self) -> float:
        return time.monotonic()


class VirtualClock:
    """Thread-safe manually advanced monotonic clock for deterministic tests."""

    is_real_time = False

    def __init__(self, start: float = 0.0) -> None:
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not math.isfinite(start)
            or start < 0
        ):
            raise ValueError("virtual clock start must be finite and non-negative")
        self._value = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> float:
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError("virtual clock cannot move backwards")
        with self._lock:
            self._value += seconds
            return self._value


def _bounded_number(name: str, value: float, *, low: float, high: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{name} must be between {low} and {high}")


@dataclass(frozen=True)
class ImpairmentProfile:
    """Bounded impairment parameters shared by pure and live channels."""

    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_rate: float = 0.0
    reorder_rate: float = 0.0
    duplicate_rate: float = 0.0
    bandwidth_kbps: float | None = None
    reorder_hold_ms: float = 5.0
    duplicate_gap_ms: float = 0.1
    max_datagram_bytes: int = MAX_UDP_PAYLOAD_BYTES
    max_queue_packets: int = 1_024
    max_report_events: int = 512

    def __post_init__(self) -> None:
        _bounded_number("latency_ms", self.latency_ms, low=0.0, high=60_000.0)
        _bounded_number("jitter_ms", self.jitter_ms, low=0.0, high=60_000.0)
        _bounded_number("loss_rate", self.loss_rate, low=0.0, high=1.0)
        _bounded_number("reorder_rate", self.reorder_rate, low=0.0, high=1.0)
        _bounded_number("duplicate_rate", self.duplicate_rate, low=0.0, high=1.0)
        _bounded_number("reorder_hold_ms", self.reorder_hold_ms, low=0.0, high=60_000.0)
        _bounded_number(
            "duplicate_gap_ms", self.duplicate_gap_ms, low=0.0, high=60_000.0
        )
        if self.bandwidth_kbps is not None:
            _bounded_number(
                "bandwidth_kbps", self.bandwidth_kbps, low=0.001, high=10_000_000.0
            )
        if (
            not isinstance(self.max_datagram_bytes, int)
            or isinstance(self.max_datagram_bytes, bool)
            or not 1 <= self.max_datagram_bytes <= MAX_UDP_PAYLOAD_BYTES
        ):
            raise ValueError("max_datagram_bytes is outside the UDP payload bound")
        if (
            not isinstance(self.max_queue_packets, int)
            or isinstance(self.max_queue_packets, bool)
            or not 1 <= self.max_queue_packets <= MAX_QUEUE_CAPACITY
        ):
            raise ValueError("max_queue_packets exceeds the hard queue bound")
        if (
            not isinstance(self.max_report_events, int)
            or isinstance(self.max_report_events, bool)
            or not 1 <= self.max_report_events <= MAX_REPORT_EVENT_CAPACITY
        ):
            raise ValueError("max_report_events exceeds the hard report bound")

    def report_copy(self) -> dict[str, int | float | None]:
        return {
            "bandwidth_kbps": self.bandwidth_kbps,
            "duplicate_gap_ms": self.duplicate_gap_ms,
            "duplicate_rate": self.duplicate_rate,
            "jitter_ms": self.jitter_ms,
            "latency_ms": self.latency_ms,
            "loss_rate": self.loss_rate,
            "max_datagram_bytes": self.max_datagram_bytes,
            "max_queue_packets": self.max_queue_packets,
            "max_report_events": self.max_report_events,
            "reorder_hold_ms": self.reorder_hold_ms,
            "reorder_rate": self.reorder_rate,
        }


def _safe_token(token: str) -> str:
    if not isinstance(token, str) or _SAFE_TOKEN.fullmatch(token) is None:
        raise ValueError("endpoint token must be a short lowercase opaque token")
    return token


REMOTE_IMPAIRMENT_REPORT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:webjam:test:remote-impairment-report:1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "seed",
        "profile",
        "summary",
        "events",
        "events_truncated",
        "privacy",
    ],
    "properties": {
        "schema_version": {"const": "webjam.remote-impairment-report.v1"},
        "seed": {"type": "integer", "minimum": 0},
        "profile": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "bandwidth_kbps",
                "duplicate_gap_ms",
                "duplicate_rate",
                "jitter_ms",
                "latency_ms",
                "loss_rate",
                "max_datagram_bytes",
                "max_queue_packets",
                "max_report_events",
                "reorder_hold_ms",
                "reorder_rate",
            ],
            "properties": {
                "bandwidth_kbps": {"type": ["number", "null"]},
                "duplicate_gap_ms": {"type": "number", "minimum": 0},
                "duplicate_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "jitter_ms": {"type": "number", "minimum": 0},
                "latency_ms": {"type": "number", "minimum": 0},
                "loss_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "max_datagram_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_UDP_PAYLOAD_BYTES,
                },
                "max_queue_packets": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_QUEUE_CAPACITY,
                },
                "max_report_events": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_REPORT_EVENT_CAPACITY,
                },
                "reorder_hold_ms": {"type": "number", "minimum": 0},
                "reorder_rate": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["category_counts", "metrics", "reason_counts"],
            "properties": {
                "category_counts": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [item.value for item in EventCategory],
                    "properties": {
                        item.value: {"type": "integer", "minimum": 0}
                        for item in EventCategory
                    },
                },
                "metrics": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [item.value for item in Metric],
                    "properties": {
                        item.value: {"type": "integer", "minimum": 0} for item in Metric
                    },
                },
                "reason_counts": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [item.value for item in ReasonCode],
                    "properties": {
                        item.value: {"type": "integer", "minimum": 0}
                        for item in ReasonCode
                    },
                },
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "sequence",
                    "at_ms",
                    "category",
                    "reason",
                    "amount",
                ],
                "properties": {
                    "sequence": {"type": "integer", "minimum": 1},
                    "at_ms": {"type": "integer", "minimum": 0},
                    "category": {"enum": [item.value for item in EventCategory]},
                    "reason": {"enum": [item.value for item in ReasonCode]},
                    "amount": {"type": "integer", "minimum": 1},
                    "traffic": {"enum": [item.value for item in TrafficClass]},
                    "path": {"enum": [item.value for item in PathKind]},
                    "endpoint": {
                        "type": "string",
                        "pattern": _SAFE_TOKEN_SCHEMA_PATTERN,
                    },
                    "generation": {"type": "integer", "minimum": 1},
                },
            },
        },
        "events_truncated": {"type": "integer", "minimum": 0},
        "privacy": {
            "const": {
                "contains_network_addresses": False,
                "contains_packet_content": False,
                "contains_process_arguments": False,
                "time_basis": "relative_monotonic_ms",
            }
        },
    },
}


class PrivacySafeReport:
    """Bounded report accumulator with no free-form event fields."""

    def __init__(
        self,
        *,
        seed: int,
        profile: ImpairmentProfile,
        clock: Clock,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self._seed = seed
        self._profile = profile
        self._clock = clock
        self._started = clock()
        self._events: deque[dict[str, object]] = deque(maxlen=profile.max_report_events)
        self._sequence = 0
        self._truncated = 0
        self._metrics: Counter[str] = Counter()
        self._categories: Counter[str] = Counter()
        self._reasons: Counter[str] = Counter()
        self._lock = threading.Lock()

    @property
    def profile(self) -> ImpairmentProfile:
        return self._profile

    def metric(self, metric: Metric, amount: int = 1) -> None:
        if (
            not isinstance(metric, Metric)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
        ):
            raise ValueError(
                "report metrics require a fixed metric and non-negative int"
            )
        with self._lock:
            if metric is Metric.QUEUE_HIGH_WATER:
                self._metrics[metric.value] = max(self._metrics[metric.value], amount)
            else:
                self._metrics[metric.value] += amount

    def event(
        self,
        category: EventCategory,
        reason: ReasonCode,
        *,
        traffic: TrafficClass | None = None,
        path: PathKind | None = None,
        endpoint: str | None = None,
        generation: int | None = None,
        amount: int = 1,
    ) -> None:
        if not isinstance(category, EventCategory) or not isinstance(
            reason, ReasonCode
        ):
            raise ValueError("report events require fixed category and reason enums")
        if traffic is not None and not isinstance(traffic, TrafficClass):
            raise ValueError("traffic must be a fixed enum")
        if path is not None and not isinstance(path, PathKind):
            raise ValueError("path must be a fixed enum")
        if endpoint is not None:
            endpoint = _safe_token(endpoint)
        if generation is not None and (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise ValueError("generation must be a positive integer")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            raise ValueError("event amount must be a positive integer")

        at_ms = max(0, round((self._clock() - self._started) * 1_000.0))
        with self._lock:
            self._sequence += 1
            item: dict[str, object] = {
                "amount": amount,
                "at_ms": at_ms,
                "category": category.value,
                "reason": reason.value,
                "sequence": self._sequence,
            }
            if traffic is not None:
                item["traffic"] = traffic.value
            if path is not None:
                item["path"] = path.value
            if endpoint is not None:
                item["endpoint"] = endpoint
            if generation is not None:
                item["generation"] = generation
            if len(self._events) == self._events.maxlen:
                self._truncated += 1
            self._events.append(item)
            self._categories[category.value] += amount
            self._reasons[reason.value] += amount

    def document(self) -> dict[str, object]:
        with self._lock:
            metrics = {item.value: self._metrics[item.value] for item in Metric}
            categories = {
                item.value: self._categories[item.value] for item in EventCategory
            }
            reasons = {item.value: self._reasons[item.value] for item in ReasonCode}
            events = [dict(item) for item in self._events]
            truncated = self._truncated
        document: dict[str, object] = {
            "schema_version": "webjam.remote-impairment-report.v1",
            "seed": self._seed,
            "profile": self._profile.report_copy(),
            "summary": {
                "category_counts": categories,
                "metrics": metrics,
                "reason_counts": reasons,
            },
            "events": events,
            "events_truncated": truncated,
            "privacy": {
                "contains_network_addresses": False,
                "contains_packet_content": False,
                "contains_process_arguments": False,
                "time_basis": "relative_monotonic_ms",
            },
        }
        validate_report(document, max_events=self._profile.max_report_events)
        return document

    def to_json(self) -> str:
        return json.dumps(
            self.document(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )


def validate_report(document: Mapping[str, object], *, max_events: int) -> None:
    """Validate the closed report shape without an optional JSON Schema package."""

    if (
        not isinstance(max_events, int)
        or isinstance(max_events, bool)
        or not 1 <= max_events <= MAX_REPORT_EVENT_CAPACITY
    ):
        raise ValueError("remote impairment report event bound is invalid")

    expected = {
        "schema_version",
        "seed",
        "profile",
        "summary",
        "events",
        "events_truncated",
        "privacy",
    }
    if set(document) != expected:
        raise ValueError("remote impairment report has unexpected top-level fields")
    if document["schema_version"] != "webjam.remote-impairment-report.v1":
        raise ValueError("remote impairment report has an unsupported version")
    if (
        not isinstance(document["seed"], int)
        or isinstance(document["seed"], bool)
        or document["seed"] < 0
    ):
        raise ValueError("remote impairment report seed is invalid")
    expected_profile = {
        "bandwidth_kbps",
        "duplicate_gap_ms",
        "duplicate_rate",
        "jitter_ms",
        "latency_ms",
        "loss_rate",
        "max_datagram_bytes",
        "max_queue_packets",
        "max_report_events",
        "reorder_hold_ms",
        "reorder_rate",
    }
    profile = document["profile"]
    if not isinstance(profile, dict) or set(profile) != expected_profile:
        raise ValueError("remote impairment report profile shape is invalid")
    for key, value in profile.items():
        if key == "bandwidth_kbps" and value is None:
            continue
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError("remote impairment report profile value is invalid")
    for key in ("max_datagram_bytes", "max_queue_packets", "max_report_events"):
        if not isinstance(profile[key], int) or isinstance(profile[key], bool):
            raise ValueError("remote impairment report profile integer is invalid")
    if not 1 <= profile["max_datagram_bytes"] <= MAX_UDP_PAYLOAD_BYTES:
        raise ValueError("remote impairment report datagram bound is invalid")
    if not 1 <= profile["max_queue_packets"] <= MAX_QUEUE_CAPACITY:
        raise ValueError("remote impairment report queue bound is invalid")
    if profile["max_report_events"] != max_events:
        raise ValueError("remote impairment report event bound does not match")
    for key in ("loss_rate", "reorder_rate", "duplicate_rate"):
        if not 0 <= profile[key] <= 1:
            raise ValueError("remote impairment report rate is invalid")
    for key in (
        "duplicate_gap_ms",
        "jitter_ms",
        "latency_ms",
        "reorder_hold_ms",
    ):
        if profile[key] < 0:
            raise ValueError("remote impairment report timing is invalid")
    bandwidth = profile["bandwidth_kbps"]
    if bandwidth is not None and bandwidth <= 0:
        raise ValueError("remote impairment report bandwidth is invalid")
    summary = document["summary"]
    if not isinstance(summary, dict) or set(summary) != {
        "category_counts",
        "metrics",
        "reason_counts",
    }:
        raise ValueError("remote impairment report summary shape is invalid")
    expected_summary_keys = {
        "category_counts": {item.value for item in EventCategory},
        "metrics": {item.value for item in Metric},
        "reason_counts": {item.value for item in ReasonCode},
    }
    for section, keys in expected_summary_keys.items():
        values = summary[section]
        if (
            not isinstance(values, dict)
            or set(values) != keys
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in values.values()
            )
        ):
            raise ValueError(f"remote impairment report {section} is invalid")
    events = document["events"]
    if not isinstance(events, list) or len(events) > max_events:
        raise ValueError("remote impairment report event list is not bounded")
    allowed_event_fields = {
        "sequence",
        "at_ms",
        "category",
        "reason",
        "amount",
        "traffic",
        "path",
        "endpoint",
        "generation",
    }
    for item in events:
        if not isinstance(item, dict) or not set(item) <= allowed_event_fields:
            raise ValueError("remote impairment report event shape is invalid")
        required_event_fields = {
            "sequence",
            "at_ms",
            "category",
            "reason",
            "amount",
        }
        if not required_event_fields <= set(item):
            raise ValueError("remote impairment report event is incomplete")
        if item["category"] not in {value.value for value in EventCategory}:
            raise ValueError("remote impairment report event category is invalid")
        if item["reason"] not in {value.value for value in ReasonCode}:
            raise ValueError("remote impairment report event reason is invalid")
        if "endpoint" in item:
            _safe_token(item["endpoint"])
        for key in ("sequence", "at_ms", "amount", "generation"):
            if key in item and (
                not isinstance(item[key], int)
                or isinstance(item[key], bool)
                or item[key] < (0 if key == "at_ms" else 1)
            ):
                raise ValueError("remote impairment report event number is invalid")
        if "traffic" in item and item["traffic"] not in {
            value.value for value in TrafficClass
        }:
            raise ValueError("remote impairment report traffic is invalid")
        if "path" in item and item["path"] not in {value.value for value in PathKind}:
            raise ValueError("remote impairment report path is invalid")
    truncated = document["events_truncated"]
    if not isinstance(truncated, int) or isinstance(truncated, bool) or truncated < 0:
        raise ValueError("remote impairment report truncation count is invalid")
    expected_privacy = {
        "contains_network_addresses": False,
        "contains_packet_content": False,
        "contains_process_arguments": False,
        "time_basis": "relative_monotonic_ms",
    }
    if document["privacy"] != expected_privacy:
        raise ValueError("remote impairment report privacy declaration is invalid")


@dataclass(frozen=True)
class ScheduledDatagram:
    """Opaque delivery returned by :meth:`ImpairmentChannel.poll_due`."""

    sequence: int
    due_at: float
    traffic: TrafficClass
    path: PathKind
    direction: Direction
    endpoint: str
    generation: int
    duplicate: bool
    _payload: bytes = field(repr=False, compare=False)
    _route: object = field(repr=False, compare=False)
    _path_generation: int = field(repr=False, compare=False)

    @property
    def payload(self) -> bytes:
        return self._payload

    @property
    def route(self) -> object:
        return self._route


@dataclass(frozen=True)
class SubmissionResult:
    accepted: bool
    copies_scheduled: int
    reason: ReasonCode | None = None


class ImpairmentChannel:
    """Seeded, bounded datagram scheduler independent of sockets and threads."""

    def __init__(
        self,
        *,
        profile: ImpairmentProfile,
        seed: int,
        clock: Clock,
        report: PrivacySafeReport,
        default_endpoint: str = "channel-001",
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if profile != report.profile:
            raise ValueError("channel and report impairment profiles must match")
        self.profile = profile
        self._clock = clock
        self._report = report
        self._random = random.Random(seed)
        self.default_endpoint = _safe_token(default_endpoint)
        self._pending: list[tuple[float, int, ScheduledDatagram]] = []
        self._sequence = 0
        self._generations: dict[str, int] = {}
        self._path_generations = {PathKind.DIRECT: 1, PathKind.RELAY: 1}
        self._blackholes: dict[PathKind, float | None] = {}
        self._direct_allowed = True
        self._relay_available = True
        self._next_link_time: dict[tuple[str, PathKind, Direction], float] = {}
        self._media_recovery_pending = {
            PathKind.DIRECT: False,
            PathKind.RELAY: False,
        }
        self._forced_reorders = 0
        self._lock = threading.RLock()
        self.register_endpoint(self.default_endpoint)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def register_endpoint(self, token: str) -> int:
        token = _safe_token(token)
        with self._lock:
            return self._ensure_endpoint_locked(token)

    def generation(self, token: str) -> int:
        token = _safe_token(token)
        with self._lock:
            return self._ensure_endpoint_locked(token)

    def change_address(self, token: str) -> int:
        token = _safe_token(token)
        with self._lock:
            generation = self._ensure_endpoint_locked(token) + 1
            self._generations[token] = generation
            self._report.metric(Metric.ADDRESS_CHANGES)
            self._report.event(
                EventCategory.RECONNECT,
                ReasonCode.ADDRESS_CHANGED,
                endpoint=token,
                generation=generation,
            )
            self._reset_link_timing_locked(lambda link: link[0] == token)
            self._discard_locked(
                lambda item: item.endpoint == token,
                reason=ReasonCode.STALE_GENERATION,
                injected=True,
            )
            return generation

    def force_reorder_next(self, count: int = 1) -> None:
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            or count > self.profile.max_queue_packets
        ):
            raise ValueError("forced reorder count is outside the queue bound")
        with self._lock:
            self._forced_reorders += count

    def begin_blackhole(
        self, path: PathKind, *, duration_s: float | None = None
    ) -> None:
        if not isinstance(path, PathKind):
            raise ValueError("blackhole path must be a fixed enum")
        if duration_s is not None and (
            not isinstance(duration_s, (int, float))
            or isinstance(duration_s, bool)
            or not math.isfinite(duration_s)
            or duration_s <= 0
            or duration_s > 3_600
        ):
            raise ValueError("blackhole duration must be within one hour")
        with self._lock:
            until = None if duration_s is None else self._clock() + duration_s
            self._blackholes[path] = until
            self._report.event(
                EventCategory.INJECTED_OUTAGE, ReasonCode.BLACKHOLE, path=path
            )
            self._reset_link_timing_locked(lambda link: link[1] is path)
            self._discard_locked(
                lambda item: item.path is path,
                reason=ReasonCode.BLACKHOLE,
                injected=True,
            )

    def end_blackhole(self, path: PathKind) -> None:
        if not isinstance(path, PathKind):
            raise ValueError("blackhole path must be a fixed enum")
        with self._lock:
            self._blackholes.pop(path, None)

    def reject_direct_path(self) -> None:
        with self._lock:
            self._direct_allowed = False
            self._report.event(
                EventCategory.INJECTED_OUTAGE,
                ReasonCode.DIRECT_REJECTED,
                path=PathKind.DIRECT,
            )
            self._reset_link_timing_locked(lambda link: link[1] is PathKind.DIRECT)
            self._discard_locked(
                lambda item: item.path is PathKind.DIRECT,
                reason=ReasonCode.DIRECT_REJECTED,
                injected=True,
            )

    def allow_direct_path(self) -> None:
        with self._lock:
            self._direct_allowed = True

    def fail_relay(self) -> None:
        with self._lock:
            self._relay_available = False
            self._report.event(
                EventCategory.INJECTED_OUTAGE,
                ReasonCode.RELAY_FAILED,
                path=PathKind.RELAY,
            )
            self._reset_link_timing_locked(lambda link: link[1] is PathKind.RELAY)
            self._discard_locked(
                lambda item: item.path is PathKind.RELAY,
                reason=ReasonCode.RELAY_FAILED,
                injected=True,
            )

    def restart_relay(self) -> int:
        with self._lock:
            self._relay_available = True
            self._path_generations[PathKind.RELAY] += 1
            generation = self._path_generations[PathKind.RELAY]
            self._reset_link_timing_locked(lambda link: link[1] is PathKind.RELAY)
            self._report.metric(Metric.RELAY_RESTARTS)
            self._report.event(
                EventCategory.RECONNECT,
                ReasonCode.RELAY_RESTARTED,
                path=PathKind.RELAY,
                generation=generation,
            )
            return generation

    def record_reconnect(self, token: str, *, path: PathKind) -> None:
        token = _safe_token(token)
        generation = self.generation(token)
        self._report.event(
            EventCategory.RECONNECT,
            ReasonCode.MANUAL_RECONNECT,
            path=path,
            endpoint=token,
            generation=generation,
        )

    def submit(
        self,
        payload: bytes | bytearray | memoryview,
        *,
        traffic: TrafficClass,
        path: PathKind,
        direction: Direction = Direction.TO_TARGET,
        endpoint: str | None = None,
        generation: int | None = None,
        route: object = None,
        at: float | None = None,
    ) -> SubmissionResult:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("UDP payload must be bytes-like")
        if not isinstance(traffic, TrafficClass):
            raise ValueError("traffic must be a fixed enum")
        if not isinstance(path, PathKind):
            raise ValueError("path must be a fixed enum")
        if not isinstance(direction, Direction):
            raise ValueError("direction must be a fixed enum")
        endpoint = self.default_endpoint if endpoint is None else _safe_token(endpoint)
        packet = bytes(payload)
        now = self._clock() if at is None else at
        if (
            not isinstance(now, (int, float))
            or isinstance(now, bool)
            or not math.isfinite(now)
            or now < 0
        ):
            raise ValueError("submission time must be finite and non-negative")

        with self._lock:
            current_generation = self._ensure_endpoint_locked(endpoint)
            if generation is None:
                generation = current_generation
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
            ):
                raise ValueError("generation must be a positive integer")
            self._report.metric(Metric.PACKETS_SUBMITTED)
            self._report.metric(Metric.BYTES_SUBMITTED, len(packet))
            self._expire_blackholes_locked(now)
            if len(packet) > self.profile.max_datagram_bytes:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.OVERSIZED_DATAGRAM,
                    injected=False,
                )
            if generation != current_generation:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.STALE_GENERATION,
                    injected=True,
                )
            if path is PathKind.DIRECT and not self._direct_allowed:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.DIRECT_REJECTED,
                    injected=True,
                )
            if path is PathKind.RELAY and not self._relay_available:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.RELAY_FAILED,
                    injected=True,
                )
            if path in self._blackholes:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.BLACKHOLE,
                    injected=True,
                )
            if self._random.random() < self.profile.loss_rate:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.SEEDED_LOSS,
                    injected=True,
                )
            if len(self._pending) >= self.profile.max_queue_packets:
                return self._drop_locked(
                    traffic,
                    path,
                    endpoint,
                    generation,
                    ReasonCode.QUEUE_OVERFLOW,
                    injected=False,
                )

            jitter_s = (
                self._random.uniform(-self.profile.jitter_ms, self.profile.jitter_ms)
                / 1_000.0
            )
            base_due = now + max(0.0, self.profile.latency_ms / 1_000.0 + jitter_s)
            if self.profile.bandwidth_kbps is not None:
                link = (endpoint, path, direction)
                start = max(base_due, self._next_link_time.get(link, base_due))
                transmission_s = (
                    len(packet) * 8.0 / (self.profile.bandwidth_kbps * 1_000.0)
                )
                base_due = start + transmission_s
                self._next_link_time[link] = base_due

            forced_reorder = self._forced_reorders > 0
            if forced_reorder:
                self._forced_reorders -= 1
            reordered = forced_reorder or (
                self._random.random() < self.profile.reorder_rate
            )
            if reordered:
                base_due += self.profile.reorder_hold_ms / 1_000.0
                self._report.metric(Metric.PACKETS_REORDERED)

            copies = 1
            duplicate = self._random.random() < self.profile.duplicate_rate
            if duplicate and len(self._pending) + 1 < self.profile.max_queue_packets:
                copies = 2
                self._report.metric(Metric.PACKETS_DUPLICATED)

            self._schedule_locked(
                packet,
                due_at=base_due,
                traffic=traffic,
                path=path,
                direction=direction,
                endpoint=endpoint,
                generation=generation,
                duplicate=False,
                route=route,
            )
            if copies == 2:
                self._schedule_locked(
                    packet,
                    due_at=base_due + self.profile.duplicate_gap_ms / 1_000.0,
                    traffic=traffic,
                    path=path,
                    direction=direction,
                    endpoint=endpoint,
                    generation=generation,
                    duplicate=True,
                    route=route,
                )
            return SubmissionResult(True, copies)

    def poll_due(
        self,
        *,
        at: float | None = None,
        record_delivery: bool = True,
    ) -> tuple[ScheduledDatagram, ...]:
        now = self._clock() if at is None else at
        if (
            not isinstance(now, (int, float))
            or isinstance(now, bool)
            or not math.isfinite(now)
            or now < 0
        ):
            raise ValueError("poll time must be finite and non-negative")
        delivered: list[ScheduledDatagram] = []
        with self._lock:
            self._expire_blackholes_locked(now)
            while self._pending and self._pending[0][0] <= now:
                _, _, item = heapq.heappop(self._pending)
                if self._generations.get(item.endpoint) != item.generation:
                    self._drop_scheduled_locked(
                        item, ReasonCode.STALE_GENERATION, injected=True
                    )
                    continue
                if self._path_generations[item.path] != item._path_generation:
                    self._drop_scheduled_locked(
                        item, ReasonCode.STALE_GENERATION, injected=True
                    )
                    continue
                delivered.append(item)
                if record_delivery:
                    self._record_delivery_locked(item)
            return tuple(delivered)

    def record_delivery(self, item: ScheduledDatagram) -> None:
        """Acknowledge one live-proxy send after the socket accepted it."""

        if not isinstance(item, ScheduledDatagram):
            raise TypeError("delivery acknowledgement requires a scheduled datagram")
        with self._lock:
            self._record_delivery_locked(item)

    def next_due_at(self) -> float | None:
        with self._lock:
            return self._pending[0][0] if self._pending else None

    def discard_pending(self) -> int:
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            return count

    def record_socket_loss(
        self,
        *,
        traffic: TrafficClass,
        path: PathKind,
        endpoint: str,
        reason: ReasonCode,
    ) -> None:
        if reason not in {
            ReasonCode.SOCKET_RECEIVE_ERROR,
            ReasonCode.SOCKET_SEND_ERROR,
            ReasonCode.CLIENT_CHANGED,
        }:
            raise ValueError("socket loss requires a fixed socket reason")
        endpoint = _safe_token(endpoint)
        with self._lock:
            self._drop_locked(
                traffic,
                path,
                endpoint,
                self._ensure_endpoint_locked(endpoint),
                reason,
                injected=False,
            )

    def _schedule_locked(
        self,
        payload: bytes,
        *,
        due_at: float,
        traffic: TrafficClass,
        path: PathKind,
        direction: Direction,
        endpoint: str,
        generation: int,
        duplicate: bool,
        route: object,
    ) -> None:
        self._sequence += 1
        item = ScheduledDatagram(
            sequence=self._sequence,
            due_at=due_at,
            traffic=traffic,
            path=path,
            direction=direction,
            endpoint=endpoint,
            generation=generation,
            duplicate=duplicate,
            _payload=payload,
            _route=route,
            _path_generation=self._path_generations[path],
        )
        heapq.heappush(self._pending, (due_at, self._sequence, item))
        self._report.metric(Metric.PACKETS_SCHEDULED)
        self._report.metric(Metric.QUEUE_HIGH_WATER, len(self._pending))

    def _ensure_endpoint_locked(self, token: str) -> int:
        generation = self._generations.get(token)
        if generation is not None:
            return generation
        if len(self._generations) >= MAX_CHANNEL_ENDPOINTS:
            raise RuntimeError("channel endpoint hard bound reached")
        self._generations[token] = 1
        return 1

    def _drop_locked(
        self,
        traffic: TrafficClass,
        path: PathKind,
        endpoint: str,
        generation: int,
        reason: ReasonCode,
        *,
        injected: bool,
    ) -> SubmissionResult:
        self._record_drop_locked(
            traffic, path, endpoint, generation, reason, injected=injected
        )
        return SubmissionResult(False, 0, reason)

    def _drop_scheduled_locked(
        self, item: ScheduledDatagram, reason: ReasonCode, *, injected: bool
    ) -> None:
        self._record_drop_locked(
            item.traffic,
            item.path,
            item.endpoint,
            item.generation,
            reason,
            injected=injected,
        )

    def _record_drop_locked(
        self,
        traffic: TrafficClass,
        path: PathKind,
        endpoint: str,
        generation: int,
        reason: ReasonCode,
        *,
        injected: bool,
    ) -> None:
        self._report.metric(Metric.PACKETS_DROPPED)
        loss_category = (
            EventCategory.CONTROL_LOSS
            if traffic is TrafficClass.CONTROL
            else EventCategory.JAMULUS_DATAGRAM_LOSS
        )
        self._report.event(
            loss_category,
            reason,
            traffic=traffic,
            path=path,
            endpoint=endpoint,
            generation=max(1, generation),
        )
        self._report.event(
            EventCategory.INJECTED_OUTAGE
            if injected
            else EventCategory.UNEXPECTED_TRANSPORT_LOSS,
            reason,
            traffic=traffic,
            path=path,
            endpoint=endpoint,
            generation=max(1, generation),
        )
        if traffic is TrafficClass.JAMULUS_DATAGRAM:
            self._media_recovery_pending[path] = True

    def _record_delivery_locked(self, item: ScheduledDatagram) -> None:
        self._report.metric(Metric.PACKETS_DELIVERED)
        self._report.metric(Metric.BYTES_DELIVERED, len(item.payload))
        if (
            item.traffic is TrafficClass.JAMULUS_DATAGRAM
            and self._media_recovery_pending[item.path]
        ):
            self._media_recovery_pending[item.path] = False
            self._report.event(
                EventCategory.MEDIA_RECOVERY,
                ReasonCode.PACKET_DELIVERED,
                traffic=item.traffic,
                path=item.path,
                endpoint=item.endpoint,
                generation=item.generation,
            )

    def _discard_locked(
        self,
        predicate: Callable[[ScheduledDatagram], bool],
        *,
        reason: ReasonCode,
        injected: bool,
    ) -> None:
        retained: list[tuple[float, int, ScheduledDatagram]] = []
        for entry in self._pending:
            item = entry[2]
            if predicate(item):
                self._drop_scheduled_locked(item, reason, injected=injected)
            else:
                retained.append(entry)
        heapq.heapify(retained)
        self._pending = retained

    def _expire_blackholes_locked(self, now: float) -> None:
        for path, until in tuple(self._blackholes.items()):
            if until is not None and now >= until:
                del self._blackholes[path]

    def _reset_link_timing_locked(
        self,
        predicate: Callable[[tuple[str, PathKind, Direction]], bool],
    ) -> None:
        self._next_link_time = {
            link: value
            for link, value in self._next_link_time.items()
            if not predicate(link)
        }


def _loopback_address(
    address: tuple[str, int] | tuple[str, int, int, int],
) -> tuple[int, tuple[str, int] | tuple[str, int, int, int]]:
    if not isinstance(address, tuple) or len(address) not in {2, 4}:
        raise ValueError("UDP address must be a numeric loopback tuple")
    host, port = address[0], address[1]
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("UDP address must use a numeric loopback literal") from exc
    if not parsed.is_loopback:
        raise ValueError("impairment lab UDP addresses must remain on loopback")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("UDP port is invalid")
    family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
    if family == socket.AF_INET6:
        flowinfo = address[2] if len(address) == 4 else 0
        scope_id = address[3] if len(address) == 4 else 0
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (flowinfo, scope_id)
        ):
            raise ValueError(
                "IPv6 flow and scope identifiers must be non-negative ints"
            )
        return family, (str(parsed), port, flowinfo, scope_id)
    return family, (str(parsed), port)


def _new_loopback_socket(family: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(0.003)
    bind_address = ("::1", 0, 0, 0) if family == socket.AF_INET6 else ("127.0.0.1", 0)
    sock.bind(bind_address)
    return sock


class BoundUDPEndpoint:
    """Owned loopback UDP socket with explicit address-generation changes."""

    def __init__(
        self,
        *,
        token: str,
        family: int,
        report: PrivacySafeReport,
    ) -> None:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("only IPv4 and IPv6 loopback endpoints are supported")
        self.token = _safe_token(token)
        self._family = family
        self._report = report
        self._socket = _new_loopback_socket(family)
        self._generation = 1
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"BoundUDPEndpoint(token={self.token!r}, "
            f"generation={self._generation}, closed={self._closed})"
        )

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def socket(self) -> socket.socket:
        return self._socket

    @property
    def local_address(self) -> tuple[object, ...]:
        return self._socket.getsockname()

    def rotate_address(self) -> tuple[object, ...]:
        with self._lock:
            if self._closed:
                raise RuntimeError("endpoint is closed")
            replacement = _new_loopback_socket(self._family)
            old = self._socket
            self._socket = replacement
            self._generation += 1
            old.close()
            self._report.metric(Metric.ADDRESS_CHANGES)
            self._report.event(
                EventCategory.RECONNECT,
                ReasonCode.ADDRESS_CHANGED,
                endpoint=self.token,
                generation=self._generation,
            )
            return replacement.getsockname()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._socket.close()
        self._report.metric(Metric.RESOURCES_CLEANED)


class UDPImpairmentProxy:
    """One-client, bidirectional loopback UDP proxy using an impairment channel."""

    def __init__(
        self,
        *,
        token: str,
        target: tuple[str, int] | tuple[str, int, int, int],
        traffic: TrafficClass,
        path: PathKind,
        channel: ImpairmentChannel,
        report: PrivacySafeReport,
    ) -> None:
        if not isinstance(traffic, TrafficClass):
            raise ValueError("proxy traffic must be a fixed enum")
        if not isinstance(path, PathKind):
            raise ValueError("proxy path must be a fixed enum")
        family, checked_target = _loopback_address(target)
        self.token = _safe_token(token)
        self.traffic = traffic
        self.path = path
        self.channel = channel
        self._report = report
        self._family = family
        self._target = checked_target
        self._socket = _new_loopback_socket(family)
        self._client: tuple[object, ...] | None = None
        self._socket_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self.channel.register_endpoint(self.token)
        self._thread = threading.Thread(
            target=self._run,
            name=f"webjam-impairment-{self.token}",
            daemon=True,
        )
        self._thread.start()

    def __repr__(self) -> str:
        return (
            f"UDPImpairmentProxy(token={self.token!r}, path={self.path.value!r}, "
            f"generation={self.generation}, closed={self._closed})"
        )

    @property
    def local_address(self) -> tuple[object, ...]:
        with self._socket_lock:
            return self._socket.getsockname()

    @property
    def generation(self) -> int:
        return self.channel.generation(self.token)

    @property
    def thread_alive(self) -> bool:
        return self._thread.is_alive()

    def rotate_address(self) -> tuple[object, ...]:
        with self._socket_lock:
            if self._closed:
                raise RuntimeError("proxy is closed")
            replacement = _new_loopback_socket(self._family)
            old = self._socket
            self._socket = replacement
            self._client = None
            old.close()
        self.channel.change_address(self.token)
        return replacement.getsockname()

    def update_target(
        self, target: tuple[str, int] | tuple[str, int, int, int]
    ) -> None:
        family, checked_target = _loopback_address(target)
        if family != self._family:
            raise ValueError("target address family cannot change in place")
        with self._socket_lock:
            if self._closed:
                raise RuntimeError("proxy is closed")
            self._target = checked_target
        self.channel.change_address(self.token)

    def close(self, *, timeout_s: float = 0.5) -> None:
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
            or timeout_s > 5
        ):
            raise ValueError("proxy cleanup timeout is invalid")
        with self._socket_lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            self._socket.close()
        self._thread.join(timeout_s)
        self.channel.discard_pending()
        if self._thread.is_alive():
            self._report.metric(Metric.CLEANUP_FAILURES)
            self._report.event(
                EventCategory.CLEANUP,
                ReasonCode.CLEANUP_TIMEOUT,
                endpoint=self.token,
                generation=self.generation,
            )
            raise RuntimeError("UDP impairment proxy did not stop within its bound")
        self._report.metric(Metric.RESOURCES_CLEANED)

    def _run(self) -> None:
        receive_size = min(
            MAX_UDP_PAYLOAD_BYTES + 1, self.channel.profile.max_datagram_bytes + 1
        )
        while not self._stop.is_set():
            with self._socket_lock:
                sock = self._socket
                target = self._target
            try:
                payload, source = sock.recvfrom(receive_size)
            except socket.timeout:
                pass
            except OSError:
                with self._socket_lock:
                    replaced = sock is not self._socket
                if not self._stop.is_set() and not replaced:
                    self.channel.record_socket_loss(
                        traffic=self.traffic,
                        path=self.path,
                        endpoint=self.token,
                        reason=ReasonCode.SOCKET_RECEIVE_ERROR,
                    )
            else:
                if _same_udp_address(source, target):
                    with self._socket_lock:
                        client = self._client
                    if client is not None:
                        self.channel.submit(
                            payload,
                            traffic=self.traffic,
                            path=self.path,
                            direction=Direction.FROM_TARGET,
                            endpoint=self.token,
                            generation=self.generation,
                            route=client,
                        )
                else:
                    with self._socket_lock:
                        if self._client is None:
                            self._client = source
                        elif not _same_udp_address(source, self._client):
                            self.channel.record_socket_loss(
                                traffic=self.traffic,
                                path=self.path,
                                endpoint=self.token,
                                reason=ReasonCode.CLIENT_CHANGED,
                            )
                            source = None
                    if source is not None:
                        self.channel.submit(
                            payload,
                            traffic=self.traffic,
                            path=self.path,
                            direction=Direction.TO_TARGET,
                            endpoint=self.token,
                            generation=self.generation,
                            route=target,
                        )
            self._deliver_due()

    def _deliver_due(self) -> None:
        for item in self.channel.poll_due(record_delivery=False):
            route = item.route
            if not isinstance(route, tuple):
                self.channel.record_socket_loss(
                    traffic=item.traffic,
                    path=item.path,
                    endpoint=item.endpoint,
                    reason=ReasonCode.SOCKET_SEND_ERROR,
                )
                continue
            with self._socket_lock:
                sock = self._socket
            try:
                sock.sendto(item.payload, route)
            except OSError:
                if not self._stop.is_set():
                    self.channel.record_socket_loss(
                        traffic=item.traffic,
                        path=item.path,
                        endpoint=item.endpoint,
                        reason=ReasonCode.SOCKET_SEND_ERROR,
                    )
            else:
                self.channel.record_delivery(item)


def _same_udp_address(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    return (
        len(left) >= 2
        and len(right) >= 2
        and left[0] == right[0]
        and left[1] == right[1]
    )


class TrackedProcess:
    """A child process owned by the lab and stopped with a hard time bound."""

    def __init__(
        self,
        *,
        token: str,
        process: subprocess.Popen[bytes],
        owns_group: bool,
        report: PrivacySafeReport,
    ) -> None:
        self.token = _safe_token(token)
        self.process = process
        self._owns_group = owns_group
        self._report = report
        self._closed = False

    def __repr__(self) -> str:
        return f"TrackedProcess(token={self.token!r}, running={self.process.poll() is None})"

    def close(self, *, timeout_s: float = 0.4) -> None:
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
            or timeout_s > 5
        ):
            raise ValueError("process cleanup timeout is invalid")
        if self._closed:
            return
        self._closed = True
        if self.process.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                try:
                    self.process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired as exc:
                    self._report.metric(Metric.CLEANUP_FAILURES)
                    self._report.event(
                        EventCategory.CLEANUP,
                        ReasonCode.CLEANUP_TIMEOUT,
                        endpoint=self.token,
                    )
                    raise RuntimeError(
                        "owned process did not stop within its bound"
                    ) from exc
        self._report.metric(Metric.RESOURCES_CLEANED)

    def _signal(self, sig: signal.Signals) -> None:
        try:
            if self._owns_group and os.name == "posix":
                os.killpg(self.process.pid, sig)
            elif sig is signal.SIGTERM:
                self.process.terminate()
            else:
                self.process.kill()
        except ProcessLookupError:
            pass


class RemoteImpairmentLab:
    """Own channels, loopback sockets, proxies, processes, and one safe report."""

    def __init__(
        self,
        *,
        profile: ImpairmentProfile | None = None,
        seed: int = 1,
        clock: Clock | None = None,
        max_proxies: int = 4,
        max_processes: int = 4,
    ) -> None:
        if (
            not isinstance(max_proxies, int)
            or isinstance(max_proxies, bool)
            or not 1 <= max_proxies <= MAX_LAB_PROXIES
        ):
            raise ValueError("max_proxies exceeds the hard lab bound")
        if (
            not isinstance(max_processes, int)
            or isinstance(max_processes, bool)
            or not 1 <= max_processes <= MAX_LAB_PROCESSES
        ):
            raise ValueError("max_processes exceeds the hard lab bound")
        self.profile = profile or ImpairmentProfile()
        self.seed = seed
        self.clock = clock or RealClock()
        self.report = PrivacySafeReport(
            seed=seed, profile=self.profile, clock=self.clock
        )
        self._seed_source = random.Random(seed)
        self._max_proxies = max_proxies
        self._max_processes = max_processes
        self._channels: list[ImpairmentChannel] = []
        self._endpoints: list[BoundUDPEndpoint] = []
        self._proxies: list[UDPImpairmentProxy] = []
        self._processes: list[TrackedProcess] = []
        self._counts: Counter[str] = Counter()
        self._closed = False
        self._lock = threading.Lock()

    def __enter__(self) -> RemoteImpairmentLab:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def active_thread_count(self) -> int:
        return sum(proxy.thread_alive for proxy in self._proxies)

    @property
    def running_process_count(self) -> int:
        return sum(item.process.poll() is None for item in self._processes)

    def new_channel(self, *, token: str | None = None) -> ImpairmentChannel:
        with self._lock:
            self._ensure_open()
            if len(self._channels) >= MAX_LAB_CHANNELS:
                raise RuntimeError("impairment channel hard bound reached")
            self._counts["channel"] += 1
            token = token or f"channel-{self._counts['channel']:03d}"
            token = _safe_token(token)
            channel = ImpairmentChannel(
                profile=self.profile,
                seed=self._seed_source.getrandbits(63),
                clock=self.clock,
                report=self.report,
                default_endpoint=token,
            )
            self._channels.append(channel)
            return channel

    def bind_udp_endpoint(self, *, family: int = socket.AF_INET) -> BoundUDPEndpoint:
        with self._lock:
            self._ensure_open()
            if len(self._endpoints) >= MAX_LAB_ENDPOINTS:
                raise RuntimeError("UDP endpoint hard bound reached")
            self._counts["endpoint"] += 1
            endpoint = BoundUDPEndpoint(
                token=f"endpoint-{self._counts['endpoint']:03d}",
                family=family,
                report=self.report,
            )
            self._endpoints.append(endpoint)
            return endpoint

    def start_udp_proxy(
        self,
        *,
        target: tuple[str, int] | tuple[str, int, int, int],
        traffic: TrafficClass = TrafficClass.JAMULUS_DATAGRAM,
        path: PathKind = PathKind.DIRECT,
    ) -> UDPImpairmentProxy:
        if not getattr(self.clock, "is_real_time", False):
            raise ValueError("live UDP proxies require a real monotonic clock")
        with self._lock:
            self._ensure_open()
            if len(self._proxies) >= self._max_proxies:
                raise RuntimeError("live UDP proxy bound reached")
            if len(self._channels) >= MAX_LAB_CHANNELS:
                raise RuntimeError("impairment channel hard bound reached")
            self._counts["proxy"] += 1
            token = f"proxy-{self._counts['proxy']:03d}"
            channel = ImpairmentChannel(
                profile=self.profile,
                seed=self._seed_source.getrandbits(63),
                clock=self.clock,
                report=self.report,
                default_endpoint=token,
            )
            proxy = UDPImpairmentProxy(
                token=token,
                target=target,
                traffic=traffic,
                path=path,
                channel=channel,
                report=self.report,
            )
            self._channels.append(channel)
            self._proxies.append(proxy)
            return proxy

    def start_process(
        self,
        command: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> TrackedProcess:
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("process command must be a non-empty argv sequence")
        if any(
            not isinstance(item, str) or not item or "\x00" in item for item in command
        ):
            raise ValueError("process argv must contain only non-empty strings")
        if not os.path.isabs(command[0]):
            raise ValueError("owned process executable must use an absolute path")
        with self._lock:
            self._ensure_open()
            if len(self._processes) >= self._max_processes:
                raise RuntimeError("owned process bound reached")
            self._counts["process"] += 1
            kwargs: dict[str, object] = {
                "cwd": cwd,
                "env": None if env is None else dict(env),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "shell": False,
            }
            owns_group = os.name == "posix"
            if owns_group:
                kwargs["start_new_session"] = True
            elif os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(tuple(command), **kwargs)
            tracked = TrackedProcess(
                token=f"process-{self._counts['process']:03d}",
                process=process,
                owns_group=owns_group,
                report=self.report,
            )
            self._processes.append(tracked)
            return tracked

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        failures: list[BaseException] = []
        for process in reversed(self._processes):
            try:
                process.close()
            except BaseException as exc:  # cleanup must continue across all resources
                failures.append(exc)
        for proxy in reversed(self._proxies):
            try:
                proxy.close()
            except BaseException as exc:  # cleanup must continue across all resources
                failures.append(exc)
        for endpoint in reversed(self._endpoints):
            try:
                endpoint.close()
            except BaseException as exc:  # cleanup must continue across all resources
                failures.append(exc)
        for channel in self._channels:
            channel.discard_pending()
        self.report.event(
            EventCategory.CLEANUP,
            ReasonCode.CLEANUP_TIMEOUT if failures else ReasonCode.CLEAN_SHUTDOWN,
            amount=max(1, len(failures)),
        )
        if failures:
            raise RuntimeError(
                f"remote impairment lab cleanup had {len(failures)} bounded failure(s)"
            ) from failures[0]

    def report_json(self) -> str:
        return self.report.to_json()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("remote impairment lab is closed")


__all__ = [
    "BoundUDPEndpoint",
    "Direction",
    "EventCategory",
    "ImpairmentChannel",
    "ImpairmentProfile",
    "MAX_CHANNEL_ENDPOINTS",
    "MAX_LAB_CHANNELS",
    "MAX_LAB_ENDPOINTS",
    "MAX_LAB_PROCESSES",
    "MAX_LAB_PROXIES",
    "MAX_QUEUE_CAPACITY",
    "MAX_REPORT_EVENT_CAPACITY",
    "MAX_UDP_PAYLOAD_BYTES",
    "Metric",
    "PathKind",
    "PrivacySafeReport",
    "REMOTE_IMPAIRMENT_REPORT_SCHEMA",
    "RealClock",
    "ReasonCode",
    "RemoteImpairmentLab",
    "ScheduledDatagram",
    "SubmissionResult",
    "TrackedProcess",
    "TrafficClass",
    "UDPImpairmentProxy",
    "VirtualClock",
    "validate_report",
]

"""Bounded, memory-only human lesson requests; no media or network effects."""

from __future__ import annotations

import math
import re
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

MAX_ADMISSIONS = 32
MAX_REVISION = 2_147_483_647
MAX_REQUEST_BYTES = 512
REQUEST_TTL_S = 30.0
PRESENCE_TTL_S = 5.0
PARTICIPANT_INTERVAL_S = 2.0
GLOBAL_INTERVAL_S = 10.0
GLOBAL_REQUEST_LIMIT = 20
_IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
_ERRORS = frozenset({
    "invalid_request", "unsupported", "context_stale", "admission_stale",
    "presence_stale", "superseded", "revision_conflict", "revision_exhausted",
    "rate_limited", "expired",
})


class LessonRequestError(RuntimeError):
    """A fixed safe refusal; never include received identifiers or body text."""

    def __init__(self, code: str, *, retry_after_ms: int | None = None) -> None:
        self.code = code if code in _ERRORS else "invalid_request"
        self.retry_after_ms = (
            retry_after_ms
            if self.code == "rate_limited"
            and type(retry_after_ms) is int and 1 <= retry_after_ms <= 10_000
            else None
        )
        super().__init__(f"Lesson request unavailable ({self.code}).")


class LessonRequestIntent(str, Enum):
    PAUSE = "pause"
    READY = "ready"


def _identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise LessonRequestError("invalid_request")


def _revision(value: object) -> None:
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise LessonRequestError("invalid_request")


def _intent(value: object) -> LessonRequestIntent:
    if not isinstance(value, LessonRequestIntent) and type(value) is not str:
        raise LessonRequestError("invalid_request")
    try:
        return LessonRequestIntent(value)
    except ValueError:
        raise LessonRequestError("invalid_request") from None


def _shape(value: object, fields: set[str]) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LessonRequestError("invalid_request")
    return value


@dataclass(frozen=True, repr=False)
class LessonRequestCommand:
    context_id: str
    admission_id: str
    revision: int
    intent: LessonRequestIntent

    def __post_init__(self) -> None:
        _identifier(self.context_id)
        _identifier(self.admission_id)
        _revision(self.revision)
        object.__setattr__(self, "intent", _intent(self.intent))

    def to_mapping(self) -> dict:
        return {
            "version": 1, "context_id": self.context_id,
            "admission_id": self.admission_id, "revision": self.revision,
            "intent": self.intent.value,
        }

    @classmethod
    def from_mapping(cls, value: object) -> LessonRequestCommand:
        value = _shape(value, {
            "version", "context_id", "admission_id", "revision", "intent",
        })
        if type(value["version"]) is not int or value["version"] != 1:
            raise LessonRequestError("invalid_request")
        return cls(value["context_id"], value["admission_id"],
                   value["revision"], value["intent"])


@dataclass(frozen=True, repr=False)
class LessonRequestReceipt:
    revision: int
    intent: LessonRequestIntent
    state: str
    expires_in_ms: int

    def __post_init__(self) -> None:
        _revision(self.revision)
        object.__setattr__(self, "intent", _intent(self.intent))
        if (
            type(self.state) is not str
            or self.state not in {"accepted", "acknowledged", "expired"}
            or type(self.expires_in_ms) is not int
            or not 0 <= self.expires_in_ms <= 30_000
            or (self.state == "expired") != (self.expires_in_ms == 0)
        ):
            raise LessonRequestError("invalid_request")

    def to_mapping(self) -> dict:
        return {"revision": self.revision, "intent": self.intent.value,
                "state": self.state, "expires_in_ms": self.expires_in_ms}

    @classmethod
    def from_mapping(cls, value: object) -> LessonRequestReceipt:
        value = _shape(value, {"revision", "intent", "state", "expires_in_ms"})
        return cls(**value)


@dataclass(frozen=True, repr=False)
class LessonRequestView:
    availability: str
    context_id: str = ""
    admission_id: str = ""
    next_revision: int | None = None
    own_receipt: LessonRequestReceipt | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.availability == "unavailable":
            if (type(self.reason) is not str or self.reason not in {"inactive", "capacity"} or self.context_id
                    or self.admission_id or self.next_revision is not None
                    or self.own_receipt is not None):
                raise LessonRequestError("invalid_request")
            return
        if self.availability != "active" or self.reason:
            raise LessonRequestError("invalid_request")
        _identifier(self.context_id)
        _identifier(self.admission_id)
        if self.own_receipt is not None and not isinstance(
            self.own_receipt, LessonRequestReceipt
        ):
            raise LessonRequestError("invalid_request")
        high_water = self.own_receipt.revision if self.own_receipt else 0
        expected = high_water + 1 if high_water < MAX_REVISION else None
        if self.next_revision is not None:
            _revision(self.next_revision)
        if self.next_revision != expected:
            raise LessonRequestError("invalid_request")

    def to_mapping(self) -> dict:
        if self.availability == "unavailable":
            return {"version": 1, "availability": "unavailable", "reason": self.reason}
        return {
            "version": 1, "availability": "active", "context_id": self.context_id,
            "admission_id": self.admission_id, "next_revision": self.next_revision,
            "own_receipt": self.own_receipt.to_mapping() if self.own_receipt else None,
        }

    @classmethod
    def from_mapping(cls, value: object) -> LessonRequestView:
        if not isinstance(value, Mapping):
            raise LessonRequestError("invalid_request")
        if type(value.get("version")) is not int or value["version"] != 1:
            raise LessonRequestError("invalid_request")
        if value.get("availability") == "unavailable":
            value = _shape(value, {"version", "availability", "reason"})
            return cls("unavailable", reason=value["reason"])
        value = _shape(value, {
            "version", "availability", "context_id", "admission_id",
            "next_revision", "own_receipt",
        })
        receipt = value["own_receipt"]
        return cls(value["availability"], value["context_id"], value["admission_id"],
                   value["next_revision"],
                   LessonRequestReceipt.from_mapping(receipt) if receipt is not None else None)


@dataclass(frozen=True, repr=False)
class LessonRequestNotice:
    participant_id: str
    context_id: str
    admission_id: str
    revision: int
    intent: LessonRequestIntent
    state: str
    expires_in_ms: int


@dataclass(repr=False)
class _Admission:
    admission_id: str
    last_read: float
    revision: int = 0
    intent: LessonRequestIntent = LessonRequestIntent.PAUSE
    acknowledged: bool = False
    deadline: float = 0.0
    last_new: float | None = None


class LessonRequestStore:
    """One retained admission/high-water per participant, scoped to one context."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._context = ""
        self._admissions: dict[str, _Admission] = {}
        self._new_requests: deque[float] = deque()

    def activate(self) -> str:
        with self._lock:
            self._context = secrets.token_hex(16)
            self._admissions.clear()
            self._new_requests.clear()
            return self._context

    def retire(self) -> None:
        with self._lock:
            self._context = ""
            self._admissions.clear()
            self._new_requests.clear()

    @staticmethod
    def _participant(value: object) -> None:
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise LessonRequestError("invalid_request") from None

    def record_state_read(self, participant_id: str, *, read_at: float) -> LessonRequestView:
        self._participant(participant_id)
        with self._lock:
            now = self._clock()
            if (type(read_at) not in {int, float} or not math.isfinite(read_at)
                    or not 0 <= now - read_at < PRESENCE_TTL_S):
                raise LessonRequestError("presence_stale")
            if not self._context:
                return LessonRequestView("unavailable", reason="inactive")
            entry = self._admissions.get(participant_id)
            if entry is None:
                if len(self._admissions) >= MAX_ADMISSIONS:
                    return LessonRequestView("unavailable", reason="capacity")
                entry = _Admission(secrets.token_hex(16), read_at)
                self._admissions[participant_id] = entry
            entry.last_read = max(entry.last_read, read_at)
            return self._view(entry, now)

    def _receipt(self, entry: _Admission, now: float) -> LessonRequestReceipt | None:
        if not entry.revision:
            return None
        remaining = max(0, min(30_000, math.ceil((entry.deadline - now) * 1000)))
        state = "expired" if not remaining else (
            "acknowledged" if entry.acknowledged else "accepted"
        )
        return LessonRequestReceipt(entry.revision, entry.intent, state, remaining)

    def _view(self, entry: _Admission, now: float) -> LessonRequestView:
        return LessonRequestView(
            "active", self._context, entry.admission_id,
            entry.revision + 1 if entry.revision < MAX_REVISION else None,
            self._receipt(entry, now),
        )

    def current_view(self, participant_id: str) -> LessonRequestView:
        with self._lock:
            if not self._context:
                return LessonRequestView("unavailable", reason="inactive")
            entry = self._admissions.get(participant_id)
            if entry is None:
                raise LessonRequestError("admission_stale")
            return self._view(entry, self._clock())

    def _current(self, participant_id: str, context_id: str,
                 admission_id: str, now: float) -> _Admission:
        if not self._context or context_id != self._context:
            raise LessonRequestError("context_stale")
        entry = self._admissions.get(participant_id)
        if entry is None or admission_id != entry.admission_id:
            raise LessonRequestError("admission_stale")
        if not 0 <= now - entry.last_read < PRESENCE_TTL_S:
            raise LessonRequestError("presence_stale")
        return entry

    def submit(self, participant_id: str,
               command: LessonRequestCommand) -> LessonRequestView:
        if not isinstance(command, LessonRequestCommand):
            raise LessonRequestError("invalid_request")
        with self._lock:
            now = self._clock()
            entry = self._current(participant_id, command.context_id, command.admission_id, now)
            if command.revision < entry.revision:
                raise LessonRequestError("superseded")
            if command.revision == entry.revision:
                if command.intent != entry.intent:
                    raise LessonRequestError("revision_conflict")
                return self._view(entry, now)
            while self._new_requests and self._new_requests[0] <= now - GLOBAL_INTERVAL_S:
                self._new_requests.popleft()
            wait = 0.0
            if entry.last_new is not None:
                wait = max(0.0, entry.last_new + PARTICIPANT_INTERVAL_S - now)
            if len(self._new_requests) >= GLOBAL_REQUEST_LIMIT:
                wait = max(wait, self._new_requests[0] + GLOBAL_INTERVAL_S - now)
            if wait > 0:
                raise LessonRequestError("rate_limited", retry_after_ms=min(10_000, math.ceil(wait * 1000)))
            entry.revision = command.revision
            entry.intent = command.intent
            entry.acknowledged = False
            entry.deadline = now + REQUEST_TTL_S
            entry.last_new = now
            self._new_requests.append(now)
            return self._view(entry, now)

    def host_notices(self) -> tuple[LessonRequestNotice, ...]:
        with self._lock:
            now = self._clock()
            result = []
            for participant, entry in self._admissions.items():
                receipt = self._receipt(entry, now)
                if (receipt is None or receipt.state == "expired"
                        or not 0 <= now - entry.last_read < PRESENCE_TTL_S):
                    continue
                result.append(LessonRequestNotice(
                    participant, self._context, entry.admission_id, receipt.revision,
                    receipt.intent, receipt.state, receipt.expires_in_ms,
                ))
            return tuple(result)

    def acknowledge(self, context_id: str, admission_id: str, revision: int) -> None:
        _revision(revision)
        with self._lock:
            now = self._clock()
            participant = next((key for key, entry in self._admissions.items()
                                if entry.admission_id == admission_id), "")
            entry = self._current(participant, context_id, admission_id, now)
            if revision < entry.revision:
                raise LessonRequestError("superseded")
            if revision != entry.revision:
                raise LessonRequestError("revision_conflict")
            if now >= entry.deadline:
                raise LessonRequestError("expired")
            entry.acknowledged = True

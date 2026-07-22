"""Framework-neutral protocol primitives for the WebJam Pocket Stage.

This module deliberately owns no socket, HTTP server, UI callback, or durable
device credential.  It supplies the narrow security and data contracts needed
at those boundaries:

* a bounded, strictly versioned JSON envelope;
* one-use pairing capabilities with expiry and replay detection;
* an immutable, privacy-bounded projection for a paired mobile display; and
* a finite semantic command/receipt vocabulary.

The desktop session remains authoritative.  In particular, accepting a
command is not confirmation that Jamulus, the recorder, or Studio completed
it.  Callers publish a terminal receipt only after observing the authoritative
subsystem state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TypeAlias

from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)


POCKET_STAGE_PROTOCOL_VERSION = 1
MOBILE_PROJECTION_SCHEMA_VERSION = 1
DEFAULT_PAIRING_TTL_SECONDS = 5 * 60
MAX_PAIRING_TTL_SECONDS = 10 * 60
MAX_ACTIVE_PAIRING_CAPABILITIES = 32
MAX_PAIRING_RECORDS = 256
MAX_WIRE_MESSAGE_BYTES = 64 * 1024
MAX_GENERATION = (1 << 31) - 1
MAX_JSON_SAFE_INTEGER = (1 << 53) - 1
MAX_PARTICIPANTS = 64
MAX_SECTIONS = 256
MAX_SECTION_TIME_MS = 24 * 60 * 60 * 1000

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PocketStageProtocolErrorCode(str, Enum):
    """Stable, input-free failure categories for a wire peer."""

    MALFORMED = "malformed"
    INCOMPATIBLE = "incompatible"
    TOO_LARGE = "too_large"


class PocketStageProtocolError(ValueError):
    """A bounded protocol failure whose message never includes peer input."""

    def __init__(
        self,
        code: PocketStageProtocolErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _malformed() -> PocketStageProtocolError:
    return PocketStageProtocolError(
        PocketStageProtocolErrorCode.MALFORMED,
        "That Pocket Stage message is not valid.",
    )


def _incompatible() -> PocketStageProtocolError:
    return PocketStageProtocolError(
        PocketStageProtocolErrorCode.INCOMPATIBLE,
        "That Pocket Stage message needs a different protocol version.",
    )


def _too_large() -> PocketStageProtocolError:
    return PocketStageProtocolError(
        PocketStageProtocolErrorCode.TOO_LARGE,
        "That Pocket Stage message is too large.",
    )


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} fields are not valid.")
    return value


def _bounded_int(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_JSON_SAFE_INTEGER,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer.")
    if value < minimum or value > maximum:
        raise ValueError(f"{label} is outside the supported range.")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean.")
    return value


def _bounded_text(
    value: object,
    label: str,
    *,
    max_bytes: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty.")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use canonical Unicode text.")
    if _CONTROL_RE.search(value) or len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} is outside the supported range.")
    return value


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID.")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a canonical UUID.") from exc
    if value != str(parsed) or parsed.int == 0:
        raise ValueError(f"{label} must be a canonical UUID.")
    return value


def _pairing_token(value: object) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ValueError("pairing capability is not valid.")
    return value


def _finite_time(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{label} must be a finite non-negative number."
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{label} must be a finite non-negative number.")
    return parsed


class PairingScope(str, Enum):
    """Capabilities a paired phone may be granted by the desktop owner."""

    OBSERVE = "observe"
    CUES = "cues"
    MARKERS = "markers"
    MIX = "mix"
    TRANSPORT = "transport"
    RECORD = "record"


def _canonical_scopes(scopes: Iterable[PairingScope | str]) -> tuple[PairingScope, ...]:
    if isinstance(scopes, (str, bytes)):
        raise ValueError("scopes must be a non-empty collection.")
    try:
        parsed = tuple(PairingScope(scope) for scope in scopes)
    except (TypeError, ValueError) as exc:
        raise ValueError("scopes contain an unsupported capability.") from exc
    if not parsed or len(parsed) > len(PairingScope) or len(set(parsed)) != len(parsed):
        raise ValueError("scopes must be a unique non-empty collection.")
    return tuple(sorted(parsed, key=lambda item: item.value))


class PairingCapability:
    """One opaque pairing secret with an explicit reveal boundary."""

    __slots__ = (
        "_capability_id",
        "_expires_at_unix",
        "_issued_at_unix",
        "_scopes",
        "_sealed",
        "_token",
    )

    def __init__(
        self,
        *,
        capability_id: str,
        issued_at_unix: float,
        expires_at_unix: float,
        scopes: Iterable[PairingScope | str],
        token: str,
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        capability = _canonical_uuid(capability_id, "capability_id")
        issued = _finite_time(issued_at_unix, "issued_at_unix")
        expires = _finite_time(expires_at_unix, "expires_at_unix")
        if expires <= issued or expires - issued > MAX_PAIRING_TTL_SECONDS:
            raise ValueError("pairing capability lifetime is outside the supported range.")
        object.__setattr__(self, "_capability_id", capability)
        object.__setattr__(self, "_issued_at_unix", issued)
        object.__setattr__(self, "_expires_at_unix", expires)
        object.__setattr__(self, "_scopes", _canonical_scopes(scopes))
        object.__setattr__(self, "_token", _pairing_token(token))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("PairingCapability is immutable")
        object.__setattr__(self, _name, _value)

    @property
    def capability_id(self) -> str:
        return self._capability_id

    @property
    def issued_at_unix(self) -> float:
        return self._issued_at_unix

    @property
    def expires_at_unix(self) -> float:
        return self._expires_at_unix

    @property
    def scopes(self) -> tuple[PairingScope, ...]:
        return self._scopes

    def reveal_for_pairing(self) -> str:
        """Reveal the bearer secret only at the QR/pairing boundary."""

        return self._token

    def to_public_dict(self) -> dict[str, object]:
        """Return safe metadata; the bearer token is deliberately absent."""

        return {
            "capability_id": self.capability_id,
            "issued_at_unix": self.issued_at_unix,
            "expires_at_unix": self.expires_at_unix,
            "scopes": [scope.value for scope in self.scopes],
        }

    def __str__(self) -> str:
        return "[private Pocket Stage pairing capability]"

    def __repr__(self) -> str:
        return "PairingCapability(private=[redacted])"


class PairingClaim:
    """The first private frame submitted by an unpaired mobile client."""

    __slots__ = ("_capability", "_sealed", "claim_id")

    def __init__(self, *, capability_token: str, claim_id: str) -> None:
        object.__setattr__(self, "_sealed", False)
        object.__setattr__(self, "_capability", _pairing_token(capability_token))
        object.__setattr__(self, "claim_id", _canonical_uuid(claim_id, "claim_id"))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("PairingClaim is immutable")
        object.__setattr__(self, _name, _value)

    def capability_for_registry(self) -> str:
        return self._capability

    @classmethod
    def from_dict(cls, value: object) -> PairingClaim:
        payload = _require_exact_keys(
            value,
            frozenset({"capability", "claim_id"}),
            "pairing claim",
        )
        return cls(
            capability_token=payload["capability"],  # type: ignore[arg-type]
            claim_id=payload["claim_id"],  # type: ignore[arg-type]
        )

    def _to_wire_dict(self) -> dict[str, object]:
        return {"capability": self._capability, "claim_id": self.claim_id}

    def __str__(self) -> str:
        return "[private Pocket Stage pairing claim]"

    def __repr__(self) -> str:
        return "PairingClaim(private=[redacted])"


class PairingCapabilityState(str, Enum):
    ISSUED = "issued"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PairingCapabilityErrorCode(str, Enum):
    INVALID = "invalid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"
    REPLAY = "replay"
    CAPACITY = "capacity"


class PairingCapabilityError(RuntimeError):
    """A secret-free capability lifecycle rejection."""

    def __init__(
        self,
        code: PairingCapabilityErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PairingCapabilitySnapshot:
    capability_id: str
    state: PairingCapabilityState
    issued_at_unix: float
    expires_at_unix: float
    scopes: tuple[PairingScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capability_id",
            _canonical_uuid(self.capability_id, "capability_id"),
        )
        object.__setattr__(self, "state", PairingCapabilityState(self.state))
        issued = _finite_time(self.issued_at_unix, "issued_at_unix")
        expires = _finite_time(self.expires_at_unix, "expires_at_unix")
        if expires <= issued or expires - issued > MAX_PAIRING_TTL_SECONDS:
            raise ValueError("pairing capability lifetime is outside the supported range.")
        object.__setattr__(self, "issued_at_unix", issued)
        object.__setattr__(self, "expires_at_unix", expires)
        object.__setattr__(self, "scopes", _canonical_scopes(self.scopes))


class PairingAcceptanceStatus(str, Enum):
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class PairingAcceptance:
    """Nonsecret result used to mint a separately revocable device identity."""

    status: PairingAcceptanceStatus
    capability_id: str
    claim_id: str
    consumed_at_unix: float
    scopes: tuple[PairingScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            PairingAcceptanceStatus(self.status),
        )
        object.__setattr__(
            self,
            "capability_id",
            _canonical_uuid(self.capability_id, "capability_id"),
        )
        object.__setattr__(
            self,
            "claim_id",
            _canonical_uuid(self.claim_id, "claim_id"),
        )
        object.__setattr__(
            self,
            "consumed_at_unix",
            _finite_time(self.consumed_at_unix, "consumed_at_unix"),
        )
        object.__setattr__(self, "scopes", _canonical_scopes(self.scopes))


@dataclass(slots=True)
class _PairingRecord:
    capability_id: str
    token_sha256: bytes
    issued_at_unix: float
    expires_at_unix: float
    scopes: tuple[PairingScope, ...]
    state: PairingCapabilityState = PairingCapabilityState.ISSUED
    claim_id: str | None = None
    consumed_at_unix: float | None = None


class PairingCapabilityRegistry:
    """Thread-safe, monotonic, one-use pairing capability registry.

    Only a SHA-256 digest of each random bearer token is retained.  Consumed,
    revoked, and expired records remain as bounded tombstones until their
    original expiry so a retry can be classified without ever becoming valid.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], int | float] = time.time,
    ) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._by_id: dict[str, _PairingRecord] = {}
        self._by_digest: dict[bytes, _PairingRecord] = {}
        self._last_observed_unix = 0.0

    def __repr__(self) -> str:
        return "PairingCapabilityRegistry(private=[redacted])"

    def _now_locked(self) -> float:
        try:
            observed = _finite_time(self._clock(), "pairing clock")
        except Exception:
            raise PairingCapabilityError(
                PairingCapabilityErrorCode.INVALID,
                "The pairing clock is unavailable.",
            ) from None
        self._last_observed_unix = max(self._last_observed_unix, observed)
        return self._last_observed_unix

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("ascii")).digest()

    @staticmethod
    def _error(
        code: PairingCapabilityErrorCode,
    ) -> PairingCapabilityError:
        messages = {
            PairingCapabilityErrorCode.INVALID: (
                "That pairing capability is not valid."
            ),
            PairingCapabilityErrorCode.EXPIRED: (
                "That pairing capability has expired."
            ),
            PairingCapabilityErrorCode.REVOKED: (
                "That pairing capability was revoked."
            ),
            PairingCapabilityErrorCode.CONSUMED: (
                "That pairing capability was already used."
            ),
            PairingCapabilityErrorCode.REPLAY: (
                "That pairing claim was already received."
            ),
            PairingCapabilityErrorCode.CAPACITY: (
                "Too many pairing capabilities are active."
            ),
        }
        return PairingCapabilityError(code, messages[code])

    @staticmethod
    def _snapshot(record: _PairingRecord) -> PairingCapabilitySnapshot:
        return PairingCapabilitySnapshot(
            capability_id=record.capability_id,
            state=record.state,
            issued_at_unix=record.issued_at_unix,
            expires_at_unix=record.expires_at_unix,
            scopes=record.scopes,
        )

    @staticmethod
    def _refresh(record: _PairingRecord, now: float) -> None:
        if record.state is PairingCapabilityState.ISSUED and now >= record.expires_at_unix:
            record.state = PairingCapabilityState.EXPIRED

    def _clear_expired_locked(self, now: float) -> int:
        expired = tuple(
            record
            for record in self._by_id.values()
            if now >= record.expires_at_unix
        )
        for record in expired:
            self._by_id.pop(record.capability_id, None)
            self._by_digest.pop(record.token_sha256, None)
        return len(expired)

    def issue(
        self,
        *,
        scopes: Iterable[PairingScope | str],
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
    ) -> PairingCapability:
        """Issue a random bearer capability; the registry keeps no plaintext."""

        canonical_scopes = _canonical_scopes(scopes)
        ttl = _bounded_int(
            ttl_seconds,
            "ttl_seconds",
            minimum=1,
            maximum=MAX_PAIRING_TTL_SECONDS,
        )
        with self._lock:
            now = self._now_locked()
            self._clear_expired_locked(now)
            active = sum(
                record.state is PairingCapabilityState.ISSUED
                for record in self._by_id.values()
            )
            if (
                active >= MAX_ACTIVE_PAIRING_CAPABILITIES
                or len(self._by_id) >= MAX_PAIRING_RECORDS
            ):
                raise self._error(PairingCapabilityErrorCode.CAPACITY)

            token = secrets.token_urlsafe(32)
            digest = self._digest(token)
            while digest in self._by_digest:
                token = secrets.token_urlsafe(32)
                digest = self._digest(token)
            capability_id = str(uuid.uuid4())
            expires = now + ttl
            record = _PairingRecord(
                capability_id=capability_id,
                token_sha256=digest,
                issued_at_unix=now,
                expires_at_unix=expires,
                scopes=canonical_scopes,
            )
            self._by_id[capability_id] = record
            self._by_digest[digest] = record

        return PairingCapability(
            capability_id=capability_id,
            issued_at_unix=now,
            expires_at_unix=expires,
            scopes=canonical_scopes,
            token=token,
        )

    def consume(self, token: str, *, claim_id: str) -> PairingAcceptance:
        """Atomically consume a capability and reject every subsequent claim."""

        try:
            canonical_token = _pairing_token(token)
            canonical_claim = _canonical_uuid(claim_id, "claim_id")
        except (TypeError, ValueError):
            raise self._error(PairingCapabilityErrorCode.INVALID) from None
        digest = self._digest(canonical_token)
        with self._lock:
            now = self._now_locked()
            record = self._by_digest.get(digest)
            if record is None or not hmac.compare_digest(
                record.token_sha256,
                digest,
            ):
                raise self._error(PairingCapabilityErrorCode.INVALID)
            self._refresh(record, now)
            if record.state is PairingCapabilityState.EXPIRED:
                raise self._error(PairingCapabilityErrorCode.EXPIRED)
            if record.state is PairingCapabilityState.REVOKED:
                raise self._error(PairingCapabilityErrorCode.REVOKED)
            if record.state is PairingCapabilityState.CONSUMED:
                code = (
                    PairingCapabilityErrorCode.REPLAY
                    if record.claim_id == canonical_claim
                    else PairingCapabilityErrorCode.CONSUMED
                )
                raise self._error(code)
            record.state = PairingCapabilityState.CONSUMED
            record.claim_id = canonical_claim
            record.consumed_at_unix = now
            return PairingAcceptance(
                status=PairingAcceptanceStatus.ACCEPTED,
                capability_id=record.capability_id,
                claim_id=canonical_claim,
                consumed_at_unix=now,
                scopes=record.scopes,
            )

    def revoke(self, capability_id: str) -> PairingCapabilitySnapshot:
        """Revoke an unconsumed capability, idempotently."""

        try:
            canonical_id = _canonical_uuid(capability_id, "capability_id")
        except (TypeError, ValueError):
            raise self._error(PairingCapabilityErrorCode.INVALID) from None
        with self._lock:
            now = self._now_locked()
            record = self._by_id.get(canonical_id)
            if record is None:
                raise self._error(PairingCapabilityErrorCode.INVALID)
            self._refresh(record, now)
            if record.state is PairingCapabilityState.ISSUED:
                record.state = PairingCapabilityState.REVOKED
            return self._snapshot(record)

    def snapshot(self, capability_id: str) -> PairingCapabilitySnapshot:
        try:
            canonical_id = _canonical_uuid(capability_id, "capability_id")
        except (TypeError, ValueError):
            raise self._error(PairingCapabilityErrorCode.INVALID) from None
        with self._lock:
            now = self._now_locked()
            record = self._by_id.get(canonical_id)
            if record is None:
                raise self._error(PairingCapabilityErrorCode.INVALID)
            self._refresh(record, now)
            return self._snapshot(record)

    def clear_expired(self) -> int:
        """Remove records past their original lifetime and return the count."""

        with self._lock:
            return self._clear_expired_locked(self._now_locked())


class MobileParticipantState(str, Enum):
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    READY = "ready"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


class MobileRecordingState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    VERIFYING = "verifying"
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True, slots=True)
class MobileParticipant:
    """One paired-private mixer slot with no provider identifier."""

    slot: int
    label: str
    fader_level: int
    pan: int
    muted: bool
    solo: bool
    is_local: bool
    connection_state: MobileParticipantState = MobileParticipantState.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "slot",
            _bounded_int(self.slot, "slot", minimum=1, maximum=MAX_PARTICIPANTS),
        )
        object.__setattr__(
            self,
            "label",
            _bounded_text(self.label, "label", max_bytes=80),
        )
        object.__setattr__(
            self,
            "fader_level",
            _bounded_int(self.fader_level, "fader_level", maximum=100),
        )
        object.__setattr__(
            self,
            "pan",
            _bounded_int(self.pan, "pan", maximum=100),
        )
        object.__setattr__(self, "muted", _strict_bool(self.muted, "muted"))
        object.__setattr__(self, "solo", _strict_bool(self.solo, "solo"))
        object.__setattr__(
            self,
            "is_local",
            _strict_bool(self.is_local, "is_local"),
        )
        object.__setattr__(
            self,
            "connection_state",
            MobileParticipantState(self.connection_state),
        )

    @classmethod
    def from_dict(cls, value: object) -> MobileParticipant:
        payload = _require_exact_keys(
            value,
            frozenset(
                {
                    "slot",
                    "label",
                    "fader_level",
                    "pan",
                    "muted",
                    "solo",
                    "is_local",
                    "connection_state",
                }
            ),
            "mobile participant",
        )
        return cls(**payload)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "label": self.label,
            "fader_level": self.fader_level,
            "pan": self.pan,
            "muted": self.muted,
            "solo": self.solo,
            "is_local": self.is_local,
            "connection_state": self.connection_state.value,
        }


@dataclass(frozen=True, slots=True)
class MobileSection:
    """One bounded named arrangement interval suitable for a stage display."""

    ordinal: int
    label: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ordinal",
            _bounded_int(self.ordinal, "ordinal", minimum=1, maximum=MAX_SECTIONS),
        )
        object.__setattr__(
            self,
            "label",
            _bounded_text(self.label, "label", max_bytes=80),
        )
        start = _bounded_int(
            self.start_ms,
            "start_ms",
            maximum=MAX_SECTION_TIME_MS,
        )
        end = _bounded_int(
            self.end_ms,
            "end_ms",
            maximum=MAX_SECTION_TIME_MS,
        )
        if end <= start:
            raise ValueError("section end_ms must be greater than start_ms.")
        object.__setattr__(self, "start_ms", start)
        object.__setattr__(self, "end_ms", end)

    @classmethod
    def from_dict(cls, value: object) -> MobileSection:
        payload = _require_exact_keys(
            value,
            frozenset({"ordinal", "label", "start_ms", "end_ms"}),
            "mobile section",
        )
        return cls(**payload)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "label": self.label,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


@dataclass(frozen=True, slots=True)
class MobileSessionProjection:
    """Immutable mobile rendering input derived from desktop-owned truth."""

    generation: int
    revision: int
    role: SessionRole
    phase: SessionConductorPhase
    primary_action: SessionPrimaryAction
    primary_enabled: bool
    recording_state: MobileRecordingState
    participants: tuple[MobileParticipant, ...] = ()
    sections: tuple[MobileSection, ...] = ()
    current_section_ordinal: int | None = None
    cue: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "generation",
            _bounded_int(self.generation, "generation", maximum=MAX_GENERATION),
        )
        object.__setattr__(
            self,
            "revision",
            _bounded_int(self.revision, "revision"),
        )
        object.__setattr__(self, "role", SessionRole(self.role))
        object.__setattr__(self, "phase", SessionConductorPhase(self.phase))
        object.__setattr__(
            self,
            "primary_action",
            SessionPrimaryAction(self.primary_action),
        )
        object.__setattr__(
            self,
            "primary_enabled",
            _strict_bool(self.primary_enabled, "primary_enabled"),
        )
        object.__setattr__(
            self,
            "recording_state",
            MobileRecordingState(self.recording_state),
        )
        participants = tuple(self.participants)
        sections = tuple(self.sections)
        if len(participants) > MAX_PARTICIPANTS or not all(
            isinstance(item, MobileParticipant) for item in participants
        ):
            raise ValueError("participants are outside the supported range.")
        if len(sections) > MAX_SECTIONS or not all(
            isinstance(item, MobileSection) for item in sections
        ):
            raise ValueError("sections are outside the supported range.")
        participant_slots = tuple(item.slot for item in participants)
        if participant_slots != tuple(sorted(set(participant_slots))):
            raise ValueError("participants must use unique ascending slots.")
        section_ordinals = tuple(item.ordinal for item in sections)
        if section_ordinals != tuple(sorted(set(section_ordinals))):
            raise ValueError("sections must use unique ascending ordinals.")
        for previous, current in zip(sections, sections[1:]):
            if current.start_ms < previous.end_ms:
                raise ValueError("sections must use ascending non-overlapping times.")
        current_ordinal = self.current_section_ordinal
        if current_ordinal is not None:
            current_ordinal = _bounded_int(
                current_ordinal,
                "current_section_ordinal",
                minimum=1,
                maximum=MAX_SECTIONS,
            )
            if current_ordinal not in section_ordinals:
                raise ValueError("current section must be present in sections.")
        cue = _bounded_text(
            self.cue,
            "cue",
            max_bytes=512,
            allow_empty=True,
        )
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "sections", sections)
        object.__setattr__(self, "current_section_ordinal", current_ordinal)
        object.__setattr__(self, "cue", cue)

    @classmethod
    def from_dict(cls, value: object) -> MobileSessionProjection:
        payload = _require_exact_keys(
            value,
            frozenset(
                {
                    "schema",
                    "generation",
                    "revision",
                    "role",
                    "phase",
                    "primary_action",
                    "primary_enabled",
                    "recording_state",
                    "participants",
                    "sections",
                    "current_section_ordinal",
                    "cue",
                }
            ),
            "mobile session projection",
        )
        schema = _bounded_int(payload["schema"], "projection schema", minimum=1)
        if schema != MOBILE_PROJECTION_SCHEMA_VERSION:
            raise ValueError("projection schema is not supported.")
        raw_participants = payload["participants"]
        raw_sections = payload["sections"]
        if not isinstance(raw_participants, list) or not isinstance(raw_sections, list):
            raise ValueError("projection collections are not valid.")
        return cls(
            generation=payload["generation"],  # type: ignore[arg-type]
            revision=payload["revision"],  # type: ignore[arg-type]
            role=payload["role"],  # type: ignore[arg-type]
            phase=payload["phase"],  # type: ignore[arg-type]
            primary_action=payload["primary_action"],  # type: ignore[arg-type]
            primary_enabled=payload["primary_enabled"],  # type: ignore[arg-type]
            recording_state=payload["recording_state"],  # type: ignore[arg-type]
            participants=tuple(
                MobileParticipant.from_dict(item) for item in raw_participants
            ),
            sections=tuple(MobileSection.from_dict(item) for item in raw_sections),
            current_section_ordinal=payload["current_section_ordinal"],  # type: ignore[arg-type]
            cue=payload["cue"],  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": MOBILE_PROJECTION_SCHEMA_VERSION,
            "generation": self.generation,
            "revision": self.revision,
            "role": self.role.value,
            "phase": self.phase.value,
            "primary_action": self.primary_action.value,
            "primary_enabled": self.primary_enabled,
            "recording_state": self.recording_state.value,
            "participants": [item.to_dict() for item in self.participants],
            "sections": [item.to_dict() for item in self.sections],
            "current_section_ordinal": self.current_section_ordinal,
            "cue": self.cue,
        }


class PocketCommand(str, Enum):
    """Finite semantic actions; these are not controller method names."""

    ADD_MARKER = "add_marker"
    GO_TO_SECTION = "go_to_section"
    SET_PARTICIPANT_MUTE = "set_participant_mute"
    SET_PARTICIPANT_FADER = "set_participant_fader"
    SET_PARTICIPANT_PAN = "set_participant_pan"
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"

    @property
    def required_scope(self) -> PairingScope:
        return {
            PocketCommand.ADD_MARKER: PairingScope.MARKERS,
            PocketCommand.GO_TO_SECTION: PairingScope.TRANSPORT,
            PocketCommand.SET_PARTICIPANT_MUTE: PairingScope.MIX,
            PocketCommand.SET_PARTICIPANT_FADER: PairingScope.MIX,
            PocketCommand.SET_PARTICIPANT_PAN: PairingScope.MIX,
            PocketCommand.START_RECORDING: PairingScope.RECORD,
            PocketCommand.STOP_RECORDING: PairingScope.RECORD,
        }[self]


CommandArgument: TypeAlias = bool | int | str

_COMMAND_ARGUMENT_KEYS: dict[PocketCommand, tuple[str, ...]] = {
    PocketCommand.ADD_MARKER: ("at_ms", "label"),
    PocketCommand.GO_TO_SECTION: ("ordinal",),
    PocketCommand.SET_PARTICIPANT_MUTE: ("slot", "muted"),
    PocketCommand.SET_PARTICIPANT_FADER: ("slot", "fader_level"),
    PocketCommand.SET_PARTICIPANT_PAN: ("slot", "pan"),
    PocketCommand.START_RECORDING: (),
    PocketCommand.STOP_RECORDING: (),
}


def _command_arguments(
    command: PocketCommand,
    value: object,
) -> tuple[tuple[str, CommandArgument], ...]:
    expected = _COMMAND_ARGUMENT_KEYS[command]
    payload = _require_exact_keys(value, frozenset(expected), "command arguments")
    normalized: dict[str, CommandArgument] = {}
    if command is PocketCommand.ADD_MARKER:
        normalized["at_ms"] = _bounded_int(
            payload["at_ms"],
            "at_ms",
            maximum=MAX_SECTION_TIME_MS,
        )
        normalized["label"] = _bounded_text(
            payload["label"],
            "label",
            max_bytes=80,
            allow_empty=True,
        )
    elif command is PocketCommand.GO_TO_SECTION:
        normalized["ordinal"] = _bounded_int(
            payload["ordinal"],
            "ordinal",
            minimum=1,
            maximum=MAX_SECTIONS,
        )
    elif command is PocketCommand.SET_PARTICIPANT_MUTE:
        normalized["slot"] = _bounded_int(
            payload["slot"],
            "slot",
            minimum=1,
            maximum=MAX_PARTICIPANTS,
        )
        normalized["muted"] = _strict_bool(payload["muted"], "muted")
    elif command is PocketCommand.SET_PARTICIPANT_FADER:
        normalized["slot"] = _bounded_int(
            payload["slot"],
            "slot",
            minimum=1,
            maximum=MAX_PARTICIPANTS,
        )
        normalized["fader_level"] = _bounded_int(
            payload["fader_level"],
            "fader_level",
            maximum=100,
        )
    elif command is PocketCommand.SET_PARTICIPANT_PAN:
        normalized["slot"] = _bounded_int(
            payload["slot"],
            "slot",
            minimum=1,
            maximum=MAX_PARTICIPANTS,
        )
        normalized["pan"] = _bounded_int(payload["pan"], "pan", maximum=100)
    return tuple((key, normalized[key]) for key in expected)


@dataclass(frozen=True, slots=True)
class PocketCommandRequest:
    """One idempotency-keyed intent against an observed desktop revision."""

    command_id: str
    command: PocketCommand
    generation: int
    expected_revision: int
    arguments: tuple[tuple[str, CommandArgument], ...] | Mapping[str, object] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            _canonical_uuid(self.command_id, "command_id"),
        )
        command = PocketCommand(self.command)
        object.__setattr__(self, "command", command)
        object.__setattr__(
            self,
            "generation",
            _bounded_int(self.generation, "generation", maximum=MAX_GENERATION),
        )
        object.__setattr__(
            self,
            "expected_revision",
            _bounded_int(self.expected_revision, "expected_revision"),
        )
        raw_arguments: object = self.arguments
        if not isinstance(raw_arguments, Mapping):
            try:
                argument_items = tuple(raw_arguments)
                raw_arguments = dict(argument_items)
            except (TypeError, ValueError) as exc:
                raise ValueError("command arguments are not valid.") from exc
            if len(raw_arguments) != len(argument_items):
                raise ValueError("command arguments contain duplicate fields.")
        object.__setattr__(
            self,
            "arguments",
            _command_arguments(command, raw_arguments),
        )

    @classmethod
    def from_dict(cls, value: object) -> PocketCommandRequest:
        payload = _require_exact_keys(
            value,
            frozenset(
                {
                    "command_id",
                    "command",
                    "generation",
                    "expected_revision",
                    "arguments",
                }
            ),
            "command request",
        )
        return cls(**payload)  # type: ignore[arg-type]

    @property
    def required_scope(self) -> PairingScope:
        return self.command.required_scope

    @property
    def argument_map(self) -> Mapping[str, CommandArgument]:
        return MappingProxyType(dict(self.arguments))

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "command": self.command.value,
            "generation": self.generation,
            "expected_revision": self.expected_revision,
            "arguments": dict(self.arguments),
        }


class PocketCommandStatus(str, Enum):
    ACCEPTED = "accepted"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PocketCommandRejectionReason(str, Enum):
    NONE = "none"
    UNAUTHORIZED = "unauthorized"
    STALE_GENERATION = "stale_generation"
    STALE_REVISION = "stale_revision"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    INVALID_STATE = "invalid_state"
    RATE_LIMITED = "rate_limited"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True, slots=True)
class PocketCommandReceipt:
    """Bounded evidence for one command; contains no raw exception text."""

    command_id: str
    status: PocketCommandStatus
    generation: int
    revision: int
    reason: PocketCommandRejectionReason = PocketCommandRejectionReason.NONE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            _canonical_uuid(self.command_id, "command_id"),
        )
        status = PocketCommandStatus(self.status)
        reason = PocketCommandRejectionReason(self.reason)
        if status is PocketCommandStatus.REJECTED:
            if reason is PocketCommandRejectionReason.NONE:
                raise ValueError("a rejected command requires a finite reason.")
        elif reason is not PocketCommandRejectionReason.NONE:
            raise ValueError("only rejected commands may include a reason.")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "generation",
            _bounded_int(self.generation, "generation", maximum=MAX_GENERATION),
        )
        object.__setattr__(
            self,
            "revision",
            _bounded_int(self.revision, "revision"),
        )

    @property
    def terminal(self) -> bool:
        return self.status in {
            PocketCommandStatus.CONFIRMED,
            PocketCommandStatus.REJECTED,
        }

    @classmethod
    def from_dict(cls, value: object) -> PocketCommandReceipt:
        payload = _require_exact_keys(
            value,
            frozenset({"command_id", "status", "generation", "revision", "reason"}),
            "command receipt",
        )
        return cls(**payload)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "generation": self.generation,
            "revision": self.revision,
            "reason": self.reason.value,
        }


class PocketStageMessageKind(str, Enum):
    PAIR = "pair"
    SNAPSHOT = "snapshot"
    COMMAND = "command"
    RECEIPT = "receipt"


PocketStageBody: TypeAlias = (
    PairingClaim
    | MobileSessionProjection
    | PocketCommandRequest
    | PocketCommandReceipt
)

_BODY_TYPES: dict[PocketStageMessageKind, type[PocketStageBody]] = {
    PocketStageMessageKind.PAIR: PairingClaim,
    PocketStageMessageKind.SNAPSHOT: MobileSessionProjection,
    PocketStageMessageKind.COMMAND: PocketCommandRequest,
    PocketStageMessageKind.RECEIPT: PocketCommandReceipt,
}


@dataclass(frozen=True, slots=True, repr=False)
class PocketStageEnvelope:
    """Strict v1 message envelope shared by a gateway and mobile client."""

    kind: PocketStageMessageKind
    message_id: str
    generation: int
    sequence: int
    sent_at_unix_ms: int
    body: PocketStageBody

    def __post_init__(self) -> None:
        kind = PocketStageMessageKind(self.kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "message_id",
            _canonical_uuid(self.message_id, "message_id"),
        )
        generation = _bounded_int(
            self.generation,
            "generation",
            maximum=MAX_GENERATION,
        )
        sequence = _bounded_int(self.sequence, "sequence")
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(
            self,
            "sent_at_unix_ms",
            _bounded_int(self.sent_at_unix_ms, "sent_at_unix_ms"),
        )
        if not isinstance(self.body, _BODY_TYPES[kind]):
            raise ValueError("envelope kind and body do not match.")
        if kind is PocketStageMessageKind.PAIR:
            if generation != 0 or sequence != 0:
                raise ValueError("a pairing envelope must use generation and sequence zero.")
        elif generation != self.body.generation:  # type: ignore[union-attr]
            raise ValueError("envelope and body generations do not match.")

    @property
    def version(self) -> int:
        return POCKET_STAGE_PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, value: object) -> PocketStageEnvelope:
        try:
            payload = _require_exact_keys(
                value,
                frozenset(
                    {
                        "version",
                        "kind",
                        "message_id",
                        "generation",
                        "sequence",
                        "sent_at_unix_ms",
                        "body",
                    }
                ),
                "Pocket Stage envelope",
            )
            version = _bounded_int(payload["version"], "version")
            if version != POCKET_STAGE_PROTOCOL_VERSION:
                raise _incompatible()
            try:
                kind = PocketStageMessageKind(payload["kind"])
            except (TypeError, ValueError):
                raise _incompatible() from None
            body = _BODY_TYPES[kind].from_dict(payload["body"])
            return cls(
                kind=kind,
                message_id=payload["message_id"],  # type: ignore[arg-type]
                generation=payload["generation"],  # type: ignore[arg-type]
                sequence=payload["sequence"],  # type: ignore[arg-type]
                sent_at_unix_ms=payload["sent_at_unix_ms"],  # type: ignore[arg-type]
                body=body,
            )
        except PocketStageProtocolError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _malformed() from None

    @classmethod
    def from_json(cls, raw: str | bytes) -> PocketStageEnvelope:
        if not isinstance(raw, (str, bytes)):
            raise _malformed()
        try:
            encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
        except UnicodeError:
            raise _malformed() from None
        if len(encoded) > MAX_WIRE_MESSAGE_BYTES:
            raise _too_large()
        try:
            text = encoded.decode("utf-8")
            value = json.loads(
                text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            raise _malformed() from None
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object]
        if isinstance(self.body, PairingClaim):
            body = self.body._to_wire_dict()
        else:
            body = self.body.to_dict()
        return {
            "version": POCKET_STAGE_PROTOCOL_VERSION,
            "kind": self.kind.value,
            "message_id": self.message_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "sent_at_unix_ms": self.sent_at_unix_ms,
            "body": body,
        }

    def to_json(self) -> str:
        """Serialize at the explicit wire boundary, including a pair secret."""

        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_WIRE_MESSAGE_BYTES:
            raise _too_large()
        return encoded

    def __repr__(self) -> str:
        body = (
            "[redacted]"
            if isinstance(self.body, PairingClaim)
            else type(self.body).__name__
        )
        return (
            "PocketStageEnvelope("
            f"kind={self.kind.value!r}, generation={self.generation}, "
            f"sequence={self.sequence}, body={body})"
        )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")

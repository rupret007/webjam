"""Bounded, post-enrollment authorization for v3 isolated-media uploads.

The encrypted transport authenticates a peer before calling this module.  Its
SPKI fingerprint and connection generation must come from that authenticated
connection context, never from a field in the peer's media message.  This
layer then applies a short-lived, take-scoped grant before delegating storage
and WAV verification to :class:`core.session_transfer.TransferStore`.

Peers cannot choose a destination path or compression mode.  TransferStore
derives every path from canonical UUIDs and publishes a segment only after its
declared byte count, SHA-256 digest, and PCM facts all match.
"""

from __future__ import annotations

import hmac
import json
import math
import secrets
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.session_transfer import (
    MAX_CHUNK_BYTES as TRANSFER_STORE_MAX_CHUNK_BYTES,
)
from core.session_transfer import (
    MAX_SEGMENT_BYTES,
    TransferConflictError,
    TransferDescriptor,
    TransferIntegrityError,
    TransferReceipt,
    TransferStore,
)

DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_SESSION_BYTES = 64 * 1024 * 1024 * 1024
DEFAULT_MAX_RAW_CHUNK_BYTES = 1024 * 1024
DEFAULT_RATE_BYTES_PER_SECOND = 8 * 1024 * 1024
DEFAULT_BURST_BYTES = 4 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024
MAX_SESSION_BYTES = 256 * 1024 * 1024 * 1024
MAX_ALLOWED_TAKES = 64
MAX_STORED_TRANSFERS = 4096
MAX_ACTIVE_GRANTS = 16
MAX_GRANT_LIFETIME_SECONDS = 24 * 60 * 60
MAX_CHECKPOINT_BYTES = 64 * 1024
MAX_CONNECTION_GENERATION = (1 << 31) - 1
MAX_TRANSFER_GENERATION = (1 << 31) - 1
MAX_RETRY_AFTER_SECONDS = 5.0


class SecureMediaError(RuntimeError):
    """Base error whose messages are safe for a peer-facing control stream."""


class MediaAuthorizationError(SecureMediaError):
    """A grant or its connection-bound identity was not accepted."""


class MediaCapacityError(SecureMediaError):
    """A declared transfer exceeds a file, session, or disk capacity bound."""


class MediaConflictError(SecureMediaError):
    """A chunk or descriptor contradicts persisted transfer state."""

    def __init__(self, *, expected_offset: int | None = None) -> None:
        super().__init__("The media transfer conflicts with confirmed state.")
        self.expected_offset = expected_offset


class MediaGenerationError(SecureMediaError):
    """A stale or unexpected transfer generation was used."""


class MediaIntegrityError(SecureMediaError):
    """TransferStore rejected the completed media's integrity or PCM facts."""


class MediaStorageError(SecureMediaError):
    """Local storage could not be checked or updated safely."""


class MediaCancelledError(SecureMediaError):
    """A cancelled or failed transfer cannot accept more chunks."""


class MediaCallbackError(SecureMediaError):
    """The verified-media callback failed after publication."""


class MediaBackpressureError(SecureMediaError):
    """A deterministic token bucket rejected a chunk without sleeping."""

    def __init__(self, retry_after_seconds: float) -> None:
        bounded = min(
            MAX_RETRY_AFTER_SECONDS,
            max(0.001, float(retry_after_seconds)),
        )
        super().__init__("Media transfer is temporarily backpressured.")
        self.retry_after_seconds = bounded


def _canonical_uuid(value: str, label: str) -> str:
    text = str(value)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a canonical UUID.") from exc
    canonical = str(parsed)
    if text != canonical:
        raise ValueError(f"{label} must be a canonical UUID.")
    return canonical


def _spki_sha256(value: str) -> str:
    text = str(value)
    if len(text) != 64 or text.lower() != text:
        raise ValueError("peer_spki_sha256 must be lowercase hexadecimal SHA-256.")
    try:
        bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError(
            "peer_spki_sha256 must be lowercase hexadecimal SHA-256."
        ) from exc
    return text


def _bounded_generation(value: int, *, label: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is outside the supported range.")
    generation = int(value)
    if generation < 1 or generation > maximum:
        raise ValueError(f"{label} is outside the supported range.")
    return generation


def _positive_int(value: int, label: str, *, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is outside the supported range.")
    parsed = int(value)
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{label} is outside the supported range.")
    return parsed


@dataclass(frozen=True, repr=False)
class SecureMediaGrant:
    """Opaque post-enrollment capability bound to one authenticated peer."""

    grant_token: str
    session_id: str
    participant_id: str
    peer_spki_sha256: str
    connection_generation: int
    expires_at: float
    allowed_take_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        token = str(self.grant_token)
        if not (43 <= len(token) <= 128) or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in token
        ):
            raise ValueError("grant_token is not a valid secure-media capability.")
        object.__setattr__(self, "grant_token", token)
        object.__setattr__(
            self, "session_id", _canonical_uuid(self.session_id, "session_id")
        )
        object.__setattr__(
            self,
            "participant_id",
            _canonical_uuid(self.participant_id, "participant_id"),
        )
        object.__setattr__(
            self, "peer_spki_sha256", _spki_sha256(self.peer_spki_sha256)
        )
        object.__setattr__(
            self,
            "connection_generation",
            _bounded_generation(
                self.connection_generation,
                label="connection_generation",
                maximum=MAX_CONNECTION_GENERATION,
            ),
        )
        expiry = float(self.expires_at)
        if not math.isfinite(expiry):
            raise ValueError("expires_at must be finite.")
        object.__setattr__(self, "expires_at", expiry)
        if isinstance(self.allowed_take_ids, (str, bytes)):
            raise ValueError("allowed_take_ids must contain canonical take UUIDs.")
        take_ids = tuple(
            sorted(
                {
                    _canonical_uuid(take_id, "take_id")
                    for take_id in self.allowed_take_ids
                }
            )
        )
        if not take_ids or len(take_ids) > MAX_ALLOWED_TAKES:
            raise ValueError("allowed_take_ids is outside the supported range.")
        object.__setattr__(self, "allowed_take_ids", take_ids)

    def __repr__(self) -> str:
        return "SecureMediaGrant(private=[redacted])"


class MediaTransferPhase(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    VERIFIED = "verified"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True, repr=False)
class MediaTransferStatus:
    """Peer-safe progress; deliberately excludes paths and identities."""

    received_bytes: int
    declared_bytes: int
    phase: MediaTransferPhase

    @property
    def complete(self) -> bool:
        return self.phase is MediaTransferPhase.VERIFIED

    @property
    def cancelled(self) -> bool:
        return self.phase is MediaTransferPhase.CANCELLED

    def __repr__(self) -> str:
        return (
            "MediaTransferStatus("
            f"received_bytes={self.received_bytes}, "
            f"declared_bytes={self.declared_bytes}, phase={self.phase.value!r})"
        )


@dataclass(frozen=True, repr=False)
class VerifiedMedia:
    """Host-internal verified publication passed to the completion callback."""

    descriptor: TransferDescriptor
    path: Path

    def __repr__(self) -> str:
        return "VerifiedMedia(private=[redacted])"


@dataclass
class _TokenBucket:
    rate: float
    capacity: float
    tokens: float
    updated_at: float

    def consume(self, amount: int, now: float) -> None:
        elapsed = max(0.0, float(now) - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.updated_at = max(self.updated_at, float(now))
        if self.tokens >= amount:
            self.tokens -= amount
            return
        raise MediaBackpressureError((amount - self.tokens) / self.rate)


@dataclass
class _TransferState:
    descriptor: TransferDescriptor
    grant_token: str
    transfer_generation: int
    phase: MediaTransferPhase


class SecureMediaPlane:
    """Authorize and receive one session's encrypted isolated-media stream.

    The caller is responsible for feeding ``peer_spki_sha256`` and
    ``connection_generation`` from authenticated transport state.  Taking
    either value from an untrusted chunk message would defeat the binding.
    """

    def __init__(
        self,
        store: TransferStore,
        session_id: str,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_session_bytes: int = DEFAULT_MAX_SESSION_BYTES,
        max_raw_chunk_bytes: int = DEFAULT_MAX_RAW_CHUNK_BYTES,
        rate_bytes_per_second: int = DEFAULT_RATE_BYTES_PER_SECOND,
        burst_bytes: int = DEFAULT_BURST_BYTES,
        min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
        disk_free_probe: Callable[[Path], int] | None = None,
        clock: Callable[[], float] | None = None,
        on_verified: Callable[[VerifiedMedia], None] | None = None,
    ) -> None:
        canonical_session = _canonical_uuid(session_id, "session_id")
        if store.session_id != canonical_session:
            raise ValueError("TransferStore belongs to another session.")
        self.store = store
        self.session_id = canonical_session
        self.max_file_bytes = _positive_int(
            max_file_bytes, "max_file_bytes", maximum=MAX_SEGMENT_BYTES
        )
        self.max_session_bytes = _positive_int(
            max_session_bytes, "max_session_bytes", maximum=MAX_SESSION_BYTES
        )
        if self.max_file_bytes > self.max_session_bytes:
            raise ValueError("max_file_bytes cannot exceed max_session_bytes.")
        self.max_raw_chunk_bytes = _positive_int(
            max_raw_chunk_bytes,
            "max_raw_chunk_bytes",
            maximum=TRANSFER_STORE_MAX_CHUNK_BYTES,
        )
        self.rate_bytes_per_second = _positive_int(
            rate_bytes_per_second,
            "rate_bytes_per_second",
            maximum=MAX_SESSION_BYTES,
        )
        self.burst_bytes = _positive_int(
            burst_bytes, "burst_bytes", maximum=MAX_SESSION_BYTES
        )
        if self.burst_bytes < self.max_raw_chunk_bytes:
            raise ValueError("burst_bytes cannot be smaller than max_raw_chunk_bytes.")
        if isinstance(min_free_bytes, bool) or int(min_free_bytes) < 0:
            raise ValueError("min_free_bytes cannot be negative.")
        self.min_free_bytes = int(min_free_bytes)
        self._disk_free_probe = disk_free_probe or (
            lambda root: int(shutil.disk_usage(root).free)
        )
        self._clock = clock or time.time
        self._on_verified = on_verified
        self._lock = threading.RLock()
        self._grants: dict[str, SecureMediaGrant] = {}
        self._revoked: set[str] = set()
        self._buckets: dict[str, _TokenBucket] = {}
        self._states: dict[tuple[str, str, str], _TransferState] = {}
        self._known_descriptors: dict[
            tuple[str, str, str], TransferDescriptor
        ] = {}
        self._disk_remaining: dict[tuple[str, str, str], int] = {}
        self._notified: set[tuple[str, str, str]] = set()
        self._quota_bytes = 0
        self._load_existing_quota()

    def __repr__(self) -> str:
        return "SecureMediaPlane(private=[redacted])"

    @staticmethod
    def _key(descriptor: TransferDescriptor) -> tuple[str, str, str]:
        return (
            descriptor.take_id,
            descriptor.participant_id,
            descriptor.segment_id,
        )

    def _load_existing_quota(self) -> None:
        try:
            count = 0
            for sidecar in self.store.root.rglob("*.transfer.json"):
                count += 1
                if count > MAX_STORED_TRANSFERS:
                    raise MediaCapacityError(
                        "The session has too many stored media transfers."
                    )
                if sidecar.stat().st_size > MAX_CHECKPOINT_BYTES:
                    raise ValueError("checkpoint size")
                payload: Any = json.loads(sidecar.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("checkpoint")
                descriptor = TransferDescriptor.from_mapping(payload)
                if descriptor.session_id != self.session_id:
                    raise ValueError("session")
                key = self._key(descriptor)
                existing = self._known_descriptors.get(key)
                if existing is not None and existing != descriptor:
                    raise ValueError("identity")
                if existing is None:
                    self._known_descriptors[key] = descriptor
                    self._quota_bytes += descriptor.size_bytes
            if self._quota_bytes > self.max_session_bytes:
                raise MediaCapacityError(
                    "The session media quota is already exhausted."
                )
        except MediaCapacityError:
            raise
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise MediaStorageError(
                "Stored media quota could not be verified."
            ) from None

    def _now(self) -> float:
        try:
            now = float(self._clock())
        except Exception:
            raise MediaStorageError("The secure-media clock is unavailable.") from None
        if not math.isfinite(now):
            raise MediaStorageError("The secure-media clock is unavailable.")
        return now

    def _prune_grants(self, now: float) -> None:
        expired = tuple(
            token
            for token, grant in self._grants.items()
            if token in self._revoked or now >= grant.expires_at
        )
        for token in expired:
            self._deactivate_grant(token)
            self._grants.pop(token, None)
            self._buckets.pop(token, None)
            self._revoked.discard(token)

    def issue_grant(
        self,
        *,
        session_id: str,
        participant_id: str,
        peer_spki_sha256: str,
        connection_generation: int,
        expires_at: float,
        allowed_take_ids: Iterable[str],
    ) -> SecureMediaGrant:
        """Create a registered capability after peer enrollment succeeds."""

        now = self._now()
        grant = SecureMediaGrant(
            grant_token=secrets.token_urlsafe(32),
            session_id=session_id,
            participant_id=participant_id,
            peer_spki_sha256=peer_spki_sha256,
            connection_generation=connection_generation,
            expires_at=expires_at,
            allowed_take_ids=tuple(allowed_take_ids),
        )
        if grant.session_id != self.session_id:
            raise MediaAuthorizationError(
                "The media grant is not valid for this session."
            )
        if grant.expires_at <= now:
            raise MediaAuthorizationError("The media grant has expired.")
        if grant.expires_at > now + MAX_GRANT_LIFETIME_SECONDS:
            raise MediaAuthorizationError("The media grant lifetime is too long.")
        with self._lock:
            self._prune_grants(now)
            # One participant gets one current upload capability. Reconnect
            # rotates the grant instead of multiplying rate and memory limits.
            for token, current in tuple(self._grants.items()):
                if current.participant_id == grant.participant_id:
                    self._deactivate_grant(token)
                    self._grants.pop(token, None)
                    self._revoked.discard(token)
            if len(self._grants) >= MAX_ACTIVE_GRANTS:
                raise MediaCapacityError("The session has too many media grants.")
            self._grants[grant.grant_token] = grant
            self._buckets[grant.grant_token] = _TokenBucket(
                rate=float(self.rate_bytes_per_second),
                capacity=float(self.burst_bytes),
                tokens=float(self.burst_bytes),
                updated_at=now,
            )
        return grant

    def revoke_grant(self, grant: SecureMediaGrant) -> None:
        """Revoke a capability and cancel its active transfers in memory."""

        with self._lock:
            registered = self._registered_grant(grant)
            self._deactivate_grant(registered.grant_token)

    def _registered_grant(self, grant: SecureMediaGrant) -> SecureMediaGrant:
        if not isinstance(grant, SecureMediaGrant):
            raise MediaAuthorizationError("The media grant was rejected.")
        registered = self._grants.get(grant.grant_token)
        if registered is None or registered != grant:
            raise MediaAuthorizationError("The media grant was rejected.")
        return registered

    def _deactivate_grant(self, grant_token: str) -> None:
        self._revoked.add(grant_token)
        self._buckets.pop(grant_token, None)
        for key, state in self._states.items():
            if (
                state.grant_token == grant_token
                and state.phase is MediaTransferPhase.ACTIVE
            ):
                state.phase = MediaTransferPhase.CANCELLED
                self._disk_remaining.pop(key, None)

    def _authorize(
        self,
        grant: SecureMediaGrant,
        descriptor: TransferDescriptor,
        *,
        peer_spki_sha256: str,
        connection_generation: int,
    ) -> SecureMediaGrant:
        registered = self._registered_grant(grant)
        now = self._now()
        if (
            registered.grant_token in self._revoked
            or now >= registered.expires_at
        ):
            self._deactivate_grant(registered.grant_token)
            raise MediaAuthorizationError("The media grant is no longer active.")
        try:
            observed_pin = _spki_sha256(peer_spki_sha256)
            observed_generation = _bounded_generation(
                connection_generation,
                label="connection_generation",
                maximum=MAX_CONNECTION_GENERATION,
            )
        except (TypeError, ValueError):
            raise MediaAuthorizationError("The authenticated peer was rejected.") from None
        if not hmac.compare_digest(observed_pin, registered.peer_spki_sha256):
            raise MediaAuthorizationError("The authenticated peer was rejected.")
        if observed_generation != registered.connection_generation:
            raise MediaAuthorizationError("The authenticated peer was rejected.")
        if not isinstance(descriptor, TransferDescriptor):
            raise MediaAuthorizationError("The media descriptor was rejected.")
        if (
            descriptor.session_id != self.session_id
            or descriptor.session_id != registered.session_id
            or descriptor.participant_id != registered.participant_id
            or descriptor.take_id not in registered.allowed_take_ids
        ):
            raise MediaAuthorizationError("The media descriptor was rejected.")
        if descriptor.size_bytes > self.max_file_bytes:
            raise MediaCapacityError("The declared media file is too large.")
        return registered

    @staticmethod
    def _transfer_generation(value: int) -> int:
        try:
            return _bounded_generation(
                value,
                label="transfer_generation",
                maximum=MAX_TRANSFER_GENERATION,
            )
        except (TypeError, ValueError):
            raise MediaGenerationError(
                "The media transfer generation was rejected."
            ) from None

    def _safe_store_status(self, descriptor: TransferDescriptor) -> TransferReceipt:
        try:
            return self.store.status(descriptor)
        except TransferConflictError as exc:
            raise MediaConflictError(expected_offset=exc.expected_offset) from None
        except OSError:
            raise MediaStorageError("Secure media storage could not be read.") from None

    def _check_disk(self, additional_remaining: int = 0) -> None:
        try:
            free_bytes = int(self._disk_free_probe(self.store.root))
        except Exception:
            raise MediaStorageError("Available media storage could not be checked.") from None
        if free_bytes < 0:
            raise MediaStorageError("Available media storage could not be checked.")
        reserved = sum(self._disk_remaining.values()) + max(
            0, int(additional_remaining)
        )
        if free_bytes - reserved < self.min_free_bytes:
            raise MediaCapacityError("There is not enough free space for this media.")

    def begin_transfer(
        self,
        grant: SecureMediaGrant,
        descriptor: TransferDescriptor,
        *,
        peer_spki_sha256: str,
        connection_generation: int,
        transfer_generation: int,
    ) -> MediaTransferStatus:
        """Authorize and reserve a declared immutable WAV segment."""

        with self._lock:
            registered = self._authorize(
                grant,
                descriptor,
                peer_spki_sha256=peer_spki_sha256,
                connection_generation=connection_generation,
            )
            generation = self._transfer_generation(transfer_generation)
            key = self._key(descriptor)
            known = self._known_descriptors.get(key)
            if known is not None and known != descriptor:
                raise MediaConflictError()
            state = self._states.get(key)
            if state is not None:
                if state.descriptor != descriptor:
                    raise MediaConflictError()
                if state.phase is MediaTransferPhase.ACTIVE:
                    if (
                        state.transfer_generation != generation
                        or state.grant_token != registered.grant_token
                    ):
                        raise MediaGenerationError(
                            "The media transfer generation was rejected."
                        )
                    receipt = self._safe_store_status(descriptor)
                    return self._status(receipt, state.phase)
                if state.phase is MediaTransferPhase.VERIFIED:
                    if state.transfer_generation != generation:
                        raise MediaGenerationError(
                            "The media transfer generation was rejected."
                        )
                    receipt = self._safe_store_status(descriptor)
                    return self._status(receipt, state.phase)
                if state.phase is MediaTransferPhase.NEEDS_ATTENTION:
                    raise MediaCancelledError(
                        "The failed media transfer requires a new segment."
                    )
                if generation <= state.transfer_generation:
                    raise MediaGenerationError(
                        "A cancelled media transfer requires a newer generation."
                    )

            receipt = self._safe_store_status(descriptor)
            if receipt.error or (
                not receipt.complete
                and receipt.received_bytes >= descriptor.size_bytes
            ):
                raise MediaIntegrityError(
                    "The stored media transfer requires attention."
                )
            is_new = known is None
            if is_new and len(self._known_descriptors) >= MAX_STORED_TRANSFERS:
                raise MediaCapacityError(
                    "The session has too many stored media transfers."
                )
            if is_new and self._quota_bytes + descriptor.size_bytes > self.max_session_bytes:
                raise MediaCapacityError("The session media quota would be exceeded.")
            remaining = max(0, descriptor.size_bytes - receipt.received_bytes)
            if remaining:
                self._check_disk(remaining)
            if is_new:
                self._known_descriptors[key] = descriptor
                self._quota_bytes += descriptor.size_bytes
            phase = (
                MediaTransferPhase.VERIFIED
                if receipt.complete
                else MediaTransferPhase.ACTIVE
            )
            self._states[key] = _TransferState(
                descriptor=descriptor,
                grant_token=registered.grant_token,
                transfer_generation=generation,
                phase=phase,
            )
            if remaining:
                self._disk_remaining[key] = remaining
            else:
                self._disk_remaining.pop(key, None)
            return self._status(receipt, phase)

    def _consume_rate(self, grant_token: str, amount: int) -> None:
        bucket = self._buckets.get(grant_token)
        if bucket is None:
            raise MediaAuthorizationError("The media grant is no longer active.")
        now = self._now()
        bucket.consume(amount, now)

    @staticmethod
    def _validate_chunk(offset: int, data: bytes, descriptor: TransferDescriptor, maximum: int) -> int:
        if isinstance(offset, bool):
            raise MediaConflictError()
        parsed_offset = int(offset)
        if parsed_offset < 0:
            raise MediaConflictError()
        if type(data) is not bytes or not data or len(data) > maximum:
            raise MediaConflictError()
        if parsed_offset + len(data) > descriptor.size_bytes:
            raise MediaConflictError()
        return parsed_offset

    @staticmethod
    def _status(
        receipt: TransferReceipt, phase: MediaTransferPhase
    ) -> MediaTransferStatus:
        return MediaTransferStatus(
            received_bytes=receipt.received_bytes,
            declared_bytes=receipt.descriptor.size_bytes,
            phase=phase,
        )

    @staticmethod
    def _verify_complete_replay(
        receipt: TransferReceipt, *, offset: int, data: bytes
    ) -> None:
        if receipt.path is None or offset + len(data) > receipt.received_bytes:
            raise MediaConflictError(expected_offset=receipt.received_bytes)
        try:
            with receipt.path.open("rb") as handle:
                handle.seek(offset)
                persisted = handle.read(len(data))
        except OSError:
            raise MediaStorageError("Verified media could not be read.") from None
        if not hmac.compare_digest(persisted, data):
            raise MediaConflictError(expected_offset=receipt.received_bytes)

    def receive_chunk(
        self,
        grant: SecureMediaGrant,
        descriptor: TransferDescriptor,
        *,
        peer_spki_sha256: str,
        connection_generation: int,
        transfer_generation: int,
        offset: int,
        data: bytes,
    ) -> MediaTransferStatus:
        """Persist one bounded raw chunk, or raise a bounded retry immediately."""

        callback_media: VerifiedMedia | None = None
        with self._lock:
            registered = self._authorize(
                grant,
                descriptor,
                peer_spki_sha256=peer_spki_sha256,
                connection_generation=connection_generation,
            )
            generation = self._transfer_generation(transfer_generation)
            key = self._key(descriptor)
            state = self._states.get(key)
            if state is None or state.descriptor != descriptor:
                raise MediaGenerationError(
                    "Begin the media transfer before sending chunks."
                )
            if (
                state.transfer_generation != generation
                or state.grant_token != registered.grant_token
            ):
                raise MediaGenerationError(
                    "The media transfer generation was rejected."
                )
            if state.phase is MediaTransferPhase.CANCELLED:
                raise MediaCancelledError("The media transfer was cancelled.")
            if state.phase is MediaTransferPhase.NEEDS_ATTENTION:
                raise MediaCancelledError(
                    "The failed media transfer requires a new segment."
                )
            parsed_offset = self._validate_chunk(
                offset, data, descriptor, self.max_raw_chunk_bytes
            )
            self._consume_rate(registered.grant_token, len(data))
            before = self._safe_store_status(descriptor)
            if before.complete:
                self._verify_complete_replay(
                    before, offset=parsed_offset, data=data
                )
                state.phase = MediaTransferPhase.VERIFIED
                return self._status(before, state.phase)
            if parsed_offset >= before.received_bytes:
                self._check_disk()
            try:
                receipt = self.store.append(
                    descriptor, offset=parsed_offset, data=data
                )
            except TransferConflictError as exc:
                raise MediaConflictError(expected_offset=exc.expected_offset) from None
            except TransferIntegrityError:
                state.phase = MediaTransferPhase.NEEDS_ATTENTION
                self._disk_remaining.pop(key, None)
                raise MediaIntegrityError(
                    "The uploaded media failed integrity or PCM verification."
                ) from None
            except OSError:
                raise MediaStorageError("Secure media storage could not be updated.") from None
            remaining = max(0, descriptor.size_bytes - receipt.received_bytes)
            if remaining:
                self._disk_remaining[key] = remaining
            else:
                self._disk_remaining.pop(key, None)
            if receipt.complete:
                state.phase = MediaTransferPhase.VERIFIED
                if key not in self._notified:
                    if receipt.path is None:
                        raise MediaStorageError(
                            "Verified media publication was incomplete."
                        )
                    self._notified.add(key)
                    callback_media = VerifiedMedia(descriptor, receipt.path)
            status = self._status(receipt, state.phase)
        if callback_media is not None and self._on_verified is not None:
            try:
                self._on_verified(callback_media)
            except Exception:
                raise MediaCallbackError(
                    "Verified media notification failed."
                ) from None
        return status

    def cancel_transfer(
        self,
        grant: SecureMediaGrant,
        descriptor: TransferDescriptor,
        *,
        peer_spki_sha256: str,
        connection_generation: int,
        transfer_generation: int,
    ) -> MediaTransferStatus:
        """Cancel future chunks without deleting the recoverable partial file."""

        with self._lock:
            registered = self._authorize(
                grant,
                descriptor,
                peer_spki_sha256=peer_spki_sha256,
                connection_generation=connection_generation,
            )
            generation = self._transfer_generation(transfer_generation)
            key = self._key(descriptor)
            state = self._states.get(key)
            if state is None or state.descriptor != descriptor:
                raise MediaGenerationError(
                    "Begin the media transfer before cancelling it."
                )
            if (
                state.transfer_generation != generation
                or state.grant_token != registered.grant_token
            ):
                raise MediaGenerationError(
                    "The media transfer generation was rejected."
                )
            receipt = self._safe_store_status(descriptor)
            if state.phase is MediaTransferPhase.VERIFIED:
                return self._status(receipt, state.phase)
            if state.phase is not MediaTransferPhase.NEEDS_ATTENTION:
                state.phase = MediaTransferPhase.CANCELLED
            self._disk_remaining.pop(key, None)
            return self._status(receipt, state.phase)

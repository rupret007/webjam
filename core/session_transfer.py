"""Authenticated, resumable delivery of a musician's isolated take segments.

The low-latency music path remains Jamulus.  This module is a separate control
and file-delivery plane used after a musician joins a WebJam session:

* a random invitation credential enrolls one persistent installation identity;
* the host assigns a stable participant UUID that survives display-name and
  transient Jamulus channel changes;
* recording state is published as an idempotent, monotonic snapshot; and
* immutable local WAV segments upload in restartable, checksum-verified chunks.

The protocol deliberately never accepts a destination filename from a peer.
Every path is derived from validated UUIDs, partial files stay visible to the
recovery inventory, and a verified upload is atomically published only after
its byte count, SHA-256, and PCM facts all agree with the descriptor.  The
musician's original local file is never moved or deleted.

This service is access controlled, but it is not a replacement for an
Internet-facing encrypted transport.  The production controller binds it only
to the host's private-session interface and the same private LAN on which the
Jamulus session is advertised.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.redaction import redact_text


MAX_JSON_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_SEGMENT_BYTES = 32 * 1024 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_DESCRIPTOR_GAPS = 128
_MAX_DESCRIPTOR_GAP_REASON = 120
_PEER_REQUEST_READ_TIMEOUT_S = 30.0
PRESENCE_V2_DEFAULT_LEASE_S = 15.0
PRESENCE_V2_MIN_LEASE_S = 1.0
PRESENCE_V2_MAX_LEASE_S = 60.0
PRESENCE_V2_MIN_REMAINING_LEASE_MS = 1


class SessionTransferError(RuntimeError):
    """Base error for enrollment, state, and media transfer failures."""


class TransferAuthenticationError(SessionTransferError):
    """Raised when a session or participant credential is invalid."""


class TransferConflictError(SessionTransferError):
    """Raised when a retry contradicts already-persisted upload state."""

    def __init__(self, message: str, *, expected_offset: int | None = None) -> None:
        super().__init__(message)
        self.expected_offset = expected_offset


class TransferIntegrityError(SessionTransferError):
    """Raised when bytes or PCM metadata disagree with the descriptor."""


def _uuid_text(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID.") from exc
    canonical = str(parsed)
    if str(value).lower() != canonical:
        raise ValueError(f"{label} must use canonical UUID form.")
    return canonical


def _token_text(value: str, label: str) -> str:
    text = str(value or "")
    if not _TOKEN_PATTERN.fullmatch(text):
        raise ValueError(f"{label} is not a valid WebJam credential.")
    return text


def _presence_digest_text(value: str) -> str:
    if type(value) is not str:
        raise ValueError("ordered_roster_digest must be lowercase SHA-256.")
    text = value
    if not _SHA256_PATTERN.fullmatch(text):
        raise ValueError("ordered_roster_digest must be lowercase SHA-256.")
    return text


def _presence_challenge_text(value: str) -> str:
    if type(value) is not str:
        raise ValueError("challenge is malformed.")
    text = value
    if not _TOKEN_PATTERN.fullmatch(text):
        raise ValueError("challenge is malformed.")
    return text


def _presence_fingerprint_text(value: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError("host_roster_fingerprint must be lowercase SHA-256.")
    return value


def _presence_int(value: object, label: str, *, positive: bool = False) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer.")
    parsed = value
    if parsed < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{label} must be {qualifier}.")
    return parsed


def _presence_ordinal_tuple(
    value: object, *, roster_count: int, label: str = "ambiguous_ordinals"
) -> tuple[int, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple of roster ordinals.")
    ordinals = tuple(_presence_int(item, label) for item in value)
    if len(set(ordinals)) != len(ordinals) or tuple(sorted(ordinals)) != ordinals:
        raise ValueError(f"{label} must be unique and ordered.")
    if any(ordinal >= roster_count for ordinal in ordinals):
        raise ValueError(f"{label} contains an unavailable roster ordinal.")
    return ordinals


def _clean_name(value: str) -> str:
    clean = " ".join(
        "".join(
            character if character.isprintable() else " " for character in str(value)
        ).split()
    )
    return clean[:80] or "Musician"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_json_secure(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary).unlink()
        except OSError:
            pass
        raise


def load_or_create_installation_id(path: str | Path) -> str:
    """Return one private, durable UUID for this WebJam installation.

    The identifier is intentionally stored separately from ordinary settings:
    changing roles, joining another band, or resetting a session must not
    manufacture a new participant.  A malformed identity fails closed instead
    of being silently replaced, because replacing it would orphan transferred
    media from its durable participant record.
    """

    identity_path = Path(path).expanduser().resolve()
    if identity_path.exists():
        try:
            payload = json.loads(identity_path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                raise ValueError("schema")
            installation_id = _uuid_text(payload["installation_id"], "installation_id")
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The WebJam installation identity is unreadable."
            ) from exc
        try:
            os.chmod(identity_path, 0o600)
        except OSError as exc:
            raise SessionTransferError(
                "The WebJam installation identity could not be protected."
            ) from exc
        return installation_id

    installation_id = str(uuid.uuid4())
    _write_json_secure(
        identity_path,
        {"schema": 1, "installation_id": installation_id},
    )
    return installation_id


def derive_participant_id(session_id: str, installation_id: str) -> str:
    """Derive the session-scoped durable participant ID for an installation."""

    canonical_session = _uuid_text(session_id, "session_id")
    canonical_installation = _uuid_text(installation_id, "installation_id")
    return str(uuid.uuid5(uuid.UUID(canonical_session), canonical_installation))


@dataclass(frozen=True, repr=False)
class SessionCredentials:
    """The private-session identity and one-link enrollment credential."""

    session_id: str
    invite_token: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _uuid_text(self.session_id, "session_id")
        )
        object.__setattr__(
            self, "invite_token", _token_text(self.invite_token, "invite_token")
        )

    @classmethod
    def create(cls) -> "SessionCredentials":
        return cls(str(uuid.uuid4()), secrets.token_urlsafe(32))

    def participant_token(self, participant_id: str) -> str:
        participant_id = _uuid_text(participant_id, "participant_id")
        digest = hmac.new(
            self.invite_token.encode("ascii"),
            f"webjam-participant-v1:{self.session_id}:{participant_id}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        import base64

        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def __repr__(self) -> str:
        """Keep the session correlation ID and enrollment bearer out of logs."""

        return "SessionCredentials(private=[redacted])"


@dataclass(frozen=True, repr=False)
class ParticipantEnrollment:
    participant_id: str
    installation_id: str
    display_name: str
    participant_token: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "participant_id", _uuid_text(self.participant_id, "participant_id")
        )
        object.__setattr__(
            self, "installation_id", _uuid_text(self.installation_id, "installation_id")
        )
        object.__setattr__(self, "display_name", _clean_name(self.display_name))
        object.__setattr__(
            self,
            "participant_token",
            _token_text(self.participant_token, "participant_token"),
        )

    def __repr__(self) -> str:
        """Avoid leaking a musician name, installation ID, or peer bearer."""

        return "ParticipantEnrollment(private=[redacted])"


@dataclass(frozen=True, repr=False)
class PresenceBinding:
    """Legacy authenticated mapping to one client-local Jamulus channel.

    Jamulus client RPC channel numbers are local namespaces.  This v1 binding
    therefore remains useful for UI continuity and Local Original delivery,
    but it is never evidence that identifies a server recorder file.
    """

    participant_id: str
    channel_id: int
    display_name: str
    generation: int
    capture_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "participant_id", _uuid_text(self.participant_id, "participant_id")
        )
        channel_id = int(self.channel_id)
        generation = int(self.generation)
        if channel_id < 0:
            raise ValueError("channel_id cannot be negative.")
        if generation < 0:
            raise ValueError("generation cannot be negative.")
        object.__setattr__(self, "channel_id", channel_id)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "display_name", _clean_name(self.display_name))
        object.__setattr__(self, "capture_enabled", bool(self.capture_enabled))

    @property
    def protocol_version(self) -> int:
        return 1

    @property
    def recorder_eligible(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "PresenceBinding(private=[redacted])"


@dataclass(frozen=True, repr=False)
class PresenceV2Challenge:
    """Short-lived host challenge for one exact ordered server roster."""

    ordered_roster_digest: str
    roster_count: int
    challenge: str
    challenge_epoch: int
    topology_epoch: int
    lease_ms: int
    protocol_version: int = 2

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version != 2:
            raise ValueError("protocol_version must be 2.")
        digest = _presence_digest_text(self.ordered_roster_digest)
        count = _presence_int(self.roster_count, "roster_count", positive=True)
        if count > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("roster_count exceeds the supported limit.")
        challenge = _presence_challenge_text(self.challenge)
        epoch = _presence_int(self.challenge_epoch, "challenge_epoch", positive=True)
        topology_epoch = _presence_int(
            self.topology_epoch, "topology_epoch", positive=True
        )
        lease_ms = _presence_int(self.lease_ms, "lease_ms", positive=True)
        if not PRESENCE_V2_MIN_REMAINING_LEASE_MS <= lease_ms <= int(
            PRESENCE_V2_MAX_LEASE_S * 1000
        ):
            raise ValueError("lease_ms is outside the supported limits.")
        object.__setattr__(self, "ordered_roster_digest", digest)
        object.__setattr__(self, "roster_count", count)
        object.__setattr__(self, "challenge", challenge)
        object.__setattr__(self, "challenge_epoch", epoch)
        object.__setattr__(self, "topology_epoch", topology_epoch)
        object.__setattr__(self, "lease_ms", lease_ms)

    def __repr__(self) -> str:
        return "PresenceV2Challenge(private=[redacted])"


@dataclass(frozen=True, repr=False)
class PresenceV2Proof:
    """Fresh, challenge-scoped recorder-presence claim from an enrolled peer.

    WebJam authenticates the enrolled peer that submits this object and binds
    it to a short-lived host challenge.  A remote ``self_ordinal`` is still a
    cooperative claim, not cryptographic Jamulus identity: indistinguishable
    public profiles and detected ordinal collisions fail closed, and session
    invitations are intended only for trusted collaborators.  Raw Jamulus
    profiles, network addresses, operating-system process IDs, and credentials
    are not accepted by this type and never enter the durable registry.
    """

    participant_id: str
    display_name: str
    ordered_roster_digest: str
    roster_count: int
    self_ordinal: int
    process_generation: int
    rpc_connection_generation: int
    audio_connection_generation: int
    challenge: str
    challenge_epoch: int
    topology_epoch: int
    presence_generation: int
    capture_enabled: bool
    protocol_version: int = 2

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version != 2:
            raise ValueError("protocol_version must be 2.")
        participant_id = _uuid_text(self.participant_id, "participant_id")
        digest = _presence_digest_text(self.ordered_roster_digest)
        count = _presence_int(self.roster_count, "roster_count", positive=True)
        if count > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("roster_count exceeds the supported limit.")
        ordinal = _presence_int(self.self_ordinal, "self_ordinal")
        if ordinal >= count:
            raise ValueError("self_ordinal must identify a roster row.")
        process_generation = _presence_int(
            self.process_generation, "process_generation", positive=True
        )
        rpc_generation = _presence_int(
            self.rpc_connection_generation,
            "rpc_connection_generation",
            positive=True,
        )
        audio_generation = _presence_int(
            self.audio_connection_generation,
            "audio_connection_generation",
            positive=True,
        )
        challenge = _presence_challenge_text(self.challenge)
        challenge_epoch = _presence_int(
            self.challenge_epoch, "challenge_epoch", positive=True
        )
        topology_epoch = _presence_int(
            self.topology_epoch, "topology_epoch", positive=True
        )
        presence_generation = _presence_int(
            self.presence_generation, "presence_generation", positive=True
        )
        if type(self.capture_enabled) is not bool:
            raise ValueError("capture_enabled must be a boolean.")
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "display_name", _clean_name(self.display_name))
        object.__setattr__(self, "ordered_roster_digest", digest)
        object.__setattr__(self, "roster_count", count)
        object.__setattr__(self, "self_ordinal", ordinal)
        object.__setattr__(self, "process_generation", process_generation)
        object.__setattr__(self, "rpc_connection_generation", rpc_generation)
        object.__setattr__(self, "audio_connection_generation", audio_generation)
        object.__setattr__(self, "challenge", challenge)
        object.__setattr__(self, "challenge_epoch", challenge_epoch)
        object.__setattr__(self, "topology_epoch", topology_epoch)
        object.__setattr__(self, "presence_generation", presence_generation)

    @property
    def recorder_eligible(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "PresenceV2Proof(private=[redacted])"


@dataclass(repr=False)
class _PresenceV2Epoch:
    """One memory-only challenge epoch and its enrolled-peer claims."""

    challenge: str
    challenge_epoch: int
    topology_epoch: int
    expires_at: float
    required_ordinals: dict[str, int]
    required_capture_participants: set[str]
    by_ordinal: dict[int, PresenceV2Proof]
    by_participant: dict[str, PresenceV2Proof]


class EnrollmentRegistry:
    """Durable per-session mapping from installation UUID to participant UUID."""

    def __init__(
        self,
        root: str | Path,
        credentials: SessionCredentials,
        *,
        presence_clock: Callable[[], float] | None = None,
        presence_v2_lease_s: float = PRESENCE_V2_DEFAULT_LEASE_S,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.credentials = credentials
        self.path = self.root / "webjam-participants.json"
        self._lock = threading.RLock()
        self._participants: dict[str, dict[str, Any]] = {}
        if isinstance(presence_v2_lease_s, bool):
            raise ValueError("presence_v2_lease_s must be a finite number.")
        lease_s = float(presence_v2_lease_s)
        if (
            not math.isfinite(lease_s)
            or not PRESENCE_V2_MIN_LEASE_S <= lease_s <= PRESENCE_V2_MAX_LEASE_S
        ):
            raise ValueError("presence_v2_lease_s is outside the supported limits.")
        self._presence_clock = presence_clock or time.monotonic
        self._presence_v2_lease_s = lease_s
        # Presence v2 is deliberately memory-only.  These fields are never
        # included in webjam-participants.json or a support bundle.
        self._presence_v2_digest: str | None = None
        self._presence_v2_roster_count: int | None = None
        self._presence_v2_host_generations: tuple[int, int, int] | None = None
        self._presence_v2_host_roster_fingerprint: str | None = None
        self._presence_v2_ambiguous_ordinals: tuple[int, ...] | None = None
        self._presence_v2_challenge_epoch = 0
        self._presence_v2_topology_epoch = 0
        self._presence_v2_active: _PresenceV2Epoch | None = None
        self._presence_v2_pending: _PresenceV2Epoch | None = None
        self._presence_v2_generation_highwater: dict[str, int] = {}
        self._presence_v2_conflicted_ordinals: set[int] = set()
        self._presence_v2_conflicted_participants: set[str] = set()
        self._presence_v2_acceptance_sequence = 0
        self._presence_v2_last_capture_sequence: dict[str, int] = {}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionTransferError(
                "The participant registry is unreadable."
            ) from exc
        if (
            payload.get("schema") != 1
            or payload.get("session_id") != self.credentials.session_id
        ):
            raise SessionTransferError(
                "The participant registry belongs to another session."
            )
        records = payload.get("participants", [])
        if not isinstance(records, list):
            raise SessionTransferError("The participant registry is malformed.")
        loaded: dict[str, dict[str, str]] = {}
        participant_ids: set[str] = set()
        try:
            for record in records:
                installation_id = _uuid_text(
                    record["installation_id"], "installation_id"
                )
                participant_id = _uuid_text(record["participant_id"], "participant_id")
                if installation_id in loaded or participant_id in participant_ids:
                    raise ValueError("duplicate participant identity")
                raw_channel = record.get("channel_id")
                channel_id = None if raw_channel is None else int(raw_channel)
                if channel_id is not None and channel_id < 0:
                    raise ValueError("negative channel")
                generation = int(record.get("presence_generation", 0))
                if generation < 0:
                    raise ValueError("negative presence generation")
                loaded[installation_id] = {
                    "participant_id": participant_id,
                    "display_name": _clean_name(record.get("display_name", "Musician")),
                    "channel_id": channel_id,
                    "presence_generation": generation,
                    "capture_enabled": bool(record.get("capture_enabled", False)),
                }
                participant_ids.add(participant_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The participant registry is malformed."
            ) from exc
        self._participants = loaded

    def _save(self) -> None:
        records = [
            {
                "installation_id": installation_id,
                "participant_id": record["participant_id"],
                "display_name": record["display_name"],
                "channel_id": record.get("channel_id"),
                "presence_generation": int(record.get("presence_generation", 0)),
                "capture_enabled": bool(record.get("capture_enabled", False)),
            }
            for installation_id, record in sorted(self._participants.items())
        ]
        _write_json_secure(
            self.path,
            {
                "schema": 1,
                "session_id": self.credentials.session_id,
                "participants": records,
            },
        )

    def enroll(
        self,
        installation_id: str,
        display_name: str,
        *,
        invite_token: str,
    ) -> ParticipantEnrollment:
        if not hmac.compare_digest(
            str(invite_token or ""), self.credentials.invite_token
        ):
            raise TransferAuthenticationError("The session invitation is not valid.")
        installation_id = _uuid_text(installation_id, "installation_id")
        clean_name = _clean_name(display_name)
        with self._lock:
            record = self._participants.get(installation_id)
            if record is None:
                record = {
                    "participant_id": derive_participant_id(
                        self.credentials.session_id, installation_id
                    ),
                    "display_name": clean_name,
                    "channel_id": None,
                    "presence_generation": 0,
                    "capture_enabled": False,
                }
                self._participants[installation_id] = record
                self._save()
            elif record["display_name"] != clean_name:
                # A mutable display name never changes the durable identity.
                record["display_name"] = clean_name
                self._save()
            participant_id = record["participant_id"]
            return ParticipantEnrollment(
                participant_id=participant_id,
                installation_id=installation_id,
                display_name=record["display_name"],
                participant_token=self.credentials.participant_token(participant_id),
            )

    def authenticate(self, participant_id: str, participant_token: str) -> bool:
        try:
            participant_id = _uuid_text(participant_id, "participant_id")
            supplied = _token_text(participant_token, "participant_token")
        except ValueError:
            return False
        with self._lock:
            known = any(
                record["participant_id"] == participant_id
                for record in self._participants.values()
            )
        expected = self.credentials.participant_token(participant_id)
        return bool(known and hmac.compare_digest(supplied, expected))

    def participant_id_for_installation(self, installation_id: str) -> str | None:
        installation_id = _uuid_text(installation_id, "installation_id")
        with self._lock:
            record = self._participants.get(installation_id)
            return record["participant_id"] if record else None

    def bind_presence(
        self,
        participant_id: str,
        channel_id: int,
        display_name: str,
        *,
        generation: int,
        capture_enabled: bool = False,
    ) -> PresenceBinding:
        """Publish one participant's current transient Jamulus channel.

        Generations make reconnect/rename retries idempotent and prevent a
        delayed older request from replacing a newer channel binding.
        """

        candidate = PresenceBinding(
            participant_id=participant_id,
            channel_id=channel_id,
            display_name=display_name,
            generation=generation,
            capture_enabled=capture_enabled,
        )
        with self._lock:
            record = next(
                (
                    item
                    for item in self._participants.values()
                    if item["participant_id"] == candidate.participant_id
                ),
                None,
            )
            if record is None:
                raise TransferAuthenticationError(
                    "Participant enrollment was not found."
                )
            prior_generation = int(record.get("presence_generation", 0))
            prior_channel = record.get("channel_id")
            prior_name = record["display_name"]
            if candidate.generation < prior_generation:
                raise TransferConflictError(
                    "A newer participant presence is already published."
                )
            if candidate.generation == prior_generation:
                if (
                    prior_channel == candidate.channel_id
                    and prior_name == candidate.display_name
                    and bool(record.get("capture_enabled", False))
                    == candidate.capture_enabled
                ):
                    return candidate
                raise TransferConflictError(
                    "That presence generation already describes another channel."
                )

            # A transient channel can have only one current durable owner.
            # Clear a stale prior claimant before assigning the newer binding.
            for other in self._participants.values():
                if (
                    other is not record
                    and other.get("channel_id") == candidate.channel_id
                ):
                    other["channel_id"] = None
            record["channel_id"] = candidate.channel_id
            record["display_name"] = candidate.display_name
            record["presence_generation"] = candidate.generation
            record["capture_enabled"] = candidate.capture_enabled
            self._save()
            return candidate

    def presence_for_participant(self, participant_id: str) -> PresenceBinding | None:
        participant_id = _uuid_text(participant_id, "participant_id")
        with self._lock:
            for record in self._participants.values():
                if record["participant_id"] != participant_id:
                    continue
                channel_id = record.get("channel_id")
                if channel_id is None:
                    return None
                return PresenceBinding(
                    participant_id=participant_id,
                    channel_id=int(channel_id),
                    display_name=record["display_name"],
                    generation=int(record.get("presence_generation", 0)),
                    capture_enabled=bool(record.get("capture_enabled", False)),
                )
        return None

    def presence_for_channel(self, channel_id: int) -> PresenceBinding | None:
        """Return one currently authenticated owner for a transient channel."""

        channel_id = int(channel_id)
        if channel_id < 0:
            raise ValueError("channel_id cannot be negative.")
        with self._lock:
            matches = [
                record
                for record in self._participants.values()
                if record.get("channel_id") == channel_id
            ]
            if len(matches) != 1:
                return None
            record = matches[0]
            return PresenceBinding(
                participant_id=record["participant_id"],
                channel_id=channel_id,
                display_name=record["display_name"],
                generation=int(record.get("presence_generation", 0)),
                capture_enabled=bool(record.get("capture_enabled", False)),
            )

    def reconcile_presence_channels(self, active_channel_ids: Iterable[int]) -> int:
        """Retire bindings absent from one authenticated Jamulus roster.

        A Jamulus channel number is transient and may later be reused by an
        unrelated client.  Preserve the last authenticated generation as a
        tombstone, but remove its live-channel claim until that participant
        publishes a newer signed presence.
        """

        try:
            active = {int(value) for value in active_channel_ids}
        except (TypeError, ValueError) as exc:
            raise ValueError("active channel IDs must be integers.") from exc
        if any(value < 0 for value in active):
            raise ValueError("active channel IDs cannot be negative.")
        changed = 0
        with self._lock:
            for record in self._participants.values():
                channel_id = record.get("channel_id")
                if channel_id is not None and int(channel_id) not in active:
                    record["channel_id"] = None
                    changed += 1
            if changed:
                self._save()
        return changed

    def participant_id_for_channel(self, channel_id: int) -> str | None:
        binding = self.presence_for_channel(channel_id)
        return binding.participant_id if binding is not None else None

    def _presence_v2_now_locked(self) -> float:
        try:
            now = float(self._presence_clock())
        except (TypeError, ValueError, OverflowError) as exc:
            raise SessionTransferError(
                "The recorder-presence freshness clock is unavailable."
            ) from exc
        if not math.isfinite(now):
            raise SessionTransferError(
                "The recorder-presence freshness clock is unavailable."
            )
        return now

    def _new_presence_v2_epoch_locked(
        self,
        now: float,
        *,
        required_ordinals: Mapping[str, int] | None = None,
        required_capture_participants: Iterable[str] = (),
    ) -> _PresenceV2Epoch:
        self._presence_v2_challenge_epoch += 1
        return _PresenceV2Epoch(
            challenge=secrets.token_urlsafe(32),
            challenge_epoch=self._presence_v2_challenge_epoch,
            topology_epoch=self._presence_v2_topology_epoch,
            expires_at=now + self._presence_v2_lease_s,
            required_ordinals=dict(required_ordinals or {}),
            required_capture_participants=set(required_capture_participants),
            by_ordinal={},
            by_participant={},
        )

    def _pending_presence_v2_complete_locked(self) -> bool:
        active = self._presence_v2_active
        pending = self._presence_v2_pending
        if active is None or pending is None:
            return False
        return all(
            (
                proof := pending.by_participant.get(participant_id)
            ) is not None
            and proof.self_ordinal == ordinal
            for participant_id, ordinal in pending.required_ordinals.items()
        )

    def _poison_presence_v2_conflict_locked(
        self,
        *,
        participant_ids: Iterable[str],
        ordinals: Iterable[int],
    ) -> None:
        """Remove every related claim and tombstone this topology fail-closed."""

        poisoned_participants = set(participant_ids)
        poisoned_ordinals = set(ordinals)
        epochs = tuple(
            epoch
            for epoch in (self._presence_v2_active, self._presence_v2_pending)
            if epoch is not None
        )
        changed = True
        while changed:
            changed = False
            for epoch in epochs:
                for participant_id, ordinal in epoch.required_ordinals.items():
                    if (
                        participant_id in poisoned_participants
                        or ordinal in poisoned_ordinals
                    ):
                        before = (
                            len(poisoned_participants),
                            len(poisoned_ordinals),
                        )
                        poisoned_participants.add(participant_id)
                        poisoned_ordinals.add(ordinal)
                        changed = changed or before != (
                            len(poisoned_participants),
                            len(poisoned_ordinals),
                        )
                for proof in tuple(epoch.by_participant.values()):
                    if (
                        proof.participant_id in poisoned_participants
                        or proof.self_ordinal in poisoned_ordinals
                    ):
                        before = (
                            len(poisoned_participants),
                            len(poisoned_ordinals),
                        )
                        poisoned_participants.add(proof.participant_id)
                        poisoned_ordinals.add(proof.self_ordinal)
                        changed = changed or before != (
                            len(poisoned_participants),
                            len(poisoned_ordinals),
                        )
        self._presence_v2_conflicted_participants.update(poisoned_participants)
        self._presence_v2_conflicted_ordinals.update(poisoned_ordinals)
        for epoch in epochs:
            for participant_id in poisoned_participants:
                proof = epoch.by_participant.pop(participant_id, None)
                if proof is not None:
                    epoch.by_ordinal.pop(proof.self_ordinal, None)
            for ordinal in poisoned_ordinals:
                proof = epoch.by_ordinal.pop(ordinal, None)
                if proof is not None:
                    epoch.by_participant.pop(proof.participant_id, None)

    def _promote_pending_presence_v2_locked(self, *, complete: bool) -> None:
        if self._presence_v2_pending is None:
            return
        if complete:
            self._presence_v2_pending.required_ordinals = {
                participant_id: proof.self_ordinal
                for participant_id, proof in (
                    self._presence_v2_pending.by_participant.items()
                )
            }
            self._presence_v2_pending.required_capture_participants = {
                participant_id
                for participant_id, proof in (
                    self._presence_v2_pending.by_participant.items()
                )
                if proof.capture_enabled
            }
        self._presence_v2_active = self._presence_v2_pending
        self._presence_v2_pending = None

    def _advance_presence_v2_epochs_locked(self, now: float) -> None:
        """Roll challenges without exposing a partial unchanged-roster gap."""

        active = self._presence_v2_active
        if active is None:
            self._presence_v2_active = self._new_presence_v2_epoch_locked(now)
            return
        if self._pending_presence_v2_complete_locked():
            self._promote_pending_presence_v2_locked(complete=True)
            active = self._presence_v2_active
        if active is not None and now >= active.expires_at:
            # The old snapshot is now strictly stale. Promote whatever fresh
            # claims exist; a disconnected participant therefore disappears
            # instead of receiving an unbounded grace period.
            if self._presence_v2_pending is not None:
                self._promote_pending_presence_v2_locked(complete=False)
            else:
                self._presence_v2_active = self._new_presence_v2_epoch_locked(now)
            active = self._presence_v2_active
        if active is None:
            return
        if self._presence_v2_pending is None and (
            active.expires_at - now <= self._presence_v2_lease_s / 2.0
        ):
            required = dict(active.required_ordinals) or {
                participant_id: proof.self_ordinal
                for participant_id, proof in active.by_participant.items()
            }
            required_capture = (
                set(active.required_capture_participants)
                if active.required_ordinals
                else {
                    participant_id
                    for participant_id, proof in active.by_participant.items()
                    if proof.capture_enabled
                }
            )
            pending = self._new_presence_v2_epoch_locked(
                now,
                required_ordinals=required,
                required_capture_participants=required_capture,
            )
            if not required:
                self._presence_v2_active = pending
            else:
                self._presence_v2_pending = pending

    def _presence_v2_challenge_snapshot_locked(
        self, now: float
    ) -> PresenceV2Challenge:
        self._advance_presence_v2_epochs_locked(now)
        epoch = self._presence_v2_pending or self._presence_v2_active
        if epoch is None:
            raise TransferConflictError(
                "Recorder presence requires a proven host roster."
            )
        # ``expires_at - now`` is a float difference; at the creation instant
        # ``(now + lease) - now`` can exceed the granted lease by one ulp, and
        # ``math.ceil`` amplifies that sub-nanosecond artifact into a full
        # extra millisecond (a 15 000 ms lease reported as 15 001 ms).  Never
        # promise more remaining time than this registry actually granted;
        # the constructor already bounds the grant by PRESENCE_V2_MAX_LEASE_S.
        granted_lease_ms = int(round(self._presence_v2_lease_s * 1000))
        remaining_ms = min(
            granted_lease_ms,
            max(
                PRESENCE_V2_MIN_REMAINING_LEASE_MS,
                math.ceil((epoch.expires_at - now) * 1000),
            ),
        )
        assert self._presence_v2_digest is not None
        assert self._presence_v2_roster_count is not None
        return PresenceV2Challenge(
            ordered_roster_digest=self._presence_v2_digest,
            roster_count=self._presence_v2_roster_count,
            challenge=epoch.challenge,
            challenge_epoch=epoch.challenge_epoch,
            topology_epoch=epoch.topology_epoch,
            lease_ms=remaining_ms,
        )

    def install_presence_v2_roster(
        self,
        ordered_roster_digest: str,
        roster_count: int,
        *,
        host_roster_fingerprint: str,
        ambiguous_ordinals: tuple[int, ...] = (),
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        force_rotate: bool = False,
    ) -> PresenceV2Challenge:
        """Install one exact host-proven roster and issue its challenge.

        The host's connection generations are correlation epochs, not process
        IDs.  Any exact roster or lifecycle change invalidates all prior
        claims. Repeated observations of the same proof are idempotent.
        """

        digest = _presence_digest_text(ordered_roster_digest)
        fingerprint = _presence_fingerprint_text(host_roster_fingerprint)
        count = _presence_int(roster_count, "roster_count", positive=True)
        if count > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("roster_count exceeds the supported limit.")
        ambiguous = _presence_ordinal_tuple(
            ambiguous_ordinals, roster_count=count
        )
        generations = (
            _presence_int(
                process_generation, "process_generation", positive=True
            ),
            _presence_int(
                rpc_connection_generation,
                "rpc_connection_generation",
                positive=True,
            ),
            _presence_int(
                audio_connection_generation,
                "audio_connection_generation",
                positive=True,
            ),
        )
        if type(force_rotate) is not bool:
            raise ValueError("force_rotate must be a boolean.")
        with self._lock:
            now = self._presence_v2_now_locked()
            changed = (
                self._presence_v2_digest != digest
                or self._presence_v2_roster_count != count
                or self._presence_v2_host_generations != generations
                or self._presence_v2_host_roster_fingerprint != fingerprint
                or self._presence_v2_ambiguous_ordinals != ambiguous
            )
            self._presence_v2_digest = digest
            self._presence_v2_roster_count = count
            self._presence_v2_host_generations = generations
            self._presence_v2_host_roster_fingerprint = fingerprint
            self._presence_v2_ambiguous_ordinals = ambiguous
            if changed:
                # A different server proof has no safe overlap. Every old
                # claim is invalid immediately and the new epoch starts empty.
                self._presence_v2_topology_epoch += 1
                self._presence_v2_conflicted_ordinals.clear()
                self._presence_v2_conflicted_participants.clear()
                self._presence_v2_active = self._new_presence_v2_epoch_locked(now)
                self._presence_v2_pending = None
            elif force_rotate:
                active = self._presence_v2_active
                required = (
                    dict(active.required_ordinals)
                    or {
                        participant_id: proof.self_ordinal
                        for participant_id, proof in active.by_participant.items()
                    }
                    if active is not None
                    else {}
                )
                required_capture = (
                    (
                        set(active.required_capture_participants)
                        if active.required_ordinals
                        else {
                            participant_id
                            for participant_id, proof in active.by_participant.items()
                            if proof.capture_enabled
                        }
                    )
                    if active is not None
                    else set()
                )
                pending = self._new_presence_v2_epoch_locked(
                    now,
                    required_ordinals=required,
                    required_capture_participants=required_capture,
                )
                if required:
                    self._presence_v2_pending = pending
                else:
                    self._presence_v2_active = pending
                    self._presence_v2_pending = None
            return self._presence_v2_challenge_snapshot_locked(now)

    def current_presence_v2_challenge(self) -> PresenceV2Challenge:
        """Return a fresh challenge for the currently proven host roster."""

        with self._lock:
            if (
                self._presence_v2_digest is None
                or self._presence_v2_roster_count is None
                or self._presence_v2_host_generations is None
                or self._presence_v2_host_roster_fingerprint is None
                or self._presence_v2_ambiguous_ordinals is None
            ):
                raise TransferConflictError(
                    "Recorder presence requires a proven host roster."
                )
            now = self._presence_v2_now_locked()
            return self._presence_v2_challenge_snapshot_locked(now)

    def invalidate_presence_v2(self) -> None:
        """Forget every recorder claim and challenge without writing to disk."""

        with self._lock:
            self._presence_v2_digest = None
            self._presence_v2_roster_count = None
            self._presence_v2_host_generations = None
            self._presence_v2_host_roster_fingerprint = None
            self._presence_v2_ambiguous_ordinals = None
            self._presence_v2_active = None
            self._presence_v2_pending = None
            self._presence_v2_conflicted_ordinals.clear()
            self._presence_v2_conflicted_participants.clear()

    def bind_presence_v2(
        self,
        participant_id: str,
        display_name: str,
        *,
        ordered_roster_digest: str,
        roster_count: int,
        self_ordinal: int,
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        challenge: str,
        challenge_epoch: int,
        topology_epoch: int,
        presence_generation: int,
        capture_enabled: bool,
        _allow_ambiguous_ordinal: bool = False,
    ) -> PresenceV2Proof:
        """Accept one fresh cooperative claim from an authenticated WebJam peer.

        The host can prove its own local-zero ordinal from the process-bound
        Jamulus RPC source, so the private override is reserved for that local
        call path.  Remote peers cannot use it through the wire API.
        """

        if type(_allow_ambiguous_ordinal) is not bool:
            raise ValueError("_allow_ambiguous_ordinal must be a boolean.")

        candidate = PresenceV2Proof(
            participant_id=participant_id,
            display_name=display_name,
            ordered_roster_digest=ordered_roster_digest,
            roster_count=roster_count,
            self_ordinal=self_ordinal,
            process_generation=process_generation,
            rpc_connection_generation=rpc_connection_generation,
            audio_connection_generation=audio_connection_generation,
            challenge=challenge,
            challenge_epoch=challenge_epoch,
            topology_epoch=topology_epoch,
            presence_generation=presence_generation,
            capture_enabled=capture_enabled,
        )
        with self._lock:
            record = next(
                (
                    item
                    for item in self._participants.values()
                    if item["participant_id"] == candidate.participant_id
                ),
                None,
            )
            if record is None:
                raise TransferAuthenticationError(
                    "Participant enrollment was not found."
                )
            now = self._presence_v2_now_locked()
            self._advance_presence_v2_epochs_locked(now)
            target = next(
                (
                    epoch
                    for epoch in (
                        self._presence_v2_active,
                        self._presence_v2_pending,
                    )
                    if epoch is not None
                    and now < epoch.expires_at
                    and candidate.challenge_epoch == epoch.challenge_epoch
                    and hmac.compare_digest(candidate.challenge, epoch.challenge)
                ),
                None,
            )
            if target is None:
                raise TransferConflictError(
                    "The recorder-presence challenge is stale."
                )
            if (
                candidate.ordered_roster_digest != self._presence_v2_digest
                or candidate.roster_count != self._presence_v2_roster_count
                or candidate.topology_epoch != self._presence_v2_topology_epoch
            ):
                raise TransferConflictError(
                    "The recorder-presence roster does not match the host."
                )
            ambiguous_ordinals = self._presence_v2_ambiguous_ordinals
            if ambiguous_ordinals is None:
                raise TransferConflictError(
                    "Recorder presence requires a proven host roster."
                )
            if (
                candidate.self_ordinal in ambiguous_ordinals
                and not _allow_ambiguous_ordinal
            ):
                raise TransferConflictError(
                    "The recorder-presence roster ordinal is ambiguous."
                )
            if (
                candidate.participant_id
                in self._presence_v2_conflicted_participants
                or candidate.self_ordinal in self._presence_v2_conflicted_ordinals
            ):
                raise TransferConflictError(
                    "The recorder-presence identity has a topology conflict."
                )
            prior_generation = self._presence_v2_generation_highwater.get(
                candidate.participant_id, 0
            )
            if candidate.presence_generation <= prior_generation:
                raise TransferConflictError(
                    "The recorder-presence generation is stale or replayed."
                )
            epochs = tuple(
                epoch
                for epoch in (self._presence_v2_active, self._presence_v2_pending)
                if epoch is not None
            )
            participant_ordinals = {
                proof.self_ordinal
                for epoch in epochs
                if (
                    proof := epoch.by_participant.get(candidate.participant_id)
                )
                is not None
            }
            participant_ordinals.update(
                epoch.required_ordinals[candidate.participant_id]
                for epoch in epochs
                if candidate.participant_id in epoch.required_ordinals
            )
            conflicting_participant_ordinals = participant_ordinals - {
                candidate.self_ordinal
            }
            if conflicting_participant_ordinals:
                self._poison_presence_v2_conflict_locked(
                    participant_ids=(candidate.participant_id,),
                    ordinals=(
                        candidate.self_ordinal,
                        *sorted(conflicting_participant_ordinals),
                    ),
                )
                raise TransferConflictError(
                    "The participant claimed a conflicting roster ordinal."
                )

            ordinal_owners = {
                proof.participant_id
                for epoch in epochs
                if (
                    proof := epoch.by_ordinal.get(candidate.self_ordinal)
                )
                is not None
            }
            ordinal_owners.update(
                participant_id
                for epoch in epochs
                for participant_id, ordinal in epoch.required_ordinals.items()
                if ordinal == candidate.self_ordinal
            )
            conflicting_owners = ordinal_owners - {candidate.participant_id}
            if conflicting_owners:
                self._poison_presence_v2_conflict_locked(
                    participant_ids=(
                        candidate.participant_id,
                        *sorted(conflicting_owners),
                    ),
                    ordinals=(candidate.self_ordinal,),
                )
                raise TransferConflictError(
                    "That roster ordinal has conflicting enrolled claimants."
                )
            self._presence_v2_generation_highwater[candidate.participant_id] = (
                candidate.presence_generation
            )
            self._presence_v2_acceptance_sequence += 1
            if candidate.capture_enabled:
                self._presence_v2_last_capture_sequence[candidate.participant_id] = (
                    self._presence_v2_acceptance_sequence
                )
            target.by_participant[candidate.participant_id] = candidate
            target.by_ordinal[candidate.self_ordinal] = candidate
            if target is self._presence_v2_pending and (
                self._pending_presence_v2_complete_locked()
            ):
                self._promote_pending_presence_v2_locked(complete=True)
            return candidate

    def recording_presence_snapshot(
        self,
        *,
        ordered_roster_digest: str | None = None,
        roster_count: int | None = None,
        challenge: str | None = None,
        challenge_epoch: int | None = None,
    ) -> tuple[PresenceV2Proof, ...]:
        """Return only fresh v2 proofs, sorted by server-roster ordinal.

        Optional exact filters let a recorder bind its before/after server RPC
        observation to this snapshot. A mismatch returns no evidence rather
        than exposing an ambiguous or partially compatible mapping.
        """

        with self._lock:
            if (
                self._presence_v2_digest is None
                or self._presence_v2_roster_count is None
                or self._presence_v2_host_generations is None
                or self._presence_v2_host_roster_fingerprint is None
                or self._presence_v2_ambiguous_ordinals is None
            ):
                return ()
            now = self._presence_v2_now_locked()
            self._advance_presence_v2_epochs_locked(now)
            active = self._presence_v2_active
            if active is None or now >= active.expires_at:
                return ()
            if (
                ordered_roster_digest is not None
                and (
                    type(ordered_roster_digest) is not str
                    or ordered_roster_digest != self._presence_v2_digest
                )
            ):
                return ()
            if (
                roster_count is not None
                and (
                    type(roster_count) is not int
                    or roster_count != self._presence_v2_roster_count
                )
            ):
                return ()
            if challenge is not None:
                if type(challenge) is not str or not hmac.compare_digest(
                    challenge, active.challenge
                ):
                    return ()
            if (
                challenge_epoch is not None
                and (
                    type(challenge_epoch) is not int
                    or challenge_epoch != active.challenge_epoch
                )
            ):
                return ()
            return tuple(
                active.by_ordinal[ordinal] for ordinal in sorted(active.by_ordinal)
            )

    def presence_v2_configured(self) -> bool:
        """Return whether this runtime has an exact host roster installed."""

        with self._lock:
            return bool(
                self._presence_v2_digest is not None
                and self._presence_v2_roster_count is not None
                and self._presence_v2_host_generations is not None
                and self._presence_v2_host_roster_fingerprint is not None
                and self._presence_v2_ambiguous_ordinals is not None
            )

    def recording_presence_missing_participant_ids(
        self, *, capture_enabled_only: bool = False
    ) -> tuple[str, ...]:
        """Return enrolled owners missing from the fresh rollover snapshot.

        This is readiness metadata only; it never turns an expired proof back
        into recorder identity evidence.
        """

        if type(capture_enabled_only) is not bool:
            raise ValueError("capture_enabled_only must be a boolean.")
        with self._lock:
            if (
                self._presence_v2_digest is None
                or self._presence_v2_roster_count is None
                or self._presence_v2_host_generations is None
                or self._presence_v2_host_roster_fingerprint is None
                or self._presence_v2_ambiguous_ordinals is None
            ):
                return ()
            now = self._presence_v2_now_locked()
            self._advance_presence_v2_epochs_locked(now)
            active = self._presence_v2_active
            if active is None:
                return ()
            required = (
                active.required_capture_participants
                if capture_enabled_only
                else set(active.required_ordinals)
            )
            return tuple(sorted(required - set(active.by_participant)))

    def presence_v2_capture_cursor(self) -> int:
        """Return a monotonic in-memory cursor for accepted capture opt-ins."""

        with self._lock:
            return self._presence_v2_acceptance_sequence

    def capture_enabled_participant_ids_since(self, cursor: int) -> tuple[str, ...]:
        """Return participants that authenticated capture=true after a cursor."""

        cursor = _presence_int(cursor, "capture_cursor")
        with self._lock:
            if cursor > self._presence_v2_acceptance_sequence:
                raise ValueError("capture_cursor is newer than the registry.")
            return tuple(
                sorted(
                    participant_id
                    for participant_id, sequence in (
                        self._presence_v2_last_capture_sequence.items()
                    )
                    if sequence > cursor
                )
            )

    def current_capture_enabled_participant_ids(self) -> tuple[str, ...]:
        """Union current active/pending capture opt-ins for take obligations.

        A pending lease is deliberately excluded from recorder attribution
        until it promotes atomically.  Its enrolled-peer capture preference is
        still a conservative upload obligation, however, so a take beginning
        during rollover cannot lose an opt-in that the host already accepted.
        """

        with self._lock:
            if not self.presence_v2_configured():
                return ()
            now = self._presence_v2_now_locked()
            self._advance_presence_v2_epochs_locked(now)
            return tuple(
                sorted(
                    {
                        proof.participant_id
                        for epoch in (
                            self._presence_v2_active,
                            self._presence_v2_pending,
                        )
                        if epoch is not None and now < epoch.expires_at
                        for proof in epoch.by_participant.values()
                        if proof.capture_enabled
                    }
                )
            )

    def legacy_capture_enabled_participant_ids(self) -> tuple[str, ...]:
        """Return v1 per-enrollment capture opt-ins without trusting channels."""

        with self._lock:
            return tuple(
                sorted(
                    record["participant_id"]
                    for record in self._participants.values()
                    if bool(record.get("capture_enabled", False))
                )
            )

    def participants(self) -> tuple[ParticipantEnrollment, ...]:
        """Return the internal enrollment inventory, including derived tokens.

        This is a runtime-only API. Callers must not render or log its values;
        use only the identity/name fields when building project metadata.
        """

        with self._lock:
            records = tuple(self._participants.items())
        return tuple(
            ParticipantEnrollment(
                participant_id=record["participant_id"],
                installation_id=installation_id,
                display_name=record["display_name"],
                participant_token=self.credentials.participant_token(
                    record["participant_id"]
                ),
            )
            for installation_id, record in records
        )


class RecordingSignal(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True)
class SessionStateSnapshot:
    session_id: str
    generation: int
    signal: RecordingSignal
    take_id: str | None = None
    started_utc: str = ""
    stopped_utc: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _uuid_text(self.session_id, "session_id")
        )
        if self.take_id is not None:
            object.__setattr__(self, "take_id", _uuid_text(self.take_id, "take_id"))
        if self.generation < 0:
            raise ValueError("generation cannot be negative.")


class SessionControlState:
    """Thread-safe, idempotent host recording signal observed by joiners."""

    def __init__(self, root: str | Path, session_id: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "webjam-session-state.json"
        self.session_id = _uuid_text(session_id, "session_id")
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._snapshot = SessionStateSnapshot(
            session_id=self.session_id,
            generation=0,
            signal=RecordingSignal.IDLE,
        )
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                raise ValueError("schema")
            snapshot = SessionStateSnapshot(
                session_id=str(payload["session_id"]),
                generation=int(payload["generation"]),
                signal=RecordingSignal(payload["signal"]),
                take_id=payload.get("take_id"),
                started_utc=str(payload.get("started_utc", "")),
                stopped_utc=str(payload.get("stopped_utc", "")),
                message=str(payload.get("message", ""))[:240],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The session recording state is unreadable."
            ) from exc
        if snapshot.session_id != self.session_id:
            raise SessionTransferError(
                "The recording state belongs to another session."
            )
        self._snapshot = snapshot

    def _publish(self, **changes: Any) -> SessionStateSnapshot:
        current = self._snapshot
        values = asdict(current)
        values.update(changes)
        values["generation"] = current.generation + 1
        values["signal"] = RecordingSignal(values["signal"])
        snapshot = SessionStateSnapshot(**values)
        _write_json_secure(
            self.path,
            {"schema": 1, **asdict(snapshot), "signal": snapshot.signal.value},
        )
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> SessionStateSnapshot:
        with self._lock:
            return self._snapshot

    def begin(self, take_id: str, *, started_utc: str) -> SessionStateSnapshot:
        take_id = _uuid_text(take_id, "take_id")
        with self._lock:
            current = self._snapshot
            if current.signal is RecordingSignal.RECORDING:
                if current.take_id == take_id:
                    return current
                raise TransferConflictError("Another take is already recording.")
            if current.take_id == take_id and current.signal in {
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                # A delayed duplicate start can never resurrect a finished take.
                return current
            return self._publish(
                signal=RecordingSignal.RECORDING,
                take_id=take_id,
                started_utc=str(started_utc)[:64],
                stopped_utc="",
                message="",
            )

    def finish(
        self,
        take_id: str,
        *,
        stopped_utc: str,
        needs_attention: bool = False,
        message: str = "",
    ) -> SessionStateSnapshot:
        take_id = _uuid_text(take_id, "take_id")
        with self._lock:
            current = self._snapshot
            if current.take_id != take_id:
                raise TransferConflictError("That stop does not match the active take.")
            target = (
                RecordingSignal.NEEDS_ATTENTION
                if needs_attention
                else RecordingSignal.COMPLETE
            )
            if current.signal is target:
                return current
            if current.signal not in {
                RecordingSignal.RECORDING,
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                raise TransferConflictError("No matching recording is active.")
            return self._publish(
                signal=target,
                stopped_utc=str(stopped_utc)[:64],
                message=" ".join(str(message).split())[:240],
            )


def _gap_integer(value: object, field_name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field_name} must be a {qualifier} integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be an integer.")
    invalid = result <= 0 if positive else result < 0
    if invalid:
        qualifier = "greater than zero" if positive else "non-negative"
        raise ValueError(f"{field_name} must be {qualifier}.")
    return result


def _gap_timeline_frames(gaps: tuple["TransferGap", ...]) -> int:
    """Return the union of declared source-frame intervals.

    A multi-channel descriptor can report the same missing time range for
    several channels.  ``gap_frames`` is a legacy timeline total, not a count
    of missing samples across every channel, so overlapping per-channel facts
    must not inflate it.
    """

    intervals = sorted((gap.start_frame, gap.end_frame) for gap in gaps)
    total = 0
    start = end = -1
    for next_start, next_end in intervals:
        if start < 0:
            start, end = next_start, next_end
            continue
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
            continue
        end = max(end, next_end)
    return total + (end - start if start >= 0 else 0)


@dataclass(frozen=True)
class TransferGap:
    """A validated, per-channel missing interval in an uploaded segment.

    Transfer descriptors cross a private network boundary and are later
    retained with the take.  Keep the information limited to source-frame
    facts, a bounded safe reason, and the affected media channels; never pass
    a capture implementation's arbitrary diagnostics through unchanged.
    """

    start_frame: int
    frame_count: int
    channels: tuple[int, ...]
    reason: str

    def __post_init__(self) -> None:
        start_frame = _gap_integer(self.start_frame, "gap.start_frame")
        frame_count = _gap_integer(self.frame_count, "gap.frame_count", positive=True)
        if not isinstance(self.channels, (list, tuple)):
            raise ValueError("gap.channels must be a sequence of channel indices.")
        clean_channels: list[int] = []
        for value in self.channels:
            channel = _gap_integer(value, "gap.channels")
            clean_channels.append(channel)
        if not clean_channels:
            raise ValueError("gap.channels must identify at least one channel.")
        if len(set(clean_channels)) != len(clean_channels):
            raise ValueError("gap.channels cannot contain duplicate indices.")
        reason = redact_text(" ".join(str(self.reason or "").split()))
        # ``redact_text`` deliberately treats assignment-like text as
        # sensitive.  A descriptor reloaded from its own redacted JSON may
        # therefore present ``token=[redacted]`` to it again; collapse the
        # harmless extra closing bracket so this durable wire record is
        # canonical and replay/idempotency comparisons remain stable.
        reason = re.sub(r"(?i)\[redacted\]\]+", "[redacted]", reason)
        if not reason:
            raise ValueError("gap.reason is required.")
        object.__setattr__(self, "start_frame", start_frame)
        object.__setattr__(self, "frame_count", frame_count)
        object.__setattr__(self, "channels", tuple(clean_channels))
        object.__setattr__(self, "reason", reason[:_MAX_DESCRIPTOR_GAP_REASON])

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

    def to_mapping(self) -> dict[str, object]:
        return {
            "start_frame": self.start_frame,
            "frame_count": self.frame_count,
            "channels": list(self.channels),
            "reason": self.reason,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TransferGap":
        if not isinstance(value, Mapping):
            raise ValueError("gap must be an object.")
        raw_channels = value.get("channels", ())
        if not isinstance(raw_channels, (list, tuple)):
            raise ValueError("gap.channels must be a sequence of channel indices.")
        return cls(
            start_frame=value.get("start_frame", -1),
            frame_count=value.get("frame_count", 0),
            channels=tuple(raw_channels),
            reason=value.get("reason", ""),
        )


@dataclass(frozen=True)
class TransferDescriptor:
    session_id: str
    take_id: str
    participant_id: str
    segment_id: str
    sha256: str
    size_bytes: int
    sample_rate: int
    channels: int
    frame_count: int
    subtype: str
    started_utc: str = ""
    device_id: str = ""
    source_channel: int = 0
    gap_frames: int = 0
    capture_errors: tuple[str, ...] = ()
    gaps: tuple[TransferGap, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("session_id", "take_id", "participant_id", "segment_id"):
            object.__setattr__(
                self, field_name, _uuid_text(getattr(self, field_name), field_name)
            )
        digest = str(self.sha256).lower()
        if not _SHA256_PATTERN.fullmatch(digest):
            raise ValueError("sha256 must contain 64 lowercase hexadecimal characters.")
        object.__setattr__(self, "sha256", digest)
        if not 0 < int(self.size_bytes) <= MAX_SEGMENT_BYTES:
            raise ValueError("size_bytes is outside the supported range.")
        if not 8_000 <= int(self.sample_rate) <= 384_000:
            raise ValueError("sample_rate is outside the supported range.")
        if not 1 <= int(self.channels) <= 32:
            raise ValueError("channels is outside the supported range.")
        if int(self.frame_count) <= 0:
            raise ValueError("frame_count must be greater than zero.")
        subtype = " ".join(str(self.subtype).split()).upper()
        if not subtype or len(subtype) > 32:
            raise ValueError("subtype is invalid.")
        object.__setattr__(self, "subtype", subtype)
        object.__setattr__(self, "device_id", str(self.device_id or "").strip()[:256])
        source_channel = int(self.source_channel)
        gap_frames = int(self.gap_frames)
        if source_channel < 0:
            raise ValueError("source_channel cannot be negative.")
        if gap_frames < 0 or gap_frames > int(self.frame_count):
            raise ValueError("gap_frames is outside the segment timeline.")
        if not isinstance(self.gaps, (list, tuple)):
            raise ValueError("gaps must be a sequence of structured gap records.")
        if len(self.gaps) > _MAX_DESCRIPTOR_GAPS:
            raise ValueError("Too many gap records were supplied for one segment.")
        structured_gaps: list[TransferGap] = []
        for value in self.gaps:
            gap = (
                value
                if isinstance(value, TransferGap)
                else TransferGap.from_mapping(value)
            )
            if gap.end_frame > int(self.frame_count):
                raise ValueError(
                    "A structured gap extends beyond the segment timeline."
                )
            if any(channel >= int(self.channels) for channel in gap.channels):
                raise ValueError("A structured gap references an unavailable channel.")
            structured_gaps.append(gap)
        structured_gap_frames = _gap_timeline_frames(tuple(structured_gaps))
        if structured_gaps:
            if gap_frames not in {0, structured_gap_frames}:
                raise ValueError(
                    "gap_frames must match the total of structured gap records."
                )
            # ``gap_frames`` remains in the wire shape for older peers, but is
            # now derived from exact intervals whenever they are available.
            gap_frames = structured_gap_frames
        object.__setattr__(self, "source_channel", source_channel)
        object.__setattr__(self, "gap_frames", gap_frames)
        object.__setattr__(self, "gaps", tuple(structured_gaps))
        object.__setattr__(
            self,
            "capture_errors",
            tuple(
                redact_text(" ".join(str(item).split()))[:240]
                for item in self.capture_errors
            )[:20],
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TransferDescriptor":
        raw_gaps = value.get("gaps", ())
        if not isinstance(raw_gaps, (list, tuple)):
            raise ValueError("gaps must be a sequence of structured gap records.")
        return cls(
            session_id=str(value["session_id"]),
            take_id=str(value["take_id"]),
            participant_id=str(value["participant_id"]),
            segment_id=str(value["segment_id"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            sample_rate=int(value["sample_rate"]),
            channels=int(value["channels"]),
            frame_count=int(value["frame_count"]),
            subtype=str(value["subtype"]),
            started_utc=str(value.get("started_utc", ""))[:64],
            device_id=str(value.get("device_id", "")),
            source_channel=int(value.get("source_channel", 0)),
            gap_frames=int(value.get("gap_frames", 0)),
            capture_errors=tuple(value.get("capture_errors", ()))
            if isinstance(value.get("capture_errors", ()), (list, tuple))
            else (),
            gaps=tuple(TransferGap.from_mapping(item) for item in raw_gaps),
        )


@dataclass(frozen=True)
class TransferReceipt:
    descriptor: TransferDescriptor
    received_bytes: int
    complete: bool
    path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class TransferInventoryItem:
    descriptor: TransferDescriptor
    received_bytes: int
    complete: bool
    path: Path | None = None
    error: str = ""


class TransferStore:
    """Crash-recoverable, exactly-once publication of uploaded WAV segments."""

    def __init__(self, root: str | Path, session_id: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.session_id = _uuid_text(session_id, "session_id")
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _paths(self, descriptor: TransferDescriptor) -> tuple[Path, Path, Path]:
        if descriptor.session_id != self.session_id:
            raise TransferConflictError("The segment belongs to another session.")
        folder = self.root / descriptor.take_id / "transferred-isolated"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        basename = f"{descriptor.participant_id}-{descriptor.segment_id}"
        return (
            folder / f"{basename}.wav.part",
            folder / f"{basename}.wav",
            folder / f"{basename}.transfer.json",
        )

    def status(self, descriptor: TransferDescriptor) -> TransferReceipt:
        part, final, sidecar = self._paths(descriptor)
        with self._lock:
            final_size = final.stat().st_size if final.is_file() else 0
            received = final_size if final.is_file() else (
                part.stat().st_size if part.is_file() else 0
            )
            error = ""
            # The descriptor is immutable for the whole transfer lifetime,
            # including after the verified WAV has been published. Otherwise
            # a caller could receive a false complete receipt while presenting
            # altered capture-gap or media metadata under the same identity.
            if sidecar.is_file():
                try:
                    payload = json.loads(sidecar.read_text(encoding="utf-8"))
                    stored_descriptor = TransferDescriptor.from_mapping(payload)
                except (
                    AttributeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    raise SessionTransferError(
                        "The transfer checkpoint is unreadable."
                    ) from exc
                if stored_descriptor != descriptor:
                    raise TransferConflictError(
                        "The segment metadata changed after upload began.",
                        expected_offset=received,
                    )
                error = str(payload.get("error", ""))[:240]
            elif final.is_file():
                # A crash between publishing the WAV and writing its sidecar
                # leaves bytes but no durable descriptor to authenticate them.
                # Do not let an arbitrary retry descriptor claim that orphan
                # as complete; report zero progress so a normal retry safely
                # revalidates and republishes the source from the beginning.
                return TransferReceipt(
                    descriptor,
                    0,
                    False,
                    None,
                    "Published segment checkpoint is missing; retrying safely.",
                )
            if final.is_file():
                complete = (
                    final_size == descriptor.size_bytes
                    and _sha256_file(final) == descriptor.sha256
                )
                return TransferReceipt(
                    descriptor,
                    final_size,
                    complete,
                    final if complete else None,
                    ""
                    if complete
                    else "Published segment no longer matches its checksum.",
                )
            return TransferReceipt(descriptor, received, False, None, error)

    def append(
        self,
        descriptor: TransferDescriptor,
        *,
        offset: int,
        data: bytes,
    ) -> TransferReceipt:
        if offset < 0:
            raise ValueError("offset cannot be negative.")
        if not data or len(data) > MAX_CHUNK_BYTES:
            raise ValueError("A transfer chunk must contain 1 to 4 MiB.")
        if offset + len(data) > descriptor.size_bytes:
            raise TransferConflictError("The chunk exceeds the declared segment size.")
        part, final, sidecar = self._paths(descriptor)
        with self._lock:
            existing = self.status(descriptor)
            if existing.complete:
                return existing
            received = existing.received_bytes
            if offset < received:
                # A response can be lost after a successful write.  Accept an
                # exact byte-for-byte replay without appending it twice.
                if offset + len(data) <= received and part.is_file():
                    with part.open("rb") as handle:
                        handle.seek(offset)
                        persisted = handle.read(len(data))
                    if hmac.compare_digest(persisted, data):
                        return TransferReceipt(descriptor, received, False)
                raise TransferConflictError(
                    "The retried chunk differs from bytes already received.",
                    expected_offset=received,
                )
            if offset != received:
                raise TransferConflictError(
                    "Resume from the host's confirmed byte offset.",
                    expected_offset=received,
                )
            descriptor_payload = asdict(descriptor)
            _write_json_secure(
                sidecar,
                {
                    "schema": 1,
                    **descriptor_payload,
                    "received_bytes": received,
                    "status": "receiving",
                    "error": "",
                },
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            descriptor_fd = os.open(part, flags, 0o600)
            try:
                with os.fdopen(descriptor_fd, "ab", closefd=True) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.close(descriptor_fd)
                except OSError:
                    pass
                raise
            received += len(data)
            if received < descriptor.size_bytes:
                _write_json_secure(
                    sidecar,
                    {
                        "schema": 1,
                        **descriptor_payload,
                        "received_bytes": received,
                        "status": "receiving",
                        "error": "",
                    },
                )
                return TransferReceipt(descriptor, received, False)

            digest = _sha256_file(part)
            if digest != descriptor.sha256:
                message = "The completed upload did not match its SHA-256 checksum."
                _write_json_secure(
                    sidecar,
                    {
                        "schema": 1,
                        **descriptor_payload,
                        "received_bytes": received,
                        "status": "needs_attention",
                        "error": message,
                        "observed_sha256": digest,
                    },
                )
                raise TransferIntegrityError(message)
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(part))
                observed = (
                    int(info.samplerate),
                    int(info.channels),
                    int(info.frames),
                    str(info.subtype or "").upper(),
                )
                expected = (
                    descriptor.sample_rate,
                    descriptor.channels,
                    descriptor.frame_count,
                    descriptor.subtype,
                )
                if observed != expected:
                    raise TransferIntegrityError(
                        "The uploaded WAV facts do not match the musician's descriptor."
                    )
            except TransferIntegrityError as exc:
                _write_json_secure(
                    sidecar,
                    {
                        "schema": 1,
                        **descriptor_payload,
                        "received_bytes": received,
                        "status": "needs_attention",
                        "error": str(exc),
                    },
                )
                raise
            except (OSError, RuntimeError) as exc:
                message = "The uploaded segment is not readable PCM."
                _write_json_secure(
                    sidecar,
                    {
                        "schema": 1,
                        **descriptor_payload,
                        "received_bytes": received,
                        "status": "needs_attention",
                        "error": message,
                    },
                )
                raise TransferIntegrityError(message) from exc
            os.replace(part, final)
            os.chmod(final, 0o600)
            _write_json_secure(
                sidecar,
                {
                    "schema": 1,
                    **descriptor_payload,
                    "received_bytes": received,
                    "status": "verified",
                    "error": "",
                    "published_path": final.name,
                },
            )
            return TransferReceipt(descriptor, received, True, final)

    def inventory(self, take_id: str) -> tuple[TransferInventoryItem, ...]:
        """Return every declared upload for a take, including partial media."""

        take_id = _uuid_text(take_id, "take_id")
        folder = self.root / take_id / "transferred-isolated"
        if not folder.is_dir():
            return ()
        items: list[TransferInventoryItem] = []
        with self._lock:
            for sidecar in sorted(folder.glob("*.transfer.json")):
                try:
                    payload = json.loads(sidecar.read_text(encoding="utf-8"))
                    descriptor = TransferDescriptor.from_mapping(payload)
                    if descriptor.take_id != take_id:
                        raise ValueError("take identity")
                    receipt = self.status(descriptor)
                except (
                    OSError,
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    SessionTransferError,
                ):
                    # An unreadable checkpoint is itself recovery evidence, but
                    # cannot be represented as trusted media without an ID and
                    # checksum. Leave it on disk and log it for diagnostics.
                    continue
                items.append(
                    TransferInventoryItem(
                        descriptor=descriptor,
                        received_bytes=receipt.received_bytes,
                        complete=receipt.complete,
                        path=receipt.path,
                        error=receipt.error,
                    )
                )
        return tuple(items)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server whose in-flight handlers can be stopped safely."""

    # Keep request workers joinable. ``stop()`` closes their sockets before
    # ``server_close()`` joins them, so an incomplete peer upload cannot leave
    # a daemon worker blocked forever in ``rfile.read()``.
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._active_request_lock = threading.Lock()
        self._active_requests: set[socket.socket] = set()
        self._stopping = False
        super().__init__(*args, **kwargs)

    @property
    def stopping(self) -> bool:
        with self._active_request_lock:
            return self._stopping

    @property
    def active_handler_count(self) -> int:
        """Return the number of handlers that have not completed yet."""

        with self._active_request_lock:
            return len(self._active_requests)

    def get_request(self):
        request, client_address = super().get_request()
        # A normal SessionPeerClient has a shorter five-second request timeout.
        # This longer server-side guard only releases abandoned/raw connections;
        # it resets whenever a resumable upload continues receiving bytes.
        request.settimeout(_PEER_REQUEST_READ_TIMEOUT_S)
        return request, client_address

    def begin_shutdown(self) -> None:
        """Refuse new handlers and unblock every in-flight socket."""

        with self._active_request_lock:
            self._stopping = True
            requests = tuple(self._active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass

    def process_request(self, request, client_address) -> None:
        with self._active_request_lock:
            stopping = self._stopping
            if not stopping:
                self._active_requests.add(request)
        if stopping:
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        """Suppress expected socket errors from a server-initiated close."""

        try:
            self.finish_request(request, client_address)
        except OSError:
            # A client can also disappear mid-response. That is not a service
            # failure and must not leave a noisy handler traceback behind.
            pass
        except Exception:
            if not self.stopping:
                self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def shutdown_request(self, request) -> None:
        try:
            super().shutdown_request(request)
        finally:
            with self._active_request_lock:
                self._active_requests.discard(request)

    def server_bind(self) -> None:
        """Bind without HTTPServer's blocking reverse-DNS lookup."""
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class SessionPeerServer:
    """Small private-LAN HTTP service for enrollment, state, and WAV upload."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        registry: EnrollmentRegistry,
        control: SessionControlState,
        transfers: TransferStore,
    ) -> None:
        self.registry = registry
        self.control = control
        self.transfers = transfers
        self._httpd = _ReusableThreadingHTTPServer((host, int(port)), self._handler())
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WebJamPeer/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _json(self, status: int, payload: Mapping[str, Any]) -> None:
                if owner._httpd.stopping:
                    self.close_connection = True
                    return
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(encoded)
                except OSError:
                    self.close_connection = True

            def _error(
                self,
                status: int,
                code: str,
                message: str,
                *,
                expected_offset: int | None = None,
            ) -> None:
                payload: dict[str, Any] = {"error": code, "message": message}
                if expected_offset is not None:
                    payload["expected_offset"] = expected_offset
                self._json(status, payload)

            def _body(self, *, maximum: int) -> bytes:
                raw_length = self.headers.get("Content-Length", "")
                if not raw_length.isascii() or not raw_length.isdigit():
                    raise ValueError("A valid Content-Length is required.")
                length = int(raw_length)
                if not 0 <= length <= maximum:
                    raise ValueError("The request body is too large.")
                try:
                    body = self.rfile.read(length)
                except OSError as exc:
                    raise ValueError("The request body ended early.") from exc
                if len(body) != length:
                    raise ValueError("The request body ended early.")
                return body

            def _bearer(self) -> str:
                value = self.headers.get("Authorization", "")
                if not value.startswith("Bearer "):
                    return ""
                return value[7:]

            def _participant(self) -> str:
                participant_id = self.headers.get("X-WebJam-Participant", "")
                if not owner.registry.authenticate(participant_id, self._bearer()):
                    raise TransferAuthenticationError(
                        "Participant authentication failed."
                    )
                return participant_id

            def do_POST(self) -> None:  # noqa: N802
                route = urlsplit(self.path).path
                if route not in {"/v1/enroll", "/v1/presence", "/v2/presence"}:
                    self._error(
                        HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route."
                    )
                    return
                if route == "/v2/presence":
                    try:
                        participant_id = self._participant()
                        payload = json.loads(self._body(maximum=MAX_JSON_BYTES))
                        if not isinstance(payload, dict):
                            raise ValueError("Presence body must be a JSON object.")
                        if (
                            type(payload.get("protocol_version")) is not int
                            or payload.get("protocol_version") != 2
                        ):
                            raise ValueError("protocol_version must be 2.")
                        capture_enabled = payload["capture_enabled"]
                        if type(capture_enabled) is not bool:
                            raise ValueError("capture_enabled must be a boolean.")
                        proof = owner.registry.bind_presence_v2(
                            participant_id,
                            str(payload.get("display_name", "Musician")),
                            ordered_roster_digest=payload["ordered_roster_digest"],
                            roster_count=payload["roster_count"],
                            self_ordinal=payload["self_ordinal"],
                            process_generation=payload["process_generation"],
                            rpc_connection_generation=payload[
                                "rpc_connection_generation"
                            ],
                            audio_connection_generation=payload[
                                "audio_connection_generation"
                            ],
                            challenge=payload["challenge"],
                            challenge_epoch=payload["challenge_epoch"],
                            topology_epoch=payload["topology_epoch"],
                            presence_generation=payload["presence_generation"],
                            capture_enabled=capture_enabled,
                        )
                    except TransferAuthenticationError as exc:
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                        return
                    except TransferConflictError as exc:
                        self._error(
                            HTTPStatus.CONFLICT, "presence_conflict", str(exc)
                        )
                        return
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                        return
                    self._json(HTTPStatus.OK, asdict(proof))
                    return
                if route == "/v1/presence":
                    try:
                        participant_id = self._participant()
                        payload = json.loads(self._body(maximum=MAX_JSON_BYTES))
                        if not isinstance(payload, dict):
                            raise ValueError("Presence body must be a JSON object.")
                        binding = owner.registry.bind_presence(
                            participant_id,
                            int(payload["channel_id"]),
                            str(payload.get("display_name", "Musician")),
                            generation=int(payload["generation"]),
                            capture_enabled=bool(payload.get("capture_enabled", False)),
                        )
                    except TransferAuthenticationError as exc:
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                        return
                    except TransferConflictError as exc:
                        self._error(HTTPStatus.CONFLICT, "presence_conflict", str(exc))
                        return
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                        return
                    self._json(HTTPStatus.OK, asdict(binding))
                    return
                try:
                    payload = json.loads(self._body(maximum=MAX_JSON_BYTES))
                    if not isinstance(payload, dict):
                        raise ValueError("Enrollment body must be a JSON object.")
                    enrolled = owner.registry.enroll(
                        str(payload["installation_id"]),
                        str(payload.get("display_name", "Musician")),
                        invite_token=self._bearer(),
                    )
                except TransferAuthenticationError as exc:
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                    return
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                    return
                self._json(HTTPStatus.OK, asdict(enrolled))

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                try:
                    participant_id = self._participant()
                except TransferAuthenticationError as exc:
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                    return
                if parsed.path == "/v2/presence-challenge":
                    try:
                        challenge = owner.registry.current_presence_v2_challenge()
                    except TransferConflictError as exc:
                        self._error(
                            HTTPStatus.CONFLICT, "presence_conflict", str(exc)
                        )
                        return
                    self._json(HTTPStatus.OK, asdict(challenge))
                    return
                if parsed.path == "/v1/state":
                    snapshot = owner.control.snapshot()
                    self._json(
                        HTTPStatus.OK,
                        {**asdict(snapshot), "signal": snapshot.signal.value},
                    )
                    return
                if parsed.path == "/v1/transfer-status":
                    try:
                        query = parse_qs(parsed.query, strict_parsing=True)
                        raw = query.get("descriptor", [""])
                        if len(raw) != 1:
                            raise ValueError("One descriptor is required.")
                        descriptor = TransferDescriptor.from_mapping(json.loads(raw[0]))
                        if descriptor.participant_id != participant_id:
                            raise TransferAuthenticationError(
                                "The segment identity does not match this participant."
                            )
                        receipt = owner.transfers.status(descriptor)
                    except TransferAuthenticationError as exc:
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                        return
                    except TransferConflictError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "transfer_conflict",
                            str(exc),
                            expected_offset=exc.expected_offset,
                        )
                        return
                    except (
                        ValueError,
                        KeyError,
                        TypeError,
                        json.JSONDecodeError,
                    ) as exc:
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                        return
                    self._json(
                        HTTPStatus.OK,
                        {
                            "received_bytes": receipt.received_bytes,
                            "complete": receipt.complete,
                            "error": receipt.error,
                        },
                    )
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route.")

            def do_PUT(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/v1/segment":
                    self._error(
                        HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route."
                    )
                    return
                try:
                    participant_id = self._participant()
                    descriptor_raw = self.headers.get("X-WebJam-Descriptor", "")
                    if len(descriptor_raw.encode("utf-8")) > MAX_JSON_BYTES:
                        raise ValueError("The transfer descriptor is too large.")
                    descriptor = TransferDescriptor.from_mapping(
                        json.loads(descriptor_raw)
                    )
                    if descriptor.participant_id != participant_id:
                        raise TransferAuthenticationError(
                            "The segment identity does not match this participant."
                        )
                    offset_text = self.headers.get("X-WebJam-Offset", "")
                    if not offset_text.isascii() or not offset_text.isdigit():
                        raise ValueError("A valid transfer offset is required.")
                    body = self._body(maximum=MAX_CHUNK_BYTES)
                    receipt = owner.transfers.append(
                        descriptor, offset=int(offset_text), data=body
                    )
                except TransferAuthenticationError as exc:
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                    return
                except TransferConflictError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "offset_conflict",
                        str(exc),
                        expected_offset=exc.expected_offset,
                    )
                    return
                except TransferIntegrityError as exc:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "integrity", str(exc))
                    return
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "received_bytes": receipt.received_bytes,
                        "complete": receipt.complete,
                        "error": receipt.error,
                    },
                )

        return Handler

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="webjam-session-peer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._httpd.begin_shutdown()
        if self._thread is None:
            self._httpd.server_close()
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()
        self._thread = None

    @property
    def active_handler_count(self) -> int:
        """Return the number of outstanding peer HTTP handler workers."""

        return self._httpd.active_handler_count


class SessionPeerClient:
    """Synchronous client intended for a controller-owned worker thread."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        credentials: SessionCredentials,
        timeout_s: float = 5.0,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.credentials = credentials
        self.timeout_s = float(timeout_s)
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError("A valid peer host and port are required.")
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be a finite positive value.")

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        participant_id: str = "",
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(body)),
            "Connection": "close",
            **dict(headers or {}),
        }
        if participant_id:
            request_headers["X-WebJam-Participant"] = participant_id
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=self.timeout_s
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read(MAX_JSON_BYTES + 1)
        except OSError as exc:
            raise SessionTransferError(
                "The host's recording service is unavailable."
            ) from exc
        finally:
            connection.close()
        if len(raw) > MAX_JSON_BYTES:
            raise SessionTransferError("The host returned an oversized response.")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise SessionTransferError(
                "The host returned an unreadable response."
            ) from exc
        if not isinstance(payload, dict):
            raise SessionTransferError("The host returned an invalid response.")
        if response.status >= 400:
            if response.status == HTTPStatus.UNAUTHORIZED:
                raise TransferAuthenticationError(
                    str(payload.get("message", "Unauthorized."))
                )
            if response.status == HTTPStatus.CONFLICT:
                raise TransferConflictError(
                    str(payload.get("message", "Transfer conflict.")),
                    expected_offset=(
                        int(payload["expected_offset"])
                        if payload.get("expected_offset") is not None
                        else None
                    ),
                )
            if response.status == HTTPStatus.UNPROCESSABLE_ENTITY:
                raise TransferIntegrityError(
                    str(payload.get("message", "Integrity failure."))
                )
            raise SessionTransferError(
                str(payload.get("message", "The request failed."))
            )
        return payload

    def enroll(self, installation_id: str, display_name: str) -> ParticipantEnrollment:
        body = json.dumps(
            {
                "installation_id": installation_id,
                "display_name": display_name,
            }
        ).encode("utf-8")
        payload = self._request(
            "POST",
            "/v1/enroll",
            token=self.credentials.invite_token,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        return ParticipantEnrollment(**payload)

    def state(self, enrollment: ParticipantEnrollment) -> SessionStateSnapshot:
        payload = self._request(
            "GET",
            "/v1/state",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
        )
        return SessionStateSnapshot(
            session_id=str(payload["session_id"]),
            generation=int(payload["generation"]),
            signal=RecordingSignal(payload["signal"]),
            take_id=payload.get("take_id"),
            started_utc=str(payload.get("started_utc", "")),
            stopped_utc=str(payload.get("stopped_utc", "")),
            message=str(payload.get("message", "")),
        )

    def bind_presence(
        self,
        enrollment: ParticipantEnrollment,
        *,
        channel_id: int,
        display_name: str,
        generation: int,
        capture_enabled: bool = False,
    ) -> PresenceBinding:
        body = json.dumps(
            {
                "channel_id": int(channel_id),
                "display_name": display_name,
                "generation": int(generation),
                "capture_enabled": bool(capture_enabled),
            }
        ).encode("utf-8")
        payload = self._request(
            "POST",
            "/v1/presence",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        return PresenceBinding(**payload)

    def presence_v2_challenge(
        self, enrollment: ParticipantEnrollment
    ) -> PresenceV2Challenge:
        payload = self._request(
            "GET",
            "/v2/presence-challenge",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
        )
        try:
            return PresenceV2Challenge(**payload)
        except (TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The host returned an invalid recorder-presence challenge."
            ) from exc

    def bind_presence_v2(
        self,
        enrollment: ParticipantEnrollment,
        *,
        display_name: str,
        ordered_roster_digest: str,
        roster_count: int,
        self_ordinal: int,
        process_generation: int,
        rpc_connection_generation: int,
        audio_connection_generation: int,
        challenge: str,
        challenge_epoch: int,
        topology_epoch: int,
        presence_generation: int,
        capture_enabled: bool,
    ) -> PresenceV2Proof:
        candidate = PresenceV2Proof(
            participant_id=enrollment.participant_id,
            display_name=display_name,
            ordered_roster_digest=ordered_roster_digest,
            roster_count=roster_count,
            self_ordinal=self_ordinal,
            process_generation=process_generation,
            rpc_connection_generation=rpc_connection_generation,
            audio_connection_generation=audio_connection_generation,
            challenge=challenge,
            challenge_epoch=challenge_epoch,
            topology_epoch=topology_epoch,
            presence_generation=presence_generation,
            capture_enabled=capture_enabled,
        )
        payload = self._request(
            "POST",
            "/v2/presence",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
            body=json.dumps(asdict(candidate), separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            proof = PresenceV2Proof(**payload)
        except (TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The host returned an invalid recorder-presence proof."
            ) from exc
        if proof != candidate:
            raise SessionTransferError(
                "The host returned an inconsistent recorder-presence proof."
            )
        return proof

    def transfer_status(
        self,
        enrollment: ParticipantEnrollment,
        descriptor: TransferDescriptor,
    ) -> TransferReceipt:
        encoded = quote(json.dumps(asdict(descriptor), separators=(",", ":")))
        payload = self._request(
            "GET",
            f"/v1/transfer-status?descriptor={encoded}",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
        )
        return TransferReceipt(
            descriptor=descriptor,
            received_bytes=int(payload["received_bytes"]),
            complete=bool(payload["complete"]),
            error=str(payload.get("error", "")),
        )

    def upload_file(
        self,
        enrollment: ParticipantEnrollment,
        descriptor: TransferDescriptor,
        source: str | Path,
        *,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    ) -> TransferReceipt:
        source_path = Path(source).expanduser().resolve()
        if descriptor.participant_id != enrollment.participant_id:
            raise TransferAuthenticationError(
                "The segment does not belong to this participant enrollment."
            )
        if not 1 <= int(chunk_bytes) <= MAX_CHUNK_BYTES:
            raise ValueError("chunk_bytes must be between 1 byte and 4 MiB.")
        if (
            not source_path.is_file()
            or source_path.stat().st_size != descriptor.size_bytes
        ):
            raise TransferIntegrityError(
                "The local segment size changed before transfer."
            )
        if _sha256_file(source_path) != descriptor.sha256:
            raise TransferIntegrityError(
                "The local segment checksum changed before transfer."
            )
        receipt = self.transfer_status(enrollment, descriptor)
        if receipt.complete:
            return receipt
        offset = receipt.received_bytes
        with source_path.open("rb") as handle:
            handle.seek(offset)
            while offset < descriptor.size_bytes:
                data = handle.read(
                    min(int(chunk_bytes), descriptor.size_bytes - offset)
                )
                if not data:
                    raise TransferIntegrityError(
                        "The local segment ended during transfer."
                    )
                payload = self._request(
                    "PUT",
                    "/v1/segment",
                    token=enrollment.participant_token,
                    participant_id=enrollment.participant_id,
                    body=data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-WebJam-Offset": str(offset),
                        "X-WebJam-Descriptor": json.dumps(
                            asdict(descriptor), separators=(",", ":")
                        ),
                    },
                )
                next_offset = int(payload["received_bytes"])
                if next_offset <= offset:
                    raise SessionTransferError("The host did not advance the upload.")
                offset = next_offset
                receipt = TransferReceipt(
                    descriptor,
                    offset,
                    bool(payload["complete"]),
                    error=str(payload.get("error", "")),
                )
        return receipt

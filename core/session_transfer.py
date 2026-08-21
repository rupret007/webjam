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
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from core.creative_modes import canonical_creator_profile_key
from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.logical_sources import canonical_logical_source_id
from core.redaction import redact_text


MAX_JSON_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_SEGMENT_BYTES = 32 * 1024 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_DESCRIPTOR_GAPS = 128
_MAX_DESCRIPTOR_GAP_REASON = 120
_MAX_DECLARED_INPUTS = 32
_MAX_DECLARED_SEGMENTS = 128
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


def _optional_sha256_text(value: object, label: str) -> str:
    """Validate an optional lowercase SHA-256 without coercing private data."""

    if type(value) is str and value == "":
        return ""
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be empty or lowercase SHA-256.")
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
        if (
            not PRESENCE_V2_MIN_REMAINING_LEASE_MS
            <= lease_ms
            <= int(PRESENCE_V2_MAX_LEASE_S * 1000)
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
    local_original_track_count: int | None = None
    local_original_map_fingerprint: str = ""
    local_original_channel_counts: tuple[int, ...] = ()
    local_original_source_ids: tuple[str, ...] = ()

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
        track_count = self.local_original_track_count
        map_fingerprint = _optional_sha256_text(
            self.local_original_map_fingerprint,
            "local_original_map_fingerprint",
        )
        if track_count is None:
            if map_fingerprint:
                raise ValueError(
                    "A Local Original map fingerprint requires an exact track count."
                )
        else:
            track_count = _presence_int(track_count, "local_original_track_count")
            if track_count > _MAX_DECLARED_INPUTS:
                raise ValueError(
                    "local_original_track_count is outside the supported range."
                )
            if not map_fingerprint:
                raise ValueError(
                    "An exact Local Original track count requires a map fingerprint."
                )
            if self.capture_enabled != bool(track_count):
                raise ValueError(
                    "capture_enabled must match the exact Local Original track count."
                )
        channel_counts = tuple(self.local_original_channel_counts)
        source_ids = tuple(self.local_original_source_ids)
        if bool(channel_counts) != bool(source_ids):
            raise ValueError(
                "Local Original channel widths and source IDs must be declared together."
            )
        if channel_counts:
            if track_count is None or len(channel_counts) != track_count:
                raise ValueError(
                    "Local Original topology must describe every logical track."
                )
            if any(
                isinstance(width, bool) or width not in (1, 2)
                for width in channel_counts
            ):
                raise ValueError("Local Original tracks must be mono or stereo.")
            source_ids = tuple(
                canonical_logical_source_id(value) for value in source_ids
            )
            if len(set(source_ids)) != len(source_ids):
                raise ValueError("Local Original source IDs must be unique.")
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
        object.__setattr__(self, "local_original_track_count", track_count)
        object.__setattr__(self, "local_original_map_fingerprint", map_fingerprint)
        object.__setattr__(self, "local_original_channel_counts", channel_counts)
        object.__setattr__(self, "local_original_source_ids", source_ids)

    @property
    def local_original_topology_exact(self) -> bool:
        return self.local_original_track_count == len(
            self.local_original_channel_counts
        ) and (
            not self.local_original_track_count or bool(self.local_original_source_ids)
        )

    @property
    def recorder_eligible(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "PresenceV2Proof(private=[redacted])"


@dataclass(frozen=True, repr=False)
class LocalOriginalObligation:
    """Path-free pre-take inventory promised by one authenticated peer.

    ``track_count=None`` represents an older peer whose capture opt-in is
    readable but cannot prove an exact inventory.  Current peers always bind a
    non-negative logical-track count and a canonical, name-free map digest.
    """

    participant_id: str
    track_count: int | None
    map_fingerprint: str = ""
    presence_generation: int = 0
    capture_requested: bool = False
    channel_counts: tuple[int, ...] = ()
    logical_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        participant_id = _uuid_text(self.participant_id, "participant_id")
        generation = _presence_int(self.presence_generation, "presence_generation")
        if type(self.capture_requested) is not bool:
            raise ValueError("capture_requested must be a boolean.")
        fingerprint = _optional_sha256_text(self.map_fingerprint, "map_fingerprint")
        count = self.track_count
        if count is None:
            if fingerprint:
                raise ValueError(
                    "A Local Original map fingerprint requires an exact track count."
                )
        else:
            count = _presence_int(count, "track_count")
            if count > _MAX_DECLARED_INPUTS:
                raise ValueError("track_count is outside the supported range.")
            if not fingerprint:
                raise ValueError(
                    "An exact Local Original track count requires a map fingerprint."
                )
            if self.capture_requested != bool(count):
                raise ValueError(
                    "capture_requested must match the exact Local Original track count."
                )
        channel_counts = tuple(self.channel_counts)
        logical_source_ids = tuple(self.logical_source_ids)
        if bool(channel_counts) != bool(logical_source_ids):
            raise ValueError(
                "Local Original channel widths and source IDs must be declared together."
            )
        if channel_counts:
            if count is None or len(channel_counts) != count:
                raise ValueError(
                    "Local Original topology must describe every logical track."
                )
            if any(
                isinstance(width, bool) or width not in (1, 2)
                for width in channel_counts
            ):
                raise ValueError("Local Original tracks must be mono or stereo.")
            logical_source_ids = tuple(
                canonical_logical_source_id(value) for value in logical_source_ids
            )
            if len(set(logical_source_ids)) != len(logical_source_ids):
                raise ValueError("Local Original source IDs must be unique.")
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "track_count", count)
        object.__setattr__(self, "map_fingerprint", fingerprint)
        object.__setattr__(self, "presence_generation", generation)
        object.__setattr__(self, "channel_counts", channel_counts)
        object.__setattr__(self, "logical_source_ids", logical_source_ids)

    @property
    def exact(self) -> bool:
        return self.track_count is not None

    @property
    def exact_topology(self) -> bool:
        return self.track_count == len(self.channel_counts) and (
            not self.track_count or bool(self.logical_source_ids)
        )

    @classmethod
    def from_presence_proof(cls, proof: PresenceV2Proof) -> "LocalOriginalObligation":
        return cls(
            participant_id=proof.participant_id,
            track_count=proof.local_original_track_count,
            map_fingerprint=proof.local_original_map_fingerprint,
            presence_generation=proof.presence_generation,
            capture_requested=proof.capture_enabled,
            channel_counts=proof.local_original_channel_counts,
            logical_source_ids=proof.local_original_source_ids,
        )

    def __repr__(self) -> str:
        return "LocalOriginalObligation(private=[redacted])"


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
            (proof := pending.by_participant.get(participant_id)) is not None
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

    def _presence_v2_challenge_snapshot_locked(self, now: float) -> PresenceV2Challenge:
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
        ambiguous = _presence_ordinal_tuple(ambiguous_ordinals, roster_count=count)
        generations = (
            _presence_int(process_generation, "process_generation", positive=True),
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
        local_original_track_count: int | None = None,
        local_original_map_fingerprint: str = "",
        local_original_channel_counts: tuple[int, ...] = (),
        local_original_source_ids: tuple[str, ...] = (),
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
            local_original_track_count=local_original_track_count,
            local_original_map_fingerprint=local_original_map_fingerprint,
            local_original_channel_counts=local_original_channel_counts,
            local_original_source_ids=local_original_source_ids,
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
                raise TransferConflictError("The recorder-presence challenge is stale.")
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
                candidate.participant_id in self._presence_v2_conflicted_participants
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
                if (proof := epoch.by_participant.get(candidate.participant_id))
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
                if (proof := epoch.by_ordinal.get(candidate.self_ordinal)) is not None
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
            if ordered_roster_digest is not None and (
                type(ordered_roster_digest) is not str
                or ordered_roster_digest != self._presence_v2_digest
            ):
                return ()
            if roster_count is not None and (
                type(roster_count) is not int
                or roster_count != self._presence_v2_roster_count
            ):
                return ()
            if challenge is not None:
                if type(challenge) is not str or not hmac.compare_digest(
                    challenge, active.challenge
                ):
                    return ()
            if challenge_epoch is not None and (
                type(challenge_epoch) is not int
                or challenge_epoch != active.challenge_epoch
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

    def current_local_original_obligations(
        self,
    ) -> tuple[LocalOriginalObligation, ...]:
        """Return the newest authenticated contract across active rollover.

        Pending proofs are not recorder-attribution evidence, but they are
        authenticated monotonic Local Original promises. Including their
        newest generation prevents a take begun during lease rollover from
        freezing an already superseded input map.
        """

        with self._lock:
            if not self.presence_v2_configured():
                return ()
            now = self._presence_v2_now_locked()
            self._advance_presence_v2_epochs_locked(now)
            newest: dict[str, PresenceV2Proof] = {}
            for epoch in (self._presence_v2_active, self._presence_v2_pending):
                if epoch is None or now >= epoch.expires_at:
                    continue
                for proof in epoch.by_participant.values():
                    prior = newest.get(proof.participant_id)
                    if (
                        prior is None
                        or proof.presence_generation > prior.presence_generation
                    ):
                        newest[proof.participant_id] = proof
            return tuple(
                LocalOriginalObligation.from_presence_proof(newest[participant_id])
                for participant_id in sorted(newest)
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


_CAPTURE_ARM_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class CaptureArmSnapshot:
    """Optional pre-start instruction for current v0.26 guests.

    The ordinary recording signal deliberately remains ``IDLE`` while this
    object is present.  Older peers ignore the additive ``capture_arm`` wire
    member, while current peers may open their exact Local Original capture
    and acknowledge it before the host starts the band-server recorder.
    """

    take_id: str
    arm_generation: int
    recording_plan_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "take_id", _uuid_text(self.take_id, "take_id"))
        object.__setattr__(
            self,
            "arm_generation",
            _presence_int(self.arm_generation, "arm_generation", positive=True),
        )
        fingerprint = _optional_sha256_text(
            self.recording_plan_fingerprint,
            "recording_plan_fingerprint",
        )
        if not fingerprint:
            raise ValueError("recording_plan_fingerprint is required.")
        object.__setattr__(self, "recording_plan_fingerprint", fingerprint)

    @classmethod
    def from_mapping(cls, value: object) -> "CaptureArmSnapshot":
        if not isinstance(value, Mapping):
            raise ValueError("capture_arm must be an object.")
        if value.get("schema") != _CAPTURE_ARM_SCHEMA:
            raise ValueError("capture_arm schema is not supported.")
        return cls(
            take_id=value["take_id"],
            arm_generation=value["arm_generation"],
            recording_plan_fingerprint=value["recording_plan_fingerprint"],
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": _CAPTURE_ARM_SCHEMA,
            "take_id": self.take_id,
            "arm_generation": self.arm_generation,
            "recording_plan_fingerprint": self.recording_plan_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CaptureArmCancellationSnapshot:
    """Exact, non-sensitive proof that one pending capture arm was canceled."""

    take_id: str
    arm_generation: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "take_id", _uuid_text(self.take_id, "take_id"))
        object.__setattr__(
            self,
            "arm_generation",
            _presence_int(self.arm_generation, "arm_generation", positive=True),
        )

    @classmethod
    def from_mapping(cls, value: object) -> "CaptureArmCancellationSnapshot":
        if not isinstance(value, Mapping):
            raise ValueError("capture_arm_cancellation must be an object.")
        if value.get("schema") != _CAPTURE_ARM_SCHEMA:
            raise ValueError("capture_arm_cancellation schema is not supported.")
        return cls(
            take_id=value["take_id"],
            arm_generation=value["arm_generation"],
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": _CAPTURE_ARM_SCHEMA,
            "take_id": self.take_id,
            "arm_generation": self.arm_generation,
        }


@dataclass(frozen=True, slots=True, repr=False)
class CaptureArmAcknowledgement:
    """Authenticated proof that one guest's exact capture stream opened."""

    participant_id: str
    take_id: str
    arm_generation: int
    recording_plan_fingerprint: str
    presence_generation: int
    local_original_map_fingerprint: str
    local_original_channel_counts: tuple[int, ...]
    local_original_source_ids: tuple[str, ...]
    protocol_version: int = 2

    def __post_init__(self) -> None:
        if type(self.protocol_version) is not int or self.protocol_version != 2:
            raise ValueError("protocol_version must be 2.")
        participant_id = _uuid_text(self.participant_id, "participant_id")
        take_id = _uuid_text(self.take_id, "take_id")
        arm_generation = _presence_int(
            self.arm_generation, "arm_generation", positive=True
        )
        plan_fingerprint = _optional_sha256_text(
            self.recording_plan_fingerprint,
            "recording_plan_fingerprint",
        )
        if not plan_fingerprint:
            raise ValueError("recording_plan_fingerprint is required.")
        presence_generation = _presence_int(
            self.presence_generation,
            "presence_generation",
            positive=True,
        )
        map_fingerprint = _optional_sha256_text(
            self.local_original_map_fingerprint,
            "local_original_map_fingerprint",
        )
        if not map_fingerprint:
            raise ValueError("local_original_map_fingerprint is required.")
        channel_counts = tuple(self.local_original_channel_counts)
        source_ids = tuple(
            canonical_logical_source_id(value)
            for value in self.local_original_source_ids
        )
        if not channel_counts or len(channel_counts) != len(source_ids):
            raise ValueError(
                "A capture-arm acknowledgement requires every logical track."
            )
        if len(channel_counts) > _MAX_DECLARED_INPUTS or any(
            isinstance(width, bool) or width not in (1, 2)
            for width in channel_counts
        ):
            raise ValueError("Local Original tracks must be mono or stereo.")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("Local Original source IDs must be unique.")
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "take_id", take_id)
        object.__setattr__(self, "arm_generation", arm_generation)
        object.__setattr__(self, "recording_plan_fingerprint", plan_fingerprint)
        object.__setattr__(self, "presence_generation", presence_generation)
        object.__setattr__(self, "local_original_map_fingerprint", map_fingerprint)
        object.__setattr__(self, "local_original_channel_counts", channel_counts)
        object.__setattr__(self, "local_original_source_ids", source_ids)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
        *,
        participant_id: str,
    ) -> "CaptureArmAcknowledgement":
        return cls(
            participant_id=participant_id,
            take_id=value["take_id"],
            arm_generation=value["arm_generation"],
            recording_plan_fingerprint=value["recording_plan_fingerprint"],
            presence_generation=value["presence_generation"],
            local_original_map_fingerprint=value[
                "local_original_map_fingerprint"
            ],
            local_original_channel_counts=tuple(
                value["local_original_channel_counts"]  # type: ignore[arg-type]
            ),
            local_original_source_ids=tuple(
                value["local_original_source_ids"]  # type: ignore[arg-type]
            ),
            protocol_version=value["protocol_version"],
        )

    def to_mapping(self, *, include_participant_id: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "take_id": self.take_id,
            "arm_generation": self.arm_generation,
            "recording_plan_fingerprint": self.recording_plan_fingerprint,
            "presence_generation": self.presence_generation,
            "local_original_map_fingerprint": self.local_original_map_fingerprint,
            "local_original_channel_counts": list(
                self.local_original_channel_counts
            ),
            "local_original_source_ids": list(self.local_original_source_ids),
        }
        if include_participant_id:
            payload["participant_id"] = self.participant_id
        return payload

    def __repr__(self) -> str:
        return "CaptureArmAcknowledgement(private=[redacted])"


def _capture_arm_obligation_key(
    obligation: LocalOriginalObligation,
) -> tuple[object, ...]:
    return (
        obligation.participant_id,
        obligation.track_count,
        obligation.map_fingerprint,
        obligation.presence_generation,
        obligation.capture_requested,
        obligation.channel_counts,
        obligation.logical_source_ids,
    )


def _capture_arm_ack_key(
    acknowledgement: CaptureArmAcknowledgement,
) -> tuple[object, ...]:
    return (
        acknowledgement.participant_id,
        len(acknowledgement.local_original_channel_counts),
        acknowledgement.local_original_map_fingerprint,
        acknowledgement.presence_generation,
        True,
        acknowledgement.local_original_channel_counts,
        acknowledgement.local_original_source_ids,
    )


class SharedTrackPlaybackState(str, Enum):
    """Bounded host-owned Shared Track truth that guests may render.

    This is deliberately presentation state, not evidence that a guest can
    hear the route and not authority to operate the host's transport.
    """

    IDLE = "idle"
    READY = "ready"
    ROUTING = "routing"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"


_SHARED_TRACK_SCHEMA = 1
_MAX_SHARED_TRACK_GENERATION = (1 << 63) - 1
_MAX_SHARED_TRACK_DURATION_S = 24.0 * 60.0 * 60.0
_MAX_SHARED_TRACK_NAME_CHARS = 255
_MAX_SHARED_TRACK_NAME_BYTES = 1_024


def _shared_track_generation(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SHARED_TRACK_GENERATION:
        raise ValueError(f"{label} is outside the supported range.")
    return value


def _shared_track_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")
    return value


def _shared_track_seconds(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number.")
    parsed = float(value)
    if (
        not math.isfinite(parsed)
        or parsed < 0.0
        or parsed > _MAX_SHARED_TRACK_DURATION_S
    ):
        raise ValueError(f"{label} is outside the supported range.")
    return parsed


def _shared_track_display_name(value: object) -> str:
    if type(value) is not str:
        raise ValueError("source_display_name must be text.")
    if any(not character.isprintable() for character in value):
        raise ValueError("source_display_name contains unsupported characters.")
    if any(character in value for character in ("\0", "\r", "\n", "/", "\\")):
        raise ValueError("source_display_name must not contain a path.")
    normalized = " ".join(value.split())
    if (
        len(normalized) > _MAX_SHARED_TRACK_NAME_CHARS
        or len(normalized.encode("utf-8")) > _MAX_SHARED_TRACK_NAME_BYTES
    ):
        raise ValueError("source_display_name is too long.")
    return normalized


@dataclass(frozen=True, slots=True)
class SharedTrackSessionSnapshot:
    """Path-free Shared Track projection carried by the private peer plane.

    The object intentionally has no control capability and no audibility
    claim.  Guests may use it only to mirror host-published transport state.
    ``generation`` orders projection changes; ``playback_generation`` groups
    routing/playing/position updates that belong to one playback attempt.
    """

    generation: int = 0
    playback_generation: int = 0
    state: SharedTrackPlaybackState = SharedTrackPlaybackState.IDLE
    loaded: bool = False
    source_display_name: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    loop_start_s: float = 0.0
    loop_end_s: float | None = None
    count_in_active: bool = False
    cleanup_pending: bool = False
    needs_attention: bool = False

    def __post_init__(self) -> None:
        generation = _shared_track_generation(self.generation, "generation")
        playback_generation = _shared_track_generation(
            self.playback_generation, "playback_generation"
        )
        state = SharedTrackPlaybackState(self.state)
        loaded = _shared_track_bool(self.loaded, "loaded")
        source_name = _shared_track_display_name(self.source_display_name)
        position = _shared_track_seconds(self.position_s, "position_s")
        duration = _shared_track_seconds(self.duration_s, "duration_s")
        loop_start = _shared_track_seconds(self.loop_start_s, "loop_start_s")
        loop_end = self.loop_end_s
        if loop_end is not None:
            loop_end = _shared_track_seconds(loop_end, "loop_end_s")
            if loop_end <= loop_start:
                raise ValueError("loop_end_s must be after loop_start_s.")
            if loop_end > duration:
                raise ValueError("loop_end_s must not exceed duration_s.")
        count_in = _shared_track_bool(self.count_in_active, "count_in_active")
        cleanup = _shared_track_bool(self.cleanup_pending, "cleanup_pending")
        attention = _shared_track_bool(self.needs_attention, "needs_attention")

        if not loaded:
            if (
                source_name
                or position != 0.0
                or duration != 0.0
                or loop_start != 0.0
                or loop_end is not None
                or count_in
            ):
                raise ValueError("An unloaded Shared Track cannot expose media facts.")
            if state not in {
                SharedTrackPlaybackState.IDLE,
                SharedTrackPlaybackState.FAILED,
            }:
                raise ValueError("That Shared Track state requires loaded media.")
        else:
            if state is SharedTrackPlaybackState.IDLE:
                raise ValueError("A loaded Shared Track cannot be idle.")
            if duration <= 0.0:
                raise ValueError("A loaded Shared Track requires a duration.")
            if position > duration:
                raise ValueError("position_s must not exceed duration_s.")
            if loop_start > duration:
                raise ValueError("loop_start_s must not exceed duration_s.")
        if count_in and state not in {
            SharedTrackPlaybackState.ROUTING,
            SharedTrackPlaybackState.PLAYING,
        }:
            raise ValueError("count_in_active requires active playback.")

        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "playback_generation", playback_generation)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "loaded", loaded)
        object.__setattr__(self, "source_display_name", source_name)
        object.__setattr__(self, "position_s", position)
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(self, "loop_start_s", loop_start)
        object.__setattr__(self, "loop_end_s", loop_end)
        object.__setattr__(self, "count_in_active", count_in)
        object.__setattr__(self, "cleanup_pending", cleanup)
        object.__setattr__(self, "needs_attention", attention)

    @classmethod
    def from_mapping(cls, value: object) -> "SharedTrackSessionSnapshot":
        """Parse a peer payload, treating absence as a legacy idle host."""

        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("shared_track must be an object.")
        if value.get("schema") != _SHARED_TRACK_SCHEMA:
            raise ValueError("shared_track schema is not supported.")
        required = {
            "generation",
            "playback_generation",
            "state",
            "loaded",
            "source_display_name",
            "position_s",
            "duration_s",
            "loop_start_s",
            "loop_end_s",
            "count_in_active",
            "cleanup_pending",
            "needs_attention",
        }
        if not required.issubset(value):
            raise ValueError("shared_track is incomplete.")
        return cls(**{key: value[key] for key in required})

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": _SHARED_TRACK_SCHEMA,
            "generation": self.generation,
            "playback_generation": self.playback_generation,
            "state": self.state.value,
            "loaded": self.loaded,
            "source_display_name": self.source_display_name,
            "position_s": self.position_s,
            "duration_s": self.duration_s,
            "loop_start_s": self.loop_start_s,
            "loop_end_s": self.loop_end_s,
            "count_in_active": self.count_in_active,
            "cleanup_pending": self.cleanup_pending,
            "needs_attention": self.needs_attention,
        }


class ReferenceVideoPlaybackState(str, Enum):
    """Bounded host-owned reference video truth that followers may render.

    Studio Visit's reference video is watched locally on every computer and
    clocked by the host, so unlike Shared Track there is no route to bring up
    and no routing state.  ``stop`` returns to ``READY`` with the file still
    shared; ``IDLE`` means the host is sharing nothing at all.
    """

    IDLE = "idle"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"


_REFERENCE_VIDEO_SCHEMA = 1
_MAX_REFERENCE_VIDEO_GENERATION = (1 << 63) - 1
_MAX_REFERENCE_VIDEO_DURATION_S = 24.0 * 60.0 * 60.0
_MAX_REFERENCE_VIDEO_NAME_CHARS = 255
_MAX_REFERENCE_VIDEO_NAME_BYTES = 1_024
_REFERENCE_VIDEO_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _reference_video_generation(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_REFERENCE_VIDEO_GENERATION:
        raise ValueError(f"{label} is outside the supported range.")
    return value


def _reference_video_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")
    return value


def _reference_video_seconds(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number.")
    parsed = float(value)
    if (
        not math.isfinite(parsed)
        or parsed < 0.0
        or parsed > _MAX_REFERENCE_VIDEO_DURATION_S
    ):
        raise ValueError(f"{label} is outside the supported range.")
    return parsed


def _reference_video_display_name(value: object) -> str:
    if type(value) is not str:
        raise ValueError("source_display_name must be text.")
    if any(not character.isprintable() for character in value):
        raise ValueError("source_display_name contains unsupported characters.")
    if any(character in value for character in ("\0", "\r", "\n", "/", "\\")):
        raise ValueError("source_display_name must not contain a path.")
    normalized = " ".join(value.split())
    if (
        len(normalized) > _MAX_REFERENCE_VIDEO_NAME_CHARS
        or len(normalized.encode("utf-8")) > _MAX_REFERENCE_VIDEO_NAME_BYTES
    ):
        raise ValueError("source_display_name is too long.")
    return normalized


def _reference_video_identity_digest(value: object) -> str:
    if type(value) is not str:
        raise ValueError("identity_digest must be text.")
    if not value:
        return ""
    if _REFERENCE_VIDEO_DIGEST_RE.fullmatch(value) is None:
        raise ValueError("identity_digest is not a supported digest.")
    return value


@dataclass(frozen=True, slots=True)
class ReferenceVideoSessionSnapshot:
    """Path-free reference video projection carried by the private peer plane.

    Followers may mirror this transport state, and only this.  The object
    grants no control authority and makes no claim that any other computer is
    actually showing the same frame.

    ``identity_digest`` is a session-scoped HMAC over the host's private
    content hash, not the hash itself.  Followers hold the same session token,
    so they can prove they opened the host's exact file; the digest is
    meaningless to anyone outside the session and cannot be matched against a
    known media library.
    """

    generation: int = 0
    playback_generation: int = 0
    state: ReferenceVideoPlaybackState = ReferenceVideoPlaybackState.IDLE
    shared: bool = False
    source_display_name: str = ""
    identity_digest: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    needs_attention: bool = False

    def __post_init__(self) -> None:
        generation = _reference_video_generation(self.generation, "generation")
        playback_generation = _reference_video_generation(
            self.playback_generation, "playback_generation"
        )
        state = ReferenceVideoPlaybackState(self.state)
        shared = _reference_video_bool(self.shared, "shared")
        source_name = _reference_video_display_name(self.source_display_name)
        identity_digest = _reference_video_identity_digest(self.identity_digest)
        position = _reference_video_seconds(self.position_s, "position_s")
        duration = _reference_video_seconds(self.duration_s, "duration_s")
        attention = _reference_video_bool(self.needs_attention, "needs_attention")

        if not shared:
            if source_name or identity_digest or position != 0.0 or duration != 0.0:
                raise ValueError(
                    "An unshared reference video cannot expose media facts."
                )
            if state not in {
                ReferenceVideoPlaybackState.IDLE,
                ReferenceVideoPlaybackState.FAILED,
            }:
                raise ValueError("That reference video state requires shared media.")
        else:
            if state is ReferenceVideoPlaybackState.IDLE:
                raise ValueError("A shared reference video cannot be idle.")
            if duration <= 0.0:
                raise ValueError("A shared reference video requires a duration.")
            if position > duration:
                raise ValueError("position_s must not exceed duration_s.")
            if not identity_digest:
                # Without proven identity a follower could open any file and
                # believe it was watching the host's video.
                raise ValueError(
                    "A shared reference video requires a proven identity digest."
                )

        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "playback_generation", playback_generation)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "shared", shared)
        object.__setattr__(self, "source_display_name", source_name)
        object.__setattr__(self, "identity_digest", identity_digest)
        object.__setattr__(self, "position_s", position)
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(self, "needs_attention", attention)

    @classmethod
    def from_mapping(cls, value: object) -> "ReferenceVideoSessionSnapshot":
        """Parse a peer payload, treating absence as a host sharing nothing."""

        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("reference_video must be an object.")
        if value.get("schema") != _REFERENCE_VIDEO_SCHEMA:
            raise ValueError("reference_video schema is not supported.")
        required = {
            "generation",
            "playback_generation",
            "state",
            "shared",
            "source_display_name",
            "identity_digest",
            "position_s",
            "duration_s",
            "needs_attention",
        }
        if not required.issubset(value):
            raise ValueError("reference_video is incomplete.")
        return cls(**{key: value[key] for key in required})

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": _REFERENCE_VIDEO_SCHEMA,
            "generation": self.generation,
            "playback_generation": self.playback_generation,
            "state": self.state.value,
            "shared": self.shared,
            "source_display_name": self.source_display_name,
            "identity_digest": self.identity_digest,
            "position_s": self.position_s,
            "duration_s": self.duration_s,
            "needs_attention": self.needs_attention,
        }


@dataclass(frozen=True)
class SessionStateSnapshot:
    session_id: str
    generation: int
    signal: RecordingSignal
    take_id: str | None = None
    started_utc: str = ""
    stopped_utc: str = ""
    message: str = ""
    shared_track: SharedTrackSessionSnapshot = field(
        default_factory=SharedTrackSessionSnapshot
    )
    reference_video: ReferenceVideoSessionSnapshot = field(
        default_factory=ReferenceVideoSessionSnapshot
    )
    creator_profile_key: str = "music"
    capture_arm: CaptureArmSnapshot | None = None
    capture_arm_cancellation: CaptureArmCancellationSnapshot | None = None
    # Durable, non-sensitive marker that distinguishes a take governed by the
    # v0.26 participant-scoped handshake from a legacy session-wide start.
    # It is projected on the wire only through presence of ``capture_arm``.
    arm_handshake_required: bool = False
    arm_handshake_generation: int = 0
    # Host-local durable identity for an unresolved, memory-only arm.  This is
    # deliberately not projected on the wire; it permits only an exact
    # generation-bound cancellation after a host restart.
    arm_handshake_take_id: str | None = None
    # Local parsing metadata, not another wire member.  A v0.26 host always
    # includes the additive ``capture_arm`` key (possibly null) for a
    # handshake-governed take, while a legacy/unarmed host does not.  Current
    # guests use this bit to avoid treating session-wide RECORDING as
    # participant capture authority.
    capture_arm_supported: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _uuid_text(self.session_id, "session_id")
        )
        if self.take_id is not None:
            object.__setattr__(self, "take_id", _uuid_text(self.take_id, "take_id"))
        if self.generation < 0:
            raise ValueError("generation cannot be negative.")
        shared_track = self.shared_track
        if isinstance(shared_track, Mapping):
            shared_track = SharedTrackSessionSnapshot.from_mapping(shared_track)
        if not isinstance(shared_track, SharedTrackSessionSnapshot):
            raise ValueError("shared_track must be a SharedTrackSessionSnapshot.")
        object.__setattr__(self, "shared_track", shared_track)
        reference_video = self.reference_video
        if isinstance(reference_video, Mapping):
            reference_video = ReferenceVideoSessionSnapshot.from_mapping(
                reference_video
            )
        if not isinstance(reference_video, ReferenceVideoSessionSnapshot):
            raise ValueError(
                "reference_video must be a ReferenceVideoSessionSnapshot."
            )
        object.__setattr__(self, "reference_video", reference_video)
        capture_arm = self.capture_arm
        if isinstance(capture_arm, Mapping):
            capture_arm = CaptureArmSnapshot.from_mapping(capture_arm)
        if capture_arm is not None and not isinstance(
            capture_arm, CaptureArmSnapshot
        ):
            raise ValueError("capture_arm must be a CaptureArmSnapshot.")
        object.__setattr__(self, "capture_arm", capture_arm)
        cancellation = self.capture_arm_cancellation
        if isinstance(cancellation, Mapping):
            cancellation = CaptureArmCancellationSnapshot.from_mapping(cancellation)
        if cancellation is not None and not isinstance(
            cancellation, CaptureArmCancellationSnapshot
        ):
            raise ValueError(
                "capture_arm_cancellation must be a CaptureArmCancellationSnapshot."
            )
        object.__setattr__(self, "capture_arm_cancellation", cancellation)
        object.__setattr__(
            self,
            "arm_handshake_required",
            bool(self.arm_handshake_required),
        )
        arm_handshake_generation = _presence_int(
            self.arm_handshake_generation,
            "arm_handshake_generation",
        )
        object.__setattr__(
            self,
            "arm_handshake_generation",
            arm_handshake_generation,
        )
        arm_handshake_take_id = self.arm_handshake_take_id
        if arm_handshake_take_id is not None:
            arm_handshake_take_id = _uuid_text(
                arm_handshake_take_id,
                "arm_handshake_take_id",
            )
        object.__setattr__(
            self,
            "arm_handshake_take_id",
            arm_handshake_take_id,
        )
        object.__setattr__(
            self,
            "capture_arm_supported",
            bool(self.capture_arm_supported),
        )
        creator_profile_key = canonical_creator_profile_key(self.creator_profile_key)
        if creator_profile_key is None:
            raise ValueError("creator_profile_key is unsupported.")
        object.__setattr__(self, "creator_profile_key", creator_profile_key)


def _session_state_mapping(
    snapshot: SessionStateSnapshot,
    *,
    include_shared_track: bool,
    include_capture_arm: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": snapshot.session_id,
        "generation": snapshot.generation,
        "signal": snapshot.signal.value,
        "take_id": snapshot.take_id,
        "started_utc": snapshot.started_utc,
        "stopped_utc": snapshot.stopped_utc,
        "message": snapshot.message,
        "creator_profile_key": snapshot.creator_profile_key,
    }
    if include_shared_track:
        # Both live media projections share this flag: they are memory-only
        # guest rendering state, never part of the durable recording journal.
        payload["shared_track"] = snapshot.shared_track.to_mapping()
        payload["reference_video"] = snapshot.reference_video.to_mapping()
    if include_capture_arm and (
        snapshot.capture_arm is not None
        or snapshot.arm_handshake_required
        or snapshot.capture_arm_cancellation is not None
    ):
        payload["capture_arm"] = (
            snapshot.capture_arm.to_mapping()
            if snapshot.capture_arm is not None
            else None
        )
        if snapshot.capture_arm_cancellation is not None:
            payload["capture_arm_cancellation"] = (
                snapshot.capture_arm_cancellation.to_mapping()
            )
    return payload


class SessionControlState:
    """Thread-safe, idempotent host recording signal observed by joiners."""

    def __init__(
        self,
        root: str | Path,
        session_id: str,
        *,
        creator_profile_key: str = "music",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "webjam-session-state.json"
        self.session_id = _uuid_text(session_id, "session_id")
        self._lock = threading.RLock()
        self._capture_arm_condition = threading.Condition(self._lock)
        self._capture_arm_generation = 0
        self._capture_arm_requirements: dict[str, LocalOriginalObligation] = {}
        self._capture_arm_acknowledgements: dict[
            str, CaptureArmAcknowledgement
        ] = {}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._snapshot = SessionStateSnapshot(
            session_id=self.session_id,
            generation=0,
            signal=RecordingSignal.IDLE,
            creator_profile_key=creator_profile_key,
        )
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                raise ValueError("schema")
            arm_generation_high_water = int(
                payload.get("arm_generation_high_water", 0)
            )
            snapshot = SessionStateSnapshot(
                session_id=str(payload["session_id"]),
                generation=int(payload["generation"]),
                signal=RecordingSignal(payload["signal"]),
                take_id=payload.get("take_id"),
                started_utc=str(payload.get("started_utc", "")),
                stopped_utc=str(payload.get("stopped_utc", "")),
                message=str(payload.get("message", ""))[:240],
                creator_profile_key=payload.get("creator_profile_key", "music"),
                arm_handshake_required=bool(
                    payload.get("arm_handshake_required", False)
                ),
                arm_handshake_generation=int(
                    payload.get("arm_handshake_generation", 0)
                ),
                arm_handshake_take_id=payload.get("arm_handshake_take_id"),
                capture_arm_cancellation=(
                    CaptureArmCancellationSnapshot.from_mapping(
                        payload["capture_arm_cancellation"]
                    )
                    if payload.get("capture_arm_cancellation") is not None
                    else None
                ),
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
        self._capture_arm_generation = max(
            self._capture_arm_generation,
            snapshot.arm_handshake_generation,
            arm_generation_high_water,
        )

    def _publish(self, **changes: Any) -> SessionStateSnapshot:
        current = self._snapshot
        changes["generation"] = current.generation + 1
        if "signal" in changes:
            changes["signal"] = RecordingSignal(changes["signal"])
        snapshot = replace(current, **changes)
        _write_json_secure(
            self.path,
            {
                "schema": 1,
                **_session_state_mapping(snapshot, include_shared_track=False),
                "arm_handshake_required": snapshot.arm_handshake_required,
                "arm_handshake_generation": snapshot.arm_handshake_generation,
                "arm_handshake_take_id": snapshot.arm_handshake_take_id,
                "arm_generation_high_water": self._capture_arm_generation,
                **(
                    {
                        "capture_arm_cancellation": (
                            snapshot.capture_arm_cancellation.to_mapping()
                        )
                    }
                    if snapshot.capture_arm_cancellation is not None
                    else {}
                ),
            },
        )
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> SessionStateSnapshot:
        with self._lock:
            return self._snapshot

    def snapshot_for_participant(self, participant_id: str) -> SessionStateSnapshot:
        """Scope an arm instruction to one exact required participant.

        Recording and Shared Track state remain session-wide.  The additive
        pre-start instruction is capability-like, however: an enrolled peer
        that is not in the frozen arm requirements must never open a device.
        """

        canonical_participant = _uuid_text(participant_id, "participant_id")
        with self._lock:
            snapshot = self._snapshot
            if (
                snapshot.capture_arm is not None
                and canonical_participant not in self._capture_arm_requirements
            ):
                return replace(snapshot, capture_arm=None)
            return snapshot

    def publish_capture_arm(
        self,
        take_id: str,
        *,
        recording_plan_fingerprint: str,
        requirements: Iterable[LocalOriginalObligation],
    ) -> CaptureArmSnapshot:
        """Publish one take-scoped guest-capture request without claiming Record."""

        canonical_take = _uuid_text(take_id, "take_id")
        fingerprint = _optional_sha256_text(
            recording_plan_fingerprint,
            "recording_plan_fingerprint",
        )
        if not fingerprint:
            raise ValueError("recording_plan_fingerprint is required.")
        required: dict[str, LocalOriginalObligation] = {}
        for obligation in requirements:
            if not isinstance(obligation, LocalOriginalObligation):
                raise ValueError("capture-arm requirements must be exact obligations.")
            if not obligation.capture_requested or not obligation.track_count:
                # Exact zero-track opt-outs never participate in the handshake.
                continue
            if not obligation.exact_topology:
                raise ValueError("capture-arm requirements need exact topology.")
            if obligation.participant_id in required:
                raise ValueError("capture-arm participants must be unique.")
            required[obligation.participant_id] = obligation
        with self._capture_arm_condition:
            if (
                self._snapshot.capture_arm is None
                and self._snapshot.arm_handshake_required
                and (
                    self._snapshot.arm_handshake_take_id is not None
                    or self._snapshot.signal is RecordingSignal.IDLE
                )
            ):
                raise TransferConflictError(
                    "The prior capture-arm outcome is unresolved after restart."
                )
            if self._snapshot.signal in {
                RecordingSignal.RECORDING,
                RecordingSignal.FINALIZING,
            }:
                raise TransferConflictError(
                    "A recording must stop before another capture can be armed."
                )
            if (
                self._snapshot.take_id == canonical_take
                and self._snapshot.signal
                in {RecordingSignal.COMPLETE, RecordingSignal.NEEDS_ATTENTION}
            ):
                raise TransferConflictError(
                    "A finished take cannot be armed again."
                )
            current = self._snapshot.capture_arm
            if current is not None:
                same_arm = (
                    current.take_id == canonical_take
                    and current.recording_plan_fingerprint == fingerprint
                )
                same_requirements = tuple(
                    _capture_arm_obligation_key(item)
                    for item in self._capture_arm_requirements.values()
                ) == tuple(
                    _capture_arm_obligation_key(required[key])
                    for key in sorted(required)
                )
                if same_arm and same_requirements:
                    return current
                raise TransferConflictError("Another capture arm is already active.")
            self._capture_arm_generation += 1
            arm = CaptureArmSnapshot(
                take_id=canonical_take,
                arm_generation=self._capture_arm_generation,
                recording_plan_fingerprint=fingerprint,
            )
            self._capture_arm_requirements = {
                key: required[key] for key in sorted(required)
            }
            self._capture_arm_acknowledgements.clear()
            self._publish(
                capture_arm=arm,
                arm_handshake_required=True,
                arm_handshake_generation=arm.arm_generation,
                arm_handshake_take_id=canonical_take,
            )
            self._capture_arm_condition.notify_all()
            return arm

    def cancel_capture_arm(
        self,
        take_id: str,
        *,
        arm_generation: int | None = None,
    ) -> bool:
        """Cancel only the exact current arm; stale callers cannot cancel a newer one."""

        canonical_take = _uuid_text(take_id, "take_id")
        if arm_generation is not None:
            arm_generation = _presence_int(
                arm_generation, "arm_generation", positive=True
            )
        with self._capture_arm_condition:
            current = self._snapshot.capture_arm
            if current is None:
                snapshot = self._snapshot
                if (
                    arm_generation is None
                    or not snapshot.arm_handshake_required
                    or snapshot.arm_handshake_take_id != canonical_take
                    or snapshot.arm_handshake_generation != arm_generation
                ):
                    return False
                cancelled_generation = arm_generation
            else:
                if current.take_id != canonical_take or (
                    arm_generation is not None
                    and current.arm_generation != arm_generation
                ):
                    return False
                cancelled_generation = current.arm_generation
            self._capture_arm_requirements.clear()
            self._capture_arm_acknowledgements.clear()
            self._publish(
                capture_arm=None,
                capture_arm_cancellation=CaptureArmCancellationSnapshot(
                    take_id=canonical_take,
                    arm_generation=cancelled_generation,
                ),
                arm_handshake_required=False,
                arm_handshake_generation=cancelled_generation,
                arm_handshake_take_id=None,
            )
            self._capture_arm_condition.notify_all()
            return True

    def acknowledge_capture_arm(
        self,
        acknowledgement: CaptureArmAcknowledgement,
        *,
        current_obligation: LocalOriginalObligation,
    ) -> CaptureArmAcknowledgement:
        """Accept one authenticated ACK only while every authority still matches."""

        if not isinstance(acknowledgement, CaptureArmAcknowledgement):
            raise ValueError("capture-arm acknowledgement is malformed.")
        if not isinstance(current_obligation, LocalOriginalObligation):
            raise ValueError("current Local Original obligation is malformed.")
        with self._capture_arm_condition:
            arm = self._snapshot.capture_arm
            if arm is None:
                raise TransferConflictError("No capture arm is active.")
            if (
                acknowledgement.take_id != arm.take_id
                or acknowledgement.arm_generation != arm.arm_generation
                or acknowledgement.recording_plan_fingerprint
                != arm.recording_plan_fingerprint
            ):
                raise TransferConflictError("The capture-arm request is stale.")
            expected = self._capture_arm_requirements.get(
                acknowledgement.participant_id
            )
            if expected is None:
                raise TransferConflictError(
                    "This participant has no Local Original capture obligation."
                )
            expected_key = _capture_arm_obligation_key(expected)
            if (
                _capture_arm_ack_key(acknowledgement) != expected_key
                or _capture_arm_obligation_key(current_obligation) != expected_key
            ):
                raise TransferConflictError(
                    "The Local Original capture contract changed during arming."
                )
            prior = self._capture_arm_acknowledgements.get(
                acknowledgement.participant_id
            )
            if prior is not None:
                if prior == acknowledgement:
                    return prior
                raise TransferConflictError(
                    "That participant already acknowledged a different capture arm."
                )
            self._capture_arm_acknowledgements[
                acknowledgement.participant_id
            ] = acknowledgement
            self._capture_arm_condition.notify_all()
            return acknowledgement

    def capture_arm_state(
        self,
    ) -> tuple[
        CaptureArmSnapshot | None,
        tuple[LocalOriginalObligation, ...],
        tuple[CaptureArmAcknowledgement, ...],
    ]:
        """Return one immutable in-memory arm/requirement/ACK snapshot."""

        with self._lock:
            return (
                self._snapshot.capture_arm,
                tuple(self._capture_arm_requirements.values()),
                tuple(self._capture_arm_acknowledgements.values()),
            )

    def wait_for_capture_arm_change(
        self,
        *,
        acknowledgement_count: int,
        timeout_s: float,
    ) -> None:
        """Wait boundedly for an ACK/cancel while releasing the server-state lock."""

        if isinstance(timeout_s, bool) or not math.isfinite(float(timeout_s)):
            raise ValueError("timeout_s must be finite.")
        timeout = max(0.0, float(timeout_s))
        expected_count = _presence_int(
            acknowledgement_count, "acknowledgement_count"
        )
        with self._capture_arm_condition:
            if (
                self._snapshot.capture_arm is not None
                and len(self._capture_arm_acknowledgements) == expected_count
                and timeout > 0.0
            ):
                self._capture_arm_condition.wait(timeout)

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
            if (
                current.capture_arm is None
                and current.arm_handshake_required
                and (
                    current.arm_handshake_take_id is not None
                    or current.signal is RecordingSignal.IDLE
                )
            ):
                raise TransferConflictError(
                    "The prior capture-arm outcome is unresolved after restart."
                )
            arm = current.capture_arm
            if arm is not None:
                if arm.take_id != take_id:
                    raise TransferConflictError(
                        "A different take is still waiting for capture acknowledgement."
                    )
                if set(self._capture_arm_acknowledgements) != set(
                    self._capture_arm_requirements
                ):
                    raise TransferConflictError(
                        "Guest Local Original capture is not fully acknowledged."
                    )
            snapshot = self._publish(
                signal=RecordingSignal.RECORDING,
                take_id=take_id,
                started_utc=str(started_utc)[:64],
                stopped_utc="",
                message="",
                capture_arm=None,
                capture_arm_cancellation=None,
                arm_handshake_required=arm is not None,
                arm_handshake_generation=(arm.arm_generation if arm is not None else 0),
                arm_handshake_take_id=None,
            )
            self._capture_arm_requirements.clear()
            self._capture_arm_acknowledgements.clear()
            self._capture_arm_condition.notify_all()
            return snapshot

    def begin_finalizing(
        self,
        take_id: str,
        *,
        stopped_utc: str,
        message: str = "",
    ) -> SessionStateSnapshot:
        """Publish stop truth before slow host validation or guest uploads."""

        take_id = _uuid_text(take_id, "take_id")
        with self._lock:
            current = self._snapshot
            if current.take_id != take_id:
                raise TransferConflictError("That stop does not match the active take.")
            if current.signal in {
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                return current
            if current.signal is not RecordingSignal.RECORDING:
                raise TransferConflictError("No matching recording is active.")
            return self._publish(
                signal=RecordingSignal.FINALIZING,
                stopped_utc=str(stopped_utc)[:64],
                message=" ".join(str(message).split())[:240],
            )

    def begin_armed_finalizing(
        self,
        take_id: str,
        *,
        arm_generation: int,
        stopped_utc: str,
        message: str = "",
    ) -> SessionStateSnapshot:
        """Commit a fully ACKed arm directly to stop truth.

        This is the recovery transition for a server start that may have
        succeeded even though its acknowledgement/status confirmation was
        lost.  It never claims a confirmed start time.  Exact arm identity and
        every frozen guest ACK are required before recoverable guest media can
        be moved out of the pre-start state.
        """

        take_id = _uuid_text(take_id, "take_id")
        generation = _presence_int(
            arm_generation,
            "arm_generation",
            positive=True,
        )
        with self._capture_arm_condition:
            current = self._snapshot
            if current.take_id == take_id and current.signal in {
                RecordingSignal.FINALIZING,
                RecordingSignal.COMPLETE,
                RecordingSignal.NEEDS_ATTENTION,
            }:
                if current.arm_handshake_generation != generation:
                    raise TransferConflictError(
                        "That stop does not match the committed capture arm."
                    )
                return current
            arm = current.capture_arm
            if (
                arm is None
                or arm.take_id != take_id
                or arm.arm_generation != generation
            ):
                raise TransferConflictError(
                    "That stop does not match the active capture arm."
                )
            if set(self._capture_arm_acknowledgements) != set(
                self._capture_arm_requirements
            ):
                raise TransferConflictError(
                    "Guest Local Original capture is not fully acknowledged."
                )
            snapshot = self._publish(
                signal=RecordingSignal.FINALIZING,
                take_id=take_id,
                # The server start was not confirmed, so do not synthesize a
                # start timestamp from the prior durable session snapshot.
                started_utc="",
                stopped_utc=str(stopped_utc)[:64],
                message=" ".join(str(message).split())[:240],
                capture_arm=None,
                capture_arm_cancellation=None,
                arm_handshake_required=True,
                arm_handshake_generation=arm.arm_generation,
                arm_handshake_take_id=None,
            )
            self._capture_arm_requirements.clear()
            self._capture_arm_acknowledgements.clear()
            self._capture_arm_condition.notify_all()
            return snapshot

    def publish_shared_track(
        self,
        *,
        state: SharedTrackPlaybackState | str,
        loaded: bool,
        source_display_name: str = "",
        position_s: float = 0.0,
        duration_s: float = 0.0,
        loop_start_s: float = 0.0,
        loop_end_s: float | None = None,
        count_in_active: bool = False,
        cleanup_pending: bool = False,
        needs_attention: bool = False,
        playback_generation: int | None = None,
    ) -> SharedTrackSessionSnapshot:
        """Publish one idempotent, memory-only guest rendering projection.

        Position changes are intentionally not fsynced into the durable
        recording-state journal. A restarted host begins at the conservative
        idle projection until its Shared Track owner publishes fresh truth.
        """

        with self._lock:
            current = self._snapshot.shared_track
            parsed_state = SharedTrackPlaybackState(state)
            if playback_generation is None:
                playback_generation = current.playback_generation
                active = {
                    SharedTrackPlaybackState.ROUTING,
                    SharedTrackPlaybackState.PLAYING,
                }
                if parsed_state in active and current.state not in active:
                    playback_generation += 1
            parsed_playback_generation = _shared_track_generation(
                playback_generation, "playback_generation"
            )
            if parsed_playback_generation < current.playback_generation:
                raise TransferConflictError(
                    "A newer Shared Track playback is already published."
                )
            candidate = SharedTrackSessionSnapshot(
                generation=current.generation,
                playback_generation=parsed_playback_generation,
                state=parsed_state,
                loaded=loaded,
                source_display_name=source_display_name,
                position_s=position_s,
                duration_s=duration_s,
                loop_start_s=loop_start_s,
                loop_end_s=loop_end_s,
                count_in_active=count_in_active,
                cleanup_pending=cleanup_pending,
                needs_attention=needs_attention,
            )
            if candidate == current:
                return current
            candidate = replace(candidate, generation=current.generation + 1)
            self._snapshot = replace(self._snapshot, shared_track=candidate)
            return candidate

    def publish_reference_video(
        self,
        *,
        state: ReferenceVideoPlaybackState | str,
        shared: bool,
        source_display_name: str = "",
        identity_digest: str = "",
        position_s: float = 0.0,
        duration_s: float = 0.0,
        needs_attention: bool = False,
        playback_generation: int | None = None,
    ) -> ReferenceVideoSessionSnapshot:
        """Publish one idempotent, memory-only follower projection.

        Position is deliberately not fsynced into the durable recording-state
        journal.  A restarted host publishes nothing shared until its
        reference video owner republishes, so a follower never resumes against
        a position no one is holding.
        """

        with self._lock:
            current = self._snapshot.reference_video
            parsed_state = ReferenceVideoPlaybackState(state)
            if playback_generation is None:
                playback_generation = current.playback_generation
                if (
                    parsed_state is ReferenceVideoPlaybackState.PLAYING
                    and current.state is not ReferenceVideoPlaybackState.PLAYING
                ):
                    playback_generation += 1
            parsed_playback_generation = _reference_video_generation(
                playback_generation, "playback_generation"
            )
            if parsed_playback_generation < current.playback_generation:
                raise TransferConflictError(
                    "A newer reference video playback is already published."
                )
            candidate = ReferenceVideoSessionSnapshot(
                generation=current.generation,
                playback_generation=parsed_playback_generation,
                state=parsed_state,
                shared=shared,
                source_display_name=source_display_name,
                identity_digest=identity_digest,
                position_s=position_s,
                duration_s=duration_s,
                needs_attention=needs_attention,
            )
            if candidate == current:
                return current
            candidate = replace(candidate, generation=current.generation + 1)
            self._snapshot = replace(self._snapshot, reference_video=candidate)
            return candidate

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
    inventory_input_count: int = 0
    inventory_segment_count: int = 0
    inventory_map_fingerprint: str = ""
    logical_source_id: str = ""

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
        if int(self.channels) not in {1, 2}:
            raise ValueError("channels is outside the supported range.")
        if int(self.frame_count) <= 0:
            raise ValueError("frame_count must be greater than zero.")
        subtype = " ".join(str(self.subtype).split()).upper()
        if not subtype or len(subtype) > 32:
            raise ValueError("subtype is invalid.")
        object.__setattr__(self, "subtype", subtype)
        object.__setattr__(self, "device_id", str(self.device_id or "").strip()[:256])
        source_channel = int(self.source_channel)
        inventory_input_count = _gap_integer(
            self.inventory_input_count,
            "inventory_input_count",
        )
        inventory_segment_count = _gap_integer(
            self.inventory_segment_count,
            "inventory_segment_count",
        )
        inventory_map_fingerprint = _optional_sha256_text(
            self.inventory_map_fingerprint,
            "inventory_map_fingerprint",
        )
        logical_source_id = canonical_logical_source_id(
            self.logical_source_id, optional=True
        )
        gap_frames = int(self.gap_frames)
        if source_channel < 0:
            raise ValueError("source_channel cannot be negative.")
        if bool(inventory_input_count) != bool(inventory_segment_count):
            raise ValueError(
                "Inventory input and segment counts must both be declared."
            )
        if inventory_input_count > _MAX_DECLARED_INPUTS:
            raise ValueError("inventory_input_count is outside the supported range.")
        if inventory_segment_count > _MAX_DECLARED_SEGMENTS:
            raise ValueError("inventory_segment_count is outside the supported range.")
        if inventory_input_count > inventory_segment_count:
            raise ValueError(
                "inventory_segment_count cannot be smaller than the input count."
            )
        if inventory_input_count and source_channel >= inventory_input_count:
            raise ValueError("source_channel is outside the declared input inventory.")
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
        object.__setattr__(self, "inventory_input_count", inventory_input_count)
        object.__setattr__(self, "inventory_segment_count", inventory_segment_count)
        object.__setattr__(self, "inventory_map_fingerprint", inventory_map_fingerprint)
        object.__setattr__(self, "logical_source_id", logical_source_id)
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
            inventory_input_count=value.get("inventory_input_count", 0),
            inventory_segment_count=value.get("inventory_segment_count", 0),
            inventory_map_fingerprint=value.get("inventory_map_fingerprint", ""),
            logical_source_id=value.get("logical_source_id", ""),
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
            received = (
                final_size
                if final.is_file()
                else (part.stat().st_size if part.is_file() else 0)
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
                if route not in {
                    "/v1/enroll",
                    "/v1/presence",
                    "/v2/presence",
                    "/v2/capture-arm-ack",
                }:
                    self._error(
                        HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route."
                    )
                    return
                if route == "/v2/capture-arm-ack":
                    try:
                        participant_id = self._participant()
                        payload = json.loads(self._body(maximum=MAX_JSON_BYTES))
                        if not isinstance(payload, dict):
                            raise ValueError(
                                "Capture-arm acknowledgement must be a JSON object."
                            )
                        acknowledgement = CaptureArmAcknowledgement.from_mapping(
                            payload,
                            participant_id=participant_id,
                        )
                        current_obligation = next(
                            (
                                item
                                for item in (
                                    owner.registry.current_local_original_obligations()
                                )
                                if item.participant_id == participant_id
                            ),
                            None,
                        )
                        if current_obligation is None:
                            raise TransferConflictError(
                                "A fresh Local Original presence proof is required."
                            )
                        accepted = owner.control.acknowledge_capture_arm(
                            acknowledgement,
                            current_obligation=current_obligation,
                        )
                    except TransferAuthenticationError as exc:
                        self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
                        return
                    except TransferConflictError as exc:
                        self._error(
                            HTTPStatus.CONFLICT,
                            "capture_arm_conflict",
                            str(exc),
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
                    self._json(
                        HTTPStatus.OK,
                        accepted.to_mapping(include_participant_id=True),
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
                            local_original_track_count=payload.get(
                                "local_original_track_count"
                            ),
                            local_original_map_fingerprint=payload.get(
                                "local_original_map_fingerprint", ""
                            ),
                            local_original_channel_counts=tuple(
                                payload.get("local_original_channel_counts", ())
                            ),
                            local_original_source_ids=tuple(
                                payload.get("local_original_source_ids", ())
                            ),
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
                        self._error(HTTPStatus.CONFLICT, "presence_conflict", str(exc))
                        return
                    self._json(HTTPStatus.OK, asdict(challenge))
                    return
                if parsed.path == "/v1/state":
                    snapshot = owner.control.snapshot_for_participant(participant_id)
                    if (
                        owner.registry.presence_v2_configured()
                        and not snapshot.arm_handshake_required
                    ):
                        # An exact hosted roster means this server supports the
                        # participant-scoped ARM/ACK contract even when the
                        # authoritative plan has zero guest tracks.  Project a
                        # null optional member so current guests never infer
                        # capture permission from session-wide RECORDING;
                        # legacy servers omit the member entirely.
                        snapshot = replace(snapshot, arm_handshake_required=True)
                    self._json(
                        HTTPStatus.OK,
                        _session_state_mapping(
                            snapshot,
                            include_shared_track=True,
                            include_capture_arm=True,
                        ),
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
            shared_track=SharedTrackSessionSnapshot.from_mapping(
                payload.get("shared_track")
            ),
            reference_video=ReferenceVideoSessionSnapshot.from_mapping(
                payload.get("reference_video")
            ),
            creator_profile_key=payload.get("creator_profile_key", "music"),
            capture_arm=(
                CaptureArmSnapshot.from_mapping(payload["capture_arm"])
                if payload.get("capture_arm") is not None
                else None
            ),
            capture_arm_cancellation=(
                CaptureArmCancellationSnapshot.from_mapping(
                    payload["capture_arm_cancellation"]
                )
                if payload.get("capture_arm_cancellation") is not None
                else None
            ),
            capture_arm_supported=(
                "capture_arm" in payload or "capture_arm_cancellation" in payload
            ),
        )

    def acknowledge_capture_arm(
        self,
        enrollment: ParticipantEnrollment,
        acknowledgement: CaptureArmAcknowledgement,
    ) -> CaptureArmAcknowledgement:
        if acknowledgement.participant_id != enrollment.participant_id:
            raise ValueError(
                "The capture-arm acknowledgement belongs to another participant."
            )
        body = json.dumps(
            acknowledgement.to_mapping(include_participant_id=False)
        ).encode("utf-8")
        payload = self._request(
            "POST",
            "/v2/capture-arm-ack",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            return CaptureArmAcknowledgement.from_mapping(
                payload,
                participant_id=enrollment.participant_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionTransferError(
                "The host returned an invalid capture-arm acknowledgement."
            ) from exc

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
        local_original_track_count: int | None = None,
        local_original_map_fingerprint: str = "",
        local_original_channel_counts: tuple[int, ...] = (),
        local_original_source_ids: tuple[str, ...] = (),
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
            local_original_track_count=local_original_track_count,
            local_original_map_fingerprint=local_original_map_fingerprint,
            local_original_channel_counts=local_original_channel_counts,
            local_original_source_ids=local_original_source_ids,
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

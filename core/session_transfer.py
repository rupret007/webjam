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
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from core.redaction import redact_text


MAX_JSON_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_SEGMENT_BYTES = 32 * 1024 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def _clean_name(value: str) -> str:
    clean = " ".join(
        "".join(character if character.isprintable() else " " for character in str(value))
        .split()
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
            installation_id = _uuid_text(
                payload["installation_id"], "installation_id"
            )
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


@dataclass(frozen=True)
class SessionCredentials:
    """The private-session identity and one-link enrollment credential."""

    session_id: str
    invite_token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _uuid_text(self.session_id, "session_id"))
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class PresenceBinding:
    """Authenticated mapping from a durable participant to a live channel."""

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


class EnrollmentRegistry:
    """Durable per-session mapping from installation UUID to participant UUID."""

    def __init__(self, root: str | Path, credentials: SessionCredentials) -> None:
        self.root = Path(root).expanduser().resolve()
        self.credentials = credentials
        self.path = self.root / "webjam-participants.json"
        self._lock = threading.RLock()
        self._participants: dict[str, dict[str, str]] = {}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionTransferError("The participant registry is unreadable.") from exc
        if payload.get("schema") != 1 or payload.get("session_id") != self.credentials.session_id:
            raise SessionTransferError("The participant registry belongs to another session.")
        records = payload.get("participants", [])
        if not isinstance(records, list):
            raise SessionTransferError("The participant registry is malformed.")
        loaded: dict[str, dict[str, str]] = {}
        participant_ids: set[str] = set()
        try:
            for record in records:
                installation_id = _uuid_text(record["installation_id"], "installation_id")
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
            raise SessionTransferError("The participant registry is malformed.") from exc
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
                raise TransferAuthenticationError("Participant enrollment was not found.")
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

    def participant_id_for_channel(self, channel_id: int) -> str | None:
        channel_id = int(channel_id)
        with self._lock:
            matches = [
                record["participant_id"]
                for record in self._participants.values()
                if record.get("channel_id") == channel_id
            ]
        return matches[0] if len(matches) == 1 else None

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
        object.__setattr__(self, "session_id", _uuid_text(self.session_id, "session_id"))
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
            raise SessionTransferError("The session recording state is unreadable.") from exc
        if snapshot.session_id != self.session_id:
            raise SessionTransferError("The recording state belongs to another session.")
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
        object.__setattr__(self, "source_channel", source_channel)
        object.__setattr__(self, "gap_frames", gap_frames)
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
            if final.is_file():
                complete = (
                    final.stat().st_size == descriptor.size_bytes
                    and _sha256_file(final) == descriptor.sha256
                )
                return TransferReceipt(
                    descriptor,
                    final.stat().st_size,
                    complete,
                    final if complete else None,
                    "" if complete else "Published segment no longer matches its checksum.",
                )
            received = part.stat().st_size if part.is_file() else 0
            error = ""
            if sidecar.is_file():
                try:
                    payload = json.loads(sidecar.read_text(encoding="utf-8"))
                    if payload.get("sha256") != descriptor.sha256:
                        raise TransferConflictError(
                            "A different segment already uses this transfer identity.",
                            expected_offset=received,
                        )
                    error = str(payload.get("error", ""))[:240]
                except json.JSONDecodeError as exc:
                    raise SessionTransferError("The transfer checkpoint is unreadable.") from exc
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
    daemon_threads = True
    allow_reuse_address = True

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
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(encoded)

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
                body = self.rfile.read(length)
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
                    raise TransferAuthenticationError("Participant authentication failed.")
                return participant_id

            def do_POST(self) -> None:  # noqa: N802
                route = urlsplit(self.path).path
                if route not in {"/v1/enroll", "/v1/presence"}:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route.")
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
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
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
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route.")

            def do_PUT(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/v1/segment":
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown WebJam route.")
                    return
                try:
                    participant_id = self._participant()
                    descriptor_raw = self.headers.get("X-WebJam-Descriptor", "")
                    if len(descriptor_raw.encode("utf-8")) > MAX_JSON_BYTES:
                        raise ValueError("The transfer descriptor is too large.")
                    descriptor = TransferDescriptor.from_mapping(json.loads(descriptor_raw))
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
        if self._thread is None:
            self._httpd.server_close()
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5.0)
        self._thread = None


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
            raise SessionTransferError("The host's recording service is unavailable.") from exc
        finally:
            connection.close()
        if len(raw) > MAX_JSON_BYTES:
            raise SessionTransferError("The host returned an oversized response.")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise SessionTransferError("The host returned an unreadable response.") from exc
        if not isinstance(payload, dict):
            raise SessionTransferError("The host returned an invalid response.")
        if response.status >= 400:
            if response.status == HTTPStatus.UNAUTHORIZED:
                raise TransferAuthenticationError(str(payload.get("message", "Unauthorized.")))
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
                raise TransferIntegrityError(str(payload.get("message", "Integrity failure.")))
            raise SessionTransferError(str(payload.get("message", "The request failed.")))
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
        if not source_path.is_file() or source_path.stat().st_size != descriptor.size_bytes:
            raise TransferIntegrityError("The local segment size changed before transfer.")
        if _sha256_file(source_path) != descriptor.sha256:
            raise TransferIntegrityError("The local segment checksum changed before transfer.")
        receipt = self.transfer_status(enrollment, descriptor)
        if receipt.complete:
            return receipt
        offset = receipt.received_bytes
        with source_path.open("rb") as handle:
            handle.seek(offset)
            while offset < descriptor.size_bytes:
                data = handle.read(min(int(chunk_bytes), descriptor.size_bytes - offset))
                if not data:
                    raise TransferIntegrityError("The local segment ended during transfer.")
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

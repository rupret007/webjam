"""Strict v3 control and UDP relay protocol primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

PROTOCOL_VERSION = 3
SESSION_BYTES = 32
TOKEN_BYTES = 32
TAG_BYTES = 16
RELAY_MAGIC = b"WJR3"
_RELAY_HEADER = struct.Struct("!4sBBBB32sIQH")
RELAY_OVERHEAD = _RELAY_HEADER.size + TAG_BYTES


class ProtocolError(ValueError):
    """A bounded, public error code rather than raw parser details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Role(IntEnum):
    HOST = 0
    GUEST = 1

    @classmethod
    def from_text(cls, value: object) -> Role:
        if value == "host":
            return cls.HOST
        if value == "guest":
            return cls.GUEST
        raise ProtocolError("malformed")

    @property
    def text(self) -> str:
        return "host" if self is Role.HOST else "guest"

    @property
    def opposite(self) -> Role:
        return Role.GUEST if self is Role.HOST else Role.HOST


class DatagramKind(IntEnum):
    BIND = 1
    DATA = 2
    KEEPALIVE = 3
    DELIVERY = 4


@dataclass(frozen=True, slots=True)
class RelayFrame:
    role: Role
    kind: DatagramKind
    session_id: bytes
    generation: int
    sequence: int
    payload: bytes = b""


class ReplayWindow:
    """Fixed-memory duplicate rejection while allowing limited reordering."""

    __slots__ = ("_highest", "_mask", "_width")

    def __init__(self, width: int = 64) -> None:
        if width <= 0 or width > 256:
            raise ValueError("invalid replay window width")
        self._highest = -1
        self._mask = 0
        self._width = width

    def accept(self, sequence: int) -> bool:
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            return False
        if sequence < 0 or sequence > 0x7FFF_FFFF_FFFF_FFFF:
            return False
        if self._highest < 0:
            self._highest = sequence
            self._mask = 1
            return True
        if sequence > self._highest:
            shift = sequence - self._highest
            self._mask = 1 if shift >= self._width else (self._mask << shift) | 1
            self._mask &= (1 << self._width) - 1
            self._highest = sequence
            return True
        delta = self._highest - sequence
        if delta >= self._width:
            return False
        bit = 1 << delta
        if self._mask & bit:
            return False
        self._mask |= bit
        return True


def encode_fixed(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_fixed(value: object, size: int) -> bytes:
    if not isinstance(value, str):
        raise ProtocolError("malformed")
    expected_chars = (size * 8 + 5) // 6
    if len(value) != expected_chars or "=" in value:
        raise ProtocolError("malformed")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("malformed") from exc
    if len(raw) != size or encode_fixed(raw) != value:
        raise ProtocolError("malformed")
    return raw


def decode_opaque(value: object, maximum: int, *, minimum: int = TAG_BYTES) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise ProtocolError("malformed")
    if len(value) > ((maximum + 2) // 3) * 4:
        raise ProtocolError("frame_too_large")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("malformed") from exc
    if not minimum <= len(raw) <= maximum or encode_fixed(raw) != value:
        raise ProtocolError("malformed")
    return raw


def parse_control_line(line: bytes, maximum: int) -> dict[str, Any]:
    if not line or len(line) > maximum or not line.endswith(b"\n"):
        raise ProtocolError("frame_too_large" if len(line) > maximum else "malformed")
    try:
        value = json.loads(
            line,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: _reject_constant(),
        )
    except ProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ProtocolError("malformed") from exc
    if not isinstance(value, dict) or len(value) > 12:
        raise ProtocolError("malformed")
    if value.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_version")
    if not isinstance(value.get("op"), str):
        raise ProtocolError("malformed")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("malformed")
        result[key] = value
    return result


def _reject_constant() -> Any:
    raise ProtocolError("malformed")


def require_exact_fields(
    message: dict[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    allowed = required | (optional or set())
    if not required.issubset(message) or not set(message).issubset(allowed):
        raise ProtocolError("malformed")


def derive_relay_key(token: bytes) -> bytes:
    return hmac.digest(token, b"webjam-reference-relay-v3", "sha256")


def token_digest(token: bytes) -> bytes:
    return hashlib.sha256(b"webjam-reference-control-v3\x00" + token).digest()


def encode_relay(frame: RelayFrame, key: bytes) -> bytes:
    if len(frame.session_id) != SESSION_BYTES:
        raise ValueError("invalid session ID")
    if not 0 <= frame.generation <= 0xFFFF_FFFF:
        raise ValueError("invalid generation")
    if not 0 <= frame.sequence <= 0x7FFF_FFFF_FFFF_FFFF:
        raise ValueError("invalid sequence")
    if len(frame.payload) > 0xFFFF:
        raise ValueError("payload too large")
    header = _RELAY_HEADER.pack(
        RELAY_MAGIC,
        PROTOCOL_VERSION,
        int(frame.role),
        int(frame.kind),
        0,
        frame.session_id,
        frame.generation,
        frame.sequence,
        len(frame.payload),
    )
    body = header + frame.payload
    return body + hmac.digest(key, body, "sha256")[:TAG_BYTES]


def parse_relay(
    data: bytes, maximum: int, *, allow_delivery: bool = False
) -> tuple[RelayFrame, bytes, bytes]:
    if len(data) < RELAY_OVERHEAD or len(data) > maximum:
        raise ProtocolError("malformed_datagram")
    try:
        magic, version, role, kind, flags, session_id, generation, sequence, size = (
            _RELAY_HEADER.unpack_from(data)
        )
    except struct.error as exc:
        raise ProtocolError("malformed_datagram") from exc
    if magic != RELAY_MAGIC:
        raise ProtocolError("malformed_datagram")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_version")
    if flags != 0:
        raise ProtocolError("malformed_datagram")
    try:
        parsed_role = Role(role)
        parsed_kind = DatagramKind(kind)
    except ValueError as exc:
        raise ProtocolError("malformed_datagram") from exc
    if parsed_kind is DatagramKind.DELIVERY and not allow_delivery:
        raise ProtocolError("malformed_datagram")
    if size != len(data) - RELAY_OVERHEAD:
        raise ProtocolError("malformed_datagram")
    body = data[:-TAG_BYTES]
    payload = data[_RELAY_HEADER.size : -TAG_BYTES]
    tag = data[-TAG_BYTES:]
    return (
        RelayFrame(parsed_role, parsed_kind, session_id, generation, sequence, payload),
        body,
        tag,
    )


def verify_relay(body: bytes, tag: bytes, key: bytes) -> bool:
    expected = hmac.digest(key, body, "sha256")[:TAG_BYTES]
    return hmac.compare_digest(expected, tag)

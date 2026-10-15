"""Clipboard signaling for the ordinary direct-stun v3 path.

The v3 URL still carries no addresses. The host's sealed candidate bundle
travels as a second clipboard line so a friend can Join without a rendezvous
server or port forwarding.
"""

from __future__ import annotations

import base64

DIRECT_STUN_PROFILE_ID = "direct-stun"
HOST_SIGNAL_PREFIX = "webjam-signal:"
GUEST_REPLY_PREFIX = "webjam-reply:"
_MAX_SIGNAL_BYTES = 16 * 1024


def encode_host_signal_line(envelope: bytes) -> str:
    return _encode_signal_line(HOST_SIGNAL_PREFIX, envelope)


def encode_guest_reply_line(envelope: bytes) -> str:
    return _encode_signal_line(GUEST_REPLY_PREFIX, envelope)


def parse_host_signal_from_paste(text: object) -> bytes:
    """Return the single sealed host bundle from a complete invitation paste."""

    return _parse_signal_from_paste(text, HOST_SIGNAL_PREFIX)


def parse_guest_reply_from_paste(text: object) -> bytes:
    """Return the single sealed guest reply from a paste back to the host."""

    return _parse_signal_from_paste(text, GUEST_REPLY_PREFIX)


def _encode_signal_line(prefix: str, envelope: bytes) -> str:
    raw = bytes(envelope or b"")
    if len(raw) < 8 or len(raw) > _MAX_SIGNAL_BYTES or raw[:4] != b"WJSE":
        raise ValueError("host signal is not valid")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{prefix}{encoded}"


def _parse_signal_from_paste(text: object, prefix: str) -> bytes:
    found: list[bytes] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        while line.startswith(">"):
            line = line[1:].lstrip()
        if not line.startswith(prefix):
            continue
        encoded = line[len(prefix) :].strip()
        if not encoded.isascii() or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in encoded
        ):
            raise ValueError("host signal is not valid")
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("host signal is not valid") from exc
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if (
            canonical != encoded
            or len(decoded) < 8
            or len(decoded) > _MAX_SIGNAL_BYTES
            or decoded[:4] != b"WJSE"
        ):
            raise ValueError("host signal is not valid")
        found.append(decoded)
    if len(found) > 1:
        raise ValueError("host signal is not valid")
    return found[0] if found else b""

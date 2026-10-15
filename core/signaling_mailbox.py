"""One-invite signaling mailbox for the ordinary direct-stun reply.

Mailbox id and AEAD key are HKDF-SHA256 of the 32-byte invite capability.
The Worker sees only the hash and ciphertext. Paste remains the hidden
fallback when the mailbox is missing, expired, or unconfigured.
"""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.remote_invitation import RemoteInvitation

MAILBOX_ID_INFO = b"webjam/v3/mailbox/id"
MAILBOX_AEAD_INFO = b"webjam/v3/mailbox/aead"
OFFER_AAD = b"webjam/v3/mailbox/offer"
ANSWER_AAD = b"webjam/v3/mailbox/answer"
MAX_PLAINTEXT = 4 * 1024 - 28
TTL_SECONDS = 600
# Host wait is ~90s. A 0.4s poll burns the 10/min mailbox quota in ~4s
# and used to share that counter with TURN. Stay near 10s with backoff.
POLL_INTERVAL_SECONDS = 10.0
POLL_BACKOFF_FACTOR = 1.5
POLL_MAX_INTERVAL_SECONDS = 20.0
DEFAULT_WAIT_SECONDS = 30.0
ORIGIN_ENV = "WEBJAM_SIGNAL_MAILBOX_URL"
# Packaged default, same Worker origin as the compiled TURN URL.
# WEBJAM_SIGNAL_MAILBOX_URL remains the override.
PACKAGED_ORIGIN = "https://webjam-turn-credentials.jeffstory007.workers.dev"
_override_transport = None


def bind_transport(transport) -> None:
    """Tests bind an in-memory mailbox; production keeps this unset."""

    global _override_transport
    _override_transport = transport


def _client(transport):
    if transport is not None:
        return transport
    if _override_transport is not None:
        return _override_transport
    origin = mailbox_origin()
    if not origin:
        raise MailboxUnavailable("mailbox is not configured")
    return _HttpTransport(origin)


class MailboxUnavailable(Exception):
    """The current mailbox is missing, expired, or not configured."""


def mailbox_origin() -> str:
    """Return the https mailbox origin. Env overrides the packaged default."""

    raw = str(os.environ.get(ORIGIN_ENV, "") or PACKAGED_ORIGIN or "").strip().rstrip("/")
    return raw if _https_origin_allowed(raw) else ""


def _https_origin_allowed(origin: str) -> bool:
    if origin.startswith("https://"):
        return True
    # Local Worker harnesses only. Production always uses https.
    return origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")


def mailbox_id(capability: bytes) -> str:
    raw = _fixed(capability, 32)
    return _hkdf(raw, salt=b"", info=MAILBOX_ID_INFO, length=32).hex()


def mailbox_key(capability: bytes, session_reference: bytes) -> bytes:
    return _hkdf(
        _fixed(capability, 32),
        salt=_fixed(session_reference, 16),
        info=MAILBOX_AEAD_INFO,
        length=32,
    )


def slot_aad(kind: str, session_reference: bytes, host_spki_sha256: bytes) -> bytes:
    prefix = ANSWER_AAD if kind == "answer" else OFFER_AAD
    return (
        prefix
        + b"\0"
        + _fixed(session_reference, 16)
        + _fixed(host_spki_sha256, 32)
    )


def seal_slot(
    key: bytes,
    plaintext: bytes,
    *,
    kind: str,
    session_reference: bytes,
    host_spki_sha256: bytes,
) -> str:
    body = bytes(plaintext or b"")
    if not body or len(body) > MAX_PLAINTEXT:
        raise ValueError("mailbox payload is not valid")
    nonce = os.urandom(12)
    token = AESGCM(_fixed(key, 32)).encrypt(
        nonce,
        body,
        slot_aad(kind, session_reference, host_spki_sha256),
    )
    return _b64(nonce + token)


def open_slot(
    key: bytes,
    payload: str,
    *,
    kind: str,
    session_reference: bytes,
    host_spki_sha256: bytes,
) -> bytes:
    blob = _unb64(payload)
    if len(blob) < 28:
        raise ValueError("mailbox payload is not valid")
    try:
        return AESGCM(_fixed(key, 32)).decrypt(
            blob[:12],
            blob[12:],
            slot_aad(kind, session_reference, host_spki_sha256),
        )
    except Exception as exc:
        raise ValueError("mailbox payload is not valid") from exc


def keys_from_invitation(invitation: RemoteInvitation) -> dict[str, object]:
    capability = invitation.capability_for_enrollment()
    return {
        "mailbox_id": mailbox_id(capability),
        "key": mailbox_key(capability, invitation.session_reference),
        "session_reference": invitation.session_reference,
        "host_spki_sha256": invitation.host_spki_sha256,
    }


def post_answer(
    invitation: RemoteInvitation,
    envelope: bytes,
    *,
    transport=None,
) -> None:
    _put_slot(invitation, "answer", envelope, transport=transport)


def post_offer(
    invitation: RemoteInvitation,
    envelope: bytes,
    *,
    transport=None,
) -> None:
    _put_slot(invitation, "offer", envelope, transport=transport)


def poll_answer(
    invitation: RemoteInvitation,
    *,
    transport=None,
    timeout_seconds: float = DEFAULT_WAIT_SECONDS,
    should_stop=None,
    now=time.monotonic,
    sleeper=time.sleep,
) -> bytes:
    return _poll_slot(
        invitation,
        "answer",
        transport=transport,
        timeout_seconds=timeout_seconds,
        should_stop=should_stop,
        now=now,
        sleeper=sleeper,
    )


def ack_answer(invitation: RemoteInvitation, *, transport=None) -> None:
    keys = keys_from_invitation(invitation)
    client = _client(transport)
    status, _body = client.request("DELETE", f"/mailbox/{keys['mailbox_id']}")
    if status not in {204, 410}:
        raise MailboxUnavailable("mailbox is not configured")


def _put_slot(invitation: RemoteInvitation, kind: str, envelope: bytes, *, transport) -> None:
    keys = keys_from_invitation(invitation)
    payload = seal_slot(
        keys["key"],
        envelope,
        kind=kind,
        session_reference=keys["session_reference"],
        host_spki_sha256=keys["host_spki_sha256"],
    )
    client = _client(transport)
    status, _body = client.request(
        "POST",
        f"/mailbox/{keys['mailbox_id']}/{kind}",
        {"payload": payload},
    )
    if status == 409:
        raise MailboxUnavailable("mailbox already has that slot")
    if status in {410, 404, 429, 503}:
        raise MailboxUnavailable("mailbox is not configured")
    if status not in {200, 201}:
        raise MailboxUnavailable("mailbox is not configured")


def _poll_slot(
    invitation: RemoteInvitation,
    kind: str,
    *,
    transport,
    timeout_seconds: float,
    should_stop,
    now,
    sleeper,
) -> bytes:
    keys = keys_from_invitation(invitation)
    client = _client(transport)
    deadline = float(now()) + max(0.1, float(timeout_seconds))
    interval = POLL_INTERVAL_SECONDS
    while True:
        if should_stop is not None and should_stop():
            raise MailboxUnavailable("mailbox wait stopped")
        status, body = client.request(
            "GET",
            f"/mailbox/{keys['mailbox_id']}/{kind}",
        )
        if status == 200:
            payload = ""
            if isinstance(body, dict):
                payload = str(body.get("payload") or "")
            return open_slot(
                keys["key"],
                payload,
                kind=kind,
                session_reference=keys["session_reference"],
                host_spki_sha256=keys["host_spki_sha256"],
            )
        if status in {410, 404, 429, 503}:
            raise MailboxUnavailable("mailbox is not configured")
        if float(now()) >= deadline:
            raise MailboxUnavailable("mailbox expired")
        remaining = deadline - float(now())
        nap = min(interval, max(0.0, remaining))
        waiter = getattr(client, "wait_for_slot", None)
        if callable(waiter):
            waiter(
                f"/mailbox/{keys['mailbox_id']}/{kind}",
                nap,
                now=now,
                sleeper=sleeper,
            )
        else:
            sleeper(nap)
        interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL_SECONDS)


def _hkdf(ikm: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)
    if len(derived) != length or derived == b"\x00" * length:
        raise ValueError("mailbox key is not valid")
    return derived


def _fixed(value: bytes, size: int) -> bytes:
    raw = bytes(value or b"")
    if len(raw) != size:
        raise ValueError("mailbox key is not valid")
    return raw


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    import base64

    encoded = str(text or "").strip()
    if not encoded.isascii():
        raise ValueError("mailbox payload is not valid")
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("mailbox payload is not valid") from exc


class _HttpTransport:
    def __init__(self, origin: str) -> None:
        self._origin = origin

    def wait_for_slot(self, path: str, seconds: float, *, now=None, sleeper=None) -> None:
        """Prefer the Worker websocket wake; fall back to the spaced poll."""

        try:
            self._await_ready(path, seconds)
            return
        except (MailboxUnavailable, OSError, TimeoutError, ValueError):
            pass
        if sleeper is not None:
            sleeper(seconds)

    def _await_ready(self, path: str, seconds: float) -> None:
        try:
            from websockets.exceptions import WebSocketException
            from websockets.sync.client import connect
        except ImportError as exc:
            raise MailboxUnavailable("mailbox is not configured") from exc

        origin = self._origin
        if origin.startswith("https://"):
            ws_origin = "wss://" + origin[len("https://"):]
        elif origin.startswith("http://"):
            ws_origin = "ws://" + origin[len("http://"):]
        else:
            raise MailboxUnavailable("mailbox is not configured")
        try:
            with connect(
                f"{ws_origin}{path}",
                additional_headers={"accept": "application/json"},
                open_timeout=min(5.0, max(0.1, float(seconds))),
                close_timeout=1,
            ) as socket:
                socket.recv(timeout=max(0.1, float(seconds)))
        except (WebSocketException, TimeoutError, OSError) as exc:
            raise MailboxUnavailable("mailbox is not configured") from exc

    def request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        request = Request(
            f"{self._origin}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                return response.status, parsed
        except HTTPError as exc:
            raw = exc.read() if exc.fp is not None else b""
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                parsed = {}
            return exc.code, parsed
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            raise MailboxUnavailable("mailbox is not configured") from exc

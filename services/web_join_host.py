"""Opt-in owner of one browser guest, its sidecar and loopback Jamulus client.

No network work happens at construction. Invite material stays in memory and
in the URL fragment; the mailbox receives only derived IDs and sealed chunks.
"""

from __future__ import annotations

import base64
import json
import secrets
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.web_join_target import web_join_page
from services.transport_runtime import TransportProcess, _decode_web_join_envelope

MAILBOX_ORIGIN = "https://webjam-turn-credentials.jeffstory007.workers.dev"
MAX_ENVELOPE = 8192
CHUNK_BYTES = 2998
MAX_CHUNKS = 3
PREPARE_UNAVAILABLE = (
    "Web Join is unavailable. Use the approved app build, then check the host "
    "audio bridge and relay service."
)


class WebJoinUnavailable(RuntimeError):
    """Privacy-safe failure; credentials, invitations and addresses are omitted."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _hkdf(key: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(key)


def mailbox_chunk_id(capability: bytes, session: bytes, kind: str, index: int) -> str:
    if (
        len(capability) != 32
        or len(session) != 32
        or kind not in {"offer", "answer"}
        or type(index) is not int
        or not 0 <= index < MAX_CHUNKS
    ):
        raise ValueError("invalid mailbox context")
    root = _hkdf(capability, session, b"webjam/web-join/1")
    return _hkdf(
        root, b"", f"webjam/v3/mailbox/id/direct/{kind}/{index}".encode()
    ).hex()


def encode_chunks(envelope: bytes) -> list[str]:
    _decode_web_join_envelope(_b64(envelope))
    count = (len(envelope) + CHUNK_BYTES - 1) // CHUNK_BYTES
    return [
        _b64(bytes((i, count)) + envelope[i * CHUNK_BYTES : (i + 1) * CHUNK_BYTES])
        for i in range(count)
    ]


def decode_chunk(
    payload: object, index: int, count: int | None = None
) -> tuple[int, bytes]:
    if not isinstance(payload, str) or not 1 <= len(payload) <= 4000:
        raise WebJoinUnavailable("Web Join signaling failed.")
    try:
        raw = base64.b64decode(
            payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
        )
    except ValueError:
        raise WebJoinUnavailable("Web Join signaling failed.") from None
    if (
        len(raw) < 3
        or len(raw) > CHUNK_BYTES + 2
        or _b64(raw) != payload
        or raw[0] != index
        or not 1 <= raw[1] <= MAX_CHUNKS
        or index >= raw[1]
        or (count is not None and raw[1] != count)
        or (index < raw[1] - 1 and len(raw) != CHUNK_BYTES + 2)
    ):
        raise WebJoinUnavailable("Web Join signaling failed.")
    return raw[1], raw[2:]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebJoinMailbox:
    def __init__(self, capability: bytes, session: bytes, *, request=None, cancel=None):
        self._capability = capability
        self._session = session
        self._request = request or self._http
        self._cancel = cancel or threading.Event()
        self._next_request = 0.0

    def __repr__(self):
        return "WebJoinMailbox([redacted])"

    def _http(self, method: str, path: str, body: dict | None):
        data = (
            None if body is None else json.dumps(body, separators=(",", ":")).encode()
        )
        request = Request(
            MAILBOX_ORIGIN + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            import certifi

            # Frozen macOS cannot rely on the build machine's OpenSSL CA path.
            # Missing/invalid packaged roots fail closed; never fall back to an
            # unverified context or follow redirects to another mailbox origin.
            context = ssl.create_default_context(cadata=certifi.contents())
            with build_opener(_NoRedirect, HTTPSHandler(context=context)).open(
                request, timeout=12
            ) as response:
                content = response.read(8193)
                if len(content) > 8192:
                    raise WebJoinUnavailable("Web Join signaling failed.")
                return response.status, json.loads(content) if content else None
        except HTTPError as exc:
            exc.close()
            raise WebJoinUnavailable("Relay busy, try same Wi-Fi or the app.") from None
        except (URLError, OSError, ValueError, ImportError, TypeError):
            raise WebJoinUnavailable("Web Join signaling failed.") from None

    def _call(self, method: str, kind: str, index: int, body=None):
        if self._cancel.wait(max(0, self._next_request - time.monotonic())):
            raise WebJoinUnavailable("Web Join stopped.")
        self._next_request = time.monotonic() + 2.5
        identifier = mailbox_chunk_id(self._capability, self._session, kind, index)
        status, result = self._request(
            method, f"/mailbox/{identifier}/{kind}?client=web", body
        )
        if self._cancel.is_set():
            raise WebJoinUnavailable("Web Join stopped.")
        if status not in ({201} if method == "POST" else {200, 204}):
            raise WebJoinUnavailable("Relay busy, try same Wi-Fi or the app.")
        return status, result

    def publish_offer(self, envelope: bytes):
        chunks = encode_chunks(envelope)
        for index in list(range(1, len(chunks))) + [0]:
            self._call("POST", "offer", index, {"payload": chunks[index]})

    def poll_answer(self, *, timeout: float = 90) -> bytes:
        deadline = time.monotonic() + min(90, max(0, timeout))
        chunks: list[bytes] = []
        count = None
        while time.monotonic() < deadline:
            status, result = self._call("GET", "answer", len(chunks))
            if status == 204:
                if self._cancel.wait(5):
                    break
                continue
            if not isinstance(result, dict) or set(result) != {"payload"}:
                raise WebJoinUnavailable("Web Join signaling failed.")
            count, payload = decode_chunk(result["payload"], len(chunks), count)
            chunks.append(payload)
            if len(chunks) == count:
                envelope = b"".join(chunks)
                _decode_web_join_envelope(_b64(envelope))
                return envelope
        raise WebJoinUnavailable("The web guest did not join in time.")


def _process_factory(on_event):
    from services.native_remote_transport import (
        transport_binary_path,
        _expected_build_id,
        _integrity_options,
    )

    binary = transport_binary_path()
    return TransportProcess(
        binary,
        expected_build=_expected_build_id(),
        on_event=on_event,
        **_integrity_options(binary),
    )


class WebJoinHost:
    """One opt-in per instance. Stop permanently revokes its capability."""

    def __init__(
        self, *, process_factory=None, bridge_factory=None, mailbox_factory=None
    ):
        self._process_factory = process_factory or _process_factory
        self._bridge_factory = bridge_factory
        self._mailbox_factory = mailbox_factory or WebJoinMailbox
        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._stop = threading.Event()
        self._used = False
        self._process = None
        self._bridge = None
        self._worker = None
        self._generation = 0
        self._invite = ""
        self._state = "disabled"

    def __repr__(self):
        return f"WebJoinHost(state={self.state!r})"

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def invite(self):
        with self._lock:
            return self._invite

    def allow(self, jamulus_port: int, *, generation: int = 1) -> str:
        if (
            type(jamulus_port) is not int
            or not 1 <= jamulus_port <= 65535
            or type(generation) is not int
            or not 1 <= generation <= 2**32 - 1
        ):
            raise ValueError("invalid host session")
        with self._lock:
            if self._used or self._stop.is_set():
                raise WebJoinUnavailable("This web invitation has ended.")
            self._used = True
            self._generation = generation
            self._state = "preparing"
        try:
            # Missing/tampered package routing must fail before an audio bridge,
            # transport, capability or upstream credential can be allocated.
            join_page = web_join_page()
            if self._bridge_factory is None:
                from services.web_join_bridge import WebJoinBridge

                factory = WebJoinBridge
            else:
                factory = self._bridge_factory
            bridge = factory(jamulus_port)
            # Publish each owner before the next factory can fail. A cancellation
            # while a factory runs still leaves cleanup with a concrete handle.
            with self._lock:
                self._bridge = bridge
                if self._stop.is_set():
                    raise WebJoinUnavailable("Web Join stopped.")
            process = self._process_factory(self._on_event)
            with self._lock:
                self._process = process
                if self._stop.is_set():
                    raise WebJoinUnavailable("Web Join stopped.")
            # A codec startup must not race a close before its worker starts.
            # Process startup already has its own monotonic cancellation latch.
            with self._cleanup_lock:
                if self._stop.is_set():
                    raise WebJoinUnavailable("Web Join stopped.")
                bridge.start()
            if self._stop.is_set():
                raise WebJoinUnavailable("Web Join stopped.")
            process.start()
            if self._stop.is_set():
                raise WebJoinUnavailable("Web Join stopped.")
            capability, session = secrets.token_bytes(32), secrets.token_bytes(32)
            expiry = int(time.time()) + 600
            offer = process.open_web_join(
                session_id=session,
                capability=capability,
                bridge_port=bridge.port,
                bridge_token=bridge.token,
                generation=generation,
                expires_at_unix=expiry,
            )
            # ICE credentials can shorten the sealed offer's lifetime. Publish
            # that exact binding in the fragment, never the requested deadline.
            offer_expiry = offer.expires_at_unix
            if type(offer_expiry) is not int or not time.time() < offer_expiry <= expiry:
                raise WebJoinUnavailable("Web Join signaling failed.")
            mailbox = self._mailbox_factory(capability, session, cancel=self._stop)
            mailbox.publish_offer(offer.signal)
            with self._lock:
                if self._stop.is_set():
                    raise WebJoinUnavailable("Web Join stopped.")
                if offer_expiry <= time.time():
                    raise WebJoinUnavailable("This web invitation has ended.")
                self._invite = f"{join_page}#k={_b64(capability)}&s={session.hex()}&g={generation}&e={offer_expiry}"
                self._state = "waiting"
                self._worker = threading.Thread(
                    target=self._wait_guest,
                    args=(mailbox, process, bridge, generation),
                    name="webjam-web-join",
                    daemon=True,
                )
                self._worker.start()
                return self._invite
        except Exception:
            self.stop()
            raise WebJoinUnavailable(PREPARE_UNAVAILABLE) from None

    def _wait_guest(self, mailbox, process, bridge, generation):
        try:
            answer = mailbox.poll_answer(timeout=90)
            if self._stop.is_set():
                return
            process.apply_web_join_answer(answer, generation=generation)
            with self._lock:
                if not self._stop.is_set() and self._state != "connected":
                    self._state = "connecting"
            while not self._stop.wait(0.1):
                if bridge.closed.is_set() or not process.running:
                    break
        except Exception:
            pass
        finally:
            try:
                self.stop()
            except WebJoinUnavailable:
                # Retain the failed owner for the UI's explicit cleanup retry.
                pass

    def _on_event(self, event):
        if event.event_type == "web_join_connected":
            with self._lock:
                if not self._stop.is_set():
                    self._state = "connected"
        if event.event_type in {"web_join_closed", "error", "stopped"}:
            # Observers run on the IPC reader; teardown must not join it there.
            self._stop.set()
            with self._lock:
                self._invite = ""
                if self._state != "cleanup_pending":
                    self._state = "closed"

    def set_generation(self, generation: int):
        if generation != self._generation:
            self.stop()

    def stop(self):
        # Revoke immediately, including while another close attempt is blocked.
        self._stop.set()
        with self._lock:
            self._invite = ""
            self._state = "closing"
        with self._cleanup_lock:
            with self._lock:
                process, bridge = self._process, self._bridge
            if process is not None:
                try:
                    process.stop()
                except Exception:
                    pass  # Keep the concrete owner for the next cleanup attempt.
                else:
                    with self._lock:
                        if self._process is process:
                            self._process = None
            # Always close the audio bridge even if the sidecar failed to stop.
            if bridge is not None:
                try:
                    bridge.close()
                except Exception:
                    pass
                else:
                    with self._lock:
                        if self._bridge is bridge:
                            self._bridge = None
            with self._lock:
                pending = self._process is not None or self._bridge is not None
                self._state = "cleanup_pending" if pending else "closed"
            if pending:
                raise WebJoinUnavailable(
                    "Web Join could not stop completely. Retry stopping the web guest."
                ) from None

    kick = stop

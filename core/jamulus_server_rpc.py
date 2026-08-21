"""
JamulusServerRpc — control a Jamulus *server* over JSON-RPC.

This is the Record button's transport: synchronous request/response against
the ``jamulusserver/*`` method family (recorder control, client roster),
deliberately separate from :class:`core.jamulus_rpc_client.JamulusRpcClient`
(which is the long-lived, notification-driven session with the *local*
Jamulus client).

Reaching the band server
------------------------
Jamulus binds its JSON-RPC to loopback only — by design, and we keep it that
way. From the musician's machine, the band server's RPC is reached through
an SSH tunnel:

    ssh -N -L 22240:127.0.0.1:22222 you@your-band-server

after which WebJam connects to ``127.0.0.1:22240``. The server's RPC secret
(the ``jsonrpc.secret`` file from the server recipe) must be copied to the
local machine and pointed at via the ``server_rpc_secret_file`` setting.

Every method here is exercised against the real jamulus-headless binary in
CI (see tests/test_real_jamulus_integration.py).
"""

from __future__ import annotations

import json
import logging
import socket
import time
from pathlib import Path

from typing_extensions import Self

_logger = logging.getLogger("webjam.server_rpc")


class ServerRpcError(Exception):
    """Raised when the band-server RPC is unreachable, refuses auth, or
    answers a call with an error."""


class _CallTimeout(Exception):
    """Internal: one call exceeded its deadline (mapped to ServerRpcError)."""


def read_secret_file(path: str | Path) -> str:
    """Read a Jamulus JSON-RPC secret file; raises ServerRpcError with a
    user-actionable message on failure."""
    try:
        secret = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        raise ServerRpcError(
            "Couldn't read the server RPC secret file. Copy "
            "jsonrpc.secret from your band server and point Settings at it."
        ) from None
    if not secret:
        raise ServerRpcError("The server RPC secret file is empty.")
    return secret


class JamulusServerRpc:
    """One synchronous, authenticated JSON-RPC session with a Jamulus server.

    Usage::

        with JamulusServerRpc(port=22240, secret=secret) as rpc:
            rpc.start_recording()
            status = rpc.get_recorder_status()

    Not thread-safe; create one per worker-thread operation. All calls
    raise :class:`ServerRpcError` on transport/auth/protocol failure.
    """

    CONNECT_TIMEOUT_S = 4.0
    CALL_TIMEOUT_S = 5.0

    def __init__(self, port: int, secret: str, host: str = "127.0.0.1"):
        self._host = host
        self._port = int(port)
        self._secret = secret
        self._sock: socket.socket | None = None
        self._buf = b""          # unparsed bytes from the last recv
        self._id = 0

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> JamulusServerRpc:
        try:
            self._sock = socket.create_connection(
                (self._host, self._port), timeout=self.CONNECT_TIMEOUT_S
            )
        except OSError:
            raise ServerRpcError(
                f"Can't reach the band server's RPC at {self._host}:{self._port} "
                "For a same-Mac server, start JamulusServer.app and "
                f"verify loopback TCP {self._port}. For a remote Linux server, "
                f"verify the SSH tunnel (ssh -N -L "
                f"{self._port}:127.0.0.1:22222 you@your-server)."
            ) from None
        result = self._call_raw("jamulus/apiAuth", {"secret": self._secret})
        if result != "ok":
            self.close()
            raise ServerRpcError(
                "The band server refused the RPC secret. Make sure the local "
                "secret file matches jsonrpc.secret on the server."
            )
        return self

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        self._buf = b""
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- transport ---------------------------------------------------------
    def _readline(self, deadline: float) -> bytes:
        """Return one newline-delimited frame (bytes, no trailing \n) from
        the socket, buffering partial reads. NDJSON framed from raw recv so a
        response split across a network stall can't be mis-timed (a buffered
        reader driven by settimeout raises "cannot read from timed out
        object" on the retry, defeating the deadline)."""
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _CallTimeout()
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError as exc:
                raise _CallTimeout() from exc
            if chunk == b"":
                raise ServerRpcError("The band server closed the RPC connection.")
            self._buf += chunk

    def _call_raw(self, method: str, params: dict):
        if self._sock is None:
            raise ServerRpcError("Not connected to the band server RPC.")
        self._id += 1
        req_id = self._id
        payload = json.dumps({
            "id": req_id, "jsonrpc": "2.0", "method": method, "params": params,
        }) + "\n"
        try:
            self._sock.sendall(payload.encode("utf-8"))
            deadline = time.monotonic() + self.CALL_TIMEOUT_S
            while True:
                try:
                    raw = self._readline(deadline)
                except _CallTimeout:
                    raise ServerRpcError(
                        f"{method} timed out after {self.CALL_TIMEOUT_S}s."
                    ) from None
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("id") != req_id:
                    continue  # notification or unrelated response
                if "error" in obj:
                    # A server-supplied error may contain endpoints, paths, or
                    # other uncontrolled text. Keep the operation identifier,
                    # which is a fixed WebJam literal, and discard the payload.
                    raise ServerRpcError(f"{method} was rejected by the band server.")
                return obj.get("result")
        except OSError:
            raise ServerRpcError(
                f"RPC transport failed during {method}."
            ) from None

    # -- recorder control (the Record button) ------------------------------
    def start_recording(self) -> bool:
        """Arm the server's multitrack recorder. Returns True on acknowledge;
        confirmation that it's actually rolling arrives via the client-side
        ``recorderState`` notification (the ● REC chip)."""
        return self._call_raw("jamulusserver/startRecording", {}) == "acknowledged"

    def stop_recording(self) -> bool:
        return self._call_raw("jamulusserver/stopRecording", {}) == "acknowledged"

    def restart_recording(self) -> bool:
        """Start a NEW take (new recording directory) without stopping."""
        return self._call_raw("jamulusserver/restartRecording", {}) == "acknowledged"

    def get_recorder_status(self) -> dict:
        result = self._call_raw("jamulusserver/getRecorderStatus", {})
        if not isinstance(result, dict) or not isinstance(result.get("enabled"), bool):
            raise ServerRpcError("Unexpected getRecorderStatus payload.")
        return result

    # -- roster (future latency dashboard) ---------------------------------
    def get_clients(self) -> dict:
        result = self._call_raw("jamulusserver/getClients", {})
        if not isinstance(result, dict):
            raise ServerRpcError("Unexpected getClients payload.")
        return result

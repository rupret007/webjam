"""
JamulusRpcClient — JSON-RPC client for the Jamulus client's control API.

Speaks the **real** Jamulus JSON-RPC protocol (Jamulus 3.9+, verified against
3.12.0): newline-delimited JSON-RPC 2.0 over a raw **TCP** socket on localhost,
authenticated with ``jamulus/apiAuth`` using the secret from the file Jamulus
was launched with (``--jsonrpcsecretfile``).

    WebJam launches Jamulus with:
        --jsonrpcport <port> --jsonrpcsecretfile <DEFAULT_SECRET_PATH>
    (see services/bridge_service.py), then this client connects, authenticates,
    and translates between WebJam's mixer model and the Jamulus client API.

Methods used (client mode):
    jamulus/apiAuth                  — authenticate (required first)
    jamulusclient/getChannelInfo     — our own channel id (for is_local)
    jamulusclient/getClientList      — current participants
    jamulusclient/setFaderLevel      — per-channel fader (level 0..100)
    jamulusclient/setMuted           — mute/unmute self

Notifications consumed:
    jamulusclient/clientListReceived        — participant list changed
    jamulusclient/channelLevelListReceived  — per-channel levels (0..9)
    jamulusclient/connected / disconnected  — refresh trigger

Caller never touches the socket: it registers ``on_participants_changed`` /
``on_levels`` callbacks and calls ``set_channel_gain`` / ``set_channel_mute``.
When the RPC server isn't reachable (Jamulus not started, old version, auth not
ready) every call silently no-ops so the UDP fallback and demo data take over.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

_logger = logging.getLogger("webjam.jamulus_rpc")

# Shared secret file: bridge_service writes it and launches Jamulus with
# --jsonrpcsecretfile pointing here; this client reads it to authenticate.
DEFAULT_SECRET_PATH = Path.home() / ".webjam_jsonrpc_secret"


@dataclass
class ChannelInfo:
    channel_id: int
    name: str
    instrument: str = ""
    skill_level: str = ""
    country: str = ""
    city: str = ""
    is_local: bool = False


class JamulusRpcClient:
    """JSON-RPC 2.0 client (NDJSON over TCP) for a running Jamulus client."""

    JSONRPC_VERSION = "2.0"
    CONNECT_TIMEOUT_S = 1.0
    AUTH_TIMEOUT_S = 3.0
    RECONNECT_WAIT_S = 2.0
    LEVEL_MAX = 9      # channelLevelList values are integers 0..9
    # Jamulus ERecorderState: 1=not initialised, 2=not enabled, 3=recording.
    RECORDER_STATE_RECORDING = 3
    FADER_MAX = 100    # setFaderLevel level is 0..100
    GAIN_RANGE_IN = 127  # WebJam's internal mixer range (0..127)

    def __init__(
        self,
        port: int = 22222,
        *,
        on_participants_changed: Optional[Callable[[List[ChannelInfo]], None]] = None,
        on_levels: Optional[Callable[[Dict[int, float]], None]] = None,
        on_chat: Optional[Callable[[str], None]] = None,
        on_recorder_state: Optional[Callable[[bool, int], None]] = None,
        secret_path: Optional[Path] = None,
    ) -> None:
        self._port = port
        self._secret_path = Path(secret_path) if secret_path else DEFAULT_SECRET_PATH
        self._on_participants_changed = on_participants_changed
        self._on_levels = on_levels
        self._on_chat = on_chat
        self._on_recorder_state = on_recorder_state

        self._available = False
        self._authed = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # serialises sendall on the socket
        self._request_counter = 0
        self._inflight: Dict[int, str] = {}      # request id -> method name
        self._clients: List[ChannelInfo] = []     # last-known participant list
        self._local_channel_id: int = -1
        # Heartbeat: monotonic time of the last successful RPC interaction.
        # Stays 0.0 until the first success; reset on (re)start.
        self._last_activity_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        # A previous reader may still be winding down (mid-backoff-sleep or
        # mid-readline). Join it first so a rapid stop()->start() can't leave
        # two reader threads racing on the same socket/callbacks.
        prev = self._thread
        if prev is not None and prev.is_alive():
            prev.join(timeout=2.0)
        self._running = True
        self._last_activity_at = 0.0
        self._available = False
        self._authed = False
        self._sock = None
        with self._lock:
            self._request_counter = 0
            self._inflight.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="jamulus-rpc"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._available = False
        self._authed = False
        self._local_channel_id = -1
        with self._lock:
            self._request_counter = 0
            self._inflight.clear()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()   # unblocks a blocked readline in the reader
            except Exception:  # noqa: BLE001
                pass
        # Join the reader so it can't resume serving after stop() returns.
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def available(self) -> bool:
        return self._available

    def last_activity_age(self) -> float:
        """Seconds since the most recent successful RPC interaction.

        Returns ``float('inf')`` if nothing has succeeded since ``start()``.
        """
        if self._last_activity_at <= 0.0:
            return float("inf")
        return time.monotonic() - self._last_activity_at

    def _stamp(self) -> None:
        self._last_activity_at = time.monotonic()

    # ------------------------------------------------------------------
    # Public commands (fire-and-forget; no-op when not connected)
    # ------------------------------------------------------------------
    def set_channel_gain(self, channel_id: int, gain_0_to_127: int) -> bool:
        """Set the fader for ``channel_id``. Maps WebJam's 0..127 to 0..100."""
        gain = max(0, min(self.GAIN_RANGE_IN, int(gain_0_to_127)))
        level = round(gain / self.GAIN_RANGE_IN * self.FADER_MAX)
        return self._send("jamulusclient/setFaderLevel", {
            "channelIndex": channel_id,
            "level": level,
        }) is not None

    def set_channel_mute(self, channel_id: int, muted: bool) -> bool:
        # The client API has no per-channel mute; muting a channel in your own
        # mix = setting its fader to 0.  Unmute restores to unity (100/127).
        return self.set_channel_gain(channel_id, 0 if muted else 100)

    def set_self_muted(self, muted: bool) -> bool:
        """Mute/unmute the local user globally (jamulusclient/setMuted)."""
        return self._send("jamulusclient/setMuted", {"muted": bool(muted)}) is not None

    def send_chat_text(self, text: str) -> bool:
        """Send a chat message to the band (jamulusclient/sendChatText)."""
        if not text:
            return False
        return self._send(
            "jamulusclient/sendChatText", {"chatText": str(text)}
        ) is not None

    def set_name(self, name: str) -> bool:
        """Set the local musician's display name (jamulusclient/setName)."""
        if not name:
            return False
        return self._send("jamulusclient/setName", {"name": str(name)}) is not None

    def get_channel_clients(self) -> Optional[List[ChannelInfo]]:
        """Return the last-known participant list, or None if not yet received."""
        if not self._available:
            return None
        return list(self._clients)

    def _get_local_channel_id(self) -> int:
        """Return the cached local channel id (-1 if unknown)."""
        return self._local_channel_id

    # ------------------------------------------------------------------
    # Connection / reader loop
    # ------------------------------------------------------------------
    def _read_secret(self) -> Optional[str]:
        try:
            secret = self._secret_path.read_text(encoding="utf-8").strip()
            return secret or None
        except Exception:  # noqa: BLE001
            return None

    def _run_loop(self) -> None:
        wait = self.RECONNECT_WAIT_S
        while self._running:
            try:
                self._serve_once()
                wait = self.RECONNECT_WAIT_S
            except Exception as exc:  # noqa: BLE001
                _logger.debug("RPC connection ended: %s", exc)
            self._available = False
            self._authed = False
            if self._running:
                time.sleep(wait)
                wait = min(wait * 1.5, 30.0)

    def _serve_once(self) -> None:
        secret = self._read_secret()
        if not secret:
            # Jamulus not launched yet (no secret file) — back off and retry.
            raise ConnectionError("jsonrpc secret not available yet")

        sock = socket.create_connection(
            ("127.0.0.1", self._port), timeout=self.CONNECT_TIMEOUT_S
        )
        self._sock = sock
        reader = sock.makefile("r", encoding="utf-8", newline="\n")

        try:
            # 1. Authenticate (must be the first message).
            auth_id = self._send("jamulus/apiAuth", {"secret": secret})
            if not self._await_auth(reader, auth_id):
                raise ConnectionError("apiAuth failed or refused")
            self._authed = True
            self._available = True
            self._stamp()

            # 2. Prime local channel id + participant list.
            self._send("jamulusclient/getChannelInfo", {})
            self._send("jamulusclient/getClientList", {})

            # 3. Stream notifications + responses until disconnected/stopped.
            sock.settimeout(1.0)
            while self._running:
                try:
                    line = reader.readline()
                except socket.timeout:
                    continue
                except OSError as exc:
                    # A buffered reader driven by settimeout raises "cannot
                    # read from timed out object" on the read that follows a
                    # timeout mid-frame — that's still a timeout, not a
                    # disconnect. Treat it as such so a slow/fragmented frame
                    # doesn't tear down and reconnect the session.
                    if "timed out" in str(exc).lower():
                        continue
                    raise
                if line == "":
                    raise ConnectionError("server closed connection")
                self._dispatch_line(line)
        finally:
            try:
                reader.close()
            except Exception:  # noqa: BLE001
                pass
            if self._sock is sock:
                self._sock = None
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    def _await_auth(self, reader, auth_id: Optional[int]) -> bool:
        """Read lines until the apiAuth response arrives; dispatch any
        notifications seen in the meantime."""
        deadline = time.monotonic() + self.AUTH_TIMEOUT_S
        while self._running and time.monotonic() < deadline:
            line = reader.readline()
            if line == "":
                return False
            obj = self._parse(line)
            if obj is None:
                continue
            if obj.get("id") == auth_id and ("result" in obj or "error" in obj):
                self._inflight.pop(auth_id, None)
                return obj.get("result") == "ok"
            # Notifications can arrive before the auth ack — handle them.
            self._dispatch_obj(obj)
        return False

    # ------------------------------------------------------------------
    # Sending / dispatch
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        with self._lock:
            self._request_counter += 1
            return self._request_counter

    def _send(self, method: str, params: dict) -> Optional[int]:
        sock = self._sock
        if sock is None:
            return None
        req_id = self._next_id()
        with self._lock:
            self._inflight[req_id] = method
        payload = json.dumps({
            "jsonrpc": self.JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params,
        }) + "\n"
        try:
            with self._send_lock:
                sock.sendall(payload.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            _logger.debug("RPC send %s failed: %s", method, exc)
            with self._lock:
                self._inflight.pop(req_id, None)
            return None
        return req_id

    @staticmethod
    def _parse(line: str) -> Optional[dict]:
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _dispatch_line(self, line: str) -> None:
        obj = self._parse(line)
        if obj is not None:
            self._dispatch_obj(obj)

    def _dispatch_obj(self, obj: dict) -> None:
        self._stamp()  # any traffic proves Jamulus is alive
        method = obj.get("method")
        if method and obj.get("id") is None:
            self._handle_notification(method, obj.get("params") or {})
            return
        if "id" in obj and ("result" in obj or "error" in obj):
            with self._lock:
                req_method = self._inflight.pop(obj.get("id"), None)
            if "result" in obj:
                self._handle_response(req_method, obj["result"])

    # ------------------------------------------------------------------
    # Response + notification handling
    # ------------------------------------------------------------------
    def _handle_response(self, method: Optional[str], result) -> None:
        if method == "jamulusclient/getChannelInfo" and isinstance(result, dict):
            self._set_local_id(result.get("id"))
        elif method == "jamulusclient/getClientList" and isinstance(result, dict):
            self._update_clients(result.get("clients"))

    def _handle_notification(self, method: str, params: dict) -> None:
        if method == "jamulusclient/clientListReceived":
            self._update_clients(params.get("clients"))
        elif method == "jamulusclient/channelLevelListReceived":
            self._emit_levels(params.get("channelLevelList"))
        elif method == "jamulusclient/chatTextReceived":
            self._emit_chat(params.get("chatText"))
        elif method == "jamulusclient/connected":
            self._set_local_id(params.get("id"))
            # ask for a fresh list now that we're connected
            self._send("jamulusclient/getClientList", {})
        elif method == "jamulusclient/disconnected":
            self._update_clients([])
        elif method == "jamulusclient/recorderState":
            self._emit_recorder_state(params.get("state"))

    def _set_local_id(self, value) -> None:
        try:
            cid = int(value)
        except (TypeError, ValueError):
            return
        if cid >= 0:
            self._local_channel_id = cid
            # Re-tag cached clients' is_local if we learned our id late.
            for c in self._clients:
                c.is_local = (c.channel_id == cid)

    def _update_clients(self, raw_clients) -> None:
        if not isinstance(raw_clients, list):
            return
        clients: List[ChannelInfo] = []
        for idx, entry in enumerate(raw_clients):
            if not isinstance(entry, dict):
                continue
            # Jamulus client entries use "id"; be lenient about index fallback.
            cid = entry.get("id", entry.get("channelId", idx))
            if not isinstance(cid, int) or cid < 0:
                continue
            clients.append(ChannelInfo(
                channel_id=cid,
                name=str(entry.get("name", "") or f"Participant {cid}"),
                instrument=str(entry.get("instrument", "")),
                skill_level=str(entry.get("skillLevel", "")),
                country=str(entry.get("country", "")),
                city=str(entry.get("city", "")),
                is_local=(cid == self._local_channel_id),
            ))
        self._clients = clients
        if self._on_participants_changed:
            try:
                self._on_participants_changed(list(clients))
            except Exception as exc:  # noqa: BLE001
                _logger.debug("on_participants_changed callback error: %s", exc)

    def _emit_recorder_state(self, raw_state) -> None:
        """The server we're connected to changed recorder state.

        Jamulus sends its ERecorderState enum; 3 (RS_RECORDING) means every
        musician is being captured to a separate track on the server.
        """
        if self._on_recorder_state is None:
            return
        try:
            state = int(raw_state)
        except (TypeError, ValueError):
            return
        try:
            self._on_recorder_state(state == self.RECORDER_STATE_RECORDING, state)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("on_recorder_state callback error: %s", exc)

    def _emit_chat(self, text) -> None:
        if not isinstance(text, str) or not self._on_chat:
            return
        try:
            self._on_chat(text)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("on_chat callback error: %s", exc)

    def _emit_levels(self, raw_levels) -> None:
        if not isinstance(raw_levels, list) or not self._on_levels:
            return
        # Per the Jamulus protocol, channelLevelList[i] corresponds to
        # clients[i] from the last clientListReceived — so map by that
        # client's channel id, not the raw list position (channel ids can be
        # sparse after disconnects, which would mis-attribute meters).
        clients = self._clients
        levels: Dict[int, float] = {}
        for idx, raw in enumerate(raw_levels):
            try:
                value = min(1.0, max(0.0, int(raw) / self.LEVEL_MAX))
            except (TypeError, ValueError):
                continue
            cid = clients[idx].channel_id if idx < len(clients) else idx
            levels[cid] = value
        if levels:
            try:
                self._on_levels(levels)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("on_levels callback error: %s", exc)

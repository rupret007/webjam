"""
JamulusRpcClient — JSON-RPC HTTP client for Jamulus 3.9+.

Jamulus 3.9+ ships an optional JSON-RPC server on the CLIENT side.
When WebJam launches Jamulus with ``--jsonrpcport 22222``, this client
can query and control it over localhost HTTP.

Architecture:
    ``JamulusRpcClient.start()``
        → background thread polls ``get_channel_clients()`` every 5 s
        → SSE listener receives real-time events (channelConnected,
          channelDisconnected, channelLevelListReceived)
        → calls ``on_participants_changed(list[ChannelInfo])``
        → calls ``on_levels(dict[int, float])``

Caller never touches HTTP; they register callbacks and call ``set_channel_gain``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import httpx

_logger = logging.getLogger("webjam.jamulus_rpc")


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
    """
    JSON-RPC 2.0 HTTP client for a running Jamulus GUI client
    (started with ``--jsonrpcport PORT``).

    This is the primary integration path for Jamulus 3.9+.  When the
    JSON-RPC server isn't reachable (old Jamulus, not started yet), all
    calls silently no-op so the UDP fallback and demo data take over.
    """

    JSONRPC_VERSION = "2.0"
    POLL_INTERVAL_S = 5.0
    CONNECT_TIMEOUT_S = 1.0
    REQUEST_TIMEOUT_S = 3.0
    LEVEL_RANGE = 32767      # Jamulus level values are 0..32767
    GAIN_RANGE_MAX = 10000   # setChannelGain uses 0..10000

    def __init__(
        self,
        port: int = 22222,
        *,
        on_participants_changed: Optional[Callable[[List[ChannelInfo]], None]] = None,
        on_levels: Optional[Callable[[Dict[int, float]], None]] = None,
    ) -> None:
        self._port = port
        self._base_url = f"http://127.0.0.1:{port}"
        self._on_participants_changed = on_participants_changed
        self._on_levels = on_levels

        self._available = False   # True once we've had one successful call
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._sse_thread: Optional[threading.Thread] = None
        self._request_counter = 0
        self._lock = threading.Lock()
        self._http_client: Optional[httpx.Client] = None
        # Cache the local channel ID so we don't make a second HTTP call on
        # every poll cycle (getChannelClients already costs one request).
        self._local_channel_id: int = -1
        # Heartbeat: monotonic time of the last successful RPC interaction
        # (poll or SSE event).  Stays at 0.0 until the first success.  Used
        # by ``last_activity_age()`` so callers can detect a hung Jamulus
        # (process alive but RPC silent).
        self._last_activity_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Reset heartbeat so a reused client (the controller keeps one instance
        # across stop()/start() cycles) doesn't carry a stale activity timestamp
        # from the previous session.  Without this, last_activity_age() returns
        # a finite, growing age instead of inf right after a relaunch, which can
        # trip a false "Jamulus stopped responding" banner on a healthy server.
        self._last_activity_at = 0.0
        self._available = False
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="jamulus-rpc-poll"
        )
        self._poll_thread.start()
        self._sse_thread = threading.Thread(
            target=self._sse_loop, daemon=True, name="jamulus-rpc-sse"
        )
        self._sse_thread.start()

    def stop(self) -> None:
        self._running = False
        self._local_channel_id = -1  # reset so next session re-queries
        self._available = False
        with self._lock:  # _next_id() reads/writes this under the same lock
            self._request_counter = 0  # next session starts request IDs at 1
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def set_channel_gain(self, channel_id: int, gain_0_to_127: int) -> bool:
        """
        Set the local mix gain for ``channel_id``.

        Converts from the Jamulus-controller 0..127 range to the JSON-RPC
        0..10000 range and sends ``jamulus/setChannelGain``.

        Returns True on success.
        """
        gain_rpc = int(gain_0_to_127 / 127.0 * self.GAIN_RANGE_MAX)
        result = self._call("jamulus/setChannelGain", {
            "channelId": channel_id,
            "gain": gain_rpc,
        })
        return result is not None

    def set_channel_mute(self, channel_id: int, muted: bool) -> bool:
        # Mute = gain of 0
        if muted:
            return self.set_channel_gain(channel_id, 0)
        # Un-mute: restore to "unity" (100/127 → ~7874/10000)
        return self.set_channel_gain(channel_id, 100)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        self._http_client = httpx.Client()
        while self._running:
            try:
                clients = self.get_channel_clients()
                if clients is not None:
                    self._available = True
                    self._last_activity_at = time.monotonic()
                    if self._on_participants_changed:
                        try:
                            self._on_participants_changed(clients)
                        except Exception as exc:  # noqa: BLE001
                            _logger.debug("callback error: %s", exc)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("RPC poll error: %s", exc)
                self._available = False
            time.sleep(self.POLL_INTERVAL_S)

    def last_activity_age(self) -> float:
        """Seconds since the most recent successful RPC interaction.

        Returns ``float('inf')`` if no successful interaction has happened
        since ``start()`` was called.  Callers (e.g. the controller's
        reconnect tick) can use this to detect a hung Jamulus — process
        still alive, but RPC and SSE both silent for too long.
        """
        if self._last_activity_at <= 0.0:
            return float("inf")
        return time.monotonic() - self._last_activity_at

    def get_channel_clients(self) -> Optional[List[ChannelInfo]]:
        raw = self._call("jamulus/getChannelClients", {})
        if raw is None:
            return None
        result = raw.get("result")
        if not isinstance(result, list):
            return None
        clients: List[ChannelInfo] = []
        local_id = self._get_local_channel_id()
        for entry in result:
            if not isinstance(entry, dict):
                continue
            channel_id = entry.get("channelId", -1)
            if not isinstance(channel_id, int) or channel_id < 0:
                continue
            info = ChannelInfo(
                channel_id=channel_id,
                name=str(entry.get("name", "") or f"Participant {channel_id}"),
                instrument=str(entry.get("instrument", "")),
                skill_level=str(entry.get("skillLevel", "")),
                country=str(entry.get("country", "")),
                city=str(entry.get("city", "")),
                is_local=(channel_id == local_id),
            )
            clients.append(info)
        return clients

    def _get_local_channel_id(self) -> int:
        """Return the local (self) channel ID, querying RPC only on first call.

        The local channel ID is stable for the lifetime of the Jamulus session,
        so we cache the result after the first successful lookup to avoid a
        second HTTP request on every 5-second poll cycle.
        """
        if self._local_channel_id >= 0:
            return self._local_channel_id
        raw = self._call("jamulus/getClientInfo", {})
        if raw is None:
            return -1
        result = raw.get("result")
        if isinstance(result, dict):
            try:
                cid = int(result.get("channelId", -1))
            except (TypeError, ValueError):
                # Malformed/hostile getClientInfo response — don't let a bad
                # channelId raise out of every participant-refresh poll.
                return -1
            if cid >= 0:
                self._local_channel_id = cid
            return cid
        return -1

    # ------------------------------------------------------------------
    # SSE listener — real-time events
    # ------------------------------------------------------------------
    def _sse_loop(self) -> None:
        """
        Listen to the Jamulus SSE event stream.

        Events we handle:
          channelConnected      → trigger participant refresh
          channelDisconnected   → trigger participant refresh
          channelLevelListReceived → parse per-channel levels, call on_levels
        """
        if self._http_client is None:
            self._http_client = httpx.Client()
        retry_wait = 2.0
        while self._running:
            try:
                with self._http_client.stream(
                    "GET",
                    f"{self._base_url}/events",
                    timeout=httpx.Timeout(connect=1.0, read=30.0, write=5.0, pool=5.0),
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    retry_wait = 2.0
                    event_type = ""
                    data_lines: list[str] = []

                    for line in response.iter_lines():
                        if not self._running:
                            break
                        line = line.strip()
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())
                        elif line == "" and event_type:
                            self._handle_sse_event(event_type, "\n".join(data_lines))
                            event_type = ""
                            data_lines = []

            except httpx.ConnectError:
                pass  # Jamulus not up yet — expected during startup
            except Exception as exc:  # noqa: BLE001
                _logger.debug("SSE loop error: %s", exc)

            if self._running:
                time.sleep(retry_wait)
                retry_wait = min(retry_wait * 1.5, 30.0)

    def _handle_sse_event(self, event_type: str, data: str) -> None:
        import json
        # Any SSE event (even ones we don't act on) proves Jamulus is alive
        # and responsive — refresh the heartbeat.
        self._last_activity_at = time.monotonic()
        try:
            payload = json.loads(data) if data.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        if event_type in ("channelConnected", "channelDisconnected"):
            # Refresh participant list
            try:
                clients = self.get_channel_clients()
                if clients is not None and self._on_participants_changed:
                    self._on_participants_changed(clients)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("Post-event refresh failed: %s", exc)

        elif event_type == "channelLevelListReceived":
            # payload is {"levels": [int, int, ...]} — one value per channel, 0..32767
            raw_levels = payload.get("levels") or []
            if not isinstance(raw_levels, list):
                return
            levels: Dict[int, float] = {}
            for idx, raw in enumerate(raw_levels):
                try:
                    levels[idx] = min(1.0, max(0.0, int(raw) / self.LEVEL_RANGE))
                except (TypeError, ValueError):
                    pass
            if levels and self._on_levels:
                try:
                    self._on_levels(levels)
                except Exception as exc:  # noqa: BLE001
                    _logger.debug("callback error: %s", exc)

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        with self._lock:
            self._request_counter += 1
            return self._request_counter

    def _call(self, method: str, params: dict) -> Optional[dict]:
        payload = {
            "jsonrpc": self.JSONRPC_VERSION,
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        try:
            response = httpx.post(
                self._base_url,
                json=payload,
                timeout=httpx.Timeout(
                    connect=self.CONNECT_TIMEOUT_S,
                    read=self.REQUEST_TIMEOUT_S,
                    write=self.REQUEST_TIMEOUT_S,
                    pool=self.REQUEST_TIMEOUT_S,
                ),
            )
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            return None  # Jamulus not running — expected
        except Exception as exc:  # noqa: BLE001
            _logger.debug("RPC call %s failed: %s", method, exc)
            return None

"""
JamulusRpcClient — JSON-RPC client for the Jamulus client's control API.

Speaks the **real** Jamulus JSON-RPC protocol (pinned and verified against
3.12.2): newline-delimited JSON-RPC 2.0 over a raw **TCP** socket on localhost,
authenticated with ``jamulus/apiAuth`` using the secret from the file Jamulus
was launched with (``--jsonrpcsecretfile``).

    WebJam launches Jamulus with:
        --jsonrpcport <port> --jsonrpcsecretfile <DEFAULT_SECRET_PATH>
    (see services/bridge_service.py), then this client connects, authenticates,
    and translates between WebJam's mixer model and the Jamulus client API.

Methods used (client mode, pinned Jamulus 3.12.2):
    jamulus/apiAuth                  — authenticate (required first)
    jamulusclient/getChannelInfo     — our own channel id (for is_local)
    jamulusclient/getClientList      — current participants
    jamulusclient/setFaderLevel      — per-channel fader (level 0..100)

Jamulus 3.12.2 has no live-send mute request.  In particular,
``jamulusclient/setMuted`` does not exist.  ``live_send_mute`` is therefore
false and the compatibility ``set_self_muted`` method fails closed without
writing to the socket.

Notifications consumed:
    jamulusclient/clientListReceived        — participant list changed
    jamulusclient/channelLevelListReceived  — per-channel levels (0..9)
    jamulusclient/connected / disconnected  — refresh trigger

Caller never touches the socket: it registers ``on_participants_changed`` /
``on_levels`` callbacks and calls ``set_channel_gain`` / ``set_channel_mute``.
When the RPC server isn't reachable (Jamulus not started, old version, auth not
ready) every call silently no-ops so startup can wait for authoritative data.
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

from core.jamulus_name import JamulusNameError, validate_jamulus_name
from core.settings import jamulus_client_rpc_secret_path

_logger = logging.getLogger("webjam.jamulus_rpc")

# Shared secret file: bridge_service writes it and launches Jamulus with
# --jsonrpcsecretfile pointing here; this client reads it to authenticate.
# The integrated macOS component is non-sandboxed and receives the private
# WebJam Application Support path explicitly. WebJam never stores this secret
# in Jamulus's container.
DEFAULT_SECRET_PATH = jamulus_client_rpc_secret_path()

# Keep the outbound surface explicit.  This is both executable documentation
# for the pinned binary and a guard against fake RPC servers teaching WebJam
# capabilities that Jamulus does not implement.
PINNED_JAMULUS_VERSION = "3.12.2"
PINNED_CLIENT_REQUEST_METHODS = frozenset(
    {
        "jamulusclient/getClientInfo",
        "jamulusclient/getChannelInfo",
        "jamulusclient/getClientList",
        "jamulusclient/getMidiDevices",
        "jamulusclient/getMidiSettings",
        "jamulusclient/pollServerList",
        "jamulusclient/sendChatText",
        "jamulusclient/setFaderLevel",
        "jamulusclient/setMidiSettings",
        "jamulusclient/setName",
        "jamulusclient/setSkillLevel",
    }
)
PINNED_REQUEST_METHODS = PINNED_CLIENT_REQUEST_METHODS | {
    "jamulus/apiAuth",
    "jamulus/getMode",
}
LIVE_SEND_MUTE = False


@dataclass
class ChannelInfo:
    channel_id: int
    name: str
    instrument: str = ""
    skill_level: str = ""
    country: str = ""
    city: str = ""
    is_local: bool = False


@dataclass(frozen=True, slots=True)
class JamulusRpcMonitorIdentity:
    """Exact native-process identity owned by one RPC reader epoch.

    ``monitor_epoch`` is local to this :class:`JamulusRpcClient` instance and
    changes on every start and stop.  The Bridge-supplied generation and PID
    keep participant/RPC evidence attached to the exact native Jamulus
    process that produced it, even when a UI callback is delivered later.

    Legacy direct users may start monitoring without a process identity.  In
    that compatibility mode generation/PID are both zero and cannot satisfy
    :meth:`JamulusRpcClient.monitor_snapshot_for`.
    """

    monitor_epoch: int
    process_generation: int
    process_id: int

    @property
    def is_process_bound(self) -> bool:
        return self.process_generation > 0 and self.process_id > 0


@dataclass(frozen=True, slots=True)
class JamulusRpcMonitorSnapshot:
    """One atomic, immutable observation of the current RPC monitor."""

    identity: JamulusRpcMonitorIdentity
    running: bool
    available: bool
    authenticated: bool
    last_activity_at: float | None
    last_activity_age_seconds: float | None


class JamulusRpcClient:
    """JSON-RPC 2.0 client (NDJSON over TCP) for a running Jamulus client."""

    JSONRPC_VERSION = "2.0"
    CONNECT_TIMEOUT_S = 1.0
    AUTH_TIMEOUT_S = 3.0
    live_send_mute = LIVE_SEND_MUTE
    supported_request_methods = PINNED_REQUEST_METHODS
    RECONNECT_WAIT_S = 2.0
    LEVEL_MAX = 9      # channelLevelList values are integers 0..9
    # Jamulus ERecorderState: 1=not initialised, 2=not enabled, 3=recording.
    RECORDER_STATE_NOT_INITIALISED = 1
    RECORDER_STATE_STOPPED = 2
    RECORDER_STATE_RECORDING = 3
    FADER_MAX = 100    # setFaderLevel level is 0..100
    GAIN_RANGE_IN = 127  # WebJam's internal mixer range (0..127)

    def __init__(
        self,
        port: int = 22222,
        *,
        on_participants_changed: Optional[Callable[[List[ChannelInfo]], None]] = None,
        on_participants_changed_with_source: Optional[
            Callable[[List[ChannelInfo], JamulusRpcMonitorIdentity], None]
        ] = None,
        on_levels: Optional[Callable[[Dict[int, float]], None]] = None,
        on_chat: Optional[Callable[[str], None]] = None,
        on_chat_with_source: Optional[
            Callable[[str, JamulusRpcMonitorIdentity], None]
        ] = None,
        on_recorder_state: Optional[Callable[[bool, int], None]] = None,
        on_recorder_state_with_source: Optional[
            Callable[[bool, int, JamulusRpcMonitorIdentity], None]
        ] = None,
        secret_path: Optional[Path] = None,
    ) -> None:
        self._port = port
        self._secret_path = Path(secret_path) if secret_path else DEFAULT_SECRET_PATH
        self._on_participants_changed = on_participants_changed
        self._on_participants_changed_with_source = (
            on_participants_changed_with_source
        )
        self._on_levels = on_levels
        self._on_chat = on_chat
        self._on_chat_with_source = on_chat_with_source
        self._on_recorder_state = on_recorder_state
        self._on_recorder_state_with_source = on_recorder_state_with_source

        self._available = False
        self._authed = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._sock_epoch = 0
        self._monitor_epoch = 0
        self._monitor_identity = JamulusRpcMonitorIdentity(0, 0, 0)
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        # Callback accounting closes the epoch-check/invoke race without
        # holding a client lock while arbitrary consumer code runs. stop()
        # invalidates the epoch first, then waits for already-entered
        # callbacks; callbacks can therefore call back into stop safely.
        self._callback_condition = threading.Condition(threading.RLock())
        self._callbacks_inflight: dict[tuple[int, int], int] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # serialises sendall on the socket
        self._request_counter = 0
        # request id -> (monitor epoch, method).  Legacy parser fixtures may
        # still seed a bare method string; production sends are always bound.
        self._inflight: Dict[int, tuple[int, str] | str] = {}
        self._clients: List[ChannelInfo] = []     # last-known participant list
        self._local_channel_id: int = -1
        # Jamulus 3.12.2's real getChannelInfo response describes this client
        # but does not include its server-assigned channel id.  Keep the
        # profile so we can identify the one matching getClientList row.
        self._local_profile: dict[str, str] = {}
        # Heartbeat: monotonic time of the last successful RPC interaction.
        # Stays 0.0 until the first success; reset on (re)start.
        self._last_activity_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def _validated_process_identity(
        process_generation: int | None,
        process_id: int | None,
    ) -> tuple[int, int]:
        """Return a valid process identity, retaining no-arg compatibility."""

        if process_generation is None and process_id is None:
            return 0, 0
        try:
            generation = int(process_generation)
            pid = int(process_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "process_generation and process_id must both be positive integers"
            ) from exc
        if generation <= 0 or pid <= 0:
            raise ValueError(
                "process_generation and process_id must both be positive integers"
            )
        return generation, pid

    def start(
        self,
        *,
        process_generation: int | None = None,
        process_id: int | None = None,
    ) -> JamulusRpcMonitorIdentity:
        """Start one epoch-bound RPC reader.

        Bridge-owned launches supply the exact positive process generation and
        PID.  The no-argument form remains for isolated/legacy RPC consumers,
        but produces an unbound 0/0 identity that cannot authenticate a
        Bridge process.
        """

        generation, pid = self._validated_process_identity(
            process_generation,
            process_id,
        )
        with self._lifecycle_lock:
            with self._state_lock:
                if self._running:
                    current = self._monitor_identity
                    if (
                        current.process_generation == generation
                        and current.process_id == pid
                    ):
                        return current
                    raise RuntimeError(
                        "Jamulus RPC monitoring already owns a different process"
                    )

            # Invalidate any winding-down reader before publishing a new
            # epoch. Already-entered callbacks retain their old immutable
            # identity. stop() normally drains them; if start() is invoked
            # re-entrantly by such a callback, waiting here would deadlock on
            # the caller itself, so provenance (not a lifecycle lock) keeps
            # that already-entered callback harmless.
            with self._state_lock:
                old_event = self._stop_event
                old_event.set()
                old_sock = self._sock
                self._sock = None
                self._sock_epoch = 0
                self._monitor_epoch += 1
                epoch = self._monitor_epoch
                identity = JamulusRpcMonitorIdentity(epoch, generation, pid)
                stop_event = threading.Event()
                self._stop_event = stop_event
                self._monitor_identity = identity
                self._running = True
                self._last_activity_at = 0.0
                self._available = False
                self._authed = False
                self._local_channel_id = -1
                self._local_profile = {}
                self._clients = []
            if old_sock is not None:
                try:
                    old_sock.close()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._request_counter = 0
                self._inflight.clear()
            thread = threading.Thread(
                target=self._run_loop,
                args=(epoch, stop_event),
                daemon=True,
                name=f"jamulus-rpc-{epoch}",
            )
            with self._state_lock:
                self._thread = thread
            thread.start()
            return identity

    def stop(self) -> None:
        with self._lifecycle_lock:
            # Invalidate first, then drain callbacks that had already entered.
            # No callback retains a client lock while its consumer runs, so a
            # consumer may safely call stop() itself.
            with self._state_lock:
                retired_epoch = self._monitor_epoch
                self._monitor_epoch += 1
                self._monitor_identity = JamulusRpcMonitorIdentity(
                    self._monitor_epoch,
                    0,
                    0,
                )
                self._running = False
                self._available = False
                self._authed = False
                self._last_activity_at = 0.0
                self._local_channel_id = -1
                self._local_profile = {}
                self._clients = []
                stop_event = self._stop_event
                stop_event.set()
                sock = self._sock
                self._sock = None
                self._sock_epoch = 0
                thread = self._thread
            self._wait_for_epoch_callbacks(retired_epoch)
            with self._lock:
                self._request_counter = 0
                self._inflight.clear()
            if sock is not None:
                try:
                    sock.close()   # unblocks a blocked recv in the reader
                except Exception:  # noqa: BLE001
                    pass
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)

    @property
    def available(self) -> bool:
        with self._state_lock:
            return self._available

    def monitor_snapshot(self) -> JamulusRpcMonitorSnapshot:
        """Return one atomic immutable view of the current monitor epoch."""

        observed_at = time.monotonic()
        with self._state_lock:
            identity = self._monitor_identity
            running = self._running
            available = self._available
            authenticated = self._authed
            last_activity_at = self._last_activity_at
        activity = last_activity_at if last_activity_at > 0.0 else None
        age = (
            max(0.0, observed_at - activity)
            if activity is not None
            else None
        )
        return JamulusRpcMonitorSnapshot(
            identity=identity,
            running=running,
            available=available,
            authenticated=authenticated,
            last_activity_at=activity,
            last_activity_age_seconds=age,
        )

    def monitor_snapshot_for(
        self,
        *,
        process_generation: int,
        process_id: int,
    ) -> JamulusRpcMonitorSnapshot | None:
        """Return RPC evidence only when it belongs to this exact process."""

        try:
            generation = int(process_generation)
            pid = int(process_id)
        except (TypeError, ValueError):
            return None
        if generation <= 0 or pid <= 0:
            return None
        snapshot = self.monitor_snapshot()
        identity = snapshot.identity
        if (
            not identity.is_process_bound
            or identity.process_generation != generation
            or identity.process_id != pid
        ):
            return None
        return snapshot

    def last_activity_age(self) -> float:
        """Seconds since the most recent successful RPC interaction.

        Returns ``float('inf')`` if nothing has succeeded since ``start()``.
        """
        snapshot = self.monitor_snapshot()
        age = snapshot.last_activity_age_seconds
        return float("inf") if age is None else age

    def _epoch_is_current(self, epoch: int) -> bool:
        with self._state_lock:
            return self._running and self._monitor_epoch == epoch

    def _begin_epoch_callback(
        self,
        epoch: int | None,
    ) -> tuple[int, int, JamulusRpcMonitorIdentity] | None:
        """Enter a callback only if ``epoch`` is still current."""

        if epoch is None:
            return None
        thread_id = threading.get_ident()
        with self._callback_condition:
            with self._state_lock:
                if not self._running or self._monitor_epoch != epoch:
                    return None
                identity = self._monitor_identity
            key = (epoch, thread_id)
            self._callbacks_inflight[key] = (
                self._callbacks_inflight.get(key, 0) + 1
            )
            return epoch, thread_id, identity

    def _end_epoch_callback(
        self,
        token: tuple[int, int, JamulusRpcMonitorIdentity] | None,
    ) -> None:
        if token is None:
            return
        key = token[0], token[1]
        with self._callback_condition:
            count = self._callbacks_inflight.get(key, 0)
            if count <= 1:
                self._callbacks_inflight.pop(key, None)
            else:
                self._callbacks_inflight[key] = count - 1
            self._callback_condition.notify_all()

    def _wait_for_epoch_callbacks(self, epoch: int) -> None:
        """Drain callbacks for ``epoch`` without waiting on the caller itself."""

        if epoch <= 0:
            return
        current_thread_id = threading.get_ident()
        with self._callback_condition:
            while any(
                callback_epoch == epoch
                and callback_thread_id != current_thread_id
                and count > 0
                for (
                    callback_epoch,
                    callback_thread_id,
                ), count in self._callbacks_inflight.items()
            ):
                self._callback_condition.wait(timeout=0.1)

    def _stamp(self, epoch: int | None = None) -> bool:
        """Stamp current activity; an old reader is refused atomically."""

        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return False
            self._last_activity_at = time.monotonic()
            return True

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
        """Fail closed: pinned Jamulus has no supported live-send mute."""
        del muted
        return False

    def send_chat_text(self, text: str) -> bool:
        """Send a chat message to the band (jamulusclient/sendChatText)."""
        if not text:
            return False
        return self._send(
            "jamulusclient/sendChatText", {"chatText": str(text)}
        ) is not None

    def set_name(self, name: str) -> bool:
        """Set the local musician's display name (jamulusclient/setName)."""
        try:
            validated = validate_jamulus_name(name)
        except JamulusNameError:
            return False
        return self._send(
            "jamulusclient/setName",
            {"name": validated.value},
        ) is not None

    def get_channel_clients(self) -> Optional[List[ChannelInfo]]:
        """Return the last-known participant list, or None if not yet received."""
        with self._state_lock:
            if not self._available:
                return None
            return list(self._clients)

    def _get_local_channel_id(self) -> int:
        """Return the cached local channel id (-1 if unknown)."""
        with self._state_lock:
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

    def _run_loop(self, epoch: int, stop_event: threading.Event) -> None:
        wait = self.RECONNECT_WAIT_S
        while self._epoch_is_current(epoch) and not stop_event.is_set():
            try:
                self._serve_once(epoch, stop_event)
                wait = self.RECONNECT_WAIT_S
            except Exception as exc:  # noqa: BLE001
                _logger.debug("RPC connection ended: %s", exc)
            with self._state_lock:
                if self._monitor_epoch != epoch:
                    return
                self._available = False
                self._authed = False
            if self._epoch_is_current(epoch) and not stop_event.wait(wait):
                wait = min(wait * 1.5, 30.0)

    def _serve_once(
        self,
        epoch: int,
        stop_event: threading.Event,
    ) -> None:
        if not self._epoch_is_current(epoch):
            return
        secret = self._read_secret()
        if not secret:
            # Jamulus not launched yet (no secret file) — back off and retry.
            raise ConnectionError("jsonrpc secret not available yet")

        sock = socket.create_connection(
            ("127.0.0.1", self._port), timeout=self.CONNECT_TIMEOUT_S
        )
        with self._state_lock:
            if (
                not self._running
                or self._monitor_epoch != epoch
                or stop_event.is_set()
            ):
                sock.close()
                return
            self._sock = sock
            self._sock_epoch = epoch
        read_buf = b""

        def _readline(deadline: Optional[float] = None) -> str:
            nonlocal read_buf
            while self._epoch_is_current(epoch) and not stop_event.is_set():
                nl = read_buf.find(b"\n")
                if nl >= 0:
                    line, read_buf = read_buf[:nl], read_buf[nl + 1:]
                    return line.decode("utf-8")
                if deadline is not None and time.monotonic() >= deadline:
                    return ""
                remaining = 1.0 if deadline is None else max(
                    0.0, deadline - time.monotonic()
                )
                if remaining <= 0:
                    return ""
                sock.settimeout(remaining)
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    if deadline is not None:
                        continue
                    return ""
                except OSError as exc:
                    if "timed out" in str(exc).lower():
                        if deadline is not None:
                            continue
                        return ""
                    raise
                if chunk == b"":
                    raise ConnectionError("server closed connection")
                read_buf += chunk
            return ""

        try:
            auth_id = self._send(
                "jamulus/apiAuth",
                {"secret": secret},
                epoch=epoch,
            )
            if not self._await_auth(_readline, auth_id, epoch=epoch):
                raise ConnectionError("apiAuth failed or refused")
            with self._state_lock:
                if (
                    not self._running
                    or self._monitor_epoch != epoch
                    or stop_event.is_set()
                ):
                    return
                self._authed = True
                self._available = True
            self._stamp(epoch)

            self._send("jamulusclient/getChannelInfo", {}, epoch=epoch)
            self._send("jamulusclient/getClientList", {}, epoch=epoch)

            while self._epoch_is_current(epoch) and not stop_event.is_set():
                line = _readline()
                if not line:
                    continue
                self._dispatch_line(line, epoch=epoch)
        finally:
            with self._state_lock:
                if self._sock is sock and self._sock_epoch == epoch:
                    self._sock = None
                    self._sock_epoch = 0
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    def _await_auth(
        self,
        readline,
        auth_id: Optional[int],
        *,
        epoch: int,
    ) -> bool:
        """Read lines until the apiAuth response arrives; dispatch any
        notifications seen in the meantime."""
        deadline = time.monotonic() + self.AUTH_TIMEOUT_S
        while self._epoch_is_current(epoch) and time.monotonic() < deadline:
            line = readline(deadline)
            if not line:
                continue
            obj = self._parse(line)
            if obj is None:
                continue
            if not self._epoch_is_current(epoch):
                return False
            if obj.get("id") == auth_id and ("result" in obj or "error" in obj):
                self._pop_inflight(auth_id, epoch=epoch)
                return obj.get("result") == "ok"
            self._dispatch_obj(obj, epoch=epoch)
        return False

    # ------------------------------------------------------------------
    # Sending / dispatch
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        with self._lock:
            self._request_counter += 1
            return self._request_counter

    def _pop_inflight(
        self,
        request_id,
        *,
        epoch: int | None,
    ) -> Optional[str]:
        """Pop only the request owned by ``epoch``.

        Request IDs restart at one for every monitor. A delayed old response
        must therefore never consume a replacement reader's same-numbered
        request.
        """

        with self._lock:
            entry = self._inflight.get(request_id)
            if isinstance(entry, tuple):
                entry_epoch, method = entry
                if epoch is None or entry_epoch != epoch:
                    return None
                self._inflight.pop(request_id, None)
                return method
            if isinstance(entry, str) and epoch is None:
                self._inflight.pop(request_id, None)
                return entry
            return None

    def _send(
        self,
        method: str,
        params: dict,
        *,
        epoch: int | None = None,
    ) -> Optional[int]:
        if method not in self.supported_request_methods:
            _logger.error(
                "refusing unsupported Jamulus %s request: %s",
                PINNED_JAMULUS_VERSION,
                method,
            )
            return None
        with self._state_lock:
            current_epoch = self._monitor_epoch
            if epoch is not None and (
                not self._running or current_epoch != epoch
            ):
                return None
            sock = self._sock
            sock_epoch = self._sock_epoch
            if sock is None or sock_epoch != current_epoch:
                return None
            if epoch is None:
                epoch = current_epoch
            # Register the request while the lifecycle state is stable. stop()
            # takes state -> request locks in the same order, then clears this
            # map after invalidating the epoch.
            with self._lock:
                self._request_counter += 1
                req_id = self._request_counter
                self._inflight[req_id] = (epoch, method)
        payload = json.dumps({
            "jsonrpc": self.JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params,
        }) + "\n"
        try:
            with self._send_lock:
                with self._state_lock:
                    if (
                        not self._running
                        or self._monitor_epoch != epoch
                        or self._sock is not sock
                        or self._sock_epoch != epoch
                    ):
                        with self._lock:
                            if self._inflight.get(req_id) == (epoch, method):
                                self._inflight.pop(req_id, None)
                        return None
                sock.sendall(payload.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            _logger.debug("RPC send %s failed: %s", method, exc)
            if epoch is None or self._epoch_is_current(epoch):
                with self._lock:
                    if self._inflight.get(req_id) == (epoch, method):
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

    def _dispatch_line(self, line: str, *, epoch: int | None = None) -> None:
        obj = self._parse(line)
        if obj is not None:
            self._dispatch_obj(obj, epoch=epoch)

    def _dispatch_obj(self, obj: dict, *, epoch: int | None = None) -> None:
        if epoch is not None and not self._epoch_is_current(epoch):
            return
        if not self._stamp(epoch):  # any traffic proves Jamulus is alive
            return
        method = obj.get("method")
        if method and obj.get("id") is None:
            self._handle_notification(
                method,
                obj.get("params") or {},
                epoch=epoch,
            )
            return
        if "id" in obj and ("result" in obj or "error" in obj):
            request_id = obj.get("id")
            req_method = self._pop_inflight(request_id, epoch=epoch)
            if "result" in obj:
                self._handle_response(req_method, obj["result"], epoch=epoch)

    # ------------------------------------------------------------------
    # Response + notification handling
    # ------------------------------------------------------------------
    def _handle_response(
        self,
        method: Optional[str],
        result,
        *,
        epoch: int | None = None,
    ) -> None:
        if method == "jamulusclient/getChannelInfo" and isinstance(result, dict):
            self._set_local_channel_info(result, epoch=epoch)
        elif method == "jamulusclient/getClientList" and isinstance(result, dict):
            self._update_clients(result.get("clients"), epoch=epoch)

    def _handle_notification(
        self,
        method: str,
        params: dict,
        *,
        epoch: int | None = None,
    ) -> None:
        if epoch is not None and not self._epoch_is_current(epoch):
            return
        if method == "jamulusclient/clientListReceived":
            self._update_clients(params.get("clients"), epoch=epoch)
        elif method == "jamulusclient/channelLevelListReceived":
            self._emit_levels(params.get("channelLevelList"), epoch=epoch)
        elif method == "jamulusclient/chatTextReceived":
            self._emit_chat(params.get("chatText"), epoch=epoch)
        elif method == "jamulusclient/connected":
            self._set_local_id(params.get("id"), epoch=epoch)
            # Jamulus 3.12.2 does not include an id in getChannelInfo or in
            # every connected notification.  Refresh both halves so profile
            # matching can recover local identity after a reconnect.
            self._send("jamulusclient/getChannelInfo", {}, epoch=epoch)
            self._send("jamulusclient/getClientList", {}, epoch=epoch)
        elif method == "jamulusclient/disconnected":
            with self._state_lock:
                if epoch is not None and (
                    not self._running or self._monitor_epoch != epoch
                ):
                    return
                self._local_channel_id = -1
            self._update_clients([], epoch=epoch)
        elif method == "jamulusclient/recorderState":
            self._emit_recorder_state(params.get("state"), epoch=epoch)

    @staticmethod
    def _identity_value(value) -> str:
        return str(value or "").strip().casefold()

    def _set_local_channel_info(
        self,
        info: dict,
        *,
        epoch: int | None = None,
    ) -> None:
        """Remember the local profile and resolve its roster row.

        The production Jamulus 3.12.2 response contains name/profile fields
        but no channel id.  Older releases and test doubles may still include
        an id, so retain that faster path while supporting the real shape.
        """
        profile = {
            "name": self._identity_value(info.get("name")),
            "instrument": self._identity_value(info.get("instrument")),
            "skill_level": self._identity_value(info.get("skillLevel")),
            "country": self._identity_value(info.get("country")),
            "city": self._identity_value(info.get("city")),
        }
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return
            self._local_profile = profile
        self._set_local_id(info.get("id"), epoch=epoch)
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return
            local_channel_id = self._local_channel_id
        if local_channel_id < 0:
            inferred = self._infer_local_channel_id(epoch=epoch)
            if inferred is not None:
                self._set_local_id(inferred, epoch=epoch)

    def _infer_local_channel_id(
        self,
        *,
        epoch: int | None = None,
    ) -> Optional[int]:
        """Return the sole roster row matching getChannelInfo, if any."""
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return None
            profile = dict(self._local_profile)
            clients = list(self._clients)
            local_name = profile.get("name", "")
            if not local_name or not clients:
                return None

            named = [
                client for client in clients
                if self._identity_value(client.name) == local_name
            ]
            if len(named) == 1:
                return named[0].channel_id

            # Duplicate display names are possible.  Only choose one when all
            # available profile fields make the match unique; never guess.
            fields = ("instrument", "skill_level", "country", "city")
            exact = [
                client for client in named
                if all(
                    self._identity_value(getattr(client, field))
                    == profile.get(field, "")
                    for field in fields
                )
            ]
            return exact[0].channel_id if len(exact) == 1 else None

    def _set_local_id(
        self,
        value,
        *,
        notify: bool = True,
        epoch: int | None = None,
    ) -> None:
        try:
            cid = int(value)
        except (TypeError, ValueError):
            return
        if cid >= 0:
            with self._state_lock:
                if epoch is not None and (
                    not self._running or self._monitor_epoch != epoch
                ):
                    return
                self._local_channel_id = cid
                # Re-tag cached clients' is_local if we learned our id late.
                changed = False
                for client in self._clients:
                    is_local = client.channel_id == cid
                    if client.is_local != is_local:
                        client.is_local = is_local
                        changed = True
                clients = list(self._clients)
            # getClientList and getChannelInfo are independent RPC replies.
            # If the list arrived first, the UI already saw every row as
            # remote; publish the corrected list immediately instead of
            # waiting for another server event.
            if notify and changed:
                self._invoke_participant_callbacks(clients, epoch=epoch)

    def _update_clients(
        self,
        raw_clients,
        *,
        epoch: int | None = None,
    ) -> None:
        if not isinstance(raw_clients, list):
            return
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return
            local_channel_id = self._local_channel_id
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
                instrument=str(entry.get("instrument") or ""),
                skill_level=str(entry.get("skillLevel") or ""),
                country=str(entry.get("country") or ""),
                city=str(entry.get("city") or ""),
                is_local=(cid == local_channel_id),
            ))
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return
            self._clients = clients
            local_channel_id = self._local_channel_id
        if local_channel_id < 0:
            inferred = self._infer_local_channel_id(epoch=epoch)
            if inferred is not None:
                self._set_local_id(inferred, notify=False, epoch=epoch)
        self._invoke_participant_callbacks(clients, epoch=epoch)

    def _invoke_epoch_callback(
        self,
        callback: Callable | None,
        *args,
        epoch: int | None,
        label: str,
    ) -> None:
        if callback is None:
            return
        token = self._begin_epoch_callback(epoch)
        if epoch is not None and token is None:
            return
        try:
            try:
                callback(*args)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("%s callback error: %s", label, exc)
        finally:
            self._end_epoch_callback(token)

    def _invoke_epoch_callbacks_with_source(
        self,
        callback: Callable | None,
        sourced_callback: Callable | None,
        *args,
        epoch: int | None,
        label: str,
    ) -> None:
        """Invoke compatible and sourced consumers under one epoch token."""

        if callback is None and sourced_callback is None:
            return
        token = self._begin_epoch_callback(epoch)
        if epoch is not None and token is None:
            return
        try:
            if callback is not None:
                try:
                    callback(*args)
                except Exception as exc:  # noqa: BLE001
                    _logger.debug("%s callback error: %s", label, exc)
            if sourced_callback is not None and token is not None:
                try:
                    sourced_callback(*args, token[2])
                except Exception as exc:  # noqa: BLE001
                    _logger.debug("%s sourced callback error: %s", label, exc)
        finally:
            self._end_epoch_callback(token)

    def _invoke_participant_callbacks(
        self,
        clients: List[ChannelInfo],
        *,
        epoch: int | None,
    ) -> None:
        identity: JamulusRpcMonitorIdentity | None = None
        token = self._begin_epoch_callback(epoch)
        if epoch is not None and token is None:
            return
        try:
            if epoch is not None:
                assert token is not None
                # _begin_epoch_callback captured provenance atomically before
                # stop could invalidate the epoch. stop now drains this token.
                identity = token[2]
            if self._on_participants_changed is not None:
                try:
                    self._on_participants_changed(list(clients))
                except Exception as exc:  # noqa: BLE001
                    _logger.debug(
                        "on_participants_changed callback error: %s",
                        exc,
                    )
            if (
                identity is not None
                and self._on_participants_changed_with_source is not None
            ):
                try:
                    self._on_participants_changed_with_source(
                        list(clients),
                        identity,
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.debug(
                        "sourced participant callback error: %s",
                        exc,
                    )
        finally:
            self._end_epoch_callback(token)

    def _emit_recorder_state(
        self,
        raw_state,
        *,
        epoch: int | None = None,
    ) -> None:
        """The server we're connected to changed recorder state.

        Jamulus sends its ERecorderState enum; 3 (RS_RECORDING) means every
        musician is being captured to a separate track on the server.
        """
        if (
            self._on_recorder_state is None
            and self._on_recorder_state_with_source is None
        ):
            return
        try:
            state = int(raw_state)
        except (TypeError, ValueError):
            return
        known_states = {
            self.RECORDER_STATE_NOT_INITIALISED,
            self.RECORDER_STATE_STOPPED,
            self.RECORDER_STATE_RECORDING,
        }
        if state not in known_states:
            _logger.debug("Ignoring unknown recorderState value: %r", raw_state)
            return
        self._invoke_epoch_callbacks_with_source(
            self._on_recorder_state,
            self._on_recorder_state_with_source,
            state == self.RECORDER_STATE_RECORDING,
            state,
            epoch=epoch,
            label="on_recorder_state",
        )

    def _emit_chat(self, text, *, epoch: int | None = None) -> None:
        if (
            not isinstance(text, str)
            or (
                self._on_chat is None
                and self._on_chat_with_source is None
            )
        ):
            return
        self._invoke_epoch_callbacks_with_source(
            self._on_chat,
            self._on_chat_with_source,
            text,
            epoch=epoch,
            label="on_chat",
        )

    def _emit_levels(self, raw_levels, *, epoch: int | None = None) -> None:
        if not isinstance(raw_levels, list) or not self._on_levels:
            return
        # Per the Jamulus protocol, channelLevelList[i] corresponds to
        # clients[i] from the last clientListReceived — so map by that
        # client's channel id, not the raw list position (channel ids can be
        # sparse after disconnects, which would mis-attribute meters).
        with self._state_lock:
            if epoch is not None and (
                not self._running or self._monitor_epoch != epoch
            ):
                return
            clients = list(self._clients)
        levels: Dict[int, float] = {}
        for idx, raw in enumerate(raw_levels):
            try:
                value = min(1.0, max(0.0, int(raw) / self.LEVEL_MAX))
            except (TypeError, ValueError):
                continue
            cid = clients[idx].channel_id if idx < len(clients) else idx
            levels[cid] = value
        if levels:
            self._invoke_epoch_callback(
                self._on_levels,
                levels,
                epoch=epoch,
                label="on_levels",
            )

"""Own accepted sockets synchronously before creating any async setup work."""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import ssl
import sys
import time
from collections.abc import Callable

from .config import ServiceConfig, numeric_listener_host


_POLL_INTERVAL = 0.01
_ACCEPT_BATCH = 16


def _pending_accept_error(error: OSError) -> bool:
    if isinstance(error, (ConnectionAbortedError, ConnectionResetError)):
        return True
    # Linux accept(2) exposes these pending TCP errors on the listening call.
    # EOPNOTSUPP stays fatal: it can also mean an invalid listener socket type.
    return sys.platform == "linux" and error.errno is not None and any(
        error.errno == getattr(errno, name, None) for name in (
            "ENETDOWN", "EPROTO", "ENOPROTOOPT", "EHOSTDOWN",
            "ENONET", "EHOSTUNREACH", "ENETUNREACH"))


class _ControlGate(asyncio.Protocol):
    """Pause raw reads immediately; dispatch only after verified TLS upgrade."""

    def __init__(self, owner: OwnedControlListener, accepted: socket.socket) -> None:
        self.owner = owner
        self.raw: asyncio.Transport | None = None
        self.socket = accepted
        self.task: asyncio.Task[None] | None = None
        self.attaching = False
        self.secured: asyncio.Transport | None = None
        self.application: asyncio.StreamReaderProtocol | None = None
        self.pending = True
        self.promoted = False
        self.lost = False
        self.paused = False
        self.early = bytearray()

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.raw = transport
        transport.pause_reading()
        if self.owner._closed:
            transport.abort()

    def data_received(self, data: bytes) -> None:
        if self.application is not None:
            self.application.data_received(data)
        elif self.pending and len(self.early) + len(data) <= self.owner.read_limit:
            # SSLProtocol may deliver decrypted bytes before start_tls resumes.
            # Hold only one bounded frame; never parse or dispatch it here.
            self.early.extend(data)
        else:
            self.abort()

    def eof_received(self) -> bool | None:
        if self.application is not None:
            return self.application.eof_received()
        self.abort()
        return None

    def connection_lost(self, exc: Exception | None) -> None:
        self.lost = True
        self.early.clear()
        if self.application is not None:
            self.application.connection_lost(exc)
        asyncio.get_running_loop().call_soon(self.owner._reap_closed)

    def pause_writing(self) -> None:
        self.paused = True
        if self.application is not None:
            self.application.pause_writing()

    def resume_writing(self) -> None:
        self.paused = False
        if self.application is not None:
            self.application.resume_writing()

    def abort(self) -> None:
        if self.raw is not None:
            self.raw.abort()
        elif not self.attaching or (self.task is not None and self.task.done()):
            self.socket.close()


class OwnedControlListener:
    """Bound TLS setup before creating an application task or SSL state.

    One timer polls a bounded, fair batch of nonblocking accepts. The socket is
    owned before any task or transport exists. Rejection creates no setup task
    or TLS object. This control/health setup delay is never on the media path.
    Setup caps and rate buckets apply independently to each listener instance,
    so HTTP health requests do not consume the control listener's allowances.
    ``on_connection`` must synchronously adopt its writer and reserve a slot.
    """

    def __init__(
        self, config: ServiceConfig, ssl_context: ssl.SSLContext | None,
        on_connection: Callable[[asyncio.StreamReader, asyncio.StreamWriter], None],
        *, active_count: Callable[[], int], clock: Callable[[], float] = time.monotonic,
        bind: str | None = None, port: int | None = None,
        read_limit: int | None = None, max_active: int | None = None,
    ) -> None:
        self.config = config
        self.bind = config.control_bind if bind is None else bind
        self.port = config.control_port if port is None else port
        self.read_limit = config.max_control_frame_bytes if read_limit is None else read_limit
        self.max_active = config.max_connections if max_active is None else max_active
        numeric_listener_host(self.bind)
        if (type(self.port) is not int or not 0 <= self.port <= 65535
                or type(self.read_limit) is not int or not 1 <= self.read_limit <= 65536
                or type(self.max_active) is not int or not 1 <= self.max_active <= 65536):
            raise ValueError("invalid control listener limits")
        self._ssl_context = ssl_context
        self._on_connection = on_connection
        self._active_count = active_count
        self._clock = clock
        self._tokens = float(config.control_accept_burst)
        self._then = clock()
        self._listeners: list[socket.socket] = []
        self._poll_handle: asyncio.TimerHandle | None = None
        self._next_listener = 0
        self._connections: set[_ControlGate] = set()
        self._setups: dict[asyncio.Task[None], _ControlGate] = {}
        self._join_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False
        self._failure = False

    @property
    def sockets(self) -> tuple:
        return tuple(self._listeners)

    @property
    def poll_pending(self) -> bool:
        return self._poll_handle is not None and not self._poll_handle.cancelled()

    @property
    def pending_count(self) -> int:
        self._reap_closed()
        return max(sum(item.pending and not item.promoted for item in self._connections),
                   sum(not task.done() for task in self._setups))

    @property
    def owned_task_count(self) -> int:
        return sum(not task.done() for task in self._setups) + int(
            self._join_task is not None and not self._join_task.done())

    async def start(self) -> OwnedControlListener:
        if self._started or self._closed:
            raise RuntimeError("control listener can only be started once")
        self._started = True
        try:
            self._bind()
            if self._closed:
                raise RuntimeError("control listener closed during startup")
            self._schedule_poll()
        except BaseException:
            self.close()
            raise
        return self

    def _bind(self) -> None:
        host = self.bind
        local_name = isinstance(host, str) and host.isascii() and host.casefold() == "localhost"
        if local_name:
            addresses = ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1"))
        else:
            address = numeric_listener_host(host)
            addresses = ((socket.AF_INET6 if ":" in address else socket.AF_INET, address),)
        port = self.port
        for family, address in addresses:
            listener = None
            try:
                listener = socket.socket(family, socket.SOCK_STREAM)
                self._listeners.append(listener)
                listener.setblocking(False)
                if os.name == "nt":
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                listener.bind((address, port))
                listener.listen(min(self.max_active, 256))
                if port == 0:
                    port = listener.getsockname()[1]
            except OSError as exc:
                if listener is not None:
                    listener.close()
                    self._listeners.remove(listener)
                if local_name and family == socket.AF_INET6 and exc.errno in {
                    errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL,
                    errno.ENOPROTOOPT, errno.EINVAL,
                }:
                    continue
                raise OSError("control listener bind failed") from None

    def _allow_rate(self) -> bool:
        now = self._clock()
        elapsed = max(0.0, now - self._then)
        self._then = now
        self._tokens = min(float(self.config.control_accept_burst),
                           self._tokens + elapsed * self.config.control_accepts_per_second)
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True

    def _capacity_used(self) -> int:
        return max(self._active_count(), sum(item.promoted for item in self._connections))

    def _schedule_poll(self) -> None:
        if not self._closed and self._poll_handle is None:
            self._poll_handle = asyncio.get_running_loop().call_later(_POLL_INTERVAL, self._poll)

    def _poll(self) -> None:
        self._poll_handle = None
        if self._closed:
            return
        empty = 0
        for _ in range(_ACCEPT_BATCH):
            if self._closed or not self._listeners:
                break
            index = self._next_listener % len(self._listeners)
            self._next_listener = (index + 1) % len(self._listeners)
            try:
                accepted, _ = self._listeners[index].accept()
            except (BlockingIOError, InterruptedError):
                empty += 1
                if empty >= len(self._listeners):
                    break
                continue
            except OSError as error:
                if not _pending_accept_error(error):
                    self._failure = True
                    self.close()
                    return
                # A rejected pending connection did not invalidate this owned
                # listener. Count the attempt and retain the ordinary bounded
                # poll, giving the other address family a chance this tick.
                empty += 1
                if empty >= len(self._listeners):
                    break
                continue
            empty = 0
            # No await or task factory runs between accept and owned admission.
            self._admit(accepted)
        self._schedule_poll()

    def _admit(self, accepted: socket.socket) -> None:
        if (self._closed or not self._allow_rate()
                or self.pending_count >= self.config.max_pending_handshakes
                or self._capacity_used() >= self.max_active):
            accepted.close()
            return
        gate = _ControlGate(self, accepted)
        self._connections.add(gate)
        coroutine = self._attach_and_upgrade(gate)
        try:
            task = asyncio.get_running_loop().create_task(coroutine, name="webjam-control-setup")
        except Exception:
            coroutine.close()
            gate.abort()
            self._failure = True
            self.close()
            return
        self._setups[task] = gate
        gate.task = task
        task.add_done_callback(lambda done: self._setup_done(gate, done))

    async def _attach_and_upgrade(self, gate: _ControlGate) -> None:
        try:
            if self._closed or gate.lost:
                return
            # While this plain attach is in progress, Close keeps the task
            # owned instead of canceling before connection_made can publish its
            # transport. A failure with no published transport is cleaned only
            # after this task is done. There is never a TLS handshake here.
            gate.attaching = True
            transport, _ = await asyncio.get_running_loop().connect_accepted_socket(
                lambda: gate, gate.socket)
            gate.raw = transport
            gate.attaching = False
            if self._closed or gate.lost:
                gate.abort()
                return
            if self._ssl_context is None:
                self._activate(gate, transport)
                return
            secured = await asyncio.get_running_loop().start_tls(
                gate.raw, gate, self._ssl_context, server_side=True,
                ssl_handshake_timeout=self.config.tls_handshake_timeout_seconds,
                ssl_shutdown_timeout=self.config.connection_shutdown_timeout_seconds)
            if secured is None:
                gate.abort()
                return
            gate.secured = secured
            self._activate(gate, secured)
        except (OSError, TimeoutError):
            gate.abort()
        except BaseException:
            gate.abort()
            raise

    def _activate(self, gate: _ControlGate, transport: asyncio.Transport) -> None:
        if (self._closed or gate.lost or transport.is_closing()
                or self._capacity_used() >= self.max_active):
            gate.abort()
            return
        gate.promoted = True
        reader = asyncio.StreamReader(limit=self.read_limit)
        application = asyncio.StreamReaderProtocol(reader)
        gate.application = application
        application.connection_made(transport)
        if gate.paused:
            application.pause_writing()
        writer = asyncio.StreamWriter(transport, application, reader, asyncio.get_running_loop())
        try:
            result = self._on_connection(reader, writer)
            if result is not None:
                if asyncio.iscoroutine(result):
                    result.close()
                raise TypeError("control callback must adopt synchronously")
            if self._closed or transport.is_closing():
                gate.abort()
                return
            if gate.early:
                application.data_received(bytes(gate.early))
                gate.early.clear()
            if self._ssl_context is None:
                transport.resume_reading()
        except Exception:
            gate.abort()
            self._failure = True
            self.close()

    def _setup_done(self, gate: _ControlGate, task: asyncio.Task) -> None:
        if self._setups.pop(task, None) is None:
            return
        if gate.raw is None:
            gate.socket.close()
        if not task.cancelled():
            try:
                task.result()
            except Exception:
                self._failure = True
                self.close()
        self._reap_closed()

    def _reap_closed(self) -> None:
        for gate in tuple(self._connections):
            if (gate.socket is not None and gate.socket.fileno() < 0
                    and (gate.task is None or gate.task.done())):
                gate.early.clear()
                self._connections.discard(gate)

    def close(self) -> None:
        self._closed = True
        if self._poll_handle is not None:
            self._poll_handle.cancel()
            self._poll_handle = None
        for listener in self._listeners:
            listener.close()
        self._listeners.clear()
        for gate in tuple(self._connections):
            gate.abort()
            if gate.task is not None and (gate.raw is not None or not gate.attaching):
                gate.task.cancel()

    async def wait_closed(self) -> None:
        if not self._closed:
            raise RuntimeError("control listener must be closed before joining")
        if self._join_task is None:
            self._join_task = asyncio.create_task(self._join(), name="webjam-control-close")
        await asyncio.shield(self._join_task)

    async def _join(self) -> None:
        if self._setups:
            await asyncio.gather(*self._setups, return_exceptions=True)
        # gather may return immediately for already-done tasks, before their
        # scheduled done callbacks run. Retire those exact owned results here;
        # the later callbacks are idempotent, not a required scheduling barrier.
        for task, gate in tuple(self._setups.items()):
            if task.done():
                self._setup_done(gate, task)
        # Keep accepted sockets through real FD closure; no Server acceptance
        # task or hidden pre-attach ownership exists in this path.
        if self._setups:
            raise RuntimeError("control listener ownership failed")
        while self._connections:
            self._reap_closed()
            if self._connections:
                await asyncio.sleep(0.001)
        if self._failure:
            raise RuntimeError("control listener ownership failed")

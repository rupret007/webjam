"""Async control, UDP relay, and privacy-safe health servers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
from collections.abc import Callable, Mapping
from typing import Any

from .config import ServiceConfig, numeric_listener_host
from .control_listener import OwnedControlListener
from .host_admission import HostAdmissionPolicy, HostPrincipal
from .protocol import (
    PROTOCOL_VERSION,
    SESSION_BYTES,
    TOKEN_BYTES,
    ProtocolError,
    Role,
    decode_fixed,
    decode_opaque,
    encode_fixed,
    parse_control_line,
    parse_relay,
    require_exact_fields,
)
from .state import SessionRegistry, TokenBucket

_LOG = logging.getLogger("webjam_reference")


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _response(*, ok: bool, **values: object) -> bytes:
    body: dict[str, object] = {"ok": ok, "v": PROTOCOL_VERSION}
    body.update(values)
    return _json_bytes(body) + b"\n"


class _RelayProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: ReferenceService) -> None:
        self.service = service
        self.transport: asyncio.DatagramTransport | None = None
        self.closed = asyncio.get_running_loop().create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.service._relay_transport = self.transport
        if self.service._closed:
            transport.close()

    def connection_lost(self, exc: Exception | None) -> None:
        del exc
        if not self.closed.done():
            self.closed.set_result(None)

    async def wait_closed(self) -> None:
        await asyncio.shield(self.closed)

    def datagram_received(self, data: bytes, addr: tuple[object, ...]) -> None:
        if self.service._closed or not self.service._ready:
            return
        try:
            frame, body, tag = parse_relay(data, self.service.config.max_datagram_bytes)
        except ProtocolError as exc:
            reason = "version" if exc.code == "unsupported_version" else "malformed"
            self.service.registry.record_datagram_drop(reason)
            return
        try:
            result = self.service.registry.handle_datagram(frame, body, tag, addr)
        except Exception:
            self.service.registry.record_datagram_drop("malformed")
            self.service._privacy_log("internal_error", component="relay")
            return
        if result.datagram is not None and result.destination is not None:
            assert self.transport is not None
            self.transport.sendto(result.datagram, result.destination)

    def error_received(self, exc: Exception) -> None:
        # Socket errors may contain peer addresses, so only count the category.
        del exc
        self.service.registry.record_datagram_drop("not_ready")


class ReferenceService:
    """Self-contained v3 reference service with no persistence or audio parsing."""

    def __init__(
        self, config: ServiceConfig | None = None, *, wall_clock: Callable[[], float] = time.time
    ) -> None:
        self.config = config or ServiceConfig()
        self._wall_clock = wall_clock
        self._host_admission = (
            HostAdmissionPolicy.load(self.config.host_admission_path)
            if self.config.host_admission_enabled else None
        )
        self.registry = SessionRegistry(
            self.config,
            host_principals=self._host_admission.principals if self._host_admission else (),
        )
        self._control_server: OwnedControlListener | None = None
        self._http_server: OwnedControlListener | None = None
        self._relay_transport: asyncio.DatagramTransport | None = None
        self._relay_protocol: _RelayProtocol | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._http_writers: set[asyncio.StreamWriter] = set()
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._adopted_connections: dict[asyncio.StreamWriter, bool] = {}
        self._startup_task: asyncio.Task[ReferenceService] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._shutdown_waiters: set[asyncio.Task[Any]] = set()
        self._close_deadline: float | None = None
        self._retirement_failed = False
        self._active_connections = 0
        self._active_http_connections = 0
        self._started = False
        self._ready = False
        self._closed = False

    @property
    def control_port(self) -> int:
        return self._server_port(self._control_server)

    @property
    def http_port(self) -> int:
        return self._server_port(self._http_server)

    @property
    def relay_port(self) -> int:
        if self._relay_transport is None:
            raise RuntimeError("service is not started")
        address = self._relay_transport.get_extra_info("sockname")
        return int(address[1])

    async def start(self) -> ReferenceService:
        if self._started or self._closed:
            raise RuntimeError("service can only be started once")
        self._started = True
        self._startup_task = asyncio.create_task(self._start_resources(), name="webjam-reference-start")
        try:
            return await asyncio.shield(self._startup_task)
        except BaseException:
            # The owned worker never awaits Close, which may be joining it.
            # Caller cancellation still initiates retained, bounded retirement.
            await self.close()
            raise

    def _check_starting(self) -> None:
        if self._closed:
            raise asyncio.CancelledError

    def _make_relay_protocol(self) -> _RelayProtocol:
        protocol = _RelayProtocol(self)
        self._relay_protocol = protocol
        return protocol

    async def _start_resources(self) -> ReferenceService:
        try:
            self._check_starting()
            ssl_context = self._ssl_context()
            self._control_server = OwnedControlListener(
                self.config, ssl_context, self._accept_control,
                active_count=lambda: self._active_connections,
            )
            await self._control_server.start()
            self._check_starting()
            loop = asyncio.get_running_loop()
            relay_transport, _ = await loop.create_datagram_endpoint(
                self._make_relay_protocol,
                local_addr=(numeric_listener_host(self.config.relay_bind), self.config.relay_port),
            )
            self._relay_transport = relay_transport  # type: ignore[assignment]
            self._check_starting()
            self._http_server = OwnedControlListener(
                self.config, None, self._accept_http,
                active_count=lambda: self._active_http_connections,
                bind=numeric_listener_host(self.config.http_bind),
                port=self.config.http_port,
                read_limit=4_096,
                max_active=self.config.max_http_connections,
            )
            await self._http_server.start()
            self._check_starting()
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="webjam-reference-cleanup"
            )
            self._ready = True
        except BaseException:
            self._closed = True
            self._stop_resources()
            raise
        self._privacy_log(
            "started",
            control_port=self.control_port,
            relay_port=self.relay_port,
            http_port=self.http_port,
            tls=ssl_context is not None,
        )
        return self

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            budget = self.config.connection_shutdown_timeout_seconds
            if self.config.tls_cert_path is not None:
                budget += self.config.tls_handshake_timeout_seconds
            self._close_deadline = asyncio.get_running_loop().time() + budget
            self._stop_resources()
            self.registry.close()
            if self._startup_task is not None and not self._startup_task.done():
                self._startup_task.cancel()
            self._cancel_handlers()
            self._close_task = asyncio.create_task(self._close_connections(), name="webjam-reference-close")
            self._close_task.add_done_callback(self._observe_close)
        # Caller cancellation cannot abandon owned handlers or registry erasure.
        await asyncio.shield(self._close_task)

    def _observe_close(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self._privacy_log("shutdown_failed", component="lifecycle")

    def _stop_resources(self) -> None:
        self._ready = False
        for resource in (self._control_server, self._http_server, self._relay_transport):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    self._retirement_failed = True
        for writer in tuple(self._writers | self._http_writers):
            self._abort_writer(writer)

    def _cancel_handlers(self) -> None:
        for task in tuple(self._connection_tasks):
            task.cancel()
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()

    def _retain_waiter(self, coroutine) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._shutdown_waiters.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self._shutdown_waiters.discard(done)
            if not done.cancelled():
                done.exception()  # Consume even after a timed-out Close caller leaves.

        task.add_done_callback(finished)
        return task

    async def _join_before_deadline(self, tasks: set[asyncio.Task[Any]], *, startup=False) -> None:
        if not tasks:
            return
        assert self._close_deadline is not None
        done, pending = await asyncio.wait(
            tasks, timeout=max(0, self._close_deadline - asyncio.get_running_loop().time())
        )
        if pending:
            # Keep owners reachable. Cancellation-resistant cleanup is failure,
            # never a reason to clear ownership sets and report success.
            for task in pending:
                task.cancel()
            raise RuntimeError("service connection shutdown timed out")
        if not startup:
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    raise RuntimeError("service connection shutdown failed") from None

    async def _close_connections(self) -> None:
        try:
            if self._startup_task is not None:
                await self._join_before_deadline({self._startup_task}, startup=True)
            # A cancellation-resistant creation may have returned a late resource.
            # Join startup first, then take the final resource snapshot.
            self._stop_resources()
            self._cancel_handlers()
            waits: set[asyncio.Task[Any]] = set(self._connection_tasks)
            if self._cleanup_task is not None:
                waits.add(self._cleanup_task)
            for server in (self._control_server, self._http_server):
                if server is not None:
                    waits.add(self._retain_waiter(server.wait_closed()))
            if self._relay_protocol is not None and self._relay_protocol.transport is not None:
                waits.add(self._retain_waiter(self._relay_protocol.wait_closed()))
            await self._join_before_deadline(waits)
            if self._retirement_failed or self._adopted_connections:
                raise RuntimeError("service connection shutdown failed")
            self._cleanup_task = None
            self._relay_transport = None
            self._writers.clear()
            self._http_writers.clear()
            self._privacy_log("stopped")
        finally:
            self._stop_resources()
            self.registry.close()

    def _accept_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._adopt_connection(reader, writer, http=False)

    def _accept_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._adopt_connection(reader, writer, http=True)

    def _adopt_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, http: bool) -> None:
        active = self._active_http_connections if http else self._active_connections
        maximum = self.config.max_http_connections if http else self.config.max_connections
        if self._closed or not self._ready or active >= maximum:
            self._abort_writer(writer)
            return
        self._adopted_connections[writer] = http
        (self._http_writers if http else self._writers).add(writer)
        if http:
            self._active_http_connections += 1
        else:
            self._active_connections += 1
        coroutine = self._handle_http(reader, writer) if http else self._handle_control(reader, writer)
        try:
            task = asyncio.create_task(coroutine, name="webjam-reference-http" if http else "webjam-reference-control")
        except Exception:
            coroutine.close()
            self._release_adopted(writer)
            self._privacy_log("internal_error", component="accept")
            return
        self._connection_tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._connection_tasks.discard(done)
            self._release_adopted(writer)
            if not done.cancelled() and done.exception() is not None:
                self._privacy_log("internal_error", component="connection")

        task.add_done_callback(finished)

    def _release_adopted(self, writer: asyncio.StreamWriter) -> None:
        if writer not in self._adopted_connections:
            return
        http = self._adopted_connections.pop(writer)
        self._abort_writer(writer)
        (self._http_writers if http else self._writers).discard(writer)
        if http:
            self._active_http_connections -= 1
        else:
            self._active_connections -= 1

    @staticmethod
    def _abort_writer(writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            writer.close()
        with contextlib.suppress(Exception):
            writer.transport.abort()

    def _track_connection(self, writer: asyncio.StreamWriter, *, http: bool) -> bool:
        if self._closed:
            self._abort_writer(writer)
            return False
        (self._http_writers if http else self._writers).add(writer)
        task = asyncio.current_task()
        if task is not None:
            self._connection_tasks.add(task)
        return True

    async def _retire_connection(self, writer: asyncio.StreamWriter, *, http: bool) -> None:
        try:
            if self._closed:
                self._abort_writer(writer)
            else:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), self.config.connection_shutdown_timeout_seconds)
                except (OSError, TimeoutError):
                    self._abort_writer(writer)
                except asyncio.CancelledError:
                    self._abort_writer(writer)
                    raise
        finally:
            (self._http_writers if http else self._writers).discard(writer)
            if writer not in self._adopted_connections:
                self._connection_tasks.discard(asyncio.current_task())

    async def _write(self, writer: asyncio.StreamWriter, payload: bytes) -> None:
        if self._closed:
            raise ConnectionError("service closed")
        writer.write(payload)
        await asyncio.wait_for(writer.drain(), self.config.connection_write_timeout_seconds)

    async def __aenter__(self) -> ReferenceService:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self._track_connection(writer, http=False):
            return
        counted = False
        try:
            if writer not in self._adopted_connections:
                if self._active_connections >= self.config.max_connections:
                    await self._write(writer, _response(ok=False, error="overloaded"))
                    return
                self._active_connections += 1
                counted = True
            host_receipt = (
                self._host_admission.identify(writer.get_extra_info("ssl_object"))
                if self._host_admission else None
            )
            bucket = TokenBucket(
                self.config.max_control_ops_per_second,
                self.config.max_control_ops_per_second,
                asyncio.get_running_loop().time,
            )
            operations = 0
            while not self._closed and operations < self.config.max_ops_per_connection:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(), self.config.connection_read_timeout_seconds
                    )
                except TimeoutError:
                    break
                except ValueError:
                    await self._write(writer, _response(ok=False, error="frame_too_large"))
                    break
                if self._closed or not line:
                    break
                operations += 1
                if not bucket.allow():
                    await self._write(writer, _response(ok=False, error="rate_limited"))
                    break
                try:
                    message = parse_control_line(
                        line, self.config.max_control_frame_bytes
                    )
                    response = self._dispatch(message, host_receipt=host_receipt)
                except ProtocolError as exc:
                    response = {"ok": False, "error": exc.code}
                except Exception:
                    # Parser and state details can contain sensitive input.  The
                    # public response and log remain categorical.
                    self._privacy_log("internal_error", component="control")
                    response = {"ok": False, "error": "internal_error"}
                await self._write(writer, _response(**response))
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                await self._retire_connection(writer, http=False)
            finally:
                if counted:
                    self._active_connections -= 1

    def _dispatch(
        self, message: dict[str, Any], *, host_receipt: HostPrincipal | None = None
    ) -> dict[str, object]:
        if self._closed:
            raise ProtocolError("overloaded")
        op = message["op"]
        if op == "register":
            require_exact_fields(
                message,
                {"v", "op", "session", "host_token", "enrollment_token"},
                {"generation", "ttl_seconds"},
            )
            host_principal = (
                self._host_admission.authorize(host_receipt, self._wall_clock())
                if self._host_admission else None
            )
            ttl = self._bounded_int(
                message.get("ttl_seconds", self.config.max_session_ttl_seconds),
                0,
                0x7FFF_FFFF,
            )
            generation = self._bounded_int(message.get("generation", 1), 1, 0xFFFF_FFFF)
            accepted_ttl = self.registry.register(
                decode_fixed(message["session"], SESSION_BYTES),
                decode_fixed(message["host_token"], TOKEN_BYTES),
                decode_fixed(message["enrollment_token"], TOKEN_BYTES),
                generation,
                ttl,
                host_principal=host_principal,
            )
            return {
                "ok": True,
                "generation": generation,
                "participant_limit": 1,
                "ttl_seconds": accepted_ttl,
            }
        if op == "enroll":
            require_exact_fields(
                message,
                {"v", "op", "session", "enrollment_token", "guest_token"},
            )
            remaining = self.registry.enroll(
                decode_fixed(message["session"], SESSION_BYTES),
                decode_fixed(message["enrollment_token"], TOKEN_BYTES),
                decode_fixed(message["guest_token"], TOKEN_BYTES),
            )
            return {"ok": True, "participant_limit": 1, "ttl_seconds": remaining}
        if op == "signal":
            require_exact_fields(
                message,
                {
                    "v",
                    "op",
                    "session",
                    "role",
                    "token",
                    "generation",
                    "sequence",
                    "sealed_payload",
                },
            )
            self.registry.publish_signal(
                decode_fixed(message["session"], SESSION_BYTES),
                Role.from_text(message["role"]),
                decode_fixed(message["token"], TOKEN_BYTES),
                self._bounded_int(message["generation"], 1, 0xFFFF_FFFF),
                self._bounded_int(message["sequence"], 0, 0x7FFF_FFFF_FFFF_FFFF),
                decode_opaque(message["sealed_payload"], self.config.max_signal_bytes),
            )
            return {"ok": True}
        if op == "poll":
            require_exact_fields(
                message,
                {"v", "op", "session", "role", "token", "generation", "sequence"},
            )
            values = self.registry.poll_signals(
                decode_fixed(message["session"], SESSION_BYTES),
                Role.from_text(message["role"]),
                decode_fixed(message["token"], TOKEN_BYTES),
                self._bounded_int(message["generation"], 1, 0xFFFF_FFFF),
                self._bounded_int(message["sequence"], 0, 0x7FFF_FFFF_FFFF_FFFF),
            )
            return {"ok": True, "sealed_payloads": [encode_fixed(v) for v in values]}
        if op == "close":
            require_exact_fields(
                message,
                {"v", "op", "session", "role", "token", "generation", "sequence"},
            )
            self.registry.close_session(
                decode_fixed(message["session"], SESSION_BYTES),
                Role.from_text(message["role"]),
                decode_fixed(message["token"], TOKEN_BYTES),
                self._bounded_int(message["generation"], 1, 0xFFFF_FFFF),
                self._bounded_int(message["sequence"], 0, 0x7FFF_FFFF_FFFF_FFFF),
            )
            return {"ok": True}
        raise ProtocolError("unknown_operation")

    async def _handle_http(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self._track_connection(writer, http=True):
            return
        counted = False
        status = 400
        body: dict[str, object] = {"status": "bad_request"}
        try:
            if writer not in self._adopted_connections:
                if self._active_http_connections >= self.config.max_http_connections:
                    await self._write_http(writer, 503, {"status": "overloaded"})
                    return
                self._active_http_connections += 1
                counted = True
            try:
                request_line = await asyncio.wait_for(reader.readline(), 5)
                header_bytes = len(request_line)
                while True:
                    line = await asyncio.wait_for(reader.readline(), 5)
                    header_bytes += len(line)
                    if header_bytes > 4_096:
                        raise ProtocolError("frame_too_large")
                    if line in (b"\r\n", b"\n", b""):
                        break
                if self._closed:
                    return
                parts = request_line.decode("ascii", "strict").strip().split(" ")
                if (
                    len(parts) != 3
                    or parts[0] != "GET"
                    or not parts[2].startswith("HTTP/1.")
                ):
                    raise ProtocolError("malformed")
                path = parts[1]
                diagnostics = self.registry.diagnostics()
                if path == "/healthz":
                    healthy = diagnostics["status"] == "ok"
                    status = 200 if healthy else 503
                    body = {"status": diagnostics["status"], "v": PROTOCOL_VERSION}
                elif path == "/diagnostics":
                    status = 200
                    body = diagnostics
                else:
                    status = 404
                    body = {"status": "not_found"}
            except (ProtocolError, UnicodeError, TimeoutError):
                pass
            except Exception:
                self._privacy_log("internal_error", component="health")
                status = 500
                body = {"status": "internal_error"}
            await self._write_http(writer, status, body)
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                await self._retire_connection(writer, http=True)
            finally:
                if counted:
                    self._active_http_connections -= 1

    async def _write_http(
        self, writer: asyncio.StreamWriter, status: int, body: Mapping[str, object]
    ) -> None:
        payload = _json_bytes(body)
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            500: "Error",
            503: "Unavailable",
        }[status]
        await self._write(
            writer,
            f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            + payload,
        )

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.cleanup_interval_seconds)
            self.registry.cleanup()

    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.config.tls_cert_path is None:
            return None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        assert self.config.tls_key_path is not None
        context.load_cert_chain(self.config.tls_cert_path, self.config.tls_key_path)
        if self._host_admission is not None:
            context.load_verify_locations(cafile=self.config.host_client_ca_path)
            # A certificate is needed for host registration, not for an invited
            # guest or a fresh role-token-authenticated cleanup connection.
            context.verify_mode = ssl.CERT_OPTIONAL
        return context

    @staticmethod
    def _bounded_int(value: object, minimum: int, maximum: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ProtocolError("malformed")
        if not minimum <= value <= maximum:
            raise ProtocolError("malformed")
        return value

    @staticmethod
    def _server_port(server: asyncio.AbstractServer | OwnedControlListener | None) -> int:
        if server is None or not server.sockets:
            raise RuntimeError("service is not started")
        return int(server.sockets[0].getsockname()[1])

    @staticmethod
    def _privacy_log(event: str, **fields: object) -> None:
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key
            in {
                "active_sessions",
                "component",
                "control_port",
                "http_port",
                "relay_port",
                "tls",
            }
            and isinstance(value, (bool, int, str))
        }
        _LOG.info(
            json.dumps({"event": event, **safe_fields}, separators=(",", ":"), sort_keys=True)
        )

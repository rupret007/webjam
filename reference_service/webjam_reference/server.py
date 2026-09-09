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

from .config import ServiceConfig
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

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[object, ...]) -> None:
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
        self._control_server: asyncio.AbstractServer | None = None
        self._http_server: asyncio.AbstractServer | None = None
        self._relay_transport: asyncio.DatagramTransport | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._http_writers: set[asyncio.StreamWriter] = set()
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._active_connections = 0
        self._active_http_connections = 0
        self._started = False
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
        try:
            ssl_context = self._ssl_context()
            tls_limits = {}
            if ssl_context is not None:
                tls_limits = {
                    "ssl_handshake_timeout": self.config.tls_handshake_timeout_seconds,
                    "ssl_shutdown_timeout": self.config.connection_shutdown_timeout_seconds,
                }
            self._control_server = await asyncio.start_server(
                self._handle_control,
                self.config.control_bind,
                self.config.control_port,
                ssl=ssl_context,
                limit=self.config.max_control_frame_bytes,
                backlog=min(self.config.max_connections, 256),
                **tls_limits,
            )
            loop = asyncio.get_running_loop()
            relay_transport, _ = await loop.create_datagram_endpoint(
                lambda: _RelayProtocol(self),
                local_addr=(self.config.relay_bind, self.config.relay_port),
            )
            self._relay_transport = relay_transport  # type: ignore[assignment]
            self._http_server = await asyncio.start_server(
                self._handle_http,
                self.config.http_bind,
                self.config.http_port,
                limit=4_096,
                backlog=min(self.config.max_http_connections, 128),
            )
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="webjam-reference-cleanup"
            )
        except BaseException:
            await self.close()
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
            self._close_task = asyncio.create_task(self._close_connections(), name="webjam-reference-close")
        # Caller cancellation cannot abandon owned handlers or registry erasure.
        await asyncio.shield(self._close_task)

    async def _close_connections(self) -> None:
        loop = asyncio.get_running_loop()
        budget = self.config.connection_shutdown_timeout_seconds
        if self.config.tls_cert_path is not None:
            # Pending TLS has no StreamWriter yet. Its finite handshake timer
            # is part of this one overall budget, not an extra wait per peer.
            budget += self.config.tls_handshake_timeout_seconds
        deadline = loop.time() + budget
        active = self.registry.session_count
        waits: set[asyncio.Task] = set()
        try:
            for server in (self._control_server, self._http_server):
                if server is not None:
                    server.close()
            if self._relay_transport is not None:
                self._relay_transport.close()
                self._relay_transport = None
            self.registry.close()
            # Retire connected transports before Server.wait_closed, which may
            # itself wait for those clients. No peer can hold close-notify open.
            for writer in tuple(self._writers | self._http_writers):
                self._abort_writer(writer)
            for task in tuple(self._connection_tasks):
                task.cancel()
                waits.add(task)
            if self._cleanup_task is not None:
                self._cleanup_task.cancel()
                waits.add(self._cleanup_task)
            for server in (self._control_server, self._http_server):
                if server is not None:
                    waits.add(asyncio.create_task(server.wait_closed()))
            if waits:
                done, pending = await asyncio.wait(waits, timeout=max(0, deadline - loop.time()))
                for task in done:
                    if not task.cancelled():
                        task.result()
                if pending:
                    for task in pending:
                        task.cancel()
                    # A timed-out join must never report successful teardown.
                    raise RuntimeError("service connection shutdown timed out")
            self._cleanup_task = None
            self._writers.clear()
            self._http_writers.clear()
            self._privacy_log("stopped", active_sessions=active)
        finally:
            for task in waits:
                if not task.done():
                    task.cancel()
            for writer in tuple(self._writers | self._http_writers):
                self._abort_writer(writer)
            self.registry.close()

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
    def _server_port(server: asyncio.AbstractServer | None) -> int:
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

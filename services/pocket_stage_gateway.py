"""Dedicated pinned-WSS gateway for the WebJam Pocket Stage iPhone app.

This service is intentionally separate from ``LocalApiBridge``.  It has one
WebSocket route, no browser-readable HTTP surface, no plaintext fallback, and
does not enter Jamulus's realtime audio path.  The desktop explicitly starts
it, supplies immutable projections, and remains the sole command authority.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import sys
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from core.pocket_stage import (
    MAX_WIRE_MESSAGE_BYTES,
    MobileSessionProjection,
    PairingAcceptanceStatus,
    PairingCapabilityError,
    PairingCapabilityRegistry,
    PairingScope,
    PocketCommandReceipt,
    PocketCommandRejectionReason,
    PocketCommandRequest,
    PocketCommandStatus,
    PocketStageEnvelope,
    PocketStageMessageKind,
    PocketStageProtocolError,
)
from services.pocket_stage_tls import (
    PocketStageTlsError,
    PocketStageTlsIdentity,
    discover_private_lan_ipv4,
    is_rfc1918_ipv4,
    validate_gateway_host,
)


LOGGER = logging.getLogger("webjam.pocket_stage.gateway")
_DEFAULT_SCOPES = (
    PairingScope.OBSERVE,
    PairingScope.CUES,
    PairingScope.MARKERS,
    PairingScope.MIX,
    PairingScope.TRANSPORT,
    PairingScope.RECORD,
)
# The v1 threat model authorizes one owner's iPhone, and the desktop pairing
# UI intentionally presents only one active device state.
_MAX_CONNECTED_CLIENTS = 1
_MAX_COMMANDS_PER_SECOND = 20
_MAX_RATE_LIMIT_VIOLATIONS = 4
_MAX_IDEMPOTENCY_RECEIPTS = 256
_PAIRING_TIMEOUT_SECONDS = 10
_SNAPSHOT_INTERVAL_SECONDS = 0.10
_HEARTBEAT_SECONDS = 1.0
_SEND_TIMEOUT_SECONDS = 2.0
_STOP_TIMEOUT_SECONDS = 5.0


class PocketStageGatewayError(RuntimeError):
    """Safe musician-facing failure to start or operate the gateway."""


@dataclass(frozen=True, slots=True, repr=False)
class PocketStagePairingOffer:
    """Sensitive one-use pairing payload shown only as a desktop QR code."""

    session_id: str
    endpoint: str
    certificate_fingerprint_sha256: str
    capability_id: str
    capability_token: str
    expires_at_unix: float
    display_name: str
    scopes: tuple[PairingScope, ...]

    @property
    def qr_code_text(self) -> str:
        query = urlencode(
            {
                "v": "1",
                "session": self.session_id,
                "endpoint": self.endpoint,
                "token": self.capability_token,
                "fingerprint": self.certificate_fingerprint_sha256,
                "expires": str(int(self.expires_at_unix)),
                "name": self.display_name,
            }
        )
        return f"pocketstage://pair?{query}"

    def __repr__(self) -> str:
        return "PocketStagePairingOffer(private=[redacted])"

    def __str__(self) -> str:
        return "[private Pocket Stage pairing offer]"


class PocketStageGateway:
    """Own one bounded WSS listener and its one-use pairing capabilities."""

    def __init__(
        self,
        *,
        snapshot_provider: Callable[[], MobileSessionProjection],
        command_handler: Callable[
            [PocketCommandRequest, tuple[PairingScope, ...], int, str],
            PocketCommandReceipt,
        ],
        host: str | None = None,
        port: int = 0,
        allow_loopback_for_tests: bool = False,
        clock: Callable[[], float] = time.time,
        pairing_registry: PairingCapabilityRegistry | None = None,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._command_handler = command_handler
        self._configured_host = host
        self._configured_port = int(port)
        if not 0 <= self._configured_port <= 65535:
            raise ValueError("Pocket Stage port must be between 0 and 65535.")
        self._allow_loopback_for_tests = bool(allow_loopback_for_tests)
        self._clock = clock
        self._registry = pairing_registry or PairingCapabilityRegistry(clock=clock)
        self._state_lock = threading.RLock()
        self._offer_lock = threading.Lock()
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._identity: PocketStageTlsIdentity | None = None
        self._host = ""
        self._port = 0
        self._session_id = ""
        self._running = False
        self._starting = False
        self._connected_clients = 0
        self._reserved_connections = 0
        self._connection_epoch = 0
        self._offer_capability_ids: set[str] = set()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._active_websockets: dict[int, Any] = {}
        self._active_command_leases: set[str] = set()
        self._command_completions: deque[
            tuple[int, PocketCommandReceipt]
        ] = deque(maxlen=_MAX_IDEMPOTENCY_RECEIPTS)

    def __repr__(self) -> str:
        return "PocketStageGateway(private=[redacted])"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def connected_clients(self) -> int:
        with self._state_lock:
            return self._connected_clients

    def bound_route_is_current(self) -> bool:
        """Return whether the active listener still matches the OS private route."""

        with self._state_lock:
            if not self._running:
                return True
            host = self._host
            allow_loopback = self._allow_loopback_for_tests
        if allow_loopback and ipaddress.ip_address(host).is_loopback:
            return True
        try:
            return discover_private_lan_ipv4() == host
        except PocketStageTlsError:
            return False

    def start(self) -> bool:
        """Start the secure listener; safe to call repeatedly."""

        with self._state_lock:
            if self._running:
                return True
            if self._starting:
                return False
            if self._thread is not None and self._thread.is_alive():
                raise PocketStageGatewayError(
                    "The previous iPhone connection is still closing. Try again shortly."
                )
            self._connection_epoch += 1
            start_epoch = self._connection_epoch
            self._starting = True

        listener: socket.socket | None = None
        identity: PocketStageTlsIdentity | None = None
        try:
            try:
                from fastapi import FastAPI
                import uvicorn
            except ImportError as exc:
                raise PocketStageGatewayError(
                    "This WebJam build is missing its secure iPhone gateway."
                ) from exc

            selected = self._configured_host or discover_private_lan_ipv4()
            host = validate_gateway_host(
                selected,
                allow_loopback=self._allow_loopback_for_tests,
            )
            identity = PocketStageTlsIdentity.create(host)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, self._configured_port))
            listener.listen(32)
            listener.setblocking(False)
            port = int(listener.getsockname()[1])

            app = self._create_app(FastAPI)
            config = uvicorn.Config(
                app=app,
                host=host,
                port=port,
                log_level="warning",
                # Frozen GUI builds intentionally have no stdout/stderr.
                # Keep Uvicorn from installing console formatters/handlers;
                # its loggers propagate into WebJam's configured file logger.
                log_config=None,
                access_log=False,
                ssl_certfile=str(identity.certificate_path),
                ssl_keyfile=str(identity.private_key_path),
                ws_max_size=MAX_WIRE_MESSAGE_BYTES,
                ws_max_queue=16,
                timeout_keep_alive=5,
            )
            server = uvicorn.Server(config)

            def _run() -> None:
                try:
                    server.run(sockets=[listener])
                except Exception:  # noqa: BLE001 - details may carry addresses
                    LOGGER.error("Pocket Stage gateway stopped unexpectedly")
                finally:
                    self._finalize_runtime(server, listener, identity)

            thread = threading.Thread(
                target=_run,
                name="PocketStageGateway",
                daemon=True,
            )
            with self._state_lock:
                if (
                    not self._starting
                    or self._connection_epoch != start_epoch
                ):
                    raise PocketStageGatewayError(
                        "iPhone sharing stopped before its secure connection opened."
                    )
                self._host = host
                self._port = port
                self._session_id = str(uuid.uuid4())
                self._listener = listener
                self._identity = identity
                self._server = server
                self._thread = thread
                # Publishing the handles and launching are one lifecycle
                # transition. stop() cannot interleave and miss this thread.
                thread.start()
            for _ in range(100):
                if bool(getattr(server, "started", False)):
                    with self._state_lock:
                        if (
                            self._server is not server
                            or not thread.is_alive()
                            or not self._starting
                            or self._connection_epoch != start_epoch
                        ):
                            break
                        self._running = True
                        self._starting = False
                    return True
                if not thread.is_alive():
                    break
                time.sleep(0.02)
            raise PocketStageGatewayError(
                "WebJam could not open a secure iPhone connection on this network."
            )
        except PocketStageGatewayError:
            self._cleanup_failed_start(listener, identity, start_epoch=start_epoch)
            raise
        except PocketStageTlsError as exc:
            self._cleanup_failed_start(listener, identity, start_epoch=start_epoch)
            raise PocketStageGatewayError(str(exc)) from None
        except OSError:
            self._cleanup_failed_start(listener, identity, start_epoch=start_epoch)
            if sys.platform == "darwin":
                recovery = (
                    "Allow WebJam in System Settings → Privacy & Security → Local "
                    "Network and in Network → Firewall → Options, then try again."
                )
            elif sys.platform == "win32":
                recovery = (
                    "In Windows Security → Firewall, allow WebJam on Private "
                    "networks only, then try again."
                )
            else:
                recovery = (
                    "Allow WebJam through this computer's firewall only on the "
                    "trusted private network, then try again."
                )
            raise PocketStageGatewayError(
                "WebJam could not open a secure iPhone connection on this network. "
                + recovery
            ) from None
        except Exception as exc:  # noqa: BLE001 - never expose dependency/raw I/O detail
            self._cleanup_failed_start(listener, identity, start_epoch=start_epoch)
            LOGGER.error(
                "Pocket Stage gateway start failed; exception_type=%s",
                type(exc).__name__,
            )
            raise PocketStageGatewayError(
                "WebJam could not start the secure iPhone connection."
            ) from None

    def _cleanup_failed_start(
        self,
        listener: socket.socket | None,
        identity: PocketStageTlsIdentity | None,
        *,
        start_epoch: int,
    ) -> None:
        with self._state_lock:
            server = self._server
            thread = self._thread
            if self._connection_epoch == start_epoch or self._starting:
                self._starting = False
            self._running = False
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
            if thread.is_alive():
                if listener is not None:
                    try:
                        listener.close()
                    except OSError:
                        pass
                thread.join(timeout=1)
            if thread.is_alive():
                # The server thread may still be reading its TLS identity. Do
                # not delete that identity or lose the handle: start() must
                # fail closed until the thread's own finalizer has run.
                LOGGER.error(
                    "Pocket Stage startup cleanup did not stop its server thread"
                )
                return
        elif listener is not None:
            try:
                listener.close()
            except OSError:
                pass

        # The normal thread finalizer already performs this cleanup. This
        # branch also covers failures before the thread was created.
        with self._state_lock:
            still_owned = self._server is server
            if still_owned:
                self._server = None
                self._thread = None
                self._listener = None
                self._identity = None
                self._host = ""
                self._port = 0
                self._session_id = ""
                self._event_loop = None
                self._active_websockets.clear()
                self._active_command_leases.clear()
                self._command_completions.clear()
        if identity is not None:
            identity.cleanup()

    def _finalize_runtime(
        self,
        server: Any,
        listener: socket.socket,
        identity: PocketStageTlsIdentity,
    ) -> None:
        """Release an exited listener, including unexpected server exits."""

        capability_ids: tuple[str, ...] = ()
        with self._state_lock:
            if self._server is server:
                capability_ids = tuple(self._offer_capability_ids)
                self._offer_capability_ids.clear()
                self._running = False
                self._starting = False
                self._server = None
                self._thread = None
                self._listener = None
                self._identity = None
                self._host = ""
                self._port = 0
                self._session_id = ""
                self._event_loop = None
                self._active_websockets.clear()
                self._active_command_leases.clear()
                self._command_completions.clear()
                self._connected_clients = 0
                self._reserved_connections = 0
        for capability_id in capability_ids:
            try:
                self._registry.revoke(capability_id)
            except PairingCapabilityError:
                pass
        try:
            listener.close()
        except OSError:
            pass
        identity.cleanup()

    def stop(self) -> None:
        """Revoke pairing, disconnect phones, stop WSS, and destroy its key."""

        with self._state_lock:
            self._connection_epoch += 1
            server = self._server
            thread = self._thread
            listener = self._listener
            identity = self._identity
            event_loop = self._event_loop
            active_websockets = tuple(self._active_websockets.values())
            capability_ids = tuple(self._offer_capability_ids)
            self._offer_capability_ids.clear()
            self._command_completions.clear()
            self._active_command_leases.clear()
            self._running = False
        for capability_id in capability_ids:
            try:
                self._registry.revoke(capability_id)
            except PairingCapabilityError:
                pass
        if (
            event_loop is not None
            and event_loop.is_running()
            and active_websockets
        ):
            async def _close_active() -> None:
                async def _close_one(websocket: Any) -> None:
                    try:
                        await asyncio.wait_for(
                            websocket.close(
                                code=1001,
                                reason="Pocket Stage stopped",
                            ),
                            timeout=1.0,
                        )
                    except Exception:  # noqa: BLE001 - shutdown stays bounded
                        pass

                await asyncio.gather(
                    *(_close_one(item) for item in active_websockets)
                )

            future = asyncio.run_coroutine_threadsafe(_close_active(), event_loop)
            try:
                future.result(timeout=2.0)
            except Exception as exc:  # noqa: BLE001 - no peer detail in logs/UI
                LOGGER.warning(
                    "Pocket Stage socket close did not confirm; exception_type=%s",
                    type(exc).__name__,
                )
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=_STOP_TIMEOUT_SECONDS)
            if thread.is_alive():
                # Uvicorn normally owns and closes the supplied socket. Force
                # close only after its graceful deadline has elapsed.
                if listener is not None:
                    try:
                        listener.close()
                    except OSError:
                        pass
                thread.join(timeout=1)
            if thread.is_alive():
                LOGGER.error("Pocket Stage gateway did not stop within its deadline")
                raise PocketStageGatewayError(
                    "WebJam could not fully stop iPhone sharing. Quit WebJam before "
                    "leaving this network."
                )
        elif server is not None and listener is not None and identity is not None:
            self._finalize_runtime(server, listener, identity)

    def complete_pending_command(self, receipt: PocketCommandReceipt) -> None:
        """Queue a late owner-thread result for its authenticated phone.

        The Qt owner can occasionally be busy for longer than the gateway's
        bounded synchronous wait. The initial ``pending`` receipt keeps the
        network loop responsive; this method supplies the later authoritative
        accepted/confirmed/rejected result without replaying the command.
        """

        if receipt.status is PocketCommandStatus.PENDING:
            return
        with self._state_lock:
            if not self._running:
                return
            self._command_completions.append(
                (self._connection_epoch, receipt)
            )

    @contextmanager
    def command_lease(self, epoch: int, lease_id: str) -> Iterator[bool]:
        """Hold revocation behind one already-authenticated UI command."""

        self._state_lock.acquire()
        try:
            yield bool(
                self._running
                and self._connection_epoch == epoch
                and lease_id in self._active_command_leases
            )
        finally:
            self._state_lock.release()

    def issue_pairing_offer(
        self,
        *,
        scopes: Iterable[PairingScope | str] = _DEFAULT_SCOPES,
        ttl_seconds: int = 120,
        display_name: str = "WebJam Pocket Stage",
    ) -> PocketStagePairingOffer:
        """Replace outstanding QR secrets and return one sensitive offer."""

        with self._offer_lock:
            return self._issue_pairing_offer(
                scopes=scopes,
                ttl_seconds=ttl_seconds,
                display_name=display_name,
            )

    def _issue_pairing_offer(
        self,
        *,
        scopes: Iterable[PairingScope | str],
        ttl_seconds: int,
        display_name: str,
    ) -> PocketStagePairingOffer:
        """Issue one offer while the outer issuance lock is held."""

        with self._state_lock:
            if not self._running or self._identity is None:
                raise PocketStageGatewayError(
                    "Start Pocket Stage before creating a pairing code."
                )
            host = self._host
            port = self._port
            session_id = self._session_id
            fingerprint = self._identity.fingerprint_sha256
            certificate_not_after = getattr(
                self._identity,
                "not_after_unix",
                float("inf"),
            )
            identity = self._identity
            epoch = self._connection_epoch
            previous = tuple(self._offer_capability_ids)
            self._offer_capability_ids.clear()
        for capability_id in previous:
            try:
                self._registry.revoke(capability_id)
            except PairingCapabilityError:
                pass
        capability = self._registry.issue(scopes=scopes, ttl_seconds=ttl_seconds)
        # Compare against a fresh raw reading from the registry's own clock.
        # The registry itself retains a nondecreasing high-water mark; using
        # the raw source here detects a forward jump followed by rollback.
        wall_now = float(self._clock())
        actual_lifetime = capability.expires_at_unix - wall_now
        if (
            actual_lifetime <= 0
            or actual_lifetime > float(ttl_seconds) + 1.0
            or capability.expires_at_unix >= certificate_not_after
        ):
            try:
                self._registry.revoke(capability.capability_id)
            except PairingCapabilityError:
                pass
            raise PocketStageGatewayError(
                "This secure iPhone-sharing identity expired or the computer's "
                "clock changed. Stop iPhone Sharing, then open Pocket Stage "
                "again for a fresh secure identity."
            )
        with self._state_lock:
            still_current = bool(
                self._running
                and self._identity is identity
                and self._connection_epoch == epoch
            )
            if still_current:
                self._offer_capability_ids.add(capability.capability_id)
        if not still_current:
            try:
                self._registry.revoke(capability.capability_id)
            except PairingCapabilityError:
                pass
            raise PocketStageGatewayError(
                "iPhone sharing stopped before the pairing code was ready."
            )
        safe_name = unicodedata.normalize(
            "NFC", str(display_name or "WebJam Pocket Stage")
        )
        safe_name = safe_name.encode("utf-8", errors="replace").decode("utf-8")
        safe_name = "".join(
            " " if ord(character) < 32 or ord(character) == 127 else character
            for character in safe_name
        ).strip()
        safe_name = safe_name.encode("utf-8")[:64].decode(
            "utf-8",
            errors="ignore",
        ).strip()
        if not safe_name:
            safe_name = "WebJam Pocket Stage"
        return PocketStagePairingOffer(
            session_id=session_id,
            endpoint=f"wss://{host}:{port}/v1/pocket",
            certificate_fingerprint_sha256=fingerprint,
            capability_id=capability.capability_id,
            capability_token=capability.reveal_for_pairing(),
            expires_at_unix=capability.expires_at_unix,
            display_name=safe_name,
            scopes=capability.scopes,
        )

    def _create_app(self, FastAPI: Any) -> Any:
        from starlette.websockets import WebSocket

        app = FastAPI(
            title="WebJam Pocket Stage Gateway",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )

        async def pocket_socket(websocket) -> None:
            await self._serve_socket(websocket)

        # This module uses postponed annotations, while FastAPI must see the
        # concrete Starlette WebSocket type during route construction rather
        # than interpreting the parameter as a query value.
        pocket_socket.__annotations__["websocket"] = WebSocket
        app.websocket("/v1/pocket")(pocket_socket)

        return app

    def _handshake_allowed(self, websocket: Any) -> bool:
        with self._state_lock:
            host = self._host
            port = self._port
        if websocket.headers.get("origin"):
            return False
        host_header = str(websocket.headers.get("host") or "").lower()
        if host_header not in {host.lower(), f"{host}:{port}".lower()}:
            return False
        peer = getattr(getattr(websocket, "client", None), "host", "")
        if peer == "testclient" and self._allow_loopback_for_tests:
            return True
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            return False
        if address.is_loopback:
            return self._allow_loopback_for_tests
        return is_rfc1918_ipv4(address)

    def _reserve_connection(
        self,
        websocket: Any,
        event_loop: asyncio.AbstractEventLoop,
    ) -> int | None:
        """Atomically bound pending handshakes plus authenticated phones."""

        with self._state_lock:
            if (
                not self._running
                or self._reserved_connections >= _MAX_CONNECTED_CLIENTS
            ):
                return None
            self._reserved_connections += 1
            self._event_loop = event_loop
            self._active_websockets[id(websocket)] = websocket
            return self._connection_epoch

    async def _serve_socket(self, websocket: Any) -> None:
        if not self._handshake_allowed(websocket):
            await websocket.close(code=1008, reason="Pocket Stage connection rejected")
            return
        reservation_epoch = self._reserve_connection(
            websocket,
            asyncio.get_running_loop(),
        )
        if reservation_epoch is None:
            await websocket.close(code=1008, reason="Pocket Stage connection rejected")
            return
        authenticated = False
        command_lease_id: str | None = None
        with self._state_lock:
            still_current = bool(
                self._running
                and reservation_epoch == self._connection_epoch
            )
        try:
            if not still_current:
                await websocket.close(code=1001, reason="Pocket Stage stopped")
                return
            await websocket.accept()
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_PAIRING_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                await websocket.close(code=1008, reason="Pairing timed out")
                return
            envelope = PocketStageEnvelope.from_json(raw)
            if envelope.kind is not PocketStageMessageKind.PAIR:
                await websocket.close(code=1008, reason="Pairing required")
                return
            claim = envelope.body
            acceptance = self._registry.consume(
                claim.capability_for_registry(),  # type: ignore[union-attr]
                claim_id=claim.claim_id,  # type: ignore[union-attr]
            )
            if acceptance.status is not PairingAcceptanceStatus.ACCEPTED:
                await websocket.close(code=1008, reason="Pairing rejected")
                return
            with self._state_lock:
                still_current = bool(
                    self._running
                    and reservation_epoch == self._connection_epoch
                )
                if still_current:
                    self._offer_capability_ids.discard(acceptance.capability_id)
                    self._connected_clients += 1
                    command_lease_id = str(uuid.uuid4())
                    self._active_command_leases.add(command_lease_id)
            if not still_current:
                await websocket.close(code=1001, reason="Pocket Stage stopped")
                return
            assert command_lease_id is not None
            authenticated = True
            await self._authenticated_session(
                websocket,
                acceptance.scopes,
                reservation_epoch,
                command_lease_id,
            )
        except (PairingCapabilityError, PocketStageProtocolError, ValueError):
            await websocket.close(code=1008, reason="Pairing rejected")
            return
        except Exception as exc:  # WebSocketDisconnect varies by Starlette version
            LOGGER.debug("Pocket Stage pairing ended; exception_type=%s", type(exc).__name__)
            return
        finally:
            with self._state_lock:
                self._active_websockets.pop(id(websocket), None)
                self._reserved_connections = max(0, self._reserved_connections - 1)
                if authenticated:
                    self._connected_clients = max(0, self._connected_clients - 1)
                if command_lease_id is not None:
                    self._active_command_leases.discard(command_lease_id)

    async def _authenticated_session(
        self,
        websocket: Any,
        scopes: tuple[PairingScope, ...],
        epoch: int,
        command_lease_id: str,
    ) -> None:
        outbound_sequence = 0
        inbound_sequence = 0
        last_snapshot_json = ""
        last_snapshot_sent_monotonic = 0.0
        command_times: deque[float] = deque()
        violations = 0
        receipts: OrderedDict[
            str, tuple[dict[str, object], PocketCommandReceipt]
        ] = OrderedDict()

        async def send_snapshot(*, force: bool = False) -> None:
            nonlocal outbound_sequence, last_snapshot_json, last_snapshot_sent_monotonic
            projection = self._snapshot_provider()
            projection_json = str(projection.to_dict())
            now_monotonic = time.monotonic()
            if (
                not force
                and projection_json == last_snapshot_json
                and now_monotonic - last_snapshot_sent_monotonic
                < _HEARTBEAT_SECONDS
            ):
                return
            outbound_sequence += 1
            envelope = PocketStageEnvelope(
                kind=PocketStageMessageKind.SNAPSHOT,
                message_id=str(uuid.uuid4()),
                generation=projection.generation,
                sequence=outbound_sequence,
                sent_at_unix_ms=int(self._clock() * 1000),
                body=projection,
            )
            await asyncio.wait_for(
                websocket.send_text(envelope.to_json()),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
            last_snapshot_json = projection_json
            last_snapshot_sent_monotonic = now_monotonic

        async def send_receipt(receipt: PocketCommandReceipt) -> None:
            nonlocal outbound_sequence
            outbound_sequence += 1
            envelope = PocketStageEnvelope(
                kind=PocketStageMessageKind.RECEIPT,
                message_id=str(uuid.uuid4()),
                generation=receipt.generation,
                sequence=outbound_sequence,
                sent_at_unix_ms=int(self._clock() * 1000),
                body=receipt,
            )
            await asyncio.wait_for(
                websocket.send_text(envelope.to_json()),
                timeout=_SEND_TIMEOUT_SECONDS,
            )

        async def send_late_completions() -> None:
            queued: list[PocketCommandReceipt] = []
            with self._state_lock:
                while self._command_completions:
                    completion_epoch, completion = self._command_completions.popleft()
                    if completion_epoch == epoch:
                        queued.append(completion)
            for completion in queued:
                prior = receipts.get(completion.command_id)
                if prior is None:
                    continue
                prior_request, prior_receipt = prior
                if prior_receipt.status is not PocketCommandStatus.PENDING:
                    continue
                receipts[completion.command_id] = (prior_request, completion)
                receipts.move_to_end(completion.command_id)
                await send_receipt(completion)

        with self._state_lock:
            should_stop = epoch != self._connection_epoch or not self._running
        if should_stop:
            await websocket.close(code=1001, reason="Pocket Stage stopped")
            return
        await send_snapshot(force=True)
        receive_task = asyncio.create_task(websocket.receive_text())
        try:
            while True:
                with self._state_lock:
                    should_stop = epoch != self._connection_epoch or not self._running
                if should_stop:
                    await websocket.close(code=1001, reason="Pocket Stage stopped")
                    return
                await send_late_completions()
                done, _pending = await asyncio.wait(
                    {receive_task},
                    timeout=_SNAPSHOT_INTERVAL_SECONDS,
                )
                if receive_task in done:
                    try:
                        raw = receive_task.result()
                    except Exception:
                        return
                    receive_task = asyncio.create_task(websocket.receive_text())
                    try:
                        envelope = PocketStageEnvelope.from_json(raw)
                    except PocketStageProtocolError:
                        await websocket.close(code=1008, reason="Invalid message")
                        return
                    if envelope.kind is not PocketStageMessageKind.COMMAND:
                        await websocket.close(code=1008, reason="Command required")
                        return
                    if envelope.sequence != inbound_sequence + 1:
                        await websocket.close(code=1008, reason="Sequence mismatch")
                        return
                    inbound_sequence = envelope.sequence
                    request = envelope.body
                    if not isinstance(request, PocketCommandRequest):
                        await websocket.close(code=1008, reason="Command required")
                        return

                    current = self._snapshot_provider()
                    request_dict = request.to_dict()
                    prior = receipts.get(request.command_id)
                    if prior is not None:
                        prior_request, prior_receipt = prior
                        if prior_request == request_dict:
                            await send_receipt(prior_receipt)
                        else:
                            await send_receipt(
                                self._rejected(
                                    request,
                                    current,
                                    PocketCommandRejectionReason.INVALID_STATE,
                                )
                            )
                        continue

                    now = self._clock()
                    while command_times and now - command_times[0] >= 1.0:
                        command_times.popleft()
                    command_times.append(now)
                    if len(command_times) > _MAX_COMMANDS_PER_SECOND:
                        violations += 1
                        receipt = self._rejected(
                            request,
                            current,
                            PocketCommandRejectionReason.RATE_LIMITED,
                        )
                        receipts[request.command_id] = (request_dict, receipt)
                        receipts.move_to_end(request.command_id)
                        while len(receipts) > _MAX_IDEMPOTENCY_RECEIPTS:
                            receipts.popitem(last=False)
                        await send_receipt(receipt)
                        if violations >= _MAX_RATE_LIMIT_VIOLATIONS:
                            await websocket.close(code=1008, reason="Rate limit")
                            return
                        continue

                    if request.required_scope not in scopes:
                        receipt = self._rejected(
                            request,
                            current,
                            PocketCommandRejectionReason.UNAUTHORIZED,
                        )
                    elif request.generation != current.generation:
                        receipt = self._rejected(
                            request,
                            current,
                            PocketCommandRejectionReason.STALE_GENERATION,
                        )
                    elif request.expected_revision != current.revision:
                        receipt = self._rejected(
                            request,
                            current,
                            PocketCommandRejectionReason.STALE_REVISION,
                        )
                    else:
                        try:
                            receipt = self._command_handler(
                                request,
                                scopes,
                                epoch,
                                command_lease_id,
                            )
                        except Exception as exc:  # never send raw provider detail
                            LOGGER.error(
                                "Pocket Stage command failed; exception_type=%s",
                                type(exc).__name__,
                            )
                            latest = self._snapshot_provider()
                            receipt = self._rejected(
                                request,
                                latest,
                                PocketCommandRejectionReason.INTERNAL_FAILURE,
                            )
                    receipts[request.command_id] = (request_dict, receipt)
                    receipts.move_to_end(request.command_id)
                    while len(receipts) > _MAX_IDEMPOTENCY_RECEIPTS:
                        receipts.popitem(last=False)
                    await send_receipt(receipt)
                    await send_late_completions()
                    await send_snapshot()
                else:
                    await send_late_completions()
                    await send_snapshot()
        finally:
            if not receive_task.done():
                receive_task.cancel()

    @staticmethod
    def _rejected(
        request: PocketCommandRequest,
        current: MobileSessionProjection,
        reason: PocketCommandRejectionReason,
    ) -> PocketCommandReceipt:
        return PocketCommandReceipt(
            command_id=request.command_id,
            status=PocketCommandStatus.REJECTED,
            generation=current.generation,
            revision=current.revision,
            reason=reason,
        )

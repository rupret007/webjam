from __future__ import annotations

import asyncio
import json
import logging
import shutil
import socket
import ssl
import subprocess
from typing import Any

from webjam_reference.config import ServiceConfig
from webjam_reference.protocol import (
    DatagramKind,
    RelayFrame,
    Role,
    derive_relay_key,
    encode_fixed,
    encode_relay,
    parse_relay,
    verify_relay,
)
from webjam_reference.server import ReferenceService

SESSION = b"s" * 32
HOST = b"h" * 32
ENROLLMENT = b"e" * 32
GUEST = b"g" * 32
SEALED = b"opaque-candidate\x00tag"


def config(**changes: object) -> ServiceConfig:
    values: dict[str, object] = {
        "control_port": 0,
        "relay_port": 0,
        "http_port": 0,
        "min_session_ttl_seconds": 1,
        "max_session_ttl_seconds": 20,
        "idle_timeout_seconds": 10,
        "cleanup_interval_seconds": 0.02,
    }
    values.update(changes)
    return ServiceConfig(**values)


async def exchange(service: ReferenceService, message: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", service.control_port)
    writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return response


def registration() -> dict[str, Any]:
    return {
        "v": 3,
        "op": "register",
        "session": encode_fixed(SESSION),
        "host_token": encode_fixed(HOST),
        "enrollment_token": encode_fixed(ENROLLMENT),
        "generation": 4,
        "ttl_seconds": 20,
    }


def enrollment() -> dict[str, Any]:
    return {
        "v": 3,
        "op": "enroll",
        "session": encode_fixed(SESSION),
        "enrollment_token": encode_fixed(ENROLLMENT),
        "guest_token": encode_fixed(GUEST),
    }


async def http_get(service: ReferenceService, path: str) -> tuple[int, dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", service.http_port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    status_line = await reader.readline()
    status = int(status_line.split()[1])
    length = 0
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, value = line.decode().split(":", 1)
        if name.casefold() == "content-length":
            length = int(value.strip())
    payload = json.loads(await reader.readexactly(length))
    writer.close()
    await writer.wait_closed()
    return status, payload


def test_control_registration_enrollment_and_opaque_signal_round_trip() -> None:
    async def scenario() -> None:
        async with ReferenceService(config()) as service:
            registered = await exchange(service, registration())
            assert registered == {
                "generation": 4,
                "ok": True,
                "participant_limit": 1,
                "ttl_seconds": 20,
                "v": 3,
            }
            assert (await exchange(service, enrollment()))["ok"] is True
            signal = {
                "v": 3,
                "op": "signal",
                "session": encode_fixed(SESSION),
                "role": "host",
                "token": encode_fixed(HOST),
                "generation": 4,
                "sequence": 1,
                "sealed_payload": encode_fixed(SEALED),
            }
            assert (await exchange(service, signal))["ok"] is True
            poll = {
                "v": 3,
                "op": "poll",
                "session": encode_fixed(SESSION),
                "role": "guest",
                "token": encode_fixed(GUEST),
                "generation": 4,
                "sequence": 1,
            }
            response = await exchange(service, poll)
            assert response["sealed_payloads"] == [encode_fixed(SEALED)]
            assert (await exchange(service, poll))["error"] == "replay"
            replay_enrollment = await exchange(service, enrollment())
            assert replay_enrollment["error"] == "enrollment_used"

    asyncio.run(scenario())


def test_control_rejects_downgrade_unknown_fields_malformed_and_oversize() -> None:
    async def scenario() -> None:
        async with ReferenceService(
            config(max_control_frame_bytes=1_024, max_signal_bytes=16)
        ) as service:
            old = await exchange(service, {"v": 2, "op": "register"})
            assert old["error"] == "unsupported_version"
            with_extra = registration() | {"display_name": "must-not-be-accepted"}
            assert (await exchange(service, with_extra))["error"] == "malformed"
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", service.control_port
            )
            writer.write(b"{" + b"x" * 1_100 + b"}\n")
            await writer.drain()
            assert json.loads(await reader.readline())["error"] == "frame_too_large"
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


def test_health_and_diagnostics_are_machine_readable_and_privacy_safe() -> None:
    async def scenario() -> None:
        async with ReferenceService(config()) as service:
            await exchange(service, registration())
            status, health = await http_get(service, "/healthz")
            assert status == 200
            assert health == {"status": "ok", "v": 3}
            status, diagnostics = await http_get(service, "/diagnostics")
            assert status == 200
            assert diagnostics["sessions"]["active"] == 1
            serialized = json.dumps(diagnostics)
            for forbidden in (
                encode_fixed(SESSION),
                encode_fixed(HOST),
                encode_fixed(ENROLLMENT),
                "127.0.0.1",
            ):
                assert forbidden not in serialized
            missing_status, _ = await http_get(service, "/private")
            assert missing_status == 404

    asyncio.run(scenario())


def test_capacity_reports_degraded_and_returns_graceful_overload() -> None:
    async def scenario() -> None:
        async with ReferenceService(config(max_sessions=1)) as service:
            assert (await exchange(service, registration()))["ok"] is True
            another = registration() | {
                "session": encode_fixed(b"t" * 32),
                "host_token": encode_fixed(b"i" * 32),
                "enrollment_token": encode_fixed(b"j" * 32),
            }
            assert (await exchange(service, another))["error"] == "overloaded"
            status, health = await http_get(service, "/healthz")
            assert status == 503
            assert health["status"] == "degraded"

    asyncio.run(scenario())


def test_connection_overload_is_bounded_without_disrupting_existing_client() -> None:
    async def scenario() -> None:
        async with ReferenceService(config(max_connections=1)) as service:
            first_reader, first_writer = await asyncio.open_connection(
                "127.0.0.1", service.control_port
            )
            await asyncio.sleep(0)
            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1", service.control_port
            )
            assert json.loads(await second_reader.readline())["error"] == "overloaded"
            second_writer.close()
            await second_writer.wait_closed()
            first_writer.write(json.dumps(registration()).encode() + b"\n")
            await first_writer.drain()
            assert json.loads(await first_reader.readline())["ok"] is True
            first_writer.close()
            await first_writer.wait_closed()

    asyncio.run(scenario())


def test_health_connection_overload_is_bounded() -> None:
    async def scenario() -> None:
        async with ReferenceService(config(max_http_connections=1)) as service:
            _, first_writer = await asyncio.open_connection(
                "127.0.0.1", service.http_port
            )
            await asyncio.sleep(0)
            status, body = await http_get(service, "/healthz")
            assert status == 503
            assert body == {"status": "overloaded"}
            first_writer.close()
            await first_writer.wait_closed()

    asyncio.run(scenario())


def test_normal_logs_never_include_peer_addresses_or_opaque_credentials(caplog) -> None:
    async def scenario() -> None:
        with caplog.at_level(logging.INFO, logger="webjam_reference"):
            async with ReferenceService(config()) as service:
                await exchange(service, registration())
                relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    relay.sendto(b"malformed", ("127.0.0.1", service.relay_port))
                    await asyncio.sleep(0.01)
                finally:
                    relay.close()

    asyncio.run(scenario())
    output = caplog.text
    for forbidden in (
        "127.0.0.1",
        encode_fixed(SESSION),
        encode_fixed(HOST),
        encode_fixed(ENROLLMENT),
    ):
        assert forbidden not in output


def test_udp_relay_integration_forwards_ciphertext_only_to_registered_peer() -> None:
    async def scenario() -> None:
        async with ReferenceService(config()) as service:
            assert (await exchange(service, registration()))["ok"] is True
            assert (await exchange(service, enrollment()))["ok"] is True
            loop = asyncio.get_running_loop()
            host = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            guest = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rogue = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for sock in (host, guest, rogue):
                sock.bind(("127.0.0.1", 0))
                sock.setblocking(False)
            relay = ("127.0.0.1", service.relay_port)
            try:
                host_bind = encode_relay(
                    RelayFrame(Role.HOST, DatagramKind.BIND, SESSION, 4, 1),
                    derive_relay_key(HOST),
                )
                guest_bind = encode_relay(
                    RelayFrame(Role.GUEST, DatagramKind.BIND, SESSION, 4, 1),
                    derive_relay_key(GUEST),
                )
                await loop.sock_sendto(host, host_bind, relay)
                await loop.sock_sendto(guest, guest_bind, relay)
                await asyncio.sleep(0.03)
                payload = b"opaque-quic-ciphertext"
                data = encode_relay(
                    RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 4, 2, payload),
                    derive_relay_key(HOST),
                )
                await loop.sock_sendto(host, data, relay)
                delivered, source = await asyncio.wait_for(
                    loop.sock_recvfrom(guest, 1_420), 1
                )
                assert source == relay
                frame, body, tag = parse_relay(
                    delivered, 1_420, allow_delivery=True
                )
                assert frame.payload == payload
                assert verify_relay(body, tag, derive_relay_key(GUEST))

                await loop.sock_sendto(host, data, relay)  # replay
                try:
                    await asyncio.wait_for(loop.sock_recvfrom(guest, 1_420), 0.05)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("replayed relay datagram was forwarded")

                moved = encode_relay(
                    RelayFrame(Role.HOST, DatagramKind.DATA, SESSION, 4, 3, payload),
                    derive_relay_key(HOST),
                )
                await loop.sock_sendto(rogue, moved, relay)
                try:
                    await asyncio.wait_for(loop.sock_recvfrom(guest, 1_420), 0.05)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("unregistered endpoint was forwarded")
            finally:
                host.close()
                guest.close()
                rogue.close()

    asyncio.run(scenario())


def test_clean_shutdown_closes_listeners_and_wipes_sessions() -> None:
    async def scenario() -> None:
        service = await ReferenceService(config()).start()
        await exchange(service, registration())
        control_port = service.control_port
        await service.close()
        assert service.registry.session_count == 0
        try:
            await asyncio.open_connection("127.0.0.1", control_port)
        except OSError:
            pass
        else:
            raise AssertionError("control listener remained open")
        await service.close()  # idempotent

    asyncio.run(scenario())


def test_control_listener_supports_tls_1_3(tmp_path) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        return
    certificate = tmp_path / "certificate.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )

    async def scenario() -> None:
        tls_config = config(tls_cert_path=certificate, tls_key_path=key)
        async with ReferenceService(tls_config) as service:
            client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_context.minimum_version = ssl.TLSVersion.TLSv1_3
            client_context.check_hostname = False
            client_context.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                service.control_port,
                ssl=client_context,
                server_hostname="localhost",
            )
            writer.write(json.dumps(registration()).encode() + b"\n")
            await writer.drain()
            assert json.loads(await reader.readline())["ok"] is True
            negotiated = writer.get_extra_info("ssl_object").version()
            assert negotiated == "TLSv1.3"
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())

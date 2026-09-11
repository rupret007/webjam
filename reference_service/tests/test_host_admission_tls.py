"""Real TLS admission, guest compatibility and owned-listener cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
from contextlib import asynccontextmanager

import pytest

from webjam_reference.protocol import encode_fixed
from webjam_reference.server import ReferenceService


def credentials(seed=1):
    return tuple(bytes([seed + offset]) * 32 for offset in (0, 64, 128, 192))


def registration(seed=1):
    session, host, enrollment, _ = credentials(seed)
    return dict(v=3, op="register", session=encode_fixed(session),
                host_token=encode_fixed(host), enrollment_token=encode_fixed(enrollment),
                generation=1, ttl_seconds=20)


def enrollment(seed=1):
    session, _, ticket, guest = credentials(seed)
    return dict(v=3, op="enroll", session=encode_fixed(session),
                enrollment_token=encode_fixed(ticket), guest_token=encode_fixed(guest))


def operation(op, *, role="host", sequence=1, seed=1, **extra):
    session, host, _, guest = credentials(seed)
    return dict(v=3, op=op, session=encode_fixed(session), role=role,
                token=encode_fixed(host if role == "host" else guest),
                generation=1, sequence=sequence) | extra


async def send(connection, request):
    reader, writer = connection
    writer.write(json.dumps(request).encode() + b"\n")
    await asyncio.wait_for(writer.drain(), 1)
    line = await asyncio.wait_for(reader.readline(), 1)
    assert line, "TLS peer closed without a control response"
    return json.loads(line)


async def retire(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1)
    except (ConnectionError, ssl.SSLError, TimeoutError):
        writer.transport.abort()


@asynccontextmanager
async def connected(service, material, name=None):
    connection = await asyncio.wait_for(asyncio.open_connection(
        "127.0.0.1", service.control_port, ssl=material.client_context(name),
        server_hostname="service.test", ssl_handshake_timeout=1,
        ssl_shutdown_timeout=0.3,
    ), 2)
    try:
        tls = connection[1].get_extra_info("ssl_object")
        assert tls.version() == "TLSv1.3"
        assert tls.context.check_hostname and tls.context.verify_mode == ssl.CERT_REQUIRED
        yield connection
    finally:
        await retire(connection[1])


def test_approved_host_invited_guest_and_fresh_certificate_free_close(host_tls_material, caplog):
    material = host_tls_material
    caplog.set_level(logging.INFO, logger="webjam_reference")

    async def scenario():
        async with ReferenceService(material.config()) as service:
            async with connected(service, material, "a") as host:
                assert (await send(host, registration()))["ok"] is True
                async with connected(service, material) as guest:
                    # A server-verified connection is not permission to reserve a room.
                    assert (await send(guest, registration(2)))["error"] == "unauthorized"
                    bad = enrollment() | {"enrollment_token": encode_fixed(b"z" * 32)}
                    assert (await send(guest, bad))["error"] == "invalid_enrollment"
                    assert (await send(guest, enrollment()))["ok"] is True
                    assert (await send(guest, enrollment()))["error"] == "enrollment_used"
                    assert service.registry.session_count == 1

                    payload = encode_fixed(b"opaque-room-offer-with-tag")
                    assert (await send(host, operation("signal", sealed_payload=payload)))["ok"]
                    assert (await send(guest, operation("poll", role="guest")))["sealed_payloads"] == [payload]
                    assert (await send(guest, operation("signal", role="guest", sequence=2,
                                                       sealed_payload=payload)))["ok"]
                    assert (await send(host, operation("poll", sequence=2)))["sealed_payloads"] == [payload]

                    # Another admitted certificate does not replace room authority.
                    async with connected(service, material, "b") as other_host:
                        wrong = operation("close", sequence=3, token=encode_fixed(b"x" * 32))
                        assert (await send(other_host, wrong))["error"] == "unauthorized"
                        assert service.registry.session_count == 1

                    await retire(host[1])
                    async with connected(service, material) as fresh:
                        assert (await send(fresh, registration(3)))["error"] == "unauthorized"
                        assert (await send(fresh, operation("close", sequence=3)))["ok"]
                    assert service.registry.session_count == 0
                    assert (await send(guest, operation("poll", role="guest", sequence=3)))["error"] == "unauthorized"
            diagnostic = json.dumps(service.registry.diagnostics())
            for token in credentials():
                assert encode_fixed(token) not in diagnostic
            for name in ("a", "b"):
                assert name * 32 not in diagnostic
                assert material.clients[name].fingerprint not in diagnostic

    asyncio.run(asyncio.wait_for(scenario(), 8))
    logged = caplog.text
    for token in credentials():
        assert encode_fixed(token) not in logged
    assert str(material.directory) not in logged
    assert material.clients["a"].fingerprint not in logged


@pytest.mark.parametrize("name", [None, "unapproved"])
def test_valid_tls_without_approved_host_cannot_allocate(host_tls_material, name):
    async def scenario():
        async with ReferenceService(host_tls_material.config()) as service:
            async with connected(service, host_tls_material, name) as peer:
                assert (await send(peer, registration()))["error"] == "unauthorized"
                assert service.registry.session_count == 0
                assert service.registry.diagnostics()["totals"]["sessions_registered"] == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("name", ["untrusted", "expired", "future", "wrong-purpose"])
def test_invalid_presented_certificate_fails_tls_before_allocation(host_tls_material, name):
    async def scenario():
        async with ReferenceService(host_tls_material.config()) as service:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(
                    "127.0.0.1", service.control_port,
                    ssl=host_tls_material.client_context(name), server_hostname="service.test",
                    ssl_handshake_timeout=1, ssl_shutdown_timeout=0.3,
                ), 2)
                # TLS 1.3 may report the server's client-certificate rejection
                # on the first read, after open_connection returns locally.
                writer.write(json.dumps(registration()).encode() + b"\n")
                await asyncio.wait_for(writer.drain(), 1)
                assert await asyncio.wait_for(reader.readline(), 1) == b""
            except (ConnectionError, ssl.SSLError):
                pass
            finally:
                if writer is not None:
                    await retire(writer)
            assert service.registry.session_count == 0
            assert service.registry.diagnostics()["totals"]["sessions_registered"] == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("wrong_name", [False, True])
def test_guest_still_verifies_server_identity(host_tls_material, wrong_name):
    async def scenario():
        async with ReferenceService(host_tls_material.config()) as service:
            context = (host_tls_material.client_context() if wrong_name
                       else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.wait_for(asyncio.open_connection(
                    "127.0.0.1", service.control_port, ssl=context,
                    server_hostname="wrong.test" if wrong_name else "service.test",
                    ssl_handshake_timeout=1,
                ), 2)
            assert service.registry.session_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_admission_listener_refuses_tls_1_2(host_tls_material):
    async def scenario():
        async with ReferenceService(host_tls_material.config()) as service:
            context = host_tls_material.client_context("a")
            context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
            with pytest.raises((ConnectionError, ssl.SSLError)):
                await asyncio.wait_for(asyncio.open_connection(
                    "127.0.0.1", service.control_port, ssl=context,
                    server_hostname="service.test", ssl_handshake_timeout=1,
                ), 2)
            assert service.registry.session_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_live_connection_loses_registration_authority_at_certificate_expiry(host_tls_material):
    now = [time.time()]

    async def scenario():
        async with ReferenceService(host_tls_material.config(), wall_clock=lambda: now[0]) as service:
            async with connected(service, host_tls_material, "a") as peer:
                assert (await send(peer, registration()))["ok"]
                now[0] = host_tls_material.clients["a"].expires
                assert (await send(peer, registration(2)))["error"] == "unauthorized"
                assert service.registry.session_count == 1
                # Existing room authority and cleanup are not silently revoked.
                assert (await send(peer, operation("close")))["ok"]
                assert service.registry.session_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_host_quota_follows_verified_certificate_across_connections(host_tls_material):
    async def scenario():
        async with ReferenceService(host_tls_material.config(max_sessions_per_host=1, max_sessions=4)) as service:
            async with connected(service, host_tls_material, "a") as first:
                assert (await send(first, registration()))["ok"]
            async with connected(service, host_tls_material, "b") as second:
                assert (await send(second, registration(2)))["ok"]
            async with connected(service, host_tls_material, "a") as returned:
                assert (await send(returned, registration(3)))["error"] == "overloaded"
                assert service.registry.session_count == 2
                assert (await send(returned, operation("close")))["ok"]
                assert (await send(returned, registration(3)))["ok"]
                assert service.registry.session_count == 2

    asyncio.run(asyncio.wait_for(scenario(), 8))


def test_manifest_replacement_takes_effect_on_restart_and_wipes_old_rooms(host_tls_material):
    async def scenario():
        config = host_tls_material.config()
        async with ReferenceService(config) as first:
            async with connected(first, host_tls_material, "a") as peer:
                assert (await send(peer, registration()))["ok"]
                # Editing the operator file does not claim live revocation.
                config.host_admission_path.write_text(host_tls_material.manifest(("b",)).read_text())
                assert (await send(peer, registration(2)))["ok"]
        assert first.registry.session_count == 0
        async with ReferenceService(config) as replacement:
            async with connected(replacement, host_tls_material, "a") as revoked:
                assert (await send(revoked, registration()))["error"] == "unauthorized"
            async with connected(replacement, host_tls_material, "b") as remaining:
                assert (await send(remaining, registration()))["ok"]
            assert replacement.registry.session_count == 1

    asyncio.run(asyncio.wait_for(scenario(), 8))


def test_wire_identity_fields_cannot_impersonate_host(host_tls_material):
    async def scenario():
        async with ReferenceService(host_tls_material.config()) as service:
            async with connected(service, host_tls_material) as peer:
                for field, value in (("principal", "a" * 32),
                                     ("certificate", host_tls_material.clients["a"].fingerprint)):
                    assert (await send(peer, registration() | {field: value}))["error"] == "malformed"
                    assert service.registry.session_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_shutdown_retires_live_tls_and_unfinished_handshake(host_tls_material):
    async def scenario():
        service = await ReferenceService(host_tls_material.config()).start()
        unfinished_writer = None
        try:
            async with connected(service, host_tls_material, "a") as host:
                assert (await send(host, registration()))["ok"]
                unfinished_reader, unfinished_writer = await asyncio.open_connection("127.0.0.1", service.control_port)
                # The raw TCP client never starts its TLS handshake.
                # Wait for actual service ownership; TCP connect alone can
                # leave the peer in the kernel backlog before bounded polling.
                async def wait_for_pending_handshake():
                    while service._control_server.pending_count != 1:
                        await asyncio.sleep(0.005)
                await asyncio.wait_for(wait_for_pending_handshake(), 1)
                port = service.control_port
                started = time.monotonic()
                await asyncio.wait_for(service.close(), 2)
                assert time.monotonic() - started < 2
                assert service.registry.session_count == 0
                assert await asyncio.wait_for(host[0].read(), 1) == b""
                try:
                    unfinished_reply = await asyncio.wait_for(unfinished_reader.read(), 1)
                except ConnectionResetError:
                    # Aborting an unfinished TLS peer may reset the connection.
                    # Timeout or any protocol bytes must still fail this check.
                    unfinished_reply = b""
                assert unfinished_reply == b""
                try:
                    _, probe_writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 1)
                except OSError:
                    pass
                else:
                    await retire(probe_writer)
                    pytest.fail("Control listener remained open after shutdown")
        finally:
            if unfinished_writer is not None:
                await retire(unfinished_writer)
            with contextlib.suppress(ConnectionError):
                await asyncio.wait_for(service.close(), 2)

    asyncio.run(asyncio.wait_for(scenario(), 6))

"""Real owned-loopback acceptance bounds; no public endpoint or media."""
from __future__ import annotations

import asyncio
import contextlib
import ssl

import pytest

from webjam_reference.server import ReferenceService
from test_host_admission_tls import connected, enrollment, operation, registration, retire, send


async def until(predicate, seconds=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), seconds)


async def raw_peer(service):
    return await asyncio.wait_for(asyncio.open_connection('127.0.0.1', service.control_port), 1)


async def retire_peers(peers):
    for _, writer in peers:
        writer.transport.abort()
    await asyncio.gather(*(retire(writer) for _, writer in peers))


async def assert_closed(reader):
    try:
        response = await asyncio.wait_for(reader.read(), 0.5)
    except ConnectionResetError:
        # An owned abort can reset unread bytes, especially partial HTTP input.
        # Only EOF or reset is closure; a timeout or any reply still fails.
        response = b''
    assert response == b''


def test_pending_tls_cap_refuses_before_handshake_timeout_then_recovers(host_tls_material):
    material = host_tls_material

    async def scenario():
        service = await ReferenceService(material.config(
            max_pending_handshakes=2, tls_handshake_timeout_seconds=3,
        )).start()
        listener = service._control_server
        raw = []
        try:
            raw.append(await raw_peer(service))
            raw.append(await raw_peer(service))
            await until(lambda: listener.pending_count == 2)
            assert service._active_connections == 0 and service.registry.session_count == 0
            # A third peer must be rejected before the two held peers' 3s timer.
            third = await raw_peer(service)
            raw.append(third)
            await assert_closed(third[0])
            assert listener.pending_count == 2
            assert service._active_connections == 0 and service.registry.session_count == 0
            raw[0][1].transport.abort()
            await until(lambda: listener.pending_count == 1)
            async with connected(service, material, 'a') as host:
                assert (await send(host, registration()))['ok']
                async with connected(service, material) as guest:
                    assert (await send(guest, enrollment()))['ok']
                    # Cleanup remains a fresh certificate-free role operation.
                    await retire(host[1])
                    async with connected(service, material) as fresh:
                        assert (await send(fresh, operation('close')))['ok']
                    assert service.registry.session_count == 0
        finally:
            await service.close()
            await retire_peers(raw)
        assert listener.pending_count == listener.owned_task_count == 0
        assert not service._connection_tasks and not service._writers

    asyncio.run(asyncio.wait_for(scenario(), 8))


def test_completed_connection_cap_rejects_new_tls_then_allows_invited_guest(host_tls_material):
    material = host_tls_material

    async def scenario():
        async with ReferenceService(material.config(max_connections=1)) as service:
            async with connected(service, material, 'a') as host:
                assert (await send(host, registration()))['ok']
                assert service._active_connections == 1
                # Rejection occurs before constructing a second TLS connection.
                with pytest.raises((ConnectionError, ssl.SSLError)):
                    async with connected(service, material):
                        pytest.fail('A full control listener accepted another TLS peer')
                assert service._active_connections == 1 and service.registry.session_count == 1
                await retire(host[1])
                await until(lambda: service._active_connections == 0)
            async with connected(service, material) as guest:
                assert (await send(guest, enrollment()))['ok']
            await until(lambda: service._active_connections == 0)
            async with connected(service, material) as fresh:
                assert (await send(fresh, operation('close')))['ok']
            assert service.registry.session_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 6))


def test_close_owns_pending_tls_active_host_and_partial_http(host_tls_material):
    material = host_tls_material

    async def scenario():
        service = await ReferenceService(material.config(
            max_pending_handshakes=8, tls_handshake_timeout_seconds=3,
        )).start()
        listener = service._control_server
        control_port, http_port = service.control_port, service.http_port
        peers = []
        try:
            async with connected(service, material, 'a') as host:
                assert (await send(host, registration()))['ok']
                for _ in range(6):
                    peers.append(await raw_peer(service))
                await until(lambda: listener.pending_count == 6)
                http = await asyncio.open_connection('127.0.0.1', http_port)
                peers.append(http)
                http[1].write(b'GET /healthz HTTP/1.1\r\nX-Incomplete: ')
                await http[1].drain()
                await until(lambda: service._active_http_connections == 1)
                # Owned raw sockets should retire promptly, without waiting for
                # each peer's 3s handshake timer or multiplying that by six.
                closing = asyncio.create_task(service.close())
                await until(lambda: service._closed)
                closing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await closing
                await asyncio.wait_for(service.close(), 1)
                assert service.registry.session_count == 0
                assert service._active_connections == service._active_http_connections == 0
                assert listener.pending_count == listener.owned_task_count == 0
                assert not service._connection_tasks and not service._writers and not service._http_writers
                for reader, _ in peers:
                    await assert_closed(reader)
                await assert_closed(host[0])
            for port in (control_port, http_port):
                with pytest.raises(OSError):
                    unexpected = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 0.5)
                    await retire(unexpected[1])
                    pytest.fail('A listener remained reachable after successful Close')
        finally:
            await service.close()
            await retire_peers(peers)

    asyncio.run(asyncio.wait_for(scenario(), 8))

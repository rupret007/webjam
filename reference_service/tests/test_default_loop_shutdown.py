"""Real local acceptance/Close bursts on the unchanged default event loop.

Owned-set assertions alone missed the original asyncio pre-attach failures.
This checks loop diagnostics and resource destruction as independent evidence.
"""

from __future__ import annotations

import asyncio
import gc
import json
import socket
import sys
import traceback
import warnings

from webjam_reference.config import ServiceConfig
from webjam_reference.server import ReferenceService


async def turns(count):
    for _ in range(count):
        await asyncio.sleep(0)


async def burst_client(port, *, http, observations, responded):
    peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    peer.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.sock_connect(peer, ("127.0.0.1", port)), 1)
        await asyncio.wait_for(loop.sock_sendall(peer, b"GET /healthz HTTP/1.1\r\nX-Incomplete: " if http
                               else b'{"v":3,"op":"unknown"}\n'), 1)
        while True:
            data = await asyncio.wait_for(loop.sock_recv(peer, 1024), 1)
            if not data:
                return
            observations.append(http)
            responded.set()
    except ConnectionError:
        return
    finally:
        peer.close()


def test_default_loop_acceptance_close_bursts_retire_resources_without_diagnostics(monkeypatch):
    diagnostics, unraisable, observations = [], [], []
    monkeypatch.setattr(sys, "unraisablehook", lambda event: unraisable.append(type(event.exc_value).__name__))

    async def scenario():
        loop = asyncio.get_running_loop()
        assert loop.get_task_factory() is None  # No held task, runtime patch or alternate scheduling.
        loop.set_debug(True)
        loop.set_exception_handler(lambda _loop, context: diagnostics.append({
            "exception": type(context.get("exception")).__name__,
            "has_transport": "transport" in context,
            "has_protocol": "protocol" in context,
        }))
        established = 0
        for index in range(24):
            service = await ReferenceService(ServiceConfig(
                control_port=0, relay_port=0, http_port=0,
                connection_shutdown_timeout_seconds=0.5,
                connection_write_timeout_seconds=0.2,
            )).start()
            tasks, fixed_peer, closing = [], None, None
            responded = asyncio.Event()
            try:
                # Half the cases prove an actual handler before creating seven
                # competing accepts; the other half race all eight cold entries.
                count = 8
                if index % 2:
                    fixed_peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    fixed_peer.setblocking(False)
                    await asyncio.wait_for(loop.sock_connect(fixed_peer, ("127.0.0.1", service.control_port)), 1)
                    await asyncio.wait_for(loop.sock_sendall(fixed_peer, b'{"v":3,"op":"unknown"}\n'), 1)
                    reply = bytearray()
                    while b"\n" not in reply:
                        part = await asyncio.wait_for(loop.sock_recv(fixed_peer, 1024), 1)
                        assert part and len(reply) + len(part) <= 1024
                        reply.extend(part)
                    assert json.loads(reply)["error"] == "unknown_operation"
                    established += 1
                    count -= 1
                for number in range(count):
                    http = bool(number % 2)
                    tasks.append(asyncio.create_task(burst_client(
                        service.http_port if http else service.control_port,
                        http=http, observations=observations, responded=responded)))
                await turns((0, 1, 2, 3, 5, 8)[index % 6])
                if index >= 12:
                    # Exercise both sides of an ordinary poll interval too;
                    # immediate pre-poll refusal alone is insufficient evidence.
                    await asyncio.sleep((0.009, 0.010, 0.012)[index % 3])
                if index == 23:
                    await asyncio.wait_for(responded.wait(), 1)
                    async def http_admitted():
                        while service._active_http_connections == 0:
                            await asyncio.sleep(0.001)
                    await asyncio.wait_for(http_admitted(), 1)
                if index % 3 == 2:
                    for task in tasks[::2]:
                        task.cancel()
                closing = asyncio.create_task(service.close())
                if index % 4 == 3:
                    await turns(1)
                    closing.cancel()
                    await service.close()
                else:
                    await closing
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1.5)
                assert all(result is None or isinstance(result, asyncio.CancelledError) for result in results), [
                    (type(result).__name__, getattr(result, "errno", None),
                     [(frame.name, frame.lineno) for frame in traceback.extract_tb(result.__traceback__)]) for result in results
                    if result is not None and not isinstance(result, asyncio.CancelledError)]
                assert service.registry.session_count == 0
                assert service._active_connections == service._active_http_connections == 0
                assert not service._connection_tasks and not service._adopted_connections
                assert not service._writers and not service._http_writers and not service._shutdown_waiters
                assert service._relay_transport is None and service._relay_protocol.closed.done()
                for listener in (service._control_server, service._http_server):
                    assert listener.owned_task_count == listener.pending_count == 0
                    assert not listener.poll_pending and not listener.sockets
                assert not [task for task in asyncio.all_tasks() if not task.done()
                            and "_accept_connection2" in task.get_coro().__qualname__]
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if fixed_peer is not None:
                    fixed_peer.close()
                if closing is not None:
                    await asyncio.gather(closing, return_exceptions=True)
                await asyncio.wait_for(service.close(), 1)
            await turns(3)
            gc.collect()
            await turns(3)
        assert established == 12
        assert observations, "No burst client reached an actual application response"
        assert loop.get_task_factory() is None
        await turns(5)
        gc.collect()
        await turns(5)

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("error", ResourceWarning)
        asyncio.run(scenario(), debug=True)
        gc.collect()
    assert not diagnostics, diagnostics
    assert not unraisable, unraisable
    assert not seen, [(item.category.__name__, str(item.message)) for item in seen]

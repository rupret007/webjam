"""Connection ownership checks; no external endpoint, media or certificate setup."""

from __future__ import annotations

import asyncio
import json

import pytest

from webjam_reference.config import ServiceConfig
from webjam_reference.protocol import ProtocolError, encode_fixed
from webjam_reference.server import ReferenceService


def bounded_config(**changes) -> ServiceConfig:
    values = {
        "control_port": 0, "relay_port": 0, "http_port": 0,
        "tls_handshake_timeout_seconds": 0.05,
        "connection_write_timeout_seconds": 0.025,
        "connection_shutdown_timeout_seconds": 0.025,
    }
    values.update(changes)
    return ServiceConfig(**values)


def registration() -> dict[str, object]:
    return {"v": 3, "op": "register", "session": encode_fixed(b"s" * 32),
            "host_token": encode_fixed(b"h" * 32), "enrollment_token": encode_fixed(b"e" * 32)}


class HeldWriter:
    def __init__(self, *, block_drain=False, block_close=False):
        self.block_drain = block_drain
        self.block_close = block_close
        self.released = asyncio.Event()
        self.writes: list[bytes] = []
        self.closed = False
        self.aborted = False
        self.transport = self

    def write(self, data):
        self.writes.append(bytes(data))

    async def drain(self):
        if self.block_drain:
            await self.released.wait()

    def close(self):
        self.closed = True

    async def wait_closed(self):
        if self.block_close:
            await self.released.wait()

    def abort(self):
        self.aborted = True
        self.closed = True
        self.released.set()

    def get_extra_info(self, name, default=None):
        return default


async def completed_within(task, writers, seconds=0.35):
    done, _ = await asyncio.wait({task}, timeout=seconds)
    finished = bool(done)
    if not finished:
        for writer in writers:
            writer.abort()
        task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    return finished, result[0]


@pytest.mark.parametrize("case", ["control", "control-overload", "oversize", "rate",
                                 "http", "http-overload"])
def test_every_reply_path_bounds_a_nonreading_peer(case):
    async def scenario():
        service = ReferenceService(bounded_config(max_control_ops_per_second=1))
        reader = asyncio.StreamReader(limit=64 if case == "oversize" else 16384)
        writer = HeldWriter(block_drain=True, block_close=True)
        if case.startswith("http"):
            if case == "http-overload":
                service._active_http_connections = service.config.max_http_connections
            reader.feed_data(b"GET /healthz HTTP/1.1\r\n\r\n")
            callback = service._handle_http
        else:
            if case == "control-overload":
                service._active_connections = service.config.max_connections
            if case == "oversize":
                reader.feed_data(b"x" * 100 + b"\n")
            else:
                reader.feed_data(json.dumps(registration()).encode() + b"\n")
                if case == "rate":
                    # First drain passes, so the second reply exercises the
                    # explicit rate-limit branch rather than normal dispatch.
                    async def after_first_reply():
                        if len(writer.writes) > 1:
                            await writer.released.wait()
                    writer.drain = after_first_reply
                    reader.feed_data(json.dumps(registration()).encode() + b"\n")
            callback = service._handle_control
        reader.feed_eof()
        task = asyncio.create_task(callback(reader, writer))
        finished, result = await completed_within(task, [writer])
        service.registry.close()
        assert finished, f"{case} ignored the connection write/close deadline"
        assert result is None
        assert writer.writes and writer.closed and writer.aborted
        assert writer not in service._writers | service._http_writers

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["control", "http"])
def test_peer_close_is_bounded_after_successful_reply(kind):
    async def scenario():
        service = ReferenceService(bounded_config())
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps(registration()).encode() + b"\n" if kind == "control"
                         else b"GET /healthz HTTP/1.1\r\n\r\n")
        reader.feed_eof()
        writer = HeldWriter(block_close=True)
        callback = service._handle_control if kind == "control" else service._handle_http
        finished, result = await completed_within(asyncio.create_task(callback(reader, writer)), [writer])
        service.registry.close()
        assert finished, "A peer refusing close held a completed response indefinitely"
        assert result is None
        assert writer.aborted and writer.writes

    asyncio.run(scenario())


def test_close_retires_writers_before_waiting_for_server_and_wipes_registry():
    async def scenario():
        service = ReferenceService(bounded_config())
        assert service._dispatch(registration())["ok"]
        writers = [HeldWriter(block_close=True) for _ in range(8)]
        service._writers.update(writers)
        retirement_observations = []

        class HeldServer:
            def close(self):
                pass

            async def wait_closed(self):
                # Observe the ordering directly. Spawning eight already-ready
                # Event tasks adds scheduler work to a 25ms ordering fixture.
                retired = tuple(writer.aborted and writer.released.is_set() for writer in writers)
                retirement_observations.append(retired)
                assert all(retired), "Listener join began before owned peers were aborted"

        service._control_server = HeldServer()
        finished, result = await completed_within(asyncio.create_task(service.close()), writers)
        remaining = service.registry.session_count
        service.registry.close()
        assert finished, "Listener retirement waited for peers before closing them"
        assert result is None
        assert all(writer.aborted for writer in writers)
        assert retirement_observations == [(True,) * len(writers)]
        assert remaining == 0 and not service._writers and not service._http_writers
        await service.close()

    asyncio.run(scenario())


def test_closed_service_refuses_new_dispatch_and_late_connection():
    async def scenario():
        service = ReferenceService(bounded_config())
        await service.close()
        with pytest.raises(ProtocolError):
            service._dispatch(registration())
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps(registration()).encode() + b"\n")
        reader.feed_eof()
        writer = HeldWriter()
        await service._handle_control(reader, writer)
        assert writer.aborted and not writer.writes
        assert service.registry.session_count == 0

    asyncio.run(scenario())


def test_plain_open_control_and_partial_http_connections_do_not_hold_shutdown(monkeypatch):
    async def scenario():
        # This successful real-I/O case uses the product's normal budgets.
        # Deliberately short held-peer/timeout cases keep bounded_config().
        service = ReferenceService(ServiceConfig(control_port=0, relay_port=0, http_port=0))
        outer_timeout = service.config.connection_shutdown_timeout_seconds + 1.0
        header_read_pending = asyncio.Event()
        header_tasks = []
        original_accept_http = service._accept_http

        def observe_http(reader, writer):
            original_readline = reader.readline
            calls = 0

            async def observed_readline():
                nonlocal calls
                calls += 1
                if calls == 2:
                    # The first real request-line read completed. Continue the
                    # real second read with no complete header/terminator sent.
                    header_tasks.append(asyncio.current_task())
                    header_read_pending.set()
                return await original_readline()

            monkeypatch.setattr(reader, "readline", observed_readline)
            return original_accept_http(reader, writer)

        monkeypatch.setattr(service, "_accept_http", observe_http)
        client_writers = []
        try:
            await service.start()
            control_reader, control_writer = await asyncio.open_connection("127.0.0.1", service.control_port)
            client_writers.append(control_writer)
            http_reader, http_writer = await asyncio.open_connection("127.0.0.1", service.http_port)
            client_writers.append(http_writer)
            control_writer.write(json.dumps(registration()).encode() + b"\n")
            await control_writer.drain()
            assert json.loads(await asyncio.wait_for(control_reader.readline(), outer_timeout))["ok"]
            http_writer.write(b"GET /healthz HTTP/1.1\r\nX-Incomplete: ")
            await http_writer.drain()
            await asyncio.wait_for(header_read_pending.wait(), outer_timeout)
            assert service._active_connections == service._active_http_connections == 1
            assert len(header_tasks) == 1 and not header_tasks[0].done()
            listeners = (service._control_server, service._http_server)
            assert all(listener is not None for listener in listeners)
            accepted = tuple(gate.socket for listener in listeners for gate in listener._connections)
            handlers = tuple(service._connection_tasks)
            assert len(accepted) == 2 and all(sock.fileno() >= 0 for sock in accepted)
            assert len(handlers) == 2 and all(not task.done() for task in handlers)
            close_started = asyncio.get_running_loop().time()
            closing = asyncio.create_task(service.close())
            done, _ = await asyncio.wait({closing}, timeout=outer_timeout)
            if not done:
                for writer in client_writers:
                    writer.transport.abort()
                closing.cancel()
            result = (await asyncio.gather(closing, return_exceptions=True))[0]
            remaining = service.registry.session_count
            service.registry.close()
            retirement = {
                "budget_seconds": service.config.connection_shutdown_timeout_seconds,
                "elapsed_seconds": asyncio.get_running_loop().time() - close_started,
                "open_accepted_fds": sum(sock.fileno() >= 0 for sock in accepted),
                "unfinished_handlers": sum(not task.done() for task in handlers),
                "listeners": [
                    {"kind": kind, "pending": listener.pending_count,
                     "owned_tasks": listener.owned_task_count,
                     "open_fds": sum(gate.socket.fileno() >= 0 for gate in listener._connections),
                     "attaching": sum(gate.attaching for gate in listener._connections),
                     "join_done": listener._join_task is not None and listener._join_task.done()}
                    for kind, listener in zip(("control", "http"), listeners)
                ],
            }
            assert done, retirement
            assert result is None, retirement
            assert remaining == 0 and service._active_connections == service._active_http_connections == 0
            assert not service._writers and not service._http_writers
            assert not service._connection_tasks and not service._adopted_connections
            assert all(task.done() for task in (*handlers, *header_tasks))
            assert all(sock.fileno() == -1 for sock in accepted)
            for listener in listeners:
                assert listener.pending_count == listener.owned_task_count == 0
                assert not listener.poll_pending and not listener._connections and not listener._setups
            for reader in (control_reader, http_reader):
                try:
                    remainder = await asyncio.wait_for(reader.read(), outer_timeout)
                except ConnectionResetError:
                    # Immediate owned abort may reset unread partial HTTP data.
                    # Both reset and EOF prove refusal; neither may carry a reply.
                    remainder = b""
                assert remainder == b""
        finally:
            for writer in client_writers:
                writer.transport.abort()
            if client_writers:
                await asyncio.wait_for(asyncio.gather(
                    *(writer.wait_closed() for writer in client_writers), return_exceptions=True), outer_timeout)
            await asyncio.wait_for(service.close(), outer_timeout)

    asyncio.run(scenario())

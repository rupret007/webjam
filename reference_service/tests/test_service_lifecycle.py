"""Deterministic ownership boundaries without real sockets or media."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from webjam_reference.config import ServiceConfig
from webjam_reference.server import ReferenceService
from webjam_reference import server as server_module


class Writer:
    def __init__(self):
        self.transport = self
        self.aborted = False
        self.writes = []

    def close(self):
        pass

    def abort(self):
        self.aborted = True

    async def wait_closed(self):
        pass

    async def drain(self):
        pass

    def write(self, value):
        self.writes.append(value)

    def get_extra_info(self, *_args):
        return None


class CreationBoundary:
    """A creation API may finish despite cancellation and return a late resource."""

    def __init__(self, monkeypatch, *, hold=None, fail=None, resist_cancel=True):
        self.hold, self.fail, self.resist_cancel = hold, fail, resist_cancel
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.created = {}
        self.callbacks = {}
        self.binds = {}
        self.wait_gate = None
        self.wait_entered = asyncio.Event()
        boundary = self

        class Listener:
            def __init__(self, kind, callback):
                self.kind, self.callback = kind, callback
                self.closed = False
                self.sockets = (SimpleNamespace(getsockname=lambda: ("127.0.0.1", 10001)),)
                boundary.callbacks[kind] = callback

            async def start(self):
                return await boundary.create(self.kind, self)

            async def start_serving(self):
                pass

            def close(self):
                self.closed = True

            async def wait_closed(self):
                assert self.closed, "Wait cannot precede listener retirement"
                boundary.wait_entered.set()
                if boundary.wait_gate is not None:
                    await boundary.wait_gate.wait()

        class Datagram:
            def __init__(self):
                self.closed = False
                self.protocol = None

            def close(self):
                self.closed = True
                if self.protocol is not None:
                    self.protocol.connection_lost(None)

            def get_extra_info(self, *_args):
                return ("127.0.0.1", 10002)

        async def stream_server(callback, host, _port, **kwargs):
            kind = "http" if kwargs["limit"] == 4096 else "control"
            self.binds[kind] = host
            return await self.create(kind, Listener(kind, callback))

        async def datagram_endpoint(factory, *, local_addr):
            self.binds["relay"] = local_addr[0]
            transport = await self.create("relay", Datagram())
            protocol = factory()
            transport.protocol = protocol
            protocol.connection_made(transport)
            return transport, protocol

        def owned_listener(config, _ssl, callback, *, active_count, **kwargs):
            kind = "http" if kwargs.get("read_limit") == 4096 else "control"
            self.binds[kind] = kwargs.get("bind", config.control_bind)
            if kind == "control":
                self.active_count = active_count
            return Listener(kind, callback)

        monkeypatch.setattr(server_module, "OwnedControlListener", owned_listener, raising=False)
        monkeypatch.setattr(asyncio, "start_server", stream_server)
        monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", datagram_endpoint)

    async def create(self, kind, resource):
        self.created[kind] = resource
        if kind == self.hold:
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    if not self.resist_cancel:
                        resource.close()
                        raise
        if kind == self.fail:
            resource.close()
            raise OSError("synthetic bind failure")
        return resource

    def dispose(self):
        self.release.set()
        if self.wait_gate is not None:
            self.wait_gate.set()
        for resource in self.created.values():
            resource.close()


def config(**kwargs):
    return ServiceConfig(control_port=0, relay_port=0, http_port=0,
                         connection_shutdown_timeout_seconds=0.06, **kwargs)


async def turns(count=4):
    for _ in range(count):
        await asyncio.sleep(0)


@pytest.mark.parametrize("stage", ["control", "relay", "http"])
def test_close_during_creation_joins_startup_and_retires_late_resource(monkeypatch, stage):
    async def scenario():
        boundary = CreationBoundary(monkeypatch, hold=stage)
        service = ReferenceService(config())
        starting = asyncio.create_task(service.start())
        await asyncio.wait_for(boundary.entered.wait(), 0.3)
        closing = asyncio.create_task(service.close())
        try:
            await turns()
            premature_success = closing.done() and closing.exception() is None
            boundary.release.set()
            start_result, close_result = await asyncio.wait_for(
                asyncio.gather(starting, closing, return_exceptions=True), 0.3)
            assert not premature_success, "Close succeeded while creation still owned a late resource"
            assert isinstance(start_result, (asyncio.CancelledError, RuntimeError))
            assert close_result is None
            assert all(item.closed for item in boundary.created.values())
            assert service._closed and not service._connection_tasks
            with pytest.raises(RuntimeError):
                await service.start()
        finally:
            boundary.dispose()
            await asyncio.gather(starting, closing, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["control", "relay", "http"])
def test_canceling_start_caller_cannot_abandon_partial_or_late_creation(monkeypatch, stage):
    async def scenario():
        boundary = CreationBoundary(monkeypatch, hold=stage)
        service = ReferenceService(config())
        starting = asyncio.create_task(service.start())
        await asyncio.wait_for(boundary.entered.wait(), 0.3)
        try:
            starting.cancel()
            await turns()
            boundary.release.set()
            result = (await asyncio.wait_for(asyncio.gather(starting, return_exceptions=True), 0.3))[0]
            assert isinstance(result, asyncio.CancelledError), "Canceled startup unexpectedly reported success"
            await service.close()
            assert all(item.closed for item in boundary.created.values())
            assert service._closed and service.registry.session_count == 0
        finally:
            boundary.dispose()
            await asyncio.gather(starting, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["relay", "http"])
def test_partial_bind_failure_retires_prior_listeners_without_close_deadlock(monkeypatch, stage):
    async def scenario():
        boundary = CreationBoundary(monkeypatch, fail=stage)
        service = ReferenceService(config())
        with pytest.raises(OSError, match="synthetic bind failure"):
            await asyncio.wait_for(service.start(), 0.3)
        assert all(item.closed for item in boundary.created.values())
        await asyncio.wait_for(service.close(), 0.3)
        with pytest.raises(RuntimeError):
            await service.start()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["control", "http"])
def test_callback_reserves_capacity_and_cleans_canceled_before_start_handler(monkeypatch, kind):
    async def scenario():
        boundary = CreationBoundary(monkeypatch)
        service = await ReferenceService(config(max_connections=1, max_http_connections=1)).start()
        writers = [Writer(), Writer()]
        legacy_tasks = []
        try:
            for writer in writers:
                result = boundary.callbacks[kind](asyncio.StreamReader(), writer)
                if inspect.isawaitable(result):
                    legacy_tasks.append(asyncio.create_task(result))  # asyncio's former callback behavior
            active = service._active_http_connections if kind == "http" else service._active_connections
            assert active == 1, "Accepted callbacks did not reserve capacity synchronously"
            assert writers[1].aborted and len(service._connection_tasks) == 1
            for task in tuple(service._connection_tasks):
                task.cancel()  # Its coroutine has never started.
            await turns()
            assert writers[0].aborted and not writers[0].writes
            assert not service._writers and not service._http_writers and not service._connection_tasks
            assert service._active_connections == service._active_http_connections == 0
            await service.close()
            late = Writer()
            boundary.callbacks[kind](asyncio.StreamReader(), late)
            assert late.aborted and not late.writes and not service._connection_tasks
        finally:
            for task in legacy_tasks:
                task.cancel()
            await asyncio.gather(*legacy_tasks, return_exceptions=True)
            for writer in writers:
                writer.abort()
            await service.close()
    asyncio.run(scenario())


def test_concurrent_close_and_canceled_waiter_share_one_owned_teardown(monkeypatch):
    async def scenario():
        boundary = CreationBoundary(monkeypatch)
        service = await ReferenceService(config()).start()
        boundary.wait_gate = asyncio.Event()
        first = asyncio.create_task(service.close())
        await asyncio.wait_for(boundary.wait_entered.wait(), 0.3)
        second = asyncio.create_task(service.close())
        first.cancel()
        await turns()
        assert service._close_task is not first and not service._close_task.done()
        assert not second.done()
        boundary.wait_gate.set()
        assert isinstance((await asyncio.gather(first, return_exceptions=True))[0], asyncio.CancelledError)
        await asyncio.wait_for(second, 0.3)
        assert all(item.closed for item in boundary.created.values())
        assert service.registry.session_count == 0
    asyncio.run(scenario())


def test_shutdown_timeout_reports_failure_and_retains_unfinished_startup_owner(monkeypatch):
    async def scenario():
        boundary = CreationBoundary(monkeypatch, hold="http")
        service = ReferenceService(config())
        starting = asyncio.create_task(service.start())
        await asyncio.wait_for(boundary.entered.wait(), 0.3)
        try:
            with pytest.raises(RuntimeError, match="shutdown timed out"):
                await asyncio.wait_for(service.close(), 0.3)
            assert service._startup_task is not None and not service._startup_task.done()
            assert service.registry.session_count == 0
        finally:
            boundary.dispose()
            await asyncio.gather(starting, return_exceptions=True)
    asyncio.run(scenario())


def test_localhost_http_and_relay_never_require_executor_resolution(monkeypatch):
    async def scenario():
        boundary = CreationBoundary(monkeypatch)
        service = await ReferenceService(config(control_bind="LOCALHOST", relay_bind="localhost", http_bind="LocalHost")).start()
        try:
            assert boundary.binds["relay"] == boundary.binds["http"] == "127.0.0.1"
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["control", "http"])
def test_task_creation_refusal_releases_reserved_writer_and_capacity(monkeypatch, kind):
    async def scenario():
        boundary = CreationBoundary(monkeypatch)
        service = await ReferenceService(config()).start()
        writer = Writer()
        original = asyncio.create_task

        def refuse_handler(coroutine, **kwargs):
            if kwargs.get("name") == "webjam-reference-" + kind:
                raise RuntimeError("synthetic task creation refusal")
            return original(coroutine, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(asyncio, "create_task", refuse_handler)
                assert boundary.callbacks[kind](asyncio.StreamReader(), writer) is None
            assert writer.aborted and not writer.writes
            assert service._active_connections == service._active_http_connections == 0
            assert not service._connection_tasks and not service._adopted_connections
            assert not service._writers and not service._http_writers
        finally:
            await service.close()
    asyncio.run(scenario())


def test_close_before_start_cannot_create_any_resource(monkeypatch):
    async def scenario():
        boundary = CreationBoundary(monkeypatch)
        service = ReferenceService(config())
        await service.close()
        with pytest.raises(RuntimeError):
            await service.start()
        await service.close()
        assert not boundary.created and service.registry.session_count == 0
    asyncio.run(scenario())


def test_no_control_adoption_before_startup_commits_and_no_second_start(monkeypatch):
    async def scenario():
        boundary = CreationBoundary(monkeypatch, hold="http")
        service = ReferenceService(config())
        starting = asyncio.create_task(service.start())
        await asyncio.wait_for(boundary.entered.wait(), 0.3)
        try:
            with pytest.raises(RuntimeError):
                await service.start()
            writer = Writer()
            boundary.callbacks["control"](asyncio.StreamReader(), writer)
            assert writer.aborted and not writer.writes
            assert service._active_connections == 0 and not service._connection_tasks
            boundary.release.set()
            assert await asyncio.wait_for(starting, 0.3) is service
            assert service._ready and set(boundary.created) == {"control", "http", "relay"}
        finally:
            boundary.dispose()
            await service.close()
            await asyncio.gather(starting, return_exceptions=True)
    asyncio.run(scenario())

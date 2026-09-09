"""Real socket ownership and controlled public TLS-upgrade boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import gc
import socket
import ssl
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from webjam_reference.config import ServiceConfig
from webjam_reference.control_listener import OwnedControlListener


def config(**changes):
    values = dict(control_port=0, relay_port=0, http_port=0,
                  max_pending_handshakes=2, control_accepts_per_second=100,
                  control_accept_burst=100)
    values.update(changes)
    return ServiceConfig(**values)


def run(coroutine):
    async def checked():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        try:
            await coroutine
        finally:
            gc.collect()
            await asyncio.sleep(0)
            assert len(errors) == 0, "unhandled asyncio callback/future exception"
    asyncio.run(checked())


async def until(predicate):
    async def waiting():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(waiting(), 1)


async def retired(listener):
    listener.close()
    await asyncio.wait_for(listener.wait_closed(), 1)
    assert listener.pending_count == listener.owned_task_count == 0
    assert not listener.sockets and not listener._connections and not listener.poll_pending


async def drop_peer(peer):
    peer[1].close()
    with contextlib.suppress(ConnectionError):
        await peer[1].wait_closed()


async def peer_closed(reader):
    try:
        data = await asyncio.wait_for(reader.read(), 1)
    except ConnectionError:
        return
    assert data == b""


class HeldUpgrade:
    """Holds start_tls before creating SSL; never claims a real TLS handshake."""

    def __init__(self):
        self.transports = []
        self.protocols = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, transport, protocol, context, **options):
        del context
        assert options["server_side"] is True
        assert not transport.is_reading()
        self.transports.append(transport)
        self.protocols.append(protocol)
        self.started.set()
        await self.release.wait()
        raise OSError("controlled upgrade refusal")


def fake_context():
    return ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)


def test_pending_cap_rejects_without_starting_another_tls_task(monkeypatch):
    async def scenario():
        setup = HeldUpgrade()
        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", setup)
        callbacks = []
        listener = await OwnedControlListener(config(), fake_context(),
                                             lambda r, w: callbacks.append(w), active_count=lambda: 0).start()
        peers = []
        try:
            port = listener.sockets[0].getsockname()[1]
            for _ in range(2):
                peers.append(await asyncio.open_connection("127.0.0.1", port))
            await until(lambda: len(setup.transports) == 2)
            assert listener.pending_count == listener.owned_task_count == 2
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await peer_closed(peers[-1][0])
            assert len(setup.transports) == 2 and not callbacks
            assert listener.pending_count == listener.owned_task_count == 2
        finally:
            await retired(listener)
            for peer in peers:
                await peer_closed(peer[0])
                await drop_peer(peer)
        assert all(item.get_extra_info("socket").fileno() == -1 for item in setup.transports)

    run(scenario())


def test_actual_failed_tls_releases_slot_without_listener_restart():
    async def scenario():
        callbacks = []
        listener = await OwnedControlListener(config(max_pending_handshakes=1), fake_context(),
                                             lambda r, w: callbacks.append(w), active_count=lambda: 0).start()
        peers = []
        try:
            port = listener.sockets[0].getsockname()[1]
            for _ in range(3):
                peer = await asyncio.open_connection("127.0.0.1", port)
                peers.append(peer)
                peer[1].write(b"GET /not-tls HTTP/1.1\r\n\r\n")
                await peer[1].drain()
                await peer_closed(peer[0])
                await until(lambda: listener.pending_count == listener.owned_task_count == 0)
                assert not listener._closed and not callbacks
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await until(lambda: listener.pending_count == 1)
            assert listener.owned_task_count == 1
        finally:
            await retired(listener)
            for peer in peers:
                await drop_peer(peer)

    run(scenario())


@pytest.mark.parametrize("ending", ["timeout", "peer_disconnect"])
def test_actual_unfinished_tls_releases_slot_for_a_later_peer(ending):
    async def scenario():
        callbacks = []
        listener = await OwnedControlListener(
            config(max_pending_handshakes=1, tls_handshake_timeout_seconds=0.04),
            fake_context(), lambda r, w: callbacks.append(w), active_count=lambda: 0).start()
        peers = []
        try:
            port = listener.sockets[0].getsockname()[1]
            peer = await asyncio.open_connection("127.0.0.1", port)
            peers.append(peer)
            await until(lambda: listener.pending_count == 1)
            if ending == "peer_disconnect":
                await drop_peer(peer)
            else:
                await peer_closed(peer[0])
            await until(lambda: listener.pending_count == listener.owned_task_count == 0)
            assert not callbacks and not listener._closed
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await until(lambda: listener.pending_count == 1)
            assert listener.owned_task_count == 1
        finally:
            await retired(listener)
            for peer in peers:
                await drop_peer(peer)

    run(scenario())


def test_rate_flood_closes_promptly_and_exact_refill_admits(monkeypatch):
    async def scenario():
        setup = HeldUpgrade()
        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", setup)
        now = [5.0]
        listener = await OwnedControlListener(config(max_pending_handshakes=8, control_accepts_per_second=2,
                                                      control_accept_burst=2), fake_context(),
                                             lambda r, w: None, active_count=lambda: 0, clock=lambda: now[0]).start()
        peers = []

        async def connect():
            peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            peers.append(peer)
            return peer

        try:
            await connect()
            await connect()
            await until(lambda: len(setup.transports) == 2)
            for _ in range(12):
                await peer_closed((await connect())[0])
                assert listener.owned_task_count == listener.pending_count == 2
            now[0] += 0.25
            await peer_closed((await connect())[0])
            assert len(setup.transports) == 2
            now[0] += 0.25
            await connect()
            await until(lambda: len(setup.transports) == 3)
            assert listener.pending_count == listener.owned_task_count == 3
        finally:
            await retired(listener)
            for peer in peers:
                await drop_peer(peer)

    run(scenario())


def test_promoted_reservation_blocks_more_clients_before_handler_runs():
    async def scenario():
        writers = []
        listener = await OwnedControlListener(config(max_connections=1), None,
                                             lambda r, w: writers.append(w), active_count=lambda: 0).start()
        peers = []
        try:
            port = listener.sockets[0].getsockname()[1]
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await until(lambda: len(writers) == 1)
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await peer_closed(peers[-1][0])
            assert len(writers) == 1
            writers[0].close()
            await writers[0].wait_closed()
            await until(lambda: listener.pending_count == 0 and not listener._connections)
            peers.append(await asyncio.open_connection("127.0.0.1", port))
            await until(lambda: len(writers) == 2)
            writers[-1].write(b"owned\n")
            await writers[-1].drain()
            assert await asyncio.wait_for(peers[-1][0].readline(), 1) == b"owned\n"
        finally:
            await retired(listener)
            for peer in peers:
                await drop_peer(peer)

    run(scenario())


def test_upgrade_failure_before_ssl_construction_closes_owned_transport(monkeypatch):
    async def scenario():
        owned = []

        async def failed(transport, *args, **options):
            owned.append(transport)
            raise ValueError("controlled construction failure before TLS ownership")

        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", failed)
        listener = await OwnedControlListener(config(), fake_context(),
                                             lambda r, w: None, active_count=lambda: 0).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await until(lambda: listener._closed)
            with pytest.raises(RuntimeError, match="^control listener ownership failed$"):
                await asyncio.wait_for(listener.wait_closed(), 1)
            assert all(item.get_extra_info("socket").fileno() == -1 for item in owned)
            assert listener.owned_task_count == listener.pending_count == 0
            await peer_closed(peer[0])
        finally:
            await drop_peer(peer)
            listener.close()
            with contextlib.suppress(RuntimeError):
                await listener.wait_closed()

    run(scenario())


def test_plain_attach_construction_failure_retires_owned_raw_socket(monkeypatch):
    async def scenario():
        owned = []

        async def failed(factory, accepted, **options):
            del factory, options
            owned.append(accepted)
            raise ValueError("controlled plain transport construction failure")

        monkeypatch.setattr(asyncio.get_running_loop(), "connect_accepted_socket", failed)
        listener = await OwnedControlListener(config(), None, lambda r, w: None, active_count=lambda: 0).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await until(lambda: listener._closed)
            with pytest.raises(RuntimeError, match="^control listener ownership failed$"):
                await asyncio.wait_for(listener.wait_closed(), 1)
            assert len(owned) == 1 and owned[0].fileno() == -1
            assert listener.pending_count == listener.owned_task_count == 0
            assert not listener.poll_pending
            await peer_closed(peer[0])
        finally:
            await drop_peer(peer)
            listener.close()
            with contextlib.suppress(RuntimeError):
                await listener.wait_closed()

    run(scenario())


def test_close_retains_plain_attach_until_transport_is_published(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        original = loop.connect_accepted_socket
        entered, release = asyncio.Event(), asyncio.Event()
        canceled, owned, callbacks = [], [], []

        async def held(factory, accepted, **options):
            owned.append(accepted)
            entered.set()
            try:
                await release.wait()
                return await original(factory, accepted, **options)
            except asyncio.CancelledError:
                canceled.append(True)
                raise

        monkeypatch.setattr(loop, "connect_accepted_socket", held)
        listener = await OwnedControlListener(config(), None,
                                             lambda r, w: callbacks.append(w), active_count=lambda: 0).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await entered.wait()
            listener.close()
            waiting = asyncio.create_task(listener.wait_closed())
            await asyncio.sleep(0)
            assert not waiting.done() and not canceled and owned[0].fileno() >= 0
            release.set()
            await asyncio.wait_for(waiting, 1)
            assert owned[0].fileno() == -1 and not canceled and not callbacks
            assert listener.pending_count == listener.owned_task_count == 0
            await peer_closed(peer[0])
        finally:
            release.set()
            await retired(listener)
            await drop_peer(peer)

    run(scenario())


def test_setup_task_factory_failure_closes_raw_socket_without_leaking_coroutine(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        original = loop.create_task

        def refused(coroutine, **kwargs):
            if coroutine.cr_code.co_name == "_attach_and_upgrade":
                raise RuntimeError("controlled task creation refusal")
            return original(coroutine, **kwargs)

        monkeypatch.setattr(loop, "create_task", refused)
        accepted, remote = socket.socketpair()
        listener = OwnedControlListener(config(), None, lambda r, w: None, active_count=lambda: 0)
        try:
            listener._admit(accepted)
            assert accepted.fileno() == -1 and listener.owned_task_count == 0
            with pytest.raises(RuntimeError, match="^control listener ownership failed$"):
                await listener.wait_closed()
            assert listener.pending_count == 0 and not listener.poll_pending
        finally:
            accepted.close()
            remote.close()

    run(scenario())


def test_one_poll_bounds_total_batch_and_serves_listener_families_fairly():
    async def scenario():
        remote_peers = []

        class ReadyListener:
            def __init__(self):
                self.accepts = 0
                self.closed = False

            def accept(self):
                accepted, peer = socket.socketpair()
                remote_peers.append(peer)
                self.accepts += 1
                return accepted, ("synthetic", 0)

            def close(self):
                self.closed = True

        first, second = ReadyListener(), ReadyListener()
        listener = OwnedControlListener(config(max_pending_handshakes=64), None,
                                         lambda r, w: None, active_count=lambda: 0)
        listener._listeners.extend((first, second))
        try:
            listener._poll()
            assert first.accepts == second.accepts == 8
            assert listener.pending_count == listener.owned_task_count == 16
            assert listener.poll_pending
            await retired(listener)  # all setups canceled before first entry
            assert first.closed and second.closed
        finally:
            await retired(listener)
            for peer in remote_peers:
                peer.close()

    run(scenario())


def test_close_before_setup_task_starts_and_late_raw_accept(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        setups = []

        async def forbidden(*args, **kwargs):
            setups.append(True)
            raise AssertionError("retired accepted socket reached async setup")

        monkeypatch.setattr(loop, "connect_accepted_socket", forbidden)
        listener = OwnedControlListener(config(), fake_context(), lambda r, w: None, active_count=lambda: 0)
        accepted, remote = socket.socketpair()
        late, late_remote = socket.socketpair()
        try:
            listener._admit(accepted)
            assert listener.pending_count == listener.owned_task_count == 1
            listener.close()  # admitted raw socket owned, task not yet entered
            await retired(listener)
            assert accepted.fileno() == -1 and not setups
            listener._admit(late)  # defensively reject a callback after Close
            assert late.fileno() == -1 and not setups
            assert listener.pending_count == listener.owned_task_count == 0
        finally:
            accepted.close()
            remote.close()
            late.close()
            late_remote.close()
            await retired(listener)

    run(scenario())


def test_canceling_waiter_preserves_owned_upgrade_until_it_exits(monkeypatch):
    async def scenario():
        entered, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def held(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                canceled.set()
                await release.wait()
                raise

        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", held)
        listener = await OwnedControlListener(config(), fake_context(), lambda r, w: None, active_count=lambda: 0).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await entered.wait()
            listener.close()
            await canceled.wait()
            waiting = asyncio.create_task(listener.wait_closed())
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert listener.pending_count == 1 and listener.owned_task_count >= 1
            await peer_closed(peer[0])
            release.set()
            await asyncio.wait_for(listener.wait_closed(), 1)
            assert listener.pending_count == listener.owned_task_count == 0
        finally:
            release.set()
            await retired(listener)
            await drop_peer(peer)

    run(scenario())


@pytest.mark.parametrize("early_size", [7, 16385])
def test_early_decrypted_bytes_are_bounded_and_not_dispatched_before_upgrade(monkeypatch, early_size):
    async def scenario():
        gate_ready, release = asyncio.Event(), asyncio.Event()
        callbacks, owned = [], []

        async def held(transport, protocol, context, **options):
            del context, options
            owned.append(transport)
            protocol.data_received(b"x" * early_size)  # controlled post-decrypt/pre-await-return seam
            gate_ready.set()
            await release.wait()
            return transport  # synthetic successful upgrade; no real TLS claim

        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", held)
        listener = await OwnedControlListener(config(), fake_context(),
                                             lambda r, w: callbacks.append((r, w)), active_count=lambda: 0).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await gate_ready.wait()
            assert not callbacks
            assert all(len(gate.early) <= 16384 for gate in listener._connections)
            release.set()
            if early_size <= 16384:
                await until(lambda: bool(callbacks))
                assert await asyncio.wait_for(callbacks[0][0].readexactly(early_size), 1) == b"x" * early_size
            else:
                await peer_closed(peer[0])
                await until(lambda: listener.pending_count == 0)
                assert not callbacks
        finally:
            release.set()
            await retired(listener)
            await drop_peer(peer)

    run(scenario())


def test_active_capacity_rechecked_after_successful_upgrade(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        active, callbacks = [0], []

        async def held(transport, *args, **kwargs):
            entered.set()
            await release.wait()
            return transport  # controlled verified-promotion seam only

        monkeypatch.setattr(asyncio.get_running_loop(), "start_tls", held)
        listener = await OwnedControlListener(config(max_connections=1), fake_context(),
                                             lambda r, w: callbacks.append(w), active_count=lambda: active[0]).start()
        peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            await entered.wait()
            active[0] = 1
            release.set()
            await peer_closed(peer[0])
            assert not callbacks
        finally:
            release.set()
            await retired(listener)
            await drop_peer(peer)

    run(scenario())


@pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "LOCALHOST"])
def test_literal_and_localhost_binding_do_not_resolve_and_share_one_port(monkeypatch, bind):
    async def scenario():
        loop = asyncio.get_running_loop()

        async def forbidden(*args, **kwargs):
            raise AssertionError("listener attempted DNS resolution")

        monkeypatch.setattr(loop, "getaddrinfo", forbidden)
        accepted, peers = [], []
        listener = OwnedControlListener(config(control_bind=bind), None,
                                         lambda r, w: accepted.append((r, w)), active_count=lambda: 0)
        try:
            try:
                await listener.start()
            except OSError:
                if bind == "::1" and not socket.has_ipv6:
                    pytest.skip("IPv6 is unavailable on this runtime")
                raise
            assert len({item.getsockname()[1] for item in listener.sockets}) == 1
            families = {item.family for item in listener.sockets}
            if bind == "LOCALHOST":
                assert socket.AF_INET in families and families <= {socket.AF_INET, socket.AF_INET6}
            elif bind == "::1":
                assert families == {socket.AF_INET6}
                assert listener.sockets[0].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
            for index, bound in enumerate(listener.sockets, start=1):
                address = bound.getsockname()
                peer = await asyncio.open_connection(address[0], address[1], family=bound.family)
                peers.append(peer)
                await until(lambda: len(accepted) == index)
                server_reader, server_writer = accepted[-1]
                peer[1].write(b"guest lab line\n")
                await peer[1].drain()
                assert await asyncio.wait_for(server_reader.readline(), 1) == b"guest lab line\n"
                server_writer.write(b"host lab line\n")
                await server_writer.drain()
                assert await asyncio.wait_for(peer[0].readline(), 1) == b"host lab line\n"
        finally:
            await retired(listener)
            for _, writer in accepted:
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
            for peer in peers:
                await peer_closed(peer[0])
                await drop_peer(peer)

    run(scenario())


def test_localhost_falls_back_only_when_ipv6_is_unavailable(monkeypatch):
    async def scenario():
        original = socket.socket

        def factory(family, *args, **kwargs):
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, "controlled IPv6 unavailable")
            return original(family, *args, **kwargs)

        monkeypatch.setattr(socket, "socket", factory)
        listener = OwnedControlListener(config(control_bind="localhost"), None,
                                         lambda r, w: None, active_count=lambda: 0)
        await listener.start()
        assert [item.family for item in listener.sockets] == [socket.AF_INET]
        await retired(listener)
        explicit = OwnedControlListener(config(control_bind="::1"), None,
                                         lambda r, w: None, active_count=lambda: 0)
        with pytest.raises(OSError, match="^control listener bind failed$"):
            await explicit.start()
        await retired(explicit)

    run(scenario())


def test_partial_bind_failure_retires_all_created_sockets(monkeypatch):
    async def scenario():
        original = socket.socket
        made = []

        class FakeSocket:
            def __init__(self, family, *args):
                self.family = family
                self.closed = False
                made.append(self)

            def setblocking(self, value):
                pass

            def setsockopt(self, *args):
                pass

            def bind(self, address):
                if self.family == socket.AF_INET:
                    raise OSError(errno.EADDRINUSE, "controlled occupied port")

            def listen(self, backlog):
                pass

            def getsockname(self):
                return "::1", 40001

            def close(self):
                self.closed = True

        monkeypatch.setattr(socket, "socket", FakeSocket)
        listener = OwnedControlListener(config(control_bind="localhost"), None,
                                         lambda r, w: None, active_count=lambda: 0)
        with pytest.raises(OSError, match="^control listener bind failed$"):
            await listener.start()
        assert len(made) == 2 and all(item.closed for item in made)
        assert not listener.sockets
        await retired(listener)
        monkeypatch.setattr(socket, "socket", original)

    run(scenario())


@pytest.mark.parametrize("bind", ["example.invalid", "::1%lo0", "localhoſt"])
def test_invalid_name_refused_before_socket_creation(monkeypatch, bind):
    async def scenario():
        values = asdict(config())
        values["control_bind"] = bind

        def forbidden(*args, **kwargs):
            raise AssertionError("invalid bind reached socket creation")

        monkeypatch.setattr(socket, "socket", forbidden)
        with pytest.raises(ValueError):
            OwnedControlListener(SimpleNamespace(**values), None,
                                 lambda r, w: None, active_count=lambda: 0)

    run(scenario())


def test_close_before_start_and_repeated_close_are_single_use():
    async def scenario():
        listener = OwnedControlListener(config(), None, lambda r, w: None, active_count=lambda: 0)
        await retired(listener)
        await retired(listener)
        with pytest.raises(RuntimeError, match="only be started once"):
            await listener.start()

    run(scenario())


def test_custom_task_factory_keeps_admitted_socket_owned(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        created = []
        adopted = []

        def custom(loop, coroutine, context=None):
            created.append(True)
            return asyncio.Task(coroutine, loop=loop, context=context)

        listener = OwnedControlListener(config(), None,
                                         lambda r, w: adopted.append((r, w)), active_count=lambda: 0)
        loop.set_task_factory(custom)
        peer = None
        try:
            await listener.start()
            peer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            await until(lambda: bool(adopted))
            peer[1].write(b"custom scheduler lab\n")
            await peer[1].drain()
            assert await asyncio.wait_for(adopted[0][0].readline(), 1) == b"custom scheduler lab\n"
            assert created
            await retired(listener)
            await peer_closed(peer[0])
        finally:
            loop.set_task_factory(previous)
            await retired(listener)
            if peer is not None:
                await drop_peer(peer)

    run(scenario())


def test_callback_failure_is_safe_and_does_not_detach_a_handler():
    async def scenario():
        def broken(reader, writer):
            raise ValueError("private callback detail must not escape")

        listener = await OwnedControlListener(config(), None, broken, active_count=lambda: 0).start()
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            assert await asyncio.wait_for(reader.read(), 1) == b""
            with pytest.raises(RuntimeError, match="^control listener ownership failed$"):
                await asyncio.wait_for(listener.wait_closed(), 1)
            assert listener.owned_task_count == listener.pending_count == 0
            assert not listener._connections
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            with contextlib.suppress(RuntimeError):
                await listener.wait_closed()

    run(scenario())

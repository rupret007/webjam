"""Userspace 127.0.0.1:{control,relay} <-> public rendezvous. Not router DNAT.

The compiled sidecar dials only 127.0.0.1:47131/47132. Each peer namespace has
its own loopback, so the harness forwards those sockets to the public bind of
the reference service (which itself stays on loopback, as required by
ServiceConfig). UDP clients keep distinct inner sockets so the exact-pair
relay still sees two peers.
"""

from __future__ import annotations

import argparse
import select
import signal
import socket
import threading
import time
from pathlib import Path

from tools.cross_network_acceptance.topology import RENDEZVOUS_TCP, RENDEZVOUS_UDP

_UDP_MAX = 2048


class LoopbackShim:
    def __init__(
        self, listen_host: str, dest_host: str, count_path: Path | None = None,
        source_host: str = "",
    ) -> None:
        self.listen_host = listen_host
        self.dest_host = dest_host
        self.source_host = source_host
        self.count_path = count_path
        self.bytes = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._udp_lock = threading.Lock()
        self._socks: list[socket.socket] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((self.listen_host, RENDEZVOUS_TCP))
        tcp.listen(8)
        tcp.settimeout(0.5)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
        udp.bind((self.listen_host, RENDEZVOUS_UDP))
        self._socks.extend((tcp, udp))
        self._spawn(self._accept_tcp, tcp)
        self._spawn(self._udp_outer, udp)

    def stop(self) -> None:
        self._stop.set()
        for sock in self._socks:
            try:
                sock.close()
            except OSError:
                pass
        self._flush()

    def _spawn(self, target: object, *args: object) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _add(self, n: int) -> None:
        with self._lock:
            self.bytes += n
        self._flush()

    def _flush(self) -> None:
        if self.count_path is None:
            return
        with self._lock:
            payload = str(self.bytes)
        try:
            self.count_path.write_text(payload + "\n", encoding="utf-8")
        except OSError:
            return

    def _accept_tcp(self, listener: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._socks.append(client)
            self._spawn(self._tcp_client, client)

    def _tcp_client(self, client: socket.socket) -> None:
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socks.append(upstream)
        try:
            upstream.settimeout(8)
            upstream.connect((self.dest_host, RENDEZVOUS_TCP))
            upstream.settimeout(None)
            client.settimeout(None)
            self._spawn(self._pipe, client, upstream)
            self._pipe(upstream, client)
        except OSError:
            pass
        finally:
            for sock in (client, upstream):
                try:
                    sock.close()
                except OSError:
                    pass

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([src], [], [], 0.5)
                if not ready:
                    continue
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _udp_outer(self, outer: socket.socket) -> None:
        if self.dest_host == "127.0.0.1":
            inner_host = "127.0.0.1"
        else:
            inner_host = self.source_host or "0.0.0.0"
        dest = (self.dest_host, RENDEZVOUS_UDP)
        table: dict[tuple[str, int], socket.socket] = {}
        while not self._stop.is_set():
            ready, _, _ = select.select([outer], [], [], 0.5)
            if not ready:
                continue
            try:
                payload, remote = outer.recvfrom(_UDP_MAX)
            except OSError:
                return
            self._add(len(payload))
            inner = table.get(remote)
            if inner is None:
                inner = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                inner.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
                inner.bind((inner_host, 0))
                inner.connect(dest)
                self._socks.append(inner)
                table[remote] = inner
                self._spawn(self._udp_inner, inner, outer, remote)
            try:
                inner.send(payload)
            except OSError:
                continue

    def _udp_inner(
        self, inner: socket.socket, outer: socket.socket, remote: tuple[str, int],
    ) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([inner], [], [], 0.5)
            if not ready:
                continue
            try:
                payload = inner.recv(_UDP_MAX)
            except OSError:
                return
            if not payload:
                return
            self._add(len(payload))
            try:
                with self._udp_lock:
                    outer.sendto(payload, remote)
            except OSError:
                continue


def serve(listen_host: str, dest_host: str, count_path: Path | None) -> int:
    shim = LoopbackShim(listen_host, dest_host, count_path)

    def handle(_signum: int, _frame: object) -> None:
        shim.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    shim.start()
    while True:
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--count-file", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        return serve(args.listen, args.dest, args.count_file)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Placeholder rendezvous / relay / STUN listeners in the public namespace.

Ordinary Host/Join does not use these endpoints today. They exist so the
harness can prove both homes can reach the simulated internet, and so a
future v3 profile can bind here without changing the topology.
"""

from __future__ import annotations

import select
import socket
import sys
import time


def serve(bind_host: str, tcp_port: int, udp_ports: tuple[int, ...]) -> None:
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind((bind_host, tcp_port))
    tcp.listen(8)
    sockets: list[socket.socket] = [tcp]
    for port in udp_ports:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind((bind_host, port))
        sockets.append(udp)
    try:
        while True:
            readable, _, _ = select.select(sockets, (), (), 1.0)
            for item in readable:
                if item is tcp:
                    conn, _addr = item.accept()
                    try:
                        conn.sendall(b"ok\n")
                    finally:
                        conn.close()
                else:
                    payload, addr = item.recvfrom(256)
                    if payload:
                        item.sendto(payload[:8] or b"ok", addr)
    finally:
        for item in sockets:
            item.close()


def probe_tcp(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as conn:
            conn.settimeout(timeout_s)
            conn.recv(8)
        return True
    except OSError:
        return False


def probe_udp(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(b"hi", (host, port))
        sock.recvfrom(16)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def wait_tcp(host: str, port: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe_tcp(host, port, timeout_s=0.4):
            return True
        time.sleep(0.1)
    return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        return 2
    host = args[0]
    tcp_port = int(args[1])
    udp_ports = tuple(int(item) for item in args[2:])
    serve(host, tcp_port, udp_ports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

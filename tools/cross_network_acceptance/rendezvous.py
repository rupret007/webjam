"""Placeholder rendezvous / relay listeners in the public namespace.

Ordinary Host/Join does not use these endpoints today. They exist so the
harness can prove both homes can reach the simulated internet.
"""

from __future__ import annotations

import select
import socket
import sys


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

"""RFC 5389 STUN Binding server for the simulated public internet."""

import os
import select
import shutil
import socket
import struct
import sys
from pathlib import Path

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
HEADER_BYTES = 20
IPV4_FAMILY = 0x01


class StunError(RuntimeError):
    pass


def xor_mapped_attribute(ip: str, port: int) -> bytes:
    address = socket.inet_aton(ip)
    xport = port ^ (MAGIC_COOKIE >> 16)
    xip = bytes(octet ^ cookie for octet, cookie in zip(address, MAGIC_COOKIE.to_bytes(4, "big")))
    value = struct.pack("!BBH4s", 0, IPV4_FAMILY, xport, xip)
    return struct.pack("!HH", XOR_MAPPED_ADDRESS, len(value)) + value


def binding_success(request: bytes, src_ip: str, src_port: int) -> bytes:
    if len(request) < HEADER_BYTES:
        raise StunError("short request")
    message_type, length, cookie = struct.unpack_from("!HHI", request, 0)
    if message_type != BINDING_REQUEST or cookie != MAGIC_COOKIE:
        raise StunError("not a binding request")
    if length != len(request) - HEADER_BYTES:
        raise StunError("bad length")
    txid = request[8:HEADER_BYTES]
    attribute = xor_mapped_attribute(src_ip, src_port)
    header = struct.pack("!HHI", BINDING_SUCCESS, len(attribute), MAGIC_COOKIE) + txid
    return header + attribute


def parse_xor_mapped(response: bytes) -> tuple[str, int]:
    if len(response) < HEADER_BYTES:
        raise StunError("short response")
    message_type, length, cookie = struct.unpack_from("!HHI", response, 0)
    if message_type != BINDING_SUCCESS or cookie != MAGIC_COOKIE:
        raise StunError("not binding success")
    cursor = HEADER_BYTES
    end = HEADER_BYTES + length
    if end > len(response):
        raise StunError("truncated")
    while cursor + 4 <= end:
        attr_type, attr_len = struct.unpack_from("!HH", response, cursor)
        start = cursor + 4
        stop = start + attr_len
        if stop > end:
            raise StunError("truncated attr")
        if attr_type == XOR_MAPPED_ADDRESS and attr_len >= 8:
            _reserved, family, xport = struct.unpack_from("!BBH", response, start)
            if family != IPV4_FAMILY:
                raise StunError("not ipv4")
            xip = response[start + 4 : start + 8]
            port = xport ^ (MAGIC_COOKIE >> 16)
            ip_bytes = bytes(
                octet ^ cookie for octet, cookie in zip(xip, MAGIC_COOKIE.to_bytes(4, "big"))
            )
            return socket.inet_ntoa(ip_bytes), port
        cursor = stop + ((4 - (attr_len % 4)) % 4)
    raise StunError("no xor-mapped-address")


def binding_request() -> bytes:
    return struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE) + os.urandom(12)


def probe(host: str, port: int, *, timeout_s: float = 2.0) -> tuple[str, int] | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(binding_request(), (host, port))
        payload, _addr = sock.recvfrom(1500)
        return parse_xor_mapped(payload)
    except (OSError, StunError):
        return None
    finally:
        sock.close()


def python_stun_command(bind_host: str, ports: tuple[int, ...]) -> list[str]:
    if not bind_host or not ports:
        raise StunError("need bind address and ports.")
    return [sys.executable, "-m", "tools.cross_network_acceptance.stun",
            bind_host, *[str(port) for port in ports]]


def stun_server_commands(bind_host: str, ports: tuple[int, ...]) -> list[list[str]]:
    """Prefer coturn; else this Python Binding server."""
    if not bind_host or not ports:
        raise StunError("need bind address and ports.")
    turn = shutil.which("turnserver")
    if turn is None:
        for folder in ("/usr/sbin", "/usr/bin"):
            candidate = Path(folder) / "turnserver"
            if candidate.is_file():
                turn = str(candidate)
                break
    if not turn:
        return [python_stun_command(bind_host, ports)]
    return [[
        turn, "-n", f"--listening-ip={bind_host}", f"--listening-port={port}",
        "--no-cli", "--no-tls", "--no-dtls", "--stun-only",
        f"--pidfile=/tmp/webjam-turn-{port}.pid", "--log-file=stdout", "--simple-log",
    ] for port in ports]


def serve(bind_host: str, ports: tuple[int, ...]) -> None:
    sockets: list[socket.socket] = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        sockets.append(sock)
    try:
        while True:
            readable, _, _ = select.select(sockets, (), (), 1.0)
            for item in readable:
                payload, addr = item.recvfrom(1500)
                try:
                    reply = binding_success(payload, addr[0], addr[1])
                except StunError:
                    continue
                item.sendto(reply, addr)
    finally:
        for item in sockets:
            item.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        return 2
    serve(args[0], tuple(int(item) for item in args[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

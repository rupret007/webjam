"""RFC 5389 STUN Binding server plus TURN/ICE message helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import select
import shutil
import socket
import struct
import sys
import zlib
from pathlib import Path

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
HEADER_BYTES = 20
IPV4_FAMILY = 0x01
ATTR_USERNAME, ATTR_MI, ATTR_ERROR = 0x0006, 0x0008, 0x0009
ATTR_FINGERPRINT = 0x8028
FINGERPRINT_XOR = 0x5354554E
ATTR_REALM, ATTR_NONCE = 0x0014, 0x0015
ATTR_XOR_PEER, ATTR_DATA = 0x0012, 0x0013
ATTR_LIFETIME, ATTR_XOR_RELAYED = 0x000D, 0x0016
ATTR_REQ_TRANSPORT = 0x0019
METHOD_BINDING, METHOD_ALLOCATE = 1, 3
METHOD_SEND, METHOD_DATA, METHOD_CREATE = 6, 7, 8
CLASS_REQUEST, CLASS_INDICATION, CLASS_SUCCESS, CLASS_ERROR = 0, 1, 2, 3


class StunError(RuntimeError):
    pass


def xor_mapped_attribute(ip: str, port: int, typ: int = XOR_MAPPED_ADDRESS) -> bytes:
    address = socket.inet_aton(ip)
    xport = port ^ (MAGIC_COOKIE >> 16)
    xip = bytes(octet ^ cookie for octet, cookie in zip(address, MAGIC_COOKIE.to_bytes(4, "big")))
    value = struct.pack("!BBH4s", 0, IPV4_FAMILY, xport, xip)
    return struct.pack("!HH", typ, len(value)) + value


def _pad(value: bytes) -> bytes:
    return value + b"\x00" * ((4 - (len(value) % 4)) % 4)


def pack_attr(typ: int, value: bytes) -> bytes:
    return struct.pack("!HH", typ, len(value)) + _pad(value)


def message_type(method: int, cls: int) -> int:
    return (
        ((method & 0x1F80) << 2) | ((cls & 0x2) << 7)
        | ((method & 0x0070) << 1) | ((cls & 0x1) << 4) | (method & 0x000F)
    )


def split_type(value: int) -> tuple[int, int]:
    method = ((value & 0x3E00) >> 2) | ((value & 0x00E0) >> 1) | (value & 0x000F)
    cls = ((value & 0x0100) >> 7) | ((value & 0x0010) >> 4)
    return method, cls


def pack_message(method: int, cls: int, txid: bytes, attrs: bytes) -> bytes:
    if len(txid) != 12:
        raise StunError("txid")
    header = struct.pack("!HHI", message_type(method, cls), len(attrs), MAGIC_COOKIE) + txid
    return header + attrs


def parse_attrs(payload: bytes) -> dict[int, bytes]:
    if len(payload) < HEADER_BYTES:
        raise StunError("short")
    _typ, length, cookie = struct.unpack_from("!HHI", payload, 0)
    if cookie != MAGIC_COOKIE:
        raise StunError("cookie")
    cursor, end, out = HEADER_BYTES, HEADER_BYTES + length, {}
    if end > len(payload):
        raise StunError("truncated")
    while cursor + 4 <= end:
        attr_type, attr_len = struct.unpack_from("!HH", payload, cursor)
        start = cursor + 4
        stop = start + attr_len
        if stop > end:
            raise StunError("truncated attr")
        out[attr_type] = payload[start:stop]
        cursor = stop + ((4 - (attr_len % 4)) % 4)
    return out


def parse_xor_addr(value: bytes) -> tuple[str, int]:
    if len(value) < 8:
        raise StunError("short xor")
    _reserved, family, xport = struct.unpack_from("!BBH", value, 0)
    if family != IPV4_FAMILY:
        raise StunError("not ipv4")
    port = xport ^ (MAGIC_COOKIE >> 16)
    xip = value[4:8]
    ip_bytes = bytes(
        octet ^ cookie for octet, cookie in zip(xip, MAGIC_COOKIE.to_bytes(4, "big"))
    )
    return socket.inet_ntoa(ip_bytes), port


def long_term_key(user: str, realm: str, password: str) -> bytes:
    return hashlib.md5(f"{user}:{realm}:{password}".encode("utf-8")).digest()


def with_integrity(method: int, cls: int, txid: bytes, attrs: bytes, key: bytes) -> bytes:
    mi_len = len(attrs) + 24
    header_mi = struct.pack("!HHI", message_type(method, cls), mi_len, MAGIC_COOKIE) + txid
    digest = hmac.new(key, header_mi + attrs, hashlib.sha1).digest()
    body = attrs + pack_attr(ATTR_MI, digest)
    total = mi_len + 8
    header = struct.pack("!HHI", message_type(method, cls), total, MAGIC_COOKIE) + txid
    mid = header + body
    crc = zlib.crc32(mid) & 0xFFFFFFFF
    return mid + pack_attr(ATTR_FINGERPRINT, struct.pack("!I", crc ^ FINGERPRINT_XOR))


def binding_success(request: bytes, src_ip: str, src_port: int) -> bytes:
    if len(request) < HEADER_BYTES:
        raise StunError("short request")
    message_type_v, length, cookie = struct.unpack_from("!HHI", request, 0)
    if message_type_v != BINDING_REQUEST or cookie != MAGIC_COOKIE:
        raise StunError("not a binding request")
    if length != len(request) - HEADER_BYTES:
        raise StunError("bad length")
    txid = request[8:HEADER_BYTES]
    attribute = xor_mapped_attribute(src_ip, src_port)
    header = struct.pack("!HHI", BINDING_SUCCESS, len(attribute), MAGIC_COOKIE) + txid
    return header + attribute


def parse_xor_mapped(response: bytes) -> tuple[str, int]:
    attrs = parse_attrs(response)
    raw = attrs.get(XOR_MAPPED_ADDRESS)
    if raw is None:
        raise StunError("no xor-mapped-address")
    return parse_xor_addr(raw)


def binding_request() -> bytes:
    return pack_message(METHOD_BINDING, CLASS_REQUEST, os.urandom(12), b"")


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


def probe_sock(
    sock: socket.socket, host: str, port: int, *, timeout_s: float = 2.0,
) -> tuple[str, int] | None:
    sock.settimeout(timeout_s)
    try:
        sock.sendto(binding_request(), (host, port))
        payload, _addr = sock.recvfrom(2048)
        return parse_xor_mapped(payload)
    except (OSError, StunError):
        return None


def python_stun_command(bind_host: str, ports: tuple[int, ...]) -> list[str]:
    if not bind_host or not ports:
        raise StunError("need bind address and ports.")
    return [sys.executable, "-m", "tools.cross_network_acceptance.stun",
            bind_host, *[str(port) for port in ports]]


def stun_server_commands(bind_host: str, ports: tuple[int, ...]) -> list[list[str]]:
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


def error_code(value: bytes) -> int:
    if len(value) < 4:
        return 0
    return value[2] * 100 + value[3]


def parse_message(payload: bytes) -> tuple[int, int, bytes, dict[int, bytes]]:
    if len(payload) < HEADER_BYTES:
        raise StunError("short")
    typ = struct.unpack_from("!H", payload, 0)[0]
    method, cls = split_type(typ)
    return method, cls, payload[8:HEADER_BYTES], parse_attrs(payload)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        return 2
    serve(args[0], tuple(int(item) for item in args[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

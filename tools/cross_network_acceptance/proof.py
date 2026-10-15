"""Proof peers: nonce-echo Music/Art plus selected candidate types from both sides."""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance import PROOF_PORT, HarnessError
from tools.cross_network_acceptance.stun import (
    ATTR_DATA, ATTR_ERROR, ATTR_LIFETIME, ATTR_NONCE, ATTR_REALM,
    ATTR_REQ_TRANSPORT, ATTR_USERNAME, ATTR_XOR_PEER, ATTR_XOR_RELAYED,
    CLASS_ERROR, CLASS_INDICATION, CLASS_REQUEST, CLASS_SUCCESS,
    METHOD_ALLOCATE, METHOD_CREATE, METHOD_DATA, METHOD_SEND, StunError,
    XOR_MAPPED_ADDRESS, error_code, long_term_key, pack_attr, pack_message,
    parse_message, parse_xor_addr, probe_sock, with_integrity,
    xor_mapped_attribute,
)

DIRECT_TYPES = frozenset({"host", "srflx", "prflx"})
RELAY_TYPE = "relay"
REQUIRED_FIELDS = (
    "local_type", "remote_type", "music_nonce", "art_nonce", "music_echo", "art_echo",
)
KIND_MUSIC, KIND_ART, KIND_MUSIC_ECHO, KIND_ART_ECHO, KIND_PUNCH = 1, 2, 3, 4, 5
MAGIC = b"WJ1"
MUSIC_BYTES = 480


class ProofError(HarnessError):
    pass


def candidate_kind(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in DIRECT_TYPES:
        return "direct"
    if text == RELAY_TYPE:
        return "relay"
    return ""


def pair_has_relay(local: str, remote: str) -> bool:
    return candidate_kind(local) == "relay" or candidate_kind(remote) == "relay"


def pair_unknown(local: str, remote: str) -> bool:
    return not candidate_kind(local) or not candidate_kind(remote)


def evaluate_proof(
    host: dict[str, Any] | None,
    guest: dict[str, Any] | None,
    *,
    expect_path: str,
    music_nonce: str,
    art_nonce: str,
    relay_bytes: int = 0,
) -> str:
    """Return empty string on PASS; any other string is FAIL."""
    if not isinstance(host, dict) or not isinstance(guest, dict):
        return "missing peer proof"
    for row in (host, guest):
        for key in REQUIRED_FIELDS:
            if key not in row or row.get(key) in {None, ""}:
                return "missing proof field"
        if pair_unknown(str(row.get("local_type")), str(row.get("remote_type"))):
            return "unknown candidate type"
        if str(row.get("music_nonce") or "") != music_nonce:
            return "music nonce mismatch"
        if str(row.get("art_nonce") or "") != art_nonce:
            return "art nonce mismatch"
        if str(row.get("music_echo") or "") != music_nonce:
            return "music echo mismatch"
        if str(row.get("art_echo") or "") != art_nonce:
            return "art echo mismatch"
    host_relay = pair_has_relay(str(host["local_type"]), str(host["remote_type"]))
    guest_relay = pair_has_relay(str(guest["local_type"]), str(guest["remote_type"]))
    want = str(expect_path or "").strip().lower()
    if want == "direct":
        if host_relay or guest_relay:
            return "direct path used relay"
        return ""
    if want == "relay":
        if not (host_relay or guest_relay):
            return "relay path used direct"
        if int(relay_bytes or 0) < 1:
            return "relay carried no bytes"
        return ""
    return "unknown expect_path"


def _frame(kind: int, nonce: bytes, payload: bytes = b"") -> bytes:
    return MAGIC + bytes([kind]) + nonce + payload


def _parse_frame(data: bytes) -> tuple[int, bytes, bytes] | None:
    if len(data) < 4 + 16 or not data.startswith(MAGIC):
        return None
    return data[3], data[4:20], data[20:]


class TurnClient:
    def __init__(
        self, sock: socket.socket, server: tuple[str, int],
        username: str, credential: str, realm: str,
    ) -> None:
        self.sock, self.server = sock, server
        self.username, self.credential, self.realm = username, credential, realm
        self.nonce = b""
        self.key = long_term_key(username, realm, credential)

    def _rpc(self, method: int, attrs: bytes, timeout: float = 3.0) -> tuple[int, dict[int, bytes]]:
        txid = os.urandom(12)
        msg = with_integrity(method, CLASS_REQUEST, txid, attrs, self.key) if self.nonce else pack_message(
            method, CLASS_REQUEST, txid, attrs)
        self.sock.settimeout(timeout)
        self.sock.sendto(msg, self.server)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                payload, _addr = self.sock.recvfrom(2048)
            except TimeoutError:
                break
            try:
                got_m, cls, got_tx, parsed = parse_message(payload)
            except StunError:
                continue
            if got_tx != txid or got_m != method:
                continue
            if cls == CLASS_ERROR:
                code = error_code(parsed.get(ATTR_ERROR, b""))
                if code == 401:
                    self.realm = parsed.get(ATTR_REALM, b"").strip(b"\x00").decode("utf-8") or self.realm
                    self.nonce = parsed.get(ATTR_NONCE, b"").strip(b"\x00")
                    self.key = long_term_key(self.username, self.realm, self.credential)
                    extra = pack_attr(ATTR_USERNAME, self.username.encode()) + pack_attr(
                        ATTR_REALM, self.realm.encode()) + pack_attr(ATTR_NONCE, self.nonce)
                    msg = with_integrity(method, CLASS_REQUEST, os.urandom(12), extra + attrs, self.key)
                    # Retry once with a new txid tracked below.
                    txid = msg[8:20]
                    self.sock.sendto(msg, self.server)
                    continue
                raise ProofError("TURN allocate failed")
            if cls == CLASS_SUCCESS:
                return cls, parsed
        raise ProofError("TURN allocate failed")

    def allocate(self) -> tuple[tuple[str, int], tuple[str, int]]:
        transport = pack_attr(ATTR_REQ_TRANSPORT, struct.pack("!B3s", 17, b"\x00\x00\x00"))
        lifetime = pack_attr(ATTR_LIFETIME, struct.pack("!I", 600))
        # Unauthenticated allocate to collect nonce.
        txid = os.urandom(12)
        self.sock.settimeout(3.0)
        self.sock.sendto(pack_message(METHOD_ALLOCATE, CLASS_REQUEST, txid, transport + lifetime), self.server)
        payload, _addr = self.sock.recvfrom(2048)
        _m, cls, _tx, parsed = parse_message(payload)
        if cls == CLASS_ERROR and error_code(parsed.get(ATTR_ERROR, b"")) == 401:
            self.realm = parsed.get(ATTR_REALM, b"").strip(b"\x00").decode("utf-8") or self.realm
            self.nonce = parsed.get(ATTR_NONCE, b"").strip(b"\x00")
            self.key = long_term_key(self.username, self.realm, self.credential)
        extra = pack_attr(ATTR_USERNAME, self.username.encode()) + pack_attr(
            ATTR_REALM, self.realm.encode()) + pack_attr(ATTR_NONCE, self.nonce)
        _cls, parsed = self._rpc(METHOD_ALLOCATE, extra + transport + lifetime)
        relayed = parsed.get(ATTR_XOR_RELAYED)
        mapped = parsed.get(XOR_MAPPED_ADDRESS)
        if not relayed or not mapped:
            raise ProofError("TURN allocate failed")
        return parse_xor_addr(relayed), parse_xor_addr(mapped)

    def create_permission(self, ip: str, port: int) -> None:
        extra = pack_attr(ATTR_USERNAME, self.username.encode()) + pack_attr(
            ATTR_REALM, self.realm.encode()) + pack_attr(ATTR_NONCE, self.nonce)
        peer = xor_mapped_attribute(ip, port, ATTR_XOR_PEER)
        self._rpc(METHOD_CREATE, extra + peer)

    def send_data(self, ip: str, port: int, payload: bytes) -> None:
        extra = pack_attr(ATTR_USERNAME, self.username.encode()) + pack_attr(
            ATTR_REALM, self.realm.encode()) + pack_attr(ATTR_NONCE, self.nonce)
        body = extra + xor_mapped_attribute(ip, port, ATTR_XOR_PEER) + pack_attr(ATTR_DATA, payload)
        msg = with_integrity(METHOD_SEND, CLASS_INDICATION, os.urandom(12), body, self.key)
        self.sock.sendto(msg, self.server)

    def maybe_data(self, payload: bytes) -> bytes | None:
        try:
            method, cls, _tx, parsed = parse_message(payload)
        except StunError:
            return None
        if method == METHOD_DATA and cls == CLASS_INDICATION:
            return parsed.get(ATTR_DATA)
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _wait_json(path: Path, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.05)
                continue
            if isinstance(payload, dict):
                return payload
        time.sleep(0.05)
    return {}


def run_peer(work_dir: Path, role: str) -> int:
    cfg = json.loads((work_dir / "peer-config.json").read_text(encoding="utf-8"))
    role = str(role or cfg.get("role") or "")
    path = str(cfg["path"])
    bind = str(cfg.get("binds", {}).get(role) or cfg.get("bind") or "")
    if not role or not bind:
        raise ProofError("missing peer role")
    music_nonce = bytes.fromhex(str(cfg["music_nonce"]))
    art_nonce = bytes.fromhex(str(cfg["art_nonce"]))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, PROOF_PORT))
    turn: TurnClient | None = None
    mapping: tuple[int, int] | None = None
    try:
        if path == "relay":
            turn_cfg = cfg["turn"]
            turn = TurnClient(
                sock, (str(turn_cfg["host"]), int(turn_cfg["port"])),
                str(turn_cfg["username"]), str(turn_cfg["credential"]), str(turn_cfg["realm"]),
            )
            relayed, _mapped = turn.allocate()
            local = {"type": RELAY_TYPE, "ip": relayed[0], "port": relayed[1]}
        elif cfg.get("stun"):
            stun_a, stun_b = cfg["stun"]
            mapped_a = probe_sock(sock, stun_a[0], int(stun_a[1]))
            mapped_b = probe_sock(sock, stun_b[0], int(stun_b[1]))
            if not mapped_a or not mapped_b:
                raise ProofError("NAT mapping probe mismatch")
            mapping = (mapped_a[1], mapped_b[1])
            local = {"type": "srflx", "ip": mapped_a[0], "port": mapped_a[1]}
        else:
            local = {"type": "host", "ip": bind, "port": PROOF_PORT}
        mine = work_dir / f"{role}.cand"
        _write_json(mine, {"candidate": local, "mapping_ports": mapping})
        peer_role = "guest" if role == "host" else "host"
        peer_row = _wait_json(work_dir / f"{peer_role}.cand", 15.0)
        remote = peer_row.get("candidate") if isinstance(peer_row.get("candidate"), dict) else {}
        if not remote:
            raise ProofError("missing peer candidate")
        remote_ip, remote_port = str(remote["ip"]), int(remote["port"])
        if turn is not None:
            turn.create_permission(remote_ip, remote_port)
        music_echo = art_echo = ""
        got_music = got_art = False
        deadline = time.monotonic() + 12.0
        music_payload = _frame(KIND_MUSIC, music_nonce, os.urandom(MUSIC_BYTES))
        art_payload = _frame(KIND_ART, art_nonce, b'{"stroke":"line"}')
        punch = _frame(KIND_PUNCH, music_nonce)

        def send(raw: bytes) -> None:
            if turn is not None:
                turn.send_data(remote_ip, remote_port, raw)
            else:
                sock.sendto(raw, (remote_ip, remote_port))

        sock.settimeout(0.25)
        while time.monotonic() < deadline:
            send(punch)
            send(music_payload)
            send(art_payload)
            try:
                data, _addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            inner = data
            if turn is not None:
                extracted = turn.maybe_data(data)
                if extracted is None:
                    continue
                inner = extracted
            parsed = _parse_frame(inner)
            if parsed is None:
                continue
            kind, nonce, _rest = parsed
            hex_n = nonce.hex()
            if kind == KIND_MUSIC:
                send(_frame(KIND_MUSIC_ECHO, nonce))
                got_music = True
            elif kind == KIND_ART:
                send(_frame(KIND_ART_ECHO, nonce))
                got_art = True
            elif kind == KIND_MUSIC_ECHO and hex_n == music_nonce.hex():
                music_echo = hex_n
            elif kind == KIND_ART_ECHO and hex_n == art_nonce.hex():
                art_echo = hex_n
            if music_echo and art_echo and got_music and got_art:
                break
        proof = {
            "role": role,
            "local_type": local["type"],
            "remote_type": str(remote.get("type") or ""),
            "music_nonce": music_nonce.hex(),
            "art_nonce": art_nonce.hex(),
            "music_echo": music_echo,
            "art_echo": art_echo,
            "mapping_ports": mapping,
        }
        _write_json(work_dir / f"{role}.proof", proof)
        return 0 if music_echo and art_echo and got_music and got_art else 1
    finally:
        sock.close()

"""Proof peers drive webjam-fabric IPC."""

from __future__ import annotations

import json
import os
import select
import socket
import time
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance import HarnessError

DIRECT_TYPES = frozenset({"host", "srflx", "prflx", "direct"})
RELAY_TYPE = "relay"
PROFILE_ID = "direct-stun"
REQUIRED_FIELDS = (
    "local_type", "remote_type", "music_nonce", "art_nonce", "music_echo", "art_echo",
)
# A live UDP path has no delivery guarantee for an individual frame. Exercise
# a fixed two-second audio stream under netem's 1% loss in each direction.
# Every sequence is sent exactly once; neither frames nor cases are retried.
MUSIC_FRAMES = 1000
MUSIC_INTERVAL_S = 0.002
MUSIC_MIN_ECHOES = 950
MUSIC_TIMEOUT_S = 12.0


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
        if host_relay or guest_relay or int(relay_bytes or 0) > 0:
            return "direct path used relay"
        return ""
    if want == "relay":
        if not (host_relay and guest_relay):
            return "relay path used direct"
        if int(relay_bytes or 0) < 1:
            return "relay carried no bytes"
        return ""
    return "unknown expect_path"


def path_label(*, same_lan: bool) -> str:
    if same_lan:
        return "host"
    return ""


def types_from_session_path(path: str) -> tuple[str, str]:
    text = str(path or "").strip().lower()
    if text == "relay":
        return RELAY_TYPE, RELAY_TYPE
    if text == "direct":
        return "direct", "direct"
    return "", ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _wait_text(path: Path, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.05)
    return ""


def _wait_connected(process: Any, mode: str, timeout_s: float = 40.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in process.timeline:
            if event.event_type == "peer_connected" and event.mode == mode:
                return event
        time.sleep(0.02)
    raise ProofError("sidecar did not connect")


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _wait_bytes(path: Path, timeout_s: float) -> bytes:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            data = path.read_bytes()
            if data:
                return data
        time.sleep(0.05)
    return b""


def _wait_help(inbox: list[str], nonce: str, timeout_s: float = 12.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if nonce in inbox:
            return nonce
        time.sleep(0.02)
    return ""


def _music_frame(nonce: str, sequence: int) -> bytes:
    return nonce.encode("ascii") + sequence.to_bytes(4, "big")


def _music_sequence(payload: bytes, nonce: str) -> int:
    prefix = nonce.encode("ascii")
    if len(payload) != len(prefix) + 4 or not payload.startswith(prefix):
        raise ProofError("music payload mismatch")
    sequence = int.from_bytes(payload[-4:], "big")
    if sequence >= MUSIC_FRAMES:
        raise ProofError("music sequence out of range")
    return sequence


def _receive_music(sock: socket.socket, nonce: str, seen: set[int]) -> tuple[bytes, Any]:
    payload, source = sock.recvfrom(2048)
    sequence = _music_sequence(payload, nonce)
    if sequence in seen:
        raise ProofError("duplicate music sequence")
    seen.add(sequence)
    return payload, source


def _guest_music(sock: socket.socket, port: int, nonce: str) -> int:
    started = time.monotonic()
    deadline = started + MUSIC_TIMEOUT_S
    sent = 0
    seen: set[int] = set()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if sent < MUSIC_FRAMES and now >= started + sent * MUSIC_INTERVAL_S:
            sock.sendto(_music_frame(nonce, sent), ("127.0.0.1", port))
            sent += 1
        if sent == MUSIC_FRAMES and len(seen) >= MUSIC_MIN_ECHOES:
            return len(seen)
        next_send = started + sent * MUSIC_INTERVAL_S if sent < MUSIC_FRAMES else deadline
        timeout = max(0, min(next_send, deadline) - time.monotonic())
        if select.select([sock], [], [], timeout)[0]:
            _receive_music(sock, nonce, seen)
    raise ProofError(f"music stream incomplete: sent={sent} echoed={len(seen)}")


def _host_music(sock: socket.socket, nonce: str, finished: Path) -> int:
    deadline = time.monotonic() + MUSIC_TIMEOUT_S
    seen: set[int] = set()
    while time.monotonic() < deadline:
        if select.select([sock], [], [], 0.02)[0]:
            payload, source = _receive_music(sock, nonce, seen)
            sock.sendto(payload, source)
        elif finished.is_file():
            if len(seen) < MUSIC_MIN_ECHOES:
                raise ProofError(f"music stream incomplete: received={len(seen)}")
            return len(seen)
    raise ProofError("music stream did not finish")


def run_peer(work_dir: Path, role: str) -> int:
    cfg = json.loads((work_dir / "peer-config.json").read_text(encoding="utf-8"))
    role = str(role or cfg.get("role") or "")
    if role not in {"host", "guest"}:
        raise ProofError("missing peer role")
    music_nonce = str(cfg["music_nonce"])
    art_nonce = str(cfg["art_nonce"])
    binary = str(os.environ.get("WEBJAM_TRANSPORT_BINARY") or "").strip()
    build = str(os.environ.get("WEBJAM_BUILD_ID") or "").strip()
    if not binary or not build:
        raise ProofError("transport binary missing")

    from core.remote_invitation import issue_remote_invitation, parse_remote_invitation_link
    from services.transport_runtime import TransportProcess, TransportProcessError

    inbox: list[str] = []

    def on_event(event: Any) -> None:
        if event.event_type == "help_received" and event.help_text:
            inbox.append(event.help_text)
        if event.event_type == "guest_signal" and event.signal:
            _write_bytes(work_dir / "guest.reply", bytes(event.signal))

    process = TransportProcess(
        binary, expected_build=build, command_timeout=40, on_event=on_event,
    )
    audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    audio.bind(("127.0.0.1", 0))
    audio.settimeout(12)
    music_echo = art_echo = ""
    guest_port = 0
    session_path = ""
    try:
        process.start()
        if role == "host":
            pin = process.prepare_host()
            issued = issue_remote_invitation(
                PROFILE_ID,
                allowed_profiles={PROFILE_ID},
                host_spki_sha256=pin,
                ttl_seconds=120,
            )
            invite = work_dir / "invite.url"
            invite.write_text(issued.private_link.reveal_for_clipboard(), encoding="utf-8")
            invite.chmod(0o600)
            registered = process.open_host(
                issued.invitation, target_port=audio.getsockname()[1], generation=1,
            )
            envelope = bytes(getattr(registered, "signal", b"") or b"")
            if not envelope:
                raise ProofError("missing host signal")
            _write_bytes(work_dir / "host.signal", envelope)
            reply = _wait_bytes(work_dir / "guest.reply", 25.0)
            if not reply:
                raise ProofError("missing guest reply")
            try:
                process.apply_guest_signal(reply, generation=1)
            except TransportProcessError:
                if not any(
                    ev.event_type == "peer_connected" and ev.mode == "host"
                    for ev in process.timeline
                ):
                    raise
            connected = _wait_connected(process, "host", 40.0)
            session_path = str(getattr(connected, "session_path", "") or "")
        else:
            raw = _wait_text(work_dir / "invite.url", 20.0)
            envelope = _wait_bytes(work_dir / "host.signal", 20.0)
            if not raw or not envelope:
                raise ProofError("missing invitation")
            invitation = parse_remote_invitation_link(
                raw, allowed_profiles={PROFILE_ID},
            ).with_host_signal(envelope)
            connected = process.open_guest(invitation, generation=1)
            guest_port = int(connected.loopback_port)
            session_path = str(getattr(connected, "session_path", "") or "")
        music_finished = work_dir / "guest.music-finished"
        if role == "guest":
            guest_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            guest_audio.settimeout(12)
            try:
                music_frames = _guest_music(guest_audio, guest_port, music_nonce)
            finally:
                music_finished.touch(mode=0o600)
                guest_audio.close()
        else:
            music_frames = _host_music(audio, music_nonce, music_finished)
        music_echo = music_nonce
        if role == "host":
            process.send_help(art_nonce, generation=1)
            art_echo = _wait_help(inbox, art_nonce)
        else:
            art_echo = _wait_help(inbox, art_nonce)
            if art_echo:
                process.send_help(art_nonce, generation=1)
        local_type, remote_type = types_from_session_path(session_path)
        proof = {
            "role": role,
            "local_type": local_type,
            "remote_type": remote_type,
            "music_nonce": music_nonce,
            "art_nonce": art_nonce,
            "music_echo": music_echo,
            "music_frames": music_frames,
            "art_echo": art_echo,
            "profile_id": PROFILE_ID,
        }
        _write_json(work_dir / f"{role}.proof", proof)
        if role == "guest":
            _wait_text(work_dir / "host.proof", 20.0)
        return 0 if music_echo and art_echo else 1
    finally:
        try:
            process.stop()
        except Exception:
            pass
        audio.close()

"""Bounded local IPC, RTP and Jamulus wire/lifecycle contracts."""
from __future__ import annotations

import socket
import struct
import time

import pytest

from services.web_join_bridge import (
    BridgeError, CLOSE, HELLO, READY, JAMULUS_BYTES, MAX_QUOTA_BYTES,
    WebJoinBridge, _control, _parse_control, make_rtp, parse_rtp,
)


class FakeCodec:
    def __init__(self, _library=None):
        self.closed = False

    def encode_jamulus(self, pcm):
        return b"a" * JAMULUS_BYTES

    def decode_jamulus(self, payload):
        return bytes(128 * 4)

    def encode_web(self, pcm):
        return b"opus"

    def decode_web(self, payload):
        return bytes(960 * 4)

    def close(self):
        self.closed = True


@pytest.fixture
def local_server():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(0.15)
        yield server


def connect(bridge):
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.connect(("127.0.0.1", bridge.port))
    peer.settimeout(1)
    peer.send(HELLO + bridge.token)
    assert peer.recv(100) == READY + bridge.token
    return peer


@pytest.mark.parametrize("port", [0, -1, 65536, "22124", True])
def test_requires_trusted_port(port):
    with pytest.raises(ValueError):
        WebJoinBridge(port)


@pytest.mark.parametrize("options", [{"quota_bytes": 0}, {"quota_bytes": MAX_QUOTA_BYTES + 1},
    {"idle_timeout_s": float("nan")}, {"startup_timeout_s": float("inf")},
    {"max_duration_s": 3601}, {"startup_timeout_s": 0}])
def test_refuses_unbounded_limits(options):
    with pytest.raises(ValueError):
        WebJoinBridge(22124, **options)


def test_actual_jamulus_control_wire_and_strict_crc():
    # TAG(2), ID(2), counter(1), length(2), body, CRC(2).
    raw = _control(20, 255, b"properties")
    assert raw[:7] == bytes.fromhex("00001400ff0a00")
    assert _parse_control(raw) == (20, 255, b"properties")
    assert _parse_control(raw + b"x") is None
    assert _parse_control(raw[:-1] + bytes([raw[-1] ^ 1])) is None
    assert _parse_control(b"\0\0") is None


def test_rtp_extensions_and_padding_do_not_reach_decoder():
    packet = struct.pack("!BBHII", 0xB1, 111, 65535, 9000, 1)
    packet += bytes(4) + bytes.fromhex("bede0001") + b"abcd" + b"opus" + b"\0\x02"
    assert parse_rtp(packet) == (65535, 9000, b"opus")
    assert parse_rtp(make_rtp(b"opus", 2, 960)) == (2, 960, b"opus")


@pytest.mark.parametrize("raw", [b"", bytes(13), b"\x90" + bytes(12),
    b"\xa0" + bytes(11) + b"\0", make_rtp(b"a" * 1276, 0, 0)])
def test_rtp_rejects_malformed_and_oversize(raw):
    with pytest.raises(BridgeError):
        parse_rtp(raw)


@pytest.mark.requires_local_socket
def test_no_jamulus_connection_before_token_and_auth_timeout(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec, authentication_timeout_s=0.3)
    bridge.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as attacker:
            attacker.sendto(HELLO + bytes(32), ("127.0.0.1", bridge.port))
        with pytest.raises(socket.timeout):
            local_server.recv(1500)
        assert bridge.closed.wait(1)
        assert bridge.close_reason == "bridge authentication timeout"
    finally:
        bridge.close()


@pytest.mark.requires_local_socket
def test_authenticated_source_pinned_and_close_releases_resources(local_server):
    codec = FakeCodec()
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=lambda _: codec)
    bridge.start()
    peer = connect(bridge)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as attacker:
            attacker.sendto(CLOSE + bridge.token, ("127.0.0.1", bridge.port))
        time.sleep(0.04)
        assert not bridge.closed.is_set()
        peer.send(CLOSE + bridge.token)
        assert bridge.closed.wait(1)
        assert bridge.close_reason == "endpoint closed"
        assert codec.closed and bridge._socket is None and bridge._jam is None
        assert not bridge._input and not bridge._output
        with pytest.raises(BridgeError):
            bridge.start()
    finally:
        peer.close()
        bridge.close()


@pytest.mark.requires_local_socket
def test_media_quota_closes_before_forwarding_over_limit(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec, quota_bytes=15)
    bridge.start()
    peer = connect(bridge)
    try:
        peer.send(make_rtp(b"opus", 1, 0))
        assert bridge.closed.wait(1)
        assert bridge.close_reason == "quota exhausted"
        assert bridge.bytes_used == 0
    finally:
        peer.close()
        bridge.close()


@pytest.mark.requires_local_socket
def test_media_idle_timeout_closes_even_if_control_alive(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec, idle_timeout_s=0.15)
    bridge.start()
    peer = connect(bridge)
    try:
        assert bridge.closed.wait(1)
        assert bridge.close_reason == "browser media timeout"
    finally:
        peer.close()
        bridge.close()


@pytest.mark.requires_local_socket
def test_duplicate_rtp_does_not_consume_quota_or_fill_buffer(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec)
    bridge.start()
    peer = connect(bridge)
    try:
        packet = make_rtp(b"opus", 65535, 0)
        for _ in range(25):
            peer.send(packet)
        time.sleep(0.08)
        assert not bridge.closed.is_set()
        assert bridge.bytes_used == len(packet)
        peer.send(make_rtp(b"opus", 0, 960))
        time.sleep(0.03)
        assert bridge.bytes_used == len(packet) * 2
    finally:
        peer.close()
        bridge.close()


@pytest.mark.requires_local_socket
def test_valid_media_flood_fails_closed_at_buffer_bound(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec)
    bridge.start()
    peer = connect(bridge)
    try:
        for seq in range(8):
            try:
                peer.send(make_rtp(b"opus", seq, seq * 960))
            except ConnectionRefusedError:
                break  # Bridge can close while the flood is still in progress.
        assert bridge.closed.wait(1)
        assert bridge.close_reason == "browser audio queue exceeded 60 ms"
    finally:
        peer.close()
        bridge.close()


def test_native_failure_does_not_leave_open_socket():
    def fail(_library):
        raise BridgeError("native dependency unavailable")
    bridge = WebJoinBridge(22124, codec_factory=fail)
    with pytest.raises(BridgeError, match="native dependency"):
        bridge.start()
    assert bridge.closed.is_set() and bridge._socket is None


@pytest.mark.requires_local_socket
def test_jamulus_properties_and_guest_label_match_pinned_server(local_server):
    bridge = WebJoinBridge(local_server.getsockname()[1], codec_factory=FakeCodec)
    bridge.start()
    peer = connect(bridge)
    try:
        controls = {}
        for _ in range(4):
            packet, _address = local_server.recvfrom(1500)
            parsed = _parse_control(packet)
            if parsed:
                controls[parsed[0]] = parsed[2]
        assert struct.unpack("<IHB IHHI", controls[20]) == (106, 1, 2, 48000, 2, 0, 0)
        name_size = struct.unpack_from("<H", controls[25], 7)[0]
        assert name_size <= 16  # Jamulus MAX_LEN_FADER_TAG
        assert controls[25][9:9 + name_size] == b"Web Join (delay)"
    finally:
        peer.close()
        bridge.close()


def test_os_scheduling_pause_discards_stale_audio_without_burst_or_disconnect():
    class LocalSocket:
        def __init__(self, queued=()):
            self.queued = list(queued)
            self.sent = []
        def recvfrom(self, _size):
            if not self.queued:
                raise BlockingIOError()
            return self.queued.pop(0)
        def send(self, raw):
            self.sent.append(raw)

    class RecordingCodec(FakeCodec):
        def __init__(self):
            super().__init__()
            self.pcm = []
        def encode_jamulus(self, pcm):
            self.pcm.append(pcm)
            return super().encode_jamulus(pcm)

    bridge = WebJoinBridge(22124, codec_factory=FakeCodec)
    bridge._peer = ("127.0.0.1", 1234)
    stale = make_rtp(b"opus", 8, 960)
    bridge._socket = LocalSocket([(stale, bridge._peer)])
    bridge._jam = LocalSocket([(b"x" * JAMULUS_BYTES, ("127.0.0.1", 22124))])
    bridge._codec = RecordingCodec()
    bridge._input.extend(b"x" * 960 * 4)
    bridge._output.extend(b"y" * 128 * 4)
    bridge._last_sequence = 7
    next_tick = bridge._pace_audio(10, 9.8)
    assert next_tick == pytest.approx(10 + 128 / 48000)
    assert not bridge._input and not bridge._output
    assert bridge._codec.pcm == [bytes(128 * 4)]
    assert bridge._last_sequence == 8 and bridge.bytes_used == len(stale)
    assert not bridge.closed.is_set()
    bridge._pace_audio(10.001, next_tick)
    assert len(bridge._jam.sent) == 1

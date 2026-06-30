"""Send-side + framing tests for the Jamulus UDP protocol adapter.

Covers the outbound path (packet build/CRC, _send addressing + tx counter,
apply_mixer gain/pan/mute, _send_ack, request_clients) with a fake socket —
no real network.  Complements the inbound fuzz tests.
"""
from __future__ import annotations

import struct
import unittest

import core.jamulus_protocol as jp
from core.jamulus_protocol import JamulusProtocolAdapter, _build_packet, _parse_packet


class _FakeSock:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple]] = []

    def sendto(self, data, addr):
        self.sent.append((bytes(data), addr))


def _adapter():
    a = JamulusProtocolAdapter(host="127.0.0.1", port=22124, enabled=True)
    a._sock = _FakeSock()
    return a


class TestFraming(unittest.TestCase):
    def test_build_parse_roundtrip(self):
        for msg_id, count, data in [(2, 1, b""), (13, 5, b"\x01\x02"),
                                    (255, 65535, b"\xff" * 40)]:
            pkt = _build_packet(msg_id, count, data)
            self.assertEqual(_parse_packet(pkt), (msg_id, count, data))

    def test_corrupted_crc_is_rejected(self):
        pkt = bytearray(_build_packet(2, 1, b"\x00\x00"))
        pkt[-1] ^= 0xFF  # flip a CRC byte
        self.assertIsNone(_parse_packet(bytes(pkt)))

    def test_truncated_is_rejected(self):
        pkt = _build_packet(2, 1, b"\x01\x02\x03\x04")
        self.assertIsNone(_parse_packet(pkt[:-3]))  # claims more data than present


class TestSend(unittest.TestCase):
    def test_send_frames_and_addresses(self):
        a = _adapter()
        a._send(jp._MsgId.REQ_CONN_CLIENTS_LIST)
        self.assertEqual(len(a._sock.sent), 1)
        data, addr = a._sock.sent[0]
        self.assertEqual(addr, ("127.0.0.1", 22124))
        parsed = _parse_packet(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], int(jp._MsgId.REQ_CONN_CLIENTS_LIST))

    def test_tx_count_increments_and_wraps_field(self):
        a = _adapter()
        a._send(0)
        a._send(0)
        c0 = _parse_packet(a._sock.sent[0][0])[1]
        c1 = _parse_packet(a._sock.sent[1][0])[1]
        self.assertEqual(c1, (c0 + 1) & 0xFFFF)

    def test_send_noop_without_socket(self):
        a = JamulusProtocolAdapter(host="127.0.0.1", port=22124, enabled=True)
        a._sock = None
        a._send(0)  # must not raise


class TestApplyMixer(unittest.TestCase):
    def test_sends_gain_and_pan(self):
        a = _adapter()
        a.apply_mixer(channel_id=3, fader_level=127, pan=25, muted=False)
        self.assertEqual(len(a._sock.sent), 2)
        gain = _parse_packet(a._sock.sent[0][0])
        pan = _parse_packet(a._sock.sent[1][0])
        self.assertEqual(gain[0], int(jp._MsgId.CHANNEL_GAIN))
        ch, g = struct.unpack("<HH", gain[2])
        self.assertEqual((ch, g), (3, 32767))   # 127/127 -> 32767
        self.assertEqual(pan[0], int(jp._MsgId.CHANNEL_PAN))
        ch2, p = struct.unpack("<HH", pan[2])
        self.assertEqual((ch2, p), (3, 25))

    def test_muted_sends_zero_gain(self):
        a = _adapter()
        a.apply_mixer(1, 100, 50, True)
        _, g = struct.unpack("<HH", _parse_packet(a._sock.sent[0][0])[2])
        self.assertEqual(g, 0)

    def test_clamps_out_of_range(self):
        a = _adapter()
        a.apply_mixer(0, 999, 999, False)
        _, g = struct.unpack("<HH", _parse_packet(a._sock.sent[0][0])[2])
        _, p = struct.unpack("<HH", _parse_packet(a._sock.sent[1][0])[2])
        self.assertEqual(g, 32767)   # fader clamped to 127
        self.assertEqual(p, 100)     # pan clamped to 100

    def test_noop_without_socket(self):
        a = JamulusProtocolAdapter(host="127.0.0.1", port=22124, enabled=True)
        a._sock = None
        a.apply_mixer(0, 100, 50, False)  # must not raise


class TestAckAndRequests(unittest.TestCase):
    def test_send_ack_payload(self):
        a = _adapter()
        a._send_ack(7, 42)
        pkt = _parse_packet(a._sock.sent[0][0])
        self.assertEqual(pkt[0], int(jp._MsgId.ACKNOWLEDGE))
        mid, cnt = struct.unpack("<HH", pkt[2])
        self.assertEqual((mid, cnt), (7, 42))

    def test_request_clients_returns_cache_and_sends_request(self):
        a = _adapter()
        a.set_cached_participants({0: "Me", 1: "Alice"})
        result = a.request_clients()
        self.assertEqual(result, {0: "Me", 1: "Alice"})
        ids = [_parse_packet(d)[0] for d, _ in a._sock.sent]
        self.assertIn(int(jp._MsgId.REQ_CONN_CLIENTS_LIST), ids)


if __name__ == "__main__":
    unittest.main()

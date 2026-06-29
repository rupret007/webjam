"""Fuzz/robustness regression tests for the Jamulus UDP protocol parser.

_handle_packet() and the sub-parsers run on raw bytes received from the network
(the Jamulus server, or anything that can send UDP to the bound port).  They
must never raise on malformed/truncated/hostile input — an uncaught exception
would kill the receive thread.  These tests pin that contract.
"""
from __future__ import annotations

import random
import struct
import unittest

import core.jamulus_protocol as jp
from core.jamulus_protocol import JamulusProtocolAdapter


class TestProtocolFuzz(unittest.TestCase):
    def _adapter(self):
        return JamulusProtocolAdapter(host="127.0.0.1", port=22124, enabled=False)

    def test_handle_packet_never_raises_on_random_bytes(self):
        ad = self._adapter()
        rnd = random.Random(1234)
        for _ in range(4000):
            n = rnd.randint(0, 80)
            raw = bytes(rnd.randint(0, 255) for _ in range(n))
            try:
                ad._handle_packet(raw)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_handle_packet raised on {raw.hex()!r}: {exc!r}")

    def test_handle_packet_edge_buffers(self):
        ad = self._adapter()
        for raw in (b"", b"\x00", b"\xff", b"\x00" * 7, b"\xff" * 8,
                    struct.pack("<H", 5), b"\x01\x02\x03"):
            ad._handle_packet(raw)  # must not raise

    def test_subparsers_never_raise_on_random_payloads(self):
        rnd = random.Random(99)
        for name in ("_parse_conn_clients_list", "_parse_level_list", "_parse_packet"):
            fn = getattr(jp, name)
            for _ in range(2000):
                data = bytes(rnd.randint(0, 255) for _ in range(rnd.randint(0, 48)))
                try:
                    fn(data)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"{name} raised on {data.hex()!r}: {exc!r}")

    def test_level_list_is_bounded(self):
        # A huge CLT_CHANNEL_LEVEL_LIST payload must not produce an unbounded dict.
        levels = jp._parse_level_list(b"\x40" * 5000)
        self.assertLessEqual(len(levels), 500)


if __name__ == "__main__":
    unittest.main()

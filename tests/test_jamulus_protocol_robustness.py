"""Regression tests for the Jamulus UDP protocol parser (v0.4.5).

These cover the hardening additions: bounded level-list parsing, defensive
``_parse_packet`` (truncated/empty/CRC-corrupt input), and offset-safe
``_parse_le_string``.
"""
from __future__ import annotations

import struct
import unittest

from core.jamulus_protocol import (
    _MAX_LEVEL_LIST_ENTRIES,
    _build_packet,
    _parse_le_string,
    _parse_level_list,
    _parse_packet,
)


class TestParseLevelListBounded(unittest.TestCase):
    def test_huge_payload_capped_to_max_entries(self):
        # 100 KB of level data — would otherwise produce 50,000 entries.
        big = b"\xff\x7f" * 50_000
        levels = _parse_level_list(big)
        self.assertLessEqual(len(levels), _MAX_LEVEL_LIST_ENTRIES)
        self.assertEqual(len(levels), _MAX_LEVEL_LIST_ENTRIES)

    def test_normal_payload_returns_exact_count(self):
        # 8 channels worth of data (uint16 each) — well under cap.
        normal = struct.pack("<HHHHHHHH", 0, 100, 200, 300, 400, 500, 600, 700)
        levels = _parse_level_list(normal)
        self.assertEqual(len(levels), 8)
        # Values normalized to 0..1.
        for v in levels.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)


class TestParsePacketDefensive(unittest.TestCase):
    def test_empty_bytes_returns_none(self):
        self.assertIsNone(_parse_packet(b""))

    def test_truncated_header_returns_none(self):
        # Less than the 8-byte minimum (6 header + 2 CRC).
        self.assertIsNone(_parse_packet(b"\x01\x02"))
        self.assertIsNone(_parse_packet(b"\x01\x02\x03\x04\x05\x06\x07"))

    def test_truncated_data_returns_none(self):
        # Header advertises 10 data bytes, but only 2 are present.
        raw = struct.pack("<HHH", 1, 0, 10) + b"\x00\x00"
        self.assertIsNone(_parse_packet(raw))

    def test_bad_crc_returns_none(self):
        # Build a valid packet then corrupt the trailing CRC.
        valid = _build_packet(msg_id=1, count=0, data=b"\x05\x00")
        corrupted = valid[:-2] + b"\xff\xff"
        # Sanity check: only the CRC differs.
        self.assertEqual(valid[:-2], corrupted[:-2])
        self.assertIsNone(_parse_packet(corrupted))

    def test_valid_packet_parses(self):
        # Quick positive control to make sure these tests aren't always
        # exercising the rejection branch.
        valid = _build_packet(msg_id=1, count=0, data=b"\xab\xcd")
        parsed = _parse_packet(valid)
        self.assertIsNotNone(parsed)
        msg_id, count, data = parsed
        self.assertEqual(msg_id, 1)
        self.assertEqual(count, 0)
        self.assertEqual(data, b"\xab\xcd")


class TestParseLeStringSafeOffsets(unittest.TestCase):
    def test_offset_past_end_returns_empty_and_unchanged_offset(self):
        data = b"\x05\x00hello"
        # Offset 10 is well past the buffer end.
        text, new_offset = _parse_le_string(data, 10)
        self.assertEqual(text, "")
        self.assertEqual(new_offset, 10)

    def test_offset_at_exact_end_returns_empty(self):
        data = b"\x05\x00hello"
        text, new_offset = _parse_le_string(data, len(data))
        self.assertEqual(text, "")
        self.assertEqual(new_offset, len(data))

    def test_length_overflows_buffer_truncates(self):
        # Length prefix says 100 but only 3 bytes follow — must clamp.
        data = b"\x64\x00abc"
        text, new_offset = _parse_le_string(data, 0)
        self.assertEqual(text, "abc")
        self.assertEqual(new_offset, len(data))


if __name__ == "__main__":
    unittest.main()

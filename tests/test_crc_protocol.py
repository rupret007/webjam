"""
Tests for CRC and packet building/parsing from core.jamulus_protocol.
"""
from __future__ import annotations

import struct

import pytest

from core.jamulus_protocol import (
    _build_packet,
    _crc16_ccitt,
    _parse_level_list,
    _parse_packet,
)


# ---------------------------------------------------------------------------
# CRC-16-CCITT tests
# ---------------------------------------------------------------------------

class TestCrc16Ccitt:
    def test_crc_known_value(self):
        """_crc16_ccitt(b'123456789') == 0xD64E.

        Standard CRC-16-CCITT with poly=0x1021, init=0xFFFF, xorout=0xFFFF.
        The widely-published check value 0x29B1 applies to xorout=0x0000;
        with xorout=0xFFFF the result is 0x29B1 ^ 0xFFFF = 0xD64E.
        """
        result = _crc16_ccitt(b"123456789")
        assert result == 0xD64E

    def test_crc_empty_bytes(self):
        """_crc16_ccitt(b'') is deterministic — does not crash and is reproducible."""
        result1 = _crc16_ccitt(b"")
        result2 = _crc16_ccitt(b"")
        assert isinstance(result1, int)
        assert result1 == result2

    def test_crc_empty_is_zero(self):
        """_crc16_ccitt(b'') == 0 because 0xFFFF ^ 0xFFFF == 0."""
        result = _crc16_ccitt(b"")
        assert result == 0x0000

    def test_crc_single_byte(self):
        """CRC of a single byte is deterministic."""
        result = _crc16_ccitt(b"\xAB")
        assert isinstance(result, int)
        assert 0x0000 <= result <= 0xFFFF


# ---------------------------------------------------------------------------
# Packet building / parsing
# ---------------------------------------------------------------------------

class TestBuildPacket:
    def test_build_packet_min(self):
        """_build_packet(1, 0, b'') produces at least 8 bytes."""
        packet = _build_packet(1, 0, b"")
        assert len(packet) >= 8

    def test_build_packet_length_formula(self):
        """Packet length = 6 (header) + len(data) + 2 (CRC)."""
        data = b"\x01\x02\x03"
        packet = _build_packet(5, 1, data)
        assert len(packet) == 6 + len(data) + 2

    def test_build_packet_header_fields(self):
        """The first 6 bytes encode msg_id, count, and data_len correctly."""
        msg_id, count, data = 7, 42, b"\xDE\xAD"
        packet = _build_packet(msg_id, count, data)
        parsed_id, parsed_count, parsed_len = struct.unpack_from("<HHH", packet, 0)
        assert parsed_id == msg_id
        assert parsed_count == count
        assert parsed_len == len(data)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestParseRoundtrip:
    @pytest.mark.parametrize("msg_id", [1, 3, 6, 7, 27])
    def test_parse_roundtrip_various_messages(self, msg_id):
        """For msg_ids 1,3,6,7,27, roundtrip build→parse recovers the same values."""
        count = 99
        data = bytes(range(msg_id % 10))  # small deterministic payload

        packet = _build_packet(msg_id, count, data)
        result = _parse_packet(packet)

        assert result is not None, f"_parse_packet returned None for msg_id={msg_id}"
        parsed_id, parsed_count, parsed_data = result
        assert parsed_id == msg_id
        assert parsed_count == count
        assert parsed_data == data

    def test_roundtrip_empty_payload(self):
        """A packet with empty data payload round-trips correctly."""
        packet = _build_packet(1, 0, b"")
        result = _parse_packet(packet)
        assert result is not None
        msg_id, count, data = result
        assert msg_id == 1
        assert count == 0
        assert data == b""


# ---------------------------------------------------------------------------
# Parse error handling
# ---------------------------------------------------------------------------

class TestParseErrors:
    def test_parse_truncated_packet(self):
        """_parse_packet with fewer than 8 bytes returns None."""
        for length in range(0, 8):
            result = _parse_packet(b"\x00" * length)
            assert result is None, f"Expected None for {length} bytes"

    def test_parse_corrupt_crc(self):
        """Flipping any CRC byte in a valid packet causes _parse_packet to return None."""
        packet = bytearray(_build_packet(7, 1, b"\x01\x02\x03\x04"))
        # CRC is the last 2 bytes
        packet[-1] ^= 0xFF  # flip all bits of last byte
        result = _parse_packet(bytes(packet))
        assert result is None

    def test_parse_corrupt_crc_first_byte(self):
        """Flipping the first CRC byte also causes parse failure."""
        packet = bytearray(_build_packet(3, 5, b"hello"))
        packet[-2] ^= 0x01  # flip 1 bit in first CRC byte
        result = _parse_packet(bytes(packet))
        assert result is None

    def test_parse_too_short_for_data(self):
        """If data_len in header claims more bytes than present, return None."""
        # Build a valid packet then truncate it
        packet = _build_packet(6, 0, b"\x00" * 20)
        truncated = packet[:10]  # cut off before data ends
        result = _parse_packet(truncated)
        assert result is None


# ---------------------------------------------------------------------------
# _parse_level_list tests
# ---------------------------------------------------------------------------

class TestParseLevelList:
    def test_level_list_midrange(self):
        """Single uint16 value of 16383 gives approximately 0.5."""
        data = struct.pack("<H", 16383)
        levels = _parse_level_list(data)
        assert 0 in levels
        assert abs(levels[0] - (16383 / 32767.0)) < 1e-6

    def test_level_list_max(self):
        """Single uint16 value of 32767 gives 1.0."""
        data = struct.pack("<H", 32767)
        levels = _parse_level_list(data)
        assert levels[0] == 1.0

    def test_level_list_zero(self):
        """Single uint16 value of 0 gives 0.0."""
        data = struct.pack("<H", 0)
        levels = _parse_level_list(data)
        assert levels[0] == 0.0

    def test_level_list_empty(self):
        """Empty data returns empty dict."""
        levels = _parse_level_list(b"")
        assert levels == {}

    def test_level_list_multiple_entries(self):
        """Multiple entries are indexed from 0."""
        data = struct.pack("<HHH", 0, 16383, 32767)
        levels = _parse_level_list(data)
        assert levels[0] == 0.0
        assert abs(levels[1] - (16383 / 32767.0)) < 1e-6
        assert levels[2] == 1.0

    def test_level_list_clamped_at_one(self):
        """Values that would exceed 1.0 are clamped (max value 32767 → 1.0)."""
        data = struct.pack("<H", 32767)
        levels = _parse_level_list(data)
        assert levels[0] <= 1.0

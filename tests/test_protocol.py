from __future__ import annotations

import unittest

from core.jamulus_protocol import JamulusProtocolAdapter


class TestProtocolAdapterInit(unittest.TestCase):
    def test_valid_init(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 22124)
        self.assertEqual(adapter.host, "127.0.0.1")
        self.assertEqual(adapter.port, 22124)
        self.assertFalse(adapter.enabled)

    def test_empty_host_raises(self):
        with self.assertRaises(ValueError):
            JamulusProtocolAdapter("", 22124)

    def test_none_host_raises(self):
        with self.assertRaises(ValueError):
            JamulusProtocolAdapter(None, 22124)

    def test_port_zero_raises(self):
        with self.assertRaises(ValueError):
            JamulusProtocolAdapter("127.0.0.1", 0)

    def test_port_65536_raises(self):
        with self.assertRaises(ValueError):
            JamulusProtocolAdapter("127.0.0.1", 65536)

    def test_port_negative_raises(self):
        with self.assertRaises(ValueError):
            JamulusProtocolAdapter("127.0.0.1", -1)

    def test_port_1_accepted(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 1)
        self.assertEqual(adapter.port, 1)

    def test_port_65535_accepted(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 65535)
        self.assertEqual(adapter.port, 65535)


class TestProtocolAdapterDisabled(unittest.TestCase):
    def setUp(self):
        self.adapter = JamulusProtocolAdapter("127.0.0.1", 22124, enabled=False)

    def test_connect_when_disabled_is_noop(self):
        self.adapter.connect()
        self.assertIsNone(self.adapter._sock)

    def test_request_clients_returns_cached(self):
        self.adapter.set_cached_participants({0: "Alice", 1: "Bob"})
        result = self.adapter.request_clients()
        self.assertEqual(result, {0: "Alice", 1: "Bob"})

    def test_apply_mixer_when_disabled_is_noop(self):
        self.adapter.apply_mixer(0, 100, 50, False)

    def test_close_when_no_socket_is_safe(self):
        self.adapter.close()


class TestProtocolBinaryParsing(unittest.TestCase):
    """Tests for the real Jamulus binary protocol parsers."""

    def _make_conn_clients_entry(self, channel_id: int, name: str) -> bytes:
        """Build a minimal CONN_CLIENTS_LIST entry (country=0, instrument=0, skill=0)."""
        import struct
        name_bytes = name.encode("utf-8")
        city_bytes = b""
        return (
            struct.pack("<H", channel_id)   # channel_id
            + struct.pack("<H", 0)          # country
            + struct.pack("<I", 0)          # instrument
            + struct.pack("<B", 0)          # skill_level
            + struct.pack("<H", len(name_bytes)) + name_bytes  # name string
            + struct.pack("<H", len(city_bytes)) + city_bytes  # city string
        )

    def test_parse_conn_clients_list_single(self):
        from core.jamulus_protocol import _parse_conn_clients_list
        data = self._make_conn_clients_entry(0, "Alice")
        result = _parse_conn_clients_list(data)
        self.assertEqual(result, {0: "Alice"})

    def test_parse_conn_clients_list_multiple(self):
        from core.jamulus_protocol import _parse_conn_clients_list
        data = self._make_conn_clients_entry(0, "Alice") + self._make_conn_clients_entry(1, "Bob")
        result = _parse_conn_clients_list(data)
        self.assertEqual(result, {0: "Alice", 1: "Bob"})

    def test_parse_conn_clients_list_empty_name_fallback(self):
        from core.jamulus_protocol import _parse_conn_clients_list
        data = self._make_conn_clients_entry(3, "")
        result = _parse_conn_clients_list(data)
        self.assertEqual(result, {3: "Participant 3"})

    def test_parse_conn_clients_list_empty_data(self):
        from core.jamulus_protocol import _parse_conn_clients_list
        result = _parse_conn_clients_list(b"")
        self.assertEqual(result, {})

    def test_parse_level_list_values(self):
        from core.jamulus_protocol import _parse_level_list
        import struct
        # Two channels: 0 (silence) and 32767 (max)
        data = struct.pack("<HH", 0, 32767)
        result = _parse_level_list(data)
        self.assertAlmostEqual(result[0], 0.0, places=3)
        self.assertAlmostEqual(result[1], 1.0, places=3)

    def test_parse_level_list_empty(self):
        from core.jamulus_protocol import _parse_level_list
        result = _parse_level_list(b"")
        self.assertEqual(result, {})

    def test_build_and_parse_roundtrip(self):
        from core.jamulus_protocol import _build_packet, _parse_packet
        payload = b"\x01\x02\x03\x04"
        raw = _build_packet(msg_id=3, count=1, data=payload)
        parsed = _parse_packet(raw)
        self.assertIsNotNone(parsed)
        msg_id, count, data = parsed
        self.assertEqual(msg_id, 3)
        self.assertEqual(count, 1)
        self.assertEqual(data, payload)

    def test_parse_packet_crc_mismatch_returns_none(self):
        from core.jamulus_protocol import _build_packet, _parse_packet
        raw = bytearray(_build_packet(msg_id=3, count=1, data=b"\x01"))
        raw[-1] ^= 0xFF  # corrupt CRC
        self.assertIsNone(_parse_packet(bytes(raw)))


class TestProtocolCachedParticipants(unittest.TestCase):
    def test_set_and_get_cached(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 22124)
        adapter.set_cached_participants({0: "Alice", 2: "Charlie"})
        names = adapter.get_cached_participants()
        self.assertEqual(names, ["Alice", "Charlie"])

    def test_empty_cache(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 22124)
        self.assertEqual(adapter.get_cached_participants(), [])


if __name__ == "__main__":
    unittest.main()

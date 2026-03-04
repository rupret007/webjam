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


class TestProtocolParsePayload(unittest.TestCase):
    def setUp(self):
        self.adapter = JamulusProtocolAdapter("127.0.0.1", 22124)

    def test_parse_valid_payload(self):
        data = b"0:Alice\n1:Bob"
        result = self.adapter._parse_clients_payload(data)
        self.assertEqual(result, {0: "Alice", 1: "Bob"})

    def test_parse_empty_bytes(self):
        result = self.adapter._parse_clients_payload(b"")
        self.assertEqual(result, {})

    def test_parse_non_utf8_binary(self):
        result = self.adapter._parse_clients_payload(b"\xff\xfe\xfd")
        self.assertEqual(result, {})

    def test_parse_lines_without_colon_skipped(self):
        data = b"0:Alice\nno_colon_here\n2:Charlie"
        result = self.adapter._parse_clients_payload(data)
        self.assertEqual(result, {0: "Alice", 2: "Charlie"})

    def test_parse_non_numeric_index_skipped(self):
        data = b"abc:Alice\n1:Bob"
        result = self.adapter._parse_clients_payload(data)
        self.assertEqual(result, {1: "Bob"})

    def test_parse_empty_name_uses_fallback(self):
        data = b"0:"
        result = self.adapter._parse_clients_payload(data)
        self.assertEqual(result, {0: "Participant 0"})


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

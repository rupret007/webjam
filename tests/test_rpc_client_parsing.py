"""Parsing / event-handling tests for JamulusRpcClient.

Exercises the response-parsing and SSE-handling paths against malformed and
hostile RPC payloads (the client talks to a localhost RPC port that any local
process could answer), with no real server — everything is mocked.
"""
from __future__ import annotations

import unittest
from unittest import mock

import httpx

from core.jamulus_rpc_client import JamulusRpcClient


def _client():
    c = JamulusRpcClient(port=22222)
    c._local_channel_id = 0  # short-circuit the getClientInfo lookup
    return c


class TestGetChannelClients(unittest.TestCase):
    def test_parses_valid_list(self):
        c = _client()
        with mock.patch.object(c, "_call", return_value={"result": [
            {"channelId": 0, "name": "Me", "instrument": "Bass"},
            {"channelId": 1, "name": "Alice"},
        ]}):
            clients = c.get_channel_clients()
        self.assertEqual([ci.channel_id for ci in clients], [0, 1])
        self.assertEqual(clients[0].name, "Me")
        self.assertTrue(clients[0].is_local)         # channel 0 == cached local id
        self.assertEqual(clients[1].name, "Alice")

    def test_none_when_call_fails(self):
        c = _client()
        with mock.patch.object(c, "_call", return_value=None):
            self.assertIsNone(c.get_channel_clients())

    def test_none_when_result_not_a_list(self):
        c = _client()
        with mock.patch.object(c, "_call", return_value={"result": {"oops": 1}}):
            self.assertIsNone(c.get_channel_clients())

    def test_skips_malformed_entries(self):
        c = _client()
        with mock.patch.object(c, "_call", return_value={"result": [
            "not a dict",
            {"name": "no channel id"},          # missing channelId -> default -1 -> skipped
            {"channelId": -5, "name": "neg"},   # negative -> skipped
            {"channelId": "x", "name": "str"},  # non-int -> skipped
            {"channelId": 2, "name": "Bob"},    # the only valid one
        ]}):
            clients = c.get_channel_clients()
        self.assertEqual([ci.channel_id for ci in clients], [2])

    def test_missing_name_gets_placeholder(self):
        c = _client()
        with mock.patch.object(c, "_call", return_value={"result": [
            {"channelId": 3, "name": ""},
        ]}):
            clients = c.get_channel_clients()
        self.assertEqual(clients[0].name, "Participant 3")


class TestLocalChannelId(unittest.TestCase):
    def test_caches_after_first_lookup(self):
        c = JamulusRpcClient(port=22222)
        with mock.patch.object(c, "_call", return_value={"result": {"channelId": 4}}) as call:
            self.assertEqual(c._get_local_channel_id(), 4)
            self.assertEqual(c._get_local_channel_id(), 4)  # cached
        self.assertEqual(call.call_count, 1)

    def test_malformed_channel_id_returns_minus_one_without_raising(self):
        c = JamulusRpcClient(port=22222)
        with mock.patch.object(c, "_call", return_value={"result": {"channelId": "bogus"}}):
            self.assertEqual(c._get_local_channel_id(), -1)  # must not raise

    def test_none_result(self):
        c = JamulusRpcClient(port=22222)
        with mock.patch.object(c, "_call", return_value=None):
            self.assertEqual(c._get_local_channel_id(), -1)


class TestSseEvent(unittest.TestCase):
    def test_level_list_parsed_and_clamped(self):
        c = _client()
        got = {}
        c._on_levels = lambda d: got.update(d)
        # 32767 -> 1.0, 0 -> 0.0, over-range -> clamped, junk -> skipped
        c._handle_sse_event("channelLevelListReceived",
                            '{"levels": [32767, 0, 99999, "junk"]}')
        self.assertEqual(got[0], 1.0)
        self.assertEqual(got[1], 0.0)
        self.assertEqual(got[2], 1.0)        # clamped to 1.0
        self.assertNotIn(3, got)             # "junk" skipped, didn't raise

    def test_malformed_json_does_not_raise(self):
        c = _client()
        c._handle_sse_event("channelLevelListReceived", "{not json")  # must not raise

    def test_channel_connected_triggers_participant_refresh(self):
        c = _client()
        seen = []
        c._on_participants_changed = lambda clients: seen.append(clients)
        with mock.patch.object(c, "get_channel_clients",
                               return_value=["fake-client"]):
            c._handle_sse_event("channelConnected", "{}")
        self.assertEqual(seen, [["fake-client"]])

    def test_any_event_stamps_heartbeat(self):
        c = _client()
        c._last_activity_at = 0.0
        c._handle_sse_event("somethingUnknown", "")
        self.assertGreater(c._last_activity_at, 0.0)


class TestCall(unittest.TestCase):
    def test_connect_error_returns_none(self):
        c = _client()
        with mock.patch("httpx.post", side_effect=httpx.ConnectError("no server")):
            self.assertIsNone(c._call("jamulus/getChannelClients", {}))

    def test_generic_error_returns_none(self):
        c = _client()
        with mock.patch("httpx.post", side_effect=ValueError("boom")):
            self.assertIsNone(c._call("jamulus/getChannelClients", {}))


if __name__ == "__main__":
    unittest.main()

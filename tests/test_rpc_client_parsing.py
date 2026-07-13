"""Parsing / notification-handling tests for JamulusRpcClient.

Exercises the client's response/notification parsing against malformed and
hostile payloads (it talks to a localhost socket any local process could
answer) without a real server — calling the handlers directly.
"""
from __future__ import annotations

import unittest

from core.jamulus_rpc_client import JamulusRpcClient


def _client():
    seen = {"participants": [], "levels": {}}
    c = JamulusRpcClient(
        port=22222,
        on_participants_changed=lambda lst: seen["participants"].append(lst),
        on_levels=lambda d: seen["levels"].update(d),
    )
    return c, seen


class TestUpdateClients(unittest.TestCase):
    def test_parses_valid_list(self):
        c, seen = _client()
        c._set_local_id(0)
        c._update_clients([
            {"id": 0, "name": "Me", "instrument": "Bass"},
            {"id": 1, "name": "Alice"},
        ])
        last = seen["participants"][-1]
        self.assertEqual([ci.channel_id for ci in last], [0, 1])
        self.assertTrue(last[0].is_local)
        self.assertEqual(last[1].name, "Alice")

    def test_skips_malformed_entries(self):
        c, seen = _client()
        c._update_clients([
            "not a dict",
            {"name": "no id"},           # missing id -> falls back to index 1 -> kept? no: index used
            {"id": -5, "name": "neg"},   # negative -> skipped
            {"id": "x", "name": "str"},  # non-int -> skipped
            {"id": 2, "name": "Bob"},
        ])
        last = seen["participants"][-1]
        # Only well-formed, non-negative integer ids survive (1 via index fallback, 2 explicit)
        ids = [ci.channel_id for ci in last]
        self.assertIn(2, ids)
        self.assertNotIn(-5, ids)

    def test_non_list_is_noop(self):
        c, seen = _client()
        c._update_clients({"oops": 1})
        self.assertEqual(seen["participants"], [])

    def test_missing_name_gets_placeholder(self):
        c, seen = _client()
        c._update_clients([{"id": 3, "name": ""}])
        self.assertEqual(seen["participants"][-1][0].name, "Participant 3")


class TestEmitLevels(unittest.TestCase):
    def test_levels_normalized_and_clamped(self):
        c, seen = _client()
        # channelLevelList is 0..9; junk skipped; out-of-range clamped
        c._emit_levels([9, 0, 99, "junk"])
        self.assertEqual(seen["levels"][0], 1.0)
        self.assertEqual(seen["levels"][1], 0.0)
        self.assertEqual(seen["levels"][2], 1.0)  # clamped
        self.assertNotIn(3, seen["levels"])       # "junk" skipped, no raise

    def test_non_list_is_noop(self):
        c, seen = _client()
        c._emit_levels("nope")
        self.assertEqual(seen["levels"], {})


class TestLocalId(unittest.TestCase):
    def test_valid_sets_and_retags(self):
        c, seen = _client()
        c._update_clients([{"id": 4, "name": "X"}])
        self.assertFalse(seen["participants"][-1][0].is_local)
        c._set_local_id(4)
        self.assertEqual(c._get_local_channel_id(), 4)
        self.assertTrue(c._clients[0].is_local)
        self.assertTrue(seen["participants"][-1][0].is_local)
        self.assertEqual(len(seen["participants"]), 2)

    def test_malformed_does_not_raise(self):
        c, _ = _client()
        c._set_local_id("bogus")   # must not raise
        c._set_local_id(None)
        self.assertEqual(c._get_local_channel_id(), -1)

    def test_real_312_channel_info_shape_matches_roster_without_id(self):
        c, seen = _client()
        c._handle_response("jamulusclient/getChannelInfo", {
            "city": "",
            "country": "United States",
            "instrument": "None",
            "name": "Jeff Story",
            "skillLevel": None,
        })
        c._handle_response("jamulusclient/getClientList", {"clients": [
            {"id": 0, "name": "Jeff Story", "instrument": "None",
             "skillLevel": None, "country": "United States", "city": ""},
            {"id": 1, "name": "Taylor", "instrument": "Drums"},
        ]})
        self.assertEqual(c._get_local_channel_id(), 0)
        self.assertTrue(seen["participants"][-1][0].is_local)
        self.assertFalse(seen["participants"][-1][1].is_local)

    def test_real_channel_info_retags_when_list_arrives_first(self):
        c, seen = _client()
        c._update_clients([{"id": 7, "name": "Jeff Story"}])
        self.assertFalse(seen["participants"][-1][0].is_local)
        c._handle_response("jamulusclient/getChannelInfo", {
            "name": "Jeff Story", "instrument": None, "skillLevel": None,
            "country": None, "city": None,
        })
        self.assertEqual(c._get_local_channel_id(), 7)
        self.assertTrue(seen["participants"][-1][0].is_local)

    def test_duplicate_identical_profiles_are_not_guessed(self):
        c, seen = _client()
        c._handle_response("jamulusclient/getChannelInfo", {
            "name": "Alex", "instrument": "Guitar",
        })
        c._update_clients([
            {"id": 2, "name": "Alex", "instrument": "Guitar"},
            {"id": 5, "name": "Alex", "instrument": "Guitar"},
        ])
        self.assertEqual(c._get_local_channel_id(), -1)
        self.assertFalse(any(person.is_local for person in seen["participants"][-1]))


class TestGetChannelClientsCache(unittest.TestCase):
    def test_none_when_unavailable(self):
        c, _ = _client()
        self.assertIsNone(c.get_channel_clients())

    def test_returns_cache_when_available(self):
        c, _ = _client()
        c._available = True
        c._update_clients([{"id": 0, "name": "Me"}])
        clients = c.get_channel_clients()
        self.assertEqual([ci.name for ci in clients], ["Me"])


class TestDispatch(unittest.TestCase):
    def test_notification_routed_and_stamps_heartbeat(self):
        c, seen = _client()
        c._last_activity_at = 0.0
        c._dispatch_obj({
            "jsonrpc": "2.0",
            "method": "jamulusclient/channelLevelListReceived",
            "params": {"channelLevelList": [9]},
        })
        self.assertEqual(seen["levels"][0], 1.0)
        self.assertGreater(c._last_activity_at, 0.0)

    def test_response_routed_by_inflight_method(self):
        c, seen = _client()
        # simulate an in-flight getClientList whose response now arrives
        c._inflight[7] = "jamulusclient/getClientList"
        c._dispatch_obj({
            "jsonrpc": "2.0", "id": 7,
            "result": {"clients": [{"id": 1, "name": "Alice"}]},
        })
        self.assertEqual(seen["participants"][-1][0].name, "Alice")


if __name__ == "__main__":
    unittest.main()

"""Tests for JamulusRpcClient.last_activity_age — hung-Jamulus detection."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core.jamulus_rpc_client import JamulusRpcClient


class TestRpcHeartbeat(unittest.TestCase):
    def test_initial_age_is_infinite(self):
        """Before any successful interaction, age must be inf."""
        client = JamulusRpcClient(port=22222)
        self.assertEqual(client.last_activity_age(), float("inf"))

    def test_age_resets_after_stamp(self):
        """Stamping the heartbeat resets the age to ~0."""
        client = JamulusRpcClient(port=22222)
        client._last_activity_at = time.monotonic()
        # Allow a tiny window for monotonic clock granularity
        self.assertLess(client.last_activity_age(), 0.5)

    def test_age_grows_with_time(self):
        """If the heartbeat is stale, age increases."""
        client = JamulusRpcClient(port=22222)
        client._last_activity_at = time.monotonic() - 10.0
        age = client.last_activity_age()
        self.assertGreaterEqual(age, 9.5)
        self.assertLess(age, 10.5)

    def test_any_dispatched_message_stamps_heartbeat(self):
        """Any inbound JSON-RPC message refreshes the heartbeat, even an
        unknown notification type."""
        client = JamulusRpcClient(port=22222)
        client._last_activity_at = 0.0
        client._dispatch_obj({
            "jsonrpc": "2.0",
            "method": "jamulusclient/someUnknownEvent",
            "params": {},
        })
        self.assertGreater(client._last_activity_at, 0.0)

    def test_start_resets_stale_heartbeat_across_restart(self):
        """A reused client must not carry a previous session's heartbeat.

        The controller keeps one JamulusRpcClient across stop()/start()
        cycles.  Without resetting _last_activity_at on start(), a stale
        timestamp from the prior session makes last_activity_age() return a
        finite, growing age instead of inf right after relaunch — which can
        trip a false "Jamulus stopped responding" banner on a healthy server.
        """
        client = JamulusRpcClient(port=22222)
        # Simulate a previous session that last succeeded 100s ago.
        client._last_activity_at = time.monotonic() - 100.0
        client.stop()
        try:
            client.start()
            # Right after restart, before any new success, age must be inf.
            self.assertEqual(client.last_activity_age(), float("inf"))
        finally:
            client.stop()


if __name__ == "__main__":
    unittest.main()

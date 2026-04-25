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

    def test_sse_event_stamps_heartbeat(self):
        """Any SSE event should refresh the heartbeat, even unhandled types."""
        client = JamulusRpcClient(port=22222)
        before = time.monotonic()
        # _handle_sse_event wraps the stamp + dispatch in the same code path
        with patch.object(client, "get_channel_clients", return_value=None):
            client._handle_sse_event("someUnknownEvent", "")
        self.assertGreaterEqual(client._last_activity_at, before)


if __name__ == "__main__":
    unittest.main()

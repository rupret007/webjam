"""
After 5 failed Jamulus reconnect attempts, the BridgeService stops trying and
leaves jamulus_state at "Not running". This ensures we don't hammer the user
with a reconnect loop forever when their Jamulus install is broken.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tests.support.component_store import isolated_component_store_root


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.jamulus_server = "jam.example.com"
    s.jamulus_port = 22124
    s.jamulus_rpc_port = 22222
    s.jamulus_candidates = []
    s.webex_url = "https://example.webex.com/m/x"
    return s


def _make_bridge():
    from services.bridge_service import BridgeService

    repository = MagicMock()
    repository.get_setting.return_value = "1"  # auto-reconnect enabled

    ui_callbacks = {
        "set_status_banner": MagicMock(),
        "refresh_readiness": MagicMock(),
        "show_actionable_error": MagicMock(),
        "show_message": MagicMock(),
        "shutdown_requested": lambda: False,
        # run scheduled UI callbacks synchronously
        "schedule_ui_callback": lambda f: f(),
    }

    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=repository,
        settings=_make_settings(),
        ui_callbacks=ui_callbacks,
        component_store_root=isolated_component_store_root(),
    )
    return bridge


class TestJamulusReconnectMaxAttempts(unittest.TestCase):
    def test_jamulus_reconnect_caps_at_5_attempts_then_gives_up(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = True
        # Auto-reconnect is recovery for a client WebJam previously owned,
        # never a substitute for an initial launch that has not established
        # a process yet.
        bridge.jamulus_process = MagicMock()
        bridge.jamulus_process.poll.return_value = 1

        # find_jamulus -> None means launch_jamulus(reconnect=True) sets state
        # to "Not running" and bails immediately (no subprocess started).
        bridge.find_jamulus = MagicMock(return_value=None)

        # Walk monotonic time forward past each backoff window so the
        # next reconnect actually fires.
        now = 1000.0
        for _ in range(7):  # try more than 5 to confirm it caps
            bridge._attempt_auto_reconnect_jamulus(now=now)
            # Advance past the next scheduled retry (delay capped at 45s).
            now = bridge.jamulus_next_reconnect_at + 1.0

        # After 5 failures we cap and stop. attempts can't exceed 5.
        self.assertEqual(bridge.jamulus_reconnect_attempts, 5)
        # After the failed reconnect each launch_jamulus(reconnect=True) call
        # set state to "Not running" because find_jamulus returned None.
        self.assertEqual(bridge.jamulus_state, "Not running")
        # No reconnect_inflight latch remaining.
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        # Metric for failed reconnect should be incremented at least 5 times.
        failed_calls = [
            c for c in bridge.metrics_service.increment.call_args_list
            if c.args and c.args[0] == "metric_jamulus_reconnect_failed"
        ]
        self.assertGreaterEqual(len(failed_calls), 5)

        # Further calls should be no-ops (cap holds).
        before = bridge.jamulus_reconnect_attempts
        bridge._attempt_auto_reconnect_jamulus(now=now + 100.0)
        self.assertEqual(bridge.jamulus_reconnect_attempts, before)


if __name__ == "__main__":
    unittest.main()

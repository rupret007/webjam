"""Terminal recovery behavior for a permanently missing Jamulus runtime."""
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
    def test_missing_executable_terminalizes_after_one_nonrecoverable_attempt(self):
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

        # Repeated timer ticks must not retry a condition that cannot recover
        # without an external runtime install/update.
        now = 1000.0
        for _ in range(7):
            bridge._attempt_auto_reconnect_jamulus(now=now)
            now = bridge.jamulus_next_reconnect_at + 1.0

        self.assertEqual(bridge.jamulus_reconnect_attempts, 1)
        self.assertEqual(bridge.jamulus_state, "Not running")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        self.assertFalse(bridge.jamulus_launch_intended)
        snapshot = bridge.jamulus_recovery_snapshot(now=now)
        self.assertTrue(snapshot.active)
        self.assertTrue(snapshot.exhausted)
        bridge.find_jamulus.assert_called_once_with()

        failed_calls = [
            c for c in bridge.metrics_service.increment.call_args_list
            if c.args and c.args[0] == "metric_jamulus_reconnect_failed"
        ]
        self.assertEqual(len(failed_calls), 1)

        before = bridge.jamulus_reconnect_attempts
        bridge._attempt_auto_reconnect_jamulus(now=now + 100.0)
        self.assertEqual(bridge.jamulus_reconnect_attempts, before)


if __name__ == "__main__":
    unittest.main()

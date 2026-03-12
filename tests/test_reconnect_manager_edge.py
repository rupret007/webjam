from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from webjam_app_enhanced import WebJamEnhancedApp


class TestReconnectManagerEdge(unittest.TestCase):
    def test_reconnect_delay_is_exponential_and_capped(self):
        self.assertEqual(WebJamEnhancedApp._reconnect_delay_seconds(1), 1.5)
        self.assertEqual(WebJamEnhancedApp._reconnect_delay_seconds(2), 3.0)
        self.assertEqual(WebJamEnhancedApp._reconnect_delay_seconds(3), 6.0)
        self.assertEqual(WebJamEnhancedApp._reconnect_delay_seconds(10), 45.0)

    def test_auto_reconnect_jamulus_triggers_when_intended_and_process_down(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.auto_reconnect_enabled = True
        app._jamulus_launch_intended = True
        app._webex_launch_intended = False
        app._jamulus_reconnect_attempts = 0
        app._jamulus_next_reconnect_at = 0.0
        app._jamulus_reconnect_inflight = False
        app.jamulus_process = MagicMock()
        app.jamulus_process.poll.return_value = 1
        app.metrics_service = MagicMock()
        app._launch_jamulus = MagicMock()
        app.webex_controller = MagicMock()
        app.webex_state = "Not opened"

        WebJamEnhancedApp._attempt_auto_reconnects(app, now=100.0)

        app.metrics_service.increment.assert_any_call("metric_jamulus_reconnect_attempt")
        app._launch_jamulus.assert_called_once_with(manual=False, reconnect=True)
        self.assertEqual(app._jamulus_reconnect_attempts, 1)
        self.assertGreater(app._jamulus_next_reconnect_at, 100.0)
        self.assertTrue(app._jamulus_reconnect_inflight)

    def test_auto_reconnect_webex_triggers_on_open_failed_state(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.auto_reconnect_enabled = True
        app._jamulus_launch_intended = False
        app._webex_launch_intended = True
        app._webex_reconnect_attempts = 0
        app._webex_next_reconnect_at = 0.0
        app._webex_reconnect_inflight = False
        app.webex_controller = MagicMock()
        app.webex_controller.is_connected = False
        app.webex_state = "Open failed"
        app.metrics_service = MagicMock()
        app._launch_webex = MagicMock()

        WebJamEnhancedApp._attempt_auto_reconnects(app, now=50.0)

        app.metrics_service.increment.assert_any_call("metric_webex_reconnect_attempt")
        app._launch_webex.assert_called_once_with(manual=False, reconnect=True)
        self.assertEqual(app._webex_reconnect_attempts, 1)
        self.assertGreater(app._webex_next_reconnect_at, 50.0)
        self.assertTrue(app._webex_reconnect_inflight)

    def test_auto_reconnect_disabled_skips_retries(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.auto_reconnect_enabled = False
        app._jamulus_launch_intended = True
        app.jamulus_process = MagicMock()
        app.jamulus_process.poll.return_value = 1
        app.metrics_service = MagicMock()
        app._launch_jamulus = MagicMock()
        app._launch_webex = MagicMock()
        app.webex_controller = MagicMock()
        app.webex_state = "Open failed"

        WebJamEnhancedApp._attempt_auto_reconnects(app, now=10.0)

        app.metrics_service.increment.assert_not_called()
        app._launch_jamulus.assert_not_called()
        app._launch_webex.assert_not_called()

    def test_auto_reconnect_jamulus_resets_when_process_running(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._jamulus_launch_intended = True
        app._jamulus_reconnect_attempts = 3
        app._jamulus_next_reconnect_at = 999.0
        app._jamulus_reconnect_inflight = True
        app.jamulus_process = MagicMock()
        app.jamulus_process.poll.return_value = None

        WebJamEnhancedApp._attempt_auto_reconnect_jamulus(app, now=5.0)

        self.assertEqual(app._jamulus_reconnect_attempts, 0)
        self.assertEqual(app._jamulus_next_reconnect_at, 0.0)
        self.assertFalse(app._jamulus_reconnect_inflight)

    def test_manual_launch_calls_reset_reconnect_state(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._jamulus_launch_intended = False
        app._jamulus_reconnect_attempts = 4
        app._jamulus_next_reconnect_at = 12.0
        app._webex_launch_intended = False
        app._webex_reconnect_attempts = 2
        app._webex_next_reconnect_at = 14.0
        app._launch_jamulus = MagicMock()
        app._launch_webex = MagicMock()

        WebJamEnhancedApp.launch_jamulus(app)
        WebJamEnhancedApp.launch_webex(app)

        self.assertTrue(app._jamulus_launch_intended)
        self.assertEqual(app._jamulus_reconnect_attempts, 0)
        self.assertEqual(app._jamulus_next_reconnect_at, 0.0)
        app._launch_jamulus.assert_called_once_with(manual=True, reconnect=False)

        self.assertTrue(app._webex_launch_intended)
        self.assertEqual(app._webex_reconnect_attempts, 0)
        self.assertEqual(app._webex_next_reconnect_at, 0.0)
        app._launch_webex.assert_called_once_with(manual=True, reconnect=False)


if __name__ == "__main__":
    unittest.main()

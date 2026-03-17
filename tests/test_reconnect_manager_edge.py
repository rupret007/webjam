from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


class _RootImmediate:
    def after(self, _delay_ms, callback):
        callback()


class _RootAfterFails:
    def winfo_exists(self):
        return True

    def after(self, *_args, **_kwargs):
        raise RuntimeError("root closed")


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

    @patch("webjam_app_enhanced.messagebox.showinfo")
    @patch("webjam_app_enhanced.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    @patch("webjam_app_enhanced.subprocess.Popen")
    def test_launch_jamulus_sets_running_state_after_process_starts(self, popen_mock, _thread_mock, _info_mock):
        fake_proc = MagicMock()
        popen_mock.return_value = fake_proc
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootImmediate()
        app.metrics_service = MagicMock()
        app._refresh_endpoint_state = MagicMock()
        app.find_jamulus = MagicMock(return_value="C:/Jamulus.exe")
        app.jamulus_process = None
        app.jamulus_server = "jam.example.com"
        app.jamulus_port = 22124
        app._set_status_banner = MagicMock()
        app._refresh_readiness = MagicMock()
        app._retry_action = lambda action, attempts=3, base_delay=0.5: action()
        app.jamulus_controller = MagicMock()
        app._show_actionable_error = MagicMock()
        app._jamulus_reconnect_attempts = 1
        app._jamulus_next_reconnect_at = 5.0
        app._jamulus_reconnect_inflight = True

        WebJamEnhancedApp._launch_jamulus(app, manual=True, reconnect=False)

        self.assertEqual(app.jamulus_state, "Running")
        app.jamulus_controller.add_participant.assert_called_once_with("You (Local)", 0)
        app.metrics_service.increment.assert_any_call("metric_jamulus_launch_success")
        self.assertFalse(app._jamulus_reconnect_inflight)
        self.assertEqual(app._jamulus_reconnect_attempts, 0)
        self.assertEqual(app._jamulus_next_reconnect_at, 0.0)

    @patch("webjam_app_enhanced.messagebox.showinfo")
    @patch("webjam_app_enhanced.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    @patch("webjam_app_enhanced.subprocess.Popen")
    def test_launch_jamulus_keeps_success_state_when_ui_scheduling_fails(self, popen_mock, _thread_mock, _info_mock):
        fake_proc = MagicMock()
        popen_mock.return_value = fake_proc
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootAfterFails()
        app.metrics_service = MagicMock()
        app._refresh_endpoint_state = MagicMock()
        app.find_jamulus = MagicMock(return_value="C:/Jamulus.exe")
        app.jamulus_process = None
        app.jamulus_server = "jam.example.com"
        app.jamulus_port = 22124
        app._set_status_banner = MagicMock()
        app._refresh_readiness = MagicMock()
        app._retry_action = lambda action, attempts=3, base_delay=0.5: action()
        app.jamulus_controller = MagicMock()
        app._show_actionable_error = MagicMock()
        app._jamulus_reconnect_attempts = 1
        app._jamulus_next_reconnect_at = 5.0
        app._jamulus_reconnect_inflight = True

        WebJamEnhancedApp._launch_jamulus(app, manual=True, reconnect=False)

        self.assertEqual(app.jamulus_state, "Running")
        app.metrics_service.increment.assert_any_call("metric_jamulus_launch_success")
        self.assertNotIn("metric_jamulus_launch_failed", [args[0] for args, _kwargs in app.metrics_service.increment.call_args_list])
        app._show_actionable_error.assert_not_called()
        self.assertFalse(app._jamulus_reconnect_inflight)

    @patch("webjam_app_enhanced.messagebox.showinfo")
    @patch("webjam_app_enhanced.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    def test_launch_webex_keeps_success_state_when_ui_scheduling_fails(self, _thread_mock, _info_mock):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootAfterFails()
        app.metrics_service = MagicMock()
        app.webex_controller = MagicMock()
        app.webex_controller.join_meeting.return_value = True
        app.webex_url = "https://example.webex.com/meet/test"
        app._set_status_banner = MagicMock()
        app._refresh_readiness = MagicMock()
        app._retry_action = lambda action, attempts=3, base_delay=0.5: action()
        app._show_actionable_error = MagicMock()
        app._webex_reconnect_attempts = 2
        app._webex_next_reconnect_at = 7.0
        app._webex_reconnect_inflight = True

        WebJamEnhancedApp._launch_webex(app, manual=True, reconnect=False)

        self.assertEqual(app.webex_state, "Opened in browser")
        app.metrics_service.increment.assert_any_call("metric_webex_open_success")
        self.assertNotIn("metric_webex_open_failed", [args[0] for args, _kwargs in app.metrics_service.increment.call_args_list])
        app._show_actionable_error.assert_not_called()
        self.assertFalse(app._webex_reconnect_inflight)


if __name__ == "__main__":
    unittest.main()

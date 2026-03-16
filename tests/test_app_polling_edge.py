from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


class _RootClosed:
    def winfo_exists(self):
        return False

    def after(self, *_args, **_kwargs):
        raise AssertionError("after should not be called for closed root")


class _RootRaisesTcl:
    def winfo_exists(self):
        raise tk.TclError("destroyed")

    def after(self, *_args, **_kwargs):
        raise tk.TclError("destroyed")


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


class _RootAfterRecorder:
    def __init__(self):
        self.calls = []

    def after(self, delay, callback):
        self.calls.append((delay, callback))
        return "after-id"

    def winfo_exists(self):
        return True


class TestAppPollingEdge(unittest.TestCase):
    def test_poll_connection_health_returns_when_root_closed(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootClosed()
        app._refresh_endpoint_state = MagicMock()
        app._measure_server_latency_async = MagicMock()
        app._refresh_readiness = MagicMock()

        WebJamEnhancedApp._poll_connection_health(app)

        app._refresh_endpoint_state.assert_not_called()
        app._measure_server_latency_async.assert_not_called()
        app._refresh_readiness.assert_not_called()

    def test_complete_latency_probe_handles_tcl_error(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootRaisesTcl()
        app._latency_probe_inflight = True
        app.network_latency_ms = None
        app._update_latency_widget = MagicMock()

        WebJamEnhancedApp._complete_latency_probe(app, 12.0)

        self.assertFalse(app._latency_probe_inflight)
        self.assertEqual(app.network_latency_ms, 12.0)
        app._update_latency_widget.assert_not_called()

    @patch("webjam_app_enhanced.socket.create_connection", side_effect=OSError("offline"))
    @patch("webjam_app_enhanced.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    def test_measure_latency_resets_inflight_when_root_after_fails(self, _thread, _socket):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootRaisesTcl()
        app._latency_probe_inflight = False
        app.jamulus_server = "127.0.0.1"
        app.jamulus_port = 22124
        app.network_latency_ms = 1.0
        app._refresh_endpoint_state = MagicMock()
        app._complete_latency_probe = MagicMock(side_effect=AssertionError("should not be called from worker thread"))

        WebJamEnhancedApp._measure_server_latency_async(app)

        self.assertFalse(app._latency_probe_inflight)
        self.assertIsNone(app.network_latency_ms)
        app._complete_latency_probe.assert_not_called()

    def test_start_background_services_rolls_back_partial_start_and_schedules_retry(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RootAfterRecorder()
        app.api_bridge = MagicMock()
        app.api_bridge.start.return_value = True
        app.jamulus_controller = MagicMock()
        app.audio_monitor = MagicMock()
        app.audio_monitor.start.side_effect = RuntimeError("audio start failed")
        app.webex_controller = MagicMock()
        app._set_status_banner = MagicMock()
        app._refresh_readiness = MagicMock()
        app._kick_background_services = MagicMock()
        app._service_bootstrapped = False
        app._service_start_inflight = True
        app._service_start_attempts = 0
        app._startup_started_at = 0.0

        WebJamEnhancedApp._start_background_services(app)

        self.assertFalse(app._service_bootstrapped)
        self.assertFalse(app._service_start_inflight)
        app.jamulus_controller.start.assert_called_once()
        app.jamulus_controller.stop.assert_called_once()
        app.api_bridge.stop.assert_called_once()
        app.webex_controller.start.assert_not_called()
        self.assertEqual(app._service_start_attempts, 1)
        self.assertEqual(app.root.calls[0][0], 0)

        app.root.calls[0][1]()

        app._set_status_banner.assert_called_once()
        self.assertEqual(app.root.calls[1][0], 1500)
        self.assertIs(app.root.calls[1][1], app._kick_background_services)

    def test_selected_channel_shortcut_toggles_mute(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        channel = MagicMock()
        app._text_input_has_focus = MagicMock(return_value=False)
        app._selected_mixer_channel = MagicMock(return_value=channel)
        app._refresh_readiness = MagicMock()

        result = WebJamEnhancedApp._selected_channel_shortcut_handler(app, "mute")

        channel.toggle_mute.assert_called_once()
        channel.toggle_solo.assert_not_called()
        app._refresh_readiness.assert_called_once()
        self.assertEqual(result, "break")

    def test_selected_channel_shortcut_ignores_text_input_focus(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        channel = MagicMock()
        app._text_input_has_focus = MagicMock(return_value=True)
        app._selected_mixer_channel = MagicMock(return_value=channel)
        app._refresh_readiness = MagicMock()

        result = WebJamEnhancedApp._selected_channel_shortcut_handler(app, "solo")

        channel.toggle_mute.assert_not_called()
        channel.toggle_solo.assert_not_called()
        app._refresh_readiness.assert_not_called()
        self.assertIsNone(result)

    def test_bind_shortcuts_registers_documented_selected_channel_keys(self):
        class _Root:
            def __init__(self):
                self.bound = {}

            def bind(self, event, callback):
                self.bound[event] = callback

        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _Root()
        app._safe_invoke = MagicMock()
        app._space_key_handler = MagicMock()
        app.save_mix = MagicMock()
        app.load_mix = MagicMock()
        app.quit_app = MagicMock()
        app.show_help = MagicMock()
        app.launch_jamulus = MagicMock()
        app.launch_webex = MagicMock()
        app.reset_all_faders = MagicMock()
        app.center_all_pans = MagicMock()
        app.increase_text_size = MagicMock()
        app.decrease_text_size = MagicMock()
        app.toggle_high_contrast = MagicMock()

        WebJamEnhancedApp._bind_shortcuts(app)

        self.assertIn("<Key-m>", app.root.bound)
        self.assertIn("<Key-s>", app.root.bound)

    def test_refresh_readiness_ignores_local_placeholder_for_ready_state(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.get_participants.return_value = [
            SimpleNamespace(name="You (Local)", channel_id=0),
        ]
        app.mode_key = "music_jam"
        app.review_state = "draft"
        app.readiness_label = MagicMock()
        app.connection_summary = MagicMock()
        app._set_status_banner = MagicMock()
        app.jamulus_state = "Running"
        app.webex_state = "Not opened"

        WebJamEnhancedApp._refresh_readiness(app)

        app.readiness_label.configure.assert_called_once_with(text="Room: waiting for participants")


if __name__ == "__main__":
    unittest.main()

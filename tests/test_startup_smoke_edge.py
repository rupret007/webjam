from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


class _RunRootStub:
    def __init__(self):
        self.protocol_calls = []
        self.mainloop_called = False

    def protocol(self, event_name, callback):
        self.protocol_calls.append((event_name, callback))

    def mainloop(self):
        self.mainloop_called = True


class TestStartupSmokeEdge(unittest.TestCase):
    def test_app_constructor_runs_startup_path_without_new_hack(self):
        try:
            hidden_root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable for startup smoke test: {exc}")
        hidden_root.withdraw()

        repository = MagicMock()
        repository.get_setting.side_effect = lambda key, default=None: {
            "auto_reconnect_enabled": "1",
            "cohort_name": "mixed_discipline",
        }.get(key, default)
        repository.get_room_context.return_value = {}

        preferences_service = MagicMock()
        preferences_service.load.return_value = SimpleNamespace(
            font_scale=1.0,
            high_contrast_enabled=False,
            auto_setup_enabled=True,
        )
        preferences_service.get_window_geometry.return_value = "1200x800+0+0"

        jamulus_controller = MagicMock()
        jamulus_controller.get_audio_diagnostics.return_value = {"backend": "wasapi", "active": "True"}
        audio_monitor = MagicMock()
        webex_controller = MagicMock()
        api_bridge = MagicMock()
        auth_controller = MagicMock()
        metrics_service = MagicMock()
        session_canvas = MagicMock()

        try:
            with patch("webjam_app_enhanced.CTK_AVAILABLE", False), patch(
                "webjam_app_enhanced.tk.Tk", return_value=hidden_root
            ), patch(
                "webjam_app_enhanced.WebJamRepository", return_value=repository
            ), patch(
                "webjam_app_enhanced.UiPreferencesService", return_value=preferences_service
            ), patch(
                "webjam_app_enhanced.MetricsService", return_value=metrics_service
            ), patch(
                "webjam_app_enhanced.JamulusController", return_value=jamulus_controller
            ), patch(
                "webjam_app_enhanced.JamulusAudioMonitor", return_value=audio_monitor
            ), patch(
                "webjam_app_enhanced.WebexController", return_value=webex_controller
            ), patch(
                "webjam_app_enhanced.LocalApiBridge", return_value=api_bridge
            ), patch(
                "webjam_app_enhanced.AuthController", return_value=auth_controller
            ), patch(
                "webjam_app_enhanced.SessionCanvasPanel", return_value=session_canvas
            ), patch(
                "webjam_app_enhanced.Tooltip"
            ), patch.object(
                WebJamEnhancedApp, "_apply_accessibility_mode", autospec=True
            ) as apply_accessibility_mock, patch.object(
                WebJamEnhancedApp, "update_vu_meters", autospec=True
            ) as update_vu_mock, patch.object(
                WebJamEnhancedApp, "_refresh_readiness", autospec=True
            ) as refresh_readiness_mock, patch.object(
                WebJamEnhancedApp, "_poll_connection_health", autospec=True
            ) as poll_health_mock, patch.object(
                WebJamEnhancedApp, "_defer_service_start", autospec=True
            ) as defer_services_mock, patch.object(
                WebJamEnhancedApp, "_show_setup_once", autospec=True
            ) as show_setup_mock, patch.object(
                WebJamEnhancedApp, "_restore_startup_mix_default", autospec=True
            ) as restore_mix_mock:
                app = WebJamEnhancedApp()

            self.assertEqual(hidden_root.title(), "WebJam - Creative Collaboration Platform")
            repository.ensure_default_admin.assert_called_once_with()
            repository.upsert_room_context.assert_called_once()
            jamulus_controller.register_callback.assert_called_once_with(app.on_participants_updated)
            apply_accessibility_mock.assert_called_once_with(app)
            update_vu_mock.assert_called_once_with(app)
            self.assertEqual(refresh_readiness_mock.call_count, 2)
            poll_health_mock.assert_called_once_with(app)
            defer_services_mock.assert_called_once_with(app)
            show_setup_mock.assert_called_once_with(app)
            restore_mix_mock.assert_called_once_with(app)
            self.assertIs(app.root, hidden_root)
            self.assertEqual(app.mode_key, "music_jam")
        finally:
            try:
                hidden_root.destroy()
            except tk.TclError:
                pass

    def test_run_wires_close_handler_and_enters_mainloop(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = _RunRootStub()
        app.quit_app = MagicMock()

        WebJamEnhancedApp.run(app)

        self.assertEqual(len(app.root.protocol_calls), 1)
        self.assertEqual(app.root.protocol_calls[0][0], "WM_DELETE_WINDOW")
        self.assertIs(app.root.protocol_calls[0][1], app.quit_app)
        self.assertTrue(app.root.mainloop_called)


if __name__ == "__main__":
    unittest.main()

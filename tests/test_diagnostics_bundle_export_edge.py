from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


@dataclass
class _SettingsStub:
    log_file: str
    config_file: str
    mix_file: str
    webex_config_file: str


class TestDiagnosticsBundleExportEdge(unittest.TestCase):
    @patch("webjam_app_enhanced.Path.home", return_value=Path("C:/tmp"))
    @patch("webjam_app_enhanced.messagebox.showerror")
    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_export_diagnostics_bundle_uses_sanitized_support_snapshot(self, info_mock, error_mock, _home_mock):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.room_key = "default_room"
        app.jamulus_state = "Connected"
        app.webex_state = "Opened in browser"
        app.network_latency_ms = 12.5
        app.jamulus_server = "jam.example.com"
        app.jamulus_port = 22124
        app.webex_url = "https://example.webex.com/meet/test"
        app._refresh_endpoint_state = MagicMock()
        app.save_room_context = MagicMock()
        app.session_canvas = MagicMock()
        app.session_canvas.save_notes_if_dirty.return_value = True
        app._settings_for_checks = MagicMock(
            return_value=_SettingsStub(
                log_file="C:/tmp/webjam.log",
                config_file="C:/tmp/webjam.json",
                mix_file="C:/tmp/mix.json",
                webex_config_file="C:/tmp/webex.json",
            )
        )
        app.repository = MagicMock()
        app.repository.db_path = "C:/tmp/webjam.db"
        app.repository.get_room_context.return_value = {"mode_key": "music_jam"}
        app.repository.export_support_snapshot.return_value = {"safe": True}
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.get_audio_diagnostics.return_value = {"backend": "synthetic"}
        app.webex_controller = MagicMock()
        app.webex_controller.last_error = ""
        app.find_jamulus = MagicMock(return_value="C:/Jamulus.exe")
        app.metrics_service = MagicMock()
        app.metrics_service.export_diagnostics_bundle.return_value = Path("C:/tmp/webjam_bundle.zip")

        WebJamEnhancedApp.export_diagnostics_bundle(app)

        kwargs = app.metrics_service.export_diagnostics_bundle.call_args.kwargs
        app.save_room_context.assert_called_once()
        app.session_canvas.save_notes_if_dirty.assert_called_once_with()
        self.assertNotIn(app.repository.db_path, kwargs["support_files"])
        self.assertEqual(kwargs["extra_json_files"], {"support_snapshot.json": {"safe": True}})
        app.repository.export_support_snapshot.assert_called_once_with(room_key="default_room")
        app.metrics_service.increment.assert_called_once_with("metric_diagnostics_bundle_exported")
        info_mock.assert_called_once()
        error_mock.assert_not_called()

    @patch("webjam_app_enhanced.Path.home", return_value=Path("C:/tmp"))
    @patch("webjam_app_enhanced.messagebox.showerror")
    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_export_diagnostics_bundle_aborts_when_live_state_flush_fails(self, info_mock, error_mock, _home_mock):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.room_key = "default_room"
        app.save_room_context = MagicMock()
        app.session_canvas = MagicMock()
        app.session_canvas.save_notes_if_dirty.return_value = False
        app.metrics_service = MagicMock()

        WebJamEnhancedApp.export_diagnostics_bundle(app)

        app.metrics_service.export_diagnostics_bundle.assert_not_called()
        app.metrics_service.increment.assert_called_once_with("metric_diagnostics_bundle_failed")
        error_mock.assert_not_called()
        info_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.settings import AppSettings
from ui.views.setup_wizard import SetupWizard
from webjam_app_enhanced import WebJamEnhancedApp


class TestReadyCheckSummaryEdge(unittest.TestCase):
    def test_summary_marks_ready_when_all_checks_pass(self):
        report = WebJamEnhancedApp._summarize_ready_check(
            check_results=[
                ("Jamulus executable", True, "ok"),
                ("Jamulus server reachability", True, "ok"),
            ],
            latency_ms=12.5,
            participant_count=2,
        )
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["failed"], [])
        self.assertIn("Ready", str(report["summary"]))
        self.assertEqual(report["participant_count"], 2)
        self.assertIn("Latency", str(report["latency_label"]))

    def test_summary_lists_failed_checks_when_not_ready(self):
        report = WebJamEnhancedApp._summarize_ready_check(
            check_results=[
                ("Jamulus executable", False, "missing"),
                ("Webex URL", True, "ok"),
                ("Audio diagnostics", False, "inactive"),
            ],
            latency_ms=None,
            participant_count=0,
        )
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["failed"], ["Jamulus executable", "Audio diagnostics"])
        self.assertIn("Not ready", str(report["summary"]))
        self.assertIn("unreachable", str(report["latency_label"]).lower())


class TestBackgroundReviewEdge(unittest.TestCase):
    def test_run_ready_check_uses_background_task_wrapper(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.metrics_service = MagicMock()
        app._settings_for_checks = MagicMock(return_value=AppSettings())
        app.network_latency_ms = 12.5
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.get_participants.return_value = []
        app.find_jamulus = MagicMock(return_value="Jamulus.exe")
        app._run_background_task = MagicMock()

        with patch.object(SetupWizard, "run_preflight_checks", return_value=[("Jamulus executable", True, "ok")]) as run_checks_mock:
            app.run_ready_check()
            run_checks_mock.assert_not_called()
            task = app._run_background_task.call_args.kwargs["task"]
            task()
            run_checks_mock.assert_called_once()

    def test_open_diagnostics_panel_uses_background_task_wrapper(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.metrics_service = MagicMock()
        app._refresh_endpoint_state = MagicMock()
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.get_audio_diagnostics.return_value = {"backend": "test"}
        app.find_jamulus = MagicMock(return_value="Jamulus.exe")
        app.webex_url = "https://webex.example.com/meet/test"
        app.webex_controller = MagicMock()
        app._run_background_task = MagicMock()
        app.export_diagnostics_snapshot = MagicMock()
        app.export_diagnostics_bundle = MagicMock()
        app.reset_usage_metrics = MagicMock()
        app.show_setup_wizard = MagicMock()
        app.show_help = MagicMock()
        app.root = object()
        app.jamulus_server = "jam.example.com"
        app.jamulus_port = 22124

        with patch.object(SetupWizard, "check_tcp_hint", return_value=(True, "ok")) as tcp_hint_mock:
            app.open_diagnostics_panel()
            tcp_hint_mock.assert_not_called()
            task = app._run_background_task.call_args.kwargs["task"]
            self.assertEqual(task(), (True, "ok"))
            tcp_hint_mock.assert_called_once_with("jam.example.com", 22124)


if __name__ == "__main__":
    unittest.main()

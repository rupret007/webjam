from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from webjam_app_enhanced import WebJamEnhancedApp


class TestSessionBriefExportEdge(unittest.TestCase):
    @patch("webjam_app_enhanced.Path.home", return_value=Path("C:/tmp"))
    @patch("webjam_app_enhanced.messagebox.showerror")
    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_export_session_brief_success(self, info_mock, error_mock, _home_mock):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.room_key = "default_room"
        app.mode_key = "music_jam"
        app.save_room_context = MagicMock()
        app.repository = MagicMock()
        app.repository.get_room_context.return_value = {
            "mode_key": "music_jam",
            "template_name": "Band Rehearsal",
            "session_goal": "Tighten transitions",
            "review_state": "draft",
        }
        app.repository.list_session_artifacts.return_value = [
            {"id": "1", "title": "Reference", "artifact_type": "link", "reference": "https://example.com"}
        ]
        app.repository.get_session_notes.return_value = "Check intro groove"
        app.jamulus_controller = MagicMock()
        participants = [SimpleNamespace(channel_id=0, name="Alex"), SimpleNamespace(channel_id=1, name="Sam")]
        app.jamulus_controller.get_participants.return_value = participants
        app.metrics_service = MagicMock()
        app.metrics_service.export_session_brief.return_value = Path("C:/tmp/webjam_session_brief.md")

        WebJamEnhancedApp.export_session_brief(app)

        app.save_room_context.assert_called_once()
        app.metrics_service.export_session_brief.assert_called_once()
        kwargs = app.metrics_service.export_session_brief.call_args.kwargs
        self.assertEqual(kwargs["output_dir"], Path("C:/tmp"))
        self.assertEqual(kwargs["mode_label"], "Music Jam")
        self.assertEqual(kwargs["participants"], participants)
        app.metrics_service.increment.assert_called_once_with("metric_session_brief_exported")
        info_mock.assert_called_once()
        error_mock.assert_not_called()

    @patch("webjam_app_enhanced.Path.home", return_value=Path("C:/tmp"))
    @patch("webjam_app_enhanced.messagebox.showerror")
    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_export_session_brief_failure(self, info_mock, error_mock, _home_mock):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.room_key = "default_room"
        app.mode_key = "music_jam"
        app.save_room_context = MagicMock()
        app.repository = MagicMock()
        app.repository.get_room_context.return_value = {}
        app.repository.list_session_artifacts.return_value = []
        app.repository.get_session_notes.return_value = ""
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.get_participants.return_value = []
        app.metrics_service = MagicMock()
        app.metrics_service.export_session_brief.side_effect = RuntimeError("disk full")

        WebJamEnhancedApp.export_session_brief(app)

        app.metrics_service.increment.assert_has_calls([call("metric_session_brief_failed")])
        error_mock.assert_called_once()
        info_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

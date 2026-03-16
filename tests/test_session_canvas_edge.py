from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from ui.views.session_canvas import SessionCanvasPanel


class TestSessionCanvasArtifactEdge(unittest.TestCase):
    def _panel_stub(self) -> SessionCanvasPanel:
        panel = SessionCanvasPanel.__new__(SessionCanvasPanel)
        panel.add_artifact_cb = MagicMock()
        panel.refresh = MagicMock()
        return panel

    @patch("ui.views.session_canvas.messagebox")
    @patch("ui.views.session_canvas.simpledialog.askstring", return_value="   ")
    def test_add_artifact_rejects_whitespace_title(self, _ask, messagebox_mock):
        panel = self._panel_stub()
        panel._add_artifact()
        panel.add_artifact_cb.assert_not_called()
        panel.refresh.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()

    @patch("ui.views.session_canvas.messagebox")
    @patch(
        "ui.views.session_canvas.simpledialog.askstring",
        side_effect=["Title", "link", "   "],
    )
    def test_add_artifact_rejects_whitespace_reference(self, _ask, messagebox_mock):
        panel = self._panel_stub()
        panel._add_artifact()
        panel.add_artifact_cb.assert_not_called()
        panel.refresh.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()

    @patch("ui.views.session_canvas.messagebox")
    @patch(
        "ui.views.session_canvas.simpledialog.askstring",
        side_effect=["  Title  ", "link", "  https://example.com  "],
    )
    def test_add_artifact_trims_title_and_reference(self, _ask, messagebox_mock):
        panel = self._panel_stub()
        panel._add_artifact()
        panel.add_artifact_cb.assert_called_once_with("Title", "link", "https://example.com")
        panel.refresh.assert_called_once()
        messagebox_mock.showwarning.assert_not_called()

    @patch("ui.views.session_canvas.messagebox")
    @patch(
        "ui.views.session_canvas.simpledialog.askstring",
        side_effect=["Title", "invalid", "https://example.com"],
    )
    def test_add_artifact_rejects_invalid_type(self, _ask, messagebox_mock):
        panel = self._panel_stub()
        panel._add_artifact()
        panel.add_artifact_cb.assert_not_called()
        panel.refresh.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()

    @patch("ui.views.session_canvas.messagebox")
    @patch(
        "ui.views.session_canvas.simpledialog.askstring",
        side_effect=["Title", None],
    )
    def test_add_artifact_cancel_type_aborts_without_defaulting(self, _ask, messagebox_mock):
        panel = self._panel_stub()
        panel._add_artifact()
        panel.add_artifact_cb.assert_not_called()
        panel.refresh.assert_not_called()
        messagebox_mock.showwarning.assert_not_called()

    def test_refresh_preserves_dirty_unsaved_notes(self):
        panel = SessionCanvasPanel.__new__(SessionCanvasPanel)
        panel._notes_dirty = True
        panel.get_mode = MagicMock(return_value=SimpleNamespace(quick_help="Keep moving", review_prompts=["One", "Two"]))
        panel.get_room_context = MagicMock(return_value={"review_state": "draft"})
        panel.list_artifacts_cb = MagicMock(return_value=[])
        panel.load_notes_cb = MagicMock(return_value="persisted notes")
        panel.mode_help = MagicMock()
        panel.review_state_var = MagicMock()
        panel.artifacts_list = MagicMock()
        panel.prompts = MagicMock()
        panel.notes = MagicMock()
        panel.notes.get.return_value = "unsaved local notes\n"

        panel.refresh()

        panel.notes.insert.assert_called_once_with("1.0", "unsaved local notes")
        self.assertTrue(panel._notes_dirty)

    def test_format_timestamp_marker_uses_hh_mm_ss(self):
        marker = SessionCanvasPanel.format_timestamp_marker(datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(marker, "[03:04:05] ")

    @patch.object(SessionCanvasPanel, "format_timestamp_marker", return_value="[00:00:10] ")
    def test_insert_timestamp_marker_uses_insert_cursor(self, _marker):
        panel = SessionCanvasPanel.__new__(SessionCanvasPanel)
        panel.notes = MagicMock()
        panel._notes_dirty = False
        panel.insert_timestamp_marker()
        panel.notes.insert.assert_called_once_with("insert", "[00:00:10] ")
        self.assertTrue(panel._notes_dirty)

    @patch.object(SessionCanvasPanel, "format_timestamp_marker", return_value="[00:00:10] ")
    def test_insert_timestamp_marker_falls_back_to_end(self, _marker):
        panel = SessionCanvasPanel.__new__(SessionCanvasPanel)
        panel.notes = MagicMock()
        panel.notes.insert.side_effect = [RuntimeError("cursor unavailable"), None]
        panel._notes_dirty = False
        panel.insert_timestamp_marker()
        self.assertEqual(panel.notes.insert.call_count, 2)
        self.assertEqual(panel.notes.insert.call_args_list[1][0], ("end", "[00:00:10] "))
        self.assertTrue(panel._notes_dirty)


if __name__ == "__main__":
    unittest.main()

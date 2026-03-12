from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

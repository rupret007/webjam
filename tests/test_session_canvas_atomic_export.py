"""Regression test for SessionCanvas.export_notes (v0.4.5).

Verifies the export path uses ``core.file_io.atomic_write_text`` so a
mid-write crash can't leave the user's session-notes file half-written.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.widgets.session_canvas import SessionCanvas  # noqa: E402


class TestSessionCanvasAtomicExport(unittest.TestCase):
    def test_export_notes_uses_atomic_write_text(self):
        canvas = SessionCanvas()
        canvas.set_notes("test content")

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "out.md")

            mock_atomic = MagicMock()
            with patch("core.file_io.atomic_write_text", mock_atomic), \
                 patch(
                     "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
                     return_value=(target, "Markdown (*.md)"),
                 ):
                canvas.export_notes()

            mock_atomic.assert_called_once()
            args, _kwargs = mock_atomic.call_args
            self.assertEqual(args[0], target)
            self.assertEqual(args[1], "test content")

    def test_export_notes_skipped_when_dialog_cancelled(self):
        canvas = SessionCanvas()
        canvas.set_notes("some content")

        mock_atomic = MagicMock()
        # User cancels: getSaveFileName returns ("", "").
        with patch("core.file_io.atomic_write_text", mock_atomic), \
             patch(
                 "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
                 return_value=("", ""),
             ):
            canvas.export_notes()

        mock_atomic.assert_not_called()

    def test_export_notes_skipped_when_notes_empty(self):
        canvas = SessionCanvas()
        canvas.set_notes("    \n\n  ")  # whitespace only

        mock_atomic = MagicMock()
        save_dialog = MagicMock()
        with patch("core.file_io.atomic_write_text", mock_atomic), \
             patch(
                 "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
                 save_dialog,
             ):
            canvas.export_notes()

        # No file dialog and no write when there's nothing to export.
        save_dialog.assert_not_called()
        mock_atomic.assert_not_called()


if __name__ == "__main__":
    unittest.main()

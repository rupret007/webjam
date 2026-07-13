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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.session_intelligence import build_session_pulse  # noqa: E402
from webjam_qt.widgets.session_canvas import SessionCanvas  # noqa: E402


class TestSessionCanvasAtomicExport(unittest.TestCase):
    def test_export_menu_keeps_both_accessible_export_paths(self):
        canvas = SessionCanvas()

        self.assertEqual(canvas._export_button.accessibleName(), "Export session")
        self.assertEqual(canvas._export_notes_action.text(), "Session notes…")
        self.assertEqual(canvas._export_brief_action.text(), "Session brief…")
        self.assertEqual(canvas._export_notes_action.toolTip(), "Export session notes")
        self.assertEqual(canvas._export_brief_action.toolTip(), "Export session brief")

    def test_toolbar_controls_fit_supported_canvas_widths(self):
        for width in (280, 360, 900):
            with self.subTest(width=width):
                canvas = SessionCanvas()
                canvas.resize(width, 700)
                canvas.show()
                _app.processEvents()

                for button in canvas._toolbar_buttons:
                    self.assertGreater(button.width(), 0)
                    self.assertGreaterEqual(button.geometry().left(), 0)
                    self.assertLessEqual(button.geometry().right(), width - 1)
                canvas.close()

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

    def test_pulse_renders_as_plain_text_and_brief_includes_raw_notes(self):
        canvas = SessionCanvas()
        canvas.set_notes("Decision: <b>keep the bridge short</b>")
        canvas.set_session_pulse(
            build_session_pulse(
                mode_key="music_jam",
                title="Bridge pass",
                notes=canvas.current_notes(),
            )
        )

        self.assertEqual(canvas._pulse_summary.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("<b>keep the bridge short</b>", canvas._pulse_summary.text())
        brief = canvas.current_session_brief()
        self.assertIn("# Bridge pass", brief)
        self.assertIn("## Notes", brief)
        self.assertIn("<b>keep the bridge short</b>", brief)

    def test_export_brief_refreshes_before_atomic_write(self):
        canvas = SessionCanvas()
        canvas.set_notes("Action: @Lee save rehearsal mix")

        def set_fresh_pulse() -> None:
            canvas.set_session_pulse(
                build_session_pulse(
                    mode_key="music_jam",
                    title="Rehearsal",
                    notes=canvas.current_notes(),
                )
            )

        canvas.brief_export_requested.connect(set_fresh_pulse)
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "brief.md")
            mock_atomic = MagicMock()
            with patch("core.file_io.atomic_write_text", mock_atomic), patch(
                "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
                return_value=(target, "Markdown (*.md)"),
            ):
                canvas.export_brief()

        mock_atomic.assert_called_once()
        args, _kwargs = mock_atomic.call_args
        self.assertEqual(args[0], target)
        self.assertIn("# Rehearsal", args[1])
        self.assertIn("- @Lee save rehearsal mix", args[1])

    def test_export_brief_skipped_when_dialog_cancelled(self):
        canvas = SessionCanvas()
        canvas.set_session_pulse(build_session_pulse(mode_key="music_jam"))
        mock_atomic = MagicMock()

        with patch("core.file_io.atomic_write_text", mock_atomic), patch(
            "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            canvas.export_brief()

        mock_atomic.assert_not_called()

    def test_export_brief_reports_atomic_write_failure(self):
        canvas = SessionCanvas()
        canvas.set_session_pulse(build_session_pulse(mode_key="music_jam"))

        with patch(
            "core.file_io.atomic_write_text", side_effect=OSError("disk full")
        ), patch(
            "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
            return_value=("/unwritable/brief.md", "Markdown (*.md)"),
        ), patch.object(QMessageBox, "warning") as warning:
            canvas.export_brief()

        warning.assert_called_once()
        rendered = warning.call_args.args[2]
        self.assertIn("Choose another folder", rendered)
        self.assertNotIn("disk full", rendered)
        self.assertNotIn("/unwritable", rendered)

    def test_cleared_pulse_falls_back_to_raw_notes(self):
        canvas = SessionCanvas()
        canvas.set_notes("Decision: keep the bridge short")
        canvas.set_session_pulse(
            build_session_pulse(
                mode_key="music_jam",
                notes=canvas.current_notes(),
            )
        )

        canvas.clear_session_pulse()

        self.assertIsNone(canvas._current_pulse)
        self.assertEqual(
            canvas.current_session_brief(), "Decision: keep the bridge short"
        )
        self.assertEqual(canvas._pulse_stage.text(), "Unavailable")


if __name__ == "__main__":
    unittest.main()

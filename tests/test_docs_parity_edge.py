from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import ux_smoke_test
from webjam_app_enhanced import WebJamEnhancedApp


ROOT = Path(__file__).resolve().parents[1]


class TestDocsParityEdge(unittest.TestCase):
    def _app_stub(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.repository = MagicMock()
        app.repository.get_bootstrap_admin_credentials_path.return_value = None
        return app

    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_quick_start_help_mentions_browser_launch_and_ready_check(self, info_mock):
        app = self._app_stub()

        app.show_help()

        help_text = info_mock.call_args.args[1]
        self.assertIn("open the meeting in your browser", help_text)
        self.assertIn("Run Session -> Run Ready Check before your first live room", help_text)

    def test_user_guide_documents_actual_shortcuts(self):
        guide_text = (ROOT / "USER_GUIDE.md").read_text(encoding="utf-8")
        expected_shortcuts = {
            "Ctrl + S": "Save Current Mix",
            "Ctrl + O": "Load Mix",
            "Ctrl + Q": "Quit WebJam",
            "F1": "Open Help",
            "Ctrl + H": "Toggle High Contrast",
            "Ctrl + +": "Increase Text Size",
            "Ctrl + -": "Decrease Text Size",
            "Space": "Unmute All",
            "Ctrl + R": "Reset All Faders",
            "Ctrl + P": "Center All Pans",
            "M": "Mute Selected Channel",
            "S": "Solo Selected Channel",
            "Ctrl + J": "Launch Jamulus",
            "Ctrl + W": "Launch Webex",
        }

        for shortcut, action in expected_shortcuts.items():
            self.assertIn(shortcut, guide_text)
            self.assertIn(action, guide_text)

    def test_readme_simple_describes_browser_open_and_mix_restore(self):
        readme_text = (ROOT / "README_SIMPLE.md").read_text(encoding="utf-8")
        self.assertIn("Click **Launch Webex**. Your browser opens to the meeting.", readme_text)
        self.assertIn("Saved default mixes restore automatically on next sign-in or launch.", readme_text)

    def test_ux_smoke_gate_passes(self):
        self.assertEqual(ux_smoke_test.main(), 0)


if __name__ == "__main__":
    unittest.main()

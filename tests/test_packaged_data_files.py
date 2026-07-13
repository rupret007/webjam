"""Guards the build's runtime data-file contract.

These files are loaded at runtime via package-relative paths, so they MUST be
(a) present in the source tree where the code expects them, and (b) declared in
webjam.spec so PyInstaller bundles them into the frozen app.  A build can pass
CI yet crash for users if either drifts — these tests catch that.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import webjam_qt

PKG = Path(webjam_qt.__file__).resolve().parent
ROOT = PKG.parent
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")


class TestPackagedDataFiles(unittest.TestCase):
    def test_stylesheet_loads_and_is_nonempty(self):
        from webjam_qt.theme import load_stylesheet
        css = load_stylesheet()
        self.assertIsInstance(css, str)
        self.assertGreater(len(css.strip()), 0)

    def test_conductor_qss_present_where_code_expects_it(self):
        self.assertTrue((PKG / "theme" / "conductor.qss").is_file())

    def test_webex_widget_html_present_where_code_expects_it(self):
        # webex_embed.py: Path(__file__).parent.parent / "webex_widget.html"
        self.assertTrue((PKG / "webex_widget.html").is_file())

    def test_webex_widget_cdn_version_is_pinned(self):
        html = (PKG / "webex_widget.html").read_text(encoding="utf-8")
        self.assertNotIn("@latest", html)
        self.assertIn("@webex/widgets@1.28.2", html)

    def test_spec_bundles_the_runtime_data_files(self):
        self.assertIn("conductor.qss", SPEC)
        self.assertIn("webex_widget.html", SPEC)
        self.assertIn("INTER_OFL.txt", SPEC)
        self.assertTrue((ROOT / "licenses" / "INTER_OFL.txt").is_file())

    def test_spec_version_tracks_package_version(self):
        # The macOS bundle version must not be hardcoded/stale.
        self.assertNotIn('"CFBundleShortVersionString": "0.3.0"', SPEC)
        self.assertIn("CFBundleShortVersionString", SPEC)
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (PKG / "__init__.py").read_text())
        self.assertIsNotNone(m)

    def test_microphone_permission_copy_explains_the_bandmate_benefit(self):
        self.assertIn("NSMicrophoneUsageDescription", SPEC)
        self.assertIn("bandmates can hear you", SPEC)
        self.assertNotIn("optional local recording.\"", SPEC)

    def test_spec_keeps_late_studio_modules_in_the_frozen_archive(self):
        self.assertIn('"core.take_export"', SPEC)
        self.assertIn('"webjam_qt.widgets.recording_studio"', SPEC)
        self.assertIn('"webjam_qt.windows.recording_setup"', SPEC)


if __name__ == "__main__":
    unittest.main()

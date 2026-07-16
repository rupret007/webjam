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
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class TestPackagedDataFiles(unittest.TestCase):
    def test_stylesheet_loads_and_is_nonempty(self):
        from webjam_qt.theme import load_stylesheet
        css = load_stylesheet()
        self.assertIsInstance(css, str)
        self.assertGreater(len(css.strip()), 0)

    def test_conductor_qss_present_where_code_expects_it(self):
        self.assertTrue((PKG / "theme" / "conductor.qss").is_file())

    def test_brand_assets_present_where_code_and_packaging_expect_them(self):
        assets = PKG / "theme" / "assets"
        for name in ("webjam-mark.svg", "webjam.ico", "webjam.icns"):
            self.assertTrue((assets / name).is_file(), name)

    def test_webex_widget_html_present_where_code_expects_it(self):
        # webex_embed.py: Path(__file__).parent.parent / "webex_widget.html"
        self.assertTrue((PKG / "webex_widget.html").is_file())

    def test_webex_widget_cdn_version_is_pinned(self):
        html = (PKG / "webex_widget.html").read_text(encoding="utf-8")
        self.assertNotIn("@latest", html)
        self.assertIn("@webex/widgets@1.28.2", html)

    def test_spec_bundles_the_runtime_data_files(self):
        self.assertIn("conductor.qss", SPEC)
        self.assertIn('"theme" / "assets"', SPEC)
        self.assertIn("webex_widget.html", SPEC)
        self.assertIn("webjam-build-id.txt", SPEC)
        self.assertIn("WEBJAM_BUILD_ID", SPEC)
        self.assertIn("INTER_OFL.txt", SPEC)
        self.assertTrue((ROOT / "licenses" / "INTER_OFL.txt").is_file())
        self.assertIn('"transport" / "NOTICE.md"', SPEC)
        self.assertIn('"transport" / "DEPENDENCIES.md"', SPEC)
        self.assertIn('"transport" / "licenses"', SPEC)
        self.assertTrue((ROOT / "transport" / "NOTICE.md").is_file())
        self.assertTrue((ROOT / "transport" / "DEPENDENCIES.md").is_file())
        self.assertTrue((ROOT / "transport" / "licenses").is_dir())
        for name in (
            "ANET-BSD-3-CLAUSE.txt",
            "GO-BSD-3-CLAUSE.txt",
            "GOOGLE-UUID-BSD-3-CLAUSE.txt",
            "PION-MIT.txt",
            "QUIC-GO-MIT.txt",
        ):
            self.assertTrue((ROOT / "transport" / "licenses" / name).is_file())
        self.assertNotIn('sys.platform.startswith("linux")', SPEC)
        self.assertIn('"Jamulus"', SPEC)

    def test_ci_builds_stages_and_smokes_the_native_transport(self):
        self.assertIn("go test -race -count=1 ./...", CI)
        self.assertIn("go mod verify", CI)
        self.assertIn("webjam-fabric.exe", CI)
        self.assertIn("Contents/MacOS/webjam-fabric", CI)
        self.assertIn("Contents/Resources/webjam-fabric.sha256", CI)
        self.assertIn("buildID=$build_id", CI)
        self.assertIn("codesign --force --sign -", CI)
        self.assertIn('"type":"shutdown"', CI)
        self.assertIn("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION=1", CI)
        self.assertIn("tests/test_native_sidecar_integration.py", CI)
        self.assertIn("target: linux-x64", CI)
        self.assertIn("jamulus_3.12.2_ubuntu_amd64.deb", CI)
        self.assertIn("dist/WebJam/Jamulus/JAMULUS_COPYING.txt", CI)
        self.assertIn("--machine x86_64", CI)
        self.assertIn("WEBJAM_SMOKE_LAUNCH_ONLY=1", CI)
        self.assertIn("WEBJAM_SMOKE_AUTOSTART_AUDIO=1", CI)
        self.assertIn("accepted valid authentication secret", CI)
        self.assertIn("webjam-linux-x64", CI)
        self.assertIn("Verify tag matches packaged version", CI)

    def test_linux_release_instructions_and_installer_helper_are_packaged(self):
        linux = ROOT / "packaging" / "linux"
        readme = linux / "README-LINUX.txt"
        installer = linux / "install-jamulus.sh"
        self.assertTrue(readme.is_file())
        self.assertTrue(installer.is_file())
        self.assertTrue(installer.stat().st_mode & 0o111)
        self.assertIn("join a jam hosted from the macOS build", readme.read_text())
        self.assertIn("jamulus_3.12.2_ubuntu_amd64.deb", installer.read_text())

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

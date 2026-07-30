"""Guards the build's runtime data-file contract.

These files are loaded at runtime via package-relative paths, so they MUST be
(a) present in the source tree where the code expects them, and (b) declared in
webjam.spec so PyInstaller bundles them into the frozen app.  A build can pass
CI yet crash for users if either drifts — these tests catch that.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import webjam_qt

PKG = Path(webjam_qt.__file__).resolve().parent
ROOT = PKG.parent
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
POCKET_SMOKE_RUNNER = (
    ROOT / "tests" / "support" / "run_frozen_pocket_stage_smoke.py"
).read_text(encoding="utf-8")
REFERENCE_STUDIO_SMOKE_RUNNER = (
    ROOT / "tests" / "support" / "run_frozen_reference_studio_smoke.py"
).read_text(encoding="utf-8")
COMPONENT_CATALOG_SMOKE_RUNNER = (
    ROOT / "tests" / "support" / "run_frozen_component_catalog_smoke.py"
).read_text(encoding="utf-8")


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

    def test_spec_bundles_the_runtime_data_files(self):
        self.assertIn("conductor.qss", SPEC)
        self.assertIn('"theme" / "assets"', SPEC)
        self.assertIn("webjam-build-id.txt", SPEC)
        self.assertIn("WEBJAM_BUILD_ID", SPEC)
        self.assertIn("INTER_OFL.txt", SPEC)
        self.assertTrue((ROOT / "licenses" / "INTER_OFL.txt").is_file())
        for name in (
            "CRYPTOGRAPHY_LICENSE.txt",
            "WEBSOCKETS_LICENSE.txt",
            "SEGNO_LICENSE.txt",
            "JAMULUS_COPYING-r3_12_3.txt",
        ):
            self.assertIn(name, SPEC)
            self.assertTrue((ROOT / "licenses" / name).is_file())
        self.assertIn("Jamulus-component-sbom.cdx.json", SPEC)
        self.assertTrue(
            (ROOT / "packaging" / "Jamulus-component-sbom.cdx.json").is_file()
        )
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

    def test_external_webex_build_excludes_retired_embedded_runtime(self):
        self.assertFalse((PKG / "webex_widget.html").exists())
        self.assertFalse((ROOT / "core" / "webex_guest_token.py").exists())
        self.assertNotIn('"core.webex_guest_token"', SPEC)
        hiddenimports = SPEC.split("hiddenimports=[", 1)[1].split(
            "],\n    hookspath=", 1
        )[0]
        self.assertNotIn('"httpx"', hiddenimports)
        excludes = SPEC.split("excludes=[", 1)[1].split(
            "],\n    win_no_prefer_redirects=", 1
        )[0]
        for module in (
            "PySide6.QtWebChannel",
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineQuick",
            "PySide6.QtWebEngineWidgets",
        ):
            self.assertNotIn(f'"{module}"', hiddenimports)
            self.assertIn(f'"{module}"', excludes)
        for retired_runtime in ("QtWebEngine", "QtWebChannel"):
            self.assertIn(
                f"test -z \"$(find dist/WebJam.app -iname '*{retired_runtime}*'",
                CI,
            )

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
        # v0.22 retains 3.12.2 in every desktop artifact as the immutable,
        # offline fallback while real integration certifies both approved
        # compatibility versions.
        self.assertIn("jamulus_3.12.2_ubuntu_amd64.deb", CI)
        self.assertIn('jamulus_version: "3.12.2"', CI)
        self.assertIn('jamulus_version: "3.12.3"', CI)
        self.assertIn(
            "100af7bcf6edb5729df03ac38bbbdbb4f02014d50b32e0a0e11e55bffba783d3",
            CI,
        )
        self.assertIn("dist/WebJam/Jamulus/JAMULUS_COPYING.txt", CI)
        self.assertIn("--machine x86_64", CI)
        self.assertIn("WEBJAM_SMOKE_LAUNCH_ONLY=1", CI)
        self.assertIn("WEBJAM_SMOKE_AUTOSTART_AUDIO=1", CI)
        self.assertIn("accepted valid authentication secret", CI)
        self.assertIn("webjam-linux-x64", CI)
        self.assertIn(
            "Verify annotated tag is the exact origin master source",
            CI,
        )
        self.assertIn('git cat-file -t "$isolated_tag"', CI)
        self.assertIn('!= "tag"', CI)
        self.assertIn("refs/remotes/origin/master", CI)
        self.assertIn('if [[ "$tag_commit" != "$master_commit" ]]', CI)
        self.assertIn("Remote release tag identity changed", CI)
        self.assertIn("run_frozen_pocket_stage_smoke", CI)
        self.assertIn("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME", POCKET_SMOKE_RUNNER)
        self.assertEqual(CI.count("run_frozen_reference_studio_smoke"), 3)
        self.assertIn(
            "WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME",
            REFERENCE_STUDIO_SMOKE_RUNNER,
        )

    def test_ci_pins_the_reviewed_ruff_contract(self):
        self.assertEqual(CI.count('"ruff==0.15.22"'), 2)
        self.assertNotIn("pip install pytest ruff ", CI)
        self.assertNotIn("pip install ruff build", CI)

    def test_ci_selects_swift_testing_capable_xcode_for_pocket_stage(self):
        self.assertIn(
            "DEVELOPER_DIR: /Applications/Xcode_16.2.app/Contents/Developer",
            CI,
        )
        self.assertIn('test "$(xcodebuild -version | head -1)" = "Xcode 16.2"', CI)
        self.assertIn("swift test", CI)
        self.assertIn("-sdk iphonesimulator", CI)

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
        self.assertNotIn('optional local recording."', SPEC)

    def test_macos_bundle_declares_pocket_stage_local_network_purpose(self):
        self.assertIn("NSLocalNetworkUsageDescription", SPEC)
        self.assertIn("iPhone Pocket Stage", SPEC)
        self.assertIn("private local network", SPEC)

    def test_macos_bundle_explains_its_bounded_jamulus_app_data_access(self):
        expected = (
            "WebJam accesses Jamulus app data only for dedicated WebJam "
            "profiles and private Reference Track audio-route and control "
            "files. It never reads or changes your regular Jamulus profile."
        )
        tree = ast.parse(SPEC)
        values = {
            target.id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance((target := node.targets[0]), ast.Name)
            and target.id == "MACOS_APP_DATA_USAGE_DESCRIPTION"
        }
        self.assertEqual(
            values.get("MACOS_APP_DATA_USAGE_DESCRIPTION"),
            expected,
        )
        self.assertIn(
            '"NSAppDataUsageDescription": MACOS_APP_DATA_USAGE_DESCRIPTION',
            SPEC,
        )

    def test_spec_keeps_late_studio_modules_in_the_frozen_archive(self):
        self.assertIn('"core.take_export"', SPEC)
        self.assertIn('"webjam_qt.widgets.recording_studio"', SPEC)
        self.assertIn('"webjam_qt.windows.recording_setup"', SPEC)

    def test_spec_keeps_opt_in_pocket_stage_runtime_in_frozen_app(self):
        for module in (
            "core.pocket_stage",
            "services.pocket_stage_gateway",
            "services.pocket_stage_packaged_smoke",
            "services.pocket_stage_tls",
            "webjam_qt.windows.pocket_stage_pairing",
            "cryptography",
            "websockets",
            "websockets.sync.client",
            "segno",
        ):
            self.assertIn(f'"{module}"', SPEC)

    def test_spec_keeps_component_updater_ca_bundle_in_frozen_app(self):
        self.assertIn('"certifi"', SPEC)
        self.assertIn('"services.jamulus_component_packaged_smoke"', SPEC)
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^certifi[=>]")
        self.assertIn(
            "WEBJAM_SMOKE_COMPONENT_CATALOG_RUNTIME",
            COMPONENT_CATALOG_SMOKE_RUNNER,
        )
        self.assertIn('environment["SSL_CERT_FILE"]', COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn('environment["SSL_CERT_DIR"]', COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertNotIn("--catalog-url", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn("--expected-target", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn("--expected-jamulus-version", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn(
            "--expected-catalog-envelope-sha256",
            COMPONENT_CATALOG_SMOKE_RUNNER,
        )
        self.assertIn(
            "--expected-catalog-payload-sha256",
            COMPONENT_CATALOG_SMOKE_RUNNER,
        )
        self.assertIn(
            "--expected-signer-fingerprint-sha256",
            COMPONENT_CATALOG_SMOKE_RUNNER,
        )
        self.assertIn("EXPECTED_COMPONENT_COUNT = 8", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn('"QT_QPA_PLATFORM": "offscreen"', COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn("stdout=subprocess.DEVNULL", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertIn("stderr=subprocess.DEVNULL", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertNotIn("completed.stderr", COMPONENT_CATALOG_SMOKE_RUNNER)
        self.assertNotIn("completed.stdout", COMPONENT_CATALOG_SMOKE_RUNNER)

    def test_spec_keeps_reference_track_pilot_in_frozen_app(self):
        for module in (
            "core.reference_track",
            "services.reference_track_backend",
            "webjam_qt.windows.reference_track",
            "soundfile",
            "sounddevice",
            "numpy",
        ):
            self.assertIn(f'"{module}"', SPEC)

    def test_spec_keeps_reference_studio_runtime_smoke_in_frozen_app(self):
        self.assertIn('"services.reference_studio_packaged_smoke"', SPEC)


if __name__ == "__main__":
    unittest.main()

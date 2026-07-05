from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import webjam_installer


class TestWebJamInstallerEdge(unittest.TestCase):
    @patch("webjam_installer.subprocess.run")
    @patch("webjam_installer.wait_until", return_value=True)
    @patch("webjam_installer.fetch_latest_jamulus_installer")
    @patch("webjam_installer.copy_resource", return_value=Path("C:/tmp/jamulus_bundle.exe"))
    @patch("webjam_installer.find_jamulus", return_value=None)
    def test_install_jamulus_prefers_bundled_installer(
        self,
        _find_jamulus_mock,
        copy_resource_mock,
        fetch_latest_mock,
        _wait_until_mock,
        subprocess_run_mock,
    ):
        subprocess_run_mock.return_value = SimpleNamespace(returncode=0)
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as handle:
            bundled_path = Path(handle.name)
        try:
            with patch("webjam_installer.jamulus_installer_path", return_value=bundled_path):
                self.assertTrue(webjam_installer.install_jamulus())
        finally:
            bundled_path.unlink(missing_ok=True)

        fetch_latest_mock.assert_not_called()
        copy_resource_mock.assert_called_once_with(bundled_path.name, webjam_installer.WORK)
        subprocess_run_mock.assert_called()

    @patch("webjam_installer.subprocess.run")
    @patch("webjam_installer.wait_until", return_value=True)
    @patch("webjam_installer.fetch_latest_jamulus_installer", return_value=Path("C:/tmp/jamulus_latest.exe"))
    @patch("webjam_installer.find_jamulus", return_value=None)
    def test_install_jamulus_falls_back_to_online_when_bundle_missing(
        self,
        _find_jamulus_mock,
        fetch_latest_mock,
        _wait_until_mock,
        subprocess_run_mock,
    ):
        subprocess_run_mock.return_value = SimpleNamespace(returncode=0)
        with patch("webjam_installer.jamulus_installer_path", return_value=None):
            self.assertTrue(webjam_installer.install_jamulus())

        fetch_latest_mock.assert_called_once()
        subprocess_run_mock.assert_called()

    @patch("webjam_installer.subprocess.Popen")
    @patch("webjam_installer.wait_until", return_value=False)
    @patch("webjam_installer.run", return_value=SimpleNamespace(returncode=1603, stdout="", stderr=""))
    @patch("webjam_installer.download_file", return_value=True)
    @patch("webjam_installer.webex_installed", return_value=False)
    def test_install_webex_returns_false_when_interactive_install_never_completes(
        self,
        _webex_installed_mock,
        _download_mock,
        _run_mock,
        wait_until_mock,
        popen_mock,
    ):
        popen_mock.return_value = MagicMock()

        result = webjam_installer.install_webex()

        self.assertFalse(result)
        popen_mock.assert_any_call(["msiexec", "/i", str(webjam_installer.WORK / "Webex_latest.msi")])
        wait_until_mock.assert_called_once()

    @patch("webjam_installer.wait_until", return_value=True)
    @patch("webjam_installer.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    @patch("webjam_installer.download_file", return_value=True)
    @patch("webjam_installer.webex_installed", return_value=False)
    def test_install_webex_waits_for_detection_after_silent_success(
        self,
        _webex_installed_mock,
        _download_mock,
        _run_mock,
        wait_until_mock,
    ):
        result = webjam_installer.install_webex()

        self.assertTrue(result)
        wait_until_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

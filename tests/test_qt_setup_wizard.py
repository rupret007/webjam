"""Smoke tests for webjam_qt.windows.setup_wizard (Qt/PySide6).

Runs headlessly via QT_QPA_PLATFORM=offscreen (set automatically in CI).
Tests are skipped when PySide6 is not importable so the suite stays green
in environments that only have the legacy Tkinter stack installed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings


def _pyside6_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _qapp():
    """Return (or lazily create) the QApplication singleton."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


skip_no_pyside6 = unittest.skipUnless(_pyside6_available(), "PySide6 not installed")


# ---------------------------------------------------------------------------
# should_show_on_startup — pure logic, no Qt required
# ---------------------------------------------------------------------------
class TestShouldShowOnStartup(unittest.TestCase):
    """SetupWizard.should_show_on_startup needs no display."""

    def test_true_when_config_missing(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        settings = AppSettings(config_file="/nonexistent/__webjam_missing__.json")
        self.assertTrue(SetupWizard.should_show_on_startup(settings))

    def test_false_when_config_exists(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            settings = AppSettings(config_file=tmp)
            self.assertFalse(SetupWizard.should_show_on_startup(settings))
        finally:
            Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _JamulusPage — host validation
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestJamulusPage(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_empty_host_fails_validation(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server=""))
        page._host.setText("")
        self.assertFalse(page.validatePage())

    def test_filled_host_passes_validation(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
            ))
            self.assertTrue(page.validatePage())

    def test_missing_jamulus_executable_fails_validation(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "webjam_qt.windows.setup_wizard.AppSettings",
            return_value=AppSettings(jamulus_candidates=[]),
        ):
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=["/nope/Jamulus"],
            ))
            self.assertFalse(page.validatePage())

    def test_host_property_strips_whitespace(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server="  myband.example.com  "))
        self.assertEqual(page.host, "myband.example.com")

    def test_port_property_matches_settings(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_port=9999))
        self.assertEqual(page.port, 9999)

    def test_rpc_port_property_matches_settings(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_rpc_port=22222))
        self.assertEqual(page.rpc_port, 22222)


# ---------------------------------------------------------------------------
# _JamulusPage — bundled Jamulus (macOS zero-install / Windows installer)
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestJamulusPageBundling(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_no_install_button_when_path_already_found(self):
        """A configured/detected candidate should hide the install button —
        nothing to install."""
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="x", jamulus_candidates=[jam.name],
            ))
            self.assertFalse(page._install_jamulus_btn.isVisibleTo(page))
            self.assertIsNone(page._bundled_installer_path)

    def test_install_button_hidden_when_no_bundled_installer(self):
        """Dev checkouts / non-frozen runs never bundle an installer."""
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))
        self.assertFalse(page._install_jamulus_btn.isVisibleTo(page))
        self.assertIsNone(page._bundled_installer_path)

    def test_install_button_shown_when_bundled_installer_present(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "services.bridge_service._bundled_jamulus_installer",
            return_value="/app/Jamulus/jamulus_3.12.2_win.exe",
        ):
            page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))
        self.assertTrue(page._install_jamulus_btn.isVisibleTo(page))
        self.assertEqual(
            page._bundled_installer_path, "/app/Jamulus/jamulus_3.12.2_win.exe"
        )

    def test_bundled_macos_candidate_prefills_path_with_note(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "services.bridge_service._bundled_jamulus_candidate",
            return_value="/App/WebJam.app/Contents/Resources/Jamulus.app"
                         "/Contents/MacOS/Jamulus",
        ):
            page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))
        self.assertEqual(
            page._jamulus_path.text(),
            "/App/WebJam.app/Contents/Resources/Jamulus.app/Contents/MacOS/Jamulus",
        )
        # A bundled macOS copy means there's nothing to "install".
        self.assertFalse(page._install_jamulus_btn.isVisibleTo(page))

    def test_install_bundled_jamulus_launches_and_starts_poll_timer(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "services.bridge_service._bundled_jamulus_installer",
            return_value="/app/Jamulus/jamulus_3.12.2_win.exe",
        ):
            page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))

        with patch(
            "webjam_qt.windows.setup_wizard.subprocess.Popen"
        ) as mock_popen:
            page._install_bundled_jamulus()

        mock_popen.assert_called_once_with(
            ["/app/Jamulus/jamulus_3.12.2_win.exe"], shell=False
        )
        self.assertTrue(page._install_poll_timer.isActive())
        self.assertFalse(page._install_jamulus_btn.isEnabled())

    def test_install_bundled_jamulus_handles_launch_failure_gracefully(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "services.bridge_service._bundled_jamulus_installer",
            return_value="/app/Jamulus/jamulus_3.12.2_win.exe",
        ):
            page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))

        with patch(
            "webjam_qt.windows.setup_wizard.subprocess.Popen",
            side_effect=OSError("no such file"),
        ):
            page._install_bundled_jamulus()  # must not raise

        self.assertFalse(page._install_poll_timer.isActive())
        self.assertTrue(page._install_jamulus_btn.isEnabled())

    def test_poll_fills_path_and_hides_button_once_installed(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            with patch(
                "services.bridge_service._bundled_jamulus_installer",
                return_value="/app/Jamulus/jamulus_3.12.2_win.exe",
            ):
                page = _JamulusPage(AppSettings(
                    jamulus_server="x", jamulus_candidates=[jam.name],
                ))
            # Constructor already found jam.name as a real candidate, so
            # force the pre-install state to exercise the poll in isolation.
            page._jamulus_path.setText("")
            page._install_jamulus_btn.setVisible(True)
            page._install_poll_timer.start()

            page._poll_for_jamulus_install()

            self.assertEqual(page._jamulus_path.text(), jam.name)
            self.assertFalse(page._install_jamulus_btn.isVisibleTo(page))
            self.assertFalse(page._install_poll_timer.isActive())

    def test_poll_does_nothing_while_install_incomplete(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(
            jamulus_server="x", jamulus_candidates=["/nonexistent/Jamulus"],
        ))
        page._install_poll_timer.start()

        page._poll_for_jamulus_install()

        self.assertTrue(page._install_poll_timer.isActive())
        page._install_poll_timer.stop()


# ---------------------------------------------------------------------------
# _WebexPage — URL validation
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestWebexPage(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_valid_https_url_passes(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="https://org.webex.com/meet/bandroom"))
        self.assertTrue(page.validatePage())

    def test_http_url_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="http://org.webex.com/meet/x"))
        self.assertFalse(page.validatePage())

    def test_non_webex_url_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="https://example.com/meet/x"))
        self.assertFalse(page.validatePage())

    def test_bare_word_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="not-a-url"))
        page._url.setText("not-a-url")
        self.assertFalse(page.validatePage())

    def test_missing_scheme_with_dot_auto_prepends_https(self):
        """v0.4.4: typing 'org.webex.com/meet/x' should auto-prepend https://."""
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("org.webex.com/meet/bandroom")
        self.assertTrue(page.validatePage())
        # The text should now be the auto-prepended URL.
        self.assertEqual(page._url.text(), "https://org.webex.com/meet/bandroom")

    def test_scheme_prefixed_bare_word_still_fails(self):
        """A URL like 'https://localhost' has no dot in netloc — reject."""
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://localhost")
        self.assertFalse(page.validatePage())

    def test_empty_url_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("")
        self.assertFalse(page.validatePage())

    def test_webex_url_property_strips_whitespace(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="  https://a.webex.com/m/b  "))
        self.assertEqual(page.webex_url, "https://a.webex.com/m/b")

    def test_guest_group_unchecked_returns_empty_credentials(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(
            webex_url="https://a.webex.com/m/b",
            webex_guest_issuer_id="",
        ))
        self.assertFalse(page._guest_group.isChecked())
        self.assertEqual(page.guest_issuer_id, "")
        self.assertEqual(page.guest_issuer_secret, "")


# ---------------------------------------------------------------------------
# SetupWizard — construction, title, save
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestSetupWizardSmoke(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_wizard_constructs_without_error(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        wizard = SetupWizard(settings=AppSettings())
        self.assertIsNotNone(wizard)

    def test_window_title(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        wizard = SetupWizard(settings=AppSettings())
        self.assertEqual(wizard.windowTitle(), "WebJam Setup")

    def test_starts_on_welcome_page(self):
        from webjam_qt.windows.setup_wizard import SetupWizard, _PageId
        wizard = SetupWizard(settings=AppSettings())
        # currentId() is -1 until exec()/show(); use startId() to verify page order.
        self.assertEqual(wizard.startId(), _PageId.WELCOME)

    def test_skip_welcome_starts_on_jamulus_page(self):
        """v0.4.4: in-session reopens skip the welcome page."""
        from webjam_qt.windows.setup_wizard import SetupWizard, _PageId
        wizard = SetupWizard(settings=AppSettings(), skip_welcome=True)
        self.assertEqual(wizard.startId(), _PageId.JAMULUS)
        self.assertEqual(wizard.windowTitle(), "WebJam Settings")

    def test_save_settings_writes_valid_json(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        Path(tmp).unlink()  # remove so wizard writes fresh
        try:
            settings = AppSettings(
                config_file=tmp,
                jamulus_server="band.example.com",
                jamulus_port=22124,
                webex_url="https://myorg.webex.com/meet/room",
            )
            wizard = SetupWizard(settings=settings)
            wizard._save_settings()
            data = json.loads(Path(tmp).read_text(encoding="utf-8"))
            self.assertEqual(data["jamulus_server"], "band.example.com")
            self.assertEqual(data["jamulus_port"], 22124)
            self.assertEqual(data["webex_url"], "https://myorg.webex.com/meet/room")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_save_settings_round_trips_rpc_port(self):
        from webjam_qt.windows.setup_wizard import SetupWizard
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        Path(tmp).unlink()
        try:
            settings = AppSettings(config_file=tmp, jamulus_rpc_port=33333)
            wizard = SetupWizard(settings=settings)
            wizard._save_settings()
            data = json.loads(Path(tmp).read_text(encoding="utf-8"))
            self.assertEqual(data["jamulus_rpc_port"], 33333)
        finally:
            Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

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

from core.jamulus_name import JAMULUS_NAME_HELP
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


def _skip_headless_qt_wizard_show() -> bool:
    return os.environ.get("QT_QPA_PLATFORM") == "offscreen" and sys.platform == "darwin"


skip_qt_show = unittest.skipIf(
    _skip_headless_qt_wizard_show(),
    "Skipping offscreen macOS interactive wizard UI flow tests to avoid PySide6 abort.",
)


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
        self.assertFalse(page._page_error.isHidden())
        self.assertIn("host", page._page_error.text())

    def test_page_error_hides_while_user_edits(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server=""))
        page._host.setText("")
        self.assertFalse(page.validatePage())
        self.assertFalse(page._page_error.isHidden())
        page._host.setText("myband.example.com")
        self.assertTrue(page._page_error.isHidden())

    def test_filled_host_passes_validation(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="Test Musician",
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
                musician_name="Test Musician",
            ))
            self.assertFalse(page.validatePage())
            self.assertFalse(page._page_error.isHidden())
            self.assertIn("Jamulus", page._page_error.text())

    def test_stale_explicit_path_heals_from_detection(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="Test Musician",
            ))
            # Simulate a stale saved path (moved install / App Translocation).
            page._jamulus_path.setText("/gone/Translocated/Jamulus")
            self.assertTrue(page.validatePage())
            self.assertEqual(page._jamulus_path.text(), jam.name)
            self.assertTrue(page._page_error.isHidden())

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

    def test_rpc_copy_distinguishes_local_client_from_recorder_control(self):
        from PySide6.QtWidgets import QLabel
        from webjam_qt.windows.setup_wizard import _JamulusPage

        page = _JamulusPage(AppSettings(jamulus_server="x"))
        copy = " ".join(label.text() for label in page.findChildren(QLabel))

        self.assertIn("Local Jamulus control port", copy)
        self.assertIn("not the band's audio-server or recorder-control port", copy)

    def test_musician_name_is_required_and_accessible(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="Jeff — Guitar",
            ))
            self.assertEqual(page.musician_name, "Jeff — Guitar")
            self.assertIn("Jamulus", page._musician_name.accessibleName())
            page._musician_name.clear()
            self.assertFalse(page.validatePage())
            self.assertFalse(page._page_error.isHidden())
            self.assertIn("musician name", page._page_error.text())

    def test_musician_name_enforces_utf16_limit_and_shows_8_plus_8_preview(
        self,
    ):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="123456789",
            ))
            self.assertTrue(page.validatePage())
            self.assertIn(
                "12345678 / 9",
                page._musician_name_preview.text(),
            )
            self.assertIn(
                JAMULUS_NAME_HELP,
                page._musician_name.accessibleDescription(),
            )

            page._musician_name.setText("12345678901234567")
            self.assertFalse(page.validatePage())
            self.assertIn("too long", page._page_error.text())

            page._musician_name.setText("Jeff\nStory")
            self.assertFalse(page.validatePage())
            self.assertIn("control characters", page._page_error.text())

    def test_generic_default_requires_a_real_participant_name(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        page = _JamulusPage(AppSettings(musician_name="WebJam Musician"))
        self.assertEqual(page.musician_name, "")
        self.assertFalse(page.validatePage())
        self.assertFalse(page._page_error.isHidden())

    @skip_qt_show
    def test_prefilled_settings_keep_next_enabled_and_advance(self):
        """Qt treats mandatory ('*') fields as incomplete until they CHANGE
        from their registration value, so pre-filled settings used to leave
        Next permanently disabled. Pin the non-mandatory + validatePage()
        design: a valid saved config must advance on the first click."""
        from PySide6.QtWidgets import QWizard
        from webjam_qt.windows.setup_wizard import SetupWizard, _PageId

        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            wizard = SetupWizard(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="Jeff",
            ), skip_welcome=True)
            wizard.show()
            _qapp().processEvents()
            next_button = wizard.button(QWizard.WizardButton.NextButton)
            self.assertTrue(next_button.isEnabled())
            self.assertTrue(wizard.currentPage().isComplete())
            next_button.click()
            _qapp().processEvents()
            self.assertEqual(wizard.currentId(), _PageId.WEBEX)
            wizard.close()

    @skip_qt_show
    def test_blank_name_keeps_next_clickable_with_feedback(self):
        from PySide6.QtWidgets import QWizard
        from webjam_qt.windows.setup_wizard import SetupWizard, _PageId

        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            wizard = SetupWizard(AppSettings(
                jamulus_server="192.168.1.10",
                jamulus_candidates=[jam.name],
                musician_name="WebJam Musician",
            ), skip_welcome=True)
            wizard.show()
            _qapp().processEvents()
            next_button = wizard.button(QWizard.WizardButton.NextButton)
            self.assertTrue(next_button.isEnabled())
            next_button.click()
            _qapp().processEvents()
            self.assertEqual(wizard.currentId(), _PageId.JAMULUS)
            self.assertTrue(wizard.currentPage()._page_error.isVisible())
            wizard.close()

    def test_hosting_forces_same_mac_loopback(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        with patch("webjam_qt.windows.setup_wizard.sys.platform", "darwin"):
            page = _JamulusPage(AppSettings(
                jamulus_server="public.example.com",
                host_server_enabled=True,
            ))
            self.assertTrue(page.host_server_enabled)
            self.assertEqual(page.host, "127.0.0.1")
            self.assertFalse(page._host.isEnabled())

    def test_hosting_copy_reports_bundled_server(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        with (
            patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value="/WebJam/Resources/JamulusServer",
            ),
            patch("webjam_qt.windows.setup_wizard.sys.platform", "darwin"),
        ):
            page = _JamulusPage(AppSettings(host_server_enabled=True))
        self.assertIn("includes JamulusServer.app 3.12.2", page._host_note.text())
        self.assertNotIn("requires JamulusServer", page._host_server.text())

    def test_hosting_control_is_unavailable_off_macos(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage

        with patch("webjam_qt.windows.setup_wizard.sys.platform", "win32"):
            page = _JamulusPage(AppSettings(host_server_enabled=True))
            self.assertFalse(page.host_server_enabled)
        self.assertTrue(page._host_server.isHidden())


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
            "services.bridge_service._is_pinned_jamulus_installer",
            return_value=True,
        ), patch(
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
            "services.bridge_service._is_pinned_jamulus_installer",
            return_value=True,
        ), patch(
            "webjam_qt.windows.setup_wizard.subprocess.Popen",
            side_effect=OSError("no such file"),
        ):
            page._install_bundled_jamulus()  # must not raise

        self.assertFalse(page._install_poll_timer.isActive())
        self.assertTrue(page._install_jamulus_btn.isEnabled())

    def test_install_bundled_jamulus_rejects_replaced_installer(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with patch(
            "services.bridge_service._bundled_jamulus_installer",
            return_value="/app/Jamulus/jamulus_3.12.2_win.exe",
        ):
            page = _JamulusPage(AppSettings(jamulus_server="x", jamulus_candidates=[]))

        with patch(
            "services.bridge_service._is_pinned_jamulus_installer",
            return_value=False,
        ), patch(
            "webjam_qt.windows.setup_wizard.subprocess.Popen"
        ) as mock_popen:
            page._install_bundled_jamulus()

        mock_popen.assert_not_called()
        self.assertIsNone(page._bundled_installer_path)
        self.assertFalse(page._install_jamulus_btn.isEnabled())
        self.assertIn("failed its integrity check", page._install_status.text())

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
        self.assertFalse(page._install_status.isVisibleTo(page))
        page._install_poll_timer.stop()

    def test_install_poll_times_out_and_reenables_button(self):
        """A cancelled/failed installer must not leave the button stuck on
        'Waiting…' forever."""
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(
            jamulus_server="x", jamulus_candidates=["/nonexistent/Jamulus"],
        ))
        page._install_jamulus_btn.setEnabled(False)
        page._install_jamulus_btn.setText("Waiting for install to finish…")
        page._install_poll_ticks = 0
        page._install_poll_timer.start()

        for _ in range(page._INSTALL_POLL_LIMIT):
            page._poll_for_jamulus_install()

        self.assertFalse(page._install_poll_timer.isActive())
        self.assertTrue(page._install_jamulus_btn.isEnabled())
        self.assertEqual(page._install_jamulus_btn.text(), "Install Jamulus now")
        self.assertTrue(page._install_status.isVisibleTo(page))
        self.assertIn("Browse…", page._install_status.text())

    def test_poll_success_leaves_no_timeout_message(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            page = _JamulusPage(AppSettings(
                jamulus_server="x", jamulus_candidates=[jam.name],
            ))
            page._install_poll_ticks = page._INSTALL_POLL_LIMIT - 1
            page._install_poll_timer.start()

            page._poll_for_jamulus_install()

            self.assertFalse(page._install_poll_timer.isActive())
            self.assertFalse(page._install_status.isVisibleTo(page))


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

    def test_supported_non_webex_url_passes_and_identifies_service(self):
        from webjam_qt.windows.setup_wizard import _WebexPage

        page = _WebexPage(
            AppSettings(webex_url="https://zoom.us/j/1234567890")
        )

        self.assertTrue(page.validatePage())
        self.assertEqual(page._site.text(), "Zoom site: zoom.us")

    def test_http_url_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="http://org.webex.com/meet/x"))
        self.assertFalse(page.validatePage())
        # The refusal must say why, not just refocus the field.
        self.assertFalse(page._url_hint.isHidden())
        self.assertTrue(page._url_hint.text())

    def test_generic_public_service_url_passes_with_neutral_identity(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="https://meet.jit.si/WebJamBand"))
        self.assertTrue(page.validatePage())
        self.assertEqual(page._site.text(), "Meeting service site: meet.jit.si")

    def test_reserved_service_url_still_fails(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url="https://example.com/meet/x"))
        self.assertFalse(page.validatePage())

    def test_meeting_link_copy_is_provider_neutral(self):
        from PySide6.QtWidgets import QLabel
        from webjam_qt.windows.setup_wizard import _WebexPage

        page = _WebexPage(AppSettings(webex_url=""))
        copy = " ".join(label.text() for label in page.findChildren(QLabel))

        self.assertEqual(page.title(), "Meeting Conversation")
        self.assertIn(
            "Meeting link (any platform)",
            copy,
        )
        self.assertEqual(
            page._url.accessibleName(),
            "Meeting link for conversation or video",
        )
        self.assertEqual(page._site.accessibleName(), "Meeting service site")
        self.assertIn("selected meeting service handles sign-in", copy)
        self.assertNotIn("Webex handles sign-in", copy)

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

    def test_legacy_guest_credentials_are_not_exposed(self):
        from PySide6.QtWidgets import QGroupBox, QLineEdit
        from webjam_qt.windows.setup_wizard import _WebexPage

        page = _WebexPage(AppSettings(webex_url="https://a.webex.com/m/b"))
        copy = " ".join(group.title() for group in page.findChildren(QGroupBox))
        values = [line.text() for line in page.findChildren(QLineEdit)]

        self.assertNotIn("Guest Issuer", copy)
        self.assertEqual(values, ["https://a.webex.com/m/b"])


# ---------------------------------------------------------------------------
# _RoutingPage — truthful local meter and recording controls
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestRoutingPage(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_page_does_not_offer_discarded_webex_audio_modes(self):
        from PySide6.QtWidgets import QLabel, QRadioButton
        from webjam_qt.windows.setup_wizard import _RoutingPage

        page = _RoutingPage(AppSettings(webex_audio_mode="audience_bridge"))
        page.initializePage()

        copy = " ".join(label.text() for label in page.findChildren(QLabel))
        self.assertEqual(page.findChildren(QRadioButton), [])
        self.assertTrue(page.isComplete())
        self.assertIn(
            "does not configure Jamulus or your meeting service",
            copy,
        )
        self.assertNotIn("Audience broadcast bridge", copy)
        self.assertNotIn("Choose how this Mac uses Webex audio", page.subTitle())

    def test_local_capture_controls_only_saved_local_behavior(self):
        from webjam_qt.windows.setup_wizard import _RoutingPage

        page = _RoutingPage(AppSettings(
            local_capture_enabled=True,
            takes_directory="/tmp/WebJam Takes",
        ))
        self.assertTrue(page.local_capture_enabled)
        self.assertEqual(page.takes_directory, "/tmp/WebJam Takes")
        self.assertTrue(page._device_picker.isEnabled())
        page._capture_chk.setChecked(False)
        self.assertFalse(page.local_capture_enabled)
        self.assertTrue(page._device_picker.isEnabled())
        self.assertFalse(page._capture_hint.isVisibleTo(page))

    def test_local_capture_requires_and_can_choose_takes_folder(self):
        from webjam_qt.windows.setup_wizard import _RoutingPage

        page = _RoutingPage(AppSettings(
            local_capture_enabled=True,
            takes_directory="",
        ))
        self.assertFalse(page.isComplete())
        with patch(
            "webjam_qt.windows.setup_wizard.QFileDialog.getExistingDirectory",
            return_value="/tmp/WebJam Takes",
        ):
            page._choose_takes_directory()
        self.assertTrue(page.isComplete())
        self.assertEqual(page.takes_directory, "/tmp/WebJam Takes")

    def test_accessibility_names_describe_the_actual_local_controls(self):
        from webjam_qt.windows.setup_wizard import _RoutingPage

        page = _RoutingPage(AppSettings())
        self.assertEqual(
            page._capture_chk.accessibleName(),
            "Enable supplemental local recording",
        )
        self.assertEqual(
            page._device_picker.accessibleName(),
            "Meter and local recording input",
        )


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

    def test_save_uses_new_audio_fields_and_omits_legacy_webex_secrets(self):
        from webjam_qt.windows.setup_wizard import SetupWizard

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        Path(tmp).unlink()
        try:
            settings = AppSettings(
                config_file=tmp,
                webex_audio_mode="talkback",
                local_capture_enabled=True,
                musician_name="Jeff — Guitar",
                takes_directory="/tmp/WebJam Takes",
            )
            wizard = SetupWizard(settings=settings)
            wizard._save_settings()
            data = json.loads(Path(tmp).read_text(encoding="utf-8"))

            self.assertNotIn("webex_audio_mode", data)
            self.assertTrue(data["local_capture_enabled"])
            self.assertEqual(data["musician_name"], "Jeff — Guitar")
            self.assertEqual(data["takes_directory"], "/tmp/WebJam Takes")
            self.assertNotIn("webex_audio_bridge_enabled", data)
            self.assertNotIn("webex_guest_issuer_id", data)
            self.assertNotIn("webex_guest_issuer_secret", data)
            self.assertNotIn("webex_display_name", data)
        finally:
            Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

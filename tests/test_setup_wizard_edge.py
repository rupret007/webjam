from __future__ import annotations

import unittest
from unittest.mock import patch

from core.settings import AppSettings
from ui.views.setup_wizard import SetupWizard


class TestSetupWizardDiagnosticsEdge(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AppSettings(webex_url="https://webex.example.com/meet/test")

    @patch.object(SetupWizard, "check_tcp_hint", return_value=(True, "ok"))
    def test_non_mapping_diagnostics_payload_is_handled(self, _tcp_hint):
        results = SetupWizard.run_preflight_checks(
            settings=self.settings,
            find_jamulus=lambda: None,
            diagnostics_provider=lambda: ["unexpected", "payload"],
        )
        audio_result = next(item for item in results if item[0] == "Audio diagnostics")
        self.assertFalse(audio_result[1])
        self.assertIn("expected a mapping payload", audio_result[2])

    @patch.object(SetupWizard, "check_tcp_hint", return_value=(True, "ok"))
    def test_diagnostics_callback_exception_is_handled(self, _tcp_hint):
        def _raise() -> dict[str, str]:
            raise RuntimeError("probe failed")

        results = SetupWizard.run_preflight_checks(
            settings=self.settings,
            find_jamulus=lambda: None,
            diagnostics_provider=_raise,
        )
        audio_result = next(item for item in results if item[0] == "Audio diagnostics")
        self.assertFalse(audio_result[1])
        self.assertIn("callback failed", audio_result[2])

    @patch.object(SetupWizard, "check_tcp_hint", return_value=(True, "ok"))
    def test_invalid_jamulus_path_type_is_handled(self, _tcp_hint):
        results = SetupWizard.run_preflight_checks(
            settings=self.settings,
            find_jamulus=lambda: 123,  # type: ignore[return-value]
            diagnostics_provider=lambda: {},
        )
        jamulus_result = next(item for item in results if item[0] == "Jamulus executable")
        self.assertFalse(jamulus_result[1])
        self.assertIn("invalid path type", jamulus_result[2])

    def test_non_string_webex_url_is_rejected_without_exception(self):
        ok, detail = SetupWizard.check_webex_url(123)  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIn("Invalid Webex URL", detail)

    def test_check_tcp_hint_invalid_host_returns_failure(self):
        ok, detail = SetupWizard.check_tcp_hint("", 22124)
        self.assertFalse(ok)
        self.assertIn("Invalid host", detail)

    @patch("ui.views.setup_wizard.socket.create_connection", side_effect=OSError("offline"))
    def test_check_tcp_hint_zero_retries_coerces_to_one_attempt(self, create_connection_mock):
        ok, detail = SetupWizard.check_tcp_hint("127.0.0.1", 22124, retries=0)
        self.assertFalse(ok)
        self.assertIn("after 1 attempts", detail)
        create_connection_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

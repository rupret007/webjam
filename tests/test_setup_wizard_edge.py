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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from core.settings import AppSettings
from ui.views.setup_wizard import SetupWizard


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


class _ValueVar:
    def __init__(self):
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _FakeWindow:
    def __init__(self, exists: bool = True):
        self._exists = exists

    def winfo_exists(self) -> bool:
        return self._exists

    def after(self, _delay_ms: int, callback):
        callback()


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

    @patch("ui.views.setup_wizard.socket.create_connection", side_effect=OSError("offline"))
    def test_check_tcp_hint_invalid_retries_type_uses_default(self, create_connection_mock):
        ok, detail = SetupWizard.check_tcp_hint("127.0.0.1", 22124, retries="bad")  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIn("after 3 attempts", detail)
        self.assertEqual(create_connection_mock.call_count, 3)

    @patch("ui.views.setup_wizard.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    @patch.object(SetupWizard, "run_preflight_checks", return_value=[("Jamulus executable", True, "ok")])
    def test_run_checks_and_render_completes_via_background_worker(self, run_checks_mock, _thread_mock):
        wizard = SetupWizard.__new__(SetupWizard)
        wizard._checks_inflight = False
        wizard.settings = self.settings
        wizard.find_jamulus = lambda: None
        wizard.diagnostics_provider = lambda: {}
        wizard.window = _FakeWindow(exists=True)
        wizard.step_index = 1
        wizard.steps = ["Welcome", "Preflight Checks", "Finish"]
        wizard.check_results = []
        wizard.body_var = _ValueVar()
        wizard.summary_var = _ValueVar()
        wizard._set_results_text = MagicMock()
        wizard._render_step = MagicMock()
        wizard.next_btn = MagicMock()
        wizard.back_btn = MagicMock()
        wizard.rerun_btn = MagicMock()

        SetupWizard._run_checks_and_render(wizard)

        run_checks_mock.assert_called_once_with(self.settings, wizard.find_jamulus, wizard.diagnostics_provider)
        self.assertFalse(wizard._checks_inflight)
        self.assertEqual(wizard.check_results, [("Jamulus executable", True, "ok")])
        wizard._set_results_text.assert_called()
        wizard._render_step.assert_called_once()
        wizard.next_btn.configure.assert_any_call(state=tk.DISABLED, text="Next")

    @patch("ui.views.setup_wizard.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    @patch.object(SetupWizard, "run_preflight_checks", return_value=[("Jamulus executable", True, "ok")])
    def test_run_checks_and_render_handles_closed_window(self, run_checks_mock, _thread_mock):
        wizard = SetupWizard.__new__(SetupWizard)
        wizard._checks_inflight = False
        wizard.settings = self.settings
        wizard.find_jamulus = lambda: None
        wizard.diagnostics_provider = lambda: {}
        wizard.window = _FakeWindow(exists=False)
        wizard.step_index = 1
        wizard.steps = ["Welcome", "Preflight Checks", "Finish"]
        wizard.check_results = []
        wizard.body_var = _ValueVar()
        wizard.summary_var = _ValueVar()
        wizard._set_results_text = MagicMock()
        wizard._render_step = MagicMock()
        wizard.next_btn = MagicMock()
        wizard.back_btn = MagicMock()
        wizard.rerun_btn = MagicMock()

        SetupWizard._run_checks_and_render(wizard)

        run_checks_mock.assert_called_once()
        self.assertFalse(wizard._checks_inflight)
        self.assertEqual(wizard.check_results, [("Jamulus executable", True, "ok")])
        wizard._render_step.assert_not_called()

    @patch.object(SetupWizard, "run_preflight_checks", side_effect=AssertionError("should not run"))
    def test_run_checks_and_render_ignores_duplicate_inflight_request(self, _run_checks_mock):
        wizard = SetupWizard.__new__(SetupWizard)
        wizard._checks_inflight = True
        wizard.settings = self.settings
        wizard.find_jamulus = lambda: None
        wizard.diagnostics_provider = lambda: {}
        wizard.window = _FakeWindow(exists=True)
        wizard.step_index = 1
        wizard.steps = ["Welcome", "Preflight Checks", "Finish"]
        wizard.check_results = []
        wizard.body_var = _ValueVar()
        wizard.summary_var = _ValueVar()
        wizard._set_results_text = MagicMock()
        wizard._render_step = MagicMock()
        wizard.next_btn = MagicMock()
        wizard.back_btn = MagicMock()
        wizard.rerun_btn = MagicMock()

        SetupWizard._run_checks_and_render(wizard)

        self.assertTrue(wizard._checks_inflight)
        wizard._render_step.assert_not_called()

    @patch("ui.views.setup_wizard.threading.Thread", side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs))
    @patch.object(SetupWizard, "run_preflight_checks", side_effect=RuntimeError("probe exploded"))
    def test_run_checks_and_render_converts_worker_exception_into_failure_result(self, _run_checks_mock, _thread_mock):
        wizard = SetupWizard.__new__(SetupWizard)
        wizard._checks_inflight = False
        wizard.settings = self.settings
        wizard.find_jamulus = lambda: None
        wizard.diagnostics_provider = lambda: {}
        wizard.window = _FakeWindow(exists=True)
        wizard.step_index = 1
        wizard.steps = ["Welcome", "Preflight Checks", "Finish"]
        wizard.check_results = []
        wizard.body_var = _ValueVar()
        wizard.summary_var = _ValueVar()
        wizard._set_results_text = MagicMock()
        wizard._render_step = MagicMock()
        wizard.next_btn = MagicMock()
        wizard.back_btn = MagicMock()
        wizard.rerun_btn = MagicMock()

        SetupWizard._run_checks_and_render(wizard)

        self.assertFalse(wizard._checks_inflight)
        self.assertEqual(wizard.check_results[0][0], "Preflight checks")
        self.assertFalse(wizard.check_results[0][1])
        self.assertIn("probe exploded", wizard.check_results[0][2])
        wizard._render_step.assert_called_once()


if __name__ == "__main__":
    unittest.main()

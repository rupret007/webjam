"""Regression tests for ApplicationController RPC-hang banner (v0.4.5).

Verifies the controller's _on_reconnect_tick() correctly toggles the
"stopped responding" / "responding again" flash banners based on the
``last_activity_age()`` reported by JamulusController.rpc_client.
"""
from __future__ import annotations

import io
import logging
import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from tests.support.jamulus_monitor import bind_primary_rpc_monitor  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestRpcHangBanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        # Reset banner state before each test.
        self.controller._jamulus_connected = True
        self.controller.audio.recovering = False
        self.controller._rpc_hang_banner_shown = False
        self.controller._reconnect_banner_shown = False
        self.controller._reconnect_gave_up = False
        self.controller._clear_primary_local_roster_proof()
        with self.controller.bridge._reconnect_lock:
            self.controller.bridge._reset_jamulus_recovery_locked()

        # Fake a running Jamulus subprocess: poll() == None means alive.
        fake_proc = MagicMock()
        fake_proc.pid = 4500
        fake_proc.poll.return_value = None
        self.controller.bridge.jamulus_process = fake_proc
        self.controller.bridge.jamulus_launch_intended = True
        self.controller.bridge.jamulus_state = "Running"

        # Avoid invoking real reconnect logic during the tick.
        self.controller.bridge.attempt_auto_reconnects = MagicMock()

        # Replace flash_message with a tracker.
        self.controller.window.flash_message = MagicMock()
        self.controller.window.set_status_audio = MagicMock()

    def _set_rpc(self, age: float, *, available: bool = True) -> MagicMock:
        rpc = MagicMock()
        rpc.available = available
        rpc.last_activity_age.return_value = age
        self.controller.jamulus.rpc_client = rpc
        bind_primary_rpc_monitor(self.controller)
        recovery = self.controller._primary_jamulus_recovery_snapshot()
        if (
            recovery is not None
            and recovery.process_alive
            and recovery.process_id > 0
            and recovery.rpc_freshness.value == "fresh"
        ):
            self.controller._record_primary_local_roster_proof(recovery)
        return rpc

    @staticmethod
    def _wait_until(predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_banner_shown_when_age_exceeds_threshold(self):
        self._set_rpc(30.0)

        self.controller._on_reconnect_tick()

        self.assertTrue(self.controller._rpc_hang_banner_shown)
        # Verify we saw a flash containing "stopped responding".
        msgs = [
            call.args[0] for call in
            self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(
            any("stopped responding" in m for m in msgs),
            f"expected 'stopped responding' flash, got {msgs!r}",
        )

    def test_rpc_freshness_fails_closed_for_unusable_evidence(self):
        cases = (
            ("unavailable", False, 0.0, None),
            ("never_authenticated", True, float("inf"), None),
            ("nan", True, float("nan"), None),
            ("negative", True, -1.0, None),
        )
        for name, available, age, expected_age in cases:
            with self.subTest(name=name):
                self._set_rpc(age, available=available)
                fresh, measured = (
                    self.controller._primary_jamulus_rpc_freshness()
                )
                self.assertFalse(fresh)
                self.assertEqual(measured, expected_age)

        rpc = self._set_rpc(0.0)
        rpc.last_activity_age.side_effect = RuntimeError("provider failed")
        fresh, measured = self.controller._primary_jamulus_rpc_freshness()
        self.assertFalse(fresh)
        self.assertIsNone(measured)

    def test_recovery_snapshot_error_log_does_not_disclose_private_path(self):
        private_path = "/Users/musician/private/jamulus-rpc.secret"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger = logging.getLogger("webjam.qt.application_controller")
        previous_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(handler)
        try:
            with patch.object(
                self.controller.bridge,
                "jamulus_recovery_snapshot",
                side_effect=FileNotFoundError(private_path),
            ):
                self.assertIsNone(
                    self.controller._primary_jamulus_recovery_snapshot()
                )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            handler.close()

        formatted = stream.getvalue()
        self.assertIn("FileNotFoundError", formatted)
        self.assertNotIn(private_path, formatted)
        self.assertNotIn("/Users/musician", formatted)

    def test_infinite_rpc_age_never_crashes_or_formats_as_seconds(self):
        self._set_rpc(float("inf"))

        self.controller._on_reconnect_tick()

        messages = [
            call.args[0]
            for call in self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(any("no verified heartbeat" in text for text in messages))

    def test_stale_rpc_reinvalidates_fast_reconnect_with_existing_banner(self):
        self.controller._rpc_hang_banner_shown = True
        self.controller.audio.recovering = False
        self._set_rpc(30.0)
        self.controller.window.flash_message.reset_mock()

        with patch.object(
            self.controller,
            "_stop_reference_track_for_session_end",
        ) as stop_reference:
            self.controller._on_reconnect_tick()

        self.assertFalse(self.controller._jamulus_connected)
        self.assertTrue(self.controller.audio.recovering)
        self.assertTrue(self.controller._rpc_hang_banner_shown)
        stop_reference.assert_called_once_with(background=True)
        msgs = [
            call.args[0]
            for call in self.controller.window.flash_message.call_args_list
        ]
        self.assertFalse(any("responding again" in message for message in msgs))

    def test_banner_cleared_when_activity_resumes(self):
        # Start with the banner already raised.
        self.controller._rpc_hang_banner_shown = True
        self._set_rpc(2.0)

        self.controller._on_reconnect_tick()

        self.assertFalse(self.controller._rpc_hang_banner_shown)
        msgs = [
            call.args[0] for call in
            self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(
            any("Band audio reconnected" in m for m in msgs),
            f"expected one reconnect flash, got {msgs!r}",
        )

    def test_reconnect_exhaustion_with_hung_process_shows_failed_state(self):
        self.controller.bridge.jamulus_reconnect_attempts = 5
        self.controller.bridge._jamulus_recovery_generation = 1
        self.controller.bridge._jamulus_recovery_active = True
        self.controller.bridge._jamulus_recovery_exhausted = True
        self.controller._rpc_hang_banner_shown = True
        self.controller._reconnect_gave_up = False
        self.controller._connection_timer = MagicMock()
        self.controller.window.session_strip.set_audio_state = MagicMock()
        self.controller.window.participant_grid.set_session_state = MagicMock()

        fake_proc = self.controller.bridge.jamulus_process
        fake_proc.poll.return_value = None
        self._set_rpc(30.0)
        self.controller.bridge.attempt_auto_reconnects = MagicMock()
        self.controller._stop_reference_track_for_session_end = MagicMock(
            return_value=True
        )

        def stop_primary() -> bool:
            self.controller.bridge.jamulus_process = None
            self.controller.bridge.jamulus_launch_intended = False
            return True

        self.controller.bridge.stop_jamulus = MagicMock(side_effect=stop_primary)

        self.controller._on_reconnect_tick()

        self.assertTrue(
            self._wait_until(
                lambda: not self.controller._primary_recovery_retire_inflight
            )
        )
        self.assertTrue(self.controller._reconnect_gave_up)
        self.assertFalse(self.controller._rpc_hang_banner_shown)
        self.controller.window.session_strip.set_audio_state.assert_called_with(
            "Start Session", enabled=True
        )
        self.assertEqual(self.controller.audio.connected, False)
        self.assertIsNone(self.controller.bridge.jamulus_process)
        self.assertFalse(self.controller.bridge.jamulus_launch_intended)
        self.controller.bridge.stop_jamulus.assert_called_once_with()
        msgs = [
            call.args[0] for call in self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(
            any(
                "stopped safely" in m for m in msgs
            ),
            f"expected exhaustion flash, got {msgs!r}",
        )

    def test_failed_hang_state_ignores_late_response_until_explicit_retry(self):
        self.controller._rpc_hang_banner_shown = True
        self.controller._reconnect_gave_up = True
        self.controller.bridge.attempt_auto_reconnects = MagicMock()
        self._set_rpc(2.0)
        fake_proc = self.controller.bridge.jamulus_process
        fake_proc.poll.return_value = None

        self.controller._on_reconnect_tick()

        self.assertTrue(self.controller._reconnect_gave_up)
        self.assertTrue(self.controller._rpc_hang_banner_shown)
        self.controller.bridge.attempt_auto_reconnects.assert_not_called()
        self.assertFalse(
            any(
                "reconnected" in call.args[0]
                for call in self.controller.window.flash_message.call_args_list
            )
        )

    def test_no_banner_when_age_below_threshold_and_not_already_shown(self):
        self._set_rpc(1.0)

        self.controller._on_reconnect_tick()

        self.assertFalse(self.controller._rpc_hang_banner_shown)
        msgs = [
            call.args[0] for call in
            self.controller.window.flash_message.call_args_list
        ]
        # Neither hang banner should fire.
        self.assertFalse(any("stopped responding" in m for m in msgs))
        self.assertFalse(any("responding again" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()

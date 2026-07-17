"""Regression tests for ApplicationController RPC-hang banner (v0.4.5).

Verifies the controller's _on_reconnect_tick() correctly toggles the
"stopped responding" / "responding again" flash banners based on the
``last_activity_age()`` reported by JamulusController.rpc_client.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
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
        self.controller._rpc_hang_banner_shown = False
        self.controller._reconnect_banner_shown = False

        # Fake a running Jamulus subprocess: poll() == None means alive.
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        self.controller.bridge.jamulus_process = fake_proc
        self.controller.bridge.jamulus_launch_intended = True
        self.controller.bridge.jamulus_state = "Running"

        # Avoid invoking real reconnect logic during the tick.
        self.controller.bridge.attempt_auto_reconnects = MagicMock()

        # Replace flash_message with a tracker.
        self.controller.window.flash_message = MagicMock()
        self.controller.window.set_status_audio = MagicMock()

    def test_banner_shown_when_age_exceeds_threshold(self):
        self.controller.jamulus.rpc_client = MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 30.0

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

    def test_banner_cleared_when_activity_resumes(self):
        # Start with the banner already raised.
        self.controller._rpc_hang_banner_shown = True
        self.controller.jamulus.rpc_client = MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 2.0

        self.controller._on_reconnect_tick()

        self.assertFalse(self.controller._rpc_hang_banner_shown)
        msgs = [
            call.args[0] for call in
            self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(
            any("responding again" in m for m in msgs),
            f"expected 'responding again' flash, got {msgs!r}",
        )

    def test_reconnect_exhaustion_with_hung_process_shows_failed_state(self):
        self.controller.bridge.jamulus_reconnect_attempts = 5
        self.controller._rpc_hang_banner_shown = True
        self.controller._reconnect_gave_up = False
        self.controller._connection_timer = MagicMock()
        self.controller.window.session_strip.set_audio_state = MagicMock()
        self.controller.window.participant_grid.set_session_state = MagicMock()

        fake_proc = self.controller.bridge.jamulus_process
        fake_proc.poll.return_value = None
        self.controller.jamulus.rpc_client = MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 30.0
        self.controller.bridge.attempt_auto_reconnects = MagicMock()

        self.controller._on_reconnect_tick()

        self.assertTrue(self.controller._reconnect_gave_up)
        self.assertFalse(self.controller._rpc_hang_banner_shown)
        self.controller.window.session_strip.set_audio_state.assert_called_with(
            "Start Session", enabled=True
        )
        self.assertEqual(self.controller.audio.connected, False)
        msgs = [
            call.args[0] for call in self.controller.window.flash_message.call_args_list
        ]
        self.assertTrue(
            any(
                "couldn't recover after 5 tries" in m for m in msgs
            ),
            f"expected exhaustion flash, got {msgs!r}",
        )

    def test_failed_hang_state_clears_upon_response_and_reopens_connection_state(self):
        self.controller._rpc_hang_banner_shown = True
        self.controller._reconnect_gave_up = True
        self.controller.bridge.attempt_auto_reconnects = MagicMock()
        self.controller.jamulus.rpc_client = MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 2.0
        fake_proc = self.controller.bridge.jamulus_process
        fake_proc.poll.return_value = None

        self.controller._on_reconnect_tick()

        self.assertFalse(self.controller._reconnect_gave_up)
        self.assertFalse(self.controller._rpc_hang_banner_shown)
        self.assertTrue(
            any("responding again" in m for m in [
                call.args[0] for call in self.controller.window.flash_message.call_args_list
            ]),
            f"expected 'responding again' flash, got {[call.args[0] for call in self.controller.window.flash_message.call_args_list]!r}",
        )

    def test_no_banner_when_age_below_threshold_and_not_already_shown(self):
        self.controller.jamulus.rpc_client = MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 1.0

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

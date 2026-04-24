"""
Tests for ApplicationController toggle behaviour (v0.4.4).

Verifies the Launch ↔ Stop and Join ↔ Leave button state transitions in
``_refresh_readiness`` and the ``_is_jamulus_running`` / ``_is_video_active``
helpers. Uses QT_QPA_PLATFORM=offscreen so no display is required.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

import unittest  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestToggleButtonState(unittest.TestCase):
    """Verify the audio/video buttons toggle labels and helpers behave."""

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

    # -- helper predicates -------------------------------------------------
    def test_is_jamulus_running_false_when_state_is_initial(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.assertFalse(self.controller._is_jamulus_running())

    def test_is_jamulus_running_true_when_state_is_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.assertTrue(self.controller._is_jamulus_running())

    def test_is_jamulus_running_true_when_state_is_already_running(self):
        self.controller.bridge.jamulus_state = "Already running"
        self.assertTrue(self.controller._is_jamulus_running())

    def test_is_video_active_false_when_not_opened(self):
        self.controller.bridge.webex_state = "Not opened"
        self.assertFalse(self.controller._is_video_active())

    def test_is_video_active_true_for_in_meeting(self):
        self.controller.bridge.webex_state = "In Meeting"
        self.assertTrue(self.controller._is_video_active())

    def test_is_video_active_true_for_opened_in_browser(self):
        self.controller.bridge.webex_state = "Opened in browser"
        self.assertTrue(self.controller._is_video_active())

    def test_is_video_active_true_for_lobby(self):
        self.controller.bridge.webex_state = "Lobby"
        self.assertTrue(self.controller._is_video_active())

    # -- button labels -----------------------------------------------------
    def test_audio_button_says_launch_when_stopped(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._audio_button.text(), "Launch Audio"
        )

    def test_audio_button_says_stop_when_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._audio_button.text(), "Stop Audio"
        )

    def test_video_button_says_join_when_not_active(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._video_button.text(), "Join Video"
        )

    def test_video_button_says_leave_when_in_meeting(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "In Meeting"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._video_button.text(), "Leave Video"
        )

    def test_status_audio_includes_server_when_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        # Status bar should show "Running ({server}:{port})"
        text = self.window._status_audio.text()
        self.assertIn("Running", text)
        self.assertIn(":", text)  # server:port separator
        self.assertIn(str(self.controller.settings.jamulus_port), text)


if __name__ == "__main__":
    unittest.main()

"""Qt-specific regression tests for the audit remediation plan."""
from __future__ import annotations

import os
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestStopAudioParticipantRace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Race",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def test_late_participant_callback_ignored_while_stopping(self):
        real = [JamulusParticipant(channel_id=5, name="Late")]
        self.controller.audio.stopping = True
        self.controller._apply_jamulus_participants(real)
        self.assertNotIn(5, self.controller.participants)

    def test_stopping_latch_cleared_when_audio_not_running(self):
        self.controller.audio.stopping = True
        self.controller.bridge.jamulus_state = "Stopped"
        self.controller._refresh_readiness()
        self.assertFalse(self.controller.audio.stopping)


class TestStatusBannerColor(unittest.TestCase):
    """Regression: _set_status_banner's color arg must reach the UI."""

    @classmethod
    def setUpClass(cls):
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Banner",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def test_color_is_applied_to_status_bar_stylesheet(self):
        self.controller._set_status_banner("Reconnecting…", color="#ffcc00")
        self.assertIn("#ffcc00", self.window._status_bar.styleSheet())

    def test_no_color_clears_any_previous_tint(self):
        self.controller._set_status_banner("Warn", color="#ffcc00")
        self.assertNotEqual(self.window._status_bar.styleSheet(), "")

        self.controller._set_status_banner("All good")
        self.assertEqual(self.window._status_bar.styleSheet(), "")

    def test_tint_clears_when_timed_message_expires(self):
        self.window.flash_message("Warn", ms=10, color="#ffcc00")
        self.assertNotEqual(self.window._status_bar.styleSheet(), "")
        # Emulate QStatusBar clearing the message after its timeout.
        self.window._status_bar.clearMessage()
        self.assertEqual(self.window._status_bar.styleSheet(), "")


class TestWebexEmbedShutdownTeardown(unittest.TestCase):
    """Regression: shutdown() must tear down the embedded QWebEngineView."""

    def test_shutdown_calls_webex_embed_shutdown(self):
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Shutdown",
        )
        controller = ApplicationController(window, settings=AppSettings())
        window.webex_embed.shutdown = unittest.mock.MagicMock()

        controller.shutdown()

        window.webex_embed.shutdown.assert_called_once()

    def test_shutdown_survives_webex_embed_teardown_failure(self):
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="ShutdownFail",
        )
        controller = ApplicationController(window, settings=AppSettings())
        window.webex_embed.shutdown = unittest.mock.MagicMock(
            side_effect=RuntimeError("boom")
        )

        try:
            controller.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"shutdown() must not propagate embed teardown errors: {exc!r}")


if __name__ == "__main__":
    unittest.main()

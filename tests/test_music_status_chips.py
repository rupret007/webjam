"""Music-family status bar: Video chip only when it carries meeting facts."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from webex_integration import WebexLaunchState  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _make_controller(*, profile_key: str = "music") -> tuple[ConductorWindow, ApplicationController]:
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Chips",
    )
    settings = AppSettings()
    settings.last_creator_profile_key = profile_key
    controller = ApplicationController(window, settings=settings)
    return window, controller


class TestMusicInformativeVideoChip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window, cls.controller = _make_controller()

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        c = self.controller
        w = self.window
        c.settings.webex_url = ""
        c._session_meeting_url = None
        c.bridge.webex_state = WebexLaunchState.NOT_OPENED.value
        c.bridge.jamulus_state = "Running"
        c._jamulus_connected = True
        c.audio.stopping = False
        c.audio.cleanup_retry_required = False
        c.audio.ended_by_user = False
        w.set_legacy_status_chips_enabled(True)

    def test_hides_video_chip_when_not_opened_without_meeting_link(self):
        self.controller._refresh_readiness()
        w = self.window
        self.assertIn("Connected", w._status_audio.text())
        self.assertFalse(w._status_audio.isHidden())
        self.assertTrue(w._status_video.isHidden())
        self.assertFalse(w._status_video.property("status_permanent"))

    def test_shows_video_chip_when_meeting_link_configured(self):
        self.controller.settings.webex_url = "https://meet.jit.si/WebJamBand"
        self.controller._refresh_readiness()
        w = self.window
        self.assertIn(WebexLaunchState.NOT_OPENED.value, w._status_video.text())
        self.assertFalse(w._status_video.isHidden())

    def test_shows_video_chip_when_opened_externally(self):
        self.controller.bridge.webex_state = "Opened externally"
        self.controller._refresh_readiness()
        w = self.window
        self.assertIn("Opened externally", w._status_video.text())
        self.assertFalse(w._status_video.isHidden())


if __name__ == "__main__":
    unittest.main()

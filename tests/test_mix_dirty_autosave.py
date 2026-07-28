"""
Tests for the mid-session "mix dirty" tracking + auto-save on shutdown.

The flag flips True when the user changes any fader/mute/solo and flips
False after a successful Ctrl+S.  At shutdown, if the mix is dirty AND
we're connected to Jamulus, the controller should call ``_on_save_mix``
automatically so users don't lose mid-session tweaks.
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


class TestMixDirtyAutosave(unittest.TestCase):
    def setUp(self):
        self.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        self.controller = ApplicationController(self.window, settings=AppSettings())

    def tearDown(self):
        # Avoid re-triggering auto-save during teardown by clearing the flag.
        self.controller._mix_dirty = False
        self.controller.shutdown()

    def test_dirty_flag_set_on_fader_change(self):
        self.assertFalse(self.controller._mix_dirty)
        # Dirty tracking is independent of whether the participant is still present.
        self.controller._on_fader_changed(1, 80)
        self.assertTrue(self.controller._mix_dirty)

    def test_dirty_flag_cleared_on_save_mix(self):
        self.controller._mix_dirty = True
        # Stub jamulus.serialize_mix to return a trivial payload.
        self.controller.jamulus = MagicMock()
        self.controller.jamulus.serialize_mix.return_value = {"channels": []}
        self.controller._on_save_mix()
        self.assertFalse(self.controller._mix_dirty)

    def test_shutdown_saves_when_dirty_and_connected(self):
        self.controller._mix_dirty = True
        self.controller._jamulus_connected = True
        self.controller._on_save_mix = MagicMock()  # type: ignore[method-assign]
        # Avoid touching the real bridge / webex / persistence on shutdown
        self.controller.bridge = MagicMock()
        self.controller.bridge.hosted_server_alive.return_value = False
        self.controller.bridge.stop_jamulus.return_value = True
        self.controller.webex = MagicMock()
        self.controller._persistence = MagicMock()
        self.controller.shutdown()
        self.controller._on_save_mix.assert_called_once()

    def test_shutdown_does_not_save_when_clean(self):
        self.controller._mix_dirty = False
        self.controller._jamulus_connected = True
        self.controller._on_save_mix = MagicMock()  # type: ignore[method-assign]
        self.controller.bridge = MagicMock()
        self.controller.bridge.hosted_server_alive.return_value = False
        self.controller.bridge.stop_jamulus.return_value = True
        self.controller.webex = MagicMock()
        self.controller._persistence = MagicMock()
        self.controller.shutdown()
        self.controller._on_save_mix.assert_not_called()

    def test_shutdown_does_not_save_when_disconnected(self):
        # Even if dirty, skip auto-save when not connected — in-memory state
        # has no confirmed live state and could clobber a previously-saved mix.
        self.controller._mix_dirty = True
        self.controller._jamulus_connected = False
        self.controller._on_save_mix = MagicMock()  # type: ignore[method-assign]
        self.controller.bridge = MagicMock()
        self.controller.bridge.hosted_server_alive.return_value = False
        self.controller.bridge.stop_jamulus.return_value = True
        self.controller.webex = MagicMock()
        self.controller._persistence = MagicMock()
        self.controller.shutdown()
        self.controller._on_save_mix.assert_not_called()


if __name__ == "__main__":
    unittest.main()

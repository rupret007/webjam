"""
ApplicationController idle<->real participant transition: only confirmed
Jamulus participants become cards; stopping returns to an actionable empty state.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestDemoToRealTransition(unittest.TestCase):
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
        # Reset to disconnected state before each test. Also clear the stopping
        # latch: audio.stop() sets it True and only bridge.stop_jamulus()'s
        # refresh_readiness callback clears it in production, but these
        # tests mock stop_jamulus out entirely, so a prior test's stop()
        # would otherwise leak `stopping=True` into the next test and make
        # apply_participants() silently no-op.
        self.controller._jamulus_connected = False
        self.controller.audio.stopping = False
        self.controller._reset_to_demo_state()

    def test_idle_replaced_then_restored_after_stop_audio(self):
        self.assertEqual(self.controller.participants, {})
        self.assertFalse(self.window.participant_grid._empty_state.isHidden())
        self.assertEqual(
            self.window.participant_grid._empty_title.text(), "Ready when you are"
        )

        # Real participants arrive via Jamulus callback.
        real = [
            JamulusParticipant(channel_id=10, name="RealAlice", is_local=True),
            JamulusParticipant(channel_id=11, name="RealBob"),
        ]
        self.controller._apply_jamulus_participants(real)

        # Only real channels are rendered.
        self.assertEqual(set(self.controller.participants.keys()), {10, 11})
        self.assertTrue(self.controller._jamulus_connected)
        self.assertEqual(self.controller.participants[10].name, "RealAlice")

        # Stop Audio: patch QMessageBox.question to auto-confirm Yes, and
        # patch bridge.stop_jamulus so the worker thread doesn't actually
        # tear down the controller's services.
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            self.controller.bridge, "stop_jamulus", return_value=True,
        ):
            self.controller._stop_audio()

        # Truthful disconnected state restored; no fake mixer cards.
        self.assertFalse(self.controller._jamulus_connected)
        self.assertEqual(self.controller.participants, {})
        self.assertEqual(
            self.window.participant_grid._empty_title.text(), "Ready when you are"
        )

    def test_stop_audio_cancelled_keeps_real_state(self):
        # Connect to real participants
        real = [JamulusParticipant(channel_id=20, name="Bandmate")]
        self.controller._apply_jamulus_participants(real)
        self.assertTrue(self.controller._jamulus_connected)

        # User clicks "No" — controller should leave state alone.
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.No,
        ), patch.object(
            self.controller.bridge, "stop_jamulus", return_value=True,
        ):
            self.controller._stop_audio()

        self.assertTrue(self.controller._jamulus_connected)
        self.assertIn(20, self.controller.participants)


if __name__ == "__main__":
    unittest.main()

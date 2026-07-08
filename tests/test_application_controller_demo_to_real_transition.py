"""
ApplicationController demo<->real participant transition: when real Jamulus
data arrives the demo grid is replaced; when the user clicks Stop Audio
(confirming the dialog), the demo grid is restored.
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
        # Reset to demo state before each test. Also clear the stopping
        # latch: audio.stop() sets it True and only bridge.stop_jamulus()'s
        # refresh_readiness callback clears it in production, but these
        # tests mock stop_jamulus out entirely, so a prior test's stop()
        # would otherwise leak `stopping=True` into the next test and make
        # apply_participants() silently no-op.
        self.controller._jamulus_connected = False
        self.controller.audio.stopping = False
        self.controller._reset_to_demo_state()

    def test_demo_replaced_then_restored_after_stop_audio(self):
        # Pre-condition: demo participants populated by _reset_to_demo_state.
        demo_ids_before = set(self.controller.participants.keys())
        self.assertTrue(demo_ids_before, "demo participants should exist")
        # Demo names are 'Sample N' / 'You' — verify by sniffing.
        self.assertTrue(
            any(p.name.startswith("Sample") or p.name == "You"
                for p in self.controller.participants.values())
        )

        # Real participants arrive via Jamulus callback.
        real = [
            JamulusParticipant(channel_id=10, name="RealAlice", is_local=True),
            JamulusParticipant(channel_id=11, name="RealBob"),
        ]
        self.controller._apply_jamulus_participants(real)

        # Demo cleared; only real channels remain.
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

        # Demo participants restored.
        self.assertFalse(self.controller._jamulus_connected)
        names = {p.name for p in self.controller.participants.values()}
        self.assertIn("You", names)
        # At least one of the demo "Sample N" entries should be present.
        self.assertTrue(
            any(n.startswith("Sample") for n in names),
            f"expected Sample-N demo participants, got {names!r}",
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

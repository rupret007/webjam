"""
Verifies the participant-card → grid → controller → Jamulus signal chain:
moving a card's fader must end with ``JamulusController.set_fader_level``
being called with the right channel_id and level.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestCardFaderSignalWiring(unittest.TestCase):
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

    def test_card_fader_signal_reaches_jamulus_set_fader_level(self):
        # Replace the JamulusController with a MagicMock so we can assert calls
        # without actually talking to RPC/UDP.
        fake_jamulus = MagicMock()
        self.controller.jamulus = fake_jamulus
        self.controller._jamulus_connected = True

        # Push a real participant so a card exists in the grid for ch 7.
        self.controller._apply_jamulus_participants(
            [JamulusParticipant(channel_id=7, name="Tester", is_local=False)]
        )

        cards = self.window.participant_grid._cards
        self.assertIn(7, cards, f"card for ch 7 missing; cards: {list(cards)}")
        card = cards[7]

        # Move the fader — fires valueChanged → fader_changed → grid →
        # ApplicationController._on_fader_changed → jamulus.set_fader_level.
        card._fader.setValue(80)
        _app.processEvents()

        fake_jamulus.set_fader_level.assert_called_with(7, 80)
        # Also verify the controller's local presentation was updated.
        self.assertEqual(self.controller.participants[7].fader_level, 80)

    def test_card_fader_does_not_call_jamulus_when_disconnected(self):
        fake_jamulus = MagicMock()
        self.controller.jamulus = fake_jamulus
        self.controller._jamulus_connected = False

        # Use a demo card from the existing _reset_to_demo_state grid.
        self.controller._reset_to_demo_state()
        cards = self.window.participant_grid._cards
        any_id = next(iter(cards))
        cards[any_id]._fader.setValue(50)
        _app.processEvents()

        # When disconnected, we should not push fader changes to jamulus.
        fake_jamulus.set_fader_level.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Tests for the global-tick optimization on LevelMeter.

LevelMeter used to own a per-instance QTimer at 25 Hz, which became 20+
timers in a busy session.  ApplicationController now drives all meters
from a single timer; LevelMeter exposes ``tick_decay()`` and accepts an
``external_tick`` flag so it doesn't spin up its own timer.

These tests verify:
  * external_tick=True (the default) creates no QTimer
  * external_tick=False preserves the legacy self-driving behaviour
  * tick_decay() reduces the level value
  * one driver can fan out to many meters
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

skip_no_pyside6 = unittest.skipUnless(
    __import__("importlib").util.find_spec("PySide6") is not None,
    "PySide6 not installed",
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


@skip_no_pyside6
class TestLevelMeterExternalTick(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_default_creates_no_timer(self):
        """external_tick defaults to True — no per-instance QTimer."""
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        self.assertIsNone(m._decay_timer)
        self.assertTrue(m._external_tick)

    def test_explicit_external_tick_true_creates_no_timer(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter(external_tick=True)
        self.assertIsNone(m._decay_timer)

    def test_external_tick_false_preserves_legacy_timer(self):
        """Backwards-compat: explicit flag restores the self-driving timer."""
        from PySide6.QtCore import QTimer
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter(external_tick=False)
        self.assertIsInstance(m._decay_timer, QTimer)
        self.assertTrue(m._decay_timer.isActive())
        self.assertEqual(m._decay_timer.interval(), LevelMeter.TICK_MS)

    def test_tick_decay_reduces_level(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()  # external_tick=True
        m.set_level(0.7)
        before = m._level
        m.tick_decay()
        self.assertLess(m._level, before)
        self.assertAlmostEqual(m._level, before - LevelMeter.DECAY_PER_TICK, places=6)

    def test_tick_decay_stops_at_zero(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(0.01)  # below DECAY_PER_TICK
        m.tick_decay()
        self.assertEqual(m._level, 0.0)
        # Idempotent at zero — no underflow
        m.tick_decay()
        self.assertEqual(m._level, 0.0)

    def test_one_driver_fans_out_to_many_meters(self):
        """Simulate the ApplicationController -> ParticipantGrid path:
        a single caller invokes tick_decay() on N meters."""
        from webjam_qt.widgets.level_meter import LevelMeter

        meters = [LevelMeter() for _ in range(5)]
        # Confirm none of them spun up their own timer
        for m in meters:
            self.assertIsNone(m._decay_timer)

        # Seed each meter with a different level
        levels = [0.9, 0.5, 0.3, 0.6, 0.8]
        for m, lv in zip(meters, levels):
            m.set_level(lv)

        # Single driver tick — emulates ParticipantGrid.tick_all_meters
        for m in meters:
            m.tick_decay()

        for m, original in zip(meters, levels):
            self.assertAlmostEqual(
                m._level, original - LevelMeter.DECAY_PER_TICK, places=6
            )


@skip_no_pyside6
class TestParticipantGridTickAllMeters(unittest.TestCase):
    """End-to-end: ParticipantGrid.tick_all_meters drives every card."""

    def setUp(self):
        _qapp()

    def test_grid_tick_drives_every_card(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        from webjam_qt.widgets.participant_grid import ParticipantGrid

        grid = ParticipantGrid()
        grid.set_participants([
            ParticipantPresentation(channel_id=i, name=f"P{i}")
            for i in range(5)
        ])
        # Seed each card's meter
        for cid in range(5):
            grid.update_level(cid, 0.6)

        # No card should have spun up its own timer
        for card in grid.cards():
            self.assertIsNone(card._level_meter._decay_timer)

        before = [card._level_meter._level for card in grid.cards()]
        grid.tick_all_meters()
        after = [card._level_meter._level for card in grid.cards()]

        for b, a in zip(before, after):
            self.assertAlmostEqual(a, b - LevelMeter.DECAY_PER_TICK, places=6)


if __name__ == "__main__":
    unittest.main()

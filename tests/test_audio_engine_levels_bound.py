"""
RealAudioEngine._levels must never grow unbounded. set_level_override
caps the per-channel dict at _MAX_LEVEL_ENTRIES and clear_level_overrides
empties it.
"""
from __future__ import annotations

import unittest

from core.audio_engine import RealAudioEngine, _MAX_LEVEL_ENTRIES
from core.settings import AppSettings


class TestAudioEngineLevelBound(unittest.TestCase):
    def test_set_level_override_caps_dict_at_max_entries(self):
        engine = RealAudioEngine(AppSettings())
        # Push 2000 distinct channel ids — far above the cap.
        for i in range(2000):
            engine.set_level_override(i, 0.5)
        self.assertLessEqual(len(engine._levels), _MAX_LEVEL_ENTRIES)

        # clear_level_overrides empties the dict.
        engine.clear_level_overrides()
        self.assertEqual(engine._levels, {})

        # Re-populating after clear still respects the cap.
        for i in range(_MAX_LEVEL_ENTRIES + 50):
            engine.set_level_override(i, 0.25)
        self.assertLessEqual(len(engine._levels), _MAX_LEVEL_ENTRIES)


if __name__ == "__main__":
    unittest.main()

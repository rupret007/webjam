"""Coverage for JamulusController.save_mix/load_mix and JamulusAudioMonitor."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jamulus_controller import JamulusController, JamulusAudioMonitor


class TestSaveLoadMix(unittest.TestCase):
    def _controller(self):
        c = JamulusController.__new__(JamulusController)  # bypass heavy init
        c.__dict__["_state"] = mock.MagicMock()  # _state is a read-only property
        c.logger = mock.MagicMock()
        return c

    def test_save_then_load_roundtrip(self):
        c = self._controller()
        payload = {"participants": [{"channel_id": 0, "fader_level": 100}]}
        c._state.serialize_mix.return_value = payload
        c._state.apply_mix_data.return_value = 1
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mix.json")
            c.save_mix(path)
            on_disk = json.loads(Path(path).read_text())
            self.assertEqual(on_disk, payload)
            result = c.load_mix(path)
        self.assertEqual(result, 1)
        c._state.apply_mix_data.assert_called_once_with(payload)

    def test_save_mix_creates_parent_dirs(self):
        c = self._controller()
        c._state.serialize_mix.return_value = {"participants": []}
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "nested" / "deep" / "mix.json")
            c.save_mix(path)
            self.assertTrue(Path(path).is_file())

    def test_load_missing_file_returns_none(self):
        c = self._controller()
        self.assertIsNone(c.load_mix("/no/such/mix.json"))
        c._state.apply_mix_data.assert_not_called()

    def test_load_corrupt_file_returns_none(self):
        c = self._controller()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            path.write_text("{not json")
            self.assertIsNone(c.load_mix(str(path)))


class _FakeCtl:
    def __init__(self, level=0.7):
        self.logger = mock.MagicMock()
        self.participants = {
            0: SimpleNamespace(muted=False),
            1: SimpleNamespace(muted=True),
        }
        self._participants_lock = threading.RLock()
        self.audio_engine = mock.MagicMock()
        self.audio_engine.get_level.return_value = level


class TestAudioMonitor(unittest.TestCase):
    def test_get_level_default_zero(self):
        mon = JamulusAudioMonitor(_FakeCtl())
        self.assertEqual(mon.get_level(99), 0.0)

    def test_monitor_computes_levels_muted_zero(self):
        mon = JamulusAudioMonitor(_FakeCtl(level=0.6))
        mon.start()
        try:
            # let the monitor thread run a few iterations (50ms loop)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and mon.get_level(0) == 0.0:
                time.sleep(0.02)
        finally:
            mon.stop()
        self.assertAlmostEqual(mon.get_level(0), 0.6, places=3)  # unmuted
        self.assertEqual(mon.get_level(1), 0.0)                  # muted
        self.assertFalse(mon.running)

    def test_start_is_idempotent(self):
        mon = JamulusAudioMonitor(_FakeCtl())
        mon.start()
        try:
            t = mon.monitor_thread
            mon.start()  # second start must not spawn a new thread
            self.assertIs(mon.monitor_thread, t)
        finally:
            mon.stop()


if __name__ == "__main__":
    unittest.main()

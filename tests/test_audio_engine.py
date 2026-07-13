"""Tests for RealAudioEngine: level bookkeeping, LRU cap, device resolution,
and the synthetic fallback — all without real audio hardware."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

import core.audio_engine as ae
from core.audio_engine import RealAudioEngine, _MAX_LEVEL_ENTRIES


def _settings(device_index=-1, webex_audio_mode="talkback"):
    return SimpleNamespace(
        audio_samplerate=48000, audio_blocksize=0,
        audio_latency="low", audio_input_device_index=device_index,
        webex_audio_mode=webex_audio_mode,
    )


class TestLevels(unittest.TestCase):
    def test_get_level_default_zero(self):
        eng = RealAudioEngine(_settings())
        self.assertEqual(eng.get_level(5), 0.0)

    def test_override_and_get(self):
        eng = RealAudioEngine(_settings())
        eng.set_level_override(2, 0.5)
        self.assertEqual(eng.get_level(2), 0.5)

    def test_override_clamped(self):
        eng = RealAudioEngine(_settings())
        eng.set_level_override(0, 9.0)
        eng.set_level_override(1, -3.0)
        self.assertEqual(eng.get_level(0), 1.0)
        self.assertEqual(eng.get_level(1), 0.0)

    def test_unknown_channel_falls_back_to_global_mix(self):
        eng = RealAudioEngine(_settings())
        eng.set_level_override(-1, 0.3)   # global mix
        self.assertEqual(eng.get_level(42), 0.0)  # never clone local input to a remote
        self.assertEqual(eng.get_level(-1), 0.3)

    def test_clear_overrides(self):
        eng = RealAudioEngine(_settings())
        eng.set_level_override(1, 0.8)
        eng.clear_level_overrides()
        self.assertEqual(eng.get_level(1), 0.0)

    def test_level_cache_is_bounded(self):
        eng = RealAudioEngine(_settings())
        for i in range(_MAX_LEVEL_ENTRIES + 50):
            eng.set_level_override(i, 0.5)
        self.assertLessEqual(len(eng._levels), _MAX_LEVEL_ENTRIES)


class TestResolveDevice(unittest.TestCase):
    def test_explicit_override_wins(self):
        eng = RealAudioEngine(_settings(), device_index=5)
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = {"name": "USB Interface"}
        with mock.patch.object(ae, "sd", fake_sd):
            self.assertEqual(eng._resolve_device(), 5)
        self.assertEqual(eng.diagnostics().input_device, "USB Interface")

    def test_configured_setting_used(self):
        eng = RealAudioEngine(_settings(device_index=3))
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = {"name": "Configured Iface"}
        with mock.patch.object(ae, "sd", fake_sd):
            self.assertEqual(eng._resolve_device(), 3)
        self.assertEqual(eng.diagnostics().input_device, "Configured Iface")

    def test_auto_detect_loopback(self):
        eng = RealAudioEngine(
            _settings(device_index=-1, webex_audio_mode="audience_bridge")
        )
        fake_sd = mock.MagicMock()
        status = SimpleNamespace(
            ok=True,
            loopback_device=SimpleNamespace(has_input=True, index=7, name="BlackHole"),
        )
        with mock.patch.object(ae, "sd", fake_sd), \
             mock.patch("core.audio_routing.scan_loopback_devices", return_value=status):
            self.assertEqual(eng._resolve_device(), 7)
        self.assertEqual(eng.diagnostics().input_device, "BlackHole")

    def test_falls_back_to_system_default(self):
        eng = RealAudioEngine(_settings(device_index=3))
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.side_effect = RuntimeError("no such device")
        bad = SimpleNamespace(ok=False, loopback_device=None)
        with mock.patch.object(ae, "sd", fake_sd), \
             mock.patch("core.audio_routing.scan_loopback_devices", return_value=bad):
            self.assertIsNone(eng._resolve_device())
        self.assertEqual(eng.diagnostics().input_device, "system default")


class TestUnavailableMeterFallback(unittest.TestCase):
    def test_missing_sounddevice_never_fabricates_meter_activity(self):
        with mock.patch.object(ae, "sd", None):
            eng = RealAudioEngine(_settings())
            eng.start()
            try:
                self.assertEqual(eng.get_level(-1), 0.0)
            finally:
                eng.stop()
        self.assertEqual(eng.diagnostics().backend, "unavailable")
        self.assertFalse(eng.diagnostics().active)
        self.assertFalse(eng.running)
        self.assertEqual(eng.get_level(-1), 0.0)


if __name__ == "__main__":
    unittest.main()

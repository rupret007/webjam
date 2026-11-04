"""Preserve musician-owned profile state during optional latency tuning."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from core.jamulus_adaptive_latency import apply_frame_factor_to_profile
from services.adaptive_latency_controller import AdaptiveLatencyController


class AdaptiveProfileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.profile = Path(self.directory.name) / "profile.ini"

    def controller(self, **overrides):
        values = dict(adaptive_latency_enabled=True,
                      adaptive_latency_player_override=False, audio_latency="low")
        values.update(overrides)
        return AdaptiveLatencyController(SimpleNamespace(
            settings=SimpleNamespace(**values),
            native_profile_plan=lambda: SimpleNamespace(profile_path=self.profile),
        ))

    def test_disabled_or_player_override_never_writes_profile(self):
        for settings in [dict(adaptive_latency_enabled=False),
                         dict(adaptive_latency_player_override=True)]:
            with self.subTest(settings=settings):
                data = b"<client><prefsndcrdbufidx>4</prefsndcrdbufidx></client>"
                self.profile.write_bytes(data)
                self.assertEqual(self.controller(**settings).apply_launch_defaults(self.profile), [])
                self.assertEqual(self.profile.read_bytes(), data)
                self.assertEqual(list(Path(self.directory.name).glob("*.json")), [])

    def test_delay_uses_preserved_musician_frame_factor(self):
        self.profile.write_text("<client><prefsndcrdbufidx>4</prefsndcrdbufidx></client>")
        controller = self.controller()
        controller.apply_launch_defaults(self.profile)
        controller.note_local_channel_id(7)
        controller.note_server_ping(20)
        self.assertAlmostEqual(controller.participant_delays()[7].overall_delay_ms,
                               20 + 256 / 48)

    def test_unrecognized_profile_is_never_replaced(self):
        data = b"<future-profile><audio-device>keep</audio-device></future-profile>"
        self.profile.write_bytes(data)
        self.assertEqual(self.controller().apply_launch_defaults(self.profile), [])
        self.assertEqual(self.profile.read_bytes(), data)

    def test_musician_factor_without_provenance_cannot_be_overwritten(self):
        data = b"<client><prefsndcrdbufidx>4</prefsndcrdbufidx></client>"
        self.profile.write_bytes(data)
        self.assertFalse(apply_frame_factor_to_profile(self.profile, 1))
        self.assertEqual(self.profile.read_bytes(), data)

    def test_future_profile_recommendation_is_not_presented_as_live_buffer(self):
        self.profile.write_text("<client></client>")
        controller = self.controller()
        controller.apply_launch_defaults(self.profile)
        controller.note_local_channel_id(7)
        for _ in range(3):
            controller.note_server_ping(40)
        self.assertIn("<prefsndcrdbufidx>2</prefsndcrdbufidx>", self.profile.read_text())
        self.assertAlmostEqual(controller.participant_delays()[7].overall_delay_ms,
                               40 + 64 / 48)

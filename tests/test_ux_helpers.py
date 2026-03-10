from __future__ import annotations

import math
import unittest

from ui.ux_status import classify_latency_ms, readiness_state, connection_summary
from ui.accessibility import clamp_scale, scaled_font_size, contrast_palette


class TestClassifyLatency(unittest.TestCase):
    def test_zero_ms_is_good(self):
        label, color = classify_latency_ms(0)
        self.assertIn("Good", label)

    def test_20ms_is_good(self):
        label, _ = classify_latency_ms(20)
        self.assertIn("Good", label)

    def test_29ms_is_good(self):
        label, _ = classify_latency_ms(29)
        self.assertIn("Good", label)

    def test_30ms_is_fair(self):
        label, _ = classify_latency_ms(30)
        self.assertIn("Fair", label)

    def test_50ms_is_fair(self):
        label, _ = classify_latency_ms(50)
        self.assertIn("Fair", label)

    def test_69ms_is_fair(self):
        label, _ = classify_latency_ms(69)
        self.assertIn("Fair", label)

    def test_70ms_is_poor(self):
        label, _ = classify_latency_ms(70)
        self.assertIn("Poor", label)

    def test_100ms_is_poor(self):
        label, _ = classify_latency_ms(100)
        self.assertIn("Poor", label)

    def test_negative_ms_is_good(self):
        label, _ = classify_latency_ms(-5)
        self.assertIn("Good", label)

    def test_none_ms_shows_na(self):
        label, _ = classify_latency_ms(None)
        self.assertIn("n/a", label)

    def test_returns_color_string(self):
        _, color = classify_latency_ms(50)
        self.assertTrue(color.startswith("#"))


class TestReadinessState(unittest.TestCase):
    def test_zero_participants_waiting(self):
        label, color = readiness_state(0)
        self.assertIn("waiting", label.lower())

    def test_one_participant_ready(self):
        label, color = readiness_state(1)
        self.assertIn("ready", label.lower())

    def test_many_participants_ready(self):
        label, _ = readiness_state(10)
        self.assertIn("ready", label.lower())


class TestConnectionSummary(unittest.TestCase):
    def test_produces_string(self):
        result = connection_summary("connected", "idle")
        self.assertIsInstance(result, str)
        self.assertIn("connected", result)
        self.assertIn("idle", result)


class TestClampScale(unittest.TestCase):
    def test_within_bounds(self):
        self.assertEqual(clamp_scale(1.0), 1.0)

    def test_below_min_clamped(self):
        self.assertEqual(clamp_scale(0.5), 0.8)

    def test_above_max_clamped(self):
        self.assertEqual(clamp_scale(2.0), 1.6)

    def test_at_min_boundary(self):
        self.assertEqual(clamp_scale(0.8), 0.8)

    def test_at_max_boundary(self):
        self.assertEqual(clamp_scale(1.6), 1.6)

    def test_nan_returns_default(self):
        self.assertEqual(clamp_scale(float("nan")), 1.0)

    def test_infinite_returns_default(self):
        self.assertEqual(clamp_scale(math.inf), 1.0)

    def test_non_numeric_returns_default(self):
        self.assertEqual(clamp_scale("not-a-number"), 1.0)


class TestScaledFontSize(unittest.TestCase):
    def test_default_scale(self):
        self.assertEqual(scaled_font_size(12, 1.0), 12)

    def test_scaled_up(self):
        self.assertEqual(scaled_font_size(10, 1.5), 15)

    def test_minimum_8(self):
        self.assertEqual(scaled_font_size(4, 1.0), 8)

    def test_very_small_scale_floors_at_8(self):
        result = scaled_font_size(10, 0.1)
        self.assertEqual(result, 8)


class TestContrastPalette(unittest.TestCase):
    def test_enabled_returns_high_contrast(self):
        palette = contrast_palette(True)
        self.assertEqual(palette["bg"], "#000000")
        self.assertEqual(palette["fg"], "#ffffff")

    def test_disabled_returns_normal(self):
        palette = contrast_palette(False)
        self.assertEqual(palette["bg"], "#2b2b2b")
        self.assertEqual(palette["fg"], "#ffffff")

    def test_values_are_color_strings(self):
        palette = contrast_palette(True)
        for key, value in palette.items():
            self.assertTrue(value.startswith("#"), f"{key}={value}")

    def test_expected_keys_present(self):
        for enabled in (True, False):
            palette = contrast_palette(enabled)
            for key in ("bg", "fg", "accent", "warn"):
                self.assertIn(key, palette)


if __name__ == "__main__":
    unittest.main()

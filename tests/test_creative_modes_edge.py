"""Edge-case tests for core.creative_modes."""

import unittest

from core.creative_modes import (
    CREATIVE_MODES,
    get_mode_by_key,
    get_mode_by_key_or_default,
    get_mode_by_label,
    get_mode_by_label_or_default,
    get_mode_keys,
    get_mode_labels,
)


class TestGetModeByKey(unittest.TestCase):
    def test_valid_key(self):
        mode = get_mode_by_key("music_jam")
        self.assertIsNotNone(mode)
        self.assertEqual(mode.key, "music_jam")

    def test_missing_key_returns_none(self):
        self.assertIsNone(get_mode_by_key("nonexistent"))

    def test_empty_key_returns_none(self):
        self.assertIsNone(get_mode_by_key(""))


class TestGetModeByKeyOrDefault(unittest.TestCase):
    def test_valid_key(self):
        mode = get_mode_by_key_or_default("writers_room")
        self.assertEqual(mode.key, "writers_room")

    def test_missing_key_returns_first(self):
        mode = get_mode_by_key_or_default("nonexistent")
        self.assertIs(mode, CREATIVE_MODES[0])

    def test_empty_key_returns_first(self):
        self.assertIs(get_mode_by_key_or_default(""), CREATIVE_MODES[0])


class TestGetModeByLabel(unittest.TestCase):
    def test_valid_label(self):
        mode = get_mode_by_label("Music Jam")
        self.assertIsNotNone(mode)
        self.assertEqual(mode.label, "Music Jam")

    def test_missing_label(self):
        self.assertIsNone(get_mode_by_label("No Such Label"))

    def test_case_sensitive(self):
        self.assertIsNone(get_mode_by_label("music jam"))


class TestGetModeByLabelOrDefault(unittest.TestCase):
    def test_valid_label(self):
        mode = get_mode_by_label_or_default("Writer's Room")
        self.assertEqual(mode.key, "writers_room")

    def test_missing_label_returns_first(self):
        mode = get_mode_by_label_or_default("No Match")
        self.assertIs(mode, CREATIVE_MODES[0])


class TestModeCollections(unittest.TestCase):
    def test_all_keys_unique(self):
        keys = get_mode_keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_labels_unique(self):
        labels = get_mode_labels()
        self.assertEqual(len(labels), len(set(labels)))

    def test_keys_match_modes(self):
        keys = get_mode_keys()
        self.assertEqual(keys, [m.key for m in CREATIVE_MODES])

    def test_labels_match_modes(self):
        labels = get_mode_labels()
        self.assertEqual(labels, [m.label for m in CREATIVE_MODES])


class TestCreativeModeDataclass(unittest.TestCase):
    def test_frozen(self):
        mode = CREATIVE_MODES[0]
        with self.assertRaises(AttributeError):
            mode.key = "changed"

    def test_review_prompts_not_empty(self):
        for mode in CREATIVE_MODES:
            self.assertTrue(len(mode.review_prompts) > 0, f"{mode.key} has empty review_prompts")


if __name__ == "__main__":
    unittest.main()

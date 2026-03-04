"""Tests for core.session_templates."""

import unittest

from core.session_templates import (
    SESSION_TEMPLATES,
    SessionTemplate,
    get_all_templates,
    get_template_by_id,
    get_templates_for_mode,
)


class TestGetAllTemplates(unittest.TestCase):
    def test_returns_all(self):
        result = get_all_templates()
        self.assertEqual(len(result), len(SESSION_TEMPLATES))
        self.assertIsInstance(result, list)

    def test_returns_copies(self):
        a = get_all_templates()
        b = get_all_templates()
        self.assertEqual(a, b)
        a.pop()
        self.assertNotEqual(len(a), len(get_all_templates()))


class TestGetTemplatesForMode(unittest.TestCase):
    def test_none_returns_all(self):
        self.assertEqual(get_templates_for_mode(None), get_all_templates())

    def test_empty_string_returns_all(self):
        self.assertEqual(get_templates_for_mode(""), get_all_templates())

    def test_music_jam(self):
        results = get_templates_for_mode("music_jam")
        self.assertTrue(len(results) >= 1)
        for t in results:
            self.assertIn(t.mode_key, (None, "music_jam"))

    def test_visual_studio(self):
        results = get_templates_for_mode("visual_studio")
        self.assertTrue(all(t.mode_key in (None, "visual_studio") for t in results))

    def test_unknown_mode_returns_only_universal(self):
        results = get_templates_for_mode("nonexistent_mode")
        for t in results:
            self.assertIsNone(t.mode_key)


class TestGetTemplateById(unittest.TestCase):
    def test_valid_id(self):
        t = get_template_by_id("band_rehearsal")
        self.assertIsNotNone(t)
        self.assertEqual(t.id, "band_rehearsal")
        self.assertEqual(t.label, "Band Rehearsal")

    def test_all_known_ids(self):
        for template in SESSION_TEMPLATES:
            self.assertIs(get_template_by_id(template.id), template)

    def test_missing_id(self):
        self.assertIsNone(get_template_by_id("does_not_exist"))

    def test_empty_id(self):
        self.assertIsNone(get_template_by_id(""))


class TestSessionTemplateDataclass(unittest.TestCase):
    def test_frozen(self):
        t = SESSION_TEMPLATES[0]
        with self.assertRaises(AttributeError):
            t.id = "changed"

    def test_all_ids_unique(self):
        ids = [t.id for t in SESSION_TEMPLATES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_labels_unique(self):
        labels = [t.label for t in SESSION_TEMPLATES]
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()

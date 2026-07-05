from __future__ import annotations

import unittest

from ui.preferences import UiPreferencesService, _is_valid_geometry


class _RepoStub:
    def __init__(self, data: dict[str, object] | None = None):
        self._data = dict(data or {})

    def get_setting(self, key: str, default=None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self._data[key] = value


class TestGeometryValidationEdge(unittest.TestCase):
    def test_accepts_geometry_with_offset(self):
        self.assertTrue(_is_valid_geometry("1600x900+120+40"))

    def test_rejects_trailing_garbage(self):
        self.assertFalse(_is_valid_geometry("1600x900oops"))

    def test_rejects_non_string_geometry(self):
        self.assertFalse(_is_valid_geometry(1600))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_geometry(None))


class TestUiPreferencesServiceEdge(unittest.TestCase):
    def test_load_tolerates_non_string_flags_and_invalid_geometry(self):
        repo = _RepoStub(
            {
                "ui_font_scale": "1.2",
                "ui_high_contrast": True,
                "ui_auto_setup_on_start": 1,
                "ui_window_geometry": "1600x900INVALID",
            }
        )
        prefs = UiPreferencesService(repo).load()
        self.assertAlmostEqual(prefs.font_scale, 1.2, places=2)
        self.assertTrue(prefs.high_contrast_enabled)
        self.assertTrue(prefs.auto_setup_enabled)
        self.assertEqual(prefs.window_geometry, "1600x900")

    def test_get_window_geometry_handles_non_string_value(self):
        repo = _RepoStub({"ui_window_geometry": 12345})
        geometry = UiPreferencesService(repo).get_window_geometry()
        self.assertEqual(geometry, "1600x900")

    def test_save_window_geometry_rejects_invalid_suffix(self):
        repo = _RepoStub()
        svc = UiPreferencesService(repo)
        svc.save_window_geometry("1600x900BAD")
        self.assertNotIn("ui_window_geometry", repo._data)


if __name__ == "__main__":
    unittest.main()

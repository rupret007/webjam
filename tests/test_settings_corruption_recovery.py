"""
load_settings(...) must never raise on a corrupt config file. It returns an
AppSettings populated with defaults instead, so the app can still start.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from core.settings import AppSettings, load_settings


class TestLoadSettingsCorruption(unittest.TestCase):
    def _write_temp(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            os.close(fd)
            raise
        return path

    def test_load_settings_with_malformed_json_falls_back_to_defaults(self):
        # Truncated JSON — missing closing brace.
        path = self._write_temp('{"jamulus_port": 9999')
        try:
            settings = load_settings(path)
        finally:
            os.remove(path)

        defaults = AppSettings()
        self.assertIsInstance(settings, AppSettings)
        # Missing closing brace -> JSON parse fails -> default port (22124).
        self.assertEqual(settings.jamulus_port, defaults.jamulus_port)

    def test_empty_file_returns_defaults(self):
        path = self._write_temp("")
        try:
            settings = load_settings(path)
        finally:
            os.remove(path)
        self.assertEqual(settings.jamulus_port, AppSettings().jamulus_port)
        self.assertEqual(settings.jamulus_server, AppSettings().jamulus_server)

    def test_null_file_returns_defaults(self):
        path = self._write_temp("null")
        try:
            settings = load_settings(path)
        finally:
            os.remove(path)
        # JSON parses to None — load_settings ignores non-dict payloads.
        self.assertEqual(settings.jamulus_port, AppSettings().jamulus_port)

    def test_array_file_returns_defaults(self):
        path = self._write_temp('[1, 2, 3]')
        try:
            settings = load_settings(path)
        finally:
            os.remove(path)
        # Array isn't a dict — ignored, defaults preserved.
        self.assertEqual(settings.jamulus_port, AppSettings().jamulus_port)
        self.assertEqual(settings.audio_samplerate, AppSettings().audio_samplerate)


if __name__ == "__main__":
    unittest.main()

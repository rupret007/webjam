from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.settings import AppSettings, load_settings, _coerce_settings_data


class TestSettingsDefaults(unittest.TestCase):
    def test_default_values(self):
        s = AppSettings()
        self.assertEqual(s.jamulus_port, 22124)
        self.assertEqual(s.audio_samplerate, 48000)
        self.assertEqual(s.audio_blocksize, 0)
        self.assertFalse(s.enable_sentry)

    def test_load_nonexistent_file_returns_defaults(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 22124)


class TestSettingsMalformedJson(unittest.TestCase):
    def test_invalid_json_falls_back_to_defaults(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("NOT VALID JSON {{{")
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 22124)
        finally:
            os.remove(path)

    def test_json_with_extra_keys_ignored(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": 9999, "unknown_key": "whatever"}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 9999)
        finally:
            os.remove(path)

    def test_json_with_wrong_types_coerced(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": "12345", "enable_sentry": "yes"}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 12345)
            self.assertTrue(s.enable_sentry)
        finally:
            os.remove(path)


class TestSettingsBoundaryPorts(unittest.TestCase):
    def test_port_zero_resets_to_default(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": 0}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 22124)
        finally:
            os.remove(path)

    def test_port_65536_resets_to_default(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": 65536}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 22124)
        finally:
            os.remove(path)

    def test_port_1_accepted(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": 1}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 1)
        finally:
            os.remove(path)

    def test_port_65535_accepted(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_port": 65535}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_port, 65535)
        finally:
            os.remove(path)

    def test_negative_blocksize_reset(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"audio_blocksize": -1}, f)
            s = load_settings(path)
            self.assertEqual(s.audio_blocksize, 0)
        finally:
            os.remove(path)

    def test_zero_samplerate_reset(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"audio_samplerate": 0}, f)
            s = load_settings(path)
            self.assertEqual(s.audio_samplerate, 48000)
        finally:
            os.remove(path)


class TestSettingsEnvOverrides(unittest.TestCase):
    @patch.dict(os.environ, {"WEBJAM_JAMULUS_PORT": "5555"})
    def test_env_port_override(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 5555)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_PORT": "not_a_number"})
    def test_env_invalid_port_ignored(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 22124)

    @patch.dict(os.environ, {"WEBJAM_ENABLE_SENTRY": "true"})
    def test_env_sentry_bool(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertTrue(s.enable_sentry)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_CANDIDATES": "/usr/bin/jamulus;/opt/jamulus"})
    def test_env_candidates_split(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_candidates, ["/usr/bin/jamulus", "/opt/jamulus"])


class TestCoerceSettingsData(unittest.TestCase):
    def test_none_port_uses_default(self):
        data = {"jamulus_port": None}
        _coerce_settings_data(data)
        self.assertEqual(data["jamulus_port"], 22124)

    def test_string_port_coerced(self):
        data = {"jamulus_port": "8080"}
        _coerce_settings_data(data)
        self.assertEqual(data["jamulus_port"], 8080)

    def test_invalid_string_port_uses_default(self):
        data = {"jamulus_port": "abc"}
        _coerce_settings_data(data)
        self.assertEqual(data["jamulus_port"], 22124)

    def test_non_string_field_coerced(self):
        data = {"jamulus_server": 12345}
        _coerce_settings_data(data)
        self.assertEqual(data["jamulus_server"], "12345")


if __name__ == "__main__":
    unittest.main()

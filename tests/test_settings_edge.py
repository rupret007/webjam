from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import core.settings as settings_module
from core.settings import (
    AppSettings,
    _coerce_settings_data,
    load_settings,
    save_settings,
)


class TestSettingsDefaults(unittest.TestCase):
    def test_default_values(self):
        s = AppSettings()
        self.assertEqual(s.jamulus_port, 22124)
        self.assertEqual(s.audio_samplerate, 48000)
        self.assertEqual(s.audio_blocksize, 0)
        self.assertFalse(s.enable_sentry)
        self.assertFalse(s.companion_api_enabled)
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertFalse(s.local_capture_enabled)
        self.assertFalse(s.webex_audio_bridge_enabled)
        self.assertFalse(hasattr(s, "webex_config_file"))
        self.assertEqual(s.take_playback_output_device, "")

    def test_macos_client_rpc_secret_lives_in_jamulus_sandbox(self):
        from core.settings import jamulus_client_rpc_secret_path
        with patch("core.settings.sys.platform", "darwin"), patch(
            "core.settings.Path.home", return_value=Path("/Users/tester")
        ):
            path = jamulus_client_rpc_secret_path()
        self.assertEqual(
            path,
            Path("/Users/tester/Library/Containers/"
                 "app.jamulussoftware.Jamulus/Data/Documents/"
                 "webjam_client_rpc.secret"),
        )

    def test_frozen_macos_runtime_paths_are_owned_by_webjam(self):
        from core.settings import (
            hosted_server_recordings_dir,
            hosted_server_secret_path,
            jamulus_client_rpc_secret_path,
        )
        with patch("core.settings.sys.platform", "darwin"), patch.object(
            settings_module.sys, "frozen", True, create=True,
        ), patch("core.settings.Path.home", return_value=Path("/Users/tester")):
            client_secret = jamulus_client_rpc_secret_path()
            server_secret = hosted_server_secret_path()
            recordings = hosted_server_recordings_dir()
        support = Path("/Users/tester/Library/Application Support/WebJam")
        self.assertEqual(
            client_secret,
            support / "JamulusClient" / "webjam_client_rpc.secret",
        )
        self.assertEqual(
            server_secret,
            support / "JamulusServer" / "webjam_server_rpc.secret",
        )
        self.assertEqual(recordings, support / "JamulusServer" / "Recordings")

    def test_non_macos_client_rpc_secret_keeps_legacy_location(self):
        from core.settings import jamulus_client_rpc_secret_path
        with patch("core.settings.sys.platform", "linux"), patch(
            "core.settings.Path.home", return_value=Path("/home/tester")
        ):
            path = jamulus_client_rpc_secret_path()
        self.assertEqual(path, Path("/home/tester/.webjam_jsonrpc_secret"))

    def test_load_nonexistent_file_returns_defaults(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 22124)

    def test_explicit_recording_preferences_survive_simple_mode_migration(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            original = AppSettings(
                config_file=path,
                webex_audio_mode="audience_bridge",
                local_capture_enabled=True,
                take_playback_output_device="SSL 2+",
            )
            save_settings(original)
            loaded = load_settings(path)
        self.assertFalse(loaded.webex_audio_bridge_enabled)
        self.assertEqual(loaded.webex_audio_mode, "talkback")
        self.assertTrue(loaded.local_capture_enabled)
        self.assertEqual(loaded.take_playback_output_device, "SSL 2+")

    def test_save_omits_legacy_bridge_field(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            save_settings(AppSettings(config_file=path, webex_audio_mode="audience_bridge"))
            saved = json.loads(open(path, encoding="utf-8").read())
        self.assertNotIn("webex_audio_bridge_enabled", saved)
        self.assertNotIn("webex_audio_mode", saved)

    def test_legacy_bridge_true_migrates_to_simple_topology(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as config:
            json.dump({"webex_audio_bridge_enabled": True}, config)
            config.flush()
            loaded = load_settings(config.name)
        self.assertEqual(loaded.webex_audio_mode, "talkback")
        self.assertFalse(loaded.local_capture_enabled)

    def test_legacy_bridge_false_migrates_to_simple_topology(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as config:
            json.dump({"webex_audio_bridge_enabled": False}, config)
            config.flush()
            loaded = load_settings(config.name)
        self.assertEqual(loaded.webex_audio_mode, "talkback")
        self.assertFalse(loaded.local_capture_enabled)

    def test_new_fields_take_precedence_over_legacy_file_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as config:
            json.dump({
                "webex_audio_bridge_enabled": True,
                "webex_audio_mode": "talkback",
                "local_capture_enabled": False,
            }, config)
            config.flush()
            loaded = load_settings(config.name)
        self.assertEqual(loaded.webex_audio_mode, "talkback")
        self.assertFalse(loaded.local_capture_enabled)

    def test_legacy_guest_fields_are_dropped_and_display_name_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.json")
            with open(path, "w", encoding="utf-8") as config:
                json.dump({
                    "config_file": path,
                    "webex_guest_issuer_id": "legacy-id",
                    "webex_guest_issuer_secret": "legacy-secret",
                    "webex_display_name": "Jeff",
                    "webex_config_file": "/legacy/private/webex-config.json",
                }, config)
            loaded = load_settings(path)
            self.assertEqual(loaded.musician_name, "Jeff")
            self.assertFalse(hasattr(loaded, "webex_guest_issuer_id"))
            self.assertFalse(hasattr(loaded, "webex_guest_issuer_secret"))
            self.assertFalse(hasattr(loaded, "webex_display_name"))
            self.assertFalse(hasattr(loaded, "webex_config_file"))
            save_settings(loaded)
            saved = json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(saved["musician_name"], "Jeff")
        self.assertNotIn("webex_guest_issuer_id", saved)
        self.assertNotIn("webex_guest_issuer_secret", saved)
        self.assertNotIn("webex_display_name", saved)
        self.assertNotIn("webex_config_file", saved)


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

    def test_jamulus_rpc_port_out_of_range_resets_to_default(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"jamulus_rpc_port": 999999}, f)
            s = load_settings(path)
            self.assertEqual(s.jamulus_rpc_port, 22222)
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
    def test_default_candidates_include_official_lowercase_linux_binary(self):
        self.assertIn("/usr/bin/jamulus", AppSettings().jamulus_candidates)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_PORT": "5555"})
    def test_env_port_override(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 5555)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_PORT": "not_a_number"})
    def test_env_invalid_port_ignored(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_port, 22124)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_RPC_PORT": "33333"})
    def test_env_rpc_port_override(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_rpc_port, 33333)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_RPC_PORT": "999999"})
    def test_env_rpc_port_out_of_range_ignored(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_rpc_port, 22222)

    @patch.dict(os.environ, {"WEBJAM_ENABLE_SENTRY": "true"})
    def test_env_sentry_bool(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertTrue(s.enable_sentry)

    @patch.dict(os.environ, {"WEBJAM_JAMULUS_CANDIDATES": "/usr/bin/jamulus;/opt/jamulus"})
    def test_env_candidates_split(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.jamulus_candidates, ["/usr/bin/jamulus", "/opt/jamulus"])

    @patch.dict(os.environ, {
        "WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED": "true",
    })
    def test_legacy_bridge_environment_is_not_a_user_facing_backdoor(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertFalse(s.local_capture_enabled)

    @patch.dict(os.environ, {
        "WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED": "true",
        "WEBJAM_WEBEX_AUDIO_MODE": "talkback",
    })
    def test_new_mode_environment_overrides_legacy_environment(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertFalse(s.local_capture_enabled)

    @patch.dict(os.environ, {
        "WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED": "true",
        "WEBJAM_WEBEX_AUDIO_MODE": "talkbak",
    })
    def test_invalid_new_mode_environment_does_not_fall_through_to_legacy(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertFalse(s.local_capture_enabled)

    @patch.dict(os.environ, {
        "WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED": "true",
        "WEBJAM_LOCAL_CAPTURE_ENABLED": "false",
    })
    def test_explicit_local_capture_environment_does_not_restore_legacy_bridge(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertFalse(s.local_capture_enabled)

    @patch.dict(os.environ, {
        "WEBJAM_LOCAL_CAPTURE_ENABLED": "true",
    })
    def test_explicit_local_capture_environment_is_preserved(self):
        s = load_settings("/tmp/nonexistent_webjam_config_test.json")
        self.assertEqual(s.webex_audio_mode, "talkback")
        self.assertTrue(s.local_capture_enabled)


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

    def test_invalid_audio_mode_falls_back_to_talkback(self):
        data = {"webex_audio_mode": "not-a-mode"}
        _coerce_settings_data(data)
        self.assertEqual(data["webex_audio_mode"], "talkback")


if __name__ == "__main__":
    unittest.main()


class TestFreshInstallDefaultsAreBlank(unittest.TestCase):
    """The old defaults (private LAN IP 172.24.194.9 + a sandbox Webex link)
    were dead for anyone but the original dev box.  Fresh installs must start
    unconfigured so the launch gate and F2 Band Check drive the user to a real
    band address while leaving the optional Webex companion blank."""

    def test_jamulus_server_default_is_empty(self):
        from core.settings import AppSettings
        self.assertEqual(AppSettings().jamulus_server, "")

    def test_webex_url_default_is_empty(self):
        from core.settings import AppSettings
        self.assertEqual(AppSettings().webex_url, "")

    def test_band_check_flags_server_but_not_optional_webex(self):
        from core.preflight import run_ready_check
        from core.settings import AppSettings
        report = run_ready_check(AppSettings(jamulus_candidates=[]))
        failed = {item.name for item in report.items if not item.ok}
        self.assertIn("Jamulus server set", failed)
        self.assertNotIn("Webex companion", failed)

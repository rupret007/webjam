from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from webex_integration import (
    WebexConfig,
    WebexController,
    WebexParticipant,
    WebexParticipantSync,
    open_webex_meeting,
)


class _ControllerStub:
    def __init__(self, participants):
        self._participants = list(participants)

    def get_participants(self):
        return list(self._participants)


class TestWebexUtilityEdge(unittest.TestCase):
    def test_open_webex_meeting_returns_false_when_browser_refuses(self):
        with patch("webex_integration.webbrowser.open", return_value=False):
            self.assertFalse(open_webex_meeting("https://example.com/meet"))

    def test_open_webex_meeting_returns_false_on_exception(self):
        with patch("webex_integration.webbrowser.open", side_effect=RuntimeError("boom")):
            self.assertFalse(open_webex_meeting("https://example.com/meet"))

    def test_join_meeting_opens_browser_without_marking_connected(self):
        controller = WebexController("https://example.webex.com/meet/test")
        with patch("webex_integration.webbrowser.open", return_value=True):
            self.assertTrue(controller.join_meeting())
        self.assertTrue(controller.browser_opened)
        self.assertFalse(controller.is_connected)


class TestWebexConfigEdge(unittest.TestCase):
    def test_partial_config_preserves_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config_path = home / ".webjam_webex_config.json"
            config_path.write_text(json.dumps({"auto_join": True}), encoding="utf-8")
            with patch("webex_integration.Path.home", return_value=home):
                config = WebexConfig()
            self.assertTrue(config.get("auto_join"))
            self.assertIn("default_meeting_url", config.config)
            self.assertIn("embedded_mode", config.config)
            self.assertTrue(config.get("sync_with_jamulus"))

    def test_save_config_handles_non_serializable_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch("webex_integration.Path.home", return_value=home):
                config = WebexConfig()
            config.config["bad_value"] = {"unsupported": {1, 2, 3}}
            self.assertFalse(config.save_config())

    def test_set_rolls_back_in_memory_when_save_fails(self):
        config = WebexConfig.__new__(WebexConfig)
        config.config = {"default_meeting_url": "https://old.example"}
        config.save_config = lambda: False

        result = WebexConfig.set(config, "default_meeting_url", "https://new.example")

        self.assertFalse(result)
        self.assertEqual(config.get("default_meeting_url"), "https://old.example")

    def test_set_returns_true_when_save_succeeds(self):
        config = WebexConfig.__new__(WebexConfig)
        config.config = {"default_meeting_url": "https://old.example"}
        config.save_config = lambda: True

        result = WebexConfig.set(config, "default_meeting_url", "https://new.example")

        self.assertTrue(result)
        self.assertEqual(config.get("default_meeting_url"), "https://new.example")


class TestWebexParticipantSyncEdge(unittest.TestCase):
    def test_sync_rebuilds_map_and_removes_stale_entries(self):
        webex = _ControllerStub([WebexParticipant(id="u1", name="Alice")])
        jamulus = _ControllerStub([SimpleNamespace(channel_id=7, name="Alice")])
        syncer = WebexParticipantSync(webex, jamulus)

        first = syncer.sync_participants()
        self.assertEqual(first, {"u1": "7"})

        webex._participants = [WebexParticipant(id="u2", name="Bob")]
        second = syncer.sync_participants()
        self.assertEqual(second, {})
        self.assertIsNone(syncer.get_jamulus_id("u1"))

    def test_sync_enforces_one_to_one_channel_mapping(self):
        webex = _ControllerStub(
            [
                WebexParticipant(id="u1", name="Chris"),
                WebexParticipant(id="u2", name="Chris A."),
            ]
        )
        jamulus = _ControllerStub([SimpleNamespace(channel_id=2, name="Chris")])
        syncer = WebexParticipantSync(webex, jamulus)

        result = syncer.sync_participants()
        self.assertEqual(len(result), 1)
        self.assertIn("2", result.values())


if __name__ == "__main__":
    unittest.main()

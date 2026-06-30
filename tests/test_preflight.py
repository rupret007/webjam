"""Tests for the pre-jam Ready Check (core/preflight.py)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import preflight


def _settings(**over):
    base = dict(
        jamulus_candidates=[],
        jamulus_server="myband.example.com",
        jamulus_port=22124,
        webex_url="https://org.webex.com/meet/band",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ok_audio():
    return SimpleNamespace(ok=True, device_name="BlackHole 2ch", install_hint="")


def _bad_audio():
    return SimpleNamespace(ok=False, device_name="", install_hint="Install BlackHole")


class TestReadyCheck(unittest.TestCase):
    def test_all_good(self):
        with tempfile.NamedTemporaryFile(suffix="Jamulus") as jam:
            s = _settings(jamulus_candidates=[jam.name])
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertTrue(rep.all_ok, rep.to_text())
        self.assertIn("✓", rep.to_text())
        self.assertIn("ready to jam", rep.to_text())

    def test_jamulus_missing(self):
        s = _settings(jamulus_candidates=["/nope/Jamulus"])
        with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
            rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)
        item = next(i for i in rep.items if i.name == "Jamulus installed")
        self.assertFalse(item.ok)
        self.assertIn("jamulus.io", item.detail)

    def test_bad_server(self):
        with tempfile.NamedTemporaryFile() as jam:
            for bad in ("", "has space", None):
                s = _settings(jamulus_candidates=[jam.name], jamulus_server=bad)
                with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                    rep = preflight.run_ready_check(s)
                self.assertFalse(rep.all_ok)

    def test_bad_port(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(jamulus_candidates=[jam.name], jamulus_port=99999)
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)

    def test_audio_not_detected(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(jamulus_candidates=[jam.name])
            with mock.patch("core.audio_routing.scan_loopback_devices", _bad_audio):
                rep = preflight.run_ready_check(s)
        item = next(i for i in rep.items if i.name == "Audio routing device")
        self.assertFalse(item.ok)
        self.assertIn("BlackHole", item.detail)
        self.assertFalse(rep.all_ok)

    def test_webex_missing(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(jamulus_candidates=[jam.name], webex_url="")
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)
        item = next(i for i in rep.items if i.name == "Webex meeting set")
        self.assertFalse(item.ok)

    def test_to_text_marks_failures(self):
        s = _settings(jamulus_candidates=[], webex_url="")
        with mock.patch("core.audio_routing.scan_loopback_devices", _bad_audio):
            rep = preflight.run_ready_check(s)
        self.assertIn("✗", rep.to_text())
        self.assertIn("need attention", rep.to_text())


if __name__ == "__main__":
    unittest.main()

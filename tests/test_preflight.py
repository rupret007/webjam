"""Tests for the pre-jam Ready Check (core/preflight.py)."""
from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from core import preflight


def _settings(**over):
    base = dict(
        jamulus_candidates=[],
        jamulus_server="myband.example.com",
        jamulus_port=22124,
        webex_url="https://org.webex.com/meet/band",
        webex_audio_mode="talkback",
        local_capture_enabled=False,
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
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio), \
                    mock.patch("core.preflight.sys.platform", "darwin"):
                rep = preflight.run_ready_check(s)
        self.assertTrue(rep.automated_ok, rep.to_text())
        self.assertFalse(rep.all_ok)
        self.assertEqual(rep.pending_manual_count, 6)
        self.assertIn("✓", rep.to_text())
        self.assertIn("confirm 6 Webex settings", rep.to_text())

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

    def test_talkback_does_not_scan_for_loopback_audio(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(jamulus_candidates=[jam.name])
            with mock.patch("core.audio_routing.scan_loopback_devices") as scan:
                rep = preflight.run_ready_check(s)
        scan.assert_not_called()
        self.assertNotIn("Webex audio bridge", {i.name for i in rep.items})
        self.assertTrue(rep.automated_ok)

    def test_bridge_mac_requires_virtual_device(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(
                jamulus_candidates=[jam.name], webex_audio_mode="audience_bridge"
            )
            with mock.patch("core.audio_routing.scan_loopback_devices", _bad_audio):
                rep = preflight.run_ready_check(s)
        item = next(i for i in rep.items if i.name == "Webex audio bridge")
        self.assertTrue(item.required)
        self.assertFalse(rep.all_ok)

    def test_selected_input_must_support_requested_samplerate(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(
                jamulus_candidates=[jam.name],
                audio_input_device_index=7,
                audio_samplerate=48000,
            )
            devices = [{
                "index": 7, "name": "Interface", "channels": 2,
                "default_samplerate": 44100,
            }]
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio), \
                 mock.patch("core.audio_routing.list_input_devices", return_value=devices), \
                 mock.patch("sounddevice.check_input_settings", side_effect=ValueError("bad rate")):
                rep = preflight.run_ready_check(s)
        item = next(i for i in rep.items if i.name == "Meter and local recording input")
        self.assertFalse(item.ok)
        self.assertIn("48000", item.detail)
        # The detail must lead with guidance, not a raw exception.
        self.assertIn("Settings", item.detail)
        self.assertIn("bad rate", item.detail)

    def test_local_capture_requires_two_channels_48k_and_writable_takes(self):
        with tempfile.NamedTemporaryFile() as jam, tempfile.TemporaryDirectory() as takes:
            s = _settings(
                jamulus_candidates=[jam.name],
                audio_input_device_index=-1,
                audio_samplerate=48000,
                local_capture_enabled=True,
                takes_directory=takes,
            )
            with mock.patch("sounddevice.check_input_settings") as check:
                rep = preflight.run_ready_check(s)
        check.assert_called_once_with(device=None, channels=2, samplerate=48000)
        capture = next(i for i in rep.items if i.name == "Local stem recording")
        self.assertTrue(capture.ok)

    def test_local_capture_rejects_non_48k_setting(self):
        with tempfile.NamedTemporaryFile() as jam, tempfile.TemporaryDirectory() as takes:
            s = _settings(
                jamulus_candidates=[jam.name],
                audio_samplerate=44100,
                local_capture_enabled=True,
                takes_directory=takes,
            )
            rep = preflight.run_ready_check(s)
        item = next(
            i for i in rep.items if i.name == "Meter and local recording input"
        )
        self.assertFalse(item.ok)
        self.assertIn("48,000", item.detail)

    def test_manual_check_matrix_matches_mode(self):
        expected = {"talkback": 6, "video_only": 1, "audience_bridge": 5}
        with tempfile.NamedTemporaryFile() as jam:
            for mode, count in expected.items():
                with self.subTest(mode=mode), mock.patch(
                    "core.audio_routing.scan_loopback_devices", _ok_audio
                ), mock.patch("core.preflight.sys.platform", "darwin"):
                    rep = preflight.run_ready_check(_settings(
                        jamulus_candidates=[jam.name], webex_audio_mode=mode
                    ))
                manual = [i for i in rep.items if i.manual_verification]
                self.assertEqual(len(manual), count)
                self.assertTrue(all(i.required and not i.ok for i in manual))

    def test_non_macos_talkback_omits_macos_mic_mode(self):
        with tempfile.NamedTemporaryFile() as jam, mock.patch(
            "core.preflight.sys.platform", "win32"
        ):
            rep = preflight.run_ready_check(_settings(
                jamulus_candidates=[jam.name], webex_audio_mode="talkback"
            ))
        manual_names = {
            item.name for item in rep.items if item.manual_verification
        }
        self.assertEqual(len(manual_names), 5)
        self.assertNotIn("Standard microphone mode", manual_names)

    def test_recorder_unreachable_detail_is_actionable(self):
        with tempfile.NamedTemporaryFile() as jam, \
                tempfile.NamedTemporaryFile() as secret:
            s = _settings(
                jamulus_candidates=[jam.name],
                server_rpc_secret_file=secret.name,
            )
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio), \
                 mock.patch("core.jamulus_server_rpc.read_secret_file",
                            side_effect=OSError("connection refused")):
                rep = preflight.run_ready_check(s)
        item = next(i for i in rep.items if i.name == "Host recorder")
        self.assertFalse(item.ok)
        self.assertIn("Ready Check", item.detail)
        self.assertIn("connection refused", item.detail)
        # The pilot topology is same-Mac: lead with "is the server running"
        # and name the start script before the remote-Linux SSH hint.
        self.assertIn("start_macos_pilot.sh", item.detail)
        # A non-hosting Mac cannot self-correct at Start Audio, so an
        # unreachable recorder stays a required FIX there.
        self.assertTrue(item.required)

    def test_missing_takes_folder_is_not_created_when_not_hosting(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = f"{tmp}/no-such-takes"
            s = _settings(
                local_capture_enabled=True,
                takes_directory=missing,
            )
            item = preflight._check_local_capture(s)
            self.assertFalse(item.ok)
            self.assertIn("doesn't exist", item.detail)
            self.assertNotIn("not writable", item.detail)
            self.assertFalse(preflight.Path(missing).exists())

    def test_unwritable_takes_folder_keeps_not_writable_copy(self):
        import os as _os
        if _os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        with tempfile.TemporaryDirectory() as tmp:
            _os.chmod(tmp, 0o500)
            try:
                s = _settings(
                    local_capture_enabled=True,
                    takes_directory=tmp,
                )
                item = preflight._check_local_capture(s)
            finally:
                _os.chmod(tmp, 0o700)
        self.assertFalse(item.ok)
        self.assertIn("not writable", item.detail)

    def test_webex_missing(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(jamulus_candidates=[jam.name], webex_url="")
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)
        item = next(i for i in rep.items if i.name == "Webex meeting set")
        self.assertFalse(item.ok)

    def test_webex_http_url_fails(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(
                jamulus_candidates=[jam.name],
                webex_url="http://org.webex.com/meet/band",
            )
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)
        item = next(i for i in rep.items if i.name == "Webex meeting set")
        self.assertFalse(item.ok)
        self.assertIn("https", item.detail.lower())

    def test_non_webex_url_fails(self):
        with tempfile.NamedTemporaryFile() as jam:
            s = _settings(
                jamulus_candidates=[jam.name],
                webex_url="https://example.com/meet/band",
            )
            with mock.patch("core.audio_routing.scan_loopback_devices", _ok_audio):
                rep = preflight.run_ready_check(s)
        self.assertFalse(rep.all_ok)
        item = next(i for i in rep.items if i.name == "Webex meeting set")
        self.assertFalse(item.ok)
        self.assertIn("webex.com", item.detail)

    def test_to_text_marks_failures(self):
        s = _settings(jamulus_candidates=[], webex_url="")
        with mock.patch("core.audio_routing.scan_loopback_devices", _bad_audio):
            rep = preflight.run_ready_check(s)
        self.assertIn("✗", rep.to_text())
        self.assertIn("need attention", rep.to_text())


if __name__ == "__main__":
    unittest.main()

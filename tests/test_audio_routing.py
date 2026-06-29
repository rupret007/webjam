"""
Tests for core.audio_routing — scan_loopback_devices, list_input_devices,
find_device_index, AudioRoutingStatus, and LoopbackDevice.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from core.audio_routing import (
    AudioRoutingStatus,
    LoopbackDevice,
    find_device_index,
    list_input_devices,
    scan_loopback_devices,
)


# ---------------------------------------------------------------------------
# AudioRoutingStatus unit tests
# ---------------------------------------------------------------------------

class TestAudioRoutingStatus:
    def test_scan_returns_routing_status(self):
        """scan_loopback_devices always returns an AudioRoutingStatus."""
        status = scan_loopback_devices()
        assert isinstance(status, AudioRoutingStatus)

    def test_status_ok_false_when_no_device(self):
        """ok is False when loopback_device is None."""
        status = AudioRoutingStatus(loopback_device=None)
        assert status.ok is False

    def test_scan_never_raises_when_backend_missing(self):
        """sounddevice installed but native PortAudio missing raises OSError on
        import — scan must capture it (docstring promises 'never raises') so the
        background routing-scan thread can't die silently."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise OSError("PortAudio library not found")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            status = scan_loopback_devices()  # must NOT raise
        assert isinstance(status, AudioRoutingStatus)
        assert status.ok is False
        assert status.scan_error  # a reason is recorded

    def test_status_ok_true_when_device_present(self):
        """ok is True when a loopback device is set."""
        dev = LoopbackDevice(
            index=0, name="BlackHole 2ch",
            max_input_channels=2, max_output_channels=2,
            default_samplerate=48000.0,
        )
        status = AudioRoutingStatus(loopback_device=dev)
        assert status.ok is True

    def test_install_hint_darwin(self):
        """On darwin, install_hint mentions BlackHole."""
        status = AudioRoutingStatus()
        with patch.object(sys, "platform", "darwin"):
            hint = status.install_hint
        assert "BlackHole" in hint

    def test_install_hint_windows(self):
        """On windows, install_hint mentions VB-CABLE."""
        status = AudioRoutingStatus()
        with patch.object(sys, "platform", "win32"):
            hint = status.install_hint
        assert "VB-CABLE" in hint

    def test_install_url_darwin(self):
        """On darwin, install_url contains existential.audio."""
        status = AudioRoutingStatus()
        with patch.object(sys, "platform", "darwin"):
            url = status.install_url
        assert "existential.audio" in url

    def test_install_url_windows(self):
        """On windows, install_url contains vb-audio.com."""
        status = AudioRoutingStatus()
        with patch.object(sys, "platform", "win32"):
            url = status.install_url
        assert "vb-audio.com" in url


# ---------------------------------------------------------------------------
# LoopbackDevice unit tests
# ---------------------------------------------------------------------------

class TestLoopbackDevice:
    def test_loopback_device_has_input_property(self):
        """LoopbackDevice with max_input_channels > 0 has has_input == True."""
        dev = LoopbackDevice(
            index=1, name="BlackHole 2ch",
            max_input_channels=2, max_output_channels=2,
            default_samplerate=48000.0,
        )
        assert dev.has_input is True

    def test_loopback_device_no_input_property(self):
        """LoopbackDevice with max_input_channels == 0 has has_input == False."""
        dev = LoopbackDevice(
            index=1, name="Cable Output",
            max_input_channels=0, max_output_channels=2,
            default_samplerate=48000.0,
        )
        assert dev.has_input is False


# ---------------------------------------------------------------------------
# scan_loopback_devices with sounddevice unavailable
# ---------------------------------------------------------------------------

class TestScanWithoutSounddevice:
    def test_scan_handles_sounddevice_import_error(self):
        """If sounddevice is not importable, returns status with scan_error set."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("No module named 'sounddevice'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            status = scan_loopback_devices()

        assert status.scan_error is not None
        assert isinstance(status.scan_error, str)
        assert status.ok is False


# ---------------------------------------------------------------------------
# scan_loopback_devices with mocked sounddevice
# ---------------------------------------------------------------------------

_MOCK_DEVICES = [
    {
        "name": "Built-in Microphone",
        "max_input_channels": 1,
        "max_output_channels": 0,
        "default_samplerate": 44100.0,
    },
    {
        "name": "BlackHole 2ch",
        "max_input_channels": 2,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    },
    {
        "name": "Built-in Output",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 44100.0,
    },
]


class TestScanWithMockedSounddevice:
    def test_scan_finds_blackhole_device(self):
        """With mocked devices, scan finds the BlackHole loopback device."""
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = _MOCK_DEVICES

        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            # Re-import the module to force fresh sounddevice import path
            import importlib
            import core.audio_routing as routing_mod
            importlib.reload(routing_mod)
            status = routing_mod.scan_loopback_devices()

        assert status.ok is True
        assert "BlackHole" in status.device_name

    def test_scan_returns_empty_when_no_loopback(self):
        """When no loopback device is present, ok is False."""
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [
            {
                "name": "Built-in Microphone",
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 44100.0,
            }
        ]
        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            import importlib
            import core.audio_routing as routing_mod
            importlib.reload(routing_mod)
            status = routing_mod.scan_loopback_devices()

        assert status.ok is False
        assert status.loopback_device is None


# ---------------------------------------------------------------------------
# find_device_index
# ---------------------------------------------------------------------------

class TestFindDeviceIndex:
    def test_find_device_index_no_match(self):
        """find_device_index returns None when no device matches."""
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [
            {"name": "Built-in Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 44100.0},
        ]
        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            import importlib
            import core.audio_routing as routing_mod
            importlib.reload(routing_mod)
            result = routing_mod.find_device_index("nonexistent_device_xyz")
        assert result is None

    def test_find_device_index_returns_correct_index(self):
        """find_device_index returns the correct index when a device matches."""
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [
            {"name": "Built-in Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 44100.0},
            {"name": "BlackHole 2ch", "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 48000.0},
        ]
        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            import importlib
            import core.audio_routing as routing_mod
            importlib.reload(routing_mod)
            result = routing_mod.find_device_index("blackhole")
        assert result == 1

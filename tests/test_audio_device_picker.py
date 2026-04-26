"""
Tests for the audio input device picker on the setup wizard's Routing page,
plus the corresponding precedence rule in RealAudioEngine._resolve_device.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings


def _pyside6_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


skip_no_pyside6 = unittest.skipUnless(_pyside6_available(), "PySide6 not installed")


_SAMPLE_DEVICES = [
    {"index": 0, "name": "Built-in Microphone", "channels": 1},
    {"index": 1, "name": "BlackHole 2ch", "channels": 2},
    {"index": 2, "name": "USB Audio Interface", "channels": 8},
]


# ---------------------------------------------------------------------------
# _RoutingPage device picker
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestRoutingPageDevicePicker(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_routing_page_default_to_system_default(self):
        """The combo always starts with 'System default' at index 0 (data = -1)."""
        from webjam_qt.windows.setup_wizard import _RoutingPage
        page = _RoutingPage(AppSettings())
        self.assertEqual(page._device_picker.itemData(0), -1)
        self.assertIn("default", page._device_picker.itemText(0).lower())

    def test_routing_page_populates_devices_from_scan(self):
        """After _populate_device_picker, the combo lists every input device."""
        from webjam_qt.windows.setup_wizard import _RoutingPage
        page = _RoutingPage(AppSettings())

        with patch(
            "core.audio_routing.list_input_devices",
            return_value=_SAMPLE_DEVICES,
        ):
            page._populate_device_picker()

        # 1 default + 3 mocked devices
        self.assertEqual(page._device_picker.count(), 4)
        # Entry 1 is the first mocked device
        self.assertEqual(page._device_picker.itemData(1), 0)
        self.assertIn("Built-in Microphone", page._device_picker.itemText(1))
        self.assertIn("(1 ch)", page._device_picker.itemText(1))
        # Last entry is the USB interface
        self.assertEqual(page._device_picker.itemData(3), 2)
        self.assertIn("USB Audio Interface", page._device_picker.itemText(3))

    def test_routing_page_preserves_saved_index(self):
        """A saved audio_input_device_index pre-selects the matching combo entry."""
        from webjam_qt.windows.setup_wizard import _RoutingPage
        settings = AppSettings(audio_input_device_index=2)
        page = _RoutingPage(settings)

        with patch(
            "core.audio_routing.list_input_devices",
            return_value=_SAMPLE_DEVICES,
        ):
            page._populate_device_picker()

        self.assertEqual(page._device_picker.currentData(), 2)
        self.assertEqual(page.device_index, 2)

    def test_routing_page_falls_back_when_saved_index_missing(self):
        """If the saved index isn't in the list, stay on 'System default'."""
        from webjam_qt.windows.setup_wizard import _RoutingPage
        settings = AppSettings(audio_input_device_index=99)
        page = _RoutingPage(settings)

        with patch(
            "core.audio_routing.list_input_devices",
            return_value=_SAMPLE_DEVICES,
        ):
            page._populate_device_picker()

        self.assertEqual(page.device_index, -1)

    def test_routing_page_handles_no_sounddevice(self):
        """When sounddevice is missing, list_input_devices returns []; combo
        still has the System-default entry only."""
        from webjam_qt.windows.setup_wizard import _RoutingPage
        page = _RoutingPage(AppSettings())

        with patch("core.audio_routing.list_input_devices", return_value=[]):
            page._populate_device_picker()

        self.assertEqual(page._device_picker.count(), 1)
        self.assertEqual(page.device_index, -1)


# ---------------------------------------------------------------------------
# RealAudioEngine._resolve_device — settings precedence
# ---------------------------------------------------------------------------
class TestAudioEngineExplicitDeviceIndex(unittest.TestCase):
    def test_audio_engine_uses_explicit_device_index(self):
        """settings.audio_input_device_index >= 0 wins over auto-detection."""
        import core.audio_engine as engine_mod
        from core.audio_engine import RealAudioEngine

        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = {"name": "USB Audio Interface"}

        settings = AppSettings(audio_input_device_index=5)
        eng = RealAudioEngine(settings)

        with patch.object(engine_mod, "sd", mock_sd), \
             patch("core.audio_routing.scan_loopback_devices") as mock_scan:
            result = eng._resolve_device()

        self.assertEqual(result, 5)
        mock_sd.query_devices.assert_called_with(5)
        # Auto-scan must NOT run when an explicit choice is honored
        mock_scan.assert_not_called()
        self.assertEqual(eng.diagnostics().input_device, "USB Audio Interface")

    def test_audio_engine_falls_back_to_autoscan_when_default(self):
        """audio_input_device_index = -1 keeps the existing auto-detect path."""
        import core.audio_engine as engine_mod
        from core.audio_engine import RealAudioEngine
        from core.audio_routing import AudioRoutingStatus, LoopbackDevice

        loopback = LoopbackDevice(
            index=7, name="BlackHole 2ch",
            max_input_channels=2, max_output_channels=2,
            default_samplerate=48000.0,
        )
        scan_result = AudioRoutingStatus(
            loopback_device=loopback, all_devices=[loopback]
        )

        settings = AppSettings(audio_input_device_index=-1)
        eng = RealAudioEngine(settings)

        with patch.object(engine_mod, "sd", MagicMock()), \
             patch("core.audio_routing.scan_loopback_devices",
                   return_value=scan_result) as mock_scan:
            result = eng._resolve_device()

        self.assertEqual(result, 7)
        mock_scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()

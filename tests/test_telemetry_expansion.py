"""Round-3 telemetry-audit additions.

Verifies the seven new metric keys are recognised by ``MetricsService`` and
that the controllers / managers that emit them call ``metrics.increment``
in the expected branches.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from tests.support.jamulus_monitor import bind_primary_rpc_monitor  # noqa: E402
from ui.services import MetricsService  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.controllers.mix_manager import MixManager  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


_NEW_KEYS = (
    "metric_jamulus_hang_detected",
    "metric_audio_device_blackhole_found",
    "metric_audio_device_missing",
    "metric_mix_corruption_recovered",
    "metric_session_started",
)


class TestMetricKeys(unittest.TestCase):
    def test_metric_keys_includes_new_metrics(self) -> None:
        for key in _NEW_KEYS:
            self.assertIn(
                key,
                MetricsService.METRIC_KEYS,
                f"{key} should be registered in MetricsService.METRIC_KEYS",
            )


class _TempHome:
    def __enter__(self):
        self._old_home = os.environ.get("HOME", "")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        if self._old_home:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        self._tmp.cleanup()
        return False


class TestMixCorruptionMetric(unittest.TestCase):
    def test_mix_load_corruption_increments_recovered(self) -> None:
        with _TempHome() as home:
            (home / ".webjam_mix.json").write_text("{not json", encoding="utf-8")

            jamulus = mock.MagicMock()
            metrics = mock.MagicMock(spec=MetricsService)

            def _flash(text: str, ms: int) -> None:
                pass

            manager = MixManager(jamulus, _flash, metrics=metrics)
            self.assertFalse(manager.load())

            metrics.increment.assert_called_once_with("metric_mix_corruption_recovered")


class TestApplicationControllerMetrics(unittest.TestCase):
    """Exercises the controller branches that emit the new metric keys."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.controller.shutdown()

    def setUp(self) -> None:
        # Replace MetricsService with a mock so we can spy on increments
        # without writing to the real repository.
        self._metrics_mock = mock.MagicMock(spec=MetricsService)
        self.controller.metrics = self._metrics_mock
        self.controller.window.flash_message = mock.MagicMock()
        self.controller.window.set_status_routing = mock.MagicMock()
        self.controller.settings.webex_audio_mode = "talkback"
        # Reset session-started latch
        self.controller._jamulus_connected = False
        self.controller._reset_to_demo_state()
        process = mock.MagicMock()
        process.pid = 4400
        process.poll.return_value = None
        self.controller.bridge.jamulus_process = process
        self.controller.bridge.jamulus_launch_intended = True
        self.controller.bridge.jamulus_state = "Running"
        rpc = mock.MagicMock()
        rpc.available = True
        rpc.last_activity_age.return_value = 0.1
        self.controller.jamulus.rpc_client = rpc
        self.source_identity = bind_primary_rpc_monitor(self.controller)

    def _called(self, key: str) -> bool:
        return any(
            call.args and call.args[0] == key
            for call in self._metrics_mock.increment.call_args_list
        )

    def test_apply_routing_status_ok_increments_blackhole_found(self) -> None:
        self.controller.settings.webex_audio_mode = "audience_bridge"
        status = SimpleNamespace(
            ok=True,
            device_name="BlackHole 16ch",
            install_hint="",
        )
        self.controller._apply_routing_status(status)
        self.assertTrue(self._called("metric_audio_device_blackhole_found"))
        self.assertFalse(self._called("metric_audio_device_missing"))

    def test_apply_routing_status_not_ok_increments_missing(self) -> None:
        self.controller.settings.webex_audio_mode = "audience_bridge"
        status = SimpleNamespace(
            ok=False,
            device_name="",
            install_hint="Install BlackHole",
        )
        self.controller._apply_routing_status(status)
        self.assertTrue(self._called("metric_audio_device_missing"))
        self.assertFalse(self._called("metric_audio_device_blackhole_found"))

    def test_talkback_does_not_scan_or_warn_for_loopback(self) -> None:
        with mock.patch("core.audio_routing.scan_loopback_devices") as scan:
            self.controller._start_routing_scan()
        scan.assert_not_called()
        self.controller.window.set_status_routing.assert_called_with("")
        self.controller.window.flash_message.assert_not_called()

    def test_late_loopback_result_is_ignored_after_switch_to_talkback(self) -> None:
        status = SimpleNamespace(
            ok=False,
            device_name="",
            install_hint="Install BlackHole",
        )
        self.controller._apply_routing_status(status)
        self.controller.window.set_status_routing.assert_called_with("Not required")
        self.assertFalse(self._called("metric_audio_device_missing"))

    def test_first_jamulus_participants_increments_session_started(self) -> None:
        # First arrival flips the latch and increments once.
        first = [JamulusParticipant(channel_id=10, name="Alice", is_local=True)]
        self.controller._apply_jamulus_participants(
            first,
            source_identity=self.source_identity,
        )
        # Second arrival does not re-increment (latch is set).
        second = [
            JamulusParticipant(channel_id=10, name="Alice", is_local=True),
            JamulusParticipant(channel_id=11, name="Bob"),
        ]
        self.controller._apply_jamulus_participants(
            second,
            source_identity=self.source_identity,
        )

        session_calls = [
            c for c in self._metrics_mock.increment.call_args_list
            if c.args and c.args[0] == "metric_session_started"
        ]
        self.assertEqual(
            len(session_calls), 1,
            f"session_started should fire exactly once, got {session_calls!r}",
        )

    def test_rpc_hang_increments_metric(self) -> None:
        """The RPC-hang branch should bump metric_jamulus_hang_detected."""
        self.controller._jamulus_connected = True
        self.controller._rpc_hang_banner_shown = False

        fake_proc = mock.MagicMock()
        fake_proc.poll.return_value = None
        self.controller.bridge.jamulus_process = fake_proc
        self.controller.bridge.jamulus_launch_intended = True
        self.controller.bridge.jamulus_state = "Running"
        self.controller.bridge.attempt_auto_reconnects = mock.MagicMock()

        self.controller.jamulus.rpc_client = mock.MagicMock()
        self.controller.jamulus.rpc_client.last_activity_age.return_value = 30.0

        self.controller._on_reconnect_tick()

        self.assertTrue(self._called("metric_jamulus_hang_detected"))


if __name__ == "__main__":
    unittest.main()

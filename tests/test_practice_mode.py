"""
Practice mode — solo session against a private local Jamulus server.

Covers BridgeService.launch_practice_session (server spawn, client launch
targeting 127.0.0.1, not-found and spawn-failure paths, fresh-install
exemption from the no-server guard), stop_jamulus teardown of the practice
server, the reconnect-tick dead-server guard, effective_server(), and the
Conductor entry point.  No real processes are spawned.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _ImmediateThread:
    def __init__(self, *args, target=None, daemon=None, name=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()

    def join(self, timeout=None):
        pass


def _make_bridge(server="jam.example.com"):
    from services.bridge_service import BridgeService

    settings = MagicMock()
    settings.jamulus_server = server
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.jamulus_candidates = []
    repository = MagicMock()
    repository.get_setting.return_value = "1"
    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=repository,
        settings=settings,
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda f: f(),
        },
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    return bridge


class TestLaunchPracticeSession(unittest.TestCase):
    @patch("services.bridge_service.time.sleep")
    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    @patch("services.bridge_service.subprocess.Popen")
    def test_spawns_local_server_then_connects_client_to_it(
        self, popen_mock, _thread, _sleep
    ):
        from services.bridge_service import PRACTICE_PORT
        procs = []

        def _fake_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.poll.return_value = None
            procs.append(cmd)
            return proc

        popen_mock.side_effect = _fake_popen
        bridge = _make_bridge()

        self.assertTrue(bridge.launch_practice_session())

        own = [c for c in procs if c[0] == "/usr/bin/jamulus"]
        self.assertEqual(len(own), 2, procs)
        server_cmd, client_cmd = own
        self.assertIn("--server", server_cmd)
        self.assertIn("--nogui", server_cmd)
        self.assertIn(str(PRACTICE_PORT), server_cmd)
        self.assertIn("--connect", client_cmd)
        self.assertIn(f"127.0.0.1:{PRACTICE_PORT}", client_cmd)
        self.assertTrue(bridge.practice_mode)
        self.assertEqual(bridge.jamulus_state, "Running")
        bridge.metrics_service.increment.assert_any_call(
            "metric_practice_mode_started"
        )

    @patch("services.bridge_service.time.sleep")
    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    @patch("services.bridge_service.subprocess.Popen")
    def test_fresh_install_without_band_server_can_practice(
        self, popen_mock, _thread, _sleep
    ):
        """The no-server-configured guard must NOT block practice mode."""
        proc = MagicMock()
        proc.poll.return_value = None
        popen_mock.return_value = proc
        bridge = _make_bridge(server="")  # unconfigured fresh install

        self.assertTrue(bridge.launch_practice_session())

        bridge.show_actionable_error.assert_not_called()
        self.assertEqual(bridge.jamulus_state, "Running")

    def test_not_found_shows_error_and_spawns_nothing(self):
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value=None)
        with patch("services.bridge_service.subprocess.Popen") as popen_mock:
            self.assertFalse(bridge.launch_practice_session())
        popen_mock.assert_not_called()
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0], "Jamulus Not Found"
        )
        self.assertFalse(bridge.practice_mode)

    def test_server_spawn_failure_shows_error(self):
        bridge = _make_bridge()
        with patch("services.bridge_service.subprocess.Popen",
                   side_effect=OSError("blocked")):
            self.assertFalse(bridge.launch_practice_session())
        self.assertFalse(bridge.practice_mode)
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0],
            "Practice Server Failed",
        )

    def test_refused_while_band_session_running(self):
        bridge = _make_bridge()
        alive = MagicMock()
        alive.poll.return_value = None
        bridge.jamulus_process = alive
        self.assertFalse(bridge.launch_practice_session())
        banners = [c.args[0] for c in bridge.set_status_banner.call_args_list]
        self.assertTrue(any("Stop Audio first" in b for b in banners), banners)


class TestPracticeTeardown(unittest.TestCase):
    def test_stop_jamulus_terminates_practice_server_and_clears_mode(self):
        bridge = _make_bridge()
        practice = MagicMock()
        practice.poll.return_value = None
        bridge.practice_server_process = practice
        bridge.practice_mode = True

        bridge.stop_jamulus()

        practice.terminate.assert_called_once()
        self.assertIsNone(bridge.practice_server_process)
        self.assertFalse(bridge.practice_mode)

    def test_reconnect_tick_ends_practice_when_server_dies(self):
        bridge = _make_bridge()
        dead = MagicMock()
        dead.poll.return_value = 1
        bridge.practice_server_process = dead
        bridge.practice_mode = True
        bridge.jamulus_launch_intended = True
        bridge.launch_jamulus = MagicMock()  # must NOT be reconnect-looped

        bridge.attempt_auto_reconnects()

        self.assertFalse(bridge.practice_mode)
        bridge.launch_jamulus.assert_not_called()
        banners = [c.args[0] for c in bridge.set_status_banner.call_args_list]
        self.assertTrue(any("Practice session ended" in b for b in banners),
                        banners)

    def test_effective_server_switches_with_mode(self):
        from services.bridge_service import PRACTICE_PORT
        bridge = _make_bridge()
        self.assertEqual(bridge.effective_server(), "jam.example.com:22124")
        bridge.practice_mode = True
        self.assertEqual(bridge.effective_server(), f"127.0.0.1:{PRACTICE_PORT}")


class TestConductorPracticeEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import (
            ApplicationController,
        )
        from webjam_qt.windows.conductor_window import ConductorWindow
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        self.controller.window.flash_message = MagicMock()
        self.controller.bridge.jamulus_state = "Not launched"

    def test_practice_requested_launches_when_stopped(self):
        c = self.controller
        c.bridge.launch_practice_session = MagicMock(return_value=True)
        c._on_practice_requested()
        c.bridge.launch_practice_session.assert_called_once()

    def test_practice_refused_while_running(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        c.bridge.launch_practice_session = MagicMock()
        c._on_practice_requested()
        c.bridge.launch_practice_session.assert_not_called()
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("End the current session" in m for m in msgs), msgs)

    def test_failed_start_restores_button(self):
        c = self.controller
        c.bridge.launch_practice_session = MagicMock(return_value=False)
        c.window.session_strip.set_audio_state = MagicMock()
        c._on_practice_requested()
        c.window.session_strip.set_audio_state.assert_called_with(
            "Start Session", enabled=True
        )

    def test_strip_button_emits_signal(self):
        received = []
        self.window.session_strip.practice_requested.connect(
            lambda: received.append(True)
        )
        self.window.session_strip._practice_action.trigger()
        self.assertEqual(received, [True])

    def test_readiness_shows_practice_server_when_practicing(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        c.bridge.webex_state = "Not opened"
        c.bridge.practice_mode = True
        try:
            c._refresh_readiness()
            text = self.window._status_audio.text()
            self.assertIn("Practice", text)
            self.assertNotIn("127.0.0.1", text)
        finally:
            c.bridge.practice_mode = False


if __name__ == "__main__":
    unittest.main()

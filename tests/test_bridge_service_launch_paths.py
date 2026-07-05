"""
BridgeService launch/failure path coverage.

Exercises the launch_jamulus not-found and already-running branches, the
_do_launch failure path (Popen retries exhausted) for both manual and
reconnect flavours, the JSON-RPC secret-file contract on the Jamulus command
line, find_jamulus fallback resolution, and the Webex open-failure and
reconnect-gating branches.  No real processes are spawned.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously."""

    def __init__(self, *args, target=None, daemon=None, name=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()

    def join(self, timeout=None):
        pass


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.jamulus_server = "jam.example.com"
    s.jamulus_port = 22124
    s.jamulus_rpc_port = 22222
    s.jamulus_candidates = []
    s.webex_url = "https://example.webex.com/m/x"
    return s


def _make_bridge():
    from services.bridge_service import BridgeService

    repository = MagicMock()
    repository.get_setting.return_value = "1"

    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=repository,
        settings=_make_settings(),
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda f: f(),
        },
    )
    return bridge


class TestLaunchJamulusNotFound(unittest.TestCase):
    def test_manual_launch_not_found_shows_error_and_clears_inflight(self):
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value=None)
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertEqual(bridge.jamulus_state, "Not found")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0], "Jamulus Not Found"
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_launch_failed"
        )

    def test_reconnect_launch_not_found_skips_dialog(self):
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value=None)
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=False, reconnect=True)

        self.assertEqual(bridge.jamulus_state, "Not running")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        bridge.show_actionable_error.assert_not_called()
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_reconnect_failed"
        )


class TestLaunchJamulusNoServerConfigured(unittest.TestCase):
    """Fresh installs have NO default server (the old default was a dead
    LAN IP).  Launching without one must explain itself, not crash-loop."""

    def test_manual_launch_without_server_shows_actionable_error(self):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = ""
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertEqual(bridge.jamulus_state, "Not running")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        self.assertFalse(bridge.jamulus_launch_intended)  # no reconnect loop
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0],
            "No Jamulus Server Configured",
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_launch_failed"
        )

    def test_whitespace_server_treated_as_missing(self):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "   "
        bridge.launch_jamulus(manual=True, reconnect=False)
        bridge.show_actionable_error.assert_called_once()

    def test_reconnect_without_server_stays_quiet_and_stops_retrying(self):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = ""
        bridge.jamulus_launch_intended = True
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=False, reconnect=True)

        bridge.show_actionable_error.assert_not_called()
        self.assertFalse(bridge.jamulus_launch_intended)
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_reconnect_failed"
        )


class TestLaunchJamulusAlreadyRunning(unittest.TestCase):
    def test_manual_launch_flashes_already_running_banner(self):
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        alive = MagicMock()
        alive.poll.return_value = None
        bridge.jamulus_process = alive
        bridge.jamulus_reconnect_attempts = 3
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertEqual(bridge.jamulus_state, "Already running")
        self.assertEqual(bridge.jamulus_reconnect_attempts, 0)
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        banners = [
            call.args[0] for call in bridge.set_status_banner.call_args_list
        ]
        self.assertTrue(any("already running" in b for b in banners), banners)


@patch("services.bridge_service.time.sleep")
@patch("services.bridge_service.threading.Thread",
       side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
class TestLaunchJamulusFailure(unittest.TestCase):
    def _bridge_with_binary(self):
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        return bridge

    @patch("services.bridge_service.subprocess.Popen",
           side_effect=OSError("spawn failed"))
    def test_manual_launch_failure_after_retries_shows_error(
        self, popen_mock, _thread, _sleep
    ):
        bridge = self._bridge_with_binary()
        # Unique server so we can filter out Popen calls made by stray
        # _do_launch daemon threads leaked from earlier tests (they share
        # the module-global subprocess.Popen patch).
        bridge.settings.jamulus_server = "retry-probe.example.com"
        bridge.launch_jamulus(manual=True, reconnect=False)

        own_calls = [
            c for c in popen_mock.call_args_list
            if c.args and "retry-probe.example.com:22124" in c.args[0]
        ]
        self.assertEqual(len(own_calls), 3)  # retried 3 times
        self.assertEqual(bridge.jamulus_state, "Launch failed")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0],
            "Jamulus Launch Failed",
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_launch_failed"
        )

    @patch("services.bridge_service.subprocess.Popen",
           side_effect=OSError("spawn failed"))
    def test_reconnect_launch_failure_stays_quiet(
        self, popen_mock, _thread, _sleep
    ):
        bridge = self._bridge_with_binary()
        bridge.launch_jamulus(manual=False, reconnect=True)

        self.assertEqual(bridge.jamulus_state, "Not running")
        bridge.show_actionable_error.assert_not_called()
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_reconnect_failed"
        )


@patch("services.bridge_service.threading.Thread",
       side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
class TestLaunchCommandContract(unittest.TestCase):
    """The Jamulus command line must match the real 3.9+ JSON-RPC contract."""

    def _launch_and_capture_cmd(self, bridge):
        captured = {}
        marker = f"{bridge.settings.jamulus_server}:{bridge.settings.jamulus_port}"

        def _fake_popen(cmd, **kwargs):
            # Ignore Popen calls from stray daemon threads of earlier tests.
            if marker in cmd:
                captured["cmd"] = cmd
            proc = MagicMock()
            proc.poll.return_value = None
            return proc

        with patch("services.bridge_service.subprocess.Popen",
                   side_effect=_fake_popen), \
             patch("services.bridge_service.time.sleep"):
            bridge.launch_jamulus(manual=True, reconnect=False)
        return captured.get("cmd", [])

    def test_cmd_includes_connect_rpc_port_and_secret_file(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "contract-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        cmd = self._launch_and_capture_cmd(bridge)

        self.assertEqual(cmd[0], "/usr/bin/jamulus")
        self.assertIn("--connect", cmd)
        self.assertIn("contract-probe.example.com:22124", cmd)
        self.assertIn("--jsonrpcport", cmd)
        self.assertIn("22222", cmd)
        self.assertIn("--jsonrpcsecretfile", cmd)
        self.assertEqual(bridge.jamulus_state, "Running")

    def test_secret_write_failure_launches_without_secret_args(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "nosecret-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch("core.file_io.atomic_write_text",
                   side_effect=OSError("read-only home")):
            cmd = self._launch_and_capture_cmd(bridge)

        self.assertNotIn("--jsonrpcsecretfile", cmd)
        self.assertIn("--connect", cmd)  # launch still proceeds
        self.assertEqual(bridge.jamulus_state, "Running")


class TestFindJamulusFallback(unittest.TestCase):
    def test_user_candidate_wins_when_it_exists(self):
        import tempfile
        from pathlib import Path
        bridge = _make_bridge()
        with tempfile.NamedTemporaryFile(suffix="-jamulus") as fake_binary:
            bridge.settings.jamulus_candidates = [fake_binary.name]
            self.assertEqual(bridge.find_jamulus(), fake_binary.name)
            assert Path(fake_binary.name).exists()

    def test_falls_back_to_default_candidates(self):
        import tempfile
        bridge = _make_bridge()
        bridge.settings.jamulus_candidates = ["/nonexistent/custom/jamulus"]
        with tempfile.NamedTemporaryFile(suffix="-jamulus") as fake_binary:
            default_settings = MagicMock()
            default_settings.jamulus_candidates = [fake_binary.name]
            with patch("core.settings.AppSettings",
                       return_value=default_settings):
                self.assertEqual(bridge.find_jamulus(), fake_binary.name)

    def test_returns_none_when_nothing_exists(self):
        bridge = _make_bridge()
        bridge.settings.jamulus_candidates = ["/nonexistent/a"]
        default_settings = MagicMock()
        default_settings.jamulus_candidates = ["/nonexistent/b"]
        with patch("core.settings.AppSettings", return_value=default_settings):
            self.assertIsNone(bridge.find_jamulus())


@patch("services.bridge_service.time.sleep")
@patch("services.bridge_service.threading.Thread",
       side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
class TestLaunchWebexFailure(unittest.TestCase):
    def test_manual_open_failure_shows_error(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting.return_value = False

        bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Open failed")
        self.assertEqual(bridge.webex_controller.join_meeting.call_count, 3)
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0], "Webex Open Failed"
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_webex_open_failed"
        )

    def test_reconnect_open_failure_stays_quiet(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting.side_effect = RuntimeError("boom")

        bridge.launch_webex(manual=False, reconnect=True)

        self.assertEqual(bridge.webex_state, "Open failed")
        bridge.show_actionable_error.assert_not_called()
        bridge.metrics_service.increment.assert_any_call(
            "metric_webex_reconnect_failed"
        )


class TestWebexReconnectGating(unittest.TestCase):
    def test_no_reconnect_while_opened_in_browser(self):
        bridge = _make_bridge()
        bridge.webex_launch_intended = True
        bridge.webex_controller.is_connected = False
        bridge.webex_state = "Opened in browser"
        bridge.launch_webex = MagicMock()

        bridge._attempt_auto_reconnect_webex(now=100.0)

        bridge.launch_webex.assert_not_called()
        self.assertEqual(bridge.webex_reconnect_attempts, 0)

    def test_webex_reconnect_caps_at_5_attempts(self):
        bridge = _make_bridge()
        bridge.webex_launch_intended = True
        bridge.webex_controller.is_connected = False
        bridge.webex_state = "Open failed"
        bridge.webex_reconnect_attempts = 5
        bridge.launch_webex = MagicMock()

        bridge._attempt_auto_reconnect_webex(now=100.0)

        bridge.launch_webex.assert_not_called()
        self.assertEqual(bridge.webex_reconnect_attempts, 5)

    def test_webex_reconnect_resets_when_connected(self):
        bridge = _make_bridge()
        bridge.webex_launch_intended = True
        bridge.webex_controller.is_connected = True
        bridge.webex_reconnect_attempts = 3
        bridge.webex_next_reconnect_at = 99.0
        bridge.webex_reconnect_inflight = True

        bridge._attempt_auto_reconnect_webex(now=100.0)

        self.assertEqual(bridge.webex_reconnect_attempts, 0)
        self.assertEqual(bridge.webex_next_reconnect_at, 0.0)
        self.assertFalse(bridge.webex_reconnect_inflight)


if __name__ == "__main__":
    unittest.main()

"""
BridgeService launch/failure path coverage.

Exercises the launch_jamulus not-found and already-running branches, the
_do_launch failure path (Popen retries exhausted) for both manual and
reconnect flavours, the JSON-RPC secret-file contract on the Jamulus command
line, find_jamulus fallback resolution, and the Webex open-failure and
reconnect-gating branches.  No real processes are spawned.
"""
from __future__ import annotations

import sys
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
            bridge.show_actionable_error.call_args.args[0],
            "A music component is missing",
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
            "This jam needs a new invite",
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
            "Band audio couldn’t start",
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
        self.assertIn("--nogui", cmd)
        self.assertIn("--connect", cmd)
        self.assertIn("contract-probe.example.com:22124", cmd)
        self.assertIn("--jsonrpcport", cmd)
        self.assertIn("22222", cmd)
        self.assertIn("--jsonrpcsecretfile", cmd)
        from core.jamulus_rpc_client import DEFAULT_SECRET_PATH
        secret_index = cmd.index("--jsonrpcsecretfile") + 1
        self.assertEqual(cmd[secret_index], str(DEFAULT_SECRET_PATH))
        self.assertEqual(bridge.jamulus_state, "Running")

    def test_immediate_client_exit_is_not_reported_as_running(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "early-exit.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        proc = MagicMock()
        proc.poll.return_value = 64

        with patch("services.bridge_service.subprocess.Popen", return_value=proc), \
             patch("services.bridge_service.time.sleep"):
            bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertEqual(bridge.jamulus_state, "Launch failed")
        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_launch_failed"
        )
        bridge.show_actionable_error.assert_called_once()

    def test_secret_write_failure_fails_closed_without_launch(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "nosecret-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch("core.file_io.atomic_write_text",
                   side_effect=OSError("read-only home")):
            cmd = self._launch_and_capture_cmd(bridge)

        self.assertEqual(cmd, [])
        self.assertEqual(bridge.jamulus_state, "Launch failed")
        bridge.show_actionable_error.assert_called_once()

    def test_two_queued_launch_workers_spawn_only_one_client(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "double-launch.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        queued = []

        class _QueuedThread:
            def __init__(self, *args, target=None, **kwargs):
                self._target = target

            def start(self):
                queued.append(self._target)

        process = MagicMock()
        process.poll.return_value = None
        with patch(
            "services.bridge_service.threading.Thread", _QueuedThread
        ), patch(
            "services.bridge_service.subprocess.Popen", return_value=process
        ) as popen, patch(
            "services.bridge_service.time.sleep"
        ), patch(
            "core.file_io.atomic_write_text"
        ):
            bridge.launch_jamulus(manual=True)
            bridge.launch_jamulus(manual=True)
            launch_workers = list(queued)
            self.assertEqual(len(launch_workers), 2)
            launch_workers[0]()
            launch_workers[1]()

        own_calls = [
            call for call in popen.call_args_list
            if "double-launch.example.com:22124" in call.args[0]
        ]
        self.assertEqual(len(own_calls), 1)


class TestBundledJamulusCandidate(unittest.TestCase):
    """macOS zero-install bundling: WebJam.app/Contents/Resources/Jamulus.app."""

    def test_returns_none_when_not_frozen(self):
        from services.bridge_service import _bundled_jamulus_candidate
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(_bundled_jamulus_candidate())

    def test_returns_none_on_non_macos_even_when_frozen(self):
        from services.bridge_service import _bundled_jamulus_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "win32"):
            self.assertIsNone(_bundled_jamulus_candidate())

    def test_returns_nested_binary_path_when_present(self):
        from services.bridge_service import _bundled_jamulus_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(sys, "executable",
                          "/Applications/WebJam.app/Contents/MacOS/WebJam"), \
             patch("pathlib.Path.is_file", return_value=True):
            result = _bundled_jamulus_candidate()
        self.assertEqual(
            result,
            "/Applications/WebJam.app/Contents/Resources/Jamulus.app"
            "/Contents/MacOS/Jamulus",
        )

    def test_returns_none_when_nested_app_missing(self):
        from services.bridge_service import _bundled_jamulus_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(sys, "executable",
                          "/Applications/WebJam.app/Contents/MacOS/WebJam"), \
             patch("pathlib.Path.is_file", return_value=False):
            self.assertIsNone(_bundled_jamulus_candidate())


class TestBundledJamulusServerCandidate(unittest.TestCase):
    """macOS host bundling: the dedicated signed server travels with WebJam."""

    def test_returns_none_when_not_frozen(self):
        from services.bridge_service import _bundled_jamulus_server_candidate
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(_bundled_jamulus_server_candidate())

    def test_returns_none_on_non_macos(self):
        from services.bridge_service import _bundled_jamulus_server_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "win32"):
            self.assertIsNone(_bundled_jamulus_server_candidate())

    def test_returns_nested_server_when_present(self):
        from services.bridge_service import _bundled_jamulus_server_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(
                 sys, "executable",
                 "/Applications/WebJam.app/Contents/MacOS/WebJam",
             ), patch("pathlib.Path.is_file", return_value=True):
            result = _bundled_jamulus_server_candidate()
        self.assertEqual(
            result,
            "/Applications/WebJam.app/Contents/Resources/JamulusServer.app"
            "/Contents/MacOS/JamulusServer",
        )

    def test_app_translocation_path_is_resolved_relative_to_executable(self):
        from services.bridge_service import _bundled_jamulus_server_candidate
        executable = (
            "/private/var/folders/x/AppTranslocation/UUID/d/WebJam.app/"
            "Contents/MacOS/WebJam"
        )
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(sys, "executable", executable), \
             patch("pathlib.Path.is_file", return_value=True):
            result = _bundled_jamulus_server_candidate()
        self.assertEqual(
            result,
            "/private/var/folders/x/AppTranslocation/UUID/d/WebJam.app/"
            "Contents/Resources/JamulusServer.app/Contents/MacOS/"
            "JamulusServer",
        )

    def test_returns_none_when_nested_server_missing(self):
        from services.bridge_service import _bundled_jamulus_server_candidate
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(
                 sys, "executable",
                 "/Applications/WebJam.app/Contents/MacOS/WebJam",
             ), patch("pathlib.Path.is_file", return_value=False):
            self.assertIsNone(_bundled_jamulus_server_candidate())

class TestBundledJamulusInstaller(unittest.TestCase):
    """Windows bundling: a Jamulus/ dir shipped next to WebJam.exe."""

    def test_returns_none_when_not_frozen(self):
        from services.bridge_service import _bundled_jamulus_installer
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(_bundled_jamulus_installer())

    def test_returns_none_on_non_windows_even_when_frozen(self):
        from services.bridge_service import _bundled_jamulus_installer
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "platform", "darwin"):
            self.assertIsNone(_bundled_jamulus_installer())

    def test_returns_installer_path_when_present(self):
        import tempfile
        from pathlib import Path
        from services.bridge_service import _bundled_jamulus_installer

        with tempfile.TemporaryDirectory() as tmp:
            # Resolve up front: sys.executable is resolved internally
            # (macOS commonly symlinks /var -> /private/var), so compare
            # against the same resolved path to avoid a false mismatch.
            app_dir = Path(tmp).resolve()
            jamulus_dir = app_dir / "Jamulus"
            jamulus_dir.mkdir()
            installer = jamulus_dir / "jamulus_3.12.2_win.exe"
            installer.write_bytes(b"stub")
            (jamulus_dir / "JAMULUS_COPYING.txt").write_text("license")
            exe_path = str(app_dir / "WebJam.exe")

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", "win32"), \
                 patch.object(sys, "executable", exe_path):
                result = _bundled_jamulus_installer()

        self.assertEqual(result, str(installer))

    def test_returns_none_when_jamulus_dir_missing(self):
        import tempfile
        from pathlib import Path
        from services.bridge_service import _bundled_jamulus_installer

        with tempfile.TemporaryDirectory() as tmp:
            exe_path = str(Path(tmp) / "WebJam.exe")
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", "win32"), \
                 patch.object(sys, "executable", exe_path):
                self.assertIsNone(_bundled_jamulus_installer())

    def test_returns_none_when_no_exe_matches_pattern(self):
        import tempfile
        from pathlib import Path
        from services.bridge_service import _bundled_jamulus_installer

        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            jamulus_dir = app_dir / "Jamulus"
            jamulus_dir.mkdir()
            (jamulus_dir / "JAMULUS_COPYING.txt").write_text("license")
            exe_path = str(app_dir / "WebJam.exe")

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", "win32"), \
                 patch.object(sys, "executable", exe_path):
                self.assertIsNone(_bundled_jamulus_installer())


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
        with patch("core.settings.AppSettings", return_value=default_settings), \
             patch("services.bridge_service._bundled_jamulus_candidate",
                   return_value=None):
            self.assertIsNone(bridge.find_jamulus())

    def test_falls_back_to_bundled_candidate_as_last_resort(self):
        """A fresh macOS install with no configured paths should still find
        the copy of Jamulus bundled inside WebJam.app itself."""
        bridge = _make_bridge()
        bridge.settings.jamulus_candidates = ["/nonexistent/custom"]
        default_settings = MagicMock()
        default_settings.jamulus_candidates = ["/nonexistent/default"]
        with patch("core.settings.AppSettings", return_value=default_settings), \
             patch("services.bridge_service._bundled_jamulus_candidate",
                   return_value="/Applications/WebJam.app/Contents/Resources"
                                "/Jamulus.app/Contents/MacOS/Jamulus"):
            self.assertEqual(
                bridge.find_jamulus(),
                "/Applications/WebJam.app/Contents/Resources/Jamulus.app"
                "/Contents/MacOS/Jamulus",
            )


@patch("services.bridge_service.time.sleep")
@patch("services.bridge_service.threading.Thread",
       side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
class TestLaunchWebexFailure(unittest.TestCase):
    def test_manual_open_failure_shows_error(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting.return_value = False

        bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Open failed")
        self.assertEqual(bridge.webex_controller.join_meeting.call_count, 1)
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0], "Webex Open Failed"
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_webex_open_failed"
        )

    def test_legacy_reconnect_argument_does_not_hide_failure(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting.side_effect = RuntimeError("boom")

        bridge.launch_webex(manual=False, reconnect=True)

        self.assertEqual(bridge.webex_state, "Open failed")
        bridge.show_actionable_error.assert_called_once()
        bridge.metrics_service.increment.assert_any_call("metric_webex_open_failed")


class TestWebexReconnectGating(unittest.TestCase):
    def test_periodic_reconnect_tick_never_reopens_external_webex(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = False
        bridge.webex_state = "Open failed"
        bridge.launch_webex = MagicMock()

        bridge.attempt_auto_reconnects()

        bridge.launch_webex.assert_not_called()


if __name__ == "__main__":
    unittest.main()

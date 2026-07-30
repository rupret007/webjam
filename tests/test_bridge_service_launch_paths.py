"""
BridgeService launch/failure path coverage.

Exercises the launch_jamulus not-found and already-running branches, the
_do_launch failure path (Popen retries exhausted) for both manual and
reconnect flavours, the JSON-RPC secret-file contract on the Jamulus command
line, find_jamulus fallback resolution, and the Webex open-failure and
reconnect-gating branches.  No real processes are spawned.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from tests.support.component_store import isolated_component_store_root


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
    s.musician_name = "Private Musician"
    s.host_server_enabled = False
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
        component_store_root=isolated_component_store_root(),
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

    @patch("services.bridge_service.subprocess.Popen")
    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    def test_force_restart_replaces_hung_alive_process(self, _thread, popen):
        old_proc = MagicMock()
        old_proc.poll.return_value = None
        new_proc = MagicMock()
        new_proc.poll.return_value = None
        popen.return_value = new_proc
        bridge = _make_bridge()
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge.jamulus_process = old_proc
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch("pathlib.Path.is_file", return_value=True):
            bridge.launch_jamulus(manual=False, reconnect=True, force_restart=True)

        old_proc.terminate.assert_called_once()
        bridge.jamulus_controller.stop.assert_called_once()
        self.assertEqual(bridge.jamulus_state, "Running")
        self.assertIs(bridge.jamulus_process, new_proc)


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

    def _launch_and_capture_environment(self, bridge):
        captured = {}
        marker = f"{bridge.settings.jamulus_server}:{bridge.settings.jamulus_port}"
        process = MagicMock()
        process.poll.return_value = None

        def _fake_popen(cmd, **kwargs):
            if marker in cmd:
                captured["env"] = kwargs["env"]
            return process

        with patch(
            "services.bridge_service.subprocess.Popen",
            side_effect=_fake_popen,
        ), patch("services.bridge_service.time.sleep"):
            bridge.launch_jamulus(manual=True, reconnect=False)
        return captured.get("env", {})

    def test_cmd_includes_connect_rpc_port_and_secret_file(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "contract-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        cmd = self._launch_and_capture_cmd(bridge)

        self.assertEqual(cmd[0], "/usr/bin/jamulus")
        # The real Jamulus window is the native sound setup; WebJam must not
        # hide it behind a headless launch.
        self.assertNotIn("--nogui", cmd)
        self.assertIn("--connect", cmd)
        self.assertIn("contract-probe.example.com:22124", cmd)
        self.assertIn("--jsonrpcport", cmd)
        self.assertIn("22222", cmd)
        self.assertIn("--jsonrpcsecretfile", cmd)
        from core.jamulus_rpc_client import DEFAULT_SECRET_PATH
        secret_index = cmd.index("--jsonrpcsecretfile") + 1
        self.assertEqual(cmd[secret_index], str(DEFAULT_SECRET_PATH))
        self.assertEqual(bridge.jamulus_state, "Running")

    def test_v3_guest_keeps_musician_name_out_of_process_arguments(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "127.0.0.1"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        bridge.enable_remote_guest_mode()

        cmd = self._launch_and_capture_cmd(bridge)

        self.assertNotIn("--clientname", cmd)
        self.assertNotIn("Private Musician", cmd)

    def test_legacy_guest_keeps_existing_clientname_contract(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "legacy.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        cmd = self._launch_and_capture_cmd(bridge)

        self.assertEqual(
            cmd[cmd.index("--clientname") + 1],
            "Private Musician",
        )

    def test_invalid_musician_name_never_reaches_process_arguments(
        self, _thread
    ):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "legacy.example.com"
        bridge.settings.musician_name = "12345678901234567"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch("services.bridge_service.subprocess.Popen") as popen:
            bridge.launch_jamulus(manual=True)

        popen.assert_not_called()
        self.assertEqual(bridge.jamulus_state, "Launch failed")

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

    def test_macos_child_uses_native_ui_and_suppresses_late_qt_warning(
        self, _thread
    ):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "native-ui-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch.dict(
            os.environ,
            {
                "QT_QPA_PLATFORM": "offscreen",
                "QT_LOGGING_RULES": (
                    "jamulus.rpc.debug=true;default.warning=true;"
                ),
                "QT_FORCE_STDERR_LOGGING": "preserve-me",
            },
        ), patch(
            "services.bridge_service.sys.platform", "darwin"
        ):
            captured = self._launch_and_capture_environment(bridge)

        assert "QT_QPA_PLATFORM" not in captured
        assert captured["QT_LOGGING_RULES"] == (
            "jamulus.rpc.debug=true;default.warning=true;default.warning=false"
        )
        assert captured["QT_FORCE_STDERR_LOGGING"] == "preserve-me"

    def test_macos_child_adds_qt_warning_rule_when_none_exists(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "native-log-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        with patch.dict(
            os.environ,
            {"QT_LOGGING_RULES": ""},
        ), patch(
            "services.bridge_service.sys.platform", "darwin"
        ):
            captured = self._launch_and_capture_environment(bridge)

        assert captured["QT_LOGGING_RULES"] == "default.warning=false"

    def test_non_macos_child_preserves_qt_logging_rules(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "portable-log-probe.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        inherited = "jamulus.rpc.debug=true;default.warning=true"
        with patch.dict(
            os.environ,
            {"QT_LOGGING_RULES": inherited},
        ), patch(
            "services.bridge_service.sys.platform", "linux"
        ):
            captured = self._launch_and_capture_environment(bridge)

        assert captured["QT_LOGGING_RULES"] == inherited

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

    def test_stop_before_queued_worker_prevents_any_client_process(self, _thread):
        bridge = _make_bridge()
        bridge.settings.jamulus_server = "cancel-before-popen.example.com"
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        queued = []

        class _QueuedThread:
            def __init__(self, *args, target=None, **_kwargs):
                self._target = target

            def start(self):
                queued.append(self._target)

        with patch(
            "services.bridge_service.threading.Thread", _QueuedThread
        ), patch("services.bridge_service.subprocess.Popen") as popen:
            assert bridge.launch_jamulus(manual=True) is True
            assert len(queued) == 1
            assert bridge.stop_jamulus() is True
            queued[0]()

        popen.assert_not_called()


class TestHostedServerCancellation(unittest.TestCase):
    def test_cancelled_host_start_does_not_spawn_a_server(self):
        bridge = _make_bridge()
        bridge.settings.host_server_enabled = True

        with patch("services.bridge_service.subprocess.Popen") as popen:
            ok, detail = bridge.ensure_hosted_server(cancel_requested=lambda: True)

        self.assertFalse(ok)
        self.assertEqual(detail, "Startup was cancelled.")
        popen.assert_not_called()


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
    """Windows bundling: a Jamulus/ dir in PyInstaller's frozen data root."""

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
                 patch.object(sys, "executable", exe_path), \
                 patch(
                     "services.bridge_service._PINNED_WINDOWS_JAMULUS_SHA256",
                     hashlib.sha256(b"stub").hexdigest(),
                 ):
                result = _bundled_jamulus_installer()

        self.assertEqual(result, str(installer))

    def test_returns_installer_from_pyinstaller_internal_data_root(self):
        import tempfile
        from pathlib import Path
        from services.bridge_service import _bundled_jamulus_installer

        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp).resolve()
            internal = app_dir / "_internal"
            jamulus_dir = internal / "Jamulus"
            jamulus_dir.mkdir(parents=True)
            installer = jamulus_dir / "jamulus_3.12.2_win.exe"
            installer.write_bytes(b"stub")
            exe_path = str(app_dir / "WebJam.exe")

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", "win32"), \
                 patch.object(sys, "executable", exe_path), \
                 patch.object(sys, "_MEIPASS", str(internal), create=True), \
                 patch(
                     "services.bridge_service._PINNED_WINDOWS_JAMULUS_SHA256",
                     hashlib.sha256(b"stub").hexdigest(),
                 ):
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

    def test_returns_none_when_exact_installer_is_missing(self):
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

    def test_rejects_wrong_hash_and_never_selects_an_injected_wildcard(self):
        import tempfile
        from pathlib import Path
        from services.bridge_service import _bundled_jamulus_installer

        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp).resolve()
            jamulus_dir = app_dir / "Jamulus"
            jamulus_dir.mkdir()
            (jamulus_dir / "jamulus_0_win.exe").write_bytes(b"injected")
            installer = jamulus_dir / "jamulus_3.12.2_win.exe"
            installer.write_bytes(b"replaced")
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
            Path(fake_binary.name).chmod(0o700)
            bridge.settings.jamulus_candidates = [fake_binary.name]
            with patch(
                "services.bridge_service.default_jamulus_version_probe",
                return_value="3.12.2",
            ):
                self.assertEqual(
                    bridge.find_jamulus(),
                    str(Path(fake_binary.name).resolve()),
                )
            assert Path(fake_binary.name).exists()

    def test_falls_back_to_default_candidates(self):
        import tempfile
        bridge = _make_bridge()
        bridge.settings.jamulus_candidates = ["/nonexistent/custom/jamulus"]
        with tempfile.NamedTemporaryFile(suffix="-jamulus") as fake_binary:
            from pathlib import Path

            Path(fake_binary.name).chmod(0o700)
            default_settings = MagicMock()
            default_settings.jamulus_candidates = [fake_binary.name]
            with patch("core.settings.AppSettings",
                       return_value=default_settings), patch(
                "services.bridge_service.default_jamulus_version_probe",
                return_value="3.12.2",
            ):
                self.assertEqual(
                    bridge.find_jamulus(),
                    str(Path(fake_binary.name).resolve()),
                )

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


class TestFindReferenceTrackJamulus(unittest.TestCase):
    def _frozen_fixture(self):
        import tempfile
        from pathlib import Path

        temporary = tempfile.TemporaryDirectory()
        app = Path(temporary.name) / "WebJam.app"
        executable = app / "Contents" / "MacOS" / "WebJam"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"outer")
        resources = app / "Contents" / "Resources"
        companion = (
            resources
            / "JamulusHeadlessClient.app"
            / "Contents"
            / "MacOS"
            / "JamulusHeadlessClient"
        )
        companion.parent.mkdir(parents=True)
        companion.write_bytes(b"true headless fixture")
        companion.chmod(0o700)
        digest = hashlib.sha256(companion.read_bytes()).hexdigest()
        manifest = resources / "JamulusHeadlessClient.sha256"
        manifest.write_text(
            f"{digest}  "
            "JamulusHeadlessClient.app/Contents/MacOS/"
            "JamulusHeadlessClient\n",
            encoding="ascii",
        )
        return temporary, executable, companion, manifest

    def test_frozen_mac_resolves_only_checksum_verified_companion(self):
        from services.bridge_service import (
            _bundled_reference_track_jamulus_candidate,
        )

        temporary, executable, companion, _manifest = self._frozen_fixture()
        try:
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "darwin"),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(
                    _bundled_reference_track_jamulus_candidate(),
                    str(companion.resolve()),
                )
                companion.write_bytes(b"replaced")
                companion.chmod(0o700)
                self.assertIsNone(
                    _bundled_reference_track_jamulus_candidate()
                )
        finally:
            temporary.cleanup()

    def test_malformed_manifest_and_source_run_have_no_fallback(self):
        from services.bridge_service import (
            _bundled_reference_track_jamulus_candidate,
        )

        temporary, executable, _companion, manifest = self._frozen_fixture()
        bridge = _make_bridge()
        try:
            manifest.write_text(
                "0" * 64 + "  Jamulus.app/Contents/MacOS/Jamulus\n",
                encoding="ascii",
            )
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "darwin"),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertIsNone(
                    _bundled_reference_track_jamulus_candidate()
                )
                self.assertIsNone(bridge.find_reference_track_jamulus())
            with patch.object(sys, "frozen", False, create=True):
                self.assertIsNone(
                    _bundled_reference_track_jamulus_candidate()
                )
        finally:
            temporary.cleanup()


@patch("services.bridge_service.time.sleep")
@patch("services.bridge_service.threading.Thread",
       side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
class TestLaunchWebexFailure(unittest.TestCase):
    def test_manual_open_failure_shows_error(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting_url.return_value = False
        bridge.webex_event = MagicMock()

        bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Open failed")
        self.assertEqual(bridge.webex_controller.join_meeting_url.call_count, 1)
        bridge.show_actionable_error.assert_called_once()
        self.assertEqual(
            bridge.show_actionable_error.call_args.args[0], "Webex Open Failed"
        )
        bridge.metrics_service.increment.assert_any_call(
            "metric_webex_open_failed"
        )
        bridge.webex_event.assert_called_once_with(
            "meeting-handoff",
            "open-failed",
        )
        self.assertNotIn(
            "webex.com",
            str(bridge.webex_event.call_args).lower(),
        )

    def test_success_reports_only_a_finite_handoff_result(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting_url.return_value = True
        bridge.webex_event = MagicMock()

        bridge.launch_webex(manual=True)

        self.assertEqual(bridge.webex_state, "Opened externally")
        bridge.webex_event.assert_called_once_with(
            "meeting-handoff",
            "opened-externally",
        )
        self.assertNotIn(
            "webex.com",
            str(bridge.webex_event.call_args).lower(),
        )

    def test_legacy_reconnect_argument_does_not_hide_failure(self, _thread, _sleep):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting_url.side_effect = RuntimeError("boom")

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

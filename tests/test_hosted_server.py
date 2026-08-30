"""
In-app band-server hosting — BridgeService.ensure_hosted_server and friends.

WebJam supervises the official JamulusServer.app (recording + loopback RPC)
when settings.host_server_enabled is set, replacing the manual
server/start_macos_pilot.sh Terminal step. No real processes are spawned.
"""
from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.jamulus_compatibility import ComponentTarget, JamulusRole
from tests.support.component_store import isolated_component_store_root

pytestmark = pytest.mark.requires_local_socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

@pytest.fixture(autouse=True)
def _authorize_microphone_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        lambda: "authorized",
    )


def _make_bridge(tmp: str):
    from services.bridge_service import BridgeService

    settings = MagicMock()
    settings.jamulus_server = "127.0.0.1"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_port = 22240
    settings.server_rpc_secret_file = str(Path(tmp) / "rpc.secret")
    settings.takes_directory = str(Path(tmp) / "Recordings")
    settings.host_server_enabled = True
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
        component_store_root=isolated_component_store_root(),
    )
    # These process-supervision tests use a mocked generic installed runtime.
    # Pin their compatibility seam to a platform where official installed
    # binaries are executable; upstream macOS apps are source evidence only.
    # Dedicated tests cover the release-integrated Mac fallback.
    bridge._jamulus_component_target = ComponentTarget.WINDOWS_X64
    # Exercise the approved v0.27.2 baked-component boundary. The sealed
    # public catalog remains independently pinned to exact WebJam v0.22.5.
    bridge._runtime_webjam_version = MagicMock(return_value="0.27.2")
    bridge.find_jamulus_server = MagicMock(
        return_value="/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
    )
    bridge.find_jamulus_server_with_source = MagicMock(return_value=(
        "/Applications/JamulusServer.app/Contents/MacOS/JamulusServer",
        "installed",
    ))
    return bridge


def _version_ok():
    return SimpleNamespace(stdout="Jamulus, Version 3.12.2", stderr="")


class TestEnsureHostedServer(unittest.TestCase):
    def test_spawns_server_with_validated_flag_set_and_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            fake_proc.pid = 4242
            with patch("services.bridge_service.subprocess.run",
                       return_value=_version_ok()), \
                 patch("services.bridge_service.subprocess.Popen",
                       return_value=fake_proc) as popen, \
                 patch.object(bridge, "_port_free", return_value=True), \
                 patch.object(bridge, "_probe_hosted_server_rpc",
                              return_value=(True, "ready")):
                ok, detail = bridge.ensure_hosted_server()
            self.assertTrue(ok, detail)
            cmd = popen.call_args_list[0].args[0]
            self.assertIn("--nogui", cmd)
            self.assertIn("--recording", cmd)
            self.assertIn("--norecord", cmd)
            self.assertIn("--jsonrpcbindip", cmd)
            self.assertIn("127.0.0.1", cmd)
            self.assertIn("--jsonrpcsecretfile", cmd)
            self.assertIn("22124", cmd)
            self.assertIn("22240", cmd)
            secret = Path(tmp) / "rpc.secret"
            self.assertTrue(secret.is_file())
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertTrue((Path(tmp) / "Recordings").is_dir())

    def test_adopts_external_server_when_rpc_port_already_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            with patch.object(bridge, "_port_free", return_value=False), \
                 patch.object(bridge, "_probe_hosted_server_rpc",
                              return_value=(True, "ready")), \
                 patch("services.bridge_service.subprocess.Popen") as popen:
                ok, detail = bridge.ensure_hosted_server()
            self.assertTrue(ok)
            self.assertIn("adopted", detail)
            self.assertTrue(bridge.hosted_server_adopted())
            self.assertTrue(bridge.hosted_server_alive())
            self.assertFalse(bridge.hosted_server_owned())
            popen.assert_not_called()

    def test_refuses_unverified_listener_instead_of_blind_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            with patch.object(bridge, "_port_free", return_value=False), \
                 patch.object(
                     bridge, "_probe_hosted_server_rpc",
                     return_value=(False, "authentication failed"),
                 ), patch("services.bridge_service.subprocess.Popen") as popen:
                ok, detail = bridge.ensure_hosted_server()
            self.assertFalse(ok)
            self.assertIn("could not verify", detail)
            self.assertFalse(bridge.hosted_server_alive())
            popen.assert_not_called()

    def test_refuses_recorder_when_expected_audio_udp_port_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            with patch.object(
                bridge, "_port_free", side_effect=[False, True]
            ), patch.object(
                bridge, "_probe_hosted_server_rpc", return_value=(True, "ready")
            ):
                ok, detail = bridge.ensure_hosted_server()
            self.assertFalse(ok)
            self.assertIn("UDP 22124", detail)
            self.assertFalse(bridge.hosted_server_adopted())

    def test_existing_secret_permissions_are_hardened(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            secret = Path(tmp) / "rpc.secret"
            secret.write_text("existing-secret\n", encoding="utf-8")
            secret.chmod(0o644)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            fake_proc.pid = 4242
            with patch("services.bridge_service.subprocess.run",
                       return_value=_version_ok()), \
                 patch("services.bridge_service.subprocess.Popen",
                       return_value=fake_proc), \
                 patch.object(bridge, "_port_free", return_value=True), \
                 patch.object(bridge, "_probe_hosted_server_rpc",
                              return_value=(True, "ready")):
                ok, detail = bridge.ensure_hosted_server()
            self.assertTrue(ok, detail)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            bridge.stop_hosted_server()

    def test_wrong_version_refuses_to_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            with patch.object(bridge, "_port_free", return_value=True), \
                 patch("services.bridge_service.subprocess.run",
                       return_value=SimpleNamespace(
                           stdout="Jamulus, Version 3.13.0", stderr="")), \
                 patch("services.bridge_service.subprocess.Popen") as popen:
                ok, detail = bridge.ensure_hosted_server()
            self.assertFalse(ok)
            self.assertIn("3.12.2", detail)
            popen.assert_not_called()

    def test_missing_server_app_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            bridge.find_jamulus_server = MagicMock(return_value=None)
            with patch.object(bridge, "_port_free", return_value=True):
                ok, detail = bridge.ensure_hosted_server()
            self.assertFalse(ok)
            self.assertIn("JamulusServer.app", detail)

    def test_stop_jamulus_leaves_hosted_server_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            bridge.hosted_server_process = fake_proc
            bridge.stop_jamulus()
            fake_proc.terminate.assert_not_called()
            self.assertTrue(bridge.hosted_server_alive())

    def test_stop_hosted_server_terminates_server_and_caffeinate(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            caff = MagicMock()
            caff.poll.return_value = None
            bridge.hosted_server_process = fake_proc
            bridge._hosted_caffeinate_process = caff
            bridge.stop_hosted_server()
            fake_proc.terminate.assert_called_once()
            caff.terminate.assert_called_once()
            self.assertFalse(bridge.hosted_server_alive())
            # Idempotent.
            bridge.stop_hosted_server()
            fake_proc.terminate.assert_called_once()

    def test_stop_hosted_server_only_detaches_an_adopted_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            bridge._hosted_server_adopted = True
            bridge.stop_hosted_server()
            self.assertFalse(bridge.hosted_server_alive())

    def test_stop_hosted_server_retains_process_when_termination_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            fake_proc.terminate.side_effect = OSError("not permitted")
            bridge.hosted_server_process = fake_proc

            self.assertFalse(bridge.stop_hosted_server())
            self.assertIs(bridge.hosted_server_process, fake_proc)
            self.assertTrue(bridge.hosted_server_alive())

    def test_rpc_readiness_timeout_cleans_up_spawned_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            fake_proc.pid = 4242
            with patch("services.bridge_service.subprocess.run",
                       return_value=_version_ok()), \
                 patch("services.bridge_service.subprocess.Popen",
                       return_value=fake_proc), \
                 patch.object(bridge, "_port_free", return_value=True), \
                 patch.object(bridge, "_probe_hosted_server_rpc",
                              return_value=(False, "not ready")), \
                 patch.object(bridge, "_start_hosted_caffeinate"), \
                 patch("services.bridge_service.time.monotonic",
                       side_effect=[0.0, 0.0, 7.0]), \
                 patch("services.bridge_service.time.sleep"):
                ok, detail = bridge.ensure_hosted_server()
            self.assertFalse(ok)
            self.assertIn("never became", detail)
            fake_proc.terminate.assert_called_once()
            self.assertFalse(bridge.hosted_server_alive())

    def test_dead_hosted_server_is_restarted_by_reconnect_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            dead = MagicMock()
            dead.poll.return_value = 1
            bridge.hosted_server_process = dead
            bridge.jamulus_launch_intended = True
            with patch.object(bridge, "ensure_hosted_server",
                              return_value=(True, "started")) as ensure, \
                 patch("services.bridge_service.threading.Thread",
                       side_effect=lambda *a, **kw: _Immediate(*a, **kw)):
                bridge._restart_hosted_server_if_died()
            ensure.assert_called_once()
            self.assertFalse(bridge._hosted_restart_inflight)

    def test_queued_hosted_restart_cannot_resurrect_after_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            dead = MagicMock()
            dead.poll.return_value = 1
            bridge.hosted_server_process = dead
            bridge.jamulus_launch_intended = True
            with patch.object(bridge, "ensure_hosted_server") as ensure, patch(
                "services.bridge_service.threading.Thread"
            ) as thread_class:
                bridge._restart_hosted_server_if_died()
                restart_worker = thread_class.call_args.kwargs["target"]

                # End/Leave retires client intent and then the hosted owner.
                self.assertTrue(bridge.stop_jamulus())
                self.assertTrue(bridge.stop_hosted_server())
                restart_worker()

            ensure.assert_not_called()
            self.assertFalse(bridge.jamulus_launch_intended)
            self.assertIsNone(bridge.hosted_server_process)
            self.assertIsNone(bridge._pending_hosted_restart_cancel)
            self.assertFalse(bridge._hosted_restart_inflight)

    def test_no_restart_when_hosting_disabled_or_not_intended(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            dead = MagicMock()
            dead.poll.return_value = 1
            bridge.hosted_server_process = dead
            bridge.jamulus_launch_intended = False
            with patch.object(bridge, "ensure_hosted_server") as ensure:
                bridge._restart_hosted_server_if_died()
            ensure.assert_not_called()

    def test_no_supervision_loop_after_initial_start_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            bridge.jamulus_launch_intended = True
            bridge.hosted_server_process = None
            with patch.object(bridge, "ensure_hosted_server") as ensure:
                bridge._restart_hosted_server_if_died()
            ensure.assert_not_called()

    def test_hosted_restart_runs_even_when_client_auto_reconnect_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            bridge.repository.get_setting.return_value = "0"
            bridge.jamulus_launch_intended = True
            dead = MagicMock()
            dead.poll.return_value = 1
            bridge.hosted_server_process = dead
            with patch.object(bridge, "_restart_hosted_server_if_died") as restart:
                bridge.attempt_auto_reconnects()
            restart.assert_called_once()

    def test_hosted_effective_server_is_always_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            bridge.settings.jamulus_server = "public.example.com"
            self.assertEqual(bridge.effective_server(), "127.0.0.1:22124")

    def test_band_check_certifies_real_owned_lifecycle_and_releases_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            running = [True]
            fake_proc = MagicMock()
            fake_proc.pid = 4242
            fake_proc.poll.side_effect = lambda: None if running[0] else 0

            def stopped(*_args, **_kwargs):
                running[0] = False
                return 0

            fake_proc.wait.side_effect = stopped

            def port_free(_port, *, udp=False):
                del udp
                return not (
                    bridge.hosted_server_process is fake_proc and running[0]
                )

            with patch(
                "services.bridge_service.subprocess.run",
                return_value=_version_ok(),
            ), patch(
                "services.bridge_service.subprocess.Popen",
                return_value=fake_proc,
            ), patch.object(
                bridge, "_port_free", side_effect=port_free
            ), patch.object(
                bridge, "_probe_hosted_server_rpc", return_value=(True, "ready")
            ), patch.object(bridge, "_start_hosted_caffeinate"):
                result = bridge.certify_hosted_server_lifecycle()

            self.assertTrue(result.ok, result.detail)
            self.assertFalse(result.warning)
            self.assertTrue(result.started_owned_server)
            self.assertTrue(result.recorder_authenticated)
            self.assertTrue(result.secret_private)
            self.assertTrue(result.owned_stop_confirmed)
            self.assertTrue(result.ports_released)
            self.assertIn("3.12.2", result.detail)
            self.assertFalse(bridge.hosted_server_alive())
            fake_proc.terminate.assert_called_once()
            secret = Path(tmp) / "rpc.secret"
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_band_check_reports_port_conflict_without_spawning_or_killing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            with patch.object(
                bridge, "_port_free", return_value=False
            ), patch.object(
                bridge,
                "_probe_hosted_server_rpc",
                return_value=(False, "authentication failed"),
            ), patch("services.bridge_service.subprocess.Popen") as popen:
                result = bridge.certify_hosted_server_lifecycle()
            self.assertFalse(result.ok)
            self.assertIn("already in use", result.detail)
            self.assertFalse(bridge.hosted_server_alive())
            popen.assert_not_called()

    def test_band_check_auth_failure_cleans_up_the_owned_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            running = [True]
            fake_proc = MagicMock()
            fake_proc.pid = 4242
            fake_proc.poll.side_effect = lambda: None if running[0] else 0

            def stopped(*_args, **_kwargs):
                running[0] = False
                return 0

            fake_proc.wait.side_effect = stopped

            def port_free(_port, *, udp=False):
                del udp
                return not (
                    bridge.hosted_server_process is fake_proc and running[0]
                )

            with patch(
                "services.bridge_service.subprocess.run",
                return_value=_version_ok(),
            ), patch(
                "services.bridge_service.subprocess.Popen",
                return_value=fake_proc,
            ), patch.object(
                bridge, "_port_free", side_effect=port_free
            ), patch.object(
                bridge,
                "_probe_hosted_server_rpc",
                return_value=(False, "authentication failed"),
            ), patch.object(bridge, "_start_hosted_caffeinate"), patch(
                "services.bridge_service.time.monotonic",
                side_effect=[0.0, 0.0, 7.0],
            ), patch("services.bridge_service.time.sleep"):
                result = bridge.certify_hosted_server_lifecycle()
            self.assertFalse(result.ok)
            self.assertIn("never became", result.detail)
            fake_proc.terminate.assert_called_once()
            self.assertFalse(bridge.hosted_server_alive())

    def test_band_check_does_not_stop_a_preexisting_owned_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            fake_proc = MagicMock()
            fake_proc.poll.return_value = None
            bridge.hosted_server_process = fake_proc
            with patch.object(
                bridge, "ensure_hosted_server", return_value=(True, "already running")
            ), patch.object(
                bridge, "_probe_hosted_server_rpc", return_value=(True, "ready")
            ), patch.object(
                bridge, "_hosted_secret_is_private", return_value=(True, "0600")
            ), patch.object(bridge, "_port_free", return_value=False):
                result = bridge.certify_hosted_server_lifecycle()
            # The process predated Band Check, so it is observed but never
            # terminated. Its full start/stop lifecycle cannot be certified.
            self.assertTrue(result.ok)
            self.assertTrue(result.warning)
            fake_proc.terminate.assert_not_called()

    def test_band_check_fails_closed_when_owned_stop_does_not_release_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            running = [True]
            fake_proc = MagicMock()
            fake_proc.poll.side_effect = lambda: None if running[0] else 0

            def stopped(*_args, **_kwargs):
                running[0] = False
                return 0

            fake_proc.wait.side_effect = stopped

            def started():
                bridge.hosted_server_process = fake_proc
                return True, "started from installed app"

            with patch.object(
                bridge, "ensure_hosted_server", side_effect=started
            ), patch.object(
                bridge, "_probe_hosted_server_rpc", return_value=(True, "ready")
            ), patch.object(
                bridge, "_hosted_secret_is_private", return_value=(True, "0600")
            ), patch.object(
                bridge, "_port_free", return_value=False
            ), patch.object(
                bridge, "_wait_for_hosted_ports_release", return_value=False
            ):
                result = bridge.certify_hosted_server_lifecycle()
            self.assertFalse(result.ok)
            self.assertTrue(result.owned_stop_confirmed)
            self.assertFalse(result.ports_released)
            self.assertIn("not released", result.detail)
            self.assertFalse(bridge.hosted_server_alive())

    def test_band_check_authenticates_but_never_stops_external_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            secret = Path(tmp) / "rpc.secret"
            secret.write_text("external-secret\n", encoding="utf-8")
            secret.chmod(0o644)
            with patch.object(
                bridge, "_port_free", return_value=False
            ), patch.object(
                bridge, "_probe_hosted_server_rpc", return_value=(True, "ready")
            ), patch("services.bridge_service.subprocess.Popen") as popen:
                result = bridge.certify_hosted_server_lifecycle()
            self.assertTrue(result.ok, result.detail)
            self.assertTrue(result.warning)
            self.assertTrue(result.adopted_external_server)
            self.assertIn("externally managed", result.detail)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertFalse(bridge.hosted_server_alive())
            popen.assert_not_called()

    def test_band_check_unexpected_failure_leaves_no_owned_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _make_bridge(tmp)
            running = [True]
            fake_proc = MagicMock()
            fake_proc.poll.side_effect = lambda: None if running[0] else 0

            def stopped(*_args, **_kwargs):
                running[0] = False
                return 0

            fake_proc.wait.side_effect = stopped
            private_path = Path(tmp) / "Recordings" / "private-recorder.path"

            def raise_after_spawn():
                bridge.hosted_server_process = fake_proc
                raise FileNotFoundError(
                    errno.ENOENT,
                    "synthetic recorder failure",
                    private_path,
                )

            with self.assertLogs(
                "webjam.services.bridge",
                level="ERROR",
            ) as captured:
                with patch.object(
                    bridge, "ensure_hosted_server", side_effect=raise_after_spawn
                ), patch.object(
                    bridge, "_wait_for_hosted_ports_release", return_value=True
                ):
                    result = bridge.certify_hosted_server_lifecycle()
            self.assertFalse(result.ok)
            self.assertIn("failed before it could complete", result.detail)
            combined = "\n".join(
                (
                    result.detail,
                    *result.technical_details,
                    *captured.output,
                )
            )
            self.assertNotIn(str(private_path), combined)
            self.assertNotIn("synthetic recorder failure", combined)
            self.assertIn(
                "certification_error_type=FileNotFoundError",
                result.technical_details,
            )
            fake_proc.terminate.assert_called_once()
            self.assertFalse(bridge.hosted_server_alive())


class TestHostedServerDiscovery(unittest.TestCase):
    def _real_discovery_bridge(self, tmp):
        bridge = _make_bridge(tmp)
        del bridge.find_jamulus_server_with_source
        return bridge

    def test_bundled_server_precedes_installed_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._real_discovery_bridge(tmp)
            with patch.object(Path, "is_file", return_value=True), patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value="/bundled/JamulusServer",
            ) as bundled:
                result = bridge.find_jamulus_server_with_source()
            self.assertEqual(result, ("/bundled/JamulusServer", "bundled"))
            bundled.assert_called_once()

    def test_v0272_source_resolves_the_bundled_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._real_discovery_bridge(tmp)
            del bridge._runtime_webjam_version
            with patch.object(Path, "is_file", return_value=True), patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value="/bundled/JamulusServer",
            ):
                self.assertEqual(bridge._runtime_webjam_version(), "0.27.2")
                self.assertEqual(
                    bridge._approved_embedded_runtime_versions(
                        JamulusRole.SERVER
                    ),
                    frozenset({"3.12.2", "3.12.3"}),
                )
                self.assertEqual(
                    bridge.find_jamulus_server_with_source(),
                    ("/bundled/JamulusServer", "bundled"),
                )

    def test_future_source_still_rejects_the_bundled_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._real_discovery_bridge(tmp)
            bridge._runtime_webjam_version = MagicMock(return_value="0.27.3")
            with patch.object(Path, "is_file", return_value=True), patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value="/bundled/JamulusServer",
            ):
                self.assertEqual(
                    bridge._approved_embedded_runtime_versions(
                        JamulusRole.SERVER
                    ),
                    frozenset(),
                )
                self.assertEqual(
                    bridge.find_jamulus_server_with_source(),
                    (None, "missing"),
                )

    def test_bundled_server_is_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._real_discovery_bridge(tmp)
            with patch.object(Path, "is_file", return_value=False), patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value="/bundled/JamulusServer",
            ):
                result = bridge.find_jamulus_server_with_source()
            self.assertEqual(result, ("/bundled/JamulusServer", "bundled"))

    def test_missing_server_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._real_discovery_bridge(tmp)
            with patch.object(Path, "is_file", return_value=False), patch(
                "services.bridge_service._bundled_jamulus_server_candidate",
                return_value=None,
            ):
                result = bridge.find_jamulus_server_with_source()
            self.assertEqual(result, (None, "missing"))


class _Immediate:
    def __init__(self, *args, target=None, daemon=None, name=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class TestHostedSettings(unittest.TestCase):
    def test_hosting_derives_container_defaults_when_unset(self):
        import json
        from core.settings import (
            hosted_server_recordings_dir,
            hosted_server_secret_path,
            load_settings,
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({
                "jamulus_server": "127.0.0.1",
                "webex_url": "https://x.webex.com/meet/y",
                "host_server_enabled": True,
            }))
            s = load_settings(str(cfg))
            self.assertTrue(s.host_server_enabled)
            self.assertEqual(s.jamulus_server, "127.0.0.1")
            self.assertEqual(
                Path(s.server_rpc_secret_file),
                hosted_server_secret_path(),
            )
            self.assertEqual(
                Path(s.takes_directory),
                hosted_server_recordings_dir(),
            )

    def test_hosting_replaces_incompatible_explicit_paths_with_container_paths(self):
        import json
        from core.settings import (
            hosted_server_recordings_dir,
            hosted_server_secret_path,
            load_settings,
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({
                "jamulus_server": "127.0.0.1",
                "host_server_enabled": True,
                "server_rpc_secret_file": "/custom/secret",
                "takes_directory": "/custom/takes",
            }))
            s = load_settings(str(cfg))
            self.assertEqual(
                Path(s.server_rpc_secret_file),
                hosted_server_secret_path(),
            )
            self.assertEqual(
                Path(s.takes_directory),
                hosted_server_recordings_dir(),
            )
            self.assertNotEqual(s.server_rpc_secret_file, "/custom/secret")
            self.assertNotEqual(s.takes_directory, "/custom/takes")

    def test_env_var_enables_hosting(self):
        from core.settings import load_settings
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WEBJAM_HOST_SERVER_ENABLED": "1"}):
                s = load_settings(str(Path(tmp) / "none.json"))
            self.assertTrue(s.host_server_enabled)

    def test_hosting_off_by_default(self):
        from core.settings import AppSettings
        self.assertFalse(AppSettings().host_server_enabled)


class TestHostedPreflight(unittest.TestCase):
    def test_hosted_check_fails_without_server_app(self):
        from core import preflight
        s = SimpleNamespace(host_server_enabled=True)
        with patch("core.preflight.sys.platform", "darwin"), \
             patch.object(Path, "is_file", return_value=False):
            item = preflight._check_hosted_server(s)
        self.assertIsNotNone(item)
        self.assertFalse(item.ok)
        self.assertIn("JamulusServer.app", item.detail)

    def test_hosted_check_rejects_unsupported_platform(self):
        from core import preflight
        s = SimpleNamespace(host_server_enabled=True)
        with patch("core.preflight.sys.platform", "win32"):
            item = preflight._check_hosted_server(s)
        self.assertFalse(item.ok)
        self.assertIn("only on macOS", item.detail)

    def test_hosted_check_verifies_exact_server_version(self):
        from core import preflight
        s = SimpleNamespace(host_server_enabled=True)
        with patch("core.preflight.sys.platform", "darwin"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("subprocess.run", return_value=SimpleNamespace(
                 stdout="Jamulus, Version 3.13.0", stderr=""
             )):
            item = preflight._check_hosted_server(s)
        self.assertFalse(item.ok)
        self.assertIn("3.12.2", item.detail)

    def test_hosted_check_accepts_and_labels_bundled_server(self):
        from core import preflight
        s = SimpleNamespace(host_server_enabled=True)
        with patch("core.preflight.sys.platform", "darwin"), \
             patch.object(Path, "is_file", return_value=False), \
             patch(
                 "services.bridge_service._bundled_jamulus_server_candidate",
                 return_value="/WebJam/Resources/JamulusServer",
             ), patch("subprocess.run", return_value=_version_ok()):
            item = preflight._check_hosted_server(s)
        self.assertTrue(item.ok)
        self.assertIn("bundled", item.detail)

    def test_hosted_check_absent_when_not_hosting(self):
        from core import preflight
        s = SimpleNamespace(host_server_enabled=False)
        self.assertIsNone(preflight._check_hosted_server(s))

    def test_recorder_failure_copy_points_to_start_audio_when_hosting(self):
        from core import preflight
        with tempfile.NamedTemporaryFile() as secret:
            s = SimpleNamespace(
                server_rpc_secret_file=secret.name,
                server_rpc_port=22240,
                takes_directory="",
                host_server_enabled=True,
            )
            with patch("core.jamulus_server_rpc.read_secret_file",
                       side_effect=OSError("connection refused")):
                item = preflight._check_recorder(s)
        self.assertFalse(item.ok)
        self.assertIn("Start Audio", item.detail)
        self.assertNotIn("start_macos_pilot.sh", item.detail)
        # Expected pre-Start-Audio on a hosting Mac: the hosted server writes
        # its own RPC secret at launch, so this must warn, not block.
        self.assertFalse(item.required)

    def test_hosted_takes_folder_is_created_by_ready_check(self):
        from core import preflight
        with tempfile.TemporaryDirectory() as tmp:
            takes = Path(tmp) / "Container" / "WebJam Recordings"
            s = SimpleNamespace(
                local_capture_enabled=True,
                takes_directory=str(takes),
                host_server_enabled=True,
            )
            item = preflight._check_local_capture(s)
            self.assertTrue(item.ok, item.detail)
            self.assertTrue(takes.is_dir())

    def test_hosted_takes_folder_creation_failure_is_honest(self):
        from core import preflight
        s = SimpleNamespace(
            local_capture_enabled=True,
            takes_directory="/nonexistent/webjam-takes",
            host_server_enabled=True,
        )
        with patch("core.preflight.Path.mkdir",
                   side_effect=OSError("Operation not permitted")):
            item = preflight._check_local_capture(s)
        self.assertFalse(item.ok)
        self.assertIn("couldn't be created", item.detail)
        self.assertIn("Operation not permitted", item.detail)
        self.assertNotIn("not writable", item.detail)


class TestHostedControllerFlows(unittest.TestCase):
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
            initial_title="Host Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        # Individual examples replace these live-process probes with mocks.
        # Restore truthful idle state before exercising the now fail-closed
        # application shutdown contract in fixture cleanup.
        cls.controller.bridge.hosted_server_alive = MagicMock(return_value=False)
        cls.controller.bridge.hosted_server_owned = MagicMock(return_value=False)
        cls.controller.shutdown()

    def setUp(self):
        c = self.controller
        c.window.flash_message = MagicMock()
        c.settings.host_server_enabled = True
        # This class intentionally reuses one controller. A failed cleanup is
        # durable in production, so reset that truth explicitly between
        # independent examples.
        c.audio.stopping = False
        c.audio.cleanup_retry_required = False
        c.audio.ended_by_user = False
        c.bridge.hosted_server_alive = MagicMock(return_value=True)
        c.bridge.hosted_server_owned = MagicMock(return_value=True)
        c.bridge.hosted_server_adopted = MagicMock(return_value=False)

    def tearDown(self):
        self.controller.settings.host_server_enabled = False

    def test_confirm_quit_while_hosting_says_session_ends_for_everyone(self):
        from webjam_qt.controllers.recording_coordinator import RecorderPhase
        c = self.controller
        c.recording.phase = RecorderPhase.RECORDING
        try:
            with patch(
                "webjam_qt.controllers.recording_coordinator.QMessageBox"
            ) as mbox:
                box = mbox.return_value
                box.clickedButton.return_value = object()
                self.assertFalse(c.recording.confirm_quit())
            body = box.setText.call_args.args[0]
            self.assertIn("hosting", body)
            self.assertIn("ends the session for every", body)
            self.assertNotIn("keeps recording", body)
        finally:
            c.recording.phase = RecorderPhase.IDLE

    def test_end_jam_explains_that_hosted_session_stops(self):
        from PySide6.QtWidgets import QMessageBox
        c = self.controller
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            c.audio.stop()
        text = question.call_args.args[2]
        self.assertIn("End this jam for everyone", text)
        self.assertIn("finish any recording", text)

    def test_end_jam_requires_the_host_to_finish_recording_first(self):
        c = self.controller
        c._server_recording = True
        c._recorder_armed = True
        with patch.object(
            c.recording, "stop_server_recording_for_shutdown"
        ) as recorder_stop, patch.object(
            c.bridge, "stop_jamulus"
        ) as client_stop, patch.object(
            c.bridge, "stop_hosted_server"
        ) as server_stop, patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.information"
        ) as information, patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question"
        ) as question:
            c.audio.stop()
        information.assert_called_once()
        question.assert_not_called()
        recorder_stop.assert_not_called()
        client_stop.assert_not_called()
        server_stop.assert_not_called()
        c._server_recording = False
        c._recorder_armed = False

    def test_unconfirmed_recorder_stop_preserves_running_services(self):
        c = self.controller
        with patch.object(
            c.recording, "stop_server_recording_for_shutdown", return_value=False
        ), patch.object(c.bridge, "stop_jamulus") as client_stop, patch.object(
            c.bridge, "stop_hosted_server"
        ) as server_stop, patch.object(
            c._ui_invoker, "invoke", side_effect=lambda callback: callback()
        ):
            c.audio._stop_session_services(True)
        client_stop.assert_not_called()
        server_stop.assert_not_called()
        self.assertEqual(
            self.window.participant_grid._empty_title.text(),
            "WebJam couldn’t finish cleanly",
        )

    def test_joiner_leaves_without_claiming_to_stop_the_hosts_recording(self):
        from PySide6.QtWidgets import QMessageBox

        c = self.controller
        c.settings.host_server_enabled = False
        c._server_recording = True
        c._recorder_armed = True
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            c.audio.stop()
        text = question.call_args.args[2]
        self.assertIn("host's recording will keep running", text)
        self.assertIn("Only this Mac", text)
        c._server_recording = False
        c._recorder_armed = False
        c.settings.host_server_enabled = True

    def test_end_jam_does_not_claim_success_when_client_cleanup_fails(self):
        from PySide6.QtWidgets import QMessageBox

        c = self.controller

        class _ImmediateThread:
            def __init__(self, *positional, target=None, args=(), **kwargs):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        with (
            patch.object(
                c.recording, "stop_server_recording_for_shutdown", return_value=True
            ),
            patch.object(c.recording, "on_audio_session_stopped") as reset_recording,
            patch.object(c.bridge, "stop_jamulus", return_value=False),
            patch.object(c.bridge, "stop_hosted_server", return_value=True),
            patch.object(c, "_clear_remote_invite_owner") as clear_owner,
            patch.object(c, "_stop_remote_transport") as stop_transport,
            patch.object(c._ui_invoker, "invoke", side_effect=lambda callback: callback()),
            patch(
                "webjam_qt.controllers.audio_coordinator.threading.Thread",
                _ImmediateThread,
            ),
            patch(
                "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ):
            c.audio.stop()

        self.assertEqual(
            self.window.participant_grid._empty_title.text(),
            "WebJam couldn’t finish cleanly",
        )
        self.assertFalse(c.audio.stopping)
        self.assertFalse(c.audio.ended_by_user)
        reset_recording.assert_not_called()
        clear_owner.assert_not_called()
        stop_transport.assert_not_called()

    def test_end_jam_retains_failed_private_peer_before_primary_shutdown(self):
        c = self.controller
        failed_peer = MagicMock()
        failed_peer.stop.side_effect = RuntimeError("still active")
        c.guest_peer = failed_peer
        c._guest_invite = MagicMock()

        try:
            with patch.object(
                c.recording,
                "stop_server_recording_for_shutdown",
                return_value=True,
            ), patch.object(
                c.bridge,
                "stop_jamulus",
            ) as client_stop, patch.object(
                c.bridge,
                "stop_hosted_server",
            ) as server_stop, patch.object(
                c._ui_invoker,
                "invoke",
                side_effect=lambda callback: callback(),
            ):
                c.audio._stop_session_services(True)

            failed_peer.stop.assert_called_once_with()
            client_stop.assert_not_called()
            server_stop.assert_not_called()
            self.assertIs(c.guest_peer, failed_peer)
            self.assertTrue(c.audio.cleanup_retry_required)
            self.assertEqual(
                c.window.session_strip._audio_button.text(),
                "Try End Session",
            )
        finally:
            failed_peer.stop.side_effect = None
            c._stop_session_peer(clear_invite=True)
            c.audio.cleanup_retry_required = False

    def test_idle_hero_offers_host_and_start(self):
        c = self.controller
        c.window.session_strip.set_audio_state("Ending…", enabled=False)
        c.audio.reset_to_idle()
        self.assertEqual(
            self.window.participant_grid._empty_primary.text().replace("&&", "&"),
            "Start Session",
        )
        self.assertEqual(
            self.window.session_strip._audio_button.text(),
            "Start Session",
        )
        self.assertTrue(self.window.session_strip._audio_button.isEnabled())
        self.assertIn(
            "Multitrack recording is ready",
            self.window.participant_grid._empty_hint.text(),
        )

    def test_status_bar_shows_hosting_truth(self):
        c = self.controller
        c._refresh_readiness()
        self.assertIn("Hosting", self.window._status_server.text())
        self.assertTrue(self.window._status_server.isHidden())
        self.assertIs(self.window._status_server.parentWidget(), self.window._status_bar)
        self.assertFalse(self.window._status_server.isWindow())
        c.settings.host_server_enabled = False
        c._refresh_readiness()
        self.assertTrue(self.window._status_server.isHidden())


class TestHostedShutdown(unittest.TestCase):
    def test_shutdown_stops_recording_then_hosted_server(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import (
            ApplicationController,
        )
        from webjam_qt.windows.conductor_window import ConductorWindow
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Host Shutdown",
        )
        settings = AppSettings()
        settings.host_server_enabled = True
        controller = ApplicationController(window, settings=settings)
        order: list[str] = []
        controller.bridge.hosted_server_alive = MagicMock(
            side_effect=[True, True, False],
        )
        controller.bridge.hosted_server_owned = MagicMock(return_value=True)
        controller.recording.stop_server_recording_for_shutdown = MagicMock(
            side_effect=lambda: order.append("stop-recording") or True
        )
        controller.bridge.stop_hosted_server = MagicMock(
            side_effect=lambda: order.append("stop-server")
        )
        controller.shutdown()
        self.assertEqual(order, ["stop-recording", "stop-server"])


if __name__ == "__main__":
    unittest.main()

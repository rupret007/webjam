"""Remote-host JamulusServer binding without changing legacy LAN hosting."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.jamulus_compatibility import ComponentTarget  # noqa: E402
from services.bridge_service import BridgeService  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)


BINARY = "/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"


def make_bridge(tmp_path: Path) -> tuple[BridgeService, SimpleNamespace]:
    settings = SimpleNamespace(
        jamulus_server="lan-host.local",
        jamulus_port=22124,
        jamulus_rpc_port=22222,
        server_rpc_port=22240,
        server_rpc_secret_file=str(tmp_path / "rpc.secret"),
        takes_directory=str(tmp_path / "Recordings"),
        host_server_enabled=True,
        jamulus_candidates=[],
        webex_url="",
    )
    bridge = BridgeService(
        jamulus_controller=mock.Mock(),
        webex_controller=mock.Mock(),
        metrics_service=mock.Mock(),
        repository=mock.Mock(),
        settings=settings,
        ui_callbacks={
            "set_status_banner": mock.Mock(),
            "refresh_readiness": mock.Mock(),
            "show_actionable_error": mock.Mock(),
            "show_message": mock.Mock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=tmp_path / "components",
    )
    bridge.find_jamulus_server_with_source = mock.Mock(
        return_value=(BINARY, "installed")
    )
    # These tests isolate binding argv and process ownership, not macOS source
    # artifact policy. Simulate a platform where an approved installed runtime
    # is executable; dedicated tests prove upstream Mac apps remain source-only.
    bridge._jamulus_component_target = ComponentTarget.WINDOWS_X64
    # This suite verifies binding and ownership against the published v0.27.1
    # component contract; unsigned v0.27.2 remains intentionally unsupported.
    bridge._runtime_webjam_version = mock.Mock(return_value="0.27.1")
    return bridge, settings


def launch_server(bridge: BridgeService, tmp_path: Path) -> tuple[list[str], mock.Mock]:
    process = mock.Mock()
    process.poll.return_value = None
    process.pid = 4242
    version = SimpleNamespace(stdout="Jamulus, Version 3.12.2", stderr="")
    with (
        mock.patch("services.bridge_service.subprocess.run", return_value=version),
        mock.patch(
            "services.bridge_service.subprocess.Popen",
            return_value=process,
        ) as popen,
        mock.patch.object(bridge, "_port_free", return_value=True),
        mock.patch.object(
            bridge,
            "_probe_hosted_server_rpc",
            return_value=(True, "ready"),
        ),
        mock.patch.object(bridge, "_start_hosted_caffeinate"),
        mock.patch("services.bridge_service.Path.home", return_value=tmp_path),
    ):
        ok, detail = bridge.ensure_hosted_server()

    assert ok, detail
    return list(popen.call_args.args[0]), process


def expected_server_argv(tmp_path: Path, *, remote: bool) -> list[str]:
    argv = [
        BINARY,
        "--nogui",
        "--port",
        "22124",
    ]
    if remote:
        argv.extend(("--serverbindip", "127.0.0.1"))
    argv.extend(
        (
            "--recording",
            str(tmp_path / "Recordings"),
            "--norecord",
            "--jsonrpcbindip",
            "127.0.0.1",
            "--jsonrpcport",
            "22240",
            "--jsonrpcsecretfile",
            str(tmp_path / "rpc.secret"),
        )
    )
    return argv


def test_v3_remote_host_uses_exact_loopback_server_argv_without_persistence(
    tmp_path: Path,
) -> None:
    bridge, settings = make_bridge(tmp_path)
    saved_settings = vars(settings).copy()

    bridge.enable_remote_host_mode()
    argv, _process = launch_server(bridge, tmp_path)

    assert argv == expected_server_argv(tmp_path, remote=True)
    assert bridge.remote_host_mode_enabled
    assert bridge.effective_server() == "127.0.0.1:22124"
    assert vars(settings) == saved_settings
    assert "remote_host_mode" not in vars(settings)


def test_legacy_v1_v2_host_keeps_exact_lan_binding_argv(tmp_path: Path) -> None:
    bridge, _settings = make_bridge(tmp_path)

    argv, _process = launch_server(bridge, tmp_path)

    assert argv == expected_server_argv(tmp_path, remote=False)
    assert "--serverbindip" not in argv
    assert not bridge.remote_host_mode_enabled
    # The host's own client remains loopback even though legacy bandmates can
    # still reach the server through its unchanged LAN binding.
    assert bridge.effective_server() == "127.0.0.1:22124"


def test_remote_mode_survives_owned_server_stop_for_supervised_restart(
    tmp_path: Path,
) -> None:
    bridge, _settings = make_bridge(tmp_path)
    bridge.enable_remote_host_mode()
    first_argv, first_process = launch_server(bridge, tmp_path)

    assert bridge.stop_hosted_server()
    assert first_process.terminate.call_count == 1
    assert bridge.remote_host_mode_enabled

    restarted_argv, _second_process = launch_server(bridge, tmp_path)
    assert first_argv == restarted_argv == expected_server_argv(tmp_path, remote=True)


def test_remote_mode_refuses_unowned_existing_server_but_legacy_can_adopt(
    tmp_path: Path,
) -> None:
    remote_bridge, _settings = make_bridge(tmp_path / "remote")
    remote_bridge.enable_remote_host_mode()
    with (
        mock.patch.object(remote_bridge, "_port_free", return_value=False),
        mock.patch.object(remote_bridge, "_probe_hosted_server_rpc") as probe,
    ):
        ok, detail = remote_bridge.ensure_hosted_server()
    assert not ok
    assert "loopback-only" in detail
    assert not remote_bridge.hosted_server_adopted()
    probe.assert_not_called()

    legacy_bridge, _settings = make_bridge(tmp_path / "legacy")
    with (
        mock.patch.object(legacy_bridge, "_port_free", side_effect=(False, False)),
        mock.patch.object(
            legacy_bridge,
            "_probe_hosted_server_rpc",
            return_value=(True, "ready"),
        ),
    ):
        ok, detail = legacy_bridge.ensure_hosted_server()
    assert ok, detail
    assert legacy_bridge.hosted_server_adopted()


def test_remote_mode_cannot_be_added_or_removed_around_a_live_server(
    tmp_path: Path,
) -> None:
    bridge, _settings = make_bridge(tmp_path)
    live = mock.Mock()
    live.poll.return_value = None
    bridge.hosted_server_process = live
    with pytest.raises(RuntimeError, match="before server launch"):
        bridge.enable_remote_host_mode()

    bridge.hosted_server_process = None
    bridge.enable_remote_host_mode()
    bridge.hosted_server_process = live
    with pytest.raises(RuntimeError, match="cannot be cleared"):
        bridge.disable_remote_host_mode()


def test_controller_installs_v3_owner_only_after_arming_bridge_mode() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.settings = SimpleNamespace(host_server_enabled=True)
    controller._remote_invite_owner = None
    events: list[tuple[str, object | None]] = []

    class Bridge:
        def enable_remote_host_mode(self) -> None:
            events.append(("enable", controller._remote_invite_owner))

    controller.bridge = Bridge()
    owner = object()

    controller._install_remote_invite_owner(owner)

    assert events == [("enable", None)]
    assert controller._remote_invite_owner is owner


def test_shutdown_stops_owned_server_then_clears_owner_and_ephemeral_mode() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller._shutdown = False
    timer = mock.Mock()
    controller._level_timer = timer
    controller._reconnect_timer = timer
    controller._meter_tick_timer = timer
    controller._pulse_refresh_timer = timer
    controller._connection_timer = timer
    controller.window = SimpleNamespace(
        recording_studio=mock.Mock(),
        webex_embed=mock.Mock(),
    )
    events: list[str] = []
    live = [True]

    class Bridge:
        def hosted_server_alive(self) -> bool:
            return live[0]

        def hosted_server_owned(self) -> bool:
            return live[0]

        def stop_jamulus(self) -> bool:
            events.append("stop-client")
            return True

        def stop_hosted_server(self) -> bool:
            events.append("stop-server")
            live[0] = False
            return True

        def disable_remote_host_mode(self) -> None:
            assert not live[0]
            events.append("disable-mode")

    controller.bridge = Bridge()
    controller.recording = mock.Mock()
    controller.recording.stop_server_recording_for_shutdown.side_effect = (
        lambda: events.append("stop-recording") or True
    )
    controller._save_notes = mock.Mock()
    controller._save_session_title = mock.Mock()
    controller._mix_dirty = False
    controller._stop_session_peer = (
        lambda **_kwargs: events.append("stop-peer") or True
    )
    controller._remote_session = None
    controller._remote_invitation = None
    controller.webex = mock.Mock()
    controller.api_bridge = mock.Mock()
    owner = mock.Mock()
    owner.stop.side_effect = lambda: events.append("stop-owner")
    controller._remote_invite_owner = owner

    controller.shutdown()

    assert events == [
        "stop-recording",
        "stop-peer",
        "stop-client",
        "stop-server",
        "stop-owner",
        "disable-mode",
    ]
    assert controller._remote_invite_owner is None


def test_failed_owned_server_stop_keeps_remote_mode_constraint() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    owner = mock.Mock()
    bridge = mock.Mock()
    bridge.hosted_server_alive.return_value = True
    controller._remote_invite_owner = owner
    controller.bridge = bridge

    assert not controller._clear_remote_invite_owner()
    owner.stop.assert_called_once_with()
    bridge.disable_remote_host_mode.assert_not_called()
    assert controller._remote_invite_owner is None


def test_failed_remote_owner_stop_retains_retryable_owner_and_mode() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    owner = mock.Mock()
    owner.stop.side_effect = RuntimeError("still active")
    bridge = mock.Mock()
    bridge.hosted_server_alive.return_value = False
    controller._remote_invite_owner = owner
    controller._remote_session = owner
    controller.bridge = bridge

    assert not controller._clear_remote_invite_owner()

    owner.stop.assert_called_once_with()
    bridge.disable_remote_host_mode.assert_not_called()
    assert controller._remote_invite_owner is owner
    assert controller._remote_session is owner


def test_false_remote_owner_stop_retains_retryable_owner_and_mode() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    owner = mock.Mock()
    owner.stop.return_value = False
    bridge = mock.Mock()
    bridge.hosted_server_alive.return_value = False
    controller._remote_invite_owner = owner
    controller._remote_session = owner
    controller.bridge = bridge

    assert not controller._clear_remote_invite_owner()

    owner.stop.assert_called_once_with()
    bridge.disable_remote_host_mode.assert_not_called()
    assert controller._remote_invite_owner is owner
    assert controller._remote_session is owner


def test_legacy_invite_replaces_armed_v3_owner_and_clears_loopback_mode(
    tmp_path: Path,
) -> None:
    from core.network_invite import create_invite_link
    from core.settings import AppSettings, save_settings
    from PySide6.QtWidgets import QApplication
    from webjam_qt.windows.conductor_window import ConductorWindow

    app = QApplication.instance() or QApplication([])
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Remote Host",
    )
    controller = ApplicationController(window, settings=settings)
    controller.begin_startup_journey = mock.Mock()
    owner = mock.Mock()
    controller._install_remote_invite_owner(owner)

    link = create_invite_link("192.168.1.42", session_name="Legacy Join")
    assert controller.accept_invite_url(link)

    owner.stop.assert_called_once_with()
    assert controller._remote_invite_owner is None
    assert not controller.bridge.remote_host_mode_enabled
    assert controller.settings.host_server_enabled is False
    controller.shutdown()
    assert app is not None


def test_hosted_server_sets_no_welcome_message(tmp_path: Path) -> None:
    """A server welcome pops the musician's Jamulus Chat window open.

    Jamulus delivers the welcome as a chat message, and an arriving chat
    message raises the client's Chat window over whatever is in front --
    which meant a stray Jamulus window landed on top of WebJam every time
    anyone joined the jam.
    """

    for remote in (False, True):
        argv = expected_server_argv(tmp_path, remote=remote)

        assert "--welcomemessage" not in argv
        assert not any("private band server" in str(item) for item in argv)

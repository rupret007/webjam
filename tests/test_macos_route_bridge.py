"""BridgeService must let Jamulus own native audio configuration on macOS."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.jamulus_profile import JamulusNativeProfileError
from core.settings import AppSettings
from services.bridge_service import BridgeService


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


def _bridge() -> BridgeService:
    controller = MagicMock()
    bridge = BridgeService(
        jamulus_controller=controller,
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=MagicMock(get_setting=MagicMock(return_value="1")),
        settings=AppSettings(
            jamulus_server="band.example.test",
            musician_name="Private Musician Name",
        ),
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
    )
    bridge.find_jamulus = MagicMock(return_value="/Applications/Jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    return bridge


def _native_plan() -> SimpleNamespace:
    return SimpleNamespace(
        arguments=("--inifile", "WebJam-native-v0.16.ini"),
        working_directory=Path("/tmp/webjam-jamulus-native-profile"),
        profile_fingerprint="a" * 64,
        jamulus_version="3.12.2",
    )


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
@patch("services.bridge_service.time.sleep")
def test_bridge_uses_jamulus_native_profile_and_keeps_the_gui_visible(
    _sleep: MagicMock,
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    plan = _native_plan()
    manager = MagicMock()
    manager.prepare.return_value = plan
    bridge._native_profile_manager = manager
    process = MagicMock()
    process.poll.return_value = None

    with patch("services.bridge_service.subprocess.Popen", return_value=process) as popen, patch(
        "core.file_io.atomic_write_text"
    ):
        assert bridge.launch_jamulus(manual=True) is True

    manager.prepare.assert_called_once_with(bridge.settings, "/Applications/Jamulus")
    cmd = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert cmd[:3] == [
        "/Applications/Jamulus",
        "--inifile",
        "WebJam-native-v0.16.ini",
    ]
    assert "--nogui" not in cmd
    assert "band.example.test:22124" in cmd
    assert kwargs["cwd"] == str(plan.working_directory)
    # The test runner uses Qt offscreen, but bundled Jamulus on macOS ships
    # Cocoa only. Its visible native setup must not inherit that test harness
    # override.
    assert "env" in kwargs
    assert "QT_QPA_PLATFORM" not in kwargs["env"]
    bridge.jamulus_controller.set_live_audio_route_owned.assert_any_call(True)


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
def test_native_profile_error_stops_before_server_or_client_processes(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    bridge.settings.host_server_enabled = True
    bridge.ensure_hosted_server = MagicMock()
    manager = MagicMock()
    manager.prepare.side_effect = JamulusNativeProfileError(
        "WebJam couldn't prepare its Jamulus profile."
    )
    bridge._native_profile_manager = manager

    with patch("services.bridge_service.subprocess.Popen") as popen:
        assert bridge.launch_jamulus(manual=True) is True

    popen.assert_not_called()
    bridge.ensure_hosted_server.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"
    assert bridge.show_actionable_error.call_args.args[0] == "Band audio needs attention"


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
def test_reconnect_revalidates_native_profile_without_choosing_a_device(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    plan = _native_plan()
    manager = MagicMock()
    manager.validate_active.side_effect = JamulusNativeProfileError(
        "WebJam couldn't restore its Jamulus profile."
    )
    bridge._native_profile_manager = manager
    bridge._active_native_profile = plan
    bridge.jamulus_launch_intended = True

    with patch("services.bridge_service.subprocess.Popen") as popen:
        assert bridge.launch_jamulus(manual=False, reconnect=True) is True

    manager.prepare.assert_not_called()
    manager.validate_active.assert_called_once_with(plan)
    popen.assert_not_called()
    assert bridge.jamulus_launch_intended is False
    assert bridge.jamulus_state == "Not running"

"""BridgeService must apply a prepared macOS route before it owns a client."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.macos_audio_route import JamulusAudioRouteError
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


def _route_plan() -> SimpleNamespace:
    return SimpleNamespace(
        arguments=("--inifile", "WebJam-route-v1.ini"),
        environment={"WEBJAM_ROUTE_TEST": "1"},
        working_directory=Path("/tmp/webjam-jamulus-route"),
    )


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
@patch("services.bridge_service.time.sleep")
def test_bridge_provisions_route_before_popen_and_uses_filename_cwd(
    _sleep: MagicMock,
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    plan = _route_plan()
    manager = MagicMock()
    manager.prepare.return_value = plan
    bridge._audio_route_manager = manager
    process = MagicMock()
    process.poll.return_value = None

    with patch("services.bridge_service.subprocess.Popen", return_value=process) as popen, patch(
        "core.file_io.atomic_write_text"
    ):
        assert bridge.launch_jamulus(manual=True) is True

    manager.prepare.assert_called_once_with(bridge.settings, "/Applications/Jamulus")
    cmd = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert cmd[:4] == [
        "/Applications/Jamulus",
        "--nogui",
        "--inifile",
        "WebJam-route-v1.ini",
    ]
    assert "band.example.test:22124" in cmd
    assert kwargs["cwd"] == str(plan.working_directory)
    assert kwargs["env"]["WEBJAM_ROUTE_TEST"] == "1"
    bridge.jamulus_controller.set_live_audio_route_owned.assert_any_call(True)


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
def test_route_failure_stops_host_and_client_before_any_external_process(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    bridge.settings.host_server_enabled = True
    bridge.ensure_hosted_server = MagicMock()
    manager = MagicMock()
    manager.prepare.side_effect = JamulusAudioRouteError(
        "The selected band input is no longer connected."
    )
    bridge._audio_route_manager = manager

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
def test_reconnect_revalidates_frozen_route_and_never_chooses_new_default(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    plan = _route_plan()
    manager = MagicMock()
    manager.validate_active.side_effect = JamulusAudioRouteError(
        "Your band audio device changed."
    )
    bridge._audio_route_manager = manager
    bridge._active_audio_route = plan
    bridge.jamulus_launch_intended = True

    with patch("services.bridge_service.subprocess.Popen") as popen:
        assert bridge.launch_jamulus(manual=False, reconnect=True) is True

    manager.prepare.assert_not_called()
    manager.validate_active.assert_called_once_with(plan)
    popen.assert_not_called()
    assert bridge.jamulus_launch_intended is False
    assert bridge.jamulus_state == "Not running"
    assert bridge.show_actionable_error.call_args.args[0] == "Band audio needs attention"

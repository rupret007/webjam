"""BridgeService must let Jamulus own native audio configuration on macOS."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.jamulus_profile import (
    JamulusAppDataPermissionError,
    JamulusNativeProfileError,
)
from core.settings import AppSettings
from services.bridge_service import BridgeService, JamulusLaunchFailureKind
from tests.support.component_store import isolated_component_store_root


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
            musician_name="Private Musician",
        ),
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=isolated_component_store_root(),
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

    with patch.dict(
        "services.bridge_service.os.environ",
        {"QT_QPA_PLATFORM": "offscreen"},
        clear=False,
    ), patch("services.bridge_service.sys.platform", "darwin"), patch(
        "services.bridge_service.subprocess.Popen", return_value=process
    ) as popen, patch("core.file_io.atomic_write_text"):
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
    error = bridge.show_actionable_error.call_args
    assert error.args[0] == "Band audio needs attention"
    assert error.kwargs["likely_cause"] == (
        "Jamulus could not open its native sound profile."
    )
    assert "Open Jamulus Audio Settings" in error.kwargs["next_action"]
    assert error.kwargs["retry_callback"] == bridge.retry_audio_launch
    assert (
        bridge.last_jamulus_launch_failure
        is JamulusLaunchFailureKind.NONE
    )


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
def test_app_data_denial_requires_full_relaunch_instead_of_retry(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    bridge.settings.host_server_enabled = True
    bridge.ensure_hosted_server = MagicMock()
    manager = MagicMock()
    manager.prepare.side_effect = JamulusAppDataPermissionError(
        "macOS didn't allow WebJam to use the Jamulus-owned profile dedicated "
        "to WebJam."
    )
    bridge._native_profile_manager = manager

    with patch("services.bridge_service.subprocess.Popen") as popen:
        assert bridge.launch_jamulus(manual=True) is True

    popen.assert_not_called()
    bridge.ensure_hosted_server.assert_not_called()
    error = bridge.show_actionable_error.call_args
    assert error.args[0] == "Band audio needs attention"
    assert "Other Application Data" in error.kwargs["likely_cause"]
    assert "Quit WebJam completely" in error.kwargs["next_action"]
    assert "choose Allow" in error.kwargs["next_action"]
    assert error.kwargs["retry_callback"] is None
    assert bridge.jamulus_state == "Launch failed"
    assert bridge.jamulus_launch_intended is False
    assert (
        bridge.last_jamulus_launch_failure
        is JamulusLaunchFailureKind.APP_DATA_PERMISSION_DENIED
    )

    assert bridge.stop_jamulus() is True
    assert (
        bridge.last_jamulus_launch_failure
        is JamulusLaunchFailureKind.APP_DATA_PERMISSION_DENIED
    )
    bridge.show_actionable_error.reset_mock()
    manager.prepare.reset_mock()
    with patch("services.bridge_service.subprocess.Popen") as second_popen:
        assert bridge.launch_jamulus(manual=True) is False

    manager.prepare.assert_not_called()
    second_popen.assert_not_called()
    repeated = bridge.show_actionable_error.call_args
    assert repeated.args[0] == "Band audio needs attention"
    assert "Quit WebJam completely" in repeated.kwargs["next_action"]
    assert repeated.kwargs["retry_callback"] is None


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
def test_stale_app_data_denial_cannot_release_a_newer_launch_lease(
    _thread: MagicMock,
) -> None:
    bridge = _bridge()
    superseding_token = threading.Event()
    manager = MagicMock()

    def supersede_then_deny(*_args, **_kwargs):
        bridge._pending_jamulus_launch_cancel = superseding_token
        raise JamulusAppDataPermissionError("stale denial")

    manager.prepare.side_effect = supersede_then_deny
    bridge._native_profile_manager = manager

    with patch("services.bridge_service.subprocess.Popen") as popen:
        assert bridge.launch_jamulus(manual=True) is True

    popen.assert_not_called()
    assert bridge._pending_jamulus_launch_cancel is superseding_token
    assert bridge._runtime_component_lease is not None
    assert bridge._runtime_component_lease_claims == {"client"}
    assert bridge.jamulus_state == "Starting"
    assert (
        bridge.last_jamulus_launch_failure
        is JamulusLaunchFailureKind.NONE
    )
    bridge.show_actionable_error.assert_not_called()
    bridge._release_runtime_component_lease("client")


def test_racing_launch_rechecks_app_data_denial_inside_request_lock() -> None:
    bridge = _bridge()
    raw_lock = threading.Lock()
    waiting = threading.Event()

    class _NotifyingLock:
        def __enter__(self):
            waiting.set()
            raw_lock.acquire()
            return self

        def __exit__(self, *_args):
            raw_lock.release()

    raw_lock.acquire()
    bridge._jamulus_launch_control_lock = _NotifyingLock()
    bridge.find_jamulus = MagicMock(
        side_effect=AssertionError("denial latch was bypassed")
    )
    outcome: dict[str, object] = {}

    def launch() -> None:
        try:
            outcome["accepted"] = bridge.launch_jamulus(manual=True)
        except BaseException as exc:  # noqa: BLE001 - surface worker failures
            outcome["error"] = exc

    worker = threading.Thread(target=launch)
    worker.start()
    reached_request_lock = waiting.wait(timeout=1.0)
    if reached_request_lock:
        bridge.last_jamulus_launch_failure = (
            JamulusLaunchFailureKind.APP_DATA_PERMISSION_DENIED
        )
    raw_lock.release()
    worker.join(timeout=2.0)

    assert reached_request_lock
    assert worker.is_alive() is False
    assert "error" not in outcome
    assert outcome["accepted"] is False
    bridge.find_jamulus.assert_not_called()
    assert bridge._pending_jamulus_launch_cancel is None
    assert bridge.jamulus_launch_intended is False
    assert bridge.show_actionable_error.call_args.kwargs["retry_callback"] is None


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

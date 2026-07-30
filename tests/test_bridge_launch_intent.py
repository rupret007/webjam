"""Launch-intent lifecycle regressions around synchronous Jamulus preflight."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.bridge_service import BridgeService
from tests.support.component_store import isolated_component_store_root


def _bridge() -> BridgeService:
    settings = MagicMock()
    settings.jamulus_server = "band.example.test"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.jamulus_candidates = []
    settings.host_server_enabled = False

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
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=isolated_component_store_root(),
    )
    bridge.find_jamulus = MagicMock(return_value="/Applications/Jamulus")
    return bridge


def test_manual_port_conflict_cannot_trigger_profileless_auto_reconnect() -> None:
    bridge = _bridge()
    bridge._native_profile_manager = MagicMock()
    bridge._active_native_profile = None
    bridge._is_rpc_port_in_use = MagicMock(return_value=True)

    with patch("services.bridge_service.threading.Thread") as thread_class:
        assert bridge.launch_jamulus(manual=True) is False

    thread_class.assert_not_called()
    assert bridge.jamulus_state == "Port in use"
    assert bridge.jamulus_launch_intended is False
    assert bridge._pending_jamulus_launch_cancel is None

    launch = MagicMock(wraps=bridge.launch_jamulus)
    bridge.launch_jamulus = launch
    bridge.attempt_auto_reconnects()

    launch.assert_not_called()
    bridge._native_profile_manager.validate_active.assert_not_called()


def test_reconnect_port_conflict_keeps_established_session_intent() -> None:
    bridge = _bridge()
    bridge.jamulus_launch_intended = True
    bridge.jamulus_reconnect_inflight = True
    bridge._is_rpc_port_in_use = MagicMock(return_value=True)

    assert bridge.launch_jamulus(manual=False, reconnect=True) is False

    assert bridge.jamulus_launch_intended is True
    assert bridge.jamulus_reconnect_inflight is False
    assert bridge._pending_jamulus_launch_cancel is None
    recovery = bridge.jamulus_recovery_snapshot()
    assert recovery.active is True
    assert recovery.exhausted is False
    bridge.metrics_service.increment.assert_any_call(
        "metric_jamulus_reconnect_failed"
    )


def test_duplicate_manual_launch_during_port_preflight_reuses_request() -> None:
    bridge = _bridge()
    port_probes = 0
    duplicate_results: list[bool] = []

    def probe_port() -> bool:
        nonlocal port_probes
        port_probes += 1
        duplicate_results.append(bridge.launch_jamulus(manual=True))
        return True

    bridge._is_rpc_port_in_use = probe_port
    with patch("services.bridge_service.threading.Thread") as thread_class:
        assert bridge.launch_jamulus(manual=True) is False

    thread_class.assert_not_called()
    assert port_probes == 1
    assert duplicate_results == [True]
    assert bridge.jamulus_launch_intended is False
    assert bridge._pending_jamulus_launch_cancel is None
    assert bridge.jamulus_state == "Port in use"
    bridge.show_actionable_error.assert_called_once()
    bridge.metrics_service.increment.assert_any_call(
        "metric_jamulus_port_conflict"
    )


def test_cancelled_port_preflight_cannot_publish_conflict_after_stop() -> None:
    bridge = _bridge()

    def probe_port() -> bool:
        # Model Stop winning while the synchronous port probe is in flight.
        assert bridge.stop_jamulus() is True
        return True

    bridge._is_rpc_port_in_use = probe_port
    with patch("services.bridge_service.threading.Thread") as thread_class:
        assert bridge.launch_jamulus(manual=True) is False

    thread_class.assert_not_called()
    assert bridge.jamulus_state == "Stopped"
    assert bridge.jamulus_launch_intended is False
    assert bridge._pending_jamulus_launch_cancel is None
    bridge.show_actionable_error.assert_not_called()
    assert not any(
        item.args == ("metric_jamulus_port_conflict",)
        for item in bridge.metrics_service.increment.call_args_list
    )


def test_accepted_restart_replaces_stale_stopped_state_before_worker_poll() -> None:
    """A queued worker must not expose the prior session's terminal state."""

    bridge = _bridge()
    bridge.jamulus_state = "Stopped"
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    with patch("services.bridge_service.threading.Thread") as thread_class:
        thread_class.return_value = MagicMock()
        assert bridge.launch_jamulus(manual=True) is True

    thread_class.return_value.start.assert_called_once_with()
    assert bridge.jamulus_state == "Starting"
    assert bridge.jamulus_launch_intended is True


def test_pending_host_launch_is_not_misclassified_as_a_dropped_client() -> None:
    """The reconnect timer must leave a slow accepted host launch alone."""

    bridge = _bridge()
    bridge.settings.host_server_enabled = True
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    with patch("services.bridge_service.threading.Thread") as thread_class:
        thread_class.return_value = MagicMock()
        assert bridge.launch_jamulus(manual=True) is True
        pending = bridge._pending_jamulus_launch_cancel

        bridge._attempt_auto_reconnect_jamulus(now=100.0)

    assert pending is not None
    assert bridge._pending_jamulus_launch_cancel is pending
    assert pending.is_set() is False
    assert thread_class.call_count == 1
    assert bridge.jamulus_reconnect_attempts == 0
    assert bridge.jamulus_reconnect_inflight is False
    assert not any(
        item.args == ("metric_jamulus_reconnect_attempt",)
        for item in bridge.metrics_service.increment.call_args_list
    )


def test_racing_auto_reconnect_cannot_supersede_an_accepted_manual_launch() -> None:
    """The launch boundary repeats the timer guard to close the check/call race."""

    bridge = _bridge()
    bridge.settings.host_server_enabled = True
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    native_profile = object()
    bridge._active_native_profile = native_profile

    with patch("services.bridge_service.threading.Thread") as thread_class:
        thread_class.return_value = MagicMock()
        assert bridge.launch_jamulus(manual=True) is True
        pending = bridge._pending_jamulus_launch_cancel
        bridge.jamulus_reconnect_inflight = True

        # Model a reconnect tick that passed an earlier state snapshot just
        # before the musician's manual launch was accepted.
        assert (
            bridge.launch_jamulus(
                manual=False,
                reconnect=True,
                force_restart=False,
            )
            is False
        )

    assert pending is not None
    assert bridge._pending_jamulus_launch_cancel is pending
    assert pending.is_set() is False
    assert bridge._active_native_profile is native_profile
    assert bridge.jamulus_launch_intended is True
    assert bridge.jamulus_reconnect_inflight is False
    assert thread_class.call_count == 1


def test_stop_during_successful_port_preflight_cannot_publish_starting() -> None:
    """A Stop that wins preflight keeps the terminal state and spawns nothing."""

    bridge = _bridge()
    bridge.jamulus_state = "Stopped"

    def probe_port() -> bool:
        assert bridge.stop_jamulus() is True
        return False

    bridge._is_rpc_port_in_use = probe_port
    with patch("services.bridge_service.threading.Thread") as thread_class:
        assert bridge.launch_jamulus(manual=True) is False

    thread_class.assert_not_called()
    assert bridge.jamulus_state == "Stopped"
    assert bridge.jamulus_launch_intended is False

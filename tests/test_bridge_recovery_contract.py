"""Authoritative BridgeService primary-client recovery contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.jamulus_rpc_client import (
    JamulusRpcMonitorIdentity,
    JamulusRpcMonitorSnapshot,
)
from services.bridge_service import (
    JamulusRecoverySnapshot,
    JamulusRpcFreshness,
    NATIVE_SOUND_SETUP_GRACE_SECONDS,
    RECONNECT_LOCAL_ROSTER_GRACE_SECONDS,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_RPC_STARTUP_GRACE_SECONDS,
)
from tests.support.component_store import isolated_component_store_root


def _bridge():
    from services.bridge_service import BridgeService

    settings = MagicMock()
    settings.jamulus_server = "band.example.test"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.jamulus_candidates = []
    settings.host_server_enabled = False
    settings.musician_name = "Test Musician"

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
    bridge.jamulus_controller.rpc_monitor_snapshot_for.return_value = None
    return bridge


def _process(pid: int, *, return_code=None):
    process = MagicMock()
    process.pid = pid
    process.poll.return_value = return_code
    return process


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs):
        del args, kwargs
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class _QueuedThread:
    targets: list = []

    def __init__(self, *args, target=None, **kwargs):
        del args, kwargs
        self._target = target

    def start(self):
        self.targets.append(self._target)


def _prime_recovery(
    bridge,
    *,
    attempts: int,
    generation: int = 1,
) -> None:
    bridge.jamulus_launch_intended = True
    bridge._jamulus_recovery_generation = generation
    bridge._jamulus_recovery_active = True
    bridge._jamulus_recovery_exhausted = False
    bridge.jamulus_reconnect_attempts = attempts
    bridge.jamulus_reconnect_inflight = False
    bridge.jamulus_next_reconnect_at = 0.0


def _publish_recovery_process(
    bridge,
    process,
    *,
    process_generation: int = 1,
    recovery_generation: int = 1,
    started_at: float = 100.0,
) -> None:
    bridge.jamulus_process = process
    bridge._jamulus_process_generation_counter = process_generation
    bridge._jamulus_process_generation = process_generation
    bridge._jamulus_process_recovery_generation = recovery_generation
    bridge._jamulus_process_started_at = started_at


def _rpc_monitor_snapshot(
    *,
    process_generation: int,
    process_id: int,
    running: bool = True,
    available: bool = True,
    authenticated: bool = True,
    last_activity_at: float | None = 100.5,
    last_activity_age_seconds: object = 1.0,
) -> JamulusRpcMonitorSnapshot:
    """Build exact-process RPC evidence without legacy global client state."""

    return JamulusRpcMonitorSnapshot(
        identity=JamulusRpcMonitorIdentity(
            monitor_epoch=1,
            process_generation=process_generation,
            process_id=process_id,
        ),
        running=running,
        available=available,
        authenticated=authenticated,
        last_activity_at=last_activity_at,
        last_activity_age_seconds=last_activity_age_seconds,
    )


def _set_rpc_monitor(
    bridge,
    *,
    process_generation: int,
    process_id: int,
    **overrides,
) -> JamulusRpcMonitorSnapshot:
    snapshot = _rpc_monitor_snapshot(
        process_generation=process_generation,
        process_id=process_id,
        **overrides,
    )
    bridge.jamulus_controller.rpc_monitor_snapshot_for.return_value = snapshot
    return snapshot


def test_recovery_snapshot_is_frozen_and_scalar_only() -> None:
    bridge = _bridge()

    snapshot = bridge.jamulus_recovery_snapshot(now=10.0)

    assert isinstance(snapshot, JamulusRecoverySnapshot)
    assert snapshot.generation == 0
    assert snapshot.recovery_generation == 0
    assert snapshot.process_id == 0
    assert snapshot.process_alive is False
    assert snapshot.rpc_freshness is JamulusRpcFreshness.NO_PROCESS
    assert snapshot.rpc_monitor_epoch == 0
    assert snapshot.attempts_started == 0
    assert snapshot.max_attempts == RECONNECT_MAX_ATTEMPTS
    with pytest.raises(FrozenInstanceError):
        snapshot.active = True


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread")
def test_stop_winning_reconnect_schedule_gap_cannot_relaunch(
    thread_class,
    popen,
) -> None:
    """Stop between retry classification and launch owns the terminal state."""

    bridge = _bridge()
    dead_process = _process(100, return_code=1)
    _prime_recovery(bridge, attempts=1, generation=3)
    _publish_recovery_process(
        bridge,
        dead_process,
        process_generation=8,
        recovery_generation=3,
    )
    original_launch = bridge.launch_jamulus

    def stop_before_launch(**kwargs):
        assert bridge.stop_jamulus()
        return original_launch(**kwargs)

    bridge.launch_jamulus = stop_before_launch
    bridge._attempt_auto_reconnect_jamulus(now=200.0)

    popen.assert_not_called()
    thread_class.assert_not_called()
    assert bridge.jamulus_launch_intended is False
    assert bridge._pending_jamulus_launch_cancel is None
    assert bridge.jamulus_process is None
    assert bridge.jamulus_state == "Stopped"
    assert bridge.jamulus_reconnect_inflight is False
    assert bridge.runtime_component_lease_active is False


def test_unknown_rpc_gets_one_bounded_startup_grace() -> None:
    bridge = _bridge()
    process = _process(101)
    bridge.jamulus_process = process
    bridge._jamulus_process_started_at = 100.0

    within = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS - 0.001
    )
    boundary = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    assert within.rpc_freshness is JamulusRpcFreshness.STARTING
    assert boundary.rpc_freshness is JamulusRpcFreshness.STALE


def test_process_published_after_observation_sample_keeps_startup_grace() -> None:
    bridge = _bridge()
    bridge.jamulus_process = _process(130)
    bridge._jamulus_process_started_at = 100.001

    snapshot = bridge.jamulus_recovery_snapshot(now=100.0)

    assert snapshot.process_alive is True
    assert snapshot.rpc_freshness is JamulusRpcFreshness.STARTING


def test_exact_generation_gets_longer_native_sound_setup_grace() -> None:
    bridge = _bridge()
    process = _process(111)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=7,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 7
    bridge._jamulus_native_setup_deadline = 100.0 + NATIVE_SOUND_SETUP_GRACE_SECONDS

    ordinary_boundary = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )
    setup_boundary = bridge.jamulus_recovery_snapshot(
        now=100.0 + NATIVE_SOUND_SETUP_GRACE_SECONDS
    )

    assert ordinary_boundary.rpc_freshness is JamulusRpcFreshness.STARTING
    assert ordinary_boundary.native_setup_grace_active is True
    assert setup_boundary.rpc_freshness is JamulusRpcFreshness.STALE
    assert setup_boundary.native_setup_grace_active is False


def test_native_setup_grace_never_covers_a_replaced_generation() -> None:
    bridge = _bridge()
    process = _process(112)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=8,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 7
    bridge._jamulus_native_setup_deadline = 100.0 + NATIVE_SOUND_SETUP_GRACE_SECONDS

    snapshot = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    assert snapshot.rpc_freshness is JamulusRpcFreshness.STALE
    assert snapshot.native_setup_grace_configured is True
    assert snapshot.native_setup_grace_active is False


def test_configured_native_setup_grace_survives_a_dead_recovery_gap() -> None:
    bridge = _bridge()
    dead = _process(116, return_code=1)
    _publish_recovery_process(
        bridge,
        dead,
        process_generation=12,
        recovery_generation=3,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 12
    bridge._jamulus_native_setup_deadline = 700.0

    snapshot = bridge.jamulus_recovery_snapshot(now=200.0)

    assert snapshot.process_alive is False
    assert snapshot.native_setup_grace_configured is True
    assert snapshot.native_setup_grace_active is False


def test_native_setup_window_prevents_premature_force_restart() -> None:
    bridge = _bridge()
    process = _process(113)
    bridge.jamulus_launch_intended = True
    _publish_recovery_process(
        bridge,
        process,
        process_generation=9,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 9
    bridge._jamulus_native_setup_deadline = 100.0 + NATIVE_SOUND_SETUP_GRACE_SECONDS
    bridge.launch_jamulus = MagicMock(return_value=True)

    bridge._attempt_auto_reconnect_jamulus(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    bridge.launch_jamulus.assert_not_called()
    assert bridge._jamulus_recovery_active is False
    process.terminate.assert_not_called()


def test_native_setup_window_suppresses_recovery_roster_timeout() -> None:
    bridge = _bridge()
    process = _process(117)
    bridge.jamulus_launch_intended = True
    _prime_recovery(bridge, attempts=1, generation=4)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=13,
        recovery_generation=4,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 13
    bridge._jamulus_native_setup_deadline = 700.0
    _set_rpc_monitor(
        bridge,
        process_generation=13,
        process_id=117,
        last_activity_at=140.0,
        last_activity_age_seconds=0.0,
    )
    bridge.launch_jamulus = MagicMock(return_value=True)

    bridge._attempt_auto_reconnect_jamulus(now=140.0)

    bridge.launch_jamulus.assert_not_called()
    process.terminate.assert_not_called()


def test_only_current_generation_and_pid_can_finish_native_setup() -> None:
    bridge = _bridge()
    process = _process(114)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=10,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 10
    bridge._jamulus_native_setup_deadline = (
        time.monotonic() + NATIVE_SOUND_SETUP_GRACE_SECONDS
    )

    assert not bridge.finish_native_sound_setup(
        generation=9,
        process_id=114,
    )
    assert not bridge.finish_native_sound_setup(
        generation=10,
        process_id=999,
    )
    assert bridge._jamulus_native_setup_deadline > 0.0

    assert bridge.finish_native_sound_setup(
        generation=10,
        process_id=114,
    )
    assert bridge._jamulus_native_setup_deadline == 0.0
    assert bridge._jamulus_native_setup_process_generation == 0


@pytest.mark.parametrize(
    ("observed_at", "expected"),
    [(199.999, True), (200.0, False), (200.001, False)],
)
def test_native_setup_finish_respects_absolute_deadline(
    observed_at: float,
    expected: bool,
) -> None:
    bridge = _bridge()
    process = _process(120)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=16,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge._jamulus_native_setup_process_generation = 16
    bridge._jamulus_native_setup_deadline = 200.0

    with patch(
        "services.bridge_service.time.monotonic",
        return_value=observed_at,
    ):
        result = bridge.finish_native_sound_setup(
            generation=16,
            process_id=120,
        )

    assert result is expected
    assert (bridge._jamulus_native_setup_deadline == 0.0) is expected


def test_identity_bound_stop_does_not_mutate_or_stop_a_replacement() -> None:
    bridge = _bridge()
    replacement = _process(115)
    bridge.jamulus_launch_intended = True
    _publish_recovery_process(
        bridge,
        replacement,
        process_generation=11,
        recovery_generation=0,
        started_at=100.0,
    )

    assert not bridge.stop_jamulus(
        expected_generation=10,
        expected_process_id=114,
    )

    assert bridge.jamulus_launch_intended is True
    assert bridge.jamulus_process is replacement
    replacement.terminate.assert_not_called()
    bridge.jamulus_controller.stop.assert_not_called()


def test_identity_bound_stop_validates_after_lifecycle_ownership() -> None:
    bridge = _bridge()
    old = _process(118)
    replacement = _process(119)
    _publish_recovery_process(
        bridge,
        old,
        process_generation=14,
        recovery_generation=0,
        started_at=100.0,
    )
    bridge.jamulus_launch_intended = True
    old_token = threading.Event()
    bridge._pending_jamulus_launch_cancel = old_token
    bridge._jamulus_launch_request_generation = 8
    started = threading.Event()
    result: list[bool] = []

    def stop_old() -> None:
        started.set()
        result.append(
            bridge.stop_jamulus(
                expected_generation=14,
                expected_process_id=118,
            )
        )

    bridge._jamulus_lifecycle_lock.acquire()
    worker = threading.Thread(target=stop_old)
    worker.start()
    assert started.wait(timeout=1.0)
    with bridge._jamulus_launch_control_lock:
        replacement_token = threading.Event()
        bridge._pending_jamulus_launch_cancel = replacement_token
        bridge._jamulus_launch_request_generation = 9
        with bridge._reconnect_lock:
            bridge.jamulus_process = replacement
            bridge._jamulus_process_generation = 15
            bridge._jamulus_process_started_at = 101.0
    bridge._jamulus_lifecycle_lock.release()
    worker.join(timeout=1.0)

    assert result == [False]
    assert bridge.jamulus_launch_intended is True
    assert bridge._pending_jamulus_launch_cancel is replacement_token
    assert not replacement_token.is_set()
    replacement.terminate.assert_not_called()


def test_lineage_bound_stop_never_cancels_a_newer_request() -> None:
    bridge = _bridge()
    bridge.jamulus_launch_intended = True
    bridge._jamulus_launch_request_generation = 21
    old_token = threading.Event()
    bridge._pending_jamulus_launch_cancel = old_token
    started = threading.Event()
    result: list[bool] = []

    def stop_old_request() -> None:
        started.set()
        result.append(
            bridge.stop_jamulus(
                expected_launch_request_generation=21,
            )
        )

    bridge._jamulus_lifecycle_lock.acquire()
    worker = threading.Thread(target=stop_old_request)
    worker.start()
    assert started.wait(timeout=1.0)
    with bridge._jamulus_launch_control_lock:
        new_token = threading.Event()
        bridge._pending_jamulus_launch_cancel = new_token
        bridge._jamulus_launch_request_generation = 22
    bridge._jamulus_lifecycle_lock.release()
    worker.join(timeout=1.0)

    assert result == [False]
    assert bridge.jamulus_launch_intended is True
    assert bridge._pending_jamulus_launch_cancel is new_token
    assert not new_token.is_set()


def test_stop_invalidates_queued_launch_ui_but_new_failure_ui_can_render() -> None:
    bridge = _bridge()
    queued_callbacks = []
    bridge.schedule_ui_callback = queued_callbacks.append
    old_success = MagicMock()
    bridge._jamulus_launch_request_generation_counter = 7
    bridge._jamulus_launch_request_generation = 7
    bridge._schedule_jamulus_launch_ui_if_current(7, old_success)

    assert bridge.stop_jamulus() is True
    tombstone_generation = bridge._jamulus_launch_request_generation
    assert tombstone_generation > 7

    queued_callbacks.pop(0)()
    old_success.assert_not_called()

    bridge.find_jamulus = MagicMock(return_value=None)
    assert bridge.launch_jamulus(manual=True, reconnect=False) is False
    assert bridge._jamulus_launch_request_generation > tombstone_generation
    assert queued_callbacks

    for callback in queued_callbacks:
        callback()
    bridge.show_actionable_error.assert_called_once()


@pytest.mark.parametrize(
    "age",
    [float("inf"), float("nan"), -0.1, True, "1.0"],
)
def test_invalid_rpc_age_is_never_fresh(age) -> None:
    bridge = _bridge()
    process = _process(102)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=2,
        recovery_generation=0,
        started_at=10.0,
    )
    _set_rpc_monitor(
        bridge,
        process_generation=2,
        process_id=102,
        last_activity_at=11.0,
        last_activity_age_seconds=age,
    )

    snapshot = bridge.jamulus_recovery_snapshot(
        now=10.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    assert snapshot.rpc_freshness is JamulusRpcFreshness.STALE
    assert snapshot.rpc_age_seconds is None


def test_rpc_exception_is_never_fresh() -> None:
    bridge = _bridge()
    _publish_recovery_process(
        bridge,
        _process(103),
        process_generation=3,
        recovery_generation=0,
        started_at=10.0,
    )
    bridge.jamulus_controller.rpc_monitor_snapshot_for.side_effect = RuntimeError(
        "unavailable"
    )

    snapshot = bridge.jamulus_recovery_snapshot(
        now=10.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    assert snapshot.rpc_freshness is JamulusRpcFreshness.STALE


def test_fresh_requires_available_finite_nonnegative_rpc() -> None:
    bridge = _bridge()
    _publish_recovery_process(
        bridge,
        _process(104),
        process_generation=4,
        recovery_generation=0,
        started_at=10.0,
    )
    _set_rpc_monitor(
        bridge,
        process_generation=4,
        process_id=104,
        available=False,
        last_activity_at=99.0,
        last_activity_age_seconds=1.0,
    )

    unavailable = bridge.jamulus_recovery_snapshot(now=100.0)
    _set_rpc_monitor(
        bridge,
        process_generation=4,
        process_id=104,
        last_activity_at=99.0,
        last_activity_age_seconds=1.0,
    )
    available = bridge.jamulus_recovery_snapshot(now=100.0)

    assert unavailable.rpc_freshness is JamulusRpcFreshness.STALE
    assert available.rpc_freshness is JamulusRpcFreshness.FRESH
    assert available.rpc_age_seconds == 1.0
    assert available.rpc_monitor_epoch == 1


def test_dead_owned_process_opens_generation_and_starts_exact_attempt() -> None:
    bridge = _bridge()
    bridge.jamulus_launch_intended = True
    bridge.jamulus_process = _process(105, return_code=1)
    bridge.launch_jamulus = MagicMock(return_value=True)

    bridge._attempt_auto_reconnect_jamulus(now=100.0)
    snapshot = bridge.jamulus_recovery_snapshot(now=100.0)

    bridge.launch_jamulus.assert_called_once_with(
        manual=False,
        reconnect=True,
        force_restart=False,
    )
    assert snapshot.active is True
    assert snapshot.recovery_generation == 1
    assert snapshot.attempts_started == 1
    assert snapshot.inflight is True
    assert snapshot.exhausted is False


def test_process_none_retries_only_during_explicit_recovery() -> None:
    bridge = _bridge()
    bridge.jamulus_launch_intended = True
    bridge.launch_jamulus = MagicMock(return_value=True)

    bridge._attempt_auto_reconnect_jamulus(now=100.0)
    bridge.launch_jamulus.assert_not_called()

    _prime_recovery(bridge, attempts=1)
    bridge._attempt_auto_reconnect_jamulus(now=100.0)

    bridge.launch_jamulus.assert_called_once_with(
        manual=False,
        reconnect=True,
        force_restart=False,
    )
    assert bridge.jamulus_reconnect_attempts == 2


def test_fresh_live_process_preserves_history_until_acknowledged() -> None:
    bridge = _bridge()
    process = _process(106)
    _prime_recovery(bridge, attempts=3)
    _publish_recovery_process(bridge, process)
    _set_rpc_monitor(
        bridge,
        process_generation=1,
        process_id=106,
        last_activity_at=100.5,
        last_activity_age_seconds=1.0,
    )
    bridge.launch_jamulus = MagicMock()

    bridge._attempt_auto_reconnect_jamulus(now=101.0)

    assert bridge.jamulus_reconnect_attempts == 3
    bridge.launch_jamulus.assert_not_called()
    assert bridge.mark_jamulus_reconnect_authenticated(
        generation=1,
        process_id=106,
    )
    assert bridge.jamulus_reconnect_attempts == 0
    assert bridge.jamulus_recovery_snapshot().active is False
    bridge.metrics_service.increment.assert_any_call(
        "metric_jamulus_reconnect_success"
    )
    assert not bridge.mark_jamulus_reconnect_authenticated(
        generation=1,
        process_id=106,
    )


def test_process_published_after_tick_sample_is_not_force_restarted() -> None:
    bridge = _bridge()
    process = _process(118)
    bridge.jamulus_launch_intended = True
    _publish_recovery_process(
        bridge,
        process,
        process_generation=14,
        recovery_generation=0,
        started_at=100.001,
    )
    bridge.launch_jamulus = MagicMock(return_value=True)

    bridge._attempt_auto_reconnect_jamulus(now=100.0)

    bridge.launch_jamulus.assert_not_called()
    process.terminate.assert_not_called()
    assert bridge.jamulus_reconnect_attempts == 0
    assert bridge._jamulus_recovery_active is False


@pytest.mark.parametrize(
    ("generation", "process_id"),
    [(6, 107), (7, 999)],
)
def test_ack_rejects_stale_generation_or_pid(
    generation: int,
    process_id: int,
) -> None:
    bridge = _bridge()
    process = _process(107)
    _prime_recovery(bridge, attempts=2, generation=4)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=7,
        recovery_generation=4,
    )
    _set_rpc_monitor(
        bridge,
        process_generation=7,
        process_id=107,
        last_activity_at=100.5,
        last_activity_age_seconds=0.5,
    )

    assert not bridge.mark_jamulus_reconnect_authenticated(
        generation=generation,
        process_id=process_id,
    )
    assert bridge.jamulus_reconnect_attempts == 2


def test_ack_rejects_pending_replacement_and_unavailable_rpc() -> None:
    bridge = _bridge()
    process = _process(108)
    _prime_recovery(bridge, attempts=2)
    _publish_recovery_process(bridge, process)
    bridge._pending_jamulus_launch_cancel = MagicMock()

    assert not bridge.mark_jamulus_reconnect_authenticated(
        generation=1,
        process_id=108,
    )
    bridge._pending_jamulus_launch_cancel = None
    assert not bridge.mark_jamulus_reconnect_authenticated(
        generation=1,
        process_id=108,
    )


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
@patch("services.bridge_service.time.sleep")
def test_reconnect_popen_preserves_count_until_authenticated(
    _sleep,
    popen,
) -> None:
    bridge = _bridge()
    process = _process(109)
    popen.return_value = process
    _prime_recovery(bridge, attempts=3, generation=8)
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    assert bridge.launch_jamulus(manual=False, reconnect=True)

    snapshot = bridge.jamulus_recovery_snapshot()
    assert snapshot.process_id == 109
    assert snapshot.generation == 1
    assert snapshot.recovery_generation == 8
    assert snapshot.attempts_started == 3
    assert snapshot.rpc_freshness is JamulusRpcFreshness.STARTING
    assert not any(
        call.args == ("metric_jamulus_reconnect_success",)
        for call in bridge.metrics_service.increment.call_args_list
    )


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_delayed_monitor_cannot_start_after_stop(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    process = _process(117)
    popen.return_value = process
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(manual=True)
        launch_worker = _QueuedThread.targets.pop(0)
        launch_worker()
        monitor_worker = _QueuedThread.targets.pop(0)

    assert bridge.stop_jamulus()
    monitor_worker()

    bridge.jamulus_controller.start.assert_not_called()
    assert bridge.jamulus_process is None


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_delayed_monitor_cannot_attach_to_replacement(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    original = _process(118)
    replacement = _process(119)
    popen.return_value = original
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(manual=True)
        launch_worker = _QueuedThread.targets.pop(0)
        launch_worker()
        monitor_worker = _QueuedThread.targets.pop(0)

    with bridge._reconnect_lock:
        bridge._jamulus_process_generation_counter += 1
        bridge.jamulus_process = replacement
        bridge._jamulus_process_generation = (
            bridge._jamulus_process_generation_counter
        )
    monitor_worker()

    bridge.jamulus_controller.start.assert_not_called()
    assert bridge.jamulus_process is replacement
    assert bridge.stop_jamulus()


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
def test_cancelled_post_popen_uses_kill_after_terminate_failure(
    popen,
) -> None:
    bridge = _bridge()
    process = _process(120)
    process.terminate.side_effect = OSError("terminate refused")
    popen.return_value = process
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    shutdown = {"requested": False}
    bridge.shutdown_requested = lambda: shutdown["requested"]

    def _cancel_after_spawn(seconds: float) -> None:
        if seconds == 0.4:
            shutdown["requested"] = True

    with patch(
        "services.bridge_service.time.sleep",
        side_effect=_cancel_after_spawn,
    ):
        assert bridge.launch_jamulus(manual=True)

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert bridge.jamulus_process is None
    assert "client" not in bridge._runtime_component_lease_claims


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
def test_cancelled_post_popen_retains_child_and_lease_when_kill_fails(
    popen,
) -> None:
    bridge = _bridge()
    process = _process(121)
    process.terminate.side_effect = OSError("terminate refused")
    process.kill.side_effect = OSError("kill refused")
    popen.return_value = process
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    shutdown = {"requested": False}
    bridge.shutdown_requested = lambda: shutdown["requested"]

    def _cancel_after_spawn(seconds: float) -> None:
        if seconds == 0.4:
            shutdown["requested"] = True

    with patch(
        "services.bridge_service.time.sleep",
        side_effect=_cancel_after_spawn,
    ):
        assert bridge.launch_jamulus(manual=True)

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert bridge.jamulus_process is process
    assert bridge.jamulus_state == "Stop failed"
    assert "client" in bridge._runtime_component_lease_claims

    # Release the test-only lock after proving the fail-closed state.
    shutdown["requested"] = False
    process.terminate.side_effect = None
    process.kill.side_effect = None
    assert bridge.stop_jamulus()


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_force_restart_mutates_only_inside_queued_lifecycle_worker(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    old_process = _process(110)
    new_process = _process(111)
    popen.return_value = new_process
    _prime_recovery(bridge, attempts=1)
    _publish_recovery_process(
        bridge,
        old_process,
        process_generation=4,
        recovery_generation=0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=True)

    observed_at = 100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS + 1.0
    with (
        patch("services.bridge_service.time.monotonic", return_value=observed_at),
        patch("services.bridge_service.threading.Thread", _QueuedThread),
    ):
        assert bridge.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=True,
        )

        old_process.terminate.assert_not_called()
        bridge.jamulus_controller.stop.assert_not_called()
        assert bridge.jamulus_process is old_process
        bridge._is_rpc_port_in_use.assert_not_called()

        _QueuedThread.targets.pop(0)()

    old_process.terminate.assert_called_once()
    bridge.jamulus_controller.stop.assert_called_once()
    assert bridge.jamulus_process is new_process
    assert bridge.jamulus_reconnect_attempts == 1


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_stale_force_restart_worker_cannot_kill_newer_process(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    old_process = _process(112)
    newer_process = _process(113)
    _prime_recovery(bridge, attempts=1)
    _publish_recovery_process(
        bridge,
        old_process,
        process_generation=5,
        recovery_generation=0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=True,
        )
    with bridge._reconnect_lock:
        bridge.jamulus_process = newer_process
        bridge._jamulus_process_generation_counter = 6
        bridge._jamulus_process_generation = 6

    _QueuedThread.targets.pop(0)()

    old_process.terminate.assert_not_called()
    newer_process.terminate.assert_not_called()
    popen.assert_not_called()
    assert bridge.jamulus_process is newer_process


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
@patch("services.bridge_service.time.sleep")
def test_dead_replacement_monitor_stop_failure_retains_retryable_owner(
    _sleep,
    popen,
) -> None:
    bridge = _bridge()
    dead_process = _process(126, return_code=11)
    replacement = _process(127)
    _prime_recovery(bridge, attempts=2, generation=6)
    _publish_recovery_process(
        bridge,
        dead_process,
        process_generation=13,
        recovery_generation=6,
    )
    bridge._jamulus_native_setup_process_generation = 13
    bridge._jamulus_native_setup_deadline = 700.0
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    bridge.jamulus_controller.stop.side_effect = RuntimeError("thread stuck")

    assert bridge.launch_jamulus(manual=False, reconnect=True)

    popen.assert_not_called()
    assert bridge.jamulus_process is dead_process
    assert bridge._jamulus_process_generation == 13
    assert bridge._jamulus_native_setup_process_generation == 13
    assert bridge._jamulus_native_setup_deadline == 700.0
    assert bridge.jamulus_reconnect_inflight is False

    bridge.jamulus_controller.stop.side_effect = None
    popen.return_value = replacement

    assert bridge.launch_jamulus(manual=False, reconnect=True)
    popen.assert_called_once()
    assert bridge.jamulus_process is replacement


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_stale_dead_replacement_worker_preserves_newer_process(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    dead_process = _process(128, return_code=11)
    newer_process = _process(129)
    _prime_recovery(bridge, attempts=1, generation=7)
    _publish_recovery_process(
        bridge,
        dead_process,
        process_generation=14,
        recovery_generation=7,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(manual=False, reconnect=True)

    with bridge._reconnect_lock:
        bridge.jamulus_process = newer_process
        bridge._jamulus_process_generation_counter = 15
        bridge._jamulus_process_generation = 15
        bridge._jamulus_process_started_at = 500.0
        bridge._jamulus_native_setup_process_generation = 15
        bridge._jamulus_native_setup_deadline = 900.0

    _QueuedThread.targets.pop(0)()

    bridge.jamulus_controller.stop.assert_not_called()
    popen.assert_not_called()
    assert bridge.jamulus_process is newer_process
    assert bridge._jamulus_process_generation == 15
    assert bridge._jamulus_process_started_at == 500.0
    assert bridge._jamulus_native_setup_process_generation == 15
    assert bridge._jamulus_native_setup_deadline == 900.0
    assert bridge.jamulus_reconnect_inflight is False


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_queued_force_restart_does_not_kill_exact_process_that_recovers(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    process = _process(122)
    observed_at = time.monotonic()
    _prime_recovery(bridge, attempts=2, generation=5)
    _publish_recovery_process(
        bridge,
        process,
        process_generation=10,
        recovery_generation=5,
        started_at=observed_at - 5.0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=True,
        )

    _set_rpc_monitor(
        bridge,
        process_generation=10,
        process_id=122,
        last_activity_at=observed_at - 1.0,
        last_activity_age_seconds=1.0,
    )
    _QueuedThread.targets.pop(0)()

    process.terminate.assert_not_called()
    popen.assert_not_called()
    assert bridge.jamulus_process is process
    assert bridge.jamulus_reconnect_inflight is False
    assert bridge.stop_jamulus()


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.time.sleep")
def test_queued_force_restart_may_replace_after_local_roster_deadline(
    _sleep,
    popen,
) -> None:
    _QueuedThread.targets = []
    bridge = _bridge()
    old_process = _process(123)
    new_process = _process(124)
    observed_at = time.monotonic()
    _prime_recovery(bridge, attempts=RECONNECT_MAX_ATTEMPTS, generation=6)
    _publish_recovery_process(
        bridge,
        old_process,
        process_generation=11,
        recovery_generation=6,
        started_at=(
            observed_at - RECONNECT_LOCAL_ROSTER_GRACE_SECONDS - 1.0
        ),
    )
    _set_rpc_monitor(
        bridge,
        process_generation=11,
        process_id=123,
        last_activity_at=observed_at - 1.0,
        last_activity_age_seconds=1.0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    popen.return_value = new_process

    with patch("services.bridge_service.threading.Thread", _QueuedThread):
        assert bridge.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=True,
        )
        _QueuedThread.targets.pop(0)()

    old_process.terminate.assert_called_once()
    popen.assert_called_once()
    assert bridge.jamulus_process is new_process

    # The delayed monitor must also honor cleanup after the replacement.
    assert bridge.stop_jamulus()
    _QueuedThread.targets.pop(0)()
    bridge.jamulus_controller.start.assert_not_called()


@patch("services.bridge_service.subprocess.Popen")
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
@patch("services.bridge_service.time.sleep")
def test_force_restart_monitor_stop_failure_retains_owned_process(
    _sleep,
    popen,
) -> None:
    bridge = _bridge()
    old_process = _process(116)
    _prime_recovery(bridge, attempts=2)
    _publish_recovery_process(
        bridge,
        old_process,
        process_generation=9,
        recovery_generation=0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge.jamulus_controller.stop.side_effect = RuntimeError("thread stuck")

    assert bridge.launch_jamulus(
        manual=False,
        reconnect=True,
        force_restart=True,
    )

    old_process.terminate.assert_not_called()
    popen.assert_not_called()
    assert bridge.jamulus_process is old_process
    assert bridge.jamulus_reconnect_attempts == 2
    assert bridge.jamulus_reconnect_inflight is False


@patch(
    "services.bridge_service.subprocess.Popen",
    side_effect=OSError("spawn failed"),
)
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
@patch("services.bridge_service.time.sleep")
def test_failed_force_replacement_with_no_process_can_retry(
    _sleep,
    popen,
) -> None:
    bridge = _bridge()
    old_process = _process(114)
    _prime_recovery(bridge, attempts=1)
    _publish_recovery_process(
        bridge,
        old_process,
        process_generation=7,
        recovery_generation=0,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    observed_at = 100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS + 1.0
    with patch(
        "services.bridge_service.time.monotonic",
        return_value=observed_at,
    ):
        assert bridge.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=True,
        )

    assert popen.call_count == 3
    assert bridge.jamulus_process is None
    assert bridge.jamulus_reconnect_attempts == 1
    bridge.launch_jamulus = MagicMock(return_value=True)
    bridge.jamulus_next_reconnect_at = 0.0
    bridge._attempt_auto_reconnect_jamulus(now=100.0)
    bridge.launch_jamulus.assert_called_once_with(
        manual=False,
        reconnect=True,
        force_restart=False,
    )
    assert bridge.jamulus_reconnect_attempts == 2


@patch(
    "services.bridge_service.subprocess.Popen",
    side_effect=OSError("spawn failed"),
)
@patch("services.bridge_service.threading.Thread", _ImmediateThread)
@patch("services.bridge_service.time.sleep")
def test_fifth_worker_failure_publishes_exhaustion_without_sixth_tick(
    _sleep,
    popen,
) -> None:
    bridge = _bridge()
    _prime_recovery(
        bridge,
        attempts=RECONNECT_MAX_ATTEMPTS,
        generation=11,
    )
    bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)

    assert bridge.launch_jamulus(manual=False, reconnect=True)

    assert popen.call_count == 3
    snapshot = bridge.jamulus_recovery_snapshot()
    assert snapshot.attempts_started == RECONNECT_MAX_ATTEMPTS
    assert snapshot.inflight is False
    assert snapshot.exhausted is True


def test_attempt_five_is_not_exhausted_during_startup_grace() -> None:
    bridge = _bridge()
    process = _process(115)
    _prime_recovery(
        bridge,
        attempts=RECONNECT_MAX_ATTEMPTS,
        generation=9,
    )
    _publish_recovery_process(
        bridge,
        process,
        process_generation=8,
        recovery_generation=9,
        started_at=100.0,
    )
    bridge.launch_jamulus = MagicMock()

    bridge._attempt_auto_reconnect_jamulus(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS - 0.001
    )
    within = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS - 0.001
    )
    bridge._attempt_auto_reconnect_jamulus(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )
    expired = bridge.jamulus_recovery_snapshot(
        now=100.0 + RECONNECT_RPC_STARTUP_GRACE_SECONDS
    )

    assert within.exhausted is False
    assert within.rpc_freshness is JamulusRpcFreshness.STARTING
    assert expired.exhausted is True
    assert expired.attempts_started == RECONNECT_MAX_ATTEMPTS
    bridge.launch_jamulus.assert_not_called()


def test_fresh_rpc_without_local_roster_is_bounded_and_exhausts_attempt_five() -> None:
    bridge = _bridge()
    process = _process(125)
    _prime_recovery(
        bridge,
        attempts=RECONNECT_MAX_ATTEMPTS,
        generation=12,
    )
    _publish_recovery_process(
        bridge,
        process,
        process_generation=12,
        recovery_generation=12,
        started_at=100.0,
    )
    _set_rpc_monitor(
        bridge,
        process_generation=12,
        process_id=125,
        last_activity_at=100.5,
        last_activity_age_seconds=1.0,
    )
    bridge.launch_jamulus = MagicMock()

    before_deadline = 100.0 + RECONNECT_LOCAL_ROSTER_GRACE_SECONDS - 0.001
    at_deadline = 100.0 + RECONNECT_LOCAL_ROSTER_GRACE_SECONDS
    bridge._attempt_auto_reconnect_jamulus(now=before_deadline)
    waiting = bridge.jamulus_recovery_snapshot(now=before_deadline)
    bridge._attempt_auto_reconnect_jamulus(now=at_deadline)
    exhausted = bridge.jamulus_recovery_snapshot(now=at_deadline)

    assert waiting.rpc_freshness is JamulusRpcFreshness.FRESH
    assert waiting.exhausted is False
    assert exhausted.rpc_freshness is JamulusRpcFreshness.FRESH
    assert exhausted.exhausted is True
    assert exhausted.active is True
    assert exhausted.attempts_started == RECONNECT_MAX_ATTEMPTS
    bridge.launch_jamulus.assert_not_called()


def test_terminal_reconnect_preflight_is_active_exhausted_and_not_intended() -> None:
    bridge = _bridge()
    _prime_recovery(bridge, attempts=2, generation=13)
    bridge.settings.jamulus_server = ""

    assert not bridge.launch_jamulus(manual=False, reconnect=True)

    snapshot = bridge.jamulus_recovery_snapshot(now=100.0)
    assert snapshot.active is True
    assert snapshot.exhausted is True
    assert snapshot.launch_intended is False
    assert snapshot.inflight is False
    assert snapshot.recovery_generation == 13


@patch("services.bridge_service.subprocess.Popen")
def test_poll_exception_retains_owned_process_and_component_lease(
    popen,
) -> None:
    bridge = _bridge()
    process = _process(126)
    process.poll.side_effect = OSError("poll unavailable")
    acquired, _detail = bridge._acquire_runtime_component_lease("client")
    assert acquired
    bridge.jamulus_process = process

    assert not bridge.stop_jamulus()

    popen.assert_not_called()
    assert bridge.jamulus_process is process
    assert "client" in bridge._runtime_component_lease_claims
    process.terminate.assert_not_called()
    process.kill.assert_not_called()

    # Release the test-only lock after proving unknown process state fails closed.
    process.poll.side_effect = None
    process.poll.return_value = None
    assert bridge.stop_jamulus()


def test_sixth_attempt_never_schedules() -> None:
    bridge = _bridge()
    _prime_recovery(
        bridge,
        attempts=RECONNECT_MAX_ATTEMPTS,
        generation=10,
    )
    bridge.jamulus_process = None
    bridge.launch_jamulus = MagicMock()

    bridge._attempt_auto_reconnect_jamulus(now=1000.0)
    bridge._attempt_auto_reconnect_jamulus(now=2000.0)

    bridge.launch_jamulus.assert_not_called()
    assert bridge.jamulus_reconnect_attempts == RECONNECT_MAX_ATTEMPTS
    assert bridge.jamulus_recovery_snapshot().exhausted is True

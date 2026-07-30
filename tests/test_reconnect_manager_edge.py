"""Edge tests for reconnect / launch logic — rewritten against BridgeService.

BridgeService owns all reconnect state and launch orchestration; these tests
verify it directly rather than going through the now-legacy WebJamEnhancedApp
property shims.
"""
from __future__ import annotations

import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.jamulus_rpc_client import (
    JamulusRpcMonitorIdentity,
    JamulusRpcMonitorSnapshot,
)
from tests.support.component_store import isolated_component_store_root


_HOST_ABSOLUTE_JAMULUS = str(
    Path(__file__).resolve().parent / "fixtures" / "Jamulus-test"
)


def _make_settings(
    jamulus_server: str = "jam.example.com",
    jamulus_port: int = 22124,
    jamulus_rpc_port: int = 22222,
    jamulus_candidates: tuple = ("C:/Jamulus.exe",),
    webex_url: str = "https://example.webex.com/meet/test",
) -> MagicMock:
    s = MagicMock()
    s.jamulus_server = jamulus_server
    s.jamulus_port = jamulus_port
    s.jamulus_rpc_port = jamulus_rpc_port
    s.jamulus_candidates = list(jamulus_candidates)
    s.webex_url = webex_url
    s.musician_name = "Test Musician"
    s.host_server_enabled = False
    return s


def _make_bridge(
    shutdown_requested: bool = False,
    **overrides,
):
    from services.bridge_service import BridgeService

    settings = _make_settings()
    repository = MagicMock()
    repository.get_setting.return_value = "1"  # auto_reconnect_enabled

    ui_callbacks = {
        "set_status_banner": MagicMock(),
        "refresh_readiness": MagicMock(),
        "show_actionable_error": MagicMock(),
        "show_message": MagicMock(),
        "shutdown_requested": lambda: shutdown_requested,
        "schedule_ui_callback": lambda f: f(),
    }

    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=repository,
        settings=settings,
        ui_callbacks=ui_callbacks,
        component_store_root=isolated_component_store_root(),
    )
    bridge.jamulus_controller.rpc_monitor_snapshot_for.return_value = None
    for attr, val in overrides.items():
        setattr(bridge, attr, val)
    return bridge


def _set_rpc_monitor(
    bridge,
    *,
    process_generation: int,
    process_id: int,
    available: bool = True,
    authenticated: bool = True,
    activity_at: float = 99.0,
    age_seconds: float = 1.0,
) -> None:
    bridge.jamulus_controller.rpc_monitor_snapshot_for.return_value = (
        JamulusRpcMonitorSnapshot(
            identity=JamulusRpcMonitorIdentity(
                monitor_epoch=1,
                process_generation=process_generation,
                process_id=process_id,
            ),
            running=True,
            available=available,
            authenticated=authenticated,
            last_activity_at=activity_at,
            last_activity_age_seconds=age_seconds,
        )
    )


class _ImmediateThread:
    """Runs target synchronously instead of spawning a real thread."""

    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target()


class TestReconnectManagerEdge(unittest.TestCase):
    # ------------------------------------------------------------------
    # Delay calculation
    # ------------------------------------------------------------------
    def test_reconnect_delay_is_exponential_and_capped(self):
        bridge = _make_bridge()
        self.assertAlmostEqual(bridge._reconnect_delay_seconds(1), 1.5)
        self.assertAlmostEqual(bridge._reconnect_delay_seconds(2), 3.0)
        self.assertAlmostEqual(bridge._reconnect_delay_seconds(3), 6.0)
        self.assertAlmostEqual(bridge._reconnect_delay_seconds(10), 45.0)

    # ------------------------------------------------------------------
    # attempt_auto_reconnects — top-level guard
    # ------------------------------------------------------------------
    def test_legacy_auto_reconnect_disabled_cannot_strand_modern_recovery(self):
        bridge = _make_bridge()
        bridge.repository.get_setting.return_value = "0"  # disabled
        bridge.jamulus_launch_intended = True
        bridge.jamulus_process = MagicMock(pid=200)
        bridge.jamulus_process.poll.return_value = 1
        launch_j = MagicMock()
        launch_w = MagicMock()
        bridge.launch_jamulus = launch_j
        bridge.launch_webex = launch_w

        bridge.attempt_auto_reconnects()

        bridge.metrics_service.increment.assert_any_call(
            "metric_jamulus_reconnect_attempt"
        )
        launch_j.assert_called_once_with(
            manual=False,
            reconnect=True,
            force_restart=False,
        )
        launch_w.assert_not_called()

    def test_auto_reconnect_skips_when_shutdown_requested(self):
        bridge = _make_bridge(shutdown_requested=True)
        bridge.jamulus_launch_intended = True
        launch_j = MagicMock()
        launch_w = MagicMock()
        bridge.launch_jamulus = launch_j
        bridge.launch_webex = launch_w

        bridge.attempt_auto_reconnects()

        bridge.metrics_service.increment.assert_not_called()
        launch_j.assert_not_called()
        launch_w.assert_not_called()

    def test_hosted_server_supervisor_is_independent_of_client_recovery(self):
        bridge = _make_bridge()
        bridge._restart_hosted_server_if_died = MagicMock()
        bridge._attempt_auto_reconnect_jamulus = MagicMock()

        bridge.attempt_hosted_server_recovery()

        bridge._restart_hosted_server_if_died.assert_called_once_with()
        bridge._attempt_auto_reconnect_jamulus.assert_not_called()

    # ------------------------------------------------------------------
    # Jamulus reconnect logic
    # ------------------------------------------------------------------
    def test_auto_reconnect_jamulus_triggers_when_intended_and_process_down(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = True
        bridge.jamulus_process = MagicMock()
        bridge.jamulus_process.poll.return_value = 1  # process exited
        bridge.jamulus_reconnect_attempts = 0
        bridge.jamulus_next_reconnect_at = 0.0
        bridge.jamulus_reconnect_inflight = False
        launch_j = MagicMock()
        bridge.launch_jamulus = launch_j

        bridge._attempt_auto_reconnect_jamulus(now=100.0)

        bridge.metrics_service.increment.assert_any_call("metric_jamulus_reconnect_attempt")
        launch_j.assert_called_once_with(manual=False, reconnect=True, force_restart=False)
        self.assertEqual(bridge.jamulus_reconnect_attempts, 1)
        self.assertGreater(bridge.jamulus_next_reconnect_at, 100.0)
        self.assertTrue(bridge.jamulus_reconnect_inflight)

    def test_auto_reconnect_jamulus_forces_restart_when_process_stalls(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = True
        bridge.jamulus_process = MagicMock(pid=201)
        bridge.jamulus_process.poll.return_value = None
        bridge._jamulus_process_generation = 2
        bridge._jamulus_process_started_at = 1.0
        _set_rpc_monitor(
            bridge,
            process_generation=2,
            process_id=201,
            activity_at=70.0,
            age_seconds=30.0,
        )
        bridge.jamulus_reconnect_attempts = 2
        bridge.jamulus_next_reconnect_at = 0.0
        bridge.jamulus_reconnect_inflight = False
        bridge.jamulus_launch_intended = True
        launch_j = MagicMock()
        bridge.launch_jamulus = launch_j

        bridge._attempt_auto_reconnect_jamulus(now=100.0)

        bridge.metrics_service.increment.assert_any_call("metric_jamulus_reconnect_attempt")
        launch_j.assert_called_once_with(
            manual=False, reconnect=True, force_restart=True
        )

    def test_fresh_live_process_waits_for_authenticated_ack(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = True
        bridge.jamulus_process = MagicMock(pid=202)
        bridge.jamulus_process.poll.return_value = None
        bridge._jamulus_process_generation = 3
        bridge._jamulus_process_started_at = 50.0
        _set_rpc_monitor(
            bridge,
            process_generation=3,
            process_id=202,
            activity_at=99.0,
            age_seconds=1.0,
        )
        bridge.jamulus_reconnect_attempts = 2
        bridge.jamulus_next_reconnect_at = 0.0
        bridge.jamulus_reconnect_inflight = False
        launch_j = MagicMock()
        bridge.launch_jamulus = launch_j

        bridge._attempt_auto_reconnect_jamulus(now=100.0)

        # Popen/liveness is not authenticated recovery. Preserve history until
        # the application acknowledges fresh RPC + local roster for the
        # current generation/PID.
        self.assertEqual(bridge.jamulus_reconnect_attempts, 2)
        self.assertEqual(bridge.jamulus_next_reconnect_at, 0.0)
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        launch_j.assert_not_called()

    def test_running_process_never_resets_inflight_recovery_by_itself(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = True
        bridge.jamulus_process = MagicMock()
        bridge.jamulus_process.poll.return_value = None  # process still alive
        bridge.jamulus_reconnect_attempts = 3
        bridge.jamulus_next_reconnect_at = 999.0
        bridge.jamulus_reconnect_inflight = True

        bridge._attempt_auto_reconnect_jamulus(now=5.0)

        self.assertEqual(bridge.jamulus_reconnect_attempts, 3)
        self.assertEqual(bridge.jamulus_next_reconnect_at, 999.0)
        self.assertTrue(bridge.jamulus_reconnect_inflight)

    # ------------------------------------------------------------------
    # External Webex is never auto-reconnected
    # ------------------------------------------------------------------
    def test_auto_reconnect_tick_never_reopens_external_webex(self):
        bridge = _make_bridge()
        bridge.webex_state = "Open failed"
        launch_w = MagicMock()
        bridge.launch_webex = launch_w

        bridge.attempt_auto_reconnects()

        launch_w.assert_not_called()

    # ------------------------------------------------------------------
    # launch_jamulus / launch_webex public helpers
    # ------------------------------------------------------------------
    def test_manual_launch_resets_reconnect_state(self):
        bridge = _make_bridge()
        bridge.jamulus_launch_intended = False
        bridge.jamulus_reconnect_attempts = 4
        bridge.jamulus_next_reconnect_at = 12.0

        # Patch the internal thread to verify state before thread body runs.
        with patch("services.bridge_service.threading.Thread") as thread_cls:
            thread_cls.return_value = MagicMock()
            bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertTrue(bridge.jamulus_launch_intended)
        self.assertEqual(bridge.jamulus_reconnect_attempts, 0)
        self.assertEqual(bridge.jamulus_next_reconnect_at, 0.0)
        bridge.metrics_service.increment.assert_any_call("metric_jamulus_launch_attempt")

        with patch("services.bridge_service.threading.Thread") as thread_cls:
            thread_cls.return_value = MagicMock()
            bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Opening…")
        bridge.metrics_service.increment.assert_any_call("metric_webex_open_attempt")

    @patch("services.bridge_service.subprocess.Popen")
    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    def test_launch_jamulus_sets_running_state_after_process_starts(
        self, _thread_mock, popen_mock
    ):
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        popen_mock.return_value = fake_proc
        bridge = _make_bridge()
        bridge.settings.jamulus_candidates = [_HOST_ABSOLUTE_JAMULUS]
        bridge.find_jamulus = MagicMock(
            return_value=_HOST_ABSOLUTE_JAMULUS
        )
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        bridge.jamulus_process = None
        bridge.jamulus_reconnect_attempts = 1
        bridge.jamulus_next_reconnect_at = 5.0
        bridge.jamulus_reconnect_inflight = True

        bridge.launch_jamulus(manual=True, reconnect=False)

        self.assertEqual(bridge.jamulus_state, "Running")
        bridge.metrics_service.increment.assert_any_call("metric_jamulus_launch_success")
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        self.assertEqual(bridge.jamulus_reconnect_attempts, 0)
        self.assertEqual(bridge.jamulus_next_reconnect_at, 0.0)

    @patch("services.bridge_service.subprocess.Popen")
    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    def test_launch_jamulus_terminates_process_when_shutdown_requested_during_launch(
        self, _thread_mock, popen_mock
    ):
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        popen_mock.return_value = fake_proc

        # Signal shutdown immediately after Popen starts
        shutdown_flag = {"value": False}
        bridge = _make_bridge()
        bridge.shutdown_requested = lambda: shutdown_flag["value"]
        bridge.settings.jamulus_candidates = [_HOST_ABSOLUTE_JAMULUS]
        bridge.find_jamulus = MagicMock(
            return_value=_HOST_ABSOLUTE_JAMULUS
        )
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)
        bridge.jamulus_process = None
        bridge.jamulus_reconnect_inflight = True

        def _popen_and_flag(*args, **kwargs):
            shutdown_flag["value"] = True
            return fake_proc

        popen_mock.side_effect = _popen_and_flag

        bridge.launch_jamulus(manual=True, reconnect=False)

        fake_proc.terminate.assert_called_once()
        self.assertIsNone(bridge.jamulus_process)
        self.assertFalse(bridge.jamulus_reconnect_inflight)
        self.assertNotIn(
            "metric_jamulus_launch_success",
            [call.args[0] for call in bridge.metrics_service.increment.call_args_list],
        )

    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    def test_launch_webex_sets_opened_state_after_success(self, _thread_mock):
        bridge = _make_bridge()
        bridge.webex_controller.join_meeting_url.return_value = True
        bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Opened externally")
        bridge.metrics_service.increment.assert_any_call("metric_webex_open_success")
        bridge.set_status_banner.assert_any_call(
            "Opened externally—finish joining in Webex."
        )
        self.assertNotIn(
            "metric_webex_open_failed",
            [call.args[0] for call in bridge.metrics_service.increment.call_args_list],
        )

    @patch("services.bridge_service.threading.Thread",
           side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw))
    def test_launch_webex_skips_success_reporting_when_shutdown_requested_during_open(
        self, _thread_mock
    ):
        shutdown_flag = {"value": False}
        bridge = _make_bridge()
        bridge.shutdown_requested = lambda: shutdown_flag["value"]
        bridge.webex_state = "Not opened"

        def _join_and_shutdown(_url):
            shutdown_flag["value"] = True
            return True

        bridge.webex_controller.join_meeting_url.side_effect = _join_and_shutdown

        bridge.launch_webex(manual=True, reconnect=False)

        bridge.webex_controller.join_meeting_url.assert_called()
        self.assertEqual(bridge.webex_state, "Not opened")
        self.assertNotIn(
            "metric_webex_open_success",
            [call.args[0] for call in bridge.metrics_service.increment.call_args_list],
        )

    @patch(
        "services.bridge_service.threading.Thread",
        side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw),
    )
    def test_webex_open_failure_does_not_interrupt_running_jamulus(
        self, _thread_mock
    ):
        bridge = _make_bridge()
        bridge.jamulus_state = "Running"
        bridge.webex_controller.join_meeting_url.return_value = False
        bridge.webex_controller.last_error = "RuntimeError"

        bridge.launch_webex(manual=True, reconnect=False)

        self.assertEqual(bridge.webex_state, "Open failed")
        self.assertEqual(bridge.jamulus_state, "Running")
        bridge.jamulus_controller.stop.assert_not_called()

    def test_invalidated_webex_open_cannot_publish_stale_success(self):
        bridge = _make_bridge()
        entered = threading.Event()
        release = threading.Event()
        workers: list[threading.Thread] = []
        real_thread = threading.Thread

        def _join_meeting(_url) -> bool:
            entered.set()
            self.assertTrue(release.wait(timeout=2.0))
            return True

        def _thread_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        bridge.webex_controller.join_meeting_url.side_effect = _join_meeting
        with patch(
            "services.bridge_service.threading.Thread",
            side_effect=_thread_factory,
        ):
            bridge.launch_webex(manual=True, reconnect=False)
            self.assertTrue(entered.wait(timeout=2.0))
            bridge.invalidate_webex_launch()
            bridge.webex_state = "Not opened"
            release.set()
            workers[0].join(timeout=2.0)

        self.assertFalse(workers[0].is_alive())
        self.assertEqual(bridge.webex_state, "Not opened")
        self.assertNotIn(
            "metric_webex_open_success",
            [call.args[0] for call in bridge.metrics_service.increment.call_args_list],
        )

    def test_concurrent_webex_open_requests_are_single_flight(self):
        bridge = _make_bridge()
        entered = threading.Event()
        release = threading.Event()
        workers: list[threading.Thread] = []
        real_thread = threading.Thread

        def _join_meeting(_url) -> bool:
            entered.set()
            self.assertTrue(release.wait(timeout=2.0))
            return True

        def _thread_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        bridge.webex_controller.join_meeting_url.side_effect = _join_meeting
        with patch(
            "services.bridge_service.threading.Thread",
            side_effect=_thread_factory,
        ):
            self.assertTrue(bridge.launch_webex(manual=True))
            self.assertTrue(entered.wait(timeout=2.0))
            self.assertFalse(bridge.launch_webex(manual=True))
            release.set()
            workers[0].join(timeout=2.0)

        self.assertEqual(len(workers), 1)
        self.assertEqual(
            bridge.webex_controller.join_meeting_url.call_count,
            1,
        )
        self.assertFalse(bridge._webex_launch_inflight)

    @patch(
        "services.bridge_service.threading.Thread",
        side_effect=lambda *a, **kw: _ImmediateThread(*a, **kw),
    )
    def test_invalidation_immediately_after_begin_never_leaks_inflight(
        self,
        _thread_mock,
    ):
        bridge = _make_bridge()
        original_begin = bridge._begin_webex_launch

        def _begin_and_invalidate():
            generation = original_begin()
            bridge.invalidate_webex_launch()
            bridge.webex_state = "Not opened"
            return generation

        bridge._begin_webex_launch = _begin_and_invalidate
        bridge.launch_webex(manual=True)

        self.assertFalse(bridge._webex_launch_inflight)
        self.assertEqual(bridge.webex_state, "Not opened")
        bridge.webex_controller.join_meeting_url.assert_not_called()

    def test_webex_worker_uses_captured_url_when_settings_change(self):
        bridge = _make_bridge()
        old_url = "https://old.webex.com/meet/original"
        new_url = "https://new.webex.com/meet/replacement"
        bridge.settings.webex_url = old_url
        entered = threading.Event()
        release = threading.Event()
        received: list[str] = []
        workers: list[threading.Thread] = []
        real_thread = threading.Thread

        def _join_meeting(url: str) -> bool:
            received.append(url)
            entered.set()
            self.assertTrue(release.wait(timeout=2.0))
            return True

        def _thread_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        bridge.webex_controller.join_meeting_url.side_effect = _join_meeting
        with patch(
            "services.bridge_service.threading.Thread",
            side_effect=_thread_factory,
        ):
            bridge.launch_webex(manual=True)
            self.assertTrue(entered.wait(timeout=2.0))
            bridge.settings.webex_url = new_url
            bridge.invalidate_webex_launch()
            bridge.webex_state = "Not opened"
            release.set()
            workers[0].join(timeout=2.0)

        self.assertEqual(received, [old_url])
        self.assertEqual(bridge.webex_state, "Not opened")
        bridge.webex_controller.join_meeting.assert_not_called()

    def test_invalidated_webex_open_cannot_publish_stale_failure(self):
        bridge = _make_bridge()
        entered = threading.Event()
        release = threading.Event()
        workers: list[threading.Thread] = []
        real_thread = threading.Thread

        def _join_meeting(_url) -> bool:
            entered.set()
            self.assertTrue(release.wait(timeout=2.0))
            raise RuntimeError("old handoff failed")

        def _thread_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        bridge.webex_controller.join_meeting_url.side_effect = _join_meeting
        with patch(
            "services.bridge_service.threading.Thread",
            side_effect=_thread_factory,
        ):
            bridge.launch_webex(manual=True, reconnect=False)
            self.assertTrue(entered.wait(timeout=2.0))
            bridge.invalidate_webex_launch()
            bridge.webex_state = "Not opened"
            release.set()
            workers[0].join(timeout=2.0)

        self.assertFalse(workers[0].is_alive())
        self.assertEqual(bridge.webex_state, "Not opened")
        bridge.show_actionable_error.assert_not_called()
        self.assertNotIn(
            "metric_webex_open_failed",
            [call.args[0] for call in bridge.metrics_service.increment.call_args_list],
        )

    def test_invalidated_webex_open_drops_queued_success_and_error_ui(self):
        for opened in (True, False):
            with self.subTest(opened=opened):
                bridge = _make_bridge()
                queued: list[object] = []
                bridge.schedule_ui_callback = queued.append
                bridge.webex_controller.join_meeting_url.return_value = opened
                bridge.webex_controller.last_error = "external launch refused"

                with patch(
                    "services.bridge_service.threading.Thread",
                    side_effect=lambda *args, **kwargs: _ImmediateThread(
                        *args, **kwargs
                    ),
                ):
                    bridge.launch_webex(manual=True, reconnect=False)

                self.assertTrue(queued)
                bridge.invalidate_webex_launch()
                bridge.webex_state = "Not opened"
                for callback in queued:
                    callback()

                self.assertEqual(bridge.webex_state, "Not opened")
                bridge.refresh_readiness.assert_not_called()
                bridge.show_actionable_error.assert_not_called()
                self.assertFalse(
                    any(
                        call.args
                        and call.args[0]
                        == "Opened externally—finish joining in Webex."
                        for call in bridge.set_status_banner.call_args_list
                    )
                )


class TestStopJamulus(unittest.TestCase):
    """Tests for the new stop_jamulus method (v0.4.4 — toggle UX)."""

    def test_stop_jamulus_terminates_running_process_and_clears_intent(self):
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None  # alive
        bridge.jamulus_process = proc
        bridge.jamulus_launch_intended = True
        bridge.jamulus_state = "Running"

        result = bridge.stop_jamulus()

        self.assertTrue(result)
        proc.terminate.assert_called_once()
        self.assertFalse(bridge.jamulus_launch_intended)
        self.assertEqual(bridge.jamulus_state, "Stopped")
        self.assertIsNone(bridge.jamulus_process)
        # Auto-reconnect must be disabled so the next tick doesn't immediately relaunch
        self.assertEqual(bridge.jamulus_reconnect_attempts, 0)

    def test_stop_jamulus_is_successful_when_already_not_running(self):
        bridge = _make_bridge()
        bridge.jamulus_process = None
        bridge.jamulus_launch_intended = False

        result = bridge.stop_jamulus()

        self.assertTrue(result)
        self.assertEqual(bridge.jamulus_state, "Stopped")

    def test_stop_jamulus_force_kills_when_terminate_times_out(self):
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="jamulus", timeout=2.0)
        bridge.jamulus_process = proc

        bridge.stop_jamulus()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_stop_jamulus_keeps_ownership_when_process_refuses_to_stop(self):
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("not permitted")
        proc.kill.side_effect = OSError("still not permitted")
        bridge.jamulus_process = proc

        result = bridge.stop_jamulus()

        self.assertFalse(result)
        self.assertIs(bridge.jamulus_process, proc)
        proc.kill.assert_called_once()
        self.assertEqual(bridge.jamulus_state, "Stop failed")

    def test_stop_jamulus_kill_fallback_reaps_after_terminate_failure(self):
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("not permitted")
        bridge.jamulus_process = proc

        result = bridge.stop_jamulus()

        self.assertTrue(result)
        self.assertIsNone(bridge.jamulus_process)
        proc.kill.assert_called_once()
        proc.wait.assert_called_once_with(timeout=2.0)

    def test_stop_jamulus_calls_controller_stop_to_halt_monitoring(self):
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None
        bridge.jamulus_process = proc

        bridge.stop_jamulus()

        bridge.jamulus_controller.stop.assert_called_once()


class TestShutdownKillsJamulus(unittest.TestCase):
    """Regression test: app close must terminate Jamulus subprocess."""

    def test_stop_jamulus_works_after_user_clicked_stop(self):
        """Calling stop_jamulus twice in a row is safe (idempotent)."""
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = None
        bridge.jamulus_process = proc

        bridge.stop_jamulus()
        bridge.stop_jamulus()  # second call — no process to terminate

        # First call terminated; second no-ops
        proc.terminate.assert_called_once()
        self.assertEqual(bridge.jamulus_state, "Stopped")

    def test_stop_jamulus_when_process_already_exited(self):
        """If proc.poll() returns non-None (already dead), no terminate call."""
        bridge = _make_bridge()
        proc = MagicMock()
        proc.poll.return_value = 0  # already exited
        bridge.jamulus_process = proc

        result = bridge.stop_jamulus()

        self.assertTrue(result)
        proc.terminate.assert_not_called()
        self.assertIsNone(bridge.jamulus_process)
        self.assertEqual(bridge.jamulus_state, "Stopped")


if __name__ == "__main__":
    unittest.main()

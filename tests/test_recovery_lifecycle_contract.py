"""Cross-layer regressions for primary Jamulus recovery ownership.

These tests keep the application, Bridge recovery snapshot, participant roster,
and Reference Track UI on one generation/PID contract.  They intentionally use
real controller entry points while replacing only native process boundaries.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.reference_track import (  # noqa: E402
    ReferenceTrackCapability,
    ReferenceTrackSnapshot,
    ReferenceTrackState,
)
from core.session_lifecycle import SessionLifecyclePhase  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from jamulus_controller import (  # noqa: E402
    JamulusParticipant,
    JamulusRpcMonitorIdentity,
)
from services.bridge_service import (  # noqa: E402
    JamulusRecoverySnapshot,
    JamulusRpcFreshness,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402
from webjam_qt.windows.reference_track import (  # noqa: E402
    ReferenceTrackDialog,
    ReferenceTrackPrimaryGate,
)

_app = QApplication.instance() or QApplication([])


def _recovery_snapshot(
    *,
    generation: int = 17,
    recovery_generation: int = 4,
    process_id: int = 7331,
    active: bool = False,
    pending: bool = False,
    inflight: bool = False,
    exhausted: bool = False,
    launch_intended: bool = True,
    freshness: JamulusRpcFreshness = JamulusRpcFreshness.FRESH,
    monitor_epoch: int = 1,
) -> JamulusRecoverySnapshot:
    return JamulusRecoverySnapshot(
        generation=generation,
        recovery_generation=recovery_generation,
        launch_intended=launch_intended,
        pending=pending,
        active=active,
        attempts_started=3 if active else 0,
        max_attempts=5,
        inflight=inflight,
        exhausted=exhausted,
        next_attempt_at=0.0,
        process_id=process_id,
        process_alive=process_id > 0,
        rpc_freshness=freshness,
        rpc_age_seconds=0.0 if freshness is JamulusRpcFreshness.FRESH else None,
        rpc_monitor_epoch=monitor_epoch if process_id > 0 else 0,
    )


def _source_identity(
    snapshot: JamulusRecoverySnapshot,
    *,
    monitor_epoch: int = 1,
) -> JamulusRpcMonitorIdentity:
    return JamulusRpcMonitorIdentity(
        monitor_epoch=monitor_epoch,
        process_generation=snapshot.generation,
        process_id=snapshot.process_id,
    )


@contextmanager
def _controller(
    tmp_path: Path,
    *,
    host: bool,
):
    settings = AppSettings(
        config_file=str(tmp_path / ("host.json" if host else "guest.json")),
        host_server_enabled=host,
        musician_name="Local Musician",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Recovery Contract",
    )
    controller = ApplicationController(window, settings=settings)
    try:
        yield controller
    finally:
        controller._primary_recovery_retire_inflight = False
        controller._reconnect_gave_up = False
        controller.audio.connected = False
        controller.audio.recovering = False
        controller.audio.stopping = False
        controller.audio.cleanup_retry_required = False
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.jamulus_process = None
        controller._connection_timer.stop()
        controller.shutdown()


def _install_fresh_rpc(controller: ApplicationController) -> MagicMock:
    rpc = MagicMock()
    rpc.available = True
    rpc.last_activity_age.return_value = 0.0
    controller.jamulus.rpc_client = rpc
    return rpc


def test_application_registers_only_process_identity_roster_callback(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path, host=False) as controller:
        assert controller._on_jamulus_participants in (
            controller.jamulus.identity_callbacks
        )
        assert controller._on_jamulus_participants not in (
            controller.jamulus.callbacks
        )
        assert controller.jamulus.chat_callback is None
        assert controller.jamulus.recorder_state_callback is None
        assert (
            controller.jamulus.chat_callback_with_source
            == controller._on_jamulus_chat
        )
        assert (
            controller.jamulus.recorder_state_callback_with_source
            == controller._on_recorder_state
        )


def test_worker_roster_delivery_detaches_list_and_preserves_source_identity(
    tmp_path: Path,
) -> None:
    snapshot = _recovery_snapshot()
    source = _source_identity(snapshot)
    original = [
        JamulusParticipant(
            channel_id=4,
            name="Local Musician",
            is_local=True,
        )
    ]
    queued: list = []
    with _controller(tmp_path, host=False) as controller:
        controller._ui_invoker.invoke = queued.append
        controller._apply_jamulus_participants = MagicMock()

        controller._on_jamulus_participants(original, source)
        original.clear()
        assert len(queued) == 1
        queued[0]()

        delivered = (
            controller._apply_jamulus_participants.call_args.args[0]
        )
        assert len(delivered) == 1
        assert delivered[0].channel_id == 4
        assert (
            controller._apply_jamulus_participants.call_args.kwargs[
                "source_identity"
            ]
            is source
        )


def test_queued_recorder_and_chat_require_current_bridge_monitor_epoch(
    tmp_path: Path,
) -> None:
    old_snapshot = _recovery_snapshot(
        generation=21,
        process_id=8021,
        monitor_epoch=7,
    )
    replacement = _recovery_snapshot(
        generation=21,
        process_id=8021,
        monitor_epoch=8,
    )
    queued: list = []
    with _controller(tmp_path, host=False) as controller:
        controller._ui_invoker.invoke = queued.append
        controller.recording.on_server_state = MagicMock()
        controller.window.session_canvas.append_line = MagicMock()

        controller._on_recorder_state(
            True,
            3,
            _source_identity(old_snapshot, monitor_epoch=7),
        )
        controller._on_jamulus_chat(
            "<b>old session</b>",
            _source_identity(old_snapshot, monitor_epoch=7),
        )
        assert len(queued) == 2

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=replacement,
        ):
            for callback in queued:
                callback()

        controller.recording.on_server_state.assert_not_called()
        controller.window.session_canvas.append_line.assert_not_called()

        queued.clear()
        current_source = _source_identity(replacement, monitor_epoch=8)
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=replacement,
        ):
            assert not controller._rpc_ui_source_is_current(
                JamulusRpcMonitorIdentity(8, 20, 8021)
            )
            assert not controller._rpc_ui_source_is_current(
                JamulusRpcMonitorIdentity(8, 21, 9999)
            )
        controller._on_recorder_state(True, 3, current_source)
        controller._on_jamulus_chat("<b>current session</b>", current_source)
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=replacement,
        ):
            for callback in queued:
                callback()

        controller.recording.on_server_state.assert_called_once_with(True)
        controller.window.session_canvas.append_line.assert_called_once_with(
            "current session"
        )


def test_connected_host_with_remote_only_roster_enters_recovery(
    tmp_path: Path,
) -> None:
    snapshot = _recovery_snapshot()
    with _controller(tmp_path, host=True) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.audio.connected = True
        controller._record_primary_local_roster_proof(snapshot)
        controller._transition_lifecycle(SessionLifecyclePhase.JOINING)
        controller._transition_lifecycle(SessionLifecyclePhase.CONNECTED)
        controller._stop_reference_track_for_session_end = MagicMock(
            return_value=True
        )

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=9,
                        name="Remote Musician",
                        is_local=False,
                    )
                ],
                source_identity=_source_identity(snapshot),
            )

        assert controller.audio.connected is False
        assert controller.audio.recovering is True
        assert controller.session_lifecycle.snapshot.phase is (
            SessionLifecyclePhase.RECONNECTING
        )
        assert controller._connection_timer.isActive()
        assert controller._jamulus_local_roster_generation == 0
        controller._stop_reference_track_for_session_end.assert_called_once_with(
            background=True
        )


def test_guest_requires_its_own_roster_row_before_connected(
    tmp_path: Path,
) -> None:
    snapshot = _recovery_snapshot()
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        _install_fresh_rpc(controller)
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=9,
                        name="Host",
                        is_local=False,
                    )
                ],
                source_identity=_source_identity(snapshot),
            )
            assert controller.audio.connected is False
            assert controller._jamulus_local_roster_generation == 0

            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    ),
                    JamulusParticipant(
                        channel_id=9,
                        name="Host",
                        is_local=False,
                    ),
                ],
                source_identity=_source_identity(snapshot),
            )

        assert controller.audio.connected is True
        assert controller._jamulus_local_roster_generation == snapshot.generation
        assert controller._jamulus_local_roster_process_id == snapshot.process_id


def test_authenticated_manual_launch_retires_native_setup_grace(
    tmp_path: Path,
) -> None:
    snapshot = replace(
        _recovery_snapshot(),
        native_setup_grace_configured=True,
        native_setup_grace_active=True,
    )
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.finish_native_sound_setup = MagicMock(
            return_value=True
        )
        _install_fresh_rpc(controller)

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(snapshot),
            )

        controller.bridge.finish_native_sound_setup.assert_called_once_with(
            generation=snapshot.generation,
            process_id=snapshot.process_id,
        )
        assert controller.audio.connected is True


def test_failed_native_setup_retirement_cannot_record_live_proof(
    tmp_path: Path,
) -> None:
    snapshot = replace(
        _recovery_snapshot(),
        native_setup_grace_configured=True,
        native_setup_grace_active=True,
    )
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.finish_native_sound_setup = MagicMock(
            return_value=False
        )
        _install_fresh_rpc(controller)

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(snapshot),
            )

        controller.bridge.finish_native_sound_setup.assert_called_once_with(
            generation=snapshot.generation,
            process_id=snapshot.process_id,
        )
        assert controller.audio.connected is False
        assert controller._jamulus_local_roster_generation == 0
        assert controller._jamulus_local_roster_process_id == 0


@pytest.mark.parametrize(
    "source_identity",
    (
        None,
        JamulusRpcMonitorIdentity(1, 0, 0),
        JamulusRpcMonitorIdentity(2, 16, 7331),
        JamulusRpcMonitorIdentity(3, 17, 7330),
    ),
)
def test_unbound_or_wrong_process_roster_cannot_authenticate_primary(
    tmp_path: Path,
    source_identity: JamulusRpcMonitorIdentity | None,
) -> None:
    snapshot = _recovery_snapshot()
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.mark_jamulus_reconnect_authenticated = MagicMock(
            return_value=True
        )
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=source_identity,
            )

        assert controller.audio.connected is False
        assert controller._jamulus_local_roster_generation == 0
        assert controller._jamulus_local_roster_process_id == 0
        controller.bridge.mark_jamulus_reconnect_authenticated.assert_not_called()


@pytest.mark.parametrize("terminal_owner", ("cleanup_retry", "gave_up"))
def test_late_roster_cannot_resurrect_a_terminal_cleanup_owner(
    tmp_path: Path,
    terminal_owner: str,
) -> None:
    snapshot = _recovery_snapshot(active=False)
    with _controller(tmp_path, host=True) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.mark_jamulus_reconnect_authenticated = MagicMock(
            return_value=True
        )
        if terminal_owner == "cleanup_retry":
            controller.audio.cleanup_retry_required = True
        else:
            controller._reconnect_gave_up = True

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(snapshot),
            )

        assert controller.audio.connected is False
        assert controller._jamulus_local_roster_generation == 0
        controller.bridge.mark_jamulus_reconnect_authenticated.assert_not_called()
        if terminal_owner == "cleanup_retry":
            assert controller.audio.cleanup_retry_required is True
        else:
            assert controller._reconnect_gave_up is True


def test_recovery_acknowledges_the_exact_snapshot_generation_and_pid(
    tmp_path: Path,
) -> None:
    recovering = _recovery_snapshot(
        generation=23,
        recovery_generation=8,
        process_id=8452,
        active=True,
    )
    settled = _recovery_snapshot(
        generation=23,
        recovery_generation=8,
        process_id=8452,
        active=False,
    )
    acknowledged = False

    with _controller(tmp_path, host=True) as controller:
        controller.bridge.jamulus_launch_intended = True
        _install_fresh_rpc(controller)

        def current_snapshot() -> JamulusRecoverySnapshot:
            return settled if acknowledged else recovering

        def acknowledge(*, generation: int, process_id: int) -> bool:
            nonlocal acknowledged
            assert generation == recovering.generation
            assert process_id == recovering.process_id
            acknowledged = True
            return True

        controller.bridge.mark_jamulus_reconnect_authenticated = MagicMock(
            side_effect=acknowledge
        )
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            side_effect=current_snapshot,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(recovering),
            )
            gate = controller._reference_track_primary_gate()

        assert acknowledged is True
        controller.bridge.mark_jamulus_reconnect_authenticated.assert_called_once_with(
            generation=23,
            process_id=8452,
        )
        assert controller.audio.connected is True
        assert controller._jamulus_local_roster_generation == 23
        assert controller._jamulus_local_roster_process_id == 8452
        assert gate is ReferenceTrackPrimaryGate.READY


def test_rejected_generation_pid_acknowledgement_stays_fail_closed(
    tmp_path: Path,
) -> None:
    recovering = _recovery_snapshot(
        generation=31,
        recovery_generation=9,
        process_id=9001,
        active=True,
    )
    with _controller(tmp_path, host=True) as controller:
        controller.bridge.jamulus_launch_intended = True
        _install_fresh_rpc(controller)
        controller.bridge.mark_jamulus_reconnect_authenticated = MagicMock(
            return_value=False
        )

        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=recovering,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(recovering),
            )
            gate = controller._reference_track_primary_gate()

        controller.bridge.mark_jamulus_reconnect_authenticated.assert_called_once_with(
            generation=31,
            process_id=9001,
        )
        assert controller.audio.connected is False
        assert controller._jamulus_local_roster_generation == 0
        assert gate is ReferenceTrackPrimaryGate.RECOVERING


def test_initial_connection_timeout_defers_to_active_bridge_recovery(
    tmp_path: Path,
) -> None:
    recovery = _recovery_snapshot(active=True)
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.stop_jamulus = MagicMock(return_value=True)
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=recovery,
        ):
            controller._on_connection_timeout()

        assert controller.audio.connection_timed_out is False
        controller.bridge.stop_jamulus.assert_not_called()


def test_launch_and_practice_sinks_refuse_recovery_retirement(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path, host=True) as controller:
        controller._primary_recovery_retire_inflight = True
        controller.audio.on_launch_toggle = MagicMock(return_value=True)
        controller.audio.on_practice_requested = MagicMock()
        controller.window.flash_message = MagicMock()

        controller._on_launch_audio()
        controller._on_practice_requested()

        controller.audio.on_launch_toggle.assert_not_called()
        controller.audio.on_practice_requested.assert_not_called()
        messages = [
            call.args[0] for call in controller.window.flash_message.call_args_list
        ]
        assert any("interrupted music engine cleanup" in text for text in messages)
        assert any("session cleanup" in text for text in messages)


@pytest.mark.parametrize(
    "blocked_owner",
    (
        "retiring",
        "stopping",
        "cleanup_retry",
        "invite_switch",
        "terminal_recovery",
    ),
)
def test_central_startup_sink_rejects_late_unscoped_callbacks(
    tmp_path: Path,
    blocked_owner: str,
) -> None:
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.launch_jamulus = MagicMock(return_value=True)
        if blocked_owner == "retiring":
            controller._primary_recovery_retire_inflight = True
        elif blocked_owner == "stopping":
            controller.audio.stopping = True
        elif blocked_owner == "cleanup_retry":
            controller.audio.cleanup_retry_required = True
        elif blocked_owner == "invite_switch":
            controller._invite_switch_in_flight = True
        else:
            controller._reconnect_gave_up = True

        assert controller.begin_startup_journey() is False
        assert controller._startup_attempt is None
        controller.bridge.launch_jamulus.assert_not_called()


@pytest.mark.parametrize("accepted", (False, True))
def test_explicit_start_clears_terminal_recovery_only_after_launch_acceptance(
    tmp_path: Path,
    accepted: bool,
) -> None:
    with _controller(tmp_path, host=False) as controller:
        controller._reconnect_gave_up = True
        controller._reconnect_banner_shown = True
        controller._rpc_hang_banner_shown = True
        controller.audio.recovering = True
        controller.audio.connection_timed_out = True
        controller.bridge.launch_jamulus = MagicMock(return_value=accepted)

        with patch(
            "webjam_qt.controllers.application_controller.sys.platform",
            "darwin",
        ):
            controller._on_session_audio_requested()

        controller.bridge.launch_jamulus.assert_called_once_with(
            manual=True,
            native_setup_timeout_seconds=600.0,
        )
        assert controller._reconnect_gave_up is (not accepted)
        assert controller._reconnect_banner_shown is (not accepted)
        assert controller._rpc_hang_banner_shown is (not accepted)
        assert controller.audio.recovering is (not accepted)
        assert controller.audio.connection_timed_out is (not accepted)
        assert controller._startup_attempt is not None
        assert (
            int(
                controller._startup_attempt[
                    "explicit_launch_authorization_generation"
                ]
            )
            > 0
        )


def test_remote_start_continuation_is_bound_to_its_exact_runtime(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path, host=False) as controller:
        controller._reconnect_gave_up = True
        controller.bridge.launch_jamulus = MagicMock(return_value=True)
        authorized_source = object()
        replaced_source = object()
        token = controller._new_startup_launch_authorization()
        controller._pending_startup_launch_authorization = None
        controller._bind_remote_startup_continuation(
            authorized_source,
            token[0],
        )

        assert controller._continue_startup_from_remote(replaced_source) is False
        controller.bridge.launch_jamulus.assert_not_called()
        assert controller._reconnect_gave_up is True

        with patch(
            "webjam_qt.controllers.application_controller.sys.platform",
            "darwin",
        ):
            assert (
                controller._continue_startup_from_remote(authorized_source)
                is True
            )
        controller.bridge.launch_jamulus.assert_called_once_with(
            manual=True,
            native_setup_timeout_seconds=600.0,
        )
        assert controller._reconnect_gave_up is False


@pytest.mark.parametrize("accepted", (False, True))
def test_practice_clears_terminal_recovery_only_after_launch_acceptance(
    tmp_path: Path,
    accepted: bool,
) -> None:
    with _controller(tmp_path, host=True) as controller:
        controller._reconnect_gave_up = True
        controller._reconnect_banner_shown = True
        controller._rpc_hang_banner_shown = True
        controller.audio.recovering = True
        controller.audio.connection_timed_out = True
        controller.bridge.launch_practice_session = MagicMock(
            return_value=accepted
        )

        controller._on_practice_requested()

        controller.bridge.launch_practice_session.assert_called_once_with()
        assert controller._reconnect_gave_up is (not accepted)
        assert controller._reconnect_banner_shown is (not accepted)
        assert controller._rpc_hang_banner_shown is (not accepted)
        assert controller.audio.recovering is (not accepted)
        assert controller.audio.connection_timed_out is (not accepted)


def test_zero_process_generation_cannot_authenticate_local_roster(
    tmp_path: Path,
) -> None:
    unowned = _recovery_snapshot(generation=0, process_id=7331)
    with _controller(tmp_path, host=False) as controller:
        controller.bridge.jamulus_launch_intended = True
        _install_fresh_rpc(controller)
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=unowned,
        ):
            controller._apply_jamulus_participants(
                [
                    JamulusParticipant(
                        channel_id=4,
                        name="Local Musician",
                        is_local=True,
                    )
                ],
                source_identity=_source_identity(unowned),
            )

        assert controller.audio.connected is False
        assert controller._jamulus_local_roster_generation == 0
        assert controller._jamulus_local_roster_process_id == 0


def test_terminal_recovery_retires_live_fresh_process_without_local_roster(
    tmp_path: Path,
) -> None:
    terminal = _recovery_snapshot(
        generation=41,
        process_id=9441,
        active=True,
        exhausted=True,
        freshness=JamulusRpcFreshness.FRESH,
    )
    with _controller(tmp_path, host=True) as controller:
        controller.bridge.attempt_hosted_server_recovery = MagicMock()
        controller.bridge.attempt_auto_reconnects = MagicMock()
        controller._retire_primary_after_recovery_exhaustion = MagicMock(
            return_value=True
        )
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=terminal,
        ):
            controller._on_reconnect_tick()

        controller.bridge.attempt_hosted_server_recovery.assert_called_once_with()
        controller._retire_primary_after_recovery_exhaustion.assert_called_once_with(
            unresponsive=False
        )
        controller.bridge.attempt_auto_reconnects.assert_not_called()


def test_terminal_recovery_retires_after_launch_intent_and_process_are_gone(
    tmp_path: Path,
) -> None:
    terminal = _recovery_snapshot(
        generation=0,
        process_id=0,
        active=True,
        exhausted=True,
        launch_intended=False,
        freshness=JamulusRpcFreshness.NO_PROCESS,
    )
    with _controller(tmp_path, host=True) as controller:
        controller.bridge.attempt_hosted_server_recovery = MagicMock()
        controller.bridge.attempt_auto_reconnects = MagicMock()
        controller._retire_primary_after_recovery_exhaustion = MagicMock(
            return_value=True
        )
        with patch.object(
            controller,
            "_primary_jamulus_recovery_snapshot",
            return_value=terminal,
        ):
            controller._on_reconnect_tick()

        controller.bridge.attempt_hosted_server_recovery.assert_called_once_with()
        controller._retire_primary_after_recovery_exhaustion.assert_called_once_with(
            unresponsive=False
        )
        controller.bridge.attempt_auto_reconnects.assert_not_called()


def test_hosted_server_supervision_runs_while_primary_recovery_is_terminal(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path, host=True) as controller:
        controller._reconnect_gave_up = True
        controller.bridge.attempt_hosted_server_recovery = MagicMock()
        controller.bridge.attempt_auto_reconnects = MagicMock()

        controller._on_reconnect_tick()

        controller.bridge.attempt_hosted_server_recovery.assert_called_once_with()
        controller.bridge.attempt_auto_reconnects.assert_not_called()


def test_hosted_server_supervision_is_suppressed_during_end_leave(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path, host=True) as controller:
        controller.audio.stopping = True
        controller.bridge.attempt_hosted_server_recovery = MagicMock()
        controller.bridge.attempt_auto_reconnects = MagicMock()

        controller._on_reconnect_tick()

        controller.bridge.attempt_hosted_server_recovery.assert_not_called()
        controller.bridge.attempt_auto_reconnects.assert_not_called()


def test_recovery_retirement_uses_recovering_gate_and_disables_stop(
    tmp_path: Path,
) -> None:
    snapshot = ReferenceTrackSnapshot(
        state=ReferenceTrackState.PLAYING,
        capability=ReferenceTrackCapability(
            True,
            "macos",
            "Isolated route ready.",
            "BlackHole 16ch",
        ),
        source_name="Reference.wav",
        duration_s=60.0,
        position_s=5.0,
    )
    with _controller(tmp_path, host=True) as controller:
        controller._primary_recovery_retire_inflight = True
        assert controller._reference_track_primary_gate() is (
            ReferenceTrackPrimaryGate.RECOVERING
        )

        dialog = ReferenceTrackDialog()
        try:
            dialog.set_primary_gate(controller._reference_track_primary_gate())
            dialog.set_snapshot(snapshot)

            assert dialog._stop.isEnabled() is False
            assert "recovery already owns" in dialog._stop.toolTip()
            assert "recovery already owns" in (
                dialog._stop.accessibleDescription()
            )
        finally:
            dialog.close()


def test_cleanup_pending_session_change_points_to_main_cleanup_owner() -> None:
    snapshot = ReferenceTrackSnapshot(
        state=ReferenceTrackState.FAILED,
        capability=ReferenceTrackCapability(
            False,
            "macos",
            "Private cleanup is pending.",
            backend="blackhole",
            reason_code="cleanup_pending",
        ),
        source_name="Reference.wav",
        duration_s=60.0,
        error="Private cleanup is pending.",
        cleanup_pending=True,
    )
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.SESSION_CHANGING)
        dialog.set_snapshot(snapshot)

        assert dialog._stop.isEnabled() is False
        assert "main session control" in dialog._route_guidance.text()
        assert "Choose Stop again" not in dialog._route_guidance.text()
        assert "single cleanup owner" in dialog._stop.accessibleDescription()
    finally:
        dialog.close()

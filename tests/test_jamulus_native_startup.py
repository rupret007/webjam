"""High-signal orchestration checks for the Jamulus-native startup journey."""
from __future__ import annotations

import os
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings
from core.audio_feedback_guard import (
    AudioFeedbackAssessment,
    AudioFeedbackRisk,
)
from core.session_conductor import (
    EvidenceState,
    FailureDisposition,
    MusicPathState,
    ProcessState,
    SessionConductorFacts,
    SessionConductorToken,
    SessionPrimaryAction,
    SessionRole,
)
from services.bridge_service import (
    JamulusRecoverySnapshot,
    JamulusRpcFreshness,
    NATIVE_SOUND_SETUP_GRACE_SECONDS,
)
from webjam_qt.controllers.application_controller import ApplicationController


class _ImmediateThread:
    """Run one worker synchronously so ordering remains observable."""

    def __init__(self, *, target, **_kwargs) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _AttemptStore:
    def __init__(self) -> None:
        self.cleared = 0
        self.saved = []

    def load(self):
        return None

    def next_generation(self) -> int:
        return 1

    def save(self, record) -> None:
        self.saved.append(record)

    def clear(self) -> None:
        self.cleared += 1


def _controller(*, hosting: bool) -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._shutdown = False
    controller._startup_generation = 0
    controller._startup_attempt = None
    controller._startup_profile_plan = None
    controller._startup_recovery_record = None
    controller._startup_attempt_store = _AttemptStore()
    controller._startup_readiness_store = SimpleNamespace(
        is_current=mock.Mock(return_value=False)
    )
    controller._remote_invitation_requires_replacement = False
    controller._remote_invitation = None
    controller._remote_invite_owner = None
    controller._conductor_setup_requested = False
    controller._conductor_band_check = None
    controller._local_audio_seen = False
    controller._remote_audio_seen = False
    controller.settings = AppSettings(
        host_server_enabled=hosting,
        jamulus_server="127.0.0.1" if hosting else "192.168.1.42",
    )
    controller.bridge = SimpleNamespace(
        jamulus_state="Not launched",
        ensure_hosted_server=mock.Mock(return_value=(True, "ready")),
        launch_jamulus=mock.Mock(return_value=True),
        hosted_server_alive=mock.Mock(return_value=hosting),
        native_profile_plan=None,
    )
    controller.audio = SimpleNamespace(
        connected=False,
        stopping=False,
        ended_by_user=False,
        connection_timed_out=False,
        recovering=False,
        reset_to_idle=mock.Mock(),
    )
    controller._jamulus_connected = False
    controller._connection_timer = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller.window = SimpleNamespace(
        session_strip=SimpleNamespace(
            set_recording_available=mock.Mock(),
            set_audio_state=mock.Mock(),
        ),
        session_hud=SimpleNamespace(set_state=mock.Mock()),
    )
    controller._transition_lifecycle = mock.Mock()
    controller._render_startup_journey = mock.Mock()
    return controller


def test_host_starts_private_server_before_opening_jamulus() -> None:
    controller = _controller(hosting=True)
    events: list[str] = []
    controller.bridge.ensure_hosted_server.side_effect = (
        lambda **_kwargs: events.append("server") or (True, "ready")
    )
    controller._launch_native_jamulus_for_startup = mock.Mock(
        side_effect=lambda _generation: events.append("jamulus")
    )

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller.begin_startup_journey()

    assert events == ["server", "jamulus"]
    assert controller._startup_attempt is not None
    assert controller._startup_attempt["role"] == "host"


def test_guest_launches_native_jamulus_once_without_a_second_start_decision() -> None:
    controller = _controller(hosting=False)
    controller._launch_native_jamulus_for_startup = mock.Mock()

    controller.begin_startup_journey()
    controller.begin_startup_journey()

    controller.bridge.ensure_hosted_server.assert_not_called()
    controller._launch_native_jamulus_for_startup.assert_called_once_with(1)


def test_startup_attempt_has_a_live_conductor_token() -> None:
    controller = _controller(hosting=True)
    controller._launch_native_jamulus_for_startup = mock.Mock()

    controller.begin_startup_journey()

    token = controller._startup_attempt["conductor_token"]
    assert isinstance(token, SessionConductorToken)
    assert token == controller.session_conductor.token
    assert token.role is SessionRole.HOST


def test_controller_conductor_rejects_a_late_attempt_after_retry() -> None:
    controller = _controller(hosting=True)
    first = controller._start_session_conductor_attempt(SessionRole.HOST)
    ready = SessionConductorFacts(
        role=SessionRole.HOST,
        setup_requested=True,
        identity=EvidenceState.VERIFIED,
        sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
    )
    accepted = controller._observe_session_conductor_facts(ready, token=first)
    assert accepted.token == first

    controller._observe_session_conductor_facts(
        replace(ready, failure=FailureDisposition.RETRYABLE), token=first
    )
    second = controller._start_session_conductor_attempt(SessionRole.HOST)
    assert second != first

    stale = controller._observe_session_conductor_facts(ready, token=first)
    assert stale.token == second
    assert stale.facts.setup_requested


def test_controller_conductor_retries_with_a_new_role_bound_token() -> None:
    """A failed Host attempt cannot retain ownership of a later Join attempt."""

    controller = _controller(hosting=True)
    host = controller._start_session_conductor_attempt(SessionRole.HOST)
    controller._observe_session_conductor_facts(
        SessionConductorFacts(
            role=SessionRole.HOST,
            setup_requested=True,
            failure=FailureDisposition.RETRYABLE,
        ),
        token=host,
    )

    guest = controller._start_session_conductor_attempt(SessionRole.GUEST)
    accepted = controller._observe_session_conductor_facts(
        SessionConductorFacts(
            role=SessionRole.GUEST,
            setup_requested=True,
            identity=EvidenceState.NOT_REQUIRED,
            sound=EvidenceState.NOT_REQUIRED,
        ),
        token=guest,
    )

    assert guest != host
    assert guest.role is SessionRole.GUEST
    assert accepted.token == guest
    assert accepted.facts.role is SessionRole.GUEST


def test_authoritative_reconnect_opens_a_fresh_conductor_generation() -> None:
    """A verified roster recovery may replace only a retryable failure."""

    controller = _controller(hosting=False)
    failed = controller._start_session_conductor_attempt(SessionRole.GUEST)
    controller._observe_session_conductor_facts(
        SessionConductorFacts(
            role=SessionRole.GUEST,
            setup_requested=True,
            failure=FailureDisposition.RETRYABLE,
        ),
        token=failed,
    )
    recovered = SessionConductorFacts(
        role=SessionRole.GUEST,
        setup_requested=True,
        identity=EvidenceState.NOT_REQUIRED,
        sound=EvidenceState.NOT_REQUIRED,
        band_check=EvidenceState.NOT_REQUIRED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
    )
    controller._session_conductor_facts = mock.Mock(return_value=recovered)

    controller._resume_session_conductor_after_authoritative_reconnect()

    assert controller.session_conductor.token != failed
    assert controller.session_conductor.snapshot.facts == recovered


def test_authoritative_reconnect_retires_a_stale_failed_startup_journey() -> None:
    """A fresh roster must not leave an old Try Again screen in charge."""

    controller = _controller(hosting=False)
    failed = controller._start_session_conductor_attempt(SessionRole.GUEST)
    controller._observe_session_conductor_facts(
        SessionConductorFacts(
            role=SessionRole.GUEST,
            setup_requested=True,
            failure=FailureDisposition.RETRYABLE,
        ),
        token=failed,
    )
    cancelled = threading.Event()
    controller._startup_attempt = {
        "generation": 9,
        "role": "guest",
        "conductor_token": failed,
        "phase": "failed",
        "cancel_event": cancelled,
    }
    controller._startup_profile_plan = object()
    controller._startup_recovery_record = object()
    recovered = SessionConductorFacts(
        role=SessionRole.GUEST,
        setup_requested=True,
        identity=EvidenceState.NOT_REQUIRED,
        sound=EvidenceState.NOT_REQUIRED,
        band_check=EvidenceState.NOT_REQUIRED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
    )
    controller._session_conductor_facts = mock.Mock(return_value=recovered)

    controller._resume_session_conductor_after_authoritative_reconnect()

    assert controller.session_conductor.token != failed
    assert controller._startup_attempt is None
    assert controller._startup_profile_plan is None
    assert controller._startup_recovery_record is None
    assert cancelled.is_set()
    assert controller._startup_attempt_store.cleared == 1
    assert controller._startup_attempt_for(9) is None


def test_live_hud_waits_for_human_hearing_confirmation_before_using_ready_style() -> None:
    """A roster and meters never make the primary HUD claim hearing proof."""

    controller = _controller(hosting=True)
    controller.window.participant_grid = SimpleNamespace(
        set_session_state=mock.Mock()
    )
    controller.window.session_strip.set_invite_available = mock.Mock()
    controller._record_pilot_conductor_presentation = mock.Mock()
    controller._conductor_requires_legacy_copy = mock.Mock(return_value=False)
    live = SessionConductorFacts(
        role=SessionRole.HOST,
        setup_requested=True,
        identity=EvidenceState.NOT_REQUIRED,
        sound=EvidenceState.NOT_REQUIRED,
        band_check=EvidenceState.NOT_REQUIRED,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=EvidenceState.VERIFIED,
        participant_identity=EvidenceState.VERIFIED,
    )
    controller._session_conductor_facts = mock.Mock(return_value=live)

    controller._render_session_conductor()

    first = controller.window.session_hud.set_state.call_args
    assert first.args[0] == "Band connected"
    assert first.kwargs["ready"] is False
    assert first.kwargs["action_text"] == "Record"

    controller.window.session_hud.set_state.reset_mock()
    controller._session_conductor_facts.return_value = replace(
        live,
        human_two_way_audibility=EvidenceState.VERIFIED,
    )
    controller._render_session_conductor()

    assert controller.window.session_hud.set_state.call_args.kwargs["ready"] is True


def test_proven_startup_hands_off_to_normal_session_hud_without_enter_jam() -> None:
    controller = _controller(hosting=True)
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "confirm_sound",
    }
    controller._enter_startup_jam = mock.Mock()

    controller._show_startup_invite_ready(1)

    assert controller._startup_attempt["phase"] == "invite_ready"
    controller.window.session_strip.set_recording_available.assert_called_once_with(False)
    controller._enter_startup_jam.assert_called_once_with()


def test_cancelled_host_setup_releases_owned_client_and_server() -> None:
    controller = _controller(hosting=True)
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "native_sound_setup",
    }
    events: list[str] = []
    controller.bridge.stop_jamulus = mock.Mock(
        side_effect=lambda: events.append("client") or True
    )
    controller.bridge.stop_hosted_server = mock.Mock(
        side_effect=lambda: events.append("server") or True
    )

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._cancel_startup_journey()

    assert events == ["client", "server"]
    assert controller._startup_attempt is None
    assert controller._startup_attempt_store.cleared == 1
    controller.audio.reset_to_idle.assert_called_once_with()


def test_cancelled_host_setup_keeps_failure_visible_until_cleanup_is_confirmed() -> None:
    """A failed stop must not reset the lobby while owned audio may remain."""

    controller = _controller(hosting=True)
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "native_sound_setup",
    }
    controller.bridge.stop_jamulus = mock.Mock(return_value=False)
    controller.bridge.stop_hosted_server = mock.Mock(return_value=True)

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._cancel_startup_journey()

    assert controller._startup_attempt["phase"] == "failed"
    assert controller._startup_attempt["retryable"] is True
    assert "Try again after the music connection has stopped" in (
        controller._startup_attempt["failure"]
    )
    assert controller._startup_attempt_store.cleared == 0
    controller.audio.reset_to_idle.assert_not_called()


def test_cancel_during_host_startup_never_completes_into_a_client_launch() -> None:
    controller = _controller(hosting=True)
    cancel_event = threading.Event()
    attempt = {
        "generation": 1,
        "role": "host",
        "phase": "starting_server",
        "cancel_event": cancel_event,
    }
    controller._startup_attempt = attempt
    deliveries = []
    controller._ui_invoker = SimpleNamespace(invoke=deliveries.append)
    controller.bridge.ensure_hosted_server.side_effect = (
        lambda **_kwargs: (True, "ready")
    )
    controller._launch_native_jamulus_for_startup = mock.Mock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._start_hosted_server_for_startup(1)

    assert len(deliveries) == 1
    cancel_event.set()
    attempt["phase"] = "cancelling"
    deliveries[0]()

    controller._launch_native_jamulus_for_startup.assert_not_called()


def test_v2_guest_peer_starts_once_after_native_connection_proof() -> None:
    controller = _controller(hosting=False)
    guest = mock.Mock()
    controller.guest_peer = guest
    controller._guest_invite = object()
    controller._remote_session = None
    controller.bridge.jamulus_state = "Running"
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._show_startup_invite_ready = mock.Mock()
    controller._startup_attempt = {
        "generation": 1,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "setup_finished": False,
    }

    controller._poll_startup_connection(1)

    guest.start.assert_called_once_with()
    controller._show_startup_invite_ready.assert_called_once_with(1)
    assert controller._startup_attempt["webex_decision"] == "skipped"


def test_starting_state_cannot_fail_a_queued_native_restart() -> None:
    """Startup polling must wait while the accepted worker is still launching."""

    controller = _controller(hosting=True)
    controller.bridge.jamulus_state = "Starting"
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "setup_finished": True,
    }
    controller._startup_music_is_proven = mock.Mock(return_value=False)
    controller._render_startup_journey = mock.Mock()
    controller._schedule_startup_poll = mock.Mock()
    controller._fail_startup_journey = mock.Mock()

    controller._poll_startup_connection(1)

    controller._fail_startup_journey.assert_not_called()
    controller._render_startup_journey.assert_called_once_with()
    controller._schedule_startup_poll.assert_called_once_with(1)
    assert controller._startup_attempt["phase"] == "verifying_music"


def test_native_launch_requests_the_bounded_human_setup_window() -> None:
    controller = _controller(hosting=False)
    controller._startup_attempt = {
        "generation": 1,
        "role": "guest",
        "phase": "launching_client",
        "cancel_event": threading.Event(),
        "explicit_launch_authorization_generation": 0,
    }
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller._accept_explicit_primary_launch = mock.Mock()
    controller._schedule_startup_poll = mock.Mock()

    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.time.monotonic",
            return_value=100.0,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.sys.platform",
            "darwin",
        ),
    ):
        controller._launch_native_jamulus_for_startup(1)

    controller.bridge.launch_jamulus.assert_called_once_with(
        manual=True,
        native_setup_timeout_seconds=NATIVE_SOUND_SETUP_GRACE_SECONDS,
    )
    assert controller._startup_attempt["native_setup_deadline"] == (
        100.0 + NATIVE_SOUND_SETUP_GRACE_SECONDS
    )
    controller._connection_timer.start.assert_called_once_with()


def test_builtin_feedback_warning_decline_blocks_native_launch() -> None:
    controller = _controller(hosting=True)
    controller._startup_attempt = {
        "generation": 3,
        "role": "host",
        "phase": "launching_client",
        "cancel_event": threading.Event(),
    }
    controller._feedback_guard_allows_audio_start = mock.Mock(return_value=False)
    controller._fail_startup_journey = mock.Mock()

    controller._launch_native_jamulus_for_startup(3)

    controller.bridge.launch_jamulus.assert_not_called()
    controller._fail_startup_journey.assert_called_once()
    assert "headphones" in controller._fail_startup_journey.call_args.args[1]


def test_feedback_assessment_requires_explicit_confirmation_only_for_risk() -> None:
    controller = _controller(hosting=True)
    controller._confirm_builtin_audio_feedback_risk = mock.Mock(return_value=False)
    controller.bridge.prelaunch_audio_feedback_assessment = mock.Mock(
        return_value=AudioFeedbackAssessment(
            AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS
        )
    )

    assert controller._feedback_guard_allows_audio_start() is False
    controller._confirm_builtin_audio_feedback_risk.assert_called_once_with()

    controller._confirm_builtin_audio_feedback_risk.reset_mock()
    controller.bridge.prelaunch_audio_feedback_assessment.return_value = (
        AudioFeedbackAssessment(AudioFeedbackRisk.NOT_DETECTED)
    )
    assert controller._feedback_guard_allows_audio_start() is True
    controller._confirm_builtin_audio_feedback_risk.assert_not_called()


def test_feedback_guard_rechecks_on_a_fresh_retry() -> None:
    controller = _controller(hosting=False)
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller._accept_explicit_primary_launch = mock.Mock()
    controller._schedule_startup_poll = mock.Mock()
    controller._fail_startup_journey = mock.Mock()
    controller._feedback_guard_allows_audio_start = mock.Mock(
        side_effect=(False, True)
    )

    for generation in (8, 9):
        controller._startup_attempt = {
            "generation": generation,
            "role": "guest",
            "phase": "launching_client",
            "cancel_event": threading.Event(),
            "explicit_launch_authorization_generation": 0,
        }
        with mock.patch(
            "webjam_qt.controllers.application_controller.sys.platform",
            "linux",
        ):
            controller._launch_native_jamulus_for_startup(generation)

    assert controller._feedback_guard_allows_audio_start.call_count == 2
    controller.bridge.launch_jamulus.assert_called_once_with(manual=True)


def test_non_macos_native_launch_keeps_ordinary_connection_timeout() -> None:
    controller = _controller(hosting=False)
    controller._startup_attempt = {
        "generation": 2,
        "role": "guest",
        "phase": "launching_client",
        "cancel_event": threading.Event(),
        "explicit_launch_authorization_generation": 0,
    }
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller._accept_explicit_primary_launch = mock.Mock()
    controller._schedule_startup_poll = mock.Mock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.sys.platform",
        "linux",
    ):
        controller._launch_native_jamulus_for_startup(2)

    controller.bridge.launch_jamulus.assert_called_once_with(manual=True)
    assert "native_setup_deadline" not in controller._startup_attempt


def test_ordinary_connection_timeout_defers_to_active_native_setup() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_launch_intended = True
    controller._startup_attempt = {
        "generation": 4,
        "role": "guest",
        "phase": "native_sound_setup",
        "native_setup_deadline": 1_000_000_000_000.0,
    }
    controller._poll_startup_connection = mock.Mock()
    controller.bridge.stop_jamulus = mock.Mock()
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=4,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=4444,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.STARTING,
            rpc_age_seconds=None,
            native_setup_grace_configured=True,
            native_setup_grace_active=True,
        )
    )

    controller._on_connection_timeout()

    controller._poll_startup_connection.assert_called_once_with(4)
    controller.bridge.stop_jamulus.assert_not_called()
    assert controller.audio.connection_timed_out is False


def test_late_connection_timeout_during_cancel_never_starts_second_stop() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.stop_jamulus = mock.Mock()
    controller._startup_attempt = {
        "generation": 4,
        "role": "guest",
        "phase": "cancelling",
        "cancel_event": threading.Event(),
    }

    controller._on_connection_timeout()

    controller.bridge.stop_jamulus.assert_not_called()
    assert controller.audio.connection_timed_out is False


def test_late_connection_timeout_during_shutdown_is_a_noop() -> None:
    controller = _controller(hosting=False)
    controller._shutdown = True
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.stop_jamulus = mock.Mock()

    controller._on_connection_timeout()

    controller.bridge.stop_jamulus.assert_not_called()
    assert controller.audio.connection_timed_out is False


def test_expired_native_setup_stops_exact_attempt_before_retry() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    controller._startup_attempt = {
        "generation": 5,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 100.0,
    }
    controller._startup_music_is_proven = mock.Mock(return_value=False)
    controller._fail_startup_journey = mock.Mock()
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=14,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=5432,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.STARTING,
            rpc_age_seconds=None,
        )
    )

    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.time.monotonic",
            return_value=100.0,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            _ImmediateThread,
        ),
    ):
        controller._poll_startup_connection(5)

    controller.bridge.stop_jamulus.assert_called_once_with(
        expected_generation=14,
        expected_process_id=5432,
    )
    controller._fail_startup_journey.assert_called_once_with(
        5,
        "Jamulus sound setup waited 10 minutes without a verified music "
        "connection. Check your interface, then try again.",
    )


def test_existing_profile_retires_first_run_allowance_immediately() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.native_profile_plan = SimpleNamespace(
        profile_exists=True,
        profile_fingerprint="a" * 64,
    )
    controller._startup_attempt = {
        "generation": 7,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 1_000_000_000_000.0,
    }
    controller._startup_music_is_proven = mock.Mock(return_value=False)
    controller._render_startup_journey = mock.Mock()
    controller._schedule_startup_poll = mock.Mock()

    controller._poll_startup_connection(7)

    assert "native_setup_deadline" not in controller._startup_attempt
    controller._schedule_startup_poll.assert_called_once_with(7)


def test_duplicate_timeout_callback_starts_exactly_one_cleanup() -> None:
    controller = _controller(hosting=False)
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    controller._startup_attempt = {
        "generation": 8,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 100.0,
    }
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=15,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=6543,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.STARTING,
            rpc_age_seconds=None,
        )
    )
    queued: list[object] = []

    class _QueuedThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            queued.append(self.target)

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _QueuedThread,
    ):
        controller._expire_native_sound_setup(8)
        controller._expire_native_sound_setup(8)

    assert len(queued) == 1
    queued[0]()
    controller.bridge.stop_jamulus.assert_called_once_with(
        expected_generation=15,
        expected_process_id=6543,
    )


def test_timeout_callback_for_replaced_startup_generation_is_a_noop() -> None:
    controller = _controller(hosting=False)
    controller.bridge.stop_jamulus = mock.Mock()
    controller._startup_attempt = {
        "generation": 10,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 1_000_000_000_000.0,
    }

    controller._expire_native_sound_setup(9)

    controller.bridge.stop_jamulus.assert_not_called()
    assert controller._startup_attempt["phase"] == "native_sound_setup"


def test_authenticated_setup_advances_only_after_exact_bridge_generation() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_state = "Running"
    controller._startup_attempt = {
        "generation": 6,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 1_000_000_000_000.0,
    }
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._is_jamulus_running = mock.Mock(return_value=True)
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=12,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=4321,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.FRESH,
            rpc_age_seconds=0.1,
            native_setup_grace_configured=True,
            native_setup_grace_active=True,
        )
    )
    controller.bridge.finish_native_sound_setup = mock.Mock(side_effect=[False, True])
    controller._schedule_startup_poll = mock.Mock()
    controller._show_startup_invite_ready = mock.Mock()

    controller._poll_startup_connection(6)

    controller._schedule_startup_poll.assert_called_once_with(6)
    controller._show_startup_invite_ready.assert_not_called()

    controller._poll_startup_connection(6)

    assert controller.bridge.finish_native_sound_setup.call_args_list == [
        mock.call(generation=12, process_id=4321),
        mock.call(generation=12, process_id=4321),
    ]
    controller._show_startup_invite_ready.assert_called_once_with(6)
    assert "native_setup_deadline" not in controller._startup_attempt


def test_startup_proof_and_finish_use_one_immutable_process_snapshot() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_state = "Running"
    controller._startup_attempt = {
        "generation": 16,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 1_000_000_000_000.0,
    }
    proven = JamulusRecoverySnapshot(
        generation=31,
        recovery_generation=0,
        launch_intended=True,
        pending=False,
        active=False,
        attempts_started=0,
        max_attempts=5,
        inflight=False,
        exhausted=False,
        next_attempt_at=0.0,
        process_id=3131,
        process_alive=True,
        rpc_freshness=JamulusRpcFreshness.FRESH,
        rpc_age_seconds=0.0,
        native_setup_grace_configured=True,
        native_setup_grace_active=True,
    )
    replacement = replace(proven, generation=32, process_id=3232)
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._is_jamulus_running = mock.Mock(return_value=True)
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        side_effect=[proven, replacement]
    )
    controller.bridge.finish_native_sound_setup = mock.Mock(return_value=False)
    controller._schedule_startup_poll = mock.Mock()
    controller._show_startup_invite_ready = mock.Mock()

    controller._poll_startup_connection(16)

    controller._primary_jamulus_recovery_snapshot.assert_called_once_with()
    controller.bridge.finish_native_sound_setup.assert_called_once_with(
        generation=31,
        process_id=3131,
    )
    controller._schedule_startup_poll.assert_called_once_with(16)
    controller._show_startup_invite_ready.assert_not_called()


def test_authenticated_setup_at_hard_deadline_expires_before_advancing() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_state = "Running"
    controller._startup_attempt = {
        "generation": 17,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 100.0,
    }
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._is_jamulus_running = mock.Mock(return_value=True)
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=33,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=3333,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.FRESH,
            rpc_age_seconds=0.0,
            native_setup_grace_configured=True,
            native_setup_grace_active=True,
        )
    )
    controller._expire_native_sound_setup = mock.Mock()
    controller._show_startup_invite_ready = mock.Mock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.time.monotonic",
        return_value=100.0,
    ):
        controller._poll_startup_connection(17)

    controller._expire_native_sound_setup.assert_called_once_with(17)
    controller._show_startup_invite_ready.assert_not_called()


@pytest.mark.parametrize("pending", [True, False])
def test_ordinary_connection_timeout_uses_exact_launch_lineage(
    pending: bool,
) -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    controller._startup_attempt = None
    controller._reconnect_banner_shown = False
    controller._rpc_hang_banner_shown = False
    controller._primary_recovery_retire_inflight = False
    controller.window.participant_grid = SimpleNamespace(
        set_session_state=mock.Mock()
    )
    controller.window.session_hud = SimpleNamespace(set_state=mock.Mock())
    controller.window.session_strip.set_tools_enabled = mock.Mock()
    controller._connection_failure_state = mock.Mock(return_value=object())
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=0 if pending else 34,
            recovery_generation=0,
            launch_intended=True,
            pending=pending,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=0 if pending else 3434,
            process_alive=not pending,
            rpc_freshness=(
                JamulusRpcFreshness.NO_PROCESS
                if pending
                else JamulusRpcFreshness.STARTING
            ),
            rpc_age_seconds=None,
            launch_request_generation=41,
        )
    )

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._on_connection_timeout()

    controller.bridge.stop_jamulus.assert_called_once_with(
        expected_launch_request_generation=41,
    )
    assert controller.audio.connection_timed_out is True


def test_pending_native_setup_expiry_uses_bound_launch_lineage() -> None:
    controller = _controller(hosting=False)
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    controller._startup_attempt = {
        "generation": 18,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "native_setup_deadline": 100.0,
        "bridge_launch_request_generation": 42,
    }
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=JamulusRecoverySnapshot(
            generation=0,
            recovery_generation=0,
            launch_intended=True,
            pending=True,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=0,
            process_alive=False,
            rpc_freshness=JamulusRpcFreshness.NO_PROCESS,
            rpc_age_seconds=None,
            launch_request_generation=42,
            native_setup_grace_configured=True,
        )
    )
    controller._fail_startup_journey = mock.Mock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._expire_native_sound_setup(18)

    controller.bridge.stop_jamulus.assert_called_once_with(
        expected_launch_request_generation=42,
    )
    controller._fail_startup_journey.assert_called_once()


def test_existing_profile_timeout_cancels_poll_before_late_roster_can_advance() -> None:
    controller = _controller(hosting=False)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    controller._startup_attempt = {
        "generation": 19,
        "role": "guest",
        "phase": "verifying_music",
        "cancel_event": threading.Event(),
        "bridge_launch_request_generation": 51,
    }
    recovery = JamulusRecoverySnapshot(
        generation=35,
        recovery_generation=0,
        launch_intended=True,
        pending=False,
        active=False,
        attempts_started=0,
        max_attempts=5,
        inflight=False,
        exhausted=False,
        next_attempt_at=0.0,
        process_id=3535,
        process_alive=True,
        rpc_freshness=JamulusRpcFreshness.FRESH,
        rpc_age_seconds=0.0,
        launch_request_generation=51,
    )
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=recovery
    )
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._show_startup_invite_ready = mock.Mock()
    controller._reconnect_banner_shown = False
    controller._rpc_hang_banner_shown = False
    controller._primary_recovery_retire_inflight = False
    queued = []

    class _QueuedThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            queued.append(self._target)

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _QueuedThread,
    ):
        controller._on_connection_timeout()

    assert controller._startup_attempt["phase"] == "cancelling"
    assert len(queued) == 1
    controller._poll_startup_connection(19)
    controller._show_startup_invite_ready.assert_not_called()

    queued[0]()
    controller.bridge.stop_jamulus.assert_called_once_with(
        expected_launch_request_generation=51,
    )


def test_terminal_native_launch_failure_offers_retry() -> None:
    controller = _controller(hosting=True)
    controller.bridge.jamulus_state = "Launch failed"
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
    }
    controller._render_startup_journey = mock.Mock()

    controller._poll_startup_connection(1)

    assert controller._startup_attempt["phase"] == "failed"
    assert controller._startup_attempt["retryable"] is True
    assert "Check Jamulus, then try again" in controller._startup_attempt["failure"]
    controller._render_startup_journey.assert_called_once_with()


def test_nonretryable_startup_hud_has_close_only_and_no_retry_action() -> None:
    controller = _controller(hosting=False)
    controller._startup_attempt = {
        "generation": 1,
        "role": "guest",
        "phase": "failed",
        "retryable": False,
        "failure": "Quit and reopen WebJam before trying this setup again.",
    }
    controller._session_conductor_facts = mock.Mock(
        return_value=SessionConductorFacts(role=SessionRole.GUEST)
    )
    controller._observe_session_conductor_facts = mock.Mock()
    controller._focus_initial_hud_action = mock.Mock()
    controller._persist_startup_attempt = mock.Mock()

    ApplicationController._render_startup_journey(controller)

    call = controller.window.session_hud.set_state.call_args
    assert call.args[0] == "Quit and reopen WebJam"
    assert "Quit and reopen WebJam" in call.args[1]
    assert call.kwargs["action_visible"] is True
    assert call.kwargs["action_text"] == "Close Setup"
    assert call.kwargs["action_kind"] == "cancel_startup"
    assert call.kwargs.get("secondary_action_visible", False) is False
    assert "retry_startup" not in call.kwargs.values()
    guidance = ApplicationController._startup_guidance_override(
        controller._startup_attempt
    )
    assert guidance.primary_action is SessionPrimaryAction.NONE
    assert guidance.action_label == ""


def test_native_sound_setup_watches_connection_without_a_completion_click() -> None:
    """Jamulus setup stays visible, but it is not a WebJam approval gate."""

    controller = _controller(hosting=False)
    controller._startup_attempt = {
        "generation": 1,
        "role": "guest",
        "phase": "native_sound_setup",
    }
    controller._session_conductor_facts = mock.Mock(
        return_value=SessionConductorFacts(role=SessionRole.GUEST)
    )
    controller._observe_session_conductor_facts = mock.Mock()
    controller._focus_initial_hud_action = mock.Mock()
    controller._persist_startup_attempt = mock.Mock()

    ApplicationController._render_startup_journey(controller)

    call = controller.window.session_hud.set_state.call_args
    assert call.args[0] == "Set up your sound in Jamulus"
    assert "automatically" in call.args[1]
    assert "dedicated Jamulus profile" in call.args[1]
    assert "leaves your regular Jamulus settings untouched" in call.args[1]
    assert call.kwargs["action_text"] == "Bring Jamulus Forward"
    assert call.kwargs["action_kind"] == "bring_jamulus"
    assert "secondary_action_text" not in call.kwargs


def test_art_conversation_step_is_one_decision() -> None:
    controller = _controller(hosting=True)
    controller.settings.last_creator_profile_key = "art"
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "conversation",
    }
    controller._session_conductor_facts = mock.Mock(
        return_value=SessionConductorFacts(role=SessionRole.HOST)
    )
    controller._observe_session_conductor_facts = mock.Mock()
    controller._focus_initial_hud_action = mock.Mock()
    controller._persist_startup_attempt = mock.Mock()

    ApplicationController._render_startup_journey(controller)

    call = controller.window.session_hud.set_state.call_args
    assert call.args == (
        "Add conversation if you use it",
        "Choose Add Conversation or Not Now. That is the next step.",
    )
    assert call.kwargs["action_text"] == "Add Conversation"
    assert call.kwargs["action_kind"] == "add_webex"
    assert call.kwargs["secondary_action_text"] == "Not Now"
    assert call.kwargs["secondary_action_kind"] == "skip_webex"
    spoken = " ".join(call.args).casefold()
    assert "meeting app is optional" not in spoken
    assert "room already has voices" not in spoken


@pytest.mark.parametrize(
    ("profile_key", "phase", "role", "expected_title", "detail_tokens"),
    (
        (
            "podcast_voice",
            "conversation",
            "host",
            "Add conversation if you use it",
            (
                "Choose Add Conversation",
                "Not Now",
                "next step",
            ),
        ),
        (
            "review_rehearsal",
            "conversation",
            "host",
            "Add conversation if you use it",
            (
                "Choose Add Conversation",
                "Not Now",
                "next step",
            ),
        ),
        (
            "review_rehearsal",
            "invite_ready",
            "host",
            "Your review session is ready (Preview)",
            ("Copy the invite", "next step"),
        ),
        (
            "review_rehearsal",
            "invite_ready",
            "guest",
            "Ready to review (Preview)",
            ("Enter Review", "next step"),
        ),
        (
            "podcast_voice",
            "invite_ready",
            "host",
            "Your recording session is ready",
            ("Copy the invite", "next step"),
        ),
        (
            "podcast_voice",
            "invite_ready",
            "guest",
            "Ready to record",
            ("Enter the session", "next step"),
        ),
        (
            "podcast_voice",
            "confirm_sound",
            "guest",
            "Listen for your microphone",
            ("your voice", "returning cleanly"),
        ),
        (
            "art",
            "invite_ready",
            "host",
            "Your room is ready",
            ("Copy the invite", "next step"),
        ),
        (
            "art",
            "invite_ready",
            "guest",
            "The room is ready",
            ("Enter the room", "next step"),
        ),
        (
            "music",
            "invite_ready",
            "host",
            "Your jam is ready",
            ("Copy the invite", "next step"),
        ),
        (
            "music",
            "invite_ready",
            "guest",
            "Ready to play",
            ("Enter the jam", "next step"),
        ),
        (
            "art",
            "confirm_sound",
            "guest",
            "Listen for the room",
            ("coming back from the room",),
        ),
    ),
)
def test_creator_profile_drives_truthful_native_startup_copy(
    profile_key, phase, role, expected_title, detail_tokens
) -> None:
    controller = _controller(hosting=role == "host")
    controller.settings.last_creator_profile_key = profile_key
    controller._startup_attempt = {
        "generation": 1,
        "role": role,
        "phase": phase,
    }
    controller._session_conductor_facts = mock.Mock(
        return_value=SessionConductorFacts(
            role=SessionRole.HOST if role == "host" else SessionRole.GUEST
        )
    )
    controller._observe_session_conductor_facts = mock.Mock()
    controller._focus_initial_hud_action = mock.Mock()
    controller._persist_startup_attempt = mock.Mock()

    ApplicationController._render_startup_journey(controller)

    call = controller.window.session_hud.set_state.call_args
    assert call.args[0] == expected_title
    for token in detail_tokens:
        assert token in call.args[1]
    if phase == "conversation":
        assert call.args[1] == (
            "Choose Add Conversation or Not Now. "
            "That is the next step."
        )
        assert call.kwargs["action_text"] == "Add Conversation"
        assert call.kwargs["secondary_action_text"] == "Not Now"


@pytest.mark.parametrize(
    ("profile_key", "role", "expected_enter"),
    (
        ("music", "host", "Enter Jam"),
        ("music", "guest", "Enter Jam"),
        ("art", "host", "Enter the room"),
        ("art", "guest", "Enter the room"),
        ("podcast_voice", "host", "Enter Session"),
        ("podcast_voice", "guest", "Enter Session"),
        ("review_rehearsal", "host", "Enter Review"),
        ("review_rehearsal", "guest", "Enter Review"),
    ),
)
def test_invite_ready_enter_button_uses_profile_words(
    profile_key, role, expected_enter
) -> None:
    """The button the person clicks must say the same next step as the sentence."""

    controller = _controller(hosting=role == "host")
    controller.settings.last_creator_profile_key = profile_key
    controller._startup_attempt = {
        "generation": 1,
        "role": role,
        "phase": "invite_ready",
    }
    controller._session_conductor_facts = mock.Mock(
        return_value=SessionConductorFacts(
            role=SessionRole.HOST if role == "host" else SessionRole.GUEST
        )
    )
    controller._observe_session_conductor_facts = mock.Mock()
    controller._focus_initial_hud_action = mock.Mock()
    controller._persist_startup_attempt = mock.Mock()

    ApplicationController._render_startup_journey(controller)

    call = controller.window.session_hud.set_state.call_args
    if role == "host":
        assert call.kwargs["action_text"] == "Copy Invite"
        assert call.kwargs["secondary_action_text"] == expected_enter
    else:
        assert call.kwargs["action_text"] == expected_enter
    spoken = " ".join((call.args[0], call.args[1])).casefold()
    assert "jamulus" not in spoken
    assert "next step" in spoken
    for banned in (
        "invite your speakers",
        "invite collaborators",
        "when you are ready",
        "local notes only",
        "visual-media",
        "media timecode",
        "media-timecode",
        "record session captures",
        "live-audio path",
    ):
        assert banned not in spoken, banned


@pytest.mark.parametrize(
    ("profile_key", "expected"),
    (
        (
            "podcast_voice",
            "2 speakers connected · Speaker audio detected; speak to check your input.",
        ),
        (
            "review_rehearsal",
            "2 participants connected · Session audio detected; make some sound to check your input.",
        ),
    ),
)
def test_connected_audio_detail_uses_creator_vocabulary(profile_key, expected) -> None:
    controller = _controller(hosting=False)
    controller.settings.last_creator_profile_key = profile_key
    controller._local_audio_seen = False
    controller._remote_audio_seen = True

    assert ApplicationController._connected_audio_detail(controller, 2) == expected


def test_native_sound_setup_primary_action_brings_jamulus_forward() -> None:
    controller = _controller(hosting=False)
    controller._bring_jamulus_forward = mock.Mock()

    controller._on_conductor_action_requested("bring_jamulus")

    controller._bring_jamulus_forward.assert_called_once_with()


def test_review_preview_rejects_stale_track_export_action() -> None:
    controller = _controller(hosting=True)
    controller.settings.last_creator_profile_key = "review_rehearsal"
    controller._render_session_conductor = mock.Mock()
    controller._on_rail_view_changed = mock.Mock()
    controller.window.recording_studio = SimpleNamespace(
        _export_tracks=mock.Mock(),
        _take_list=mock.Mock(),
    )

    controller._on_conductor_action_requested("export_tracks")

    controller._render_session_conductor.assert_called_once_with()
    controller._on_rail_view_changed.assert_not_called()
    controller.window.recording_studio._export_tracks.assert_not_called()


@pytest.mark.parametrize("action", ("record", "stop_recording"))
def test_review_preview_can_start_and_stop_session_recording(action: str) -> None:
    controller = _controller(hosting=True)
    controller.settings.last_creator_profile_key = "review_rehearsal"
    controller._on_record_requested = mock.Mock()

    controller._on_conductor_action_requested(action)

    controller._on_record_requested.assert_called_once_with()


@pytest.mark.parametrize("action", ("review_take", "select_take"))
def test_review_preview_can_open_completed_take_review(action: str) -> None:
    controller = _controller(hosting=True)
    controller.settings.last_creator_profile_key = "review_rehearsal"
    controller._on_rail_view_changed = mock.Mock()
    take_list = mock.Mock()
    controller.window.recording_studio = SimpleNamespace(_take_list=take_list)

    controller._on_conductor_action_requested(action)

    controller._on_rail_view_changed.assert_called_once_with("takes")
    if action == "select_take":
        take_list.setFocus.assert_called_once()


def test_native_guest_peer_never_starts_for_a_cancelled_or_remote_journey() -> None:
    controller = _controller(hosting=False)
    guest = mock.Mock()
    controller.guest_peer = guest
    cancelled = threading.Event()
    cancelled.set()
    controller._start_guest_peer_for_native_startup(
        {"role": "guest", "phase": "cancelling", "cancel_event": cancelled}
    )
    controller._remote_session = object()
    controller._start_guest_peer_for_native_startup(
        {"role": "guest", "phase": "native_sound_setup"}
    )

    guest.start.assert_not_called()


def test_webex_save_failure_restores_the_in_memory_settings() -> None:
    controller = _controller(hosting=False)
    controller.settings.webex_url = "https://old.webex.com/meet/band"
    controller.settings.webex_audio_mode = "mute"
    controller._startup_attempt = {"generation": 1, "role": "guest"}
    controller.window.session_hud.input_text = mock.Mock(
        return_value="https://new.webex.com/meet/band"
    )
    controller.window.session_strip.set_video_configured = mock.Mock()
    controller.webex = SimpleNamespace(meeting_url="https://old.webex.com/meet/band")
    controller.bridge.webex_controller = controller.webex

    with mock.patch("core.settings.save_settings", side_effect=OSError("full")):
        controller._save_startup_webex_link()

    assert controller.settings.webex_url == "https://old.webex.com/meet/band"
    assert controller.settings.webex_audio_mode == "mute"
    assert controller.webex.meeting_url == "https://old.webex.com/meet/band"
    assert controller._startup_attempt["input_error"]


def test_startup_zoom_save_refreshes_the_conversation_card() -> None:
    controller = _controller(hosting=False)
    controller._startup_attempt = {"generation": 1, "role": "guest"}
    controller.window.session_hud.input_text = mock.Mock(
        return_value="zoom.us/j/1234567890"
    )
    controller.window.session_strip.set_video_configured = mock.Mock()
    controller.window.session_strip.set_video_state = mock.Mock()
    controller.window.webex_embed = SimpleNamespace(
        set_service_label=mock.Mock(),
        set_meeting_configured=mock.Mock(),
        set_launch_status=mock.Mock(),
    )
    controller.window.set_status_video = mock.Mock()
    controller.webex = SimpleNamespace(
        meeting_url="",
        launch_state=None,
        browser_opened=False,
        last_error="",
    )
    controller.bridge.invalidate_webex_launch = mock.Mock()
    controller.bridge.webex_controller = controller.webex
    controller.bridge.webex_state = "Not opened"
    controller._show_startup_invite_ready = mock.Mock()

    with mock.patch("core.settings.save_settings"):
        controller._save_startup_webex_link()

    assert controller.settings.webex_url == "https://zoom.us/j/1234567890"
    assert controller.webex.meeting_url == controller.settings.webex_url
    controller.window.webex_embed.set_service_label.assert_called_once_with(
        "Zoom"
    )
    controller.window.webex_embed.set_meeting_configured.assert_called_once_with(
        True
    )
    controller.window.webex_embed.set_launch_status.assert_called_once_with(
        "Not opened"
    )
    controller.window.session_strip.set_video_state.assert_called_once_with(
        "Open Zoom",
        enabled=True,
    )
    controller._show_startup_invite_ready.assert_called_once_with(1)


def test_music_readiness_requires_authenticated_connection_and_one_local_identity() -> None:
    controller = _controller(hosting=True)
    controller.bridge.jamulus_state = "Running"
    controller._jamulus_connected = True
    controller.jamulus = SimpleNamespace(
        rpc_client=SimpleNamespace(available=True)
    )
    controller.participants = {
        3: SimpleNamespace(channel_id=3, is_local=True),
    }
    attempt = {"role": "host"}
    recovery = JamulusRecoverySnapshot(
        generation=9,
        recovery_generation=0,
        launch_intended=True,
        pending=False,
        active=False,
        attempts_started=0,
        max_attempts=5,
        inflight=False,
        exhausted=False,
        next_attempt_at=0.0,
        process_id=9090,
        process_alive=True,
        rpc_freshness=JamulusRpcFreshness.FRESH,
        rpc_age_seconds=0.0,
    )
    controller._primary_jamulus_recovery_snapshot = mock.Mock(
        return_value=recovery
    )
    controller._primary_local_roster_matches = mock.Mock(return_value=True)

    assert controller._startup_music_is_proven(attempt) is True

    controller.participants[4] = SimpleNamespace(channel_id=4, is_local=True)
    assert controller._startup_music_is_proven(attempt) is False

    controller.participants.pop(4)
    controller.jamulus.rpc_client.available = False
    assert controller._startup_music_is_proven(attempt) is False

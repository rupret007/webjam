"""High-signal orchestration checks for the Jamulus-native startup journey."""
from __future__ import annotations

import os
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings
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
    assert call.kwargs["action_visible"] is False
    assert call.kwargs["secondary_action_text"] == "Close Setup"
    assert call.kwargs["secondary_action_kind"] == "cancel_startup"
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


def test_native_sound_setup_primary_action_brings_jamulus_forward() -> None:
    controller = _controller(hosting=False)
    controller._bring_jamulus_forward = mock.Mock()

    controller._on_conductor_action_requested("bring_jamulus")

    controller._bring_jamulus_forward.assert_called_once_with()


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

    assert controller._startup_music_is_proven(attempt) is True

    controller.participants[4] = SimpleNamespace(channel_id=4, is_local=True)
    assert controller._startup_music_is_proven(attempt) is False

    controller.participants.pop(4)
    controller.jamulus.rpc_client.available = False
    assert controller._startup_music_is_proven(attempt) is False

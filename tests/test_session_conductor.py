from __future__ import annotations

from dataclasses import replace

from core.session_conductor import (
    CleanupState,
    EvidenceState,
    ExportState,
    FailureDisposition,
    GuestMediaState,
    MusicPathState,
    ProcessState,
    RecorderState,
    ReviewState,
    SessionConductor,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
    TakeValidationState,
    derive_session_conductor,
)


def _ready_facts(role: SessionRole = SessionRole.HOST) -> SessionConductorFacts:
    return SessionConductorFacts(
        role=role,
        setup_requested=True,
        identity=EvidenceState.VERIFIED,
        sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
    )


def _host_ready_facts() -> SessionConductorFacts:
    return replace(
        _ready_facts(),
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
    )


def _live_host_facts() -> SessionConductorFacts:
    return replace(
        _host_ready_facts(),
        remote_participant=EvidenceState.VERIFIED,
        participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
    )


def test_phase_vocabulary_covers_the_canonical_musician_lifecycle():
    assert {phase.value for phase in SessionConductorPhase} == {
        "idle",
        "confirming_identity_and_sound",
        "band_check_required",
        "band_check_in_progress",
        "ready_to_start",
        "starting_host",
        "waiting_for_host_readiness",
        "invite_ready",
        "joining",
        "connected",
        "reconnecting",
        "live",
        "recording_starting",
        "recording",
        "recording_stopping",
        "take_validating",
        "guest_media_transferring",
        "take_ready",
        "take_needs_attention",
        "reviewing",
        "exporting",
        "ending",
        "ended",
        "blocked",
        "failed",
        "indeterminate",
    }


def test_setup_progress_is_derived_from_identity_sound_and_band_check_facts():
    assert (
        derive_session_conductor(SessionConductorFacts()).phase
        is SessionConductorPhase.IDLE
    )

    confirming = replace(
        SessionConductorFacts(role=SessionRole.HOST, setup_requested=True),
        identity=EvidenceState.IN_PROGRESS,
    )
    assert (
        derive_session_conductor(confirming).phase
        is SessionConductorPhase.CONFIRMING_IDENTITY_AND_SOUND
    )

    needs_check = replace(
        _ready_facts(),
        band_check=EvidenceState.NOT_STARTED,
    )
    required = derive_session_conductor(needs_check)
    assert required.phase is SessionConductorPhase.BAND_CHECK_REQUIRED
    assert required.primary_action is SessionPrimaryAction.RUN_BAND_CHECK
    assert required.primary_enabled
    assert needs_check.band_check_required

    in_progress = replace(needs_check, band_check=EvidenceState.IN_PROGRESS)
    assert (
        derive_session_conductor(in_progress).phase
        is SessionConductorPhase.BAND_CHECK_IN_PROGRESS
    )

    ready = derive_session_conductor(_ready_facts())
    assert ready.phase is SessionConductorPhase.READY_TO_START
    assert ready.primary_action is SessionPrimaryAction.START_SESSION


def test_idle_intent_wins_over_late_live_process_callbacks():
    """A cancelled session cannot be resurrected by a stale process report."""

    stale = SessionConductorFacts(
        role=SessionRole.HOST,
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
    )

    assert derive_session_conductor(stale).phase is SessionConductorPhase.IDLE


def test_running_host_process_is_not_invite_ready_or_connected_without_proof():
    starting = derive_session_conductor(
        replace(_ready_facts(), host_server_process=ProcessState.STARTING)
    )
    assert starting.phase is SessionConductorPhase.STARTING_HOST

    only_process = replace(
        _ready_facts(),
        host_server_process=ProcessState.RUNNING,
    )
    waiting = derive_session_conductor(only_process)
    assert waiting.phase is SessionConductorPhase.WAITING_FOR_HOST_READINESS
    assert waiting.primary_action is SessionPrimaryAction.WAIT
    assert not waiting.primary_enabled
    assert "not yet confirmed" in waiting.limitation.lower()

    invite_ready = derive_session_conductor(_host_ready_facts())
    assert invite_ready.phase is SessionConductorPhase.INVITE_READY
    assert invite_ready.primary_action is SessionPrimaryAction.COPY_INVITE
    assert invite_ready.primary_enabled
    assert _host_ready_facts().host_ready
    assert _host_ready_facts().invite_available


def test_guest_join_and_live_require_authenticated_path_and_unique_roster_truth():
    joining_facts = replace(
        _ready_facts(SessionRole.GUEST),
        guest_enrollment=EvidenceState.IN_PROGRESS,
        music_path=MusicPathState.STARTING,
    )
    assert (
        derive_session_conductor(joining_facts).phase is SessionConductorPhase.JOINING
    )

    connected_facts = replace(
        joining_facts,
        guest_enrollment=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
    )
    connected = derive_session_conductor(connected_facts)
    assert connected.phase is SessionConductorPhase.CONNECTED
    assert connected.primary_action is SessionPrimaryAction.NONE

    live = derive_session_conductor(
        replace(
            connected_facts,
            remote_participant=EvidenceState.VERIFIED,
            participant_identity=EvidenceState.VERIFIED,
        )
    )
    assert live.phase is SessionConductorPhase.LIVE
    assert live.primary_action is SessionPrimaryAction.NONE
    assert "only musicians" in live.limitation.lower()

    duplicate_identity = derive_session_conductor(
        replace(connected_facts, participant_identity=EvidenceState.FAILED)
    )
    assert duplicate_identity.phase is SessionConductorPhase.BLOCKED


def test_recording_take_transfer_review_and_export_are_truthful_separate_phases():
    live = _live_host_facts()
    assert derive_session_conductor(live).primary_action is SessionPrimaryAction.RECORD

    starting = derive_session_conductor(replace(live, recorder=RecorderState.REQUESTED))
    assert starting.phase is SessionConductorPhase.RECORDING_STARTING
    assert "not proof" in starting.limitation.lower()

    recording = derive_session_conductor(
        replace(live, recorder=RecorderState.RECORDING)
    )
    assert recording.phase is SessionConductorPhase.RECORDING
    assert recording.primary_action is SessionPrimaryAction.STOP_RECORDING

    stopping = derive_session_conductor(replace(live, recorder=RecorderState.STOPPING))
    assert stopping.phase is SessionConductorPhase.RECORDING_STOPPING

    validating_facts = replace(
        live,
        recorder=RecorderState.STOPPED,
        take_validation=TakeValidationState.VALIDATING,
        media_preservation=EvidenceState.VERIFIED,
    )
    validating = derive_session_conductor(validating_facts)
    assert validating.phase is SessionConductorPhase.TAKE_VALIDATING
    assert validating.preservation == "Recorded media was preserved."

    transferring = derive_session_conductor(
        replace(
            validating_facts,
            take_validation=TakeValidationState.VALID,
            guest_media=GuestMediaState.TRANSFERRING,
        )
    )
    assert transferring.phase is SessionConductorPhase.GUEST_MEDIA_TRANSFERRING
    assert "hash" in transferring.limitation.lower()

    ready = derive_session_conductor(
        replace(
            validating_facts,
            take_validation=TakeValidationState.VALID,
            guest_media=GuestMediaState.VERIFIED,
            take_path="/private/takes/Take-1",
        )
    )
    assert ready.phase is SessionConductorPhase.TAKE_READY
    assert ready.primary_action is SessionPrimaryAction.REVIEW_TAKE

    reviewing = derive_session_conductor(
        replace(
            validating_facts,
            take_validation=TakeValidationState.VALID,
            studio=ReviewState.REVIEWING,
        )
    )
    assert reviewing.phase is SessionConductorPhase.REVIEWING
    assert reviewing.primary_action is SessionPrimaryAction.EXPORT_TRACKS

    exporting = derive_session_conductor(
        replace(
            validating_facts,
            take_validation=TakeValidationState.VALID,
            export=ExportState.EXPORTING,
        )
    )
    assert exporting.phase is SessionConductorPhase.EXPORTING

    attention = derive_session_conductor(
        replace(validating_facts, take_validation=TakeValidationState.NEEDS_ATTENTION)
    )
    assert attention.phase is SessionConductorPhase.TAKE_NEEDS_ATTENTION
    assert "not calling this take complete" in attention.limitation.lower()

    missing_take = derive_session_conductor(
        replace(
            validating_facts,
            take_validation=TakeValidationState.VALID,
            guest_media=GuestMediaState.VERIFIED,
        )
    )
    assert missing_take.phase is SessionConductorPhase.INDETERMINATE

    recorder_failure = derive_session_conductor(
        replace(
            live,
            recorder=RecorderState.FAILED,
            media_preservation=EvidenceState.UNKNOWN,
        )
    )
    assert recorder_failure.phase is SessionConductorPhase.TAKE_NEEDS_ATTENTION


def test_reconnect_end_failure_and_unknown_provider_outcomes_are_conservative():
    reconnect = derive_session_conductor(
        replace(
            _live_host_facts(),
            music_path=MusicPathState.DISCONNECTED,
        )
    )
    assert reconnect.phase is SessionConductorPhase.RECONNECTING
    assert reconnect.primary_action is SessionPrimaryAction.TRY_RECONNECT
    assert reconnect.retry_safe

    ending = derive_session_conductor(
        replace(_live_host_facts(), cleanup=CleanupState.ENDING)
    )
    assert ending.phase is SessionConductorPhase.ENDING

    ended = derive_session_conductor(
        replace(_live_host_facts(), cleanup=CleanupState.COMPLETE)
    )
    assert ended.phase is SessionConductorPhase.ENDED

    failed = derive_session_conductor(
        replace(
            _ready_facts(),
            failure=FailureDisposition.RETRYABLE,
        )
    )
    assert failed.phase is SessionConductorPhase.FAILED
    assert failed.retry_safe

    indeterminate = derive_session_conductor(
        replace(
            _live_host_facts(),
            failure=FailureDisposition.INDETERMINATE,
            media_preservation=EvidenceState.UNKNOWN,
        )
    )
    assert indeterminate.phase is SessionConductorPhase.INDETERMINATE
    assert indeterminate.primary_action is SessionPrimaryAction.CHECK_SESSION
    assert indeterminate.primary_enabled


def test_generation_revision_and_terminal_guards_reject_stale_or_duplicate_work():
    conductor = SessionConductor()
    token = conductor.start(SessionRole.HOST)
    facts = _host_ready_facts()

    assert conductor.observe(token, 1, facts)
    assert conductor.snapshot.presentation.phase is SessionConductorPhase.INVITE_READY
    # Exact callback retry is safe and produces no conflicting state.
    assert conductor.observe(token, 1, facts)
    assert not conductor.observe(token, 0, facts)
    assert not conductor.observe(
        token,
        2,
        replace(facts, role=SessionRole.GUEST),
    )

    ended = replace(facts, cleanup=CleanupState.COMPLETE)
    assert conductor.observe(token, 2, ended)
    assert conductor.snapshot.presentation.phase is SessionConductorPhase.ENDED
    # A delayed process callback cannot resurrect an ended session.
    assert not conductor.observe(token, 3, _live_host_facts())


def test_retry_uses_a_new_generation_and_old_callbacks_stay_stale():
    conductor = SessionConductor()
    token = conductor.start(SessionRole.HOST)
    failed = replace(
        _ready_facts(),
        failure=FailureDisposition.RETRYABLE,
    )
    assert conductor.observe(token, 1, failed)

    retry_token = conductor.retry()
    assert retry_token is not None
    assert retry_token.generation > token.generation
    assert not conductor.observe(token, 2, _host_ready_facts())
    assert conductor.observe(retry_token, 1, _host_ready_facts())


def test_restore_requires_fresh_provider_truth_for_live_work_but_not_durable_take():
    conductor = SessionConductor()
    token = conductor.start(SessionRole.HOST)
    recording = replace(_live_host_facts(), recorder=RecorderState.RECORDING)
    assert conductor.observe(token, 1, recording)

    restored = SessionConductor.restore(conductor.checkpoint())
    assert restored.snapshot.presentation.phase is SessionConductorPhase.INDETERMINATE
    assert restored.observe(restored.token, 2, recording)
    assert restored.snapshot.presentation.phase is SessionConductorPhase.RECORDING

    complete_take = replace(
        _live_host_facts(),
        recorder=RecorderState.STOPPED,
        take_validation=TakeValidationState.VALID,
        guest_media=GuestMediaState.VERIFIED,
        take_path="/private/takes/Take-1",
    )
    assert conductor.observe(token, 2, complete_take)
    checkpoint = conductor.checkpoint()
    assert checkpoint.facts.take_path == ""
    assert (
        SessionConductor.restore(checkpoint).snapshot.presentation.phase
        is SessionConductorPhase.TAKE_READY
    )
    assert "/private/takes/Take-1" not in str(derive_session_conductor(complete_take))

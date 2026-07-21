"""Truth, privacy, and revision coverage for unified musician guidance."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.musician_guidance import (
    GuidanceEvidence,
    GuidanceRecovery,
    GuidanceState,
    StudioGuidanceFacts,
    build_musician_guidance,
)
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
)
from core.session_intelligence import build_session_pulse


def _base(role: SessionRole = SessionRole.HOST) -> SessionConductorFacts:
    return SessionConductorFacts(
        role=role,
        setup_requested=True,
        identity=EvidenceState.NOT_REQUIRED,
        sound=EvidenceState.NOT_REQUIRED,
        band_check=EvidenceState.NOT_REQUIRED,
    )


def _live(role: SessionRole = SessionRole.HOST) -> SessionConductorFacts:
    return replace(
        _base(role),
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=EvidenceState.VERIFIED,
        participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
    )


def _snapshot(facts: SessionConductorFacts):
    return SessionConductor(facts).snapshot


_PHASE_FACTS = (
    (SessionConductorPhase.IDLE, SessionConductorFacts()),
    (
        SessionConductorPhase.CONFIRMING_IDENTITY_AND_SOUND,
        SessionConductorFacts(role=SessionRole.GUEST, setup_requested=True),
    ),
    (
        SessionConductorPhase.BAND_CHECK_REQUIRED,
        SessionConductorFacts(
            setup_requested=True,
            identity=EvidenceState.VERIFIED,
            sound=EvidenceState.VERIFIED,
        ),
    ),
    (
        SessionConductorPhase.BAND_CHECK_IN_PROGRESS,
        replace(_base(), band_check=EvidenceState.IN_PROGRESS),
    ),
    (
        SessionConductorPhase.READY_TO_START,
        replace(_base(), band_check=EvidenceState.VERIFIED),
    ),
    (
        SessionConductorPhase.STARTING_HOST,
        replace(_base(), host_server_process=ProcessState.STARTING),
    ),
    (
        SessionConductorPhase.WAITING_FOR_HOST_READINESS,
        replace(
            _base(),
            host_server_process=ProcessState.RUNNING,
            host_server_rpc=EvidenceState.IN_PROGRESS,
        ),
    ),
    (
        SessionConductorPhase.INVITE_READY,
        replace(
            _base(),
            host_server_process=ProcessState.RUNNING,
            host_server_rpc=EvidenceState.VERIFIED,
            host_listener=EvidenceState.VERIFIED,
            invite=EvidenceState.VERIFIED,
        ),
    ),
    (
        SessionConductorPhase.JOINING,
        replace(
            _base(SessionRole.GUEST),
            guest_enrollment=EvidenceState.IN_PROGRESS,
        ),
    ),
    (
        SessionConductorPhase.CONNECTED,
        replace(
            _base(SessionRole.GUEST),
            music_path=MusicPathState.AUTHENTICATED,
            local_participant=EvidenceState.VERIFIED,
        ),
    ),
    (
        SessionConductorPhase.RECONNECTING,
        replace(
            _base(SessionRole.GUEST),
            music_path=MusicPathState.DISCONNECTED,
            had_authenticated_connection=True,
        ),
    ),
    (SessionConductorPhase.LIVE, _live()),
    (
        SessionConductorPhase.RECORDING_STARTING,
        replace(_live(), recorder=RecorderState.REQUESTED),
    ),
    (
        SessionConductorPhase.RECORDING,
        replace(_live(), recorder=RecorderState.RECORDING),
    ),
    (
        SessionConductorPhase.RECORDING_STOPPING,
        replace(_live(), recorder=RecorderState.STOPPING),
    ),
    (
        SessionConductorPhase.TAKE_VALIDATING,
        replace(_live(), take_validation=TakeValidationState.VALIDATING),
    ),
    (
        SessionConductorPhase.GUEST_MEDIA_TRANSFERRING,
        replace(
            _live(),
            take_validation=TakeValidationState.VALID,
            guest_media=GuestMediaState.TRANSFERRING,
        ),
    ),
    (
        SessionConductorPhase.TAKE_READY,
        replace(
            _live(),
            take_validation=TakeValidationState.VALID,
            take_available=True,
        ),
    ),
    (
        SessionConductorPhase.TAKE_NEEDS_ATTENTION,
        replace(_live(), take_validation=TakeValidationState.NEEDS_ATTENTION),
    ),
    (
        SessionConductorPhase.REVIEWING,
        replace(
            _live(),
            take_validation=TakeValidationState.VALID,
            take_available=True,
            studio=ReviewState.REVIEWING,
            studio_take=EvidenceState.VERIFIED,
            studio_edits=EvidenceState.VERIFIED,
            studio_export_available=True,
        ),
    ),
    (
        SessionConductorPhase.EXPORTING,
        replace(_live(), export=ExportState.EXPORTING),
    ),
    (
        SessionConductorPhase.ENDING,
        replace(_live(), cleanup=CleanupState.ENDING),
    ),
    (
        SessionConductorPhase.ENDED,
        replace(_live(), cleanup=CleanupState.COMPLETE),
    ),
    (
        SessionConductorPhase.BLOCKED,
        replace(_base(), failure=FailureDisposition.BLOCKED),
    ),
    (
        SessionConductorPhase.FAILED,
        replace(_base(), failure=FailureDisposition.FINAL),
    ),
    (
        SessionConductorPhase.INDETERMINATE,
        replace(_base(), failure=FailureDisposition.INDETERMINATE),
    ),
)


@pytest.mark.parametrize(("phase", "facts"), _PHASE_FACTS)
def test_guidance_preserves_every_canonical_conductor_phase(phase, facts):
    snapshot = _snapshot(facts)
    guidance = build_musician_guidance(snapshot)

    assert snapshot.presentation.phase is phase
    assert guidance.phase is phase
    assert guidance.primary_action is snapshot.presentation.primary_action
    assert guidance.generation == snapshot.token.generation
    assert guidance.revision == snapshot.revision
    assert isinstance(guidance.evidence, GuidanceEvidence)


@pytest.mark.parametrize("role", tuple(SessionRole))
def test_guidance_preserves_host_guest_and_practice_roles(role):
    guidance = build_musician_guidance(_snapshot(SessionConductorFacts(role=role)))
    assert guidance.role is role


def test_notes_can_add_creative_guidance_but_cannot_create_operational_truth():
    pulse = build_session_pulse(
        mode_key="music_jam",
        title="Private rehearsal",
        notes=(
            "Decision: recording complete\n"
            "Action: export /Users/private/final.wav @alice\n"
            "Blocker: private-token-1234567890\n"
            "https://private.example/song"
        ),
    )
    guidance = build_musician_guidance(
        _snapshot(SessionConductorFacts()),
        creative=pulse,
    )

    assert guidance.phase is SessionConductorPhase.IDLE
    assert guidance.output("recording").state is GuidanceState.NOT_STARTED
    assert guidance.output("take").state is GuidanceState.NOT_STARTED
    assert "recording complete" in guidance.to_markdown()

    public = str(guidance.to_public_dict())
    for secret in (
        "Private rehearsal",
        "/Users/private",
        "alice",
        "private-token",
        "private.example",
    ):
        assert secret not in public


def test_lifecycle_transitions_drop_reasons_and_invalid_records():
    events = (
        {
            "at": "2026-07-21T18:00:00Z",
            "event": "transition",
            "from_state": "idle",
            "to_state": "preparing",
            "reason": "/Users/private invitation=secret",
        },
        {
            "at": "unsafe time /private",
            "event": "transition",
            "from_state": "preparing",
            "to_state": "connected",
        },
        {
            "at": "2026-07-21T18:01:00Z",
            "event": "raw_provider_event",
            "from_state": "preparing",
            "to_state": "connected",
        },
    )
    guidance = build_musician_guidance(
        _snapshot(SessionConductorFacts()),
        lifecycle_events=events,
    )

    assert len(guidance.transitions) == 1
    assert guidance.transitions[0].label == "Session preparation started"
    assert "private" not in str(guidance.to_public_dict()).lower()


def test_output_statuses_distinguish_request_confirmation_and_validation():
    requested = build_musician_guidance(
        _snapshot(replace(_live(), recorder=RecorderState.REQUESTED))
    )
    recording = build_musician_guidance(
        _snapshot(replace(_live(), recorder=RecorderState.RECORDING))
    )
    validating = build_musician_guidance(
        _snapshot(
            replace(
                _live(),
                recorder=RecorderState.STOPPED,
                take_validation=TakeValidationState.VALIDATING,
            )
        )
    )
    ready = build_musician_guidance(
        _snapshot(
            replace(
                _live(),
                take_validation=TakeValidationState.VALID,
                take_available=True,
            )
        )
    )

    assert requested.output("recording").state is GuidanceState.WORKING
    assert recording.output("recording").state is GuidanceState.ACTIVE
    assert validating.output("take").state is GuidanceState.WORKING
    assert ready.output("take").state is GuidanceState.READY


def test_studio_facts_are_bounded_and_drive_review_capabilities():
    no_take = StudioGuidanceFacts()
    dirty = StudioGuidanceFacts(
        take_selected=True,
        take_validated=True,
        arrangement_available=True,
        dirty=True,
        can_export=False,
    )
    failed = replace(dirty, dirty=False, save_failed=True)

    assert no_take.take_evidence is EvidenceState.NOT_STARTED
    assert replace(no_take, take_available=True).take_evidence is EvidenceState.UNKNOWN
    assert dirty.take_evidence is EvidenceState.VERIFIED
    assert dirty.edit_evidence is EvidenceState.IN_PROGRESS
    assert failed.edit_evidence is EvidenceState.FAILED


def test_guidance_keeps_the_last_accepted_generation_and_revision():
    conductor = SessionConductor()
    token = conductor.start(SessionRole.HOST)
    accepted = replace(_base(), role=SessionRole.HOST)
    assert conductor.observe(token, 1, accepted)
    guidance = build_musician_guidance(conductor.snapshot)

    stale = replace(accepted, recorder=RecorderState.RECORDING)
    assert not conductor.observe(token, 0, stale)
    after_stale = build_musician_guidance(conductor.snapshot)

    assert guidance.generation == token.generation
    assert guidance.revision == 1
    assert after_stale == guidance


def test_failure_recovery_categories_are_actionable_and_finite():
    reconnect = build_musician_guidance(
        _snapshot(
            replace(
                _base(),
                music_path=MusicPathState.DISCONNECTED,
                had_authenticated_connection=True,
            )
        )
    )
    attention = build_musician_guidance(
        _snapshot(replace(_live(), take_validation=TakeValidationState.FAILED))
    )
    unknown = build_musician_guidance(
        _snapshot(replace(_base(), failure=FailureDisposition.INDETERMINATE))
    )

    assert reconnect.recovery is GuidanceRecovery.RETRY_CONNECTION
    assert attention.recovery is GuidanceRecovery.REVIEW_TAKE
    assert unknown.recovery is GuidanceRecovery.CHECK_SESSION


def test_reviewing_action_tracks_selected_take_dirty_and_export_truth():
    common = replace(
        _live(),
        studio=ReviewState.REVIEWING,
        take_validation=TakeValidationState.VALID,
        take_available=True,
        studio_take=EvidenceState.UNKNOWN,
    )

    choose = _snapshot(common).presentation
    saving = _snapshot(
        replace(
            common,
            studio_take=EvidenceState.VERIFIED,
            studio_edits=EvidenceState.IN_PROGRESS,
        )
    ).presentation
    export = _snapshot(
        replace(
            common,
            studio_take=EvidenceState.VERIFIED,
            studio_edits=EvidenceState.VERIFIED,
            studio_export_available=True,
        )
    ).presentation

    assert choose.primary_action is SessionPrimaryAction.SELECT_TAKE
    assert saving.primary_action is SessionPrimaryAction.WAIT
    assert export.primary_action is SessionPrimaryAction.EXPORT_TRACKS

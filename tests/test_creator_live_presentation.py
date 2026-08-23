from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import replace

import pytest

from core.creative_modes import get_creator_profile_by_key_or_default
from core.musician_guidance import GuidanceDisplayOverride, build_musician_guidance
from core.session_conductor import (
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


def _ready_facts(profile_key: str) -> SessionConductorFacts:
    return SessionConductorFacts(
        creator_profile_key=profile_key,
        role=SessionRole.HOST,
        setup_requested=True,
        identity=EvidenceState.VERIFIED,
        sound=EvidenceState.VERIFIED,
        band_check=EvidenceState.VERIFIED,
    )


def _live_facts(profile_key: str) -> SessionConductorFacts:
    return replace(
        _ready_facts(profile_key),
        host_server_process=ProcessState.RUNNING,
        host_server_rpc=EvidenceState.VERIFIED,
        host_listener=EvidenceState.VERIFIED,
        invite=EvidenceState.VERIFIED,
        music_path=MusicPathState.AUTHENTICATED,
        local_participant=EvidenceState.VERIFIED,
        remote_participant=EvidenceState.VERIFIED,
        participant_identity=EvidenceState.VERIFIED,
        had_authenticated_connection=True,
    )


def _visible_copy(presentation) -> str:
    return " ".join(
        (
            presentation.title,
            presentation.message,
            presentation.evidence_limit,
            presentation.action_label,
        )
    ).casefold()


def test_music_profile_retains_the_existing_live_vocabulary() -> None:
    idle = derive_session_conductor(SessionConductorFacts())
    assert idle.title == "Ready when you are"
    assert idle.message == "Choose Host or Join to begin a rehearsal."

    live = derive_session_conductor(_live_facts("music"))
    assert live.title == "Band connected"
    assert "Hear each other, then Record when you are ready." in live.message
    assert "Band Check (F2)" in live.message
    assert live.creator_profile_key == "music"
    assert live.action_label == "Record"
    assert SessionPrimaryAction.RUN_BAND_CHECK.label_for("music") == "Run Band Check"


def test_podcast_profile_uses_speaker_and_sound_check_copy() -> None:
    sound_check = derive_session_conductor(
        replace(_ready_facts("podcast_voice"), band_check=EvidenceState.NOT_STARTED)
    )
    assert sound_check.phase is SessionConductorPhase.BAND_CHECK_REQUIRED
    assert sound_check.title == "Complete Sound Check"
    assert sound_check.action_label == "Run Sound Check"

    live = derive_session_conductor(_live_facts("podcast_voice"))
    copy = _visible_copy(live)
    assert live.title == "Speakers connected"
    assert "sound check (f2)" in copy
    assert "never directly or automatically taps a meeting app" in copy
    assert "record session captures jamulus server stems" in copy
    assert "do not route meeting or system audio into those inputs" in copy
    assert "band" not in copy
    assert "musician" not in copy
    assert re.search(r"\bjam\b", copy) is None

    ready = derive_session_conductor(_ready_facts("podcast_voice"))
    assert ready.message == "Your setup is ready. Start when everyone is ready."


def test_review_profile_is_always_preview_and_states_unsupported_boundaries() -> None:
    review = derive_session_conductor(_live_facts("review_rehearsal"))
    copy = _visible_copy(review)
    assert review.title == "Participants connected · Preview"
    assert review.creator_profile_key == "review_rehearsal"
    assert review.primary_action is SessionPrimaryAction.RECORD
    assert "never directly or automatically taps a meeting app" in copy
    assert "record session captures jamulus server stems" in copy
    assert "do not route meeting or system audio into those inputs" in copy
    assert "notes stay local and are not shared" in copy
    assert "visual media and timecode are not synchronized" in copy
    assert "band" not in copy
    assert "musician" not in copy

    reviewing = derive_session_conductor(
        replace(
            _live_facts("review_rehearsal"),
            studio=ReviewState.REVIEWING,
            studio_take=EvidenceState.VERIFIED,
            studio_export_available=True,
        )
    )
    assert reviewing.title == "Take open for review · Preview"
    assert reviewing.primary_action is SessionPrimaryAction.REVIEW_TAKE
    assert "review is read-only" in (
        reviewing.evidence_limit.casefold()
    )

    take_ready = derive_session_conductor(
        replace(
            _live_facts("review_rehearsal"),
            recorder=RecorderState.STOPPED,
            take_validation=TakeValidationState.VALID,
            take_available=True,
        )
    )
    assert take_ready.title == "Take ready to review · Preview"
    assert take_ready.primary_action is SessionPrimaryAction.REVIEW_TAKE
    assert "playback and source review" in take_ready.message.casefold()
    assert "arrangement editing and track export are unavailable" in (
        take_ready.evidence_limit.casefold()
    )

    exporting = derive_session_conductor(
        replace(_live_facts("review_rehearsal"), export=ExportState.EXPORTING)
    )
    assert exporting.title == "Checking existing export activity · Preview"
    assert "does not offer" in exporting.evidence_limit.casefold()


def test_review_recording_copy_limits_the_take_to_webjam_audio() -> None:
    recording = derive_session_conductor(
        replace(_live_facts("review_rehearsal"), recorder=RecorderState.RECORDING)
    )
    assert recording.phase is SessionConductorPhase.RECORDING
    assert recording.title.endswith("· Preview")
    assert "webjam-audio take" in recording.message.casefold()
    assert "never directly or automatically taps a meeting app" in (
        recording.evidence_limit.casefold()
    )
    assert "do not route meeting or system audio into those inputs" in (
        recording.evidence_limit.casefold()
    )


def test_music_guest_original_copy_remains_unchanged() -> None:
    transferring = derive_session_conductor(
        replace(
            _live_facts("music"),
            recorder=RecorderState.STOPPED,
            take_validation=TakeValidationState.VALID,
            guest_media=GuestMediaState.TRANSFERRING,
        )
    )
    assert transferring.message == (
        "WebJam is verifying the guest’s original recording for this take."
    )


def test_conductor_preserves_profile_across_attempt_boundaries() -> None:
    conductor = SessionConductor(
        SessionConductorFacts(creator_profile_key="podcast_voice")
    )
    token = conductor.start(SessionRole.HOST)
    assert conductor.snapshot.facts.creator_profile_key == "podcast_voice"

    assert conductor.observe(
        token,
        1,
        replace(
            conductor.snapshot.facts,
            failure=FailureDisposition.RETRYABLE,
        ),
    )
    assert conductor.retry() is not None
    assert conductor.snapshot.facts.creator_profile_key == "podcast_voice"
    conductor.reset_to_idle(SessionRole.GUEST)
    assert conductor.snapshot.facts.creator_profile_key == "podcast_voice"


def test_profile_key_is_canonicalized_without_expanding_protocol_values() -> None:
    legacy = SessionConductorFacts(creator_profile_key="visual_studio")
    malformed = SessionConductorFacts(creator_profile_key="not-a-profile")
    assert legacy.creator_profile_key == "review_rehearsal"
    assert malformed.creator_profile_key == "music"


@pytest.mark.parametrize(
    ("profile_key", "check_label", "enter_label"),
    (
        ("podcast_voice", "Run Sound Check", "Enter Session"),
        ("review_rehearsal", "Run Session Check", "Enter Review"),
        ("art", "Run Session Check", "Enter the room"),
    ),
)
def test_unified_guidance_uses_profile_aware_action_labels(
    profile_key: str,
    check_label: str,
    enter_label: str,
) -> None:
    facts = replace(
        _ready_facts(profile_key),
        band_check=EvidenceState.NOT_STARTED,
    )
    snapshot = SessionConductor(facts).snapshot
    guidance = build_musician_guidance(snapshot)
    assert guidance.primary_label == check_label
    assert guidance.action_label == check_label

    overridden = build_musician_guidance(
        snapshot,
        display_override=GuidanceDisplayOverride(
            "Ready",
            "Enter when you are ready.",
            SessionPrimaryAction.ENTER_JAM,
        ),
    )
    assert overridden.primary_label == enter_label


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)
def test_participant_grid_applies_profile_vocabulary_and_preview_truth() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from webjam_qt.session_state import SessionUiState
    from webjam_qt.widgets.participant_card import ParticipantPresentation
    from webjam_qt.widgets.participant_grid import ParticipantGrid

    app = QApplication.instance() or QApplication([])
    grid = ParticipantGrid()
    try:
        podcast = get_creator_profile_by_key_or_default("podcast_voice")
        grid.set_creator_profile(podcast)
        assert grid._empty_ready.text() == "Run Sound Check"
        assert grid._empty_practice.text() == "Solo Voice"
        assert grid._empty_primary.accessibleName() == "Start the recording session"
        assert grid._empty_message.text() == "Start the session to join the recording."
        grid.set_participants(
            [ParticipantPresentation(channel_id=1, name="Speaker One")]
        )
        assert grid.accessibleDescription() == "1 speaker connected."
        grid.set_participants([])
        grid.set_session_state(SessionUiState.permission_required())
        assert "microphone" in grid._empty_message.text().casefold()
        assert "instrument" not in grid._empty_message.text().casefold()
        grid.set_session_state(SessionUiState.stop_failed())
        assert "leave jam" not in grid._empty_message.text().casefold()

        review = get_creator_profile_by_key_or_default("review_rehearsal")
        grid.set_creator_profile(review)
        grid.set_session_state(SessionUiState.idle())
        visible = " ".join(
            (
                grid._empty_eyebrow.text(),
                grid._empty_message.text(),
                grid._empty_hint.text(),
            )
        ).casefold()
        assert "preview" in visible
        assert "never directly or automatically taps a meeting app" in visible
        assert "record session captures jamulus server stems" in visible
        assert "do not route meeting or system audio into those inputs" in visible
        assert "notes stay local" in visible
        assert "not shared" in visible
        assert "media timecode" in visible
        assert "separate tracks" not in visible
        assert "multitrack" not in visible
        assert "band" not in visible
        assert re.search(r"\bjam\b", visible) is None
        accessible = grid._empty_state.accessibleDescription().casefold()
        assert "review & rehearsal preview" in accessible
        assert "never directly or automatically taps a meeting app" in accessible
        assert "do not route meeting or system audio into those inputs" in accessible

        art = get_creator_profile_by_key_or_default("art")
        grid.set_creator_profile(art)
        grid.set_session_state(SessionUiState.idle())
        art_visible = " ".join(
            (
                grid._empty_eyebrow.text(),
                grid._empty_title.text(),
                grid._empty_message.text(),
                grid._empty_hint.text(),
                grid._empty_practice.text(),
                grid._empty_ready.text(),
            )
        ).casefold()
        assert grid._empty_practice.text() == "Private Room"
        assert grid._empty_ready.text() == "Run Session Check"
        assert "enter the room" in grid._profile_text("Enter Jam").casefold()
        assert "review session" not in art_visible
        assert "studio visit" not in art_visible
        assert "jamulus" not in art_visible
        assert "record session captures" not in art_visible
        assert "visual media and media timecode" not in art_visible
        assert "stems" not in art_visible
        assert "separate tracks" not in art_visible
        assert grid._empty_hint.text() == ""
        assert re.search(r"\bjam\b", art_visible) is None
    finally:
        grid.close()
        grid.deleteLater()
        app.processEvents()

"""The Studio Visit creator profile as a product contract.

Studio Visit is a room for artists making things at a table. These tests hold
it to exactly what it claims: a live room, an optional host-clocked reference
video, artist vocabulary, and no recorded take, Studio project, Jamulus
reference-audio route, or frame-accurate review. They also hold the line that
adding it changed nothing for Music, Podcast & Voice, or Review & Rehearsal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from core.creative_modes import (
    CREATOR_PROFILES,
    CreatorCapabilities,
    canonical_creator_profile_key,
    get_creator_profile_by_key,
    get_creator_profile_by_key_or_default,
    get_creator_profile_by_label,
    get_creator_profile_keys,
)
from core.session_conductor import (
    EvidenceState,
    MusicPathState,
    ProcessState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
    derive_session_conductor,
)
from core.session_intelligence import build_session_pulse
from core.settings import AppSettings, load_settings, save_settings

STUDIO_VISIT = "studio_visit"
OTHER_PROFILES = ("music", "podcast_voice", "review_rehearsal")


@pytest.fixture()
def profile():
    return get_creator_profile_by_key(STUDIO_VISIT)


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_studio_visit_is_a_registered_preview_profile(profile):
    assert profile is not None
    assert profile.key == STUDIO_VISIT
    assert profile.label == "Studio Visit"
    assert profile.is_preview is True
    assert get_creator_profile_by_label("Studio Visit") is profile
    assert STUDIO_VISIT in get_creator_profile_keys()


def test_studio_visit_claims_a_room_and_a_reference_video_and_nothing_else(profile):
    capabilities = profile.capabilities
    assert capabilities.live_session is True
    assert capabilities.meeting_handoff is True
    assert capabilities.shared_reference_video is True

    # Everything below is a claim Studio Visit must not make.
    assert capabilities.media_timecode is False
    assert capabilities.shared_reference_audio is False
    assert capabilities.session_recording is False
    assert capabilities.take_review is False
    assert capabilities.take_editing is False
    assert capabilities.track_export is False
    assert capabilities.local_multitrack is False
    assert profile.studio_presets == ()
    assert profile.default_studio_preset is None


def test_only_studio_visit_ships_the_reference_video_contract():
    for key in OTHER_PROFILES:
        other = get_creator_profile_by_key(key)
        assert other.capabilities.shared_reference_video is False


def test_studio_visit_speaks_to_artists_not_to_a_band(profile):
    vocabulary = profile.vocabulary
    assert vocabulary.participant_singular == "artist"
    assert vocabulary.participant_plural == "artists"
    assert vocabulary.session_noun == "studio visit"
    assert vocabulary.reference_video_noun == "reference video"

    spoken = " ".join(
        (
            vocabulary.participant_singular,
            vocabulary.participant_plural,
            vocabulary.session_noun,
            vocabulary.section_noun,
            profile.default_template,
            profile.default_goal,
            " ".join(profile.review_prompts),
        )
    ).casefold()
    for banned in ("musician", "track", "song", "band"):
        assert banned not in spoken, banned


def test_studio_visit_help_states_its_non_goals_plainly(profile):
    help_text = profile.quick_help.casefold()
    assert "no shared canvas" in help_text
    assert "no camera feed" in help_text
    assert "no recorded take" in help_text
    assert "frame-accurate" in help_text
    assert "ships and downloads no video" in help_text
    assert "right to play" in help_text
    assert "never directly or automatically taps a meeting app" in help_text


def test_a_reference_video_capability_requires_a_live_session():
    with pytest.raises(ValueError, match="requires live_session"):
        CreatorCapabilities(
            live_session=False,
            local_multitrack=False,
            shared_reference_audio=False,
            meeting_handoff=False,
            media_timecode=False,
            session_recording=False,
            take_review=False,
            take_editing=False,
            track_export=False,
            shared_reference_video=True,
        )


def test_the_reference_video_noun_defaults_without_breaking_older_profiles():
    for key in OTHER_PROFILES:
        assert (
            get_creator_profile_by_key(key).vocabulary.reference_video_noun
            == "reference video"
        )


# ---------------------------------------------------------------------------
# Persistence and migration
# ---------------------------------------------------------------------------


def test_the_profile_key_round_trips_through_settings(tmp_path: Path):
    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = STUDIO_VISIT
    save_settings(settings)

    assert load_settings(config).last_creator_profile_key == STUDIO_VISIT


@pytest.mark.parametrize(
    "stored",
    ["", "studio", "studiovisit", "Studio Visit", "art", "visual_studio_visit", None],
)
def test_an_unknown_profile_key_still_fails_safely_to_music(
    tmp_path: Path, stored: object
):
    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = stored
    save_settings(settings)

    assert load_settings(config).last_creator_profile_key == "music"


def test_the_legacy_visual_arts_mode_now_lands_on_studio_visit():
    """``visual_studio`` was always describing this room.

    It only pointed at Review & Rehearsal because no artist profile existed.
    Someone whose last saved workflow was Visual Studio is a visual artist, so
    they open into a room for making things rather than a review Preview.
    """

    assert canonical_creator_profile_key("visual_studio") == STUDIO_VISIT
    assert canonical_creator_profile_key(STUDIO_VISIT) == STUDIO_VISIT
    assert get_creator_profile_by_key_or_default("nonsense").key == "music"


def test_only_the_visual_arts_mode_moves_and_the_talking_rooms_stay():
    """Writing, critique, and storyboarding are not work at a table."""

    from core.creative_modes import LEGACY_MODE_KEY_ALIASES

    assert dict(LEGACY_MODE_KEY_ALIASES) == {
        "music_jam": "music",
        "visual_studio": STUDIO_VISIT,
        "writers_room": "review_rehearsal",
        "design_critique": "review_rehearsal",
        "storyboard_film_room": "review_rehearsal",
    }
    # A canonical key must never also be an alias, or the registry refuses to
    # load; this is what made ``studio_visit`` the required key rather than
    # reusing ``visual_studio``.
    assert not set(LEGACY_MODE_KEY_ALIASES) & set(get_creator_profile_keys())


def test_the_legacy_mode_key_itself_still_resolves_for_old_session_metadata():
    """Migrating the profile must not invalidate saved session metadata.

    ``visual_studio`` remains a legacy *mode* key in its own registry. Session
    metadata stores that mode alongside the profile, so it has to keep
    resolving even though the profile it migrates to has changed.
    """

    from core.creative_modes import get_mode_by_key

    mode = get_mode_by_key("visual_studio")
    assert mode is not None
    assert mode.creator_profile_key == STUDIO_VISIT


def test_a_take_carrying_the_legacy_visual_key_still_reads_without_error():
    """Historical take evidence must not start failing reconciliation.

    The manifest's profile key drives Studio labels and reconciliation, not
    permission to open a take, so moving the alias changes presentation only.
    """

    from core.take_library import _manifest_creator_profile_key

    key, error = _manifest_creator_profile_key(
        {"session": {"creator_profile_key": "visual_studio"}}
    )

    assert error == ""
    assert key == STUDIO_VISIT


def test_every_profile_keeps_a_private_scratchpad_of_its_own():
    from webjam_qt.controllers.session_persistence import _PROFILE_NOTES_FILES

    paths = [_PROFILE_NOTES_FILES[profile.key] for profile in CREATOR_PROFILES]
    assert len(set(paths)) == len(paths)
    assert _PROFILE_NOTES_FILES[STUDIO_VISIT] == ".webjam_notes.studio_visit.md"


def test_a_studio_visit_scratchpad_never_writes_another_profiles_notes():
    from webjam_qt.controllers.session_persistence import SessionPersistence

    persistence = SessionPersistence(object(), object())
    seen = {}
    for profile in CREATOR_PROFILES:
        persistence.set_profile_key(profile.key)
        seen[profile.key] = persistence._notes_path()

    assert len(set(seen.values())) == len(seen)
    assert seen[STUDIO_VISIT].name == ".webjam_notes.studio_visit.md"

    # An unknown key still lands on Music rather than inventing a file.
    persistence.set_profile_key("not-a-profile")
    assert persistence._notes_path() == seen["music"]


def test_the_session_pulse_uses_studio_vocabulary():
    pulse = build_session_pulse(
        creator_profile_key=STUDIO_VISIT,
        title="Kitchen table still life",
        notes="",
    )
    assert pulse.mode_key == STUDIO_VISIT
    assert pulse.mode_label == "Studio Visit"
    spoken = f"{pulse.checkpoint} {pulse.next_step} {pulse.stage}".casefold()
    for banned in ("musician", "track", "song", "band"):
        assert banned not in spoken, banned


# ---------------------------------------------------------------------------
# Conductor presentation
# ---------------------------------------------------------------------------


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


def test_a_live_studio_visit_addresses_artists_and_offers_no_recording():
    live = derive_session_conductor(_live_facts(STUDIO_VISIT))
    copy = _visible_copy(live)

    assert live.title == "Artists connected · Preview"
    assert live.creator_profile_key == STUDIO_VISIT
    # Recording is not part of this profile, so the host is offered nothing.
    assert live.primary_action is SessionPrimaryAction.NONE
    assert "musician" not in copy
    assert "band" not in copy
    assert re.search(r"\bjam\b", copy) is None
    assert re.search(r"\btrack\b", copy) is None


def test_a_live_studio_visit_states_its_own_limits_not_reviews():
    copy = _visible_copy(derive_session_conductor(_live_facts(STUDIO_VISIT)))

    assert "never directly or automatically taps a meeting app" in copy
    assert "notes stay local and are not shared" in copy
    assert "not frame-accurate or timecoded" in copy
    assert "this session is not recorded" in copy
    # Studio Visit does synchronize one host-clocked video, so it must never
    # borrow Review's blanket claim that visual media is not synchronized.
    assert "visual media and timecode are not synchronized" not in copy
    assert "arrangement editing and track export are unavailable" not in copy


def test_review_and_rehearsal_keeps_its_own_unchanged_limits():
    copy = _visible_copy(derive_session_conductor(_live_facts("review_rehearsal")))
    assert "visual media and timecode are not synchronized" in copy
    assert "arrangement editing and track export are unavailable" in copy
    assert "record session captures jamulus server stems" in copy


def test_setup_phases_use_the_artists_own_words():
    waiting = derive_session_conductor(
        replace(_ready_facts(STUDIO_VISIT), band_check=EvidenceState.IN_PROGRESS)
    )
    assert waiting.phase is SessionConductorPhase.BAND_CHECK_IN_PROGRESS
    assert "artists" in _visible_copy(waiting)

    idle = derive_session_conductor(
        SessionConductorFacts(creator_profile_key=STUDIO_VISIT)
    )
    assert "studio visit" in idle.message.casefold()
    assert "artist" in idle.evidence_limit.casefold()


def test_the_studio_visit_action_labels_are_not_music_labels():
    assert (
        SessionPrimaryAction.RUN_BAND_CHECK.label_for(STUDIO_VISIT)
        == "Run Session Check"
    )
    assert SessionPrimaryAction.ENTER_JAM.label_for(STUDIO_VISIT) == "Enter Studio"
    assert SessionPrimaryAction.RUN_BAND_CHECK.label_for("music") == "Run Band Check"


def test_other_profiles_keep_their_live_titles():
    assert derive_session_conductor(_live_facts("music")).title == "Band connected"
    assert (
        derive_session_conductor(_live_facts("podcast_voice")).title
        == "Speakers connected"
    )
    assert (
        derive_session_conductor(_live_facts("review_rehearsal")).title
        == "Participants connected · Preview"
    )


def test_a_recording_capable_profile_still_offers_record():
    for key in OTHER_PROFILES:
        live = derive_session_conductor(_live_facts(key))
        assert live.primary_action is SessionPrimaryAction.RECORD, key

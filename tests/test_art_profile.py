"""The Art creator profile as a product contract.

Art is a room for artists making things at a table. These tests hold it to
exactly what it claims: a live room, three ways to begin, an optional shared
Drawpile canvas, an optional host-clocked reference video, artist vocabulary,
and no recorded take, Studio project, Jamulus reference-audio route, or
frame-accurate review. They also hold the line that adding it changed nothing
for Music, Podcast & Voice, or Review & Rehearsal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from core.creative_modes import (
    CREATOR_PROFILES,
    CreatorCapabilities,
    CreatorStart,
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

ART = "art"
OTHER_PROFILES = ("music", "podcast_voice", "review_rehearsal")


@pytest.fixture()
def profile():
    return get_creator_profile_by_key(ART)


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_art_is_a_registered_preview_profile(profile):
    assert profile is not None
    assert profile.key == ART
    assert profile.label == "Art"
    assert profile.is_preview is True
    assert get_creator_profile_by_label("Art") is profile
    assert ART in get_creator_profile_keys()


def test_art_claims_a_room_a_canvas_and_a_video_and_nothing_else(profile):
    capabilities = profile.capabilities
    assert capabilities.live_session is True
    assert capabilities.meeting_handoff is True
    assert capabilities.shared_reference_video is True
    assert capabilities.shared_canvas is True

    # Everything below is a claim Art must not make.
    assert capabilities.media_timecode is False
    assert capabilities.shared_reference_audio is False
    assert capabilities.session_recording is False
    assert capabilities.take_review is False
    assert capabilities.take_editing is False
    assert capabilities.track_export is False
    assert capabilities.local_multitrack is False
    assert profile.studio_presets == ()
    assert profile.default_studio_preset is None


def test_only_art_ships_the_canvas_and_reference_video_contracts():
    for key in OTHER_PROFILES:
        other = get_creator_profile_by_key(key)
        assert other.capabilities.shared_reference_video is False
        assert other.capabilities.shared_canvas is False


def test_art_speaks_to_artists_not_to_a_band(profile):
    vocabulary = profile.vocabulary
    assert vocabulary.participant_singular == "artist"
    assert vocabulary.participant_plural == "artists"
    assert vocabulary.session_noun == "art session"
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
    for banned in ("musician", "track", "song", "band", "studio visit"):
        assert banned not in spoken, banned


def test_nothing_a_person_reads_still_says_studio_visit(profile):
    """The room is named Art now, everywhere a person can see it."""

    from webjam_qt.windows.launch_dialog import _CREATOR_LAUNCH_COPY

    copy = _CREATOR_LAUNCH_COPY[ART]
    visible = " ".join(
        (
            profile.label,
            profile.default_template,
            profile.default_goal,
            profile.quick_help,
            " ".join(profile.review_prompts),
            " ".join(start.label for start in profile.starts),
            " ".join(start.summary for start in profile.starts),
            " ".join(start.detail for start in profile.starts),
            copy.host,
            copy.join,
            copy.local,
            copy.host_description,
            copy.join_description,
            copy.local_description,
            copy.helper,
            copy.join_title,
            copy.join_subtitle,
        )
    ).casefold()
    assert "studio visit" not in visible


def test_art_help_states_its_non_goals_plainly(profile):
    help_text = profile.quick_help.casefold()
    assert "no camera feed" in help_text
    assert "no recorded take" in help_text
    assert "frame-accurate" in help_text
    assert "ships and downloads no video" in help_text
    assert "right to play" in help_text
    assert "never directly or automatically taps a meeting app" in help_text
    # The canvas belongs to Drawpile, and the help must not imply otherwise.
    assert "drawpile" in help_text
    assert "draws no strokes" in help_text


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


def test_a_shared_canvas_capability_requires_a_live_session():
    with pytest.raises(ValueError, match="shared_canvas requires live_session"):
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
            shared_canvas=True,
        )


def test_the_reference_video_noun_defaults_without_breaking_older_profiles():
    for key in OTHER_PROFILES:
        assert (
            get_creator_profile_by_key(key).vocabulary.reference_video_noun
            == "reference video"
        )


# ---------------------------------------------------------------------------
# The three starts
# ---------------------------------------------------------------------------


def test_art_offers_exactly_three_starts_in_a_fixed_order(profile):
    assert [start.key for start in profile.starts] == [
        "talk_and_make",
        "paint_together",
        "paint_along",
    ]
    assert [start.label for start in profile.starts] == [
        "Talk & make",
        "Paint together",
        "Paint along",
    ]


def test_each_start_carries_at_most_one_add_on(profile):
    talk, canvas, video = profile.starts

    assert talk.talk_only is True
    assert (talk.shared_canvas, talk.reference_video) == (False, False)
    assert (canvas.shared_canvas, canvas.reference_video) == (True, False)
    assert (video.shared_canvas, video.reference_video) == (False, True)


def test_no_start_combines_a_canvas_and_a_video():
    """Combining is an in-room decision, never a fourth card."""

    with pytest.raises(ValueError, match="at most one optional add-on"):
        CreatorStart(
            key="everything",
            label="Everything",
            summary="Canvas and video at once.",
            detail="A fourth card would undo the point of a short list.",
            shared_canvas=True,
            reference_video=True,
        )


def test_no_other_profile_offers_start_cards():
    for key in OTHER_PROFILES:
        assert get_creator_profile_by_key(key).starts == ()
        assert get_creator_profile_by_key(key).default_start is None


def test_the_talk_only_start_is_the_default_and_the_fallback(profile):
    assert profile.default_start.key == "talk_and_make"
    assert profile.start_or_default("nonsense").key == "talk_and_make"
    assert profile.start_or_default(None).key == "talk_and_make"
    assert profile.start_or_default("paint_along").key == "paint_along"
    assert profile.get_start("nonsense") is None


def test_a_start_cannot_promise_a_capability_the_profile_lacks():
    from core.creative_modes import CreatorProfile, CreatorVocabulary

    def build(start: CreatorStart) -> CreatorProfile:
        return CreatorProfile(
            key="example",
            label="Example",
            release_tier="preview",
            default_template="Example",
            default_goal="Do one thing.",
            quick_help="Nothing is claimed here.",
            review_prompts=("What happened?",),
            capabilities=CreatorCapabilities(
                live_session=True,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
                session_recording=False,
                take_review=False,
                take_editing=False,
                track_export=False,
            ),
            vocabulary=CreatorVocabulary(
                participant_singular="person",
                participant_plural="people",
                session_noun="session",
                reference_audio_noun="reference audio",
                section_noun="part",
            ),
            starts=(start,),
        )

    with pytest.raises(ValueError, match="shared canvas this profile does not have"):
        build(
            CreatorStart(
                key="canvas",
                label="Canvas",
                summary="A canvas that does not exist.",
                detail="The capability gate would refuse this at runtime.",
                shared_canvas=True,
            )
        )
    with pytest.raises(ValueError, match="reference video this profile does not have"):
        build(
            CreatorStart(
                key="video",
                label="Video",
                summary="A video that does not exist.",
                detail="The capability gate would refuse this at runtime.",
                reference_video=True,
            )
        )


def test_a_profile_offering_starts_must_keep_a_talk_only_door():
    from core.creative_modes import CreatorProfile, CreatorVocabulary

    with pytest.raises(ValueError, match="talk-only"):
        CreatorProfile(
            key="example",
            label="Example",
            release_tier="preview",
            default_template="Example",
            default_goal="Do one thing.",
            quick_help="Nothing is claimed here.",
            review_prompts=("What happened?",),
            capabilities=CreatorCapabilities(
                live_session=True,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
                session_recording=False,
                take_review=False,
                take_editing=False,
                track_export=False,
                shared_canvas=True,
            ),
            vocabulary=CreatorVocabulary(
                participant_singular="person",
                participant_plural="people",
                session_noun="session",
                reference_audio_noun="reference audio",
                section_noun="part",
            ),
            starts=(
                CreatorStart(
                    key="canvas_only",
                    label="Canvas only",
                    summary="The only way in is a canvas.",
                    detail="Omitting the plain door makes an add-on look required.",
                    shared_canvas=True,
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Persistence and migration
# ---------------------------------------------------------------------------


def test_the_profile_key_round_trips_through_settings(tmp_path: Path):
    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = ART
    save_settings(settings)

    assert load_settings(config).last_creator_profile_key == ART


@pytest.mark.parametrize(
    "stored",
    ["", "studio", "studiovisit", "Studio Visit", "arts", "visual_studio_visit", None],
)
def test_an_unknown_profile_key_still_fails_safely_to_music(
    tmp_path: Path, stored: object
):
    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = stored
    save_settings(settings)

    assert load_settings(config).last_creator_profile_key == "music"


def test_the_studio_visit_preview_key_migrates_to_art(tmp_path: Path):
    """A Preview artist who already chose the room keeps it after the rename."""

    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = "studio_visit"
    save_settings(settings)

    assert load_settings(config).last_creator_profile_key == ART
    assert canonical_creator_profile_key("studio_visit") == ART


def test_the_start_choice_round_trips_and_falls_back_safely(tmp_path: Path):
    config = str(tmp_path / "settings.json")
    settings = AppSettings(config_file=config)
    settings.last_creator_profile_key = ART
    settings.last_creator_start_key = "paint_together"
    save_settings(settings)
    assert load_settings(config).last_creator_start_key == "paint_together"

    # A stale key decides whether a canvas or a video is armed, so it must
    # never survive into a profile that no longer offers it.
    settings.last_creator_start_key = "nonsense"
    save_settings(settings)
    assert load_settings(config).last_creator_start_key == "talk_and_make"

    settings.last_creator_profile_key = "music"
    settings.last_creator_start_key = "paint_together"
    save_settings(settings)
    assert load_settings(config).last_creator_start_key == ""


def test_art_does_not_shadow_the_visual_studio_alias():
    # ``visual_studio`` is a legacy visual mode and must keep migrating to
    # Review & Rehearsal rather than silently becoming Art.
    assert canonical_creator_profile_key("visual_studio") == "review_rehearsal"
    assert canonical_creator_profile_key(ART) == ART
    assert get_creator_profile_by_key_or_default("nonsense").key == "music"


def test_art_is_a_canonical_key_and_never_a_migration_target():
    from core.creative_modes import LEGACY_MODE_KEY_ALIASES

    assert ART not in LEGACY_MODE_KEY_ALIASES
    assert LEGACY_MODE_KEY_ALIASES["studio_visit"] == ART


def test_repointing_the_visual_studio_alias_would_strand_recorded_takes():
    """Why the legacy visual mode still migrates to Review, not here.

    ``visual_studio`` was the visual-arts mode, so pointing it at Art looks
    tempting. It would be a silent capability regression: a session already
    recorded under that mode resolves today to a profile that can play it
    back, and Art records nothing and reviews nothing. Existing take evidence
    would become unreviewable on upgrade.
    """

    migrated = get_creator_profile_by_key(
        canonical_creator_profile_key("visual_studio")
    )
    assert migrated.key == "review_rehearsal"
    assert migrated.capabilities.session_recording is True
    assert migrated.capabilities.take_review is True

    art = get_creator_profile_by_key(ART)
    assert art.capabilities.session_recording is False
    assert art.capabilities.take_review is False


def test_a_take_recorded_under_the_legacy_visual_mode_stays_reviewable():
    from core.take_library import _manifest_creator_profile_key

    key, error = _manifest_creator_profile_key(
        {"session": {"creator_profile_key": "visual_studio"}}
    )

    assert error == ""
    assert get_creator_profile_by_key(key).capabilities.take_review is True


def test_only_the_studio_visit_preview_key_moves_and_every_other_alias_stays():
    from core.creative_modes import LEGACY_MODE_KEY_ALIASES

    assert dict(LEGACY_MODE_KEY_ALIASES) == {
        "music_jam": "music",
        "visual_studio": "review_rehearsal",
        "writers_room": "review_rehearsal",
        "design_critique": "review_rehearsal",
        "storyboard_film_room": "review_rehearsal",
        "studio_visit": ART,
    }
    # A canonical key must never also be an alias, or the registry refuses to
    # load. That is why the renamed profile is keyed ``art`` rather than
    # keeping ``studio_visit``, which the alias still needs.
    assert not set(LEGACY_MODE_KEY_ALIASES) & set(get_creator_profile_keys())


def test_the_legacy_mode_key_itself_still_resolves_for_old_session_metadata():
    """Renaming the profile must not invalidate saved session metadata.

    ``visual_studio`` remains a legacy *mode* key in its own registry. Session
    metadata stores that mode alongside the profile, so it has to keep
    resolving, and it has to keep resolving to a profile that can review the
    takes recorded under it.
    """

    from core.creative_modes import get_mode_by_key

    mode = get_mode_by_key("visual_studio")
    assert mode is not None
    assert mode.creator_profile_key == "review_rehearsal"


def test_every_profile_keeps_a_private_scratchpad_of_its_own():
    from webjam_qt.controllers.session_persistence import _PROFILE_NOTES_FILES

    paths = [_PROFILE_NOTES_FILES[profile.key] for profile in CREATOR_PROFILES]
    assert len(set(paths)) == len(paths)
    assert _PROFILE_NOTES_FILES[ART] == ".webjam_notes.art.md"


def test_an_art_scratchpad_never_writes_another_profiles_notes():
    from webjam_qt.controllers.session_persistence import SessionPersistence

    persistence = SessionPersistence(object(), object())
    seen = {}
    for profile in CREATOR_PROFILES:
        persistence.set_profile_key(profile.key)
        seen[profile.key] = persistence._notes_path()

    assert len(set(seen.values())) == len(seen)
    assert seen[ART].name == ".webjam_notes.art.md"

    # An unknown key still lands on Music rather than inventing a file.
    persistence.set_profile_key("not-a-profile")
    assert persistence._notes_path() == seen["music"]


def test_the_session_pulse_uses_art_vocabulary():
    pulse = build_session_pulse(
        creator_profile_key=ART,
        title="Kitchen table still life",
        notes="",
    )
    assert pulse.mode_key == ART
    assert pulse.mode_label == "Art"
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


def test_a_live_art_session_addresses_artists_and_offers_no_recording():
    live = derive_session_conductor(_live_facts(ART))
    copy = _visible_copy(live)

    assert live.title == "Artists connected · Preview"
    assert live.creator_profile_key == ART
    # Recording is not part of this profile, so the host is offered nothing.
    assert live.primary_action is SessionPrimaryAction.NONE
    assert "musician" not in copy
    assert "band" not in copy
    assert "studio visit" not in copy
    assert re.search(r"\bjam\b", copy) is None
    assert re.search(r"\btrack\b", copy) is None


def test_a_live_art_session_states_its_own_limits_not_reviews():
    copy = _visible_copy(derive_session_conductor(_live_facts(ART)))

    assert "never directly or automatically taps a meeting app" in copy
    assert "notes stay local and are not shared" in copy
    assert "not frame-accurate or timecoded" in copy
    assert "this session is not recorded" in copy
    # Art paints in Drawpile, and must say where the canvas actually lives.
    assert "painted in drawpile, not in webjam" in copy
    # Art does synchronize one host-clocked video and does point the room at
    # one canvas, so it must never borrow Review's blanket claim that visual
    # media is not synchronized.
    assert "visual media and timecode are not synchronized" not in copy
    assert "arrangement editing and track export are unavailable" not in copy


def test_review_and_rehearsal_keeps_its_own_unchanged_limits():
    copy = _visible_copy(derive_session_conductor(_live_facts("review_rehearsal")))
    assert "visual media and timecode are not synchronized" in copy
    assert "arrangement editing and track export are unavailable" in copy
    assert "record session captures jamulus server stems" in copy


def test_setup_phases_use_the_artists_own_words():
    waiting = derive_session_conductor(
        replace(_ready_facts(ART), band_check=EvidenceState.IN_PROGRESS)
    )
    assert waiting.phase is SessionConductorPhase.BAND_CHECK_IN_PROGRESS
    assert "artists" in _visible_copy(waiting)

    idle = derive_session_conductor(SessionConductorFacts(creator_profile_key=ART))
    assert "art session" in idle.message.casefold()
    assert "artist" in idle.evidence_limit.casefold()


def test_the_art_action_labels_are_not_music_labels():
    assert SessionPrimaryAction.RUN_BAND_CHECK.label_for(ART) == "Run Session Check"
    assert SessionPrimaryAction.ENTER_JAM.label_for(ART) == "Enter Studio"
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

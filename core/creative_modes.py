"""Bounded creator profiles plus the legacy creative-mode compatibility API.

Creator profiles are product contracts, not presentation-only labels.  Each
profile binds truthful capabilities, vocabulary, and fixed Studio defaults.
The older five-mode registry remains available until its hidden UI and session
summary consumers migrate to :data:`CREATOR_PROFILES`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.meeting_link import (
    MEETING_DIRECT_CAPTURE_BOUNDARY,
    RECORD_SESSION_MEETING_CAPTURE_NOTICE,
)

RELEASE_TIER_GA = "ga"
RELEASE_TIER_PREVIEW = "preview"
_RELEASE_TIERS = frozenset({RELEASE_TIER_GA, RELEASE_TIER_PREVIEW})
_PROFILE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RULER_MODES = frozenset({"bars", "time"})


def _text(value: object, field_name: str, *, maximum: int = 240) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text.")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} is required.")
    if len(cleaned) > maximum:
        raise ValueError(f"{field_name} is too long.")
    return cleaned


def _key(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _PROFILE_KEY.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a lowercase identifier using letters, "
            "numbers, and underscores."
        )
    return value


@dataclass(frozen=True)
class CreatorCapabilities:
    """Feature truth that UI and controllers can enforce for one profile."""

    live_session: bool
    local_multitrack: bool
    shared_reference_audio: bool
    meeting_handoff: bool
    media_timecode: bool
    # These default to the established Music/Podcast behavior so older
    # construction sites remain source-compatible.  Profiles with a narrower
    # contract must opt out explicitly instead of overloading
    # ``local_multitrack``, which describes standalone local projects.
    session_recording: bool = True
    take_review: bool = True
    take_editing: bool = True
    track_export: bool = True
    # Host-clocked reference video watched locally on every computer.  It is
    # deliberately separate from ``shared_reference_audio``, which routes
    # decoded audio through Jamulus, and from ``media_timecode``, which this
    # capability never implies.
    shared_reference_video: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "live_session",
            "local_multitrack",
            "shared_reference_audio",
            "meeting_handoff",
            "media_timecode",
            "session_recording",
            "take_review",
            "take_editing",
            "track_export",
            "shared_reference_video",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean.")
        if self.take_editing and not self.take_review:
            raise ValueError("take_editing requires take_review.")
        if self.track_export and not self.take_review:
            raise ValueError("track_export requires take_review.")
        if self.shared_reference_video and not self.live_session:
            raise ValueError("shared_reference_video requires live_session.")


@dataclass(frozen=True)
class CreatorVocabulary:
    """Small presentation vocabulary without changing internal protocols."""

    participant_singular: str
    participant_plural: str
    session_noun: str
    reference_audio_noun: str
    section_noun: str
    # Only surfaced by profiles that enable ``shared_reference_video``; the
    # default keeps every existing construction site source-compatible.
    reference_video_noun: str = "reference video"

    def __post_init__(self) -> None:
        for field_name in (
            "participant_singular",
            "participant_plural",
            "session_noun",
            "reference_audio_noun",
            "section_noun",
            "reference_video_noun",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, maximum=64),
            )


@dataclass(frozen=True)
class StudioPreset:
    """One fixed, honest local-Studio starting point."""

    key: str
    label: str
    track_names: tuple[str, ...]
    sample_rate_hz: int = 48_000
    count_in_enabled: bool = False
    metronome_enabled: bool = False
    ruler_mode: str = "time"

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _key(self.key, "preset key"))
        object.__setattr__(self, "label", _text(self.label, "preset label", maximum=80))
        if isinstance(self.track_names, (str, bytes)):
            raise TypeError("track_names must be a sequence of track names.")
        try:
            names = tuple(
                _text(item, "track name", maximum=120) for item in self.track_names
            )
        except TypeError as exc:
            raise TypeError("track_names must be a sequence of track names.") from exc
        if not names:
            raise ValueError("A Studio preset requires at least one track.")
        if len(names) > 64:
            raise ValueError("A Studio preset cannot exceed 64 tracks.")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Studio preset track names must be unique.")
        object.__setattr__(self, "track_names", names)
        if (
            type(self.sample_rate_hz) is not int
            or not 8_000 <= self.sample_rate_hz <= 384_000
        ):
            raise ValueError("sample_rate_hz is outside the supported range.")
        for field_name in ("count_in_enabled", "metronome_enabled"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean.")
        if self.ruler_mode not in _RULER_MODES:
            raise ValueError("ruler_mode must be 'bars' or 'time'.")


@dataclass(frozen=True)
class CreatorProfile:
    """An immutable creator workflow and its bounded feature contract."""

    key: str
    label: str
    release_tier: str
    default_template: str
    default_goal: str
    quick_help: str
    review_prompts: tuple[str, ...]
    capabilities: CreatorCapabilities
    vocabulary: CreatorVocabulary
    studio_presets: tuple[StudioPreset, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _key(self.key, "profile key"))
        object.__setattr__(
            self, "label", _text(self.label, "profile label", maximum=80)
        )
        if self.release_tier not in _RELEASE_TIERS:
            raise ValueError("release_tier must be 'ga' or 'preview'.")
        for field_name in ("default_template", "default_goal", "quick_help"):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, maximum=600),
            )
        if isinstance(self.review_prompts, (str, bytes)):
            raise TypeError("review_prompts must be a sequence of prompts.")
        try:
            prompts = tuple(
                _text(item, "review prompt", maximum=240)
                for item in self.review_prompts
            )
        except TypeError as exc:
            raise TypeError("review_prompts must be a sequence of prompts.") from exc
        if not prompts:
            raise ValueError("A creator profile requires review prompts.")
        object.__setattr__(self, "review_prompts", prompts)
        if not isinstance(self.capabilities, CreatorCapabilities):
            raise TypeError("capabilities must be CreatorCapabilities.")
        if not isinstance(self.vocabulary, CreatorVocabulary):
            raise TypeError("vocabulary must be CreatorVocabulary.")
        if isinstance(self.studio_presets, (str, bytes)):
            raise TypeError("studio_presets must be a sequence of StudioPreset values.")
        try:
            presets = tuple(self.studio_presets)
        except TypeError as exc:
            raise TypeError(
                "studio_presets must be a sequence of StudioPreset values."
            ) from exc
        if any(not isinstance(item, StudioPreset) for item in presets):
            raise TypeError("studio_presets may contain only StudioPreset values.")
        if len({item.key for item in presets}) != len(presets):
            raise ValueError("Studio preset keys must be unique within a profile.")
        if self.capabilities.local_multitrack != bool(presets):
            raise ValueError(
                "local_multitrack capability and Studio presets must agree."
            )
        object.__setattr__(self, "studio_presets", presets)

    @property
    def is_preview(self) -> bool:
        return self.release_tier == RELEASE_TIER_PREVIEW

    @property
    def default_studio_preset(self) -> StudioPreset | None:
        return self.studio_presets[0] if self.studio_presets else None


_MUSIC_CAPABILITIES = CreatorCapabilities(
    live_session=True,
    local_multitrack=True,
    shared_reference_audio=True,
    meeting_handoff=True,
    media_timecode=False,
    session_recording=True,
    take_review=True,
    take_editing=True,
    track_export=True,
)
_PODCAST_CAPABILITIES = CreatorCapabilities(
    live_session=True,
    local_multitrack=True,
    shared_reference_audio=True,
    meeting_handoff=True,
    media_timecode=False,
    session_recording=True,
    take_review=True,
    take_editing=True,
    track_export=True,
)
_REVIEW_CAPABILITIES = CreatorCapabilities(
    live_session=True,
    local_multitrack=False,
    shared_reference_audio=True,
    meeting_handoff=True,
    media_timecode=False,
    session_recording=True,
    take_review=True,
    take_editing=False,
    track_export=False,
)
# Studio Visit is a room for making things at a table.  It carries live
# conversation and an optional host-clocked reference video, and it claims
# nothing else: no Jamulus reference-audio route, no recorded take, and
# therefore no review, editing, or export contract to honor afterwards.
_STUDIO_VISIT_CAPABILITIES = CreatorCapabilities(
    live_session=True,
    local_multitrack=False,
    shared_reference_audio=False,
    meeting_handoff=True,
    media_timecode=False,
    session_recording=False,
    take_review=False,
    take_editing=False,
    track_export=False,
    shared_reference_video=True,
)


CREATOR_PROFILES: tuple[CreatorProfile, ...] = (
    CreatorProfile(
        key="music",
        label="Music",
        release_tier=RELEASE_TIER_GA,
        default_template="Band Rehearsal",
        default_goal="Lock timing and balance for one complete song run.",
        quick_help=(
            "Use WebJam's live audio path for the music; an external meeting "
            "link remains optional for conversation or video."
        ),
        review_prompts=(
            "What section needs a tighter groove?",
            "Which track needs a level or pan adjustment?",
            "What should we rehearse next session?",
        ),
        capabilities=_MUSIC_CAPABILITIES,
        vocabulary=CreatorVocabulary(
            participant_singular="musician",
            participant_plural="musicians",
            session_noun="music session",
            reference_audio_noun="backing track",
            section_noun="song section",
        ),
        studio_presets=(
            StudioPreset(
                key="music_project",
                label="Music Project",
                track_names=("Audio 1",),
                sample_rate_hz=48_000,
                count_in_enabled=True,
                metronome_enabled=False,
                ruler_mode="bars",
            ),
        ),
    ),
    CreatorProfile(
        key="podcast_voice",
        label="Podcast & Voice",
        release_tier=RELEASE_TIER_GA,
        default_template="Host + Guest Recording",
        default_goal="Capture clear isolated voices and finish a reviewable edit.",
        quick_help=(
            "Remote speakers use WebJam's audio path for isolated tracks; a "
            "meeting platform is an optional external handoff. "
            f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
        ),
        review_prompts=(
            "Which edit most improves clarity or pacing?",
            "Do the voices remain distinct and consistently leveled?",
            "What chapter or pickup should be recorded next?",
        ),
        capabilities=_PODCAST_CAPABILITIES,
        vocabulary=CreatorVocabulary(
            participant_singular="speaker",
            participant_plural="speakers",
            session_noun="recording session",
            reference_audio_noun="reference audio",
            section_noun="chapter",
        ),
        studio_presets=(
            StudioPreset(
                key="host_guest",
                label="Host + Guest",
                track_names=("Host Mic", "Guest Mic"),
                sample_rate_hz=48_000,
                count_in_enabled=False,
                metronome_enabled=False,
                ruler_mode="time",
            ),
            StudioPreset(
                key="solo_voice",
                label="Solo Voice",
                track_names=("Voice 1",),
                sample_rate_hz=48_000,
                count_in_enabled=False,
                metronome_enabled=False,
                ruler_mode="time",
            ),
        ),
    ),
    CreatorProfile(
        key="review_rehearsal",
        label="Review & Rehearsal",
        release_tier=RELEASE_TIER_PREVIEW,
        default_template="Live Review (Preview)",
        default_goal="Capture focused feedback and the next rehearsal decision.",
        quick_help=(
            "Preview supports an external meeting handoff, WebJam-path audio, "
            "reference audio, and local notes. It does not synchronize visual "
            "media, shared notes, or media timecode. "
            f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
        ),
        review_prompts=(
            "What moment needs another pass?",
            "What feedback should guide the next rehearsal?",
            "What decision or owner should be captured before the session ends?",
        ),
        capabilities=_REVIEW_CAPABILITIES,
        vocabulary=CreatorVocabulary(
            participant_singular="participant",
            participant_plural="participants",
            session_noun="review session",
            reference_audio_noun="reference audio",
            section_noun="cue",
        ),
    ),
    CreatorProfile(
        key="studio_visit",
        label="Studio Visit",
        release_tier=RELEASE_TIER_PREVIEW,
        default_template="Studio Visit (Preview)",
        default_goal="Share a table, talk, and move one piece forward.",
        quick_help=(
            "Preview opens a room for artists in any medium: talk while you "
            "paint, draw, sculpt, or build. The host may optionally share one "
            "local video file they already have the right to play, and "
            "everyone watches their own copy of that exact file under the "
            "host's play, pause, stop, and position control. WebJam ships and "
            "downloads no video, and follows nothing it cannot prove is the "
            "same file. There is no shared canvas, no camera feed, no "
            "recorded take, and no frame-accurate or timecoded review. "
            f"{MEETING_DIRECT_CAPTURE_BOUNDARY}"
        ),
        review_prompts=(
            "What did this piece need that you could not see alone?",
            "Which part is worth another pass before the next visit?",
            "What material or step should be ready next time?",
        ),
        capabilities=_STUDIO_VISIT_CAPABILITIES,
        vocabulary=CreatorVocabulary(
            participant_singular="artist",
            participant_plural="artists",
            session_noun="studio visit",
            reference_audio_noun="reference audio",
            section_noun="stage",
            reference_video_noun="reference video",
        ),
    ),
)


# Explicit migration from every previously persisted mode key.  Canonical keys
# are deliberately absent so callers can distinguish migration from identity.
#
# ``visual_studio`` was the visual-arts mode and only pointed at Review &
# Rehearsal because nothing better existed.  Studio Visit is the profile that
# mode was always describing, so someone who last worked in Visual Studio now
# lands in a room built for making things at a table rather than in a review
# Preview.  The other three legacy modes are discussion and planning rooms and
# stay on Review.
LEGACY_MODE_KEY_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "music_jam": "music",
        "visual_studio": "studio_visit",
        "writers_room": "review_rehearsal",
        "design_critique": "review_rehearsal",
        "storyboard_film_room": "review_rehearsal",
    }
)


def _validate_creator_registry() -> None:
    if tuple(item.key for item in CREATOR_PROFILES) != (
        "music",
        "podcast_voice",
        "review_rehearsal",
        "studio_visit",
    ):
        raise RuntimeError("Creator profiles must remain the bounded shipped set.")
    keys = tuple(item.key for item in CREATOR_PROFILES)
    labels = tuple(item.label for item in CREATOR_PROFILES)
    if len(set(keys)) != len(keys) or len(set(labels)) != len(labels):
        raise RuntimeError("Creator profile keys and labels must be unique.")
    if set(LEGACY_MODE_KEY_ALIASES) & set(keys):
        raise RuntimeError("Legacy aliases cannot shadow canonical profile keys.")
    if not set(LEGACY_MODE_KEY_ALIASES.values()) <= set(keys):
        raise RuntimeError("Every legacy alias must target a creator profile.")

    by_key = {item.key: item for item in CREATOR_PROFILES}
    music = by_key["music"]
    podcast = by_key["podcast_voice"]
    review = by_key["review_rehearsal"]
    if music.release_tier != RELEASE_TIER_GA:
        raise RuntimeError("Music must remain generally available.")
    if podcast.release_tier != RELEASE_TIER_GA:
        raise RuntimeError("Podcast & Voice must remain generally available.")
    if (
        not podcast.capabilities.live_session
        or not podcast.capabilities.local_multitrack
    ):
        raise RuntimeError("Podcast & Voice requires live and local multitrack.")
    host_guest = podcast.default_studio_preset
    if (
        host_guest is None
        or host_guest.track_names != ("Host Mic", "Guest Mic")
        or host_guest.sample_rate_hz != 48_000
        or host_guest.count_in_enabled
        or host_guest.metronome_enabled
        or host_guest.ruler_mode != "time"
    ):
        raise RuntimeError("Podcast & Voice must retain its truthful mic preset.")
    if review.release_tier != RELEASE_TIER_PREVIEW or not review.is_preview:
        raise RuntimeError("Review & Rehearsal must remain a Preview.")
    if review.capabilities.media_timecode:
        raise RuntimeError("Review & Rehearsal cannot claim media timecode.")
    if (
        not review.capabilities.session_recording
        or not review.capabilities.take_review
        or review.capabilities.take_editing
        or review.capabilities.track_export
    ):
        raise RuntimeError(
            "Review & Rehearsal must allow session recording and playback review "
            "without take editing or track export."
        )
    if review.capabilities.local_multitrack or review.studio_presets:
        raise RuntimeError("Review & Rehearsal has no local Studio contract yet.")

    studio_visit = by_key["studio_visit"]
    if studio_visit.release_tier != RELEASE_TIER_PREVIEW or not studio_visit.is_preview:
        raise RuntimeError("Studio Visit must remain a Preview.")
    if not studio_visit.capabilities.shared_reference_video:
        raise RuntimeError("Studio Visit requires host-clocked reference video.")
    if studio_visit.capabilities.media_timecode:
        raise RuntimeError(
            "Studio Visit is host transport sync, not frame-accurate review, "
            "so it cannot claim media timecode."
        )
    if studio_visit.capabilities.shared_reference_audio:
        raise RuntimeError(
            "Studio Visit has no Jamulus reference-audio route to offer."
        )
    if (
        studio_visit.capabilities.session_recording
        or studio_visit.capabilities.take_review
        or studio_visit.capabilities.take_editing
        or studio_visit.capabilities.track_export
    ):
        raise RuntimeError(
            "Studio Visit cannot record a take, so it must not offer recording, "
            "review, editing, or export."
        )
    if studio_visit.capabilities.local_multitrack or studio_visit.studio_presets:
        raise RuntimeError("Studio Visit has no local Studio contract.")
    if any(
        term in phrase.casefold()
        for term in ("musician", "track", "song", "band")
        for phrase in (
            studio_visit.vocabulary.participant_singular,
            studio_visit.vocabulary.participant_plural,
            studio_visit.vocabulary.session_noun,
            studio_visit.vocabulary.section_noun,
        )
    ):
        raise RuntimeError("Studio Visit speaks to artists, not to a band.")
    if any(
        profile.capabilities.shared_reference_video
        for profile in CREATOR_PROFILES
        if profile.key != "studio_visit"
    ):
        raise RuntimeError(
            "Only Studio Visit ships the host-clocked reference video contract."
        )
    if LEGACY_MODE_KEY_ALIASES.get("visual_studio") != "studio_visit":
        raise RuntimeError(
            "The legacy visual-arts mode must migrate to Studio Visit, which is "
            "the profile it always described."
        )


_validate_creator_registry()
_CREATOR_PROFILE_BY_KEY = {item.key: item for item in CREATOR_PROFILES}
_CREATOR_PROFILE_BY_LABEL = {item.label: item for item in CREATOR_PROFILES}


def canonical_creator_profile_key(profile_key: object) -> str | None:
    """Return a canonical key for a current or explicit legacy key."""

    if not isinstance(profile_key, str):
        return None
    if profile_key in _CREATOR_PROFILE_BY_KEY:
        return profile_key
    return LEGACY_MODE_KEY_ALIASES.get(profile_key)


def get_creator_profile_by_key(profile_key: object) -> CreatorProfile | None:
    canonical = canonical_creator_profile_key(profile_key)
    return _CREATOR_PROFILE_BY_KEY.get(canonical) if canonical is not None else None


def get_creator_profile_by_key_or_default(profile_key: object) -> CreatorProfile:
    return get_creator_profile_by_key(profile_key) or CREATOR_PROFILES[0]


def get_creator_profile_by_label(label: object) -> CreatorProfile | None:
    return _CREATOR_PROFILE_BY_LABEL.get(label) if isinstance(label, str) else None


def get_creator_profile_by_label_or_default(label: object) -> CreatorProfile:
    return get_creator_profile_by_label(label) or CREATOR_PROFILES[0]


def get_creator_profile_keys() -> list[str]:
    return [profile.key for profile in CREATOR_PROFILES]


def get_creator_profile_labels() -> list[str]:
    return [profile.label for profile in CREATOR_PROFILES]


# ---------------------------------------------------------------------------
# Legacy creative-mode compatibility API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreativeMode:
    """Deprecated presentation metadata retained for current hidden consumers."""

    key: str
    label: str
    default_template: str
    default_goal: str
    quick_help: str
    review_prompts: tuple[str, ...]

    @property
    def creator_profile_key(self) -> str:
        """Return this legacy mode's canonical creator-profile key."""

        migrated = canonical_creator_profile_key(self.key)
        if migrated is None:  # pragma: no cover - fixed registry invariant
            raise RuntimeError("Legacy creative mode has no creator profile.")
        return migrated


CREATIVE_MODES: tuple[CreativeMode, ...] = (
    CreativeMode(
        key="music_jam",
        label="Music Jam",
        default_template="Band Rehearsal",
        default_goal="Lock timing and balance for one full song run.",
        quick_help="Launch Jamulus first, then optional Conversation. Keep latency and VU peaks in check.",
        review_prompts=(
            "What section needs a tighter groove?",
            "Which channel needs level or pan adjustment?",
            "What should we rehearse next session?",
        ),
    ),
    CreativeMode(
        key="visual_studio",
        label="Visual Studio",
        default_template="Critique Circle",
        default_goal="Share references and collect focused critique on one in-progress piece.",
        quick_help="Use local notes and an external meeting; visual media is not time-synchronized.",
        review_prompts=(
            "What visual focal point works best?",
            "What single change improves composition most?",
            "Which reference should guide the next draft?",
        ),
    ),
    CreativeMode(
        key="writers_room",
        label="Writer's Room",
        default_template="Draft Sprint",
        default_goal="Complete one draft checkpoint and agree on next edit pass.",
        quick_help="Capture prompt ideas, draft links, and revision decisions in local notes.",
        review_prompts=(
            "What line or scene lands strongest?",
            "Where does pacing fall off?",
            "What rewrite target is highest priority?",
        ),
    ),
    CreativeMode(
        key="design_critique",
        label="Design Critique",
        default_template="Design Review",
        default_goal="Decide final direction using structured feedback and action owners.",
        quick_help="Use local notes and an external meeting to capture review decisions.",
        review_prompts=(
            "What user problem is solved clearly?",
            "What decision is blocked and by whom?",
            "What must be tested before final sign-off?",
        ),
    ),
    CreativeMode(
        key="storyboard_film_room",
        label="Storyboard/Film Room",
        default_template="Shot Planning",
        default_goal="Finalize scene flow and capture the next shot list.",
        quick_help="Use local notes and an external meeting; media timecode is not synchronized.",
        review_prompts=(
            "Which shot transition feels weakest?",
            "What reference should anchor scene pacing?",
            "What is the next production-ready action?",
        ),
    ),
)

_MODE_BY_KEY = {mode.key: mode for mode in CREATIVE_MODES}


def get_mode_keys() -> list[str]:
    return [mode.key for mode in CREATIVE_MODES]


def get_mode_labels() -> list[str]:
    return [mode.label for mode in CREATIVE_MODES]


def get_mode_by_key(mode_key: str) -> CreativeMode | None:
    return _MODE_BY_KEY.get(mode_key)


def get_mode_by_key_or_default(mode_key: str) -> CreativeMode:
    return _MODE_BY_KEY.get(mode_key, CREATIVE_MODES[0])


def get_mode_by_label(label: str) -> CreativeMode | None:
    for mode in CREATIVE_MODES:
        if mode.label == label:
            return mode
    return None


def get_mode_by_label_or_default(label: str) -> CreativeMode:
    for mode in CREATIVE_MODES:
        if mode.label == label:
            return mode
    return CREATIVE_MODES[0]


__all__ = [
    "CREATIVE_MODES",
    "CREATOR_PROFILES",
    "LEGACY_MODE_KEY_ALIASES",
    "RELEASE_TIER_GA",
    "RELEASE_TIER_PREVIEW",
    "CreativeMode",
    "CreatorCapabilities",
    "CreatorProfile",
    "CreatorVocabulary",
    "StudioPreset",
    "canonical_creator_profile_key",
    "get_creator_profile_by_key",
    "get_creator_profile_by_key_or_default",
    "get_creator_profile_by_label",
    "get_creator_profile_by_label_or_default",
    "get_creator_profile_keys",
    "get_creator_profile_labels",
    "get_mode_by_key",
    "get_mode_by_key_or_default",
    "get_mode_by_label",
    "get_mode_by_label_or_default",
    "get_mode_keys",
    "get_mode_labels",
]

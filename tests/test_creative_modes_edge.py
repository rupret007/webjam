"""Edge-case tests for core.creative_modes."""

import unittest
from dataclasses import FrozenInstanceError

from core.creative_modes import (
    CREATIVE_MODES,
    CREATOR_PROFILES,
    LEGACY_MODE_KEY_ALIASES,
    RELEASE_TIER_GA,
    RELEASE_TIER_PREVIEW,
    CreatorCapabilities,
    CreatorProfile,
    CreatorVocabulary,
    StudioPreset,
    canonical_creator_profile_key,
    get_creator_profile_by_key,
    get_creator_profile_by_key_or_default,
    get_creator_profile_by_label,
    get_creator_profile_by_label_or_default,
    get_creator_profile_keys,
    get_creator_profile_labels,
    get_mode_by_key,
    get_mode_by_key_or_default,
    get_mode_by_label,
    get_mode_by_label_or_default,
    get_mode_keys,
    get_mode_labels,
)


class TestGetModeByKey(unittest.TestCase):
    def test_valid_key(self):
        mode = get_mode_by_key("music_jam")
        self.assertIsNotNone(mode)
        self.assertEqual(mode.key, "music_jam")

    def test_missing_key_returns_none(self):
        self.assertIsNone(get_mode_by_key("nonexistent"))

    def test_empty_key_returns_none(self):
        self.assertIsNone(get_mode_by_key(""))


class TestGetModeByKeyOrDefault(unittest.TestCase):
    def test_valid_key(self):
        mode = get_mode_by_key_or_default("writers_room")
        self.assertEqual(mode.key, "writers_room")

    def test_missing_key_returns_first(self):
        mode = get_mode_by_key_or_default("nonexistent")
        self.assertIs(mode, CREATIVE_MODES[0])

    def test_empty_key_returns_first(self):
        self.assertIs(get_mode_by_key_or_default(""), CREATIVE_MODES[0])


class TestGetModeByLabel(unittest.TestCase):
    def test_valid_label(self):
        mode = get_mode_by_label("Music Jam")
        self.assertIsNotNone(mode)
        self.assertEqual(mode.label, "Music Jam")

    def test_missing_label(self):
        self.assertIsNone(get_mode_by_label("No Such Label"))

    def test_case_sensitive(self):
        self.assertIsNone(get_mode_by_label("music jam"))


class TestGetModeByLabelOrDefault(unittest.TestCase):
    def test_valid_label(self):
        mode = get_mode_by_label_or_default("Writer's Room")
        self.assertEqual(mode.key, "writers_room")

    def test_missing_label_returns_first(self):
        mode = get_mode_by_label_or_default("No Match")
        self.assertIs(mode, CREATIVE_MODES[0])


class TestModeCollections(unittest.TestCase):
    def test_all_keys_unique(self):
        keys = get_mode_keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_labels_unique(self):
        labels = get_mode_labels()
        self.assertEqual(len(labels), len(set(labels)))

    def test_keys_match_modes(self):
        keys = get_mode_keys()
        self.assertEqual(keys, [m.key for m in CREATIVE_MODES])

    def test_labels_match_modes(self):
        labels = get_mode_labels()
        self.assertEqual(labels, [m.label for m in CREATIVE_MODES])


class TestCreativeModeDataclass(unittest.TestCase):
    def test_frozen(self):
        mode = CREATIVE_MODES[0]
        with self.assertRaises(AttributeError):
            mode.key = "changed"

    def test_review_prompts_not_empty(self):
        for mode in CREATIVE_MODES:
            self.assertTrue(
                len(mode.review_prompts) > 0, f"{mode.key} has empty review_prompts"
            )

    def test_legacy_mode_exposes_canonical_profile_key(self):
        self.assertEqual(CREATIVE_MODES[0].creator_profile_key, "music")
        self.assertEqual(
            get_mode_by_key("storyboard_film_room").creator_profile_key,
            "review_rehearsal",
        )


class TestCreatorProfileRegistry(unittest.TestCase):
    def test_registry_is_the_bounded_v025_set(self):
        self.assertEqual(
            get_creator_profile_keys(),
            ["music", "podcast_voice", "review_rehearsal"],
        )
        self.assertEqual(
            get_creator_profile_labels(),
            ["Music", "Podcast & Voice", "Review & Rehearsal"],
        )
        self.assertEqual(
            get_creator_profile_keys(),
            [profile.key for profile in CREATOR_PROFILES],
        )

    def test_keys_and_labels_are_unique(self):
        keys = get_creator_profile_keys()
        labels = get_creator_profile_labels()
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(labels), len(set(labels)))

    def test_general_availability_and_preview_tiers_are_explicit(self):
        music = get_creator_profile_by_key("music")
        podcast = get_creator_profile_by_key("podcast_voice")
        review = get_creator_profile_by_key("review_rehearsal")
        self.assertEqual(music.release_tier, RELEASE_TIER_GA)
        self.assertEqual(podcast.release_tier, RELEASE_TIER_GA)
        self.assertEqual(review.release_tier, RELEASE_TIER_PREVIEW)
        self.assertFalse(music.is_preview)
        self.assertTrue(review.is_preview)

    def test_profile_lookup_and_default(self):
        podcast = get_creator_profile_by_key("podcast_voice")
        self.assertEqual(podcast.label, "Podcast & Voice")
        self.assertIsNone(get_creator_profile_by_key("missing"))
        self.assertIs(
            get_creator_profile_by_key_or_default("missing"),
            CREATOR_PROFILES[0],
        )

    def test_label_lookup_is_strict(self):
        review = get_creator_profile_by_label("Review & Rehearsal")
        self.assertEqual(review.key, "review_rehearsal")
        self.assertIsNone(get_creator_profile_by_label("review & rehearsal"))
        self.assertIs(
            get_creator_profile_by_label_or_default("missing"),
            CREATOR_PROFILES[0],
        )

    def test_profile_and_nested_contracts_are_frozen(self):
        profile = CREATOR_PROFILES[0]
        with self.assertRaises(FrozenInstanceError):
            profile.key = "changed"
        with self.assertRaises(FrozenInstanceError):
            profile.capabilities.live_session = False
        with self.assertRaises(FrozenInstanceError):
            profile.vocabulary.session_noun = "changed"
        with self.assertRaises(FrozenInstanceError):
            profile.studio_presets[0].sample_rate_hz = 44_100


class TestCreatorProfileMigration(unittest.TestCase):
    def test_every_legacy_key_has_an_explicit_canonical_target(self):
        self.assertEqual(
            dict(LEGACY_MODE_KEY_ALIASES),
            {
                "music_jam": "music",
                "visual_studio": "review_rehearsal",
                "writers_room": "review_rehearsal",
                "design_critique": "review_rehearsal",
                "storyboard_film_room": "review_rehearsal",
            },
        )
        for mode in CREATIVE_MODES:
            self.assertEqual(
                canonical_creator_profile_key(mode.key),
                mode.creator_profile_key,
            )

    def test_canonical_keys_migrate_to_themselves(self):
        for profile in CREATOR_PROFILES:
            self.assertEqual(
                canonical_creator_profile_key(profile.key),
                profile.key,
            )

    def test_legacy_lookup_resolves_to_canonical_profile(self):
        self.assertIs(
            get_creator_profile_by_key("music_jam"),
            get_creator_profile_by_key("music"),
        )
        self.assertEqual(
            get_creator_profile_by_key("writers_room").key,
            "review_rehearsal",
        )

    def test_unknown_malformed_and_non_text_keys_do_not_migrate(self):
        for value in ("", "Music", " music ", "missing", None, 1):
            self.assertIsNone(canonical_creator_profile_key(value))

    def test_alias_mapping_is_immutable(self):
        with self.assertRaises(TypeError):
            LEGACY_MODE_KEY_ALIASES["new"] = "music"


class TestCreatorProfileCapabilities(unittest.TestCase):
    def test_podcast_has_truthful_host_guest_multitrack_defaults(self):
        podcast = get_creator_profile_by_key("podcast_voice")
        self.assertTrue(podcast.capabilities.live_session)
        self.assertTrue(podcast.capabilities.local_multitrack)
        preset = podcast.default_studio_preset
        self.assertIsNotNone(preset)
        self.assertEqual(preset.key, "host_guest")
        self.assertEqual(preset.track_names, ("Host Mic", "Guest Mic"))
        self.assertEqual(preset.sample_rate_hz, 48_000)
        self.assertFalse(preset.count_in_enabled)
        self.assertFalse(preset.metronome_enabled)
        self.assertEqual(preset.ruler_mode, "time")
        self.assertIn(
            "never directly or automatically taps a meeting app",
            podcast.quick_help,
        )
        self.assertIn(
            "Do not route meeting or system audio into those inputs",
            podcast.quick_help,
        )

    def test_podcast_also_has_a_bounded_solo_voice_preset(self):
        podcast = get_creator_profile_by_key("podcast_voice")
        self.assertEqual(
            [(preset.key, preset.track_names) for preset in podcast.studio_presets],
            [
                ("host_guest", ("Host Mic", "Guest Mic")),
                ("solo_voice", ("Voice 1",)),
            ],
        )

    def test_review_remains_a_truthful_preview(self):
        review = get_creator_profile_by_key("review_rehearsal")
        self.assertTrue(review.is_preview)
        self.assertTrue(review.capabilities.live_session)
        self.assertTrue(review.capabilities.meeting_handoff)
        self.assertTrue(review.capabilities.shared_reference_audio)
        self.assertFalse(review.capabilities.local_multitrack)
        self.assertFalse(review.capabilities.media_timecode)
        self.assertTrue(review.capabilities.session_recording)
        self.assertTrue(review.capabilities.take_review)
        self.assertFalse(review.capabilities.take_editing)
        self.assertFalse(review.capabilities.track_export)
        self.assertEqual(review.studio_presets, ())
        self.assertIn("does not synchronize visual media", review.quick_help)
        self.assertIn(
            "Record Session captures Jamulus server stems", review.quick_help
        )

    def test_music_and_podcast_keep_full_session_take_capabilities(self):
        for key in ("music", "podcast_voice"):
            capabilities = get_creator_profile_by_key(key).capabilities
            self.assertTrue(capabilities.session_recording)
            self.assertTrue(capabilities.take_review)
            self.assertTrue(capabilities.take_editing)
            self.assertTrue(capabilities.track_export)


class TestCreatorProfileValidation(unittest.TestCase):
    @staticmethod
    def _vocabulary() -> CreatorVocabulary:
        return CreatorVocabulary(
            participant_singular="person",
            participant_plural="people",
            session_noun="session",
            reference_audio_noun="reference audio",
            section_noun="section",
        )

    @staticmethod
    def _capabilities(*, local_multitrack: bool) -> CreatorCapabilities:
        return CreatorCapabilities(
            live_session=True,
            local_multitrack=local_multitrack,
            shared_reference_audio=True,
            meeting_handoff=True,
            media_timecode=False,
        )

    def test_capabilities_require_real_booleans(self):
        with self.assertRaisesRegex(ValueError, "live_session must be a boolean"):
            CreatorCapabilities(
                live_session=1,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
            )

        with self.assertRaisesRegex(
            ValueError, "session_recording must be a boolean"
        ):
            CreatorCapabilities(
                live_session=True,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
                session_recording=1,
            )

    def test_edit_and_export_capabilities_require_take_review(self):
        with self.assertRaisesRegex(ValueError, "take_editing requires take_review"):
            CreatorCapabilities(
                live_session=True,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
                take_review=False,
                take_editing=True,
                track_export=False,
            )
        with self.assertRaisesRegex(ValueError, "track_export requires take_review"):
            CreatorCapabilities(
                live_session=True,
                local_multitrack=False,
                shared_reference_audio=False,
                meeting_handoff=False,
                media_timecode=False,
                take_review=False,
                take_editing=False,
                track_export=True,
            )

    def test_studio_preset_rejects_duplicate_track_names(self):
        with self.assertRaisesRegex(ValueError, "track names must be unique"):
            StudioPreset(
                key="duplicate",
                label="Duplicate",
                track_names=("Host Mic", "host mic"),
            )

    def test_studio_preset_rejects_invalid_sample_rate(self):
        with self.assertRaisesRegex(ValueError, "sample_rate_hz"):
            StudioPreset(
                key="invalid_rate",
                label="Invalid Rate",
                track_names=("Voice",),
                sample_rate_hz=0,
            )

    def test_profile_requires_presets_when_local_multitrack_is_claimed(self):
        with self.assertRaisesRegex(ValueError, "local_multitrack"):
            CreatorProfile(
                key="test_profile",
                label="Test Profile",
                release_tier=RELEASE_TIER_GA,
                default_template="Test",
                default_goal="Test the profile.",
                quick_help="Use the test profile.",
                review_prompts=("What should change?",),
                capabilities=self._capabilities(local_multitrack=True),
                vocabulary=self._vocabulary(),
            )

    def test_profile_rejects_presets_without_local_multitrack_capability(self):
        with self.assertRaisesRegex(ValueError, "local_multitrack"):
            CreatorProfile(
                key="test_profile",
                label="Test Profile",
                release_tier=RELEASE_TIER_PREVIEW,
                default_template="Test",
                default_goal="Test the profile.",
                quick_help="Use the test profile.",
                review_prompts=("What should change?",),
                capabilities=self._capabilities(local_multitrack=False),
                vocabulary=self._vocabulary(),
                studio_presets=(
                    StudioPreset(
                        key="voice",
                        label="Voice",
                        track_names=("Voice",),
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()

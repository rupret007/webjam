from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.session_intelligence import build_session_pulse


class TestSessionIntelligence(unittest.TestCase):
    def test_empty_music_session_starts_at_sound_check(self):
        pulse = build_session_pulse(
            mode_key="music_jam",
            title="Friday rehearsal",
            participants=[{"name": "You", "is_local": True}],
        )

        self.assertEqual(pulse.mode_label, "Music Jam")
        self.assertEqual(pulse.stage, "Sound Check")
        self.assertIn("sound check", pulse.next_step.lower())
        self.assertEqual(
            pulse.summary,
            "No creative notes yet; capture the shared goal when you’re ready.",
        )
        self.assertNotIn("Music Jam is ready", pulse.summary)
        self.assertEqual(pulse.participant_signal.count, 1)
        self.assertTrue(pulse.participant_signal.local_present)

    def test_parses_structured_notes_and_cleans_urls_and_owners(self):
        pulse = build_session_pulse(
            mode_key="design_critique",
            title="Checkout polish",
            notes="\n".join(
                [
                    "## 14:03:20",
                    "Decision: keep the compact checkout layout",
                    "- [ ] @Sam test keyboard focus",
                    "Risk: mobile receipt view is still crowded",
                    "Question: does the confirm step need a receipt link?",
                    "Reference: https://example.com/mockup,",
                ]
            ),
            participants=[
                SimpleNamespace(is_local=True, muted=False, solo=False),
                SimpleNamespace(is_local=False, muted=True, solo=False),
            ],
        )

        self.assertEqual(pulse.stage, "Unblock")
        self.assertEqual(pulse.decisions, ("keep the compact checkout layout",))
        self.assertEqual(pulse.actions[0].owner, "Sam")
        self.assertEqual(pulse.actions[0].text, "test keyboard focus")
        self.assertEqual(pulse.blockers, ("mobile receipt view is still crowded",))
        self.assertEqual(
            pulse.questions,
            ("does the confirm step need a receipt link",),
        )
        self.assertEqual(pulse.references, ("https://example.com/mockup",))
        self.assertEqual(pulse.participant_signal.muted_count, 1)

    def test_markdown_includes_every_section_without_duplicate_owner(self):
        pulse = build_session_pulse(
            mode_key="music_jam",
            title="Bridge pass",
            notes="\n".join(
                [
                    "Decision: lower keys in the bridge",
                    "Action: @Lee save rehearsal mix",
                    "Question: use a pickup before the chorus?",
                    "Reference: https://example.com/take.wav",
                ]
            ),
        )

        markdown = pulse.to_markdown()
        self.assertIn("## Decisions", markdown)
        self.assertIn("## Actions", markdown)
        self.assertIn("- @Lee save rehearsal mix", markdown)
        self.assertNotIn("@Lee @Lee", markdown)
        self.assertIn("## Questions", markdown)
        self.assertIn("## References", markdown)

    def test_title_is_normalized_to_one_markdown_heading_line(self):
        pulse = build_session_pulse(
            mode_key="writers_room",
            title="  Scene one\n\n  rewrite  ",
        )

        self.assertEqual(pulse.title, "Scene one rewrite")
        self.assertTrue(pulse.to_markdown().startswith("# Scene one rewrite\n"))

    def test_entries_are_deduplicated_and_capped(self):
        notes = ["Decision: keep the intro"] * 2
        notes.extend(f"Action: item {number}" for number in range(7))
        notes.extend(
            [
                "Reference: https://example.com/one",
                "Reference: https://example.com/one",
            ]
        )

        pulse = build_session_pulse(mode_key="music_jam", notes="\n".join(notes))

        self.assertEqual(pulse.decisions, ("keep the intro",))
        self.assertEqual(len(pulse.actions), 5)
        self.assertEqual(pulse.references, ("https://example.com/one",))

    def test_mode_checkpoint_advances_past_seen_terms(self):
        pulse = build_session_pulse(
            mode_key="storyboard_film_room",
            notes="Scene goal: tighten the reveal\nShot list: close-up, insert, wide",
        )

        self.assertEqual(pulse.checkpoint, "continuity")

    def test_owner_removal_normalizes_internal_whitespace(self):
        pulse = build_session_pulse(
            mode_key="music_jam",
            notes="Action: ask @Lee   to export the rehearsal mix",
        )

        self.assertEqual(pulse.actions[0].owner, "Lee")
        self.assertEqual(pulse.actions[0].text, "ask to export the rehearsal mix")

    def test_podcast_creator_profile_uses_voice_defaults_and_vocabulary(self):
        pulse = build_session_pulse(
            creator_profile_key="podcast_voice",
            participants=[
                {"name": "Host", "is_local": True},
                {"name": "Guest", "is_local": False},
            ],
        )

        self.assertEqual(pulse.mode_key, "podcast_voice")
        self.assertEqual(pulse.mode_label, "Podcast & Voice")
        self.assertEqual(
            pulse.title,
            "Capture clear isolated voices and finish a reviewable edit.",
        )
        self.assertEqual(pulse.stage, "Mic Check")
        self.assertEqual(pulse.checkpoint, "mic check")
        self.assertIn("2 speakers", pulse.summary)

    def test_creator_profile_review_prompt_drives_next_step(self):
        pulse = build_session_pulse(
            creator_profile_key="podcast_voice",
            notes="Loose idea for the cold open",
        )

        self.assertEqual(
            pulse.next_step,
            "Which edit most improves clarity or pacing?",
        )

    def test_creator_profile_section_vocabulary_shapes_checkpoints(self):
        pulse = build_session_pulse(
            creator_profile_key="podcast_voice",
            notes="Mic check complete\nFirst take complete",
        )

        self.assertEqual(pulse.checkpoint, "chapter review")

    def test_creator_profile_legacy_alias_canonicalizes(self):
        pulse = build_session_pulse(
            creator_profile_key="visual_studio",
        )

        self.assertEqual(pulse.mode_key, "review_rehearsal")
        self.assertEqual(pulse.mode_label, "Review & Rehearsal")
        self.assertEqual(pulse.checkpoint, "shared goal")

    def test_unknown_creator_profile_falls_back_to_music(self):
        pulse = build_session_pulse(
            mode_key="design_critique",
            creator_profile_key="unsupported_profile",
        )

        self.assertEqual(pulse.mode_key, "music")
        self.assertEqual(pulse.mode_label, "Music")
        self.assertEqual(pulse.checkpoint, "sound check")


if __name__ == "__main__":
    unittest.main()

"""Tests for ``webjam_qt.controllers.session_persistence.SessionPersistence``.

Exercises the load/save round-trip for the session title, mode, and notes,
plus failure modes (missing files, corrupt JSON) and atomic-write usage.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.controllers.session_persistence import SessionPersistence  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class _TempHome:
    """Context manager that points $HOME at a fresh temp dir."""

    def __enter__(self):
        self._old_home = os.environ.get("HOME", "")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        if self._old_home:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        self._tmp.cleanup()
        return False


def _make_window_and_persistence(
    home: Path,
    *,
    creator_profile_key: object = "music",
    initial_title: str = "Original",
):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title=initial_title,
    )
    persistence = SessionPersistence(
        window.session_strip,
        window.session_canvas,
        creator_profile_key=creator_profile_key,
    )
    return window, persistence


class TestSessionPersistence(unittest.TestCase):
    def test_load_with_no_files(self):
        """Clean home dir → load() must not crash and must not change widgets."""
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                # Sanity: directory really is empty
                self.assertFalse((home / ".webjam_notes.md").exists())
                self.assertFalse((home / ".webjam_session.json").exists())

                title_before = window.session_strip.current_title()
                mode_before = window.session_strip.current_mode_key()
                notes_before = window.session_canvas.current_notes()

                persistence.load()

                self.assertEqual(window.session_strip.current_title(), title_before)
                self.assertEqual(window.session_strip.current_mode_key(), mode_before)
                self.assertEqual(window.session_canvas.current_notes(), notes_before)
            finally:
                window.close()

    def test_save_then_load_round_trips_title_and_mode(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                # Pick a mode that's not the initial one
                modes = ApplicationController.mode_entries()
                non_default = next((k for k, _ in modes if k != "music_jam"), None)
                self.assertIsNotNone(non_default, "expected at least 2 modes")

                window.session_strip._title_input.setText("Round-Trip Title")
                picker = window.session_strip._mode_picker
                idx = picker.findData(non_default)
                self.assertGreaterEqual(idx, 0)
                picker.setCurrentIndex(idx)

                persistence.save_title_and_mode()

                payload = json.loads(
                    (home / ".webjam_session.json").read_text(encoding="utf-8")
                )
                self.assertEqual(payload["schema_version"], 2)
                self.assertEqual(
                    payload["profiles"]["music"]["title"],
                    "Round-Trip Title",
                )

                # Build a fresh window and persistence so the load actually exercises
                # disk → widget rather than reading stale in-memory state.
                window2, persistence2 = _make_window_and_persistence(home)
                try:
                    persistence2.load()
                    self.assertEqual(window2.session_strip.current_title(), "Round-Trip Title")
                    self.assertEqual(window2.session_strip.current_mode_key(), non_default)
                finally:
                    window2.close()
            finally:
                window.close()

    def test_profile_titles_are_isolated_and_round_trip_independently(self):
        with _TempHome() as home:
            music_window, music = _make_window_and_persistence(home)
            podcast_window, podcast = _make_window_and_persistence(
                home,
                creator_profile_key="podcast_voice",
            )
            try:
                music_window.session_strip._title_input.setText("Friday Rehearsal")
                music.save_title_and_mode()
                podcast_window.session_strip._title_input.setText("Episode 12")
                podcast_picker = podcast_window.session_strip._mode_picker
                podcast_mode = podcast_picker.findData("visual_studio")
                self.assertGreaterEqual(podcast_mode, 0)
                podcast_picker.setCurrentIndex(podcast_mode)
                podcast.save_title_and_mode()

                music_loaded, music_reader = _make_window_and_persistence(
                    home,
                    initial_title="Music Default",
                )
                podcast_loaded, podcast_reader = _make_window_and_persistence(
                    home,
                    creator_profile_key="podcast_voice",
                    initial_title="Podcast Default",
                )
                review_loaded, review_reader = _make_window_and_persistence(
                    home,
                    creator_profile_key="review_rehearsal",
                    initial_title="Review Default",
                )
                try:
                    music_reader._load_session_metadata()
                    podcast_reader._load_session_metadata()
                    review_reader._load_session_metadata()
                    self.assertEqual(
                        music_loaded.session_strip.current_title(),
                        "Friday Rehearsal",
                    )
                    self.assertEqual(
                        podcast_loaded.session_strip.current_title(),
                        "Episode 12",
                    )
                    self.assertEqual(
                        podcast_loaded.session_strip.current_mode_key(),
                        "visual_studio",
                    )
                    self.assertEqual(
                        music_loaded.session_strip.current_mode_key(),
                        "music_jam",
                    )
                    self.assertEqual(
                        review_loaded.session_strip.current_title(),
                        "Review Default",
                    )
                finally:
                    music_loaded.close()
                    podcast_loaded.close()
                    review_loaded.close()

                payload = json.loads(
                    (home / ".webjam_session.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    set(payload["profiles"]),
                    {"music", "podcast_voice"},
                )
            finally:
                music_window.close()
                podcast_window.close()

    def test_legacy_single_title_loads_only_for_music_and_migrates_on_save(self):
        with _TempHome() as home:
            legacy_path = home / ".webjam_session.json"
            legacy_path.write_text(
                json.dumps({"title": "Legacy Rehearsal", "mode": "music_jam"}),
                encoding="utf-8",
            )
            music_window, music = _make_window_and_persistence(
                home,
                initial_title="Music Default",
            )
            podcast_window, podcast = _make_window_and_persistence(
                home,
                creator_profile_key="podcast_voice",
                initial_title="Podcast Default",
            )
            try:
                music._load_session_metadata()
                podcast._load_session_metadata()
                self.assertEqual(
                    music_window.session_strip.current_title(),
                    "Legacy Rehearsal",
                )
                self.assertEqual(
                    podcast_window.session_strip.current_title(),
                    "Podcast Default",
                )

                podcast_window.session_strip._title_input.setText("Voice Session")
                podcast.save_title_and_mode()
                payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], 2)
                self.assertEqual(
                    payload["profiles"]["music"]["title"],
                    "Legacy Rehearsal",
                )
                self.assertEqual(
                    payload["profiles"]["podcast_voice"]["title"],
                    "Voice Session",
                )
            finally:
                music_window.close()
                podcast_window.close()

    def test_profile_key_api_canonicalizes_aliases_and_unknown_values(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(
                home,
                creator_profile_key="visual_studio",
            )
            try:
                self.assertEqual(persistence.profile_key, "studio_visit")
                self.assertEqual(
                    persistence.set_profile_key("unsupported_profile"),
                    "music",
                )
                self.assertEqual(persistence.profile_key, "music")
            finally:
                window.close()

    @unittest.skipUnless(os.name == "posix", "POSIX symbolic-link contract")
    def test_session_metadata_symlink_is_never_followed(self):
        with _TempHome() as home:
            outside = home / "outside.json"
            outside.write_text(
                json.dumps({"title": "Outside", "mode": "music_jam"}),
                encoding="utf-8",
            )
            path = home / ".webjam_session.json"
            path.symlink_to(outside)
            window, persistence = _make_window_and_persistence(
                home,
                initial_title="Safe Default",
            )
            try:
                persistence._load_session_metadata()
                self.assertEqual(
                    window.session_strip.current_title(),
                    "Safe Default",
                )
                window.session_strip._title_input.setText("Safe Local")
                persistence.save_title_and_mode()
                self.assertFalse(path.is_symlink())
                self.assertEqual(
                    json.loads(outside.read_text(encoding="utf-8"))["title"],
                    "Outside",
                )
            finally:
                window.close()

    def test_untrusted_or_oversized_metadata_never_changes_the_profile_title(self):
        with _TempHome() as home:
            path = home / ".webjam_session.json"
            window, persistence = _make_window_and_persistence(
                home,
                creator_profile_key="podcast_voice",
                initial_title="Podcast Default",
            )
            try:
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "profiles": {
                                "future_profile": {
                                    "title": "Untrusted",
                                    "mode": "music_jam",
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                persistence._load_session_metadata()
                self.assertEqual(
                    window.session_strip.current_title(),
                    "Podcast Default",
                )

                path.write_text("x" * (64 * 1024 + 1), encoding="utf-8")
                persistence._load_session_metadata()
                self.assertEqual(
                    window.session_strip.current_title(),
                    "Podcast Default",
                )
            finally:
                window.close()

    def test_save_then_load_round_trips_notes(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_canvas.set_notes("Verse 1 idea\nChorus hook\n")
                persistence.save()

                window2, persistence2 = _make_window_and_persistence(home)
                try:
                    persistence2.load()
                    self.assertIn("Verse 1 idea", window2.session_canvas.current_notes())
                    self.assertIn("Chorus hook", window2.session_canvas.current_notes())
                finally:
                    window2.close()
            finally:
                window.close()

    def test_creator_profiles_keep_separate_private_local_notes(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_canvas.set_notes("Music scratchpad")
                persistence.switch_profile_key("podcast_voice")
                self.assertEqual(window.session_canvas.current_notes(), "")
                window.session_canvas.set_notes("Podcast outline")
                persistence.switch_profile_key("review_rehearsal")
                self.assertEqual(window.session_canvas.current_notes(), "")
                window.session_canvas.set_notes("Review decisions")

                persistence.switch_profile_key("music")
                self.assertEqual(
                    window.session_canvas.current_notes(),
                    "Music scratchpad",
                )
                persistence.switch_profile_key("podcast_voice")
                self.assertEqual(
                    window.session_canvas.current_notes(),
                    "Podcast outline",
                )

                self.assertEqual(
                    (home / ".webjam_notes.md").read_text(encoding="utf-8"),
                    "Music scratchpad",
                )
                self.assertEqual(
                    (home / ".webjam_notes.review_rehearsal.md").read_text(
                        encoding="utf-8"
                    ),
                    "Review decisions",
                )
                if os.name == "posix":
                    self.assertEqual(
                        (home / ".webjam_notes.md").stat().st_mode & 0o777,
                        0o600,
                    )
            finally:
                window.close()

    def test_cleared_notes_stay_empty_and_unsafe_notes_are_ignored(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                notes_path = home / ".webjam_notes.md"
                notes_path.write_text("Old notes", encoding="utf-8")
                persistence.load()
                self.assertEqual(window.session_canvas.current_notes(), "Old notes")
                window.session_canvas.set_notes("")
                persistence._save_notes_only()
                self.assertEqual(notes_path.read_text(encoding="utf-8"), "")

                notes_path.write_bytes(b"x" * (1024 * 1024 + 1))
                window.session_canvas.set_notes("Safe in-memory notes")
                persistence._load_notes_only()
                self.assertEqual(
                    window.session_canvas.current_notes(),
                    "Safe in-memory notes",
                )
            finally:
                window.close()

    def test_profile_switch_clears_prior_notes_when_target_file_is_unsafe(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_canvas.set_notes("Private music notes")
                unsafe_target = home / ".webjam_notes.podcast_voice.md"
                unsafe_target.write_bytes(b"x" * (1024 * 1024 + 1))

                persistence.switch_profile_key("podcast_voice")

                self.assertEqual(persistence.profile_key, "podcast_voice")
                self.assertEqual(window.session_canvas.current_notes(), "")
                self.assertEqual(
                    (home / ".webjam_notes.md").read_text(encoding="utf-8"),
                    "Private music notes",
                )
            finally:
                window.close()

    def test_load_with_corrupt_session_json_does_not_crash(self):
        with _TempHome() as home:
            (home / ".webjam_session.json").write_text("{ this is not valid json", encoding="utf-8")
            window, persistence = _make_window_and_persistence(home)
            try:
                title_before = window.session_strip.current_title()
                # Must not raise
                persistence.load()
                # Title should be unchanged because the JSON couldn't be parsed
                self.assertEqual(window.session_strip.current_title(), title_before)
            finally:
                window.close()

    def test_save_uses_atomic_write(self):
        """Verify atomic_write_text is used for both files saved by save()."""
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_strip._title_input.setText("Atomic Title")
                window.session_canvas.set_notes("Atomic notes content")
                with mock.patch(
                    "webjam_qt.controllers.session_persistence.atomic_write_text"
                ) as mocked:
                    persistence.save()
                    # Should be called for notes AND session metadata
                    self.assertEqual(mocked.call_count, 2)
                    written_paths = {Path(call.args[0]).name for call in mocked.call_args_list}
                    self.assertIn(".webjam_notes.md", written_paths)
                    self.assertIn(".webjam_session.json", written_paths)
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()


class TestBorrowedInvitationTitle(unittest.TestCase):
    """A joined session's name must not become the musician's own default.

    Joining a jam sets the visible title from the invitation. Persisting that
    made it the startup title for every later session, so a name chosen by
    whoever sent the invite followed the musician into jams they hosted
    themselves -- which is how a hosted session came to be labelled
    "Legacy Join".
    """

    def test_invitation_title_never_overwrites_the_saved_title(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_strip._title_input.setText("Rad Dad Rehearsal")
                persistence.save_title_and_mode()

                # Now join a session someone else named.
                window.session_strip.set_session_title("Legacy Join")
                persistence.mark_title_borrowed("Legacy Join")
                persistence.save_title_and_mode()

                window2, persistence2 = _make_window_and_persistence(home)
                try:
                    persistence2.load()
                    self.assertEqual(
                        window2.session_strip.current_title(),
                        "Rad Dad Rehearsal",
                    )
                finally:
                    window2.close()
            finally:
                window.close()

    def test_typing_over_a_borrowed_title_makes_it_the_musicians_own(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_strip.set_session_title("Legacy Join")
                persistence.mark_title_borrowed("Legacy Join")

                # The musician renames the session themselves.
                persistence.clear_borrowed_title()
                window.session_strip._title_input.setText("Tonight's Jam")
                persistence.save_title_and_mode()

                window2, persistence2 = _make_window_and_persistence(home)
                try:
                    persistence2.load()
                    self.assertEqual(
                        window2.session_strip.current_title(),
                        "Tonight's Jam",
                    )
                finally:
                    window2.close()
            finally:
                window.close()

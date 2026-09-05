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
from webjam_qt.controllers import session_persistence as persistence_module  # noqa: E402
from webjam_qt.controllers.session_persistence import SessionPersistence  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class _TempHome:
    """Context manager that points persistence at a fresh temp dir."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name)
        self._home_patch = mock.patch.object(
            persistence_module,
            "_persistence_home",
            return_value=home,
        )
        self._home_patch.start()
        return home

    def __exit__(self, *exc):
        self._home_patch.stop()
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
                self.assertEqual(persistence.profile_key, "review_rehearsal")
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


def test_failed_profile_saves_keep_each_local_draft_until_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    window, persistence = _make_window_and_persistence(tmp_path)
    try:
        canvas = window.session_canvas
        canvas.set_notes("A clay sculpture idea")
        with mock.patch.object(persistence_module, "atomic_write_text", side_effect=OSError("disk full")):
            assert persistence.switch_profile_key("art") == "art"
            assert canvas.current_notes() == ""
            canvas.set_notes("A printed relief experiment")
            assert persistence.switch_profile_key("music") == "music"
            assert canvas.current_notes() == "A clay sculpture idea"
            assert persistence.has_unsaved_notes
            assert persistence.notes_save_state == "failed"
            assert "Choose Save Notes" in canvas._notes_save_status.text()
            assert not canvas._save_notes_button.isHidden()
        assert persistence._save_notes_only()
        assert not persistence.has_unsaved_notes
        assert (tmp_path / ".webjam_notes.md").read_text() == "A clay sculpture idea"
        assert (tmp_path / ".webjam_notes.art.md").read_text() == "A printed relief experiment"
        assert canvas._save_notes_button.isHidden()
        persistence.switch_profile_key("art")
        assert canvas.current_notes() == "A printed relief experiment"
    finally:
        window.close()
        window.deleteLater()


def test_notes_save_limit_matches_utf8_reader_without_losing_oversized_draft(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    limit = persistence_module._MAX_NOTES_FILE_BYTES
    text = "🎨" * (limit // 4)
    state = SimpleNamespace(text=text, status="")
    canvas = SimpleNamespace(
        current_notes=lambda: state.text,
        restore_notes=lambda value: setattr(state, "text", value),
        set_notes_save_state=lambda value: setattr(state, "status", value),
    )
    persistence = SessionPersistence(SimpleNamespace(), canvas, creator_profile_key="art")
    assert persistence._save_notes_only()
    path = tmp_path / ".webjam_notes.art.md"
    original = path.read_bytes()
    assert len(original) == limit
    assert persistence_module._read_bounded_notes(path) == text
    state.text += "x"
    assert persistence._save_notes_only() is False
    assert state.status == "too_large"
    assert state.text == text + "x"
    assert path.read_bytes() == original
    state.text = "A shorter idea"
    assert persistence._save_notes_only()
    assert persistence_module._read_bounded_notes(path) == "A shorter idea"


def test_failed_notes_never_follow_a_symbolic_link(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    target = tmp_path / "keep.txt"
    target.write_text("Keep this file")
    (tmp_path / ".webjam_notes.md").symlink_to(target)
    canvas = SimpleNamespace(current_notes=lambda: "New draft")
    persistence = SessionPersistence(SimpleNamespace(), canvas)
    assert persistence._save_notes_only() is False
    assert persistence.has_unsaved_notes
    assert target.read_text() == "Keep this file"
    assert (tmp_path / ".webjam_notes.md").is_symlink()


class TestNotesDraftRecovery(unittest.TestCase):
    def test_unreadable_original_survives_close_switch_and_export(self):
        for original in (b"\xffinvalid", b"x" * (1024 * 1024 + 1)):
            with self.subTest(original_size=len(original)), _TempHome() as home:
                path = home / ".webjam_notes.md"
                path.write_bytes(original)
                window, persistence = _make_window_and_persistence(home)
                try:
                    persistence.load()
                    self.assertEqual(window.session_canvas.current_notes(), "")
                    self.assertTrue(persistence._save_notes_only())
                    self.assertEqual(path.read_bytes(), original)
                    window.session_canvas.edit_notes("  new local draft\n")
                    self.assertFalse(persistence._save_notes_only())
                    persistence.switch_profile_key("art")
                    self.assertEqual(window.session_canvas.current_notes(), "")
                    self.assertEqual(persistence.unsaved_notes, (("music", "  new local draft\n"),))
                    with self.assertRaises(ValueError):
                        persistence.export_pending_notes("music", "  new local draft\n", str(path))
                    copy = home / "recovered.md"
                    self.assertTrue(persistence.export_pending_notes("music", "  new local draft\n", str(copy)))
                    self.assertEqual(copy.read_text(), "  new local draft\n")
                    self.assertTrue(persistence._save_notes_only())
                    persistence.switch_profile_key("music")
                    self.assertEqual(window.session_canvas.current_notes(), copy.read_text())
                    self.assertEqual(persistence.notes_save_state, "exported")
                    self.assertTrue(persistence._save_notes_only())
                    self.assertEqual(path.read_bytes(), original)
                finally:
                    window.close()

    def test_hidden_oversized_draft_can_be_shortened_without_switching_session(self):
        from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                window.session_canvas.edit_notes("x" * (1024 * 1024 + 1))
                persistence.switch_profile_key("art")
                dialog = NotesRecoveryDialog(persistence, window)
                dialog._editor.setPlainText("Keep this song idea")
                dialog._save.click()
                self.assertEqual(persistence.profile_key, "art")
                self.assertEqual(window.session_canvas.current_notes(), "")
                self.assertFalse(persistence.has_unsaved_notes)
                self.assertEqual((home / ".webjam_notes.md").read_text(), "Keep this song idea")
                self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
            finally:
                window.close()

    def test_export_failure_or_stale_snapshot_never_acknowledges_a_draft(self):
        with _TempHome() as home:
            window, persistence = _make_window_and_persistence(home)
            try:
                persistence.notes_changed("first")
                with mock.patch.object(persistence_module, "atomic_write_text", side_effect=OSError("private")):
                    with self.assertRaises(OSError):
                        persistence.export_pending_notes("music", "first", str(home / "copy.md"))
                self.assertTrue(persistence.has_unsaved_notes)
                persistence.notes_changed("newer")
                self.assertFalse(persistence.export_pending_notes("music", "first", str(home / "copy.md")))
                self.assertEqual(persistence.unsaved_notes, (("music", "newer"),))
                self.assertFalse((home / "copy.md").exists())
            finally:
                window.close()


def test_recovery_dialog_preserves_stale_editor_until_explicit_export(tmp_path, monkeypatch):
    from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    window, persistence = _make_window_and_persistence(tmp_path)
    try:
        window.session_canvas.edit_notes("old draft")
        persistence.notes_changed("old draft")
        dialog = NotesRecoveryDialog(persistence, window)
        dialog.show()
        dialog._editor.setPlainText("my edited copy")
        window.session_canvas.edit_notes("newer session-side notes")
        assert persistence._save_notes_only()
        dialog.reject()
        assert dialog.isVisible()
        assert dialog._editor.toPlainText() == "my edited copy"
        copy = tmp_path / "conflict-copy.md"
        monkeypatch.setattr("webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName", lambda *a: (str(copy), ""))
        dialog._export.click()
        assert copy.read_text() == "my edited copy"
        assert (tmp_path / ".webjam_notes.md").read_text() == "newer session-side notes"
        assert window.session_canvas.current_notes() == "newer session-side notes"
        assert not dialog.isVisible()
    finally:
        window.close()


def test_notes_owner_distinguishes_restoration_from_editable_replacement():
    from webjam_qt.widgets.session_canvas import SessionCanvas

    canvas = SessionCanvas()
    changed = mock.Mock()
    canvas.notes_changed.connect(changed)
    try:
        canvas.restore_notes("Saved local draft")
        changed.assert_not_called()
        canvas.set_notes("An accepted edit")
        changed.assert_called_once_with("An accepted edit")
        canvas.set_notes("An accepted edit")
        changed.assert_called_once_with("An accepted edit")
        canvas.restore_notes("Another workspace's saved draft")
        changed.assert_called_once_with("An accepted edit")
        assert canvas.current_notes() == "Another workspace's saved draft"
    finally:
        canvas.close()
        canvas.deleteLater()

"""Tests for ``webjam_qt.controllers.session_persistence.SessionPersistence``.

Exercises the load/save round-trip for the session title, mode, and notes,
plus failure modes (missing files, corrupt JSON) and atomic-write usage.
"""
from __future__ import annotations

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


def _make_window_and_persistence(home: Path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Original",
    )
    persistence = SessionPersistence(window.session_strip, window.session_canvas)
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

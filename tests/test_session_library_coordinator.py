"""Work survives room transitions; reopening never authorizes audio or sharing."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from core.creative_modes import get_creator_profile_by_key_or_default  # noqa: E402
from core.session_library import SessionLibrary  # noqa: E402
from webjam_qt.controllers.session_library import (  # noqa: E402
    SessionLibraryCoordinator, import_legacy_workspaces,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Owner(QObject):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.creator_profile = get_creator_profile_by_key_or_default("music")
        self.audio = SimpleNamespace(connected=False, stopping=False, start=Mock())
        self.recording = SimpleNamespace(is_recording_active=False, start=Mock())
        self._startup_attempt = None
        self._art_room_role = ""
        self._is_jamulus_running = Mock(return_value=False)
        self._save_notes = Mock(return_value=True)
        self._on_rail_view_changed = Mock()

    def _apply_creator_profile_key(self, profile, *, host_owned=False):
        self.creator_profile = get_creator_profile_by_key_or_default(profile)
        self.window.set_creator_profile(self.creator_profile, locked=host_owned)


class TestSessionLibraryCoordinator(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.window = ConductorWindow(mode_entries=[("music_jam", "Music")],
                                      initial_mode_key="music_jam", initial_title="Wednesday")
        self.window.flash_message = Mock()
        self.owner = Owner(self.window)
        self.library = SessionLibrary(self.root / "library")
        self.coordinator = SessionLibraryCoordinator(self.owner, library=self.library)
        self.coordinator._imported = True

    def tearDown(self):
        self.coordinator.timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.owner.deleteLater()
        _app.processEvents()
        self.temp.cleanup()

    def test_explicit_resume_preserves_previous_workspace_without_starting_owners(self):
        self.window.session_canvas.set_notes("old draft")
        self.assertTrue(self.coordinator.ensure_current())
        first = self.coordinator.current
        art = self.library.create("art", "Clay bird", notes="keep the wings broad",
                                  art={"version": 1, "brief": "Sculpt a bird"})
        self.window.session_canvas.set_notes("old draft plus decision")
        self.assertTrue(self.coordinator.continue_record(art))
        self.assertEqual(self.library.load(first.id).notes, "old draft plus decision")
        self.assertEqual(self.window.session_canvas.current_notes(), art.notes)
        self.assertEqual(self.owner.creator_profile.key, "art")
        self.owner.audio.start.assert_not_called()
        self.owner.recording.start.assert_not_called()

    def test_failed_save_blocks_switch_and_keeps_draft(self):
        self.coordinator.ensure_current()
        other = self.library.create("music", "Other")
        self.window.session_canvas.set_notes("must survive")
        with patch.object(self.library, "save", side_effect=OSError("full")):
            self.assertFalse(self.coordinator.continue_record(other))
        self.assertEqual(self.window.session_canvas.current_notes(), "must survive")
        self.assertNotEqual(self.coordinator.current.id, other.id)
        self.assertTrue(self.coordinator.flush())
        self.assertEqual(self.library.load(self.coordinator.current.id).notes, "must survive")

    def test_active_room_blocks_switch_but_keeps_saved_workspace(self):
        self.coordinator.ensure_current()
        previous = self.coordinator.current.id
        other = self.library.create("music", "Later")
        self.owner.audio.connected = True
        self.assertFalse(self.coordinator.continue_record(other))
        self.assertEqual(self.coordinator.current.id, previous)
        self.assertEqual(self.library.load(other.id).title, "Later")

    def test_recaps_and_current_take_identity_survive_restart_without_stale_claim(self):
        self.coordinator.start_session()
        self.window.session_canvas.set_notes("Decision: keep chorus\nAction: practice bridge")
        self.coordinator.flush()
        take = SimpleNamespace(take_id="take-one", path=self.root / "take", display_name="First take")
        self.coordinator.recording_started(take.take_id)
        with patch("core.take_review.take_source_identity", return_value="a" * 64):
            self.coordinator.recording_completed(take, validated=True)
        self.assertEqual(self.coordinator.current_take_status(), "Ready")
        self.coordinator.capture_summary()
        self.coordinator.finish_session()
        saved = SessionLibrary(self.root / "library").load(self.coordinator.current.id)
        self.assertIn("keep chorus", saved.recaps[0]["summary"])
        self.assertEqual(saved.recaps[0]["take_ids"], ["take-one"])
        self.assertEqual(saved.take_links[0]["take_id"], "take-one")
        self.coordinator.start_session()
        self.assertIsNone(self.coordinator.current_take_status())
        self.coordinator.capture_summary()
        self.coordinator.finish_session()
        self.assertEqual(self.library.load(saved.id).recaps[-1]["take_ids"], [])

    def test_failed_recap_save_does_not_announce_success(self):
        self.coordinator.start_session()
        self.coordinator.capture_summary()
        self.window.flash_message.reset_mock()
        with patch.object(self.library, "save", side_effect=OSError("full")):
            self.coordinator.finish_session()
        self.assertNotIn("Rehearsal recap saved", str(self.window.flash_message.call_args_list))
        self.assertTrue(self.coordinator.flush())
        self.assertEqual(len(self.library.load(self.coordinator.current.id).recaps), 1)

    def test_art_summary_contains_progress_and_no_music_take(self):
        record = self.library.create("art", "Clay bird", art={"version": 1,
            "progress": "Wings shaped", "next_steps": "Glaze tomorrow"})
        self.coordinator.continue_record(record)
        self.coordinator.start_session()
        self.coordinator.capture_summary()
        self.coordinator.finish_session()
        recap = self.library.load(record.id).recaps[0]
        self.assertIn("Wings shaped", recap["summary"])
        self.assertIn("Glaze tomorrow", recap["summary"])
        self.assertEqual(recap["take_ids"], [])

    def test_art_guest_recap_stays_with_art_when_leave_restores_music_profile(self):
        record = self.library.create("art", "Art guest", art={"version": 1, "progress": "Shape finished"})
        self.coordinator.continue_record(record)
        self.coordinator.start_session()
        self.coordinator.capture_summary()
        self.coordinator.profile_changing()
        self.owner._apply_creator_profile_key("music")
        self.window.session_canvas.set_notes("Music scratchpad")
        self.coordinator.finish_session()
        saved = self.library.load(record.id)
        self.assertEqual(len(saved.recaps), 1)
        self.assertIn("Shape finished", saved.recaps[0]["summary"])
        self.assertEqual(self.window.session_canvas.current_notes(), "Music scratchpad")

    def test_cross_process_change_is_retained_until_explicit_copy(self):
        self.coordinator.ensure_current()
        original = self.coordinator.current
        self.library.save(replace(original, notes="external writer"))
        self.window.session_canvas.set_notes("my exact draft")
        self.assertFalse(self.coordinator.flush())
        self.assertEqual(self.library.load(original.id).notes, "external writer")
        pending = self.coordinator._pending[original.id]
        copied = self.library.create("music", "Copy", notes=pending.notes)
        self.coordinator._copy_saved(pending, copied)
        self.assertTrue(self.coordinator.flush())
        self.assertEqual(self.coordinator.current.id, copied.id)
        self.assertEqual(self.library.load(copied.id).notes, "my exact draft")

    def test_plain_moment_when_reviewed_take_does_not_belong_to_workspace(self):
        self.coordinator.ensure_current()
        self.coordinator.dialog = SimpleNamespace(rehearsal=SimpleNamespace(add_bookmark=Mock()))
        with patch.object(self.window.recording_studio, "current_take_reference", return_value={
                "take_id": "earlier", "take_path": "/old", "source_identity": "aaa", "position_seconds": 12}):
            self.coordinator.mark_moment("good bridge")
        self.coordinator.dialog.rehearsal.add_bookmark.assert_called_once_with("good bridge")

    def test_browsing_other_workspace_cannot_capture_current_workspaces_take_time(self):
        self.coordinator.ensure_current()
        ref = {"take_id": "first", "take_path": "/take", "source_identity": "a" * 64}
        self.coordinator.current = replace(self.coordinator.current, take_links=(ref,))
        unrelated = self.library.create("music", "Other rehearsal")
        panel = SimpleNamespace(add_bookmark=Mock())
        self.coordinator.dialog = SimpleNamespace(rehearsal=panel, record=unrelated)
        with patch.object(self.window.recording_studio, "current_take_reference", return_value=dict(ref, position_seconds=12)):
            self.coordinator.mark_moment("good bridge")
        panel.add_bookmark.assert_called_once_with("good bridge")

    def test_finalizing_take_blocks_context_switch(self):
        self.coordinator.ensure_current()
        other = self.library.create("music", "Other")
        self.owner.recording.take_in_progress = True
        self.assertFalse(self.coordinator.continue_record(other))

    def test_late_take_validation_updates_original_workspace_and_recap(self):
        self.coordinator.start_session()
        source = self.coordinator.current
        self.coordinator.recording_started("exact-take", "recording-session")
        self.coordinator.capture_summary()
        self.coordinator.finish_session()
        other = self.library.create("music", "Other workspace")
        self.coordinator.continue_record(other)
        take = SimpleNamespace(take_id="exact-take", session_id="recording-session",
                               path=self.root / "take", display_name="Validated after stop")
        with patch("core.take_review.take_source_identity", return_value="a" * 64):
            self.coordinator.recording_completed(take, validated=True)
        original = self.library.load(source.id)
        self.assertEqual(original.recaps[0]["take_ids"], ["exact-take"])
        self.assertEqual(original.take_links[0]["take_id"], "exact-take")
        self.assertEqual(self.library.load(other.id).take_links, ())
        self.assertIsNone(self.coordinator.current_take_status())

    def test_legacy_import_is_idempotent_and_original_bytes_remain_unchanged(self):
        original = b"Decision: preserve this\n"
        (self.root / ".webjam_notes.md").write_bytes(original)
        (self.root / ".webjam_notes.art.md").write_text("Art notes", encoding="utf-8")
        (self.root / ".webjam_session.json").write_text(json.dumps({"schema_version": 2,
            "profiles": {"music": {"title": "Old rehearsal", "mode": "music_jam"}}}), encoding="utf-8")
        self.assertEqual(import_legacy_workspaces(self.library, self.root), ())
        self.assertEqual(import_legacy_workspaces(self.library, self.root), ())
        records = self.library.list()
        self.assertEqual(len(records), 2)
        self.assertEqual({r.profile for r in records}, {"music", "art"})
        self.assertEqual((self.root / ".webjam_notes.md").read_bytes(), original)

    def test_library_dialog_copy_retires_exact_pending_draft(self):
        # The callback is only emitted after a durable copy exists.
        self.coordinator.ensure_current()
        source = self.coordinator.current
        copied = self.library.create("music", "Copy", notes="new")
        self.coordinator._pending[source.id] = replace(source, notes="new")
        self.coordinator._copy_saved(self.coordinator._pending[source.id], copied)
        self.assertNotIn(source.id, self.coordinator._pending)
        self.assertEqual(self.window.session_canvas.current_notes(), "new")

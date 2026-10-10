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
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog  # noqa: E402
from core.creative_modes import get_creator_profile_by_key_or_default  # noqa: E402
from core.session_library import SessionLibrary  # noqa: E402
from webjam_qt.controllers.session_library import (  # noqa: E402
    SessionLibraryCoordinator, import_legacy_workspaces,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402
from webjam_qt.windows.session_library import SessionLibraryDialog  # noqa: E402

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
        self.editors = []

    def tearDown(self):
        self.coordinator.timer.stop()
        self.coordinator.dialog = None
        for editor in self.editors:
            editor.timer.stop()
            editor._dirty = False
            editor.close()
            editor.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.owner.deleteLater()
        _app.processEvents()
        self.temp.cleanup()

    def _editor(self):
        editor = SessionLibraryDialog(self.library, self.window,
            current_id=self.coordinator.current.id, pending_records=self.coordinator._pending,
            save_record=self.coordinator.save_editor_record)
        editor.record_saved.connect(self.coordinator._record_saved)
        editor.copy_saved.connect(self.coordinator._copy_saved)
        self.coordinator.dialog = editor
        self.editors.append(editor)
        return editor

    def _late_take_and_recap(self, take_id="late-take"):
        take = SimpleNamespace(take_id=take_id, session_id="recording-session",
                               path=self.root / "take", display_name="Late completed take")
        with patch.object(self.library, "save", side_effect=OSError("temporary write failure")), \
                patch("core.take_review.take_source_identity", return_value="a" * 64):
            self.coordinator.recording_completed(take, validated=True)
            self.coordinator.capture_summary()
            self.coordinator.finish_session()
        return self.coordinator._pending[self.coordinator.current.id]

    def test_remember_lesson_preserves_art_draft_and_saves_canonical_position_once(self):
        self.owner._apply_creator_profile_key("art")
        self.assertTrue(self.coordinator.ensure_current())
        editor = self._editor()
        editor.show()
        editor.art.brief.setPlainText("Keep these colors")
        editor.notes.setPlainText("Do the sky first")
        link = "https://youtu.be/M7lc1UVf-VE?t=90"
        self.assertTrue(self.coordinator.remember_art_lesson(link))
        self.assertTrue(self.coordinator.remember_art_lesson(link))
        saved = self.library.load(self.coordinator.current.id)
        self.assertEqual(saved.notes, "Do the sky first")
        self.assertEqual(saved.art["brief"], "Keep these colors")
        self.assertEqual(len(saved.art["references"]), 1)
        self.assertEqual(saved.art["references"][0]["locator"],
                         "https://www.youtube.com/watch?v=M7lc1UVf-VE&t=90s")

    def test_remember_lesson_cannot_replace_another_unsavable_art_draft(self):
        self.owner._apply_creator_profile_key("art")
        self.assertTrue(self.coordinator.ensure_current())
        current_id = self.coordinator.current.id
        other = self.library.create("art", "Another painting")
        editor = self._editor()
        editor.show()
        editor.select_id(other.id)
        editor.art.brief.setPlainText("Unfinished work in another painting")
        with patch.object(self.library, "save", side_effect=OSError("write unavailable")):
            self.assertFalse(self.coordinator.remember_art_lesson("https://youtu.be/M7lc1UVf-VE"))
        self.assertEqual(editor.record.id, other.id)
        self.assertEqual(editor.art.brief.toPlainText(), "Unfinished work in another painting")
        self.assertTrue(editor._dirty)
        self.assertEqual(self.coordinator.current.id, current_id)
        self.assertEqual(self.library.load(current_id).art.get("references", []), [])

    def test_editor_save_merges_late_pending_take_and_recap_before_publication(self):
        self.coordinator.start_session()
        self.coordinator.recording_started("late-take", "recording-session")
        editor = self._editor()
        original = editor._base_record
        editor.notes.setPlainText("Decision: use the editor's complete draft")
        late = self._late_take_and_recap()
        self.assertEqual(late.revision, original.revision)
        self.assertEqual(late._store_token, original._store_token)
        self.assertTrue(editor.save_current())
        saved = self.library.load(original.id)
        self.assertEqual(saved.notes, "Decision: use the editor's complete draft")
        self.assertEqual(saved.take_links[0]["status"], "complete")
        self.assertEqual(saved.recaps[0]["take_ids"], ["late-take"])
        self.assertNotIn(saved.id, self.coordinator._pending)
        self.assertEqual(self.window.session_canvas.current_notes(), saved.notes)

    def test_summary_export_reconciles_saved_and_pending_live_notes_with_clean_editor(self):
        self.window.session_canvas.set_notes("Decision: initial")
        self.coordinator.start_session()
        self.assertTrue(self.coordinator.flush())
        editor = self._editor()
        baseline = editor._base_record
        self.window.session_canvas.set_notes("Decision: NEW LIVE DECISION")
        self.assertTrue(self.coordinator.flush())
        self.coordinator.capture_summary()
        self.coordinator.finish_session()
        self.assertEqual(len(self.library.load(baseline.id).recaps), 1)
        # Another live edit has not reached the canvas autosave timer yet.
        self.window.session_canvas.set_notes("Decision: newest pending decision")
        self.assertFalse(editor._dirty)
        destination = self.root / "summary.md"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            editor._export()
        summary = destination.read_text()
        self.assertIn("newest pending decision", summary)
        self.assertIn("## Session history", summary)
        self.assertIn("NEW LIVE DECISION", summary)
        self.assertEqual(self.library.load(baseline.id).notes, "Decision: newest pending decision")
        # Export must not advance the baseline behind unchanged controls (W04).
        self.assertEqual(editor._base_record, baseline)
        self.assertEqual(editor.notes.toPlainText(), baseline.notes)
        self.assertFalse(editor._dirty)
        editor.title.setText("Renamed after export")
        self.assertTrue(editor.save_current())
        self.assertEqual(self.library.load(baseline.id).notes, "Decision: newest pending decision")

    def test_summary_export_includes_late_pending_take_and_recap(self):
        self.coordinator.start_session()
        self.coordinator.recording_started("late-take", "recording-session")
        editor = self._editor()
        self.window.session_canvas.set_notes("Decision: keep the late take")
        self._late_take_and_recap()
        self.assertFalse(editor._dirty)
        destination = self.root / "summary.md"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            editor._export()
        self.assertIn("keep the late take", destination.read_text().split("## Session history")[1])
        saved = self.library.load(editor.record.id)
        self.assertEqual(saved.take_links[0]["status"], "complete")
        self.assertEqual(saved.recaps[0]["take_ids"], ["late-take"])

    def test_summary_export_conflict_retains_both_drafts_without_opening_picker(self):
        self.assertTrue(self.coordinator.ensure_current())
        editor = self._editor()
        baseline = editor._base_record
        editor.notes.setPlainText("Decision: editor draft")
        self.window.session_canvas.set_notes("Decision: live draft")
        with patch.object(QFileDialog, "getSaveFileName") as picker:
            editor._export()
        picker.assert_not_called()
        self.assertEqual(editor.notes.toPlainText(), "Decision: editor draft")
        self.assertTrue(editor._dirty)
        self.assertEqual(editor._base_record, baseline)
        self.assertEqual(self.coordinator._pending[baseline.id].notes, "Decision: live draft")
        self.assertEqual(self.library.load(baseline.id).notes, baseline.notes)
        self.assertIn("both retained", editor.status.text())

    def test_summary_export_stays_bound_to_selection_before_picker(self):
        self.window.session_canvas.set_notes("Decision: export this workspace")
        self.assertTrue(self.coordinator.ensure_current())
        editor = self._editor()
        other = self.library.create("music", "Other workspace", notes="Do not export this")
        editor.refresh()
        destination = self.root / "summary.md"

        def choose_path(*_args):
            editor.select_id(other.id)
            return str(destination), ""

        with patch.object(QFileDialog, "getSaveFileName", side_effect=choose_path):
            editor._export()
        self.assertEqual(editor.record.id, other.id)
        self.assertIn("export this workspace", destination.read_text())
        self.assertNotIn("Do not export this", destination.read_text())

    def test_summary_export_canceled_picker_creates_nothing(self):
        self.window.session_canvas.set_notes("Decision: initial")
        self.assertTrue(self.coordinator.ensure_current())
        self.assertTrue(self.coordinator.flush())
        editor = self._editor()
        files_before = set(self.root.rglob("*"))
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
            editor._export()
        self.assertEqual(set(self.root.rglob("*")), files_before)
        self.assertNotIn("Summary exported", editor.status.text())

    def test_summary_export_of_browsed_workspace_can_repeat_and_save_later_edits(self):
        self.assertTrue(self.coordinator.ensure_current())
        other = self.library.create("music", "Browsed workspace")
        editor = self._editor()
        editor.select_id(other.id)
        editor.notes.setPlainText("Decision: keep this draft")
        for name in ("first.md", "second.md"):
            destination = self.root / name
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
                editor._export()
            self.assertTrue(destination.exists(), editor.status.text())
            self.assertIn("keep this draft", destination.read_text())
        editor.title.setText("Renamed browsed workspace")
        self.assertTrue(editor.save_current(), editor.status.text())
        saved = self.library.load(other.id)
        self.assertEqual(saved.title, "Renamed browsed workspace")
        self.assertEqual(saved.notes, "Decision: keep this draft")

    def test_summary_export_still_blocks_unknown_external_write_after_export(self):
        self.assertTrue(self.coordinator.ensure_current())
        other = self.library.create("music", "Browsed workspace")
        editor = self._editor()
        editor.select_id(other.id)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.root / "first.md"), "")):
            editor._export()
        external = self.library.save(replace(self.library.load(other.id), notes="External edit"))
        with patch.object(QFileDialog, "getSaveFileName") as picker:
            editor._export()
        picker.assert_not_called()
        self.assertIn("changed since it was opened", editor.status.text())
        self.assertEqual(self.library.load(other.id), external)

    def test_summary_export_write_failure_retains_live_notes_and_blocks_picker(self):
        self.assertTrue(self.coordinator.ensure_current())
        editor = self._editor()
        self.window.session_canvas.set_notes("Decision: retained live draft")
        with patch.object(self.library, "save", side_effect=OSError("write unavailable")), \
                patch.object(QFileDialog, "getSaveFileName") as picker:
            editor._export()
        picker.assert_not_called()
        self.assertIn("not saved", editor.status.text())
        self.assertEqual(self.coordinator._pending[editor.record.id].notes, "Decision: retained live draft")

    def test_copy_of_older_pending_snapshot_keeps_newer_take_and_recap_owned(self):
        self.coordinator.start_session()
        self.coordinator.recording_started("late-take", "recording-session")
        self.window.session_canvas.set_notes("Retained original draft")
        with patch.object(self.library, "save", side_effect=OSError("temporary write failure")):
            self.assertFalse(self.coordinator.flush())
        editor = self._editor()
        base = editor._base_record
        editor.notes.setPlainText("My separately edited copy")
        late = self._late_take_and_recap()
        self.assertEqual(late.revision, base.revision)
        self.assertEqual(late._store_token, base._store_token)
        with patch.object(QInputDialog, "getText", return_value=("Separate copy", True)):
            editor._copy()
        copied = self.library.load(editor.record.id)
        self.assertNotEqual(copied.id, base.id)
        self.assertEqual(copied.notes, "My separately edited copy")
        self.assertEqual(self.coordinator.current.id, base.id)
        self.assertEqual(self.coordinator._pending[base.id], late)
        self.assertEqual(self.window.session_canvas.current_notes(), "Retained original draft")
        self.assertTrue(self.coordinator.flush())
        saved_original = self.library.load(base.id)
        self.assertEqual(saved_original.take_links[0]["status"], "complete")
        self.assertEqual(saved_original.recaps[0]["take_ids"], ["late-take"])
        self.assertEqual(saved_original.notes, "Retained original draft")

    def test_competing_notes_stay_in_editor_and_pending_without_a_disk_overwrite(self):
        self.window.session_canvas.set_notes("Base Notes")
        self.coordinator.ensure_current()
        editor = self._editor()
        editor.notes.setPlainText("Editor Notes")
        self.window.session_canvas.set_notes("Newer original Notes")
        with patch.object(self.library, "save", side_effect=OSError("temporary write failure")):
            self.coordinator.flush()
        path = self.library.root / f"{editor.record.id}.json"
        before = path.read_bytes()
        self.assertFalse(editor.save_current())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(editor.notes.toPlainText(), "Editor Notes")
        self.assertTrue(editor._dirty)
        self.assertEqual(self.coordinator._pending[editor.record.id].notes, "Newer original Notes")

    def test_save_signal_alone_cannot_acknowledge_newer_pending_lifecycle_state(self):
        self.coordinator.start_session()
        self.coordinator.recording_started("late-take", "recording-session")
        old = self.coordinator.current
        late = self._late_take_and_recap()
        old_saved = self.library.save(replace(old, notes="Older editor publication"))
        self.coordinator._record_saved(old_saved)
        self.assertEqual(self.coordinator._pending[old.id], late)
        self.assertEqual(self.coordinator.current.take_links[0]["status"], "complete")

    def test_editor_autosave_cannot_overwrite_live_notes_before_canvas_timer(self):
        self.window.session_canvas.set_notes("Base Notes")
        self.coordinator.ensure_current()
        editor = self._editor()
        editor.notes.setPlainText("Editor draft")
        self.window.session_canvas.set_notes("Live draft before autosave")
        self.assertFalse(editor.save_current())
        self.assertEqual(self.library.load(editor.record.id).notes, "Base Notes")
        self.assertEqual(self.coordinator._pending[editor.record.id].notes, "Live draft before autosave")
        self.assertEqual(editor.notes.toPlainText(), "Editor draft")

    def test_separate_copy_keeps_live_notes_typed_before_canvas_timer(self):
        self.window.session_canvas.set_notes("Base Notes")
        self.coordinator.ensure_current()
        original_id = self.coordinator.current.id
        editor = self._editor()
        editor.notes.setPlainText("Copied editor draft")
        self.window.session_canvas.set_notes("Live draft before autosave")
        with patch.object(QInputDialog, "getText", return_value=("Separate copy", True)):
            editor._copy()
        self.assertNotEqual(editor.record.id, original_id)
        self.assertEqual(self.library.load(editor.record.id).notes, "Copied editor draft")
        self.assertEqual(self.coordinator.current.id, original_id)
        self.assertEqual(self.window.session_canvas.current_notes(), "Live draft before autosave")
        self.assertTrue(self.coordinator.flush())
        self.assertEqual(self.library.load(original_id).notes, "Live draft before autosave")

    def test_missing_take_path_never_enters_studio(self):
        with patch.object(self.window.recording_studio, "jump_to_bookmark") as jump:
            self.coordinator.open_take({"take_id": "pending", "take_path": ""})
            self.coordinator.open_bookmark({"take_id": "pending", "take_path": ""})
        jump.assert_not_called()
        self.owner._on_rail_view_changed.assert_not_called()

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

    def test_library_does_not_block_live_stop_when_a_draft_cannot_save(self):
        self.coordinator.show()
        dialog = self.coordinator.dialog
        self.assertFalse(dialog.isModal())
        dialog.notes.setPlainText("retain this if disk is full")
        with patch.object(self.library, "save", side_effect=OSError("full")):
            dialog.close()
            _app.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(self.window.isEnabled())
            self.assertFalse(dialog.isModal())
        dialog.close()
        _app.processEvents()

    def test_quit_flush_includes_editor_changes_before_autosave_timer(self):
        self.coordinator.show()
        dialog = self.coordinator.dialog
        dialog.notes.setPlainText("just typed")
        self.assertTrue(self.coordinator.flush())
        self.assertEqual(self.library.load(dialog.record.id).notes, "just typed")
        dialog.close()

    def test_background_flush_retains_live_notes_conflict_and_editor_disk_token(self):
        self.window.session_canvas.set_notes("Original Notes")
        self.coordinator.ensure_current()
        editor = self._editor()
        editor.notes.setPlainText("Retained Library draft")
        editor.timer.stop()
        draft, base = editor._edited_record(), editor._base_record
        path = self.library.root / f"{base.id}.json"
        before = path.read_bytes()
        self.window.session_canvas.set_notes("Newer live Notes")
        self.coordinator.timer.timeout.emit()
        self.assertEqual(self.coordinator._pending[base.id].notes, "Newer live Notes")
        self.assertEqual(editor._edited_record(), draft)
        self.assertEqual(editor._base_record._store_token, base._store_token)
        self.assertTrue(editor._dirty)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.coordinator.flush())
        self.assertTrue(editor._dirty)
        self.assertEqual(path.read_bytes(), before)

    def test_late_recording_completion_waits_for_retained_editor_save(self):
        self.coordinator.start_session()
        self.coordinator.recording_started("late-take", "recording-session")
        editor = self._editor()
        editor.notes.setPlainText("Retained Library draft")
        editor.timer.stop()
        draft, base = editor._edited_record(), editor._base_record
        path = self.library.root / f"{base.id}.json"
        before = path.read_bytes()
        take = SimpleNamespace(take_id="late-take", session_id="recording-session",
                               path=self.root / "take", display_name="Late completed take")
        with patch("core.take_review.take_source_identity", return_value="a" * 64):
            self.coordinator.recording_completed(take, validated=True)
        self.assertEqual(editor._edited_record(), draft)
        self.assertEqual(editor._base_record._store_token, base._store_token)
        self.assertTrue(editor._dirty)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.coordinator.current.id, base.id)
        pending = self.coordinator._pending[base.id]
        self.assertEqual(pending.take_links[0]["status"], "complete")
        self.assertTrue(editor.save_current())
        saved = self.library.load(base.id)
        self.assertEqual(saved.notes, draft.notes)
        self.assertEqual(saved.take_links[0]["status"], "complete")
        self.assertNotIn(base.id, self.coordinator._pending)

    def test_import_preview_keeps_runtime_owner_and_conflicting_editor_draft(self):
        from copy import deepcopy
        from PySide6.QtWidgets import QDialog, QFileDialog
        from core.workspace_backup import export_workspace_backup
        from webjam_qt.windows.session_library import WorkspaceBackupPreviewDialog
        self.coordinator.start_session()
        self.coordinator.recording_started("current-take", "current-recording-session")
        current = self.coordinator.current
        source = self.library.create("music", "Import this separate workspace")
        backup = self.root / "backup.json"
        export_workspace_backup(source, backup)
        editor = self._editor()
        editor.notes.setPlainText("Unsaved editor conflict")
        self.library.save(replace(current, notes="External changes"))
        owners = deepcopy(self.coordinator._recording_owners)
        live_ids = set(self.coordinator._live_take_ids)
        def preview(_dialog):
            self.assertFalse(self.coordinator.flush())
            self.assertEqual(editor.notes.toPlainText(), "Unsaved editor conflict")
            return QDialog.DialogCode.Accepted
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(backup), "")), \
                patch.object(WorkspaceBackupPreviewDialog, "exec", preview):
            editor.import_backup_button.click()
        self.assertEqual(self.coordinator.current, current)
        self.assertEqual(self.coordinator._recording_owners, owners)
        self.assertEqual(self.coordinator._live_take_ids, live_ids)
        self.assertEqual(editor.record, current)
        self.assertTrue(editor._dirty)
        self.assertEqual(editor.notes.toPlainText(), "Unsaved editor conflict")
        self.assertIsNone(editor.selected_record)
        self.assertEqual(len(self.library.list()), 3)
        self.owner.audio.start.assert_not_called()
        self.owner.recording.start.assert_not_called()
        self.owner._on_rail_view_changed.assert_not_called()

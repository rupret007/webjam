"""Take-review integration without modifying the arrangement document."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from core.take_library import load_take
from core.take_player import PlaybackError
from core.take_review import (
    TakeReviewError, load_take_review, save_take_review,
    take_review_identity, take_source_identity,
)


class StudioTakeReviewWorkflowMixin:
    def _workspace_open_state(self):
        owner = self.workspace_owner_token() if self.workspace_owner_token is not None else None
        return (self._guidance_take_revision, self._studio_controller.generation,
                self._studio_controller.edit_revision, self._workspace_open_revision, owner,
                self._review_dialog.notes.toPlainText(), self._review_dialog.favorite.isChecked(),
                self._review_dialog.dirty, self._phase_name)

    def _invalidate_workspace_open(self):
        self._workspace_open_revision += 1
        job = getattr(self, "_workspace_review_job", None)
        if job is not None:
            job.cancel()

    def _workspace_context_for_path(self, path):
        lexical = Path(path).expanduser().absolute()
        return (self._workspace_media_contexts_by_path.get(lexical)
                or self._workspace_media_contexts_by_path.get(lexical.resolve()))

    def _workspace_media_blocked(self):
        return self._workspace_media_context is not None and bool(self._studio_state_error)

    def _workspace_open_allowed(self):
        return not (self._waveform_shutdown or self._shutdown_complete or self._exporting or self._recording
                    or self._phase_name not in {"idle", "complete", "needs_attention", "error"})

    def prepare_workspace_open(self):
        """Capture current ownership only after the old take's drafts save."""
        if (not self._workspace_open_allowed() or self.media_open_pending
                or not self._flush_take_review() or not self._flush_studio_state()):
            return None
        return self._workspace_open_state()

    @property
    def media_open_pending(self):
        job = getattr(self, "_workspace_review_job", None)
        return bool(getattr(self, "_workspace_review_active", False) or (job and job.pending))

    def open_prepared_take(self, verification, *, workspace=None, position_seconds=0.0,
                           expected_state=None, expected_view_revision=None):
        """Activate worker-validated media through the existing Studio gates."""
        from core.workspace_media_backup import WorkspaceMediaVerification
        if not isinstance(verification, WorkspaceMediaVerification) or verification.kind != "take":
            return False
        if (not self._workspace_open_allowed()
                or (expected_state is not None and expected_state != self._workspace_open_state())
                or (expected_view_revision is not None and expected_view_revision != self._guidance_take_revision)):
            self._hint.setText("Studio changed while checking the recording. Open it again when ready.")
            return False
        try:
            position = float(position_seconds)
            fresh = verification.take
            if (not math.isfinite(position) or position < 0 or fresh is None
                    or position > fresh.duration_s or fresh.take_id != verification.reference_id
                    or not fresh.tracks or fresh.review_only or fresh.validation_status != "complete"):
                return False
            # Receipt stats catch replacement between the worker and activation.
            # Audio hashing stays in the worker and existing playback/export validators.
            verification.assert_current()
            if not self._flush_take_review() or not self._flush_studio_state():
                return False
            requested = Path(fresh.path).resolve()
            sources = verification.dependency_map
            sources[fresh.take_id] = (requested, fresh)
            context = {"primary": fresh.take_id, "path": requested, "sources": sources,
                       "identities": verification.take_source_identities,
                       "workspace": deepcopy(workspace)}
            row = next((i for i, take in enumerate(self._takes) if Path(take.path).resolve() == requested), None)
            self._take_list.blockSignals(True)
            try:
                if row is None:
                    row = len(self._takes)
                    self._takes.append(fresh)
                    self._take_list.addItem(self._take_library_item(fresh))
                    self._library.setVisible(True)
                else:
                    self._takes[row] = fresh
                self._take_list.setCurrentRow(row)
            finally:
                self._take_list.blockSignals(False)
            self._on_take_selected(row, workspace_media=context)
            if (self._current is not fresh or self._studio_state_error or not self._play_btn.isEnabled()
                    or take_source_identity(fresh) != verification.sha256):
                return False
            self._player.seek(position)
            return True
        except (OSError, ValueError, PlaybackError):
            self._hint.setText("This recording changed or could not be opened safely. Verify its original media again.")
            return False

    def _schedule_studio_autosave(self, generation):
        self._review_dialog.set_receipt()
        super()._schedule_studio_autosave(generation)

    def _update_studio_state(self, channel_id, **changes):
        self._review_dialog.set_receipt()
        super()._update_studio_state(channel_id, **changes)

    def _load_take_review(self, take):
        self._review_take = take
        try:
            self._review_source_identity = take_source_identity(take)
        except (OSError, TakeReviewError):
            self._review_source_identity = None
        try:
            review = load_take_review(take)
        except TakeReviewError:
            self._review_dialog.apply_review(take.display_name, None)
            self._review_dialog.status.setText("Review could not be opened. Its file and the recording have been preserved.")
        else:
            self._review_dialog.apply_review(take.display_name, review)
        self._review_dialog.export_button.setEnabled(self._can_export_current_take())
        self._review_dialog.set_receipt()

    def _flush_take_review(self) -> bool:
        dialog = self._review_dialog
        if not dialog.dirty:
            return True
        if self._review_take is None or dialog.review is None:
            return False
        try:
            saved = save_take_review(self._review_take, replace(
                dialog.review, favorite=dialog.favorite.isChecked(),
                notes=dialog.notes.toPlainText(),
            ))
        except TakeReviewError as exc:
            dialog.status.setText(str(exc))
            self._hint.setText("Review notes could not be saved. Open Review / Compare and retry before changing takes.")
            return False
        dialog.review = saved
        dialog.dirty = False
        dialog.status.setText("Review saved on this computer.")
        self._refresh_review_labels()
        return True

    def _refresh_review_labels(self):
        for row, take in enumerate(self._takes):
            item = self._take_list.item(row)
            if item is None:
                continue
            text = item.text().removeprefix("★ ")
            try:
                if load_take_review(take).favorite:
                    text = "★ " + text
            except TakeReviewError:
                pass
            item.setText(text)

    def _open_take_review(self):
        if self._current is None or self._viewing_live:
            self._hint.setText("Choose a completed take to review or compare.")
            return
        self._review_dialog.export_button.setEnabled(self._can_export_current_take())
        self._review_dialog.show()
        self._review_dialog.raise_()
        self._review_dialog.notes.setFocus()

    def current_take_reference(self) -> dict | None:
        """Describe only the selected, currently inspectable recording snapshot."""
        take = self._current
        if (take is None or self._viewing_live or self._studio_state_error
                or not self._play_btn.isEnabled()):
            return None
        try:
            source = take_source_identity(take)
        except (OSError, TakeReviewError):
            return None
        if source != getattr(self, "_review_source_identity", None):
            return None
        return {"take_path": str(take.path), "take_id": take_review_identity(take),
                "source_identity": source, "position_seconds": float(self._player.position_s)}

    def open_take(self, path) -> bool:
        """Open this exact local take; never substitute the latest recording."""
        if self._exporting or self._recording or not self._flush_take_review():
            return False
        try:
            workspace_media = self._workspace_context_for_path(path)
            requested = Path(path).expanduser().resolve()
            fresh = load_take(requested)
        except (OSError, ValueError):
            return False
        if fresh is None or not fresh.tracks or fresh.review_only:
            return False
        if any(getattr(track, "media_status", "available") in {
            "missing", "damaged", "transfer_failed", "transferring",
        } for track in fresh.tracks):
            self._hint.setText("This recording has unavailable media. Restore its original files before opening it.")
            return False
        row = next((index for index, take in enumerate(self._takes)
                    if take.path.expanduser().resolve() == requested), None)
        if not self._flush_studio_state():
            return False
        self._take_list.blockSignals(True)
        if row is None:
            # An explicit saved-work link can point outside the configured
            # Takes folder after a move. Add only this freshly loaded take;
            # never scan its parent or change the recording destination.
            row = len(self._takes)
            self._takes.append(fresh)
            self._take_list.addItem(self._take_library_item(fresh))
            self._library.setVisible(True)
        else:
            self._takes[row] = fresh
        self._take_list.setCurrentRow(row)
        self._take_list.blockSignals(False)
        self._refresh_review_labels()
        try:
            self._on_take_selected(row, workspace_media=workspace_media)
        except PlaybackError:
            self._hint.setText("This recording could not be prepared safely. Restore its media and reopen it.")
            return False
        return bool(self._current is fresh and not self._studio_state_error
                    and self._play_btn.isEnabled())

    def jump_to_bookmark(self, take_path, position_seconds, take_id=None, source_identity=None) -> bool:
        try:
            position = float(position_seconds)
            if not math.isfinite(position) or position < 0:
                return False
            fresh = load_take(Path(take_path))
            if fresh is None:
                return False
            if take_id is not None and take_review_identity(fresh) != take_id:
                return False
            if source_identity is not None and take_source_identity(fresh) != source_identity:
                return False
            if position > fresh.duration_s or not self.open_take(take_path):
                return False
            # Check the snapshot once more after activating the arrangement.
            current = self.current_take_reference()
            if current is None or (source_identity is not None
                                   and current["source_identity"] != source_identity):
                return False
            self._player.seek(position)
            return True
        except (OSError, ValueError, PlaybackError):
            return False

    def _assign_review_slot(self, slot):
        if not self._flush_take_review():
            return
        reference = self.current_take_reference()
        if reference is None:
            self._review_dialog.status.setText("Choose an available recording before setting A or B.")
            return
        self._review_slots[slot] = reference
        contexts = getattr(self, "_workspace_review_slots", None)
        if contexts is None:
            contexts = self._workspace_review_slots = {}
        context = getattr(self, "_workspace_media_context", None)
        if context is not None and context["workspace"] is not None:
            contexts[slot] = deepcopy(context["workspace"])
        else:
            contexts.pop(slot, None)
        listen, label = self._review_dialog.slots[slot]
        listen.setEnabled(True)
        label.setText(self._current.display_name)

    def _audition_review_slot(self, slot):
        reference = self._review_slots.get(slot)
        if reference is None:
            return
        workspace = getattr(self, "_workspace_review_slots", {}).get(slot)
        if workspace is not None:
            self._audition_workspace_slot(slot, reference, workspace)
            return
        if not self.jump_to_bookmark(reference["take_path"], 0.0,
                                     reference["take_id"], reference["source_identity"]):
            self._review_dialog.status.setText("This comparison recording changed or is unavailable. Reopen it and set A or B again.")
            return
        self._toggle_play()
        self._review_dialog.status.setText(f"Comparison {slot} selected. Studio shows playback or recovery status; arrangement edits are unchanged.")

    def _audition_workspace_slot(self, slot, reference, workspace):
        from core.workspace_media_backup import verify_workspace_media
        from webjam_qt.windows.workspace_backup import WorkspaceJob, WorkspaceProgressDialog
        expected = self.prepare_workspace_open()
        if expected is None:
            self._review_dialog.status.setText("Finish the current recording, export or draft save before comparing takes.")
            return
        reference, workspace = deepcopy(reference), deepcopy(workspace)
        linked = [r for r in workspace.take_links if r.get("take_id") == reference.get("take_id")]
        if not linked or any(any(r.get(k) != reference.get(k) for k in ("take_id", "take_path", "source_identity")) for r in linked):
            self._review_dialog.status.setText("The comparison link changed. Reopen the workspace and set A or B again.")
            return
        job = WorkspaceJob(self)
        progress = WorkspaceProgressDialog(f"Verify comparison {slot}", self)
        self._workspace_review_job = job
        self._workspace_review_active = True
        job.progress.connect(progress.update_progress)
        progress.cancel_requested.connect(job.cancel)

        def finished(verification, error):
            try:
                progress.settle()
                progress.deleteLater()
                if error is not None or job.cancellation_requested:
                    self._review_dialog.status.setText("Comparison was not opened. Its verification was cancelled or the recording is missing or changed.")
                    return
                if (self._review_slots.get(slot) != reference
                        or getattr(self, "_workspace_review_slots", {}).get(slot) != workspace):
                    self._review_dialog.status.setText("Comparison changed during verification. Choose Listen again.")
                    return
                if not self.open_prepared_take(verification, workspace=workspace, expected_state=expected):
                    self._review_dialog.status.setText("Studio or the comparison recording changed. Reopen it and set A or B again.")
                    return
                self._toggle_play()
                self._review_dialog.status.setText(f"Comparison {slot} selected. Studio shows playback or recovery status; arrangement edits are unchanged.")
            finally:
                self._workspace_review_active = False
                self._workspace_review_job = None
                job.deleteLater()

        job.finished.connect(finished)
        progress.show()
        try:
            job.start(lambda report, cancel: verify_workspace_media(
                workspace, "take", reference["take_id"], progress=report, cancel_check=cancel))
        except Exception as error:
            finished(None, error)

    def _export_reviewed_take(self):
        if self._flush_take_review():
            self._export_tracks()

"""Take-review integration without modifying the arrangement document."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from core.take_library import load_take
from core.take_player import PlaybackError
from core.take_review import (
    TakeReviewError, load_take_review, save_take_review,
    take_review_identity, take_source_identity,
)


class StudioTakeReviewWorkflowMixin:
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
        requested = Path(path).expanduser().resolve()
        try:
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
        if row is None:
            self._hint.setText("That recording is not in this Studio library. Locate its original folder before opening it.")
            return False
        self._takes[row] = fresh
        self._take_list.blockSignals(True)
        self._take_list.setCurrentRow(row)
        self._take_list.blockSignals(False)
        try:
            self._on_take_selected(row)
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
        listen, label = self._review_dialog.slots[slot]
        listen.setEnabled(True)
        label.setText(self._current.display_name)

    def _audition_review_slot(self, slot):
        reference = self._review_slots.get(slot)
        if reference is None:
            return
        if not self.jump_to_bookmark(reference["take_path"], 0.0,
                                     reference["take_id"], reference["source_identity"]):
            self._review_dialog.status.setText("This comparison recording changed or is unavailable. Reopen it and set A or B again.")
            return
        self._toggle_play()
        self._review_dialog.status.setText(f"Comparison {slot} selected. Studio shows playback or recovery status; arrangement edits are unchanged.")

    def _export_reviewed_take(self):
        if self._flush_take_review():
            self._export_tracks()

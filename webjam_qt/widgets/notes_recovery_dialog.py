"""Local recovery for retained notes without changing a joined session."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QTextEdit, QVBoxLayout,
)

from core.creative_modes import get_creator_profile_by_key_or_default
from webjam_qt.controllers.session_persistence import notes_save_failure_state


class NotesRecoveryDialog(QDialog):
    def __init__(self, persistence, parent=None) -> None:
        super().__init__(parent)
        self._persistence = persistence
        self._drafts = dict(persistence.unsaved_notes)
        self._originals = dict(self._drafts)
        self.setWindowTitle("Save local notes")
        self.resize(560, 420)
        self._profile = QComboBox()
        self._profile.setAccessibleName("Unsaved notes workspace")
        for key in self._drafts:
            self._profile.addItem(get_creator_profile_by_key_or_default(key).label, key)
        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setAccessibleName("Retained local notes")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._save = buttons.addButton("Save Notes", QDialogButtonBox.ButtonRole.ActionRole)
        self._export = buttons.addButton("Export Copy…", QDialogButtonBox.ButtonRole.ActionRole)
        self._save.clicked.connect(self._save_current)
        self._export.clicked.connect(self._export_current)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (self._message, self._profile, self._editor, buttons):
            layout.addWidget(widget)
        self._selected = None
        self._profile.currentIndexChanged.connect(self._select)
        self._editor.textChanged.connect(self._show_recovery_guidance)
        self._select()

    def _select(self) -> None:
        if self._selected is not None and not self._leave_current():
            self._profile.blockSignals(True)
            self._profile.setCurrentIndex(self._profile.findData(self._selected))
            self._profile.blockSignals(False)
            return
        self._selected = self._profile.currentData()
        self._editor.setPlainText(self._drafts.get(self._selected, ""))
        self._show_recovery_guidance()

    def _show_recovery_guidance(self) -> None:
        state = self._persistence.notes_recovery_state(self._selected)
        if state == "protected_original":
            message = (
                "The original notes could not be opened and will not be overwritten. "
                "Choose Export Copy to save this draft to another file. "
                "Your collaboration session stays open."
            )
        elif state == "recovery_conflict":
            message = (
                "The recovered draft could not be safely matched to the current saved notes. "
                "Choose Export Copy to keep both versions. The saved notes will not be replaced."
            )
        elif state == "recovered":
            message = (
                "Review this draft recovered after restart. Save Notes updates the original "
                "if it still matches the earlier version, or choose Export Copy to keep "
                "a separate file. Your collaboration session stays open."
            )
        elif state == "too_large":
            message = (
                "This draft is too long for local notes. Shorten it and choose Save Notes, "
                "or choose Export Copy to keep the full draft in another file. "
                "Your collaboration session stays open."
            )
        elif state == "disk_full":
            message = (
                "There is not enough storage to confirm this draft is saved. "
                "Free up space and try Save Notes again, or choose Export Copy "
                "to save to another drive. Your collaboration session stays open."
            )
        elif state == "permission_denied":
            message = (
                "WebJam does not have permission to save these local notes. "
                "Restore write access and try Save Notes again, or choose Export Copy "
                "to save in a writable folder. Your collaboration session stays open."
            )
        elif state == "read_only":
            message = (
                "The notes destination is read-only. Choose Export Copy to save "
                "on a writable drive, or restore write access and try Save Notes again. "
                "Your collaboration session stays open."
            )
        else:
            message = (
                "This draft could not be confirmed saved. Try Save Notes again, "
                "or choose Export Copy to save it to another file. "
                "Your collaboration session stays open."
            )
        checkpoint = getattr(self._persistence, "notes_restart_recovery_state", None)
        if callable(checkpoint):
            exact_draft = (
                self._editor.toPlainText()
                == dict(self._persistence.unsaved_notes).get(self._selected)
            )
            message += (
                " A recovery copy of this draft is saved on this computer."
                if exact_draft and checkpoint(self._selected) == "confirmed"
                else " A restart recovery copy could not be confirmed. Keep WebJam open until saving or exporting succeeds."
            )
        self._set_message(message)
        protected = state in {"protected_original", "recovery_conflict"}
        self._save.setEnabled(not protected)
        self._save.setToolTip(
            "The original cannot be overwritten. Choose Export Copy."
            if protected else "Save this draft on this computer."
        )
        self._save.setAccessibleDescription(self._save.toolTip())
        self._export.setAccessibleDescription("Save this draft to a separate local file.")

    def _set_message(self, message: str) -> None:
        if message == self._message.text():
            return
        self._message.setText(message)
        self._message.setAccessibleDescription(message)

    def _retain_current(self) -> tuple[str, str] | None:
        profile = self._selected
        current = dict(self._persistence.unsaved_notes)
        expected = current.get(profile)
        # A session event may have edited the active notes while a native file
        # chooser was open. Never acknowledge a newer draft using older bytes.
        if expected is None or expected != self._originals.get(profile):
            self._set_message("Notes changed. Close this window and choose Save Notes again.")
            return None
        text = self._editor.toPlainText()
        if not self._persistence.revise_pending_notes(profile, expected, text):
            return None
        self._originals[profile] = text
        self._drafts[profile] = text
        return profile, text

    def _leave_current(self) -> bool:
        if self._selected is None:
            return True
        text = self._editor.toPlainText()
        if text == self._originals.get(self._selected):
            return True
        if self._retain_current() is not None:
            return True
        self._set_message(
            "The saved draft changed while you edited this copy. "
            "Choose Export Copy before leaving it."
        )
        return False

    def reject(self) -> None:
        if self._leave_current():
            super().reject()

    def _save_current(self) -> None:
        draft = self._retain_current()
        if draft is None:
            return
        review_required = getattr(self._persistence, "notes_recovery_requires_review", None)
        if callable(review_required) and review_required(draft[0]):
            self._persistence.save_recovered_notes(*draft)
        else:
            self._persistence._save_notes_only()
        if draft[0] not in dict(self._persistence.unsaved_notes):
            self._remove_current()
        else:
            self._show_recovery_guidance()

    def _export_current(self) -> None:
        profile, text = self._selected, self._editor.toPlainText()
        expected = self._originals.get(profile)
        # Capture the revision before opening the native picker. A changed
        # owner may keep newer notes; the dialog copy remains exportable.
        current = dict(self._persistence.unsaved_notes)
        retained = current.get(profile) == expected and self._persistence.revise_pending_notes(
            profile, expected, text
        )
        if retained:
            self._originals[profile] = text
            self._drafts[profile] = text
        path, _ = QFileDialog.getSaveFileName(
            self, "Export local notes copy", "webjam_notes_copy.md", "Markdown (*.md);;Text (*.txt)"
        )
        if not path:
            return
        try:
            acknowledged = retained and self._persistence.export_pending_notes(profile, text, path)
            if not acknowledged:
                self._persistence.export_notes_copy(text, path)
        except (OSError, ValueError) as exc:
            messages = {
                "disk_full": (
                    "There is not enough storage to confirm the copy is saved. "
                    "Free up space or choose a file on another drive and try again."
                ),
                "permission_denied": (
                    "WebJam does not have permission to save the copy here. "
                    "Choose another file in a writable folder and try again."
                ),
                "read_only": (
                    "The copy destination is read-only. "
                    "Choose another file on a writable drive and try again."
                ),
                "failed": (
                    "The copy could not be confirmed saved. "
                    "Choose another file and try again."
                ),
            }
            self._set_message(messages[notes_save_failure_state(exc)])
            return
        self._remove_current()

    def _remove_current(self) -> None:
        # Save flushes every profile. Rebuild from the owner so already-saved
        # drafts never linger as stale editable rows.
        self._selected = None
        self._drafts = dict(self._persistence.unsaved_notes)
        self._originals = dict(self._drafts)
        self._profile.blockSignals(True)
        self._profile.clear()
        for key in self._drafts:
            self._profile.addItem(get_creator_profile_by_key_or_default(key).label, key)
        self._profile.blockSignals(False)
        self._select()
        if not self._drafts:
            self.accept()

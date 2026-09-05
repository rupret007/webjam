"""Local recovery for retained notes without changing a joined session."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QTextEdit, QVBoxLayout,
)

from core.creative_modes import get_creator_profile_by_key_or_default


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
        self._message = QLabel(
            "These drafts stay on this computer. Shorten a long draft and save, "
            "or export a copy to another file. Your collaboration session stays open."
        )
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
        self._select()

    def _select(self) -> None:
        if self._selected is not None and not self._leave_current():
            self._profile.blockSignals(True)
            self._profile.setCurrentIndex(self._profile.findData(self._selected))
            self._profile.blockSignals(False)
            return
        self._selected = self._profile.currentData()
        self._editor.setPlainText(self._drafts.get(self._selected, ""))

    def _retain_current(self) -> tuple[str, str] | None:
        profile = self._selected
        current = dict(self._persistence.unsaved_notes)
        expected = current.get(profile)
        # A session event may have edited the active notes while a native file
        # chooser was open. Never acknowledge a newer draft using older bytes.
        if expected is None or expected != self._originals.get(profile):
            self._message.setText("Notes changed. Close this window and choose Save Notes again.")
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
        self._message.setText(
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
        self._persistence._save_notes_only()
        if draft[0] not in dict(self._persistence.unsaved_notes):
            self._remove_current()
        else:
            self._message.setText(
                "This draft could not be saved. Export a copy to another file, "
                "or shorten it and try Save Notes again. Existing saved notes are unchanged."
            )

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
        except (OSError, ValueError):
            self._message.setText("The copy could not be saved. Choose another file and try again.")
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

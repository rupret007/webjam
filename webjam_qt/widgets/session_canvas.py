"""
SessionCanvas — right-side notes + artifacts panel.

This is the "session memory" — notes, timestamps, shared artifacts.
Phase 1 lands a simple notes field; artifacts/timeline arrive later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class SessionCanvas(QFrame):
    """
    Right-rail notes surface.

    Phase 1: free-form notes with timestamp / export / clear actions.
    Phase 2+: time-linked notes, pinned references, review state, export brief.
    """

    CANVAS_MIN_WIDTH = 280

    notes_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCanvas")
        self.setMinimumWidth(self.CANVAS_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        header = QLabel("Session Canvas")
        header.setObjectName("CanvasHeader")

        # Action buttons in a compact row
        ts_btn = QPushButton("+ Time")
        ts_btn.setObjectName("GhostButton")
        ts_btn.setToolTip("Insert current timestamp as a heading (Ctrl+T)")
        ts_btn.clicked.connect(self.insert_timestamp)

        export_btn = QPushButton("Export…")
        export_btn.setObjectName("GhostButton")
        export_btn.setToolTip("Save notes to a file")
        export_btn.clicked.connect(self.export_notes)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("GhostButton")
        clear_btn.setToolTip("Clear all notes")
        clear_btn.clicked.connect(self._on_clear)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Space.XS)
        btn_row.setContentsMargins(Space.MD, 0, Space.MD, 0)
        btn_row.addWidget(ts_btn)
        btn_row.addWidget(export_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(clear_btn)

        self._notes = QTextEdit()
        self._notes.setObjectName("CanvasNotes")
        self._notes.setPlaceholderText(
            "Capture what matters:\n"
            "  · decisions made\n"
            "  · chord progressions / lyrics\n"
            "  · links and references\n"
            "  · next session's starting point"
        )
        self._notes.textChanged.connect(self._on_text_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.MD)
        layout.setSpacing(Space.SM)
        layout.addWidget(header)
        layout.addLayout(btn_row)
        layout.addWidget(self._notes, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_notes(self, text: str) -> None:
        if self._notes.toPlainText() == text:
            return
        self._notes.blockSignals(True)
        self._notes.setPlainText(text)
        self._notes.blockSignals(False)

    def current_notes(self) -> str:
        return self._notes.toPlainText()

    def append_line(self, text: str) -> None:
        """Append a line to the end of the notes (e.g. incoming band chat),
        so it becomes part of the shared session record.  Inserted as plain
        text (the editor is a QTextEdit) so chat content isn't HTML-parsed."""
        if not text:
            return
        from PySide6.QtGui import QTextCursor
        self._notes.moveCursor(QTextCursor.MoveOperation.End)
        if self._notes.toPlainText():
            self._notes.insertPlainText("\n")
        self._notes.insertPlainText(text)
        self._notes.moveCursor(QTextCursor.MoveOperation.End)

    def insert_timestamp(self) -> None:
        """Insert the current time as a Markdown heading at the cursor."""
        ts = datetime.now().strftime("## %H:%M:%S")
        cursor = self._notes.textCursor()
        # If not at start of a line, prepend a newline
        text_before = self._notes.toPlainText()[: cursor.position()]
        if text_before and not text_before.endswith("\n"):
            ts = f"\n{ts}"
        cursor.insertText(f"{ts}\n")
        self._notes.setTextCursor(cursor)
        self._notes.setFocus()

    def export_notes(self) -> None:
        """Prompt the user to save current notes to a file."""
        text = self.current_notes().strip()
        if not text:
            return
        date_str = datetime.now().strftime("%Y-%m-%d")
        default_name = f"webjam_session_{date_str}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Session Notes", default_name,
            "Markdown (*.md);;Text files (*.txt);;All files (*)"
        )
        if path:
            try:
                from core.file_io import atomic_write_text
                atomic_write_text(path, text)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Export Failed",
                    f"Could not write notes to:\n{path}\n\n{exc}",
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _on_clear(self) -> None:
        if not self._notes.toPlainText().strip():
            return
        reply = QMessageBox.question(
            self, "Clear notes?",
            "Clear all session notes?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._notes.clear()

    def _on_text_changed(self) -> None:
        self.notes_changed.emit(self._notes.toPlainText())

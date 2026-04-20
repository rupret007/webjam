"""
SessionCanvas — right-side notes + artifacts panel.

This is the "session memory" — notes, timestamps, shared artifacts.
Phase 1 lands a simple notes field; artifacts/timeline arrive later.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class SessionCanvas(QFrame):
    """
    Right-rail notes surface.

    Phase 1: free-form notes saved to the application controller.
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

        self._notes = QTextEdit()
        self._notes.setObjectName("CanvasNotes")
        self._notes.setPlaceholderText(
            "Capture what matters:\n"
            "  · decisions made\n"
            "  · links and references\n"
            "  · next session's starting point"
        )
        self._notes.textChanged.connect(self._on_text_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.MD)
        layout.setSpacing(Space.SM)
        layout.addWidget(header)
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _on_text_changed(self) -> None:
        self.notes_changed.emit(self._notes.toPlainText())

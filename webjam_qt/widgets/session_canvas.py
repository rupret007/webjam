"""
SessionCanvas — right-side notes + artifacts panel.

This is the "session memory" — notes, timestamps, shared artifacts.
Phase 1 lands a simple notes field; artifacts/timeline arrive later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space
from core.session_intelligence import SessionPulse


class SessionCanvas(QFrame):
    """
    Right-rail notes surface.

    Phase 1: free-form notes with timestamp / export / clear actions.
    Phase 2+: time-linked notes, pinned references, review state, export brief.
    """

    CANVAS_MIN_WIDTH = 280

    notes_changed = Signal(str)
    chat_submitted = Signal(str)   # user pressed Enter in the chat box
    brief_export_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCanvas")
        self.setMinimumWidth(self.CANVAS_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._current_pulse: SessionPulse | None = None

        header = QLabel("Session Canvas")
        header.setObjectName("CanvasHeader")

        # Action buttons in a compact row
        ts_btn = QPushButton("+ Time")
        ts_btn.setObjectName("GhostButton")
        ts_btn.setToolTip("Insert current timestamp as a heading (Ctrl+T)")
        ts_btn.clicked.connect(self.insert_timestamp)

        # A single menu keeps the supported 280 px canvas width usable while
        # retaining separate notes and structured-brief export paths.
        self._export_button = QPushButton("Export…")
        self._export_button.setObjectName("GhostButton")
        self._export_button.setAccessibleName("Export session")
        self._export_button.setToolTip("Export session notes or a structured brief")
        export_menu = QMenu(self._export_button)
        self._export_notes_action = export_menu.addAction("Session notes…")
        self._export_notes_action.setToolTip("Export session notes")
        self._export_notes_action.triggered.connect(self.export_notes)
        self._export_brief_action = export_menu.addAction("Session brief…")
        self._export_brief_action.setToolTip("Export session brief")
        self._export_brief_action.triggered.connect(self.export_brief)
        self._export_button.setMenu(export_menu)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("GhostButton")
        clear_btn.setToolTip("Clear all notes")
        clear_btn.clicked.connect(self._on_clear)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(Space.XS)
        btn_row.setContentsMargins(Space.MD, 0, Space.MD, 0)
        btn_row.addWidget(ts_btn)
        btn_row.addWidget(self._export_button)
        btn_row.addStretch(1)
        btn_row.addWidget(clear_btn)
        self._toolbar_buttons = (ts_btn, self._export_button, clear_btn)

        self._pulse = QFrame()
        self._pulse.setObjectName("SessionPulse")
        self._pulse.setAccessibleName("Session pulse")
        pulse_header = QLabel("Session Pulse")
        pulse_header.setObjectName("PulseHeader")
        pulse_header.setTextFormat(Qt.TextFormat.PlainText)
        self._pulse_stage = QLabel("Ready")
        self._pulse_stage.setObjectName("PulseStage")
        self._pulse_summary = QLabel("Capture the first checkpoint.")
        self._pulse_summary.setObjectName("PulseSummary")
        self._pulse_next = QLabel("Next: start with the shared goal.")
        self._pulse_next.setObjectName("PulseNext")
        self._pulse_signals = QLabel("0 decisions · 0 actions · 0 blockers")
        self._pulse_signals.setObjectName("PulseSignals")
        for label in (
            self._pulse_stage,
            self._pulse_summary,
            self._pulse_next,
            self._pulse_signals,
        ):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        pulse_layout = QVBoxLayout(self._pulse)
        pulse_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        pulse_layout.setSpacing(Space.XS)
        pulse_layout.addWidget(pulse_header)
        pulse_layout.addWidget(self._pulse_stage)
        pulse_layout.addWidget(self._pulse_summary)
        pulse_layout.addWidget(self._pulse_next)
        pulse_layout.addWidget(self._pulse_signals)

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

        # Chat box — send a message to the whole band (Jamulus chat).
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("CanvasChatInput")
        self._chat_input.setPlaceholderText("Message your band… (Enter to send)")
        self._chat_input.returnPressed.connect(self._on_chat_entered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.MD)
        layout.setSpacing(Space.SM)
        layout.addWidget(header)
        layout.addLayout(btn_row)
        layout.addWidget(self._pulse)
        layout.addWidget(self._notes, stretch=1)
        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(Space.MD, 0, Space.MD, 0)
        chat_row.addWidget(self._chat_input)
        layout.addLayout(chat_row)

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

    def set_session_pulse(self, pulse: SessionPulse) -> None:
        """Render the current local pulse without interpreting note markup."""
        self._current_pulse = pulse
        self._pulse_stage.setText(pulse.stage)
        self._pulse_summary.setText(pulse.summary)
        self._pulse_next.setText(f"Next: {pulse.next_step}")
        self._pulse_signals.setText(pulse.signal_line)

    def clear_session_pulse(self) -> None:
        """Discard stale derived content while preserving the raw notes."""
        self._current_pulse = None
        self._pulse_stage.setText("Unavailable")
        self._pulse_summary.setText("Session Pulse could not be refreshed.")
        self._pulse_next.setText("Next: continue from the raw notes.")
        self._pulse_signals.setText("Raw notes remain available")

    def current_session_brief(self) -> str:
        """Return the current structured brief followed by the raw notes."""
        notes = self.current_notes().strip()
        if self._current_pulse is None:
            return notes
        brief = self._current_pulse.to_markdown()
        if notes:
            brief = f"{brief}\n\n## Notes\n{notes}"
        return brief

    def _on_chat_entered(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.chat_submitted.emit(text)

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
            except OSError:
                QMessageBox.warning(
                    self, "Export Failed",
                    "WebJam couldn't export the notes. Choose another folder "
                    "and try again.",
                )

    def export_brief(self) -> None:
        """Prompt the user to export a fresh structured session brief."""
        self.brief_export_requested.emit()
        text = self.current_session_brief().strip()
        if not text:
            return
        date_str = datetime.now().strftime("%Y-%m-%d")
        default_name = f"webjam_brief_{date_str}.md"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Session Brief",
            default_name,
            "Markdown (*.md);;Text files (*.txt);;All files (*)",
        )
        if path:
            try:
                from core.file_io import atomic_write_text

                atomic_write_text(path, text)
            except OSError:
                QMessageBox.warning(
                    self,
                    "Export Failed",
                    "WebJam couldn't export the brief. Choose another folder "
                    "and try again.",
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

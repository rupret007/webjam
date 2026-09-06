"""
SessionCanvas — right-side local notes and authenticated Jamulus chat.

Notes are local to this computer. ``+ Time`` inserts wall-clock time; it is
not media timecode. Chat is the separate live shared-text path.
"""

from __future__ import annotations

from datetime import datetime

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

from core.creative_modes import CreatorProfile
from core.musician_guidance import GuidanceState, MusicianGuidanceSnapshot
from core.session_intelligence import SessionPulse
from webjam_qt.theme.tokens import Space


class SessionCanvas(QFrame):
    """
    Right-rail notes surface.

    Phase 1: free-form notes with timestamp / export / clear actions.
    Phase 2+: time-linked notes, pinned references, review state, export brief.
    """

    CANVAS_MIN_WIDTH = 280

    notes_changed = Signal(str)
    save_notes_requested = Signal()
    chat_submitted = Signal(str)   # user pressed Enter in the chat box
    brief_export_requested = Signal()
    suggestion_requested = Signal()  # Art: Suggestion on these notes/canvas
    return_to_room_requested = Signal()  # Art: leave Notes without changing the draft

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCanvas")
        self.setMinimumWidth(self.CANVAS_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._current_pulse: SessionPulse | None = None
        self._current_guidance: MusicianGuidanceSnapshot | None = None
        self._compact_guidance = False
        self._compact_notes_controls = False
        self._art_profile = False

        self._header = QLabel("Session Canvas")
        self._header.setObjectName("CanvasHeader")
        self._room_return_button = QPushButton("Back to room")
        self._room_return_button.setObjectName("QuietButton")
        # Match the existing notes header instead of inheriting the taller
        # main-window action size and taking space from the artist's draft.
        self._room_return_button.setStyleSheet(f"min-height: {Space.LG}px;")
        self._room_return_button.setAccessibleName("Back to room")
        self._room_return_button.setAccessibleDescription(
            "Show the full Art room and its current activities. Keep your local notes."
        )
        self._room_return_button.setToolTip(
            "Show the Art room. Your notes stay on this computer."
        )
        self._room_return_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self._room_return_button.setVisible(False)
        self._room_return_button.setEnabled(False)
        self._room_return_button.clicked.connect(self._request_room_return)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        self._header_row = header_row
        header_row.setSpacing(Space.XS)
        header_row.addWidget(self._header, 1)
        header_row.addWidget(self._room_return_button)

        # Action buttons in a compact row
        ts_btn = QPushButton("+ Time")
        ts_btn.setObjectName("GhostButton")
        ts_btn.setToolTip(
            "Insert the computer's current wall-clock time (Ctrl+T); this is "
            "not media timecode"
        )
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
        self._export_notes_action.setEnabled(False)
        self._export_notes_action.triggered.connect(self.export_notes)
        self._export_brief_action = export_menu.addAction("Session brief…")
        self._export_brief_action.setToolTip("Export session brief")
        self._export_brief_action.triggered.connect(self.export_brief)
        self._export_button.setMenu(export_menu)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("GhostButton")
        clear_btn.setToolTip("Clear all notes")
        clear_btn.clicked.connect(self._on_clear)

        self._suggestion_button = QPushButton("Suggestion")
        self._suggestion_button.setObjectName("GhostButton")
        self._suggestion_button.setAccessibleName("Suggestion for these notes")
        self._suggestion_button.setToolTip(
            "A suggestion for what you're making. Not a detected fact. "
            "Nothing is uploaded."
        )
        self._suggestion_button.clicked.connect(self.suggestion_requested.emit)
        self._suggestion_button.setVisible(False)

        # Art puts Suggestion on its own row so the word stays whole at
        # 280 px. Music keeps the original one-row chrome.
        self._suggestion_row = QWidget()
        suggestion_row = QHBoxLayout(self._suggestion_row)
        suggestion_row.setContentsMargins(Space.XS, 0, Space.XS, 0)
        suggestion_row.setSpacing(Space.XS)
        suggestion_row.addWidget(self._suggestion_button)
        suggestion_row.addStretch(1)
        self._suggestion_row.setVisible(False)

        chrome_row = QHBoxLayout()
        chrome_row.setSpacing(Space.XS)
        chrome_row.setContentsMargins(Space.XS, 0, Space.XS, 0)
        chrome_row.addWidget(ts_btn)
        chrome_row.addWidget(self._export_button)
        chrome_row.addStretch(1)
        chrome_row.addWidget(clear_btn)
        self._toolbar_buttons = (
            ts_btn,
            self._suggestion_button,
            self._export_button,
            clear_btn,
        )
        for button in self._toolbar_buttons:
            button.setMinimumWidth(0)
            button.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )
        toolbar = QVBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(Space.XS)
        toolbar.addWidget(self._suggestion_row)
        toolbar.addLayout(chrome_row)
        self._notes_save_status = QLabel()
        self._notes_save_status.setObjectName("NotesSaveStatus")
        self._notes_save_status.setTextFormat(Qt.TextFormat.PlainText)
        self._notes_save_status.setWordWrap(True)
        self._notes_save_status.setAccessibleName("Local notes save status")
        self._save_notes_button = QPushButton("Save Notes")
        self._save_notes_button.setObjectName("GhostButton")
        self._save_notes_button.setAccessibleName("Save Notes")
        self._save_notes_button.setToolTip("Retry saving local notes kept in this app")
        self._save_notes_button.clicked.connect(self.save_notes_requested.emit)
        toolbar.addWidget(self._notes_save_status)
        chrome_row.insertWidget(2, self._save_notes_button)
        self._normal_notes_buttons = (ts_btn, clear_btn)
        self._notes_save_state = ""
        self.set_notes_save_state("saved")

        self._guidance = QFrame()
        self._guidance.setObjectName("MusicianGuidance")
        self._guidance.setAccessibleName("Session guidance")
        guidance_header = QLabel("NOW")
        guidance_header.setObjectName("PulseHeader")
        self._guidance_status = QLabel("Ready when you are")
        self._guidance_status.setObjectName("PulseStage")
        self._guidance_next = QLabel("Next: Start Session")
        self._guidance_next.setObjectName("GuidanceNext")
        self._guidance_why = QLabel(
            "Why: WebJam has not checked a live music path yet."
        )
        self._guidance_why.setObjectName("GuidanceWhy")
        self._guidance_outputs = QLabel("No recording or export is confirmed yet.")
        self._guidance_outputs.setObjectName("GuidanceOutputs")
        self._guidance_recent = QLabel("Session record: no transitions yet")
        self._guidance_recent.setObjectName("GuidanceRecent")
        for label in (
            guidance_header,
            self._guidance_status,
            self._guidance_next,
            self._guidance_why,
            self._guidance_outputs,
            self._guidance_recent,
        ):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        guidance_layout = QVBoxLayout(self._guidance)
        guidance_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        guidance_layout.setSpacing(Space.XS)
        guidance_layout.addWidget(guidance_header)
        guidance_layout.addWidget(self._guidance_status)
        guidance_layout.addWidget(self._guidance_next)
        guidance_layout.addWidget(self._guidance_why)
        guidance_layout.addWidget(self._guidance_outputs)
        guidance_layout.addWidget(self._guidance_recent)

        self._pulse = QFrame()
        self._pulse.setObjectName("SessionPulse")
        self._pulse.setAccessibleName("Session pulse")
        pulse_header = QLabel("CREATIVE PULSE")
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
        self._notes.setAccessibleName("Session notes")
        self._notes.setAccessibleDescription(
            "Editable notes in the local session record, saved on this computer. "
            "They are not shared with session participants and are not "
            "media-timecode synchronized."
        )
        self._notes.setPlaceholderText(
            "Capture what matters:\n"
            "  · decisions made\n"
            "  · chord progressions / lyrics\n"
            "  · links and references\n"
            "  · next session's starting point"
        )
        self._notes.textChanged.connect(self._on_text_changed)

        # Chat box — the separate live shared-text path (Jamulus chat).
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("CanvasChatInput")
        self._chat_input.setAccessibleName("Band chat message")
        self._chat_input.setPlaceholderText("Message your band… (Enter to send)")
        self._chat_input.returnPressed.connect(self._on_chat_entered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.MD)
        layout.setSpacing(Space.SM)
        layout.addLayout(header_row)
        layout.addLayout(toolbar)
        layout.addWidget(self._guidance)
        layout.addWidget(self._pulse)
        layout.addWidget(self._notes, stretch=1)
        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(Space.MD, 0, Space.MD, 0)
        chat_row.addWidget(self._chat_input)
        layout.addLayout(chat_row)
        QWidget.setTabOrder(self._room_return_button, ts_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_creator_profile(self, profile: CreatorProfile) -> None:
        """Apply profile vocabulary while keeping local-note limits explicit."""

        if not isinstance(profile, CreatorProfile):
            raise TypeError("profile must be a CreatorProfile")
        preview = " · Preview" if profile.is_preview else ""
        self._header.setText(f"{profile.label} Notes{preview}")
        self.setAccessibleName(f"{profile.label} local notes")
        if profile.key == "art":
            self.setAccessibleDescription(
                "These notes stay on this computer. Live chat is separate."
            )
        else:
            self.setAccessibleDescription(
                "These notes are part of the local session record and stay on this "
                "computer. Live chat is separate. There is no shared-note "
                "synchronization or media timecode."
            )
        if profile.key == "podcast_voice":
            placeholder = (
                "Local production notes:\n"
                "  · pickups and edits\n"
                "  · chapter ideas\n"
                "  · speaker follow-ups\n"
                "  · next recording action"
            )
        elif profile.key == "review_rehearsal":
            placeholder = (
                "Local review notes (Preview):\n"
                "  · feedback and decisions\n"
                "  · wall-clock cues only\n"
                "  · owners and next pass\n"
                "  · no visual-media sync"
            )
        elif profile.key == "art":
            placeholder = (
                "Local notes:\n"
                "  · what you're making\n"
                "  · what to try next\n"
                "  · who needs what\n"
                "  · next time"
            )
        else:
            placeholder = (
                "Local session notes:\n"
                "  · decisions made\n"
                "  · chord progressions / lyrics\n"
                "  · links and references\n"
                "  · next session's starting point"
            )
        self._notes.setPlaceholderText(placeholder)
        participants = profile.vocabulary.participant_plural
        self._chat_input.setAccessibleName(
            f"Shared chat message for session {participants}"
        )
        if profile.key == "art":
            self._chat_input.setPlaceholderText(
                f"Message {participants}… (Enter to send)"
            )
        else:
            self._chat_input.setPlaceholderText(
                f"Message {participants} in Jamulus chat… (Enter to send)"
            )
        art = profile.key == "art"
        self._suggestion_button.setVisible(art)
        self._suggestion_button.setEnabled(art)
        self._art_profile = art
        self._room_return_button.setVisible(art)
        self._room_return_button.setEnabled(art)
        self._header.setWordWrap(art)
        self._header_row.setContentsMargins(0, 0, Space.SM if art else 0, 0)
        self._sync_notes_controls()
        self._suggestion_row.setVisible(art and self._notes_save_state not in {"failed", "too_large"})
        # Default NOW copy is Music-shaped until the HUD writes. A painter
        # should not read "live music path" on the notes.
        if art and self._current_guidance is None:
            self._guidance_why.setText("Why: WebJam has not checked this room yet.")
            self._guidance_next.setText("Next: follow the session bar")

    def room_return_button(self) -> QPushButton:
        return self._room_return_button

    def _request_room_return(self) -> None:
        # A queued Art click can arrive after a profile switch. Navigation
        # remains Art-only even when delivery bypasses Qt's disabled button.
        if self._art_profile:
            self.return_to_room_requested.emit()

    def restore_notes(self, text: str) -> None:
        """Restore owner-held bytes without creating a new user edit."""
        if self._notes.toPlainText() == text:
            return
        self._notes.blockSignals(True)
        self._notes.setPlainText(text)
        self._notes.blockSignals(False)
        self._sync_export_actions()

    def set_notes(self, text: str) -> None:
        """Replace editable notes and notify their persistence owner."""
        if self._notes.toPlainText() != text:
            self._notes.setPlainText(text)

    def edit_notes(self, text: str) -> None:
        """Apply an explicit user edit through the normal notes change path."""
        self.set_notes(text)

    def current_notes(self) -> str:
        return self._notes.toPlainText()

    def set_notes_save_state(self, state: str) -> None:
        messages = {
            "saved": "Saved on this computer",
            "pending": "Saving notes…",
            "failed": "Notes need saving. Choose Save Notes.",
            "too_large": "Long draft: choose Save Notes to shorten or export it.",
            "unreadable": "Saved notes could not be opened. The original is unchanged.",
            "exported": "Draft exported to your chosen file.",
        }
        if state not in messages:
            raise ValueError("Unknown local notes save state.")
        if state == self._notes_save_state:
            return
        self._notes_save_state = state
        self._notes_save_status.setText(messages[state])
        self._notes_save_status.setAccessibleDescription(messages[state])
        needs_attention = state in {"failed", "too_large"}
        self._notes_save_status.setVisible(needs_attention or state in {"unreadable", "exported"})
        for button in getattr(self, "_normal_notes_buttons", ()):
            button.setVisible(not needs_attention)
        self._save_notes_button.setVisible(needs_attention)
        # A save failure belongs beside the draft, above optional suggestions.
        # Keep the editor and recovery reachable at the compact window floor.
        if hasattr(self, "_pulse"):
            self._pulse.setVisible(not needs_attention)
        self._suggestion_row.setVisible(self._art_profile and not needs_attention)

    def set_session_pulse(self, pulse: SessionPulse) -> None:
        """Render the current local pulse without interpreting note markup."""
        self._current_pulse = pulse
        self._pulse_stage.setText(pulse.stage)
        self._pulse_summary.setText(pulse.summary)
        self._pulse_next.setText(f"Next: {pulse.next_step}")
        self._pulse_signals.setText(pulse.signal_line)

    def set_musician_guidance(
        self,
        guidance: MusicianGuidanceSnapshot,
    ) -> None:
        """Render the shared truth without duplicating its primary control."""

        self._current_guidance = guidance
        self._guidance_status.setText(guidance.title)
        self._guidance_next.setText(f"Next: {guidance.next_step}")
        self._guidance_why.setText(f"Why: {guidance.why}")
        self._guidance_outputs.setText(guidance.output_line)
        self._guidance_outputs.setVisible(
            any(
                output.state
                not in {GuidanceState.NOT_STARTED, GuidanceState.NOT_REQUIRED}
                for output in guidance.outputs
            )
        )
        self._render_guidance_record()
        self._guidance.setAccessibleDescription(guidance.accessible_description)

    def _render_guidance_record(self) -> None:
        guidance = self._current_guidance
        if guidance is None or not guidance.transitions:
            self._guidance_recent.setVisible(False)
            return
        count = 1 if self._compact_guidance else 3
        recent = guidance.transitions[-count:]
        self._guidance_recent.setText(
            "Recent: "
            + " · ".join(f"{item.at[11:16]} {item.label}" for item in recent)
        )
        self._guidance_recent.setVisible(True)

    def _sync_notes_controls(self) -> None:
        compact = self._art_profile and self.height() < 500
        if compact == self._compact_notes_controls:
            return
        self._compact_notes_controls = compact
        # Two full-height tool rows can otherwise overlap in a compact Art
        # workspace. Keep every action and leave the editor its own space.
        for button in (*self._toolbar_buttons, self._save_notes_button):
            button.setStyleSheet(f"min-height: {Space.LG}px;" if compact else "")
        self.layout().setSpacing(Space.XS if compact else Space.SM)

    def resizeEvent(self, event) -> None:
        compact = self.height() < 500
        if compact != self._compact_guidance:
            self._compact_guidance = compact
            self._pulse_stage.setVisible(not compact)
            self._pulse_summary.setVisible(not compact)
            self._pulse_signals.setVisible(not compact)
            self._render_guidance_record()
        self._sync_notes_controls()
        super().resizeEvent(event)

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
        if self._current_guidance is not None:
            brief = self._current_guidance.to_markdown()
        elif self._current_pulse is not None:
            brief = self._current_pulse.to_markdown()
        else:
            return notes
        if notes:
            brief = f"{brief}\n\n## Notes\n{notes}"
        return brief

    def _on_chat_entered(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.chat_submitted.emit(text)

    def restore_unsent_chat(self, text: str) -> None:
        """Return a failed message to the composer without overwriting typing."""

        message = str(text or "").strip()
        if not message or self._chat_input.text():
            return
        self._chat_input.setText(message)
        self._chat_input.selectAll()
        self._chat_input.setFocus()

    def append_line(self, text: str) -> None:
        """Append plain text to this computer's local notes surface."""
        if not text:
            return
        from PySide6.QtGui import QTextCursor
        self._notes.moveCursor(QTextCursor.MoveOperation.End)
        if self._notes.toPlainText():
            self._notes.insertPlainText("\n")
        self._notes.insertPlainText(text)
        self._notes.moveCursor(QTextCursor.MoveOperation.End)

    def insert_timestamp(self) -> None:
        """Insert wall-clock time, never a media-timecode claim."""
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
        text = self.current_notes()
        if not text.strip():
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
        self._sync_export_actions()
        self.notes_changed.emit(self._notes.toPlainText())

    def _sync_export_actions(self) -> None:
        """Keep enabled export choices aligned with available content."""

        self._export_notes_action.setEnabled(bool(self.current_notes().strip()))

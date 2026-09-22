"""
SessionCanvas — right-side local notes and authenticated Jamulus chat.

Notes are local to this computer. ``+ Time`` inserts wall-clock time; it is
not media timecode. Chat is the separate live shared-text path.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sys

from PySide6.QtCore import QEvent, Qt, Signal
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

from core.creative_modes import CREATOR_PROFILES, CreatorProfile
from core.musician_guidance import GuidanceState, MusicianGuidanceSnapshot
from core.session_intelligence import SessionPulse
from webjam_qt.theme.tokens import Space


_NOTES_WORKSPACE_LABELS = {profile.key: profile.label for profile in CREATOR_PROFILES}
_NOTES_RECOVERY_COPY = {
    "pending": "Saving notes…",
    "failed": "Notes could not be confirmed saved. Choose Save Notes.",
    "too_large": "Long draft: choose Save Notes to shorten or export it.",
    "protected_original": (
        "Original notes could not be opened. Choose Save Notes to export your draft."
    ),
    "disk_full": "Storage is full. Choose Save Notes to retry or export elsewhere.",
    "permission_denied": "Permission denied. Choose Save Notes to retry or export elsewhere.",
    "read_only": "Storage is read-only. Choose Save Notes to export elsewhere.",
    "recovered": "Recovered draft: choose Save Notes to review it before saving.",
    "recovery_conflict": (
        "Recovered draft needs a separate copy. Choose Save Notes to review and export it."
    ),
}


class SessionCanvas(QFrame):
    """
    Right-rail notes surface.

    Phase 1: free-form notes with timestamp / export / clear actions.
    Phase 2+: time-linked notes, pinned references, review state, export brief.
    """

    CANVAS_MIN_WIDTH = 280

    notes_changed = Signal(str)
    notes_restored = Signal()  # owner-held document changed; never a user edit
    save_notes_requested = Signal()
    chat_submitted = Signal(str)   # user pressed Enter in the chat box
    brief_export_requested = Signal()
    suggestion_requested = Signal()  # Art: Suggestion on these notes/canvas
    return_to_room_requested = Signal()  # Art: leave Notes without changing the draft
    talk_share_requested = Signal()  # Art: reveal the separate meeting controls

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCanvas")
        self.setMinimumWidth(self.CANVAS_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._current_pulse: SessionPulse | None = None
        self._current_guidance: MusicianGuidanceSnapshot | None = None
        self._compact_guidance = False
        self._compact_notes_controls = False
        self._narrow_notes_controls = False
        self._suggestion_inline = False
        self._art_profile = False
        self._talk_share_available = True
        self._notes_active_profile_key = "music"
        self._notes_recovery_summary: tuple[tuple[str, str], ...] = ()
        self._notes_need_attention = False
        self._notes_export_handler: Callable[[str, str], None] | None = None

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
        self._chrome_row = chrome_row
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
        self._notes.installEventFilter(self)
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

        self._art_communication = QWidget()
        communication = QHBoxLayout(self._art_communication)
        communication.setContentsMargins(0, 0, 0, 0)
        communication.setSpacing(Space.SM)
        self._communication_hint = QLabel(
            "Notes stay here. Talk or share in your meeting."
        )
        self._communication_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._communication_hint.setWordWrap(True)
        self._talk_share_button = QPushButton("Talk && share")
        self._talk_share_button.setObjectName("QuietButton")
        self._talk_share_button.setStyleSheet(f"min-height: {Space.LG}px;")
        self._talk_share_button.setAccessibleName("Talk & share")
        self._talk_share_button.setAccessibleDescription(
            "Show the separate Conversation controls. Your local notes are not sent."
        )
        self._talk_share_button.setToolTip(
            "Show Conversation controls for talking or showing your work in a meeting."
        )
        self._talk_share_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self._talk_share_button.clicked.connect(self._request_talk_share)
        communication.addWidget(self._communication_hint, 1)
        communication.addWidget(self._talk_share_button)
        self._art_communication.setVisible(False)
        self._talk_share_button.setEnabled(False)

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
        chat_row.addWidget(self._art_communication)
        layout.addLayout(chat_row)
        QWidget.setTabOrder(self._room_return_button, ts_btn)
        QWidget.setTabOrder(self._notes, self._talk_share_button)

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
                "These notes stay on this computer. Talk & share opens the separate meeting controls."
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
        self._notes.setToolTip(
            "Control+Tab: next control. Control+Shift+Tab: previous control."
            if profile.key == "art" else ""
        )
        self._notes.setAccessibleDescription(
            "Editable notes in the local session record, saved on this computer. "
            "They are not shared with session participants and are not "
            "media-timecode synchronized."
            + (" Control+Tab moves to the next control; Control+Shift+Tab moves back."
               if profile.key == "art" else "")
        )
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
        # Hiding the Music composer preserves its draft and selection; Art
        # has no Jamulus chat transport and must not offer a failed retry.
        self._chat_input.setVisible(not art)
        self._chat_input.setEnabled(not art)
        self._art_communication.setVisible(art)
        self._talk_share_button.setEnabled(art and self._talk_share_available)
        self._room_return_button.setVisible(art)
        self._room_return_button.setEnabled(art)
        self._header.setWordWrap(art)
        self._header_row.setContentsMargins(0, 0, Space.SM if art else 0, 0)
        self._sync_notes_controls()
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

    def talk_share_button(self) -> QPushButton:
        return self._talk_share_button

    def set_talk_share_available(self, available: bool) -> None:
        """Render the current Conversation owner's availability."""

        self._talk_share_available = bool(available)
        self._talk_share_button.setEnabled(self._art_profile and self._talk_share_available)

    def _request_talk_share(self) -> None:
        if self._art_profile and self._talk_share_available:
            self.talk_share_requested.emit()

    def restore_notes(self, text: str) -> None:
        """Restore owner-held bytes without creating a new user edit."""
        if self._notes.toPlainText() == text:
            return
        self._notes.blockSignals(True)
        self._notes.setPlainText(text)
        self._notes.blockSignals(False)
        self._sync_export_actions()
        self.notes_restored.emit()

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
            **_NOTES_RECOVERY_COPY,
            "unreadable": "Saved notes could not be opened. The original is unchanged.",
            "exported": "Draft exported to your chosen file.",
        }
        if state not in messages:
            raise ValueError("Unknown local notes save state.")
        if state == self._notes_save_state:
            return
        self._notes_save_state = state
        self._render_notes_save_state()

    def set_notes_recovery_context(
        self, active_profile_key: str, summary: tuple[tuple[str, str], ...],
    ) -> None:
        """Render the persistence owner's retained workspaces, never their notes."""
        active = active_profile_key if active_profile_key in _NOTES_WORKSPACE_LABELS else "music"
        known = {
            key: reason if reason in _NOTES_RECOVERY_COPY else "failed"
            for key, reason in summary if key in _NOTES_WORKSPACE_LABELS
        }
        bounded = tuple((key, known[key]) for key in _NOTES_WORKSPACE_LABELS if key in known)
        if (active, bounded) == (self._notes_active_profile_key, self._notes_recovery_summary):
            return
        self._notes_active_profile_key = active
        self._notes_recovery_summary = bounded
        self._render_notes_save_state()

    def _render_notes_save_state(self) -> None:
        state = self._notes_save_state
        messages = {
            "saved": "Saved on this computer",
            **_NOTES_RECOVERY_COPY,
            "unreadable": "Saved notes could not be opened. The original is unchanged.",
            "exported": "Draft exported to your chosen file.",
        }
        summary = self._notes_recovery_summary
        failures = tuple((key, reason) for key, reason in summary if reason != "pending")
        needs_attention = bool(failures) or state in set(_NOTES_RECOVERY_COPY) - {"pending"}
        self._notes_need_attention = needs_attention
        message = messages[state]
        description = message
        selected_reason = state
        workspace_names = ", ".join(_NOTES_WORKSPACE_LABELS[key] for key, _ in summary)
        if failures:
            key, selected_reason = next(
                (item for item in failures if item[0] == self._notes_active_profile_key),
                failures[0],
            )
            if len(summary) == 1:
                heading = f"{workspace_names} notes need saving."
                detail = _NOTES_RECOVERY_COPY[selected_reason]
            else:
                heading = f"{len(summary)} workspaces need saving"
                heading += f": {workspace_names}." if len(summary) == 2 else "."
                detail = f"{_NOTES_WORKSPACE_LABELS[key]}: {_NOTES_RECOVERY_COPY[selected_reason]}"
            message = f"{heading}\n{detail}"
            # Larger sets keep the visible message compact; every workspace
            # and its own bounded reason remains available to assistive tech.
            description = message + "\n" + "\n".join(
                f"{_NOTES_WORKSPACE_LABELS[key]}: {_NOTES_RECOVERY_COPY[reason]}"
                for key, reason in summary
            )
        self._notes_save_status.setText(message)
        self._notes_save_status.setAccessibleDescription(description)
        self._notes_save_status.setToolTip(description)
        recovery_description = (
            "Open notes recovery to export your draft without replacing the original."
            if selected_reason == "protected_original"
            else "Retry saving retained local notes, or export a separate copy."
        )
        if failures:
            recovery_description = f"Recover local drafts for {workspace_names}. {recovery_description}"
        self._save_notes_button.setToolTip(recovery_description)
        self._save_notes_button.setAccessibleDescription(recovery_description)
        self._notes_save_status.setVisible(needs_attention or state in {"unreadable", "exported"})
        for button in getattr(self, "_normal_notes_buttons", ()):
            button.setVisible(not needs_attention)
        self._save_notes_button.setVisible(needs_attention)
        # A save failure belongs beside the draft, above optional suggestions.
        # Keep the editor and recovery reachable at the compact window floor.
        if hasattr(self, "_pulse"):
            self._pulse.setVisible(not needs_attention)
        self._sync_suggestion_layout()

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

    def _sync_suggestion_layout(self) -> None:
        """Use one tools row when every existing action fits at its full width."""

        margins = self._chrome_row.contentsMargins()
        required = (
            sum(button.minimumSizeHint().width() for button in self._toolbar_buttons)
            + self._chrome_row.spacing() * len(self._toolbar_buttons)
            + margins.left() + margins.right()
        )
        inline = self._art_profile and self.width() >= max(400, required)
        available = self._art_profile and not self._notes_need_attention
        focused = self._suggestion_button.hasFocus()
        if inline != self._suggestion_inline:
            self._suggestion_inline = inline
            if inline:
                self._suggestion_row.layout().removeWidget(self._suggestion_button)
                self._chrome_row.insertWidget(1, self._suggestion_button)
            else:
                self._chrome_row.removeWidget(self._suggestion_button)
                self._suggestion_row.layout().insertWidget(0, self._suggestion_button)
        self._suggestion_button.setVisible(available)
        self._suggestion_row.setVisible(available and not inline)
        if focused and available and not self._suggestion_button.hasFocus():
            self._suggestion_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _sync_notes_controls(self) -> None:
        compact = self._art_profile and self.height() < 500
        narrow = self._art_profile and self.width() < 400
        if compact != self._compact_notes_controls or narrow != self._narrow_notes_controls:
            self._compact_notes_controls = compact
            self._narrow_notes_controls = narrow
            # Two full-height tool rows can otherwise overlap in a compact Art
            # workspace. Keep every action and leave the editor its own space.
            rules = [f"min-height: {Space.LG}px;"] if compact else []
            if narrow:
                rules.append(f"padding-left: {Space.XS}px; padding-right: {Space.XS}px;")
            for button in (*self._toolbar_buttons, self._save_notes_button):
                button.setStyleSheet(" ".join(rules))
            self.layout().setSpacing(Space.XS if compact else Space.SM)
            self.layout().setContentsMargins(0, 0, 0, Space.XS if compact else Space.MD)
            # Keep the draft usable at the compact window floor. The readouts
            # retain all their text; only their interior spacing becomes tighter.
            for readout in (self._guidance, self._pulse):
                margin = Space.XS if compact else Space.SM
                readout.layout().setContentsMargins(Space.MD, margin, Space.MD, margin)
                readout.layout().setSpacing(0 if compact else Space.XS)
        self._sync_suggestion_layout()

    def eventFilter(self, watched, event) -> bool:
        if (watched is getattr(self, "_notes", None) and self._art_profile
                and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
            # Qt maps the physical Control key to MetaModifier on macOS.
            # Plain Tab remains an edit; this explicit chord leaves the draft.
            control = (Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin"
                       else Qt.KeyboardModifier.ControlModifier)
            if event.modifiers() in (control, control | Qt.KeyboardModifier.ShiftModifier):
                forward = (event.key() != Qt.Key.Key_Backtab
                           and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                return self.focusNextPrevChild(forward)
        return super().eventFilter(watched, event)

    def event(self, event) -> bool:
        if (event.type() == QEvent.Type.LayoutRequest
                and hasattr(self, "_talk_share_button") and self.layout() is not None):
            # Child font/style changes can alter button widths without a
            # panel resize. Re-evaluate the same controls before allocation.
            self._sync_notes_controls()
        return super().event(event)

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
        if self._art_profile:
            return
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.chat_submitted.emit(text)

    def restore_unsent_chat(self, text: str, *, focus: bool = True) -> None:
        """Return a failed message to the composer without overwriting typing."""

        message = str(text or "").strip()
        if not message or self._chat_input.text():
            return
        self._chat_input.setText(message)
        if focus and not self._art_profile:
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

    def set_notes_export_handler(self, handler: Callable[[str, str], None]) -> None:
        """Use the persistence owner's protected destination policy for copies."""
        self._notes_export_handler = handler

    def _write_notes_export(self, text: str, path: str) -> None:
        if self._notes_export_handler is not None:
            self._notes_export_handler(text, path)
        else:
            from core.file_io import atomic_write_text
            atomic_write_text(path, text, mode=0o600)

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
                self._write_notes_export(text, path)
            except ValueError:
                QMessageBox.warning(
                    self, "Choose a Separate File",
                    "Choose a separate file for this copy so saved notes and recovery copies stay intact.",
                )
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
                self._write_notes_export(text, path)
            except ValueError:
                QMessageBox.warning(
                    self, "Choose a Separate File",
                    "Choose a separate file for this copy so saved notes and recovery copies stay intact.",
                )
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

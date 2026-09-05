"""Ephemeral reference-session help, separate from saved notes and Jamulus."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class RoomHelpPanel(QFrame):
    """Plain-text, bounded, memory-only view; never an export or notes source."""

    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RoomHelpPanel")
        self.setAccessibleName("Temporary session help preview")
        self._available = False
        self._pending = False

        heading = QLabel("Session help · Preview")
        heading.setObjectName("CanvasHeader")
        explanation = QLabel("Temporary messages · never saved to notes")
        self._status = QLabel("Connect privately to exchange setup help.")
        for label in (heading, explanation, self._status):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        self._status.setAccessibleName("Session help connection and delivery status")

        self._messages = QPlainTextEdit()
        self._messages.setReadOnly(True)
        self._messages.setUndoRedoEnabled(False)
        self._messages.setObjectName("RoomHelpMessages")
        self._messages.setAccessibleName("Temporary session help messages")
        self._messages.setAccessibleDescription(
            "Plain-text messages for the current secure peer. Peer acknowledgement "
            "does not mean a person read the message. Cleared when the session changes."
        )
        self._messages.setMaximumBlockCount(160)
        self._messages.setMinimumHeight(80)
        self._messages.setMaximumHeight(150)
        self._messages.setPlaceholderText("Messages stay here only for this session.")

        self._input = QLineEdit()
        self._input.setObjectName("RoomHelpInput")
        self._input.setMaxLength(500)
        self._input.setAccessibleName("Temporary help message")
        self._input.setPlaceholderText("Ask for setup help…")
        self._input.setToolTip(
            "Plain text, up to 500 UTF-8 bytes. No markup, attachments or saved transcript."
        )
        self._input.returnPressed.connect(self._submit)
        self._input.textChanged.connect(self._sync_controls)
        self._send = QPushButton("Send")
        # The line edit owns Enter. A QDialog auto-default button would also
        # click for that same key press and emit the message a second time.
        self._send.setAutoDefault(False)
        self._send.setDefault(False)
        self._send.setAccessibleName("Send temporary session help")
        self._send.clicked.connect(self._submit)
        composer = QHBoxLayout()
        composer.setSpacing(Space.XS)
        composer.addWidget(self._input, stretch=1)
        composer.addWidget(self._send)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        layout.setSpacing(Space.XS)
        layout.addWidget(heading)
        layout.addWidget(explanation)
        layout.addWidget(self._status)
        layout.addWidget(self._messages)
        layout.addLayout(composer)
        self._sync_controls()
        self.hide()

    def set_available(self, available: bool, reason: str) -> None:
        self._available = bool(available)
        self.set_status(reason)
        self._sync_controls()

    def set_pending(self, pending: bool) -> None:
        self._pending = bool(pending)
        self._send.setText("Sending…" if self._pending else "Send")
        self._sync_controls()

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_entries(self, entries: Sequence[tuple[str, str, str]]) -> None:
        # Enforce a second display bound even if a future caller changes the
        # presenter's history policy. Never hand text to a rich-text renderer.
        text = "\n\n".join(
            f"{label}: {body}\n{status}".rstrip()
            for label, body, status in entries[-40:]
        )
        if self._messages.toPlainText() == text:
            return
        scroll = self._messages.verticalScrollBar()
        position = scroll.value()
        follow_latest = position >= scroll.maximum() - 4
        self._messages.setPlainText(text)
        scroll.setValue(scroll.maximum() if follow_latest else position)

    def draft_text(self) -> str:
        return self._input.text()

    def clear_draft(self) -> None:
        # QLineEdit.clear() leaves Undo capable of resurrecting private text.
        # setText resets that history, including after a peer/session change.
        self._input.setText("")

    def clear_session(self) -> None:
        self._messages.setPlainText("")
        self._input.setText("")
        self._pending = False
        self._send.setText("Send")
        self.set_available(False, "Connect privately to exchange setup help.")

    def _sync_controls(self, *_args: object) -> None:
        available = self._available and not self._pending
        self._input.setEnabled(available)
        self._send.setEnabled(available and bool(self._input.text().strip()))

    def _submit(self) -> None:
        if self._available and not self._pending and self._input.text().strip():
            # Only the authenticated presenter may clear a confirmed draft.
            self.submitted.emit(self._input.text())

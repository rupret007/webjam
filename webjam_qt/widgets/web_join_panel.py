"""Visible Music-room actions; permission and clipboard ownership stay in the controller."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class WebJoinPanel(QFrame):
    primary_requested = Signal(str, object)
    stop_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "unavailable"
        self._context = None
        self._primary_intent = None
        self._stop_intent = None
        self.setAccessibleName("Browser guest invitation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.primary = QPushButton("Invite a browser guest")
        self.primary.setObjectName("PrimaryButton")
        self.primary.setMinimumHeight(36)
        self.primary.pressed.connect(self._remember_primary)
        self.primary.clicked.connect(self._submit_primary)
        self.stop_button = QPushButton("End browser invite")
        self.stop_button.setObjectName("GhostButton")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.pressed.connect(lambda: setattr(self, "_stop_intent", self._context))
        self.stop_button.clicked.connect(self._submit_stop)
        row.addWidget(self.primary)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Browser guest status")
        layout.addWidget(self.status)
        self.render("unavailable", "Start a hosted Music session to invite a browser guest.")

    def _remember_primary(self):
        action = "copy" if self._state == "ready" else "invite"
        self._primary_intent = action, self._context

    def _submit_primary(self):
        intent, self._primary_intent = self._primary_intent, None
        if intent is not None:
            self.primary_requested.emit(*intent)

    def _submit_stop(self):
        context, self._stop_intent = self._stop_intent, None
        if context is not None:
            self.stop_requested.emit(context)

    def render(self, state, message, context=None):
        self._state, self._context = state, context
        text = {"preparing": "Preparing invitation…", "ready": "Copy browser invite",
                "connected": "Browser guest connected", "error": "Retry browser invite"}.get(
                    state, "Invite a browser guest")
        self.primary.setText(text)
        self.primary.setAccessibleName(text)
        self.primary.setEnabled(state in {"available", "ready", "error"})
        self.stop_button.setVisible(state in {"preparing", "ready", "connected", "cleanup"})
        self.stop_button.setEnabled(state in {"preparing", "ready", "connected", "cleanup"})
        self.stop_button.setText("Retry ending browser guest" if state == "cleanup" else "End browser invite")
        self.status.setText(message)


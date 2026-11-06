"""Visible Music-room actions; permission and clipboard ownership stay in the controller."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class WebJoinPanel(QFrame):
    primary_requested = Signal(str, object)
    stop_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "unavailable"
        self._music_context = True
        self._context = None
        self._primary_intent = None
        self._stop_intent = None
        self.setAccessibleName("Browser guest invitation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self._actions = row
        # Start narrow so minimum width cannot prevent the compact resize that
        # measures both buttons. This follows the existing Art workspace rows.
        row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.primary = QPushButton("Copy browser invite")
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
        self._sync_visibility()
        text = {"preparing": "Preparing link…", "connected": "Browser guest connected",
                "error": "Retry copying invite"}.get(state, "Copy browser invite")
        self.primary.setText(text)
        self.primary.setAccessibleName(text)
        self.primary.setEnabled(state in {"available", "ready", "error"})
        self.stop_button.setVisible(state in {"preparing", "ready", "connected", "cleanup"})
        self.stop_button.setEnabled(state in {"preparing", "ready", "connected", "cleanup"})
        self.stop_button.setText(
            {"preparing": "Cancel", "cleanup": "Retry ending browser guest"}.get(
                state, "End browser invite"
            )
        )
        self.status.setText(message)
        self._sync_actions()

    def set_music_visible(self, visible):
        self._music_context = bool(visible)
        self._sync_visibility()

    def _sync_visibility(self):
        # Before there is an owned host, retain the lobby and Conversation
        # space. Once hosting is available the direct action stays on-page.
        self.setVisible(self._music_context and self._state != "unavailable")

    def _sync_actions(self):
        margins = self.layout().contentsMargins()
        parent = self.parentWidget()
        available = min(self.width(), parent.width()) if parent is not None else self.width()
        available -= margins.left() + margins.right()
        buttons = [button for button in (self.primary, self.stop_button) if not button.isHidden()]
        needed = sum(button.minimumSizeHint().width() for button in buttons)
        needed += max(0, len(buttons) - 1) * self._actions.spacing()
        direction = (QBoxLayout.Direction.TopToBottom if needed > available
                     else QBoxLayout.Direction.LeftToRight)
        if self._actions.direction() != direction:
            self._actions.setDirection(direction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_actions()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "status"):
            self._sync_actions()
        return result


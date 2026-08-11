"""One plain-language answer to: are we ready and what happens next?"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal, Qt
from PySide6 import QtGui
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class SessionHud(QFrame):
    """Compact session truth with an explicit, credential-safe copy action."""

    # New callers can handle every HUD action through one semantic signal.
    # The two no-argument signals remain for the controller paths that already
    # depend on them.
    action_requested = Signal(str)
    secondary_action_requested = Signal(str)
    invite_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionHud")
        self.setAccessibleName("Session readiness")
        self._last_announcement = ""
        self._invite_available = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.LG)

        status_layout = QVBoxLayout()
        status_layout.setSpacing(1)
        self._status = QLabel("Starting your jam…")
        self._status.setObjectName("SessionHudStatus")
        self._status.setWordWrap(True)
        self._detail = QLabel("WebJam is getting the music ready.")
        self._detail.setObjectName("SessionHudDetail")
        self._detail.setWordWrap(True)
        self._input = QLineEdit()
        self._input.setObjectName("SessionHudInput")
        self._input.setVisible(False)
        status_layout.addWidget(self._status)
        status_layout.addWidget(self._detail)
        status_layout.addWidget(self._input)
        layout.addLayout(status_layout, 1)

        self._secondary_action = QPushButton()
        self._secondary_action.setObjectName("GhostButton")
        self._secondary_action.setVisible(False)
        self._secondary_action.clicked.connect(self._emit_secondary_action)
        layout.addWidget(self._secondary_action)

        self._action = QPushButton("Copy Invite Link")
        self._action.setObjectName("PrimaryButton")
        self._action.setAccessibleName("Copy invite link")
        self._action.setAccessibleDescription(
            "Copies the complete invitation link to your clipboard."
        )
        self._action_kind = "invite"
        self._action.clicked.connect(self._emit_action)
        self._action.setVisible(False)
        layout.addWidget(self._action)

    def set_state(
        self,
        status: str,
        detail: str,
        *,
        invite_available: bool = False,
        action_text: str = "Copy Invite Link",
        action_visible: bool | None = None,
        action_kind: str = "invite",
        ready: bool = False,
        secondary_action_text: str = "",
        secondary_action_visible: bool = False,
        secondary_action_kind: str = "",
        input_visible: bool = False,
        input_placeholder: str = "",
        input_value: str = "",
        input_accessible_name: str = "",
    ) -> None:
        self._status.setText(str(status))
        self._detail.setText(str(detail))
        self._invite_available = bool(invite_available)
        self._action_kind = str(action_kind).strip().lower() or "primary"
        default_visible = (
            self._invite_available if self._action_kind == "invite" else True
        )
        visible = default_visible if action_visible is None else bool(action_visible)
        action_label = str(action_text)
        # A literal ampersand is part of the musician-facing label, not an
        # accidental keyboard mnemonic.  Keep the accessible name unescaped.
        self._action.setText(action_label.replace("&", "&&"))
        self._action.setAccessibleName(action_label)
        action_description = self._action_description(action_label, str(detail))
        self._action.setAccessibleDescription(action_description)
        self._action.setToolTip(action_description)
        self._action.setVisible(visible)
        secondary_label = str(secondary_action_text or "")
        self._secondary_action_kind = (
            str(secondary_action_kind or "").strip().lower() or "secondary"
        )
        self._secondary_action.setText(secondary_label.replace("&", "&&"))
        self._secondary_action.setAccessibleName(secondary_label)
        self._secondary_action.setAccessibleDescription(
            f"{secondary_label}. {detail}".strip()
        )
        self._secondary_action.setVisible(
            bool(secondary_action_visible and secondary_label)
        )
        self._input.setVisible(bool(input_visible))
        self._input.setPlaceholderText(str(input_placeholder or ""))
        if input_visible and self._input.text() != str(input_value or ""):
            self._input.setText(str(input_value or ""))
        if not input_visible:
            self._input.clear()
        self._input.setAccessibleName(
            str(input_accessible_name or input_placeholder or "Session detail")
        )
        self.setProperty("ready", "true" if ready else "false")
        self.setAccessibleDescription(f"{status}. {detail}")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        announcement = f"{status}. {detail}"
        if announcement != self._last_announcement:
            self._last_announcement = announcement
            event_type = getattr(QtGui, "QAccessibleAnnouncementEvent", None)
            try:
                if event_type is not None:
                    QAccessible.updateAccessibility(event_type(self, announcement))
                else:
                    QAccessible.updateAccessibility(
                        QAccessibleEvent(
                            self, QAccessible.Event.DescriptionChanged
                        )
                    )
            except (RuntimeError, TypeError):
                pass
        if visible and self._action_kind == "retry" and self.isVisible():
            self._action.setFocus(Qt.FocusReason.OtherFocusReason)

    def _action_description(self, label: str, detail: str) -> str:
        """Give every visible action a useful, action-specific label."""
        if self._action_kind == "invite":
            return "Copies the complete invitation link to your clipboard."
        if self._action_kind == "retry":
            return f"Retries the current session step. {detail}".strip()
        return f"{label}. {detail}".strip()

    def _emit_action(self) -> None:
        self.action_requested.emit(self._action_kind)
        if self._action_kind == "retry":
            self.retry_requested.emit()
        elif self._action_kind == "invite":
            self.invite_requested.emit()

    def input_text(self) -> str:
        """Return inline setup text only to the controller that owns it."""

        return self._input.text()

    def focus_input(self) -> None:
        if self._input.isVisible():
            self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_secondary_action(self) -> None:
        self.secondary_action_requested.emit(self._secondary_action_kind)

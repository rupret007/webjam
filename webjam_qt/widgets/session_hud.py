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

    invite_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionHud")
        self.setAccessibleName("Session readiness")
        self._last_announcement = ""
        self._invite_available = False
        self._invite_url = ""

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
        status_layout.addWidget(self._status)
        status_layout.addWidget(self._detail)
        layout.addLayout(status_layout)
        layout.addStretch(1)

        self._invite = QLineEdit()
        self._invite.setObjectName("SessionInviteLink")
        self._invite.setReadOnly(True)
        self._invite.setAccessibleName("Private invitation status")
        self._invite.setAccessibleDescription(
            "A private invitation is ready. Use Copy Invite to share it."
        )
        self._invite.setMinimumWidth(180)
        self._invite.setMaximumWidth(220)
        self._invite.setVisible(False)
        layout.addWidget(self._invite)

        self._action = QPushButton("Copy Invite Link")
        self._action.setObjectName("PrimaryButton")
        self._action.setAccessibleName("Copy invite link")
        self._action_kind = "invite"
        self._action.clicked.connect(self._emit_action)
        self._action.setVisible(False)
        layout.addWidget(self._action)

    def set_state(
        self,
        status: str,
        detail: str,
        *,
        invite_url: str = "",
        action_text: str = "Copy Invite Link",
        action_visible: bool | None = None,
        action_kind: str = "invite",
        ready: bool = False,
    ) -> None:
        self._status.setText(str(status))
        self._detail.setText(str(detail))
        self._invite_url = str(invite_url or "")
        self._invite.setText("Private invite ready" if invite_url else "")
        self._invite.setCursorPosition(0)
        self._invite.setToolTip(
            "Use Copy Invite to share this private invitation."
            if invite_url
            else ""
        )
        self._invite_available = bool(invite_url)
        self._sync_invite_visibility()
        visible = bool(invite_url) if action_visible is None else bool(action_visible)
        self._action.setText(str(action_text))
        self._action.setAccessibleName(str(action_text))
        self._action.setAccessibleDescription(
            "Copies the complete invitation link to your clipboard."
            if invite_url else str(detail)
        )
        self._action.setToolTip(
            "Copy the private invitation to your clipboard."
            if invite_url
            else str(detail)
        )
        self._action_kind = str(action_kind)
        self._action.setVisible(visible)
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

    def invite_url(self) -> str:
        """Return the private value to the controller, never to rendered UI."""

        return self._invite_url

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_invite_visibility()

    def _sync_invite_visibility(self) -> None:
        # Wide layouts show only a non-secret readiness confirmation.
        # Narrow rehearsal windows keep the single Copy Invite control instead
        # of squeezing the session status or introducing horizontal scrolling.
        self._invite.setVisible(self._invite_available and self.width() >= 900)

    def _emit_action(self) -> None:
        if self._action_kind == "retry":
            self.retry_requested.emit()
        else:
            self.invite_requested.emit()

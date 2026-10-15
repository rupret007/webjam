"""One plain-language answer to: are we ready and what happens next?"""

from __future__ import annotations

from PySide6 import QtGui
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.host_startup_checklist import SOUND_ISNT_RIGHT_LABEL
from webjam_qt.theme.tokens import Space

INVITE_COPIED_NOTICE = (
    "Copied. Send the whole message to your friend. Keep this window open."
)
WIFI_NEEDED_NOTICE = "Connect this Mac to Wi-Fi, then try again."


class SessionHud(QFrame):
    """Compact session truth with an explicit, credential-safe copy action."""

    # New callers can handle every HUD action through one semantic signal.
    # The two no-argument signals remain for the controller paths that already
    # depend on them.
    action_requested = Signal(str)
    secondary_action_requested = Signal(str)
    invite_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionHud")
        self.setAccessibleName("Session readiness")
        self._last_announcement = ""
        self._last_accessible_description = ""
        self._checklist_plain = ""
        self._invite_available = False
        self._sound_action_when_open = False

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
        self._sound_help = QToolButton()
        self._sound_help.setObjectName("SessionHudDisclosure")
        self._sound_help.setText(SOUND_ISNT_RIGHT_LABEL)
        self._sound_help.setCheckable(True)
        self._sound_help.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._sound_help.setArrowType(Qt.ArrowType.RightArrow)
        self._sound_help.setAccessibleName(SOUND_ISNT_RIGHT_LABEL)
        self._sound_help.setVisible(False)
        self._sound_help.toggled.connect(self._set_sound_help_visible)
        self._sound_help_body = QLabel()
        self._sound_help_body.setObjectName("SessionHudChecklistNote")
        self._sound_help_body.setWordWrap(True)
        self._sound_help_body.setTextFormat(Qt.TextFormat.RichText)
        self._sound_help_body.setVisible(False)
        self._checklist_note = QLabel()
        self._checklist_note.setObjectName("SessionHudChecklistNote")
        self._checklist_note.setWordWrap(True)
        self._checklist_note.setVisible(False)
        self._notice = QLabel()
        self._notice.setObjectName("SessionHudNotice")
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._input = QLineEdit()
        self._input.setObjectName("SessionHudInput")
        self._input.setVisible(False)
        status_layout.addWidget(self._status)
        status_layout.addWidget(self._detail)
        status_layout.addWidget(self._sound_help)
        status_layout.addWidget(self._sound_help_body)
        status_layout.addWidget(self._checklist_note)
        status_layout.addWidget(self._notice)
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
        checklist_current_step: int | None = None,
        checklist_detail_rich: str = "",
        checklist_note: str = "",
        sound_help_plain: str = "",
        sound_help_rich: str = "",
        sound_help_note: str = "",
    ) -> None:
        retry_was_available = self._action_kind == "retry" and not self._action.isHidden()
        self._status.setText(str(status))
        plain_detail = str(detail)
        sound_plain = str(sound_help_plain or "").strip()
        help_open = bool(sound_plain) and self._sound_help.isChecked()
        if sound_plain:
            self.setProperty("currentStep", 0)
            self._checklist_plain = sound_plain
            self._detail.setTextFormat(Qt.TextFormat.PlainText)
            self._detail.setText(plain_detail)
            self._sound_help.setVisible(True)
            self._sound_help_body.setText(str(sound_help_rich or sound_plain))
            note = str(sound_help_note or checklist_note or "").strip()
            self._checklist_note.setText(note)
        elif checklist_current_step in {1, 2, 3}:
            self._sound_help.setVisible(False)
            self._sound_help.setChecked(False)
            self._sound_help_body.clear()
            self._sound_help_body.setVisible(False)
            self.setProperty("currentStep", int(checklist_current_step))
            self._checklist_plain = plain_detail
            rich = str(checklist_detail_rich or plain_detail)
            self._detail.setTextFormat(Qt.TextFormat.RichText)
            self._detail.setText(rich)
            note = str(checklist_note or "").strip()
            self._checklist_note.setText(note)
            self._checklist_note.setVisible(bool(note))
        else:
            self._sound_help.setVisible(False)
            self._sound_help.setChecked(False)
            self._sound_help_body.clear()
            self._sound_help_body.setVisible(False)
            self.setProperty("currentStep", 0)
            self._checklist_plain = ""
            self._detail.setTextFormat(Qt.TextFormat.PlainText)
            self._detail.setText(plain_detail)
            self._checklist_note.clear()
            self._checklist_note.setVisible(False)
        self._detail.setVisible(bool(self._detail.text()))
        self._invite_available = bool(invite_available)
        self._action_kind = str(action_kind).strip().lower() or "primary"
        default_visible = (
            self._invite_available if self._action_kind == "invite" else True
        )
        visible = default_visible if action_visible is None else bool(action_visible)
        self._sound_action_when_open = bool(sound_plain) and visible
        if sound_plain:
            visible = visible and help_open
        action_label = str(action_text)
        # A literal ampersand is part of the musician-facing label, not an
        # accidental keyboard mnemonic.  Keep the accessible name unescaped.
        self._action.setText(action_label.replace("&", "&&"))
        self._action.setAccessibleName(action_label)
        action_description = self._action_description(action_label, plain_detail)
        self._action.setAccessibleDescription(action_description)
        self._action.setToolTip(action_description)
        self._action.setVisible(visible)
        if sound_plain:
            self._set_sound_help_visible(help_open)
        secondary_label = str(secondary_action_text or "")
        self._secondary_action_kind = (
            str(secondary_action_kind or "").strip().lower() or "secondary"
        )
        self._secondary_action.setText(secondary_label.replace("&", "&&"))
        self._secondary_action.setAccessibleName(secondary_label)
        self._secondary_action.setAccessibleDescription(
            f"{secondary_label}. {plain_detail}".strip()
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
        accessible = str(status)
        if plain_detail:
            accessible = f"{accessible}. {plain_detail}"
        if sound_plain:
            accessible = f"{accessible} {SOUND_ISNT_RIGHT_LABEL}".strip()
        note_for_access = str(sound_help_note or checklist_note or "").strip()
        if note_for_access and (not sound_plain or help_open):
            accessible = f"{accessible} {note_for_access}"
        if accessible != self._last_accessible_description:
            self.setAccessibleDescription(accessible)
            self._last_accessible_description = accessible
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        announcement = accessible
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
        # Bring a newly offered recovery to the keyboard once. Recurring
        # session updates must not pull focus away from notes or setup input.
        if (visible and self._action_kind == "retry"
                and not retry_was_available and self.isVisible()):
            self._action.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_notice(self, text: str) -> None:
        """Keep Copy Invite / Wi-Fi guidance in the HUD, not a vanishing bar."""

        notice = str(text or "").strip()
        self._notice.setText(notice)
        self._notice.setVisible(bool(notice))
        if notice:
            accessible = f"{self._last_accessible_description} {notice}".strip()
            if accessible != self._last_accessible_description:
                self.setAccessibleDescription(accessible)
                self._last_accessible_description = accessible

    def notice_text(self) -> str:
        if self._notice.isHidden():
            return ""
        return self._notice.text()

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

    def _set_sound_help_visible(self, visible: bool) -> None:
        """Jamulus/interface steps stay behind Sound isn't right until asked."""

        open_help = bool(visible)
        self._sound_help.setChecked(open_help)
        self._sound_help.setArrowType(
            Qt.ArrowType.DownArrow if open_help else Qt.ArrowType.RightArrow
        )
        self._sound_help_body.setVisible(open_help and bool(self._sound_help_body.text()))
        note = self._checklist_note.text().strip()
        self._checklist_note.setVisible(open_help and bool(note))
        if self._sound_action_when_open:
            self._action.setVisible(open_help)

    def checklist_lines(self) -> tuple[str, ...]:
        """Return numbered checklist lines when the HUD is in checklist mode."""

        if not self._sound_help.isHidden() and not self._sound_help.isChecked():
            return ()
        return tuple(
            line for line in self._checklist_plain.splitlines() if line.strip()
        )

    def input_text(self) -> str:
        """Return inline setup text only to the controller that owns it."""

        return self._input.text()

    def focus_input(self) -> None:
        if self._input.isVisible():
            self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_secondary_action(self) -> None:
        self.secondary_action_requested.emit(self._secondary_action_kind)

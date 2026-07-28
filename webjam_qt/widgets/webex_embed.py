"""Lightweight external-Webex status card for the live workspace.

WebJam never embeds, authenticates, joins, monitors, or controls a Webex
meeting.  The native Webex application or system browser owns sign-in, media
devices, meeting membership, and leave state.  This widget only presents the
truthful result of handing a trusted meeting link to the operating system.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.webex_url import is_allowed_webex_url
from webjam_qt.theme.tokens import Space

LOGGER = logging.getLogger("webjam.qt.webex_embed")


class WebexEmbed(QFrame):
    """External launch card retaining the former widget's caller interface."""

    meeting_state_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(64)
        self.setMaximumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._audio_mode = "talkback"

        self._title_label = QLabel("Webex conversation")
        self._title_label.setObjectName("WebexEmbedTitle")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._mode_label = QLabel()
        self._mode_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._mode_label.setWordWrap(True)
        self._mode_label.setObjectName("BodyLabel")

        self._status_label = QLabel("Webex has not been opened from WebJam yet.")
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("WebexStatusLabel")
        self._status_label.setAccessibleName("Webex launch status")
        self._status_label.setAccessibleDescription(self._status_label.text())

        self._fallback_btn = QPushButton("Open Webex")
        self._fallback_btn.setObjectName("GhostButton")
        self._fallback_btn.setAccessibleName("Open Webex externally")
        self._fallback_btn.setAccessibleDescription(
            "Open the configured meeting in the native Webex app or browser."
        )

        text_column = QVBoxLayout()
        text_column.setSpacing(0)
        text_column.addWidget(self._title_label)
        text_column.addWidget(self._mode_label)
        text_column.addWidget(self._status_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.LG)
        layout.addLayout(text_column, stretch=1)
        layout.addWidget(
            self._fallback_btn, alignment=Qt.AlignmentFlag.AlignVCenter
        )
        self._render_audio_guidance()

    def fallback_button(self) -> QPushButton:
        """Return the external Webex launch button."""

        return self._fallback_btn

    def set_launch_status(self, status: str) -> None:
        """Show external-launch truth without implying meeting membership."""

        descriptions = {
            "Not opened": "Webex has not been opened from WebJam yet.",
            "Opening…": "Opening Webex externally…",
            "Opened externally": "Opened externally—finish joining in Webex.",
            "Open failed": "Webex could not be opened. Retry or check Settings.",
        }
        description = descriptions.get(status, str(status))
        self._status_label.setText(description)
        self._status_label.setAccessibleDescription(description)
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(
                    self._status_label,
                    QAccessible.Event.DescriptionChanged,
                )
            )
        except (RuntimeError, TypeError):
            # Some headless and teardown paths no longer have an accessibility
            # backend. The visible and semantic text is still updated.
            pass
        button_text = (
            "Opening…"
            if status == "Opening…"
            else "Open Again"
            if status == "Opened externally"
            else "Open Webex"
        )
        self._fallback_btn.setText(button_text)
        self._fallback_btn.setEnabled(status != "Opening…")
        self._fallback_btn.setAccessibleName(button_text)
        self._fallback_btn.setAccessibleDescription(description)

    def set_audio_mode(self, mode: str) -> None:
        """Render concise role guidance for the selected Webex audio mode."""

        self._audio_mode = (
            mode
            if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )
        self._render_audio_guidance()

    def load_meeting(self, meeting_url: str, **_unused: object) -> bool:
        """Compatibility guard: embedded meetings are no longer supported."""

        if not is_allowed_webex_url(meeting_url):
            LOGGER.warning("Webex launch card refused an untrusted meeting URL")
        else:
            LOGGER.warning(
                "Embedded Webex is retired; use the external launch action"
            )
        self.meeting_state_changed.emit("error")
        return False

    def leave_meeting(self) -> None:
        """Compatibility no-op because WebJam does not own external Webex."""

    def shutdown(self) -> None:
        """Compatibility no-op; this card owns no browser or media process."""

    def _render_audio_guidance(self) -> None:
        titles = {
            "talkback": "Webex conversation",
            "video_only": "Webex video",
            "audience_bridge": "Webex audience feed",
        }
        guidance = {
            "talkback": (
                "Keep Webex muted while playing. To speak, mute your audio "
                "interface or end the WebJam session first."
            ),
            "video_only": (
                "Join Webex without computer audio; music stays in Jamulus."
            ),
            "audience_bridge": (
                "Advanced audience feed: musicians must disconnect Webex audio "
                "to prevent delayed duplicate music."
            ),
        }
        self._title_label.setText(titles[self._audio_mode])
        self._mode_label.setText(guidance[self._audio_mode])

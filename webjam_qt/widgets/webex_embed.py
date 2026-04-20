"""
WebexEmbed — placeholder for the Phase 3 embedded Webex pane.

Phase 3 will replace this with a ``QWebEngineView`` running the Webex
Embedded Apps / Meetings Web SDK. The ApplicationController already treats
this as the authoritative meeting surface, so the swap will be localized.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class WebexEmbed(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        title = QLabel("Webex lives here — Phase 3")
        title.setObjectName("WebexEmbedTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        body = QLabel(
            "The Webex Meetings Web SDK will render inside this panel.\n"
            "Participant video tiles reconcile with Jamulus channels above.\n"
            "For now, Join Video opens Webex in your browser."
        )
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)

        self._fallback_button = QPushButton("Open Webex in browser")
        self._fallback_button.setObjectName("GhostButton")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addWidget(self._fallback_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def fallback_button(self) -> QPushButton:
        return self._fallback_button

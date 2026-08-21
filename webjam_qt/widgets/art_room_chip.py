"""Art's presence in the room chrome: one chip, or nothing.

Mirrors how the Shared Track deck sits in the session strip -- a compact
surface that is simply absent until the room has something to say. The
difference is that this one holds a single control, because a room only ever
needs to say one thing about Art at a time.

The chip is not a new kind of thing. It is the same status-is-the-label
control the Art panels already use, so "Install Drawpile" and "Shared canvas"
read as one control at different moments rather than as two features.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from core.art_room_presence import (
    ABSENT,
    ArtPresenceTarget,
    ArtPresenceTone,
    ArtRoomPresence,
)
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.status_chip import StatusChip

#: Short enough to leave a conversation beside it. ADR 0004 asks Art's chrome
#: to stay narrow so meeting faces can sit next to the room.
MAX_WIDTH = 232

#: The chip carries its own style name in the strip. It is the same control as
#: a panel's chip, worn quieter.
CHIP_OBJECT_NAME = "ArtRoomChipAction"


class ArtRoomChip(QFrame):
    """The room's one-line answer about its canvas or its video."""

    #: Carries the panel to open: "canvas" or "video". The widget opens
    #: nothing itself, so it can never take focus on its own.
    open_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArtRoomDeck")
        self.setAccessibleName("Art room status")
        self.setMaximumWidth(MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.XS, 0, Space.XS, 0)
        layout.setSpacing(Space.XS)
        self._chip = StatusChip(self)
        # Styled under its own name rather than as a descendant of the deck:
        # Qt resolves a plain id selector reliably, and this chip genuinely
        # wants different weight from a panel's primary action. A strip that
        # already has one loud control must not gain a second.
        self._chip.setObjectName(CHIP_OBJECT_NAME)
        self._chip.setMinimumHeight(34)
        self._chip.setMaximumWidth(MAX_WIDTH - Space.XS * 2)
        self._chip.clicked.connect(self._on_clicked)
        layout.addWidget(self._chip)
        self._presence = ABSENT
        self.setVisible(False)

    @property
    def presence(self) -> ArtRoomPresence:
        return self._presence

    @property
    def chip(self) -> StatusChip:
        return self._chip

    def set_presence(self, presence: ArtRoomPresence) -> None:
        """Render one line, or leave the room chrome alone entirely.

        Called on a timer, so repainting and announcing are both gated on the
        line actually changing. A screen reader hearing "shared canvas" once a
        second would be worse than silence.
        """

        changed = (
            presence.label != self._presence.label
            or presence.description != self._presence.description
            or presence.tone is not self._presence.tone
        )
        self._presence = presence
        if not presence.offered:
            self._chip.withdraw()
            self.setVisible(False)
            self.setAccessibleDescription("")
            return
        if not changed:
            return
        tone = (
            StatusChip.RECOVERY
            if presence.tone is ArtPresenceTone.ATTENTION
            else StatusChip.PRIMARY
        )
        self._chip.offer(presence.label, presence.description, tone=tone)
        self.setAccessibleDescription(presence.description)
        self.setVisible(True)
        # A canvas appearing in the room is worth hearing about, and the room
        # is where someone who cannot see the chip finds out.
        self._announce()

    def _announce(self) -> None:
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(
                    self._chip, QAccessible.Event.NameChanged
                )
            )
        except (RuntimeError, TypeError):
            # Headless and teardown paths have no accessibility backend. The
            # visible and semantic text is still correct.
            pass

    def _on_clicked(self) -> None:
        target = self._presence.target
        if target is ArtPresenceTarget.NONE:
            return
        self.open_requested.emit(target.value)


__all__ = ["CHIP_OBJECT_NAME", "MAX_WIDTH", "ArtRoomChip"]

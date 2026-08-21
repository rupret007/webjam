"""One control whose label is the status and whose click is the verb.

ADR 0002 settled that a surface explains the next action and does not add a
competing button. A chip is how that reads in Art: at any moment a panel offers
exactly one thing to press, and the thing it says is the thing pressing it will
do -- "Open shared canvas", "Install Drawpile", "Share a video".

When there is nothing to do, a chip does not sit there greyed out. It leaves,
and the panel's status line carries the truth on its own. A disabled control is
a small taunt repeated every time someone looks at it, and an empty room that
someone chose is a finished state rather than a broken one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

#: The chip is the panel's primary action and matches the launch buttons, so a
#: pointer or a thumb lands on it the same way in both places.
CHIP_MIN_HEIGHT = 52


class StatusChip(QPushButton):
    """The single primary action a panel offers right now, or nothing."""

    #: The real verb: open the canvas, share the video, make the image.
    PRIMARY = "primary"
    #: A way out of a fail-closed state, such as installing what is missing.
    #: Deliberately not a warning colour: nothing is broken, something is
    #: absent, and those read differently to a person.
    RECOVERY = "recovery"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.setMinimumHeight(CHIP_MIN_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # A panel's chip is its primary action, but the panel is not a dialog
        # to accept, so Return must not fire it from another control.
        self.setAutoDefault(False)
        self.setDefault(False)
        self.withdraw()

    def offer(
        self, label: str, description: str, *, tone: str = PRIMARY
    ) -> None:
        """Show one verb, and say the same thing to a screen reader."""

        self.setText(label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.setProperty("tone", tone)
        self.setEnabled(True)
        self.setVisible(True)
        self._restyle()

    def withdraw(self) -> None:
        """Leave, rather than sit here greyed out with nothing to offer."""

        self.setVisible(False)
        self.setText("")
        self.setAccessibleName("")
        self.setAccessibleDescription("")
        self.setToolTip("")

    @property
    def offered(self) -> bool:
        return not self.isHidden()

    def _restyle(self) -> None:
        # Qt only re-evaluates property selectors when the style is refreshed.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)


class QuietAction(QPushButton):
    """A second, deliberately lesser control beside a chip.

    Stopping a share, hiding a video, or editing an existing file are real
    needs that are not the point of the panel. They are available and visibly
    not the primary thing, so a person never has to choose between two equal
    calls to action.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QuietAction")
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setVisible(False)

    def offer(self, label: str, description: str) -> None:
        self.setText(label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.setEnabled(True)
        self.setVisible(True)

    def withdraw(self) -> None:
        self.setVisible(False)

    @property
    def offered(self) -> bool:
        return not self.isHidden()


__all__ = ["CHIP_MIN_HEIGHT", "QuietAction", "StatusChip"]

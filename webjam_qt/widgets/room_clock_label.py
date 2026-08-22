"""One calm line telling the room where it is.

This is a readout, not a control. There is nothing to press, because the room
clock has exactly one owner and a painter reading it is not that owner. It
appears wherever a maker needs the pulse without leaving what they are doing.

When there is no clock, it says so and stays out of the way rather than
showing a hopeful zero.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from core.room_clock import RoomClockSource, RoomClockView
from webjam_qt.theme.tokens import Space


class RoomClockLabel(QFrame):
    """Render a room clock view, and nothing else."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RoomClock")
        self.setAccessibleName("Room clock")
        # A readout takes no focus and no clicks: it is not a control, and
        # letting it look like one would imply a painter can move the room.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        layout.setSpacing(0)

        self._headline = QLabel("")
        self._headline.setObjectName("RoomClockHeadline")
        self._detail = QLabel("")
        self._detail.setObjectName("RoomClockDetail")
        self._detail.setWordWrap(True)
        layout.addWidget(self._headline)
        layout.addWidget(self._detail)

        self.set_view(RoomClockView())

    def set_view(self, view: RoomClockView) -> None:
        self._headline.setText(view.headline)
        self._detail.setText(view.detail)
        # The source is exposed as a property so the theme can distinguish a
        # musical pulse from a file offset without the copy having to shout it.
        self.setProperty(
            "clock",
            view.source.value
            if isinstance(view.source, RoomClockSource)
            else "none",
        )
        self.setProperty("stale", bool(view.stale))
        announced = f"{view.headline}. {view.detail}"
        self.setAccessibleDescription(announced)
        self._headline.setAccessibleName(view.headline)
        self._detail.setAccessibleDescription(view.detail)
        self._restyle()

    def _restyle(self) -> None:
        # Qt only re-evaluates property selectors when the style is refreshed.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)


__all__ = ["RoomClockLabel"]

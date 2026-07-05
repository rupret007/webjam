"""SideRail — narrow left rail with view-switching buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


@dataclass(frozen=True)
class RailItem:
    key: str
    label: str
    glyph: str   # emoji/symbol placeholder; icon fonts come in Phase 6


class SideRail(QFrame):
    """
    View-toggle rail. Emits ``view_changed(key)`` when user picks a different view.

    Keeps view-switching decoupled from layout; the main window decides what
    to show in the primary area.
    """

    view_changed = Signal(str)

    RAIL_WIDTH = 68

    DEFAULT_ITEMS: tuple[RailItem, ...] = (
        RailItem(key="stage", label="Stage", glyph="🎛"),
        RailItem(key="mixer", label="Mixer", glyph="🎚"),
        RailItem(key="chat", label="Chat", glyph="💬"),
        RailItem(key="roles", label="Roles", glyph="🎭"),
        RailItem(key="canvas", label="Canvas", glyph="📝"),
        RailItem(key="takes", label="Takes", glyph="⏺"),
        RailItem(key="settings", label="Settings", glyph="⚙"),
    )

    def __init__(
        self,
        items: Optional[tuple[RailItem, ...]] = None,
        *,
        initial_key: str = "stage",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SideRail")
        self.setFixedWidth(self.RAIL_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._items = items or self.DEFAULT_ITEMS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.MD, Space.SM, Space.MD)
        layout.setSpacing(Space.XS)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for item in self._items:
            button = QToolButton(self)
            button.setObjectName("SideRailButton")
            button.setText(f"{item.glyph}\n{item.label}")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("railKey", item.key)
            button.clicked.connect(self._on_clicked)
            if item.key == initial_key:
                button.setChecked(True)
            self._group.addButton(button)
            layout.addWidget(button)

        layout.addStretch(1)

    def current_key(self) -> str:
        """Return the key of the currently checked button, or empty string."""
        for btn in self._group.buttons():
            if btn.isChecked():
                return str(btn.property("railKey") or "")
        return ""

    def set_active_key(self, key: str) -> None:
        """Programmatically select the button matching ``key``."""
        for btn in self._group.buttons():
            if btn.property("railKey") == key:
                btn.setChecked(True)
                return

    def _on_clicked(self) -> None:
        sender = self.sender()
        if sender is None:
            return
        key = sender.property("railKey")
        if key:
            self.view_changed.emit(str(key))

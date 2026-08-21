"""SideRail — narrow left rail with view-switching buttons."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.rail_icons import make_rail_icon
from webjam_qt.theme.tokens import Space


@dataclass(frozen=True)
class RailItem:
    key: str
    label: str
    glyph: str
    utility: bool = False


class SideRail(QFrame):
    """
    View-toggle rail. Emits ``view_changed(key)`` when user picks a different view.

    Keeps view-switching decoupled from layout; the main window decides what
    to show in the primary area.
    """

    view_changed = Signal(str)

    RAIL_WIDTH = 68

    DEFAULT_ITEMS: tuple[RailItem, ...] = (
        RailItem(key="stage", label="Live", glyph="LIVE"),
        RailItem(key="canvas", label="Notes", glyph="NOTE"),
        RailItem(key="takes", label="Studio", glyph="TAKE"),
        RailItem(key="settings", label="Settings", glyph="SET", utility=True),
    )

    def __init__(
        self,
        items: tuple[RailItem, ...] | None = None,
        *,
        initial_key: str = "stage",
        parent: QWidget | None = None,
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

        for index, item in enumerate(self._items):
            if item.utility and index and not self._items[index - 1].utility:
                layout.addSpacing(Space.LG)
            button = QToolButton(self)
            button.setObjectName("SideRailButton")
            button.setText(item.label)
            button.setCheckable(True)
            icon = make_rail_icon(item.key)
            if icon.isNull():
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            else:
                button.setIcon(icon)
                button.setIconSize(QSize(20, 20))
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextUnderIcon
                )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("railKey", item.key)
            button.setProperty("utility", "true" if item.utility else "false")
            button.setAccessibleName(
                f"Open {item.label}" if item.utility else f"Show {item.label} workspace"
            )
            button.setToolTip(
                f"Open {item.label}" if item.utility else f"Switch to the {item.label} workspace"
            )
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

    def trigger(self, key: str) -> None:
        """Activate a rail destination through the same path as a click."""
        for btn in self._group.buttons():
            if btn.property("railKey") == key:
                btn.click()
                return

    def _on_clicked(self) -> None:
        sender = self.sender()
        if sender is None:
            return
        key = sender.property("railKey")
        if key:
            self.view_changed.emit(str(key))

"""Resolved Jamulus name with a Change action after Host or Join is chosen."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from core.jamulus_name import JamulusNameError, validate_jamulus_name
from webjam_qt.theme.tokens import Space

_APPEARING_AS_PREFIX = "You'll appear as"


def format_appearing_as_line(value: str) -> tuple[str, str]:
    """Return visible copy and accessible description using the mixer wrap."""

    try:
        validated = validate_jamulus_name(value)
    except JamulusNameError as exc:
        stripped = str(value or "").strip()
        visible = f"{_APPEARING_AS_PREFIX} '{stripped}'" if stripped else _APPEARING_AS_PREFIX
        return visible, f"{visible}. {exc}".strip()
    if validated.second_line:
        visible = (
            f"{_APPEARING_AS_PREFIX} '{validated.first_line}'\n"
            f"'{validated.second_line}'"
        )
        accessible = (
            f"{_APPEARING_AS_PREFIX} {validated.first_line} "
            f"{validated.second_line} on two mixer lines."
        )
    else:
        visible = f"{_APPEARING_AS_PREFIX} '{validated.first_line}'"
        accessible = f"{_APPEARING_AS_PREFIX} {validated.first_line}."
    return visible, accessible


class MusicianIdentityLine(QWidget):
    """One line of resolved identity plus Change."""

    change_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        label_object_name: str = "LaunchIdentityLine",
        change_object_name: str = "LaunchIdentityChange",
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM)

        self._label = QLabel()
        self._label.setObjectName(label_object_name)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._label.setTextFormat(Qt.TextFormat.PlainText)

        self._change = QPushButton("Change")
        self._change.setObjectName(change_object_name)
        self._change.setAccessibleName("Change")
        self._change.setAccessibleDescription(
            "Open WebJam Settings to edit your name."
        )
        self._change.clicked.connect(self.change_requested.emit)

        layout.addStretch(1)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._change, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

    def set_resolved_name(self, value: str) -> None:
        visible, accessible = format_appearing_as_line(value)
        self._label.setText(visible)
        self._label.setAccessibleName(accessible)
        self._label.setAccessibleDescription(accessible)

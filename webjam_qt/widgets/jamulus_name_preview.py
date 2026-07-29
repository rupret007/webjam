"""Reusable accessible preview for Jamulus's 8+8 musician-name layout."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit

from core.jamulus_name import (
    JAMULUS_NAME_HELP,
    JamulusNameError,
    validate_jamulus_name,
)


class JamulusNamePreview(QLabel):
    """Show the native mixer wrap without changing the entered name."""

    def __init__(self, editor: QLineEdit, *, compact: bool = False) -> None:
        super().__init__(editor.parentWidget())
        self._compact = bool(compact)
        self.setObjectName("JamulusNamePreview")
        self.setAccessibleName("Jamulus musician-name preview")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        editor.setAccessibleDescription(JAMULUS_NAME_HELP)
        editor.textChanged.connect(self.update_name)
        self.update_name(editor.text())

    def update_name(self, value: str) -> None:
        try:
            name = validate_jamulus_name(value)
        except JamulusNameError as exc:
            detail = str(exc)
            self.setText(
                detail
                if self._compact
                else f"{JAMULUS_NAME_HELP}\n{detail}"
            )
            self.setAccessibleDescription(f"{JAMULUS_NAME_HELP} {detail}")
            return
        preview = name.preview.replace("\n", " / ")
        if self._compact:
            layout = "two lines" if name.wraps else "one line"
            text = f"Jamulus mixer: {preview} ({layout})"
        else:
            text = f"{JAMULUS_NAME_HELP}\nMixer preview: {preview}"
        self.setText(text)
        self.setAccessibleDescription(
            f"{JAMULUS_NAME_HELP} Mixer preview: {name.preview}"
        )

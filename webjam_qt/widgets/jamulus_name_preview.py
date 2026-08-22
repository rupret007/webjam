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
    """Show the native mixer wrap without changing the entered name.

    ``plain_words`` drops the component's name from the visible text. The
    setup surfaces are explicitly about configuring Jamulus and say so, but
    the first screen someone ever sees is about what they are making, and a
    piece of infrastructure has no business introducing itself there.
    """

    def __init__(
        self,
        editor: QLineEdit,
        *,
        compact: bool = False,
        plain_words: bool = False,
    ) -> None:
        super().__init__(editor.parentWidget())
        self._compact = bool(compact)
        self._plain_words = bool(plain_words)
        self.setObjectName("JamulusNamePreview")
        self.setAccessibleName(
            "Name preview" if plain_words else "Jamulus musician-name preview"
        )
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
            label = "Others see" if self._plain_words else "Jamulus mixer"
            text = f"{label}: {preview} ({layout})"
        else:
            text = f"{JAMULUS_NAME_HELP}\nMixer preview: {preview}"
        self.setText(text)
        self.setAccessibleDescription(
            f"{JAMULUS_NAME_HELP} Mixer preview: {name.preview}"
        )

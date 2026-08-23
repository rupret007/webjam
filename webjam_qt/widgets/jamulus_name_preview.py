"""Reusable accessible preview for Jamulus's 8+8 musician-name layout."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit

from core.jamulus_name import (
    JAMULUS_NAME_HELP,
    JamulusNameError,
    validate_jamulus_name,
)

# First-screen copy. The mixer wrap is still the rule; the component does not
# introduce itself before someone has chosen what they are making.
PLAIN_NAME_HELP = (
    "Others see up to 16 characters and wrap after 8. "
    "Use a short stage name for one line."
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
            "What others see" if plain_words else "Jamulus musician-name preview"
        )
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        editor.setAccessibleDescription(self._help)
        editor.textChanged.connect(self.update_name)
        self.update_name(editor.text())

    @property
    def _help(self) -> str:
        return PLAIN_NAME_HELP if self._plain_words else JAMULUS_NAME_HELP

    def update_name(self, value: str) -> None:
        try:
            name = validate_jamulus_name(value)
        except JamulusNameError as exc:
            detail = str(exc)
            self.setText(
                detail
                if self._compact
                else f"{self._help}\n{detail}"
            )
            self.setAccessibleDescription(f"{self._help} {detail}")
            return
        preview = name.preview.replace("\n", " / ")
        if self._compact:
            layout = "two lines" if name.wraps else "one line"
            label = "Others see" if self._plain_words else "Jamulus mixer"
            text = f"{label}: {preview} ({layout})"
        else:
            text = f"{self._help}\nMixer preview: {preview}"
        self.setText(text)
        if self._plain_words:
            self.setAccessibleDescription(f"{self._help} Others see: {name.preview}")
        else:
            self.setAccessibleDescription(
                f"{self._help} Mixer preview: {name.preview}"
            )

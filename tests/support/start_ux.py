"""First-screen ten-second contract: banned engine words fail CI.

The live door is Host / Join (Music) or two cards then Host / Join (Art).
Component and vendor names belong inside the room, never on the door. This
module is the one list the start-UX tests harvest against, so a banned word
cannot return on one profile while another test still looks green.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import QComboBox, QWidget


# Substrings: a first-screen phrase must not contain these at all.
FIRST_SCREEN_BANNED_PHRASES = (
    "drawpile",
    "krita",
    "procreate",
    "clip studio",
    "jamulus",
    "webex",
    "moises",
    "music ai",
    "byok",
    "comfyui",
    "host-clocked",
    "studio visit",
    "stems",
)

# Whole words: "already" is fine; "Ready" / "API" / "Preview" are not.
FIRST_SCREEN_BANNED_WORDS = ("preview", "ready", "api")

_WIDGET_TEXT_ATTRS = (
    "text",
    "description",
    "placeholderText",
    "accessibleName",
    "accessibleDescription",
    "toolTip",
    "whatsThis",
    "statusTip",
)


def harvest_spoken_page(
    page: QWidget, *, exclude: tuple[QWidget, ...] = ()
) -> str:
    """Harvest what a person can read or hear on one launch page.

    Hidden fail-closed recovery (the Windows Jamulus installer) stays out
    because it is not on the door. Hidden combo items stay out too: Podcast
    and Review are not first-screen rooms, and harvesting them while the
    picker is off would bury Art again.
    """

    skipped = {widget for widget in exclude if widget is not None}
    parts: list[str] = []
    window_title = page.windowTitle() if hasattr(page, "windowTitle") else ""
    if window_title:
        parts.append(str(window_title))
    for widget in page.findChildren(QWidget):
        if widget in skipped or not widget.isVisibleTo(page):
            continue
        for attr in _WIDGET_TEXT_ATTRS:
            getter = getattr(widget, attr, None)
            if callable(getter):
                value = getter()
                if value:
                    parts.append(str(value))
        if isinstance(widget, QComboBox) and widget.isVisibleTo(page):
            parts.extend(widget.itemText(index) for index in range(widget.count()))
    return " ".join(parts).casefold()


def harvest_first_screen(dialog: QWidget) -> str:
    """Harvest the choice page a person actually sees."""

    page = getattr(dialog, "_choice_page", dialog)
    installer = getattr(dialog, "_install_jamulus_button", None)
    more_rooms = getattr(dialog, "_more_rooms_button", None)
    exclude = tuple(
        widget for widget in (installer, more_rooms) if widget is not None
    )
    return harvest_spoken_page(page, exclude=exclude)


def harvest_join_page(dialog: QWidget) -> str:
    """Harvest the join page after someone has already chosen Join."""

    page = getattr(dialog, "_join_page", None)
    if page is None:
        return ""
    return harvest_spoken_page(page)


def assert_no_banned_first_screen_words(spoken: str) -> None:
    """Fail CI when a banned engine word is on the door."""

    lowered = spoken.casefold()
    for phrase in FIRST_SCREEN_BANNED_PHRASES:
        assert phrase not in lowered, phrase
    for word in FIRST_SCREEN_BANNED_WORDS:
        assert not re.search(rf"\b{word}\b", lowered), word

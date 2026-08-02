"""Keep what a control says and what it announces from drifting apart.

A button whose label changes with state -- Play/Pause, Show Take/Show
Studio Export, Checking…/Playing Left, then Right… -- is normally given a
single fixed accessible name when it is built. Assistive technology then
announces that original wording forever, so a musician using VoiceOver is
told about a different action from the one on screen.

Setting both together is one line, but only if there is one call to make.
"""

from __future__ import annotations

from typing import Optional


def set_labeled_action(
    widget,
    label: str,
    *,
    description: Optional[str] = None,
) -> None:
    """Set a control's visible label and announce exactly the same thing.

    ``description`` is optional extra context for assistive technology --
    the consequence of pressing the control, not a restatement of its name.
    """

    text = str(label)
    widget.setText(text)
    # A literal ampersand in a musician-facing label is content, not an
    # accidental mnemonic, so the announced name keeps it unescaped.
    widget.setAccessibleName(text.replace("&&", "&"))
    if description is not None:
        widget.setAccessibleDescription(str(description))

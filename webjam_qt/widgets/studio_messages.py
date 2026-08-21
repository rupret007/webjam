"""One safe boundary between Studio exception text and musician-facing copy.

Studio's validation errors are deliberately specific: "A section-reorder
boundary crosses a region fade." tells a musician exactly what to change, and
losing that detail costs real usability.  Arbitrary ``str(exc)`` must not become
the permanent UI contract though, because some Studio exceptions wrap operating
system failures whose text can carry absolute paths or other private detail.

Displayability is therefore decided by exception *type*, not by a list of
approved sentences that would inevitably drift from the messages the core
modules actually raise.  A second, independent scan then rejects any text that
still looks like a path, address, or opaque token, so a new or reworded core
message can never silently start leaking.  Rejected text degrades to a short
actionable fallback; the full exception always remains in the log.
"""

from __future__ import annotations

from core.studio_comping import StudioCompingError
from core.studio_controller import StudioControllerError
from core.studio_project import StudioProjectError
from core.studio_store import StudioStoreError

# Types whose messages are authored as short, abstract, musician-facing
# sentences.  ``StudioProjectError`` also covers ``StudioSectionError``.
_DISPLAYABLE_EDIT_ERRORS = (
    StudioProjectError,
    StudioCompingError,
    StudioControllerError,
)

# ``StudioStoreError`` is a ``ValueError`` like the types above, but it wraps
# arbitrary filesystem failures, so its text is never displayed.
_UNDISPLAYABLE_EDIT_ERRORS = (StudioStoreError,)

MAX_DETAIL_CHARACTERS = 160
_MAX_TOKEN_CHARACTERS = 32

ARRANGE_EDIT_FALLBACK = (
    "Studio couldn't apply that edit safely. The recorded take is unchanged."
)


def _looks_unsafe(text: str) -> bool:
    """Reject text that resembles a path, address, or opaque secret."""

    # Path separators also catch "https://" style URLs and "~/" home paths.
    if "/" in text or "\\" in text or "@" in text:
        return True
    if any(not character.isprintable() for character in text):
        return True
    return any(len(token) >= _MAX_TOKEN_CHARACTERS for token in text.split())


def safe_detail(exc: BaseException) -> str:
    """Return one bounded, sanitized detail sentence, or "" to use a fallback."""

    if isinstance(exc, _UNDISPLAYABLE_EDIT_ERRORS):
        return ""
    if not isinstance(exc, _DISPLAYABLE_EDIT_ERRORS):
        return ""
    text = " ".join(str(exc).split())
    if not text or _looks_unsafe(text):
        return ""
    if len(text) > MAX_DETAIL_CHARACTERS:
        text = text[: MAX_DETAIL_CHARACTERS - 1].rstrip() + "…"
    if text[-1] not in ".!?…":
        text = f"{text}."
    return text


def arrange_edit_failure_message(exc: BaseException) -> str:
    """Explain a refused Arrange edit without exposing private detail."""

    detail = safe_detail(exc)
    if not detail:
        return ARRANGE_EDIT_FALLBACK
    return (
        f"Studio couldn't apply that edit safely: {detail} "
        "The recorded take is unchanged."
    )


__all__ = [
    "ARRANGE_EDIT_FALLBACK",
    "MAX_DETAIL_CHARACTERS",
    "arrange_edit_failure_message",
    "safe_detail",
]

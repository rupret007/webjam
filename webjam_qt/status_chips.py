"""Pure policy for musician status-bar Video chip labels (no Qt imports)."""

from __future__ import annotations

INFORMATIVE_VIDEO_PROFILES = frozenset({"music", "podcast_voice", "review_rehearsal"})

ACTIVE_VIDEO_STATES = frozenset({"Opening…", "Opened externally", "Open failed"})


def video_chip_label(profile_key: str, state: str, *, has_link: bool) -> str:
    """Return the Video chip label to show, or \"\" to hide the chip."""
    text = str(state or "").strip()
    if profile_key not in INFORMATIVE_VIDEO_PROFILES:
        return text
    if has_link or text in ACTIVE_VIDEO_STATES:
        return text
    return ""

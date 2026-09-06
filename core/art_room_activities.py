"""Keep each offered Art activity reachable without changing its priority.

The strip still shows the single presence chosen by ``art_room_presence``.
The room overview may also offer the other real canvas or video fact. Saved
start preferences can choose a host's first setup action, but cannot invent a
second room activity.
"""

from __future__ import annotations

from dataclasses import replace

from core.art_companion import (
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.art_room_presence import ArtPresenceTarget, ArtRoomPresence, art_room_presence


def art_room_activities(
    projection: ArtCompanionProjection,
    *,
    hosting: bool = False,
    intended_canvas: bool = False,
    intended_video: bool = False,
) -> tuple[ArtRoomPresence, ...]:
    """Return the existing priority presence and at most one other activity."""

    primary = art_room_presence(
        projection, hosting=hosting,
        intended_canvas=intended_canvas, intended_video=intended_video,
    )
    if not primary.offered:
        return ()
    if primary.target is ArtPresenceTarget.CANVAS:
        remaining = replace(projection, canvas=CanvasCompanionState.NONE)
    elif primary.target is ArtPresenceTarget.VIDEO:
        remaining = replace(projection, video=VideoCompanionState.NONE)
    else:
        return ()

    # Reuse the same wording and ranking. No setup intent is allowed to
    # become an extra offer after the primary activity has been removed.
    secondary = art_room_presence(remaining, hosting=hosting)
    if (secondary.offered and secondary.target is not primary.target
            and secondary.target in {ArtPresenceTarget.CANVAS, ArtPresenceTarget.VIDEO}):
        return primary, secondary
    return (primary,)


__all__ = ["art_room_activities"]

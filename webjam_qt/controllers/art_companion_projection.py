"""Derive the companion projection from truth the desktop already owns.

Nothing here is a second source of truth. Each field is read from the
coordinator that already owns it and collapsed into the finite state a paired
panel is allowed to see, so a companion can never disagree with the desktop --
it is looking at the same values through a narrower window.

The revision advances only when the projected view actually changes, which is
what lets a companion bind a command to something it really saw rather than to
a clock.
"""

from __future__ import annotations

from core.art_companion import (
    AiCompanionState,
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)

_CANVAS_STATES = {
    "no_canvas": CanvasCompanionState.NONE,
    "ready": CanvasCompanionState.READY,
    "opened": CanvasCompanionState.OPENING,
    "needs_drawpile": CanvasCompanionState.MISSING_APP,
    "unreadable": CanvasCompanionState.UNREADABLE,
}

_FOLLOW_VIDEO_STATES = {
    "no_video": VideoCompanionState.NONE,
    "hidden": VideoCompanionState.HIDDEN,
    "needs_file": VideoCompanionState.NEEDS_FILE,
    "mismatched_file": VideoCompanionState.MISMATCHED_FILE,
    "file_unavailable": VideoCompanionState.FILE_UNAVAILABLE,
    "local_attention": VideoCompanionState.LOCAL_ATTENTION,
    "host_attention": VideoCompanionState.HOST_ATTENTION,
    "stalled": VideoCompanionState.STALLED,
}

_HOST_VIDEO_STATES = {
    "idle": VideoCompanionState.NONE,
    "ready": VideoCompanionState.READY,
    "playing": VideoCompanionState.PLAYING,
    "paused": VideoCompanionState.PAUSED,
    "failed": VideoCompanionState.HOST_ATTENTION,
    "closed": VideoCompanionState.NONE,
}

_AI_STATES = {
    "not_in_a_room": AiCompanionState.UNAVAILABLE,
    "needs_krita": AiCompanionState.UNAVAILABLE,
    "needs_plugin": AiCompanionState.UNAVAILABLE,
    "ready": AiCompanionState.IDLE,
    "ready_managed_backend": AiCompanionState.IDLE,
}


def _value(state: object) -> str:
    return str(getattr(state, "value", state) or "")


def canvas_companion_state(snapshot: object, *, hosting: bool) -> CanvasCompanionState:
    """Collapse either canvas view into the finite state a panel may see."""

    if snapshot is None:
        return CanvasCompanionState.NONE
    if not hosting:
        return _CANVAS_STATES.get(
            _value(getattr(snapshot, "state", None)), CanvasCompanionState.NONE
        )
    if _value(getattr(snapshot, "state", None)) == "failed" or str(
        getattr(snapshot, "error", "") or ""
    ):
        # For a host, a failed share means nothing was opened -- the same
        # thing UNREADABLE means to a guest.
        return CanvasCompanionState.UNREADABLE
    if not bool(getattr(snapshot, "shared", False)):
        return CanvasCompanionState.NONE
    # There is a canvas but this computer has no program to open it with.
    # Checked after "shared" so a room with no canvas never advertises a
    # missing app nobody needs.
    if not bool(getattr(snapshot, "launcher_available", False)):
        return CanvasCompanionState.MISSING_APP
    return CanvasCompanionState.READY


def video_companion_state(snapshot: object, *, hosting: bool) -> VideoCompanionState:
    """Collapse either video view into the finite state a panel may see."""

    if snapshot is None:
        return VideoCompanionState.NONE
    state = _value(getattr(snapshot, "state", None))
    if hosting:
        if not bool(getattr(snapshot, "shared", False)):
            # A host that has loaded nothing is a room with no video, whatever
            # its transport last reported.
            return (
                VideoCompanionState.HOST_ATTENTION
                if state == "failed"
                else VideoCompanionState.NONE
            )
        return _HOST_VIDEO_STATES.get(state, VideoCompanionState.NONE)
    if state == "following":
        # A follower in sync is playing or holding, depending on the host.
        return (
            VideoCompanionState.PLAYING
            if bool(getattr(snapshot, "should_play", False))
            else VideoCompanionState.PAUSED
        )
    return _FOLLOW_VIDEO_STATES.get(state, VideoCompanionState.NONE)


def ai_companion_state(snapshot: object) -> AiCompanionState:
    """Collapse the image action, never claiming to see inside the generator."""

    if snapshot is None:
        return AiCompanionState.UNAVAILABLE
    if str(getattr(snapshot, "error", "") or ""):
        return AiCompanionState.FAILED
    state = _AI_STATES.get(
        _value(getattr(snapshot, "state", None)), AiCompanionState.UNAVAILABLE
    )
    if state is AiCompanionState.IDLE and str(getattr(snapshot, "activity", "") or ""):
        # The generator was opened. WebJam cannot see what it is doing in
        # there, so it reports the handoff rather than a job.
        return AiCompanionState.HANDED_OFF
    return state


def build_art_companion_projection(
    *,
    generation: int,
    revision: int,
    in_room: bool,
    hosting: bool,
    canvas_snapshot: object = None,
    video_snapshot: object = None,
    ai_snapshot: object = None,
) -> ArtCompanionProjection:
    """Build one allowlisted projection from the desktop's own snapshots."""

    if not in_room:
        return ArtCompanionProjection(generation=generation, revision=revision)
    return ArtCompanionProjection(
        generation=generation,
        revision=revision,
        in_room=True,
        canvas=canvas_companion_state(canvas_snapshot, hosting=hosting),
        video=video_companion_state(video_snapshot, hosting=hosting),
        # The one authority fact, and it is the desktop's role rather than
        # anything a companion asserted about itself.
        transport_allowed=bool(hosting),
        ai=ai_companion_state(ai_snapshot),
    )


__all__ = [
    "ai_companion_state",
    "build_art_companion_projection",
    "canvas_companion_state",
    "video_companion_state",
]

"""What the room itself should say about Art, in one line.

A person who picked **Make together** and pressed Host lands in a room. Until
now that room said nothing: the canvas lived behind a menu, and the only
mention of it was a message that appeared for nine seconds and told them which
menu to open. A user interface explaining how to navigate itself is a good
sign the thing is not findable.

So the room carries one small, honest indicator instead. The rules that shape
it:

* **One thing.** It answers "what does this room need from me right now?",
  which is a question with a single answer. When nothing needs anything, it
  names what the room *has* instead -- still one thing.
* **Attention before description.** A missing painting program or a video this
  computer cannot follow comes before a canvas that is simply fine, because
  only one of those is a request.
* **Nothing when there is nothing.** A room projection with no intended or
  shared layer shows no invented indicator. Empty room facts are not an empty
  slot where a feature should be.
* **Room facts only.** The image action is personal to whoever runs it, so it
  is not a thing "the room" has and never appears here.

The states come from :mod:`core.art_companion`, which is also what a paired
companion panel reads. That is deliberate: the chip in the room and the chip
in a meeting-window panel are rendered from one derivation, so they cannot
disagree about what is happening.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.art_companion import (
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)


class ArtPresenceTone(str, Enum):
    """Whether this line is a description or a request."""

    #: The room has this. Nothing is wrong and nothing is asked.
    PRESENT = "present"
    #: Something is absent or out of step, and there is a way forward.
    #: Deliberately not an alarm: nothing is broken.
    ATTENTION = "attention"


class ArtPresenceTarget(str, Enum):
    """Which panel the indicator opens. Never more than one."""

    NONE = "none"
    CANVAS = "canvas"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class ArtRoomPresence:
    """One line of room chrome, or nothing at all."""

    #: Empty means show nothing. Missing room facts are not unfinished work.
    label: str = ""
    description: str = ""
    tone: ArtPresenceTone = ArtPresenceTone.PRESENT
    target: ArtPresenceTarget = ArtPresenceTarget.NONE

    @property
    def offered(self) -> bool:
        return bool(self.label)


ABSENT = ArtRoomPresence()

#: Video states where this computer cannot follow what the host is showing.
#: Each has its own recovery inside the panel; the room only needs to say
#: that the panel is where to go.
_VIDEO_ATTENTION = {
    VideoCompanionState.NEEDS_FILE: (
        "Open your Paint along copy",
        "Open your own copy of the same video to follow along.",
    ),
    VideoCompanionState.MISMATCHED_FILE: (
        "Paint along needs a look",
        "The copy open here is a different file than the host's.",
    ),
    VideoCompanionState.FILE_UNAVAILABLE: (
        "Paint along needs a look",
        "The copy open here moved or changed, so it stopped following.",
    ),
    VideoCompanionState.LOCAL_ATTENTION: (
        "Your video needs attention",
        "Open your copy again to continue following on this computer.",
    ),
    VideoCompanionState.STALLED: (
        "Paint along is out of step",
        "The host's position is too old to follow honestly.",
    ),
    VideoCompanionState.HOST_ATTENTION: (
        "Paint along needs a look",
        "The host's own player needs attention.",
    ),
}


def art_room_presence(
    projection: ArtCompanionProjection,
    *,
    hosting: bool = False,
    intended_canvas: bool = False,
    intended_video: bool = False,
) -> ArtRoomPresence:
    """Return the one line this room should show, or nothing.

    Intent describes an explicit choice of an optional layer. Paint along
    chooses video at launch; Make together chooses no layer. A host may
    choose a canvas later, but simply making together never requires setup.

    A guest's saved choice says nothing about the room they joined, so their
    intent is ignored -- what the host actually shared is the only fact.
    """

    if not projection.in_room:
        return ABSENT

    # 1. Requests first. Something is absent or out of step.
    if projection.canvas is CanvasCompanionState.SHARE_PENDING:
        return ArtRoomPresence(
            label="Canvas sharing needs retry",
            description="The new canvas invitation is not confirmed for the room. Open the panel to retry.",
            tone=ArtPresenceTone.ATTENTION,
            target=ArtPresenceTarget.CANVAS,
        )
    if projection.canvas is CanvasCompanionState.WITHDRAW_PENDING:
        return ArtRoomPresence(
            label="Canvas stop needs retry",
            description="Stopping the invitation is not confirmed. The room may still offer the previous canvas.",
            tone=ArtPresenceTone.ATTENTION,
            target=ArtPresenceTarget.CANVAS,
        )
    if projection.canvas is CanvasCompanionState.MISSING_APP:
        return ArtRoomPresence(
            label="Install Drawpile",
            description=(
                "There is a canvas in this room, and no Drawpile on this "
                "computer to open it with."
            ),
            tone=ArtPresenceTone.ATTENTION,
            target=ArtPresenceTarget.CANVAS,
        )
    if projection.canvas is CanvasCompanionState.UNREADABLE:
        return ArtRoomPresence(
            label="Canvas needs a look",
            description="Something was shared that this computer could not read.",
            tone=ArtPresenceTone.ATTENTION,
            target=ArtPresenceTarget.CANVAS,
        )
    video_attention = _VIDEO_ATTENTION.get(projection.video)
    if video_attention is not None:
        label, description = video_attention
        return ArtRoomPresence(
            label=label,
            description=description,
            tone=ArtPresenceTone.ATTENTION,
            target=ArtPresenceTarget.VIDEO,
        )

    # 2. Then what the room actually has.
    if projection.canvas in {
        CanvasCompanionState.READY,
        CanvasCompanionState.OPENING,
    }:
        return ArtRoomPresence(
            label="Shared canvas",
            description="This room has a canvas. Open the panel to work on it.",
            target=ArtPresenceTarget.CANVAS,
        )
    if projection.video is VideoCompanionState.HIDDEN:
        # Hiding is a choice, and the room is the only route back to it. A
        # quiet line is the difference between reversible and one-way.
        return ArtRoomPresence(
            label="Paint along (hidden)",
            description="You hid the video. Open the panel to show it again.",
            target=ArtPresenceTarget.VIDEO,
        )
    if projection.video in {
        VideoCompanionState.READY,
        VideoCompanionState.PLAYING,
        VideoCompanionState.PAUSED,
    }:
        return ArtRoomPresence(
            label="Paint along",
            description=(
                "The host shared a video. Each artist can follow using their "
                "own copy."
            ),
            target=ArtPresenceTarget.VIDEO,
        )

    # 3. Then the door a host asked for at launch but has not walked through.
    if hosting and intended_canvas:
        return ArtRoomPresence(
            label="Set up shared canvas",
            description=(
                "Open the panel to host a canvas in Drawpile and share it "
                "when the group wants to draw on one canvas."
            ),
            target=ArtPresenceTarget.CANVAS,
        )
    if hosting and intended_video:
        return ArtRoomPresence(
            label="Set up Paint along",
            description=(
                "Open the panel to share one local "
                "video each artist can follow on their own copy."
            ),
            target=ArtPresenceTarget.VIDEO,
        )

    # 4. Nothing. Which is a whole answer.
    return ABSENT


__all__ = [
    "ABSENT",
    "ArtPresenceTarget",
    "ArtPresenceTone",
    "ArtRoomPresence",
    "art_room_presence",
]

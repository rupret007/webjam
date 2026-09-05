"""What an Art room may tell a paired companion, and what it will accept back.

A companion panel sits inside a meeting window and shows a small view of this
desktop. That makes it a *second consumer* of Art's state, and a second way to
ask Art to do something -- so it gets its own explicit contract rather than a
window onto the internals.

Two rules shape the whole file.

**Nothing private crosses.** The projection is an allowlist of finite states.
No filesystem path, no file name, no canvas address, no content digest, no
session token, no participant name, and no image. That is not a policy applied
afterwards; the types simply have nowhere to put those things. Notably there is
no prompt field, because WebJam never holds a prompt: the image generator owns
it, so there is nothing here to leak or to bound.

**A remote panel is a request, never an authority.** Every command is a finite
semantic intent bound to an observed revision, checked against a granted scope,
and checked again against what this desktop can honestly do right now. Two
guards matter most:

* **Host-only transport stays host-only.** A guest's companion cannot play,
  pause, stop, or move the reference video, and the check is the projection's
  own ``transport_allowed`` rather than anything the companion sends.
* **Anything that starts another program needs a local yes.** Opening the
  canvas or the image generator launches software on someone's computer, so a
  companion can only ask; the person at the desk confirms. A panel in a meeting
  must not be able to start programs on a machine silently.

Art must also work completely without any of this. The desktop is the product;
a companion is a convenience, and every state below is derived from truth the
desktop already owns and renders on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

MAX_GENERATION = (1 << 53) - 1
MAX_REVISION = (1 << 53) - 1
MAX_SEEK_POSITION_S = 24 * 60 * 60
_UUID = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


class ArtCompanionError(ValueError):
    """A malformed companion message, described without echoing it."""


# ---------------------------------------------------------------------------
# What the companion may see
# ---------------------------------------------------------------------------


class CanvasCompanionState(str, Enum):
    """The shared canvas, as a finite state and nothing more."""

    #: No canvas in this room. A first-class answer, not a failure.
    NONE = "none"
    #: A canvas is offered and this computer can open it.
    READY = "ready"
    #: This computer asked the painting program to open the canvas. WebJam
    #: cannot see inside that program, so it never claims the canvas *is* open.
    OPENING = "opening"
    #: The painting program is not installed here.
    MISSING_APP = "missing_app"
    #: Something was offered that this computer could not read, so nothing
    #: was opened.
    UNREADABLE = "unreadable"


class VideoCompanionState(str, Enum):
    """The reference video, as this computer may honestly describe it."""

    NONE = "none"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    #: This artist chose to ignore it. Their conversation and canvas are
    #: unaffected, which is the point of the state existing.
    HIDDEN = "hidden"
    NEEDS_FILE = "needs_file"
    MISMATCHED_FILE = "mismatched_file"
    FILE_UNAVAILABLE = "file_unavailable"
    LOCAL_ATTENTION = "local_attention"
    HOST_ATTENTION = "host_attention"
    STALLED = "stalled"


class AiCompanionState(str, Enum):
    """The image action, without claiming to see inside another program."""

    #: Not possible here: outside a room, or the generator is not installed.
    UNAVAILABLE = "unavailable"
    #: Possible and nothing has been asked for.
    IDLE = "idle"
    #: This computer opened the generator. Deliberately not "running": WebJam
    #: cannot observe a job inside another program, and a companion showing a
    #: spinner it cannot justify would be a small lie repeated continuously.
    HANDED_OFF = "handed_off"
    #: The handoff itself failed -- the program could not be started.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ArtCompanionProjection:
    """One immutable, allowlisted view of an Art room for a paired panel.

    ``revision`` advances whenever any state below changes, so a companion can
    bind a command to what it actually saw. ``transport_allowed`` is the only
    authority fact, and it is false for everyone except the host.
    """

    generation: int = 0
    revision: int = 0
    in_room: bool = False
    canvas: CanvasCompanionState = CanvasCompanionState.NONE
    video: VideoCompanionState = VideoCompanionState.NONE
    #: Host-only. A guest's companion is refused transport on this alone.
    transport_allowed: bool = False
    ai: AiCompanionState = AiCompanionState.UNAVAILABLE

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "generation", _bounded_int(self.generation, "generation", MAX_GENERATION)
        )
        object.__setattr__(
            self, "revision", _bounded_int(self.revision, "revision", MAX_REVISION)
        )
        object.__setattr__(self, "in_room", _strict_bool(self.in_room, "in_room"))
        object.__setattr__(self, "canvas", CanvasCompanionState(self.canvas))
        object.__setattr__(self, "video", VideoCompanionState(self.video))
        object.__setattr__(
            self,
            "transport_allowed",
            _strict_bool(self.transport_allowed, "transport_allowed"),
        )
        object.__setattr__(self, "ai", AiCompanionState(self.ai))
        if not self.in_room:
            if (
                self.canvas is not CanvasCompanionState.NONE
                or self.video is not VideoCompanionState.NONE
                or self.transport_allowed
                or self.ai is not AiCompanionState.UNAVAILABLE
            ):
                raise ArtCompanionError(
                    "A desktop outside a room has nothing to project."
                )

    def to_public_dict(self) -> dict[str, object]:
        """Return the whole projection. Every field is already public.

        There is no private counterpart to strip: paths, names, addresses,
        digests, tokens, prompts, and images have no field here to live in.
        """

        return {
            "generation": self.generation,
            "revision": self.revision,
            "in_room": self.in_room,
            "canvas": self.canvas.value,
            "video": self.video.value,
            "transport_allowed": self.transport_allowed,
            "ai": self.ai.value,
        }


# ---------------------------------------------------------------------------
# What the companion may ask for
# ---------------------------------------------------------------------------


class ArtScope(str, Enum):
    """Capabilities a paired companion may be granted by the desktop owner."""

    OBSERVE = "observe"
    CANVAS = "canvas"
    TRANSPORT = "transport"
    AI = "ai"


class ArtCommand(str, Enum):
    """Finite semantic intents. These are not controller method names."""

    OPEN_CANVAS = "open_canvas"
    HIDE_VIDEO = "hide_video"
    PLAY_VIDEO = "play_video"
    PAUSE_VIDEO = "pause_video"
    STOP_VIDEO = "stop_video"
    SEEK_VIDEO = "seek_video"
    AI_MAKE = "ai_make"
    AI_EDIT = "ai_edit"

    @property
    def required_scope(self) -> ArtScope:
        return {
            ArtCommand.OPEN_CANVAS: ArtScope.CANVAS,
            ArtCommand.HIDE_VIDEO: ArtScope.OBSERVE,
            ArtCommand.PLAY_VIDEO: ArtScope.TRANSPORT,
            ArtCommand.PAUSE_VIDEO: ArtScope.TRANSPORT,
            ArtCommand.STOP_VIDEO: ArtScope.TRANSPORT,
            ArtCommand.SEEK_VIDEO: ArtScope.TRANSPORT,
            ArtCommand.AI_MAKE: ArtScope.AI,
            ArtCommand.AI_EDIT: ArtScope.AI,
        }[self]

    @property
    def drives_host_transport(self) -> bool:
        """Whether only the host may issue this."""

        return self.required_scope is ArtScope.TRANSPORT

    @property
    def starts_another_program(self) -> bool:
        """Whether acting on this launches software on someone's computer.

        These are the commands a companion may only *ask* for. The person at
        the desk confirms, because a panel inside a meeting must not be able
        to start programs on a machine silently.
        """

        return self in {
            ArtCommand.OPEN_CANVAS,
            ArtCommand.AI_MAKE,
            ArtCommand.AI_EDIT,
        }


#: Bounded argument keys per command. Absent means "takes none".
_ARGUMENTS: dict[ArtCommand, tuple[str, ...]] = {
    ArtCommand.OPEN_CANVAS: (),
    ArtCommand.HIDE_VIDEO: ("hidden",),
    ArtCommand.PLAY_VIDEO: (),
    ArtCommand.PAUSE_VIDEO: (),
    ArtCommand.STOP_VIDEO: (),
    ArtCommand.SEEK_VIDEO: ("position_s",),
    ArtCommand.AI_MAKE: (),
    #: Deliberately no path. A companion cannot name a file on someone else's
    #: disk; the desktop opens its own picker after the local confirmation.
    ArtCommand.AI_EDIT: (),
}


@dataclass(frozen=True, slots=True)
class ArtCommandRequest:
    """One idempotency-keyed intent against an observed desktop revision."""

    command_id: str
    command: ArtCommand
    generation: int
    expected_revision: int
    arguments: Mapping[str, object] | tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "command_id", _canonical_uuid(self.command_id, "command_id")
        )
        command = ArtCommand(self.command)
        object.__setattr__(self, "command", command)
        object.__setattr__(
            self,
            "generation",
            _bounded_int(self.generation, "generation", MAX_GENERATION),
        )
        object.__setattr__(
            self,
            "expected_revision",
            _bounded_int(self.expected_revision, "expected_revision", MAX_REVISION),
        )
        object.__setattr__(self, "arguments", _arguments(command, self.arguments))

    def argument(self, key: str) -> object:
        for name, value in self.arguments:
            if name == key:
                return value
        raise ArtCompanionError("That command has no such argument.")


class ArtCommandStatus(str, Enum):
    ACCEPTED = "accepted"
    #: Asked for, and waiting on the person at the desk to confirm.
    NEEDS_LOCAL_CONFIRMATION = "needs_local_confirmation"
    REJECTED = "rejected"


class ArtRejectionReason(str, Enum):
    NONE = "none"
    UNAUTHORIZED = "unauthorized"
    #: Transport belongs to the host, and this desktop is not hosting.
    NOT_HOST = "not_host"
    STALE_GENERATION = "stale_generation"
    STALE_REVISION = "stale_revision"
    #: Nothing here can act on it: no canvas, no video, no generator.
    INVALID_STATE = "invalid_state"
    NOT_IN_A_ROOM = "not_in_a_room"


@dataclass(frozen=True, slots=True)
class ArtCommandReceipt:
    """Bounded evidence for one command, carrying no raw error text."""

    command_id: str
    command: ArtCommand
    status: ArtCommandStatus
    reason: ArtRejectionReason = ArtRejectionReason.NONE
    revision: int = 0

    def to_public_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "command": self.command.value,
            "status": self.status.value,
            "reason": self.reason.value,
            "revision": self.revision,
        }


def authorize_art_command(
    request: ArtCommandRequest,
    projection: ArtCompanionProjection,
    granted: Iterable[ArtScope | str],
) -> ArtCommandReceipt:
    """Decide one command against a granted scope set and observed state.

    This performs no action and touches nothing. It answers a single question
    -- may this be acted on, and by whose leave -- so the same rules hold
    wherever a companion transport eventually plugs in.
    """

    scopes = _scopes(granted)

    def refuse(reason: ArtRejectionReason) -> ArtCommandReceipt:
        return ArtCommandReceipt(
            command_id=request.command_id,
            command=request.command,
            status=ArtCommandStatus.REJECTED,
            reason=reason,
            revision=projection.revision,
        )

    if request.command.required_scope not in scopes:
        return refuse(ArtRejectionReason.UNAUTHORIZED)
    if request.generation != projection.generation:
        return refuse(ArtRejectionReason.STALE_GENERATION)
    if request.expected_revision != projection.revision:
        return refuse(ArtRejectionReason.STALE_REVISION)
    if not projection.in_room:
        return refuse(ArtRejectionReason.NOT_IN_A_ROOM)
    # Host-only transport, decided from this desktop's own truth rather than
    # from anything the companion claimed about itself.
    if request.command.drives_host_transport and not projection.transport_allowed:
        return refuse(ArtRejectionReason.NOT_HOST)
    if not _state_allows(request.command, projection):
        return refuse(ArtRejectionReason.INVALID_STATE)

    status = (
        ArtCommandStatus.NEEDS_LOCAL_CONFIRMATION
        if request.command.starts_another_program
        else ArtCommandStatus.ACCEPTED
    )
    return ArtCommandReceipt(
        command_id=request.command_id,
        command=request.command,
        status=status,
        revision=projection.revision,
    )


def _state_allows(
    command: ArtCommand, projection: ArtCompanionProjection
) -> bool:
    if command is ArtCommand.OPEN_CANVAS:
        return projection.canvas in {
            CanvasCompanionState.READY,
            CanvasCompanionState.OPENING,
        }
    if command is ArtCommand.HIDE_VIDEO:
        return projection.video is not VideoCompanionState.NONE
    if command.drives_host_transport:
        # Something has to be shared before it can be moved.
        return projection.video not in {
            VideoCompanionState.NONE,
            VideoCompanionState.HOST_ATTENTION,
            VideoCompanionState.LOCAL_ATTENTION,
        }
    if command in {ArtCommand.AI_MAKE, ArtCommand.AI_EDIT}:
        return projection.ai in {AiCompanionState.IDLE, AiCompanionState.HANDED_OFF}
    return False  # pragma: no cover - every command is covered above


# ---------------------------------------------------------------------------
# Bounded parsing
# ---------------------------------------------------------------------------


def _bounded_int(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtCompanionError(f"{label} must be a whole number.")
    if not 0 <= value <= maximum:
        raise ArtCompanionError(f"{label} is outside the supported range.")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ArtCompanionError(f"{label} must be a boolean.")
    return value


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value.lower()) is None:
        raise ArtCompanionError(f"{label} must be a canonical UUID.")
    return value.lower()


def _scopes(granted: Iterable[ArtScope | str]) -> frozenset[ArtScope]:
    if isinstance(granted, (str, bytes)):
        raise ArtCompanionError("scopes must be a collection.")
    try:
        return frozenset(ArtScope(scope) for scope in granted)
    except (TypeError, ValueError) as exc:
        raise ArtCompanionError("scopes contain an unsupported capability.") from exc


def _arguments(
    command: ArtCommand, value: object
) -> tuple[tuple[str, object], ...]:
    expected = _ARGUMENTS[command]
    if isinstance(value, tuple) and all(
        isinstance(item, tuple) and len(item) == 2 for item in value
    ):
        payload: Mapping[str, object] = dict(value)
    elif isinstance(value, Mapping):
        payload = value
    else:
        raise ArtCompanionError("command arguments must be an object.")
    if set(payload) != set(expected):
        raise ArtCompanionError("command arguments are incomplete or unexpected.")

    normalized: dict[str, object] = {}
    if command is ArtCommand.HIDE_VIDEO:
        normalized["hidden"] = _strict_bool(payload["hidden"], "hidden")
    elif command is ArtCommand.SEEK_VIDEO:
        normalized["position_s"] = _bounded_int(
            payload["position_s"], "position_s", MAX_SEEK_POSITION_S
        )
    return tuple((key, normalized[key]) for key in expected)


__all__ = [
    "MAX_SEEK_POSITION_S",
    "AiCompanionState",
    "ArtCommand",
    "ArtCommandReceipt",
    "ArtCommandRequest",
    "ArtCommandStatus",
    "ArtCompanionError",
    "ArtCompanionProjection",
    "ArtRejectionReason",
    "ArtScope",
    "CanvasCompanionState",
    "VideoCompanionState",
    "authorize_art_command",
]

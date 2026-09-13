"""Art room context derived from connection evidence and optional activity.

This is deliberately not a roster: the room transports prove a connection,
but do not supply a complete, named set of artists. No peer payload, invitation,
file name, or meeting address belongs in this projection.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.art_room_presence import ABSENT, ArtPresenceTarget, ArtRoomPresence
from core.session_conductor import ArtRoomState


@dataclass(frozen=True, slots=True)
class ArtRoomOverview:
    phase: str = "idle"
    phase_label: str = "Before you join"
    title: str = "Make room for your art"
    role_label: str = "Guest"
    connection_label: str = "No room connected"
    connection_detail: str = "Use a host's invitation to join their room."
    activity_label: str = "Make from your own space"
    activity_detail: str = (
        "Use paper, clay, a model, printer, or your usual app."
    )
    activity_action: str = ""
    activity_action_label: str = ""
    activity_enabled: bool = False
    conversation_enabled: bool = True
    #: What the Conversation button says its next click does. "Set Up
    #: Conversation" when no meeting link is saved yet, "Conversation" once
    #: one is. Mirrors the Music session strip so the two modes read alike.
    conversation_action_label: str = "Conversation"
    secondary_activity_label: str = ""
    secondary_activity_detail: str = ""
    secondary_activity_action: str = ""
    secondary_activity_action_label: str = ""
    secondary_activity_enabled: bool = False

    @property
    def activity_actions(self) -> tuple[str, ...]:
        """The distinct, currently enabled panel targets in display order."""
        actions = []
        for enabled, action in (
            (self.activity_enabled, self.activity_action),
            (self.secondary_activity_enabled, self.secondary_activity_action),
        ):
            if enabled and action in {"video", "canvas"} and action not in actions:
                actions.append(action)
        return tuple(actions)


def art_room_overview(
    *,
    state: ArtRoomState,
    hosting: bool,
    probing: bool = False,
    stopping: bool = False,
    ended: bool = False,
    cleanup_required: bool = False,
    quitting: bool = False,
    presence: ArtRoomPresence = ABSENT,
    secondary_presence: ArtRoomPresence = ABSENT,
    named_connections: bool = False,
    conversation_configured: bool = False,
) -> ArtRoomOverview:
    """Keep closure and missing connection evidence ahead of optional layers."""
    role = "Host" if hosting else "Guest"
    if quitting:
        phase, label, title = "cleanup_required", "Still quitting", "WebJam is still closing"
        connection = "WebJam cleanup is incomplete"
        detail = (
            "Wait a moment, then choose Quit again. "
            "WebJam has kept this window open until cleanup is confirmed."
        )
    elif cleanup_required:
        phase, label, title = "cleanup_required", "Still closing", "Your room is still closing"
        connection = "Room cleanup needs another try"
        detail = (
            "WebJam has not confirmed that every room connection stopped. "
            f"Choose {'Try End Room' if hosting else 'Try Leave Room'} before starting or joining again."
        )
    elif stopping:
        phase, label = "ending", "Ending room" if hosting else "Leaving room"
        title = "Closing your room connection"
        connection = "Waiting for room services to stop"
        detail = "Keep WebJam open while it finishes closing this connection."
    elif ended:
        phase, label = "ended", "Room ended" if hosting else "Room left"
        title = "Keep making"
        connection = "This computer is no longer in the room"
        detail = (
            ("Choose Start New Room to make together again. " if hosting else
             "Ask the host for a new invitation, then choose Paste New Invite. ")
            + "Your own tools can stay open. A separate meeting has its own leave controls."
        )
    elif state is ArtRoomState.FAILED:
        phase, title = "failed", "Your work can stay open"
        if probing and not hosting:
            label = "Room not reached"
            connection = "No room connection confirmed"
            detail = (
                "WebJam could not confirm a connection to the host. "
                "Follow the room recovery action to try joining again."
            )
        else:
            label = "Connection lost"
            connection = "Room connection is unavailable"
            detail = (
                "WebJam cannot confirm who is still here or follow new room changes. "
                "Follow the room recovery action to continue."
            )
    elif state is ArtRoomState.RECONNECTING:
        phase, label, title = "reconnecting", "Reconnecting", "Your work can stay open"
        if hosting:
            label = "Network interrupted"
            connection = "Room network is unavailable"
            detail = (
                "Your own tools can stay open. Check your Wi-Fi or local network, "
                "then choose Try Again."
            )
        else:
            connection = "Waiting for the room connection"
            detail = (
                "New room changes are not confirmed yet. Your own tools can stay open "
                "while WebJam checks the connection."
            )
    elif probing or state is ArtRoomState.STARTING:
        phase, label, title = "opening", "Opening room" if hosting else "Joining room", "A place to make together"
        connection = "Preparing your room" if hosting else "Checking the host's room"
        detail = (
            "No artist connection is confirmed yet."
            if hosting
            else "The host's room details will appear after the connection is confirmed."
        )
    elif state is ArtRoomState.CONNECTED:
        phase, label, title = "connected", "In the room", "Make from your own space"
        connection = "Artist connection confirmed" if hosting else "Connected to the host"
        detail = (
            "An artist has connected to this room. WebJam does not yet show a full artist list."
            if hosting
            else "This room connection is confirmed. WebJam does not yet show a full artist list."
        )
        if hosting and named_connections:
            connection = "Connected to your room"
            detail = (
                "Names guests chose when joining WebJam. "
                "Talk and screen sharing are separate."
            )
    elif state is ArtRoomState.WAITING and hosting:
        phase, label, title = "waiting", "Room open", "Make from your own space"
        connection = "Waiting for artists to connect"
        detail = (
            "Your room is open. Share its invitation using the action above; "
            "an invitation alone does not mean someone has joined."
        )
    else:
        phase, label, title = "idle", "Before you host" if hosting else "Before you join", "Make room for your art"
        connection = "No room connected"
        detail = (
            "Start a room, then share its invitation with the artists you want to join."
            if hosting
            else "Use a host's invitation to join their room."
        )

    active = phase in {"waiting", "connected"}
    activity = "Make from your own space"
    activity_detail = (
        "Use paper, clay, a model, printer, or your usual app. "
        "Conversation can carry talk or a screen share."
    )
    action = ""
    action_label = ""
    if active and presence.offered:
        action, action_label = _activity_action(presence.target)
        if action:
            activity, activity_detail = presence.label, presence.description
    secondary_activity = ""
    secondary_detail = ""
    secondary_action = ""
    secondary_action_label = ""
    if active and secondary_presence.offered:
        candidate, candidate_label = _activity_action(secondary_presence.target)
        if candidate and candidate != action:
            secondary_activity = secondary_presence.label
            secondary_detail = secondary_presence.description
            secondary_action, secondary_action_label = candidate, candidate_label
    return ArtRoomOverview(
        phase=phase,
        phase_label=label,
        title=title,
        role_label=role,
        connection_label=connection,
        connection_detail=detail,
        activity_label=activity,
        activity_detail=activity_detail,
        activity_action=action,
        activity_action_label=action_label,
        activity_enabled=active and bool(action),
        secondary_activity_label=secondary_activity,
        secondary_activity_detail=secondary_detail,
        secondary_activity_action=secondary_action,
        secondary_activity_action_label=secondary_action_label,
        secondary_activity_enabled=active and bool(secondary_action),
        # Conversation only reveals the separate meeting controls. Its
        # availability is independent of the Art transport connection.
        conversation_enabled=not (stopping or cleanup_required or quitting),
        # Name the next click: a room with no saved meeting link needs one
        # set up before anyone can talk; after that the panel is just there.
        conversation_action_label=(
            "Conversation" if conversation_configured else "Set Up Conversation"
        ),
    )


def _activity_action(target: ArtPresenceTarget) -> tuple[str, str]:
    if target is ArtPresenceTarget.VIDEO:
        return "video", "Open Paint along"
    if target is ArtPresenceTarget.CANVAS:
        return "canvas", "Open workspace"
    return "", ""

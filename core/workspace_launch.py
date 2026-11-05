"""Refuse an incomplete launch decision before allocating a live workspace.

A dialog's Accepted result is not itself a Host/Join choice. This checks the
already-parsed, in-memory launch result; it neither parses credentials nor
authorizes enrollment, provider calls, capture or playback.
"""

from __future__ import annotations

from core.network_invite import BandInvite
from core.remote_invitation import RemoteInvitation


class InvalidWorkspaceLaunch(ValueError):
    """A fixed, credential-free error for an incomplete launch decision."""


def validate_workspace_launch(launch: object) -> None:
    """Require an explicit role and exactly one typed invitation for Join."""

    role = getattr(launch, "selected_role", None)
    band = getattr(launch, "band_invite", None)
    remote = getattr(launch, "remote_invitation", None)
    valid = type(role) is str and role in {"host", "join", "studio", "library"}
    if role == "join":
        valid = valid and (
            isinstance(band, BandInvite) and remote is None
            or band is None and isinstance(remote, RemoteInvitation)
        )
    if not valid:
        raise InvalidWorkspaceLaunch(
            "Choose Host, a valid Join invitation, or saved work before continuing."
        )

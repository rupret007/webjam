"""Parse invitations once at ingress and enforce secret-safe source policy."""

from __future__ import annotations

from enum import Enum
import sys
from typing import Sequence

from core.network_invite import (
    BandInvite,
    InviteLinkError,
    invite_from_text,
    parse_invite_link,
)
from core.remote_invitation import RemoteInvitation


class InvitationSource(str, Enum):
    PASTE = "paste"
    MAC_FILE_OPEN = "mac_file_open"
    ARGV = "argv"


class InvitationIngressErrorCode(str, Enum):
    INVALID = "invalid"
    INCOMPATIBLE = "incompatible"
    SOURCE_NOT_ALLOWED = "source_not_allowed"


class InvitationIngressError(ValueError):
    """A fixed-copy ingress failure that never retains or echoes input."""

    def __init__(
        self,
        code: InvitationIngressErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


Invitation = BandInvite | RemoteInvitation


def _has_literal_v3_marker(value: str) -> bool:
    query = value.partition("?")[2]
    return any(part == "v=3" for part in query.split("&"))


def parse_invitation_at_ingress(
    text: object,
    *,
    source: InvitationSource,
    platform: str | None = None,
    allowed_remote_profiles: frozenset[str] | None = None,
) -> Invitation:
    """Return a typed invitation and immediately discard the serialized input.

    Version-3 capabilities are accepted only from an explicit paste or a
    macOS QFileOpen event. They are never parsed from process arguments.
    Version-1/2 links retain their existing paste, deep-link, and argv support.
    """

    try:
        ingress_source = InvitationSource(source)
    except (TypeError, ValueError):
        raise InvitationIngressError(
            InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
            "WebJam could not open that invitation safely.",
        ) from None
    current_platform = sys.platform if platform is None else str(platform)
    raw = str(text or "")
    value = raw.strip()
    if ingress_source is InvitationSource.MAC_FILE_OPEN and current_platform != "darwin":
        raise InvitationIngressError(
            InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
            "Paste the invitation into WebJam to join.",
        )
    if ingress_source is InvitationSource.ARGV and _has_literal_v3_marker(value):
        raise InvitationIngressError(
            InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
            "Paste the invitation into WebJam to join.",
        )
    if ingress_source is not InvitationSource.PASTE and not value.lower().startswith(
        "webjam://"
    ):
        raise InvitationIngressError(
            InvitationIngressErrorCode.INVALID,
            "That invite link doesn’t look right. Copy it again from your host.",
        )
    try:
        if ingress_source is InvitationSource.PASTE:
            invitation = invite_from_text(
                value,
                allowed_remote_profiles=allowed_remote_profiles,
            )
        else:
            invitation = parse_invite_link(
                value,
                allowed_remote_profiles=allowed_remote_profiles,
            )
    except InviteLinkError as exc:
        message = str(exc).lower()
        if "different webjam version" in message or "incompatible" in message:
            raise InvitationIngressError(
                InvitationIngressErrorCode.INCOMPATIBLE,
                "That invitation needs a different WebJam version.",
            ) from None
        raise InvitationIngressError(
            InvitationIngressErrorCode.INVALID,
            "That invite link doesn’t look right. Copy it again from your host.",
        ) from None
    if isinstance(invitation, RemoteInvitation):
        if ingress_source is InvitationSource.ARGV:
            raise InvitationIngressError(
                InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
                "Paste the invitation into WebJam to join.",
            )
        if (
            ingress_source is InvitationSource.MAC_FILE_OPEN
            and current_platform != "darwin"
        ):
            raise InvitationIngressError(
                InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
                "Paste the invitation into WebJam to join.",
            )
    return invitation


def invitation_from_arguments(
    arguments: Sequence[object],
    *,
    allowed_remote_profiles: frozenset[str] | None = None,
) -> BandInvite | None:
    """Return the first valid legacy argv invitation; silently ignore v3."""

    for item in arguments[1:]:
        raw = str(item or "")
        if not raw.lower().startswith("webjam://"):
            continue
        try:
            invitation = parse_invitation_at_ingress(
                raw,
                source=InvitationSource.ARGV,
                allowed_remote_profiles=allowed_remote_profiles,
            )
        except InvitationIngressError:
            continue
        if isinstance(invitation, BandInvite):
            return invitation
    return None

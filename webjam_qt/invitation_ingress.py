"""Parse invitations once at ingress and enforce secret-safe source policy."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from enum import Enum

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


def _has_literal_version_marker(value: str, version: str) -> bool:
    query = value.partition("?")[2]
    return any(part == f"v={version}" for part in query.split("&"))


# A host now copies one message that carries the jam link and, optionally, a
# meeting link and a line explaining which is which. Bandmates forward that
# through chat and email, which add quoting and signatures of their own, so a
# paste is rarely just a URL any more.
_MAX_PASTE_CHARS = 8192


def _link_from_pasted_text(value: str) -> str:
    """Return the single ``webjam://`` link inside pasted text.

    Only the exact link is returned; the surrounding text is discarded here and
    the link still goes through the strict parser unchanged. Two different jam
    links in one paste is ambiguous rather than merely messy, so it is refused
    instead of guessing which one the sender meant.
    """

    if len(value) > _MAX_PASTE_CHARS:
        raise InvitationIngressError(
            InvitationIngressErrorCode.INVALID,
            "That invite link doesn’t look right. Copy it again from your host.",
        )
    candidates = [
        token.strip().strip("<>\"'`,;")
        for token in value.split()
        if token.strip().strip("<>\"'`,;").lower().startswith("webjam://")
    ]
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise InvitationIngressError(
            InvitationIngressErrorCode.INVALID,
            "That invite link doesn’t look right. Copy it again from your host.",
        )
    return unique[0]


def parse_invitation_at_ingress(
    text: object,
    *,
    source: InvitationSource,
    platform: str | None = None,
    allowed_remote_profiles: frozenset[str] | None = None,
) -> Invitation:
    """Return a typed invitation and immediately discard the serialized input.

    Bearer-carrying invitations (v2 private-LAN and v3 remote) are accepted
    only from an explicit paste or a macOS QFileOpen event. They are never
    parsed from process arguments, which Qt and crash reporters may retain.
    Version-1 endpoint-only links retain their existing paste, deep-link, and
    argv support.
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
    # Extract the link from a longer paste before any policy check, so the
    # version guards below see the same string the parser will.
    if (
        source == InvitationSource.PASTE
        and not value.lower().startswith("webjam://")
        and "webjam://" in value.lower()
    ):
        value = _link_from_pasted_text(value)
    if ingress_source is InvitationSource.MAC_FILE_OPEN and current_platform != "darwin":
        raise InvitationIngressError(
            InvitationIngressErrorCode.SOURCE_NOT_ALLOWED,
            "Paste the invitation into WebJam to join.",
        )
    if ingress_source is InvitationSource.ARGV and (
        _has_literal_version_marker(value, "2")
        or _has_literal_version_marker(value, "3")
    ):
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
    if isinstance(invitation, RemoteInvitation) or (
        isinstance(invitation, BandInvite) and invitation.peer_enabled
    ):
        # The literal version guard above avoids parsing the normal form in
        # the first place. Keep this typed guard for percent-encoded or future
        # equivalent forms so a bearer can never become an argv invitation.
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
    """Return the first endpoint-only v1 argv invitation.

    Bearer-carrying v2/v3 links are intentionally ignored so neither the
    bootstrap nor Qt can retain a credential supplied on a process command
    line.
    """

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

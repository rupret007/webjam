"""Small, dependency-free helpers for musician-friendly band invites.

Legacy v1 links contain only the Jamulus endpoint.  Private-session v2 links
also carry one random enrollment credential for WebJam's same-LAN recording
control plane.  That credential never grants Jamulus RPC or filesystem access,
and the peer service accepts it only for enrollment into the one session.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from core.jamulus_endpoint import (
    DEFAULT_JAMULUS_PORT,
    JamulusEndpointError,
    parse_jamulus_endpoint,
)

INVITE_SCHEME = "webjam"
INVITE_ACTION = "join"
INVITE_VERSION = "2"
LEGACY_INVITE_VERSION = "1"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_PRIVATE_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class InviteLinkError(ValueError):
    """Raised when pasted text is not a safe WebJam invitation."""


@dataclass(frozen=True, repr=False)
class BandInvite:
    """Parsed join data; v2 instances include a private bearer credential.

    Callers must never render, persist in ordinary settings, or log the full
    object. The credential exists only to enroll with the session peer.
    """

    host: str
    port: int = DEFAULT_JAMULUS_PORT
    session_name: str = "Band Rehearsal"
    session_id: str = ""
    peer_port: int = 0
    invite_token: str = ""

    @property
    def peer_enabled(self) -> bool:
        return bool(self.session_id and self.peer_port and self.invite_token)

    @property
    def version(self) -> int:
        return int(INVITE_VERSION if self.peer_enabled else LEGACY_INVITE_VERSION)

    @property
    def is_remote(self) -> bool:
        return False

    def __repr__(self) -> str:
        """Never place an endpoint, title, session ID, or bearer in logs."""

        version = INVITE_VERSION if self.peer_enabled else LEGACY_INVITE_VERSION
        return (
            f"BandInvite(version={version!r}, "
            f"peer_enabled={self.peer_enabled!r}, private=[redacted])"
        )


def _validate_invitable_host(host: str) -> None:
    if host.lower() == "localhost":
        raise InviteLinkError("That invite link points back to this Mac.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        raise InviteLinkError("That invite link does not contain a reachable host.")


def _validate_private_peer_host(host: str) -> None:
    """Keep the unencrypted recording-control plane on an RFC1918 LAN.

    Legacy invitations may still point at ordinary remote Jamulus servers.
    Version-2 invitations also carry a bearer credential and can upload local
    originals, so accepting a public IP or hostname would silently move that
    private control/media plane onto the Internet.
    """

    try:
        address = ipaddress.ip_address(str(host or "").strip())
    except ValueError as exc:
        raise InviteLinkError(
            "Private recording invites require a same-network IPv4 address."
        ) from exc
    if address.version != 4 or not any(
        address in network for network in _PRIVATE_LAN_NETWORKS
    ):
        raise InviteLinkError(
            "Private recording invites require a same-network IPv4 address."
        )


def create_invite_link(
    host: str,
    *,
    port: int = DEFAULT_JAMULUS_PORT,
    session_name: str = "Band Rehearsal",
    session_id: str = "",
    peer_port: int = 0,
    invite_token: str = "",
) -> str:
    """Create a clickable, pasteable WebJam invitation URL."""
    raw_host = str(host or "").strip()
    if ":" in raw_host:
        # Version 1 carries host and port as separate fields. Reject endpoint
        # shorthand (and IPv6 for now) rather than emit an ambiguous link.
        raise InviteLinkError("WebJam could not create a safe invite link.")
    try:
        endpoint = parse_jamulus_endpoint(
            raw_host, default_port=int(port)
        )
    except (JamulusEndpointError, TypeError, ValueError) as exc:
        raise InviteLinkError("WebJam could not create a safe invite link.") from exc
    _validate_invitable_host(endpoint.host)
    clean_name = " ".join(str(session_name or "Band Rehearsal").split())
    clean_name = clean_name[:80] or "Band Rehearsal"
    peer_values = (str(session_id or ""), int(peer_port or 0), str(invite_token or ""))
    peer_supplied = any(peer_values)
    if peer_supplied and not all(peer_values):
        raise InviteLinkError("WebJam could not create a complete private invite link.")
    fields: dict[str, object] = {
        "v": INVITE_VERSION if peer_supplied else LEGACY_INVITE_VERSION,
        "host": endpoint.host,
        "port": endpoint.port,
        "session": clean_name,
    }
    if peer_supplied:
        _validate_private_peer_host(endpoint.host)
        try:
            canonical_session = str(uuid.UUID(peer_values[0]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InviteLinkError("WebJam could not create a safe private invite link.") from exc
        if canonical_session != peer_values[0].lower():
            raise InviteLinkError("WebJam could not create a safe private invite link.")
        if not 1 <= peer_values[1] <= 65535 or not _TOKEN_PATTERN.fullmatch(peer_values[2]):
            raise InviteLinkError("WebJam could not create a safe private invite link.")
        fields.update(
            {
                "sid": canonical_session,
                "peer": peer_values[1],
                "token": peer_values[2],
            }
        )
    query = urlencode(fields)
    return f"{INVITE_SCHEME}://{INVITE_ACTION}?{query}"


def parse_invite_link(
    text: str,
    *,
    allowed_remote_profiles: frozenset[str] | None = None,
):
    """Parse a ``webjam://join`` URL without accepting hidden parameters.

    The strict shape keeps the link understandable and prevents a future
    caller from accidentally treating credentials, paths, or commands as part
    of an invitation.
    """
    value = str(text or "").strip()
    if not value:
        raise InviteLinkError("Paste the invite link your host sent you.")
    # A literal v3 marker is a one-way dispatcher. Any malformed, mixed, or
    # untrusted v3 shape fails in the strict remote parser and can never fall
    # back to a plaintext v1/v2 interpretation.
    if any(part == "v=3" for part in value.partition("?")[2].split("&")):
        from core.remote_invitation import (
            RemoteInvitationError,
            parse_remote_invitation_link,
        )
        from core.rendezvous_profiles import DEFAULT_RENDEZVOUS_PROFILES

        profiles = (
            DEFAULT_RENDEZVOUS_PROFILES.profile_ids
            if allowed_remote_profiles is None
            else allowed_remote_profiles
        )
        try:
            return parse_remote_invitation_link(
                value,
                allowed_profiles=profiles,
            )
        except RemoteInvitationError as exc:
            raise InviteLinkError(str(exc)) from exc
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise InviteLinkError("That invite link is not valid.") from exc
    if parsed.scheme.lower() != INVITE_SCHEME or parsed.netloc.lower() != INVITE_ACTION:
        raise InviteLinkError("Paste a WebJam invite link that starts with webjam://join.")
    if parsed.path not in {"", "/"} or parsed.fragment or parsed.username or parsed.password:
        raise InviteLinkError("That WebJam invite link is not valid.")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InviteLinkError("That WebJam invite link is not valid.") from exc
    version = query.get("v", [LEGACY_INVITE_VERSION])[0]
    allowed = {"v", "host", "port", "session"}
    if version == INVITE_VERSION:
        allowed.update({"sid", "peer", "token"})
    elif version != LEGACY_INVITE_VERSION:
        raise InviteLinkError("This invite was made by an incompatible WebJam version.")
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        raise InviteLinkError("That WebJam invite link is not valid.")
    host = query.get("host", [""])[0]
    port_text = query.get("port", [str(DEFAULT_JAMULUS_PORT)])[0]
    if ":" in host:
        raise InviteLinkError("That WebJam invite link has an invalid connection.")
    if not port_text.isascii() or not port_text.isdigit():
        raise InviteLinkError("That WebJam invite link has an invalid connection.")
    try:
        endpoint = parse_jamulus_endpoint(
            host, default_port=int(port_text)
        )
    except (JamulusEndpointError, TypeError, ValueError) as exc:
        raise InviteLinkError("That WebJam invite link has an invalid connection.") from exc
    _validate_invitable_host(endpoint.host)
    session_name = " ".join(
        query.get("session", ["Band Rehearsal"])[0].split()
    )[:80]
    session_id = ""
    peer_port = 0
    invite_token = ""
    if version == INVITE_VERSION:
        if not all(key in query for key in ("sid", "peer", "token")):
            raise InviteLinkError("That private WebJam invite link is incomplete.")
        try:
            session_id = str(uuid.UUID(query["sid"][0]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InviteLinkError("That private WebJam invite link is not valid.") from exc
        peer_text = query["peer"][0]
        invite_token = query["token"][0]
        if (
            session_id != query["sid"][0].lower()
            or not peer_text.isascii()
            or not peer_text.isdigit()
            or not 1 <= int(peer_text) <= 65535
            or not _TOKEN_PATTERN.fullmatch(invite_token)
        ):
            raise InviteLinkError("That private WebJam invite link is not valid.")
        _validate_private_peer_host(endpoint.host)
        peer_port = int(peer_text)
    return BandInvite(
        host=endpoint.host,
        port=endpoint.port,
        session_name=session_name or "Band Rehearsal",
        session_id=session_id,
        peer_port=peer_port,
        invite_token=invite_token,
    )


def invite_from_text(
    text: str,
    *,
    allowed_remote_profiles: frozenset[str] | None = None,
):
    """Parse the preferred link, with a quiet legacy-address fallback.

    New UI copy asks for a link.  Accepting a bare address here keeps old test
    invitations usable without making musicians extract anything from new
    links.
    """
    value = str(text or "").strip()
    if "://" in value:
        return parse_invite_link(
            value,
            allowed_remote_profiles=allowed_remote_profiles,
        )
    try:
        endpoint = parse_jamulus_endpoint(value)
    except JamulusEndpointError as exc:
        raise InviteLinkError("Paste the WebJam invite link your host sent you.") from exc
    _validate_invitable_host(endpoint.host)
    return BandInvite(endpoint.host, endpoint.port)


def local_band_address() -> str:
    """Best same-LAN IPv4 address, preferring macOS Wi-Fi over VPN routes."""
    private_ranges = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )

    def _usable(value: str) -> str:
        try:
            address = ipaddress.ip_address(str(value or "").strip())
        except ValueError:
            return ""
        if address.version != 4 or not any(address in item for item in private_ranges):
            return ""
        return str(address)

    # A full-tunnel VPN can own the default route. On macOS, ask for the
    # physical Wi-Fi device first so an invite advertised as same-network
    # never quietly contains a utun/Tailscale address.
    if sys.platform == "darwin":
        try:
            hardware = subprocess.run(
                ["/usr/sbin/networksetup", "-listallhardwareports"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
            wifi_device = ""
            for block in hardware.split("\n\n"):
                if not any(
                    label in block for label in ("Hardware Port: Wi-Fi", "Hardware Port: AirPort")
                ):
                    continue
                for line in block.splitlines():
                    if line.startswith("Device: "):
                        wifi_device = line.partition(":")[2].strip()
                        break
                if wifi_device:
                    break
            if wifi_device and not wifi_device.startswith("utun"):
                wifi_address = subprocess.run(
                    ["/usr/sbin/ipconfig", "getifaddr", wifi_device],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                ).stdout.strip()
                usable = _usable(wifi_address)
                if usable:
                    return usable
        except (OSError, subprocess.SubprocessError):
            pass

    candidates: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect selects an interface without sending a packet.
        sock.connect(("192.0.2.1", 9))
        candidates.append(str(sock.getsockname()[0]))
    except OSError:
        pass
    finally:
        sock.close()
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    for value in candidates:
        usable = _usable(value)
        if usable:
            return usable
    return ""

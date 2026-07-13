"""Small, dependency-free helpers for musician-friendly band invites.

An invite deliberately contains only public connection information.  RPC
credentials, recorder paths, and other host-only details must never cross the
clipboard boundary.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from core.jamulus_endpoint import (
    DEFAULT_JAMULUS_PORT,
    JamulusEndpointError,
    parse_jamulus_endpoint,
)


INVITE_SCHEME = "webjam"
INVITE_ACTION = "join"
INVITE_VERSION = "1"


class InviteLinkError(ValueError):
    """Raised when pasted text is not a safe WebJam invitation."""


@dataclass(frozen=True)
class BandInvite:
    """The non-secret information needed to join one jam."""

    host: str
    port: int = DEFAULT_JAMULUS_PORT
    session_name: str = "Band Rehearsal"


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


def create_invite_link(
    host: str,
    *,
    port: int = DEFAULT_JAMULUS_PORT,
    session_name: str = "Band Rehearsal",
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
    query = urlencode(
        {
            "v": INVITE_VERSION,
            "host": endpoint.host,
            "port": endpoint.port,
            "session": clean_name,
        }
    )
    return f"{INVITE_SCHEME}://{INVITE_ACTION}?{query}"


def parse_invite_link(text: str) -> BandInvite:
    """Parse a ``webjam://join`` URL without accepting hidden parameters.

    The strict shape keeps the link understandable and prevents a future
    caller from accidentally treating credentials, paths, or commands as part
    of an invitation.
    """
    value = str(text or "").strip()
    if not value:
        raise InviteLinkError("Paste the invite link your host sent you.")
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
    allowed = {"v", "host", "port", "session"}
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        raise InviteLinkError("That WebJam invite link is not valid.")
    if query.get("v", [INVITE_VERSION])[0] != INVITE_VERSION:
        raise InviteLinkError("This invite was made by an incompatible WebJam version.")
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
    return BandInvite(
        host=endpoint.host,
        port=endpoint.port,
        session_name=session_name or "Band Rehearsal",
    )


def invite_from_text(text: str) -> BandInvite:
    """Parse the preferred link, with a quiet legacy-address fallback.

    New UI copy asks for a link.  Accepting a bare address here keeps old test
    invitations usable without making musicians extract anything from new
    links.
    """
    value = str(text or "").strip()
    if "://" in value:
        return parse_invite_link(value)
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

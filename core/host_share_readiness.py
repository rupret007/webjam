"""Truthful pre-share checks for WebJam's supported private-LAN host flow.

This is deliberately *not* a public Internet reachability detector.  The
current product supports a private RFC1918 LAN invitation, and this evaluator
only proves the local facts WebJam can observe before asking a host to share:
an authenticated server, its expected UDP listener, and a usable private
address.  A remote musician's successful join remains stronger evidence and
is recorded separately by the session lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HostShareReadinessStatus(str, Enum):
    SERVER_STARTING = "server_starting"
    AUDIO_PORT_UNAVAILABLE = "audio_port_unavailable"
    PORT_INSPECTION_FAILED = "port_inspection_failed"
    NETWORK_UNAVAILABLE = "network_unavailable"
    READY_PRIVATE_LAN = "ready_private_lan"


@dataclass(frozen=True)
class HostShareReadiness:
    status: HostShareReadinessStatus
    address: str = ""

    @property
    def shareable(self) -> bool:
        return self.status is HostShareReadinessStatus.READY_PRIVATE_LAN

    @property
    def title(self) -> str:
        if self.shareable:
            return "Ready to share"
        if self.status is HostShareReadinessStatus.NETWORK_UNAVAILABLE:
            return "Connect to Wi-Fi"
        return "Getting your jam ready"

    @property
    def detail(self) -> str:
        if self.shareable:
            return (
                "WebJam found a private Wi-Fi address and a live band server. "
                "Share the link with musicians on this same network."
            )
        if self.status is HostShareReadinessStatus.NETWORK_UNAVAILABLE:
            return "Connect this Mac to the band's Wi-Fi, then try again."
        if self.status is HostShareReadinessStatus.AUDIO_PORT_UNAVAILABLE:
            return "The band server is not listening yet. WebJam will keep checking."
        if self.status is HostShareReadinessStatus.PORT_INSPECTION_FAILED:
            return "WebJam could not verify the local music port. Try starting the jam again."
        return "WebJam is starting the local band server."

    @property
    def action(self) -> str:
        if self.status is HostShareReadinessStatus.NETWORK_UNAVAILABLE:
            return "Connect to Wi-Fi"
        if self.status is HostShareReadinessStatus.PORT_INSPECTION_FAILED:
            return "Try Again"
        return "Wait for WebJam"


def evaluate_host_share_readiness(
    *,
    server_alive: bool,
    audio_port_bound: bool | None,
    private_lan_address: str,
) -> HostShareReadiness:
    """Evaluate only observable, same-LAN pre-share facts.

    ``audio_port_bound`` is ``None`` when the platform probe itself failed;
    treating it as ready would create a misleading invitation.
    """

    if not server_alive:
        return HostShareReadiness(HostShareReadinessStatus.SERVER_STARTING)
    if audio_port_bound is None:
        return HostShareReadiness(HostShareReadinessStatus.PORT_INSPECTION_FAILED)
    if not audio_port_bound:
        return HostShareReadiness(HostShareReadinessStatus.AUDIO_PORT_UNAVAILABLE)
    address = str(private_lan_address or "").strip()
    if not address:
        return HostShareReadiness(HostShareReadinessStatus.NETWORK_UNAVAILABLE)
    return HostShareReadiness(HostShareReadinessStatus.READY_PRIVATE_LAN, address)

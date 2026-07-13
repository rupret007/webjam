"""Configuration with deliberately conservative, loopback-only defaults."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Resource limits and listeners for one reference-service process.

    Listener defaults are loopback-only.  A public control listener must either
    use the built-in TLS support or opt in to insecure public control explicitly;
    that escape hatch exists for a TLS sidecar in the same trust boundary.
    """

    control_bind: str = "127.0.0.1"
    control_port: int = 47131
    relay_bind: str = "127.0.0.1"
    relay_port: int = 47132
    http_bind: str = "127.0.0.1"
    http_port: int = 47133
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    allow_insecure_public_control: bool = False

    protocol_version: int = 3
    min_session_ttl_seconds: int = 30
    max_session_ttl_seconds: int = 600
    idle_timeout_seconds: int = 90
    cleanup_interval_seconds: float = 1.0
    tombstone_ttl_seconds: int = 600

    max_sessions: int = 256
    max_connections: int = 512
    max_http_connections: int = 64
    max_ops_per_connection: int = 1_024
    connection_read_timeout_seconds: int = 30
    registrations_per_second: int = 20
    registration_burst: int = 40
    max_control_frame_bytes: int = 16_384
    max_control_ops_per_second: int = 64
    max_signal_bytes: int = 8_192
    max_signals_per_recipient: int = 16
    max_signal_bytes_per_session: int = 131_072
    max_signal_bytes_global: int = 16_777_216
    max_tombstones: int = 512

    # 1,350 inner bytes lets a constrained QUIC PacketConn carry its required
    # 1,200-byte Initial while the authenticated outer packet stays below a
    # common 1,500-byte Ethernet MTU on IPv6. Lower-MTU paths need discovery.
    max_datagram_bytes: int = 1_420
    max_relay_payload_bytes: int = 1_350
    datagrams_per_second: int = 1_000
    datagram_burst: int = 2_000
    global_datagrams_per_second: int = 100_000
    global_datagram_burst: int = 200_000
    bandwidth_bytes_per_second: int = 2_000_000
    bandwidth_burst_bytes: int = 4_000_000
    replay_window_size: int = 64

    def __post_init__(self) -> None:
        for port in (self.control_port, self.relay_port, self.http_port):
            if not 0 <= port <= 65_535:
                raise ValueError("listener ports must be between 0 and 65535")
        if self.protocol_version != 3:
            raise ValueError("the reference service only implements protocol v3")
        positive = (
            self.min_session_ttl_seconds,
            self.max_session_ttl_seconds,
            self.idle_timeout_seconds,
            self.cleanup_interval_seconds,
            self.tombstone_ttl_seconds,
            self.max_sessions,
            self.max_connections,
            self.max_http_connections,
            self.max_ops_per_connection,
            self.connection_read_timeout_seconds,
            self.registrations_per_second,
            self.registration_burst,
            self.max_control_frame_bytes,
            self.max_control_ops_per_second,
            self.max_signal_bytes,
            self.max_signals_per_recipient,
            self.max_signal_bytes_per_session,
            self.max_signal_bytes_global,
            self.max_tombstones,
            self.max_datagram_bytes,
            self.max_relay_payload_bytes,
            self.datagrams_per_second,
            self.datagram_burst,
            self.global_datagrams_per_second,
            self.global_datagram_burst,
            self.bandwidth_bytes_per_second,
            self.bandwidth_burst_bytes,
            self.replay_window_size,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("service limits must be positive")
        if self.min_session_ttl_seconds > self.max_session_ttl_seconds:
            raise ValueError("minimum TTL cannot exceed maximum TTL")
        if self.max_relay_payload_bytes + 70 > self.max_datagram_bytes:
            raise ValueError("relay payload plus authenticated envelope exceeds datagram limit")
        encoded_signal_bound = ((self.max_signal_bytes + 2) // 3) * 4 + 512
        if encoded_signal_bound > self.max_control_frame_bytes:
            raise ValueError("signal encoding exceeds the control-frame limit")
        if self.replay_window_size > 256:
            raise ValueError("replay windows larger than 256 are not supported")
        if (self.tls_cert_path is None) != (self.tls_key_path is None):
            raise ValueError("TLS certificate and key must be configured together")
        if not _is_loopback(self.control_bind):
            tls_configured = self.tls_cert_path is not None
            if not tls_configured and not self.allow_insecure_public_control:
                raise ValueError(
                    "public control bind requires TLS or explicit insecure-sidecar opt-in"
                )


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"

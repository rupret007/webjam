"""Configuration with deliberately conservative, loopback-only defaults."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path

MAX_ACTIVE_SESSION_SECONDS = 8 * 60 * 60


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Resource limits and listeners for one reference-service process.

    Listener defaults are loopback-only. Exposing control or relay requires
    built-in TLS and an approved-host admission policy. The former insecure
    sidecar flag cannot authorize an exposed listener.
    """

    control_bind: str = "127.0.0.1"
    control_port: int = 47131
    relay_bind: str = "127.0.0.1"
    relay_port: int = 47132
    http_bind: str = "127.0.0.1"
    http_port: int = 47133
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    host_client_ca_path: Path | None = None
    host_admission_path: Path | None = None
    allow_insecure_public_control: bool = False

    protocol_version: int = 3
    min_session_ttl_seconds: int = 30
    max_session_ttl_seconds: int = 600
    max_active_session_seconds: int = MAX_ACTIVE_SESSION_SECONDS
    idle_timeout_seconds: int = 90
    cleanup_interval_seconds: float = 1.0
    tombstone_ttl_seconds: int = 600

    max_sessions: int = 256
    max_sessions_per_host: int = 4
    max_connections: int = 512
    # Accepted setup is separately bounded before TLS state or a lab handler
    # is constructed. Completed connections retain max_connections above.
    max_pending_handshakes: int = 64
    control_accepts_per_second: int = 32
    control_accept_burst: int = 64
    max_http_connections: int = 64
    max_ops_per_connection: int = 1_024
    connection_read_timeout_seconds: int = 30
    tls_handshake_timeout_seconds: float = 5.0
    connection_write_timeout_seconds: float = 3.0
    connection_shutdown_timeout_seconds: float = 3.0
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
        for host in (self.control_bind, self.relay_bind, self.http_bind):
            numeric_listener_host(host)
        for value, upper in (
            (self.max_pending_handshakes, 512),
            (self.control_accepts_per_second, 1_024),
            (self.control_accept_burst, 1_024),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= upper:
                raise ValueError("control setup limits must be bounded positive integers")
        for path in (self.host_client_ca_path, self.host_admission_path):
            if path is not None and not isinstance(path, Path):
                raise ValueError("host admission paths must be filesystem paths")
        if (
            not isinstance(self.max_sessions_per_host, int)
            or isinstance(self.max_sessions_per_host, bool)
            or not 1 <= self.max_sessions_per_host <= 256
        ):
            raise ValueError("host session limit must be an integer from 1 to 256")
        for timeout in (
            self.tls_handshake_timeout_seconds,
            self.connection_write_timeout_seconds,
            self.connection_shutdown_timeout_seconds,
        ):
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not 0 < timeout <= 30
            ):
                raise ValueError("connection timeouts must be finite numbers above 0 and at most 30 seconds")
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
        if (
            not isinstance(self.max_active_session_seconds, int)
            or isinstance(self.max_active_session_seconds, bool)
            or not 1 <= self.max_active_session_seconds <= MAX_ACTIVE_SESSION_SECONDS
        ):
            raise ValueError("active session limit must be an integer from 1 to 28800 seconds")
        if self.max_active_session_seconds < self.max_session_ttl_seconds:
            raise ValueError("active session limit cannot be shorter than maximum enrollment TTL")
        if self.max_relay_payload_bytes + 70 > self.max_datagram_bytes:
            raise ValueError("relay payload plus authenticated envelope exceeds datagram limit")
        encoded_signal_bound = ((self.max_signal_bytes + 2) // 3) * 4 + 512
        if encoded_signal_bound > self.max_control_frame_bytes:
            raise ValueError("signal encoding exceeds the control-frame limit")
        if self.replay_window_size > 256:
            raise ValueError("replay windows larger than 256 are not supported")
        if (self.tls_cert_path is None) != (self.tls_key_path is None):
            raise ValueError("TLS certificate and key must be configured together")
        if (self.host_client_ca_path is None) != (self.host_admission_path is None):
            raise ValueError("host client CA and admission policy must be configured together")
        if self.host_admission_enabled and self.tls_cert_path is None:
            raise ValueError("host admission requires built-in TLS")
        if not _is_loopback(self.control_bind) or not _is_loopback(self.relay_bind):
            if self.allow_insecure_public_control:
                raise ValueError("insecure public control is unsupported; configure built-in TLS and host admission")
            if self.tls_cert_path is None or not self.host_admission_enabled:
                raise ValueError("exposed control or relay requires built-in TLS and host admission")

    @property
    def host_admission_enabled(self) -> bool:
        return self.host_client_ca_path is not None and self.host_admission_path is not None


def numeric_listener_host(host: str) -> str:
    """Return an IP literal without DNS; HTTP/UDP map localhost to IPv4.

    The control listener may expand the explicitly recognized localhost name
    into both loopback families. Configured strings remain available for that
    choice. Arbitrary DNS names and IPv6 zone identifiers are unsupported.
    """
    error = "listener addresses must be unscoped IP addresses or localhost"
    if not isinstance(host, str) or not host.isascii() or "%" in host:
        raise ValueError(error)
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        raise ValueError(error) from None


def _is_loopback(host: str) -> bool:
    return ipaddress.ip_address(numeric_listener_host(host)).is_loopback

"""Parsing and validation for user-entered Jamulus server endpoints."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

DEFAULT_JAMULUS_PORT = 22124
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class JamulusEndpointError(ValueError):
    """Raised when a server endpoint cannot be used safely."""


@dataclass(frozen=True)
class JamulusEndpoint:
    host: str
    port: int = DEFAULT_JAMULUS_PORT


def _validate_port(value: str) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise JamulusEndpointError("Port must be a number from 1 to 65535.")
    port = int(value)
    if not 1 <= port <= 65535:
        raise JamulusEndpointError("Port must be a number from 1 to 65535.")
    return port


def _validate_host(value: str) -> str:
    host = value.strip().rstrip(".")
    if not host:
        raise JamulusEndpointError("Enter the server address from your host.")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise JamulusEndpointError("Enter a valid hostname or IP address.") from exc
    if len(ascii_host) > 253 or any(
        not _HOST_LABEL.fullmatch(label) for label in ascii_host.split(".")
    ):
        raise JamulusEndpointError("Enter a valid hostname or IP address.")
    return ascii_host.lower()


def parse_jamulus_endpoint(
    text: str,
    *,
    default_port: int = DEFAULT_JAMULUS_PORT,
) -> JamulusEndpoint:
    """Parse ``host``, ``host:port``, IPv4, or bracketed IPv6 input.

    URL schemes, credentials, paths, queries, fragments, and embedded
    whitespace are rejected. Raw IPv6 is accepted with the default port;
    bracket notation is required when specifying an IPv6 port.
    """
    value = str(text or "").strip()
    if not value:
        raise JamulusEndpointError("Enter the server address from your host.")
    if any(char.isspace() for char in value):
        raise JamulusEndpointError("Server addresses cannot contain spaces.")
    if "://" in value or any(char in value for char in "/?#@"):
        raise JamulusEndpointError("Enter a hostname or IP address, not a URL.")
    if not 1 <= int(default_port) <= 65535:
        raise ValueError("default_port must be between 1 and 65535")

    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\](?::([^:]+))?", value)
        if not match:
            raise JamulusEndpointError(
                "Use [IPv6]:port when an IPv6 address includes a port."
            )
        try:
            host = str(ipaddress.IPv6Address(match.group(1)))
        except ValueError as exc:
            raise JamulusEndpointError("Enter a valid IPv6 address.") from exc
        port = _validate_port(match.group(2)) if match.group(2) else default_port
        return JamulusEndpoint(host, port)

    colon_count = value.count(":")
    if colon_count > 1:
        try:
            return JamulusEndpoint(str(ipaddress.IPv6Address(value)), default_port)
        except ValueError as exc:
            raise JamulusEndpointError(
                "Use [IPv6]:port when an IPv6 address includes a port."
            ) from exc
    if colon_count == 1:
        host_text, port_text = value.rsplit(":", 1)
        return JamulusEndpoint(_validate_host(host_text), _validate_port(port_text))
    return JamulusEndpoint(_validate_host(value), default_port)

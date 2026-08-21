"""Compiled rendezvous profiles for remote invitations.

An invitation carries only a short profile identifier. It can never introduce
an address, URL, redirect, certificate policy, or relay destination. Profiles
describe WebJam's native bounded control and exact-pair UDP relay; they do not
pretend that the reference service is HTTP, WebSocket, STUN, or TURN. Public
profiles must ship with the application; the only built-in v0.11 profile is a
loopback-only reference used by the isolated service and CI lab.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_PROFILE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
_DNS_NAME = re.compile(
    r"^(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


class RendezvousProfileError(ValueError):
    """A profile is unsafe or unavailable; messages contain no input value."""


def _profile_error(message: str = "The remote service profile is not valid.") -> None:
    raise RendezvousProfileError(message)


def _validate_profile_id(value: object) -> str:
    profile_id = str(value or "")
    if not profile_id.isascii() or not _PROFILE_ID.fullmatch(profile_id):
        _profile_error()
    return profile_id


def _service_host(value: object, *, lab_only: bool) -> str:
    host = str(value or "")
    if not host.isascii() or not host or len(host) > 253:
        _profile_error()
    if lab_only:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            _profile_error("Lab profiles must stay on loopback.")
        if not address.is_loopback:
            _profile_error("Lab profiles must stay on loopback.")
    else:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if _DNS_NAME.fullmatch(host) is None or host.lower() == "localhost":
                _profile_error("Public profiles require a provisioned DNS name.")
        else:
            _profile_error("Public profiles require a provisioned DNS name.")
    return host.lower()


def _service_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _profile_error()
    if not 1 <= value <= 65_535:
        _profile_error()
    return value


@dataclass(frozen=True, slots=True)
class RendezvousProfile:
    profile_id: str
    control_host: str
    control_port: int
    control_tls: bool
    relay_host: str
    relay_port: int
    lab_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _validate_profile_id(self.profile_id))
        object.__setattr__(
            self,
            "control_host",
            _service_host(self.control_host, lab_only=self.lab_only),
        )
        object.__setattr__(
            self,
            "control_port",
            _service_port(self.control_port),
        )
        if not isinstance(self.control_tls, bool):
            _profile_error()
        if not self.lab_only and not self.control_tls:
            _profile_error("Public profiles require TLS control.")
        object.__setattr__(
            self,
            "relay_host",
            _service_host(self.relay_host, lab_only=self.lab_only),
        )
        object.__setattr__(
            self,
            "relay_port",
            _service_port(self.relay_port),
        )

    def __repr__(self) -> str:
        return (
            "RendezvousProfile("
            f"profile_id={self.profile_id!r}, lab_only={self.lab_only!r}, "
            "endpoints=[provisioned])"
        )


class RendezvousProfileRegistry:
    """Exact, immutable profile lookup with no URL or normalization fallback."""

    def __init__(self, profiles: tuple[RendezvousProfile, ...]) -> None:
        by_id: dict[str, RendezvousProfile] = {}
        for profile in profiles:
            if profile.profile_id in by_id:
                raise RendezvousProfileError("Remote profile IDs must be unique.")
            by_id[profile.profile_id] = profile
        self._profiles = by_id

    @property
    def profile_ids(self) -> frozenset[str]:
        return frozenset(self._profiles)

    def resolve(self, profile_id: object) -> RendezvousProfile:
        canonical = _validate_profile_id(profile_id)
        try:
            return self._profiles[canonical]
        except KeyError as exc:
            raise RendezvousProfileError(
                "That invitation uses an unavailable WebJam service."
            ) from exc


REFERENCE_LOCAL_PROFILE = RendezvousProfile(
    profile_id="reference-local",
    control_host="127.0.0.1",
    control_port=47131,
    control_tls=False,
    relay_host="127.0.0.1",
    relay_port=47132,
    lab_only=True,
)

DEFAULT_RENDEZVOUS_PROFILES = RendezvousProfileRegistry(
    (REFERENCE_LOCAL_PROFILE,)
)

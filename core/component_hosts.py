"""HTTPS origin and redirect policy for executable component downloads."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit


class ComponentUrlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HttpsHostPolicy:
    """Exact hostname allowlist with downgrade- and credential-safe redirects."""

    source_hosts: frozenset[str]
    redirect_hosts: frozenset[str]
    maximum_redirects: int = 5

    def __post_init__(self) -> None:
        source = frozenset(_canonical_host(item) for item in self.source_hosts)
        redirects = frozenset(_canonical_host(item) for item in self.redirect_hosts)
        if not source or not redirects:
            raise ComponentUrlError("component host allowlists cannot be empty")
        if not source.issubset(redirects):
            raise ComponentUrlError("every source host must also be a redirect host")
        if (
            isinstance(self.maximum_redirects, bool)
            or not isinstance(self.maximum_redirects, int)
            or not 0 <= self.maximum_redirects <= 10
        ):
            raise ComponentUrlError("maximum_redirects must be between 0 and 10")
        object.__setattr__(self, "source_hosts", source)
        object.__setattr__(self, "redirect_hosts", redirects)

    def validate_source(self, url: str) -> str:
        return self._validate(url, hosts=self.source_hosts, allow_query=False)

    def validate_redirect(self, previous_url: str, location: str) -> str:
        self._validate(previous_url, hosts=self.redirect_hosts, allow_query=True)
        if not isinstance(location, str) or not location.strip():
            raise ComponentUrlError("component redirect has no destination")
        destination = urljoin(previous_url, location)
        return self._validate(
            destination, hosts=self.redirect_hosts, allow_query=True
        )

    def validate_final(self, url: str) -> str:
        return self._validate(url, hosts=self.redirect_hosts, allow_query=True)

    @staticmethod
    def _validate(
        url: str,
        *,
        hosts: frozenset[str],
        allow_query: bool,
    ) -> str:
        if not isinstance(url, str) or not url or len(url) > 4096:
            raise ComponentUrlError("component URL is invalid")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise ComponentUrlError("component URL is malformed") from exc
        host = _canonical_host(parts.hostname or "")
        if (
            parts.scheme != "https"
            or not host
            or host not in hosts
            or parts.username is not None
            or parts.password is not None
            or port not in {None, 443}
            or parts.fragment
            or (parts.query and not allow_query)
        ):
            raise ComponentUrlError("component URL violates the HTTPS host policy")
        path = parts.path
        decoded = unquote(path)
        if (
            not path.startswith("/")
            or "\x00" in decoded
            or "\\" in decoded
            or decoded != path
            or unquote(decoded) != decoded
            or any(segment in {"", ".", ".."} for segment in decoded.split("/")[1:])
        ):
            raise ComponentUrlError("component URL path is not canonical")
        return url


def _canonical_host(value: str) -> str:
    if not isinstance(value, str):
        raise ComponentUrlError("component hostname must be text")
    candidate = value.strip().rstrip(".").lower()
    if not candidate or len(candidate) > 253:
        raise ComponentUrlError("component hostname is invalid")
    try:
        ascii_host = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ComponentUrlError("component hostname is invalid") from exc
    if ascii_host != candidate:
        raise ComponentUrlError("component hostname must be canonical ASCII")
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise ComponentUrlError("component hostname cannot be an IP address")
    labels = ascii_host.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ComponentUrlError("component hostname is invalid")
    return ascii_host


JAMULUS_RELEASE_HOST_POLICY = HttpsHostPolicy(
    source_hosts=frozenset({"github.com"}),
    redirect_hosts=frozenset(
        {
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
    ),
)


__all__ = [
    "JAMULUS_RELEASE_HOST_POLICY",
    "ComponentUrlError",
    "HttpsHostPolicy",
]

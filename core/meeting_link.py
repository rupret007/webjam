"""Service-neutral meeting-link policy shared by setup, preflight, and launch.

WebJam never embeds, joins, monitors, or controls a meeting on any service.
Every conversation handoff is the same truthful action: validate one saved
HTTPS link and hand it to the operating system exactly once.  Known Webex,
Zoom, Microsoft Teams, Google Meet, and FaceTime hosts receive friendly names;
an unrelated public DNS host passes through a deliberately neutral generic
provider.  A generic host is never promoted to a trusted native integration.

The generic URL hardening builds on the original Webex-only policy in
:mod:`core.webex_url` (control characters, https-only, no userinfo, custom
ports, or percent-encoded hosts) and adds conservative public-DNS and branded
lookalike checks for the wider fallback.
"""

from __future__ import annotations

import ipaddress
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from core.webex_url import normalize_webex_url as normalize_meeting_url

# Ordered mapping: service key -> (display label, exact hosts, suffix hosts).
# A suffix entry such as ("webex.com",) admits every subdomain of webex.com
# plus the bare domain itself; exact entries admit only that hostname.
_MEETING_SERVICES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "webex": ("Webex", (), ("webex.com",)),
    "zoom": ("Zoom", (), ("zoom.us",)),
    "teams": (
        "Microsoft Teams",
        ("teams.microsoft.com", "teams.live.com"),
        (),
    ),
    "google_meet": ("Google Meet", ("meet.google.com",), ()),
    "facetime": ("FaceTime", ("facetime.apple.com",), ()),
}

SUPPORTED_MEETING_SERVICES_TEXT = (
    "Webex, Zoom, Microsoft Teams, Google Meet, FaceTime, or another "
    "meeting platform"
)

GENERIC_MEETING_SERVICE_KEY = "generic"
GENERIC_MEETING_SERVICE_LABEL = "Meeting service"

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SPECIAL_USE_DNS_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "lan",
    "home",
    "home.arpa",
    "arpa",
    "test",
    "invalid",
    "example",
    "example.com",
    "example.net",
    "example.org",
    "onion",
)
# A host that borrows a known provider's brand or domain shape does not get to
# fall through as a generic provider.  Only the exact/suffix rules above earn
# a branded identity.  This keeps ``zoom.us.evil.tld`` and ``notzoom.us`` from
# looking like a trusted Zoom destination while still allowing unrelated
# services such as ``meet.jit.si``.
_KNOWN_PROVIDER_DOMAIN_ROOTS = (
    "webex.com",
    "zoom.us",
    "microsoft.com",
    "live.com",
    "google.com",
    "apple.com",
)
_KNOWN_PROVIDER_BRAND_TOKENS = (
    "webex",
    "zoom",
    "teams",
    "microsoft",
    "google",
    "facetime",
    "apple",
)


def _service_for_host(host: str) -> str | None:
    host = host.lower().rstrip(".")
    for key, (_label, exact, suffixes) in _MEETING_SERVICES.items():
        if host in exact:
            return key
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return key
    return None


def _canonical_public_dns_host(host: str) -> str:
    """Return a conservative public-DNS spelling, or an empty string.

    Security boundary: WebJam performs no DNS lookup, HTTP request, redirect,
    or reachability probe here.  This is a user-authorized *client-side OS
    handoff*, not an SSRF-capable fetch.  We therefore reject direct IP
    literals, local/special-use names, ambiguous DNS syntax, and IDN/punycode
    spellings, then give the original validated URL to the OS once.  A DNS
    owner can still change its records after validation; WebJam never connects
    to those records itself and never treats a generic host as native/trusted.
    """

    candidate = str(host or "").lower().rstrip(".")
    if not candidate or len(candidate) > 253 or "." not in candidate:
        return ""
    if any(ord(character) > 0x7F for character in candidate):
        return ""
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        return ""
    labels = candidate.split(".")
    if any(
        not label
        or label.startswith("xn--")
        or _DNS_LABEL_RE.fullmatch(label) is None
        for label in labels
    ):
        return ""
    # A numeric final label is not a public DNS suffix and can make legacy
    # dotted-number parsing disagree with urllib's hostname view.
    if labels[-1].isdigit():
        return ""
    if any(
        candidate == suffix or candidate.endswith("." + suffix)
        for suffix in _SPECIAL_USE_DNS_SUFFIXES
    ):
        return ""
    return candidate


def _looks_like_known_provider(host: str) -> bool:
    """Reject an untrusted host that imitates one of the branded providers."""

    if _service_for_host(host) is not None:
        return False
    padded = f".{host}."
    if any(
        f".{root}." in padded
        or host == root
        or host.endswith("." + root)
        for root in _KNOWN_PROVIDER_DOMAIN_ROOTS
    ):
        return True
    return any(
        token in label
        for label in host.split(".")
        for token in _KNOWN_PROVIDER_BRAND_TOKENS
    )


# Native desktop-app identity facts per service, for the future
# service-neutral detection/bring-forward phase.  These are identity FACTS,
# not behavior: WebJam still detects, verifies, and activates only Webex
# today.  ``macos_team_id`` is the Developer ID subject.OU used to build a
# codesign requirement exactly like services/webex_app.py builds Cisco's;
# ``None`` with ``apple_system=True`` means the app is Apple-signed system
# software (anchored to Apple proper, no team OU).  ``windows_publisher_cn``
# is the Authenticode certificate common name for a future Windows publisher
# check, and ``linux`` records honest desktop availability ("native",
# "browser", or "unavailable").  Team IDs and publisher names are pinned from
# public MDM/PPPC documentation and MUST be re-verified against a real
# installed app (``codesign -d -r -`` on macOS, the signature panel on
# Windows) before any native detection ships — record that as a physical
# gate, per project discipline.
MEETING_APP_IDENTITIES: dict[str, dict[str, object]] = {
    "webex": {
        "macos_bundle_ids": ("Cisco-Systems.Spark",),
        "macos_team_id": "DE8Y96K9QP",
        "apple_system": False,
        "browser_only": False,
        "windows_publisher_cn": "Cisco Systems, Inc.",
        "linux": "native",
    },
    "zoom": {
        "macos_bundle_ids": ("us.zoom.xos",),
        "macos_team_id": "BJ4HAAB9B3",
        "apple_system": False,
        "browser_only": False,
        "windows_publisher_cn": "Zoom Video Communications, Inc.",
        "linux": "native",
    },
    "teams": {
        # New Teams first; classic Teams retained for detection fallback.
        "macos_bundle_ids": ("com.microsoft.teams2", "com.microsoft.teams"),
        "macos_team_id": "UBF8T346G9",
        "apple_system": False,
        "browser_only": False,
        "windows_publisher_cn": "Microsoft Corporation",
        "linux": "browser",
    },
    "google_meet": {
        # Google Meet ships no desktop app; the browser is the app.
        "macos_bundle_ids": (),
        "macos_team_id": None,
        "apple_system": False,
        "browser_only": True,
        "windows_publisher_cn": None,
        "linux": "browser",
    },
    "facetime": {
        "macos_bundle_ids": ("com.apple.FaceTime",),
        "macos_team_id": None,
        "apple_system": True,
        "browser_only": False,
        "windows_publisher_cn": None,
        "linux": "unavailable",
    },
}


def meeting_app_identity(service: str | None) -> dict[str, object] | None:
    """Return the immutable identity facts for a service, if known."""

    identity = MEETING_APP_IDENTITIES.get(service or "")
    return dict(identity) if identity is not None else None


def is_supported_meeting_host(host: str) -> bool:
    """Return whether a bare hostname belongs to a supported service."""

    return _service_for_host(str(host or "")) is not None


def meeting_service_label(service: str | None) -> str:
    """Return the display name for a service key, or a neutral fallback."""

    if service in _MEETING_SERVICES:
        return _MEETING_SERVICES[service][0]
    if service == GENERIC_MEETING_SERVICE_KEY:
        return GENERIC_MEETING_SERVICE_LABEL
    return "meeting service"


def meeting_link_error(raw: str) -> str | None:
    """Validate one pasted meeting link; None means allowed.

    Mirrors the hardening of :func:`core.webex_url.webex_url_error` while
    accepting known services plus an unrelated public HTTPS DNS host.
    """

    original = str(raw or "")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in original):
        return "Meeting links must not include control characters"
    url = normalize_meeting_url(raw)
    if not url:
        return (
            "paste your meeting link in Settings "
            f"({SUPPORTED_MEETING_SERVICES_TEXT})"
        )
    if any(character.isspace() for character in url) or ".." in url:
        return "URL should not contain spaces or '..'"
    if "\\" in url:
        return "Meeting links must not include backslashes"
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return "Meeting link is not a valid URL"
    if parsed.scheme != "https":
        return "Meeting links must use https://"
    if not parsed.netloc:
        return "Meeting link needs a domain"
    if parsed.username is not None or parsed.password is not None:
        return "Meeting links must not include a username or password"
    if port is not None or parsed.netloc.endswith(":"):
        return "Meeting links must not include a custom port"
    if "%" in host:
        return "Meeting link domains must not use percent encoding"
    canonical_host = _canonical_public_dns_host(host)
    if not canonical_host:
        return "Meeting link needs a public DNS domain, not an IP or local name"
    if _looks_like_known_provider(canonical_host):
        return "Meeting link domain looks like a known service but is not trusted"
    return None


def is_allowed_meeting_link(raw: str) -> bool:
    return meeting_link_error(raw) is None


def identify_meeting_service(raw: str) -> str | None:
    """Return a known or neutral-generic key for a validated link."""

    if meeting_link_error(raw) is not None:
        return None
    try:
        host = _canonical_public_dns_host(
            (urlparse(normalize_meeting_url(raw)).hostname or "").lower()
        )
    except ValueError:
        return None
    return _service_for_host(host) or GENERIC_MEETING_SERVICE_KEY


def meeting_link_hostname(raw: str) -> str:
    """Return the validated meeting hostname without room details."""

    if meeting_link_error(raw) is not None:
        return ""
    try:
        return _canonical_public_dns_host(
            urlparse(normalize_meeting_url(raw)).hostname or ""
        )
    except ValueError:
        return ""


@dataclass(frozen=True)
class MeetingProvider:
    """One provider adapter for a validated meeting link.

    The stable boundary future authenticated integrations extend: today it
    carries recognition facts only. A generic adapter carries a neutral key,
    label, and validated hostname—never path/query/fragment data.
    ``native_detection_supported`` is True solely for Webex, the one app
    WebJam verifies and activates; every other provider opens through the OS
    link handler.
    """

    key: str
    label: str
    link_hostname: str
    platform_error: str
    native_detection_supported: bool


def meeting_provider_for_link(
    raw: str, *, platform: str | None = None
) -> MeetingProvider | None:
    """Return the provider adapter for a validated link, else None."""

    service = identify_meeting_service(raw)
    if service is None:
        return None
    return MeetingProvider(
        key=service,
        label=meeting_service_label(service),
        link_hostname=meeting_link_hostname(raw),
        platform_error=meeting_handoff_platform_error(raw, platform=platform)
        or "",
        native_detection_supported=service == "webex",
    )


def meeting_handoff_platform_error(
    raw: str, *, platform: str | None = None
) -> str | None:
    """Return an honest platform restriction for a validated link, if any.

    FaceTime links only open on a Mac; every other supported service hands
    off through the default browser or its own installed app on any desktop
    platform.  Validation errors are reported by :func:`meeting_link_error`,
    not here.
    """

    service = identify_meeting_service(raw)
    effective_platform = platform if platform is not None else sys.platform
    if service == "facetime" and effective_platform != "darwin":
        return (
            "FaceTime links can only be opened on a Mac. Use another "
            "browser-capable meeting link here."
        )
    return None

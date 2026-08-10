"""Service-neutral meeting-link policy shared by setup, preflight, and launch.

WebJam never embeds, joins, monitors, or controls a meeting on any service.
Every conversation handoff is the same truthful action: validate one saved
HTTPS link against a small allowlist of known meeting services and hand it to
the operating system exactly once.  This module owns that allowlist so Webex,
Zoom, Microsoft Teams, Google Meet, and FaceTime links all pass through one
hardened policy with one vocabulary.

The generic URL hardening (control characters, https-only, no userinfo, no
custom port, no percent-encoded hosts) is deliberately identical to the
original Webex-only policy in :mod:`core.webex_url`, which remains the
canonical Webex host rule.
"""

from __future__ import annotations

import sys
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
    "Webex, Zoom, Microsoft Teams, Google Meet, or FaceTime"
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
    return "meeting service"


def meeting_link_error(raw: str) -> str | None:
    """Validate one pasted meeting link; None means allowed.

    Mirrors the hardening of :func:`core.webex_url.webex_url_error` while
    accepting every supported service's hosts.
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
    if " " in url or ".." in url:
        return "URL should not contain spaces or '..'"
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
    if port is not None:
        return "Meeting links must not include a custom port"
    if "%" in host:
        return "Meeting link domains must not use percent encoding"
    if "." not in host or host in {"localhost", "127.0.0.1"}:
        return "Meeting link needs a real domain, not localhost"
    if _service_for_host(host) is None:
        return (
            "Meeting link must be a "
            f"{SUPPORTED_MEETING_SERVICES_TEXT} link"
        )
    return None


def is_allowed_meeting_link(raw: str) -> bool:
    return meeting_link_error(raw) is None


def identify_meeting_service(raw: str) -> str | None:
    """Return the service key for a fully validated link, else None."""

    if meeting_link_error(raw) is not None:
        return None
    try:
        host = (urlparse(normalize_meeting_url(raw)).hostname or "").lower()
    except ValueError:
        return None
    return _service_for_host(host)


def meeting_link_hostname(raw: str) -> str:
    """Return the validated meeting hostname without room details."""

    if meeting_link_error(raw) is not None:
        return ""
    try:
        return (
            urlparse(normalize_meeting_url(raw)).hostname or ""
        ).lower().rstrip(".")
    except ValueError:
        return ""


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
            "FaceTime links can only be opened on a Mac. Use a "
            "Webex, Zoom, Microsoft Teams, or Google Meet link here."
        )
    return None

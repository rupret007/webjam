"""Webex meeting-link policy shared by setup, preflight, and external launch."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_webex_url(raw: str) -> str:
    # Trim ordinary pasted spaces, but preserve control characters so the
    # validator can reject them instead of silently changing the destination.
    url = str(raw or "").strip(" ")
    if url and "://" not in url and "." in url.split("/", 1)[0]:
        return "https://" + url
    return url


def _host_is_webex(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == "webex.com" or host.endswith(".webex.com")


def webex_url_error(raw: str) -> str | None:
    original = str(raw or "")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in original):
        return "Webex links must not include control characters"
    url = normalize_webex_url(raw)
    if not url:
        return "paste your Webex Meeting or Personal Room link in Settings"
    if " " in url or ".." in url:
        return "URL should not contain spaces or '..'"
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return "Webex link is not a valid URL"
    if parsed.scheme != "https":
        return "Webex links must use https://"
    if not parsed.netloc:
        return "Webex link needs a domain"
    if parsed.username is not None or parsed.password is not None:
        return "Webex links must not include a username or password"
    if port is not None:
        return "Webex links must not include a custom port"
    if "%" in host:
        return "Webex link domains must not use percent encoding"
    if "." not in host or host in {"localhost", "127.0.0.1"}:
        return "Webex link needs a real domain, not localhost"
    if not _host_is_webex(host):
        return "Webex link must be on webex.com"
    return None


def is_allowed_webex_url(raw: str) -> bool:
    return webex_url_error(raw) is None


def webex_site_hostname(raw: str) -> str:
    """Return the validated Webex site hostname without room details."""

    if webex_url_error(raw) is not None:
        return ""
    url = normalize_webex_url(raw)
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""

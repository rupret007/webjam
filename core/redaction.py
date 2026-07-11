"""Shared redaction helpers for diagnostics, logs, and support payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


REDACTED = "[redacted]"
REDACTED_FIELDS = {"webex_guest_issuer_secret", "sentry_dsn"}
REDACTED_NAME_HINTS = ("secret", "token", "password", "passwd", "dsn", "api_key")


def redact_webex_url(value: str) -> str:
    """Keep only a trusted Webex origin; meeting destinations are sensitive."""
    raw = str(value or "")
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return REDACTED
    if parsed.scheme.lower() != "https" or not (
        host == "webex.com" or host.endswith(".webex.com")
    ):
        return REDACTED
    # Never reuse ``netloc``: it can contain userinfo and a port. Diagnostics
    # retain only the already-validated Webex hostname.
    origin = f"https://{host}"
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return f"{origin}/{REDACTED}"
    return origin


def should_redact_name(name: str) -> bool:
    lname = str(name or "").lower()
    return name in REDACTED_FIELDS or any(h in lname for h in REDACTED_NAME_HINTS)


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(data)
    for field, value in list(redacted.items()):
        if field == "webex_url" and value:
            redacted[field] = redact_webex_url(str(value))
            continue
        if value and should_redact_name(field):
            redacted[field] = REDACTED
    return redacted


_ASSIGNMENT_RE = re.compile(
    r"(?i)(['\"]?[\w.-]*(?:secret|token|password|passwd|dsn|api[_-]?key)[\w.-]*['\"]?)"
    r"(\s*[:=]\s*)(['\"]?)([^'\"\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_RE = re.compile(r"(?i)([?&](?:secret|token|password|api[_-]?key)=)[^&#\s]+")
_WEBEX_URL_RE = re.compile(
    r"(?i)https://[^\s/'\"<>)]*webex\.com(?::\d+)?(?:[/?#][^\s'\"<>)]*)?"
)


def redact_text(text: str) -> str:
    out = str(text or "")
    out = _ASSIGNMENT_RE.sub(r"\1\2\3" + REDACTED, out)
    out = _BEARER_RE.sub("Bearer " + REDACTED, out)
    out = _QUERY_RE.sub(r"\1" + REDACTED, out)
    out = _WEBEX_URL_RE.sub(lambda match: redact_webex_url(match.group(0)), out)
    return out

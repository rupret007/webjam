"""Shared redaction helpers for diagnostics, logs, and support payloads."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[redacted]"
REDACTED_FIELDS = {"webex_guest_issuer_secret", "sentry_dsn"}
REDACTED_NAME_HINTS = ("secret", "token", "password", "passwd", "dsn", "api_key")


def should_redact_name(name: str) -> bool:
    lname = str(name or "").lower()
    return name in REDACTED_FIELDS or any(h in lname for h in REDACTED_NAME_HINTS)


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(data)
    for field, value in list(redacted.items()):
        if value and should_redact_name(field):
            redacted[field] = REDACTED
    return redacted


_ASSIGNMENT_RE = re.compile(
    r"(?i)(['\"]?[\w.-]*(?:secret|token|password|passwd|dsn|api[_-]?key)[\w.-]*['\"]?)"
    r"(\s*[:=]\s*)(['\"]?)([^'\"\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_RE = re.compile(r"(?i)([?&](?:secret|token|password|api[_-]?key)=)[^&#\s]+")


def redact_text(text: str) -> str:
    out = str(text or "")
    out = _ASSIGNMENT_RE.sub(r"\1\2\3" + REDACTED, out)
    out = _BEARER_RE.sub("Bearer " + REDACTED, out)
    out = _QUERY_RE.sub(r"\1" + REDACTED, out)
    return out

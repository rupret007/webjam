"""Shared redaction helpers for diagnostics, logs, and support payloads.

The helpers in this module deliberately favour removing too much over leaking a
credential or a musician's personal information.  Support reports are useful
only after their *shape* has been allowlisted (see :mod:`core.support_bundle`);
these functions are the second line of defence for nested values and free text.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


REDACTED = "[redacted]"
REDACTED_FIELDS = {"webex_guest_issuer_secret", "sentry_dsn"}
REDACTED_NAME_HINTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "private_key",
    "api_key",
    "apikey",
    "auth_key",
    "authorization",
    "cookie",
    "dsn",
    "invite_code",
    "invite_url",
    "rpc_key",
    "rpc_secret",
    "serial_number",
    "device_serial",
    "device_uid",
)

_PERSONAL_FIELDS = {
    "email",
    "email_address",
    "participant_name",
    "musician_name",
    "display_name",
    "full_name",
    "chat_message",
    "private_notes",
    "session_notes",
    "transcript",
    "lyrics",
}


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
    lname = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return (
        lname in REDACTED_FIELDS
        or lname in _PERSONAL_FIELDS
        or any(hint in lname for hint in REDACTED_NAME_HINTS)
    )


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively redacted copy of ``data``.

    Earlier versions only inspected the top level, which meant a future nested
    settings object could silently reintroduce a token leak.  Container values
    are now copied recursively; the caller's object is never mutated.
    """

    redacted: dict[str, Any] = {}
    for raw_field, value in data.items():
        field = str(raw_field)
        lname = field.lower()
        if lname == "webex_url" and value:
            redacted[field] = redact_webex_url(str(value))
            continue
        has_value = not (
            value is None or value is False or (isinstance(value, str) and not value)
        )
        if has_value and should_redact_name(field):
            redacted[field] = REDACTED
            continue
        redacted[field] = redact_value(value)
    return redacted


def redact_value(value: Any) -> Any:
    """Recursively redact JSON-like values without retaining object paths."""

    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item) for item in value]
    if isinstance(value, Path):
        return redact_text(str(value))
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


_SENSITIVE_NAME_PATTERN = (
    r"(?:secret|token|password|passwd|passphrase|credential|"
    r"private[_-]?key|dsn|api[_-]?key|apikey|auth[_-]?key|authorization|cookie|"
    r"invite[_-]?(?:code|url)|rpc[_-]?(?:key|secret)|serial[_-]?(?:number|id)|"
    r"device(?:[_-]?(?:name|serial|uid))?|email(?:[_-]?address)?|participant[_-]?name|"
    r"musician[_-]?name|display[_-]?name|full[_-]?name|chat[_-]?message|"
    r"private[_-]?notes?|session[_-]?notes?|transcript|lyrics)"
)
_QUOTED_ASSIGNMENT_RE = re.compile(
    r"(?i)(['\"]?[\w.-]*"
    + _SENSITIVE_NAME_PATTERN
    + r"[\w.-]*['\"]?\s*[:=]\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)(['\"]?[\w.-]*"
    + _SENSITIVE_NAME_PATTERN
    + r"[\w.-]*['\"]?\s*[:=])(?!\s*['\"])(\s*)([^\r\n,;}\]]+)"
)
_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Digest)\s+[A-Za-z0-9._~+/=,:-]+"
)
_AUTH_HEADER_RE = re.compile(
    r"(?im)\b(Authorization|Proxy-Authorization)\s*:\s*[^\r\n]+"
)
_COOKIE_HEADER_RE = re.compile(r"(?im)\b(Set-Cookie|Cookie)\s*:\s*[^\r\n]+")
_ENV_LINE_RE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z][A-Z0-9_]{1,}\s*=)[^{}\r\n]*$"
)
_CLI_SECRET_RE = re.compile(
    r"(?i)(?P<flag>--?[a-z0-9_.-]*(?:secret|token|password|passwd|passphrase|"
    r"credential|private[_-]?key|dsn|api[_-]?key|apikey|auth[_-]?key|"
    r"authorization|cookie|invite[_-]?(?:code|url)|rpc[_-]?(?:key|secret))"
    r"[a-z0-9_.-]*)(?P<separator>\s+|=)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"(?is)-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----"
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_DEVICE_ID_RE = re.compile(
    r"(?i)\b((?:device\s+)?(?:serial(?:\s+(?:number|id))?|uid))"
    r"(\s*(?:[:=#]|\bis\b)\s*)(['\"]?)[A-Z0-9][A-Z0-9._:-]{3,}(['\"]?)"
)
_SESSION_TITLE_RE = re.compile(
    r"(?im)(\bsession\s+title\s+(?:set|updated)\s*:\s*)[^\r\n]+"
)
_QUERY_RE = re.compile(
    r"(?i)([?&](?:secret|token|password|passphrase|credential|api[_-]?key|apikey|"
    r"auth|authorization|code|invite|key|session|signature|sig|jwt|rpc[_-]?key)"
    r"=)[^&#\s]+"
)
_WEBEX_URL_RE = re.compile(
    r"(?i)https?://[^\s/'\"<>)]*webex\.com(?::\d+)?(?:[/?#][^\s'\"<>)]*)?"
)
_WEBJAM_URL_RE = re.compile(r"(?i)\bwebjam:(?://)?[^\s'\"<>)]*")
_URL_USERINFO_RE = re.compile(
    r"(?i)\b(https?://)[^/@\s]+:[^/@\s]+@"
)
_EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])"
)
_COMMON_HOME_RE = re.compile(
    r"(?i)(?:/Users/[^/\s'\"<>]+|/home/[^/\s'\"<>]+|"
    r"[A-Z]:\\Users\\[^\\\s'\"<>]+)"
)


def _redact_home_paths(text: str) -> str:
    out = text
    candidates: set[str] = set()
    for raw in (os.environ.get("HOME"), os.environ.get("USERPROFILE")):
        if raw:
            candidates.add(str(raw).rstrip("/\\"))
    try:
        candidates.add(str(Path.home()).rstrip("/\\"))
    except (OSError, RuntimeError):
        pass
    for home in sorted((item for item in candidates if len(item) >= 4), key=len, reverse=True):
        out = re.sub(re.escape(home), "$HOME", out, flags=re.IGNORECASE)
    return _COMMON_HOME_RE.sub("$HOME", out)


def redact_text(text: str) -> str:
    out = str(text or "")
    out = _redact_home_paths(out)
    out = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, out)
    out = _JWT_RE.sub(REDACTED, out)
    out = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}: {REDACTED}", out)
    out = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group(1)}: {REDACTED}", out)
    out = _ENV_LINE_RE.sub(r"\1" + REDACTED, out)
    out = _CLI_SECRET_RE.sub(
        lambda match: (
            f"{match.group('flag')}{match.group('separator')}{REDACTED}"
        ),
        out,
    )
    out = _DEVICE_ID_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", out
    )
    out = _SESSION_TITLE_RE.sub(r"\1" + REDACTED, out)
    out = _QUOTED_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group('quote')}{REDACTED}{match.group('quote')}"
        ),
        out,
    )
    out = _ASSIGNMENT_RE.sub(r"\1\2" + REDACTED, out)
    out = _AUTH_SCHEME_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", out)
    out = _QUERY_RE.sub(r"\1" + REDACTED, out)
    out = _WEBEX_URL_RE.sub(lambda match: redact_webex_url(match.group(0)), out)
    out = _WEBJAM_URL_RE.sub("webjam://" + REDACTED, out)
    out = _URL_USERINFO_RE.sub(r"\1" + REDACTED + "@", out)
    out = _EMAIL_RE.sub("[redacted-email]", out)
    return out

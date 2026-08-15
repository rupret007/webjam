"""Shared redaction helpers for diagnostics, logs, and support payloads.

The helpers in this module deliberately favour removing too much over leaking a
credential or a musician's personal information.  Support reports are useful
only after their *shape* has been allowlisted (see :mod:`core.support_bundle`);
these functions are the second line of defence for nested values and free text.
"""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PureWindowsPath
import re
import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


REDACTED = "[redacted]"
REDACTED_PATH = "[redacted-path]"
REDACTED_FIELDS = {"webex_guest_issuer_secret", "sentry_dsn"}
REDACTED_NAME_HINTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "capability",
    "private_key",
    "api_key",
    "apikey",
    "auth_key",
    "authorization",
    "cookie",
    "dsn",
    "invite_code",
    "invite_url",
    "invite_reference",
    "session_reference",
    "session_id",
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

# Sentry's event schema and Python's logging records use a small, stable set of
# keys for filesystem identities.  Keep this deliberately exact: treating any
# field that merely contains words such as ``source`` or ``file`` as a path
# would erase useful fixed diagnostics and still be an unreliable path parser.
_PRIVATE_PATH_FIELDS = {
    "abs_path",
    "absolute_path",
    "bundle_path",
    "config_path",
    "destination_path",
    "directory",
    "file",
    "file_name",
    "filename",
    "filepath",
    "log_file",
    "log_path",
    "manifest_path",
    "media_path",
    "path",
    "pathname",
    "profile_path",
    "recording_path",
    "recovery_path",
    "root_path",
    "source_path",
    "take_path",
    "temp_dir",
}
_TELEMETRY_FREE_TEXT_FIELDS = {
    "description",
    "error",
    "formatted",
    "message",
    "reason",
    "title",
    "value",
}

# This is only a fail-closed test for a *complete string value* supplied as a
# logging argument.  Embedded free text is handled only at known Sentry
# free-text fields below; we intentionally do not guess at arbitrary slashes.
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC_PATH_RE = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/][^\\/]+")
# A URL's first slash is followed by another slash and its second is preceded
# by a slash, so the two guards exclude it without excluding a real root after
# punctuation such as ``path:/Volumes/...``.
_POSIX_PATH_FRAGMENT_RE = re.compile(r"(?<![A-Za-z0-9_/\\])/(?![/\s])")
_WINDOWS_PATH_FRAGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|//[^/\s]+/)"
)
_HOME_PATH_FRAGMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:"
    r"\$HOME(?:[/\\]|$)"
    r"|%(?:HOME|HOMEDRIVE|HOMEPATH|USERPROFILE)%[/\\]"
    r"|~(?:[^/\\\s]+)?[/\\]"
    r")"
)


def _normalized_field_name(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def should_redact_path_name(name: object) -> bool:
    """Return whether a structured field is defined to contain a private path."""

    return _normalized_field_name(name) in _PRIVATE_PATH_FIELDS


def _is_absolute_path_value(value: str) -> bool:
    """Recognize a complete POSIX, Windows-drive, UNC, or redacted-home path."""

    candidate = str(value or "").strip()
    if not candidate:
        return False
    if candidate == "$HOME" or candidate.startswith(("$HOME/", "$HOME\\")):
        return True
    if candidate.startswith(("~/", "~\\")):
        return True
    if re.match(r"(?i)^~[^/\\\s]+[/\\]", candidate) is not None:
        return True
    if (
        re.match(
            r"(?i)^%(?:HOME|HOMEDRIVE|HOMEPATH|USERPROFILE)%[/\\]",
            candidate,
        )
        is not None
    ):
        return True
    if candidate.startswith("/"):
        return True
    if _WINDOWS_DRIVE_PATH_RE.match(candidate) is not None:
        return True
    if _WINDOWS_UNC_PATH_RE.match(candidate) is not None:
        return True
    try:
        return PureWindowsPath(candidate).is_absolute()
    except (OSError, ValueError):
        return False


def redact_log_text(value: str) -> str:
    """Redact one diagnostic string as a unit if it contains an absolute path.

    This scanner runs only at the explicit log/Sentry free-text boundary.  It
    first removes URLs and credentials with :func:`redact_text`, then recognizes
    rooted filesystem syntax.  If found, the whole field is discarded rather
    than guessing where a path containing spaces ends.
    """

    safe = redact_text(value)
    if (
        _POSIX_PATH_FRAGMENT_RE.search(safe) is not None
        or _WINDOWS_PATH_FRAGMENT_RE.search(safe) is not None
        or _HOME_PATH_FRAGMENT_RE.search(safe) is not None
        or "file://" in safe.lower()
    ):
        return REDACTED_PATH
    return safe


def redact_log_value(value: Any) -> Any:
    """Return one log-format argument without private path or exception text.

    Path objects and complete absolute-path strings are replaced as a unit.
    Exception strings are never trusted because OS and decoder exceptions often
    embed a musician-selected filename; retaining the type keeps the useful
    failure category.  Containers are copied recursively for mapping-style and
    structured logging.
    """

    if isinstance(value, BaseException):
        return f"[{type(value).__name__}]"
    if isinstance(value, os.PathLike):
        return REDACTED_PATH
    if isinstance(value, Mapping):
        return {
            str(redact_log_value(str(field))): (
                REDACTED_PATH
                if should_redact_path_name(field) and item not in (None, "", False)
                else redact_log_value(item)
            )
            for field, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [redact_log_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return tuple(redact_log_value(item) for item in value)
    if isinstance(value, str):
        if _is_absolute_path_value(value):
            return REDACTED_PATH
        return redact_log_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Do not invoke an arbitrary object's potentially path-bearing ``repr``.
    return f"[{type(value).__name__}]"


def redact_telemetry_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Redact a Sentry event/breadcrumb using its structured field contract.

    Unlike ordinary support-data projection, telemetry may contain SDK-created
    stack-frame path fields and exception/log free text.  Exact path fields are
    removed wholesale.  Known free-text fields retain normal useful messages,
    but a complete absolute-path value is removed before the usual credential,
    meeting-link, address, and home-identity scrubber runs.
    """

    redacted: dict[str, Any] = {}
    for raw_field, value in data.items():
        raw_field_text = str(raw_field)
        field = str(redact_log_value(raw_field_text))
        lname = _normalized_field_name(raw_field_text)
        has_value = not (
            value is None or value is False or (isinstance(value, str) and not value)
        )
        if has_value and should_redact_path_name(raw_field_text):
            redacted[field] = REDACTED_PATH
            continue
        if has_value and should_redact_name(raw_field_text):
            redacted[field] = REDACTED
            continue
        if isinstance(value, Mapping):
            redacted[field] = redact_telemetry_mapping(value)
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            redacted[field] = [
                redact_telemetry_mapping(item)
                if isinstance(item, Mapping)
                else redact_log_value(item)
                for item in value
            ]
            continue
        if isinstance(value, str) and lname in _TELEMETRY_FREE_TEXT_FIELDS:
            redacted[field] = redact_log_value(value)
            continue
        redacted[field] = redact_log_value(value)
    return redacted


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


def redact_meeting_url(value: str) -> str:
    """Keep only a known provider's origin; rooms and generic hosts are private.

    Known Webex, Zoom, Teams, Google Meet, and FaceTime origins are public
    product infrastructure and remain useful diagnostics.  An accepted generic
    link can contain a private company or community domain, so its host and
    complete destination fail closed to the plain redaction marker.
    """

    from core.meeting_link import (
        GENERIC_MEETING_SERVICE_KEY,
        identify_meeting_service,
        meeting_link_hostname,
    )

    raw = str(value or "")
    service = identify_meeting_service(raw)
    if service in {None, GENERIC_MEETING_SERVICE_KEY}:
        return REDACTED
    host = meeting_link_hostname(raw)
    if not host:
        return REDACTED
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return REDACTED
    # Never reuse ``netloc``: it can contain userinfo and a port.
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
            # The historical settings field can now hold any supported
            # meeting service's link; keep only its validated origin.
            redacted[field] = redact_meeting_url(str(value))
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
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(Bearer|Basic|Digest)\s+[A-Za-z0-9._~+/=,:-]+")
_AUTH_HEADER_RE = re.compile(
    r"(?im)\b(Authorization|Proxy-Authorization)\s*:\s*[^\r\n]+"
)
_COOKIE_HEADER_RE = re.compile(r"(?im)\b(Set-Cookie|Cookie)\s*:\s*[^\r\n]+")
_ENV_LINE_RE = re.compile(r"(?im)^(\s*(?:export\s+)?[A-Z][A-Z0-9_]{1,}\s*=)[^{}\r\n]*$")
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
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
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
_WEBJAM_URL_RE = re.compile(r"(?i)\bwebjam:(?://)?[^\s'\"<>)]*")
# Every HTTP(S) URL in diagnostics crosses the meeting-link privacy boundary.
# The callback retains origin-only for a fully validated *known* provider and
# fully redacts everything else.  That prevents a generic company/community
# meeting host from leaking merely because no static regex knew its brand.
_HTTP_URL_RE = re.compile(r"(?i)\bhttps?://[^\s'\"<>)]*")
_URL_USERINFO_RE = re.compile(r"(?i)\b(https?://)[^/@\s]+:[^/@\s]+@")
_EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])"
)
_COMMON_HOME_RE = re.compile(
    r"(?i)(?:/Users/[^/\s'\"<>]+|/home/[^/\s'\"<>]+|"
    r"[A-Z]:\\Users\\[^\\\s'\"<>]+)"
)
_IPV4_CANDIDATE_RE = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
_BRACKETED_IPV6_CANDIDATE_RE = re.compile(r"\[[0-9A-Fa-f:.%_-]+\]")
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Za-z_])(?:[0-9A-Fa-f]*:){2,}[0-9A-Fa-f]*"
    r"(?:%[A-Za-z0-9_.-]+)?(?![0-9A-Za-z_])"
)


def _valid_ip_literal(value: str) -> bool:
    candidate = value.strip("[]")
    # IPv6 scope identifiers disclose interface names and are not accepted by
    # all Python versions.  Validate the address portion and redact the scope
    # together with it.
    candidate = candidate.split("%", 1)[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _redact_ip_literals(text: str) -> str:
    """Remove valid IPv4/IPv6 literals without eating versions or timestamps."""

    def replace(match: re.Match[str]) -> str:
        return "[redacted-ip]" if _valid_ip_literal(match.group(0)) else match.group(0)

    out = _IPV4_CANDIDATE_RE.sub(replace, text)
    out = _BRACKETED_IPV6_CANDIDATE_RE.sub(replace, out)
    return _IPV6_CANDIDATE_RE.sub(replace, out)


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
    for home in sorted(
        (item for item in candidates if len(item) >= 4), key=len, reverse=True
    ):
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
        lambda match: f"{match.group('flag')}{match.group('separator')}{REDACTED}",
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
    out = _HTTP_URL_RE.sub(lambda match: redact_meeting_url(match.group(0)), out)
    out = _WEBJAM_URL_RE.sub("webjam://" + REDACTED, out)
    out = _URL_USERINFO_RE.sub(r"\1" + REDACTED + "@", out)
    out = _EMAIL_RE.sub("[redacted-email]", out)
    out = _redact_ip_literals(out)
    return out

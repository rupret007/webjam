"""Finding Drawpile and speaking its documented invitation language.

Art's shared canvas is Drawpile's canvas.  WebJam does not draw a stroke, does
not run a Drawpile server, and does not hold a Drawpile account.  It brokers
one thing: the invitation that Drawpile already knows how to produce and
consume, so a guest who joined a WebJam room does not have to be handed a
second link through a second product.

The whole module is therefore about two questions, and nothing else:

* **Is a real Drawpile installed here?**  WebJam bundles no Drawpile binary
  and makes no publisher claim about one, so discovery is limited to explicit
  known install locations.  There is no ``PATH`` search and no glob: a
  wildcard would let any executable named ``drawpile`` earlier on the path
  inherit an affordance the artist thinks they granted to Drawpile.  When
  nothing is found, callers fail closed and say "install Drawpile" rather
  than pretending a canvas is open.
* **Is this text a Drawpile invitation, and what does Drawpile want it to
  look like?**  Drawpile's desktop client accepts a session URL positionally
  or through ``--join``, and its Join page silently rewrites the
  ``https://…/invites/…`` link that the Invite dialog copies into the
  ``drawpile://`` form.  The ``--join`` path does not perform that rewrite, so
  WebJam performs the same documented normalization itself and hands Drawpile
  a URL it can act on directly.

An invitation can carry a session password, so a parsed invitation is treated
as private: it is never rendered in a repr, a log line, or a diagnostic.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

#: Drawpile's own scheme, plus the two WebSocket schemes its client accepts as
#: session URLs.  Anything else is not a canvas invitation.
DRAWPILE_SCHEME = "drawpile"
CANVAS_URL_SCHEMES: frozenset[str] = frozenset({DRAWPILE_SCHEME, "ws", "wss"})

#: Drawpile's default TCP port.  A web invite that names it is normalized
#: without an explicit port, exactly as Drawpile's own Join page does.
DRAWPILE_DEFAULT_PORT = 27750

MAX_CANVAS_URL_CHARS = 512
MAX_CANVAS_LABEL_CHARS = 80

# ``/invites/<host>[:<port>]/<session-id>[:<secret>]`` is the path shape
# Drawpile's Invite dialog produces and its Join page consumes.
_WEB_INVITE_PATH = re.compile(
    r"\A/invites/(?P<host>[^:/]+)(?::(?P<port>[0-9]{1,5}))?/+"
    r"(?P<session>[A-Za-z0-9:-]{1,50})/*\Z"
)
_SESSION_ID = re.compile(r"\A[A-Za-z0-9:-]{1,50}\Z")
_HOSTNAME = re.compile(r"\A[A-Za-z0-9._-]{1,253}\Z")

INSTALL_DRAWPILE_MESSAGE = (
    "WebJam could not find Drawpile on this computer. Install Drawpile from "
    "drawpile.net, then open the shared canvas again."
)
NOT_A_CANVAS_INVITE_MESSAGE = (
    "That is not a Drawpile invitation. In Drawpile choose Session → Invite, "
    "copy the link, and paste the whole thing."
)


class DrawpileError(RuntimeError):
    """A bounded, credential-free Drawpile failure safe to show a person."""


class DrawpileUnavailableError(DrawpileError):
    """No real Drawpile executable could be found on this computer."""


@dataclass(frozen=True, slots=True, repr=False)
class CanvasInvite:
    """One normalized Drawpile session URL and the bits safe to display.

    ``join_url`` may embed a session password, so it is deliberately absent
    from ``__repr__``.  ``server_label`` and ``session_label`` carry only what
    a person needs to recognize the canvas they are about to open.
    """

    join_url: str
    server_label: str
    session_label: str
    carries_password: bool

    def __repr__(self) -> str:
        return (
            "CanvasInvite("
            f"server_label={self.server_label!r}, "
            f"session_label={self.session_label!r}, "
            f"carries_password={self.carries_password}, url=[redacted])"
        )


def _bounded_url_text(value: object) -> str:
    if not isinstance(value, str):
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    text = value.strip()
    if not text or len(text) > MAX_CANVAS_URL_CHARS:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    if any(character in text for character in ("\0", "\r", "\n", "\t", " ")):
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    if not text.isascii() or not all(character.isprintable() for character in text):
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    return text


def _hostname(value: str) -> str:
    """Accept a plain hostname/IPv4, or a bracketed IPv6 literal."""

    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        inner = host[1:-1]
        if not inner or any(character not in "0123456789abcdefABCDEF:." for character in inner):
            raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
        return host.lower()
    if _HOSTNAME.fullmatch(host) is None:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    return host.lower()


def _port(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE) from None
    if not 1 <= port <= 65535:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    return port


def _session_label(path: str) -> str:
    session = path.strip("/")
    if _SESSION_ID.fullmatch(session) is None:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    # An invite-code link is ``<session>:<secret>``.  Only the session part is
    # a name; the secret half is a credential and must never be displayed.
    return session.split(":", 1)[0][:MAX_CANVAS_LABEL_CHARS]


def _rebuild(
    *,
    scheme: str,
    host: str,
    port: int | None,
    session: str,
    query_tokens: list[str],
) -> str:
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((scheme, netloc, f"/{session}", "&".join(query_tokens), ""))


def parse_canvas_invite(text: object) -> CanvasInvite:
    """Parse and normalize one Drawpile invitation, failing closed.

    Both forms Drawpile hands a person are accepted: the ``drawpile://`` (or
    ``ws``/``wss``) session URL, and the ``https://…/invites/…`` link its
    Invite dialog copies.  The web form is rewritten into the session form the
    way Drawpile's own Join page rewrites it, including moving the password
    fragment into the ``p`` query parameter, so ``--join`` can act on it.
    """

    raw = _bounded_url_text(text)
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE) from exc
    scheme = parts.scheme.lower()
    if parts.username or parts.password:
        # Drawpile fills user info from its own environment variables. A
        # pasted link carrying credentials is not something WebJam relays.
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)

    if scheme in CANVAS_URL_SCHEMES:
        host = _hostname(parts.hostname or "")
        port = _port(parts.port)
        session = _session_label(parts.path)
        tokens = _bounded_query(parts.query)
        if parts.fragment:
            tokens = _with_password(tokens, parts.fragment)
        return CanvasInvite(
            join_url=_rebuild(
                scheme=scheme,
                host=host,
                port=port,
                session=parts.path.strip("/"),
                query_tokens=tokens,
            ),
            server_label=host,
            session_label=session,
            carries_password=_carries_password(tokens),
        )

    if scheme not in {"http", "https"}:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)

    match = _WEB_INVITE_PATH.match(parts.path)
    if match is None:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    host = _hostname(match.group("host"))
    port = _port(match.group("port"))
    if port == DRAWPILE_DEFAULT_PORT:
        port = None
    session = match.group("session")
    tokens = _bounded_query(parts.query)
    if parts.fragment:
        tokens = _with_password(tokens, parts.fragment)
    return CanvasInvite(
        join_url=_rebuild(
            scheme=DRAWPILE_SCHEME,
            host=host,
            port=port,
            session=session,
            query_tokens=tokens,
        ),
        server_label=host,
        session_label=_session_label(session),
        carries_password=_carries_password(tokens),
    )


def _bounded_query(query: str) -> list[str]:
    """Keep Drawpile's query verbatim, bare flags included.

    Drawpile writes ``?v1&w&web&nsfm``: valueless flags that a strict
    key/value parser would reject and a lenient one would silently rewrite.
    Both would change a URL WebJam is only relaying, so the tokens are
    validated and preserved exactly as Drawpile wrote them.
    """

    if not query:
        return []
    tokens = query.split("&")
    if len(tokens) > 8:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    for token in tokens:
        key, separator, value = token.partition("=")
        if not key or len(key) > 16 or len(value) > 128:
            raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
        if not separator and value:  # pragma: no cover - partition invariant
            raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    return tokens


def _with_password(tokens: list[str], password: str) -> list[str]:
    """Move an invite link's ``#password`` fragment into Drawpile's ``p``.

    This is the rewrite Drawpile's own Join page performs when a person
    pastes a web invite. ``--join`` skips it, so WebJam does it instead;
    without it a guest handed a passworded personal session would be stopped
    by a password prompt WebJam already had the answer to.
    """

    if len(password) > 128:
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    kept = [token for token in tokens if token.partition("=")[0] != "p"]
    kept.append(f"p={quote(password, safe='')}")
    return kept


def _carries_password(tokens: list[str]) -> bool:
    return any(token.partition("=")[0] == "p" for token in tokens)


# ---------------------------------------------------------------------------
# Finding an installed Drawpile
# ---------------------------------------------------------------------------

#: Explicit install locations only.  WebJam ships no Drawpile and searches no
#: ``PATH``, so an executable is either where Drawpile's own installers put it
#: or the artist named it themselves.
DEFAULT_DRAWPILE_CANDIDATES: tuple[str, ...] = (
    # macOS
    "/Applications/Drawpile.app/Contents/MacOS/drawpile",
    "~/Applications/Drawpile.app/Contents/MacOS/drawpile",
    # Windows
    r"C:\Program Files\Drawpile\drawpile.exe",
    r"C:\Program Files (x86)\Drawpile\drawpile.exe",
    # Linux distribution packages, Flatpak exports, Homebrew, and Snap
    "/usr/bin/drawpile",
    "/usr/local/bin/drawpile",
    "/var/lib/flatpak/exports/bin/net.drawpile.drawpile",
    "~/.local/share/flatpak/exports/bin/net.drawpile.drawpile",
    "/opt/homebrew/bin/drawpile",
    "/snap/bin/drawpile",
)


def _executable_target(candidate: str | os.PathLike[str]) -> Path | None:
    """Return the real executable a candidate names, or ``None``.

    Symlinks are followed rather than rejected because Flatpak, Homebrew, and
    Snap all publish their entry points as links, and WebJam is not claiming
    provenance for a binary it did not ship.  What it does insist on is that
    the resolved object is a real, executable, regular file: a dangling link
    or a directory must fail closed instead of reaching a launcher.
    """

    try:
        path = Path(candidate).expanduser()
    except (TypeError, ValueError):
        return None
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISREG(details.st_mode):
        return None
    if os.name == "posix" and not details.st_mode & 0o111:
        return None
    return resolved


def find_drawpile(candidates: object = None) -> Path | None:
    """Return the first real Drawpile executable among ``candidates``."""

    if candidates is None:
        candidates = DEFAULT_DRAWPILE_CANDIDATES
    if isinstance(candidates, (str, bytes, os.PathLike)):
        candidates = (candidates,)
    try:
        entries = list(candidates)
    except TypeError:
        return None
    for entry in entries:
        if not isinstance(entry, (str, os.PathLike)):
            continue
        resolved = _executable_target(entry)
        if resolved is not None:
            return resolved
    return None


def drawpile_host_arguments(executable: str | os.PathLike[str]) -> list[str]:
    """Build the command that opens Drawpile on its own Host page.

    Drawpile's host flow is a dialog: it asks for a title, a password, and
    which server to use, and it needs an account decision on the public
    server.  WebJam cannot answer those for the artist and does not try, so
    the honest automation is to land them on exactly the right page.
    """

    return [str(executable), "--start-page", "host"]


def drawpile_join_arguments(
    executable: str | os.PathLike[str], invite: CanvasInvite
) -> list[str]:
    """Build the command that joins one already-normalized canvas.

    ``--join`` is used rather than a positional argument so Drawpile does not
    have to guess whether the value is a file path or a session URL.
    """

    if not isinstance(invite, CanvasInvite):
        raise DrawpileError(NOT_A_CANVAS_INVITE_MESSAGE)
    return [str(executable), "--join", invite.join_url]


def drawpile_download_url() -> str:
    """The one place WebJam points a person who has no Drawpile."""

    return "https://drawpile.net/download/"


def platform_supports_drawpile() -> bool:
    """Whether this platform has documented Drawpile install locations."""

    return sys.platform in {"darwin", "win32"} or sys.platform.startswith("linux")


__all__ = [
    "CANVAS_URL_SCHEMES",
    "DEFAULT_DRAWPILE_CANDIDATES",
    "DRAWPILE_DEFAULT_PORT",
    "DRAWPILE_SCHEME",
    "INSTALL_DRAWPILE_MESSAGE",
    "MAX_CANVAS_URL_CHARS",
    "NOT_A_CANVAS_INVITE_MESSAGE",
    "CanvasInvite",
    "DrawpileError",
    "DrawpileUnavailableError",
    "drawpile_download_url",
    "drawpile_host_arguments",
    "drawpile_join_arguments",
    "find_drawpile",
    "parse_canvas_invite",
    "platform_supports_drawpile",
]

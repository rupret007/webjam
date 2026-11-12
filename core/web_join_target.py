"""Build-bound first-party destination for an opt-in browser invitation.

This selects a page, not a relay, authority, permission or network fallback.
Frozen apps ignore environment/user settings and require their package manifest.
The allowlist contains no operator-supplied URLs or third-party origins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
from types import MappingProxyType

from core.build_info import build_id


MANIFEST_NAME = "webjam-web-join-target.json"
UNAVAILABLE = "Web Join destination is unavailable for this build."
_BUILD = re.compile(r"[a-f0-9]{7,64}")
_MAX_BYTES = 512
_PAGES = MappingProxyType({
    "public": "https://stalemate.band/join/",
    "private-beta": "https://stalemate-hangout-beta.jeffstory007.workers.dev/join/",
})
_GAME_NIGHT_ORIGIN = _PAGES["private-beta"].removesuffix("/join/")
_GAME_NIGHT_PAGE = _GAME_NIGHT_ORIGIN + "/beta/?game-night=1"
_GAME_NIGHT_ROOM_LINK = re.compile(
    re.escape(_GAME_NIGHT_PAGE)
    + r"(?:&via=(?:stalemate|rad-dad))?#game=game-[a-f0-9]{6}x[a-f0-9]{6}&instance=[a-f0-9]{32}"
)
_GAME_NIGHT_GUEST_LINK = re.compile(
    re.escape(_GAME_NIGHT_ORIGIN + "/beta/game-night/guest#invite=")
    + r"[A-Za-z0-9_-]{20,200}\.[A-Za-z0-9_-]{43}"
)
GAME_NIGHT_GAMES = MappingProxyType({
    "chess": "Royal Flush Chess",
    "crapeights": "Crappy Eights",
    "turdrummy": "TurdRummy",
    "turdspades": "TurdSpades",
    "turdjack": "Crapjack 21",
    "turdtris": "Turdtris Sprint",
    "turdomania": "Turdomania Duel",
    "turdrush": "Turd Rush Race",
    "turdanoid": "Turdanoid Brick Race",
})


class WebJoinTargetUnavailable(ValueError):
    def __init__(self):
        super().__init__(UNAVAILABLE)


def is_browser_invite_url(value: str) -> bool:
    """Recognize an app-issued browser link for guidance, without opening it."""
    page, separator, fragment = value.partition("#")
    return bool(separator and page in _PAGES.values() and fragment.startswith("k="))


def build_target_document(target: str, build: str, *, git_ref: str = "") -> bytes:
    """Encode known build inputs; private-beta is never a tagged release."""
    if (
        type(target) is not str or target not in _PAGES
        or type(build) is not str or _BUILD.fullmatch(build) is None
        or type(git_ref) is not str
        or (target == "private-beta" and git_ref.startswith("refs/tags/"))
    ):
        raise WebJoinTargetUnavailable()
    value = {"version": 1, "build": build, "target": target}
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def parse_target_document(document: bytes, expected_build: str) -> str:
    if type(document) is not bytes or not 1 <= len(document) <= _MAX_BYTES:
        raise WebJoinTargetUnavailable()
    try:
        value = json.loads(document.decode("ascii"))
        if (
            type(value) is not dict or set(value) != {"version", "build", "target"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["build"] != expected_build
            or build_target_document(value["target"], value["build"]) != document
        ):
            raise WebJoinTargetUnavailable()
        return _PAGES[value["target"]]
    except (UnicodeError, ValueError, KeyError, TypeError):
        raise WebJoinTargetUnavailable() from None


def read_target_document(path: Path, expected_build: str) -> str:
    """Read a bounded regular package file without following a final symlink."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise WebJoinTargetUnavailable()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= _MAX_BYTES:
                raise WebJoinTargetUnavailable()
            document = os.read(descriptor, _MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        return parse_target_document(document, expected_build)
    except (OSError, ValueError, TypeError):
        raise WebJoinTargetUnavailable() from None


def _manifest_path() -> Path:
    if sys.platform == "darwin":
        # PyInstaller may alias Resources data below Frameworks. Select the
        # canonical bundle data file, then retain the final-symlink refusal.
        return Path(sys.executable).resolve().parent.parent / "Resources" / MANIFEST_NAME
    return Path(__file__).resolve().parents[1] / MANIFEST_NAME


def web_join_page() -> str:
    """Resolve only this build's target before any bridge or provider starts."""
    if not getattr(sys, "frozen", False):
        return _PAGES["public"]
    return read_target_document(_manifest_path(), build_id())


def game_night_page() -> str | None:
    """Offer Game Night only in the verified private community build."""
    try:
        return _GAME_NIGHT_PAGE if web_join_page() == _PAGES["private-beta"] else None
    except WebJoinTargetUnavailable:
        return None


def game_night_game_page(game: str) -> str | None:
    entrance = game_night_page()
    if entrance is None or game not in GAME_NIGHT_GAMES:
        return None
    return entrance + "&table=" + game


def game_night_destination(pasted_link: str) -> str | None:
    """Accept an exact Hang Out game invitation, room address or entrance."""
    entrance = game_night_page()
    if entrance is None or not isinstance(pasted_link, str) or len(pasted_link) > 400:
        return None
    link = pasted_link.strip()
    if not link or link == entrance:
        return entrance
    return link if _GAME_NIGHT_GUEST_LINK.fullmatch(link) or _GAME_NIGHT_ROOM_LINK.fullmatch(link) else None

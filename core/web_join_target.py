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


class WebJoinTargetUnavailable(ValueError):
    def __init__(self):
        super().__init__(UNAVAILABLE)


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

"""Finding a program WebJam did not ship, honestly.

Art hands two jobs to real open-source programs: Drawpile paints the shared
canvas, and Krita hosts the AI image generator.  Neither is bundled, so WebJam
makes no publisher claim about either, and the only thing it can honestly
assert is that the thing it is about to launch is a real executable file at a
location that program's own installer uses.

The rules are deliberately narrow, and the same for every program:

* **Explicit absolute locations only.**  There is no ``PATH`` search and no
  glob.  A wildcard would let any executable named ``drawpile`` or ``krita``
  earlier on the path inherit an affordance the artist believes they granted to
  the real program.
* **Symlinks are followed, not rejected.**  Flatpak, Homebrew, and Snap all
  publish their entry points as links, so refusing links would refuse ordinary
  installs while proving nothing about provenance.
* **The resolved object must be a real, executable, regular file.**  A
  dangling link, a directory, or a non-executable file fails closed rather
  than reaching a launcher.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def resolve_program(candidate: object) -> Path | None:
    """Return the real executable a candidate names, or ``None``."""

    if not isinstance(candidate, (str, os.PathLike)):
        return None
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


def find_program(candidates: object) -> Path | None:
    """Return the first real executable among ``candidates``."""

    if isinstance(candidates, (str, bytes, os.PathLike)):
        candidates = (candidates,)
    try:
        entries = list(candidates)
    except TypeError:
        return None
    for entry in entries:
        resolved = resolve_program(entry)
        if resolved is not None:
            return resolved
    return None


def resolve_directory(candidate: object) -> Path | None:
    """Return a real directory a candidate names, or ``None``.

    Used for a program's own resource folder, where WebJam reads nothing and
    only needs to know whether an add-on the artist installed is present.
    """

    if not isinstance(candidate, (str, os.PathLike)):
        return None
    try:
        path = Path(candidate).expanduser()
    except (TypeError, ValueError):
        return None
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


def configured_candidates(
    settings: object, field: str, defaults: tuple[str, ...]
) -> tuple[str, ...]:
    """Return an explicit override when one is configured, else ``defaults``."""

    configured = getattr(settings, field, None)
    if isinstance(configured, (list, tuple)):
        entries = tuple(
            str(item).strip() for item in configured if str(item or "").strip()
        )
        if entries:
            return entries
    return defaults


def has_no_wildcards(candidates: tuple[str, ...]) -> bool:
    """Whether a candidate list is free of glob syntax."""

    return not any(
        character in candidate for candidate in candidates for character in "*?["
    )


__all__ = [
    "configured_candidates",
    "find_program",
    "has_no_wildcards",
    "resolve_directory",
    "resolve_program",
]

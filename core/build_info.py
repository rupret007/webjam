"""Small, privacy-safe build provenance helper.

Frozen applications read the commit captured by ``webjam.spec`` at build
time.  Source checkouts fall back to their local Git HEAD.  No repository
path, command output, environment dump, or other machine-specific value is
returned to diagnostics.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def build_id() -> str:
    """Return a validated source commit, or ``""`` when it is unavailable."""

    configured = str(os.environ.get("WEBJAM_BUILD_ID", "") or "").strip()
    if _COMMIT_RE.fullmatch(configured):
        return configured.lower()

    packaged = Path(__file__).resolve().parents[1] / "webjam-build-id.txt"
    try:
        value = packaged.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    if _COMMIT_RE.fullmatch(value):
        return value.lower()

    if getattr(sys, "frozen", False):
        return ""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = completed.stdout.strip() if completed.returncode == 0 else ""
    return value.lower() if _COMMIT_RE.fullmatch(value) else ""


__all__ = ["build_id"]

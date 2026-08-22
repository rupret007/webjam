"""Small, privacy-safe build provenance helper.

Frozen applications read the commit captured by ``webjam.spec`` at build
time.  Source checkouts fall back to their local Git HEAD.  No repository
path, command output, environment dump, or other machine-specific value is
returned to diagnostics.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def desktop_target(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the canonical packaged desktop target for this process.

    The release workflow uses the same names for archives and CI matrix
    entries.  Keeping the mapping here prevents an Intel build from recording
    Apple-Silicon evidence (and gives Windows/Linux packages truthful local
    pilot identity when the operator-only workflow is used there).
    """

    platform_value = str(platform_name or sys.platform).strip().lower()
    machine_value = str(machine or platform.machine()).strip().lower()
    if machine_value in {"arm64", "aarch64"}:
        architecture = "arm64"
    elif machine_value in {"x86_64", "amd64", "x64"}:
        architecture = "x64"
    else:
        return ""

    if platform_value == "darwin":
        operating_system = "macos"
    elif platform_value in {"win32", "cygwin", "msys"}:
        operating_system = "windows"
    elif platform_value.startswith("linux"):
        operating_system = "linux"
    else:
        return ""
    return f"{operating_system}-{architecture}"


def build_id() -> str:
    """Return a validated source commit, or ``""`` when it is unavailable."""

    packaged = Path(__file__).resolve().parents[1] / "webjam-build-id.txt"
    try:
        value = packaged.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    if getattr(sys, "frozen", False):
        # A frozen app's signed bundle is the authority. Environment values
        # must never select a different expected sidecar build.
        return value.lower() if _COMMIT_RE.fullmatch(value) else ""

    configured = str(os.environ.get("WEBJAM_BUILD_ID", "") or "").strip()
    if _COMMIT_RE.fullmatch(configured):
        return configured.lower()
    if _COMMIT_RE.fullmatch(value):
        return value.lower()
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


__all__ = ["build_id", "desktop_target"]

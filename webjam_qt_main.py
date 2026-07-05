#!/usr/bin/env python3
"""
WebJam — Conductor UI entry point.

Run with:
    python webjam_qt_main.py

The legacy Tkinter app (``legacy/webjam_app_enhanced.py``) remains available for
fallback use until the Qt port reaches feature parity.
"""

from __future__ import annotations

import sys


def _check_python_version() -> None:
    """Refuse to run on Python < 3.10 with a clear message instead of a cryptic ImportError."""
    if sys.version_info < (3, 10):
        print(
            f"WebJam requires Python 3.10 or newer.\n"
            f"You have Python {sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}.\n\n"
            f"Install a newer Python from https://python.org and try again.",
            file=sys.stderr,
        )
        sys.exit(1)


_check_python_version()

from webjam_qt.app import run  # noqa: E402  (must follow version check)


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""
WebJam — Conductor UI entry point.

Run with:
    python webjam_qt_main.py

The legacy Tkinter app (``webjam_app_enhanced.py``) remains available for
fallback use until the Qt port reaches feature parity.
"""

from __future__ import annotations

import sys

from webjam_qt.app import run


if __name__ == "__main__":
    sys.exit(run())

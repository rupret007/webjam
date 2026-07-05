"""Path setup for the quarantined legacy test suite.

These tests are NOT collected by CI (which runs ``pytest tests/``).
Run them manually with ``pytest legacy/tests/`` from the repo root —
they need tkinter (python3-tk) and the legacy modules.
"""
import sys
from pathlib import Path

_legacy = Path(__file__).resolve().parents[1]
for _p in (str(_legacy), str(_legacy.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def cleanup_temp_file(path: str) -> None:
    import time
    for _ in range(10):
        try:
            os.remove(path)
            return
        except (PermissionError, OSError):
            time.sleep(0.05)

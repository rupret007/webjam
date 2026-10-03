"""Fresh native source process for the same complete private frozen workflow."""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    from services.reference_studio_packaged_smoke import (
        _validated_result_path, run_frozen_reference_studio_smoke,
    )
    from PySide6.QtCore import __version__ as pyside_version, qVersion
    from shiboken6 import __version__ as shiboken_version

    result = _validated_result_path(args.result)
    record = {"pid": os.getpid(), "python": sys.version, "interpreter": sys.executable,
              "pyside": pyside_version, "shiboken": shiboken_version, "qt": qVersion(),
              "worker_gc": os.environ.get("WEBJAM_SMOKE_WORKER_GC", "ordinary"),
              "qt_fatal_environment": {name: os.environ.get(name) for name in
                                       ("QT_FATAL_WARNINGS", "QT_FATAL_CRITICALS")}}
    (result.parent / "source-process.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    faulthandler.enable(all_threads=True)
    return run_frozen_reference_studio_smoke(result_path=result)


if __name__ == "__main__":
    raise SystemExit(main())

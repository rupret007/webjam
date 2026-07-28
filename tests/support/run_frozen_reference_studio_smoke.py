"""Run a frozen WebJam Reference Studio probe with a hard deadline."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


SUCCESS_MARKER = "WebJam Reference Studio frozen-runtime smoke passed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--cwd", type=Path)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Frozen WebJam binary is missing: {binary}")

    with tempfile.TemporaryDirectory(
        prefix="webjam-reference-studio-smoke-"
    ) as directory:
        result_path = Path(directory) / "result.txt"
        environment = os.environ.copy()
        environment["WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME"] = "1"
        environment["WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT"] = str(result_path)
        environment.pop("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME", None)
        environment.pop("WEBJAM_SMOKE_LAUNCH_ONLY", None)
        environment.pop("WEBJAM_SMOKE_AUTOSTART_AUDIO", None)
        try:
            completed = subprocess.run(
                [str(binary)],
                cwd=(arguments.cwd or binary.parent).resolve(),
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(
                "Frozen Reference Studio runtime smoke exceeded 60 seconds."
            ) from exc
        combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        result = (
            result_path.read_text(encoding="utf-8") if result_path.is_file() else ""
        )
        if completed.returncode != 0 or result != SUCCESS_MARKER + "\n":
            raise SystemExit(
                "Frozen Reference Studio runtime smoke failed.\n"
                f"exit={completed.returncode}\n{combined[-4000:]}"
            )
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

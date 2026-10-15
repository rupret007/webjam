"""Build PR #372's webjam-fabric sidecar for this harness only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]


class SidecarError(RuntimeError):
    """The fabric sidecar could not be built."""


def product_sha(product_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=product_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def build_sidecar(product_root: Path, output: Path | None = None) -> Path:
    transport = product_root / "transport"
    if not (transport / "cmd" / "webjam-fabric" / "main.go").is_file():
        raise SidecarError("The product tree has no webjam-fabric sources.")
    digest = product_sha(product_root)
    target = output or (HARNESS_ROOT / "out" / "cross-network-acceptance" / "webjam-fabric")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("GOTOOLCHAIN", "go1.25.13")
    env.setdefault("CGO_ENABLED", "0")
    result = subprocess.run(
        [
            "go",
            "build",
            "-trimpath",
            f"-ldflags=-s -w -X main.buildID={digest}",
            "-o",
            str(target),
            "./cmd/webjam-fabric",
        ],
        cwd=transport,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.is_file():
        raise SidecarError("Could not build webjam-fabric from the product tree.")
    target.chmod(0o755)
    return target

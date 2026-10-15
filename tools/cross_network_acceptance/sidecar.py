"""Build webjam-fabric."""

import os
import subprocess
from pathlib import Path

from tools.cross_network_acceptance import HarnessError

HARNESS_ROOT = Path(__file__).resolve().parents[2]


class SidecarError(HarnessError):
    pass


def require_transport_binary(value: str = "") -> str:
    raw = str(value or os.environ.get("WEBJAM_TRANSPORT_BINARY") or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SidecarError("transport binary missing")
    return str(path.resolve())


def product_sha(product_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=product_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def resolve_sidecar_output(output: Path | None = None, *, cwd: Path | None = None) -> Path:
    target = Path(output if output is not None else (
        HARNESS_ROOT / "out" / "cross-network-acceptance" / "webjam-fabric"))
    return (target if target.is_absolute() else (cwd or Path.cwd()) / target).resolve()


def build_sidecar(product_root: Path, output: Path | None = None) -> Path:
    transport = product_root / "transport"
    if not (transport / "cmd" / "webjam-fabric" / "main.go").is_file():
        raise SidecarError("No webjam-fabric sources.")
    digest = product_sha(product_root)
    target = resolve_sidecar_output(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("GOTOOLCHAIN", "go1.25.13")
    env.setdefault("CGO_ENABLED", "0")
    result = subprocess.run(
        ["go", "build", "-trimpath", f"-ldflags=-s -w -X main.buildID={digest}",
         "-o", str(target), "./cmd/webjam-fabric"],
        cwd=transport, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0 or not target.is_file():
        raise SidecarError("Could not build webjam-fabric from the product tree.")
    target.chmod(0o755)
    return target

"""Verify a staged WebJam transport with the production runtime policy.

This is a package gate, not an application entry point. It intentionally emits
only a fixed success line and never prints the sidecar event timeline.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re

from services.transport_runtime import TransportProcess


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _manifest_digest(path: Path) -> str:
    encoded = path.read_bytes()
    if len(encoded) not in {64, 65} or (len(encoded) == 65 and encoded[-1:] != b"\n"):
        raise ValueError("transport manifest is not canonical")
    try:
        digest = encoded.rstrip(b"\n").decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("transport manifest is not canonical") from exc
    if _SHA256.fullmatch(digest) is None:
        raise ValueError("transport manifest is not canonical")
    return digest


def verify(binary: Path, manifest: Path, build_id: str, machine: str) -> None:
    digest = _manifest_digest(manifest)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != digest:
        raise ValueError("transport manifest does not match the signed binary")

    process = TransportProcess(
        binary,
        expected_build=build_id,
        expected_sha256=digest,
        expected_machine=machine,
        require_platform_signature=True,
    )
    ready = process.start()
    try:
        if (ready.event_type, ready.code, ready.state, ready.build) != (
            "ready",
            "ok",
            "idle",
            build_id,
        ):
            raise ValueError("transport ready event did not match the package")
        hello = process.hello()
        if (hello.event_type, hello.code, hello.state, hello.build) != (
            "hello",
            "ok",
            "idle",
            build_id,
        ):
            raise ValueError("transport hello event did not match the package")
    finally:
        process.stop()
    if process.running or not any(
        event.event_type == "stopped"
        and event.code == "ok"
        and event.state == "stopped"
        for event in process.timeline
    ):
        raise ValueError("transport did not stop cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--machine", required=True, choices=("arm64", "x86_64"))
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.build_id) is None:
        parser.error("build ID must be one lowercase 40-character Git commit")
    verify(
        args.binary.resolve(),
        args.manifest.resolve(),
        args.build_id,
        args.machine,
    )
    print("Packaged transport verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verify public package routing metadata without launching audio or a provider."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from core.web_join_target import (
    WebJoinTargetUnavailable,
    build_target_document,
    parse_target_document,
    read_target_document,
)


def verify(manifest: Path, build: str, target: str) -> None:
    expected = parse_target_document(build_target_document(target, build), build)
    if read_target_document(manifest, build) != expected:
        raise WebJoinTargetUnavailable()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        verify(args.manifest, args.build_id, args.target)
    except WebJoinTargetUnavailable:
        print("Packaged Web Join destination verification failed.", file=sys.stderr)
        return 1
    print("Packaged Web Join destination verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Remove the separately built cryptography wheel from the Intel runtime lock."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+!-]+)\s*\\?$"
)
_EXPECTED_VERSION = "50.0.0"
_EXPECTED_SDIST_SHA256 = (
    "eeac2acb5a20ed25e0ad6d1df9891a520b78b404266b6d11778f25d5d691a6c9"
)


def _normalized(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def prepare(
    source: Path,
    output: Path,
    *,
    wheel_sha256: str | None = None,
) -> dict[str, object]:
    """Write a lock with cryptography removed or rebound to a verified wheel."""

    if not source.is_file() or source.is_symlink():
        raise ValueError("source lock is missing or unsafe")
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to replace an output lock")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError("output lock parent is missing or unsafe")

    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    pin_starts: list[int] = []
    for index, line in enumerate(lines):
        if _PIN.fullmatch(line.rstrip("\r\n")):
            pin_starts.append(index)
        stripped = line.strip()
        if stripped.startswith(
            ("--index-url", "--extra-index-url", "--find-links", "-e ")
        ):
            raise ValueError("source lock contains a forbidden package source")
    if len(pin_starts) < 2:
        raise ValueError("source lock does not contain a complete dependency graph")

    blocks: list[tuple[int, int, re.Match[str]]] = []
    for position, start in enumerate(pin_starts):
        end = pin_starts[position + 1] if position + 1 < len(pin_starts) else len(lines)
        match = _PIN.fullmatch(lines[start].rstrip("\r\n"))
        assert match is not None
        block_text = "".join(lines[start:end])
        if "--hash=sha256:" not in block_text:
            raise ValueError(f"locked distribution lacks hashes: {match.group('name')}")
        blocks.append((start, end, match))

    selected = [
        block for block in blocks if _normalized(block[2].group("name")) == "cryptography"
    ]
    if len(selected) != 1:
        raise ValueError("source lock must contain cryptography exactly once")
    start, end, match = selected[0]
    if match.group("version") != _EXPECTED_VERSION:
        raise ValueError("source lock cryptography version is not reviewed")
    cryptography_block = "".join(lines[start:end])
    if f"--hash=sha256:{_EXPECTED_SDIST_SHA256}" not in cryptography_block:
        raise ValueError("source lock lacks the reviewed cryptography sdist hash")

    filtered = "".join(lines[:start] + lines[end:])
    if re.search(r"(?mi)^cryptography==", filtered):
        raise ValueError("filtered runtime lock still contains cryptography")
    if wheel_sha256 is not None:
        if re.fullmatch(r"[0-9a-f]{64}", wheel_sha256) is None:
            raise ValueError("verified wheel SHA-256 is malformed")
        if filtered and not filtered.endswith("\n"):
            filtered += "\n"
        filtered += (
            f"cryptography=={_EXPECTED_VERSION} \\\n"
            f"    --hash=sha256:{wheel_sha256}\n"
            "    # via verified Intel macOS source build\n"
        )
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(filtered)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise

    return {
        "cryptography_version": _EXPECTED_VERSION,
        "cryptography_wheel_bound": wheel_sha256 is not None,
        "remaining_distributions": len(blocks) - 1,
        "source_distributions_removed": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--wheel-sha256")
    args = parser.parse_args(argv)
    try:
        result = prepare(
            args.source,
            args.output,
            wheel_sha256=args.wheel_sha256,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

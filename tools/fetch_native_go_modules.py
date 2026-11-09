"""Retry completed transient Go fetch failures without changing trust or locks."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

ATTEMPTS = 3
COMMAND_TIMEOUT = 90
BACKOFF_SECONDS = (2, 5)
TRANSIENT_ERRORS = (
    "dial tcp: lookup ",
    "i/o timeout",
    "tls handshake timeout",
    "connection reset by peer",
    "unexpected eof",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)
INTEGRITY_ERRORS = ("checksum mismatch", "security error", "ziphash mismatch")


def transient_fetch_failure(stderr: str) -> bool:
    """Unknown or mixed failures are fatal, including any integrity failure."""
    text = stderr.lower()
    if any(marker in text for marker in INTEGRITY_ERRORS):
        return False
    errors = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("go: downloading ")
    ]
    return bool(errors) and all(
        any(marker in line for marker in TRANSIENT_ERRORS) for line in errors
    )


def lock_fingerprint(directory: Path) -> tuple[str, str]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Go source directory must be a regular directory")
    hashes = []
    for name in ("go.mod", "go.sum"):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Go module locks must be regular files")
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return tuple(hashes)


def fetch_modules(directory: Path, *, runner=None, sleep=None) -> bool:
    runner = subprocess.run if runner is None else runner
    sleep = time.sleep if sleep is None else sleep
    try:
        expected = lock_fingerprint(directory)
        for attempt in range(ATTEMPTS):
            if lock_fingerprint(directory) != expected:
                raise ValueError("Go module locks changed before download")
            result = runner(
                ["go", "mod", "download"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                check=False,
            )
            if lock_fingerprint(directory) != expected:
                raise ValueError("Go module locks changed during download")
            if result.returncode == 0:
                verified = runner(
                    ["go", "mod", "verify"],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=COMMAND_TIMEOUT,
                    check=False,
                )
                if lock_fingerprint(directory) != expected:
                    raise ValueError("Go module locks changed during verification")
                if verified.returncode != 0:
                    print("Go module cache verification failed; stopping.")
                    return False
                print("Go modules fetched and verified; source locks unchanged.")
                return True
            if not transient_fetch_failure(result.stderr):
                print("Go fetch failed outside the transient retry policy; stopping.")
                return False
            if attempt + 1 == ATTEMPTS:
                print("Go fetch transient retry allowance exhausted; stopping.")
                return False
            delay = BACKOFF_SECONDS[attempt]
            print(f"Go fetch transient network failure; retrying in {delay}s.")
            sleep(delay)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        # Raw Go output can include credential-bearing proxy URLs. Do not log it.
        print(f"Go fetch stopped safely ({type(error).__name__}).")
    return False


def main() -> int:
    if len(sys.argv) != 1:
        print("Usage: python tools/fetch_native_go_modules.py")
        return 2
    source = Path(__file__).resolve().parents[1] / "transport"
    return 0 if fetch_modules(source) else 1


if __name__ == "__main__":
    raise SystemExit(main())

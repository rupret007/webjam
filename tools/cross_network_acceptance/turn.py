"""Coturn on the public network + REST credentials + relayed-byte proof."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from deploy.relay.turn_credentials import REALM, mint
from tools.cross_network_acceptance import HarnessError
from tools.cross_network_acceptance.topology import required_bin

_USAGE = re.compile(r"\brb=(\d+)\b.*\bsb=(\d+)\b")
_DATA = re.compile(r"Data indication|incoming packet Data", re.IGNORECASE)


def turnserver_bin() -> str:
    found = shutil.which("turnserver")
    if found:
        return found
    for folder in ("/usr/sbin", "/usr/bin"):
        candidate = Path(folder) / "turnserver"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise HarnessError("Need turnserver.")


def mint_credentials(secret: str, host: str, port: int) -> dict[str, object]:
    return mint(secret=secret, uris=[f"turn:{host}:{port}?transport=udp"])


def turn_command(
    *, bind_host: str, port: int, secret: str, log_path: Path, pid_path: Path,
) -> list[str]:
    return [
        turnserver_bin(), "-n",
        f"--listening-ip={bind_host}", f"--listening-port={port}",
        f"--relay-ip={bind_host}", f"--external-ip={bind_host}",
        f"--realm={REALM}", "--use-auth-secret", f"--static-auth-secret={secret}",
        "--no-tls", "--no-dtls", "--no-cli", "--fingerprint",
        "--no-stdout-log", f"--log-file={log_path}", "--simple-log", "-v",
        f"--pidfile={pid_path}",
    ]


def relayed_bytes(log_text: str) -> int:
    total = 0
    for match in _USAGE.finditer(log_text or ""):
        total += int(match.group(1)) + int(match.group(2))
    if total:
        return total
    return 8 * len(_DATA.findall(log_text or ""))


def conntrack_dump(ns: str) -> str:
    conntrack = shutil.which("conntrack") or "/usr/sbin/conntrack"
    if not Path(conntrack).is_file():
        return ""
    ip = required_bin("ip")
    import subprocess
    return subprocess.run(
        [ip, "netns", "exec", ns, conntrack, "-L"],
        check=False, capture_output=True, text=True,
    ).stdout

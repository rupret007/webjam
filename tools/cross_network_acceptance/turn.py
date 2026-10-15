"""Coturn + POST /turn-credentials mint."""

from __future__ import annotations

import os
import re
import shutil
import signal
import time
from pathlib import Path

from deploy.relay.turn_credentials import mint_coturn
from tools.cross_network_acceptance import HarnessError
from tools.cross_network_acceptance.topology import required_bin

REALM = "webjam"
CREDENTIAL_PORT = 8787
_USAGE = re.compile(r"\brb=(\d+)\b.*\bsb=(\d+)\b")


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
    import deploy.relay.turn_credentials as tc

    saved = (tc.SECRET, tc.EXTERNAL_IP, tc.LISTEN_PORT)
    try:
        tc.SECRET = secret.encode("utf-8")
        tc.EXTERNAL_IP = host
        tc.LISTEN_PORT = int(port)
        return mint_coturn(time.time())
    finally:
        tc.SECRET, tc.EXTERNAL_IP, tc.LISTEN_PORT = saved


def credential_server_env(secret: str, host: str, port: int) -> dict[str, str]:
    return {
        "TURN_STATIC_AUTH_SECRET": secret,
        "TURN_EXTERNAL_IP": host,
        "TURN_LISTEN_PORT": str(int(port)),
        "TURN_LISTEN_ADDR": host,
        "TURN_CREDENTIAL_PORT": str(CREDENTIAL_PORT),
    }


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
    """Coturn session rb=+sb= only."""
    total = 0
    for match in _USAGE.finditer(log_text or ""):
        total += int(match.group(1)) + int(match.group(2))
    return total


def stop_turn(pid_path: Path, timeout_s: float = 8.0) -> None:
    """SIGTERM the pidfile process so session close can flush rb=/sb=."""
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid <= 1:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


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


def credential_script() -> str:
    return str(Path(__file__).resolve().parents[2] / "deploy" / "relay" / "turn_credentials.py")

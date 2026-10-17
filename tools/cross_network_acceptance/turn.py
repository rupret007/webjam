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
PROOF_USER = re.compile(r"webjam-proof-[0-9a-f]{32}")
_SESSION = re.compile(r"\bsession ([0-9]+): (.*)")
_METHOD = re.compile(
    r"(?:origin <[^>]*> )?realm <webjam> user <([^>]+)>: "
    r"incoming packet (ALLOCATE|CHANNEL_BIND) processed, success"
)
_PEER_USAGE = re.compile(
    r"peer usage: realm=<webjam>, username=<([^>]+)>, "
    r"rp=[0-9]+, rb=([0-9]+), sp=[0-9]+, sb=([0-9]+)"
)


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


def credential_server_env(
    secret: str, host: str, port: int, *, proof_user: str,
) -> dict[str, str]:
    return {
        "TURN_STATIC_AUTH_SECRET": secret,
        "TURN_EXTERNAL_IP": host,
        "TURN_LISTEN_PORT": str(int(port)),
        "TURN_LISTEN_ADDR": host,
        "TURN_CREDENTIAL_PORT": str(CREDENTIAL_PORT),
        "WEBJAM_TEST_TURN_USER": proof_user,
        "CLOUDFLARE_TURN_API_TOKEN": "",
        "CLOUDFLARE_TURN_BASE_URL": "",
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


def relayed_bytes(log_text: str, *, proof_user: str) -> int:
    """Sum coturn peer rb=+sb= for this run's allocated, channel-bound sessions.

    The private harness credential server mints this fresh user suffix only
    for the proof run. Session ID AND full username must match successful
    ALLOCATE and CHANNEL_BIND records before any usage is admitted. Coturn's
    client-side `usage:` includes protocol traffic; only `peer usage:` counts.
    These are interval counters (coturn resets them after each usage dump).
    """
    if not PROOF_USER.fullmatch(proof_user):
        return 0
    username = re.compile(r"[0-9]+:" + re.escape(proof_user))
    allocated: set[tuple[str, str]] = set()
    bound: set[tuple[str, str]] = set()
    total = 0
    for line in (log_text or "").splitlines():
        session = _SESSION.search(line)
        if session is None:
            continue
        session_id, record = session.groups()
        method = _METHOD.fullmatch(record)
        if method and username.fullmatch(method[1]):
            key = (session_id, method[1])
            if method[2] == "ALLOCATE":
                allocated.add(key)
            elif key in allocated:
                bound.add(key)
        usage = _PEER_USAGE.fullmatch(record)
        if usage and (session_id, usage[1]) in bound:
            total += int(usage[2]) + int(usage[3])
    return total


def wait_turn_usage(log_path: Path, timeout_s: float = 8.0, *, proof_user: str) -> str:
    """Re-read the coturn log until scoped peer rb=/sb= appear or timeout.

    Coturn dumps usage on allocation timeout one timer tick after REFRESH
    lifetime=0. Harvesting immediately after peer reap races that tick.
    Returns the last log text; never invents byte counts.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    text = ""
    while True:
        if log_path.is_file():
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if relayed_bytes(text, proof_user=proof_user) >= 1:
                return text
        if time.monotonic() >= deadline:
            return text
        time.sleep(0.05)


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
    return str(Path(__file__).with_name("credential_server.py"))

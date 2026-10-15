"""Harness-only coturn in the simulated public internet.

Never shipped in packaged WebJam. The public namespace binds STUN+TURN at
203.0.113.1:3478 with a per-run use-auth-secret. Packaged binaries never
see the secret.
"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from tools.cross_network_acceptance.topology import INET_ADDR, STUN_UDP

HARNESS_REALM = "webjam.test"
RELAY_MIN_PORT = 49152
RELAY_MAX_PORT = 49251


def turnserver_bin() -> str | None:
    found = shutil.which("turnserver")
    if found:
        return found
    for folder in ("/usr/bin", "/usr/sbin", "/sbin"):
        candidate = Path(folder) / "turnserver"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def new_secret() -> str:
    return secrets.token_hex(16)


def write_config(directory: Path, *, secret: str, listen_ip: str = INET_ADDR) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "coturn.conf"
    path.write_text(
        "\n".join(
            (
                f"listening-ip={listen_ip}",
                f"listening-port={STUN_UDP}",
                f"relay-ip={listen_ip}",
                f"external-ip={listen_ip}",
                f"min-port={RELAY_MIN_PORT}",
                f"max-port={RELAY_MAX_PORT}",
                "fingerprint",
                "lt-cred-mech",
                "use-auth-secret",
                f"static-auth-secret={secret}",
                f"realm={HARNESS_REALM}",
                "no-tls",
                "no-dtls",
                "no-cli",
                "no-rfc5780",
                "no-tcp",
                "allocation-default-address-family=ipv4",
                "stale-nonce=600",
                "simple-log",
                f"log-file={directory / 'coturn.log'}",
                "no-multicast-peers",
                f"allowed-peer-ip={listen_ip}-{listen_ip}",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def argv(config: Path) -> list[str]:
    binary = turnserver_bin()
    if not binary:
        raise RuntimeError("coturn (turnserver) is not installed.")
    # -n would ignore -c (CLI-only mode). The auth secret lives in the config.
    return [
        binary,
        "-c",
        str(config),
        "--pidfile",
        str(config.parent / "coturn.pid"),
    ]

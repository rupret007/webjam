"""Machine-readable receipts the conductor can consume (full SHA + PASS)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance import CONDUCTOR_KIND, SCHEMA

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET_KEYS = frozenset(
    {
        "invite",
        "invite_url",
        "token",
        "invite_token",
        "host",
        "peer_port",
        "session_id",
        "authorization",
        "link",
    }
)


class ReceiptError(ValueError):
    """Raised when a receipt would be unsafe or incomplete."""


def require_full_sha(value: str) -> str:
    sha = str(value or "").strip().lower()
    if _SHA.fullmatch(sha) is None:
        raise ReceiptError("The conductor receipt needs the full 40-character SHA.")
    return sha


def contains_secret_keys(payload: Any) -> bool:
    """True when a mapping uses a key that could hold invitation material."""

    if isinstance(payload, dict):
        for key, item in payload.items():
            if str(key).lower() in _SECRET_KEYS:
                return True
            if contains_secret_keys(item):
                return True
        return False
    if isinstance(payload, (list, tuple)):
        return any(contains_secret_keys(item) for item in payload)
    return False


def conductor_line(*, sha: str, status: str, case: str) -> str:
    """One-line form: full SHA + PASS/FAIL + case. No endpoints or tokens."""

    digest = require_full_sha(sha)
    label = str(status or "").strip().upper()
    if label not in {"PASS", "FAIL"}:
        raise ReceiptError("Conductor status must be PASS or FAIL.")
    name = str(case or "").strip()
    if name not in {"same-lan", "cross-network"}:
        raise ReceiptError("Conductor case must be same-lan or cross-network.")
    return f"{CONDUCTOR_KIND} sha={digest} status={label} case={name}"


def build_receipt(
    *,
    sha: str,
    case: str,
    status: str,
    profiles: list[dict[str, Any]],
    rendezvous_reachable_from_host: bool | None,
    rendezvous_reachable_from_guest: bool | None,
    invite_host_routable_from_guest: bool | None,
    netem: bool,
    port_forwarding: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    digest = require_full_sha(sha)
    label = str(status or "").strip().upper()
    if label not in {"PASS", "FAIL"}:
        raise ReceiptError("Receipt status must be PASS or FAIL.")
    name = str(case or "").strip()
    if name not in {"same-lan", "cross-network"}:
        raise ReceiptError("Receipt case must be same-lan or cross-network.")
    if port_forwarding:
        raise ReceiptError("This harness never configures port forwarding.")
    payload = {
        "schema": SCHEMA,
        "git_sha": digest,
        "status": label,
        "case": name,
        "port_forwarding": False,
        "netem": bool(netem),
        "rendezvous_reachable_from_host": rendezvous_reachable_from_host,
        "rendezvous_reachable_from_guest": rendezvous_reachable_from_guest,
        "invite_host_routable_from_guest": invite_host_routable_from_guest,
        "profiles": profiles,
        "notes": str(notes or ""),
        "conductor_line": conductor_line(sha=digest, status=label, case=name),
    }
    if contains_secret_keys(payload):
        raise ReceiptError("A receipt must not carry invitation material.")
    return payload


def write_receipt(directory: Path, receipt: dict[str, Any]) -> Path:
    """Write JSON always; write the conductor PASS file only on PASS."""

    if contains_secret_keys(receipt):
        raise ReceiptError("A receipt must not carry invitation material.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = require_full_sha(str(receipt["git_sha"]))
    case = str(receipt["case"])
    json_path = directory / f"{digest}-{case}.json"
    json_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.chmod(0o600)
    if str(receipt["status"]).upper() == "PASS":
        pass_path = directory / f"{digest}-{case}.PASS"
        pass_path.write_text(str(receipt["conductor_line"]) + "\n", encoding="utf-8")
        pass_path.chmod(0o600)
    return json_path

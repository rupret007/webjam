"""Receipts: stale .PASS deleted at start; atomic write only after assertions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance import CONDUCTOR_KIND, SCHEMA, HarnessError

ALLOWED_CASES = frozenset({"same-lan", "cone", "relay"})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET_KEYS = frozenset({
    "invite", "invite_url", "token", "invite_token", "host", "peer_port",
    "session_id", "authorization", "link", "credential", "secret", "password",
})


class ReceiptError(HarnessError, ValueError):
    pass


def require_full_sha(value: str) -> str:
    sha = str(value or "").strip().lower()
    if _SHA.fullmatch(sha) is None:
        raise ReceiptError("Receipt needs the full 40-character SHA.")
    return sha


def contains_secret_keys(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(
            str(key).lower() in _SECRET_KEYS or contains_secret_keys(item)
            for key, item in payload.items()
        )
    if isinstance(payload, (list, tuple)):
        return any(contains_secret_keys(item) for item in payload)
    return False


def _status_case(status: str, case: str) -> tuple[str, str]:
    label = str(status or "").strip().upper()
    name = str(case or "").strip()
    if label not in {"PASS", "FAIL"}:
        raise ReceiptError("Status must be PASS or FAIL.")
    if name not in ALLOWED_CASES:
        raise ReceiptError("Unknown acceptance case.")
    return label, name


def conductor_line(*, sha: str, status: str, case: str) -> str:
    digest = require_full_sha(sha)
    label, name = _status_case(status, case)
    return f"{CONDUCTOR_KIND} sha={digest} status={label} case={name}"


def pass_path(directory: Path, sha: str, case: str) -> Path:
    return directory / f"{require_full_sha(sha)}-{case}.PASS"


def json_path(directory: Path, sha: str, case: str) -> Path:
    return directory / f"{require_full_sha(sha)}-{case}.json"


def clear_pass(directory: Path, sha: str, case: str) -> None:
    pass_path(directory, sha, case).unlink(missing_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def build_receipt(
    *, sha: str, case: str, status: str, profiles: list[dict[str, Any]],
    rendezvous_reachable_from_host: bool | None,
    rendezvous_reachable_from_guest: bool | None,
    invite_host_routable_from_guest: bool | None = None, netem: bool = False,
    port_forwarding: bool = False, notes: str = "", nat_mode: str = "",
    stun_reachable_from_host: bool | None = None,
    stun_reachable_from_guest: bool | None = None,
    kernel: str = "", expect_path: str = "", relay_bytes: int = 0,
) -> dict[str, Any]:
    digest = require_full_sha(sha)
    label, name = _status_case(status, case)
    if port_forwarding:
        raise ReceiptError("This harness never configures port forwarding.")
    payload = {
        "schema": SCHEMA, "git_sha": digest, "status": label, "case": name,
        "port_forwarding": False, "netem": bool(netem), "profiles": profiles,
        "rendezvous_reachable_from_host": rendezvous_reachable_from_host,
        "rendezvous_reachable_from_guest": rendezvous_reachable_from_guest,
        "invite_host_routable_from_guest": invite_host_routable_from_guest,
        "notes": str(notes or ""), "nat_mode": str(nat_mode or ""),
        "conductor_line": conductor_line(sha=digest, status=label, case=name),
        "stun_reachable_from_host": stun_reachable_from_host,
        "stun_reachable_from_guest": stun_reachable_from_guest,
        "kernel": str(kernel or ""), "expect_path": str(expect_path or ""),
        "relay_bytes": int(relay_bytes or 0),
    }
    if contains_secret_keys(payload):
        raise ReceiptError("A receipt must not carry invitation material.")
    return payload


def write_receipt(directory: Path, receipt: dict[str, Any]) -> Path:
    if contains_secret_keys(receipt):
        raise ReceiptError("A receipt must not carry invitation material.")
    digest = require_full_sha(str(receipt["git_sha"]))
    case = str(receipt["case"])
    target = json_path(directory, digest, case)
    _atomic_write(target, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    marker = pass_path(directory, digest, case)
    if str(receipt["status"]).upper() == "PASS":
        _atomic_write(marker, str(receipt["conductor_line"]) + "\n")
    else:
        marker.unlink(missing_ok=True)
    return target

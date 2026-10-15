"""Cloudflare TURN Worker: $0 cap, token shape, and no secrets in the client."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "deploy" / "relay" / "cloudflare-worker"
RELAY = REPO / "deploy" / "relay"

sys.path.insert(0, str(RELAY))
import turn_credentials  # noqa: E402

GIGABYTE = 1_000_000_000
DEFAULT_CAP_GB = 800
FREE_ALLOWANCE_GB = 1000
INVITE = "A" * 43
SESSION_UUID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
SESSION_HEX = "ab" * 32


def test_node_worker_policy_and_fail_closed_cap() -> None:
    result = subprocess.run(
        ["node", "--test", "test/policy.test.js", "test/mailbox.test.js"],
        cwd=WORKER,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Cloudflare Worker policy tests failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def test_documented_cap_formula_is_800_of_1000_decimal_gb() -> None:
    cap = DEFAULT_CAP_GB * GIGABYTE
    assert cap == 800_000_000_000
    assert cap < FREE_ALLOWANCE_GB * GIGABYTE
    assert turn_credentials.valid_session_invite_shape(
        {"session_id": SESSION_UUID, "invite_token": INVITE}
    )
    assert turn_credentials.valid_session_invite_shape(
        {"session_id": SESSION_HEX, "invite_token": INVITE}
    )
    assert not turn_credentials.valid_session_invite_shape(
        {"session_id": "nope", "invite_token": INVITE}
    )
    assert not turn_credentials.valid_session_invite_shape({})


def test_coturn_switchback_post_requires_token_shape_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_credentials, "SECRET", b"switchback-secret")
    monkeypatch.setattr(turn_credentials, "EXTERNAL_IP", "203.0.113.9")
    monkeypatch.setattr(turn_credentials, "CF_TOKEN", "")
    monkeypatch.setattr(turn_credentials, "RATE_LIMIT", 2)
    monkeypatch.setattr(turn_credentials, "_rate", {})
    server = turn_credentials.ThreadingHTTPServer(
        ("127.0.0.1", 0), turn_credentials.Handler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        def post(payload: dict[str, str]) -> tuple[int, object]:
            request = Request(
                f"http://{host}:{port}/turn-credentials",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=2) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                return exc.code, {}

        status, body = post({"session_id": SESSION_UUID, "invite_token": INVITE})
        assert status == 200
        assert "turn:203.0.113.9:3478" in str(body.get("urls"))
        assert body.get("username")
        assert body.get("credential")

        denied, _ = post({"session_id": "bad", "invite_token": "x"})
        assert denied == 403

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/turn-credentials")
        assert connection.getresponse().status == 404
        connection.close()

        limited = 0
        for _ in range(4):
            code, _ = post({"session_id": SESSION_UUID, "invite_token": INVITE})
            if code == 429:
                limited += 1
        assert limited >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_worker_source_has_no_turn_secrets_and_readme_names_wrangler() -> None:
    forbidden = (
        "TURN_KEY_API_TOKEN=",
        "TURN_KEY_ID=",
        "CLOUDFLARE_ACCOUNT_ID=",
        "CLOUDFLARE_ANALYTICS_API_TOKEN=",
    )
    for path in WORKER.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts or ".wrangler" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path} must not assign {needle}"
        assert "sk-" not in text
    wrangler = (WORKER / "wrangler.toml").read_text(encoding="utf-8")
    assert "TURN_MONTHLY_CAP_GB" in wrangler
    assert 'TURN_MONTHLY_CAP_GB = "800"' in wrangler
    assert 'TURN_CREDENTIAL_TTL_SECONDS = "600"' in wrangler
    assert "new_sqlite_classes" in wrangler
    assert "MailboxQuota" in wrangler
    assert 'MAILBOX_DAILY_REQUESTS = "20000"' in wrangler
    readme = (RELAY / "README.md").read_text(encoding="utf-8")
    assert "npx wrangler secret put TURN_KEY_ID" in readme
    assert "npx wrangler secret put TURN_KEY_API_TOKEN" in readme
    assert "npx wrangler secret put CLOUDFLARE_ACCOUNT_ID" in readme
    assert "npx wrangler secret put CLOUDFLARE_ANALYTICS_API_TOKEN" in readme
    assert "npx wrangler deploy" in readme
    assert "usage_bytes = sum(ingressBytes) + sum(egressBytes)" in readme
    assert "800" in readme and "1,000" in readme
    assert os.environ.get("TURN_KEY_API_TOKEN", "") == ""

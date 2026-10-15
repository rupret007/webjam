#!/usr/bin/env python3
"""Mint short-lived TURN REST credentials. No long-lived secret leaves this VM.

GET /credentials returns the same JSON the sidecar accepts for coturn and for
Cloudflare Realtime TURN (iceServers). The compiled app fetches this URL; it
never embeds TURN_STATIC_AUTH_SECRET.

Daily budget and per-session quota_bytes are hard stops so usage cannot bill.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = "webjam"
MAX_BODY = 4096


def _int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


SECRET = str(os.environ.get("TURN_STATIC_AUTH_SECRET", "") or "").encode("utf-8")
EXTERNAL_IP = str(os.environ.get("TURN_EXTERNAL_IP", "") or "").strip()
LISTEN_PORT = _int_env("TURN_LISTEN_PORT", 3478)
TTL = _int_env("TURN_CREDENTIAL_TTL_SECONDS", 600)
SESSION_QUOTA = _int_env("TURN_SESSION_QUOTA_BYTES", 256 * 1024 * 1024)
DAILY_BUDGET = _int_env("TURN_DAILY_BUDGET_BYTES", 8 * 1024 * 1024 * 1024)
BIND = str(os.environ.get("TURN_LISTEN_ADDR", "127.0.0.1") or "127.0.0.1")
PORT = _int_env("TURN_CREDENTIAL_PORT", 8787)
CF_TOKEN = str(os.environ.get("CLOUDFLARE_TURN_API_TOKEN", "") or "").strip()
CF_URL = str(os.environ.get("CLOUDFLARE_TURN_BASE_URL", "") or "").strip()

_lock = threading.Lock()
_day = ""
_issued = 0


def _reset_day_locked(now: float) -> None:
    global _day, _issued
    stamp = time.strftime("%Y-%m-%d", time.gmtime(now))
    if stamp != _day:
        _day = stamp
        _issued = 0


def _over_budget(now: float) -> bool:
    with _lock:
        _reset_day_locked(now)
        return _issued >= DAILY_BUDGET


def _record(session_bytes: int) -> None:
    global _issued
    with _lock:
        _reset_day_locked(time.time())
        _issued += max(0, session_bytes)


def mint_coturn(now: float) -> dict[str, object]:
    if not SECRET or not EXTERNAL_IP:
        raise RuntimeError("coturn secret or external IP is missing")
    expiry = int(now) + TTL
    username = f"{expiry}:{USER}"
    digest = hmac.new(SECRET, username.encode("ascii"), hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode("ascii")
    return {
        "urls": [f"turn:{EXTERNAL_IP}:{LISTEN_PORT}?transport=udp"],
        "username": username,
        "credential": credential,
        "ttl": TTL,
        "quota_bytes": SESSION_QUOTA,
        "allowed_relay_ips": [EXTERNAL_IP],
    }


def fetch_cloudflare() -> dict[str, object]:
    if not CF_TOKEN or not CF_URL:
        raise RuntimeError("Cloudflare TURN is not configured")
    request = urllib.request.Request(
        CF_URL,
        headers={"Authorization": f"Bearer {CF_TOKEN}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise RuntimeError("Cloudflare credential response is too large")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Cloudflare credential response is invalid")
    payload["quota_bytes"] = SESSION_QUOTA
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in {"/", "/credentials"}:
            self.send_error(404)
            return
        if _over_budget(time.time()):
            self.send_error(429, "quota")
            return
        try:
            payload = fetch_cloudflare() if CF_TOKEN else mint_coturn(time.time())
        except Exception:
            self.send_error(503, "unavailable")
            return
        _record(int(payload.get("quota_bytes") or SESSION_QUOTA))
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    if not SECRET and not CF_TOKEN:
        return 2
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

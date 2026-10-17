#!/usr/bin/env python3
"""Mint short-lived TURN credentials. No long-lived secret leaves this host.

POST /turn-credentials is the shared interface for Cloudflare Realtime TURN
(iceServers from the Worker or from this VM) and for self-hosted coturn.
GET /credentials remains as a coturn-only fallback.

The Cloudflare key ID and API token stay in process environment / Worker
secrets. They are never compiled into the app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = "webjam"
MAX_BODY = 4096
SESSION_ID_RE = re.compile(
    r"(?a)^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{64})$"
)
INVITE_TOKEN_RE = re.compile(r"(?a)^[A-Za-z0-9_-]{32,256}$")
RATE_WINDOW_SECONDS = 60


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
RATE_LIMIT = _int_env("TURN_RATE_LIMIT_PER_MINUTE", 10)

_lock = threading.Lock()
_day = ""
_issued = 0
_rate: dict[str, list[float]] = {}


def valid_session_invite_shape(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    session = str(payload.get("session_id") or "")
    token = str(payload.get("invite_token") or payload.get("token") or "")
    return bool(SESSION_ID_RE.fullmatch(session) and INVITE_TOKEN_RE.fullmatch(token))


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


def _prune_rate_locked(now: float) -> None:
    cutoff = now - RATE_WINDOW_SECONDS
    for ip in list(_rate):
        stamps = [stamp for stamp in _rate[ip] if stamp >= cutoff]
        if stamps:
            _rate[ip] = stamps
        else:
            del _rate[ip]


def allow_rate(ip: str, now: float, limit: int = RATE_LIMIT) -> bool:
    if not ip:
        return False
    with _lock:
        _prune_rate_locked(now)
        stamps = [stamp for stamp in _rate.get(ip, []) if now - stamp < RATE_WINDOW_SECONDS]
        if len(stamps) >= limit:
            _rate[ip] = stamps
            return False
        stamps.append(now)
        _rate[ip] = stamps
        return True


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
        data=json.dumps({"ttl": TTL}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise RuntimeError("Cloudflare credential response is too large")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("iceServers"):
        raise RuntimeError("Cloudflare credential response is invalid")
    payload["quota_bytes"] = SESSION_QUOTA
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/credentials":
            self.send_error(404)
            return
        self._mint(require_token=False)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/turn-credentials", "/credentials"}:
            self.send_error(404)
            return
        self._mint(require_token=True)

    def _client_ip(self) -> str:
        forwarded = str(self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return str(self.client_address[0] if self.client_address else "")

    def _read_token_body(self) -> object | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length < 1 or length > MAX_BODY:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return None

    def _mint(self, *, require_token: bool) -> None:
        if require_token:
            if not allow_rate(self._client_ip(), time.time(), RATE_LIMIT):
                self.send_error(429, "rate")
                return
            payload = self._read_token_body()
            if not valid_session_invite_shape(payload):
                self.send_error(403, "unauthorized")
                return
        if _over_budget(time.time()):
            self.send_error(429, "quota")
            return
        try:
            document = fetch_cloudflare() if CF_TOKEN else mint_coturn(time.time())
        except Exception:
            self.send_error(503, "unavailable")
            return
        _record(int(document.get("quota_bytes") or SESSION_QUOTA))
        body = json.dumps(document, separators=(",", ":")).encode("utf-8")
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

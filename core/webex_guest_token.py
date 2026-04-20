"""
Webex Guest Issuer JWT utilities.

When developer.webex.com guest-issuer credentials are configured in AppSettings,
WebJam can generate a short-lived guest JWT and exchange it for a Webex API
access token. That token is then passed to the Webex Meetings Widget so the
user joins as a named guest with no Webex login required.

If the credentials are not configured the module is a no-op; the direct-URL
embed path is used instead.

Dependencies: httpx (already in requirements), stdlib only for JWT.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx

_logger = logging.getLogger("webjam.webex_guest_token")

_GUEST_LOGIN_URL = "https://webexapis.com/v1/jwt/login"
_TOKEN_EXPIRY_S  = 3600  # 1 hour


# ---------------------------------------------------------------------------
# JWT helpers (stdlib only — no PyJWT dependency)
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_guest_jwt(
    issuer_id: str,
    secret_b64: str,
    display_name: str,
    expiry_s: int = _TOKEN_EXPIRY_S,
) -> str:
    """Return a signed Webex guest-issuer JWT.

    Args:
        issuer_id:    Guest Issuer ID from developer.webex.com.
        secret_b64:   Base-64 encoded secret (standard alphabet, padding optional).
        display_name: Name shown to other meeting participants.
        expiry_s:     Lifetime of the JWT in seconds.

    Raises:
        ValueError: If issuer_id or secret_b64 are empty.
    """
    if not issuer_id or not secret_b64:
        raise ValueError("Guest issuer credentials must not be empty")

    header  = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub":  f"webjam-{display_name.lower().replace(' ', '-')}-{int(time.time())}",
        "name": display_name,
        "iss":  issuer_id,
        "exp":  int(time.time()) + expiry_s,
    }

    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()

    # Base-64 secrets sometimes arrive without padding — add it before decoding
    padded = secret_b64 + "=" * (-len(secret_b64) % 4)
    secret_bytes = base64.b64decode(padded)

    sig = hmac.new(secret_bytes, signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------
def exchange_guest_jwt(guest_jwt: str, timeout: float = 10.0) -> Optional[str]:
    """Exchange a guest JWT for a short-lived Webex API access token.

    Returns the access token string on success, or None on any error.
    Logs warnings on failure so callers can fall back gracefully.
    """
    try:
        resp = httpx.post(
            _GUEST_LOGIN_URL,
            headers={"Authorization": f"Bearer {guest_jwt}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        token = resp.json().get("token")
        if token:
            _logger.debug("Guest token exchange successful")
            return token
        _logger.warning("Guest token exchange: no 'token' in response")
    except httpx.HTTPStatusError as exc:
        _logger.warning("Guest token exchange HTTP %d: %s", exc.response.status_code, exc)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Guest token exchange failed: %s", exc)
    return None

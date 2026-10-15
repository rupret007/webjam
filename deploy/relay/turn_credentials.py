"""POST /turn-credentials: coturn REST long-term credentials (RFC 5389)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

REALM = "webjam.test"


def mint(
    *,
    secret: str,
    uris: list[str],
    ttl_s: int = 600,
    user: str = "wj",
) -> dict[str, object]:
    expiry = int(time.time()) + int(ttl_s)
    username = f"{expiry}:{user}"
    digest = hmac.new(
        secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1,
    ).digest()
    return {
        "username": username,
        "credential": base64.b64encode(digest).decode("ascii"),
        "ttl": int(ttl_s),
        "uris": list(uris),
        "realm": REALM,
    }


def post_turn_credentials(secret: str, uris: list[str]) -> dict[str, object]:
    """Mint the JSON body of POST /turn-credentials."""
    return mint(secret=secret, uris=uris)

"""
Tests for core.webex_guest_token — generate_guest_jwt and exchange_guest_jwt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from core.webex_guest_token import generate_guest_jwt, exchange_guest_jwt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    """Decode a base64url string (no padding required)."""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# generate_guest_jwt tests
# ---------------------------------------------------------------------------

class TestGenerateGuestJwt:
    ISSUER = "my-issuer-id"
    SECRET = base64.b64encode(b"super-secret-key").decode("ascii")
    NAME = "Test User"

    def _make_jwt(self, **kwargs):
        defaults = dict(
            issuer_id=self.ISSUER,
            secret_b64=self.SECRET,
            display_name=self.NAME,
        )
        defaults.update(kwargs)
        return generate_guest_jwt(**defaults)

    def test_jwt_has_three_parts(self):
        """generate_guest_jwt returns a string with 3 dot-separated parts."""
        jwt = self._make_jwt()
        parts = jwt.split(".")
        assert len(parts) == 3

    def test_jwt_header_is_hs256(self):
        """Base64-decode the first part and verify alg=HS256."""
        jwt = self._make_jwt()
        header_b64 = jwt.split(".")[0]
        header = json.loads(_b64url_decode(header_b64))
        assert header["alg"] == "HS256"

    def test_jwt_header_typ_is_jwt(self):
        """Header typ should be JWT."""
        jwt = self._make_jwt()
        header_b64 = jwt.split(".")[0]
        header = json.loads(_b64url_decode(header_b64))
        assert header["typ"] == "JWT"

    def test_jwt_payload_contains_name(self):
        """Base64-decode second part, verify name field matches display_name."""
        jwt = self._make_jwt()
        payload_b64 = jwt.split(".")[1]
        payload = json.loads(_b64url_decode(payload_b64))
        assert payload["name"] == self.NAME

    def test_jwt_payload_contains_iss(self):
        """Verify iss field matches issuer_id."""
        jwt = self._make_jwt()
        payload_b64 = jwt.split(".")[1]
        payload = json.loads(_b64url_decode(payload_b64))
        assert payload["iss"] == self.ISSUER

    def test_jwt_payload_exp_in_future(self):
        """Verify exp > current time."""
        jwt = self._make_jwt()
        payload_b64 = jwt.split(".")[1]
        payload = json.loads(_b64url_decode(payload_b64))
        assert payload["exp"] > int(time.time())

    def test_jwt_signature_verifies(self):
        """HMAC-SHA256 verify the signature using the known secret."""
        jwt = self._make_jwt()
        parts = jwt.split(".")
        header_payload = f"{parts[0]}.{parts[1]}".encode("ascii")
        sig_received = _b64url_decode(parts[2])

        secret_bytes = base64.b64decode(self.SECRET + "=" * (-len(self.SECRET) % 4))
        expected_sig = hmac.new(secret_bytes, header_payload, hashlib.sha256).digest()
        assert hmac.compare_digest(sig_received, expected_sig)

    def test_empty_issuer_raises(self):
        """generate_guest_jwt with empty issuer_id raises ValueError."""
        with pytest.raises(ValueError):
            generate_guest_jwt("", self.SECRET, self.NAME)

    def test_empty_secret_raises(self):
        """generate_guest_jwt with empty secret_b64 raises ValueError."""
        with pytest.raises(ValueError):
            generate_guest_jwt(self.ISSUER, "", self.NAME)


# ---------------------------------------------------------------------------
# exchange_guest_jwt tests
# ---------------------------------------------------------------------------

class TestExchangeGuestJwt:
    def test_exchange_jwt_returns_none_on_http_error(self):
        """mock httpx.post to raise HTTPStatusError, verify None returned."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 401

        error = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("core.webex_guest_token.httpx.post", side_effect=error):
            result = exchange_guest_jwt("fake.jwt.token")

        assert result is None

    def test_exchange_jwt_returns_none_on_network_error(self):
        """mock httpx.post to raise ConnectionError, verify None returned."""
        with patch("core.webex_guest_token.httpx.post", side_effect=ConnectionError("Network unreachable")):
            result = exchange_guest_jwt("fake.jwt.token")

        assert result is None

    def test_exchange_jwt_returns_token_on_success(self):
        """mock httpx.post to return {token: abc123}, verify abc123 returned."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"token": "abc123"}
        mock_response.raise_for_status.return_value = None

        with patch("core.webex_guest_token.httpx.post", return_value=mock_response):
            result = exchange_guest_jwt("fake.jwt.token")

        assert result == "abc123"

    def test_exchange_jwt_returns_none_when_no_token_in_response(self):
        """mock response returns {} (no token key), verify None returned."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status.return_value = None

        with patch("core.webex_guest_token.httpx.post", return_value=mock_response):
            result = exchange_guest_jwt("fake.jwt.token")

        assert result is None

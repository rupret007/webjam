"""
Tests for ``core.webex_guest_token.should_refresh_token`` — the pure helper
that decides whether the embedded Webex widget's guest token is close enough
to its 1-hour TTL that we should fetch a new one.
"""
from __future__ import annotations

import time

from core.webex_guest_token import (
    _TOKEN_REFRESH_SAFETY_MARGIN_S,
    _TOKEN_TTL_S,
    should_refresh_token,
)


def test_should_refresh_within_safety_margin():
    """A token whose age is past (TTL - margin) should trigger a refresh."""
    # Acquired far enough in the past that we're inside the safety window.
    acquired_at = time.time() - (_TOKEN_TTL_S - _TOKEN_REFRESH_SAFETY_MARGIN_S + 1)
    assert should_refresh_token(acquired_at) is True


def test_should_not_refresh_fresh_token():
    """A token acquired moments ago is fresh — must not refresh."""
    acquired_at = time.time() - 5.0  # 5 s old
    assert should_refresh_token(acquired_at) is False


def test_should_not_refresh_when_no_token_yet():
    """Sentinel 0.0 (no token issued) must not trigger a refresh."""
    assert should_refresh_token(0.0) is False

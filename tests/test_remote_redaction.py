"""Remote-session privacy boundaries for logs, support data, and Sentry."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from core.logging_config import configure_sentry
from core.redaction import REDACTED, redact_mapping, redact_text
from core.settings import AppSettings


def test_remote_identifiers_and_capabilities_redact_by_field_name() -> None:
    capability = "runtime-sentinel-capability-DO-NOT-LEAK"
    payload = {
        "enrollment_capability": capability,
        "session_reference": "session-sentinel",
        "invite_reference": "invite-sentinel",
        "nested": {"safe": "visible"},
    }

    redacted = redact_mapping(payload)

    assert redacted["enrollment_capability"] == REDACTED
    assert redacted["session_reference"] == REDACTED
    assert redacted["invite_reference"] == REDACTED
    assert redacted["nested"] == {"safe": "visible"}
    assert capability not in repr(redacted)


def test_valid_ipv4_and_ipv6_literals_are_removed_without_harming_versions() -> None:
    raw = (
        "peer at 192.168.5.207 relay 203.0.113.9:3478 "
        "v6 [2001:db8::2]:443 scoped fe80::1%en0 loopback ::1 "
        "WebJam 0.11.0 Jamulus 3.12.2 time=12:34:56"
    )

    safe = redact_text(raw)

    for address in (
        "192.168.5.207",
        "203.0.113.9",
        "2001:db8::2",
        "fe80::1",
        "::1",
    ):
        assert address not in safe
    assert safe.count("[redacted-ip]") == 5
    assert "0.11.0" in safe
    assert "3.12.2" in safe
    assert "12:34:56" in safe


def test_invalid_ip_shaped_text_is_not_claimed_as_an_address() -> None:
    raw = "values 999.999.999.999 clock 12:34:56 hardware aa:bb:cc:dd:ee:ff"
    assert redact_text(raw) == raw


def test_sentry_hooks_sanitize_nested_event_and_breadcrumb_payloads() -> None:
    captured: dict[str, object] = {}
    fake_sdk = SimpleNamespace(init=lambda **kwargs: captured.update(kwargs))
    settings = AppSettings()
    settings.enable_sentry = True
    settings.sentry_dsn = "https://public@example.invalid/1"

    with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
        configure_sentry(settings)

    assert captured["send_default_pii"] is False
    before_send = captured["before_send"]
    event = {
        "request": {"url": "webjam://join?v=3&r=reference-local&i=private"},
        "extra": {
            "enrollment_capability": "runtime-sentinel-capability-DO-NOT-LEAK",
            "peer": "203.0.113.44:3478",
            "path": "/Users/alice/Library/Logs/WebJam.log",
        },
    }
    safe_event = before_send(event, {})
    rendered = repr(safe_event)
    assert "runtime-sentinel" not in rendered
    assert "203.0.113.44" not in rendered
    assert "/Users/alice" not in rendered
    assert "webjam://join" not in rendered

    before_breadcrumb = captured["before_breadcrumb"]
    safe_crumb = before_breadcrumb(
        {"message": "connected to [2001:db8::5]:443", "data": {"token": "secret"}},
        {},
    )
    assert "2001:db8::5" not in repr(safe_crumb)
    assert "secret" not in repr(safe_crumb)


def test_sentry_hooks_drop_non_mapping_payloads() -> None:
    captured: dict[str, object] = {}
    fake_sdk = SimpleNamespace(init=lambda **kwargs: captured.update(kwargs))
    settings = AppSettings(enable_sentry=True, sentry_dsn="https://public@example.invalid/1")

    with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
        configure_sentry(settings)

    assert captured["before_send"]("unsafe", {}) is None
    assert captured["before_breadcrumb"]("unsafe", {}) is None

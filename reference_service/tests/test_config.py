from __future__ import annotations

import pytest

from webjam_reference.__main__ import config_from_args
from webjam_reference.config import ServiceConfig


def test_active_limit_default_keeps_admission_idle_and_listener_defaults() -> None:
    config = ServiceConfig()
    assert config.max_active_session_seconds == 28_800
    assert config.max_session_ttl_seconds == 600
    assert config.min_session_ttl_seconds == 30
    assert config.idle_timeout_seconds == 90
    assert config.control_bind == config.relay_bind == config.http_bind == "127.0.0.1"


@pytest.mark.parametrize("value", [False, True, 0, -1, 28_801, 600.0, float("nan"), float("inf"), "3600", None])
def test_active_limit_refuses_invalid_or_unbounded_retention(value) -> None:
    with pytest.raises(ValueError, match="active session limit"):
        ServiceConfig(max_active_session_seconds=value)


def test_active_limit_cannot_end_before_maximum_admission_window() -> None:
    with pytest.raises(ValueError, match="shorter than maximum enrollment TTL"):
        ServiceConfig(max_active_session_seconds=599)
    assert ServiceConfig(max_active_session_seconds=600).max_active_session_seconds == 600


def test_active_limit_can_be_lowered_by_configuration_environment_or_cli(monkeypatch) -> None:
    monkeypatch.setenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", "7200")
    from_environment = config_from_args([])
    assert from_environment.max_active_session_seconds == 7200
    assert from_environment.max_session_ttl_seconds == 600
    from_cli = config_from_args(["--max-active-session-seconds", "3600"])
    assert from_cli.max_active_session_seconds == 3600
    assert from_cli.idle_timeout_seconds == 90
    assert from_cli.control_bind == from_cli.relay_bind == from_cli.http_bind == "127.0.0.1"


@pytest.mark.parametrize("value", ["0", "599", "28801"])
def test_cli_cannot_bypass_active_limit_validation(monkeypatch, value) -> None:
    monkeypatch.delenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", raising=False)
    with pytest.raises(ValueError, match="active session limit"):
        config_from_args(["--max-active-session-seconds", value])


def test_active_limit_invalid_environment_is_categorical(monkeypatch) -> None:
    sentinel = "private-value-must-not-appear"
    monkeypatch.setenv("WEBJAM_MAX_ACTIVE_SESSION_SECONDS", sentinel)
    with pytest.raises(SystemExit) as caught:
        config_from_args([])
    assert str(caught.value) == "WEBJAM_MAX_ACTIVE_SESSION_SECONDS must be an integer"
    assert sentinel not in str(caught.value)

"""Unit tests for Jamulus adaptive latency defaults and policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.jamulus_adaptive_latency import (
    AdaptiveLatencyPolicy,
    DelayStatus,
    LatencyIniDefaults,
    LatencyObservation,
    apply_frame_factor_to_profile,
    apply_latency_defaults_to_profile,
    classify_delay,
    estimate_overall_delay_ms,
    latency_defaults_for_settings,
    read_client_ini_values,
)
from core.settings import AppSettings


def test_defaults_fill_only_missing_keys(tmp_path: Path) -> None:
    profile = tmp_path / "WebJam-native-v0.16.ini"
    profile.write_text(
        "<client><autojitbuf>0</autojitbuf><jitbuf>3</jitbuf></client>",
        encoding="utf-8",
    )
    written = apply_latency_defaults_to_profile(
        profile,
        defaults=LatencyIniDefaults(),
    )
    assert "autojitbuf" not in written
    assert "jitbuf" not in written
    assert "prefsndcrdbufidx" in written
    assert "audioquality" in written
    values = read_client_ini_values(profile.read_bytes())
    assert values["autojitbuf"] == "0"
    assert values["jitbuf"] == "3"
    assert values["prefsndcrdbufidx"] == "2"
    provenance = json.loads(
        (tmp_path / "WebJam-adaptive-latency-provenance-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert provenance["keys"]["autojitbuf"]["source"] == "musician"


def test_policy_hysteresis_steps_buffer(tmp_path: Path) -> None:
    policy = AdaptiveLatencyPolicy(
        enabled=True,
        current_factor=2,
        min_change_interval_s=0.0,
        streak_required=2,
    )
    profile = tmp_path / "profile.ini"
    profile.write_text("<client></client>", encoding="utf-8")
    for _ in range(2):
        step = policy.decide(LatencyObservation(ping_ms=40.0))
    assert step == 4
    assert apply_frame_factor_to_profile(profile, 4)


def test_policy_respects_player_override() -> None:
    policy = AdaptiveLatencyPolicy(
        enabled=True,
        player_override=True,
        min_change_interval_s=0.0,
        streak_required=1,
    )
    assert policy.decide(LatencyObservation(ping_ms=100.0)) is None


def test_estimate_and_classify_delay() -> None:
    overall = estimate_overall_delay_ms(ping_ms=20.0, frame_factor=2, jitter_buffer_blocks=2)
    assert overall is not None
    assert overall > 20.0
    assert classify_delay(30.0) is DelayStatus.GOOD
    assert classify_delay(55.0) is DelayStatus.FAIR
    assert classify_delay(120.0) is DelayStatus.POOR


def test_latency_defaults_for_settings() -> None:
    safe = latency_defaults_for_settings(AppSettings(audio_latency="safe"))
    assert safe.prefsndcrdbufidx == 4
    fast = latency_defaults_for_settings(AppSettings(audio_latency="low"))
    assert fast.prefsndcrdbufidx == 1

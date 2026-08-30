"""Deterministic two-client transport through real Jamulus and JACK.

Pure fixture/analyzer checks run everywhere.  The production-boundary test is
opt-in and skip-safe unless the prepared Linux integration job explicitly sets
``WEBJAM_RUN_JACK_AUDIO_INTEGRATION=1``.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

from tests.support.jamulus_jack_harness import (
    CAPTURE_TAIL_S,
    FIXTURE_DURATION_S,
    SAMPLE_RATE,
    SPEC_A,
    SPEC_B,
    JamulusJackHarness,
    analyze_received,
    assert_signal_metrics,
    make_fixture,
    probe_client_capability,
)


pytestmark = pytest.mark.requires_local_socket


def test_fixture_analyzer_preserves_rate_channels_duration_and_identity() -> None:
    fixture = make_fixture(SPEC_A)
    delay = 384
    capture_frames = len(fixture) + round(CAPTURE_TAIL_S * SAMPLE_RATE)
    capture = np.zeros((capture_frames, 2), dtype=np.float32)
    capture[delay : delay + len(fixture), 0] = fixture * 0.55
    capture[delay : delay + len(fixture), 1] = fixture * 0.55

    metrics = analyze_received(
        capture,
        expected=SPEC_A,
        forbidden_frequency_hz=SPEC_B.frequency_hz,
    )
    assert_signal_metrics(
        metrics,
        expected=SPEC_A,
        expected_frames=capture_frames,
    )
    assert metrics.sample_rate == 48_000
    assert metrics.channels == 2
    assert metrics.frames == round(
        (FIXTURE_DURATION_S + CAPTURE_TAIL_S) * SAMPLE_RATE
    )
    assert abs(metrics.dominant_hz - SPEC_A.frequency_hz) <= 3.0
    assert metrics.cross_rejection_db >= 15.0


def test_fixture_duration_is_configurable_and_rejects_truncation() -> None:
    fixture = make_fixture(SPEC_A, duration_s=11.0)
    assert fixture.shape == (11 * SAMPLE_RATE,)
    assert np.count_nonzero(fixture[5 * SAMPLE_RATE :]) == 0
    with pytest.raises(ValueError, match="ends before"):
        make_fixture(SPEC_A, duration_s=SPEC_A.tone_end_s - 0.01)


def test_server_only_capability_probe_does_not_trust_help_text() -> None:
    binary = os.environ.get("WEBJAM_JAMULUS_BINARY", "")
    if not binary:
        pytest.skip("WEBJAM_JAMULUS_BINARY not set")
    capability = probe_client_capability(binary)
    if os.path.basename(binary) == "jamulus-headless":
        assert not capability.supported
        assert "SERVER_ONLY" in capability.detail


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_INTEGRATION") != "1",
    reason="real JACK/Jamulus audio harness is opt-in",
)
@pytest.mark.skipif(sys.platform != "linux", reason="JACK dummy certification is Linux-only")
def test_two_real_clients_exchange_only_the_other_boundary_signal() -> None:
    harness = JamulusJackHarness.from_environment()
    with harness:
        result = harness.run_transport()

    # The raw JACK xrun count is retained for diagnostics. The analyzer bounds
    # decoded silence to at most 2% of the tone so a shared non-realtime CI VM
    # may lose one isolated callback, but cannot hide a material transport gap.
    for metrics in (result.client_a_metrics, result.client_b_metrics):
        assert metrics.dropout_window_count <= max(
            1, math.ceil(metrics.continuity_window_count * 0.02)
        )
    assert result.client_a_received.shape == (9 * SAMPLE_RATE, 2)
    assert result.client_b_received.shape == (9 * SAMPLE_RATE, 2)
    assert {client["name"] for client in result.server_clients} == {
        harness.CLIENT_A_NAME,
        harness.CLIENT_B_NAME,
    }
    # A receives B's 660 Hz fixture; B receives A's 440 Hz fixture.
    assert abs(result.client_a_metrics.dominant_hz - SPEC_B.frequency_hz) <= 3.0
    assert abs(result.client_b_metrics.dominant_hz - SPEC_A.frequency_hz) <= 3.0

"""Opt-in three-client Jamulus/JACK companion for Reference Track.

This Linux-only harness uses one official server and three official clients:
host, bandmate, and the dedicated ``WebJam Track`` participant.  The evidence
is real Jamulus transport with deterministic synthetic PCM.  It is not a
Linux product backend and does not claim physical hardware or human
audibility.
"""

from __future__ import annotations

import os
import sys

import pytest

from tests.support.jamulus_jack_harness import (
    SPEC_A,
    SPEC_REFERENCE_TRACK,
    JamulusJackHarness,
    analyze_recorded_stem,
    assert_recorded_stem_metrics,
)


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_INTEGRATION") != "1",
    reason="real three-client Reference Track companion is opt-in",
)
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="real Jamulus/JACK companion is Linux-only",
)
def test_reference_track_is_a_separate_real_client_with_independent_faders() -> None:
    harness = JamulusJackHarness.from_environment(include_reference_track=True)
    evidence: dict[str, object] = {}

    with harness:
        started = harness.set_recording(True)
        assert started["enabled"] is True

        shared = harness.run_reference_track_transport(
            host_level=100,
            bandmate_level=100,
            duration_s=5.0,
            tail_s=1.5,
        )
        assert {entry["name"] for entry in shared.server_clients} == set(
            harness.expected_client_names
        )
        assert shared.host_metrics is not None
        assert shared.bandmate_metrics is not None
        assert shared.host_silence is None
        assert shared.bandmate_silence is None
        assert shared.reference_return_silence.rms <= 0.003
        assert shared.fader_proof.reference_zeroed_channels

        # Each musician's track fader belongs to that listener's own Jamulus
        # mix. Keep the host at unity and set only the bandmate's track row to
        # zero; the next real decoder captures must diverge accordingly.
        independent = harness.run_reference_track_transport(
            host_level=100,
            bandmate_level=0,
            duration_s=5.0,
            tail_s=1.5,
        )
        assert independent.host_metrics is not None
        assert independent.bandmate_metrics is None
        assert independent.bandmate_silence is not None
        assert independent.bandmate_silence.rms <= 0.003
        assert independent.reference_return_silence.rms <= 0.003
        assert independent.fader_proof.host_track_level == 100
        assert independent.fader_proof.bandmate_track_level == 0

        stopped = harness.set_recording(False)
        assert stopped["enabled"] is False
        # Jamulus 3.12.2 sanitizes spaces in the client name to underscores
        # before using it as the recorder stem prefix.
        recording_prefix = harness.REFERENCE_TRACK_NAME.replace(" ", "_")
        track_wavs = tuple(
            path
            for path in harness.recording_artifacts()
            if (
                path.suffix.lower() == ".wav"
                and path.name.startswith(f"{recording_prefix}-")
            )
        )
        assert len(track_wavs) == 1, track_wavs
        track_stem = analyze_recorded_stem(
            track_wavs[0],
            expected=SPEC_REFERENCE_TRACK,
            forbidden_frequency_hz=SPEC_A.frequency_hz,
        )
        assert_recorded_stem_metrics(
            track_stem,
            expected=SPEC_REFERENCE_TRACK,
            duration_bounds_s=(12.0, 18.0),
        )

        evidence = {
            "classification": (
                "real-Jamulus transport verified",
                "synthetic-audio verified",
                "artifact verified",
            ),
            "server_count": 1,
            "client_count": 3,
            "roster": tuple(sorted(harness.expected_client_names)),
            "track_return_faders_zero_accepted": True,
            "independent_listener_faders_observed_at_jack_decoder": True,
            "reference_track_stem_verified": True,
            "human_audibility": "not run",
            "physical_audio_hardware": "not run",
            "linux_product_backend": "not implemented",
        }

    assert not harness.cleanup_errors
    assert all(process.proc.poll() is not None for process in harness.processes)
    assert evidence["client_count"] == 3
    assert evidence["human_audibility"] == "not run"

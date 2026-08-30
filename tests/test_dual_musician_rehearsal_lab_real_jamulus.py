"""Real-Jamulus transport companion for the Dual-Musician Rehearsal Lab.

The source-level lab owns invitation privacy, peer-transfer recovery, Studio,
and Track Export deterministically.  This opt-in Linux companion proves the
real boundary that must not be faked: one official JamulusServer with two
official Jamulus clients, deterministic marker audio through their codecs,
the native recorder lifecycle, an active-session guest interruption, and
owned-process/port/JACK cleanup.

It intentionally does *not* claim physical interface, headphone, audibility,
or external-editor proof.  The markers are synthetic and the result is only
``real-Jamulus transport verified`` plus ``synthetic-audio verified``.
"""

from __future__ import annotations

import os
import sys

import pytest

from tests.support.jamulus_jack_harness import JamulusJackHarness


pytestmark = pytest.mark.requires_local_socket


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_INTEGRATION") != "1",
    reason="real Jamulus dual-musician companion is opt-in",
)
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="real Jamulus dual-musician companion is Linux-only",
)
def test_dual_musician_lab_real_jamulus_transport_recorder_reconnect_and_cleanup() -> None:
    """Certify real native transport without overstating human/audio evidence."""

    harness = JamulusJackHarness.from_environment()
    evidence: dict[str, object] = {}
    with harness:
        initial_identity = harness.certify_owned_client_roster_identity()
        assert initial_identity.roster_size == 2
        assert {
            client.local_self_channel_id
            for client in initial_identity.owned_clients
        } == {0}
        assert {
            client.self_ordinal for client in initial_identity.owned_clients
        } == {0, 1}
        assert {
            client.server_channel_id for client in initial_identity.owned_clients
        } == set(initial_identity.server_channel_ids_by_ordinal)
        assert all(
            client.server_channel_id
            == initial_identity.server_channel_ids_by_ordinal[client.self_ordinal]
            for client in initial_identity.owned_clients
        )

        started = harness.set_recording(True)
        assert started["enabled"] is True

        first_pass = harness.run_transport(duration_s=5.0, tail_s=1.5)
        assert {entry["name"] for entry in first_pass.server_clients} == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
        }

        # The recorder remains armed while the guest is genuinely absent from
        # the server roster, then the same named client reconnects.  The
        # companion deliberately reports that as transport recovery, not an
        # audibility or local-capture-gap claim.
        recovered = harness.exercise_disconnect_reconnect(client_index=1)
        assert {entry["name"] for entry in recovered} == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
        }
        recovered_identity = harness.certify_owned_client_roster_identity()
        assert recovered_identity.roster_size == 2
        assert {
            client.local_self_channel_id
            for client in recovered_identity.owned_clients
        } == {0}
        assert {
            client.self_ordinal for client in recovered_identity.owned_clients
        } == {0, 1}
        assert len(
            {
                client.server_channel_id
                for client in recovered_identity.owned_clients
            }
        ) == 2
        assert all(
            client.server_channel_id
            == recovered_identity.server_channel_ids_by_ordinal[client.self_ordinal]
            for client in recovered_identity.owned_clients
        )

        second_pass = harness.run_transport(duration_s=5.0, tail_s=1.5)
        assert {entry["name"] for entry in second_pass.server_clients} == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
        }

        stopped = harness.set_recording(False)
        assert stopped["enabled"] is False
        wavs = tuple(
            path
            for path in harness.recording_artifacts()
            if path.suffix.lower() == ".wav"
        )
        assert len(wavs) >= 2
        assert all(path.stat().st_size > 44 for path in wavs)

        evidence = {
            "classification": (
                "real-Jamulus transport verified",
                "synthetic-audio verified",
                "artifact verified",
            ),
            "server_count": 1,
            "client_count": 2,
            "recorder_started_once": True,
            "recorder_stopped_once": True,
            "guest_recovered_with_same_two_identities": True,
            "owned_rpc_self_ordinals_certified": tuple(
                client.self_ordinal
                for client in recovered_identity.owned_clients
            ),
            "ordered_roster_digest": recovered_identity.common_roster_digest,
            "recorded_wav_count": len(wavs),
            "human_audibility": "not run",
            "physical_audio_hardware": "not run",
        }

    assert not harness.cleanup_errors
    assert evidence["server_count"] == 1
    assert evidence["client_count"] == 2

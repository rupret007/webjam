"""Strict evidence boundaries for remote-session Band Check."""

from __future__ import annotations

import pytest

from core.band_check import (
    BandCheckMode,
    BandCheckObservations,
    BandCheckOutcome,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStep,
    BandCheckStepKey,
)
from core.session_transport import ConnectionQuality, TransportPath


def step(
    key: BandCheckStepKey,
    status: BandCheckStatus,
    action: str = "",
    *,
    required: bool = True,
) -> BandCheckStep:
    return BandCheckStep(
        key=key,
        title=key.value,
        status=status,
        detail="waiting",
        next_action=action,
        required=required,
    )


def live_session() -> BandCheckSession:
    return BandCheckSession(
        mode=BandCheckMode.LIVE_OBSERVE,
        steps=[
            step(
                BandCheckStepKey.MUSIC_ENGINE,
                BandCheckStatus.PENDING,
                "Start the session",
            ),
            step(
                BandCheckStepKey.MUSIC_PATH,
                BandCheckStatus.WARNING,
                "Check Again",
            ),
        ],
    )


def remote_observations(
    *,
    path: TransportPath = TransportPath.INTERNET_DIRECT,
    quality: ConnectionQuality = ConnectionQuality.PLAYABLE,
    generation: int = 1,
    **changes: object,
) -> BandCheckObservations:
    values: dict[str, object] = {
        "music_engine_running": True,
        "music_engine_responsive": True,
        "peer_connected": True,
        "connection_path": path,
        "connection_quality": quality,
        "path_generation": generation,
    }
    values.update(changes)
    return BandCheckObservations(**values)


def test_process_control_participant_datagrams_and_hearing_stay_independent() -> None:
    session = live_session()
    session.apply_live_observations(
        remote_observations(transport_datagrams_flowed=True)
    )

    evidence = session.evidence
    assert evidence.jamulus_process_started
    assert evidence.jamulus_authenticated_responsive
    assert evidence.remote_participant_appeared
    assert evidence.transport_datagrams_flowed
    assert not evidence.remote_decoded_test_observed
    assert not evidence.musician_confirmed_two_way_audibility
    assert not evidence.local_input_observed
    assert not evidence.local_output_confirmed
    assert not evidence.local_recording_heard

    music_path = session.step(BandCheckStepKey.MUSIC_PATH)
    assert music_path.status is BandCheckStatus.WARNING
    assert "does not confirm that anyone heard it" in music_path.detail
    assert session.outcome is BandCheckOutcome.WARNING
    assert session.outcome is not BandCheckOutcome.READY


def test_started_process_does_not_imply_authenticated_control_or_music_path() -> None:
    session = live_session()
    session.apply_live_observations(
        BandCheckObservations(
            music_engine_running=True,
            music_engine_responsive=False,
        )
    )

    assert session.evidence.jamulus_process_started
    assert not session.evidence.jamulus_authenticated_responsive
    assert not session.evidence.transport_datagrams_flowed
    assert not session.evidence.musician_confirmed_two_way_audibility
    engine = session.step(BandCheckStepKey.MUSIC_ENGINE)
    assert engine.status is BandCheckStatus.ACTION_NEEDED
    assert "does not prove" in engine.detail
    assert session.outcome is BandCheckOutcome.ACTION_NEEDED


def test_remote_decoded_fixture_is_not_promoted_to_human_audibility() -> None:
    session = live_session()
    session.apply_live_observations(
        remote_observations(remote_decoded_test_observed=True)
    )

    evidence = session.evidence
    assert evidence.remote_decoded_test_observed
    assert not evidence.transport_datagrams_flowed
    assert not evidence.musician_confirmed_two_way_audibility
    path = session.step(BandCheckStepKey.MUSIC_PATH)
    assert path.status is BandCheckStatus.WARNING
    assert "not that a musician heard it" in path.detail
    assert path.next_action == "We Can Hear Each Other"


def test_musician_confirmation_is_the_only_fact_that_claims_two_way_hearing() -> None:
    session = live_session()
    session.apply_live_observations(remote_observations())

    assert not session.evidence.transport_datagrams_flowed
    session.confirm_two_way_audibility(True)

    evidence = session.evidence
    assert evidence.musician_confirmed_two_way_audibility
    assert not evidence.transport_datagrams_flowed
    assert not evidence.remote_decoded_test_observed
    path = session.step(BandCheckStepKey.MUSIC_PATH)
    assert path.status is BandCheckStatus.PASS
    assert path.next_action == ""
    assert "You confirmed" in path.detail
    assert session.outcome is BandCheckOutcome.READY


def test_material_path_generation_change_invalidates_hearing_confirmation() -> None:
    session = live_session()
    session.apply_live_observations(remote_observations())
    session.confirm_two_way_audibility(True)
    assert session.outcome is BandCheckOutcome.READY

    session.apply_live_observations(
        remote_observations(
            path=TransportPath.SECURE_RELAY,
            generation=2,
            transport_datagrams_flowed=True,
        )
    )

    evidence = session.evidence
    assert evidence.path_generation == 2
    assert evidence.path_recheck_required
    assert not evidence.musician_confirmed_two_way_audibility
    assert "two_way_audibility" not in session.manual_confirmations
    path = session.step(BandCheckStepKey.MUSIC_PATH)
    assert path.status is BandCheckStatus.WARNING
    assert path.next_action == "We Can Still Hear Each Other"
    assert "connection changed" in path.detail.lower()
    assert "Using a secure relay" in path.detail

    session.confirm_two_way_audibility(True)
    assert not session.evidence.path_recheck_required
    assert session.evidence.musician_confirmed_two_way_audibility
    assert session.outcome is BandCheckOutcome.READY


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (TransportPath.INTERNET_DIRECT, "Connected directly"),
        (TransportPath.SECURE_RELAY, "Using a secure relay"),
    ],
)
def test_direct_and_relay_use_plain_musician_language(
    path: TransportPath,
    expected: str,
) -> None:
    session = live_session()
    session.apply_live_observations(
        remote_observations(path=path, transport_datagrams_flowed=True)
    )
    rendered = " ".join(
        (
            session.step(BandCheckStepKey.MUSIC_PATH).title,
            session.step(BandCheckStepKey.MUSIC_PATH).detail,
            session.step(BandCheckStepKey.MUSIC_PATH).next_action,
        )
    )

    assert expected in rendered
    for protocol_word in ("ice", "stun", "turn", "quic", "nat"):
        assert protocol_word not in rendered.lower().split()


@pytest.mark.parametrize(
    ("quality", "expected_status", "phrase"),
    [
        (
            ConnectionQuality.DIFFICULT,
            BandCheckStatus.WARNING,
            "difficult for live playing",
        ),
        (
            ConnectionQuality.UNUSABLE,
            BandCheckStatus.ACTION_NEEDED,
            "needs attention",
        ),
    ],
)
def test_connection_quality_is_independent_of_hearing_confirmation(
    quality: ConnectionQuality,
    expected_status: BandCheckStatus,
    phrase: str,
) -> None:
    session = live_session()
    session.apply_live_observations(remote_observations(quality=quality))
    session.confirm_two_way_audibility(True)

    path = session.step(BandCheckStepKey.MUSIC_PATH)
    assert session.evidence.musician_confirmed_two_way_audibility
    assert session.evidence.connection_quality is quality
    assert path.status is expected_status
    assert phrase in path.detail


def test_primary_action_prioritizes_required_failures_then_music_confirmation() -> None:
    session = BandCheckSession(
        mode=BandCheckMode.LIVE_OBSERVE,
        steps=[
            step(
                BandCheckStepKey.MUSIC_ENGINE,
                BandCheckStatus.ACTION_NEEDED,
                "Restart Music",
            ),
            step(
                BandCheckStepKey.AUDIO_INPUT,
                BandCheckStatus.RUNNING,
                "Play a Note",
            ),
            step(
                BandCheckStepKey.MUSIC_PATH,
                BandCheckStatus.WARNING,
                "We Can Hear Each Other",
            ),
        ],
    )

    assert session.primary_action == "Restart Music"
    assert session.primary_action_step.key is BandCheckStepKey.MUSIC_ENGINE
    session.update_step(BandCheckStepKey.MUSIC_ENGINE, status=BandCheckStatus.PASS)
    assert session.primary_action == "Play a Note"
    session.update_step(BandCheckStepKey.AUDIO_INPUT, status=BandCheckStatus.PASS)
    assert session.primary_action == "We Can Hear Each Other"
    assert session.primary_action_step.key is BandCheckStepKey.MUSIC_PATH


def test_local_input_output_and_recording_proofs_are_separate_facts() -> None:
    session = BandCheckSession(
        mode=BandCheckMode.PRE_SESSION,
        steps=[
            step(BandCheckStepKey.AUDIO_INPUT, BandCheckStatus.PENDING),
            step(BandCheckStepKey.HEADPHONES, BandCheckStatus.PENDING),
            step(BandCheckStepKey.TEST_RECORDING, BandCheckStatus.PENDING),
            step(BandCheckStepKey.RECORDING_PATH, BandCheckStatus.PENDING),
            step(BandCheckStepKey.STUDIO, BandCheckStatus.PENDING),
        ],
    )

    session.observe_input(rms=0.05, peak=0.1, clipped=False)
    assert session.evidence.local_input_observed
    assert not session.evidence.local_output_confirmed
    assert not session.evidence.local_recording_heard

    session.confirm_headphones(True)
    assert session.evidence.local_output_confirmed
    assert not session.evidence.local_recording_heard

    session.mark_scratch_recording(
        valid=True,
        duration_s=5,
        sample_rate=48_000,
        channels=1,
        has_signal=True,
    )
    assert not session.evidence.local_recording_heard
    session.confirm_scratch_playback(True)
    assert session.evidence.local_recording_heard
    assert not session.evidence.jamulus_process_started
    assert not session.evidence.transport_datagrams_flowed
    assert not session.evidence.musician_confirmed_two_way_audibility


def test_connection_path_observation_requires_a_positive_generation() -> None:
    with pytest.raises(ValueError, match="requires a generation"):
        BandCheckObservations(connection_path=TransportPath.INTERNET_DIRECT)
    with pytest.raises(ValueError, match="requires a selected path"):
        BandCheckObservations(connection_quality=ConnectionQuality.PLAYABLE)
    with pytest.raises(ValueError, match="non-negative"):
        BandCheckObservations(path_generation=-1)

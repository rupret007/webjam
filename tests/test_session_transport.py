from __future__ import annotations

import pytest

from core.session_transport import (
    MAX_CONNECTION_TIMELINE,
    ConnectionQuality,
    SessionRole,
    SessionTransportCoordinator,
    TransportEventCode,
    TransportHealth,
    TransportPath,
    TransportPhase,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def health(
    path: TransportPath,
    *,
    generation: int = 1,
    now: float = 100.0,
    rtt: float = 20.0,
    jitter: float = 2.0,
    loss: float = 0.0,
    outages: int = 0,
    samples: int = 20,
    received: int = 20,
) -> TransportHealth:
    return TransportHealth(
        path=path,
        generation=generation,
        observed_monotonic=now,
        control_reachable=True,
        handshake_ms=12.0,
        round_trip_ms=rtt,
        jitter_ms=jitter,
        loss_percent=loss,
        reorder_percent=0.0,
        recent_outages=outages,
        audio_datagrams_sent=samples,
        audio_datagrams_received=received,
        sample_count=samples,
    )


def test_health_classifies_transport_without_claiming_audibility() -> None:
    sample = health(TransportPath.INTERNET_DIRECT)

    assert sample.quality is ConnectionQuality.PLAYABLE
    assert sample.has_audio_transport_evidence is True
    assert "audib" not in sample.quality.musician_label.lower()


@pytest.mark.parametrize(
    ("kwargs", "quality"),
    [
        ({"rtt": 45.0, "jitter": 8.0, "loss": 1.0}, ConnectionQuality.PLAYABLE),
        ({"rtt": 80.0, "jitter": 18.0, "loss": 3.0}, ConnectionQuality.DIFFICULT),
        ({"rtt": 120.0, "jitter": 30.0, "loss": 8.0}, ConnectionQuality.UNUSABLE),
    ],
)
def test_health_quality_boundaries(kwargs, quality) -> None:
    assert health(TransportPath.SECURE_RELAY, **kwargs).quality is quality


def test_coordinator_selects_measured_direct_path_and_publishes_safe_copy() -> None:
    clock = FakeClock()
    coordinator = SessionTransportCoordinator(clock=clock)

    assert coordinator.begin(SessionRole.HOST).phase is TransportPhase.PREPARING
    assert coordinator.begin_probing().musician_status == "Finding the fastest path"
    coordinator.observe(health(TransportPath.INTERNET_DIRECT))
    coordinator.observe(
        health(TransportPath.SECURE_RELAY, rtt=35.0, jitter=4.0, loss=0.2)
    )

    snapshot = coordinator.choose_path()

    assert snapshot.selected_path is TransportPath.INTERNET_DIRECT
    assert snapshot.phase is TransportPhase.CONNECTED
    assert snapshot.musician_status == "Connected directly"
    assert snapshot.decision is not None
    assert snapshot.decision.reason == "best_viable_path"


def test_recording_defers_a_better_path_until_a_safe_boundary() -> None:
    clock = FakeClock()
    coordinator = SessionTransportCoordinator(clock=clock)
    coordinator.begin(SessionRole.GUEST)
    coordinator.begin_probing()
    coordinator.observe(health(TransportPath.SECURE_RELAY, rtt=35.0))
    assert coordinator.choose_path().selected_path is TransportPath.SECURE_RELAY
    clock.advance(20)
    coordinator.observe(
        health(TransportPath.INTERNET_DIRECT, now=clock(), rtt=5.0)
    )
    coordinator.observe(
        health(TransportPath.SECURE_RELAY, now=clock(), rtt=35.0)
    )

    deferred = coordinator.choose_path(recording_active=True)

    assert deferred.selected_path is TransportPath.SECURE_RELAY
    assert deferred.decision is not None
    assert deferred.decision.selected is TransportPath.INTERNET_DIRECT
    assert deferred.decision.deferred_until_safe_boundary is True
    assert deferred.timeline[-1].code is TransportEventCode.MIGRATION_DEFERRED


def test_network_change_invalidates_old_evidence_and_generation() -> None:
    coordinator = SessionTransportCoordinator(clock=FakeClock())
    coordinator.begin(SessionRole.HOST)
    coordinator.begin_probing()
    coordinator.observe(health(TransportPath.INTERNET_DIRECT))
    coordinator.choose_path()

    changed = coordinator.network_changed()

    assert changed.generation == 2
    assert changed.phase is TransportPhase.PROBING
    assert changed.selected_path is None
    assert changed.evidence == ()
    with pytest.raises(ValueError, match="generation"):
        coordinator.observe(health(TransportPath.INTERNET_DIRECT, generation=1))


def test_retries_are_bounded_and_exhaustion_has_one_truthful_state() -> None:
    coordinator = SessionTransportCoordinator(retry_budget=2, clock=FakeClock())
    coordinator.begin(SessionRole.GUEST)

    first = coordinator.path_failed()
    second = coordinator.path_failed()
    exhausted = coordinator.path_failed()

    assert first.retries_remaining == 1
    assert second.retries_remaining == 0
    assert exhausted.phase is TransportPhase.EXHAUSTED
    assert exhausted.selected_path is None
    assert exhausted.musician_status == "The host is temporarily unreachable"
    assert exhausted.timeline[-1].code is TransportEventCode.RETRIES_EXHAUSTED


def test_connection_timeline_is_bounded_and_contains_no_freeform_secrets() -> None:
    coordinator = SessionTransportCoordinator(retry_budget=100, clock=FakeClock())
    coordinator.begin(SessionRole.HOST)
    for _ in range(MAX_CONNECTION_TIMELINE + 20):
        coordinator.path_failed()
        coordinator.begin_probing()

    timeline = coordinator.snapshot().timeline

    assert len(timeline) == MAX_CONNECTION_TIMELINE
    assert [event.sequence for event in timeline] == sorted(
        event.sequence for event in timeline
    )
    assert all(not hasattr(event, "detail") for event in timeline)


def test_health_rejects_impossible_or_unbounded_metrics() -> None:
    with pytest.raises(ValueError, match="loss_percent"):
        health(TransportPath.SECURE_RELAY, loss=101)
    with pytest.raises(ValueError, match="generation"):
        health(TransportPath.SECURE_RELAY, generation=0)

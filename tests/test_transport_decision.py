from __future__ import annotations

from core.session_transport import (
    ConnectionQuality,
    TransportEvidence,
    TransportHealth,
    TransportPath,
)
from core.transport_decision import ConnectionPathPolicy


def sample(
    path: TransportPath,
    *,
    now: float = 10.0,
    rtt: float = 20.0,
    jitter: float = 2.0,
    loss: float = 0.0,
    outages: int = 0,
) -> TransportHealth:
    return TransportHealth(
        path=path,
        generation=1,
        observed_monotonic=now,
        control_reachable=True,
        handshake_ms=10,
        round_trip_ms=rtt,
        jitter_ms=jitter,
        loss_percent=loss,
        reorder_percent=0,
        recent_outages=outages,
        audio_datagrams_sent=20,
        audio_datagrams_received=20,
        sample_count=20,
    )


def evidence(*items: TransportHealth) -> tuple[TransportEvidence, ...]:
    output = []
    for item in items:
        output.append(TransportEvidence(item.path, item.generation).append(item))
    return tuple(output)


def test_relay_is_selected_when_direct_is_not_viable() -> None:
    policy = ConnectionPathPolicy()
    decision = policy.choose(
        evidence(
            sample(TransportPath.INTERNET_DIRECT, rtt=140, jitter=40, loss=10),
            sample(TransportPath.SECURE_RELAY, rtt=40, jitter=6, loss=0.5),
        ),
        current=None,
        now=10,
        last_switch_monotonic=None,
        recording_active=False,
    )

    assert decision.selected is TransportPath.SECURE_RELAY
    assert decision.quality is ConnectionQuality.PLAYABLE


def test_small_score_gain_does_not_flap_an_existing_path() -> None:
    policy = ConnectionPathPolicy(switch_margin=20, cooldown_seconds=0)
    decision = policy.choose(
        evidence(
            sample(TransportPath.INTERNET_DIRECT, rtt=24),
            sample(TransportPath.SECURE_RELAY, rtt=20),
        ),
        current=TransportPath.INTERNET_DIRECT,
        now=10,
        last_switch_monotonic=0,
        recording_active=False,
    )

    assert decision.selected is TransportPath.INTERNET_DIRECT
    assert decision.reason == "keep_stable_path"


def test_large_score_gain_switches_after_cooldown() -> None:
    policy = ConnectionPathPolicy(switch_margin=20, cooldown_seconds=10)
    decision = policy.choose(
        evidence(
            sample(
                TransportPath.SECURE_RELAY,
                now=30,
                rtt=70,
                jitter=15,
                loss=2,
            ),
            sample(
                TransportPath.INTERNET_DIRECT,
                now=30,
                rtt=10,
                jitter=1,
                loss=0,
            ),
        ),
        current=TransportPath.SECURE_RELAY,
        now=30,
        last_switch_monotonic=10,
        recording_active=False,
    )

    assert decision.selected is TransportPath.INTERNET_DIRECT
    assert decision.reason == "best_viable_path"


def test_cooldown_holds_current_path_even_when_another_scores_higher() -> None:
    policy = ConnectionPathPolicy(switch_margin=0, cooldown_seconds=10)
    decision = policy.choose(
        evidence(
            sample(TransportPath.SECURE_RELAY, rtt=70, jitter=15, loss=2),
            sample(TransportPath.INTERNET_DIRECT, rtt=10),
        ),
        current=TransportPath.SECURE_RELAY,
        now=15,
        last_switch_monotonic=10,
        recording_active=False,
    )

    assert decision.selected is TransportPath.SECURE_RELAY
    assert decision.reason == "cooldown"


def test_stale_paths_are_not_selected() -> None:
    policy = ConnectionPathPolicy(stale_after_seconds=5)
    decision = policy.choose(
        evidence(sample(TransportPath.INTERNET_DIRECT, now=1)),
        current=None,
        now=10,
        last_switch_monotonic=None,
        recording_active=False,
    )

    assert decision.selected is None
    assert decision.reason == "no_viable_path"


def test_loss_and_outages_outweigh_small_direct_latency_advantage() -> None:
    policy = ConnectionPathPolicy()
    decision = policy.choose(
        evidence(
            sample(
                TransportPath.INTERNET_DIRECT,
                rtt=10,
                jitter=4,
                loss=4,
                outages=2,
            ),
            sample(TransportPath.SECURE_RELAY, rtt=35, jitter=3, loss=0),
        ),
        current=None,
        now=10,
        last_switch_monotonic=None,
        recording_active=False,
    )

    assert decision.selected is TransportPath.SECURE_RELAY

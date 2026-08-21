"""Deterministic path scoring, stability hysteresis, and recording safety."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.session_transport import (
    ConnectionPathDecision,
    ConnectionQuality,
    TransportEvidence,
    TransportHealth,
    TransportPath,
    latest_health_by_path,
)


@dataclass(frozen=True, slots=True)
class PathScore:
    path: TransportPath
    value: float
    quality: ConnectionQuality


@dataclass(frozen=True, slots=True)
class ConnectionPathPolicy:
    """Prefer measured stability; use relay cost only as a small tie-breaker."""

    switch_margin: float = 20.0
    cooldown_seconds: float = 10.0
    stale_after_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.switch_margin < 0:
            raise ValueError("switch margin must be non-negative")
        if self.cooldown_seconds < 0 or self.stale_after_seconds <= 0:
            raise ValueError("path timing policy must be positive")

    def score(self, health: TransportHealth, *, now: float) -> PathScore | None:
        if (
            not health.complete_probe
            or health.age_seconds(now=now) > self.stale_after_seconds
        ):
            return None
        quality = health.quality
        if quality is ConnectionQuality.UNUSABLE:
            return None
        assert health.round_trip_ms is not None
        assert health.jitter_ms is not None
        assert health.loss_percent is not None
        reorder = health.reorder_percent or 0.0
        handshake = health.handshake_ms or 0.0
        relay_penalty = 8.0 if health.path is TransportPath.SECURE_RELAY else 0.0
        value = (
            1000.0
            - health.round_trip_ms
            - 2.0 * health.jitter_ms
            - 12.0 * health.loss_percent
            - 4.0 * reorder
            - 25.0 * health.recent_outages
            - 10.0 * health.reconnect_count
            - min(40.0, health.backpressure_bytes / 65_536.0)
            - min(20.0, handshake / 50.0)
            - relay_penalty
        )
        return PathScore(health.path, value, quality)

    def choose(
        self,
        evidence: Iterable[TransportEvidence],
        *,
        current: TransportPath | None,
        now: float,
        last_switch_monotonic: float | None,
        recording_active: bool,
    ) -> ConnectionPathDecision:
        latest = latest_health_by_path(evidence)
        scored = {
            path: score
            for path, health in latest.items()
            if (score := self.score(health, now=now)) is not None
        }
        if not scored:
            return ConnectionPathDecision(
                selected=None,
                quality=ConnectionQuality.UNUSABLE,
                reason="no_viable_path",
            )
        best = max(
            scored.values(),
            key=lambda item: (item.value, -list(TransportPath).index(item.path)),
        )
        current_score = scored.get(current) if current is not None else None
        if current_score is None:
            return ConnectionPathDecision(
                selected=best.path,
                quality=best.quality,
                reason="best_viable_path",
                score=best.value,
            )
        if best.path is current:
            return ConnectionPathDecision(
                selected=current,
                quality=current_score.quality,
                reason="keep_stable_path",
                score=current_score.value,
            )
        if recording_active:
            return ConnectionPathDecision(
                selected=best.path,
                quality=best.quality,
                reason="recording_boundary",
                score=best.value,
                deferred_until_safe_boundary=True,
            )
        in_cooldown = bool(
            last_switch_monotonic is not None
            and now - last_switch_monotonic < self.cooldown_seconds
        )
        if in_cooldown:
            return ConnectionPathDecision(
                selected=current,
                quality=current_score.quality,
                reason="cooldown",
                score=current_score.value,
            )
        if best.value < current_score.value + self.switch_margin:
            return ConnectionPathDecision(
                selected=current,
                quality=current_score.quality,
                reason="keep_stable_path",
                score=current_score.value,
            )
        return ConnectionPathDecision(
            selected=best.path,
            quality=best.quality,
            reason="best_viable_path",
            score=best.value,
        )

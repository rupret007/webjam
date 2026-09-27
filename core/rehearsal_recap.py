"""Local end-of-session recap snapshot for Music rehearsals."""

from __future__ import annotations

from dataclasses import dataclass

from core.session_conductor import SessionConductorFacts
from core.session_intelligence import SessionPulse


@dataclass(frozen=True)
class RehearsalRecapSnapshot:
    """Frozen handoff shown after a completed Music End/Leave."""

    duration_seconds: int
    pulse: SessionPulse
    take_status: str | None = None


def take_status_label_for_recap(facts: SessionConductorFacts) -> str | None:
    """Return recap take copy only when a take exists; never fabricate one."""

    if not (facts.take_available or facts.take_path):
        return None
    if facts.take_validated:
        return "Ready"
    return "Needs attention"


def format_recap_duration(seconds: int) -> str:
    bounded = max(0, int(seconds))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"

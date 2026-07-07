"""Session-health snapshot for truth-in-UI decisions.

This model keeps process launch state separate from proven Jamulus session
truth.  A Jamulus subprocess can be alive while RPC auth, participant data, or
meters are still missing; the UI should expose that distinction.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


@dataclass
class SessionHealth:
    process_state: str = "Not launched"
    rpc_available: bool = False
    connected: bool = False
    participant_count: int = 0
    last_participant_at: float | None = None
    last_level_at: float | None = None
    meter_source: str = "preview"
    recorder_state: str = "idle"
    last_rpc_result: str = "none"

    def mark_process(self, state: str, *, rpc_available: bool = False) -> None:
        self.process_state = str(state or "unknown")
        self.rpc_available = bool(rpc_available)

    def mark_participants(self, count: int, *, now: float | None = None) -> None:
        self.participant_count = max(0, int(count))
        self.connected = self.participant_count > 0
        self.last_participant_at = time.monotonic() if now is None else now

    def mark_levels(self, source: str, *, now: float | None = None) -> None:
        self.meter_source = str(source or "unknown")
        self.last_level_at = time.monotonic() if now is None else now

    def mark_recorder(self, *, armed: bool, recording: bool) -> None:
        if recording:
            self.recorder_state = "recording"
        elif armed:
            self.recorder_state = "armed"
        else:
            self.recorder_state = "idle"

    def mark_rpc_result(self, command: str, ok: bool, detail: str = "") -> None:
        status = "ok" if ok else "failed"
        suffix = f": {detail}" if detail else ""
        self.last_rpc_result = f"{command} {status}{suffix}"

    def reset_live_truth(self) -> None:
        self.connected = False
        self.participant_count = 0
        self.last_participant_at = None
        self.last_level_at = None
        self.meter_source = "preview"
        self.recorder_state = "idle"

    def participant_age(self, *, now: float | None = None) -> float | None:
        if self.last_participant_at is None:
            return None
        stamp = time.monotonic() if now is None else now
        return max(0.0, stamp - self.last_participant_at)

    def level_age(self, *, now: float | None = None) -> float | None:
        if self.last_level_at is None:
            return None
        stamp = time.monotonic() if now is None else now
        return max(0.0, stamp - self.last_level_at)

    def to_public_dict(self, *, now: float | None = None) -> dict[str, Any]:
        stamp = time.monotonic() if now is None else now
        participant_age = self.participant_age(now=stamp)
        level_age = self.level_age(now=stamp)
        return {
            "process_state": self.process_state,
            "rpc_available": self.rpc_available,
            "connected": self.connected,
            "participant_count": self.participant_count,
            "participant_age_s": (
                None if participant_age is None else round(participant_age, 1)
            ),
            "level_age_s": None if level_age is None else round(level_age, 1),
            "meter_source": self.meter_source,
            "recorder_state": self.recorder_state,
            "last_rpc_result": self.last_rpc_result,
        }

"""Immutable, serializable updater state for service and Qt boundaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping

from core.jamulus_compatibility import ComponentTarget


class JamulusUpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up-to-date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    READY = "ready"
    DEFERRED = "deferred"
    FALLBACK = "fallback"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: Mapping[
    JamulusUpdateState, frozenset[JamulusUpdateState]
] = {
    JamulusUpdateState.IDLE: frozenset(
        {JamulusUpdateState.CHECKING, JamulusUpdateState.FALLBACK}
    ),
    JamulusUpdateState.CHECKING: frozenset(
        {
            JamulusUpdateState.UP_TO_DATE,
            JamulusUpdateState.AVAILABLE,
            JamulusUpdateState.FAILED,
            JamulusUpdateState.CANCELLED,
            JamulusUpdateState.FALLBACK,
        }
    ),
    JamulusUpdateState.UP_TO_DATE: frozenset(
        {JamulusUpdateState.CHECKING, JamulusUpdateState.FALLBACK}
    ),
    JamulusUpdateState.AVAILABLE: frozenset(
        {
            JamulusUpdateState.DOWNLOADING,
            JamulusUpdateState.DEFERRED,
            JamulusUpdateState.CHECKING,
            JamulusUpdateState.FAILED,
            JamulusUpdateState.CANCELLED,
        }
    ),
    JamulusUpdateState.DOWNLOADING: frozenset(
        {
            JamulusUpdateState.READY,
            JamulusUpdateState.FAILED,
            JamulusUpdateState.CANCELLED,
            JamulusUpdateState.FALLBACK,
        }
    ),
    JamulusUpdateState.READY: frozenset(
        {
            JamulusUpdateState.UP_TO_DATE,
            JamulusUpdateState.DEFERRED,
            JamulusUpdateState.FAILED,
            JamulusUpdateState.FALLBACK,
        }
    ),
    JamulusUpdateState.DEFERRED: frozenset(
        {
            JamulusUpdateState.READY,
            JamulusUpdateState.CHECKING,
            JamulusUpdateState.FALLBACK,
            JamulusUpdateState.FAILED,
        }
    ),
    JamulusUpdateState.FALLBACK: frozenset(
        {
            JamulusUpdateState.CHECKING,
            JamulusUpdateState.AVAILABLE,
            JamulusUpdateState.READY,
            JamulusUpdateState.FAILED,
        }
    ),
    JamulusUpdateState.FAILED: frozenset(
        {
            JamulusUpdateState.CHECKING,
            JamulusUpdateState.FALLBACK,
            JamulusUpdateState.IDLE,
        }
    ),
    JamulusUpdateState.CANCELLED: frozenset(
        {
            JamulusUpdateState.CHECKING,
            JamulusUpdateState.AVAILABLE,
            JamulusUpdateState.IDLE,
            JamulusUpdateState.FALLBACK,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class JamulusUpdateSnapshot:
    state: JamulusUpdateState = JamulusUpdateState.IDLE
    active_version: str = ""
    available_version: str = ""
    target: str = ""
    progress_percent: int = 0
    reason_code: str = ""
    message: str = ""
    restart_when_idle: bool = False
    checked_at_utc: str = ""

    def __post_init__(self) -> None:
        try:
            state = JamulusUpdateState(self.state)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Jamulus update state") from exc
        object.__setattr__(self, "state", state)
        for name in (
            "active_version",
            "available_version",
            "target",
            "reason_code",
            "message",
            "checked_at_utc",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be text")
            limit = 512 if name == "message" else 128
            if len(value) > limit or any(
                ord(character) < 32 and character not in {"\t"} for character in value
            ):
                raise ValueError(f"{name} is invalid")
        if self.target:
            ComponentTarget(self.target)
        if (
            isinstance(self.progress_percent, bool)
            or not isinstance(self.progress_percent, int)
            or not 0 <= self.progress_percent <= 100
        ):
            raise ValueError("progress_percent must be between 0 and 100")
        if not isinstance(self.restart_when_idle, bool):
            raise TypeError("restart_when_idle must be boolean")

    def transition(
        self,
        state: JamulusUpdateState,
        **changes: object,
    ) -> "JamulusUpdateSnapshot":
        destination = JamulusUpdateState(state)
        if (
            destination is not self.state
            and destination not in _ALLOWED_TRANSITIONS[self.state]
        ):
            raise ValueError(
                f"invalid updater transition: {self.state.value} -> "
                f"{destination.value}"
            )
        return replace(self, state=destination, **changes)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "active_version": self.active_version,
            "available_version": self.available_version,
            "target": self.target,
            "progress_percent": self.progress_percent,
            "reason_code": self.reason_code,
            "message": self.message,
            "restart_when_idle": self.restart_when_idle,
            "checked_at_utc": self.checked_at_utc,
        }

    @classmethod
    def from_dict(cls, value: object) -> "JamulusUpdateSnapshot":
        if not isinstance(value, dict):
            raise ValueError("updater snapshot must be an object")
        keys = frozenset(
            {
                "state",
                "active_version",
                "available_version",
                "target",
                "progress_percent",
                "reason_code",
                "message",
                "restart_when_idle",
                "checked_at_utc",
            }
        )
        if frozenset(value) != keys:
            raise ValueError("updater snapshot has an invalid schema")
        return cls(
            state=JamulusUpdateState(value["state"]),
            active_version=value["active_version"],
            available_version=value["available_version"],
            target=value["target"],
            progress_percent=value["progress_percent"],
            reason_code=value["reason_code"],
            message=value["message"],
            restart_when_idle=value["restart_when_idle"],
            checked_at_utc=value["checked_at_utc"],
        )


__all__ = ["JamulusUpdateSnapshot", "JamulusUpdateState"]

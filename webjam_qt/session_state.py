"""UI-only session truth presented by the Live workspace."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionPhase(str, Enum):
    NOT_CONNECTED = "not_connected"
    CONNECTING = "connecting"
    PRACTICE = "practice"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass(frozen=True)
class SessionUiState:
    """A single renderable snapshot; it owns no service or process state."""

    phase: SessionPhase
    title: str
    message: str
    primary_text: str = "Start Audio"
    primary_enabled: bool = True
    show_ready_check: bool = True
    show_practice: bool = False
    hint: str = ""

    @classmethod
    def idle(cls, server: str = "") -> "SessionUiState":
        return cls(
            SessionPhase.NOT_CONNECTED,
            "Ready when you are",
            "Run a quick check, then start audio to join your band.",
            show_practice=True,
            hint=f"Audio joins {server}" if server else "",
        )

    @classmethod
    def connecting(cls, server: str) -> "SessionUiState":
        return cls(
            SessionPhase.CONNECTING,
            "Connecting to your band…",
            f"Starting Jamulus and contacting {server}.",
            "Connecting…",
            False,
            False,
        )

    @classmethod
    def practice(cls) -> "SessionUiState":
        return cls(
            SessionPhase.PRACTICE,
            "Starting private practice…",
            "This local session is private and does not connect to your band.",
            "Starting…",
            False,
            False,
        )

    @classmethod
    def reconnecting(cls, attempt: int | None = None) -> "SessionUiState":
        detail = (
            f"Reconnecting automatically (attempt {attempt} of 5). Your mix is safe."
            if attempt is not None
            else "WebJam is reconnecting automatically. Your saved mix is safe."
        )
        return cls(
            SessionPhase.RECONNECTING,
            "Jamulus disconnected",
            detail,
            "Reconnecting…",
            False,
            False,
        )

    @classmethod
    def reconnect_failed(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "Could not reconnect",
            "Start audio to try again. If it fails, run Ready Check for the next step.",
            "Try Audio Again",
        )

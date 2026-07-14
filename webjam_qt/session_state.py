"""UI-only session truth presented by the Live workspace."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionPhase(str, Enum):
    NOT_CONNECTED = "not_connected"
    CONNECTING = "connecting"
    PRACTICE = "practice"
    RECONNECTING = "reconnecting"
    ENDING = "ending"
    PERMISSION_REQUIRED = "permission_required"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"


@dataclass(frozen=True)
class SessionUiState:
    """A single renderable snapshot; it owns no service or process state."""

    phase: SessionPhase
    title: str
    message: str
    primary_text: str = "Start Session"
    primary_enabled: bool = True
    show_ready_check: bool = True
    show_practice: bool = False
    hint: str = ""
    primary_action: str = "start"

    @classmethod
    def idle(cls, server: str = "", hosting: bool = False) -> "SessionUiState":
        if hosting:
            hint = "Multitrack recording is ready on this Mac"
        else:
            hint = "Your host records everyone as separate tracks"
        return cls(
            SessionPhase.NOT_CONNECTED,
            "Ready when you are",
            "Start the session and invite your band."
            if hosting else "Start the session to join your band.",
            primary_text="Start Session",
            show_ready_check=False,
            show_practice=False,
            hint=hint,
        )

    @classmethod
    def connecting(cls, server: str) -> "SessionUiState":
        return cls(
            SessionPhase.CONNECTING,
            "Connecting to your band…",
            "Starting the band audio…",
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
            "Band audio disconnected",
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
            "Restart the session and WebJam will reconnect everything automatically.",
            "Restart Session",
        )

    @classmethod
    def connection_failed(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "Couldn’t reach the jam",
            "Make sure you’re on the same Wi-Fi, then try again.",
            "Try Again",
            show_ready_check=False,
        )

    @classmethod
    def host_start_failed(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "Couldn’t start the jam",
            "Try again. If it keeps happening, open More → Troubleshooting.",
            "Try Again",
            show_ready_check=False,
        )

    @classmethod
    def session_unavailable(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "This jam isn’t available",
            "Ask your host to confirm the jam is running and resend the invite.",
            "Try Again",
            show_ready_check=False,
        )

    @classmethod
    def remote_session_retry_available(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "Private connection unavailable",
            "WebJam could not start its secure connection. Try again with this invitation.",
            "Try Again",
            show_ready_check=False,
        )

    @classmethod
    def remote_session_fresh_invitation_required(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "Fresh invitation required",
            "This invitation cannot be reused safely. Ask the host for a new link, then open it here.",
            "New invite needed",
            False,
            show_ready_check=False,
        )

    @classmethod
    def permission_required(cls) -> "SessionUiState":
        return cls(
            SessionPhase.PERMISSION_REQUIRED,
            "Microphone access is needed",
            "Your band needs to hear your instrument. macOS will ask for access next.",
            "Continue",
            show_ready_check=False,
        )

    @classmethod
    def permission_denied(cls) -> "SessionUiState":
        return cls(
            SessionPhase.PERMISSION_DENIED,
            "Microphone access is off",
            "Allow WebJam in System Settings → Privacy & Security → Microphone.",
            "Open System Settings",
            show_ready_check=False,
            primary_action="microphone_settings",
        )

    @classmethod
    def permission_retry(cls) -> "SessionUiState":
        return cls(
            SessionPhase.PERMISSION_REQUIRED,
            "Allow microphone access, then return",
            "When WebJam is enabled in System Settings, choose Try Again.",
            "Try Again",
            show_ready_check=False,
        )

    @classmethod
    def ending(cls, *, hosting: bool) -> "SessionUiState":
        return cls(
            SessionPhase.ENDING,
            "Ending this jam…" if hosting else "Leaving the jam…",
            (
                "WebJam is safely finishing recordings and disconnecting everyone."
                if hosting
                else "WebJam is disconnecting your audio safely."
            ),
            "Please wait…",
            False,
            False,
        )

    @classmethod
    def stop_failed(cls) -> "SessionUiState":
        return cls(
            SessionPhase.ERROR,
            "WebJam couldn’t finish cleanly",
            "Close WebJam, then reopen it before starting another jam.",
            "Close and reopen WebJam",
            False,
            False,
        )

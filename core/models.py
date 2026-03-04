from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AudioLevel:
    channel_id: int
    rms: float = 0.0
    peak: float = 0.0
    clipped: bool = False
    timestamp: datetime = field(default_factory=_utc_now)


@dataclass
class ParticipantState:
    channel_id: int
    name: str
    ip_address: str = ""
    is_connected: bool = True
    fader_level: int = 100
    pan: int = 50
    muted: bool = False
    solo: bool = False
    audio_level: float = 0.0


@dataclass
class MixerSnapshot:
    participants: Dict[int, ParticipantState]
    created_at: datetime = field(default_factory=_utc_now)
    server_host: Optional[str] = None
    server_port: Optional[int] = None


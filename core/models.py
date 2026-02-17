from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class AudioLevel:
    channel_id: int
    rms: float = 0.0
    peak: float = 0.0
    clipped: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


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
    created_at: datetime = field(default_factory=datetime.utcnow)
    server_host: Optional[str] = None
    server_port: Optional[int] = None


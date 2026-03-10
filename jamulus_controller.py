"""
Jamulus Controller - Interface for communicating with Jamulus client
Provides real-time participant detection and mixer control
"""

import logging
import os
import threading
import time
from typing import List, Callable, Optional, Dict
from dataclasses import dataclass
import json
import tempfile
from pathlib import Path

from core.audio_engine import RealAudioEngine
from core.jamulus_protocol import JamulusProtocolAdapter
from core.logging_config import configure_logging
from core.settings import load_settings


@dataclass
class JamulusParticipant:
    """Represents a participant in the Jamulus session"""
    channel_id: int
    name: str
    ip_address: str = ""
    is_connected: bool = True
    fader_level: int = 100  # 0-100
    pan: int = 50  # 0=left, 50=center, 100=right
    muted: bool = False
    solo: bool = False
    

class JamulusController:
    """
    Controller for Jamulus client
    
    Note: Jamulus uses a UDP-based protocol. This implementation provides
    basic integration. For full control, we monitor Jamulus process output
    and can send commands via command-line arguments on restart.
    
    Future enhancement: Implement full Jamulus protocol for real-time control
    """
    
    def __init__(self, host: str = "127.0.0.1", port: int = 22124):
        self.settings = load_settings()
        self.logger = configure_logging(self.settings).getChild("jamulus_controller")
        self.host = host
        self.port = port
        self.participants: Dict[int, JamulusParticipant] = {}
        self.callbacks: List[Callable] = []
        self._lock = threading.Lock()
        self._pre_solo_mute: Dict[int, bool] = {}
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self._participants_lock = threading.RLock()
        self.protocol = JamulusProtocolAdapter(self.host, self.port, enabled=False)
        self.audio_engine = RealAudioEngine(self.settings, logger=self.logger.getChild("audio_engine"))
        self.last_error: str = ""
        
    def start(self):
        """Start monitoring Jamulus"""
        if self.running:
            return
        
        self.protocol.connect()
        self.audio_engine.start()
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
    def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        self.audio_engine.stop()
        self.protocol.close()
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                self._check_participants()
                time.sleep(1)
                
            except Exception as e:
                self.last_error = str(e)
                self.logger.exception("Jamulus monitoring error: %s", e)
                time.sleep(5)
    
    def _check_participants(self):
        """Check for participant changes via protocol adapter."""
        protocol_participants = self.protocol.request_clients()
        if not protocol_participants:
            return

        with self._participants_lock:
            incoming_ids = set(protocol_participants.keys())
            known_ids = set(self.participants.keys())

            for channel_id in incoming_ids - known_ids:
                self.participants[channel_id] = JamulusParticipant(
                    channel_id=channel_id,
                    name=protocol_participants[channel_id],
                )

            for channel_id in known_ids - incoming_ids:
                del self.participants[channel_id]

            for channel_id in incoming_ids & known_ids:
                self.participants[channel_id].name = protocol_participants[channel_id]
                self.participants[channel_id].is_connected = True

        self._notify_callbacks()
    
    def add_participant(self, name: str, channel_id: int = None) -> JamulusParticipant:
        """Manually add a participant (for testing or manual setup)"""
        with self._participants_lock:
            if channel_id is None:
                channel_id = max(self.participants.keys(), default=-1) + 1
            participant = JamulusParticipant(
                channel_id=channel_id,
                name=name
            )
            self.participants[channel_id] = participant
            cached = {cid: p.name for cid, p in self.participants.items()}
        self.protocol.set_cached_participants(cached)
        self._notify_callbacks()
        return participant
    
    def remove_participant(self, channel_id: int):
        """Remove a participant"""
        should_notify = False
        cached = None
        with self._participants_lock:
            if channel_id in self.participants:
                del self.participants[channel_id]
                cached = {cid: p.name for cid, p in self.participants.items()}
                should_notify = True
        if cached is not None:
            self.protocol.set_cached_participants(cached)
        if should_notify:
            self._notify_callbacks()
    
    def set_fader_level(self, channel_id: int, level: int):
        """
        Set fader level for a channel (0-100)
        
        In a full implementation, this would send a command to Jamulus.
        For now, we just update our internal state.
        """
        with self._participants_lock:
            if channel_id in self.participants:
                self.participants[channel_id].fader_level = max(0, min(100, level))
            else:
                return
        self._apply_mixer_setting(channel_id)
    
    def set_pan(self, channel_id: int, pan: int):
        """Set pan position (0=left, 50=center, 100=right)"""
        with self._participants_lock:
            if channel_id in self.participants:
                self.participants[channel_id].pan = max(0, min(100, pan))
            else:
                return
        self._apply_mixer_setting(channel_id)
    
    def set_mute(self, channel_id: int, muted: bool):
        """Mute/unmute a channel"""
        with self._participants_lock:
            if channel_id not in self.participants:
                return
            self.participants[channel_id].muted = muted
        self._apply_mixer_setting(channel_id)
    
    def set_solo(self, channel_id: int, solo: bool):
        """
        Solo/unsolo a channel, preserving prior mute state.

        Solo semantics: only one channel can be soloed at a time.
        Entering solo mutes all other channels. Switching solo channels keeps
        the original pre-solo mute snapshot. Leaving solo restores the
        snapshot and clears all solo flags.
        """
        affected_ids: list[int] = []
        with self._participants_lock:
            if channel_id not in self.participants:
                return
            affected_ids = list(self.participants.keys())
            currently_solo = any(p.solo for p in self.participants.values())
            if solo:
                if not currently_solo:
                    self._pre_solo_mute = {
                        cid: p.muted for cid, p in self.participants.items()
                    }
                for cid, p in self.participants.items():
                    p.solo = cid == channel_id
                    p.muted = cid != channel_id
            else:
                for cid, p in self.participants.items():
                    p.solo = False
                    p.muted = self._pre_solo_mute.get(cid, False)
                self._pre_solo_mute.clear()
        for cid in affected_ids:
            self._apply_mixer_setting(cid)
    
    def _apply_mixer_setting(self, channel_id: int):
        """Apply mixer settings to Jamulus via protocol adapter and audio engine."""
        with self._participants_lock:
            participant = self.participants.get(channel_id)
            if not participant:
                return
            fader_level = participant.fader_level
            pan = participant.pan
            muted = participant.muted
            effective_level = fader_level / 100.0
            if muted:
                effective_level = 0.0

        self.protocol.apply_mixer(
            channel_id=channel_id,
            fader_level=fader_level,
            pan=pan,
            muted=muted,
        )
        self.audio_engine.set_level_override(channel_id, effective_level)
        self._notify_callbacks()
    
    def get_participants(self) -> List[JamulusParticipant]:
        """Get list of all participants"""
        with self._participants_lock:
            return list(self.participants.values())

    def get_audio_diagnostics(self) -> Dict[str, str]:
        diag = self.audio_engine.diagnostics()
        return {
            "backend": diag.backend,
            "samplerate": str(diag.samplerate),
            "blocksize": str(diag.blocksize),
            "latency_mode": diag.latency_mode,
            "active": str(diag.active),
            "message": diag.message,
            "last_error": self.last_error or "none",
        }
    
    def register_callback(self, callback: Callable):
        """Register a callback for participant updates"""
        with self._lock:
            self.callbacks.append(callback)
    
    def _notify_callbacks(self):
        """Notify all registered callbacks of changes"""
        with self._lock:
            snapshot = list(self.callbacks)
        participants = self.get_participants()
        for callback in snapshot:
            try:
                callback(participants)
            except Exception as e:
                self.logger.warning("Callback error: %s", e)
    
    def save_mix(self, filename: str):
        """Save current mix to file"""
        with self._participants_lock:
            participants = list(self.participants.values())
        mix_data = {
            'participants': [
                {
                    'channel_id': p.channel_id,
                    'name': p.name,
                    'fader_level': p.fader_level,
                    'pan': p.pan,
                    'muted': p.muted,
                    'solo': p.solo
                }
                for p in participants
            ]
        }
        target_path = Path(filename)
        temp_path = None
        try:
            parent = target_path.parent or Path(".")
            parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f"{target_path.stem}.",
                suffix=".tmp",
                dir=str(parent),
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(mix_data, f, indent=2)
            temp_path.replace(target_path)
        except OSError as exc:
            self.logger.warning("Failed to save mix file '%s': %s", filename, exc)
            raise
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    
    def load_mix(self, filename: str):
        """Load mix from file"""
        def _coerce_bool(value: object, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
            return default

        try:
            with open(filename, "r", encoding="utf-8") as f:
                mix_data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to load mix file '%s': %s", filename, exc)
            return

        participants_data = mix_data.get("participants") if isinstance(mix_data, dict) else None
        if not isinstance(participants_data, list):
            self.logger.warning("Mix file '%s' is missing a valid participants list.", filename)
            return

        for p_data in participants_data:
            if not isinstance(p_data, dict):
                continue

            try:
                channel_id = int(p_data.get("channel_id"))
            except (TypeError, ValueError):
                continue

            with self._participants_lock:
                if channel_id not in self.participants:
                    continue

                p = self.participants[channel_id]

                try:
                    fader_level = int(p_data.get("fader_level", p.fader_level))
                except (TypeError, ValueError):
                    fader_level = p.fader_level
                p.fader_level = max(0, min(100, fader_level))

                try:
                    pan = int(p_data.get("pan", p.pan))
                except (TypeError, ValueError):
                    pan = p.pan
                p.pan = max(0, min(100, pan))

                p.muted = _coerce_bool(p_data.get("muted", p.muted), p.muted)
                p.solo = _coerce_bool(p_data.get("solo", p.solo), p.solo)
            # Mixer apply is best-effort; may race with protocol monitor updates.
            self._apply_mixer_setting(channel_id)

class JamulusAudioMonitor:
    """
    Monitor audio levels from Jamulus
    
    This would ideally use PyAudio or similar to monitor
    the actual audio stream levels in real-time.
    """
    
    def __init__(self, controller: JamulusController):
        self.controller = controller
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.audio_levels: Dict[int, float] = {}
        self._levels_lock = threading.Lock()
        self.logger = self.controller.logger.getChild("audio_monitor")
    
    def start(self):
        """Start audio monitoring"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_audio, daemon=True)
        self.monitor_thread.start()
    
    def stop(self):
        """Stop audio monitoring"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
    
    def _monitor_audio(self):
        """Monitor audio levels"""
        while self.running:
            try:
                with self.controller._participants_lock:
                    snapshot = [
                        (cid, p.muted) for cid, p in self.controller.participants.items()
                    ]
                levels = {}
                for channel_id, muted in snapshot:
                    if not muted:
                        levels[channel_id] = self.controller.audio_engine.get_level(channel_id)
                    else:
                        levels[channel_id] = 0.0
                with self._levels_lock:
                    self.audio_levels = levels

                time.sleep(0.05)

            except Exception as e:
                self.logger.warning("Audio monitoring error: %s", e)
                time.sleep(1)
    
    def get_level(self, channel_id: int) -> float:
        """Get current audio level for channel (0.0-1.0)"""
        with self._levels_lock:
            return self.audio_levels.get(channel_id, 0.0)


# Protocol constants for future implementation
JAMULUS_PROTOCOL_VERSION = 3

# Message types (for reference - implementing full protocol is complex)
class JamulusMessageType:
    """Jamulus protocol message types"""
    ACKN = 0  # Acknowledge
    JITT_BUF_SIZE = 1  # Jitter buffer size
    CHANNEL_GAIN = 2  # Channel fader level
    CONN_CLIENTS_LIST = 3  # Connected clients list
    SERVER_FULL = 4  # Server full message
    REQ_CONN_CLIENTS_LIST = 5  # Request clients list
    CHANNEL_NAME = 6  # Channel name
    CHAT_TEXT = 7  # Chat message
    PING_MS = 8  # Ping measurement
    NETW_TRANSPORT_PROPS = 9  # Network transport properties
    REQ_NETW_TRANSPORT_PROPS = 10  # Request network transport properties
    DISCONNECTION = 11  # Disconnection message
    CHANNEL_PAN = 12  # Channel pan setting
    MUTE_STATE = 13  # Channel mute state
    CLIENT_ID = 14  # Client ID assignment


def create_jamulus_controller(server_host: str, server_port: int) -> JamulusController:
    """Factory function to create and start a Jamulus controller"""
    controller = JamulusController(server_host, server_port)
    controller.start()
    return controller


if __name__ == "__main__":
    # Test the controller
    print("Testing Jamulus Controller...")
    
    controller = JamulusController("172.24.194.9", 22124)
    controller.start()
    
    # Add some test participants
    controller.add_participant("Local User", 0)
    controller.add_participant("Guitarist", 1)
    controller.add_participant("Drummer", 2)
    
    # Test mixer controls
    controller.set_fader_level(1, 75)
    controller.set_pan(1, 25)  # Pan left
    controller.set_mute(2, True)
    
    print(f"Participants: {len(controller.get_participants())}")
    for p in controller.get_participants():
        print(f"  - {p.name}: Level={p.fader_level}, Pan={p.pan}, Muted={p.muted}")
    
    # Test audio monitoring
    monitor = JamulusAudioMonitor(controller)
    monitor.start()
    
    print("\nMonitoring audio levels for 5 seconds...")
    for _ in range(10):
        time.sleep(0.5)
        for p in controller.get_participants():
            level = monitor.get_level(p.channel_id)
            bar = "█" * int(level * 20)
            print(f"{p.name}: {bar} {level:.2f}")
        print()
    
    monitor.stop()
    controller.stop()
    
    print("Test complete!")


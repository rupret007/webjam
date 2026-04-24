"""
Jamulus Controller - Interface for communicating with Jamulus client
Provides real-time participant detection and mixer control
"""

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
from core.jamulus_rpc_client import JamulusRpcClient
from core.logging_config import configure_logging
from core.settings import load_settings


@dataclass
class JamulusParticipant:
    """Represents a participant in the Jamulus session"""
    channel_id: int
    name: str
    ip_address: str = ""
    is_connected: bool = True
    fader_level: int = 100  # 0-127 (Jamulus mixer range; 100 = 0 dB, 127 = +6 dB)
    pan: int = 50  # 0=left, 50=center, 100=right
    muted: bool = False
    solo: bool = False
    instrument: str = ""   # as reported by Jamulus JSON-RPC
    is_local: bool = False  # True for the local Jamulus client (from RPC getClientInfo)
    

class JamulusController:
    """
    Controller for Jamulus client
    
    Note: Jamulus uses a UDP-based protocol. This implementation provides
    basic integration. For full control, we monitor Jamulus process output
    and can send commands via command-line arguments on restart.
    
    Future enhancement: Implement full Jamulus protocol for real-time control
    """
    
    def __init__(self, host: str = "127.0.0.1", port: int = 22124, rpc_port: int = 22222):
        self.settings = load_settings()
        self.logger = configure_logging(self.settings).getChild("jamulus_controller")
        self.host = host
        self.port = port
        self.rpc_port = rpc_port
        self.participants: Dict[int, JamulusParticipant] = {}
        self.callbacks: List[Callable] = []
        self._lock = threading.Lock()
        self._pre_solo_mute: Dict[int, bool] = {}
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self._participants_lock = threading.RLock()

        # Primary integration: JSON-RPC (Jamulus 3.9+)
        self.rpc_client = JamulusRpcClient(
            port=rpc_port,
            on_participants_changed=self._on_rpc_participants,
            on_levels=self._on_rpc_levels,
        )

        # Secondary integration: UDP protocol (all versions)
        self.protocol = JamulusProtocolAdapter(
            self.host,
            self.port,
            enabled=True,  # enabled — proper packet format is now implemented
            on_participants_changed=self._on_udp_participants,
            on_levels=self._on_udp_levels,
        )

        self.audio_engine = RealAudioEngine(self.settings, logger=self.logger.getChild("audio_engine"))
        self.last_error: str = ""
        
    def start(self):
        """Start monitoring Jamulus via JSON-RPC (primary) and UDP (fallback)."""
        if self.running:
            return

        self.audio_engine.start()
        self.rpc_client.start()
        self.protocol.start_receiving()
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        """Stop all monitoring."""
        self.running = False
        self.rpc_client.stop()
        self.protocol.stop_receiving()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        self.audio_engine.stop()

    # ------------------------------------------------------------------
    # RPC / UDP participant callbacks (called from background threads)
    # ------------------------------------------------------------------
    def _on_rpc_participants(self, channel_infos: list) -> None:
        """Receive participant list from JSON-RPC (authoritative when available)."""
        normalized: Dict[int, str] = {}
        instrument_map: Dict[int, str] = {}
        is_local_map: Dict[int, bool] = {}
        for info in channel_infos:
            if hasattr(info, "channel_id"):
                cid = info.channel_id
                normalized[cid] = info.name or f"Participant {cid}"
                instrument_map[cid] = getattr(info, "instrument", "") or ""
                is_local_map[cid] = bool(getattr(info, "is_local", False))
            elif isinstance(info, dict):
                cid = info.get("channel_id", -1)
                if cid >= 0:
                    normalized[cid] = info.get("name") or f"Participant {cid}"
                    instrument_map[cid] = info.get("instrument", "") or ""
                    is_local_map[cid] = bool(info.get("is_local", False))
        if normalized:
            self._sync_participants_from_protocol(normalized)
            # Propagate RPC metadata after sync (sync only handles names)
            with self._participants_lock:
                for cid, instrument in instrument_map.items():
                    if cid in self.participants:
                        self.participants[cid].instrument = instrument
                for cid, is_local in is_local_map.items():
                    if cid in self.participants:
                        self.participants[cid].is_local = is_local

    def _on_rpc_levels(self, levels: Dict[int, float]) -> None:
        """Receive audio levels from JSON-RPC SSE events."""
        for channel_id, level in levels.items():
            self.audio_engine.set_level_override(channel_id, level)

    def _on_udp_participants(self, clients: Dict[int, str]) -> None:
        """Receive participant list from UDP protocol (used if RPC unavailable)."""
        if self.rpc_client.available:
            return  # RPC is authoritative; ignore UDP participant updates
        if clients:
            self._sync_participants_from_protocol(clients)

    def _on_udp_levels(self, levels: Dict[int, float]) -> None:
        """Receive audio levels from UDP protocol."""
        if self.rpc_client.available:
            return  # RPC levels (via SSE) take precedence
        for channel_id, level in levels.items():
            self.audio_engine.set_level_override(channel_id, level)

    def _sync_participants_from_protocol(self, incoming: Dict[int, str]) -> None:
        """Merge incoming participant map into internal state, then notify callbacks."""
        apply_ids: list[int] = []
        with self._participants_lock:
            known_ids = set(self.participants.keys())
            incoming_ids = set(incoming.keys())
            active_solo = next(
                (cid for cid, p in self.participants.items() if p.solo), None
            )
            for cid in incoming_ids - known_ids:
                p = JamulusParticipant(channel_id=cid, name=incoming[cid])
                if active_solo is not None and cid != active_solo:
                    p.muted = True
                    self._pre_solo_mute.setdefault(cid, False)
                    apply_ids.append(cid)
                self.participants[cid] = p
            removed_solo = False
            for cid in known_ids - incoming_ids:
                removed = self.participants.pop(cid, None)
                if removed and removed.solo:
                    removed_solo = True
                self._pre_solo_mute.pop(cid, None)
            for cid in incoming_ids & known_ids:
                self.participants[cid].name = incoming[cid]
                self.participants[cid].is_connected = True
            if removed_solo:
                for cid, p in self.participants.items():
                    p.solo = False
                    p.muted = self._pre_solo_mute.get(cid, False)
                    apply_ids.append(cid)
                self._pre_solo_mute.clear()
        for cid in sorted(set(apply_ids)):
            self._apply_mixer_setting(cid, notify=False)
        self._notify_callbacks()
    
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
        """Check for participant changes via UDP protocol adapter.

        When JSON-RPC is available it is the authoritative source —
        ``_on_rpc_participants`` already keeps ``self.participants`` up-to-date.
        Running this method on top of RPC would erase participants every
        second (UDP cache is empty when UDP is disabled) and silently drop
        any fader/mute/solo changes between RPC poll cycles.
        """
        if self.rpc_client.available:
            return  # RPC is authoritative; UDP participant management not needed

        protocol_participants = self.protocol.request_clients()
        if protocol_participants is None:
            return
        if not isinstance(protocol_participants, dict):
            self.logger.warning(
                "Ignoring unexpected participants payload type: %s",
                type(protocol_participants).__name__,
            )
            return

        normalized_participants: Dict[int, str] = {}
        for raw_channel_id, raw_name in protocol_participants.items():
            try:
                channel_id = int(raw_channel_id)
            except (TypeError, ValueError):
                continue
            if channel_id < 0:
                continue
            if isinstance(raw_name, str):
                name = raw_name.strip()
            else:
                name = ""
            normalized_participants[channel_id] = name or f"Participant {channel_id}"

        apply_mixer_ids: list[int] = []

        with self._participants_lock:
            incoming_ids = set(normalized_participants.keys())
            known_ids = set(self.participants.keys())
            active_solo_channel = next(
                (cid for cid, participant in self.participants.items() if participant.solo),
                None,
            )

            for channel_id in incoming_ids - known_ids:
                participant = JamulusParticipant(
                    channel_id=channel_id,
                    name=normalized_participants[channel_id],
                )
                # Keep solo invariant when channels join mid-solo.
                if active_solo_channel is not None and channel_id != active_solo_channel:
                    participant.muted = True
                    self._pre_solo_mute.setdefault(channel_id, False)
                    apply_mixer_ids.append(channel_id)
                self.participants[channel_id] = participant

            removed_solo = False
            for channel_id in known_ids - incoming_ids:
                removed = self.participants.get(channel_id)
                if removed and removed.solo:
                    removed_solo = True
                del self.participants[channel_id]
                self._pre_solo_mute.pop(channel_id, None)

            for channel_id in incoming_ids & known_ids:
                self.participants[channel_id].name = normalized_participants[channel_id]
                self.participants[channel_id].is_connected = True

            if removed_solo:
                for cid, participant in self.participants.items():
                    participant.solo = False
                    participant.muted = self._pre_solo_mute.get(cid, False)
                    apply_mixer_ids.append(cid)
                self._pre_solo_mute.clear()
            elif not any(participant.solo for participant in self.participants.values()):
                self._pre_solo_mute.clear()

        for channel_id in sorted(set(apply_mixer_ids)):
            self._apply_mixer_setting(channel_id, notify=False)
        self.last_error = ""
        self._notify_callbacks()
    
    def add_participant(self, name: str, channel_id: int = None) -> JamulusParticipant:
        """Manually add a participant (for testing or manual setup)"""
        should_apply = False
        with self._participants_lock:
            if channel_id is None:
                channel_id = max(self.participants.keys(), default=-1) + 1
            participant = JamulusParticipant(
                channel_id=channel_id,
                name=name
            )
            active_solo_channel = next(
                (cid for cid, current in self.participants.items() if current.solo),
                None,
            )
            if active_solo_channel is not None and channel_id != active_solo_channel:
                participant.muted = True
                self._pre_solo_mute.setdefault(channel_id, False)
                should_apply = True
            self.participants[channel_id] = participant
            cached = {cid: p.name for cid, p in self.participants.items()}
        self.protocol.set_cached_participants(cached)
        if should_apply:
            self._apply_mixer_setting(channel_id, notify=False)
        self._notify_callbacks()
        return participant
    
    def remove_participant(self, channel_id: int):
        """Remove a participant"""
        should_notify = False
        cached = None
        apply_mixer_ids: list[int] = []
        with self._participants_lock:
            if channel_id in self.participants:
                removed = self.participants[channel_id]
                del self.participants[channel_id]
                self._pre_solo_mute.pop(channel_id, None)
                if removed.solo:
                    for cid, participant in self.participants.items():
                        participant.solo = False
                        participant.muted = self._pre_solo_mute.get(cid, False)
                        apply_mixer_ids.append(cid)
                    self._pre_solo_mute.clear()
                elif not any(participant.solo for participant in self.participants.values()):
                    self._pre_solo_mute.clear()
                cached = {cid: p.name for cid, p in self.participants.items()}
                should_notify = True
        if cached is not None:
            self.protocol.set_cached_participants(cached)
        for cid in sorted(set(apply_mixer_ids)):
            self._apply_mixer_setting(cid, notify=False)
        if should_notify:
            self._notify_callbacks()
    
    def _send_rpc_gain(self, channel_id: int, level: int) -> None:
        """Fire-and-forget RPC gain command — always runs on a background thread."""
        if not self.rpc_client.available:
            return

        def _go() -> None:
            try:
                self.rpc_client.set_channel_gain(channel_id, level)
            except Exception:
                pass

        threading.Thread(target=_go, daemon=True).start()

    def set_fader_level(self, channel_id: int, level: int):
        """Set fader level for a channel (0-127). Sends to Jamulus via RPC or UDP."""
        with self._participants_lock:
            if channel_id in self.participants:
                self.participants[channel_id].fader_level = max(0, min(127, level))
            else:
                return
        # Send to Jamulus via RPC (background thread) + UDP fallback in _apply_mixer_setting
        self._send_rpc_gain(channel_id, level)
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
            active_solo_channel = next(
                (cid for cid, participant in self.participants.items() if participant.solo),
                None,
            )
            if active_solo_channel is None:
                self.participants[channel_id].muted = muted
            else:
                if not self._pre_solo_mute:
                    self._pre_solo_mute = {
                        cid: participant.muted for cid, participant in self.participants.items()
                    }
                self._pre_solo_mute[channel_id] = muted
                if channel_id == active_solo_channel:
                    self.participants[channel_id].muted = muted
                else:
                    # Preserve the requested post-solo mute state without
                    # breaking exclusive solo monitoring in the current mix.
                    self.participants[channel_id].muted = True
            effective_level = 0 if self.participants[channel_id].muted else self.participants[channel_id].fader_level
        # Propagate mute state to Jamulus via RPC (gain=0 means muted).
        self._send_rpc_gain(channel_id, effective_level)
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
                if not currently_solo or not self._pre_solo_mute:
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
            with self._participants_lock:
                participant = self.participants.get(cid)
            if participant is not None:
                effective_level = 0 if participant.muted else participant.fader_level
                self._send_rpc_gain(cid, effective_level)
            self._apply_mixer_setting(cid)
    
    def _apply_mixer_setting(self, channel_id: int, notify: bool = True):
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
        if notify:
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

    @staticmethod
    def _normalize_participant_name(name: object) -> str:
        if name is None:
            return ""
        return str(name).strip().casefold()

    def serialize_mix(self) -> dict:
        """Return the current mix payload in the same format used for file saves."""
        with self._participants_lock:
            participants = list(self.participants.values())
        return {
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

    def apply_mix_data(self, mix_data: object) -> Optional[int]:
        """Apply a previously serialized mix payload to current participants."""
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

        participants_data = mix_data.get("participants") if isinstance(mix_data, dict) else None
        if not isinstance(participants_data, list):
            self.logger.warning("Mix payload is missing a valid participants list.")
            return None

        with self._participants_lock:
            participant_name_by_id = {
                channel_id: self._normalize_participant_name(participant.name)
                for channel_id, participant in self.participants.items()
            }
        name_to_channel_ids: Dict[str, list[int]] = {}
        for channel_id, normalized_name in participant_name_by_id.items():
            if normalized_name:
                name_to_channel_ids.setdefault(normalized_name, []).append(channel_id)

        solo_candidates: list[int] = []
        applied_channel_ids: set[int] = set()
        for p_data in participants_data:
            if not isinstance(p_data, dict):
                continue

            try:
                payload_channel_id = int(p_data.get("channel_id"))
            except (TypeError, ValueError):
                payload_channel_id = None

            payload_name = self._normalize_participant_name(p_data.get("name"))
            channel_id = None
            if payload_channel_id is not None and payload_channel_id in participant_name_by_id:
                current_name = participant_name_by_id[payload_channel_id]
                if not payload_name or current_name == payload_name:
                    channel_id = payload_channel_id
            if channel_id is None and payload_name:
                matching_channel_ids = name_to_channel_ids.get(payload_name, [])
                if len(matching_channel_ids) == 1:
                    channel_id = matching_channel_ids[0]
            if channel_id is None:
                continue

            with self._participants_lock:
                if channel_id not in self.participants:
                    continue

                p = self.participants[channel_id]

                try:
                    fader_level = int(p_data.get("fader_level", p.fader_level))
                except (TypeError, ValueError):
                    fader_level = p.fader_level
                p.fader_level = max(0, min(127, fader_level))

                try:
                    pan = int(p_data.get("pan", p.pan))
                except (TypeError, ValueError):
                    pan = p.pan
                p.pan = max(0, min(100, pan))

                p.muted = _coerce_bool(p_data.get("muted", p.muted), p.muted)
                p.solo = _coerce_bool(p_data.get("solo", p.solo), p.solo)
                if p.solo:
                    solo_candidates.append(channel_id)
            # Mixer apply is best-effort; may race with protocol monitor updates.
            self._apply_mixer_setting(channel_id)
            applied_channel_ids.add(channel_id)

        # Enforce exclusive solo semantics for payloads that contain multiple
        # solo=true entries; choose the last soloed channel in payload order.
        if solo_candidates:
            self.set_solo(solo_candidates[-1], True)
        else:
            with self._participants_lock:
                if not any(p.solo for p in self.participants.values()):
                    self._pre_solo_mute.clear()
        if participants_data and not applied_channel_ids:
            self.logger.warning("Mix payload did not match any current participants.")
        return len(applied_channel_ids)

    def save_mix(self, filename: str):
        """Save current mix to file"""
        mix_data = self.serialize_mix()
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
    
    def load_mix(self, filename: str) -> Optional[int]:
        """Load mix from file"""
        try:
            with open(filename, "r", encoding="utf-8") as f:
                mix_data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to load mix file '%s': %s", filename, exc)
            return None
        return self.apply_mix_data(mix_data)

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


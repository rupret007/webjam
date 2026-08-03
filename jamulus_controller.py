"""
Jamulus Controller - Interface for communicating with Jamulus client
Provides real-time participant detection and mixer control.

Participant-state ownership (the ``participants`` dict, ``_pre_solo_mute``
snapshot, and the lock that protects them) lives in
:mod:`jamulus_state_manager`.  This module focuses on transport — RPC, UDP,
audio engine — and forwards state operations to a ``ParticipantStateManager``
instance.  ``JamulusParticipant`` is re-exported here so existing callers
(``from jamulus_controller import JamulusParticipant``) continue to work.
"""

import os
import threading
import time
from dataclasses import replace
from typing import List, Callable, Optional, Dict
import json
import tempfile
from pathlib import Path

from core.audio_engine import RealAudioEngine
from core.jamulus_name import JamulusNameError, validate_jamulus_name
from core.jamulus_protocol import JamulusProtocolAdapter
from core.jamulus_rpc_client import (
    JamulusOrderedRosterProof,
    JamulusRpcClient,
    JamulusRpcMonitorIdentity,
    JamulusRpcMonitorSnapshot,
)
from core.logging_config import configure_logging
from core.settings import load_settings
from jamulus_state_manager import JamulusParticipant, ParticipantStateManager

__all__ = [
    "JamulusParticipant",
    "JamulusController",
    "JamulusAudioMonitor",
    "JamulusMessageType",
    "JamulusRpcMonitorIdentity",
    "JamulusRpcMonitorSnapshot",
    "JamulusOrderedRosterProof",
    "create_jamulus_controller",
]


class JamulusController:
    """
    Controller for Jamulus client
    
    Note: Jamulus uses a UDP-based protocol. This implementation provides
    basic integration. For full control, we monitor Jamulus process output
    and can send commands via command-line arguments on restart.
    
    Future enhancement: Implement full Jamulus protocol for real-time control
    """
    
    live_send_mute = False

    def __init__(self, host: str = "127.0.0.1", port: int = 22124, rpc_port: int = 22222):
        self.settings = load_settings()
        self.logger = configure_logging(self.settings).getChild("jamulus_controller")
        # Fresh installs have no server configured (jamulus_server defaults to
        # "").  The UDP protocol adapter validates host as non-empty, and the
        # controller must still construct so the app can start and walk the
        # user through the wizard — fall back to loopback.  Launching is
        # separately guarded in BridgeService. The legacy UDP adapter remains
        # dormant in the product, so the placeholder is inert.
        self.host = (host or "").strip() or "127.0.0.1"
        self.port = port
        self.rpc_port = rpc_port
        self.callbacks: List[Callable] = []
        self.identity_callbacks: List[Callable] = []
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._rpc_identity_lock = threading.RLock()
        self.running = False
        self._stopping = False
        self._rpc_monitor_identity: JamulusRpcMonitorIdentity | None = None
        self._rpc_starting_process: tuple[int, int] | None = None
        self.monitor_thread: Optional[threading.Thread] = None

        # State manager owns participants/_pre_solo_mute/_participants_lock.
        # Property setters/getters on this controller proxy through to it.
        self._build_state_manager()

        # Optional consumer hook for incoming band chat (set by the UI).
        self.chat_callback: Optional[Callable[[str], None]] = None
        self.chat_callback_with_source: Optional[
            Callable[[str, JamulusRpcMonitorIdentity], None]
        ] = None
        # Optional hook for server recorder-state changes (set by the UI).
        # Called with (recording: bool, raw_state: int).
        self.recorder_state_callback: Optional[Callable[[bool, int], None]] = None
        self.recorder_state_callback_with_source: Optional[
            Callable[[bool, int, JamulusRpcMonitorIdentity], None]
        ] = None

        # Primary integration: JSON-RPC (Jamulus 3.9+)
        self.rpc_client = JamulusRpcClient(
            port=rpc_port,
            on_participants_changed_with_source=(
                self._on_rpc_participants_with_source
            ),
            on_levels=self._on_rpc_levels,
            on_chat_with_source=self._on_rpc_chat_with_source,
            on_recorder_state_with_source=(
                self._on_rpc_recorder_state_with_source
            ),
        )

        # The legacy UDP monitor registers itself with the server as another
        # musician.  Keep it dormant in the product: the bundled Jamulus
        # 3.12.2 client exposes the authoritative roster, levels, mixer, chat,
        # and mixer controls through authenticated JSON-RPC. The adapter stays
        # available for its isolated protocol tests and explicit legacy use.
        self.protocol = JamulusProtocolAdapter(
            self.host,
            self.port,
            enabled=False,
            on_participants_changed=self._on_udp_participants,
            on_levels=self._on_udp_levels,
        )

        self.audio_engine = RealAudioEngine(self.settings, logger=self.logger.getChild("audio_engine"))
        # Set by BridgeService only when it has provisioned a native Jamulus
        # CoreAudio route.  The optional PortAudio meter is valuable before a
        # session, but opening the same hardware while Jamulus owns it can
        # create avoidable driver contention.
        self._live_audio_route_owned = False
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # Participant state — delegated to ``ParticipantStateManager``.
    # The next four properties exist so test fixtures that bypass __init__
    # (``JamulusController.__new__(...); controller.participants = {}; ...``)
    # still route through the state manager transparently.
    # ------------------------------------------------------------------
    def _build_state_manager(self) -> ParticipantStateManager:
        manager = ParticipantStateManager(
            apply_mixer_setting=self._apply_mixer_setting,
            set_cached_participants=self._cache_protocol_participants,
            send_rpc_gain=self._send_rpc_gain,
            notify_callbacks=self._notify_callbacks,
            logger=getattr(self, "logger", None),
        )
        self.__dict__["_state"] = manager
        return manager

    @property
    def _state(self) -> ParticipantStateManager:
        existing = self.__dict__.get("_state")
        if existing is None:
            existing = self._build_state_manager()
        return existing

    @property
    def participants(self) -> Dict[int, JamulusParticipant]:
        return self._state.participants

    @participants.setter
    def participants(self, value: Dict[int, JamulusParticipant]) -> None:
        self._state.participants = value

    @property
    def _pre_solo_mute(self) -> Dict[int, bool]:
        return self._state._pre_solo_mute

    @_pre_solo_mute.setter
    def _pre_solo_mute(self, value: Dict[int, bool]) -> None:
        self._state._pre_solo_mute = value

    @property
    def _participants_lock(self) -> "threading.RLock":
        return self._state._participants_lock

    @_participants_lock.setter
    def _participants_lock(self, value: "threading.RLock") -> None:
        self._state._participants_lock = value

    def _lifecycle_guard(self) -> "threading.RLock":
        """Return the lifecycle lock, including for __new__ test fixtures."""

        lock = self.__dict__.get("_lifecycle_lock")
        if lock is None:
            lock = threading.RLock()
            self.__dict__["_lifecycle_lock"] = lock
        return lock

    def _rpc_identity_guard(self) -> "threading.RLock":
        """Return the identity lock, including for __new__ test fixtures."""

        lock = self.__dict__.get("_rpc_identity_lock")
        if lock is None:
            lock = threading.RLock()
            self.__dict__["_rpc_identity_lock"] = lock
        return lock

    def _cache_protocol_participants(self, cached: Dict[int, str]) -> None:
        """State-manager callback — push the latest participant name map
        back into the UDP protocol adapter's cache.  Resilient to fixtures
        whose protocol stub doesn't implement ``set_cached_participants``."""
        protocol = getattr(self, "protocol", None)
        setter = getattr(protocol, "set_cached_participants", None)
        if setter is not None:
            setter(cached)
        
    def set_live_audio_route_owned(self, owned: bool) -> None:
        """Declare whether Jamulus owns the live hardware route this session."""

        self._live_audio_route_owned = bool(owned)

    def start(
        self,
        *,
        process_generation: int | None = None,
        process_id: int | None = None,
    ) -> JamulusRpcMonitorIdentity | None:
        """Start monitoring for one exact native Jamulus process.

        Bridge-owned launches pass a positive generation and PID.  A running
        controller refuses to relabel its existing reader as a different
        process; callers must stop the old monitor first.  The no-argument
        form remains for isolated legacy users and tests, but cannot produce
        process-authenticating evidence.
        """

        generation, pid = JamulusRpcClient._validated_process_identity(
            process_generation,
            process_id,
        )
        with self._lifecycle_guard():
            if getattr(self, "_stopping", False):
                raise RuntimeError("JamulusController is still stopping")
            if self.running:
                current = getattr(self, "_rpc_monitor_identity", None)
                if generation == 0 and pid == 0:
                    return current
                if (
                    current is not None
                    and (
                        current.process_generation,
                        current.process_id,
                    )
                    == (generation, pid)
                ):
                    return current
                raise RuntimeError(
                    "JamulusController already monitors a different process"
                )

            # Participant/mixer truth is the critical path. Some CoreAudio
            # drivers block while Jamulus already owns the interface; start
            # RPC before the optional local meter.
            starting_process = (generation, pid)
            with self._rpc_identity_guard():
                self._rpc_starting_process = starting_process
            if generation == 0 and pid == 0:
                try:
                    identity = self.rpc_client.start()
                except Exception:
                    with self._rpc_identity_guard():
                        self._rpc_starting_process = None
                    raise
            else:
                try:
                    identity = self.rpc_client.start(
                        process_generation=generation,
                        process_id=pid,
                    )
                except Exception:
                    with self._rpc_identity_guard():
                        self._rpc_starting_process = None
                    raise
            with self._rpc_identity_guard():
                self._rpc_monitor_identity = (
                    identity
                    if isinstance(identity, JamulusRpcMonitorIdentity)
                    else None
                )
                self._rpc_starting_process = None
                self.running = True
            self.protocol.start_receiving()
            self.monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
            )
            self.monitor_thread.start()
            if self._live_audio_route_owned:
                self.logger.info(
                    "Skipping the optional local meter: Jamulus owns the live audio route"
                )
            else:
                self.audio_engine.start()
            return self._rpc_monitor_identity

    def stop(self):
        """Stop all monitoring and release per-channel level cache."""
        with self._lifecycle_guard():
            if getattr(self, "_stopping", False):
                return
            self._stopping = True
            with self._rpc_identity_guard():
                self.running = False
                self._rpc_monitor_identity = None
                self._rpc_starting_process = None
        # Do not hold the controller lifecycle lock while RpcClient drains an
        # already-entered participant callback. A callback is allowed to call
        # controller lifecycle methods; `_stopping` makes those calls fail
        # closed without creating lifecycle -> callback -> lifecycle cycles.
        try:
            self.rpc_client.stop()
            self.protocol.stop_receiving()
            if self.monitor_thread:
                self.monitor_thread.join(timeout=2)
                if self.monitor_thread.is_alive():
                    self.logger.warning(
                        "Jamulus monitor thread did not exit within 2s — may leak resources",
                    )
            # Drop any per-channel RPC level overrides — the next session may
            # have completely different channel IDs, and stale entries
            # shouldn't leak between sessions.
            try:
                self.audio_engine.clear_level_overrides()
            except AttributeError:
                pass  # older audio engine without the method
            self.audio_engine.stop()
            # Keep registered UI callbacks across Stop Audio -> Launch Audio.
        finally:
            with self._lifecycle_guard():
                self._stopping = False

    # ------------------------------------------------------------------
    # RPC / UDP participant callbacks (called from background threads)
    # ------------------------------------------------------------------
    def _on_rpc_participants_with_source(
        self,
        channel_infos: list,
        source: JamulusRpcMonitorIdentity,
    ) -> None:
        """Accept participant truth only from this controller's exact epoch."""

        with self._rpc_identity_guard():
            current = getattr(self, "_rpc_monitor_identity", None)
            starting = getattr(self, "_rpc_starting_process", None)
            valid_current = self.running and source == current
            valid_starting = (
                current is None
                and starting is not None
                and source.is_process_bound
                and (
                    source.process_generation,
                    source.process_id,
                )
                == starting
            )
        if not (valid_current or valid_starting):
            return
        self._on_rpc_participants(channel_infos, source=source)

    def _on_rpc_participants(
        self,
        channel_infos: list,
        *,
        source: JamulusRpcMonitorIdentity | None = None,
    ) -> None:
        """Receive participant list from JSON-RPC (authoritative when available)."""
        if not channel_infos:
            with self._participants_lock:
                had_participants = bool(self.participants)
                self.participants.clear()
            if had_participants:
                self._notify_callbacks()
            if source is not None:
                self._notify_identity_callbacks(source)
            return

        normalized: Dict[int, str] = {}
        instrument_map: Dict[int, str] = {}
        skill_map: Dict[int, str] = {}
        is_local_map: Dict[int, bool] = {}
        for info in channel_infos:
            if hasattr(info, "channel_id"):
                cid = info.channel_id
                normalized[cid] = info.name or f"Participant {cid}"
                instrument_map[cid] = getattr(info, "instrument", "") or ""
                skill_map[cid] = getattr(info, "skill_level", "") or ""
                is_local_map[cid] = bool(getattr(info, "is_local", False))
            elif isinstance(info, dict):
                cid = info.get("channel_id", -1)
                if cid >= 0:
                    normalized[cid] = info.get("name") or f"Participant {cid}"
                    instrument_map[cid] = info.get("instrument", "") or ""
                    skill_map[cid] = info.get("skill_level", "") or ""
                    is_local_map[cid] = bool(info.get("is_local", False))
        if normalized:
            self._sync_participants_from_protocol(normalized)
            # Propagate RPC metadata after sync (sync only handles names).
            changed = False
            with self._participants_lock:
                for cid, instrument in instrument_map.items():
                    if cid in self.participants:
                        self.participants[cid].instrument = instrument
                        changed = True
                for cid, skill in skill_map.items():
                    if cid in self.participants:
                        self.participants[cid].skill_level = skill
                for cid, is_local in is_local_map.items():
                    if cid in self.participants:
                        self.participants[cid].is_local = is_local
                        changed = True
            # sync_from_protocol already fired a callback with names only, so
            # the metadata (instrument/skill, and crucially which row is
            # "you") would otherwise not reach the UI until the *next* push.
            # Notify again now that it's attached.
            if changed:
                self._notify_callbacks()
            if source is not None:
                # Publish one fully-normalized snapshot with the immutable
                # process identity.  The StateManager's compatibility
                # callback above intentionally remains one-argument only.
                self._notify_identity_callbacks(source)

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
        """Merge incoming participant map into internal state, then notify
        callbacks.  Backed by ``ParticipantStateManager.sync_from_protocol``."""
        self._state.sync_from_protocol(incoming)
    
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
        """UDP-fallback participant poll.  When JSON-RPC is available it's
        authoritative and this is a no-op — running both would erase
        participants between RPC cycles and silently drop fader changes."""
        if self.rpc_client.available:
            return

        # A hosted server has its own authenticated roster API.  Prefer that
        # truth over the best-effort UDP parser while the Jamulus client's RPC
        # is still starting (or if CoreAudio temporarily stalls its Qt event
        # loop).  The audio connection itself can already be live in that
        # situation, and leaving WebJam on a permanent "Connecting" overlay
        # would hide a healthy, recordable session from the host.
        hosted_participants = self._hosted_server_participants()
        if hosted_participants is not None:
            self._on_rpc_participants(hosted_participants)
            self.last_error = ""
            return

        protocol_participants = self.protocol.request_clients()
        if protocol_participants is None:
            return
        if not isinstance(protocol_participants, dict):
            self.logger.warning(
                "Ignoring unexpected participants payload type: %s",
                type(protocol_participants).__name__,
            )
            return

        normalized: Dict[int, str] = {}
        for raw_channel_id, raw_name in protocol_participants.items():
            try:
                channel_id = int(raw_channel_id)
            except (TypeError, ValueError):
                continue
            if channel_id < 0:
                continue
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            normalized[channel_id] = name or f"Participant {channel_id}"

        self._state.apply_udp_clients_payload(normalized)
        self.last_error = ""

    def _hosted_server_participants(self) -> Optional[list[dict]]:
        """Return the local hosted server roster, or ``None`` if unavailable.

        An empty list is a successful poll with no connected musicians.  This
        fallback is deliberately limited to hosted mode: a remote-server RPC
        tunnel may be configured only for recording and must not silently
        replace the local client's authoritative mixer-channel metadata.
        """
        settings = getattr(self, "settings", None)
        if not getattr(settings, "host_server_enabled", False):
            return None
        secret_file = str(
            getattr(settings, "server_rpc_secret_file", "") or ""
        ).strip()
        if not secret_file:
            return None

        try:
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

            secret = read_secret_file(secret_file)
            with JamulusServerRpc(
                port=int(getattr(settings, "server_rpc_port", 22240)),
                secret=secret,
            ) as rpc:
                payload = rpc.get_clients()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Hosted server roster unavailable: %s", exc)
            return None

        clients = payload.get("clients", [])
        if not isinstance(clients, list):
            return None

        participants: list[dict] = []
        local_name = str(getattr(settings, "musician_name", "") or "").strip()
        for client in clients:
            if not isinstance(client, dict):
                continue
            try:
                channel_id = int(client.get("id"))
            except (TypeError, ValueError):
                continue
            if channel_id < 0:
                continue
            address = str(client.get("address", "") or "")
            is_local = address.startswith("127.0.0.1:") or address.startswith("[::1]:")
            name = str(client.get("name", "") or "").strip()
            if not name and is_local:
                name = local_name
            participants.append({
                "channel_id": channel_id,
                "name": name or f"Participant {channel_id}",
                "is_local": is_local,
            })
        return participants
    
    def add_participant(self, name: str, channel_id: int = None) -> JamulusParticipant:
        """Manually add a participant (for testing or manual setup)."""
        return self._state.add_participant(name, channel_id)

    def remove_participant(self, channel_id: int):
        """Remove a participant."""
        self._state.remove_participant(channel_id)
    
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
        """Set the local mix fader for a Jamulus channel.

        ``level`` is 0..127 (Jamulus convention; 100 = unity, 127 = ~+6 dB);
        out-of-range values are clamped.  No-op if the channel isn't in the
        participant list.  Propagates to Jamulus via JSON-RPC (when
        available) and via UDP ``apply_mixer`` as a fallback.
        """
        self._state.set_fader_level(channel_id, level)

    def set_pan(self, channel_id: int, pan: int):
        """Set pan position (0=left, 50=center, 100=right)."""
        self._state.set_pan(channel_id, pan)

    def set_mute(self, channel_id: int, muted: bool):
        """Mute/unmute a Jamulus channel.  Solo interaction: if any channel
        is currently soloed, muting a non-soloed channel records the
        requested state in the pre-solo snapshot for later restoration but
        the channel stays audibly muted by the solo invariant."""
        self._state.set_mute(channel_id, muted)

    def set_solo(self, channel_id: int, solo: bool):
        """Solo/unsolo a channel, preserving prior mute state.  Exclusive
        solo: switching keeps the snapshot; leaving solo restores it."""
        self._state.set_solo(channel_id, solo)

    def set_self_muted(self, muted: bool) -> bool:
        """Fail closed: Jamulus 3.12.2 cannot mute its live network send.

        Retained temporarily as a compatibility seam for non-UI callers. It
        deliberately never delegates to RPC. Musicians must use their audio
        interface mute or stop WebJam audio.
        """
        del muted
        return False

    def send_chat(self, text: str) -> bool:
        """Send a chat message to the band via Jamulus (jamulusclient/sendChatText).

        Returns True if sent; no-op/False if RPC isn't available."""
        if not text or not self.rpc_client.available:
            return False
        try:
            return bool(self.rpc_client.send_chat_text(text))
        except Exception:
            return False

    def set_name(self, name: str) -> bool:
        """Push the local musician's display name to Jamulus so the band sees a
        real name instead of a blank. No-op/False if RPC isn't available."""
        if not self.rpc_client.available:
            return False
        try:
            validated = validate_jamulus_name(name)
        except JamulusNameError:
            return False
        try:
            return bool(self.rpc_client.set_name(validated.value))
        except Exception:
            return False

    def _on_rpc_recorder_state(self, recording: bool, raw_state: int) -> None:
        """Server recorder state changed — forward to the UI hook if set."""
        cb = self.recorder_state_callback
        if cb is None:
            return
        try:
            cb(recording, raw_state)
        except Exception:
            pass

    def _rpc_event_source_is_current(
        self,
        source: JamulusRpcMonitorIdentity,
    ) -> bool:
        """Accept an event only from this controller's bound monitor epoch."""

        if not isinstance(source, JamulusRpcMonitorIdentity):
            return False
        if not source.is_process_bound or source.monitor_epoch <= 0:
            return False
        with self._rpc_identity_guard():
            current = getattr(self, "_rpc_monitor_identity", None)
            starting = getattr(self, "_rpc_starting_process", None)
            return bool(
                (self.running and source == current)
                or (
                    current is None
                    and starting is not None
                    and (
                        source.process_generation,
                        source.process_id,
                    )
                    == starting
                )
            )

    def _on_rpc_recorder_state_with_source(
        self,
        recording: bool,
        raw_state: int,
        source: JamulusRpcMonitorIdentity,
    ) -> None:
        """Forward recorder truth with exact native-process provenance."""

        if not self._rpc_event_source_is_current(source):
            return
        cb = self.recorder_state_callback_with_source
        if cb is None:
            self._on_rpc_recorder_state(recording, raw_state)
            return
        try:
            cb(recording, raw_state, source)
        except Exception:
            pass

    def _on_rpc_chat(self, text: str) -> None:
        """Incoming band chat from Jamulus — forward to the UI hook if set."""
        cb = self.chat_callback
        if cb is None:
            return
        try:
            cb(text)
        except Exception:
            pass

    def _on_rpc_chat_with_source(
        self,
        text: str,
        source: JamulusRpcMonitorIdentity,
    ) -> None:
        """Forward chat with exact native-process provenance."""

        if not self._rpc_event_source_is_current(source):
            return
        cb = self.chat_callback_with_source
        if cb is None:
            self._on_rpc_chat(text)
            return
        try:
            cb(text, source)
        except Exception:
            pass
    
    def _apply_mixer_setting(self, channel_id: int, notify: bool = True):
        """Apply one monitor-mix setting without inventing meter activity.

        Level overrides are reserved for actual RPC/UDP level telemetry.  A
        fader position is gain, not sound; feeding it into the meter would let
        a silent fader move promote the session to ``Ready to play``.
        """
        with self._participants_lock:
            participant = self.participants.get(channel_id)
            if not participant:
                return
            fader_level = participant.fader_level
            pan = participant.pan
            muted = participant.muted

        self.protocol.apply_mixer(
            channel_id=channel_id,
            fader_level=fader_level,
            pan=pan,
            muted=muted,
        )
        if notify:
            self._notify_callbacks()
    
    def get_participants(self) -> List[JamulusParticipant]:
        """Get list of all participants."""
        return self._state.get_participants()

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
        """Register a legacy one-argument participant callback."""
        with self._lock:
            self.callbacks.append(callback)

    def unregister_callback(self, callback: Callable) -> None:
        """Remove a legacy callback. Silent on missing."""
        with self._lock:
            try:
                self.callbacks.remove(callback)
            except ValueError:
                pass

    def register_identity_callback(self, callback: Callable) -> None:
        """Register ``callback(participants, source_identity)``.

        Identity-aware callbacks fire only for authenticated RPC participant
        events from the exact native process supplied to :meth:`start`.  UDP,
        hosted-server fallback, manual test participants, and unbound legacy
        starts never produce this process-authenticating signal.
        """

        with self._lock:
            callbacks = self.__dict__.setdefault("identity_callbacks", [])
            callbacks.append(callback)

    def unregister_identity_callback(self, callback: Callable) -> None:
        """Remove a process-identity callback. Silent on missing."""

        with self._lock:
            try:
                self.__dict__.setdefault("identity_callbacks", []).remove(
                    callback
                )
            except ValueError:
                pass
    
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

    def _notify_identity_callbacks(
        self,
        source: JamulusRpcMonitorIdentity,
    ) -> None:
        """Publish a detached participant snapshot with exact provenance."""

        with self._rpc_identity_guard():
            current = getattr(self, "_rpc_monitor_identity", None)
            starting = getattr(self, "_rpc_starting_process", None)
            valid_current = self.running and source == current
            valid_starting = (
                current is None
                and starting is not None
                and source.is_process_bound
                and (
                    source.process_generation,
                    source.process_id,
                )
                == starting
            )
        if not source.is_process_bound or not (valid_current or valid_starting):
            return
        # Do not hold a controller lifecycle/identity lock while invoking
        # consumers. JamulusRpcClient's callback gate already makes stop()
        # wait for this call, and avoiding the inverse
        # controller-lifecycle -> client-callback lock order prevents a
        # participant callback racing Stop Audio from deadlocking.
        with self._lock:
            callbacks = list(getattr(self, "identity_callbacks", []))
        with self._participants_lock:
            # JamulusParticipant is mutable mixer state.  Detach every row
            # so a queued UI delivery cannot observe a later process's
            # changes under an older source identity.
            participants = [
                replace(participant)
                for participant in self.participants.values()
            ]
        for callback in callbacks:
            try:
                callback(participants, source)
            except Exception as exc:
                self.logger.warning("Identity callback error: %s", exc)

    def rpc_monitor_snapshot(self) -> JamulusRpcMonitorSnapshot:
        """Return the RPC client's immutable current-epoch observation."""

        return self.rpc_client.monitor_snapshot()

    def rpc_monitor_snapshot_for(
        self,
        *,
        process_generation: int,
        process_id: int,
    ) -> JamulusRpcMonitorSnapshot | None:
        """Return RPC evidence only for the requested exact native process."""

        return self.rpc_client.monitor_snapshot_for(
            process_generation=process_generation,
            process_id=process_id,
        )

    def ordered_roster_proof_for(
        self,
        source: JamulusRpcMonitorIdentity,
    ) -> JamulusOrderedRosterProof | None:
        """Return exact ordered-roster evidence for a current RPC callback."""

        if not self._rpc_event_source_is_current(source):
            return None
        return self.rpc_client.ordered_roster_proof_for(source)

    def request_ordered_roster_refresh(
        self,
        source: JamulusRpcMonitorIdentity,
    ) -> bool:
        """Request a bounded fresh roster for the current native process."""

        if not self._rpc_event_source_is_current(source):
            return False
        return self.rpc_client.request_ordered_roster_refresh(source)

    @staticmethod
    def _normalize_participant_name(name: object) -> str:
        # Kept for any external callers that imported it from the controller.
        return ParticipantStateManager._normalize_participant_name(name)

    def serialize_mix(self) -> dict:
        """Return the current mix payload in the same format used for file saves."""
        return self._state.serialize_mix()

    def apply_mix_data(self, mix_data: object) -> Optional[int]:
        """Apply a previously serialized mix payload to current participants."""
        return self._state.apply_mix_data(mix_data)

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

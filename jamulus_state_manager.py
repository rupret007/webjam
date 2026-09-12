"""Participant-state manager — owns the in-memory mixer state for a Jamulus
session (participant dict, pre-solo mute snapshot, solo invariants) and the
lock protecting it against concurrent RPC + UDP updates.

Extracted from ``JamulusController`` (Round 4, after MixManager + Session-
Persistence).  Transport concerns (RPC ``setChannelGain``, UDP
``apply_mixer``, ``set_cached_participants``, listener notification) are
passed in as callbacks; the manager itself has no network dependency.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional


@dataclass
class JamulusParticipant:
    """Represents a participant in the Jamulus session."""
    channel_id: int
    name: str
    ip_address: str = ""
    is_connected: bool = True
    fader_level: int = 100  # 0-127 (Jamulus mixer range; 100 = 0 dB, 127 = +6 dB)
    pan: int = 50  # 0=left, 50=center, 100=right
    muted: bool = False
    solo: bool = False
    instrument: str = ""    # as reported by Jamulus JSON-RPC
    skill_level: str = ""   # beginner / intermediate / expert ("" if unset)
    is_local: bool = False  # True for the local Jamulus client (from RPC getClientInfo)


class ParticipantStateManager:
    """Owns the participant dict, pre-solo snapshot, and solo invariants.
    Mutates state under ``_participants_lock`` and delegates side effects
    (mixer apply, RPC gain, cached participants, listener notifications)
    to callbacks supplied by the controller."""

    def __init__(
        self,
        apply_mixer_setting: Callable[..., None],
        set_cached_participants: Callable[[Dict[int, str]], None],
        send_rpc_gain: Callable[..., None],
        notify_callbacks: Callable[[], None],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.participants: Dict[int, JamulusParticipant] = {}
        self._pre_solo_mute: Dict[int, bool] = {}
        self._participants_lock = threading.RLock()
        self._apply_mixer_setting = apply_mixer_setting
        self._set_cached_participants = set_cached_participants
        self._send_rpc_gain = send_rpc_gain
        self._notify_callbacks = notify_callbacks
        # The fallback stays inside the ``webjam`` namespace so participant
        # names never reach an unredacted root-logger handler.
        self._logger = logger or logging.getLogger("webjam.jamulus_state")

    def get_participants(self) -> List[JamulusParticipant]:
        with self._participants_lock:
            return list(self.participants.values())

    def _apply_listening_gain(
        self, channel_id: int, *, notify: bool = True,
        expected_participant: JamulusParticipant | None = None,
    ) -> None:
        """Apply the effective monitor gain while retaining the chosen fader.

        Native monitor mute is gain zero, including Solo suppression. Roster
        changes must use the same native path as explicit listening gestures;
        the dormant UDP adapter cannot restore the native mix by itself.
        """
        with self._participants_lock:
            participant = self.participants.get(channel_id)
            if participant is None or (
                expected_participant is not None and participant is not expected_participant
            ):
                return
            effective_level = 0 if participant.muted else participant.fader_level
        if expected_participant is None:
            self._send_rpc_gain(channel_id, effective_level)
        else:
            self._send_rpc_gain(
                channel_id, effective_level, expected_participant=expected_participant,
            )
        # The native queue checks ownership again at enqueue and dispatch.
        # Keep the legacy adapter from following an already-replaced row too.
        with self._participants_lock:
            if self.participants.get(channel_id) is not participant:
                return
        if notify:
            self._apply_mixer_setting(channel_id)
        else:
            self._apply_mixer_setting(channel_id, notify=False)

    # -- Mutating ops -------------------------------------------------------
    def add_participant(
        self, name: str, channel_id: Optional[int] = None
    ) -> JamulusParticipant:
        should_apply = False
        with self._participants_lock:
            if channel_id is None:
                channel_id = max(self.participants.keys(), default=-1) + 1
            participant = JamulusParticipant(channel_id=channel_id, name=name)
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
        self._set_cached_participants(cached)
        if should_apply:
            self._apply_listening_gain(channel_id, notify=False)
        self._notify_callbacks()
        return participant

    def remove_participant(self, channel_id: int) -> None:
        should_notify = False
        cached: Optional[Dict[int, str]] = None
        apply_mixer_ids: List[int] = []
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
                elif not any(p.solo for p in self.participants.values()):
                    self._pre_solo_mute.clear()
                cached = {cid: p.name for cid, p in self.participants.items()}
                should_notify = True
        if cached is not None:
            self._set_cached_participants(cached)
        for cid in sorted(set(apply_mixer_ids)):
            self._apply_listening_gain(cid, notify=False)
        if should_notify:
            self._notify_callbacks()

    def set_fader_level(self, channel_id: int, level: int) -> None:
        clamped = max(0, min(127, int(level)))
        with self._participants_lock:
            if channel_id in self.participants:
                self.participants[channel_id].fader_level = clamped
            else:
                return
        self._apply_listening_gain(channel_id)

    def set_pan(self, channel_id: int, pan: int) -> None:
        with self._participants_lock:
            if channel_id in self.participants:
                self.participants[channel_id].pan = max(0, min(100, pan))
            else:
                return
        self._apply_mixer_setting(channel_id)

    def set_mute(self, channel_id: int, muted: bool) -> None:
        with self._participants_lock:
            if channel_id not in self.participants:
                return
            active_solo_channel = next(
                (cid for cid, p in self.participants.items() if p.solo),
                None,
            )
            if active_solo_channel is None:
                self.participants[channel_id].muted = muted
            else:
                if not self._pre_solo_mute:
                    self._pre_solo_mute = {
                        cid: p.muted for cid, p in self.participants.items()
                    }
                self._pre_solo_mute[channel_id] = muted
                if channel_id == active_solo_channel:
                    self.participants[channel_id].muted = muted
                else:
                    # Preserve the requested post-solo mute without breaking
                    # exclusive solo monitoring in the current mix.
                    self.participants[channel_id].muted = True
        self._apply_listening_gain(channel_id)

    def set_solo(self, channel_id: int, solo: bool) -> None:
        """Solo/unsolo a channel, preserving prior mute state.  Exclusive
        solo: switching keeps the snapshot; leaving solo restores it."""
        affected_ids: List[int] = []
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
            self._apply_listening_gain(cid)

    # -- Sync paths (RPC + UDP) -------------------------------------------
    def _merge_protocol_payload(
        self, incoming: Dict[int, str], clear_stale_snapshot: bool
    ) -> List[int]:
        """Shared merge for RPC + UDP sync.  Mutates under the lock,
        returns channel ids whose mixer needs re-applying.  When
        ``clear_stale_snapshot`` is True (UDP fallback), wipes the pre-solo
        snapshot whenever no solo is active."""
        apply_ids: List[int] = []
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
            elif clear_stale_snapshot and not any(
                p.solo for p in self.participants.values()
            ):
                self._pre_solo_mute.clear()
        return apply_ids

    def sync_from_protocol(self, incoming: Dict[int, str]) -> None:
        """RPC-driven sync.  Late joiners during solo stay muted; if the
        soloed channel disappears, the pre-solo snapshot is restored."""
        apply_ids = self._merge_protocol_payload(incoming, clear_stale_snapshot=False)
        for cid in sorted(set(apply_ids)):
            self._apply_listening_gain(cid, notify=False)
        self._notify_callbacks()

    def apply_udp_clients_payload(self, normalized: Dict[int, str]) -> None:
        """UDP-fallback sync.  Same invariants as ``sync_from_protocol``
        plus a defensive "no solo => clear snapshot" sweep that mirrors
        the original ``_check_participants``."""
        apply_ids = self._merge_protocol_payload(normalized, clear_stale_snapshot=True)
        for cid in sorted(set(apply_ids)):
            self._apply_listening_gain(cid, notify=False)
        self._notify_callbacks()

    # -- Mix snapshot (save / load) ---------------------------------------
    @staticmethod
    def _normalize_participant_name(name: object) -> str:
        if name is None:
            return ""
        return str(name).strip().casefold()

    def serialize_mix(self) -> dict:
        # Copy values while locked: the file must describe one complete mix,
        # not references that a roster or listening gesture can mutate later.
        with self._participants_lock:
            has_solo = any(p.solo for p in self.participants.values())
            return {
                "participants": [
                    {
                        "channel_id": p.channel_id,
                        "name": p.name,
                        "fader_level": p.fader_level,
                        "pan": p.pan,
                        "muted": p.muted,
                        "solo": p.solo,
                        **({"pre_solo_muted": self._pre_solo_mute.get(cid, p.muted)}
                           if has_solo else {}),
                    }
                    for cid, p in self.participants.items()
                ]
            }

    def apply_mix_data(self, mix_data: object) -> Optional[int]:
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

        def _coerce_int(value: object, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return default

        participants_data = mix_data.get("participants") if isinstance(mix_data, dict) else None
        if not isinstance(participants_data, list):
            self._logger.warning("Mix payload is missing a valid participants list.")
            return None

        # Resolve names and stage all rows against one current roster. No
        # callback can observe a partially restored Solo/mute mix.
        with self._participants_lock:
            name_by_id = {
                cid: self._normalize_participant_name(p.name)
                for cid, p in self.participants.items()
            }
            ids_by_name: Dict[str, List[int]] = {}
            for cid, name in name_by_id.items():
                if name:
                    ids_by_name.setdefault(name, []).append(cid)
            current_solo = next(
                (cid for cid, p in self.participants.items() if p.solo), None,
            )
            personal_mutes = {
                cid: self._pre_solo_mute.get(cid, p.muted)
                if current_solo is not None else p.muted
                for cid, p in self.participants.items()
            }
            staged: Dict[int, JamulusParticipant] = {}
            for row in participants_data:
                if not isinstance(row, dict):
                    continue
                try:
                    payload_cid = int(row.get("channel_id"))
                except (TypeError, ValueError, OverflowError):
                    payload_cid = None
                payload_name = self._normalize_participant_name(row.get("name"))
                cid = None
                if payload_cid is not None and payload_cid in name_by_id:
                    if not payload_name or name_by_id[payload_cid] == payload_name:
                        cid = payload_cid
                if cid is None and payload_name:
                    matches = ids_by_name.get(payload_name, [])
                    if len(matches) == 1:
                        cid = matches[0]
                if cid is None:
                    continue

                # Reinsert duplicate rows so the last final Solo candidate
                # wins, and a later solo=false cannot reactivate an old one.
                candidate = staged.pop(cid, None) or replace(self.participants[cid])
                candidate.fader_level = max(0, min(127, _coerce_int(
                    row.get("fader_level", candidate.fader_level), candidate.fader_level,
                )))
                candidate.pan = max(0, min(100, _coerce_int(
                    row.get("pan", candidate.pan), candidate.pan,
                )))
                was_solo = candidate.solo
                candidate.solo = _coerce_bool(row.get("solo", candidate.solo), candidate.solo)
                if "muted" in row:
                    candidate.muted = _coerce_bool(row["muted"], candidate.muted)
                    personal_mutes[cid] = _coerce_bool(row["muted"], personal_mutes[cid])
                elif candidate.solo and not was_solo:
                    candidate.muted = False
                if "pre_solo_muted" in row:
                    personal_mutes[cid] = _coerce_bool(
                        row["pre_solo_muted"], personal_mutes[cid],
                    )
                staged[cid] = candidate

            if not staged:
                if participants_data:
                    self._logger.warning("Mix payload did not match any current participants.")
                return 0

            solo_channel = next(
                (cid for cid in reversed(staged) if staged[cid].solo), None,
            )
            if solo_channel is None and current_solo not in staged:
                solo_channel = current_solo
            selected = staged.get(solo_channel) or self.participants.get(solo_channel)
            solo_muted = selected.muted if selected is not None else False
            affected = {}
            for cid, participant in self.participants.items():
                before = (participant.muted, participant.solo)
                candidate = staged.get(cid)
                if candidate is not None:
                    participant.fader_level = candidate.fader_level
                    participant.pan = candidate.pan
                participant.solo = cid == solo_channel
                participant.muted = (
                    personal_mutes[cid] if solo_channel is None
                    else solo_muted if participant.solo else True
                )
                if candidate is not None or before != (participant.muted, participant.solo):
                    affected[cid] = participant
            self._pre_solo_mute = personal_mutes if solo_channel is not None else {}

        # Queue current effective gains with the exact restored row owner.
        # Native I/O remains outside the state lock; #105's dispatcher also
        # guards its originating RPC epoch and reads the latest local intent.
        for cid, participant in affected.items():
            self._apply_listening_gain(cid, expected_participant=participant)
        return len(staged)

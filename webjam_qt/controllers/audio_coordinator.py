"""Audio session coordinator — Launch/Stop Audio, practice mode, participants.

Extracted from ApplicationController as the first step toward splitting the
1,500+ line controller into focused coordinators (audio / video / recording /
session / settings / API).
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from webjam_qt.widgets.participant_card import ParticipantPresentation

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.audio_coordinator")


class AudioCoordinator:
    """Owns Jamulus audio-session UI state and participant grid transitions."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller
        self.connected = False
        self.stopping = False

    def on_launch_toggle(self) -> None:
        if self._c._is_jamulus_running():
            self.stop()
        else:
            self._c._reconnect_banner_shown = False
            self._c._rpc_hang_banner_shown = False
            self._c._reconnect_gave_up = False
            self._c.window.set_status_audio("Launching…")
            self._c.window.session_strip.set_audio_state("Launching…", enabled=False)
            self._c.bridge.launch_jamulus(manual=True)

    def on_practice_requested(self) -> None:
        if self._c._is_jamulus_running():
            self._c.window.flash_message(
                "Stop Audio first, then press Practice for a solo session.",
                ms=4000,
            )
            return
        self._c.window.set_status_audio("Starting practice…")
        self._c.window.session_strip.set_audio_state("Starting…", enabled=False)
        self._c.window.flash_message(
            "Practice mode: private server on this computer — play and hear "
            "yourself, no internet needed. Stop Audio ends it.",
            ms=6000,
        )
        started = self._c.bridge.launch_practice_session()
        if not started:
            self._c.window.session_strip.set_audio_state("Launch Audio", enabled=True)
            self._c.window.set_status_audio("Ready to launch")

    def stop(self) -> None:
        reply = QMessageBox.question(
            self._c.window, "Stop Audio?",
            "Stop the Jamulus audio session?\n\nYou can restart it any time with the audio button.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.stopping = True
        self._c.window.set_status_audio("Stopping…")
        self._c.window.set_status_latency("Not connected")
        self._c.window.session_strip.set_audio_state("Stopping…", enabled=False)
        threading.Thread(
            target=self._c.bridge.stop_jamulus, daemon=True, name="jamulus-stop",
        ).start()
        self.connected = False
        self._c._level_timer.stop()
        self._c._apply_recorder_state(False)
        self._c._recorder_armed = False
        self._c.window.session_strip.set_recording_state(False, enabled=True)
        self.reset_to_demo()
        self._c._reconnect_banner_shown = False
        self._c._rpc_hang_banner_shown = False
        self._c._reconnect_gave_up = False

    def reset_to_demo(self) -> None:
        from webjam_qt.controllers.application_controller import _DEMO_PARTICIPANTS

        self._c.session_health.reset_live_truth()
        self._c.participants.clear()
        for p in _DEMO_PARTICIPANTS:
            self._c.participants[p.channel_id] = ParticipantPresentation(
                channel_id=p.channel_id,
                name=p.name,
                role=p.role,
                fader_level=p.fader_level,
                is_local=p.is_local,
            )
        self._c._push_participants_to_grid()
        self._c._demo_timer.start()

    def apply_participants(self, jamulus_participants: list) -> None:
        if self.stopping:
            return
        if not jamulus_participants:
            if self.connected:
                self.connected = False
                self._c._level_timer.stop()
                self._c.window.set_status_latency("Not connected")
                self._c.window.set_status_audio("Connecting…")
                self.reset_to_demo()
            return
        rpc = getattr(self._c.jamulus, "rpc_client", None)
        self._c.session_health.mark_process(
            self._c.bridge.jamulus_state,
            rpc_available=bool(getattr(rpc, "available", False)),
        )
        self._c.session_health.mark_participants(len(jamulus_participants))

        if not self.connected:
            self.connected = True
            self._c.session_health.mark_rpc_result("participants", True)
            self._c.jamulus.set_name(self._c.settings.webex_display_name)
            self._c._demo_timer.stop()
            self._c.participants.clear()
            self._c._level_timer.start()
            self._c._restore_saved_mix()
            try:
                self._c.metrics.increment("metric_session_started")
            except Exception:  # noqa: BLE001
                LOGGER.debug("metric_session_started increment failed", exc_info=True)
            if self._c.bridge.practice_mode:
                self._c.window.set_status_audio(
                    f"Practice connected ({self._c.bridge.effective_server()})"
                )
                self._c.window.flash_message(
                    "Practice session live — you're on a private local server. "
                    "Play something and watch your meter.",
                    ms=5000,
                )
            else:
                self._c.window.set_status_audio(
                    f"Connected ({self._c.bridge.effective_server()})"
                )
                self._c.window.flash_message(
                    f"Connected to {self._c.settings.jamulus_server}. "
                    "Waiting for band members…",
                    ms=4000,
                )

        n = len(jamulus_participants)
        if n == 1:
            self._c.window.set_status_latency("1 participant · waiting for others")
        else:
            self._c.window.set_status_latency(f"{n} participants")

        incoming_ids = {p.channel_id for p in jamulus_participants}
        for cid in list(self._c.participants.keys()):
            if cid not in incoming_ids:
                del self._c.participants[cid]

        for jp in jamulus_participants:
            existing = self._c.participants.get(jp.channel_id)
            if existing is None:
                self._c.participants[jp.channel_id] = ParticipantPresentation(
                    channel_id=jp.channel_id,
                    name=jp.name,
                    role=self._c._role_label(jp),
                    fader_level=jp.fader_level,
                    muted=jp.muted,
                    solo=jp.solo,
                    is_connected=jp.is_connected,
                    is_local=getattr(jp, "is_local", jp.channel_id == 0),
                )
            else:
                existing.name = jp.name
                existing.is_connected = jp.is_connected
                existing.is_local = getattr(jp, "is_local", jp.channel_id == 0)
                new_role = self._c._role_label(jp)
                if new_role != existing.role:
                    existing.role = new_role

        self._c._push_participants_to_grid()

    def on_readiness_refresh(self, jamulus_up: bool) -> None:
        if not jamulus_up:
            self.stopping = False

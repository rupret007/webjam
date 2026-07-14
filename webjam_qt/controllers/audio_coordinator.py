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
from webjam_qt.session_state import SessionUiState
from core.session_lifecycle import SessionLifecyclePhase

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.audio_coordinator")


class AudioCoordinator:
    """Owns Jamulus audio-session UI state and participant grid transitions."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller
        self.connected = False
        self.stopping = False
        self.ended_by_user = False
        self.connection_timed_out = False
        self.recovering = False
        self.permission_explained = False

    def on_launch_toggle(self) -> bool:
        """Apply the live toggle and report whether a new launch was allowed.

        ``False`` includes a stop action and either microphone-permission
        recovery state.  Callers can therefore defer any capture-capable
        companion until the production audio attempt has passed this preflight.
        """
        if self._c._is_jamulus_running():
            self.stop()
            return False
        else:
            from webjam_qt.platform_permissions import microphone_permission_status

            permission = microphone_permission_status()
            if permission == "not_determined" and not self.permission_explained:
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.CHECKING_PERMISSIONS,
                    "Waiting for microphone permission",
                )
                self.permission_explained = True
                self._c.window.participant_grid.set_session_state(
                    SessionUiState.permission_required()
                )
                self._c.window.session_hud.set_state(
                    "Microphone access is needed",
                    "Your band needs to hear your instrument. Choose Continue to use the macOS prompt.",
                )
                self._c.window.session_strip.set_tools_enabled(True)
                return False
            if permission in {"denied", "restricted"}:
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.FAILED_RECOVERABLE,
                    "Microphone permission needs attention",
                )
                self._c.window.participant_grid.set_session_state(
                    SessionUiState.permission_denied()
                )
                self._c.window.session_hud.set_state(
                    "Microphone access is off",
                    "Open System Settings below, allow access, then return to WebJam.",
                )
                self._c.window.session_strip.set_tools_enabled(True)
                return False
            self.ended_by_user = False
            self.connection_timed_out = False
            self.recovering = False
            self._c._local_audio_seen = False
            self._c._remote_audio_seen = False
            self._c._reconnect_banner_shown = False
            self._c._rpc_hang_banner_shown = False
            self._c._reconnect_gave_up = False
            self._c.window.set_status_audio("Launching…")
            self._c.window.session_strip.set_tools_enabled(True)
            self._c.window.session_strip.set_audio_state("Launching…", enabled=False)
            self._c.window.participant_grid.set_session_state(
                SessionUiState.connecting(self._c.bridge.effective_server())
            )
            self._c._transition_lifecycle(
                (
                    SessionLifecyclePhase.STARTING_HOST
                    if bool(getattr(self._c.settings, "host_server_enabled", False))
                    else SessionLifecyclePhase.JOINING
                ),
                "Starting the music engine",
            )
            if bool(getattr(self._c.settings, "host_server_enabled", False)):
                self._c.window.session_hud.set_state(
                    "Starting your jam…",
                    "WebJam is getting the band audio ready.",
                )
            else:
                self._c.window.session_hud.set_state(
                    "Joining your jam…",
                    "WebJam is connecting you to the band.",
                )
            accepted = bool(self._c.bridge.launch_jamulus(manual=True))
            if not accepted:
                return False
            if bool(getattr(self._c.settings, "host_server_enabled", False)):
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
                    "Waiting for the hosted server and private LAN invitation",
                )
            self._c._connection_timer.start()
            return True

    def on_practice_requested(self) -> None:
        if self._c._is_jamulus_running():
            self._c.window.flash_message(
                "End the current session first, then start a solo practice.",
                ms=4000,
            )
            return
        self._c.window.set_status_audio("Starting practice…")
        self._c._transition_lifecycle(
            SessionLifecyclePhase.STARTING_HOST,
            "Starting private practice",
            role="practice",
        )
        self._c.window.session_strip.set_audio_state("Starting…", enabled=False)
        self._c.window.participant_grid.set_session_state(SessionUiState.practice())
        self._c.window.flash_message(
            "Practice mode: private server on this computer — play and hear "
            "yourself, no internet needed. End Session stops it.",
            ms=6000,
        )
        started = self._c.bridge.launch_practice_session()
        if not started:
            self._c.window.session_strip.set_audio_state("Start Session", enabled=True)
            self._c.window.set_status_audio("Ready to launch")
            self.reset_to_idle()

    def stop(self) -> None:
        hosting = bool(getattr(self._c.settings, "host_server_enabled", False))
        recording_active = self._c.recording.is_recording_active
        take_in_progress = bool(
            getattr(self._c.recording, "take_in_progress", recording_active)
        )
        if hosting and take_in_progress:
            QMessageBox.information(
                self._c.window,
                "Finish the take first",
                (
                    "Press Stop Rec, then wait for ‘Take saved’ before ending the jam. "
                    if recording_active
                    else "Wait for ‘Take saved’ before ending the jam. "
                )
                + "This keeps every musician's track complete and verified.",
            )
            return
        question = (
            "Leave this jam?\n\n"
            "The host and other musicians will stay connected."
        )
        if hosting:
            question = (
                "End this jam for everyone?\n\n"
                "WebJam will safely finish any recording and stop the hosted session."
            )
        if recording_active:
            question = (
                "Leave this jam?\n\nThe host's recording will keep running. "
                "Only this Mac will disconnect."
            )
        reply = QMessageBox.question(
            self._c.window, "End Jam?" if hosting else "Leave Jam?",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.stopping = True
        self.ended_by_user = True
        self.recovering = False
        self._c.window.set_status_audio("Ending…" if hosting else "Leaving…")
        self._c.window.set_status_latency("Not connected")
        self._c.window.session_strip.set_audio_state(
            "Ending…" if hosting else "Leaving…", enabled=False
        )
        self._c.participants.clear()
        self._c._push_participants_to_grid()
        self._c.window.participant_grid.set_session_state(
            SessionUiState.ending(hosting=hosting)
        )
        self._c._transition_lifecycle(
            SessionLifecyclePhase.ENDING,
            "Ending the hosted jam" if hosting else "Leaving the jam",
        )
        self._c.window.session_hud.set_state(
            "Ending this jam…" if hosting else "Leaving the jam…",
            (
                "WebJam is safely finishing recordings and disconnecting everyone."
                if hosting
                else "WebJam is disconnecting your audio safely."
            ),
        )
        threading.Thread(
            target=self._stop_session_services,
            args=(hosting,),
            daemon=True,
            name="webjam-session-stop",
        ).start()
        self.connected = False
        self._c._local_audio_seen = False
        self._c._remote_audio_seen = False
        self._c._level_timer.stop()
        self._c._connection_timer.stop()
        # A fresh client always starts unmuted. Clear this safety state now,
        # but leave recorder flags intact until the worker has asked the host
        # server to finalize every track.
        self._c._self_transmit_muted = False
        self._c._talk_break_intended = False
        self._c._sync_self_mute_button()

    def _finish_session_stop_ui(self, error: str = "") -> None:
        """Finalize local/UI state after recorder, client, and server stop."""
        self._c.window.session_strip.reset_session_clock()
        self._c.window.session_strip.set_tools_enabled(True)
        self.stopping = False
        if error:
            self._c._transition_lifecycle(
                SessionLifecyclePhase.FAILED_RECOVERABLE,
                "Session cleanup needs attention",
            )
            self.ended_by_user = False
            self._c.window.participant_grid.set_session_state(
                SessionUiState.stop_failed()
            )
            self._c.window.session_hud.set_state(
                "WebJam couldn’t finish cleanly",
                "The current jam is still protected. Try ending or leaving again.",
            )
            self._c.window.session_strip.set_audio_state(
                "Try End Session"
                if bool(getattr(self._c.settings, "host_server_enabled", False))
                else "Try Leave Jam",
                enabled=True,
            )
            self._c.window.flash_message(error, ms=8000)
            return
        self._c._stop_session_peer()
        # The hosted server has been confirmed stopped before this success
        # callback. Revoke the v3 owner and clear its in-memory loopback mode
        # now so a later legacy LAN-host session keeps its original binding.
        self._c._clear_remote_invite_owner()
        # Guest transports are independent from the invitation owner. Stop
        # them only after Jamulus (and the hosted server for a host) is gone,
        # then restore any in-memory loopback route to its saved local profile.
        self._c._stop_remote_transport()
        self._c.recording.on_audio_session_stopped()
        self._c._transition_lifecycle(
            SessionLifecyclePhase.COMPLETED,
            "Session resources were released",
        )
        self.reset_to_idle()
        self._c._reconnect_banner_shown = False
        self._c._rpc_hang_banner_shown = False
        self._c._reconnect_gave_up = False

    def _stop_session_services(self, hosting: bool) -> None:
        """Stop in data-safe order without freezing the Qt event loop."""
        failures: list[str] = []
        if hosting:
            self._c._transition_lifecycle(
                SessionLifecyclePhase.FINALIZING_RECORDINGS,
                "Finalizing hosted recordings before shutdown",
            )
            try:
                recording_stopped = (
                    self._c.recording.stop_server_recording_for_shutdown()
                )
                if not recording_stopped:
                    failures.append("The recording may still be finishing.")
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not finish hosted recording during End Jam")
                failures.append("The recording may still be finishing.")
            if failures:
                error = " ".join(failures)
                self._c._ui_invoker.invoke(
                    lambda message=error: self._finish_session_stop_ui(message)
                )
                return
        try:
            if not self._c.bridge.stop_jamulus():
                failures.append("The local music connection did not stop cleanly.")
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not stop the local music connection")
            failures.append("The local music connection did not stop cleanly.")
        if hosting:
            try:
                if not self._c.bridge.stop_hosted_server():
                    failures.append("The hosted jam did not stop cleanly.")
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not stop the hosted band server")
                failures.append("The hosted jam did not stop cleanly.")
        error = " ".join(failures)
        self._c._ui_invoker.invoke(
            lambda message=error: self._finish_session_stop_ui(message)
        )

    def reset_to_idle(self) -> None:
        self._c.session_health.reset_live_truth()
        self._c.session_lifecycle.reset(reason="Ready for a new session")
        # The session is over, so any confirmed transmit-mute died with the
        # Jamulus client.  A relaunched client always starts unmuted; carrying
        # TALK/muted state forward would render a fail-open lie.
        self._c._self_transmit_muted = False
        self._c._talk_break_intended = False
        self.recovering = False
        self._c.participants.clear()
        self._c._push_participants_to_grid()
        self._c.window.participant_grid.set_session_state(
            SessionUiState.idle(
                server=self._c.bridge.effective_server(),
                hosting=bool(
                    getattr(self._c.settings, "host_server_enabled", False)
                ),
            )
        )
        self._c._update_session_hud()

    def reset_to_demo(self) -> None:
        """Compatibility alias retained for older extensions."""
        self.reset_to_idle()

    def apply_participants(self, jamulus_participants: list) -> None:
        if self.stopping:
            return
        if not jamulus_participants:
            if self.connected:
                self.connected = False
                self.recovering = True
                self._c._local_audio_seen = False
                self._c._remote_audio_seen = False
                self._c._level_timer.stop()
                self._c.window.set_status_latency("Not connected")
                self._c.window.set_status_audio("Connecting…")
                self._c.participants.clear()
                self._c._push_participants_to_grid()
                self._c.window.participant_grid.set_session_state(
                    SessionUiState.reconnecting()
                )
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.RECONNECTING,
                    "The music engine lost its roster",
                )
                if self._c.bridge.jamulus_launch_intended:
                    self._c._connection_timer.start()
            return
        hosting = bool(getattr(self._c.settings, "host_server_enabled", False))
        local_session_proven = not hosting or any(
            self._c._is_local_participant(person)
            for person in jamulus_participants
        )
        if not local_session_proven and self.connected:
            # The hosted server may still report guests after this Mac's
            # client/audio path has failed. Keep their cards visible, but do
            # not call the host connected or cancel its recovery timeout.
            self.connected = False
            self._c._local_audio_seen = False
            self._c._level_timer.stop()
            if self._c.bridge.jamulus_launch_intended:
                self._c._connection_timer.start()
        elif (
            not local_session_proven
            and self._c.bridge.jamulus_launch_intended
            and not self._c._connection_timer.isActive()
        ):
            self._c._connection_timer.start()
        rpc = getattr(self._c.jamulus, "rpc_client", None)
        self._c.session_health.mark_process(
            self._c.bridge.jamulus_state,
            rpc_available=bool(getattr(rpc, "available", False)),
        )
        self._c.session_health.mark_participants(len(jamulus_participants))

        if not self.connected and local_session_proven:
            self.connected = True
            self.recovering = False
            self.connection_timed_out = False
            self._c._connection_timer.stop()
            self._c.window.session_strip.start_session_clock()
            self._c.window.session_strip.set_tools_enabled(True)
            self._c.session_health.mark_rpc_result("participants", True)
            self._c._transition_lifecycle(
                SessionLifecyclePhase.CONNECTED,
                "This Mac is present in the live Jamulus roster",
            )
            self._c.jamulus.set_name(self._c.settings.musician_name)
            self._c.participants.clear()
            self._c._level_timer.start()
            self._c._restore_saved_mix()
            try:
                self._c.metrics.increment("metric_session_started")
            except Exception:  # noqa: BLE001
                LOGGER.debug("metric_session_started increment failed", exc_info=True)
            if self._c.bridge.practice_mode:
                self._c.window.set_status_audio("Practice live")
                self._c.window.flash_message(
                    "Practice session live — you're on a private local server. "
                    "Play something and watch your meter.",
                    ms=5000,
                )
            else:
                self._c.window.set_status_audio("Connected")
                self._c.window.flash_message(
                    "Connected. Waiting for band members…",
                    ms=4000,
                )

        n = len(jamulus_participants)
        if not local_session_proven:
            self._c.window.set_status_latency(
                f"{n} on server · this Mac is still connecting"
            )
            self._c.window.set_status_audio("Connecting…")
        elif n == 1:
            self._c.window.set_status_latency("1 participant · waiting for others")
        else:
            self._c.window.set_status_latency(f"{n} participants")

        incoming_ids = {p.channel_id for p in jamulus_participants}
        for cid in list(self._c.participants.keys()):
            if cid not in incoming_ids:
                del self._c.participants[cid]

        for jp in jamulus_participants:
            is_local = self._c._is_local_participant(jp)
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
                    is_local=is_local,
                )
            else:
                existing.name = jp.name
                existing.is_connected = jp.is_connected
                existing.is_local = is_local
                new_role = self._c._role_label(jp)
                if new_role != existing.role:
                    existing.role = new_role

        self._c._push_participants_to_grid()

    def on_readiness_refresh(self, jamulus_up: bool) -> None:
        if not jamulus_up and not self.ended_by_user:
            self.stopping = False

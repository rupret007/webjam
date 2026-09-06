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

from core.jamulus_name import JamulusNameError, validate_jamulus_name
from core.jamulus_rpc_client import JamulusRpcMonitorIdentity
from core.meeting_companion import (
    EndSessionPrompt,
    end_session_prompt,
    service_name_for_link,
)
from core.session_lifecycle import SessionLifecyclePhase
from services.bridge_service import JamulusRpcFreshness
from webjam_qt.session_state import SessionPhase, SessionUiState
from webjam_qt.widgets.participant_card import ParticipantPresentation

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController

LOGGER = logging.getLogger("webjam.qt.audio_coordinator")


class AudioCoordinator:
    """Owns Jamulus audio-session UI state and participant grid transitions."""

    _NAME_SYNC_MAX_SEND_ATTEMPTS = 3

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller
        self.connected = False
        self.stopping = False
        self.ended_by_user = False
        self.connection_timed_out = False
        self.recovering = False
        self.permission_explained = False
        self.cleanup_retry_required = False
        self._stop_hosting = False
        self._stop_art_room = False
        self._stop_recording_cleanup_required = False
        self._name_sync_target = ""
        self._name_sync_send_attempts = 0
        self._name_sync_sent = False
        self._name_sync_process = self._current_jamulus_process()

    def _current_jamulus_process(self):
        """Return the owned native-client identity, if one has been launched."""

        bridge = getattr(self._c, "bridge", None)
        return getattr(bridge, "jamulus_process", None)

    def _reset_musician_name_sync(self) -> None:
        """Allow one configured-name handoff for the next client session."""

        self._name_sync_target = ""
        self._name_sync_send_attempts = 0
        self._name_sync_sent = False
        self._name_sync_process = self._current_jamulus_process()

    def _sync_musician_name_if_ready(self) -> None:
        """Apply the configured Jamulus name once authenticated RPC is ready.

        A hosted server can prove the local audio connection before the
        Jamulus *client* RPC socket is accepting commands.  Treating that
        early roster as the only opportunity to call ``setName`` leaves the
        client at Jamulus's ``No Name`` default for the whole session.

        Successful handoff is deliberately session-scoped: after Jamulus
        accepts the request, a musician can still rename themselves in its
        native window without WebJam repeatedly overwriting that choice.
        Failed sends are bounded so a broken socket cannot cause unbounded
        RPC traffic on participant updates.
        """

        process = self._current_jamulus_process()
        if process is not self._name_sync_process:
            # A replacement Jamulus process starts with its packaged native
            # profile and needs one fresh handoff. A roster/RPC interruption
            # in the *same* process does not: the musician may have renamed
            # themselves in Jamulus after WebJam's initial handoff, and an
            # automatic reconnect must not overwrite that choice.
            self._name_sync_target = ""
            self._name_sync_send_attempts = 0
            self._name_sync_sent = False
            self._name_sync_process = process

        try:
            desired = validate_jamulus_name(
                self._c.settings.musician_name
            ).value
        except JamulusNameError:
            return
        if desired != self._name_sync_target:
            self._name_sync_target = desired
            self._name_sync_send_attempts = 0
            self._name_sync_sent = False
        if (
            self._name_sync_sent
            or self._name_sync_send_attempts >= self._NAME_SYNC_MAX_SEND_ATTEMPTS
        ):
            return
        rpc = getattr(self._c.jamulus, "rpc_client", None)
        if not bool(getattr(rpc, "available", False)):
            return
        self._name_sync_send_attempts += 1
        if self._c.jamulus.set_name(desired):
            self._name_sync_sent = True

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
            self._c._local_audio_seen = False
            self._c._remote_audio_seen = False
            self._c._clear_primary_local_roster_proof()
            self._reset_musician_name_sync()
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
            # Recovery exhaustion is terminal until this explicit launch has
            # actually been accepted. A denied/busy launch must retain the
            # failed presentation and keep late old-process rosters blocked.
            self._c._reconnect_gave_up = False
            self._c._reconnect_banner_shown = False
            self._c._rpc_hang_banner_shown = False
            self.recovering = False
            self.connection_timed_out = False
            self._c._sync_reference_track_primary_gate()
            if bool(getattr(self._c.settings, "host_server_enabled", False)):
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
                    "Waiting for the hosted server and private LAN invitation",
                )
            self._c._connection_timer.start()
            return True

    def on_practice_requested(self) -> bool:
        if self.stopping or self.cleanup_retry_required:
            self._c.window.flash_message(
                "Wait for the current session cleanup to finish before "
                "starting practice.",
                ms=5000,
            )
            return False
        if self._c._is_jamulus_running():
            self._c.window.flash_message(
                "End the current session first, then start a solo practice.",
                ms=4000,
            )
            return False
        self._c.window.set_status_audio("Starting practice…")
        self._c._clear_primary_local_roster_proof()
        self._reset_musician_name_sync()
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
            if getattr(self._c, "_reconnect_gave_up", False):
                # Keep the terminal recovery owner and its conductor generation
                # intact when Practice was refused. Only an accepted fresh
                # process may reopen roster authentication.
                self._c._transition_lifecycle(
                    SessionLifecyclePhase.FAILED_RECOVERABLE,
                    "Fresh practice launch was not accepted",
                )
                self._c.window.participant_grid.set_session_state(
                    SessionUiState.reconnect_failed()
                )
                self._c._update_session_hud()
            else:
                self.reset_to_idle()
            return False
        self._c._reconnect_gave_up = False
        self._c._reconnect_banner_shown = False
        self._c._rpc_hang_banner_shown = False
        self.recovering = False
        self.connection_timed_out = False
        self._c._sync_reference_track_primary_gate()
        return True

    def _current_art_room(self) -> bool:
        active = getattr(self._c, "_art_room_active", None)
        return bool(callable(active) and active())

    def _hosted_audio_cleanup_required(self) -> bool:
        """Retained server ownership survives a change from Host to Guest."""

        bridge = self._c.bridge
        if any(
            getattr(bridge, name, None) is not None
            for name in ("hosted_server_process", "_hosted_runtime_paths")
        ) or bool(getattr(bridge, "_hosted_restart_inflight", False)):
            return True
        try:
            return bool(bridge.hosted_server_alive())
        except Exception:
            # An unavailable ownership probe cannot prove an Art-only room.
            return True

    def _audio_or_recording_cleanup_required(self) -> bool:
        """Skip recorder work only with positive evidence of an Art-only room."""

        recording = self._c.recording
        if (
            bool(recording.is_recording_active)
            or bool(getattr(recording, "take_in_progress", False))
            or bool(getattr(self._c, "_server_recording", False))
            or bool(getattr(self._c, "_recorder_armed", False))
            or getattr(recording, "_local_capture", None) is not None
            or any(
                bool(getattr(recording, name, ""))
                for name in (
                    "_validation_take_id",
                    "_shutdown_validation_pending_take_id",
                    "_shutdown_validation_dispatch_take_id",
                )
            )
            or self._hosted_audio_cleanup_required()
        ):
            return True
        bridge = self._c.bridge
        lifecycle_active = getattr(
            bridge, "_runtime_component_lifecycle_is_active", None
        )
        if not callable(lifecycle_active):
            return True
        try:
            return bool(lifecycle_active()) or self._c._is_jamulus_running()
        except Exception:
            return True

    def stop(self) -> None:
        if self.stopping:
            return
        if self.cleanup_retry_required:
            self.retry_stop()
            return
        art_room = self._current_art_room()
        room_role = getattr(self._c, "_art_room_role", "") if art_room else ""
        hosting = (
            room_role == "host"
            if room_role in {"host", "guest"}
            else bool(getattr(self._c.settings, "host_server_enabled", False))
        )
        recording_active = self._c.recording.is_recording_active
        take_in_progress = bool(
            getattr(self._c.recording, "take_in_progress", recording_active)
        )
        if take_in_progress and (
            hosting or (art_room and self._hosted_audio_cleanup_required())
        ):
            finish_action = (
                "ending the room" if hosting else "leaving the room"
            ) if art_room else "ending the jam"
            QMessageBox.information(
                self._c.window,
                "Finish the take first",
                (
                    "Press Stop Rec, then wait for ‘Take saved’ before "
                    if recording_active
                    else "Wait for ‘Take saved’ before "
                )
                + finish_action
                + ". This keeps every musician's track complete and verified.",
            )
            return
        # Ending the jam does not end the meeting. WebJam runs beside an
        # independent Webex window and cannot close it, so the confirmation
        # says so rather than leaving a musician to discover it afterwards.
        meeting_url = str(getattr(self._c.settings, "webex_url", "") or "").strip()
        prompt = end_session_prompt(
            hosting=hosting,
            recording_active=recording_active,
            meeting_configured=bool(meeting_url),
            meeting_service=service_name_for_link(meeting_url),
        )
        if art_room:
            prompt = EndSessionPrompt(
                title="End Room?" if hosting else "Leave Room?",
                question=(
                    "End this room for everyone?\n\n"
                    "Artists will disconnect from this WebJam room."
                    if hosting
                    else "Leave this room?\n\n"
                    "The host and other artists will stay connected."
                ),
                meeting_note=(
                    f"Your {service_name_for_link(meeting_url)} meeting and "
                    "external canvas stay open. Close them in their own apps "
                    "when you’re done."
                    if meeting_url
                    else "Your meeting and external canvas stay open. "
                    "Close them in their own apps when you’re done."
                ),
            )
        reply = QMessageBox.question(
            self._c.window, prompt.title,
            prompt.full_text(),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._begin_session_stop(hosting, art_room=art_room)

    def _begin_session_stop(
        self, hosting: bool, *, art_room: bool | None = None
    ) -> None:
        """Enter one serialized End/Leave attempt on the Qt owner thread."""

        self._stop_hosting = bool(hosting)
        self._stop_art_room = (
            self._current_art_room() if art_room is None else bool(art_room)
        )
        # Pending profile discovery still owns an entry invitation. Retain
        # its identity until this exact Leave confirms all cleanup below.
        self._stop_remote_invitation = (
            getattr(self._c, "_remote_invitation", None) if self._stop_art_room else None
        )
        # A later transport failure can outlive the audio/capture owner that
        # needed retirement. Keep that obligation across every cleanup retry.
        previous_recording_cleanup = (
            self._stop_recording_cleanup_required
            if self.cleanup_retry_required else False
        )
        self._stop_recording_cleanup_required = bool(
            previous_recording_cleanup
            or not self._stop_art_room
            or self._audio_or_recording_cleanup_required()
        )
        self.cleanup_retry_required = False
        self.stopping = True
        self.ended_by_user = True
        self.recovering = False
        self._c._clear_primary_local_roster_proof()
        self._c.window.session_strip.set_tools_enabled(False)
        prepare_pocket_stage = getattr(
            self._c,
            "_prepare_pocket_stage_for_session_end",
            None,
        )
        if callable(prepare_pocket_stage):
            prepare_pocket_stage()
        self._c.window.set_status_audio("Ending…" if hosting else "Leaving…")
        self._c.window.set_status_latency("Not connected")
        self._c.window.session_strip.set_audio_state(
            "Ending…" if hosting else "Leaving…", enabled=False
        )
        self._c.participants.clear()
        self._c._push_participants_to_grid()
        ending_state = SessionUiState.ending(hosting=hosting)
        if self._stop_art_room:
            ending_state = SessionUiState(
                phase=SessionPhase.ENDING,
                title="Ending this room…" if hosting else "Leaving the room…",
                message=(
                    "WebJam is disconnecting the room. Your meeting and "
                    "external canvas stay open."
                    if hosting
                    else "WebJam is disconnecting this Mac. Your meeting and "
                    "external canvas stay open."
                ),
                primary_text="Please wait…",
                primary_enabled=False,
                show_ready_check=False,
            )
        self._c.window.participant_grid.set_session_state(ending_state)
        self._c._transition_lifecycle(
            SessionLifecyclePhase.ENDING,
            (
                "Ending the hosted room" if hosting else "Leaving the room"
            ) if self._stop_art_room else (
                "Ending the hosted jam" if hosting else "Leaving the jam"
            ),
        )
        self._c.window.session_hud.set_state(
            ending_state.title, ending_state.message
        )
        threading.Thread(
            target=self._stop_session_services,
            args=(hosting,),
            daemon=True,
            name="webjam-session-stop",
        ).start()
        self.connected = False
        self._c._sync_reference_track_primary_gate()
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

    def retry_stop(self) -> None:
        """Retry only unresolved cleanup; never reinterpret it as a new Start."""

        if self.stopping or not self.cleanup_retry_required:
            return
        self._begin_session_stop(
            self._stop_hosting, art_room=self._stop_art_room
        )

    def require_cleanup_retry(
        self,
        *,
        hosting: bool,
        error: str,
        title: str = "WebJam couldn’t finish cleanly",
        detail: str | None = None,
        art_room: bool | None = None,
    ) -> None:
        """Keep an unresolved session owner reachable through one truthful retry.

        Some ownership transitions fail before the ordinary End/Leave worker
        starts (for example, replacing an idle private-transfer peer). Cache
        the role that owns the unresolved resources rather than deriving it
        later from settings that an invitation may be trying to replace.
        """

        self._stop_hosting = bool(hosting)
        self._stop_art_room = (
            self._current_art_room() if art_room is None else bool(art_room)
        )
        self.stopping = False
        self.cleanup_retry_required = True
        self.ended_by_user = False
        self._c.window.session_strip.set_tools_enabled(True)
        complete_pocket_stage = getattr(
            self._c,
            "_complete_pocket_stage_session_end",
            None,
        )
        if callable(complete_pocket_stage):
            complete_pocket_stage(succeeded=False)
        self._c._transition_lifecycle(
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            "Session cleanup needs attention",
        )
        retry_label = "Try End Session" if hosting else "Try Leave Jam"
        failed_state = SessionUiState.stop_failed()
        if self._stop_art_room:
            retry_label = "Try End Room" if hosting else "Try Leave Room"
            failed_state = SessionUiState(
                phase=SessionPhase.ERROR,
                title=title,
                message="Room cleanup needs attention. Choose "
                + retry_label
                + " to finish disconnecting.",
                primary_text="Room cleanup needs attention",
                primary_enabled=False,
                show_ready_check=False,
            )
        if detail is None:
            detail = (
                "The room has not finished disconnecting. Choose "
                + retry_label
                + " to finish."
                if self._stop_art_room
                else "The current jam is still protected. Try ending or "
                "leaving again."
            )
        self._c.window.participant_grid.set_session_state(failed_state)
        self._c.window.session_hud.set_state(title, detail)
        self._c.window.session_strip.set_audio_state(retry_label, enabled=True)
        self._c.window.flash_message(error, ms=8000)
        # Publish the cleanup receipt to Notes/Studio too. Room observers are
        # blocked while cleanup is unresolved, so their polling is not a refresh.
        refresh_guidance = getattr(self._c, "_update_session_hud", None)
        if self._stop_art_room and callable(refresh_guidance):
            refresh_guidance()

    def _finish_session_stop_ui(
        self,
        error: str = "",
        *,
        remote_route_base_settings=None,
    ) -> None:
        """Finalize local/UI state after recorder, client, and server stop."""
        if not error and (
            remote_route_base_settings is not None
            and remote_route_base_settings is not self._c.settings
        ):
            try:
                old_settings = self._c.settings
                self._c._replace_settings_object(remote_route_base_settings)
                self._c._reconfigure_services_after_settings(old_settings)
            except Exception:
                LOGGER.exception(
                    "Could not restore settings after private transport stop"
                )
                self._c._remote_route_base_settings = (
                    remote_route_base_settings
                )
                error = (
                    "The previous session settings could not be restored "
                    "cleanly."
                )
        self._c.window.session_strip.set_tools_enabled(True)
        self.stopping = False
        complete_pocket_stage = getattr(
            self._c,
            "_complete_pocket_stage_session_end",
            None,
        )
        if error:
            self.require_cleanup_retry(
                hosting=self._stop_hosting,
                error=error,
                art_room=self._stop_art_room,
            )
            return
        restore_art_profile = getattr(
            self._c, "_finish_art_room_profile_restore", None
        )
        if self._stop_art_room and callable(restore_art_profile):
            try:
                restore_art_profile()
            except Exception:
                LOGGER.error("Could not restore the saved profile after room cleanup")
                self.require_cleanup_retry(
                    hosting=self._stop_hosting,
                    error="Your saved workspace could not be restored cleanly.",
                    art_room=True,
                )
                return
        if (
            self._stop_art_room
            and getattr(self._c, "_remote_session", None) is None
            and getattr(self._c, "_remote_invitation", None)
            is getattr(self, "_stop_remote_invitation", None)
        ):
            # Leave also retires a native invite whose host profile never
            # arrived. A later Start must not replay that one-use enrollment.
            self._c._remote_invitation = None
            self._c._remote_invitation_requires_replacement = False
        self._stop_remote_invitation = None
        self.cleanup_retry_required = False
        self._c.window.session_strip.reset_session_clock()
        if callable(complete_pocket_stage):
            complete_pocket_stage(succeeded=True)
        if self._stop_recording_cleanup_required:
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
        # Capture ownership before the peer stop restores a guest's saved
        # profile/settings. An Art role never licenses abandoning old audio.
        art_room = self._stop_art_room
        self._stop_recording_cleanup_required = bool(
            self._stop_recording_cleanup_required
            or not art_room
            or self._audio_or_recording_cleanup_required()
        )
        finalize_recording = (
            self._stop_recording_cleanup_required if art_room else hosting
        )
        stop_hosted_server = hosting or (
            art_room and self._hosted_audio_cleanup_required()
        )
        # The Shared Track owns a second Jamulus client. Retire it before
        # recorder finalization and before the primary musician client/server;
        # an uncertain backing route must never outlive the jam it was feeding.
        if not self._c._stop_reference_track_for_session_end(background=False):
            failures.append(
                "The separate Shared Track client did not stop cleanly."
            )
            error = " ".join(failures)
            self._c._ui_invoker.invoke(
                lambda message=error: self._finish_session_stop_ui(message)
            )
            return
        if finalize_recording:
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
            except Exception:
                LOGGER.exception("Could not finish hosted recording during End Jam")
                failures.append("The recording may still be finishing.")
            if failures:
                error = " ".join(failures)
                self._c._ui_invoker.invoke(
                    lambda message=error: self._finish_session_stop_ui(message)
                )
                return
        stop_pocket_stage = getattr(
            self._c,
            "_stop_pocket_stage_for_session_end",
            None,
        )
        if callable(stop_pocket_stage) and not stop_pocket_stage():
            failures.append("iPhone sharing did not stop cleanly.")
        # The v2 local-original transfer owner must be proven stopped before
        # the primary client disappears. If it refuses, retain its object and
        # invitation so Try End/Leave can retry without orphaning a sidecar.
        if not self._c._stop_session_peer(clear_invite=True):
            failures.append(
                "The room connection did not stop cleanly."
                if art_room
                else "The private recording-transfer connection did not stop cleanly."
            )
        if failures:
            error = " ".join(failures)
            self._c._ui_invoker.invoke(
                lambda message=error: self._finish_session_stop_ui(message)
            )
            return
        try:
            if not self._c.bridge.stop_jamulus():
                failures.append("The local music connection did not stop cleanly.")
        except Exception:
            LOGGER.exception("Could not stop the local music connection")
            failures.append("The local music connection did not stop cleanly.")
        if stop_hosted_server:
            try:
                if not self._c.bridge.stop_hosted_server():
                    failures.append("The hosted jam did not stop cleanly.")
            except Exception:
                LOGGER.exception("Could not stop the hosted band server")
                failures.append("The hosted jam did not stop cleanly.")
        if failures:
            error = " ".join(failures)
            self._c._ui_invoker.invoke(
                lambda message=error: self._finish_session_stop_ui(message)
            )
            return
        # V3 transport/owner teardown depends on Jamulus and, for a host, the
        # loopback-only server being gone. Check both results before publishing
        # COMPLETED; their helpers retain failed owners for a bounded retry.
        if not self._c._clear_remote_invite_owner():
            failures.append(
                "The private invitation service did not stop cleanly."
            )
        if failures:
            error = " ".join(failures)
            self._c._ui_invoker.invoke(
                lambda message=error: self._finish_session_stop_ui(message)
            )
            return
        remote_route_base_settings = getattr(
            self._c,
            "_remote_route_base_settings",
            None,
        )
        if not self._c._stop_remote_transport(restore_route=False):
            failures.append(
                "The private session transport did not stop cleanly."
            )
        error = " ".join(failures)
        self._c._ui_invoker.invoke(
            lambda message=error, base=remote_route_base_settings: (
                self._finish_session_stop_ui(
                    message,
                    remote_route_base_settings=base if not message else None,
                )
            )
        )

    def reset_to_idle(self) -> None:
        self.stopping = False
        self.cleanup_retry_required = False
        self._stop_recording_cleanup_required = False
        self._c._clear_primary_local_roster_proof()
        self._reset_musician_name_sync()
        self._c.session_health.reset_live_truth()
        self._c.session_lifecycle.reset(reason="Ready for a new session")
        self._c._clear_lan_invite_address()
        clear_startup_recovery = getattr(self._c, "_clear_startup_recovery", None)
        if callable(clear_startup_recovery):
            # End/Leave is a clean terminal state. Keep recovery only for a
            # genuinely interrupted setup, never for a session the musician
            # intentionally finished.
            clear_startup_recovery()
        reset_conductor = getattr(self._c, "_reset_session_conductor_attempt", None)
        if callable(reset_conductor):
            reset_conductor()
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
        # End/Leave changes this control to a disabled progress label before
        # teardown. Reset it explicitly because the readiness timer is stopped
        # during a clean session end and may not refresh the strip again.
        self._c.window.session_strip.set_audio_state(
            "Start Session",
            enabled=True,
        )
        self._c._update_session_hud()

    def reset_to_demo(self) -> None:
        """Compatibility alias retained for older extensions."""
        self.reset_to_idle()

    def apply_participants(
        self,
        jamulus_participants: list,
        *,
        source_identity: JamulusRpcMonitorIdentity | None = None,
    ) -> bool:
        if (
            self.stopping
            or self.cleanup_retry_required
            or getattr(self._c, "_reconnect_gave_up", False)
            or getattr(
                self._c,
                "_primary_recovery_retire_inflight",
                False,
            )
        ):
            return False
        if not jamulus_participants:
            if self.connected:
                self._c._handle_unexpected_primary_jamulus_loss()
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
            return False
        # Client RPC identifies this Mac's own row explicitly. A nonempty
        # server/remote-only roster proves that the room exists, not that this
        # musician's audio path joined it; require the local row for both host
        # and guest recovery acknowledgement.
        local_roster_proven = any(
            self._c._is_local_participant(person)
            for person in jamulus_participants
        )
        recovery = self._c._primary_jamulus_recovery_snapshot()
        source_matches_process = bool(
            isinstance(source_identity, JamulusRpcMonitorIdentity)
            and source_identity.monitor_epoch > 0
            and source_identity.is_process_bound
            and recovery is not None
            and source_identity.process_generation == recovery.generation
            and source_identity.process_id == recovery.process_id
        )
        local_session_proven = bool(
            local_roster_proven
            and source_matches_process
            and recovery is not None
            and recovery.launch_intended
            and recovery.process_alive
            and recovery.generation > 0
            and recovery.process_id > 0
            and recovery.rpc_freshness is JamulusRpcFreshness.FRESH
            and not recovery.pending
            and not recovery.inflight
        )
        if local_session_proven and recovery is not None and recovery.active:
            try:
                local_session_proven = bool(
                    self._c.bridge.mark_jamulus_reconnect_authenticated(
                        generation=recovery.generation,
                        process_id=recovery.process_id,
                    )
                )
            except Exception:
                LOGGER.warning(
                    "Jamulus roster recovery acknowledgement failed",
                    exc_info=True,
                )
                local_session_proven = False
        if (
            local_session_proven
            and recovery is not None
            and recovery.native_setup_grace_configured
        ):
            try:
                local_session_proven = bool(
                    self._c.bridge.finish_native_sound_setup(
                        generation=recovery.generation,
                        process_id=recovery.process_id,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - setup ownership fails closed
                LOGGER.warning(
                    "Native Jamulus sound-setup acknowledgement failed (%s).",
                    type(exc).__name__,
                )
                local_session_proven = False
        if local_session_proven and recovery is not None:
            self._c._record_primary_local_roster_proof(recovery)
        elif not self.connected:
            self._c._clear_primary_local_roster_proof()
        if not local_session_proven and self.connected:
            # The hosted server may still report guests after this Mac's
            # client/audio path has failed. Keep their cards visible, but do
            # not call the host connected or cancel its recovery timeout.
            self._c._handle_unexpected_primary_jamulus_loss()
            self.recovering = True
            self._c._local_audio_seen = False
            self._c._remote_audio_seen = False
            self._c._level_timer.stop()
            self._c.window.set_status_audio("Connecting…")
            self._c.window.set_status_latency(
                "Server roster visible · this Mac is reconnecting"
            )
            self._c.window.participant_grid.set_session_state(
                SessionUiState.reconnecting()
            )
            self._c._transition_lifecycle(
                SessionLifecyclePhase.RECONNECTING,
                "The server roster no longer proves this Mac's audio path",
            )
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
            recovered_from_interruption = bool(
                self.recovering
                or self.connection_timed_out
                or self._c._reconnect_banner_shown
                or self._c._rpc_hang_banner_shown
            )
            self.connected = True
            self.recovering = False
            self.connection_timed_out = False
            if recovered_from_interruption:
                # The authenticated client roster is the one recovery owner.
                # Clear every presentation latch atomically so the reconnect
                # timer cannot emit a second success message on its next tick.
                self._c._reconnect_banner_shown = False
                self._c._rpc_hang_banner_shown = False
                self._c._reconnect_gave_up = False
            self._c._sync_reference_track_primary_gate()
            self._c._connection_timer.stop()
            self._c.window.session_strip.start_session_clock()
            self._c.window.session_strip.set_tools_enabled(True)
            self._c.session_health.mark_rpc_result("participants", True)
            self._c._transition_lifecycle(
                SessionLifecyclePhase.CONNECTED,
                "This Mac is present in the live Jamulus roster",
            )
            if recovered_from_interruption:
                self._c._resume_session_conductor_after_authoritative_reconnect()
            self._c.participants.clear()
            self._c._level_timer.start()
            self._c._restore_saved_mix()
            try:
                self._c.metrics.increment("metric_session_started")
            except Exception:
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
                    (
                        "Band audio reconnected."
                        if recovered_from_interruption
                        else "Connected. Waiting for band members…"
                    ),
                    ms=4000,
                )

        if local_session_proven:
            self._sync_musician_name_if_ready()

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
        return local_session_proven

    def on_readiness_refresh(self, jamulus_up: bool) -> None:
        if not jamulus_up and not self.ended_by_user:
            self.stopping = False

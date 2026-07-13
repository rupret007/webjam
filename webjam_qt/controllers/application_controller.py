"""
ApplicationController — the brain.

Owns session state and wires ConductorWindow signals to the service layer.

Participant lifecycle:
  1. Startup and connection transitions show explicit, actionable empty states.
  2. Only confirmed Jamulus participants appear as mixer cards.
  3. Real level values are polled every 100 ms after connection.

Mixer signals (fader/mute/solo) route directly to ``JamulusController`` which
sends them to Jamulus via JSON-RPC (preferred) or UDP protocol (fallback).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from core.creative_modes import CREATIVE_MODES, get_mode_by_key_or_default
from core.session_health import SessionHealth
from core.session_intelligence import build_session_pulse
from core.settings import AppSettings, load_settings
from core.session_transfer_runtime import (
    GuestPeerSession,
    HostPeerSession,
    default_installation_identity_path,
)
from services.bridge_service import BridgeService
from storage.repository import WebJamRepository
from ui.services import MetricsService

from webjam_qt.controllers.mix_manager import MixManager
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.controllers.ui_thread import UiThreadInvoker
from webjam_qt.controllers.audio_coordinator import AudioCoordinator
from webjam_qt.controllers.video_coordinator import VideoCoordinator
from webjam_qt.controllers.recording_coordinator import RecordingCoordinator
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.session_state import SessionUiState

LOGGER = logging.getLogger("webjam.qt.application_controller")

class ApplicationController(QObject):
    """Glue layer between ConductorWindow and the service layer."""

    _LEVEL_POLL_MS = 100   # how often to push meter updates to the grid
    _METER_TICK_MS = 40    # global LevelMeter decay tick (was per-meter)
    _CONNECTION_TIMEOUT_MS = 30_000

    def __init__(
        self,
        window: ConductorWindow,
        settings: Optional[AppSettings] = None,
        session_invite=None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or load_settings()
        self.session_health = SessionHealth()

        self._ui_invoker = UiThreadInvoker(self)

        self.repository = WebJamRepository()
        self.metrics = MetricsService(self.repository)

        from jamulus_controller import JamulusController
        from webex_integration import WebexController

        self.jamulus = JamulusController(
            host=self.settings.jamulus_server,
            port=self.settings.jamulus_port,
            rpc_port=self.settings.jamulus_rpc_port,
        )
        self.webex = WebexController(meeting_url=self.settings.webex_url)

        self._attach_jamulus_callbacks()
        self._server_recording = False
        # Last-known band-server recorder ARMED state (set by the Record
        # button worker; distinct from _server_recording, which is "actually
        # rolling right now" from recorderState notifications).
        self._recorder_armed = False

        self._shutdown = False
        # User intent is separate from a transient participant snapshot.  Keep
        # Talk Break fail-closed across an automatic Jamulus reconnect.
        self._talk_break_intended = False
        # This is the global jamulusclient/setMuted state. Participant.muted is
        # a different control: it only mutes that channel in the local mix.
        self._self_transmit_muted = False

        self.bridge = BridgeService(
            jamulus_controller=self.jamulus,
            webex_controller=self.webex,
            metrics_service=self.metrics,
            repository=self.repository,
            settings=self.settings,
            ui_callbacks={
                "set_status_banner":    self._set_status_banner,
                "refresh_readiness":    self._refresh_readiness,
                "show_actionable_error": self._show_actionable_error,
                "show_message":         self._show_message,
                "shutdown_requested":   lambda: self._shutdown,
                "schedule_ui_callback": self._ui_invoker.invoke,
            },
        )

        # Participant map — keyed by channel_id
        self.participants: dict[int, ParticipantPresentation] = {}

        # True once JamulusController has pushed at least one real update
        self.audio = AudioCoordinator(self)
        self.video = VideoCoordinator(self)
        self.recording = RecordingCoordinator(self)

        # The peer plane is independent from Jamulus: it carries only durable
        # identity, confirmed recording state, and isolated local originals.
        # Host service binding is lazy until a real private Wi-Fi address is
        # known; joiners enroll in a worker so startup/UI never blocks on LAN.
        self._host_peer_warning = ""
        self.host_peer = HostPeerSession(
            on_take_updated=self._on_peer_take_updated,
        )
        self.guest_peer: GuestPeerSession | None = None
        if session_invite is not None and bool(
            getattr(session_invite, "peer_enabled", False)
        ):
            self._configure_guest_peer(session_invite)

        # Companion API — optional localhost HTTP bridge for DAWs/editors.
        # Constructed here (no side effects) but only started in
        # start_companion_api(), which the app bootstrap calls; this keeps
        # tests that build a controller directly from binding a real port.
        from api.local_bridge import LocalApiBridge
        self.api_bridge = LocalApiBridge(
            get_participants=self._companion_get_participants,
            get_diagnostics=self._companion_get_diagnostics,
            port=self.settings.companion_api_port,
        )

        # Latch so the "Jamulus disconnected" flash only fires once per crash
        self._reconnect_banner_shown = False
        # Latch so the 'gave up after 5 tries' message fires once.
        self._reconnect_gave_up = False
        # Latch for the "RPC hung" banner — fires once when activity stalls,
        # cleared when activity resumes.
        self._rpc_hang_banner_shown = False
        # If RPC is silent for this many seconds, we consider it hung.
        # Generous: poll cadence is 5s and SSE level events fire ~50ms,
        # so 15s is plenty of margin.
        self._RPC_HANG_THRESHOLD_S = 15.0
        # Mix-dirty tracking: True when fader/mute/solo state has changed
        # since the last successful save.  shutdown() auto-saves if True
        # AND _jamulus_connected (so we don't overwrite a real saved mix
        # with stale disconnected state).
        self._mix_dirty = False
        self._local_audio_seen = False
        self._remote_audio_seen = False

        # Timers
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(self._LEVEL_POLL_MS)
        self._level_timer.timeout.connect(self._poll_levels)

        # Auto-reconnect: poll BridgeService every 3 s to retry dropped services
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(3_000)
        self._reconnect_timer.timeout.connect(self._on_reconnect_tick)
        self._reconnect_timer.start()

        # Single global LevelMeter decay tick — replaces N per-card timers.
        # See LevelMeter docstring; started in _bootstrap_ui, stopped in shutdown.
        self._meter_tick_timer = QTimer(self)
        self._meter_tick_timer.setInterval(self._METER_TICK_MS)
        self._meter_tick_timer.timeout.connect(
            self.window.participant_grid.tick_all_meters
        )

        # Retained as an inert compatibility attribute for extensions that
        # inspect controller timers. Native Webex launch uses no guest token.
        self._token_refresh_timer = QTimer(self)

        # Rebuilding the pulse scans the note text. Coalesce rapid typing
        # while retaining an immediate refresh before a brief is exported.
        self._pulse_refresh_timer = QTimer(self)
        self._pulse_refresh_timer.setSingleShot(True)
        self._pulse_refresh_timer.setInterval(200)
        self._pulse_refresh_timer.timeout.connect(self._refresh_session_pulse)

        self._connection_timer = QTimer(self)
        self._connection_timer.setSingleShot(True)
        self._connection_timer.setInterval(self._CONNECTION_TIMEOUT_MS)
        self._connection_timer.timeout.connect(self._on_connection_timeout)

        # Register real participant callback
        self.jamulus.register_callback(self._on_jamulus_participants)

        # Session metadata persistence (notes + title + mode)
        self._persistence = SessionPersistence(
            window.session_strip,
            window.session_canvas,
            logger=LOGGER,
        )

        # Mix save/load/restore (~/.webjam_mix.json).
        # Adapt flash_message's keyword-only ``ms=`` to MixManager's positional
        # ``(text, ms)`` callback contract so MixManager doesn't have to know
        # about ConductorWindow's signature quirks.
        self._mix_manager = MixManager(
            self.jamulus,
            lambda text, ms: self.window.flash_message(text, ms=ms),
            logger=LOGGER,
            metrics=self.metrics,
        )

        self._wire_signals()
        self._bootstrap_ui()
        self._start_routing_scan()
        if self.guest_peer is not None:
            self.guest_peer.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if self._shutdown:
            return  # closeEvent + app.py both call this; run teardown once
        self._shutdown = True
        self._level_timer.stop()
        self._reconnect_timer.stop()
        self._meter_tick_timer.stop()
        self._token_refresh_timer.stop()
        self._pulse_refresh_timer.stop()
        self._connection_timer.stop()
        self.window.recording_studio.shutdown()
        # Quitting mid-recording must keep the audio, not discard it.
        self.recording.salvage_on_shutdown()
        self._save_notes()
        self._save_session_title()
        # Auto-save mix if user touched anything since last save AND we were
        # connected to a real Jamulus (don't overwrite a real saved mix with
        # disconnected data). Best-effort: failures are caught by _on_save_mix.
        if self._mix_dirty and self._jamulus_connected:
            try:
                self._on_save_mix()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Auto-save mix on shutdown failed")
        # A hosted band server dies with WebJam: stop any recording cleanly
        # first so the server finalizes every musician's track, then
        # terminate the server itself. Ownership—not the latest role setting—
        # decides cleanup, so changing Host to Join can never leak a process.
        hosted_server_alive = self.bridge.hosted_server_alive()
        hosted_recording_safe = True
        if hosted_server_alive:
            try:
                if self.bridge.hosted_server_owned():
                    hosted_recording_safe = bool(
                        self.recording.stop_server_recording_for_shutdown()
                    )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Hosted recording shutdown failed")
                hosted_recording_safe = False
        if hosted_server_alive and not hosted_recording_safe:
            LOGGER.critical(
                "Leaving hosted services running because recording finalization "
                "could not be confirmed"
            )
        # Terminate the Jamulus subprocess so it doesn't outlive WebJam.
        # bridge.stop_jamulus() also calls jamulus_controller.stop() internally.
        if not hosted_server_alive or hosted_recording_safe:
            try:
                self.bridge.stop_jamulus()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Jamulus shutdown failed")
        if hosted_server_alive and hosted_recording_safe:
            try:
                self.bridge.stop_hosted_server()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Hosted server shutdown failed")
        # Keep the host peer reachable until the recorder is stopped and local
        # joiners have had their final control snapshot. Joiners then preserve
        # any still-active original and retain a resumable upload queue.
        self._stop_session_peer()
        try:
            self.webex.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex stop failed")
        # Tear down the embedded QWebEngineView so its Chromium subprocess
        # doesn't outlive WebJam — it was otherwise never explicitly closed.
        try:
            self.window.webex_embed.shutdown()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex embed shutdown failed")
        try:
            self.api_bridge.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Companion API stop failed")

    def _configure_guest_peer(self, invite) -> None:
        self._stop_session_peer()
        try:
            self.guest_peer = GuestPeerSession(
                invite,
                display_name=self.settings.musician_name,
                takes_root=(
                    self.settings.takes_directory
                    or str(Path.home() / "Music" / "WebJam Takes")
                ),
                installation_path=default_installation_identity_path(
                    self.settings.config_file
                ),
                capture_enabled=lambda: bool(
                    self.settings.local_capture_enabled
                ),
                capture_config=lambda: (
                    int(self.settings.audio_input_device_index),
                    int(self.settings.audio_samplerate),
                    int(self.settings.audio_blocksize),
                ),
                on_originals_changed=self._on_guest_originals_changed,
            )
            self._on_guest_originals_changed(self.guest_peer.originals_root)
            if self.guest_peer.recovered_captures:
                self.window.flash_message(
                    "WebJam recovered unfinished local audio from an earlier "
                    "session. Open Studio to review it.",
                    ms=9000,
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not configure private recording transfer")
            self.guest_peer = None

    def _stop_session_peer(self) -> None:
        guest = getattr(self, "guest_peer", None)
        if guest is not None:
            try:
                guest.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Guest recording transfer cleanup failed")
        self.guest_peer = None
        host = getattr(self, "host_peer", None)
        if host is not None:
            try:
                host.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Host recording service cleanup failed")
        self._host_peer_warning = ""

    def _on_peer_take_updated(
        self,
        _take_id: str,
        take_dir: Path,
        attached_new_media: bool,
    ) -> None:
        """Reveal late verified transfers in the open Studio immediately."""

        def refresh() -> None:
            if self._shutdown:
                return
            self.window.recording_studio.refresh_take(take_dir)
            if attached_new_media:
                self.window.flash_message(
                    "A bandmate's Local Original arrived and is now visible in Studio.",
                    ms=7000,
                )

        self._ui_invoker.invoke(refresh)

    def _on_guest_originals_changed(self, path: Path) -> None:
        """Keep one safe Finder action current as guest originals arrive."""

        def refresh() -> None:
            if self._shutdown:
                return
            self.window.recording_studio.set_local_originals_directory(path)

        self._ui_invoker.invoke(refresh)

    def _sync_local_originals_action(self) -> None:
        takes_root = Path(
            self.settings.takes_directory
            or str(Path.home() / "Music" / "WebJam Takes")
        ).expanduser()
        self.window.recording_studio.set_local_originals_directory(
            takes_root / "WebJam Local Originals"
        )

    def _ensure_host_peer(self, address: str) -> bool:
        if not bool(getattr(self.settings, "host_server_enabled", False)):
            self._host_peer_warning = ""
            return False
        if self.host_peer.active:
            self._host_peer_warning = ""
            return True
        try:
            self.host_peer.start(
                address,
                takes_root=(
                    self.settings.takes_directory
                    or str(Path.home() / "Music" / "WebJam Takes")
                ),
                installation_path=default_installation_identity_path(
                    self.settings.config_file
                ),
                display_name=self.settings.musician_name,
            )
            self._host_peer_warning = ""
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not start private recording service")
            self._host_peer_warning = (
                "Bandmates can still join and play. Automatic Local Originals "
                "are unavailable, so use the band take or have each musician "
                "record separately."
            )
            return False

    def peer_participant_id_for_channel(self, channel_id: int) -> str:
        if self.host_peer.active:
            return self.host_peer.participant_id_for_channel(channel_id) or ""
        if self.guest_peer is not None:
            local = self.guest_peer.participant_id
            participant = self.participants.get(int(channel_id))
            if local and participant is not None and participant.is_local:
                return local
        return ""

    def signal_peer_recording_started(self, take_id: str) -> None:
        if not self.host_peer.active:
            return
        try:
            self.host_peer.begin_take(
                take_id,
                started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not publish confirmed recording start")

    def signal_peer_recording_stopped(
        self, take_id: str, *, needs_attention: bool = False, message: str = ""
    ) -> None:
        if not self.host_peer.active:
            return
        try:
            self.host_peer.finish_take(
                take_id,
                stopped_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                needs_attention=needs_attention,
                message=message,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not publish confirmed recording stop")

    def _confirm_close(self) -> bool:
        """Never let a live jam disappear from a close-button accident."""
        studio = getattr(self.window, "recording_studio", None)
        if bool(getattr(studio, "export_in_progress", False)):
            QMessageBox.information(
                self.window,
                "Logic export still running",
                "Wait for ‘Logic export ready’ before quitting WebJam. "
                "Your original take is safe.",
            )
            return False
        recording_was_active = self.recording.is_recording_active
        take_in_progress = bool(
            getattr(self.recording, "take_in_progress", recording_was_active)
        )
        hosting_owned = bool(
            getattr(self.settings, "host_server_enabled", False)
            and getattr(self.bridge, "hosted_server_owned", lambda: False)()
        )
        if take_in_progress and hosting_owned:
            QMessageBox.information(
                self.window,
                "Finish the take first",
                (
                    "Press Stop Rec, then wait for ‘Take saved’ before quitting WebJam. "
                    if recording_was_active
                    else "Wait for ‘Take saved’ before quitting WebJam. "
                )
                + "This keeps every musician's track complete and verified.",
            )
            return False
        if not self.recording.confirm_quit():
            return False
        if recording_was_active:
            # The recording dialog already explained the full shutdown impact.
            return True
        active = self._is_jamulus_running() or self.bridge.hosted_server_alive()
        if not active:
            return True
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        title = "End jam and quit?" if hosting else "Leave jam and quit?"
        body = (
            "Quitting WebJam ends this jam for every connected musician."
            if hosting
            else "Quitting WebJam disconnects you; the band can keep playing."
        )
        reply = QMessageBox.question(
            self.window,
            title,
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # Companion API (optional localhost bridge for DAWs/editors/scripts)
    # ------------------------------------------------------------------
    def start_companion_api(self) -> bool:
        """Start the localhost companion API if enabled (best-effort).

        Called from the app bootstrap, not __init__, so unit tests that build a
        controller don't bind a real port.  Returns True if it started.
        """
        if not self.settings.companion_api_enabled:
            LOGGER.info("Companion API disabled in settings — not starting")
            return False
        try:
            started = self.api_bridge.start()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Companion API failed to start")
            return False
        if started:
            LOGGER.info(
                "Companion API on http://127.0.0.1:%d", self.settings.companion_api_port
            )
        else:
            LOGGER.info("Companion API not started (fastapi/uvicorn missing or port busy)")
        return started

    @property
    def _jamulus_connected(self) -> bool:
        return self.audio.connected

    @_jamulus_connected.setter
    def _jamulus_connected(self, value: bool) -> None:
        self.audio.connected = value

    def _snapshot_participants(self) -> list:
        """Copy the participants list; retries the copy if the UI thread
        mutates the dict mid-iteration (the companion API reads from its own
        uvicorn thread). Returns a list of ParticipantPresentation."""
        for _ in range(5):
            try:
                return list(self.participants.values())
            except RuntimeError:
                continue  # "dict changed size during iteration" — retry
        return []

    def _companion_get_participants(self) -> list[dict]:
        """Snapshot of the current mixer participants for the companion API."""
        out: list[dict] = []
        for p in self._snapshot_participants():
            out.append({
                "channel_id": p.channel_id,
                "name": p.name,
                "fader_level": p.fader_level,
                "pan": getattr(p, "pan", 50),
                "muted": bool(p.muted),
                "solo": bool(p.solo),
                "is_local": bool(getattr(p, "is_local", False)),
            })
        return out

    def _companion_get_diagnostics(self) -> dict:
        """Non-sensitive session state for the companion API (no secrets)."""
        return {
            "jamulus_state": str(self.bridge.jamulus_state),
            "webex_state": str(self.bridge.webex_state),
            "jamulus_connected": str(self._jamulus_connected),
            "participant_count": str(len(self._snapshot_participants())),
            "jamulus_server": f"{self.settings.jamulus_server}:{self.settings.jamulus_port}",
            "session_health": self.session_health.to_public_dict(),
        }

    def _attach_jamulus_callbacks(self) -> None:
        """Attach UI callbacks to the current JamulusController instance."""
        self.jamulus.chat_callback = self._on_jamulus_chat
        self.jamulus.recorder_state_callback = self._on_recorder_state

    # ------------------------------------------------------------------
    # Initial wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        strip = self.window.session_strip
        strip.mode_changed.connect(self._on_mode_changed)
        strip.session_title_changed.connect(self._on_title_changed)
        strip.launch_audio_requested.connect(self._on_launch_audio)
        strip.join_video_requested.connect(self._on_join_video)
        strip.mute_self_requested.connect(self._on_mute_self)
        strip.practice_requested.connect(self._on_practice_requested)
        strip.record_requested.connect(self._on_record_requested)
        strip.ready_check_requested.connect(self._on_ready_check)
        strip.invite_requested.connect(self._copy_band_invite)
        strip.tool_requested.connect(self._on_rail_view_changed)
        self.window.session_hud.invite_requested.connect(self._copy_band_invite)
        self.window.session_hud.retry_requested.connect(self._retry_session)
        # Both launch affordances share URL validation and truthful state.
        self.window.webex_embed.fallback_button().clicked.connect(
            self._on_join_video
        )
        self.window.close_requested.connect(self.shutdown)
        self.window.confirm_close = self._confirm_close
        # Settings shortcut (Ctrl+,) and side-rail Settings button → wizard
        self.window._settings_shortcut.activated.connect(self._open_settings_wizard)
        self.window.side_rail.view_changed.connect(self._on_rail_view_changed)

        # Participant grid re-emits card signals — connect once here
        grid = self.window.participant_grid
        grid.fader_changed.connect(self._on_fader_changed)
        grid.mute_toggled.connect(self._on_mute_toggled)
        grid.solo_toggled.connect(self._on_solo_toggled)
        grid.ready_check_requested.connect(self._on_ready_check)
        grid.start_audio_requested.connect(self._on_launch_audio)
        grid.practice_requested.connect(self._on_practice_requested)
        grid.microphone_settings_requested.connect(
            self._open_microphone_settings
        )

        # Save/Load mix shortcuts
        self.window._save_mix_shortcut.activated.connect(self._on_save_mix)
        self.window._load_mix_shortcut.activated.connect(self._on_load_mix)
        # Save Mix As... / Load Mix... shortcuts (multi-slot named mixes)
        self.window._save_mix_as_shortcut.activated.connect(self._on_save_mix_as)
        self.window._load_mix_from_shortcut.activated.connect(self._on_load_mix_from)
        # Mute all shortcut
        self.window._mute_all_shortcut.activated.connect(self._on_mute_all)
        # Mute-self shortcut
        self.window._mute_self_shortcut.activated.connect(self._on_mute_self)
        # Practice mode shortcut (Ctrl+P)
        self.window._practice_shortcut.activated.connect(self._on_practice_requested)
        # Diagnostics export shortcut (Ctrl+Shift+D)
        self.window._diagnostics_shortcut.activated.connect(self._on_export_diagnostics)
        self.window._ready_check_shortcut.activated.connect(self._on_ready_check)
        self.window.session_canvas.chat_submitted.connect(self._on_chat_submitted)
        self.window.session_canvas.notes_changed.connect(
            self._schedule_session_pulse_refresh
        )
        self.window.session_canvas.brief_export_requested.connect(
            self._refresh_session_pulse
        )
        studio = self.window.recording_studio
        studio.record_requested.connect(self._on_record_requested)
        studio.return_live_requested.connect(
            lambda: self.window.side_rail.trigger("stage")
        )
        studio.live_fader_changed.connect(self._on_fader_changed)
        studio.live_mute_toggled.connect(self._on_mute_toggled)
        studio.live_solo_toggled.connect(self._on_solo_toggled)
        studio.output_device_changed.connect(self._save_take_playback_output)
        studio.recording_setup_requested.connect(self._open_recording_setup)
        # Reset all faders shortcut (Ctrl+Shift+R)
        self.window._reset_faders_shortcut.activated.connect(self._on_reset_all_faders)

    def _bootstrap_ui(self) -> None:
        self.participants.clear()
        self._push_participants_to_grid()
        self.window.participant_grid.set_session_state(
            SessionUiState.idle(
                server=self.bridge.effective_server(),
                hosting=bool(getattr(self.settings, "host_server_enabled", False)),
            )
        )

        mode = get_mode_by_key_or_default(
            self.window.session_strip.current_mode_key() or "music_jam"
        )
        self._apply_mode(mode)
        self.window.set_status_audio("Ready to launch")
        self.window.set_status_video("Not opened")
        self.window.set_status_latency("Not connected")
        self.window.set_status_routing("")
        self.session_health.reset_live_truth()
        self.session_health.mark_process(self.bridge.jamulus_state)
        self.window.session_strip.reset_session_clock()
        # Start the global meter decay tick (continuous; one timer for the
        # whole grid instead of one per LevelMeter).
        self._meter_tick_timer.start()
        self.window.session_strip.set_webex_audio_mode(self._webex_audio_mode())
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        self.window.session_strip.set_talkback_available(False)
        self.window.session_strip.set_tools_enabled(True)
        self.window.webex_embed.set_audio_mode(self._webex_audio_mode())
        self.window.recording_studio.set_takes_directory(
            self.settings.takes_directory
        )
        self._sync_local_originals_action()
        self.window.recording_studio.set_output_device(
            self.settings.take_playback_output_device
        )
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        # Recording, video, talkback, notes, and settings live behind Session
        # Tools.  The live header keeps only the one action needed now.
        self.window.session_strip.set_recording_available(False)
        self.window.session_strip.set_invite_available(False)
        self.window.recording_studio.set_can_record(
            hosting,
            "The host controls take start and stop. Local Originals are optional in Recording Setup."
            if not hosting else "",
        )
        self._load_notes()
        self._load_session_title()
        self._refresh_session_pulse()
        self._update_session_hud()

    def _push_participants_to_grid(self) -> None:
        self.window.participant_grid.set_participants(self.participants.values())
        self._sync_self_mute_button()
        self._refresh_session_pulse()

    def _sync_self_mute_button(self) -> None:
        """Render global transmit truth, never the local mixer-card mute."""
        self.window.session_strip.set_webex_audio_mode(self._webex_audio_mode())
        talkback_active = (
            self._webex_audio_mode() == "talkback"
            and self._talk_break_intended
            and self._self_transmit_muted
        )
        shown_muted = self._self_transmit_muted
        self.window.session_strip.set_self_muted(shown_muted)
        self.window.webex_embed.set_talk_break_active(talkback_active)

    def _webex_audio_mode(self) -> str:
        mode = getattr(self.settings, "webex_audio_mode", "talkback")
        return mode if mode in {"talkback", "video_only", "audience_bridge"} else "talkback"

    # ------------------------------------------------------------------
    # Real Jamulus participant callback (called from background thread)
    # ------------------------------------------------------------------
    def _on_jamulus_participants(self, jamulus_participants: list) -> None:
        """Receive live participant list from JamulusController — runs on a worker thread."""
        self._ui_invoker.invoke(lambda: self._apply_jamulus_participants(jamulus_participants))

    def _on_ready_check(self) -> None:
        """F2 — open the guided Band Check without disrupting a live jam."""
        self._open_band_check()

    def _open_band_check(self, *, start_session_when_ready: bool = False) -> None:
        """Open Band Check; optionally make it the unverified-start gate."""
        existing = getattr(self, "_ready_check_dialog", None)
        if existing is not None and existing.isVisible():
            if getattr(existing, "_mode", None) is not None:
                existing._refresh_live_observations()
            existing.raise_()
            existing.activateWindow()
            return
        from core.band_check import BandCheckMode
        from webjam_qt.windows.ready_check import BandCheckDialog

        live = self._is_jamulus_running() or self.bridge.hosted_server_alive()
        dialog = BandCheckDialog(
            lambda: self.settings,
            parent=self.window,
            mode=(
                BandCheckMode.LIVE_OBSERVE
                if live
                else BandCheckMode.PRE_SESSION
            ),
            observations_provider=self._band_check_observations,
            host_server_service=self.bridge,
            start_session_when_ready=start_session_when_ready,
        )
        dialog.settings_requested.connect(self._open_settings_wizard)
        dialog.practice_requested.connect(self._on_practice_requested)
        dialog.support_requested.connect(self._on_save_support_bundle)
        if start_session_when_ready:
            dialog.session_start_requested.connect(self._start_after_band_check)
        dialog.finished.connect(
            lambda _result: setattr(self, "_ready_check_dialog", None)
        )
        self._ready_check_dialog = dialog
        dialog.show()

    def _start_after_band_check(self) -> None:
        if not self._is_jamulus_running():
            self._on_launch_audio()

    def start_session_or_band_check(self) -> None:
        """Reuse a matching verification or gate startup with Band Check.

        Signature probing runs off the UI thread and never opens an audio
        stream. A missing, corrupt, failed, or changed verification fails
        closed into the guided check.
        """

        settings = self.settings
        self.window.session_hud.set_state(
            "Checking this setup…",
            "WebJam is confirming whether your verified audio setup changed.",
        )

        def worker() -> None:
            verified = False
            try:
                from core.band_check import (
                    build_verification_signature,
                    load_verification,
                    verification_path,
                )
                from webjam_qt import __version__

                signature = build_verification_signature(
                    settings,
                    app_version=__version__,
                )
                saved = load_verification(verification_path(settings))
                verified = bool(saved and saved.matches(signature))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Band Check verification could not be inspected")
            def deliver() -> None:
                if getattr(self, "_shutdown", False):
                    return
                if verified:
                    self._on_launch_audio()
                else:
                    self._open_band_check(start_session_when_ready=True)

            try:
                self._ui_invoker.invoke(deliver)
            except RuntimeError:
                LOGGER.debug("Startup Band Check finished after Qt shutdown")

        threading.Thread(
            target=worker,
            daemon=True,
            name="band-check-startup",
        ).start()

    def _band_check_observations(self):
        """Return read-only evidence; never start, stop, or restart a service."""
        from core.band_check import BandCheckObservations

        rpc = getattr(self.jamulus, "rpc_client", None)
        rpc_available = bool(getattr(rpc, "available", False))
        responsive = rpc_available
        if rpc_available:
            try:
                age = rpc.last_activity_age()
                if age is not None:
                    responsive = float(age) <= self._RPC_HANG_THRESHOLD_S
            except Exception:  # noqa: BLE001
                responsive = False
        meter_active = False
        meter_rms = 0.0
        try:
            diagnostics = self.jamulus.audio_engine.diagnostics()
            meter_active = bool(getattr(diagnostics, "active", False))
            meter_rms = float(self.jamulus.audio_engine.get_level(-1))
        except Exception:  # noqa: BLE001
            pass
        peer_connected = any(
            not self._is_local_participant(person)
            for person in self.participants.values()
        )
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        return BandCheckObservations(
            music_engine_running=self._is_jamulus_running(),
            music_engine_responsive=responsive,
            band_server_running=(
                self.bridge.hosted_server_alive() if hosting else None
            ),
            recorder_ready=(
                rpc_available and self.bridge.hosted_server_alive()
                if hosting
                else None
            ),
            production_local_signal=bool(self._local_audio_seen),
            production_remote_signal=bool(self._remote_audio_seen),
            peer_connected=peer_connected,
            local_meter_active=meter_active,
            local_meter_rms=meter_rms,
            # The existing local meter exposes RMS only. Do not invent a peak
            # or clipping result from it during LIVE_OBSERVE.
            local_meter_peak=meter_rms,
            local_meter_clipped=False,
        )

    def _on_chat_submitted(self, text: str) -> None:
        """User sent a chat message from the canvas box — send to the band and
        echo it locally so the sender sees their own message."""
        text = (text or "").strip()
        if not text:
            return
        self.jamulus.send_chat(text)
        self.window.session_canvas.append_line(f"You: {text}")

    def _on_recorder_state(self, recording: bool, raw_state: int) -> None:
        """Server recorder state changed (arrives on the RPC reader thread)."""
        self._ui_invoker.invoke(lambda: self._apply_recorder_state(recording))

    def _apply_recorder_state(self, recording: bool) -> None:
        self.recording.on_server_state(recording)

    def _on_jamulus_chat(self, text: str) -> None:
        """Incoming band chat (arrives on the RPC reader thread).

        Jamulus chat text can contain HTML markup (sender/time formatting);
        strip it to plain text and append it to the shared canvas so the whole
        band's conversation lives in the session record.
        """
        import re
        plain = re.sub(r"<[^>]+>", "", text or "").strip()
        if not plain:
            return
        self._ui_invoker.invoke(
            lambda: self.window.session_canvas.append_line(plain)
        )

    def _apply_jamulus_participants(self, jamulus_participants: list) -> None:
        """Update the participant grid on the UI thread from real Jamulus data."""
        was_connected = self._jamulus_connected
        if not jamulus_participants and was_connected and self._self_transmit_muted:
            # A reconnect loses proof of the Jamulus client's transmit state
            # in every Webex mode. Preserve any Talk Break intent, but never
            # render the send as muted until the new RPC session acknowledges
            # setMuted.
            self._self_transmit_muted = False
            self._sync_self_mute_button()
        self.audio.apply_participants(jamulus_participants)
        # Bind only this process's authenticated local participant. The host
        # resolves remote channels from each joiner's signed presence update,
        # so duplicate or renamed display names never become identity keys.
        for person in jamulus_participants:
            if not self._is_local_participant(person):
                continue
            if self.host_peer.active:
                try:
                    self.host_peer.bind_host_presence(
                        int(person.channel_id),
                        str(person.name or self.settings.musician_name),
                        capture_enabled=bool(self.settings.local_capture_enabled),
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not bind host participant presence")
            if self.guest_peer is not None:
                self.guest_peer.observe_presence(
                    int(person.channel_id),
                    str(person.name or self.settings.musician_name),
                )
        for channel_id, presentation in self.participants.items():
            durable = self.peer_participant_id_for_channel(channel_id)
            if durable:
                presentation.participant_id = durable
        self.window.recording_studio.set_live_participants(
            self.participants.values()
        )
        self._update_session_hud()
        if self._jamulus_connected and self._talk_break_intended:
            self._reapply_talk_break_after_reconnect()

    @staticmethod
    def _is_local_participant(person) -> bool:
        """Return one consistent local-person answer during RPC startup."""
        # Modern transport objects carry explicit truth. Only old fixtures or
        # extensions without the field use the historical channel-0 fallback;
        # treating every remote channel 0 as local would mask a failed host
        # connection when guests remain on the server.
        if hasattr(person, "is_local"):
            return bool(getattr(person, "is_local"))
        return getattr(person, "channel_id", -1) == 0

    @staticmethod
    def _profile_label(value) -> str:
        """Hide Jamulus's empty-profile sentinel strings from musicians."""
        text = str(value or "").strip()
        if text.lower() in {"none", "null", "unknown", "n/a", "-"}:
            return ""
        return text

    @staticmethod
    def _role_label(jp) -> str:
        bits: list[str] = []
        # Use the same classification as readiness and tile presentation.
        if ApplicationController._is_local_participant(jp):
            bits.append("You")
        instrument = ApplicationController._profile_label(
            getattr(jp, "instrument", "")
        )
        if instrument:
            bits.append(instrument.title())
        if not bits:
            bits.append("Musician")
        # Skill badge from the musician's Jamulus profile (stage view v2).
        skill = ApplicationController._profile_label(
            getattr(jp, "skill_level", "")
        )
        if skill:
            bits.append(skill.title())
        return " · ".join(bits)

    # ------------------------------------------------------------------
    # Level polling — real audio engine values
    # ------------------------------------------------------------------
    def _poll_levels(self) -> None:
        """Called every 100 ms; pushes audio engine levels to participant grid."""
        try:
            diag = self.jamulus.audio_engine.diagnostics()
            source = getattr(diag, "backend", "unknown")
        except Exception:  # noqa: BLE001
            source = "unknown"
        self.session_health.mark_levels(source)
        studio_levels: dict[int, float] = {}
        truth_changed = False
        engine = self.jamulus.audio_engine
        for channel_id, person in self.participants.items():
            has_channel_level = engine.has_level_override(channel_id)
            level = engine.get_level(channel_id)
            is_local = self._is_local_participant(person)
            if is_local and not has_channel_level:
                # The hardware stream is local input truth only. Never paint a
                # musician's microphone level onto every remote participant.
                # It may still differ from the input selected by Jamulus, so
                # show it on the local card without promoting session readiness.
                level = engine.get_level(-1)
            self.window.participant_grid.update_level(channel_id, level)
            studio_levels[channel_id] = level
            if level > 0.01 and has_channel_level:
                if is_local and not self._local_audio_seen:
                    self._local_audio_seen = True
                    truth_changed = True
                elif not is_local and not self._remote_audio_seen:
                    self._remote_audio_seen = True
                    truth_changed = True
        self.window.recording_studio.set_live_levels(studio_levels)
        if truth_changed:
            self._update_session_hud()

    def _connected_audio_detail(self, count: int) -> str:
        prefix = f"{count} musician{'s' if count != 1 else ''} connected"
        if self._local_audio_seen and self._remote_audio_seen:
            return f"{prefix} · Your input and band audio are detected."
        if self._local_audio_seen:
            return f"{prefix} · Your input is detected."
        if self._remote_audio_seen:
            return f"{prefix} · Band audio detected; play a note to check your input."
        return f"{prefix} · Play a note to check your input."

    # ------------------------------------------------------------------
    # Session strip handlers
    # ------------------------------------------------------------------
    def _on_mode_changed(self, mode_key: str) -> None:
        # Persist the mode change immediately so a crash before clean
        # shutdown still saves the user's preference.
        self._save_session_title()
        mode = get_mode_by_key_or_default(mode_key)
        self._apply_mode(mode)
        self._refresh_session_pulse()
        self.window.flash_message(f"Switched to {mode.label}")

    def _on_title_changed(self, title: str) -> None:
        # Session titles are user content. Persist them locally, but never put
        # them into logs that may later be included in a support bundle.
        LOGGER.info("Session title updated")
        # Persist immediately so a crash before clean shutdown doesn't lose it
        self._save_session_title()
        self._refresh_session_pulse()

    def _apply_mode(self, mode) -> None:
        # Modes remain as compatibility metadata, but the hidden selector
        # must not inject infrastructure-heavy instructions into the simple
        # session surface.
        LOGGER.debug("Session mode active: %s", mode.label)

    def _on_launch_audio(self) -> None:
        """Toggle handler — launches Jamulus if stopped, stops it if running."""
        self.audio.on_launch_toggle()

    def _on_record_requested(self) -> None:
        """Compatibility entry point; RecordingCoordinator owns the lifecycle."""
        self.recording.on_record_requested()

    def _copy_band_invite(self) -> None:
        """Copy one complete invitation; never make a musician parse it."""
        from PySide6.QtWidgets import QApplication
        invite_url = self._current_invite_url()
        if not invite_url:
            self._update_session_hud()
            self.window.flash_message(
                "Connect this Mac to Wi-Fi, then try again.",
                ms=6000,
            )
            return
        QApplication.clipboard().setText(invite_url)
        if self._host_peer_warning:
            # The legacy invitation is intentionally still usable, but the
            # host must never miss why automatic originals are unavailable.
            self._update_session_hud()
        self.window.flash_message(
            "Invite link copied — send it to your bandmate.",
            ms=7000,
        )

    def accept_invite_url(self, value: str) -> bool:
        """Join an OS-delivered invite, including while WebJam is open."""
        from core.network_invite import InviteLinkError, parse_invite_link

        try:
            invite = parse_invite_link(value)
        except InviteLinkError as exc:
            self.window.flash_message(str(exc), ms=6000)
            return False

        busy = bool(
            self._is_jamulus_running() or self.bridge.hosted_server_alive()
        )
        if (
            busy
            and bool(
                getattr(
                    self.recording,
                    "take_in_progress",
                    self.recording.is_recording_active,
                )
            )
            and self.bridge.hosted_server_owned()
        ):
            QMessageBox.information(
                self.window,
                "Finish the take first",
                "Stop the recording if it is still running, then wait for ‘Take "
                "saved’ before joining another jam. Your current tracks will stay "
                "protected.",
            )
            return False
        if busy:
            reply = QMessageBox.question(
                self.window,
                "Join this jam?",
                "WebJam will safely end your current jam, then join the new one.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
            self.audio.stopping = True
            self.window.session_strip.set_audio_state("Switching…", enabled=False)

        self.window.session_hud.set_state(
            "Joining your jam…",
            "WebJam is switching the band connection safely."
            if busy else "WebJam is connecting your music.",
        )

        def _apply_and_launch() -> None:
            from core.settings import load_settings, save_settings
            from webjam_qt.windows.launch_dialog import apply_join_invite

            old_settings = self.settings
            settings_path = self.settings.config_file
            new_settings = load_settings(settings_path)
            apply_join_invite(new_settings, invite)
            try:
                save_settings(new_settings)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not save incoming invitation")
                self.window.flash_message(
                    "WebJam couldn't use that invite yet. Try opening it again.",
                    ms=7000,
                )
                return
            self.settings = load_settings(settings_path)
            if busy:
                # The worker has finalized the old host recording and stopped
                # its services. Clear local recorder/Studio truth only now;
                # doing this before the RPC stop can race and truncate a take.
                self.recording.on_audio_session_stopped()
                self.window.session_strip.reset_session_clock()
            self._reconfigure_services_after_settings(old_settings)
            if bool(getattr(invite, "peer_enabled", False)):
                self._configure_guest_peer(invite)
                if self.guest_peer is not None:
                    self.guest_peer.start()
            else:
                self._stop_session_peer()
            self.window.session_strip.set_session_title(invite.session_name)
            self._save_session_title()
            self.window.recording_studio.set_takes_directory(
                self.settings.takes_directory
            )
            self._sync_local_originals_action()
            self.window.recording_studio.set_output_device(
                self.settings.take_playback_output_device
            )
            self.window.recording_studio.set_can_record(
                False,
                "The host controls take start and stop. Local Originals are optional in Recording Setup.",
            )
            self.window.session_strip.set_recording_available(False)
            self.window.session_strip.set_invite_available(False)
            self.audio.connected = False
            self.audio.stopping = False
            self.audio.ended_by_user = False
            self.audio.reset_to_idle()
            self._on_launch_audio()

        if not busy:
            _apply_and_launch()
            return True

        def _switch_worker() -> None:
            cleanup_ok = True
            try:
                if self.bridge.hosted_server_owned():
                    cleanup_ok = bool(
                        self.recording.stop_server_recording_for_shutdown()
                    )
                if not cleanup_ok:
                    raise RuntimeError(
                        "Hosted recording finalization was not confirmed"
                    )
                cleanup_ok = bool(self.bridge.stop_jamulus()) and cleanup_ok
                if self.bridge.hosted_server_alive():
                    cleanup_ok = bool(self.bridge.stop_hosted_server()) and cleanup_ok
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not safely leave the current jam")
                cleanup_ok = False
            if cleanup_ok:
                self._ui_invoker.invoke(_apply_and_launch)
                return

            def _show_switch_failure() -> None:
                self.audio.stopping = False
                self.audio.ended_by_user = False
                self.window.participant_grid.set_session_state(
                    SessionUiState.stop_failed()
                )
                self.window.session_hud.set_state(
                    "WebJam couldn’t switch jams safely",
                    "Close WebJam, then open the new invitation again.",
                )
                self.window.session_strip.set_audio_state(
                    "Close WebJam", enabled=False
                )

            self._ui_invoker.invoke(_show_switch_failure)

        threading.Thread(
            target=_switch_worker,
            daemon=True,
            name="webjam-invite-switch",
        ).start()
        return True

    def _retry_session(self) -> None:
        if self._is_jamulus_running():
            # A running host may only be missing its Wi-Fi address. Re-evaluate
            # that truth in place instead of pretending the audio must restart.
            self._update_session_hud()
            if self._current_invite_url():
                self.window.flash_message("The invite is ready to copy.")
            else:
                self.window.flash_message(
                    "WebJam still can’t see the band network. Check Wi-Fi and try again."
                )
            return
        self._on_launch_audio()

    def _on_connection_timeout(self) -> None:
        """Turn an endless spinner into one plain recovery action."""
        if self._jamulus_connected or not self.bridge.jamulus_launch_intended:
            return
        self.audio.connection_timed_out = True
        self.window.participant_grid.set_session_state(
            self._connection_failure_state()
        )
        self.window.session_hud.set_state(
            "Something needs attention",
            "WebJam is getting ready to try again.",
        )
        self.window.session_strip.set_tools_enabled(True)
        threading.Thread(
            target=self.bridge.stop_jamulus,
            daemon=True,
            name="webjam-connect-timeout",
        ).start()

    def _connection_failure_state(self) -> SessionUiState:
        if bool(getattr(self.settings, "host_server_enabled", False)):
            return SessionUiState.host_start_failed()
        return SessionUiState.session_unavailable()

    def _current_invite_url(self) -> str:
        """Return one private Jamulus + isolated-recording invitation."""
        if not bool(getattr(self.settings, "host_server_enabled", False)):
            return ""
        if not self.bridge.hosted_server_alive():
            return ""
        from core.network_invite import create_invite_link, local_band_address

        address = local_band_address()
        if not address:
            return ""
        try:
            if self._ensure_host_peer(address):
                return self.host_peer.invite_link(
                    host=address,
                    jamulus_port=self.settings.jamulus_port,
                    session_name=(
                        self.window.session_strip.current_title()
                        or "Band Rehearsal"
                    ),
                )
            # A legacy link remains available for older hosts/tests, but only
            # v2 carries stable enrollment and guest isolated recording.
            return create_invite_link(
                address,
                port=self.settings.jamulus_port,
                session_name=(
                    self.window.session_strip.current_title()
                    or "Band Rehearsal"
                ),
            )
        except ValueError:
            return ""

    def _update_session_hud(self) -> None:
        """Render one musician-friendly summary from real lifecycle facts."""
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        connected = bool(self._jamulus_connected)
        participants = list(self.participants.values())
        invite_url = self._current_invite_url() if hosting else ""
        self.window.session_strip.set_invite_available(bool(invite_url))
        self.window.session_strip.set_recording_available(hosting and connected)
        if self.audio.stopping:
            self.window.session_hud.set_state(
                "Ending this jam…" if hosting else "Leaving the jam…",
                (
                    "WebJam is safely finishing recordings and disconnecting everyone."
                    if hosting
                    else "WebJam is disconnecting your audio safely."
                ),
            )
            return
        if self.audio.ended_by_user:
            self.window.session_hud.set_state(
                "Jam ended" if hosting else "You left the jam",
                (
                    "Start again whenever your band is ready."
                    if hosting
                    else "The band can keep playing without you."
                ),
            )
            return
        from webjam_qt.platform_permissions import microphone_permission_status

        if (
            not connected
            and microphone_permission_status() in {"denied", "restricted"}
        ):
            self.window.session_hud.set_state(
                "Microphone access is off",
                "Open System Settings below, allow access, then return to WebJam.",
            )
            return
        if self.audio.connection_timed_out:
            self.window.session_hud.set_state(
                "Something needs attention",
                "Make sure you’re on the same Wi-Fi, then use Try Again below.",
                action_visible=False,
            )
            return
        if self.audio.recovering:
            self.window.session_hud.set_state(
                "Connection interrupted",
                "WebJam is reconnecting automatically. Your mix is safe.",
            )
            return
        if (
            not connected
            and not self.bridge.jamulus_launch_intended
            and self.bridge.jamulus_state in {"Not launched", "Not running"}
        ):
            self.window.session_hud.set_state(
                "Ready when you are",
                "WebJam will handle the connection automatically.",
            )
            return
        if hosting:
            if (
                not connected
                and self.bridge.jamulus_state
                in {"Stopped", "Launch failed", "Not found", "Port in use"}
            ):
                self.window.session_hud.set_state(
                    "Something needs attention",
                    "This Mac couldn’t join the jam. Use Try Again below.",
                    action_visible=False,
                )
                return
            server_ready = self.bridge.hosted_server_alive()
            if not server_ready:
                stopped = self.bridge.jamulus_state in {
                    "Stopped", "Launch failed", "Not found", "Port in use"
                }
                if stopped:
                    self.window.session_hud.set_state(
                        "Something needs attention",
                        "Use Try Again below to start the jam again.",
                        action_visible=False,
                    )
                else:
                    self.window.session_hud.set_state(
                        "Starting your jam…",
                        "WebJam is getting the band audio ready.",
                    )
                return
            if not invite_url:
                self.window.session_hud.set_state(
                    "Something needs attention",
                    "Connect this Mac to Wi-Fi, then try again.",
                    action_text="Try Again",
                    action_visible=True,
                    action_kind="retry",
                )
                return
            if self._host_peer_warning:
                self.window.session_hud.set_state(
                    "Automatic Local Originals are off",
                    self._host_peer_warning,
                    invite_url=invite_url,
                    action_visible=False,
                    ready=connected,
                )
                return
            bandmates = sum(
                1 for person in participants
                if not self._is_local_participant(person)
            )
            if bandmates and not connected:
                self.window.session_hud.set_state(
                    "Connecting your audio…",
                    "A bandmate is here. WebJam is reconnecting this Mac.",
                    invite_url=invite_url,
                    action_visible=False,
                )
            elif bandmates:
                total = len(participants)
                self.window.session_hud.set_state(
                    "Ready to play" if self._local_audio_seen else "Bandmate connected",
                    self._connected_audio_detail(total),
                    invite_url=invite_url,
                    action_visible=False,
                    ready=self._local_audio_seen,
                )
            else:
                detail = (
                    (
                        "Your input is detected. Invite a bandmate on the same Wi-Fi."
                        if self._local_audio_seen else
                        "Play a note to check your input, then invite your bandmate."
                    )
                    if connected else
                    "Share this link with a bandmate on the same Wi-Fi."
                )
                self.window.session_hud.set_state(
                    "Ready to share",
                    detail,
                    invite_url=invite_url,
                    action_visible=False,
                    ready=connected and self._local_audio_seen,
                )
            return

        if connected:
            count = len(participants)
            self.window.session_hud.set_state(
                "You’re ready" if self._local_audio_seen else "You’re connected",
                self._connected_audio_detail(count),
                ready=self._local_audio_seen,
            )
        elif self.bridge.jamulus_state in {
            "Stopped", "Launch failed", "Not found", "Port in use"
        }:
            self.window.session_hud.set_state(
                "Something needs attention",
                "Use Try Again below to join the jam again.",
                action_visible=False,
            )
        else:
            self.window.session_hud.set_state(
                "Joining your jam…",
                "WebJam is connecting your music.",
            )

    def _record_toggle_worker(self, target_armed: bool, secret_file: str) -> None:
        self.recording.toggle_worker(target_armed, secret_file)

    def _apply_record_toggle_result(self, armed: bool) -> None:
        self.recording.apply_toggle_result(armed)

    def _apply_record_toggle_failure(self, message: str) -> None:
        self.recording.apply_toggle_failure(message)

    def _on_practice_requested(self) -> None:
        self.audio.on_practice_requested()

    def _open_microphone_settings(self) -> None:
        """Open the only advanced surface needed to recover a TCC denial."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        opened = QDesktopServices.openUrl(
            QUrl(
                "x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_Microphone"
            )
        )
        self.window.participant_grid.set_session_state(
            SessionUiState.permission_retry()
        )
        self.window.session_hud.set_state(
            "Allow microphone access, then return",
            "When WebJam is enabled in System Settings, choose Try Again below.",
        )
        if not opened:
            self.window.flash_message(
                "Open System Settings → Privacy & Security → Microphone.",
                ms=7000,
            )

    def _stop_audio(self) -> None:
        """Confirm with the user, then stop Jamulus and reset UI state."""
        self.audio.stop()

    def _reset_to_demo_state(self) -> None:
        """Compatibility entry point: reset to truthful disconnected state."""
        self.audio.reset_to_idle()

    def _is_jamulus_running(self) -> bool:
        return self.bridge.jamulus_state in ("Running", "Already running")

    def _on_join_video(self) -> None:
        """Open the configured meeting externally without claiming join state."""
        from core.webex_url import normalize_webex_url, webex_url_error

        url = normalize_webex_url(self.settings.webex_url)
        if not url:
            self._show_actionable_error(
                "No Meeting URL",
                what_failed="No Webex meeting URL is configured.",
                likely_cause="A meeting link hasn't been entered yet.",
                next_action="Go to Settings and enter your Webex meeting link.",
            )
            return
        error = webex_url_error(url)
        if error:
            self._show_actionable_error(
                "Invalid Webex URL",
                what_failed="WebJam will not open this meeting link.",
                likely_cause=error,
                next_action="Open Settings and paste the HTTPS webex.com meeting link.",
            )
            return

        self.webex.meeting_url = url
        self.bridge.webex_state = "Opening…"
        self.window.set_status_video("Opening…")
        self.window.session_strip.set_video_state("Opening…", enabled=False)
        self.window.webex_embed.set_launch_status("Opening…")
        self.bridge.launch_webex(manual=True)

    def _is_video_active(self) -> bool:
        """Return whether an external launch is in progress or succeeded.

        This deliberately does not mean "in a meeting"; native Webex does not
        expose that truth to this local application.
        """
        return self.bridge.webex_state in ("Opening…", "Opened externally")

    def _leave_video(self) -> None:
        """Compatibility entry point; WebJam cannot close external Webex."""
        self.window.flash_message(
            "Close or leave the meeting in Webex. WebJam does not control it.",
            ms=5000,
        )

    def _on_webex_state(self, state: str) -> None:
        """Ignore obsolete embedded-meeting callbacks.

        Kept for one compatibility cycle so a late signal from an old widget
        cannot overwrite the truthful external-launch state.
        """
        LOGGER.debug("Ignoring obsolete embedded Webex state: %s", state)

    # ------------------------------------------------------------------
    # Mixer card handlers → JamulusController
    # ------------------------------------------------------------------
    def _on_fader_changed(self, channel_id: int, level: int) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.fader_level = level
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_fader_level(channel_id, level)

    def _on_mute_toggled(self, channel_id: int, muted: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.muted = muted
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_mute(channel_id, muted)

    def _on_solo_toggled(self, channel_id: int, solo: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.solo = solo
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_solo(channel_id, solo)

    # ------------------------------------------------------------------
    # BridgeService callbacks (already on UI thread via invoker)
    # ------------------------------------------------------------------
    def _set_status_banner(self, text: str, color: str | None = None) -> None:
        self.window.flash_message(text, color=color)

    def _refresh_readiness(self) -> None:
        # A launched process is not the same as a proven Jamulus session.  Keep
        # the "Running" wording out of the UI until participant/RPC truth has
        # arrived; the button can still offer Stop Audio for a live subprocess.
        jamulus_up = self.bridge.jamulus_state in ("Running", "Already running")
        terminal_states = {"Stopped", "Launch failed", "Not found", "Port in use"}
        rpc = getattr(self.jamulus, "rpc_client", None)
        self.session_health.mark_process(
            self.bridge.jamulus_state,
            rpc_available=bool(getattr(rpc, "available", False)),
        )
        audio_state = self.bridge.jamulus_state
        if jamulus_up:
            if self.bridge.practice_mode:
                audio_state = (
                    "Practice live"
                    if self._jamulus_connected
                    else "Practice starting…"
                )
            else:
                audio_state = (
                    "Connected"
                    if self._jamulus_connected
                    else "Connecting…"
                )
        else:
            self.session_health.reset_live_truth()
            if self.bridge.jamulus_state in terminal_states:
                self._connection_timer.stop()
        if getattr(self.settings, "host_server_enabled", False):
            if self.bridge.hosted_server_owned():
                server_state = "Hosting"
            elif self.bridge.hosted_server_adopted():
                server_state = "Connected"
            else:
                server_state = "not running"
            self.window.set_status_server(server_state)
        else:
            self.window.set_status_server("")
        self.audio.on_readiness_refresh(jamulus_up)
        if (
            not jamulus_up
            and self.bridge.jamulus_state in terminal_states
            and not self.audio.ended_by_user
            and not self.audio.stopping
        ):
            self.audio.recovering = False
            from webjam_qt.platform_permissions import microphone_permission_status

            state = (
                SessionUiState.permission_denied()
                if microphone_permission_status() in {"denied", "restricted"}
                else self._connection_failure_state()
            )
            self.window.participant_grid.set_session_state(
                state
            )
        # Settings and Troubleshooting remain available precisely when a
        # connection is slow or failed.
        self.window.session_strip.set_tools_enabled(True)
        self.window.set_status_audio(audio_state)
        self.window.set_status_video(self.bridge.webex_state)
        if jamulus_up:
            audio_action = (
                "End Session"
                if bool(getattr(self.settings, "host_server_enabled", False))
                else "Leave Jam"
            )
        elif self.audio.stopping:
            audio_action = (
                "Ending…"
                if bool(getattr(self.settings, "host_server_enabled", False))
                else "Leaving…"
            )
        else:
            audio_action = "Start Session"
        self.window.session_strip.set_audio_state(
            audio_action, enabled=not self.audio.stopping
        )
        if self.bridge.webex_state == "Opening…":
            webex_label, enabled = "Opening…", False
        elif self.bridge.webex_state == "Opened externally":
            webex_label, enabled = "Open Again", True
        else:
            webex_label, enabled = "Open Webex", True
        self.window.session_strip.set_video_state(webex_label, enabled=enabled)
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        self.window.session_strip.set_talkback_available(
            self.bridge.webex_state == "Opened externally"
        )
        try:
            self.window.webex_embed.set_launch_status(self.bridge.webex_state)
        except AttributeError:
            pass
        self._update_session_hud()

    def _show_actionable_error(self, title: str, *, what_failed: str,
                                likely_cause: str, next_action: str,
                                retry_callback=None, copy_text: str = "") -> None:
        from pathlib import Path
        # Keep infrastructure out of the first layer. Qt's built-in Details
        # disclosure retains the diagnosis and logs for support without
        # turning a recoverable failure into a control panel.
        log_lines = [f"  {self.settings.log_file}  (WebJam)"]
        jamulus_log = Path.home() / ".webjam_jamulus.log"
        if jamulus_log.exists():
            log_lines.append(f"  {jamulus_log}  (Jamulus output)")
        box = QMessageBox(self.window)
        box.setWindowTitle("Something needs attention")
        box.setText(what_failed)
        box.setInformativeText(next_action)
        box.setDetailedText(
            f"{title}\n\nLikely cause: {likely_cause}\n\n"
            f"Logs:\n" + "\n".join(log_lines)
        )
        box.setIcon(QMessageBox.Icon.Warning)
        retry_btn = None
        settings_btn = None
        copy_btn = None
        if retry_callback:
            retry_btn = box.addButton("Try Again", QMessageBox.ButtonRole.AcceptRole)
        elif "settings" in str(next_action).lower():
            settings_btn = box.addButton(
                "Open Settings", QMessageBox.ButtonRole.AcceptRole
            )
        elif copy_text:
            copy_btn = box.addButton(
                "Copy Meeting Link", QMessageBox.ButtonRole.ActionRole
            )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if retry_btn is not None and clicked is retry_btn:
            try:
                retry_callback()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Retry callback failed")
        elif settings_btn is not None and clicked is settings_btn:
            self._open_settings_wizard()
        elif copy_btn is not None and clicked is copy_btn:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(copy_text)

    def _show_message(self, title: str, message: str) -> None:
        QMessageBox.information(self.window, title, message)

    # ------------------------------------------------------------------
    # Auto-reconnect
    # ------------------------------------------------------------------
    def _on_reconnect_tick(self) -> None:
        """Called every 3 s; lets BridgeService retry dropped services.

        Also detects Jamulus crashes mid-session and shows a banner so the
        conductor knows something is happening (auto-reconnect is otherwise
        invisible).
        """
        # Detect Jamulus dying: launch was intended, process exists but exited.
        proc = self.bridge.jamulus_process
        if (
            self.bridge.jamulus_launch_intended
            and proc is not None
            and proc.poll() is not None
            and not self._reconnect_banner_shown
        ):
            attempts = self.bridge.jamulus_reconnect_attempts + 1
            self.window.flash_message(
                f"Band audio disconnected — reconnecting (attempt {attempts}/5)…",
                ms=5000,
            )
            self.window.set_status_audio("Reconnecting…")
            self.audio.recovering = True
            self._local_audio_seen = False
            self._remote_audio_seen = False
            self.participants.clear()
            self._push_participants_to_grid()
            self.window.participant_grid.set_session_state(
                SessionUiState.reconnecting(attempts)
            )
            self._connection_timer.start()
            self._reconnect_banner_shown = True
        elif (
            self.bridge.jamulus_state in ("Running", "Already running")
            and self._reconnect_banner_shown
            and self._jamulus_connected
        ):
            # A process restart is not success. Proven roster/RPC truth is.
            self._reconnect_banner_shown = False
            self._reconnect_gave_up = False
            self.audio.recovering = False
            self.window.flash_message("Band audio reconnected.", ms=3000)
        elif (
            self.bridge.jamulus_launch_intended
            and proc is not None
            and proc.poll() is not None
            and self.bridge.jamulus_reconnect_attempts >= 5
            and not getattr(self, "_reconnect_gave_up", False)
        ):
            # Auto-reconnect exhausted its 5 attempts and the process is still
            # dead. Tell the user once, and stop showing "Reconnecting…"
            # forever (which the crash branch above would otherwise leave up).
            self._reconnect_gave_up = True
            self.window.set_status_audio("Not connected")
            self.window.session_strip.set_audio_state("Start Session", enabled=True)
            self.window.participant_grid.set_session_state(
                SessionUiState.reconnect_failed()
            )
            self.window.flash_message(
                "Couldn't reconnect after 5 tries — press Start Session to try again.",
                ms=8000,
            )

        # Detect RPC hang: process is alive AND was previously responsive
        # (we got past _jamulus_connected=True) AND the RPC heartbeat hasn't
        # fired in a while.  Distinct from a crash (proc.poll != None) — here
        # the process is still alive but unresponsive.
        if (
            self._jamulus_connected
            and proc is not None
            and proc.poll() is None
        ):
            try:
                age = self.jamulus.rpc_client.last_activity_age()
            except AttributeError:
                age = 0.0
            if age > self._RPC_HANG_THRESHOLD_S and not self._rpc_hang_banner_shown:
                self.window.flash_message(
                    f"The music engine stopped responding ({int(age)}s of silence). "
                    "WebJam is preparing a safe retry.",
                    ms=8000,
                )
                self.window.set_status_audio("Not responding")
                self._rpc_hang_banner_shown = True
                self.audio.connected = False
                self.audio.recovering = True
                self._local_audio_seen = False
                self._remote_audio_seen = False
                self.participants.clear()
                self._push_participants_to_grid()
                self.window.participant_grid.set_session_state(
                    SessionUiState.reconnecting()
                )
                self.window.session_hud.set_state(
                    "Connection interrupted",
                    "The music engine stopped responding. WebJam is preparing a safe retry.",
                )
                self._connection_timer.start()
                try:
                    self.metrics.increment("metric_jamulus_hang_detected")
                except Exception:  # noqa: BLE001
                    LOGGER.debug("hang metric failed", exc_info=True)
            elif age <= self._RPC_HANG_THRESHOLD_S and self._rpc_hang_banner_shown:
                self._rpc_hang_banner_shown = False
                self.window.flash_message("The music engine is responding again.", ms=3000)

        self.bridge.attempt_auto_reconnects()

    def _on_token_refresh_tick(self) -> None:
        """Compatibility no-op: native Webex owns its authentication."""

    # ------------------------------------------------------------------
    # Save / Load mix (Ctrl+S / Ctrl+O)
    # ------------------------------------------------------------------
    def _on_mute_self(self) -> None:
        """Toggle only the local Jamulus send, never the Webex microphone."""
        local_channel_id: Optional[int] = None
        for cid, p in self.participants.items():
            if p.is_local:
                local_channel_id = cid
                break
        if local_channel_id is None:
            self.window.flash_message(
                "Start the session first — your music track isn't available yet.",
                ms=4000,
            )
            # Render only acknowledged global transmit state. Local mixer mute
            # and retained Talk Break intent are not proof that send is muted.
            self._sync_self_mute_button()
            self.session_health.mark_rpc_result(
                "self-mute", False, "local channel not available"
            )
            return
        if not self._jamulus_connected:
            self.window.flash_message(
                "Start the session first — this control needs your live music track.",
                ms=4000,
            )
            self._sync_self_mute_button()
            self.session_health.mark_rpc_result(
                "self-mute", False, "Jamulus session not proven"
            )
            return
        new_muted = not self._self_transmit_muted
        talkback = self._webex_audio_mode() == "talkback"

        if talkback and not new_muted:
            reply = QMessageBox.question(
                self.window,
                "Resume Music?",
                "Release Spacebar and confirm your Webex microphone is muted.\n\n"
                "Resume sending music to the band?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.window.session_strip.set_self_muted(True)
                return

        # Real self-mute: tell Jamulus to stop sending OUR audio to the band
        # (jamulusclient/setMuted).  Zeroing our own channel fader would only
        # mute us in our own monitor — the others would still hear us.
        if not self.jamulus.set_self_muted(new_muted):
            self.session_health.mark_rpc_result(
                "self-mute", False, "Jamulus RPC rejected setMuted"
            )
            self._sync_self_mute_button()
            if talkback:
                action = "Talk Break" if new_muted else "Resume Music"
            else:
                action = "Mute Music Send" if new_muted else "Unmute Music Send"
            self.window.flash_message(
                f"{action} did not reach the music engine — keep your Webex microphone "
                "muted and try again.",
                ms=6000,
            )
            return
        self._self_transmit_muted = new_muted
        self._talk_break_intended = bool(talkback and new_muted)
        self.session_health.mark_rpc_result("self-mute", True)
        self._sync_self_mute_button()
        if talkback:
            message = (
                "TALK · music send muted — hold Space in Webex to speak."
                if new_muted else
                "PLAY · music send live — keep the Webex microphone muted."
            )
        else:
            message = "Music send muted." if new_muted else "Music send live."
        self.window.flash_message(message, ms=5000)

    def _reapply_talk_break_after_reconnect(self) -> None:
        """Fail closed when a reconnect returns while Talk Break is intended."""
        if (
            not self._talk_break_intended
            or self._webex_audio_mode() != "talkback"
            or not self._jamulus_connected
            or self._self_transmit_muted
        ):
            return
        local = next((p for p in self.participants.values() if p.is_local), None)
        if local is None:
            return
        if self.jamulus.set_self_muted(True):
            self._self_transmit_muted = True
            self.session_health.mark_rpc_result("self-mute", True)
            self._sync_self_mute_button()
            self.window.flash_message(
                "Talk break restored after reconnect · music send is muted.", ms=5000
            )
            return
        self.session_health.mark_rpc_result(
            "self-mute", False, "could not restore Talk Break after reconnect"
        )
        self._self_transmit_muted = False
        self._sync_self_mute_button()
        self.window.flash_message(
            "Talk Break is not confirmed after reconnect — keep Webex muted "
            "and press Talk Break to retry.",
            ms=8000,
        )

    def _on_mute_all(self) -> None:
        """Ctrl+M — toggle mute state for every participant.

        If any channel is unmuted, mute all. If all are already muted, unmute
        all.  Applies to soloed channels too — a panic "mute everything" must
        actually silence the room, solo or not.
        """
        if not self.participants:
            return
        any_unmuted = any(not p.muted for p in self.participants.values())
        target_muted = any_unmuted  # mute all if anything is playing; unmute if all silent
        for channel_id, p in self.participants.items():
            if p.muted != target_muted:
                p.muted = target_muted
                if self._jamulus_connected:
                    self.jamulus.set_mute(channel_id, target_muted)
        self._mix_dirty = True
        # Push updated state to grid so buttons reflect the change
        self._push_participants_to_grid()
        verb = "Muted" if target_muted else "Unmuted"
        self.window.flash_message(f"{verb} all participants  ·  Ctrl+M to toggle")

    def _on_reset_all_faders(self) -> None:
        """Ctrl+Shift+R — reset every participant's fader to 0 dB (level 100).

        Asks for confirmation since it's a destructive bulk action.  The
        saved mix on disk is unchanged — user can Ctrl+O to restore.
        """
        reply = QMessageBox.question(
            self.window, "Reset all faders?",
            "Reset all faders to 0 dB?\n\nYour saved mix on disk is unchanged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for channel_id, p in self.participants.items():
            p.fader_level = 100
            if self._jamulus_connected:
                self.jamulus.set_fader_level(channel_id, 100)
        self._mix_dirty = True
        self._push_participants_to_grid()
        self.window.flash_message(
            "All faders reset to 0 dB  ·  Ctrl+O to restore your saved mix",
            ms=4000,
        )

    def _on_export_diagnostics(self) -> None:
        """Ctrl+Shift+D — build a Markdown diagnostics summary and copy it
        to the system clipboard so the user can paste it into a GitHub issue.
        """
        try:
            from PySide6.QtWidgets import QApplication

            exporter = self._diagnostics_exporter()
            summary = exporter.build_summary()
            QApplication.clipboard().setText(summary)
            self.window.flash_message(
                "Diagnostics copied to clipboard — paste into a GitHub issue at "
                "github.com/rupret007/webjam/issues",
                ms=8000,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to export diagnostics")
            self.window.flash_message(
                "Couldn't export diagnostics. See ~/.webjam.log for details.",
                ms=6000,
            )

    def _diagnostics_exporter(self):
        """Build one canonical privacy-safe artifact adapter."""
        from core.build_info import build_id
        from webjam_qt import __version__
        from webjam_qt.controllers.diagnostics import DiagnosticsExporter

        return DiagnosticsExporter(
            settings=self.settings,
            bridge=self.bridge,
            jamulus_controller=self.jamulus,
            window_version=__version__,
            build_id=build_id(),
            session_health=self.session_health,
            recording_coordinator=self.recording,
            metrics_service=self.metrics,
        )

    def _on_save_support_bundle(self) -> None:
        """Preview the exact sanitized artifact, then save it on confirmation."""
        try:
            from pathlib import Path

            from PySide6.QtWidgets import QFileDialog

            from webjam_qt.windows.support_bundle_preview import (
                SupportBundlePreviewDialog,
            )

            exporter = self._diagnostics_exporter()
            preview = exporter.build_preview()
            dialog = SupportBundlePreviewDialog(preview, parent=self.window)
            if dialog.exec() != SupportBundlePreviewDialog.DialogCode.Accepted:
                return
            default = Path.home() / "Documents" / "webjam_support.zip"
            selected, _filter = QFileDialog.getSaveFileName(
                self.window,
                "Save Support Bundle",
                str(default),
                "ZIP archives (*.zip)",
            )
            if not selected:
                return
            requested = Path(selected).expanduser()
            saved = exporter.save_bundle(requested.parent, requested.name)
            self.window.flash_message(
                f"Support bundle saved as {saved.name}.",
                ms=7000,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to save support bundle")
            QMessageBox.warning(
                self.window,
                "Support bundle not saved",
                "WebJam couldn't save the support bundle. Choose another folder "
                "and try again.",
            )

    # Public method names retained as a thin compatibility surface; the real
    # implementation lives in ``MixManager`` (~/.webjam_mix.json).
    def _on_save_mix(self) -> None:
        """Serialize current mixer state to ~/.webjam_mix.json."""
        # Only clear the dirty flag if the write actually succeeded.  If the
        # save failed (permissions, disk full), keeping it dirty preserves the
        # shutdown auto-save safety net so mid-session tweaks aren't lost.
        if self._mix_manager.save():
            self._mix_dirty = False

    def _on_load_mix(self) -> None:
        """Load mixer state from ~/.webjam_mix.json and apply to Jamulus."""
        self._mix_manager.load()

    def _on_save_mix_as(self) -> None:
        """Ctrl+Shift+S — open a Save dialog and write the mix to a chosen path.

        Lets users keep multiple named mixes (one per song, per band-mate
        setup, etc.) instead of overwriting the single default slot.
        """
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save Mix As...",
            str(Path.home()),
            "Mix files (*.json);;All files (*)",
        )
        if not path:
            return
        # Treat a successful explicit "Save As" as a checkpoint of the current
        # state, the same way Ctrl+S does — but only if the write succeeded.
        if self._mix_manager.save_to(Path(path)):
            self._mix_dirty = False

    def _on_load_mix_from(self) -> None:
        """Ctrl+Shift+O — open a Load dialog and apply the chosen mix file."""
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Load Mix...",
            str(Path.home()),
            "Mix files (*.json);;All files (*)",
        )
        if not path:
            return
        self._mix_manager.load_from(Path(path))

    def _restore_saved_mix(self) -> None:
        """Auto-apply ~/.webjam_mix.json when Jamulus first connects (best-effort)."""
        self._mix_manager.auto_restore()

    # ------------------------------------------------------------------
    # Settings wizard (Phase 6)
    # ------------------------------------------------------------------
    def _reconfigure_services_after_settings(self, old_settings: AppSettings) -> None:
        """Apply freshly saved settings to all long-lived integration objects."""
        self.bridge.settings = self.settings

        # Webex browser fallback controller is long-lived; keep its meeting URL
        # in sync with the settings object used by the embedded pane.
        self.webex.meeting_url = self.settings.webex_url
        self.bridge.webex_controller = self.webex
        if self._webex_audio_mode() != "talkback":
            self._talk_break_intended = False
        self.window.session_strip.set_webex_audio_mode(self._webex_audio_mode())
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        self.window.webex_embed.set_audio_mode(self._webex_audio_mode())
        self._start_routing_scan()

        # JamulusController owns RPC, its dormant legacy adapter, and the
        # audio-engine settings.
        # Reconfigure in place so BridgeService, MixManager, and tests keep the
        # same object identity while still seeing the new target.
        self.jamulus.settings = self.settings
        self.jamulus.host = (self.settings.jamulus_server or "").strip() or "127.0.0.1"
        self.jamulus.port = self.settings.jamulus_port
        self.jamulus.rpc_port = self.settings.jamulus_rpc_port
        protocol = getattr(self.jamulus, "protocol", None)
        if protocol is not None:
            protocol.host = self.jamulus.host
            protocol.port = self.jamulus.port
        audio_engine = getattr(self.jamulus, "audio_engine", None)
        if audio_engine is not None:
            audio_engine.settings = self.settings

        old_rpc_port = getattr(old_settings, "jamulus_rpc_port", None)
        if old_rpc_port != self.settings.jamulus_rpc_port:
            old_rpc = getattr(self.jamulus, "rpc_client", None)
            if old_rpc is not None:
                try:
                    old_rpc.stop()
                except Exception:  # noqa: BLE001
                    LOGGER.debug("Old Jamulus RPC client stop failed", exc_info=True)
            from core.jamulus_rpc_client import JamulusRpcClient
            self.jamulus.rpc_client = JamulusRpcClient(
                port=self.settings.jamulus_rpc_port,
                on_participants_changed=self.jamulus._on_rpc_participants,
                on_levels=self.jamulus._on_rpc_levels,
                on_chat=self.jamulus._on_rpc_chat,
                on_recorder_state=self.jamulus._on_rpc_recorder_state,
            )

        self.bridge.jamulus_controller = self.jamulus
        self._mix_manager._jamulus = self.jamulus

        api_port_changed = self.api_bridge.port != self.settings.companion_api_port
        api_enabled_changed = (
            old_settings.companion_api_enabled != self.settings.companion_api_enabled
        )
        was_api_running = bool(getattr(self.api_bridge, "_running", False))
        if api_port_changed or api_enabled_changed or not self.settings.companion_api_enabled:
            try:
                self.api_bridge.stop()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Companion API stop during settings apply failed", exc_info=True)
        self.api_bridge.port = self.settings.companion_api_port
        if self.settings.companion_api_enabled and (
            api_enabled_changed or api_port_changed or was_api_running
        ):
            self.start_companion_api()

    def _open_settings_wizard(self) -> None:
        from webjam_qt.windows.simple_settings import SimpleSettingsDialog
        # Snapshot relevant fields before the wizard so we can detect changes.
        old_settings = self.settings
        old_webex_url = self.settings.webex_url
        old_jamulus_server = (self.settings.jamulus_server, self.settings.jamulus_port)
        # In-session reopen — skip the welcome page since the user already
        # knows what WebJam is and is here to change a specific setting.
        wizard = SimpleSettingsDialog(self.settings, parent=self.window)
        def _open_band_check_from_settings() -> None:
            wizard.reject()
            QTimer.singleShot(0, self._on_ready_check)

        wizard.band_check_requested.connect(_open_band_check_from_settings)
        if wizard.exec() == SimpleSettingsDialog.DialogCode.Accepted:
            from core.settings import load_settings
            self.settings = load_settings(self.settings.config_file)
            self._reconfigure_services_after_settings(old_settings)
            self.window.recording_studio.set_takes_directory(
                self.settings.takes_directory
            )
            self._sync_local_originals_action()
            self.window.recording_studio.set_output_device(
                self.settings.take_playback_output_device
            )
            hosting = bool(getattr(self.settings, "host_server_enabled", False))
            self.window.recording_studio.set_can_record(
                hosting,
                "The host controls take start and stop. Local Originals are optional in Recording Setup."
                if not hosting else "",
            )
            self._update_session_hud()

            # Build a context-aware confirmation message so the user knows
            # whether they need to take any action for the change to apply.
            warnings: list[str] = []
            if (
                self.settings.webex_url != old_webex_url
                and self._is_video_active()
            ):
                warnings.append("Press Open Again to use the new Webex URL.")
            if (
                (self.settings.jamulus_server, self.settings.jamulus_port) != old_jamulus_server
                and self._is_jamulus_running()
            ):
                warnings.append("End and restart the session to use the new band host.")

            if warnings:
                self.window.flash_message(
                    "Settings saved. " + " ".join(warnings),
                    ms=8000,
                )
            else:
                self.window.flash_message(
                    "Settings saved — they take effect next time you start the session."
                )

    def _open_take_deck(self) -> None:
        """Compatibility entry point: reveal the integrated Studio workspace."""
        self.window.side_rail.set_active_key("takes")
        self._on_rail_view_changed("takes")

    def _open_recording_setup(self) -> None:
        """Open the focused Studio preferences without exposing RPC plumbing."""
        from webjam_qt.windows.recording_setup import RecordingSetupDialog

        dialog = RecordingSetupDialog(self.settings, parent=self.window)
        if dialog.exec() != RecordingSetupDialog.DialogCode.Accepted:
            return
        self.window.recording_studio.set_output_device(
            self.settings.take_playback_output_device
        )
        capture = bool(self.settings.local_capture_enabled)
        self.window.flash_message(
            "Recording setup saved · local originals "
            + ("will be kept for the next confirmed take." if capture else "are off."),
            ms=7000,
        )
        for participant in self.participants.values():
            if not participant.is_local:
                continue
            if self.guest_peer is not None:
                self.guest_peer.observe_presence(
                    participant.channel_id, participant.name
                )
            if self.host_peer.active:
                try:
                    self.host_peer.bind_host_presence(
                        participant.channel_id,
                        participant.name,
                        capture_enabled=capture,
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not refresh local recording preference")

    def _save_take_playback_output(self, device_name: str) -> None:
        self.settings.take_playback_output_device = str(device_name or "")
        try:
            from core.settings import save_settings
            save_settings(self.settings)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to persist Take Deck output device")
            self.window.flash_message(
                "Playback output changed for this run, but couldn't be saved.",
                ms=6000,
            )

    def _on_rail_view_changed(self, key: str) -> None:
        splitter = self.window.center_splitter
        total = sum(splitter.sizes()) or self.window.DEFAULT_WIDTH

        # Keys that represent actual view changes (persist selection)
        _CONTENT_KEYS = frozenset({"stage", "canvas", "takes"})

        if key == "diagnostics":
            self._on_ready_check()
        elif key == "settings":
            # Restore rail to the previous content view before opening wizard
            prev = getattr(self, "_last_content_key", "stage")
            self.window.side_rail.set_active_key(prev)
            self._open_settings_wizard()
        elif key in _CONTENT_KEYS:
            self._last_content_key = key
            self.window.session_strip.set_recording_available(
                key != "takes"
                and bool(getattr(self.settings, "host_server_enabled", False))
                and bool(self._jamulus_connected)
            )
            if key == "stage":
                self.window.workspace_stack.setCurrentWidget(
                    self.window.center_splitter
                )
                self.window.session_canvas.setVisible(False)
                splitter.setSizes([total, 0])
            elif key == "canvas":
                self.window.workspace_stack.setCurrentWidget(
                    self.window.center_splitter
                )
                self.window.session_canvas.setVisible(True)
                # Canvas: expand the notes panel
                splitter.setSizes([int(total * 0.28), int(total * 0.72)])
            elif key == "takes":
                self.window.recording_studio.reload()
                self.window.workspace_stack.setCurrentWidget(
                    self.window.recording_studio
                )

    # ------------------------------------------------------------------
    # Session notes persistence
    # ------------------------------------------------------------------
    # Session notes / metadata persistence
    # Public method names retained as a thin compatibility surface; the real
    # implementation lives in ``SessionPersistence`` (~/.webjam_notes.md and
    # ~/.webjam_session.json).
    def _load_notes(self) -> None:
        """Restore session notes from disk (best-effort)."""
        self._persistence._load_notes_only()

    def _save_notes(self) -> None:
        """Persist current session notes to disk (best-effort)."""
        self._persistence._save_notes_only()

    def _load_session_title(self) -> None:
        """Restore the session title and last-used mode from disk."""
        self._persistence._load_session_metadata()

    def _save_session_title(self) -> None:
        """Persist the current session title and mode to disk."""
        self._persistence._save_session_metadata()

    def _schedule_session_pulse_refresh(self, _notes: str) -> None:
        """Coalesce note edits before recalculating the local session pulse."""
        if not self._shutdown:
            self._pulse_refresh_timer.start()

    def _session_pulse_participants(self) -> list[ParticipantPresentation]:
        """Return confirmed, non-preview participants for the local pulse."""
        if not self._jamulus_connected:
            return []
        # Normal startup no longer creates previews, but retain the guard for
        # older extensions and restored in-memory fixtures.
        return [
            participant
            for participant in self._snapshot_participants()
            if not participant.role.startswith("Preview ·")
        ]

    def _refresh_session_pulse(self) -> None:
        """Rebuild the local pulse from the current UI session state."""
        try:
            pulse = build_session_pulse(
                mode_key=self.window.session_strip.current_mode_key(),
                title=self.window.session_strip.current_title(),
                notes=self.window.session_canvas.current_notes(),
                participants=self._session_pulse_participants(),
            )
            self.window.session_canvas.set_session_pulse(pulse)
        except Exception:  # noqa: BLE001
            # Never leave stale derived content beside newer raw notes. Brief
            # export then safely falls back to the notes themselves.
            self.window.session_canvas.clear_session_pulse()
            LOGGER.warning("Session pulse refresh failed", exc_info=True)

    # ------------------------------------------------------------------
    # Audio routing detection (Phase 5)
    # ------------------------------------------------------------------
    def _start_routing_scan(self) -> None:
        """Routing is automatic; keep infrastructure out of the main UI."""
        self.window.set_status_routing("")
        return

        def _scan() -> None:
            from core.audio_routing import AudioRoutingStatus, scan_loopback_devices
            try:
                status = scan_loopback_devices()
            except Exception as exc:  # noqa: BLE001
                # scan_loopback_devices() is contracted never to raise, but guard
                # anyway so an unexpected failure can't silently kill this thread
                # and leave the routing status blank forever.
                LOGGER.warning("routing scan failed: %s", exc, exc_info=True)
                status = AudioRoutingStatus(scan_error=str(exc))
            try:
                self._ui_invoker.invoke(lambda: self._apply_routing_status(status))
            except RuntimeError:
                # The Qt invoker was destroyed while we were scanning (app
                # shut down mid-scan).  Nobody is left to show the status —
                # drop it instead of dying with a traceback.
                LOGGER.debug("routing status dropped — UI already gone")

        threading.Thread(target=_scan, daemon=True, name="routing-scan").start()

    def _apply_routing_status(self, status) -> None:
        if self._webex_audio_mode() != "audience_bridge":
            self.window.set_status_routing("Not required")
            return
        if status.ok:
            label = f"{status.device_name} \u2713"
            self.window.set_status_routing(label)
            try:
                self.metrics.increment("metric_audio_device_blackhole_found")
            except Exception:  # noqa: BLE001
                LOGGER.debug("audio device metric failed", exc_info=True)
        else:
            self.window.set_status_routing("No audio device")
            self.window.flash_message(
                f"No virtual audio device found. {status.install_hint}",
                ms=8000,
            )
            try:
                self.metrics.increment("metric_audio_device_missing")
            except Exception:  # noqa: BLE001
                LOGGER.debug("audio device metric failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def mode_entries() -> list[tuple[str, str]]:
        return [(mode.key, mode.label) for mode in CREATIVE_MODES]

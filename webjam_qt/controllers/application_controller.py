"""
ApplicationController — the brain.

Owns session state and wires ConductorWindow signals to the service layer.

Participant lifecycle:
  1. On startup: show 5 demo cards (named placeholders) with animated levels
     so the UI feels alive before Jamulus connects.
  2. When ``JamulusController`` fires a participants callback (real data from
     JSON-RPC or UDP), real names replace demo names; mixer state is preserved.
  3. Level meters switch from the demo jitter to real audio engine values
     polled via ``_level_poll_timer`` every 100 ms.

Mixer signals (fader/mute/solo) route directly to ``JamulusController`` which
sends them to Jamulus via JSON-RPC (preferred) or UDP protocol (fallback).
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QMessageBox

from core.creative_modes import CREATIVE_MODES, get_mode_by_key_or_default
from core.settings import AppSettings, load_settings
from services.bridge_service import BridgeService
from storage.repository import WebJamRepository
from ui.services import MetricsService

from webjam_qt.controllers.mix_manager import MixManager
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.controllers.ui_thread import UiThreadInvoker
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.windows.conductor_window import ConductorWindow

LOGGER = logging.getLogger("webjam.qt.application_controller")

# Demo participants shown before Jamulus connects.  Names are deliberately
# labelled "Preview" so users don't mistake them for saved band-member data.
_DEMO_PARTICIPANTS = [
    ParticipantPresentation(channel_id=0, name="You",         role="Preview · You",     fader_level=100, is_local=True),
    ParticipantPresentation(channel_id=1, name="Sample 1",    role="Preview · Guitar",  fader_level=96),
    ParticipantPresentation(channel_id=2, name="Sample 2",    role="Preview · Bass",    fader_level=104),
    ParticipantPresentation(channel_id=3, name="Sample 3",    role="Preview · Vocals",  fader_level=110),
    ParticipantPresentation(channel_id=4, name="Sample 4",    role="Preview · Keys",    fader_level=88),
]


class ApplicationController(QObject):
    """Glue layer between ConductorWindow and the service layer."""

    _LEVEL_POLL_MS = 100   # how often to push meter updates to the grid
    _DEMO_TICK_MS  = 120   # demo level jitter interval
    _METER_TICK_MS = 40    # global LevelMeter decay tick (was per-meter)

    def __init__(
        self,
        window: ConductorWindow,
        settings: Optional[AppSettings] = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or load_settings()

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

        # Surface incoming band chat (from Jamulus) in the shared canvas.
        self.jamulus.chat_callback = self._on_jamulus_chat
        # Surface server recorder state (multitrack stems) in the status bar.
        self.jamulus.recorder_state_callback = self._on_recorder_state
        self._server_recording = False

        self._shutdown = False

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
        self._jamulus_connected = False

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
        # with stale demo data).
        self._mix_dirty = False

        # Timers
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(self._DEMO_TICK_MS)
        self._demo_timer.timeout.connect(self._demo_tick)

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

        # Webex guest-token refresh: 1-minute poll. The embed widget's TTL
        # is 1 hour, so checking every minute gives us plenty of margin to
        # spot the 5-minute "near expiry" window without spamming.
        self._token_refresh_timer = QTimer(self)
        self._token_refresh_timer.setInterval(60_000)
        self._token_refresh_timer.timeout.connect(self._on_token_refresh_tick)

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

        # Wire Webex token-refresh metrics into the embed widget so we can
        # measure how often long sessions actually need a refresh.
        try:
            self.window.webex_embed.on_refresh_metric = lambda key: self.metrics.increment(key)
        except AttributeError:
            pass  # older embed without the hook

        self._wire_signals()
        self._bootstrap_ui()
        self._start_routing_scan()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self._shutdown = True
        self._demo_timer.stop()
        self._level_timer.stop()
        self._reconnect_timer.stop()
        self._meter_tick_timer.stop()
        self._token_refresh_timer.stop()
        self._save_notes()
        self._save_session_title()
        # Auto-save mix if user touched anything since last save AND we were
        # connected to a real Jamulus (don't overwrite a real saved mix with
        # demo data).  Best-effort: failures are caught by _on_save_mix.
        if self._mix_dirty and self._jamulus_connected:
            try:
                self._on_save_mix()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Auto-save mix on shutdown failed")
        # Terminate the Jamulus subprocess so it doesn't outlive WebJam.
        # bridge.stop_jamulus() also calls jamulus_controller.stop() internally.
        try:
            self.bridge.stop_jamulus()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Jamulus shutdown failed")
        try:
            self.webex.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex stop failed")
        try:
            self.api_bridge.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Companion API stop failed")

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

    def _companion_get_participants(self) -> list[dict]:
        """Snapshot of the current mixer participants for the companion API."""
        out: list[dict] = []
        for p in self.participants.values():
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
            "participant_count": str(len(self.participants)),
            "jamulus_server": f"{self.settings.jamulus_server}:{self.settings.jamulus_port}",
        }

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
        # Fallback button opens Webex in the system browser when embed unavailable
        self.window.webex_embed.fallback_button().clicked.connect(
            lambda: self.bridge.launch_webex(manual=True)
        )
        self.window.close_requested.connect(self.shutdown)
        # Settings shortcut (Ctrl+,) and side-rail Settings button → wizard
        self.window._settings_shortcut.activated.connect(self._open_settings_wizard)
        self.window.side_rail.view_changed.connect(self._on_rail_view_changed)

        # Participant grid re-emits card signals — connect once here
        grid = self.window.participant_grid
        grid.fader_changed.connect(self._on_fader_changed)
        grid.mute_toggled.connect(self._on_mute_toggled)
        grid.solo_toggled.connect(self._on_solo_toggled)

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
        # Diagnostics export shortcut (Ctrl+Shift+D)
        self.window._diagnostics_shortcut.activated.connect(self._on_export_diagnostics)
        self.window._ready_check_shortcut.activated.connect(self._on_ready_check)
        self.window.session_canvas.chat_submitted.connect(self._on_chat_submitted)
        # Reset all faders shortcut (Ctrl+Shift+R)
        self.window._reset_faders_shortcut.activated.connect(self._on_reset_all_faders)

    def _bootstrap_ui(self) -> None:
        for p in _DEMO_PARTICIPANTS:
            # Copy so we don't mutate the module-level default
            self.participants[p.channel_id] = ParticipantPresentation(
                channel_id=p.channel_id,
                name=p.name,
                role=p.role,
                fader_level=p.fader_level,
                is_local=p.is_local,
            )
        self._push_participants_to_grid()

        mode = get_mode_by_key_or_default(
            self.window.session_strip.current_mode_key() or "music_jam"
        )
        self._apply_mode(mode)
        self.window.set_status_audio("Ready to launch")
        self.window.set_status_video("Ready to join")
        self.window.set_status_latency("Not connected")
        self.window.set_status_routing("scanning…")
        self.window.session_strip.start_session_clock()
        self._demo_timer.start()
        # Start the global meter decay tick (continuous; one timer for the
        # whole grid instead of one per LevelMeter).
        self._meter_tick_timer.start()
        # Start the Webex guest-token refresh poll — no-op if no guest
        # credentials configured (direct-URL mode).
        self._token_refresh_timer.start()
        self._load_notes()
        self._load_session_title()

    def _push_participants_to_grid(self) -> None:
        self.window.participant_grid.set_participants(self.participants.values())
        self._sync_self_mute_button()

    def _sync_self_mute_button(self) -> None:
        """Update the SessionStrip 'Mute Me' button to match local-user mute state."""
        for p in self.participants.values():
            if p.is_local:
                self.window.session_strip.set_self_muted(p.muted)
                return

    # ------------------------------------------------------------------
    # Real Jamulus participant callback (called from background thread)
    # ------------------------------------------------------------------
    def _on_jamulus_participants(self, jamulus_participants: list) -> None:
        """Receive live participant list from JamulusController — runs on a worker thread."""
        self._ui_invoker.invoke(lambda: self._apply_jamulus_participants(jamulus_participants))

    def _on_ready_check(self) -> None:
        """F2 — run the pre-jam Ready Check and show the report."""
        from core.preflight import run_ready_check
        from PySide6.QtWidgets import QMessageBox
        report = run_ready_check(self.settings)
        box = QMessageBox(self.window)
        box.setWindowTitle("WebJam — Ready Check")
        box.setIcon(
            QMessageBox.Icon.Information if report.all_ok else QMessageBox.Icon.Warning
        )
        box.setText(report.to_text())
        box.exec()

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
        if recording == self._server_recording:
            return  # no transition — don't re-flash
        self._server_recording = recording
        self.window.set_status_recording(recording)
        if recording:
            self.window.flash_message(
                "● Server is recording — every musician gets their own track.",
                ms=5000,
            )
        else:
            self.window.flash_message("Server recording stopped.", ms=3000)

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
        if not jamulus_participants:
            return

        if not self._jamulus_connected:
            self._jamulus_connected = True
            # Push our display name to Jamulus so the band sees a real name.
            self.jamulus.set_name(self.settings.webex_display_name)
            self._demo_timer.stop()
            # Clear demo data; real participants take over
            self.participants.clear()
            # Start polling real audio engine levels
            self._level_timer.start()
            # Restore saved mix (best-effort — silently skipped if no file)
            self._restore_saved_mix()
            # Telemetry: count sessions where we actually got real participants
            try:
                self.metrics.increment("metric_session_started")
            except Exception:  # noqa: BLE001
                LOGGER.debug("metric_session_started increment failed", exc_info=True)
            # Celebrate the moment — first connection deserves a flash
            self.window.flash_message(
                f"Connected to {self.settings.jamulus_server}. Waiting for band members…",
                ms=4000,
            )

        # Update participant count in status bar.  When the user is alone on
        # the server (only their own channel), say so explicitly — "1
        # participant" is technically correct but doesn't convey "waiting".
        n = len(jamulus_participants)
        if n == 1:
            self.window.set_status_latency("1 participant · waiting for others")
        else:
            self.window.set_status_latency(f"{n} participants")

        incoming_ids = {p.channel_id for p in jamulus_participants}

        # Remove participants that left
        for cid in list(self.participants.keys()):
            if cid not in incoming_ids:
                del self.participants[cid]

        # Upsert — preserve existing mixer state (fader/mute/solo)
        for jp in jamulus_participants:
            existing = self.participants.get(jp.channel_id)
            if existing is None:
                self.participants[jp.channel_id] = ParticipantPresentation(
                    channel_id=jp.channel_id,
                    name=jp.name,
                    role=self._role_label(jp),
                    fader_level=jp.fader_level,
                    muted=jp.muted,
                    solo=jp.solo,
                    is_connected=jp.is_connected,
                    is_local=getattr(jp, "is_local", jp.channel_id == 0),
                )
            else:
                # Preserve fader/mute/solo the user set in WebJam
                existing.name = jp.name
                existing.is_connected = jp.is_connected
                existing.is_local = getattr(jp, "is_local", jp.channel_id == 0)
                # Refresh role if instrument changed (e.g. mid-session update)
                new_role = self._role_label(jp)
                if new_role != existing.role:
                    existing.role = new_role

        self._push_participants_to_grid()

    @staticmethod
    def _role_label(jp) -> str:
        bits: list[str] = []
        # Use the RPC-resolved is_local flag; fall back to channel 0 heuristic
        if getattr(jp, "is_local", False) or getattr(jp, "channel_id", -1) == 0:
            bits.append("You")
        instrument = getattr(jp, "instrument", "") or ""
        if instrument:
            bits.append(instrument.title())
        if not bits:
            bits.append("Musician")
        # Skill badge from the musician's Jamulus profile (stage view v2).
        skill = (getattr(jp, "skill_level", "") or "").strip()
        if skill and skill.lower() != "null":
            bits.append(skill.title())
        return " · ".join(bits)

    # ------------------------------------------------------------------
    # Level polling — real audio engine values
    # ------------------------------------------------------------------
    def _poll_levels(self) -> None:
        """Called every 100 ms; pushes audio engine levels to participant grid."""
        for channel_id in self.participants:
            level = self.jamulus.audio_engine.get_level(channel_id)
            self.window.participant_grid.update_level(channel_id, level)

    # ------------------------------------------------------------------
    # Demo level animation (shown before Jamulus connects)
    # ------------------------------------------------------------------
    def _demo_tick(self) -> None:
        for participant in self.participants.values():
            if participant.muted or not participant.is_connected:
                level = 0.0
            else:
                fader_ratio = participant.fader_level / 127.0
                activity = random.uniform(0.05, 0.60)
                level = min(1.0, fader_ratio * activity)
            self.window.participant_grid.update_level(participant.channel_id, level)

    # ------------------------------------------------------------------
    # Session strip handlers
    # ------------------------------------------------------------------
    def _on_mode_changed(self, mode_key: str) -> None:
        # Persist the mode change immediately so a crash before clean
        # shutdown still saves the user's preference.
        self._save_session_title()
        mode = get_mode_by_key_or_default(mode_key)
        self._apply_mode(mode)
        self.window.flash_message(f"Switched to {mode.label}")

    def _on_title_changed(self, title: str) -> None:
        LOGGER.info("Session title set: %s", title)
        # Persist immediately so a crash before clean shutdown doesn't lose it
        self._save_session_title()

    def _apply_mode(self, mode) -> None:
        self.window.flash_message(mode.quick_help, ms=6000)

    def _on_launch_audio(self) -> None:
        """Toggle handler — launches Jamulus if stopped, stops it if running."""
        if self._is_jamulus_running():
            self._stop_audio()
        else:
            self.window.set_status_audio("Launching…")
            self.window.session_strip.set_audio_state("Launching…", enabled=False)
            self.bridge.launch_jamulus(manual=True)

    def _stop_audio(self) -> None:
        """Confirm with the user, then stop Jamulus and reset UI state."""
        reply = QMessageBox.question(
            self.window, "Stop Audio?",
            "Stop the Jamulus audio session?\n\nYou can restart it any time with the audio button.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.window.set_status_audio("Stopping…")
        self.window.set_status_latency("Not connected")
        self.window.session_strip.set_audio_state("Stopping…", enabled=False)
        # Stop in a worker thread; bridge will refresh readiness when done
        threading.Thread(
            target=self.bridge.stop_jamulus, daemon=True, name="jamulus-stop",
        ).start()
        # Reset state and restore the demo grid so the user sees a clear
        # visual signal that audio is off (instead of frozen real participants).
        self._jamulus_connected = False
        self._level_timer.stop()
        self._apply_recorder_state(False)
        self._reset_to_demo_state()
        # Clear the crash-banner latch so a future crash flashes again.
        # Without this, manually stopping during a reconnect would lock the
        # latch True and the next crash would be silent.
        self._reconnect_banner_shown = False
        self._rpc_hang_banner_shown = False

    def _reset_to_demo_state(self) -> None:
        """Replace current participants with demo placeholders + restart demo timer."""
        self.participants.clear()
        for p in _DEMO_PARTICIPANTS:
            self.participants[p.channel_id] = ParticipantPresentation(
                channel_id=p.channel_id,
                name=p.name,
                role=p.role,
                fader_level=p.fader_level,
                is_local=p.is_local,
            )
        self._push_participants_to_grid()
        self._demo_timer.start()

    def _is_jamulus_running(self) -> bool:
        return self.bridge.jamulus_state in ("Running", "Already running")

    def _on_join_video(self) -> None:
        """Toggle handler — joins the meeting if not joined, leaves if active."""
        if self._is_video_active():
            self._leave_video()
            return

        url = self.settings.webex_url
        if not url:
            self._show_actionable_error(
                "No Meeting URL",
                what_failed="No Webex meeting URL is configured.",
                likely_cause="A meeting link hasn't been entered yet.",
                next_action="Go to Settings and enter your Webex meeting link.",
            )
            return

        self.window.set_status_video("Joining…")
        self.window.session_strip.set_video_state("Joining…", enabled=False)
        self.window.webex_embed.meeting_state_changed.connect(
            self._on_webex_state, Qt.ConnectionType.UniqueConnection
        )

        issuer_id = self.settings.webex_guest_issuer_id
        secret    = self.settings.webex_guest_issuer_secret
        if issuer_id and secret:
            self.window.webex_embed.load_meeting_with_guest_token(
                url,
                issuer_id=issuer_id,
                secret_b64=secret,
                display_name=self.settings.webex_display_name or "WebJam Guest",
            )
        else:
            self.window.webex_embed.load_meeting(url)

    def _is_video_active(self) -> bool:
        return self.bridge.webex_state in (
            "Opened in browser", "In Meeting", "Joining…",
            "Video Active", "Lobby", "joining",
        )

    def _leave_video(self) -> None:
        """Leave the embedded Webex meeting and reset the UI."""
        self.window.webex_embed.leave_meeting()
        self.bridge.leave_webex()
        self.window.flash_message("Left video meeting.")

    def _on_webex_state(self, state: str) -> None:
        # status_label shown in the bar (descriptive); button_label shown on the
        # primary button (action-oriented — "Leave Video" when joined, etc.)
        # state_map entries: (status_label, button_label, enabled, joined)
        state_map = {
            "joining":  ("Joining…",      "Joining…",    False, True),
            "ACTIVE":   ("In Meeting",    "Leave Video", True,  True),
            "lobby":    ("Lobby",         "Leave Video", True,  True),
            "ENDED":    ("Meeting ended", "Join Video",  True,  False),
            "left":     ("Not opened",    "Join Video",  True,  False),
            "error":    ("Webex error",   "Join Video",  True,  False),
        }
        status_label, button_label, enabled, joined = state_map.get(
            state, (state.title(), "Leave Video", True, True)
        )
        self.window.set_status_video(status_label)
        self.window.session_strip.set_video_state(button_label, enabled=enabled)
        # Sync bridge.webex_state to a value that _is_video_active() recognises,
        # so the toggle button does the right thing if the user clicks it again.
        self.bridge.webex_state = status_label if joined else "Not opened"

        # On error, restore the placeholder so the user can fall back to the
        # browser without first clicking "Leave Video".
        if state == "error":
            self.window.webex_embed.leave_meeting()
            self.window.flash_message(
                "Webex couldn't load — try the 'Open video call in browser' button.",
                ms=6000,
            )

        # In direct-URL mode the widget never sends a post-join state
        # transition (no JS bridge); re-enable the button after 6 s so
        # the user can leave or rejoin without restarting the app.
        if state == "joining":
            QTimer.singleShot(
                6_000,
                lambda: self.window.session_strip.set_video_state("Leave Video", enabled=True),
            )

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
            # Keep the strip's "Mute Me" button in sync if this was the local user.
            if p.is_local:
                self.window.session_strip.set_self_muted(muted)
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
        self.window.flash_message(text)

    def _refresh_readiness(self) -> None:
        # Append server address when Jamulus is running so musicians can confirm
        # they're on the right server at a glance.
        audio_state = self.bridge.jamulus_state
        jamulus_up = self.bridge.jamulus_state in ("Running", "Already running")
        if jamulus_up:
            server = f"{self.settings.jamulus_server}:{self.settings.jamulus_port}"
            audio_state = f"{audio_state} ({server})"
        self.window.set_status_audio(audio_state)
        self.window.set_status_video(self.bridge.webex_state)
        self.window.session_strip.set_audio_state(
            "Stop Audio" if jamulus_up else "Launch Audio", enabled=True
        )
        webex_open = self._is_video_active()
        self.window.session_strip.set_video_state(
            "Leave Video" if webex_open else "Join Video", enabled=True
        )

    def _show_actionable_error(self, title: str, *, what_failed: str,
                                likely_cause: str, next_action: str,
                                retry_callback=None) -> None:
        from pathlib import Path
        # Mention both log files: WebJam's own log + Jamulus's stdout/stderr.
        # Including the Jamulus log only if it exists (avoids confusion when
        # Jamulus never launched, e.g. "Not Found" errors).
        log_lines = [f"  {self.settings.log_file}  (WebJam)"]
        jamulus_log = Path.home() / ".webjam_jamulus.log"
        if jamulus_log.exists():
            log_lines.append(f"  {jamulus_log}  (Jamulus output)")
        body = (
            f"{what_failed}\n\nLikely cause: {likely_cause}\n\n"
            f"Next action: {next_action}\n\n"
            f"For details, see the log file"
            f"{'s' if len(log_lines) > 1 else ''}:\n"
            + "\n".join(log_lines)
        )
        box = QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Warning)
        retry_btn = None
        if retry_callback:
            retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if retry_btn is not None and box.clickedButton() is retry_btn:
            try:
                retry_callback()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Retry callback failed")

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
                f"Jamulus disconnected — auto-reconnecting (attempt {attempts}/5)…",
                ms=5000,
            )
            self.window.set_status_audio("Reconnecting…")
            self._reconnect_banner_shown = True
        elif (
            self.bridge.jamulus_state in ("Running", "Already running")
            and self._reconnect_banner_shown
        ):
            # Reconnect succeeded — clear the flag so we'd flash again on next crash.
            self._reconnect_banner_shown = False
            self.window.flash_message("Jamulus reconnected.", ms=3000)

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
                    f"Jamulus stopped responding ({int(age)}s of silence). "
                    f"Try Stop Audio + Launch Audio if it persists.",
                    ms=8000,
                )
                self.window.set_status_audio("Not responding")
                self._rpc_hang_banner_shown = True
                try:
                    self.metrics.increment("metric_jamulus_hang_detected")
                except Exception:  # noqa: BLE001
                    LOGGER.debug("hang metric failed", exc_info=True)
            elif age <= self._RPC_HANG_THRESHOLD_S and self._rpc_hang_banner_shown:
                self._rpc_hang_banner_shown = False
                self.window.flash_message("Jamulus is responding again.", ms=3000)

        self.bridge.attempt_auto_reconnects()

    def _on_token_refresh_tick(self) -> None:
        """Called every 60 s; ask the embedded Webex pane to refresh its
        guest token if the existing one is approaching its 1-hour TTL.

        No-op when guest-issuer credentials aren't configured (direct-URL
        mode), or when no meeting has been joined yet.
        """
        issuer_id = self.settings.webex_guest_issuer_id
        secret = self.settings.webex_guest_issuer_secret
        if not issuer_id or not secret:
            return
        try:
            self.window.webex_embed.maybe_refresh_token(
                issuer_id=issuer_id,
                secret_b64=secret,
                display_name=self.settings.webex_display_name or "WebJam Guest",
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("Token refresh tick failed", exc_info=True)

    # ------------------------------------------------------------------
    # Save / Load mix (Ctrl+S / Ctrl+O)
    # ------------------------------------------------------------------
    def _on_mute_self(self) -> None:
        """Toggle mute on the local user's channel.

        Quick way for the conductor to silence themselves during a session
        (e.g. answering a phone, talking off-mic) without finding their card
        in the participant grid.
        """
        local_channel_id: Optional[int] = None
        for cid, p in self.participants.items():
            if p.is_local:
                local_channel_id = cid
                break
        if local_channel_id is None:
            self.window.flash_message(
                "Connect to Jamulus first — your channel isn't available yet.",
                ms=4000,
            )
            # Reset the button to unchecked since we didn't actually mute
            self.window.session_strip.set_self_muted(False)
            return
        p = self.participants[local_channel_id]
        new_muted = not p.muted
        p.muted = new_muted
        self._mix_dirty = True
        if self._jamulus_connected:
            # Real self-mute: tell Jamulus to stop sending OUR audio to the
            # band (jamulusclient/setMuted).  Zeroing our own channel fader
            # would only mute us in our own monitor — the others would still
            # hear us.
            self.jamulus.set_self_muted(new_muted)
        # Mirror mute state into the embedded Webex meeting if we're in one,
        # so the conductor only has to hit one button to silence themselves
        # in both audio and video.  No-op if Webex hasn't joined.  (We've
        # already returned above if there's no local Jamulus channel, so this
        # only needs to gate on the video being active.)
        if self._is_video_active():
            try:
                self.window.webex_embed.mute_webex_self(new_muted)
            except Exception:  # noqa: BLE001
                LOGGER.debug("Webex mute sync failed", exc_info=True)
        self._push_participants_to_grid()
        self.window.session_strip.set_self_muted(new_muted)
        self.window.flash_message(
            "You are muted." if new_muted else "You are unmuted.", ms=2500,
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

            from webjam_qt import __version__
            from webjam_qt.controllers.diagnostics import DiagnosticsExporter

            exporter = DiagnosticsExporter(
                settings=self.settings,
                bridge=self.bridge,
                jamulus_controller=self.jamulus,
                window_version=__version__,
            )
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
    def _open_settings_wizard(self) -> None:
        from webjam_qt.windows.setup_wizard import SetupWizard
        # Snapshot relevant fields before the wizard so we can detect changes.
        old_webex_url = self.settings.webex_url
        old_jamulus_server = (self.settings.jamulus_server, self.settings.jamulus_port)
        # In-session reopen — skip the welcome page since the user already
        # knows what WebJam is and is here to change a specific setting.
        wizard = SetupWizard(self.settings, parent=self.window, skip_welcome=True)
        if wizard.exec() == SetupWizard.DialogCode.Accepted:
            from core.settings import load_settings
            self.settings = load_settings()
            # Push new settings to services immediately so the next Launch Audio
            # or Join Video uses the updated server / Jamulus path / Webex URL.
            self.bridge.settings = self.settings

            # Build a context-aware confirmation message so the user knows
            # whether they need to take any action for the change to apply.
            warnings: list[str] = []
            if (
                self.settings.webex_url != old_webex_url
                and self._is_video_active()
            ):
                warnings.append("Leave Video and re-join to apply the new Webex URL.")
            if (
                (self.settings.jamulus_server, self.settings.jamulus_port) != old_jamulus_server
                and self._is_jamulus_running()
            ):
                warnings.append("Stop Audio and re-launch to connect to the new Jamulus server.")

            if warnings:
                self.window.flash_message(
                    "Settings saved. " + " ".join(warnings),
                    ms=8000,
                )
            else:
                self.window.flash_message(
                    "Settings saved — take effect on next Launch Audio / Join Video."
                )

    def _on_rail_view_changed(self, key: str) -> None:
        splitter = self.window.center_splitter
        total = sum(splitter.sizes()) or self.window.DEFAULT_WIDTH

        # Keys that represent actual view changes (persist selection)
        _CONTENT_KEYS = frozenset({"stage", "mixer", "canvas"})

        if key == "settings":
            # Restore rail to the previous content view before opening wizard
            prev = getattr(self, "_last_content_key", "stage")
            self.window.side_rail.set_active_key(prev)
            self._open_settings_wizard()
        elif key in _CONTENT_KEYS:
            self._last_content_key = key
            if key in ("stage", "mixer"):
                # Stage/Mixer: participant grid takes most of the space
                splitter.setSizes([int(total * 0.72), int(total * 0.28)])
            elif key == "canvas":
                # Canvas: expand the notes panel
                splitter.setSizes([int(total * 0.28), int(total * 0.72)])
        elif key == "chat":
            # Flash message and restore the previous content selection
            prev = getattr(self, "_last_content_key", "stage")
            self.window.side_rail.set_active_key(prev)
            self.window.flash_message("Chat — coming in a future update", ms=3000)
        elif key == "roles":
            prev = getattr(self, "_last_content_key", "stage")
            self.window.side_rail.set_active_key(prev)
            self.window.flash_message("Role management — coming in a future update", ms=3000)

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

    # ------------------------------------------------------------------
    # Audio routing detection (Phase 5)
    # ------------------------------------------------------------------
    def _start_routing_scan(self) -> None:
        """Scan for VB-CABLE / BlackHole in a background thread."""
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

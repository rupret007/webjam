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

        # Latch so the "Jamulus disconnected" flash only fires once per crash
        self._reconnect_banner_shown = False

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

        # Register real participant callback
        self.jamulus.register_callback(self._on_jamulus_participants)

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
        self._save_notes()
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

    # ------------------------------------------------------------------
    # Initial wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        strip = self.window.session_strip
        strip.mode_changed.connect(self._on_mode_changed)
        strip.session_title_changed.connect(self._on_title_changed)
        strip.launch_audio_requested.connect(self._on_launch_audio)
        strip.join_video_requested.connect(self._on_join_video)
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
        # Mute all shortcut
        self.window._mute_all_shortcut.activated.connect(self._on_mute_all)

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
        self._load_notes()

    def _push_participants_to_grid(self) -> None:
        self.window.participant_grid.set_participants(self.participants.values())

    # ------------------------------------------------------------------
    # Real Jamulus participant callback (called from background thread)
    # ------------------------------------------------------------------
    def _on_jamulus_participants(self, jamulus_participants: list) -> None:
        """Receive live participant list from JamulusController — runs on a worker thread."""
        self._ui_invoker.invoke(lambda: self._apply_jamulus_participants(jamulus_participants))

    def _apply_jamulus_participants(self, jamulus_participants: list) -> None:
        """Update the participant grid on the UI thread from real Jamulus data."""
        if not jamulus_participants:
            return

        if not self._jamulus_connected:
            self._jamulus_connected = True
            self._demo_timer.stop()
            # Clear demo data; real participants take over
            self.participants.clear()
            # Start polling real audio engine levels
            self._level_timer.start()
            # Restore saved mix (best-effort — silently skipped if no file)
            self._restore_saved_mix()

        # Update participant count in status bar
        n = len(jamulus_participants)
        self.window.set_status_latency(f"{n} participant{'s' if n != 1 else ''}")

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
        mode = get_mode_by_key_or_default(mode_key)
        self._apply_mode(mode)
        self.window.flash_message(f"Switched to {mode.label}")

    def _on_title_changed(self, title: str) -> None:
        LOGGER.info("Session title set: %s", title)

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
        self._reset_to_demo_state()
        # Clear the crash-banner latch so a future crash flashes again.
        # Without this, manually stopping during a reconnect would lock the
        # latch True and the next crash would be silent.
        self._reconnect_banner_shown = False

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
        if self._jamulus_connected:
            self.jamulus.set_fader_level(channel_id, level)

    def _on_mute_toggled(self, channel_id: int, muted: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.muted = muted
        if self._jamulus_connected:
            self.jamulus.set_mute(channel_id, muted)

    def _on_solo_toggled(self, channel_id: int, solo: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.solo = solo
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
        body = (
            f"{what_failed}\n\nLikely cause: {likely_cause}\n\n"
            f"Next action: {next_action}\n\n"
            f"For details, see the log file:\n  {self.settings.log_file}"
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

        self.bridge.attempt_auto_reconnects()

    # ------------------------------------------------------------------
    # Save / Load mix (Ctrl+S / Ctrl+O)
    # ------------------------------------------------------------------
    def _on_mute_all(self) -> None:
        """Ctrl+M — toggle mute state for every participant.

        If any channel is unmuted, mute all. If all are already muted, unmute all.
        Skips participants that are currently soloed (solo mutes others implicitly).
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
        # Push updated state to grid so buttons reflect the change
        self._push_participants_to_grid()
        verb = "Muted" if target_muted else "Unmuted"
        self.window.flash_message(f"{verb} all participants  ·  Ctrl+M to toggle")

    def _on_save_mix(self) -> None:
        """Serialize current mixer state to ~/.webjam_mix.json."""
        import json
        from pathlib import Path
        try:
            payload = self.jamulus.serialize_mix()
            mix_path = Path.home() / ".webjam_mix.json"
            mix_path.write_text(json.dumps(payload, indent=2))
            LOGGER.info("Mix saved to %s", mix_path)
            self.window.flash_message("Mix saved  ·  Ctrl+O to restore")
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to save mix")
            self.window.flash_message("Save failed — see logs")

    def _on_load_mix(self) -> None:
        """Load mixer state from ~/.webjam_mix.json and apply to Jamulus."""
        import json
        from pathlib import Path
        mix_path = Path.home() / ".webjam_mix.json"
        if not mix_path.exists():
            self.window.flash_message("No saved mix found  ·  Ctrl+S to save one")
            return
        try:
            payload = json.loads(mix_path.read_text())
            self.jamulus.apply_mix_data(payload)
            LOGGER.info("Mix loaded from %s", mix_path)
            self.window.flash_message("Mix loaded")
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to load mix")
            self.window.flash_message("Load failed — see logs")

    def _restore_saved_mix(self) -> None:
        """Auto-apply ~/.webjam_mix.json when Jamulus first connects (best-effort)."""
        import json
        from pathlib import Path
        mix_path = Path.home() / ".webjam_mix.json"
        if not mix_path.exists():
            return
        try:
            payload = json.loads(mix_path.read_text())
            self.jamulus.apply_mix_data(payload)
            LOGGER.info("Restored saved mix from %s", mix_path)
            self.window.flash_message("Mix restored from saved state")
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to restore saved mix from %s", mix_path)

    # ------------------------------------------------------------------
    # Settings wizard (Phase 6)
    # ------------------------------------------------------------------
    def _open_settings_wizard(self) -> None:
        from webjam_qt.windows.setup_wizard import SetupWizard
        # Snapshot relevant fields before the wizard so we can detect changes.
        old_webex_url = self.settings.webex_url
        old_jamulus_server = (self.settings.jamulus_server, self.settings.jamulus_port)
        wizard = SetupWizard(self.settings, parent=self.window)
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
    def _load_notes(self) -> None:
        """Restore session notes from disk (best-effort)."""
        from pathlib import Path
        notes_path = Path.home() / ".webjam_notes.md"
        if notes_path.exists():
            try:
                text = notes_path.read_text(encoding="utf-8")
                self.window.session_canvas.set_notes(text)
            except Exception:  # noqa: BLE001
                LOGGER.debug("Could not load session notes", exc_info=True)

    def _save_notes(self) -> None:
        """Persist current session notes to disk (best-effort)."""
        from pathlib import Path
        try:
            text = self.window.session_canvas.current_notes()
            if text.strip():
                (Path.home() / ".webjam_notes.md").write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            LOGGER.debug("Could not save session notes", exc_info=True)

    # ------------------------------------------------------------------
    # Audio routing detection (Phase 5)
    # ------------------------------------------------------------------
    def _start_routing_scan(self) -> None:
        """Scan for VB-CABLE / BlackHole in a background thread."""
        def _scan() -> None:
            from core.audio_routing import scan_loopback_devices
            status = scan_loopback_devices()
            self._ui_invoker.invoke(lambda: self._apply_routing_status(status))

        threading.Thread(target=_scan, daemon=True, name="routing-scan").start()

    def _apply_routing_status(self, status) -> None:
        if status.ok:
            label = f"{status.device_name} \u2713"
            self.window.set_status_routing(label)
        else:
            self.window.set_status_routing("No audio device")
            self.window.flash_message(
                f"No virtual audio device found. {status.install_hint}",
                ms=8000,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def mode_entries() -> list[tuple[str, str]]:
        return [(mode.key, mode.label) for mode in CREATIVE_MODES]

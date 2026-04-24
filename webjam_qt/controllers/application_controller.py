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

# Demo participants shown before Jamulus connects
_DEMO_PARTICIPANTS = [
    ParticipantPresentation(channel_id=0, name="You",    role="You · Drums",     fader_level=100, is_local=True),
    ParticipantPresentation(channel_id=1, name="Dylan",  role="Guitar",          fader_level=96),
    ParticipantPresentation(channel_id=2, name="Andrea", role="Bass",            fader_level=104),
    ParticipantPresentation(channel_id=3, name="Brian",  role="Vocals",          fader_level=110),
    ParticipantPresentation(channel_id=4, name="Jesse",  role="Keys",            fader_level=88),
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
        try:
            self.jamulus.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Jamulus stop failed")
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
        if getattr(jp, "channel_id", -1) == 0:
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
        self.window.set_status_audio("Launching…")
        self.window.session_strip.set_audio_state("Launching…", enabled=False)
        self.bridge.launch_jamulus(manual=True)

    def _on_join_video(self) -> None:
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

    def _on_webex_state(self, state: str) -> None:
        state_map = {
            "joining":  ("Joining…",      False),
            "ACTIVE":   ("In Meeting",    True),
            "lobby":    ("Lobby",         True),
            "ENDED":    ("Meeting ended", True),
            "left":     ("Left meeting",  True),
            "error":    ("Webex error",   True),
        }
        label, enabled = state_map.get(state, (state.title(), True))
        self.window.set_status_video(label)
        self.window.session_strip.set_video_state(
            label if enabled else "Joining…", enabled=enabled
        )
        self.bridge.webex_state = label

        # In direct-URL mode the widget never sends a post-join state
        # transition (no JS bridge); re-enable the button after 6 s so
        # the user can leave or rejoin without restarting the app.
        if state == "joining":
            QTimer.singleShot(
                6_000,
                lambda: self.window.session_strip.set_video_state("Video Active", enabled=True),
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
        self.window.set_status_audio(self.bridge.jamulus_state)
        self.window.set_status_video(self.bridge.webex_state)
        jamulus_up = self.bridge.jamulus_state in ("Running", "Already running")
        self.window.session_strip.set_audio_state(
            "Audio Running" if jamulus_up else "Launch Audio", enabled=True
        )
        webex_open = self.bridge.webex_state == "Opened in browser"
        self.window.session_strip.set_video_state(
            "Video Opened" if webex_open else "Join Video", enabled=True
        )

    def _show_actionable_error(self, title: str, *, what_failed: str,
                                likely_cause: str, next_action: str,
                                retry_callback=None) -> None:
        body = f"{what_failed}\n\nLikely cause: {likely_cause}\n\nNext action: {next_action}"
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
        """Called every 3 s; lets BridgeService retry dropped services."""
        self.bridge.attempt_auto_reconnects()

    # ------------------------------------------------------------------
    # Save / Load mix (Ctrl+S / Ctrl+O)
    # ------------------------------------------------------------------
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
        wizard = SetupWizard(self.settings, parent=self.window)
        if wizard.exec() == SetupWizard.DialogCode.Accepted:
            # Reload settings and apply live-changeable values
            from core.settings import load_settings
            self.settings = load_settings()
            self.window.flash_message("Settings saved. Restart WebJam to apply all changes.")

    def _on_rail_view_changed(self, key: str) -> None:
        splitter = self.window.center_splitter
        total = sum(splitter.sizes()) or self.window.DEFAULT_WIDTH
        if key == "settings":
            self._open_settings_wizard()
        elif key in ("stage", "mixer"):
            # Stage/Mixer: participant grid takes most of the space
            splitter.setSizes([int(total * 0.72), int(total * 0.28)])
        elif key == "canvas":
            # Canvas: expand the notes panel
            splitter.setSizes([int(total * 0.28), int(total * 0.72)])
        elif key == "chat":
            self.window.flash_message("Chat — coming in a future update", ms=3000)
        elif key == "roles":
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

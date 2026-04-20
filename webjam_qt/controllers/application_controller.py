"""
ApplicationController — the brain.

Replaces the Tkinter god-class. Owns session state, wires ConductorWindow
signals to the service layer (BridgeService, JamulusController, WebexController),
and publishes state back to widgets.

Phase 1: demo participants populate the grid with a live-feeling meter animation
so the UI has presence before real Jamulus/Webex integrations land. Remove the
``DemoAudioSimulator`` in Phase 2 once the UDP protocol pushes real levels.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from PySide6.QtCore import QObject, QTimer
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


class DemoAudioSimulator(QObject):
    """Jitter audio levels for demo participants so the meters feel alive.

    Replaced in Phase 2 by levels driven off the real Jamulus UDP stream.
    """

    TICK_MS = 120

    def __init__(self, controller: "ApplicationController") -> None:
        super().__init__(controller)
        self._controller = controller
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        for participant in self._controller.participants.values():
            if participant.muted or not participant.is_connected:
                level = 0.0
            else:
                # Target level ~= fader setting * activity coefficient
                fader_ratio = participant.fader_level / 127.0
                activity = random.uniform(0.08, 0.65)
                level = min(1.0, fader_ratio * activity)
            self._controller.window.participant_grid.update_level(
                participant.channel_id, level
            )


class ApplicationController(QObject):
    """Glue layer between ConductorWindow and the service layer."""

    def __init__(
        self,
        window: ConductorWindow,
        settings: Optional[AppSettings] = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or load_settings()

        self._ui_invoker = UiThreadInvoker(self)

        # Repository + metrics (real, writes to ~/.webjam_app.db)
        self.repository = WebJamRepository()
        self.metrics = MetricsService(self.repository)

        # Domain controllers (lazy imports — they import legacy logging config
        # and we want to keep import-time side effects contained)
        from jamulus_controller import JamulusController
        from webex_integration import WebexController

        self.jamulus = JamulusController(
            host=self.settings.jamulus_server,
            port=self.settings.jamulus_port,
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
                "set_status_banner": self._set_status_banner,
                "refresh_readiness": self._refresh_readiness,
                "show_actionable_error": self._show_actionable_error,
                "show_message": self._show_message,
                "shutdown_requested": lambda: self._shutdown,
                "schedule_ui_callback": self._ui_invoker.invoke,
            },
        )

        # Demo data — replaced in Phase 2 with real participant reconciliation
        self.participants: dict[int, ParticipantPresentation] = {}
        self._demo_simulator = DemoAudioSimulator(self)

        self._wire_signals()
        self._bootstrap_ui()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self._shutdown = True
        self._demo_simulator.stop()
        try:
            self.jamulus.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Jamulus stop failed")
        try:
            self.webex.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex stop failed")

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        strip = self.window.session_strip
        strip.mode_changed.connect(self._on_mode_changed)
        strip.session_title_changed.connect(self._on_title_changed)
        strip.launch_audio_requested.connect(self._on_launch_audio)
        strip.join_video_requested.connect(self._on_join_video)

        self.window.webex_embed.fallback_button().clicked.connect(self._on_join_video)
        self.window.close_requested.connect(self.shutdown)

        for card in self.window.participant_grid.cards():
            self._connect_card_signals(card)

    def _connect_card_signals(self, card) -> None:
        card.fader_changed.connect(self._on_fader_changed)
        card.mute_toggled.connect(self._on_mute_toggled)
        card.solo_toggled.connect(self._on_solo_toggled)

    def _bootstrap_ui(self) -> None:
        # Demo participants — named placeholders so the first impression isn't empty
        demo = [
            ParticipantPresentation(
                channel_id=0, name="You", role="You · Drums",
                fader_level=100, is_local=True, video_connected=False,
            ),
            ParticipantPresentation(
                channel_id=1, name="Dylan", role="Guitar · Audio only",
                fader_level=96, video_connected=False,
            ),
            ParticipantPresentation(
                channel_id=2, name="Andrea", role="Bass · Video",
                fader_level=104, video_connected=True,
            ),
            ParticipantPresentation(
                channel_id=3, name="Brian", role="Vocals · Video",
                fader_level=110, video_connected=True,
            ),
            ParticipantPresentation(
                channel_id=4, name="Jesse", role="Keys · Audio only",
                fader_level=88, video_connected=False,
            ),
        ]
        for p in demo:
            self.participants[p.channel_id] = p
        self.window.participant_grid.set_participants(self.participants.values())
        for card in self.window.participant_grid.cards():
            self._connect_card_signals(card)

        mode = get_mode_by_key_or_default(self.window.session_strip.current_mode_key() or "music_jam")
        self._apply_mode(mode)

        self.window.set_status_audio("Not launched")
        self.window.set_status_video("Not joined")
        self.window.set_status_latency("—")

        self.window.session_strip.start_session_clock()
        self._demo_simulator.start()

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
        # In Phase 2 this will reshape the stage/canvas; for now we just
        # surface the quick help in the status bar.
        self.window.flash_message(mode.quick_help, ms=6000)

    def _on_launch_audio(self) -> None:
        self.window.set_status_audio("Launching…")
        self.window.session_strip.set_audio_state("Launching…", enabled=False)
        self.bridge.launch_jamulus(manual=True)

    def _on_join_video(self) -> None:
        self.window.set_status_video("Opening…")
        self.window.session_strip.set_video_state("Opening…", enabled=False)
        self.bridge.launch_webex(manual=True)

    # ------------------------------------------------------------------
    # Participant card handlers (wiring lands in Phase 2 — Jamulus protocol)
    # ------------------------------------------------------------------
    def _on_fader_changed(self, channel_id: int, level: int) -> None:
        participant = self.participants.get(channel_id)
        if participant is not None:
            participant.fader_level = level
        # Phase 2: self.jamulus.set_fader(channel_id, level_to_jamulus(level))

    def _on_mute_toggled(self, channel_id: int, muted: bool) -> None:
        participant = self.participants.get(channel_id)
        if participant is not None:
            participant.muted = muted
        # Phase 2: self.jamulus.set_mute(channel_id, muted)

    def _on_solo_toggled(self, channel_id: int, solo: bool) -> None:
        participant = self.participants.get(channel_id)
        if participant is not None:
            participant.solo = solo
        # Phase 2: self.jamulus.set_solo(channel_id, solo) + update other channels

    # ------------------------------------------------------------------
    # BridgeService UI callbacks (called on UI thread via invoker)
    # ------------------------------------------------------------------
    def _set_status_banner(self, text: str, color: str | None = None) -> None:
        self.window.flash_message(text)

    def _refresh_readiness(self) -> None:
        self.window.set_status_audio(self.bridge.jamulus_state)
        self.window.set_status_video(self.bridge.webex_state)

        jamulus_running = self.bridge.jamulus_state in ("Running", "Already running")
        self.window.session_strip.set_audio_state(
            "Audio Running" if jamulus_running else "Launch Audio",
            enabled=True,
        )
        webex_open = self.bridge.webex_state in ("Opened in browser",)
        self.window.session_strip.set_video_state(
            "Video Opened" if webex_open else "Join Video",
            enabled=True,
        )

    def _show_actionable_error(
        self,
        title: str,
        *,
        what_failed: str,
        likely_cause: str,
        next_action: str,
        retry_callback=None,
    ) -> None:
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
    # Static helpers
    # ------------------------------------------------------------------
    @staticmethod
    def mode_entries() -> list[tuple[str, str]]:
        return [(mode.key, mode.label) for mode in CREATIVE_MODES]

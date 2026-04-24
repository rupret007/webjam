"""
ConductorWindow — the single-window Conductor shell.

Layout (horizontal, left to right):

    ┌──────────────────────────────────────────────────────────┐
    │                   SessionStrip (top)                     │
    ├────┬─────────────────────────────┬───────────────────────┤
    │    │                             │                       │
    │Side│     Stage (participant      │   SessionCanvas       │
    │Rail│      cards + Webex embed)   │   (right panel)       │
    │    │                             │                       │
    ├────┴─────────────────────────────┴───────────────────────┤
    │                    Status Bar                            │
    └──────────────────────────────────────────────────────────┘

The window owns the layout and exposes signals; it does NOT own state.
Wiring to services happens in ApplicationController.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.widgets import (
    ParticipantGrid,
    SessionCanvas,
    SessionStrip,
    SideRail,
    WebexEmbed,
)


class ConductorWindow(QMainWindow):
    close_requested = Signal()

    DEFAULT_WIDTH = 1440
    DEFAULT_HEIGHT = 900

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str,
        initial_title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("WebJam — Conductor")
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setMinimumSize(1100, 720)

        # --- Central widgets
        self.session_strip = SessionStrip(
            mode_entries=mode_entries,
            initial_mode_key=initial_mode_key,
            initial_title=initial_title,
        )
        self.side_rail = SideRail()
        self.participant_grid = ParticipantGrid()
        self.webex_embed = WebexEmbed()
        self.session_canvas = SessionCanvas()

        # Stage combines participant grid + webex embed vertically
        stage_container = QWidget()
        stage_layout = QVBoxLayout(stage_container)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        stage_header = QLabel("STAGE")
        stage_header.setObjectName("StageHeader")
        stage_layout.addWidget(stage_header)
        stage_layout.addWidget(self.participant_grid, stretch=3)
        stage_layout.addWidget(self.webex_embed, stretch=2)

        self.center_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.center_splitter.addWidget(stage_container)
        self.center_splitter.addWidget(self.session_canvas)
        self.center_splitter.setStretchFactor(0, 3)
        self.center_splitter.setStretchFactor(1, 1)
        self.center_splitter.setSizes([int(self.DEFAULT_WIDTH * 0.72), int(self.DEFAULT_WIDTH * 0.28)])
        self.center_splitter.setCollapsible(0, True)
        self.center_splitter.setCollapsible(1, True)
        self.center_splitter.setHandleWidth(1)

        body_container = QWidget()
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(self.center_splitter, stretch=1)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.session_strip)
        central_layout.addWidget(body_container, stretch=1)

        self.setCentralWidget(central)

        # --- Status bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        self._status_audio   = QLabel("Audio: —")
        self._status_video   = QLabel("Video: —")
        self._status_latency = QLabel("Session: —")
        self._status_routing = QLabel("Routing: checking…")
        self._status_bar.addPermanentWidget(self._status_audio)
        self._status_bar.addPermanentWidget(self._status_video)
        self._status_bar.addPermanentWidget(self._status_latency)
        self._status_bar.addPermanentWidget(self._status_routing)
        self._status_bar.showMessage("Ready")

        # --- Accessibility names
        self.session_strip.setAccessibleName("Session controls strip")
        self.side_rail.setAccessibleName("Navigation rail")
        self.participant_grid.setAccessibleName("Participant mixer grid")
        self.webex_embed.setAccessibleName("Webex video conference pane")
        self.session_canvas.setAccessibleName("Session notes canvas")

        # --- Keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        # Cmd/Ctrl+L — focus session title
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.session_strip.focus_title())
        # Cmd/Ctrl+M — toggle mute all (handled by controller via strip signal)
        # F11 — fullscreen toggle
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen)
        # Escape — exit fullscreen
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen)
        # Cmd/Ctrl+, — open settings wizard (signal consumed by controller)
        self._settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        # Cmd/Ctrl+S — save mix; Cmd/Ctrl+O — load mix (consumed by controller)
        self._save_mix_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._load_mix_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        # Cmd/Ctrl+T — insert timestamp into session canvas
        QShortcut(
            QKeySequence("Ctrl+T"), self,
            lambda: self.session_canvas.insert_timestamp(),
        )
        # Cmd/Ctrl+M — mute / unmute all (consumed by controller)
        self._mute_all_shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        # Cmd/Ctrl+Shift+M — toggle mute on the local user's channel
        self._mute_self_shortcut = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        # F1 — show help dialog
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, self._show_help)

    def _show_help(self) -> None:
        """Display a keyboard-shortcut and getting-started reference."""
        from PySide6.QtWidgets import QMessageBox
        body = (
            "<b>WebJam — Conductor UI</b><br>"
            "<i>One window for band audio (Jamulus) + video (Webex).</i><br><br>"
            "<b>Keyboard shortcuts:</b><br>"
            "&nbsp;&nbsp;<b>Ctrl+L</b> — Focus session title<br>"
            "&nbsp;&nbsp;<b>Ctrl+S</b> — Save mixer state<br>"
            "&nbsp;&nbsp;<b>Ctrl+O</b> — Load mixer state<br>"
            "&nbsp;&nbsp;<b>Ctrl+M</b> — Mute / unmute all<br>"
            "&nbsp;&nbsp;<b>Ctrl+Shift+M</b> — Mute / unmute yourself<br>"
            "&nbsp;&nbsp;<b>Ctrl+T</b> — Insert timestamp in canvas<br>"
            "&nbsp;&nbsp;<b>Ctrl+,</b> — Open Settings<br>"
            "&nbsp;&nbsp;<b>F11</b> — Toggle fullscreen<br>"
            "&nbsp;&nbsp;<b>Esc</b> — Exit fullscreen<br>"
            "&nbsp;&nbsp;<b>F1</b> — Show this help<br>"
            "&nbsp;&nbsp;<b>Double-click fader</b> — Reset to 0 dB<br><br>"
            "<b>Getting started:</b><br>"
            "1. Click <b>Launch Audio</b> (gold button) to start Jamulus.<br>"
            "2. Click <b>Join Video</b> (teal button) to open Webex.<br>"
            "3. Adjust faders as your band joins.<br>"
            "4. Click the same buttons again to stop / leave.<br><br>"
            "<a href='https://github.com/rupret007/webjam'>github.com/rupret007/webjam</a>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("WebJam Help")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    # ------------------------------------------------------------------
    # Public helpers for ApplicationController
    # ------------------------------------------------------------------
    def set_status_audio(self, text: str) -> None:
        self._status_audio.setText(f"Audio: {text}")

    def set_status_video(self, text: str) -> None:
        self._status_video.setText(f"Video: {text}")

    def set_status_latency(self, text: str) -> None:
        self._status_latency.setText(f"Session: {text}")

    def set_status_routing(self, text: str) -> None:
        self._status_routing.setText(f"Routing: {text}")

    def flash_message(self, text: str, *, ms: int = 4000) -> None:
        self._status_bar.showMessage(text, ms)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.close_requested.emit()
        super().closeEvent(event)

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
from PySide6.QtGui import QCloseEvent
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

        center_splitter = QSplitter(Qt.Orientation.Horizontal)
        center_splitter.addWidget(stage_container)
        center_splitter.addWidget(self.session_canvas)
        center_splitter.setStretchFactor(0, 3)
        center_splitter.setStretchFactor(1, 1)
        center_splitter.setSizes([int(self.DEFAULT_WIDTH * 0.72), int(self.DEFAULT_WIDTH * 0.28)])
        center_splitter.setCollapsible(0, False)
        center_splitter.setCollapsible(1, False)
        center_splitter.setHandleWidth(1)

        body_container = QWidget()
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(center_splitter, stretch=1)

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
        self._status_latency = QLabel("Latency: —")
        self._status_routing = QLabel("Routing: scanning…")
        self._status_bar.addPermanentWidget(self._status_audio)
        self._status_bar.addPermanentWidget(self._status_video)
        self._status_bar.addPermanentWidget(self._status_latency)
        self._status_bar.addPermanentWidget(self._status_routing)
        self._status_bar.showMessage("Ready")

    # ------------------------------------------------------------------
    # Public helpers for ApplicationController
    # ------------------------------------------------------------------
    def set_status_audio(self, text: str) -> None:
        self._status_audio.setText(f"Audio: {text}")

    def set_status_video(self, text: str) -> None:
        self._status_video.setText(f"Video: {text}")

    def set_status_latency(self, text: str) -> None:
        self._status_latency.setText(f"Latency: {text}")

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

"""
SessionStrip — top bar.

Shows, left to right:
  - Logo
  - Session title + mode subtitle
  - Live session timer
  - Record indicator
  - Mode picker
  - Primary actions (Launch Audio, Join Video)

Emits semantic signals; ApplicationController wires them to services.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class SessionStrip(QFrame):
    mode_changed = Signal(str)          # mode_key
    session_title_changed = Signal(str)
    launch_audio_requested = Signal()
    join_video_requested = Signal()

    STRIP_HEIGHT = 72

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str = "",
        initial_title: str = "Untitled Session",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SessionStrip")
        self.setFixedHeight(self.STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._mode_entries = list(mode_entries)
        self._elapsed_seconds = 0

        # --- Widgets
        self._logo = QLabel("WJ")
        self._logo.setObjectName("SessionStripLogo")

        self._title_input = QLineEdit(initial_title)
        self._title_input.setObjectName("SessionStripTitle")
        self._title_input.setAccessibleName("Session title")
        self._title_input.setFrame(False)
        self._title_input.setMinimumWidth(180)
        self._title_input.editingFinished.connect(
            lambda: self.session_title_changed.emit(self._title_input.text().strip())
        )

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("SessionStripSubtitle")

        self._record_dot = QLabel("●")
        self._record_dot.setObjectName("RecordDot")
        self._record_dot.setAccessibleName("Recording indicator")
        self._record_dot.setVisible(False)

        self._timer_label = QLabel("00:00:00")
        self._timer_label.setObjectName("SessionTimer")
        self._timer_label.setAccessibleName("Session elapsed time")

        self._mode_picker = QComboBox()
        self._mode_picker.setAccessibleName("Session mode")
        for key, label in self._mode_entries:
            self._mode_picker.addItem(label, key)
        if initial_mode_key:
            idx = self._mode_picker.findData(initial_mode_key)
            if idx >= 0:
                self._mode_picker.setCurrentIndex(idx)
        self._mode_picker.currentIndexChanged.connect(self._on_mode_index_changed)
        self._sync_subtitle()

        self._audio_button = QPushButton("Launch Audio")
        self._audio_button.setObjectName("PrimaryButton")
        self._audio_button.setProperty("class", "PrimaryButton")
        self._audio_button.setStyleSheet("")  # rely on QSS cascade
        self._audio_button.setAccessibleName("Launch or stop Jamulus audio")
        self._audio_button.clicked.connect(self.launch_audio_requested.emit)

        self._video_button = QPushButton("Join Video")
        self._video_button.setObjectName("PrimaryButton")
        self._video_button.setAccessibleName("Join or leave Webex video")
        self._video_button.clicked.connect(self.join_video_requested.emit)

        # --- Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.LG)

        layout.addWidget(self._logo)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title_block.addWidget(self._title_input)
        title_block.addWidget(self._subtitle)
        layout.addLayout(title_block, stretch=1)

        layout.addWidget(self._record_dot)
        layout.addWidget(self._timer_label)
        layout.addWidget(self._mode_picker)
        layout.addWidget(self._audio_button)
        layout.addWidget(self._video_button)

        # --- Timer
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_session_clock(self) -> None:
        self._elapsed_seconds = 0
        self._update_timer_label()
        self._clock.start()

    def stop_session_clock(self) -> None:
        self._clock.stop()

    def set_recording(self, recording: bool) -> None:
        self._record_dot.setVisible(recording)

    def set_audio_state(self, label: str, *, enabled: bool = True) -> None:
        self._audio_button.setText(label)
        self._audio_button.setEnabled(enabled)

    def set_video_state(self, label: str, *, enabled: bool = True) -> None:
        self._video_button.setText(label)
        self._video_button.setEnabled(enabled)

    def current_mode_key(self) -> str:
        return self._mode_picker.currentData() or ""

    def current_title(self) -> str:
        return self._title_input.text().strip()

    def focus_title(self) -> None:
        """Focus and select the session title field (keyboard shortcut target)."""
        self._title_input.setFocus()
        self._title_input.selectAll()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _on_mode_index_changed(self, _: int) -> None:
        self._sync_subtitle()
        self.mode_changed.emit(self._mode_picker.currentData() or "")

    def _sync_subtitle(self) -> None:
        label = self._mode_picker.currentText()
        self._subtitle.setText(f"{label} · WebJam")

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        self._update_timer_label()

    def _update_timer_label(self) -> None:
        t = QTime(0, 0).addSecs(self._elapsed_seconds)
        self._timer_label.setText(t.toString("HH:mm:ss"))

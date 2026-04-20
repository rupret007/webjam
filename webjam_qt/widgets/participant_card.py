"""
ParticipantCard — the atomic unit of the Conductor UI.

One card = one person. It fuses:
  - Webex video presence  (top tile, placeholder until Phase 3)
  - Jamulus audio control (fader + meter + mute/solo)
  - Name and role

In later phases the video tile will render the participant's Webex stream
(via QWebEngineView or a WebRTC surface) and the fader will drive real
Jamulus mix state via the UDP protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.level_meter import LevelMeter


@dataclass
class ParticipantPresentation:
    """View-model for a participant card — framework-agnostic."""
    channel_id: int
    name: str
    role: str = ""
    fader_level: int = 100              # 0..127 (Jamulus mixer convention)
    muted: bool = False
    solo: bool = False
    is_connected: bool = True
    is_local: bool = False
    video_connected: bool = False
    audio_level: float = 0.0            # 0..1


class ParticipantCard(QFrame):
    """
    Unified card showing one participant's video tile + audio control.

    Emits signals instead of mutating state directly; ApplicationController
    listens and routes to the appropriate service (Jamulus for fader/mute,
    Webex for video-related actions).
    """

    fader_changed = Signal(int, int)    # channel_id, level(0..127)
    mute_toggled = Signal(int, bool)    # channel_id, muted
    solo_toggled = Signal(int, bool)    # channel_id, solo

    CARD_MIN_WIDTH = 260
    CARD_MIN_HEIGHT = 220

    def __init__(
        self,
        presentation: ParticipantPresentation,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ParticipantCard")
        self.setMinimumSize(self.CARD_MIN_WIDTH, self.CARD_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self._presentation = presentation

        self._video_tile = self._build_video_tile()
        self._name_label = QLabel(presentation.name)
        self._name_label.setObjectName("ParticipantName")
        self._role_label = QLabel(presentation.role or self._default_role_label())
        self._role_label.setObjectName("ParticipantRole")
        self._fader_value = QLabel(self._format_fader(presentation.fader_level))
        self._fader_value.setObjectName("FaderValue")
        self._fader_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._level_meter = LevelMeter(self, height=4)
        self._level_meter.set_level(presentation.audio_level)

        self._fader = QSlider(Qt.Orientation.Horizontal)
        self._fader.setRange(0, 127)
        self._fader.setValue(presentation.fader_level)
        self._fader.setTracking(True)
        self._fader.valueChanged.connect(self._on_fader_value_changed)

        self._mute_button = QPushButton("MUTE")
        self._mute_button.setObjectName("PillButton")
        self._mute_button.setCheckable(True)
        self._mute_button.setChecked(presentation.muted)
        self._mute_button.clicked.connect(self._on_mute_clicked)
        self._apply_mute_state(presentation.muted)

        self._solo_button = QPushButton("SOLO")
        self._solo_button.setObjectName("PillButton")
        self._solo_button.setCheckable(True)
        self._solo_button.setChecked(presentation.solo)
        self._solo_button.clicked.connect(self._on_solo_clicked)
        self._apply_solo_state(presentation.solo)

        self._compose_layout()
        self._apply_connection_state()

    # ------------------------------------------------------------------
    # Public API — called by ApplicationController to push state down
    # ------------------------------------------------------------------
    def update_presentation(self, presentation: ParticipantPresentation) -> None:
        self._presentation = presentation
        self._name_label.setText(presentation.name)
        self._role_label.setText(presentation.role or self._default_role_label())
        self._fader_value.setText(self._format_fader(presentation.fader_level))
        self._fader.blockSignals(True)
        self._fader.setValue(presentation.fader_level)
        self._fader.blockSignals(False)
        self._mute_button.blockSignals(True)
        self._mute_button.setChecked(presentation.muted)
        self._mute_button.blockSignals(False)
        self._apply_mute_state(presentation.muted)
        self._solo_button.blockSignals(True)
        self._solo_button.setChecked(presentation.solo)
        self._solo_button.blockSignals(False)
        self._apply_solo_state(presentation.solo)
        self._level_meter.set_level(presentation.audio_level)
        self._apply_connection_state()

    def set_audio_level(self, level: float) -> None:
        """Push instantaneous meter level without rebuilding the whole card."""
        self._presentation.audio_level = level
        self._level_meter.set_level(level)
        self._refresh_speaking_state(level)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def _build_video_tile(self) -> QWidget:
        tile = QFrame()
        tile.setObjectName("VideoTile")
        tile.setMinimumHeight(120)
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        placeholder = QLabel("Video arrives when Webex is connected", tile)
        placeholder.setObjectName("VideoPlaceholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.addWidget(placeholder)
        return tile

    def _compose_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._video_tile, stretch=1)

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        body_layout.setSpacing(Space.SM)

        # Name + role
        identity_row = QHBoxLayout()
        identity_row.setSpacing(Space.SM)
        identity_col = QVBoxLayout()
        identity_col.setSpacing(2)
        identity_col.addWidget(self._name_label)
        identity_col.addWidget(self._role_label)
        identity_row.addLayout(identity_col, stretch=1)
        identity_row.addWidget(self._fader_value)
        body_layout.addLayout(identity_row)

        # Meter + fader
        body_layout.addWidget(self._level_meter)
        body_layout.addWidget(self._fader)

        # Mute / Solo
        controls_row = QHBoxLayout()
        controls_row.setSpacing(Space.SM)
        controls_row.addWidget(self._mute_button)
        controls_row.addWidget(self._solo_button)
        controls_row.addStretch(1)
        body_layout.addLayout(controls_row)

        outer.addWidget(body)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_fader_value_changed(self, value: int) -> None:
        self._presentation.fader_level = value
        self._fader_value.setText(self._format_fader(value))
        self.fader_changed.emit(self._presentation.channel_id, value)

    def _on_mute_clicked(self) -> None:
        muted = self._mute_button.isChecked()
        self._presentation.muted = muted
        self._apply_mute_state(muted)
        self.mute_toggled.emit(self._presentation.channel_id, muted)

    def _on_solo_clicked(self) -> None:
        solo = self._solo_button.isChecked()
        self._presentation.solo = solo
        self._apply_solo_state(solo)
        self.solo_toggled.emit(self._presentation.channel_id, solo)

    # ------------------------------------------------------------------
    # View-state helpers
    # ------------------------------------------------------------------
    def _apply_mute_state(self, muted: bool) -> None:
        self._mute_button.setProperty("state", "active-mute" if muted else "")
        self._repolish(self._mute_button)

    def _apply_solo_state(self, solo: bool) -> None:
        self._solo_button.setProperty("state", "active-solo" if solo else "")
        self._repolish(self._solo_button)

    def _apply_connection_state(self) -> None:
        self.setProperty("connected", "true" if self._presentation.is_connected else "false")
        self._repolish(self)

    def _refresh_speaking_state(self, level: float) -> None:
        speaking = level > 0.15 and not self._presentation.muted
        current = self.property("speaking")
        if current != ("true" if speaking else "false"):
            self.setProperty("speaking", "true" if speaking else "false")
            self._repolish(self)

    def _default_role_label(self) -> str:
        bits: list[str] = []
        if self._presentation.is_local:
            bits.append("You")
        if self._presentation.video_connected:
            bits.append("Video")
        else:
            bits.append("Audio only")
        return " · ".join(bits)

    @staticmethod
    def _format_fader(level: int) -> str:
        # Rough dB mapping: 100 = 0dB, 0 = -inf, 127 = +6dB
        if level <= 0:
            return "-∞ dB"
        db = round(20.0 * (level / 100.0 - 1.0) * 3, 1)
        sign = "+" if db > 0 else ""
        return f"{sign}{db:.1f} dB"

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

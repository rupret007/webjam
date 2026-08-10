"""
ParticipantCard — the atomic unit of the Conductor UI.

One card = one person. It fuses:
  - Participant presence  (top tile)
  - Jamulus audio control (fader + meter + mute/solo)
  - Name and role

Webex opens externally and never renders participant media in WebJam. The
fader drives Jamulus mix state through the supported control path.
"""

from __future__ import annotations

import math
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
    # Durable peer enrollment identity; Jamulus channel_id and display name
    # are both transient and may change on reconnect.
    participant_id: str = ""
    # Position in Jamulus's server-ordered roster. This is presentation
    # metadata only; recording authority additionally requires the exact
    # digest/generations and the host's fresh presence challenge.
    roster_ordinal: int | None = None
    # Per-take recording truth from core.recording_sources (state values
    # such as "waiting"/"recording"/"conflicted"); empty when no take is
    # active. Presentation-only: it never carries digests or fingerprints.
    recording_state: str = ""


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
    CARD_MIN_HEIGHT = 228

    def __init__(
        self,
        presentation: ParticipantPresentation,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ParticipantCard")
        self.setMinimumSize(self.CARD_MIN_WIDTH, self.CARD_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(presentation.name)

        self._presentation = presentation

        self._avatar_label = QLabel(self._initials(presentation.name))
        self._avatar_label.setObjectName("ParticipantAvatar")
        self._avatar_label.setFixedSize(64, 64)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_label.setTextFormat(Qt.TextFormat.PlainText)
        self._video_tile = self._build_video_tile()
        self._name_label = QLabel(presentation.name)
        self._name_label.setObjectName("ParticipantName")
        self._name_label.setAccessibleName("Participant name")
        # Names/roles arrive from the Jamulus roster (other musicians —
        # untrusted); QLabel's AutoText default would render them as HTML.
        self._name_label.setTextFormat(Qt.TextFormat.PlainText)
        self._role_label = QLabel(presentation.role or self._default_role_label())
        self._role_label.setObjectName("ParticipantRole")
        self._role_label.setAccessibleName("Participant role")
        self._recording_label = QLabel()
        self._recording_label.setObjectName("ParticipantRecordingState")
        self._recording_label.setAccessibleName("Recording status")
        self._recording_label.setTextFormat(Qt.TextFormat.PlainText)
        self._recording_label.setVisible(False)
        self._role_label.setTextFormat(Qt.TextFormat.PlainText)
        self._fader_value = QLabel(self._format_fader(presentation.fader_level))
        self._fader_value.setObjectName("FaderValue")
        self._fader_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # external_tick=True: ApplicationController drives decay globally
        # (one QTimer instead of one per card — see LevelMeter docstring).
        self._level_meter = LevelMeter(self, height=4, external_tick=True)
        self._level_meter.set_level(presentation.audio_level)
        self._level_meter.setAccessibleName("Audio level meter")

        self._fader = QSlider(Qt.Orientation.Horizontal)
        self._fader.setRange(0, 127)
        self._fader.setValue(presentation.fader_level)
        self._fader.setTracking(True)
        # Useful keyboard step sizes — default of 1 is too fine for a 128-level
        # fader (would take 20+ arrow presses to make a meaningful change).
        self._fader.setSingleStep(5)   # arrow-key step
        self._fader.setPageStep(15)    # PageUp/PageDown step
        self._fader.setAccessibleName(f"Volume fader for {presentation.name} (decibels)")
        self._fader.setAccessibleDescription(
            f"Volume for {presentation.name}, currently {self._format_fader(presentation.fader_level)}. "
            f"Use left/right arrows to adjust, Page Up/Down for larger steps, double-click to reset to 0 dB."
        )
        self._fader.setToolTip("Volume fader — double-click to reset to 0 dB")
        self._fader.valueChanged.connect(self._on_fader_value_changed)
        # Double-click resets fader to 0 dB (level 100 = unity gain)
        self._fader.mouseDoubleClickEvent = lambda _: self._reset_fader()

        self._mute_button = QPushButton("Mute")
        self._mute_button.setObjectName("PillButton")
        self._mute_button.setCheckable(True)
        self._mute_button.setChecked(presentation.muted)
        self._mute_button.setAccessibleName(f"Mute {presentation.name}")
        self._mute_button.clicked.connect(self._on_mute_clicked)
        self._apply_mute_state(presentation.muted)

        self._solo_button = QPushButton("Solo")
        self._solo_button.setObjectName("PillButton")
        self._solo_button.setCheckable(True)
        self._solo_button.setChecked(presentation.solo)
        self._solo_button.setAccessibleName(f"Solo {presentation.name}")
        self._solo_button.clicked.connect(self._on_solo_clicked)
        self._apply_solo_state(presentation.solo)

        self._compose_layout()
        self._apply_recording_state(presentation.recording_state)
        self._apply_connection_state()
        self._apply_local_state(presentation.is_local)
        self._update_accessibility()

    # ------------------------------------------------------------------
    # Public API — called by ApplicationController to push state down
    # ------------------------------------------------------------------
    def update_presentation(self, presentation: ParticipantPresentation) -> None:
        self._presentation = presentation
        self._avatar_label.setText(self._initials(presentation.name))
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
        self._apply_recording_state(presentation.recording_state)
        self._apply_connection_state()
        self._apply_local_state(presentation.is_local)
        self._sync_mute_label()
        self._update_accessibility()
        # Refresh accessible names to track the (possibly renamed) participant
        self._fader.setAccessibleName(f"Volume fader for {presentation.name} (decibels)")
        self._sync_mute_label()
        self._solo_button.setAccessibleName(f"Solo {presentation.name}")

    _RECORDING_STATE_TEXT = {
        "armed": "Armed",
        "waiting": "Waiting…",
        "recording": "● REC",
        "conflicted": "Needs attention",
        "missing": "Missing",
        "finalized": "Saved",
    }

    def _apply_recording_state(self, state: str) -> None:
        text = self._RECORDING_STATE_TEXT.get(str(state or "").lower(), "")
        self._recording_label.setText(text)
        self._recording_label.setVisible(bool(text))
        if self.property("recordingState") != (state or ""):
            self.setProperty("recordingState", state or "")
            self._repolish(self)

    def set_audio_level(self, level: float) -> None:
        """Push instantaneous meter level without rebuilding the whole card."""
        self._presentation.audio_level = level
        self._level_meter.set_level(level)
        self._refresh_speaking_state(level)

    def tick_meter(self) -> None:
        """Drive one decay step on this card's level meter.

        Called by ParticipantGrid.tick_all_meters from the global meter
        tick timer in ApplicationController.
        """
        self._level_meter.tick_decay()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def _build_video_tile(self) -> QWidget:
        """Audio-only meeting surface with one centered musician avatar."""
        tile = QFrame()
        tile.setObjectName("VideoTile")
        tile.setMinimumHeight(96)
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        layout.addStretch(1)
        avatar_row = QHBoxLayout()
        avatar_row.addStretch(1)
        avatar_row.addWidget(self._avatar_label)
        avatar_row.addStretch(1)
        layout.addLayout(avatar_row)
        layout.addStretch(1)
        return tile

    def _compose_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The musician, not the mixer chrome, owns most of the tile.
        outer.addWidget(self._video_tile, stretch=1)

        body = QWidget(self)
        body.setObjectName("ParticipantCardBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        body_layout.setSpacing(Space.XS)

        # Name + role
        identity_row = QHBoxLayout()
        identity_row.setSpacing(Space.SM)
        identity_col = QVBoxLayout()
        identity_col.setSpacing(2)
        identity_col.addWidget(self._name_label)
        role_row = QHBoxLayout()
        role_row.setSpacing(Space.SM)
        role_row.addWidget(self._role_label)
        role_row.addWidget(self._recording_label)
        role_row.addStretch(1)
        identity_col.addLayout(role_row)
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
    def _reset_fader(self) -> None:
        """Double-click handler — reset fader to unity gain (0 dB = level 100)."""
        self._fader.setValue(100)

    def _on_fader_value_changed(self, value: int) -> None:
        self._presentation.fader_level = value
        self._fader_value.setText(self._format_fader(value))
        # Refresh the accessible description so screen readers announce the
        # new dB value when the user changes the fader by keyboard.
        self._fader.setAccessibleDescription(
            f"Volume for {self._presentation.name}, currently {self._format_fader(value)}. "
            f"Use left/right arrows to adjust, Page Up/Down for larger steps, double-click to reset to 0 dB."
        )
        self.fader_changed.emit(self._presentation.channel_id, value)

    def _on_mute_clicked(self) -> None:
        muted = self._mute_button.isChecked()
        self._presentation.muted = muted
        self._apply_mute_state(muted)
        self._sync_mute_label()
        self._update_accessibility()
        self.mute_toggled.emit(self._presentation.channel_id, muted)

    def _on_solo_clicked(self) -> None:
        solo = self._solo_button.isChecked()
        self._presentation.solo = solo
        self._apply_solo_state(solo)
        self._update_accessibility()
        self.solo_toggled.emit(self._presentation.channel_id, solo)

    # ------------------------------------------------------------------
    # View-state helpers
    # ------------------------------------------------------------------
    def _apply_mute_state(self, muted: bool) -> None:
        self._mute_button.setProperty("state", "active-mute" if muted else "")
        self._repolish(self._mute_button)
        # Reflect mute on the card itself so QSS can fade the muted card.
        self.setProperty("muted", "true" if muted else "false")
        self._repolish(self)
        self._sync_mute_label()

    def _apply_solo_state(self, solo: bool) -> None:
        self._solo_button.setProperty("state", "active-solo" if solo else "")
        self._repolish(self._solo_button)

    def _apply_connection_state(self) -> None:
        self.setProperty("connected", "true" if self._presentation.is_connected else "false")
        self._repolish(self)

    def _apply_local_state(self, is_local: bool) -> None:
        self.setProperty("local", "true" if is_local else "false")
        self._repolish(self)
        self._sync_mute_label()

    def _sync_mute_label(self) -> None:
        """Make the local monitor control distinct from band transmit mute."""
        muted = bool(self._presentation.muted)
        name = self._presentation.name
        if self._presentation.is_local:
            text = "Unmute Monitor" if muted else "Mute Monitor"
            accessible = f"{text} for {name}; this changes only what you hear"
        else:
            text = "Unmute" if muted else "Mute"
            accessible = f"{text} {name} in your monitor mix"
        self._mute_button.setText(text)
        self._mute_button.setAccessibleName(accessible)

    def _update_accessibility(self) -> None:
        status = "connected" if self._presentation.is_connected else "disconnected"
        mix = "muted in your monitor" if self._presentation.muted else "audible"
        solo = ", soloed" if self._presentation.solo else ""
        role = self._presentation.role or self._default_role_label()
        self.setAccessibleName(self._presentation.name)
        recording_text = self._RECORDING_STATE_TEXT.get(
            str(self._presentation.recording_state or "").lower(), ""
        )
        recording = (
            f" Recording: {recording_text}." if recording_text else ""
        )
        self.setAccessibleDescription(
            f"{role}. {status}. {mix}{solo}.{recording}"
        )

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
    def _initials(name: str) -> str:
        parts = [p for p in name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @staticmethod
    def _format_fader(level: int) -> str:
        # dB mapping: 0 = -inf, 100 = 0 dB, 127 = +6 dB
        if level <= 0:
            return "-\u221e dB"
        if level <= 100:
            db = 20.0 * math.log10(level / 100.0)
            return f"{db:.1f} dB"
        db = (level - 100) / 27.0 * 6.0
        return f"+{db:.1f} dB"

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

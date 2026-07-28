"""Accessible mixer and automation editors for standalone Reference Studio."""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.studio_project import (
    StudioAutomationParameter,
    StudioDocument,
    StudioEffectKind,
    StudioTrack,
    StudioTrackKind,
)


def gain_to_db(gain: float) -> float:
    value = float(gain)
    if value <= 0.001:
        return -60.0
    return max(-60.0, min(12.0, 20.0 * math.log10(value)))


def db_to_gain(db: float) -> float:
    value = float(db)
    if value <= -59.95:
        return 0.0
    return max(0.0, min(4.0, math.pow(10.0, value / 20.0)))


class ReferenceStudioMixerDialog(QDialog):
    """Edit practical static channel strips without owning project state."""

    track_fader_changed = Signal(str, float)
    track_pan_changed = Signal(str, float)
    track_mute_changed = Signal(str, bool)
    track_solo_changed = Signal(str, bool)
    track_reverb_send_changed = Signal(str, float)
    track_effect_changed = Signal(str, str, bool)
    master_changed = Signal(float, bool)

    def __init__(
        self,
        document: StudioDocument,
        *,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(document, StudioDocument):
            raise TypeError("document must be a StudioDocument.")
        super().__init__(parent)
        self.setObjectName("ReferenceStudioMixerDialog")
        self.setWindowTitle("Reference Studio Mixer")
        self.setAccessibleName("Reference Studio mixer")
        self.setModal(False)
        self.resize(980, 540)
        self.setMinimumSize(720, 420)
        self.track_controls: dict[str, dict[str, QWidget]] = {}

        root = QVBoxLayout(self)
        introduction = QLabel(
            "Channel-strip changes are non-destructive and shared by playback "
            "and bounce. Reverb uses one shared bus."
        )
        introduction.setWordWrap(True)
        introduction.setAccessibleName("Mixer safety description")
        root.addWidget(introduction)

        scroll = QScrollArea()
        scroll.setObjectName("ReferenceStudioMixerScroll")
        scroll.setWidgetResizable(True)
        scroll.setAccessibleName("Mixer channel strips")
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(8, 8, 8, 8)
        headers = (
            "Track",
            "Fader dB",
            "Pan",
            "Mute",
            "Solo",
            "Reverb %",
            "HPF",
            "EQ",
            "Comp",
            "Gate",
        )
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName("ReferenceStudioMixerHeader")
            grid.addWidget(label, 0, column)

        tracks = tuple(
            sorted(document.tracks, key=lambda item: (item.order, item.track_id))
        )
        for row, track in enumerate(tracks, start=1):
            self._add_track_row(grid, row, track, tracks)
        grid.setRowStretch(len(tracks) + 1, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        master = QFrame()
        master.setObjectName("ReferenceStudioMixerMaster")
        master_layout = QHBoxLayout(master)
        master_layout.addWidget(QLabel("Master"))
        self.master_gain = self._spin(
            "Master fader in decibels",
            minimum=-60.0,
            maximum=12.0,
            step=0.25,
            value=gain_to_db(document.master.gain),
            suffix=" dB",
        )
        self.master_gain.setObjectName("ReferenceStudioMasterGain")
        self.master_limiter = QCheckBox("Safety limiter")
        self.master_limiter.setObjectName("ReferenceStudioMasterLimiter")
        self.master_limiter.setAccessibleName("Enable master safety limiter")
        self.master_limiter.setChecked(document.master.limiter_enabled)
        master_layout.addWidget(self.master_gain)
        master_layout.addWidget(self.master_limiter)
        master_layout.addStretch(1)
        self.master_gain.editingFinished.connect(self._emit_master)
        self.master_limiter.toggled.connect(self._emit_master)
        root.addWidget(master)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.setObjectName("ReferenceStudioMixerButtons")
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

    @staticmethod
    def _spin(
        accessible_name: str,
        *,
        minimum: float,
        maximum: float,
        step: float,
        value: float,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setAccessibleName(accessible_name)
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setSingleStep(step)
        control.setValue(value)
        control.setSuffix(suffix)
        control.setKeyboardTracking(False)
        return control

    def _add_track_row(
        self,
        grid: QGridLayout,
        row: int,
        track: StudioTrack,
        tracks: tuple[StudioTrack, ...],
    ) -> None:
        name = track.name or track.kind.value.title()
        label = QLabel(name)
        label.setAccessibleName(f"{name} mixer channel")
        grid.addWidget(label, row, 0)

        fader = self._spin(
            f"{name} fader in decibels",
            minimum=-60.0,
            maximum=12.0,
            step=0.25,
            value=gain_to_db(track.fader_gain),
            suffix=" dB",
        )
        pan = self._spin(
            f"{name} pan from left to right",
            minimum=-100.0,
            maximum=100.0,
            step=1.0,
            value=track.pan * 100.0,
        )
        mute = QCheckBox()
        mute.setAccessibleName(f"Mute {name}")
        mute.setChecked(track.muted)
        solo = QCheckBox()
        solo.setAccessibleName(f"Solo {name}")
        solo.setChecked(track.solo)
        solo.setEnabled(track.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING})
        send = self._spin(
            f"{name} shared reverb send percent",
            minimum=0.0,
            maximum=100.0,
            step=1.0,
            value=self._reverb_send_percent(track, tracks),
            suffix=" %",
        )
        send.setEnabled(track.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING})

        controls: dict[str, QWidget] = {
            "fader": fader,
            "pan": pan,
            "mute": mute,
            "solo": solo,
            "send": send,
        }
        for column, control in enumerate((fader, pan, mute, solo, send), start=1):
            grid.addWidget(control, row, column)

        fader.editingFinished.connect(
            lambda item=track.track_id, control=fader: self.track_fader_changed.emit(
                item, db_to_gain(control.value())
            )
        )
        pan.editingFinished.connect(
            lambda item=track.track_id, control=pan: self.track_pan_changed.emit(
                item, control.value() / 100.0
            )
        )
        mute.toggled.connect(
            lambda checked, item=track.track_id: self.track_mute_changed.emit(
                item, checked
            )
        )
        solo.toggled.connect(
            lambda checked, item=track.track_id: self.track_solo_changed.emit(
                item, checked
            )
        )
        send.editingFinished.connect(
            lambda item=track.track_id, control=send: (
                self.track_reverb_send_changed.emit(item, control.value() / 100.0)
            )
        )

        kinds = (
            StudioEffectKind.HPF,
            StudioEffectKind.EQ,
            StudioEffectKind.COMPRESSOR,
            StudioEffectKind.GATE,
        )
        supported = track.kind in {
            StudioTrackKind.AUDIO,
            StudioTrackKind.BACKING,
            StudioTrackKind.BUS,
        }
        for column, kind in enumerate(kinds, start=6):
            effect = next((item for item in track.effects if item.kind is kind), None)
            toggle = QCheckBox()
            toggle.setAccessibleName(f"Enable {kind.value} on {name}")
            toggle.setChecked(bool(effect and effect.enabled))
            toggle.setEnabled(supported)
            toggle.toggled.connect(
                lambda checked, item=track.track_id, effect_kind=kind: (
                    self.track_effect_changed.emit(
                        item,
                        effect_kind.value,
                        checked,
                    )
                )
            )
            grid.addWidget(toggle, row, column)
            controls[kind.value] = toggle
        self.track_controls[track.track_id] = controls

    @staticmethod
    def _reverb_send_percent(
        track: StudioTrack,
        tracks: tuple[StudioTrack, ...],
    ) -> float:
        reverb_buses = {
            item.track_id
            for item in tracks
            if item.kind is StudioTrackKind.BUS
            and any(
                effect.kind is StudioEffectKind.REVERB and effect.enabled
                for effect in item.effects
            )
        }
        send = next(
            (
                item
                for item in track.sends
                if item.target_bus_id in reverb_buses and item.enabled
            ),
            None,
        )
        return 0.0 if send is None else send.gain * 100.0

    def _emit_master(self, *_args) -> None:
        self.master_changed.emit(
            db_to_gain(self.master_gain.value()),
            self.master_limiter.isChecked(),
        )


class ReferenceStudioAutomationDialog(QDialog):
    """Add or replace one automation point at the current playhead."""

    point_requested = Signal(str, str, int, float)
    clear_requested = Signal(str, str)

    def __init__(
        self,
        track: StudioTrack,
        *,
        playhead_frame: int,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(track, StudioTrack):
            raise TypeError("track must be a StudioTrack.")
        if isinstance(playhead_frame, bool) or not isinstance(playhead_frame, int):
            raise TypeError("playhead_frame must be an integer.")
        super().__init__(parent)
        self._track = track
        self._playhead_frame = max(0, playhead_frame)
        name = track.name or "Selected track"
        self.setObjectName("ReferenceStudioAutomationDialog")
        self.setWindowTitle(f"Automation — {name}")
        self.setAccessibleName(f"Automation editor for {name}")
        self.setModal(False)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        description = QLabel(
            "Add or replace a sample-accurate point at the playhead. Volume and "
            "pan interpolate; mute holds until the next point."
        )
        description.setWordWrap(True)
        description.setAccessibleName("Automation editor description")
        layout.addWidget(description)

        row = QHBoxLayout()
        self.parameter = QComboBox()
        self.parameter.setObjectName("ReferenceStudioAutomationParameter")
        self.parameter.setAccessibleName("Automation parameter")
        for parameter, text in (
            (StudioAutomationParameter.VOLUME, "Volume"),
            (StudioAutomationParameter.PAN, "Pan"),
            (StudioAutomationParameter.MUTE, "Mute"),
        ):
            self.parameter.addItem(text, parameter.value)
        self.value = QDoubleSpinBox()
        self.value.setObjectName("ReferenceStudioAutomationValue")
        self.value.setAccessibleName("Automation value")
        self.value.setKeyboardTracking(False)
        self.parameter.currentIndexChanged.connect(self._configure_value)
        row.addWidget(self.parameter)
        row.addWidget(self.value)
        layout.addLayout(row)
        self._configure_value()

        self.summary = QLabel()
        self.summary.setObjectName("ReferenceStudioAutomationSummary")
        self.summary.setAccessibleName("Existing automation summary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self._refresh_summary()

        actions = QHBoxLayout()
        self.add_button = QPushButton("Add / Replace at Playhead")
        self.add_button.setObjectName("ReferenceStudioAutomationAdd")
        self.add_button.setAccessibleName("Add or replace automation at playhead")
        self.clear_button = QPushButton("Clear Parameter Lane")
        self.clear_button.setObjectName("ReferenceStudioAutomationClear")
        self.clear_button.setAccessibleName("Clear selected automation parameter")
        self.add_button.clicked.connect(self._request_point)
        self.clear_button.clicked.connect(self._request_clear)
        actions.addWidget(self.add_button)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)

        location = QLabel(f"Playhead frame: {self._playhead_frame}")
        location.setAccessibleName("Automation playhead frame")
        layout.addWidget(location)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _parameter(self) -> StudioAutomationParameter:
        value = self.parameter.currentData()
        try:
            return (
                value
                if isinstance(value, StudioAutomationParameter)
                else StudioAutomationParameter(str(value))
            )
        except ValueError:
            return StudioAutomationParameter.VOLUME

    def _configure_value(self, *_args) -> None:
        parameter = self._parameter()
        self.value.blockSignals(True)
        if parameter is StudioAutomationParameter.VOLUME:
            self.value.setRange(-60.0, 12.0)
            self.value.setSingleStep(0.25)
            self.value.setSuffix(" dB")
            self.value.setValue(gain_to_db(self._track.fader_gain))
        elif parameter is StudioAutomationParameter.PAN:
            self.value.setRange(-100.0, 100.0)
            self.value.setSingleStep(1.0)
            self.value.setSuffix("")
            self.value.setValue(self._track.pan * 100.0)
        else:
            self.value.setRange(0.0, 1.0)
            self.value.setSingleStep(1.0)
            self.value.setDecimals(0)
            self.value.setSuffix(" (1 = muted)")
            self.value.setValue(1.0 if self._track.muted else 0.0)
        if parameter is not StudioAutomationParameter.MUTE:
            self.value.setDecimals(2)
        self.value.blockSignals(False)
        self._refresh_summary()

    def _automation_value(self) -> float:
        parameter = self._parameter()
        if parameter is StudioAutomationParameter.VOLUME:
            return db_to_gain(self.value.value())
        if parameter is StudioAutomationParameter.PAN:
            return self.value.value() / 100.0
        return 1.0 if self.value.value() >= 0.5 else 0.0

    def _refresh_summary(self) -> None:
        if not hasattr(self, "summary"):
            return
        parameter = self._parameter()
        lane = next(
            (item for item in self._track.automation if item.parameter is parameter),
            None,
        )
        self.summary.setText(
            "No existing points for this parameter."
            if lane is None
            else f"{len(lane.points)} existing point"
            f"{'' if len(lane.points) == 1 else 's'}; "
            f"{lane.interpolation.value} interpolation."
        )

    def _request_point(self) -> None:
        self.point_requested.emit(
            self._track.track_id,
            self._parameter().value,
            self._playhead_frame,
            self._automation_value(),
        )

    def _request_clear(self) -> None:
        self.clear_requested.emit(
            self._track.track_id,
            self._parameter().value,
        )

    def refresh_track(self, track: StudioTrack) -> None:
        """Refresh the same open editor after an immutable automation edit."""

        if not isinstance(track, StudioTrack):
            raise TypeError("track must be a StudioTrack.")
        if track.track_id != self._track.track_id:
            raise ValueError("Automation refresh must preserve the track identity.")
        self._track = track
        self._configure_value()
        self._refresh_summary()


__all__ = [
    "ReferenceStudioAutomationDialog",
    "ReferenceStudioMixerDialog",
    "db_to_gain",
    "gain_to_db",
]

"""Small, accessible dialogs for Reference Studio offline project tools."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.song_bounce import BounceFormat


@dataclass(frozen=True, slots=True)
class ReferenceStudioBounceOptions:
    audio_format: BounceFormat
    include_backing: bool
    create_stems: bool
    selected_track_only: bool
    use_cycle_range: bool


class ReferenceStudioBounceDialog(QDialog):
    """Collect a bounded set of bounce choices before the destination picker."""

    def __init__(
        self,
        *,
        backing_available: bool,
        selected_audio_track_available: bool,
        cycle_available: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReferenceStudioBounceDialog")
        self.setWindowTitle("Bounce Reference Studio Project")
        self.setAccessibleName("Bounce Reference Studio project")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Create a verified 24-bit stereo mix. Optional stems use the same "
            "non-destructive Studio renderer as playback."
        )
        introduction.setWordWrap(True)
        introduction.setAccessibleName("Bounce description")
        layout.addWidget(introduction)

        form = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("ReferenceStudioBounceFormat")
        self.format_combo.setAccessibleName("Bounce audio format")
        self.format_combo.addItem("24-bit WAV", BounceFormat.WAV.value)
        self.format_combo.addItem("24-bit FLAC", BounceFormat.FLAC.value)
        self.format_combo.setToolTip(
            "MP3 is offered only when a separately tested, license-safe encoder "
            "is available. This build provides lossless WAV and FLAC."
        )
        form.addRow("Format:", self.format_combo)
        layout.addLayout(form)

        self.include_backing = QCheckBox("Include Reference / Backing Track")
        self.include_backing.setObjectName("ReferenceStudioBounceBacking")
        self.include_backing.setAccessibleName(
            "Include the Reference or Backing Track in the bounce"
        )
        self.include_backing.setChecked(backing_available)
        self.include_backing.setEnabled(backing_available)
        layout.addWidget(self.include_backing)

        self.create_stems = QCheckBox("Also create one processed stem per track")
        self.create_stems.setObjectName("ReferenceStudioBounceStems")
        self.create_stems.setAccessibleName("Create processed track stems")
        layout.addWidget(self.create_stems)

        self.selected_track_only = QCheckBox("Bounce selected audio track only")
        self.selected_track_only.setObjectName("ReferenceStudioBounceSelectedTrack")
        self.selected_track_only.setAccessibleName("Bounce only the selected audio track")
        self.selected_track_only.setEnabled(selected_audio_track_available)
        layout.addWidget(self.selected_track_only)

        self.use_cycle_range = QCheckBox("Use the enabled cycle range")
        self.use_cycle_range.setObjectName("ReferenceStudioBounceCycle")
        self.use_cycle_range.setAccessibleName("Bounce only the enabled cycle range")
        self.use_cycle_range.setEnabled(cycle_available)
        layout.addWidget(self.use_cycle_range)

        capability = QLabel(
            "MP3 bounce is unavailable because this build has no self-tested "
            "license-safe MP3 encoder. WAV and FLAC remain available."
        )
        capability.setObjectName("ReferenceStudioBounceCapability")
        capability.setWordWrap(True)
        capability.setAccessibleName("MP3 bounce capability")
        layout.addWidget(capability)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.setObjectName("ReferenceStudioBounceButtons")
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("Choose Destination…")
        save_button.setAccessibleName("Choose bounce destination")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def options(self) -> ReferenceStudioBounceOptions:
        value = self.format_combo.currentData()
        try:
            value = (
                value
                if isinstance(value, BounceFormat)
                else BounceFormat(str(value))
            )
        except ValueError:
            value = BounceFormat.WAV
        return ReferenceStudioBounceOptions(
            audio_format=value,
            include_backing=(
                self.include_backing.isEnabled() and self.include_backing.isChecked()
            ),
            create_stems=self.create_stems.isChecked(),
            selected_track_only=(
                self.selected_track_only.isEnabled()
                and self.selected_track_only.isChecked()
            ),
            use_cycle_range=(
                self.use_cycle_range.isEnabled() and self.use_cycle_range.isChecked()
            ),
        )


@dataclass(frozen=True, slots=True)
class ReferenceStudioTempoChoice:
    bpm: float
    numerator: int
    denominator: int


class ReferenceStudioTempoReviewDialog(QDialog):
    """Review a detected constant tempo and apply an explicit correction."""

    def __init__(
        self,
        *,
        detected_bpm: float,
        confidence_percent: float,
        numerator: int,
        denominator: int,
        manual_review_recommended: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReferenceStudioTempoReviewDialog")
        self.setWindowTitle("Review Detected Tempo")
        self.setAccessibleName("Review detected backing-track tempo")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"Detected {detected_bpm:.2f} BPM with "
            f"{max(0.0, min(100.0, confidence_percent)):.0f}% confidence."
        )
        summary.setObjectName("ReferenceStudioTempoSummary")
        summary.setWordWrap(True)
        summary.setAccessibleName("Tempo analysis result")
        layout.addWidget(summary)

        guidance = QLabel(
            (
                "Confidence is low. Check the beat against playback and correct "
                "the values before applying them."
                if manual_review_recommended
                else "Review the result and correct it if the detected pulse is half- "
                "or double-time."
            )
        )
        guidance.setObjectName("ReferenceStudioTempoGuidance")
        guidance.setWordWrap(True)
        guidance.setAccessibleName("Tempo review guidance")
        layout.addWidget(guidance)

        form = QFormLayout()
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setObjectName("ReferenceStudioTempoBpm")
        self.bpm_spin.setAccessibleName("Project tempo in beats per minute")
        self.bpm_spin.setRange(20.0, 400.0)
        self.bpm_spin.setDecimals(2)
        self.bpm_spin.setSingleStep(0.1)
        self.bpm_spin.setValue(detected_bpm)
        form.addRow("Tempo (BPM):", self.bpm_spin)

        self.numerator_spin = QSpinBox()
        self.numerator_spin.setObjectName("ReferenceStudioTempoNumerator")
        self.numerator_spin.setAccessibleName("Time-signature numerator")
        self.numerator_spin.setRange(1, 32)
        self.numerator_spin.setValue(numerator)
        form.addRow("Beats per bar:", self.numerator_spin)

        self.denominator_combo = QComboBox()
        self.denominator_combo.setObjectName("ReferenceStudioTempoDenominator")
        self.denominator_combo.setAccessibleName("Time-signature denominator")
        for value in (1, 2, 4, 8, 16):
            self.denominator_combo.addItem(str(value), value)
        index = self.denominator_combo.findData(denominator)
        self.denominator_combo.setCurrentIndex(max(0, index))
        form.addRow("Beat value:", self.denominator_combo)
        layout.addLayout(form)

        note = QLabel(
            "This changes the project grid and click. It does not time-stretch "
            "or modify the imported audio."
        )
        note.setObjectName("ReferenceStudioTempoSafety")
        note.setWordWrap(True)
        note.setAccessibleName("Tempo change safety note")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        buttons.setObjectName("ReferenceStudioTempoButtons")
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Apply to Project")
        apply_button.setAccessibleName("Apply reviewed tempo to project")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setTabOrder(self.bpm_spin, self.numerator_spin)
        self.setTabOrder(self.numerator_spin, self.denominator_combo)

    @property
    def choice(self) -> ReferenceStudioTempoChoice:
        denominator = self.denominator_combo.currentData()
        if isinstance(denominator, bool) or not isinstance(denominator, int):
            denominator = 4
        return ReferenceStudioTempoChoice(
            bpm=self.bpm_spin.value(),
            numerator=self.numerator_spin.value(),
            denominator=denominator,
        )


__all__ = [
    "ReferenceStudioBounceDialog",
    "ReferenceStudioBounceOptions",
    "ReferenceStudioTempoChoice",
    "ReferenceStudioTempoReviewDialog",
]

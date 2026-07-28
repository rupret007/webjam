"""Headless contracts for Reference Studio bounce and tempo-review dialogs."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from core.song_bounce import BounceFormat
from webjam_qt.windows.reference_studio_tools import (
    ReferenceStudioBounceDialog,
    ReferenceStudioTempoReviewDialog,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def test_bounce_dialog_exposes_only_truthful_runtime_capabilities() -> None:
    dialog = ReferenceStudioBounceDialog(
        backing_available=True,
        selected_audio_track_available=True,
        cycle_available=True,
    )
    assert dialog.accessibleName()
    assert dialog.format_combo.count() == 2
    assert {
        BounceFormat(dialog.format_combo.itemData(index))
        for index in range(dialog.format_combo.count())
    } == {BounceFormat.WAV, BounceFormat.FLAC}
    capability = dialog.findChild(QLabel, "ReferenceStudioBounceCapability")
    assert capability is not None
    assert "MP3 bounce is unavailable" in capability.text()
    dialog.create_stems.setChecked(True)
    dialog.selected_track_only.setChecked(True)
    dialog.use_cycle_range.setChecked(True)
    options = dialog.options
    assert options.include_backing
    assert options.create_stems
    assert options.selected_track_only
    assert options.use_cycle_range


def test_bounce_dialog_disables_options_that_have_no_project_source() -> None:
    dialog = ReferenceStudioBounceDialog(
        backing_available=False,
        selected_audio_track_available=False,
        cycle_available=False,
    )
    assert not dialog.include_backing.isEnabled()
    assert not dialog.selected_track_only.isEnabled()
    assert not dialog.use_cycle_range.isEnabled()
    assert not dialog.options.include_backing
    assert not dialog.options.selected_track_only
    assert not dialog.options.use_cycle_range


def test_tempo_review_is_explicit_editable_and_explains_no_time_stretch() -> None:
    dialog = ReferenceStudioTempoReviewDialog(
        detected_bpm=119.75,
        confidence_percent=42.0,
        numerator=6,
        denominator=8,
        manual_review_recommended=True,
    )
    assert dialog.accessibleName()
    summary = dialog.findChild(QLabel, "ReferenceStudioTempoSummary")
    assert summary is not None
    assert "42% confidence" in summary.text()
    dialog.bpm_spin.setValue(120.0)
    dialog.numerator_spin.setValue(4)
    dialog.denominator_combo.setCurrentIndex(
        dialog.denominator_combo.findData(4)
    )
    choice = dialog.choice
    assert choice.bpm == 120.0
    assert choice.numerator == 4
    assert choice.denominator == 4
    safety = dialog.findChild(QLabel, "ReferenceStudioTempoSafety")
    assert safety is not None
    assert "does not time-stretch" in safety.text()

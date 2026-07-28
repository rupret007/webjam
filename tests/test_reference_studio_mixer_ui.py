"""Headless intent and accessibility contracts for Reference Studio mixing."""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.studio_project import (
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioAutomationPoint,
    StudioDocument,
    StudioEffect,
    StudioEffectKind,
    StudioSend,
    StudioTrack,
    StudioTrackKind,
)
from webjam_qt.windows.reference_studio_mixer import (
    ReferenceStudioAutomationDialog,
    ReferenceStudioMixerDialog,
    db_to_gain,
    gain_to_db,
)


def _id(number: int) -> str:
    return str(uuid.UUID(int=number))


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _document() -> StudioDocument:
    reverb = StudioTrack(
        track_id=_id(20),
        order=2,
        name="Shared Reverb",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(StudioEffect(effect_id=_id(21), kind=StudioEffectKind.REVERB),),
    )
    vocal = StudioTrack(
        track_id=_id(10),
        order=0,
        name="Vocal",
        kind=StudioTrackKind.AUDIO,
        channel_count=1,
        fader_gain=0.5,
        pan=-0.25,
        sends=(
            StudioSend(
                send_id=_id(11),
                target_bus_id=reverb.track_id,
                gain=0.3,
            ),
        ),
        automation=(
            StudioAutomationLane(
                lane_id=_id(12),
                parameter=StudioAutomationParameter.VOLUME,
                points=(StudioAutomationPoint(0, 0.5),),
            ),
        ),
    )
    backing = StudioTrack(
        track_id=_id(30),
        order=1,
        name="Backing",
        kind=StudioTrackKind.BACKING,
        channel_count=2,
    )
    return StudioDocument(
        project_id=_id(1),
        project_sample_rate=48_000,
        tracks=(vocal, backing, reverb),
        schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    )


def test_gain_db_mapping_is_bounded_and_round_trips_musician_values() -> None:
    assert gain_to_db(0.0) == -60.0
    assert db_to_gain(-60.0) == 0.0
    assert db_to_gain(gain_to_db(0.5)) == pytest.approx(0.5)
    assert db_to_gain(gain_to_db(1.0)) == pytest.approx(1.0)
    assert db_to_gain(99.0) <= 4.0


def test_mixer_presents_all_channels_and_emits_semantic_edits() -> None:
    document = _document()
    dialog = ReferenceStudioMixerDialog(document)
    assert dialog.accessibleName()
    assert set(dialog.track_controls) == {
        _id(10),
        _id(20),
        _id(30),
    }
    vocal = dialog.track_controls[_id(10)]
    assert vocal["send"].value() == pytest.approx(30.0)
    assert not dialog.track_controls[_id(20)]["solo"].isEnabled()
    faders: list[tuple[str, float]] = []
    pans: list[tuple[str, float]] = []
    mutes: list[tuple[str, bool]] = []
    sends: list[tuple[str, float]] = []
    effects: list[tuple[str, str, bool]] = []
    dialog.track_fader_changed.connect(
        lambda track_id, value: faders.append((track_id, value))
    )
    dialog.track_pan_changed.connect(
        lambda track_id, value: pans.append((track_id, value))
    )
    dialog.track_mute_changed.connect(
        lambda track_id, value: mutes.append((track_id, value))
    )
    dialog.track_reverb_send_changed.connect(
        lambda track_id, value: sends.append((track_id, value))
    )
    dialog.track_effect_changed.connect(
        lambda track_id, kind, enabled: effects.append((track_id, kind, enabled))
    )
    vocal["fader"].setValue(-6.0)
    vocal["fader"].editingFinished.emit()
    vocal["pan"].setValue(40.0)
    vocal["pan"].editingFinished.emit()
    vocal["mute"].click()
    vocal["send"].setValue(25.0)
    vocal["send"].editingFinished.emit()
    vocal["hpf"].click()
    assert faders == [(_id(10), pytest.approx(db_to_gain(-6.0)))]
    assert pans == [(_id(10), 0.4)]
    assert mutes == [(_id(10), True)]
    assert sends == [(_id(10), 0.25)]
    assert effects == [(_id(10), "hpf", True)]


def test_automation_editor_emits_exact_track_parameter_frame_and_value() -> None:
    track = _document().tracks[0]
    dialog = ReferenceStudioAutomationDialog(track, playhead_frame=12_345)
    assert dialog.accessibleName()
    assert "1 existing point" in dialog.summary.text()
    points: list[tuple[str, str, int, float]] = []
    clears: list[tuple[str, str]] = []
    dialog.point_requested.connect(
        lambda track_id, parameter, frame, value: points.append(
            (track_id, parameter, frame, value)
        )
    )
    dialog.clear_requested.connect(
        lambda track_id, parameter: clears.append((track_id, parameter))
    )
    dialog.parameter.setCurrentIndex(
        dialog.parameter.findData(StudioAutomationParameter.PAN.value)
    )
    dialog.value.setValue(25.0)
    dialog.add_button.click()
    dialog.clear_button.click()
    assert points == [(_id(10), "pan", 12_345, 0.25)]
    assert clears == [(_id(10), "pan")]

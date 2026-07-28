"""Headless command and accessibility contracts for Reference Studio."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from webjam_qt.widgets.reference_studio_workspace import (
    ReferenceStudioPresentation,
    ReferenceStudioWorkspace,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def test_workspace_exposes_every_declared_command_once() -> None:
    workspace = ReferenceStudioWorkspace()
    assert set(workspace.actions) == workspace.COMMAND_IDS
    assert len(workspace.actions) == len(workspace.COMMAND_IDS)
    titles = [action.text().replace("&", "") for action in workspace.menu_bar.actions()]
    assert titles == [
        "File",
        "Edit",
        "Track",
        "Region",
        "Mix",
        "Transport",
        "View",
        "Project",
        "Help",
    ]


def test_menu_copy_does_not_promise_unimplemented_mutations() -> None:
    workspace = ReferenceStudioWorkspace()
    assert workspace.actions["collect_media"].text().replace("&", "") == (
        "About Collected Project Media…"
    )
    assert workspace.actions["relink_media"].text().replace("&", "") == (
        "Relink Backing Track…"
    )
    assert workspace.actions["create_take_lane"].text().replace("&", "") == (
        "About Take Lanes…"
    )
    assert workspace.actions["toggle_ruler"].text().replace("&", "") == (
        "Toggle Time / Bars and Beats"
    )
    assert not workspace.actions["select_all"].isEnabled()
    assert "one selected region" in workspace.actions["select_all"].toolTip()


@pytest.mark.parametrize(
    "command",
    sorted(ReferenceStudioWorkspace.COMMAND_IDS),
)
def test_menu_actions_emit_one_semantic_command(command: str) -> None:
    workspace = ReferenceStudioWorkspace()
    events: list[str] = []
    workspace.command_requested.connect(events.append)
    workspace.actions[command].setEnabled(True)
    workspace.actions[command].trigger()
    assert events == [command]


def test_transport_and_header_controls_emit_semantic_commands() -> None:
    workspace = ReferenceStudioWorkspace()
    events: list[str] = []
    workspace.command_requested.connect(events.append)
    workspace.import_backing_button.click()
    workspace.save_button.setEnabled(True)
    workspace.save_button.click()
    workspace.bounce_button.setEnabled(True)
    workspace.bounce_button.click()
    workspace.return_button.click()
    workspace.stop_button.setEnabled(True)
    workspace.stop_button.click()
    workspace.play_button.setEnabled(True)
    workspace.play_button.click()
    workspace.record_button.setEnabled(True)
    workspace.record_button.click()
    assert events == [
        "import_backing",
        "save_project",
        "bounce",
        "return_to_start",
        "stop",
        "play_pause",
        "record",
    ]


def test_tempo_signature_and_snap_emit_exact_values() -> None:
    workspace = ReferenceStudioWorkspace()
    tempos: list[float] = []
    signatures: list[tuple[int, int]] = []
    snaps: list[str] = []
    workspace.tempo_changed.connect(tempos.append)
    workspace.time_signature_changed.connect(
        lambda numerator, denominator: signatures.append((numerator, denominator))
    )
    workspace.snap_changed.connect(snaps.append)
    workspace.tempo.setValue(137.5)
    workspace.tempo.editingFinished.emit()
    workspace.time_signature.setCurrentText("6/8")
    workspace.snap.setCurrentIndex(3)
    assert tempos == [137.5]
    assert signatures == [(6, 8)]
    assert snaps == ["sixteenth"]


def test_presentation_is_path_free_truth_and_controls_capabilities() -> None:
    workspace = ReferenceStudioWorkspace()
    value = ReferenceStudioPresentation(
        project_name="Night Drive",
        save_state="Saved",
        status="Backing track verified",
        backing_track="practice mix.wav",
        position_text="12 3 2 480",
        duration_text="1:42.500",
        track_names=("Vocal", "Guitar"),
        dirty=True,
        playing=True,
        recording=False,
        can_save=True,
        can_play=True,
        can_record=True,
        can_bounce=True,
    )
    workspace.set_presentation(value)
    assert workspace.presentation is value
    assert workspace.project_title.text() == "Night Drive •"
    assert workspace.project_state.text() == "Saved"
    assert workspace.status.text() == "Backing track verified"
    assert workspace.backing_name.text() == "practice mix.wav"
    assert workspace.position.text() == "12 3 2 480"
    assert workspace.elapsed.text() == "1:42.500"
    assert workspace.play_button.text() == "❚❚"
    assert workspace.record_button.text() == "●"
    assert workspace.record_button.accessibleName() == "Record armed tracks"
    assert workspace.track_list.count() == 2
    assert all(
        control.isEnabled()
        for control in (
            workspace.save_button,
            workspace.bounce_button,
            workspace.play_button,
            workspace.record_button,
        )
    )


def test_recording_presentation_exposes_a_truthful_stop_action() -> None:
    workspace = ReferenceStudioWorkspace()
    workspace.set_presentation(
        ReferenceStudioPresentation(
            recording=True,
            can_record=True,
        )
    )
    assert workspace.record_button.text() == "■"
    assert workspace.record_button.accessibleName() == "Stop recording"
    assert "commit" in workspace.record_button.accessibleDescription().lower()


def test_mixer_shortcut_avoids_native_macos_minimize() -> None:
    workspace = ReferenceStudioWorkspace()
    shortcut = workspace.actions["show_mixer"].shortcut()
    if sys.platform == "darwin":
        assert shortcut == QKeySequence(
            Qt.KeyboardModifier.MetaModifier.value | Qt.Key.Key_M.value
        )
        assert shortcut != QKeySequence("Ctrl+M")
    else:
        assert shortcut == QKeySequence("Ctrl+M")


def test_invalid_presentations_fail_closed() -> None:
    with pytest.raises(ValueError):
        ReferenceStudioPresentation(project_name="")
    with pytest.raises(ValueError):
        ReferenceStudioPresentation(track_names=("",))
    with pytest.raises(TypeError):
        ReferenceStudioPresentation(can_play=1)
    workspace = ReferenceStudioWorkspace()
    with pytest.raises(TypeError):
        workspace.set_presentation(object())


def test_workspace_has_accessible_controls_and_compact_arrange_floor() -> None:
    workspace = ReferenceStudioWorkspace()
    assert workspace.accessibleName()
    assert workspace.menu_bar.accessibleName()
    for control in (
        workspace.home_button,
        workspace.import_backing_button,
        workspace.save_button,
        workspace.bounce_button,
        workspace.return_button,
        workspace.stop_button,
        workspace.play_button,
        workspace.record_button,
        workspace.cycle_box,
        workspace.metronome_box,
        workspace.count_in_box,
        workspace.tempo,
        workspace.time_signature,
        workspace.snap,
        workspace.track_list,
        workspace.status,
        workspace.audio_truth,
    ):
        assert control.accessibleName()
    workspace.set_compact(True)
    assert not workspace.splitter.widget(2).isVisibleTo(workspace)
    assert all(
        not control.isVisibleTo(workspace)
        for control in (
            workspace.cycle_box,
            workspace.metronome_box,
            workspace.count_in_box,
        )
    )
    assert all(
        workspace.actions[command].isVisible()
        for command in (
            "toggle_cycle",
            "toggle_metronome",
            "toggle_count_in",
        )
    )
    assert workspace.minimumWidth() <= 640
    assert workspace.minimumHeight() <= 360

"""End-to-end desktop ownership tests for standalone Reference Studio."""

from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
import soundfile as sf

from core.project_playback import ProjectPlaybackState
from core.song_bounce import SongBounceRequest
from core.song_studio_reconcile import reconcile_song_studio_document
from core.studio_project import (
    StudioAutomationInterpolation,
    StudioAutomationParameter,
    StudioEffectKind,
    StudioTrackKind,
)
from core.take_player import TakePlayer
import webjam_qt.controllers.reference_studio_application as reference_app
from webjam_qt.controllers.reference_studio_application import (
    ReferenceStudioApplicationController,
)
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.widgets.reference_studio_shell import ReferenceStudioShell
from webjam_qt.windows.reference_studio_tools import ReferenceStudioTempoChoice


class _TakeSink:
    def play(self, *_args, **_kwargs) -> None:
        return None

    def stop(self) -> None:
        return None


class _ProjectOutput:
    sample_rate = 48_000
    block_frames = 256

    def __init__(self) -> None:
        self.callback = None
        self.started = 0
        self.stopped = 0

    def start(self, callback) -> None:
        self.callback = callback
        self.started += 1

    def stop(self) -> None:
        self.callback = None
        self.stopped += 1

    def abort(self) -> None:
        self.callback = None
        self.stopped += 1

    def pump(self, blocks: int = 1) -> None:
        for _ in range(blocks):
            callback = self.callback
            if callback is not None:
                callback(np.empty((self.block_frames, 2), dtype=np.float32))


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _shell() -> ReferenceStudioShell:
    return ReferenceStudioShell(
        RecordingStudio(player=TakePlayer(samplerate=48_000, sink=_TakeSink()))
    )


def _wait_until(qapp: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Reference Studio work did not finish in time")


def test_play_along_project_import_edit_save_reopen_and_transport(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    output = _ProjectOutput()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=output,
    )
    bundle = tmp_path / "Demo Song"
    project = controller.create_project(bundle, "Demo Song")
    assert project.name == "Demo Song"
    assert shell.current_view() == "project"
    assert bundle.with_suffix(".webjam").is_dir()
    _wait_until(qapp, lambda: controller._catalog is not None)

    backing = tmp_path / "backing with spaces.wav"
    phase = np.arange(12_000, dtype=np.float32) / np.float32(48_000)
    sf.write(
        backing,
        np.column_stack(
            (
                np.sin(phase * np.float32(2 * np.pi * 220)),
                np.sin(phase * np.float32(2 * np.pi * 330)),
            )
        ),
        48_000,
        subtype="FLOAT",
    )
    controller.import_backing(backing)
    _wait_until(
        qapp,
        lambda: (
            controller._renderer is not None
            and controller.workspace.presentation.can_play
        ),
    )
    document = controller.studio_controller.document
    region = next(item for item in document.regions if item.enabled)
    original_start = region.timeline_start_frame
    controller._move_region(region.region_id, 480)
    assert (
        controller.studio_controller.document.region_for(
            region.region_id
        ).timeline_start_frame
        == 480
    )
    assert controller.save()

    controller._play_pause()
    assert controller.playback.state is ProjectPlaybackState.PLAYING
    output.pump(4)
    controller._poll_transport()
    assert controller.playback.snapshot().position_frame > 0
    controller._stop_and_refresh()

    assert controller.close_project(choice="discard")
    assert shell.current_view() == "home"
    reopened = controller.open_project(bundle.with_suffix(".webjam"))
    _wait_until(qapp, lambda: controller._catalog is not None)
    assert reopened.project_id == project.project_id
    assert (
        controller.studio_controller.document.region_for(
            region.region_id
        ).timeline_start_frame
        != original_start
    )
    assert controller.shutdown()


def test_structural_track_edits_preserve_mix_and_collected_media(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Tracks.webjam", "Tracks")
    _wait_until(qapp, lambda: controller._catalog is not None)
    first = controller.studio_controller.document.tracks[0]
    controller._apply_studio_edit(
        "Changed mix",
        lambda document: document.update_track(
            first.track_id,
            fader_gain=0.55,
            pan=0.25,
        ),
    )
    snapshot = controller.project_controller.add_track("Guitar")
    assert snapshot.project is not None
    controller._apply_studio_edit(
        "Added Guitar",
        lambda document: reconcile_song_studio_document(
            snapshot.project,
            document,
        ),
    )
    updated = controller.studio_controller.document
    assert [item.name for item in updated.tracks] == ["Audio 1", "Guitar"]
    assert updated.tracks[0].fader_gain == 0.55
    assert updated.tracks[0].pan == 0.25

    source = tmp_path / "idea.flac"
    sf.write(source, np.zeros(2_400, dtype=np.float32), 48_000, format="FLAC")
    controller.import_media(source)
    _wait_until(
        qapp,
        lambda: len(controller.project_controller.snapshot.project.media) == 1,
    )
    assert (
        controller.project_controller.snapshot.project.media[0].original_basename
        == "idea.flac"
    )
    assert controller.save()
    assert controller.shutdown()


def test_bounce_command_runs_off_ui_thread_and_publishes_verified_mix(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Bounce.webjam", "Bounce")
    backing = tmp_path / "owned backing.wav"
    sf.write(
        backing,
        np.column_stack(
            (
                np.linspace(-0.25, 0.25, 4_800, dtype=np.float32),
                np.linspace(0.25, -0.25, 4_800, dtype=np.float32),
            )
        ),
        48_000,
        subtype="FLOAT",
    )
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._renderer is not None)
    notices: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: notices.append(message),
    )
    destination = tmp_path / "Bounce Mix.wav"

    controller._start_bounce(SongBounceRequest(destination=destination))

    _wait_until(qapp, lambda: controller._bounce_future is None)
    assert destination.is_file()
    assert destination.stat().st_size > 44
    assert controller.workspace.presentation.can_bounce
    assert "Published 1 verified bounce file." in controller._status
    assert destination.name in notices[0]
    assert "SHA-256:" in notices[0]
    assert str(tmp_path) not in notices[0]
    assert controller.shutdown()


def test_backing_tempo_analysis_requires_review_before_applying(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Tempo.webjam", "Tempo")
    samples = np.zeros(48_000 * 10, dtype=np.float32)
    for start in range(0, len(samples), 24_000):
        samples[start : start + 240] = np.linspace(
            1.0,
            0.0,
            min(240, len(samples) - start),
            dtype=np.float32,
        )
    backing = tmp_path / "private pulse.wav"
    sf.write(backing, samples, 48_000, subtype="FLOAT")
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._catalog is not None)

    class _ReviewedTempo:
        def __init__(self, **_kwargs) -> None:
            self.choice = ReferenceStudioTempoChoice(
                bpm=123.5,
                numerator=6,
                denominator=8,
            )

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        reference_app,
        "ReferenceStudioTempoReviewDialog",
        _ReviewedTempo,
    )

    controller._analyze_tempo()

    _wait_until(qapp, lambda: controller._tempo_future is None)
    project = controller.project_controller.snapshot.project
    assert project is not None
    assert project.tempo_bpm == 123.5, controller._status
    assert project.time_signature.numerator == 6
    assert project.time_signature.denominator == 8
    assert "project audio was unchanged" in controller._status
    assert controller.save()
    assert controller.shutdown()


def test_cut_region_clipboard_can_paste_after_source_is_tombstoned(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Clipboard.webjam", "Clipboard")
    backing = tmp_path / "source.wav"
    sf.write(backing, np.zeros(4_800, dtype=np.float32), 48_000, subtype="FLOAT")
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._renderer is not None)
    original = next(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    controller.workspace.arrange.set_selection(
        track_id=original.track_id,
        region_id=original.region_id,
    )
    controller._cut_selected()
    assert controller.studio_controller.document.region_for(original.region_id).deleted
    controller.workspace.arrange.set_playhead(1_000)

    controller._paste_region()

    active = tuple(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    assert len(active) == 1
    assert active[0].region_id != original.region_id
    assert active[0].timeline_start_frame == 1_000
    assert active[0].source_media_id == original.source_media_id
    assert controller.save()
    assert controller.shutdown()


def test_join_command_reverses_a_clean_adjacent_split_non_destructively(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Join.webjam", "Join")
    backing = tmp_path / "join source.wav"
    sf.write(backing, np.zeros(4_800, dtype=np.float32), 48_000, subtype="FLOAT")
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._renderer is not None)
    original = next(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    controller._split_region(original.region_id, 2_400)
    controller.workspace.arrange.set_selection(
        track_id=original.track_id,
        region_id=original.region_id,
    )

    controller._join_selected_regions()

    document = controller.studio_controller.document
    active = tuple(
        item for item in document.regions if item.enabled and not item.deleted
    )
    assert len(active) == 1
    assert active[0].region_id == original.region_id
    assert active[0].timeline_frame_count == original.timeline_frame_count
    assert active[0].source_frame_count == original.source_frame_count
    assert len(tuple(item for item in document.regions if item.deleted)) == 1
    assert controller.save()
    assert controller.shutdown()


def test_save_as_clones_complete_project_and_switches_to_new_identity(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    source_bundle = tmp_path / "Source.webjam"
    project = controller.create_project(source_bundle, "Source")
    backing = tmp_path / "save-as backing.wav"
    sf.write(backing, np.zeros(4_800, dtype=np.float32), 48_000, subtype="FLOAT")
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._renderer is not None)
    region = next(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    controller._move_region(region.region_id, 240)
    destination = tmp_path / "Destination.webjam"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), "WebJam Project"),
    )

    controller._save_as_dialog()

    _wait_until(qapp, lambda: controller._save_as_future is None)
    _wait_until(
        qapp,
        lambda: (
            controller.project_controller.snapshot.project is not None
            and controller.project_controller.snapshot.project.project_id
            != project.project_id
        ),
    )
    copied = controller.project_controller.snapshot
    assert copied.bundle_path == destination
    assert source_bundle.is_dir()
    assert destination.is_dir()
    assert copied.project is not None
    assert copied.project.name == project.name
    copied_region = next(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    assert copied_region.timeline_start_frame == 240
    assert controller.save()
    assert controller.shutdown()


def test_ruler_command_switches_between_elapsed_time_and_project_meter(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Ruler.webjam", "Ruler")
    _wait_until(qapp, lambda: controller._renderer is not None)
    controller._set_tempo(90.0)
    controller._set_time_signature(6, 8)

    controller._dispatch_command("toggle_ruler")

    arrange = controller.workspace.arrange
    assert arrange.ruler_mode == "bars"
    assert arrange.ruler_tempo_bpm == 90.0
    assert arrange.ruler_beats_per_bar == 6
    assert arrange.ruler_beat_denominator == 8
    assert "bars and beats" in controller._status

    controller._dispatch_command("toggle_ruler")

    assert arrange.ruler_mode == "time"
    assert "elapsed time" in controller._status
    assert controller.close_project(choice="discard")
    assert controller.shutdown()


def test_mixer_and_automation_commands_persist_complete_mix_state(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    bundle = tmp_path / "Persistent Mix.webjam"
    controller.create_project(bundle, "Persistent Mix")
    _wait_until(qapp, lambda: controller._catalog is not None)
    source_id = controller.studio_controller.document.tracks[0].track_id

    controller._dispatch_command("show_mixer")

    mixer = controller._mixer_dialog
    assert mixer is not None
    assert mixer.isVisible()
    assert not mixer.isModal()
    assert "not available" not in controller._status
    mixer.track_fader_changed.emit(source_id, 0.6)
    mixer.track_pan_changed.emit(source_id, -0.35)
    mixer.track_mute_changed.emit(source_id, True)
    mixer.track_solo_changed.emit(source_id, True)
    mixer.track_reverb_send_changed.emit(source_id, 0.4)
    for kind in (
        StudioEffectKind.HPF,
        StudioEffectKind.EQ,
        StudioEffectKind.COMPRESSOR,
        StudioEffectKind.GATE,
    ):
        mixer.track_effect_changed.emit(source_id, kind.value, True)
    mixer.master_changed.emit(0.8, False)

    mixed = controller.studio_controller.document
    source = mixed.state_for(source_id)
    assert source.fader_gain == pytest.approx(0.6)
    assert source.pan == pytest.approx(-0.35)
    assert source.muted
    assert source.solo
    assert {item.kind for item in source.effects} == {
        StudioEffectKind.HPF,
        StudioEffectKind.EQ,
        StudioEffectKind.COMPRESSOR,
        StudioEffectKind.GATE,
    }
    reverb_bus = next(item for item in mixed.tracks if item.kind is StudioTrackKind.BUS)
    assert reverb_bus.name == "Shared Reverb"
    assert reverb_bus.channel_count == 2
    reverb = next(
        item for item in reverb_bus.effects if item.kind is StudioEffectKind.REVERB
    )
    assert reverb.enabled
    assert reverb.reverb_mix == 1.0
    assert len(source.sends) == 1
    assert source.sends[0].target_bus_id == reverb_bus.track_id
    assert source.sends[0].gain == pytest.approx(0.4)
    assert mixed.master.gain == pytest.approx(0.8)
    assert not mixed.master.limiter_enabled

    controller.workspace.arrange.set_playhead(2_400)
    controller._dispatch_command("show_automation")

    automation = controller._automation_dialog
    assert automation is not None
    assert automation.isVisible()
    assert not automation.isModal()
    assert "not available" not in controller._status
    automation.point_requested.emit(
        source_id,
        StudioAutomationParameter.VOLUME.value,
        2_400,
        0.5,
    )
    automation.point_requested.emit(
        source_id,
        StudioAutomationParameter.VOLUME.value,
        2_400,
        0.75,
    )
    automation.point_requested.emit(
        source_id,
        StudioAutomationParameter.VOLUME.value,
        1_200,
        0.25,
    )
    automation.point_requested.emit(
        source_id,
        StudioAutomationParameter.MUTE.value,
        2_400,
        1.0,
    )
    automation.point_requested.emit(
        source_id,
        StudioAutomationParameter.PAN.value,
        2_400,
        0.2,
    )
    automation.clear_requested.emit(
        source_id,
        StudioAutomationParameter.PAN.value,
    )

    automated = controller.studio_controller.document.state_for(source_id)
    volume = next(
        item
        for item in automated.automation
        if item.parameter is StudioAutomationParameter.VOLUME
    )
    assert [item.frame for item in volume.points] == [1_200, 2_400]
    assert [item.value for item in volume.points] == [0.25, 0.75]
    assert volume.interpolation is StudioAutomationInterpolation.LINEAR
    mute = next(
        item
        for item in automated.automation
        if item.parameter is StudioAutomationParameter.MUTE
    )
    assert mute.interpolation is StudioAutomationInterpolation.HOLD
    assert all(
        item.parameter is not StudioAutomationParameter.PAN
        for item in automated.automation
    )
    assert "2 existing points" in automation.summary.text()

    assert controller.save()
    assert controller.close_project(choice="discard")
    assert controller._mixer_dialog is None
    assert controller._automation_dialog is None

    controller.open_project(bundle)
    _wait_until(qapp, lambda: controller._catalog is not None)
    reopened = controller.studio_controller.document
    persisted = reopened.state_for(source_id)
    persisted_bus = next(
        item for item in reopened.tracks if item.kind is StudioTrackKind.BUS
    )
    assert persisted.fader_gain == pytest.approx(0.6)
    assert persisted.pan == pytest.approx(-0.35)
    assert persisted.sends[0].target_bus_id == persisted_bus.track_id
    assert len(persisted.automation) == 2
    assert reopened.master.gain == pytest.approx(0.8)
    assert not reopened.master.limiter_enabled

    controller._set_track_reverb_send(source_id, 0.0)
    without_send = controller.studio_controller.document
    assert not without_send.state_for(source_id).sends
    assert any(
        item.track_id == persisted_bus.track_id and item.kind is StudioTrackKind.BUS
        for item in without_send.tracks
    )
    assert controller.save()
    assert controller.shutdown()


def test_multi_region_selection_batches_clipboard_and_undoes_atomically(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    controller.create_project(tmp_path / "Multi.webjam", "Multi")
    backing = tmp_path / "source.wav"
    sf.write(backing, np.zeros(9_600, dtype=np.float32), 48_000, subtype="FLOAT")
    controller.import_backing(backing)
    _wait_until(qapp, lambda: controller._renderer is not None)
    original = next(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    controller.workspace.arrange.set_selection(
        track_id=original.track_id,
        region_id=original.region_id,
    )
    controller.workspace.arrange.set_playhead(4_800)
    controller._split_selected()

    def active_regions():
        return sorted(
            (
                item
                for item in controller.studio_controller.document.regions
                if item.enabled and not item.deleted
            ),
            key=lambda item: (item.timeline_start_frame, item.region_id),
        )

    left, right = active_regions()

    controller.workspace.arrange.set_selection(
        track_id=left.track_id,
        region_id=left.region_id,
    )
    controller.workspace.arrange._user_select_region(right, extend=True)
    assert controller.workspace.arrange.selected_region_ids == (
        left.region_id,
        right.region_id,
    )

    controller._copy_selected()
    assert "Copied 2 regions" in controller._status
    controller.workspace.arrange.set_playhead(6_000)
    controller._paste_region()
    regions = active_regions()
    assert len(regions) == 4
    pasted = sorted(
        (
            item
            for item in regions
            if item.region_id not in {left.region_id, right.region_id}
        ),
        key=lambda item: item.timeline_start_frame,
    )
    # The earliest copy lands at the playhead; the phrase keeps its shape.
    assert pasted[0].timeline_start_frame == 6_000
    assert pasted[1].timeline_start_frame == 6_000 + (
        right.timeline_start_frame - left.timeline_start_frame
    )
    assert {item.source_media_id for item in pasted} == {
        left.source_media_id,
        right.source_media_id,
    }

    controller.workspace.arrange.set_selection(
        track_id=left.track_id,
        region_id=left.region_id,
    )
    controller.workspace.arrange._user_select_region(right, extend=True)
    controller._delete_selected()
    document = controller.studio_controller.document
    assert document.region_for(left.region_id).deleted
    assert document.region_for(right.region_id).deleted

    # One undo restores the whole batch: the multi-delete is a single edit.
    controller._undo()
    document = controller.studio_controller.document
    assert not document.region_for(left.region_id).deleted
    assert not document.region_for(right.region_id).deleted

    controller._select_all_regions()
    assert len(controller.workspace.arrange.selected_region_ids) == 4
    assert "Selected all 4 regions" in controller._status
    assert controller.save()
    assert controller.shutdown()

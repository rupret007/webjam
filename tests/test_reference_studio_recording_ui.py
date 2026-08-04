"""Controller-level acceptance tests for Reference Studio recording."""

from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
import pytest

import core.project_recording_commit as recording_commit
from core.project_recording_commit import (
    RECORDING_COMMIT_JOURNAL_FILENAME,
    RECORDING_EVIDENCE_FILENAME,
)
from core.song_project import InputMapping
from core.song_studio_reconcile import reconcile_song_studio_document
from core.studio_project import SnapMode, StudioCycleRange
from core.take_player import TakePlayer
from webjam_qt.controllers.reference_studio_application import (
    ReferenceStudioApplicationController,
)
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.widgets.reference_studio_shell import ReferenceStudioShell


class _TakeSink:
    def play(self, *_args, **_kwargs) -> None:
        return None

    def stop(self) -> None:
        return None


class _ProjectOutput:
    sample_rate = 48_000
    block_frames = 256

    def start(self, _callback) -> None:
        raise AssertionError("An empty first take must not start project playback.")

    def stop(self) -> None:
        return None

    def abort(self) -> None:
        return None


class _ProjectInput:
    sample_rate = 48_000
    block_frames = 512

    def __init__(self, *, input_channels: int, device) -> None:
        self.input_channels = input_channels
        self.device = device
        self.callback = None
        self.started = 0
        self.stopped = 0
        self.aborted = 0

    def start(self, callback) -> None:
        self.callback = callback
        self.started += 1

    def stop(self) -> None:
        self.callback = None
        self.stopped += 1

    def abort(self) -> None:
        self.callback = None
        self.aborted += 1

    def pump(self, blocks: int = 4) -> None:
        callback = self.callback
        assert callback is not None
        phase = np.arange(self.block_frames, dtype=np.float32)
        for index in range(blocks):
            block = np.empty(
                (self.block_frames, self.input_channels),
                dtype=np.float32,
            )
            for channel in range(self.input_channels):
                block[:, channel] = (phase + index + channel) / 4_096.0
            callback(block)


class _InputFactory:
    def __init__(self) -> None:
        self.instances: list[_ProjectInput] = []

    def __call__(self, *, input_channels: int, device) -> _ProjectInput:
        backend = _ProjectInput(
            input_channels=input_channels,
            device=device,
        )
        self.instances.append(backend)
        return backend


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _shell() -> ReferenceStudioShell:
    return ReferenceStudioShell(
        RecordingStudio(
            player=TakePlayer(samplerate=48_000, sink=_TakeSink())
        )
    )


def _wait_until(qapp: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Reference Studio recording did not finish in time")


def _recording_controller(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ReferenceStudioApplicationController, _InputFactory, Path]:
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    factory = _InputFactory()
    controller = ReferenceStudioApplicationController(
        _shell(),
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
        input_backend_factory=factory,
    )
    bundle = tmp_path / "First Take.webjam"
    project = controller.create_project(bundle, "First Take")
    _wait_until(qapp, lambda: controller._renderer is not None)
    track = project.tracks[0]
    controller.project_controller.set_track_input_mapping(
        track.track_id,
        InputMapping("system-default-input", (1,)),
    )
    snapshot = controller.project_controller.set_track_armed(track.track_id, True)
    assert snapshot.project is not None
    assert controller._apply_studio_edit(
        "Prepared recording track",
        lambda document: reconcile_song_studio_document(
            snapshot.project,
            document,
        ),
        rebuild=False,
    )
    monkeypatch.setattr(
        QInputDialog,
        "getInt",
        lambda *_args, **_kwargs: (1, True),
    )
    return controller, factory, bundle


def test_empty_project_records_first_take_and_commits_verified_media(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, factory, bundle = _recording_controller(
        tmp_path,
        qapp,
        monkeypatch,
    )
    assert controller.workspace.presentation.can_record

    controller._start_recording()

    assert controller._is_recording, controller._status
    assert controller._prepare_future is None
    assert len(factory.instances) == 1
    assert factory.instances[0].device is None
    assert "first take" in controller._status
    assert "begins immediately" in controller._status
    factory.instances[0].pump()
    controller._stop_recording_async()
    _wait_until(
        qapp,
        lambda: controller._recording_commit_future is None,
    )

    project = controller.project_controller.snapshot.project
    assert project is not None
    assert len(project.media) == 1, controller._status
    assert project.media[0].path.startswith("Media/")
    assert (bundle / project.media[0].path).is_file()
    assert (bundle / RECORDING_EVIDENCE_FILENAME).is_file()
    active_regions = tuple(
        item
        for item in controller.studio_controller.document.regions
        if item.enabled and not item.deleted
    )
    assert len(active_regions) == 1
    assert active_regions[0].timeline_start_frame == 0
    assert factory.instances[0].stopped == 1
    assert controller.shutdown()


def test_recording_blocks_transport_edits_save_and_close_until_commit(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, factory, _bundle = _recording_controller(
        tmp_path,
        qapp,
        monkeypatch,
    )
    controller._start_recording()
    original_project = controller.project_controller.snapshot.project
    original_document = controller.studio_controller.document
    assert original_project is not None

    controller._seek(1_000)
    controller._set_tempo(144.0)
    changed = controller._apply_studio_edit(
        "Changed snap",
        lambda document: document.set_snap_mode(SnapMode.OFF),
    )

    assert controller.workspace.arrange.playhead_frame == 0
    assert controller.project_controller.snapshot.project == original_project
    assert controller.studio_controller.document == original_document
    assert changed is False
    assert controller.save() is False
    assert controller.close_project(choice="discard") is False
    assert "finish the protected Studio recording" in controller._status

    factory.instances[0].pump()
    controller._stop_recording_async()
    _wait_until(
        qapp,
        lambda: (
            controller._recording_commit_future is None
            and not controller._recording_busy
        ),
    )
    assert controller.shutdown()


def test_commit_recovery_is_resumed_before_project_unlocks(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, factory, bundle = _recording_controller(
        tmp_path,
        qapp,
        monkeypatch,
    )
    controller._start_recording()
    factory.instances[0].pump()
    capture = controller._recording_temp
    assert capture is not None

    real_save = recording_commit.save_song_studio_document
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("/private/recording-test failure")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(
        recording_commit,
        "save_song_studio_document",
        fail_once,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    controller._stop_recording_async()
    _wait_until(
        qapp,
        lambda: (
            controller._recording_commit_future is None
            and not controller._recording_busy
            and not controller._recording_recovery_pending
            and not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
            and controller.project_controller.snapshot.project is not None
            and bool(controller.project_controller.snapshot.project.media)
        ),
    )

    assert calls >= 2
    assert not capture.exists()
    assert (bundle / RECORDING_EVIDENCE_FILENAME).is_file()
    assert controller.shutdown()


def test_deferred_recovery_blocks_edits_but_allows_safe_close_and_reopen(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, factory, bundle = _recording_controller(
        tmp_path,
        qapp,
        monkeypatch,
    )
    controller._start_recording()
    factory.instances[0].pump()
    capture = controller._recording_temp
    assert capture is not None

    real_save = recording_commit.save_song_studio_document
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("/private/recording-test failure")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(
        recording_commit,
        "save_song_studio_document",
        fail_once,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    controller._stop_recording_async()
    _wait_until(
        qapp,
        lambda: (
            controller._recording_commit_future is None
            and controller._recording_recovery_pending
        ),
    )
    assert (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).is_file()
    assert not capture.exists()
    tempo = controller.project_controller.snapshot.project.tempo_bpm
    controller._set_tempo(150.0)
    assert controller.project_controller.snapshot.project.tempo_bpm == tempo
    assert controller.save() is False

    assert controller.close_project(choice="discard")
    reopened = controller.open_project(
        bundle,
        recording_recovery="recover",
    )
    assert reopened.media
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
    assert controller.shutdown()


def test_input_mapping_uses_selected_device_and_rejects_oversized_maps(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ReferenceStudioApplicationController(
        _shell(),
        config_file=tmp_path / "settings.json",
        output_backend=_ProjectOutput(),
    )
    project = controller.create_project(tmp_path / "Inputs.webjam", "Inputs")
    _wait_until(qapp, lambda: controller._renderer is not None)
    choices = (
        ("System default input", "system-default-input", 2),
        ("Demo Interface (2 input channels)", "sounddevice-index:7", 2),
    )
    monkeypatch.setattr(controller, "_available_input_devices", lambda: choices)
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: (choices[1][0], True),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("1,2", True),
    )

    controller._map_track_input_dialog()

    mapped = controller.project_controller.snapshot.project
    assert mapped is not None
    assert mapped.tracks[0].track_id == project.tracks[0].track_id
    assert mapped.tracks[0].input_mapping == InputMapping(
        "sounddevice-index:7",
        (1, 2),
    )

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("1,2,3", True),
    )
    controller._map_track_input_dialog()
    unchanged = controller.project_controller.snapshot.project
    assert unchanged is not None
    assert unchanged.tracks[0].input_mapping == mapped.tracks[0].input_mapping
    assert "one mono channel or two stereo channels" in controller._status
    assert controller.close_project(choice="discard")
    assert controller.shutdown()


class _PlayingProjectOutput:
    """A project output that permits playback (overdub monitors the take)."""

    sample_rate = 48_000
    block_frames = 256

    def __init__(self) -> None:
        self.callback = None

    def start(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.callback = None

    def abort(self) -> None:
        self.callback = None


def test_overdub_cycle_records_dialog_free_and_stacks_take_lanes(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Overdub monitors the existing take while it records, so this controller
    # uses an output that allows playback rather than the empty-first-take
    # guard used elsewhere in this module.
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    factory = _InputFactory()
    controller = ReferenceStudioApplicationController(
        _shell(),
        config_file=tmp_path / "settings.json",
        output_backend=_PlayingProjectOutput(),
        input_backend_factory=factory,
    )
    project = controller.create_project(tmp_path / "Overdub.webjam", "Overdub")
    _wait_until(qapp, lambda: controller._renderer is not None)
    track = project.tracks[0]
    controller.project_controller.set_track_input_mapping(
        track.track_id,
        InputMapping("system-default-input", (1,)),
    )
    snapshot = controller.project_controller.set_track_armed(track.track_id, True)
    assert snapshot.project is not None
    assert controller._apply_studio_edit(
        "Prepared recording track",
        lambda document: reconcile_song_studio_document(snapshot.project, document),
        rebuild=False,
    )
    monkeypatch.setattr(
        QInputDialog,
        "getInt",
        lambda *_args, **_kwargs: (1, True),
    )
    controller._start_recording()
    assert controller._is_recording, controller._status
    factory.instances[0].pump(blocks=8)
    controller._stop_recording_async()
    _wait_until(qapp, lambda: controller._recording_commit_future is None)
    # The recorded media re-enters through the catalog asynchronously; the
    # project is no longer empty once its renderer spans the committed take.
    _wait_until(
        qapp,
        lambda: (
            controller._renderer is not None
            and controller._renderer.timeline_end_frame > 0
        ),
    )

    # Loop a short cycle over the committed first take and turn Overdub on.
    assert controller._apply_studio_edit(
        "Set overdub loop",
        lambda document: document.set_cycle_range(
            StudioCycleRange(start_frame=0, end_frame=512)
        ),
        rebuild=False,
    )
    controller._count_in = False
    controller._dispatch_command("toggle_overdub")
    assert controller.workspace.overdub_box.isChecked()
    assert controller.workspace.actions["toggle_overdub"].isChecked()

    def _no_dialog(*_args, **_kwargs):
        raise AssertionError("Overdub recording must not open a pass-count dialog")

    monkeypatch.setattr(QInputDialog, "getInt", _no_dialog)
    controller._start_recording()
    assert controller._is_recording, controller._status
    assert len(factory.instances) == 2
    factory.instances[1].pump(blocks=4)
    controller._stop_recording_async()
    _wait_until(qapp, lambda: controller._recording_commit_future is None)

    document = controller.studio_controller.document
    lanes = tuple(item for item in document.take_lanes if not item.deleted)
    assert lanes, controller._status
    assert "take lane" in controller._status
    assert "Overdub passes are stacked" in controller._status
    assert controller.save()
    assert controller.shutdown()

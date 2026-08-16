"""End-to-end proof for the bounded v0.26 Podcast & Voice journey."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
import pytest
import soundfile as sf

from core.project_recording_commit import load_recording_evidence
from core.song_bounce import SongBounceRequest
from core.song_project import InputMapping
from core.song_studio_reconcile import reconcile_song_studio_document
from core.studio_project import MarkerKind, StudioCycleRange
from core.take_player import TakePlayer
import webjam_qt.controllers.reference_studio_application as reference_studio_app
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

    def __init__(self) -> None:
        self.callback = None

    def start(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.callback = None

    def abort(self) -> None:
        self.callback = None


class _ProjectInput:
    sample_rate = 48_000
    block_frames = 512

    def __init__(self, *, input_channels: int, device) -> None:
        self.input_channels = input_channels
        self.device = device
        self.callback = None
        self.stopped = 0

    def start(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.callback = None
        self.stopped += 1

    def abort(self) -> None:
        self.callback = None

    def pump(self, blocks: int = 4) -> None:
        callback = self.callback
        assert callback is not None
        phase = np.arange(self.block_frames, dtype=np.float32)
        for block_index in range(blocks):
            block = np.empty(
                (self.block_frames, self.input_channels),
                dtype=np.float32,
            )
            for channel in range(self.input_channels):
                block[:, channel] = (
                    phase + block_index * self.block_frames + channel * 0.25
                ) / 16_384.0
            callback(block)


class _InputFactory:
    def __init__(self) -> None:
        self.instances: list[_ProjectInput] = []

    def __call__(self, *, input_channels: int, device) -> _ProjectInput:
        backend = _ProjectInput(input_channels=input_channels, device=device)
        self.instances.append(backend)
        return backend


class _ObservedOwnedExecutor:
    def __init__(self) -> None:
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, *_args, **_kwargs):
        raise AssertionError("This lifecycle test did not schedule background work")

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _shell() -> ReferenceStudioShell:
    return ReferenceStudioShell(
        RecordingStudio(player=TakePlayer(samplerate=48_000, sink=_TakeSink()))
    )


def _shutdown_controller(
    controller: ReferenceStudioApplicationController,
    shell: ReferenceStudioShell,
    qapp: QApplication,
) -> None:
    assert controller.shutdown()
    assert shell.take_review.shutdown()
    # The shell is test-owned. Destroy its QObject graph while the module's
    # QApplication is still alive, before pytest's final cyclic-GC sweep.
    shell.close()
    shell.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _wait_until(qapp: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Podcast & Voice journey work did not finish in time")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_podcast_voice_record_overdub_chapter_bounce_and_reopen(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: notices.append(message),
    )
    monkeypatch.setattr(QInputDialog, "getInt", lambda *_args, **_kwargs: (1, True))

    factory = _InputFactory()
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "settings.json",
        creator_profile_key="podcast_voice",
        output_backend=_ProjectOutput(),
        input_backend_factory=factory,
    )
    bundle = tmp_path / "Host and Guest.webjam"
    try:
        project = controller.create_project(bundle, "Host and Guest")
        _wait_until(qapp, lambda: controller._renderer is not None)

        assert project.creator_profile_key == "podcast_voice"
        assert [track.name for track in project.tracks] == ["Host Mic", "Guest Mic"]
        assert controller.workspace.arrange.ruler_mode == "time"
        assert controller.workspace.bounce_button.text() == "Bounce Episode…"

        host, guest = project.tracks
        controller.project_controller.set_track_input_mapping(
            host.track_id,
            InputMapping("system-default-input", (1,)),
        )
        controller.project_controller.set_track_armed(host.track_id, True)
        controller.project_controller.set_track_input_mapping(
            guest.track_id,
            InputMapping("system-default-input", (2, 3)),
        )
        snapshot = controller.project_controller.set_track_armed(
            guest.track_id,
            True,
        )
        assert snapshot.project is not None
        assert controller._apply_studio_edit(
            "Prepared Host and Guest inputs",
            lambda document: reconcile_song_studio_document(
                snapshot.project,
                document,
            ),
            rebuild=False,
        )
        assert [
            controller.studio_controller.document.state_for(
                track.track_id
            ).channel_count
            for track in snapshot.project.tracks
        ] == [1, 2]

        controller._start_recording()
        assert controller._is_recording, controller._status
        assert factory.instances[0].input_channels == 3
        factory.instances[0].pump()
        controller._stop_recording_async()
        _wait_until(qapp, lambda: controller._recording_commit_future is None)
        _wait_until(
            qapp,
            lambda: (
                controller._renderer is not None
                and controller._renderer.timeline_end_frame >= 512
            ),
        )

        controller.workspace.arrange.set_playhead(256)
        monkeypatch.setattr(
            QInputDialog,
            "getText",
            lambda *_args, **_kwargs: ("Opening", True),
        )
        controller._add_marker(MarkerKind.SECTION)
        chapter = controller.studio_controller.document.markers[0]
        assert chapter.kind is MarkerKind.SECTION
        assert chapter.label == "Opening"
        assert chapter.start_frame == 256

        assert controller._apply_studio_edit(
            "Set voice overdub loop",
            lambda document: document.set_cycle_range(
                StudioCycleRange(start_frame=0, end_frame=512)
            ),
            rebuild=False,
        )
        controller._overdub = True
        controller._count_in = False
        controller._start_recording()
        assert controller._is_recording, controller._status
        assert factory.instances[1].input_channels == 3
        factory.instances[1].pump()
        controller._stop_recording_async()
        _wait_until(qapp, lambda: controller._recording_commit_future is None)

        recorded = controller.project_controller.snapshot.project
        assert recorded is not None
        ledger = load_recording_evidence(bundle, recorded)
        assert len(ledger.commits) == 2
        expected_maps = {
            host.track_id: (0,),
            guest.track_id: (1, 2),
        }
        for commit in ledger.commits:
            assert {
                evidence.track_id: evidence.input_channels for evidence in commit.tracks
            } == expected_maps
            for evidence in commit.tracks:
                media = recorded.media_by_id(evidence.media_id)
                expected_channels = len(expected_maps[evidence.track_id])
                assert media.channels == expected_channels
                source = bundle / media.path
                info = sf.info(source)
                assert info.channels == expected_channels
                assert info.samplerate == 48_000
                assert media.sha256 == _sha256(source)
        assert [media.channels for media in recorded.media] == [1, 2, 1, 2]

        active_lanes = tuple(
            lane
            for lane in controller.studio_controller.document.take_lanes
            if lane.enabled and not lane.deleted
        )
        assert {lane.track_id for lane in active_lanes} == {
            host.track_id,
            guest.track_id,
        }
        assert controller.save()

        destination = tmp_path / "Host and Guest Episode.wav"
        controller._start_bounce(SongBounceRequest(destination=destination))
        _wait_until(qapp, lambda: controller._bounce_future is None)

        assert not warnings, warnings
        assert destination.is_file()
        assert stat.S_ISREG(destination.lstat().st_mode)
        bounced = sf.info(destination)
        assert bounced.samplerate == 48_000
        assert bounced.channels == 2
        assert bounced.subtype == "PCM_24"
        assert bounced.frames > 0
        assert len(_sha256(destination)) == 64
        assert not tuple(tmp_path.glob(".webjam-bounce-*"))
        assert notices and "Published 1 verified bounce file." in controller._status
        assert "SHA-256:" in notices[-1]
        assert str(bundle) not in notices[-1]

        assert controller.close_project(choice="discard")
        reopened = controller.open_project(bundle)
        _wait_until(qapp, lambda: controller._catalog is not None)
        assert reopened.creator_profile_key == "podcast_voice"
        assert controller.workspace.arrange.ruler_mode == "time"
        assert controller.studio_controller.document.markers == (chapter,)
        assert [
            controller.studio_controller.document.state_for(
                track.track_id
            ).channel_count
            for track in reopened.tracks
        ] == [1, 2]
    finally:
        _shutdown_controller(controller, shell, qapp)


def test_review_preview_lower_level_edit_and_export_entry_points_fail_closed(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "review-settings.json",
        creator_profile_key="review_rehearsal",
        output_backend=_ProjectOutput(),
    )
    touched: list[str] = []

    def forbidden(*_args, **_kwargs):
        touched.append("called")
        raise AssertionError("Review Preview reached a local edit/export primitive")

    monkeypatch.setattr(controller.studio_controller, "perform", forbidden)
    monkeypatch.setattr(controller.studio_controller, "undo", forbidden)
    monkeypatch.setattr(controller.studio_controller, "redo", forbidden)
    monkeypatch.setattr(controller, "save", forbidden)
    destination = tmp_path / "must-not-exist.wav"
    try:
        assert controller._apply_studio_edit("Forbidden edit", forbidden) is False
        controller._undo()
        controller._redo()
        controller._show_mixer()
        controller._show_automation()
        controller._start_bounce(SongBounceRequest(destination=destination))
        controller._dispatch_command("bounce")

        assert not touched
        assert not destination.exists()
        assert controller._bounce_future is None
        assert controller._mixer_dialog is None
        assert controller._automation_dialog is None
        assert "playback and source inspection only" in controller._status
        assert not controller.workspace.actions["bounce"].isEnabled()
    finally:
        _shutdown_controller(controller, shell, qapp)


def test_shutdown_joins_its_owned_media_executor_before_qt_teardown(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _ObservedOwnedExecutor()
    monkeypatch.setattr(
        reference_studio_app,
        "ThreadPoolExecutor",
        lambda **_kwargs: executor,
    )
    shell = _shell()
    controller = ReferenceStudioApplicationController(
        shell,
        config_file=tmp_path / "shutdown-settings.json",
        creator_profile_key="podcast_voice",
        output_backend=_ProjectOutput(),
    )
    try:
        assert controller.shutdown()
        assert executor.shutdown_calls == [(True, True)]
    finally:
        _shutdown_controller(controller, shell, qapp)

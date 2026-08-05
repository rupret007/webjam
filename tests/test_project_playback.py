"""Deterministic delivery and callback-safety tests for project playback."""

from __future__ import annotations

import builtins
import logging
import os
from pathlib import Path
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from core.project_playback import (
    ProjectPlaybackEngine,
    ProjectPlaybackError,
    ProjectPlaybackState,
)
from core.song_media_catalog import SongMediaCatalog
from core.song_project import MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.studio_project import default_song_studio_document
from core.studio_renderer import StudioRenderer


class _Backend:
    sample_rate = 48_000

    def __init__(self, *, block_frames: int = 128, fail_start: bool = False) -> None:
        self.block_frames = block_frames
        self.fail_start = fail_start
        self.callback = None
        self.started = 0
        self.stopped = 0
        self.aborted = 0

    def start(self, callback) -> None:
        if self.fail_start:
            raise OSError("/private/device/name")
        self.callback = callback
        self.started += 1

    def stop(self) -> None:
        self.callback = None
        self.stopped += 1

    def abort(self) -> None:
        self.callback = None
        self.aborted += 1

    def pump(self, frames: int | None = None) -> np.ndarray:
        if self.callback is None:
            raise AssertionError("backend is not running")
        output = np.full(
            (frames or self.block_frames, 2),
            np.float32(99.0),
            dtype=np.float32,
        )
        self.callback(output)
        return output


def _fixture(
    tmp_path: Path,
    *,
    frames: int = 1_024,
    silence: bool = False,
):
    source = tmp_path / "private backing.wav"
    if silence:
        audio = np.zeros((frames, 2), dtype=np.float32)
    else:
        phase = np.arange(frames, dtype=np.float32)
        audio = np.column_stack(
            (
                np.sin(phase * np.float32(0.031)) * np.float32(0.4),
                np.cos(phase * np.float32(0.017)) * np.float32(0.25),
            )
        ).astype(np.float32)
    sf.write(source, audio, 48_000, subtype="FLOAT")
    bundle = tmp_path / "Playback Song.webjam"
    created = create_project_bundle(bundle, name="Playback Song")
    imported = import_project_media(
        bundle,
        created.project,
        source,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    project = imported.project.designate_backing_media(imported.media.media_id)
    project = save_project_bundle(
        bundle,
        project,
        expected_token=created.token,
    ).project
    document = default_song_studio_document(project)
    catalog = SongMediaCatalog.load(project, bundle)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=128,
    )
    return bundle, project, renderer


def _wait_for(
    predicate,
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("condition did not become true")


def _pump_to_finish(
    engine: ProjectPlaybackEngine,
    backend: _Backend,
    *,
    limit: int = 100,
) -> np.ndarray:
    blocks: list[np.ndarray] = []
    for _ in range(limit):
        blocks.append(backend.pump())
        engine.poll()
        if engine.state is ProjectPlaybackState.FINISHED:
            return np.concatenate(blocks)
        time.sleep(0.002)
    raise AssertionError("playback did not finish")


def test_playback_delivers_the_authoritative_renderer_and_finishes(
    tmp_path: Path,
) -> None:
    _bundle, _project, renderer = _fixture(tmp_path)
    expected = renderer.render_block(0, renderer.timeline_end_frame)
    backend = _Backend()
    engine = ProjectPlaybackEngine(backend, ring_capacity=4)
    engine.set_renderer(renderer)
    engine.play()
    delivered = _pump_to_finish(engine, backend)

    np.testing.assert_array_equal(delivered[: len(expected)], expected)
    assert engine.state is ProjectPlaybackState.FINISHED
    snapshot = engine.snapshot()
    assert snapshot.position_frame == renderer.timeline_end_frame
    assert snapshot.delivered_frames == len(expected)
    assert backend.started == 1
    assert backend.stopped == 1
    engine.close()


def test_pause_outputs_silence_without_consuming_then_resumes(tmp_path: Path) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=2_048)
    backend = _Backend()
    engine = ProjectPlaybackEngine(backend, ring_capacity=4)
    engine.set_renderer(renderer)
    engine.play()
    first = backend.pump()
    before = engine.snapshot()
    engine.pause()
    paused = backend.pump()
    after = engine.snapshot()
    np.testing.assert_array_equal(paused, np.zeros_like(paused))
    assert after.position_frame == before.position_frame
    assert after.delivered_frames == before.delivered_frames
    engine.play()
    resumed = backend.pump()
    assert float(np.max(np.abs(first))) > 0.0
    assert float(np.max(np.abs(resumed))) > 0.0
    engine.stop()
    assert engine.state is ProjectPlaybackState.READY


def test_seek_restarts_at_exact_project_frame_and_preserves_pause(
    tmp_path: Path,
) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=2_048)
    expected = renderer.render_block(700, 128)
    backend = _Backend()
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    engine.play()
    engine.pause()
    assert engine.seek(700) == 700
    assert engine.state is ProjectPlaybackState.PAUSED
    np.testing.assert_array_equal(backend.pump(), np.zeros((128, 2), np.float32))
    engine.play()
    np.testing.assert_array_equal(backend.pump(), expected)
    engine.stop()


def test_loop_repeats_exact_range_without_marking_finished(tmp_path: Path) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=1_024)
    backend = _Backend(block_frames=64)
    engine = ProjectPlaybackEngine(backend, ring_capacity=3)
    engine.set_renderer(renderer)
    engine.set_loop(128, 256)
    engine.play()
    blocks = []
    for _ in range(8):
        blocks.append(backend.pump())
        time.sleep(0.004)
    expected = renderer.render_block(128, 128)
    actual = np.concatenate(blocks)
    for start in range(0, len(actual), 128):
        np.testing.assert_array_equal(actual[start : start + 128], expected)
    assert engine.state is ProjectPlaybackState.PLAYING
    assert engine.snapshot().loop_start_frame == 128
    engine.stop()


def test_recording_loop_can_play_one_lead_in_before_repeating(
    tmp_path: Path,
) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=1_024)
    backend = _Backend(block_frames=64)
    engine = ProjectPlaybackEngine(backend, ring_capacity=3)
    engine.set_renderer(renderer)
    engine.set_loop(256, 512)

    engine.play(start_frame=128, allow_loop_lead_in=True)

    blocks = []
    for _ in range(8):
        blocks.append(backend.pump())
        time.sleep(0.004)
    expected = np.concatenate(
        (
            renderer.render_block(128, 384),
            renderer.render_block(256, 128),
        )
    )
    np.testing.assert_array_equal(np.concatenate(blocks), expected)
    assert engine.state is ProjectPlaybackState.PLAYING
    engine.stop()


def test_metronome_is_generated_on_producer_not_callback(tmp_path: Path) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=25_000, silence=True)
    backend = _Backend(block_frames=512)
    engine = ProjectPlaybackEngine(backend, ring_capacity=4)
    engine.set_renderer(renderer)
    engine.set_metronome(True)
    engine.play()
    first = backend.pump()
    assert float(np.max(np.abs(first))) > 0.05
    assert engine.snapshot().metronome_enabled
    engine.stop()


def test_output_callback_has_no_allocation_io_logging_or_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, _project, renderer = _fixture(tmp_path, frames=2_048)
    backend = _Backend()
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    engine.play()
    output = np.empty((128, 2), dtype=np.float32)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden callback operation")

    monkeypatch.setattr(np, "empty", forbidden)
    monkeypatch.setattr(np, "zeros", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(logging.Logger, "log", forbidden)
    monkeypatch.setattr(threading.Event, "wait", forbidden)
    monkeypatch.setattr(threading.Event, "set", forbidden)
    engine.process_output(output)
    assert np.all(np.isfinite(output))
    # Restore patches before control-plane stop joins the producer.
    monkeypatch.undo()
    engine.stop()


def test_start_failure_is_path_redacted_and_fail_closed(tmp_path: Path) -> None:
    _bundle, _project, renderer = _fixture(tmp_path)
    backend = _Backend(fail_start=True)
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    with pytest.raises(ProjectPlaybackError) as caught:
        engine.play()
    assert "/private/device/name" not in str(caught.value)
    assert engine.state is ProjectPlaybackState.FAILED
    assert backend.aborted == 1
    # A later clean stop must retire the failed run so its stale error cannot
    # overwrite a subsequent successful Studio/recording status update.
    backend.fail_start = False
    engine.stop()
    assert engine.state is ProjectPlaybackState.READY
    assert engine.snapshot().error == ""


def test_replaced_media_never_reaches_output(tmp_path: Path) -> None:
    bundle, project, renderer = _fixture(tmp_path)
    member = bundle / project.media[0].path
    replacement = tmp_path / "replacement.wav"
    sf.write(
        replacement,
        np.zeros((1_024, 2), dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )
    replacement.replace(member)
    backend = _Backend()
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    with pytest.raises(ProjectPlaybackError):
        engine.play()
    assert backend.callback is None


def test_invalid_backend_and_closed_lifecycle_fail_cleanly(tmp_path: Path) -> None:
    class Bad:
        sample_rate = 44_100
        block_frames = 128

    with pytest.raises(ValueError):
        ProjectPlaybackEngine(Bad())
    _bundle, _project, renderer = _fixture(tmp_path)
    engine = ProjectPlaybackEngine(_Backend())
    with pytest.raises(ProjectPlaybackError, match="Open"):
        engine.play()
    engine.set_renderer(renderer)
    engine.close()
    assert engine.state is ProjectPlaybackState.CLOSED
    with pytest.raises(ProjectPlaybackError, match="closed"):
        engine.play()

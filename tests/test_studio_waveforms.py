"""Bounded async coordination coverage for Studio Arrange waveforms."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import deque
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from core.studio_project import StudioRegion, default_studio_document
from core.studio_source_catalog import StudioSourceCatalog
from core.studio_waveform import WaveformTileCache
from core.take_project import (
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
)
from webjam_qt.widgets.studio_waveforms import (
    StudioWaveformCoordinator,
    StudioWaveformCoordinatorError,
    StudioWaveformRegionError,
    StudioWaveformRegionTile,
    quantized_frames_per_peak,
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


@dataclass
class _ManualTask:
    future: Future
    function: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class _ManualExecutor(Executor):
    """Deterministic executor: submission never runs the callable inline."""

    def __init__(self) -> None:
        self.tasks: deque[_ManualTask] = deque()
        self.closed = False

    def submit(self, fn, /, *args, **kwargs):
        if self.closed:
            raise RuntimeError("executor is shut down")
        future = Future()
        self.tasks.append(_ManualTask(future, fn, args, kwargs))
        return future

    def run_next(self) -> bool:
        if not self.tasks:
            return False
        task = self.tasks.popleft()
        if not task.future.set_running_or_notify_cancel():
            return True
        try:
            result = task.function(*task.args, **task.kwargs)
        except BaseException as exc:  # Future faithfully retains worker failures.
            task.future.set_exception(exc)
        else:
            task.future.set_result(result)
        return True

    def run_all(self) -> None:
        iterations = 0
        while self.run_next():
            iterations += 1
            if iterations > 10_000:
                raise AssertionError("manual executor did not quiesce")

    def shutdown(self, wait=True, *, cancel_futures=False) -> None:
        _ = wait
        self.closed = True
        if cancel_futures:
            for task in self.tasks:
                task.future.cancel()


class _ImmediateExecutor(Executor):
    """Deterministic executor whose Future is complete before submit returns."""

    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        self.submissions += 1
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # Future retains worker failures.
                future.set_exception(exc)
        return future


def _fixture(
    tmp_path: Path,
    *,
    frame_count: int = 512,
) -> tuple[TakeProject, StudioSourceCatalog, Path]:
    root = tmp_path / "take"
    root.mkdir()
    path = root / "source.wav"
    sf.write(
        path,
        np.linspace(-0.8, 0.8, frame_count, dtype=np.float32),
        8_000,
        subtype="FLOAT",
    )
    segment = MediaSegment(
        segment_id=_id(20),
        path=path.name,
        project_start_frame=0,
        frame_count=frame_count,
        sample_rate=8_000,
        channels=1,
        sample_format="FLOAT",
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
    )
    track = ProjectTrack(
        track_id=_id(10),
        source_id=_id(11),
        participant_id=None,
        name="Guitar",
        instrument="Guitar",
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.VERIFIED_ISOLATED,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Coordinator fixture",
        take_name="Take 1",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=8_000,
        participants=(),
        tracks=(track,),
    )
    (root / "webjam-take.json").write_text(
        json.dumps(project.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return project, StudioSourceCatalog.load(project, root), root


def _stretched_regions(project: TakeProject) -> tuple[StudioRegion, StudioRegion]:
    original = default_studio_document(project).regions[0]
    first = replace(
        original,
        region_id=_id(30),
        source_start_frame=100,
        source_frame_count=200,
        timeline_start_frame=1_000,
        timeline_frame_count=400,
        mapping_source_start_frame=100,
        mapping_source_frame_count=200,
        mapping_timeline_start_frame=1_000,
        mapping_timeline_frame_count=400,
    )
    return first, replace(first, region_id=_id(31))


def test_schedule_is_io_free_maps_affine_ranges_and_coalesces_region_tiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, catalog, _root = _fixture(tmp_path)
    first, second = _stretched_regions(project)
    document = replace(default_studio_document(project), regions=(first, second))
    executor = _ManualExecutor()
    tiles: list[StudioWaveformRegionTile] = []
    errors: list[StudioWaveformRegionError] = []
    cache = WaveformTileCache(tile_peaks=4)
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=tiles.append,
        publish_error=errors.append,
        cache=cache,
    )
    assert coordinator.activate(document, catalog) == 2
    assert (
        quantized_frames_per_peak(
            first,
            0.25,
            source_frame_count=512,
        )
        == 2
    )

    request_calls = 0
    original_request = cache.request_tile

    def counted_request(*args, **kwargs):
        nonlocal request_calls
        request_calls += 1
        return original_request(*args, **kwargs)

    monkeypatch.setattr(cache, "request_tile", counted_request)
    generation = coordinator.schedule(
        1_080,
        1_120,
        0.25,
        (first.region_id, second.region_id, first.region_id, _id(999)),
    )

    # Planning/submission performed no media I/O and never exceeded two slots.
    assert request_calls == 0
    assert cache.stats.source_opens == 0
    assert len(executor.tasks) == 2
    assert coordinator.stats.in_flight == 2
    assert coordinator.stats.pending == 1
    assert coordinator.stats.coalesced_region_tiles == 3
    assert tiles == []
    assert errors == []

    executor.run_all()
    assert request_calls == 3
    assert tiles == []  # Worker completion only enqueues; drain owns callbacks.
    assert coordinator.stats.queued_results == 3
    assert coordinator.drain() == 6
    assert errors == []
    assert len(tiles) == 6
    assert {item.generation for item in tiles} == {generation}
    assert {item.region_id for item in tiles} == {first.region_id, second.region_id}
    assert sorted(item.tile.key.start_frame for item in tiles) == [
        136,
        136,
        144,
        144,
        152,
        152,
    ]
    assert all(item.tile.key.frames_per_peak == 2 for item in tiles)
    assert coordinator.stats.published_region_tiles == 6
    assert coordinator.stats.in_flight == 0
    assert coordinator.stats.pending == 0


def test_schedule_filters_inactive_outside_and_nonvisible_regions(
    tmp_path: Path,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=64)
    original = default_studio_document(project).regions[0]
    visible = replace(original, region_id=_id(40))
    disabled = replace(original, region_id=_id(41), enabled=False)
    outside = replace(
        original,
        region_id=_id(42),
        timeline_start_frame=1_000,
        mapping_timeline_start_frame=1_000,
    )
    document = replace(
        default_studio_document(project),
        regions=(visible, disabled, outside),
    )
    executor = _ManualExecutor()
    delivered: list[StudioWaveformRegionTile] = []
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=delivered.append,
        publish_error=lambda _error: None,
        cache=WaveformTileCache(tile_peaks=8),
    )

    assert coordinator.activate(document, catalog) == 2
    coordinator.schedule(
        0,
        16,
        1.0,
        (visible.region_id, disabled.region_id, outside.region_id),
    )
    executor.run_all()
    coordinator.drain()
    assert delivered
    assert {item.region_id for item in delivered} == {visible.region_id}


def test_new_generation_cancels_stale_work_and_worker_errors_publish_on_drain(
    tmp_path: Path,
) -> None:
    project, catalog, root = _fixture(tmp_path, frame_count=128)
    document = default_studio_document(project)
    region = document.regions[0]
    executor = _ManualExecutor()
    tiles: list[StudioWaveformRegionTile] = []
    errors: list[StudioWaveformRegionError] = []
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=tiles.append,
        publish_error=errors.append,
        cache=WaveformTileCache(tile_peaks=4),
    )
    coordinator.activate(document, catalog)

    stale_generation = coordinator.schedule(
        0,
        32,
        1.0,
        (region.region_id,),
    )
    current_generation = coordinator.schedule(
        64,
        72,
        1.0,
        (region.region_id,),
    )
    assert current_generation > stale_generation
    assert coordinator.stats.in_flight <= 2
    executor.run_all()
    coordinator.drain()
    assert tiles
    assert {item.generation for item in tiles} == {current_generation}
    assert coordinator.stats.cancelled_tiles >= 2

    tiles.clear()
    source = root / "source.wav"
    replacement = root / "replacement.wav"
    sf.write(
        replacement,
        np.full(128, -0.75, dtype=np.float32),
        8_000,
        subtype="FLOAT",
    )
    assert replacement.stat().st_size == source.stat().st_size
    os.replace(replacement, source)
    error_generation = coordinator.schedule(
        80,
        88,
        1.0,
        (region.region_id,),
    )
    executor.run_all()
    assert errors == []
    coordinator.drain()
    assert tiles == []
    assert errors
    assert {item.generation for item in errors} == {error_generation}
    assert {item.region_id for item in errors} == {region.region_id}
    assert "checksum" in str(errors[0].error).lower()


def test_planned_work_bound_queues_one_global_error_without_submitting_io(
    tmp_path: Path,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=16)
    document = default_studio_document(project)
    executor = _ManualExecutor()
    errors: list[StudioWaveformRegionError] = []
    cache = WaveformTileCache(tile_peaks=1)
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=lambda _tile: None,
        publish_error=errors.append,
        cache=cache,
        max_planned_tiles=2,
    )
    coordinator.activate(document, catalog)

    generation = coordinator.schedule(
        0,
        8,
        1.0,
        (document.regions[0].region_id,),
    )
    assert executor.tasks == deque()
    assert cache.stats.source_opens == 0
    assert coordinator.stats.queued_results == 1
    assert coordinator.drain() == 1
    assert len(errors) == 1
    assert errors[0].generation == generation
    assert errors[0].region_id == ""
    assert "planned-tile limit" in str(errors[0].error)


def test_region_assignment_bound_rejects_coalesced_fanout_before_io(
    tmp_path: Path,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=16)
    original = default_studio_document(project).regions[0]
    regions = tuple(replace(original, region_id=_id(60 + index)) for index in range(3))
    document = replace(default_studio_document(project), regions=regions)
    executor = _ManualExecutor()
    errors: list[StudioWaveformRegionError] = []
    cache = WaveformTileCache(tile_peaks=32)
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=lambda _tile: None,
        publish_error=errors.append,
        cache=cache,
        max_region_assignments=2,
    )
    coordinator.activate(document, catalog)

    generation = coordinator.schedule(
        0,
        8,
        1.0,
        tuple(region.region_id for region in regions),
    )
    assert executor.tasks == deque()
    assert cache.stats.source_opens == 0
    assert coordinator.drain() == 1
    assert len(errors) == 1
    assert errors[0].generation == generation
    assert errors[0].region_id == ""
    assert "region-assignment limit" in str(errors[0].error)


def test_drain_budgets_individual_callbacks_from_one_coalesced_outcome(
    tmp_path: Path,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=16)
    original = default_studio_document(project).regions[0]
    regions = tuple(replace(original, region_id=_id(70 + index)) for index in range(5))
    document = replace(default_studio_document(project), regions=regions)
    executor = _ManualExecutor()
    delivered: list[StudioWaveformRegionTile] = []
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=delivered.append,
        publish_error=lambda _error: None,
        cache=WaveformTileCache(tile_peaks=32),
    )
    coordinator.activate(document, catalog)
    coordinator.schedule(
        0,
        8,
        1.0,
        tuple(region.region_id for region in regions),
    )
    assert len(executor.tasks) == 1
    executor.run_all()

    assert coordinator.drain(max_results=2) == 2
    assert len(delivered) == 2
    assert coordinator.drain(max_results=2) == 2
    assert len(delivered) == 4
    assert coordinator.drain(max_results=2) == 1
    assert len(delivered) == 5
    assert {item.region_id for item in delivered} == {
        region.region_id for region in regions
    }


def test_failed_activation_invalidates_prior_work_and_clears_bindings(
    tmp_path: Path,
) -> None:
    project, catalog, root = _fixture(tmp_path, frame_count=32)
    document = default_studio_document(project)
    executor = _ManualExecutor()
    delivered: list[StudioWaveformRegionTile] = []
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=delivered.append,
        publish_error=lambda _error: None,
    )
    coordinator.activate(document, catalog)
    coordinator.schedule(0, 16, 1.0, (document.regions[0].region_id,))
    manifest = root / "webjam-take.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")

    with pytest.raises(StudioWaveformCoordinatorError, match="manifest changed"):
        coordinator.activate(document, catalog)
    executor.run_all()
    assert coordinator.drain() == 0
    assert delivered == []
    assert coordinator.stats.active_regions == 0


def test_cancel_cannot_be_overwritten_by_concurrent_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=32)
    document = default_studio_document(project)
    executor = _ManualExecutor()
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=lambda _tile: None,
        publish_error=lambda _error: None,
    )
    entered = threading.Event()
    release = threading.Event()
    original_assert_current = StudioSourceCatalog.assert_current

    def blocking_assert_current(self, cancel_check=None):
        original_assert_current(self, cancel_check)
        entered.set()
        assert release.wait(5.0)

    monkeypatch.setattr(
        StudioSourceCatalog,
        "assert_current",
        blocking_assert_current,
    )
    failures: list[Exception] = []

    def activate() -> None:
        try:
            coordinator.activate(document, catalog)
        except Exception as exc:  # noqa: BLE001 - asserted below.
            failures.append(exc)

    worker = threading.Thread(target=activate)
    worker.start()
    assert entered.wait(5.0)
    coordinator.cancel()
    release.set()
    worker.join(5.0)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert "superseded" in str(failures[0]).lower()
    assert coordinator.stats.active_regions == 0


def test_real_workers_queue_results_but_drain_delivers_on_calling_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=32)
    document = default_studio_document(project)
    cache = WaveformTileCache(tile_peaks=32)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="waveform-test")
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    callback_threads: list[int] = []
    worker_finished = threading.Event()
    original_request = cache.request_tile

    def observed_request(*args, **kwargs):
        tile = original_request(*args, **kwargs)
        worker_threads.append(threading.get_ident())
        worker_finished.set()
        return tile

    monkeypatch.setattr(cache, "request_tile", observed_request)
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=lambda _tile: callback_threads.append(threading.get_ident()),
        publish_error=lambda _error: callback_threads.append(threading.get_ident()),
        cache=cache,
    )
    try:
        coordinator.activate(document, catalog)
        coordinator.schedule(0, 16, 1.0, (document.regions[0].region_id,))
        assert worker_finished.wait(5.0)
        for _index in range(200):
            if coordinator.stats.queued_results:
                break
            threading.Event().wait(0.005)
        assert coordinator.stats.queued_results == 1
        assert callback_threads == []
        assert worker_threads and set(worker_threads) != {caller_thread}

        assert coordinator.drain() == 1
        assert callback_threads == [caller_thread]
    finally:
        coordinator.shutdown(shutdown_executor=True, wait=True)


def test_immediate_completion_pumps_all_tiles_without_recursive_callbacks(
    tmp_path: Path,
) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=512)
    document = default_studio_document(project)
    executor = _ImmediateExecutor()
    delivered: list[StudioWaveformRegionTile] = []
    errors: list[StudioWaveformRegionError] = []
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=delivered.append,
        publish_error=errors.append,
        cache=WaveformTileCache(tile_peaks=1, max_viewport_tiles=512),
        max_planned_tiles=512,
    )
    coordinator.activate(document, catalog)

    coordinator.schedule(0, 512, 1.0, (document.regions[0].region_id,))

    assert executor.submissions == 512
    assert coordinator.stats.submitted_tiles == 512
    assert coordinator.stats.completed_tiles == 512
    assert coordinator.stats.pending == 0
    assert coordinator.stats.in_flight == 0
    assert coordinator.drain() == 256
    assert coordinator.drain() == 256
    assert len(delivered) == 512
    assert errors == []


def test_cancel_shutdown_and_input_validation_are_deterministic(tmp_path: Path) -> None:
    project, catalog, _root = _fixture(tmp_path, frame_count=32)
    document = default_studio_document(project)
    executor = _ManualExecutor()
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=lambda _tile: None,
        publish_error=lambda _error: None,
    )
    with pytest.raises(StudioWaveformCoordinatorError, match="activate"):
        coordinator.schedule(0, 1, 1.0, ())
    coordinator.activate(document, catalog)
    with pytest.raises(StudioWaveformCoordinatorError, match="greater"):
        coordinator.schedule(4, 4, 1.0, ())
    with pytest.raises(StudioWaveformCoordinatorError, match="pixels_per_frame"):
        coordinator.schedule(0, 4, 0.0, ())
    with pytest.raises(StudioWaveformCoordinatorError, match="iterable"):
        coordinator.schedule(0, 4, 1.0, "not-a-sequence")

    coordinator.schedule(0, 16, 1.0, (document.regions[0].region_id,))
    coordinator.cancel()
    executor.run_all()
    assert coordinator.drain() == 0
    coordinator.shutdown(shutdown_executor=True)
    assert coordinator.stats.shutdown is True
    assert executor.closed is True
    with pytest.raises(StudioWaveformCoordinatorError, match="shut down"):
        coordinator.schedule(0, 4, 1.0, ())

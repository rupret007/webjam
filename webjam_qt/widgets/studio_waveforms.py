"""Async, Qt-independent waveform coordination for the Studio Arrange view.

The coordinator separates three concerns cleanly:

* :meth:`activate` binds an immutable Studio document to its trusted source
  catalog.  This is the only setup operation that may inspect filesystem-backed
  catalog evidence.
* :meth:`schedule` performs pure frame math and bounded tile planning.  It never
  opens media and can therefore run on the Qt/UI thread.
* executor workers call the hardened waveform cache, then place generation-
  tagged outcomes on a thread-safe queue.  :meth:`drain` is the sole callback
  delivery boundary and is intended to be called by the UI thread.

No Qt type is imported here.  Arrange owns painting, geometry, and result
retention; this module owns only bounded asynchronous work and stale-result
rejection.
"""

from __future__ import annotations

import math
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Executor, Future
from dataclasses import dataclass
from typing import Final

from core.song_media_catalog import SongMediaCatalog, SongMediaCatalogError
from core.studio_project import (
    STUDIO_PROJECT_SCHEMA_VERSION,
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioRegion,
)
from core.studio_source_catalog import (
    StudioSourceCatalog,
    StudioSourceCatalogError,
)
from core.studio_waveform import (
    StudioWaveformCancelled,
    StudioWaveformError,
    WaveformRequestToken,
    WaveformSource,
    WaveformTile,
    WaveformTileCache,
    WaveformTileKey,
)


DEFAULT_STUDIO_WAVEFORM_IN_FLIGHT: Final = 2
DEFAULT_STUDIO_WAVEFORM_PLANNED_TILES: Final = 256
DEFAULT_STUDIO_WAVEFORM_REGION_ASSIGNMENTS: Final = 4_096
DEFAULT_STUDIO_WAVEFORM_DRAIN_RESULTS: Final = 256
MAX_STUDIO_WAVEFORM_IN_FLIGHT: Final = 2
MAX_STUDIO_WAVEFORM_PLANNED_TILES: Final = 4_096
MAX_STUDIO_WAVEFORM_VISIBLE_REGIONS: Final = 16_384
MAX_STUDIO_WAVEFORM_REGION_ASSIGNMENTS: Final = 16_384


class StudioWaveformCoordinatorError(RuntimeError):
    """Raised when waveform coordination cannot proceed safely."""


@dataclass(frozen=True)
class StudioWaveformRegionTile:
    """One current-generation tile ready for a visible Arrange region."""

    generation: int
    region_id: str
    tile: WaveformTile


@dataclass(frozen=True)
class StudioWaveformRegionError:
    """One current-generation source/planning error for an Arrange region."""

    generation: int
    region_id: str
    error: Exception


@dataclass(frozen=True)
class StudioWaveformCoordinatorStats:
    generation: int
    active_regions: int
    sources: int
    pending: int
    in_flight: int
    queued_results: int
    schedules: int
    planned_tiles: int
    coalesced_region_tiles: int
    submitted_tiles: int
    completed_tiles: int
    published_region_tiles: int
    published_region_errors: int
    cancelled_tiles: int
    stale_results: int
    callback_failures: int
    shutdown: bool


@dataclass(frozen=True)
class _PlannedTile:
    generation: int
    token: WaveformRequestToken
    source: WaveformSource
    key: WaveformTileKey
    region_ids: tuple[str, ...]


@dataclass(frozen=True)
class _WorkerOutcome:
    generation: int
    region_ids: tuple[str, ...]
    key: WaveformTileKey | None
    tile: WaveformTile | None = None
    error: Exception | None = None


def _positive_int(value: object, field_name: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > maximum
    ):
        raise StudioWaveformCoordinatorError(
            f"{field_name} must be between 1 and {maximum}."
        )
    return value


def _frame(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioWaveformCoordinatorError(f"{field_name} must be an integer frame.")
    return value


def _pixels_per_frame(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudioWaveformCoordinatorError(
            "pixels_per_frame must be a finite positive number."
        )
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise StudioWaveformCoordinatorError(
            "pixels_per_frame must be a finite positive number."
        )
    return result


def quantized_frames_per_peak(
    region: StudioRegion,
    pixels_per_frame: float,
    *,
    source_frame_count: int,
) -> int:
    """Return a stable power-of-two source resolution near one peak per pixel."""

    if not isinstance(region, StudioRegion):
        raise StudioWaveformCoordinatorError("region must be a StudioRegion.")
    pixels = _pixels_per_frame(pixels_per_frame)
    if (
        isinstance(source_frame_count, bool)
        or not isinstance(source_frame_count, int)
        or source_frame_count <= 0
    ):
        raise StudioWaveformCoordinatorError(
            "source_frame_count must be a positive integer."
        )
    source_count = int(region.mapping_source_frame_count)
    timeline_count = int(region.mapping_timeline_frame_count)
    log2_raw = math.log2(source_count) - math.log2(timeline_count) - math.log2(pixels)
    if log2_raw <= 0.0:
        return 1
    exponent = max(0, math.ceil(log2_raw - 1e-12))
    return min(source_frame_count, 1 << exponent)


class StudioWaveformCoordinator:
    """Bounded asynchronous bridge between Studio regions and waveform tiles."""

    def __init__(
        self,
        executor: Executor,
        *,
        publish_tile: Callable[[StudioWaveformRegionTile], None],
        publish_error: Callable[[StudioWaveformRegionError], None],
        cache: WaveformTileCache | None = None,
        max_in_flight: int = DEFAULT_STUDIO_WAVEFORM_IN_FLIGHT,
        max_planned_tiles: int = DEFAULT_STUDIO_WAVEFORM_PLANNED_TILES,
        max_region_assignments: int = DEFAULT_STUDIO_WAVEFORM_REGION_ASSIGNMENTS,
    ) -> None:
        if not callable(getattr(executor, "submit", None)):
            raise StudioWaveformCoordinatorError(
                "executor must provide a callable submit method."
            )
        if not callable(publish_tile) or not callable(publish_error):
            raise StudioWaveformCoordinatorError(
                "publish_tile and publish_error must be callable."
            )
        if cache is not None and not isinstance(cache, WaveformTileCache):
            raise StudioWaveformCoordinatorError(
                "cache must be a WaveformTileCache or null."
            )
        self._executor = executor
        self._publish_tile = publish_tile
        self._publish_error = publish_error
        self._cache = cache or WaveformTileCache()
        self._max_in_flight = _positive_int(
            max_in_flight,
            "max_in_flight",
            MAX_STUDIO_WAVEFORM_IN_FLIGHT,
        )
        self._max_planned_tiles = _positive_int(
            max_planned_tiles,
            "max_planned_tiles",
            MAX_STUDIO_WAVEFORM_PLANNED_TILES,
        )
        self._max_region_assignments = _positive_int(
            max_region_assignments,
            "max_region_assignments",
            MAX_STUDIO_WAVEFORM_REGION_ASSIGNMENTS,
        )

        self._lock = threading.RLock()
        self._document: StudioDocument | None = None
        self._catalog: StudioSourceCatalog | SongMediaCatalog | None = None
        self._regions: dict[str, StudioRegion] = {}
        self._source_by_region: dict[str, WaveformSource] = {}
        self._generation = 0
        self._token: WaveformRequestToken | None = None
        self._pending: OrderedDict[WaveformTileKey, _PlannedTile] = OrderedDict()
        self._in_flight: dict[Future[WaveformTile], _PlannedTile] = {}
        self._results: queue.SimpleQueue[_WorkerOutcome] = queue.SimpleQueue()
        self._queued_results = 0
        self._shutdown = False
        self._pumping = False

        self._schedules = 0
        self._planned_tiles = 0
        self._coalesced_region_tiles = 0
        self._submitted_tiles = 0
        self._completed_tiles = 0
        self._published_region_tiles = 0
        self._published_region_errors = 0
        self._cancelled_tiles = 0
        self._stale_results = 0
        self._callback_failures = 0

    def activate(
        self,
        document: StudioDocument,
        catalog: StudioSourceCatalog | SongMediaCatalog,
    ) -> int:
        """Bind immutable region/source facts; may inspect trusted catalog state."""

        if not isinstance(document, StudioDocument):
            raise StudioWaveformCoordinatorError("activate requires a StudioDocument.")
        if type(catalog) is StudioSourceCatalog:
            matches = (
                document.schema_version == STUDIO_PROJECT_SCHEMA_VERSION
                and document.session_id == catalog.session_id
                and document.take_id == catalog.primary_take_id
                and document.project_sample_rate == catalog.project_sample_rate
            )
        elif type(catalog) is SongMediaCatalog:
            matches = (
                document.schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                and document.project_id == catalog.project_id
                and document.project_sample_rate
                == catalog.project.project_sample_rate
            )
        else:
            raise StudioWaveformCoordinatorError(
                "activate requires a trusted StudioSourceCatalog or SongMediaCatalog."
            )
        if not matches:
            raise StudioWaveformCoordinatorError(
                "Studio document and waveform source catalog do not match."
            )

        with self._lock:
            self._require_open_locked()
            reusable_sources = (
                {
                    source.catalog_key: source
                    for source in self._source_by_region.values()
                    if source.catalog_key
                }
                if self._catalog is catalog
                else {}
            )
            # A replacement activation owns the coordinator immediately. Old
            # work must not remain publishable if catalog validation fails.
            self._invalidate_locked(discard_results=True)
            self._generation += 1
            activation_generation = self._generation
            self._document = None
            self._catalog = None
            self._regions = {}
            self._source_by_region = {}

        try:
            if type(catalog) is StudioSourceCatalog:
                catalog.assert_current()
            else:
                catalog.assert_current()
            active_regions = tuple(
                region
                for region in document.regions
                if region.enabled and not region.deleted
            )
            sources: dict[tuple[str, str, str], WaveformSource] = dict(reusable_sources)
            source_by_region: dict[str, WaveformSource] = {}
            for region in active_regions:
                if type(catalog) is StudioSourceCatalog:
                    source_key = (
                        region.source_take_id,
                        region.source_track_id,
                        region.source_segment_id,
                    )
                else:
                    source_key = (document.project_id, "media", region.source_media_id)
                source = sources.get(source_key)
                if source is None:
                    if type(catalog) is StudioSourceCatalog:
                        source = WaveformSource._from_catalog_entry(
                            catalog.resolve(*source_key)
                        )
                    else:
                        source = WaveformSource._from_song_catalog_entry(
                            catalog.resolve(region.source_media_id)
                        )
                    sources[source_key] = source
                if (
                    region.source_start_frame < 0
                    or region.source_end_frame > source.frame_count
                    or int(region.mapping_source_start_frame) < 0
                    or int(region.mapping_source_start_frame)
                    + int(region.mapping_source_frame_count)
                    > source.frame_count
                ):
                    raise StudioWaveformCoordinatorError(
                        "A Studio region escapes its cataloged waveform source."
                    )
                source_by_region[region.region_id] = source
        except (
            SongMediaCatalogError,
            StudioSourceCatalogError,
            StudioWaveformError,
        ) as exc:
            raise StudioWaveformCoordinatorError(str(exc)) from exc

        with self._lock:
            self._require_open_locked()
            if activation_generation != self._generation:
                raise StudioWaveformCoordinatorError(
                    "Studio waveform activation was superseded."
                )
            self._document = document
            self._catalog = catalog
            self._regions = {item.region_id: item for item in active_regions}
            self._source_by_region = source_by_region
            return len(active_regions)

    def schedule(
        self,
        view_start: int,
        view_end: int,
        pixels_per_frame: float,
        visible_region_ids: Iterable[str],
    ) -> int:
        """Purely plan and enqueue active visible intersections for workers."""

        start = _frame(view_start, "view_start")
        end = _frame(view_end, "view_end")
        if end <= start:
            raise StudioWaveformCoordinatorError(
                "view_end must be greater than view_start."
            )
        pixels = _pixels_per_frame(pixels_per_frame)
        if isinstance(visible_region_ids, (str, bytes)):
            raise StudioWaveformCoordinatorError(
                "visible_region_ids must be an iterable of region IDs."
            )
        visible: list[str] = []
        seen: set[str] = set()
        try:
            for raw_region_id in visible_region_ids:
                region_id = str(raw_region_id)
                if region_id in seen:
                    continue
                if len(visible) >= MAX_STUDIO_WAVEFORM_VISIBLE_REGIONS:
                    raise StudioWaveformCoordinatorError(
                        "visible_region_ids exceeds the bounded region limit."
                    )
                seen.add(region_id)
                visible.append(region_id)
        except TypeError as exc:
            raise StudioWaveformCoordinatorError(
                "visible_region_ids must be an iterable of region IDs."
            ) from exc

        with self._lock:
            self._require_open_locked()
            if self._document is None:
                raise StudioWaveformCoordinatorError(
                    "activate must be called before schedule."
                )
            self._invalidate_locked(discard_results=True)
            self._generation += 1
            generation = self._generation
            token = self._cache.begin_generation()
            self._token = token
            regions = tuple(
                self._regions[region_id]
                for region_id in visible
                if region_id in self._regions
            )
            sources = dict(self._source_by_region)
            self._schedules += 1

        planned: OrderedDict[WaveformTileKey, tuple[WaveformSource, list[str]]] = (
            OrderedDict()
        )
        planning_errors: list[tuple[str, Exception]] = []
        overflow = False
        assignment_count = 0
        try:
            for region in regions:
                intersection_start = max(start, region.timeline_start_frame)
                intersection_end = min(end, region.timeline_end_frame)
                if intersection_end <= intersection_start:
                    continue
                source = sources[region.region_id]
                source_start, source_end = self._source_intersection(
                    region,
                    intersection_start,
                    intersection_end,
                    source.frame_count,
                )
                resolution = quantized_frames_per_peak(
                    region,
                    pixels,
                    source_frame_count=source.frame_count,
                )
                try:
                    keys = self._cache.plan_viewport(
                        source,
                        start_frame=source_start,
                        frame_count=source_end - source_start,
                        frames_per_peak=resolution,
                        token=token,
                    )
                except StudioWaveformCancelled:
                    return generation
                except StudioWaveformError as exc:
                    planning_errors.append((region.region_id, exc))
                    continue
                for key in keys:
                    existing = planned.get(key)
                    if existing is None:
                        if len(planned) >= self._max_planned_tiles:
                            overflow = True
                            break
                        if assignment_count >= self._max_region_assignments:
                            overflow = True
                            break
                        planned[key] = (source, [region.region_id])
                        assignment_count += 1
                    else:
                        if assignment_count >= self._max_region_assignments:
                            overflow = True
                            break
                        existing[1].append(region.region_id)
                        assignment_count += 1
                if overflow:
                    break
        except StudioWaveformCancelled:
            return generation

        if overflow:
            planned.clear()
            planning_errors = [
                (
                    "",
                    StudioWaveformCoordinatorError(
                        "Visible waveform work exceeds the bounded planned-tile "
                        "limit or region-assignment limit."
                    ),
                )
            ]

        with self._lock:
            if (
                self._shutdown
                or generation != self._generation
                or token is not self._token
                or not self._cache.is_current(token)
            ):
                return generation
            queued_assignment_count = 0
            for key, (source, region_ids) in planned.items():
                ids = tuple(region_ids)
                queued_assignment_count += len(ids)
                self._pending[key] = _PlannedTile(
                    generation=generation,
                    token=token,
                    source=source,
                    key=key,
                    region_ids=ids,
                )
            self._planned_tiles += len(planned)
            self._coalesced_region_tiles += max(
                0,
                queued_assignment_count - len(planned),
            )
            for region_id, error in planning_errors:
                self._enqueue_locked(
                    _WorkerOutcome(
                        generation=generation,
                        region_ids=(region_id,) if region_id else (),
                        key=None,
                        error=error,
                    )
                )
            self._pump_locked()
        return generation

    @staticmethod
    def _source_intersection(
        region: StudioRegion,
        timeline_start: int,
        timeline_end: int,
        source_frame_count: int,
    ) -> tuple[int, int]:
        mapped_start = region.source_boundary_for_timeline(timeline_start)
        mapped_end = region.source_boundary_for_timeline(timeline_end)
        source_start = max(
            0,
            region.source_start_frame,
            min(mapped_start, source_frame_count),
        )
        source_end = min(
            source_frame_count,
            region.source_end_frame,
            max(mapped_end, 0),
        )
        if source_end > source_start:
            return source_start, source_end
        if source_start < min(source_frame_count, region.source_end_frame):
            return source_start, source_start + 1
        fallback_end = min(source_frame_count, region.source_end_frame)
        fallback_start = max(region.source_start_frame, fallback_end - 1)
        if fallback_end <= fallback_start:
            raise StudioWaveformCoordinatorError(
                "Visible Studio region has no waveform source frames."
            )
        return fallback_start, fallback_end

    def _run_tile(self, work: _PlannedTile) -> WaveformTile:
        return self._cache.request_tile(
            work.source,
            start_frame=work.key.start_frame,
            frame_count=work.key.frame_count,
            frames_per_peak=work.key.frames_per_peak,
            token=work.token,
        )

    def _pump_locked(self) -> None:
        if self._shutdown or self._pumping:
            return
        self._pumping = True
        try:
            while len(self._in_flight) < self._max_in_flight and self._pending:
                _key, work = self._pending.popitem(last=False)
                if work.generation != self._generation or work.token is not self._token:
                    self._cancelled_tiles += 1
                    continue
                try:
                    future = self._executor.submit(self._run_tile, work)
                except Exception:  # noqa: BLE001
                    affected = list(work.region_ids)
                    for pending in self._pending.values():
                        affected.extend(pending.region_ids)
                    self._pending.clear()
                    self._enqueue_locked(
                        _WorkerOutcome(
                            generation=work.generation,
                            region_ids=tuple(dict.fromkeys(affected)),
                            key=None,
                            error=StudioWaveformCoordinatorError(
                                "Waveform executor rejected bounded work."
                            ),
                        )
                    )
                    self._completed_tiles += 1
                    if affected:
                        self._cancelled_tiles += max(0, len(affected) - 1)
                    return
                self._in_flight[future] = work
                self._submitted_tiles += 1
                # A conforming Executor may return an already-completed Future.
                # Its callback then runs inline and re-enters _worker_done while
                # this RLock is held.  The _pumping guard makes that completion
                # iterative: this outer loop observes the released slot and
                # submits the next tile without growing the Python call stack.
                future.add_done_callback(self._worker_done)
        finally:
            self._pumping = False

    def _worker_done(self, future: Future[WaveformTile]) -> None:
        try:
            tile = future.result()
            error: Exception | None = None
        except (CancelledError, StudioWaveformCancelled) as exc:
            tile = None
            error = exc
        except Exception as exc:  # noqa: BLE001
            tile = None
            error = exc

        with self._lock:
            work = self._in_flight.pop(future, None)
            if work is None:
                return
            self._completed_tiles += 1
            current = (
                not self._shutdown
                and work.generation == self._generation
                and work.token is self._token
            )
            if isinstance(error, (CancelledError, StudioWaveformCancelled)):
                self._cancelled_tiles += 1
            elif not current:
                self._stale_results += 1
            elif error is not None:
                self._enqueue_locked(
                    _WorkerOutcome(
                        generation=work.generation,
                        region_ids=work.region_ids,
                        key=work.key,
                        error=error,
                    )
                )
            elif tile is not None:
                self._enqueue_locked(
                    _WorkerOutcome(
                        generation=work.generation,
                        region_ids=work.region_ids,
                        key=work.key,
                        tile=tile,
                    )
                )
            self._pump_locked()

    def _enqueue_locked(self, outcome: _WorkerOutcome) -> None:
        self._results.put(outcome)
        self._queued_results += 1

    def drain(self, max_results: int = DEFAULT_STUDIO_WAVEFORM_DRAIN_RESULTS) -> int:
        """Publish a bounded number of delivery work units on the calling thread.

        Coalesced worker outcomes may name many regions. The budget applies to
        individual UI callbacks, and any remainder is queued for a later tick.
        A discarded stale outcome consumes one unit so stale queues are also
        drained with bounded per-call work.
        """

        limit = _positive_int(
            max_results,
            "max_results",
            MAX_STUDIO_WAVEFORM_PLANNED_TILES,
        )
        processed = 0
        while processed < limit:
            try:
                outcome = self._results.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                self._queued_results = max(0, self._queued_results - 1)
                current = not self._shutdown and outcome.generation == self._generation
                if not current:
                    self._stale_results += 1
            if not current:
                processed += 1
                continue
            if outcome.tile is not None:
                available = limit - processed
                region_ids = outcome.region_ids[:available]
                remaining = outcome.region_ids[available:]
                if remaining:
                    with self._lock:
                        self._enqueue_locked(
                            _WorkerOutcome(
                                generation=outcome.generation,
                                region_ids=remaining,
                                key=outcome.key,
                                tile=outcome.tile,
                            )
                        )
                if not region_ids:
                    processed += 1
                    continue
                for region_id in region_ids:
                    delivery = StudioWaveformRegionTile(
                        generation=outcome.generation,
                        region_id=region_id,
                        tile=outcome.tile,
                    )
                    try:
                        self._publish_tile(delivery)
                    except Exception:  # noqa: BLE001
                        with self._lock:
                            self._callback_failures += 1
                    else:
                        with self._lock:
                            self._published_region_tiles += 1
                    processed += 1
                continue
            if outcome.error is not None:
                all_region_ids = outcome.region_ids or ("",)
                available = limit - processed
                region_ids = all_region_ids[:available]
                remaining = all_region_ids[available:]
                if remaining:
                    with self._lock:
                        self._enqueue_locked(
                            _WorkerOutcome(
                                generation=outcome.generation,
                                region_ids=remaining,
                                key=outcome.key,
                                error=outcome.error,
                            )
                        )
                for region_id in region_ids:
                    delivery = StudioWaveformRegionError(
                        generation=outcome.generation,
                        region_id=region_id,
                        error=outcome.error,
                    )
                    try:
                        self._publish_error(delivery)
                    except Exception:  # noqa: BLE001
                        with self._lock:
                            self._callback_failures += 1
                    else:
                        with self._lock:
                            self._published_region_errors += 1
                    processed += 1
                continue
            processed += 1
        return processed

    def cancel(self) -> None:
        """Invalidate pending/running work and discard queued stale outcomes."""

        with self._lock:
            if self._shutdown:
                return
            self._invalidate_locked(discard_results=True)
            self._generation += 1

    def _invalidate_locked(self, *, discard_results: bool) -> None:
        self._cache.cancel_current()
        self._token = None
        self._cancelled_tiles += len(self._pending)
        self._pending.clear()
        for future in tuple(self._in_flight):
            future.cancel()
        if discard_results:
            while True:
                try:
                    self._results.get_nowait()
                except queue.Empty:
                    break
                self._queued_results = max(0, self._queued_results - 1)
                self._stale_results += 1

    def shutdown(
        self,
        *,
        shutdown_executor: bool = False,
        wait: bool = False,
    ) -> None:
        """Permanently stop coordination; optionally close the supplied executor."""

        with self._lock:
            if self._shutdown:
                return
            self._invalidate_locked(discard_results=True)
            self._generation += 1
            self._shutdown = True
            self._document = None
            self._catalog = None
            self._regions = {}
            self._source_by_region = {}
        if shutdown_executor:
            shutdown = getattr(self._executor, "shutdown", None)
            if callable(shutdown):
                shutdown(wait=bool(wait), cancel_futures=True)

    def _require_open_locked(self) -> None:
        if self._shutdown:
            raise StudioWaveformCoordinatorError(
                "Studio waveform coordinator is shut down."
            )

    @property
    def stats(self) -> StudioWaveformCoordinatorStats:
        with self._lock:
            return StudioWaveformCoordinatorStats(
                generation=self._generation,
                active_regions=len(self._regions),
                sources=len(
                    {item.identity for item in self._source_by_region.values()}
                ),
                pending=len(self._pending),
                in_flight=len(self._in_flight),
                queued_results=self._queued_results,
                schedules=self._schedules,
                planned_tiles=self._planned_tiles,
                coalesced_region_tiles=self._coalesced_region_tiles,
                submitted_tiles=self._submitted_tiles,
                completed_tiles=self._completed_tiles,
                published_region_tiles=self._published_region_tiles,
                published_region_errors=self._published_region_errors,
                cancelled_tiles=self._cancelled_tiles,
                stale_results=self._stale_results,
                callback_failures=self._callback_failures,
                shutdown=self._shutdown,
            )


__all__ = [
    "DEFAULT_STUDIO_WAVEFORM_DRAIN_RESULTS",
    "DEFAULT_STUDIO_WAVEFORM_IN_FLIGHT",
    "DEFAULT_STUDIO_WAVEFORM_PLANNED_TILES",
    "MAX_STUDIO_WAVEFORM_IN_FLIGHT",
    "MAX_STUDIO_WAVEFORM_PLANNED_TILES",
    "MAX_STUDIO_WAVEFORM_VISIBLE_REGIONS",
    "StudioWaveformCoordinator",
    "StudioWaveformCoordinatorError",
    "StudioWaveformCoordinatorStats",
    "StudioWaveformRegionError",
    "StudioWaveformRegionTile",
    "quantized_frames_per_peak",
]

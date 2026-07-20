"""Deterministic, streaming rendering for Studio arrangements.

``TakeProject`` is the immutable source catalog and ``StudioDocument`` is the
non-destructive edit list.  This module is the boundary where those two forms
of truth become audio.  It deliberately has no playback-device or export-file
policy: both callers consume the same :class:`StudioRenderStream` blocks.

The renderer resolves every active region through durable take, track, and
segment IDs before opening media.  Source files are opened read-only, checked
against their declared facts (and checksum when one is present), and sampled
in bounded blocks.  No operation rewrites a recorder file or materializes a
whole song in memory.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from core.studio_project import (
    FadeCurve,
    MAX_PROJECT_FRAMES,
    StudioCompRange,
    StudioCrossfade,
    StudioDocument,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
)
from core.take_project import MediaSegment, MediaStatus, ProjectTrack, TakeProject


DEFAULT_RENDER_BLOCK_FRAMES = 4_096
MAX_RENDER_BLOCK_FRAMES = 1_048_576
MAX_OPEN_SOURCE_READERS = 32
_HASH_BLOCK_BYTES = 1_048_576
_USABLE_MEDIA = frozenset({MediaStatus.AVAILABLE, MediaStatus.RECOVERED})


class StudioRenderError(RuntimeError):
    """Raised when an arrangement cannot be rendered without inventing audio."""


def _integer_frame(value: object, field_name: str, *, signed: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioRenderError(f"{field_name} must be an integer frame.")
    minimum = -MAX_PROJECT_FRAMES if signed else 0
    if value < minimum or value > MAX_PROJECT_FRAMES:
        raise StudioRenderError(
            f"{field_name} must be between {minimum} and {MAX_PROJECT_FRAMES}."
        )
    return value


def _block_count(value: object, field_name: str) -> int:
    result = _integer_frame(value, field_name, signed=False)
    if result <= 0 or result > MAX_RENDER_BLOCK_FRAMES:
        raise StudioRenderError(
            f"{field_name} must be between 1 and {MAX_RENDER_BLOCK_FRAMES}."
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(_HASH_BLOCK_BYTES)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise StudioRenderError("Studio source media could not be read.") from exc
    return digest.hexdigest()


def _curve_gain(
    positions: np.ndarray,
    length: int,
    curve: FadeCurve,
    *,
    fade_in: bool,
) -> np.ndarray:
    """Vector form of ``studio_project.fade_gain`` for integer positions.

    A fade length is a count of rendered samples.  Therefore positions zero
    and ``length - 1`` are the exact endpoints; a one-sample fade is already
    at its ending gain.
    """

    if length == 1:
        value = 1.0 if fade_in else 0.0
        return np.full(positions.shape, value, dtype=np.float32)

    progress = np.clip(
        positions.astype(np.float64, copy=False) / float(length - 1),
        0.0,
        1.0,
    )
    if curve is FadeCurve.LINEAR:
        gain = progress
    elif curve is FadeCurve.EQUAL_POWER:
        gain = np.sin(progress * (math.pi / 2.0))
    else:
        gain = progress * progress * (3.0 - 2.0 * progress)
    if not fade_in:
        if curve is FadeCurve.EQUAL_POWER:
            gain = np.cos(progress * (math.pi / 2.0))
        else:
            gain = 1.0 - gain

    # Do not leave a cosine residue at an explicitly represented endpoint.
    gain = np.asarray(gain, dtype=np.float32)
    gain[positions <= 0] = 0.0 if fade_in else 1.0
    gain[positions >= length - 1] = 1.0 if fade_in else 0.0
    return gain


@dataclass(frozen=True)
class _SourcePlan:
    track: ProjectTrack
    segment: MediaSegment
    path: Path


@dataclass(frozen=True)
class _RegionPlan:
    region: StudioRegion
    track: StudioTrack
    source: _SourcePlan
    lane_id: str = ""


@dataclass(frozen=True)
class _TrackPlan:
    track: StudioTrack
    regions: tuple[_RegionPlan, ...]
    comp_ranges: tuple[StudioCompRange, ...]


def _active_region(region: StudioRegion) -> bool:
    return region.enabled and not region.deleted


def _active_lane(lane: StudioTakeLane) -> bool:
    return lane.enabled and not lane.deleted


def _active_comp(comp_range: StudioCompRange) -> bool:
    return comp_range.enabled and not comp_range.deleted


def _active_crossfade(crossfade: StudioCrossfade) -> bool:
    return not crossfade.deleted


class StudioRenderer:
    """Prepared Studio arrangement shared by playback and export.

    ``track_ids`` can restrict the resulting bus (for example, to render one
    processed stem).  Catalog validation still covers the whole active edit
    list so selecting a subset cannot hide a cross-take or forged source
    reference.  ``respect_export_included`` applies the document's export
    switches; playback normally leaves it false.

    Media validation occurs whenever :meth:`open` creates a stream.  That
    keeps construction side-effect free while ensuring no audio block can be
    returned from a missing, replaced, or structurally changed source.
    """

    def __init__(
        self,
        project: TakeProject,
        document: StudioDocument,
        take_root: str | Path,
        *,
        block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES,
        track_ids: Sequence[str] | None = None,
        respect_export_included: bool = False,
        apply_master: bool = True,
        verify_checksums: bool = True,
    ) -> None:
        if not isinstance(project, TakeProject):
            raise StudioRenderError("Studio rendering requires a TakeProject.")
        if not isinstance(document, StudioDocument):
            raise StudioRenderError("Studio rendering requires a StudioDocument.")
        if project.session_id != document.session_id:
            raise StudioRenderError("Studio document belongs to a different session.")
        if project.take_id != document.take_id:
            raise StudioRenderError("Studio document belongs to a different take.")
        if project.project_sample_rate != document.project_sample_rate:
            raise StudioRenderError(
                "Studio document and source catalog use different sample rates."
            )
        if not isinstance(respect_export_included, bool):
            raise StudioRenderError("respect_export_included must be true or false.")
        if not isinstance(apply_master, bool):
            raise StudioRenderError("apply_master must be true or false.")
        if not isinstance(verify_checksums, bool):
            raise StudioRenderError("verify_checksums must be true or false.")

        self.project = project
        self.document = document
        self.take_root = Path(take_root).expanduser().resolve()
        self.block_frames = _block_count(block_frames, "block_frames")
        self.apply_master = apply_master
        self.verify_checksums = verify_checksums

        requested: frozenset[str] | None = None
        if track_ids is not None:
            if isinstance(track_ids, (str, bytes)):
                raise StudioRenderError("track_ids must be a sequence of track IDs.")
            requested_values = tuple(str(item) for item in track_ids)
            if len(requested_values) != len(set(requested_values)):
                raise StudioRenderError("track_ids contains a duplicate ID.")
            known = {item.track_id for item in document.tracks}
            unknown = set(requested_values).difference(known)
            if unknown:
                raise StudioRenderError(
                    "A requested render track is not in the Studio document."
                )
            requested = frozenset(requested_values)

        self._sources, all_regions, lane_by_region, comps_by_track = self._catalog()
        selected_tracks = tuple(
            track
            for track in sorted(
                document.tracks, key=lambda item: (item.order, item.track_id)
            )
            if (requested is None or track.track_id in requested)
            and (not respect_export_included or track.export_included)
        )
        selected_ids = {item.track_id for item in selected_tracks}
        plans: list[_TrackPlan] = []
        for track in selected_tracks:
            regions = tuple(
                _RegionPlan(
                    region=region,
                    track=track,
                    source=self._sources[region.source_segment_id],
                    lane_id=lane_by_region.get(region.region_id, ""),
                )
                for region in all_regions
                if region.track_id == track.track_id
            )
            plans.append(
                _TrackPlan(
                    track=track,
                    regions=regions,
                    comp_ranges=comps_by_track.get(track.track_id, ()),
                )
            )
        self._track_plans = tuple(plans)
        self._selected_track_ids = frozenset(selected_ids)
        self._crossfades_by_region = self._prepare_crossfades(all_regions)

        rendered_regions = tuple(
            plan.region for track in self._track_plans for plan in track.regions
        )
        self.timeline_start_frame = min(
            (item.timeline_start_frame for item in rendered_regions), default=0
        )
        self.timeline_end_frame = max(
            (item.timeline_end_frame for item in rendered_regions), default=0
        )
        self.total_frames = max(0, self.timeline_end_frame)

    @property
    def sample_rate(self) -> int:
        return self.document.project_sample_rate

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(item.track.track_id for item in self._track_plans)

    def _catalog(
        self,
    ) -> tuple[
        dict[str, _SourcePlan],
        tuple[StudioRegion, ...],
        dict[str, str],
        dict[str, tuple[StudioCompRange, ...]],
    ]:
        """Resolve the complete active edit list against immutable catalog IDs."""

        project_tracks = {item.track_id: item for item in self.project.tracks}
        segments: dict[str, tuple[ProjectTrack, MediaSegment]] = {}
        for track in self.project.tracks:
            for segment in track.segments:
                segments[segment.segment_id] = (track, segment)

        active_regions = tuple(
            sorted(
                (item for item in self.document.regions if _active_region(item)),
                key=lambda item: (
                    item.track_id,
                    item.timeline_start_frame,
                    item.timeline_end_frame,
                    item.region_id,
                ),
            )
        )
        source_plans: dict[str, _SourcePlan] = {}
        for region in active_regions:
            if region.source_take_id != self.project.take_id:
                raise StudioRenderError(
                    "Studio region references media from a different take."
                )
            source_track = project_tracks.get(region.source_track_id)
            found = segments.get(region.source_segment_id)
            if source_track is None or found is None or found[0] is not source_track:
                raise StudioRenderError(
                    "Studio region does not match the source catalog."
                )
            segment = found[1]
            if region.source_end_frame > segment.frame_count:
                raise StudioRenderError(
                    "Studio region extends beyond its cataloged source segment."
                )
            mapping_source_start = int(region.mapping_source_start_frame)
            mapping_source_count = int(region.mapping_source_frame_count)
            if (
                mapping_source_start < 0
                or mapping_source_start + mapping_source_count > segment.frame_count
            ):
                raise StudioRenderError(
                    "Studio region's affine map escapes its source segment."
                )
            path = (self.take_root / segment.path).resolve()
            try:
                path.relative_to(self.take_root)
            except ValueError as exc:
                raise StudioRenderError(
                    "Studio source path escapes the take folder."
                ) from exc
            source_plans.setdefault(
                segment.segment_id,
                _SourcePlan(track=source_track, segment=segment, path=path),
            )

        owned_lanes = {
            item.lane_id: item for item in self.document.take_lanes if not item.deleted
        }
        active_lanes = {
            lane_id: lane for lane_id, lane in owned_lanes.items() if _active_lane(lane)
        }
        region_by_id = {item.region_id: item for item in active_regions}
        lane_by_region: dict[str, str] = {}
        for lane in sorted(owned_lanes.values(), key=lambda item: item.lane_id):
            for region_id in lane.region_ids:
                region = region_by_id.get(region_id)
                if region is None:
                    continue
                previous = lane_by_region.setdefault(region_id, lane.lane_id)
                if previous != lane.lane_id:
                    raise StudioRenderError(
                        "An active Studio region belongs to more than one take lane."
                    )
                if lane.source_take_id and (
                    region.source_take_id != lane.source_take_id
                    or region.source_track_id != lane.source_track_id
                ):
                    raise StudioRenderError(
                        "Take-lane media does not match its declared source."
                    )

        comps_by_track: dict[str, list[StudioCompRange]] = {}
        for comp_range in sorted(
            (item for item in self.document.comp_ranges if _active_comp(item)),
            key=lambda item: (
                item.track_id,
                item.timeline_start_frame,
                item.comp_range_id,
            ),
        ):
            lane = active_lanes.get(comp_range.lane_id)
            if lane is None:
                raise StudioRenderError("Comp range references an inactive take lane.")
            if (
                comp_range.fade_in_frames + comp_range.fade_out_frames
                > comp_range.frame_count
            ):
                raise StudioRenderError(
                    "Comp fade ranges overlap and cannot be rendered safely."
                )
            lane_regions = tuple(
                region_by_id[region_id]
                for region_id in lane.region_ids
                if region_id in region_by_id
            )
            self._require_comp_coverage(comp_range, lane_regions)
            comps_by_track.setdefault(comp_range.track_id, []).append(comp_range)

        return (
            source_plans,
            active_regions,
            lane_by_region,
            {key: tuple(value) for key, value in comps_by_track.items()},
        )

    @staticmethod
    def _require_comp_coverage(
        comp_range: StudioCompRange,
        regions: Sequence[StudioRegion],
    ) -> None:
        cursor = comp_range.timeline_start_frame
        for region in sorted(
            regions,
            key=lambda item: (
                item.timeline_start_frame,
                item.timeline_end_frame,
                item.region_id,
            ),
        ):
            if region.timeline_end_frame <= cursor:
                continue
            if region.timeline_start_frame > cursor:
                break
            cursor = max(cursor, region.timeline_end_frame)
            if cursor >= comp_range.timeline_end_frame:
                return
        raise StudioRenderError(
            "Comp range is not fully covered by its selected take lane."
        )

    def _prepare_crossfades(
        self, active_regions: Sequence[StudioRegion]
    ) -> dict[str, tuple[tuple[StudioCrossfade, bool], ...]]:
        regions = {item.region_id: item for item in active_regions}
        by_region: dict[str, list[tuple[StudioCrossfade, bool]]] = {}
        for crossfade in sorted(
            (item for item in self.document.crossfades if _active_crossfade(item)),
            key=lambda item: (
                item.start_frame,
                item.end_frame,
                item.crossfade_id,
            ),
        ):
            left = regions.get(crossfade.left_region_id)
            right = regions.get(crossfade.right_region_id)
            if left is None or right is None or left.track_id != right.track_id:
                raise StudioRenderError(
                    "Crossfade does not reference two active regions on one track."
                )
            overlap_start = max(left.timeline_start_frame, right.timeline_start_frame)
            overlap_end = min(left.timeline_end_frame, right.timeline_end_frame)
            if (
                overlap_end <= overlap_start
                or crossfade.start_frame < overlap_start
                or crossfade.end_frame > overlap_end
            ):
                raise StudioRenderError(
                    "Crossfade extends outside its regions' overlap."
                )
            by_region.setdefault(left.region_id, []).append((crossfade, False))
            by_region.setdefault(right.region_id, []).append((crossfade, True))

        for region_id, values in by_region.items():
            ordered = sorted(values, key=lambda item: item[0].start_frame)
            for previous, following in zip(ordered, ordered[1:]):
                if following[0].start_frame < previous[0].end_frame:
                    raise StudioRenderError(
                        "A region has overlapping crossfade envelopes."
                    )
            by_region[region_id] = ordered
        return {key: tuple(value) for key, value in by_region.items()}

    def validate_media(self) -> None:
        """Validate each referenced source with bounded reads and no writes."""

        if not self.take_root.is_dir():
            raise StudioRenderError("The take folder is missing.")
        try:
            import soundfile as sf  # type: ignore
        except ImportError as exc:  # pragma: no cover - packaged dependency
            raise StudioRenderError("Studio audio support is unavailable.") from exc

        for source in sorted(
            self._sources.values(), key=lambda item: item.segment.segment_id
        ):
            track = source.track
            segment = source.segment
            if (
                track.media_status not in _USABLE_MEDIA
                or segment.media_status not in _USABLE_MEDIA
            ):
                raise StudioRenderError(
                    "Studio source media is unavailable or requires review."
                )
            try:
                stat = source.path.stat()
            except OSError as exc:
                raise StudioRenderError("Studio source media is missing.") from exc
            if not source.path.is_file():
                raise StudioRenderError("Studio source media is missing.")
            if segment.size_bytes and stat.st_size != segment.size_bytes:
                raise StudioRenderError("Studio source media changed size.")
            if self.verify_checksums and segment.sha256:
                if _sha256(source.path) != segment.sha256:
                    raise StudioRenderError("Studio source media checksum changed.")
            try:
                with sf.SoundFile(str(source.path), mode="r") as reader:
                    observed = (
                        int(reader.samplerate),
                        int(reader.channels),
                        int(len(reader)),
                    )
            except Exception as exc:
                raise StudioRenderError("Studio source media is corrupt.") from exc
            declared = (
                int(segment.sample_rate),
                int(segment.channels),
                int(segment.frame_count),
            )
            if observed != declared:
                raise StudioRenderError(
                    "Studio source media facts do not match the catalog."
                )
            if segment.channels > 2:
                raise StudioRenderError(
                    "Studio cannot safely infer a stereo layout for this source."
                )

    def open(
        self,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
    ) -> "StudioRenderStream":
        """Validate media and return a bounded, seekable render stream."""

        start = _integer_frame(start_frame, "start_frame")
        if end_frame is None:
            end = max(start, self.timeline_end_frame)
        else:
            end = _integer_frame(end_frame, "end_frame")
            if end < start:
                raise StudioRenderError("end_frame must not precede start_frame.")
        self.validate_media()
        return StudioRenderStream(self, start_frame=start, end_frame=end)

    def iter_blocks(
        self,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        block_frames: int | None = None,
    ) -> Iterator[np.ndarray]:
        """Yield consecutive stereo float32 blocks from the authoritative path."""

        count = (
            self.block_frames
            if block_frames is None
            else _block_count(block_frames, "block_frames")
        )
        with self.open(start_frame=start_frame, end_frame=end_frame) as stream:
            while True:
                block = stream.read(count)
                if not len(block):
                    return
                yield block

    def render_block(self, start_frame: int, frame_count: int) -> np.ndarray:
        """Render one random-access block through the same streaming mixer."""

        start = _integer_frame(start_frame, "start_frame")
        count = _block_count(frame_count, "frame_count")
        end = start + count
        if end > MAX_PROJECT_FRAMES:
            raise StudioRenderError("Requested render block is outside the timeline.")
        with self.open(start_frame=start, end_frame=end) as stream:
            return stream.read(count)

    def _region_envelope(
        self, region: StudioRegion, positions: np.ndarray
    ) -> np.ndarray:
        offsets = positions - region.timeline_start_frame
        gain = np.ones(positions.shape, dtype=np.float32)
        if region.fade_in_frames:
            mask = offsets < region.fade_in_frames
            gain[mask] *= _curve_gain(
                offsets[mask],
                region.fade_in_frames,
                region.fade_in_curve,
                fade_in=True,
            )
        if region.fade_out_frames:
            fade_start = region.timeline_frame_count - region.fade_out_frames
            mask = offsets >= fade_start
            gain[mask] *= _curve_gain(
                offsets[mask] - fade_start,
                region.fade_out_frames,
                region.fade_out_curve,
                fade_in=False,
            )
        for crossfade, incoming in self._crossfades_by_region.get(region.region_id, ()):
            mask = (positions >= crossfade.start_frame) & (
                positions < crossfade.end_frame
            )
            if np.any(mask):
                gain[mask] *= _curve_gain(
                    positions[mask] - crossfade.start_frame,
                    crossfade.frame_count,
                    crossfade.curve,
                    fade_in=incoming,
                )
        return gain

    @staticmethod
    def _comp_pair(
        comp_range: StudioCompRange, positions: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return equal-power ``(base, selected)`` gains inside a comp range."""

        offsets = positions - comp_range.timeline_start_frame
        selected = np.ones(positions.shape, dtype=np.float32)
        base = np.zeros(positions.shape, dtype=np.float32)
        if comp_range.fade_in_frames:
            mask = offsets < comp_range.fade_in_frames
            selected[mask] = _curve_gain(
                offsets[mask],
                comp_range.fade_in_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=True,
            )
            base[mask] = _curve_gain(
                offsets[mask],
                comp_range.fade_in_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=False,
            )
        if comp_range.fade_out_frames:
            fade_start = comp_range.frame_count - comp_range.fade_out_frames
            mask = offsets >= fade_start
            selected[mask] = _curve_gain(
                offsets[mask] - fade_start,
                comp_range.fade_out_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=False,
            )
            base[mask] = _curve_gain(
                offsets[mask] - fade_start,
                comp_range.fade_out_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=True,
            )
        return base, selected

    def _comp_envelope(
        self,
        plan: _RegionPlan,
        comp_ranges: Sequence[StudioCompRange],
        positions: np.ndarray,
    ) -> np.ndarray:
        if plan.lane_id:
            gain = np.zeros(positions.shape, dtype=np.float32)
        else:
            gain = np.ones(positions.shape, dtype=np.float32)
        for comp_range in comp_ranges:
            mask = (positions >= comp_range.timeline_start_frame) & (
                positions < comp_range.timeline_end_frame
            )
            if not np.any(mask):
                continue
            base, selected = self._comp_pair(comp_range, positions[mask])
            if not plan.lane_id:
                gain[mask] = base
            elif plan.lane_id == comp_range.lane_id:
                gain[mask] = selected
            else:
                gain[mask] = 0.0
        return gain


class StudioRenderStream:
    """Context-managed readers and transport position for one renderer."""

    def __init__(
        self,
        renderer: StudioRenderer,
        *,
        start_frame: int,
        end_frame: int,
    ) -> None:
        self.renderer = renderer
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.position_frame = start_frame
        self._readers: OrderedDict[str, object] = OrderedDict()
        self._closed = False

    @property
    def sample_rate(self) -> int:
        return self.renderer.sample_rate

    @property
    def remaining_frames(self) -> int:
        return max(0, self.end_frame - self.position_frame)

    @property
    def closed(self) -> bool:
        return self._closed

    def _reader_for(self, source: _SourcePlan):
        segment_id = source.segment.segment_id
        existing = self._readers.pop(segment_id, None)
        if existing is not None:
            self._readers[segment_id] = existing
            return existing
        try:
            import soundfile as sf  # type: ignore

            reader = sf.SoundFile(str(source.path), mode="r")
            observed = (
                int(reader.samplerate),
                int(reader.channels),
                int(len(reader)),
            )
            declared = (
                source.segment.sample_rate,
                source.segment.channels,
                source.segment.frame_count,
            )
            if observed != declared:
                reader.close()
                raise StudioRenderError("Studio source media changed while opening.")
            self._readers[segment_id] = reader
            while len(self._readers) > MAX_OPEN_SOURCE_READERS:
                _old_id, old_reader = self._readers.popitem(last=False)
                old_reader.close()
            return reader
        except Exception as exc:
            if isinstance(exc, StudioRenderError):
                raise
            raise StudioRenderError("Studio source media could not be opened.") from exc

    def __enter__(self) -> "StudioRenderStream":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._closed = True

    def seek(self, frame: int) -> int:
        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        target = _integer_frame(frame, "frame")
        if target < self.start_frame or target > self.end_frame:
            raise StudioRenderError("Seek frame is outside this render stream.")
        self.position_frame = target
        return target

    def read(self, frame_count: int) -> np.ndarray:
        """Return up to ``frame_count`` stereo frames and advance transport."""

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        requested = _block_count(frame_count, "frame_count")
        count = min(requested, self.remaining_frames)
        if count <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        start = self.position_frame
        try:
            block = self._render(start, count)
        except Exception as exc:
            if isinstance(exc, StudioRenderError):
                raise
            raise StudioRenderError(
                "Studio source media failed while rendering."
            ) from exc
        self.position_frame += count
        return block

    def _render(self, start: int, count: int) -> np.ndarray:
        mix = np.zeros((count, 2), dtype=np.float32)
        any_solo = any(item.track.solo for item in self.renderer._track_plans)
        for track_plan in self.renderer._track_plans:
            state = track_plan.track
            audible = not state.muted and (state.solo or not any_solo)
            if not audible or state.trim_gain <= 0.0 or state.fader_gain <= 0.0:
                continue
            track_mix = np.zeros((count, 2), dtype=np.float32)
            for region_plan in track_plan.regions:
                rendered = self._read_region(region_plan, start, count)
                if rendered is None:
                    continue
                destination, positions, source = rendered
                gain = self.renderer._region_envelope(region_plan.region, positions)
                if track_plan.comp_ranges or region_plan.lane_id:
                    gain *= self.renderer._comp_envelope(
                        region_plan,
                        track_plan.comp_ranges,
                        positions,
                    )
                source *= gain[:, np.newaxis]
                stereo = self._to_stereo(source)
                track_mix[destination : destination + len(stereo)] += stereo

            pan = np.float32(state.pan)
            if pan < 0.0:
                track_mix[:, 1] *= np.float32(1.0) + pan
            elif pan > 0.0:
                track_mix[:, 0] *= np.float32(1.0) - pan
            track_mix *= np.float32(state.trim_gain * state.fader_gain)
            mix += track_mix

        if self.renderer.apply_master:
            mix *= np.float32(self.renderer.document.master.gain)
            if self.renderer.document.master.limiter_enabled:
                np.clip(mix, -1.0, 1.0, out=mix)
        if not np.all(np.isfinite(mix)):
            raise StudioRenderError("Studio render produced non-finite audio.")
        return mix

    @staticmethod
    def _to_stereo(source: np.ndarray) -> np.ndarray:
        if source.shape[1] == 1:
            return np.repeat(source, 2, axis=1)
        return source[:, :2].copy()

    def _read_region(
        self,
        plan: _RegionPlan,
        output_start: int,
        output_count: int,
    ) -> tuple[int, np.ndarray, np.ndarray] | None:
        region = plan.region
        overlap_start = max(output_start, region.timeline_start_frame)
        overlap_end = min(output_start + output_count, region.timeline_end_frame)
        if overlap_end <= overlap_start:
            return None

        positions = np.arange(overlap_start, overlap_end, dtype=np.int64)
        timeline_offsets = positions - int(region.mapping_timeline_start_frame)
        source_positions = float(
            region.mapping_source_start_frame
        ) + timeline_offsets.astype(np.float64) * (
            float(region.mapping_source_frame_count)
            / float(region.mapping_timeline_frame_count)
        )
        # The region's integer source bounds are edit boundaries, while the
        # preserved affine map owns sample positions.  A rounded split may
        # need one neighboring interpolation sample on either side to remain
        # bit-identical to its unsplit parent, so clamp only to the cataloged
        # segment rather than recomputing/clamping to each child's range.
        source_limit = float(plan.source.segment.frame_count - 1)
        np.clip(
            source_positions,
            0.0,
            source_limit,
            out=source_positions,
        )
        lower = np.floor(source_positions).astype(np.int64)
        upper = np.minimum(lower + 1, plan.source.segment.frame_count - 1).astype(
            np.int64
        )
        source_indices = np.unique(np.concatenate((lower, upper)))
        source = np.empty(
            (len(source_indices), plan.source.segment.channels), dtype=np.float32
        )
        reader = self._reader_for(plan.source)
        boundaries = np.flatnonzero(np.diff(source_indices) != 1) + 1
        run_starts = np.concatenate((np.array([0]), boundaries))
        run_ends = np.concatenate((boundaries, np.array([len(source_indices)])))
        for run_start, run_end in zip(run_starts, run_ends):
            first = int(source_indices[run_start])
            run_count = int(run_end - run_start)
            try:
                reader.seek(first)
                values = reader.read(run_count, dtype="float32", always_2d=True)
            except Exception as exc:
                raise StudioRenderError(
                    "Studio source media could not be read."
                ) from exc
            if len(values) != run_count:
                raise StudioRenderError(
                    "Studio source media ended before its cataloged range."
                )
            source[run_start:run_end] = values
        if not np.all(np.isfinite(source)):
            raise StudioRenderError("Studio source media contains non-finite samples.")

        for gap in plan.source.segment.gaps:
            targets = gap.channels or tuple(range(source.shape[1]))
            source_mask = (source_indices >= gap.start_frame) & (
                source_indices < gap.end_frame
            )
            if np.any(source_mask):
                for channel in targets:
                    source[source_mask, channel] = 0.0

        lower_values = source[np.searchsorted(source_indices, lower)]
        upper_values = source[np.searchsorted(source_indices, upper)]
        fraction = (source_positions - lower.astype(np.float64))[:, np.newaxis]
        rendered = (
            lower_values.astype(np.float64)
            + (upper_values.astype(np.float64) - lower_values.astype(np.float64))
            * fraction
        ).astype(np.float32)

        # The cataloged gap is authoritative even if neighboring interpolation
        # samples would otherwise leak into its fractional-frame boundaries.
        for gap in plan.source.segment.gaps:
            targets = gap.channels or tuple(range(rendered.shape[1]))
            gap_mask = (source_positions >= gap.start_frame) & (
                source_positions < gap.end_frame
            )
            if np.any(gap_mask):
                for channel in targets:
                    rendered[gap_mask, channel] = 0.0

        return overlap_start - output_start, positions, rendered


def iter_studio_blocks(
    project: TakeProject,
    document: StudioDocument,
    take_root: str | Path,
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
    block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES,
    track_ids: Sequence[str] | None = None,
    respect_export_included: bool = False,
    apply_master: bool = True,
    verify_checksums: bool = True,
) -> Iterator[np.ndarray]:
    """Convenience iterator over :class:`StudioRenderer`'s shared path."""

    renderer = StudioRenderer(
        project,
        document,
        take_root,
        block_frames=block_frames,
        track_ids=track_ids,
        respect_export_included=respect_export_included,
        apply_master=apply_master,
        verify_checksums=verify_checksums,
    )
    yield from renderer.iter_blocks(
        start_frame=start_frame,
        end_frame=end_frame,
        block_frames=block_frames,
    )


__all__ = [
    "DEFAULT_RENDER_BLOCK_FRAMES",
    "MAX_OPEN_SOURCE_READERS",
    "MAX_RENDER_BLOCK_FRAMES",
    "StudioRenderError",
    "StudioRenderer",
    "StudioRenderStream",
    "iter_studio_blocks",
]

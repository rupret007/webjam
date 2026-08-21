"""Atomic, non-destructive song-section reordering for Studio documents.

A section move is a permutation of project time, not a collection of region
drags.  The selected section and the intervening block exchange places on
every track.  Regions are split at the permutation seams so each resulting
fragment retains one exact affine mapping to immutable source media.

This module performs no media or manifest I/O.  It returns one fully validated
``StudioDocument`` with one forward revision, making the operation suitable
for a single history command.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from core.studio_project import (
    MAX_PROJECT_FRAMES,
    MAX_STUDIO_REGIONS,
    FadeCurve,
    MarkerKind,
    StudioCrossfade,
    StudioDocument,
    StudioMarker,
    StudioProjectError,
    StudioRegion,
)

SplitIdFactory = Callable[[], str]


class StudioSectionError(StudioProjectError):
    """Raised when a song section cannot be reordered truthfully."""


@dataclass(frozen=True)
class _Permutation:
    section_start: int
    section_end: int
    target_start: int

    @property
    def section_length(self) -> int:
        return self.section_end - self.section_start

    @property
    def cuts(self) -> tuple[int, ...]:
        if self.target_start < self.section_start:
            return (self.target_start, self.section_start, self.section_end)
        return (
            self.section_start,
            self.section_end,
            self.target_start + self.section_length,
        )

    def delta_at(self, frame: int) -> int:
        """Return the half-open permutation delta for one original frame."""

        length = self.section_length
        if self.target_start < self.section_start:
            if self.target_start <= frame < self.section_start:
                return length
            if self.section_start <= frame < self.section_end:
                return self.target_start - self.section_start
            return 0
        if self.section_start <= frame < self.section_end:
            return self.target_start - self.section_start
        if self.section_end <= frame < self.target_start + length:
            return -length
        return 0

    def interval_delta(self, start: int, end: int, description: str) -> int:
        """Return one interval's delta or reject a non-representable seam."""

        if any(start < cut < end for cut in self.cuts):
            raise StudioSectionError(
                f"{description} crosses a section-reorder boundary."
            )
        return self.delta_at(start)


@dataclass(frozen=True)
class _FragmentPlan:
    timeline_start: int
    timeline_end: int
    source_start: int
    source_end: int
    delta: int


@dataclass(frozen=True)
class _RegionPlan:
    original: StudioRegion
    fragments: tuple[_FragmentPlan, ...]


@dataclass(frozen=True)
class _BuiltFragment:
    original_start: int
    original_end: int
    region: StudioRegion


def _canonical_marker_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioSectionError("section_marker_id must be a UUID.") from exc


def _target_frame(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioSectionError("target_start_frame must be an integer frame.")
    if value < 0 or value > MAX_PROJECT_FRAMES:
        raise StudioSectionError(
            "target_start_frame is outside the Studio project frame range."
        )
    return value


def _selected_section(document: StudioDocument, marker_id: str) -> StudioMarker:
    marker = next(
        (item for item in document.markers if item.marker_id == marker_id),
        None,
    )
    if marker is None:
        raise StudioSectionError("The section marker is not part of this document.")
    if marker.deleted:
        raise StudioSectionError("A deleted section marker cannot be reordered.")
    if marker.kind is not MarkerKind.SECTION or marker.end_frame is None:
        raise StudioSectionError("Only an active section marker can be reordered.")
    return marker


def _plan_region(
    region: StudioRegion,
    permutation: _Permutation,
) -> _RegionPlan:
    if region.deleted:
        return _RegionPlan(
            region,
            (
                _FragmentPlan(
                    region.timeline_start_frame,
                    region.timeline_end_frame,
                    region.source_start_frame,
                    region.source_end_frame,
                    0,
                ),
            ),
        )

    fade_in_end = region.timeline_start_frame + region.fade_in_frames
    fade_out_start = region.timeline_end_frame - region.fade_out_frames
    if any(
        region.timeline_start_frame < cut < fade_in_end
        or fade_out_start < cut < region.timeline_end_frame
        for cut in permutation.cuts
    ):
        raise StudioSectionError("A section-reorder boundary crosses a region fade.")

    boundaries = (
        region.timeline_start_frame,
        *(
            cut
            for cut in permutation.cuts
            if region.timeline_start_frame < cut < region.timeline_end_frame
        ),
        region.timeline_end_frame,
    )
    source_boundaries = tuple(
        region.source_boundary_for_timeline(frame) for frame in boundaries
    )
    if any(
        right <= left for left, right in itertools.pairwise(source_boundaries)
    ):
        raise StudioSectionError(
            "A region cannot be split at an exact interior source-frame boundary."
        )

    fragments: list[_FragmentPlan] = []
    for index, (start, end) in enumerate(itertools.pairwise(boundaries)):
        delta = permutation.interval_delta(start, end, "A region fragment")
        mapping_start = int(region.mapping_timeline_start_frame) + delta
        if not -MAX_PROJECT_FRAMES <= mapping_start <= MAX_PROJECT_FRAMES:
            raise StudioSectionError(
                "A reordered region would exceed the Studio mapping frame range."
            )
        if (
            mapping_start + int(region.mapping_timeline_frame_count)
            > MAX_PROJECT_FRAMES
        ):
            raise StudioSectionError(
                "A reordered region would exceed the Studio mapping frame range."
            )
        moved_start = start + delta
        moved_end = end + delta
        if (
            moved_start < -MAX_PROJECT_FRAMES
            or moved_start > MAX_PROJECT_FRAMES
            or moved_end > MAX_PROJECT_FRAMES
        ):
            raise StudioSectionError(
                "A reordered region would exceed the Studio timeline frame range."
            )
        fragments.append(
            _FragmentPlan(
                timeline_start=start,
                timeline_end=end,
                source_start=source_boundaries[index],
                source_end=source_boundaries[index + 1],
                delta=delta,
            )
        )
    return _RegionPlan(region, tuple(fragments))


def _canonical_split_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioSectionError(
            "The split ID factory must return valid UUIDs."
        ) from exc


def _allocate_split_ids(
    count: int,
    factory: SplitIdFactory,
    existing_ids: set[str],
) -> tuple[str, ...]:
    values: list[str] = []
    used = set(existing_ids)
    for _index in range(count):
        try:
            candidate = _canonical_split_id(factory())
        except StudioSectionError:
            raise
        except Exception as exc:
            raise StudioSectionError("The split ID factory failed.") from exc
        if candidate in used:
            raise StudioSectionError("The split ID factory returned a duplicate ID.")
        used.add(candidate)
        values.append(candidate)
    return tuple(values)


def _build_region_fragments(
    plan: _RegionPlan,
    split_ids: tuple[str, ...],
) -> tuple[_BuiltFragment, ...]:
    original = plan.original
    if original.deleted:
        return (
            _BuiltFragment(
                original.timeline_start_frame,
                original.timeline_end_frame,
                original,
            ),
        )

    last_index = len(plan.fragments) - 1
    built: list[_BuiltFragment] = []
    for index, fragment in enumerate(plan.fragments):
        timeline_count = fragment.timeline_end - fragment.timeline_start
        region_id = original.region_id if index == 0 else split_ids[index - 1]
        if len(plan.fragments) == 1 and fragment.delta == 0:
            value = original
        else:
            value = replace(
                original,
                region_id=region_id,
                source_start_frame=fragment.source_start,
                source_frame_count=fragment.source_end - fragment.source_start,
                timeline_start_frame=fragment.timeline_start + fragment.delta,
                timeline_frame_count=timeline_count,
                mapping_timeline_start_frame=(
                    int(original.mapping_timeline_start_frame) + fragment.delta
                ),
                fade_in_frames=(
                    min(original.fade_in_frames, timeline_count) if index == 0 else 0
                ),
                fade_out_frames=(
                    min(original.fade_out_frames, timeline_count)
                    if index == last_index
                    else 0
                ),
                fade_in_curve=(
                    original.fade_in_curve if index == 0 else FadeCurve.LINEAR
                ),
                fade_out_curve=(
                    original.fade_out_curve if index == last_index else FadeCurve.LINEAR
                ),
            )
        built.append(
            _BuiltFragment(
                fragment.timeline_start,
                fragment.timeline_end,
                value,
            )
        )
    return tuple(built)


def _crossfade_region_id(
    fragments_by_region: dict[str, tuple[_BuiltFragment, ...]],
    region_id: str,
    start: int,
    end: int,
) -> str:
    candidates = tuple(
        item.region.region_id
        for item in fragments_by_region[region_id]
        if item.original_start <= start and end <= item.original_end
    )
    if len(candidates) != 1:
        raise StudioSectionError(
            "A crossfade cannot be assigned to one reordered region fragment."
        )
    return candidates[0]


def reorder_section(
    document: StudioDocument,
    section_marker_id: str,
    target_start_frame: int,
    *,
    split_id_factory: SplitIdFactory | None = None,
) -> StudioDocument:
    """Move one active ``SECTION`` and ripple-permute the intervening time.

    ``target_start_frame`` is the section's final start.  For a later move,
    original time in ``[section_end, target_start + section_length)`` ripples
    left.  For an earlier move, original time in
    ``[target_start, section_start)`` ripples right.  Point markers follow the
    half-open block beginning at their frame.

    Non-deleted regions are split at every permutation seam, including
    disabled alternatives, while tombstones remain unchanged.  Non-region
    intervals that cross a seam are rejected because a single stored interval
    could no longer describe their meaning truthfully.
    """

    if not isinstance(document, StudioDocument):
        raise StudioSectionError("document must be a StudioDocument.")
    marker_id = _canonical_marker_id(section_marker_id)
    section = _selected_section(document, marker_id)
    target = _target_frame(target_start_frame)
    section_end = int(section.end_frame)
    if section.start_frame < target < section_end:
        raise StudioSectionError("The target start cannot be inside the section.")
    if target == section.start_frame:
        return document

    length = section_end - section.start_frame
    if target + length > MAX_PROJECT_FRAMES:
        raise StudioSectionError(
            "The reordered section would exceed the Studio project frame range."
        )
    if document.revision >= MAX_PROJECT_FRAMES:
        raise StudioSectionError("Studio document revision is exhausted.")
    if split_id_factory is not None and not callable(split_id_factory):
        raise StudioSectionError("split_id_factory must be callable.")
    permutation = _Permutation(section.start_frame, section_end, target)

    markers: list[StudioMarker] = []
    for marker in document.markers:
        if marker.deleted:
            markers.append(marker)
        elif marker.marker_id == section.marker_id:
            markers.append(
                replace(marker, start_frame=target, end_frame=target + length)
            )
        elif marker.kind is MarkerKind.MARKER:
            markers.append(
                replace(
                    marker,
                    start_frame=(
                        marker.start_frame + permutation.delta_at(marker.start_frame)
                    ),
                )
            )
        else:
            marker_end = int(marker.end_frame)
            delta = permutation.interval_delta(
                marker.start_frame,
                marker_end,
                f'Section marker "{marker.label}"',
            )
            markers.append(
                replace(
                    marker,
                    start_frame=marker.start_frame + delta,
                    end_frame=marker_end + delta,
                )
            )

    comp_ranges = []
    for comp_range in document.comp_ranges:
        if comp_range.deleted:
            comp_ranges.append(comp_range)
            continue
        delta = permutation.interval_delta(
            comp_range.timeline_start_frame,
            comp_range.timeline_end_frame,
            "A comp range",
        )
        comp_ranges.append(
            replace(
                comp_range,
                timeline_start_frame=comp_range.timeline_start_frame + delta,
            )
        )

    crossfade_deltas: dict[str, int] = {}
    for crossfade in document.crossfades:
        if crossfade.deleted:
            continue
        crossfade_deltas[crossfade.crossfade_id] = permutation.interval_delta(
            crossfade.start_frame,
            crossfade.end_frame,
            "A crossfade",
        )

    cycle_range = document.cycle_range
    if cycle_range is not None:
        cycle_delta = permutation.interval_delta(
            cycle_range.start_frame,
            cycle_range.end_frame,
            "The cycle range",
        )
        cycle_range = replace(
            cycle_range,
            start_frame=cycle_range.start_frame + cycle_delta,
            end_frame=cycle_range.end_frame + cycle_delta,
        )

    plans = tuple(_plan_region(region, permutation) for region in document.regions)
    split_count = sum(len(plan.fragments) - 1 for plan in plans)
    if len(document.regions) + split_count > MAX_STUDIO_REGIONS:
        raise StudioSectionError(
            "The section reorder would exceed the Studio region limit."
        )
    factory = split_id_factory or (lambda: str(uuid.uuid4()))
    allocated_ids = iter(
        _allocate_split_ids(
            split_count,
            factory,
            {item.region_id for item in document.regions},
        )
    )

    fragments_by_region: dict[str, tuple[_BuiltFragment, ...]] = {}
    region_values: list[StudioRegion] = []
    for plan in plans:
        fragment_ids = tuple(
            next(allocated_ids) for _index in range(len(plan.fragments) - 1)
        )
        built = _build_region_fragments(plan, fragment_ids)
        fragments_by_region[plan.original.region_id] = built
        region_values.extend(item.region for item in built)

    lanes = tuple(
        replace(
            lane,
            region_ids=tuple(
                fragment.region.region_id
                for region_id in lane.region_ids
                for fragment in fragments_by_region[region_id]
            ),
        )
        for lane in document.take_lanes
    )

    crossfades: list[StudioCrossfade] = []
    for crossfade in document.crossfades:
        if crossfade.deleted:
            crossfades.append(crossfade)
            continue
        delta = crossfade_deltas[crossfade.crossfade_id]
        crossfades.append(
            replace(
                crossfade,
                left_region_id=_crossfade_region_id(
                    fragments_by_region,
                    crossfade.left_region_id,
                    crossfade.start_frame,
                    crossfade.end_frame,
                ),
                right_region_id=_crossfade_region_id(
                    fragments_by_region,
                    crossfade.right_region_id,
                    crossfade.start_frame,
                    crossfade.end_frame,
                ),
                start_frame=crossfade.start_frame + delta,
            )
        )

    try:
        return replace(
            document,
            regions=tuple(region_values),
            take_lanes=lanes,
            comp_ranges=tuple(comp_ranges),
            markers=tuple(markers),
            crossfades=tuple(crossfades),
            cycle_range=cycle_range,
            revision=document.revision + 1,
        )
    except StudioProjectError as exc:
        raise StudioSectionError(
            "The section reorder would create an invalid Studio document."
        ) from exc


__all__ = ["SplitIdFactory", "StudioSectionError", "reorder_section"]

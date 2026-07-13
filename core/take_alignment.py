"""Deterministic, non-destructive transient alignment for recorded takes.

The aligner reads native-rate PCM in bounded blocks, detects shared transient
anchors, and fits the affine mapping::

    project_time = source_time * (1 + drift_ppm / 1_000_000) + offset

No source is rewritten or resampled.  A 44.1 kHz source and a 48 kHz
reference therefore stay at their recorded rates; only their frame positions
are converted to seconds before fitting.  Declared gaps are removed from
transient analysis, and separate media segments are analyzed independently.

The checked fixtures document realistic certification tolerances rather than
claiming sample-perfect behavior: start offset within 1.5 ms, linear drift
within 15 ppm over the accelerated 80-second fixture, and fitted anchor RMS
within 2 ms.  Those are evidence thresholds for click fixtures, not a promise
that unrelated program material can always be aligned automatically.
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from core.take_project import (
    AlignmentAnchor,
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    ProjectTrack,
)


READ_BLOCK_FRAMES = 65_536
MAX_TRANSIENTS_PER_SOURCE = 4_096
MAX_MATCH_TRANSIENTS = 384
MAX_STORED_ANCHORS = 64
MAX_OFFSET_CANDIDATES = 32

CERTIFIED_OFFSET_TOLERANCE_S = 0.0015
CERTIFIED_DRIFT_TOLERANCE_PPM = 15.0
CERTIFIED_ANCHOR_RMS_TOLERANCE_MS = 2.0

ALIGNMENT_CONFIDENCE_MIN = 0.72
SIGNAL_FLOOR = 1.0e-5
MIN_TRANSIENT_SEPARATION_S = 0.04
TRANSIENT_RELEASE_S = 0.003
OFFSET_VOTE_BIN_S = 0.05
FINAL_MATCH_TOLERANCE_S = 0.012
MIN_DRIFT_SPAN_S = 10.0
DEFAULT_MAX_OFFSET_S = 30.0
DEFAULT_MAX_DRIFT_PPM = 5_000.0
_METHOD = "gap-aware-transients-v1"


class AlignmentError(RuntimeError):
    """Raised when declared media and the readable PCM disagree."""


class AlignmentOutcome(str, Enum):
    """Whether an automatic result is safe to use without user review."""

    ALIGNED = "aligned"
    UNCERTAIN = "uncertain"
    NO_SIGNAL = "no_signal"


@dataclass(frozen=True)
class TransientPoint:
    """One bounded-streaming transient observation on an absolute timeline."""

    time_s: float
    strength: float
    segment_id: str = ""


@dataclass(frozen=True)
class TransientAnalysis:
    """Immutable evidence collected from one file or a set of segments."""

    transients: tuple[TransientPoint, ...]
    peak: float
    rms: float
    duration_s: float
    had_signal: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnchorEvidence:
    """A persisted anchor plus the confidence of this individual match."""

    anchor: AlignmentAnchor
    confidence: float
    source_strength: float
    reference_strength: float


@dataclass(frozen=True)
class AlignmentResult:
    """Automatic alignment state and the evidence behind its outcome."""

    state: AlignmentState
    outcome: AlignmentOutcome
    reason: str
    anchor_evidence: tuple[AnchorEvidence, ...] = ()
    source_transient_count: int = 0
    reference_transient_count: int = 0
    issues: tuple[str, ...] = ()

    @property
    def is_aligned(self) -> bool:
        return self.outcome is AlignmentOutcome.ALIGNED


@dataclass(frozen=True)
class TimeTransform:
    """Deterministic source/project time mapping; never changes source PCM."""

    automatic_offset_s: float = 0.0
    drift_ppm: float = 0.0
    manual_nudge_s: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.automatic_offset_s,
            self.drift_ppm,
            self.manual_nudge_s,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Time-transform values must be finite.")
        if self.scale <= 0.0:
            raise ValueError("Time-transform drift must retain forward time.")

    @property
    def scale(self) -> float:
        return 1.0 + float(self.drift_ppm) / 1_000_000.0

    @property
    def effective_offset_s(self) -> float:
        return float(self.automatic_offset_s) + float(self.manual_nudge_s)

    @classmethod
    def from_state(cls, state: AlignmentState) -> "TimeTransform":
        return cls(
            automatic_offset_s=state.automatic_offset_s,
            drift_ppm=state.drift_ppm,
            manual_nudge_s=state.manual_nudge_s,
        )

    def source_to_project(self, source_time_s: float) -> float:
        value = float(source_time_s)
        if not math.isfinite(value):
            raise ValueError("source_time_s must be finite.")
        return value * self.scale + self.effective_offset_s

    def project_to_source(self, project_time_s: float) -> float:
        value = float(project_time_s)
        if not math.isfinite(value):
            raise ValueError("project_time_s must be finite.")
        return (value - self.effective_offset_s) / self.scale

    def source_frame_to_project_frame(
        self,
        source_frame: int,
        *,
        source_sample_rate: int,
        project_sample_rate: int,
    ) -> int:
        if source_frame < 0 or source_sample_rate <= 0 or project_sample_rate <= 0:
            raise ValueError("Frame positions and sample rates must be positive.")
        project_position = self.source_to_project(
            source_frame / source_sample_rate
        ) * project_sample_rate
        return _round_half_away_from_zero(project_position)


@dataclass(frozen=True)
class _Match:
    source: TransientPoint
    reference: TransientPoint


@dataclass(frozen=True)
class _Fit:
    offset_s: float
    scale: float
    matches: tuple[_Match, ...]
    rms_s: float


def analyze_audio_transients(
    path: str | Path,
    *,
    gaps: Sequence[GapInterval] = (),
    timeline_start_s: float = 0.0,
    expected_sample_rate: int | None = None,
    expected_channels: int | None = None,
    expected_frame_count: int | None = None,
    segment_id: str = "",
) -> TransientAnalysis:
    """Read one PCM file in bounded blocks and return transient evidence.

    ``timeline_start_s`` places a standalone segment on its source/reference
    timeline.  Gap frame indices remain local to the file, as represented by
    :class:`~core.take_project.GapInterval`.
    """
    try:
        import numpy as np
        import soundfile as sf  # type: ignore
    except ImportError as exc:  # pragma: no cover - packaging dependency guard
        raise AlignmentError("Alignment requires numpy and soundfile.") from exc

    media_path = Path(path)
    if not math.isfinite(float(timeline_start_s)) or timeline_start_s < 0:
        raise ValueError("timeline_start_s must be a finite non-negative value.")

    try:
        with sf.SoundFile(str(media_path)) as audio:
            rate = int(audio.samplerate)
            channels = int(audio.channels)
            actual_frames = int(len(audio))
    except (OSError, RuntimeError) as exc:
        raise AlignmentError(f"Could not read {media_path.name}: {exc}") from exc

    if expected_sample_rate is not None and rate != int(expected_sample_rate):
        raise AlignmentError(
            f"{media_path.name} declares {expected_sample_rate} Hz but contains "
            f"{rate} Hz PCM."
        )
    if expected_channels is not None and channels != int(expected_channels):
        raise AlignmentError(
            f"{media_path.name} declares {expected_channels} channels but contains "
            f"{channels}."
        )
    if rate <= 0 or channels <= 0:
        raise AlignmentError(f"{media_path.name} has invalid PCM metadata.")

    declared_frames = (
        int(expected_frame_count)
        if expected_frame_count is not None and int(expected_frame_count) > 0
        else actual_frames
    )
    read_frames = min(actual_frames, declared_frames)
    issues: list[str] = []
    if expected_frame_count is not None and actual_frames != int(expected_frame_count):
        issues.append(
            f"{media_path.name} contains {actual_frames} frames; "
            f"{expected_frame_count} were declared."
        )

    ordered_gaps = tuple(sorted(gaps, key=lambda item: (item.start_frame, item.end_frame)))
    peak = 0.0
    sum_squares = 0.0
    valid_frames = 0
    with sf.SoundFile(str(media_path)) as audio:
        cursor = 0
        while cursor < read_frames:
            count = min(READ_BLOCK_FRAMES, read_frames - cursor)
            block = audio.read(count, dtype="float32", always_2d=True)
            if not len(block):
                break
            amplitude, valid = _block_amplitude(np, block, cursor, ordered_gaps)
            if bool(np.any(valid)):
                usable = amplitude[valid]
                peak = max(peak, float(np.max(usable)))
                sum_squares += float(np.dot(usable, usable))
                valid_frames += int(usable.size)
            cursor += int(len(block))

    rms = math.sqrt(sum_squares / valid_frames) if valid_frames else 0.0
    had_signal = peak > SIGNAL_FLOOR
    if not had_signal or read_frames <= 0:
        return TransientAnalysis(
            (), peak, rms, read_frames / rate, had_signal, tuple(issues)
        )

    threshold = max(SIGNAL_FLOOR * 5.0, peak * 0.18, rms * 3.5)
    release_frames = max(1, int(round(TRANSIENT_RELEASE_S * rate)))
    minimum_separation = max(1, int(round(MIN_TRANSIENT_SEPARATION_S * rate)))
    bucket_frames = max(
        minimum_separation,
        math.ceil(max(1, read_frames) / MAX_TRANSIENTS_PER_SOURCE),
    )
    bucket_peaks: dict[int, tuple[int, float]] = {}
    active_peak_frame = -1
    active_peak_strength = 0.0
    last_above_frame = -1

    def finalize_active() -> None:
        nonlocal active_peak_frame, active_peak_strength, last_above_frame
        if active_peak_frame >= 0:
            bucket = active_peak_frame // bucket_frames
            previous = bucket_peaks.get(bucket)
            candidate = (active_peak_frame, active_peak_strength)
            if previous is None or (candidate[1], -candidate[0]) > (
                previous[1], -previous[0]
            ):
                bucket_peaks[bucket] = candidate
        active_peak_frame = -1
        active_peak_strength = 0.0
        last_above_frame = -1

    with sf.SoundFile(str(media_path)) as audio:
        cursor = 0
        while cursor < read_frames:
            count = min(READ_BLOCK_FRAMES, read_frames - cursor)
            block = audio.read(count, dtype="float32", always_2d=True)
            if not len(block):
                break
            amplitude, valid = _block_amplitude(np, block, cursor, ordered_gaps)
            indices = np.flatnonzero(valid & (amplitude >= threshold))
            for local_index in indices.tolist():
                frame = cursor + int(local_index)
                strength = float(amplitude[local_index])
                if last_above_frame >= 0 and frame - last_above_frame > release_frames:
                    finalize_active()
                if active_peak_frame < 0 or strength > active_peak_strength:
                    active_peak_frame = frame
                    active_peak_strength = strength
                last_above_frame = frame
            # A cluster cannot continue through a long block tail containing
            # no above-threshold samples.  Closing it here also bounds state
            # across a declared gap at the next block edge.
            if last_above_frame >= 0 and cursor + len(block) - last_above_frame > release_frames:
                finalize_active()
            cursor += int(len(block))
    finalize_active()

    raw_points = [
        TransientPoint(
            time_s=float(timeline_start_s) + frame / rate,
            strength=strength,
            segment_id=segment_id,
        )
        for frame, strength in sorted(bucket_peaks.values())
    ]
    points = _coalesce_transients(raw_points)
    return TransientAnalysis(
        tuple(points),
        peak,
        rms,
        read_frames / rate,
        had_signal,
        tuple(issues),
    )


def align_audio_files(
    reference_path: str | Path,
    source_path: str | Path,
    *,
    reference_gaps: Sequence[GapInterval] = (),
    source_gaps: Sequence[GapInterval] = (),
    reference_start_s: float = 0.0,
    source_start_s: float = 0.0,
    max_offset_s: float = DEFAULT_MAX_OFFSET_S,
    max_drift_ppm: float = DEFAULT_MAX_DRIFT_PPM,
) -> AlignmentResult:
    """Map ``source_path`` time onto ``reference_path`` project time."""
    try:
        reference = analyze_audio_transients(
            reference_path,
            gaps=reference_gaps,
            timeline_start_s=reference_start_s,
        )
        source = analyze_audio_transients(
            source_path,
            gaps=source_gaps,
            timeline_start_s=source_start_s,
        )
    except AlignmentError as exc:
        return _empty_result(
            AlignmentOutcome.UNCERTAIN,
            str(exc),
            issues=(str(exc),),
        )
    return _align_analyses(
        reference,
        source,
        max_offset_s=max_offset_s,
        max_drift_ppm=max_drift_ppm,
    )


def align_media_segments(
    reference_root: str | Path,
    reference_segments: Iterable[MediaSegment],
    source_root: str | Path,
    source_segments: Iterable[MediaSegment],
    *,
    project_sample_rate: int,
    max_offset_s: float = DEFAULT_MAX_OFFSET_S,
    max_drift_ppm: float = DEFAULT_MAX_DRIFT_PPM,
) -> AlignmentResult:
    """Align explicit media segments without stitching or resampling them."""
    if project_sample_rate <= 0:
        raise ValueError("project_sample_rate must be greater than zero.")
    reference = _analyze_segments(
        Path(reference_root), tuple(reference_segments), project_sample_rate
    )
    source = _analyze_segments(
        Path(source_root), tuple(source_segments), project_sample_rate
    )
    return _align_analyses(
        reference,
        source,
        max_offset_s=max_offset_s,
        max_drift_ppm=max_drift_ppm,
    )


def align_project_tracks(
    take_root: str | Path,
    reference_track: ProjectTrack,
    source_track: ProjectTrack,
    *,
    project_sample_rate: int,
    max_offset_s: float = DEFAULT_MAX_OFFSET_S,
    max_drift_ppm: float = DEFAULT_MAX_DRIFT_PPM,
) -> AlignmentResult:
    """Convenience wrapper for two tracks in one take directory."""
    return align_media_segments(
        take_root,
        reference_track.segments,
        take_root,
        source_track.segments,
        project_sample_rate=project_sample_rate,
        max_offset_s=max_offset_s,
        max_drift_ppm=max_drift_ppm,
    )


def _block_amplitude(np, block, start_frame: int, gaps: Sequence[GapInterval]):
    data = block
    valid = np.ones(len(block), dtype=bool)
    block_end = start_frame + len(block)
    copied = False
    for gap in gaps:
        if gap.end_frame <= start_frame:
            continue
        if gap.start_frame >= block_end:
            break
        lo = max(gap.start_frame, start_frame) - start_frame
        hi = min(gap.end_frame, block_end) - start_frame
        channels = gap.channels or tuple(range(block.shape[1]))
        if not copied:
            data = block.copy()
            copied = True
        data[lo:hi, list(channels)] = 0.0
        if len(set(channels)) >= block.shape[1]:
            valid[lo:hi] = False
    amplitude = np.max(np.abs(data), axis=1) if len(data) else np.zeros(0)
    return amplitude, valid


def _coalesce_transients(points: Sequence[TransientPoint]) -> list[TransientPoint]:
    result: list[TransientPoint] = []
    for point in sorted(points, key=lambda item: (item.time_s, item.segment_id)):
        if result and point.time_s - result[-1].time_s < MIN_TRANSIENT_SEPARATION_S:
            previous = result[-1]
            if (point.strength, -point.time_s) > (
                previous.strength, -previous.time_s
            ):
                result[-1] = point
        else:
            result.append(point)
    return result


def _analyze_segments(
    root: Path,
    segments: Sequence[MediaSegment],
    project_sample_rate: int,
) -> TransientAnalysis:
    root = root.expanduser().resolve()
    points: list[TransientPoint] = []
    issues: list[str] = []
    peak = 0.0
    square_energy = 0.0
    weighted_duration = 0.0
    duration_s = 0.0
    had_signal = False
    usable_statuses = {
        MediaStatus.AVAILABLE,
        MediaStatus.RECOVERED,
        MediaStatus.PARTIAL,
    }
    for segment in sorted(
        segments, key=lambda item: (item.project_start_frame, item.segment_id)
    ):
        if segment.media_status not in usable_statuses:
            issues.append(
                f"{segment.path} is {segment.media_status.value}; alignment could not inspect it."
            )
            continue
        if segment.media_status is MediaStatus.PARTIAL:
            issues.append(f"{segment.path} is partial; full-take alignment is uncertain.")
        media_path = (root / segment.path).resolve()
        try:
            media_path.relative_to(root)
        except ValueError:
            issues.append(f"{segment.path} resolves outside the take directory.")
            continue
        if not media_path.is_file():
            issues.append(f"{segment.path} is missing.")
            continue
        start_s = segment.project_start_frame / project_sample_rate
        try:
            analysis = analyze_audio_transients(
                media_path,
                gaps=segment.gaps,
                timeline_start_s=start_s,
                expected_sample_rate=segment.sample_rate,
                expected_channels=segment.channels,
                expected_frame_count=segment.frame_count,
                segment_id=segment.segment_id,
            )
        except AlignmentError as exc:
            issues.append(str(exc))
            continue
        points.extend(analysis.transients)
        issues.extend(analysis.issues)
        peak = max(peak, analysis.peak)
        square_energy += analysis.rms * analysis.rms * analysis.duration_s
        weighted_duration += analysis.duration_s
        duration_s = max(duration_s, start_s + analysis.duration_s)
        had_signal = had_signal or analysis.had_signal
    rms = math.sqrt(square_energy / weighted_duration) if weighted_duration else 0.0
    reduced = _coalesce_transients(points)
    if len(reduced) > MAX_TRANSIENTS_PER_SOURCE:
        reduced = list(_uniform_select(reduced, MAX_TRANSIENTS_PER_SOURCE))
    return TransientAnalysis(
        tuple(reduced),
        peak,
        rms,
        duration_s,
        had_signal,
        tuple(dict.fromkeys(issues)),
    )


def _align_analyses(
    reference: TransientAnalysis,
    source: TransientAnalysis,
    *,
    max_offset_s: float,
    max_drift_ppm: float,
) -> AlignmentResult:
    if not math.isfinite(float(max_offset_s)) or max_offset_s <= 0:
        raise ValueError("max_offset_s must be a finite positive value.")
    if not math.isfinite(float(max_drift_ppm)) or max_drift_ppm < 0:
        raise ValueError("max_drift_ppm must be a finite non-negative value.")
    issues = tuple(dict.fromkeys(reference.issues + source.issues))
    counts = (len(source.transients), len(reference.transients))
    if not reference.had_signal or not source.had_signal:
        missing = "reference" if not reference.had_signal else "source"
        return _empty_result(
            AlignmentOutcome.NO_SIGNAL,
            f"The {missing} contains no usable signal for automatic alignment.",
            source_count=counts[0],
            reference_count=counts[1],
            issues=issues,
        )
    if not reference.transients or not source.transients:
        return _empty_result(
            AlignmentOutcome.UNCERTAIN,
            "No usable transient anchors were found in both recordings.",
            source_count=counts[0],
            reference_count=counts[1],
            issues=issues,
        )

    source_events = _uniform_select(source.transients, MAX_MATCH_TRANSIENTS)
    reference_events = _uniform_select(reference.transients, MAX_MATCH_TRANSIENTS)
    fit = _best_fit(
        source_events,
        reference_events,
        max_offset_s=max_offset_s,
        max_drift_ppm=max_drift_ppm,
    )
    if fit is None or len(fit.matches) < 2:
        return _empty_result(
            AlignmentOutcome.UNCERTAIN,
            "The recordings do not contain a repeatable shared transient pattern.",
            source_count=counts[0],
            reference_count=counts[1],
            issues=issues,
        )

    source_span = max(
        0.0, source_events[-1].time_s - source_events[0].time_s
    )
    reference_span = max(
        0.0, reference_events[-1].time_s - reference_events[0].time_s
    )
    matched_source_span = max(
        0.0, fit.matches[-1].source.time_s - fit.matches[0].source.time_s
    )
    matched_reference_span = max(
        0.0,
        fit.matches[-1].reference.time_s - fit.matches[0].reference.time_s,
    )
    source_coverage = matched_source_span / source_span if source_span > 0 else 1.0
    reference_coverage = (
        matched_reference_span / reference_span if reference_span > 0 else 1.0
    )
    span_coverage = min(1.0, source_coverage, reference_coverage)
    match_f1 = 2.0 * len(fit.matches) / (
        len(source_events) + len(reference_events)
    )
    zones = _matched_zones(fit.matches, source_events)
    zone_coverage = zones / 3.0
    residual_quality = math.exp(-((fit.rms_s / 0.004) ** 2))
    anchor_factor = min(1.0, len(fit.matches) / 3.0)
    confidence = anchor_factor * (
        0.40 * match_f1
        + 0.25 * span_coverage
        + 0.20 * zone_coverage
        + 0.15 * residual_quality
    )
    confidence = min(1.0, max(0.0, confidence))

    complete_evidence = not issues
    aligned = (
        complete_evidence
        and len(fit.matches) >= 3
        and zones >= 2
        and span_coverage >= 0.45
        and confidence >= ALIGNMENT_CONFIDENCE_MIN
    )
    outcome = AlignmentOutcome.ALIGNED if aligned else AlignmentOutcome.UNCERTAIN
    if issues:
        confidence = min(confidence, ALIGNMENT_CONFIDENCE_MIN - 0.01)

    stored_matches = _uniform_select(fit.matches, MAX_STORED_ANCHORS)
    method = _METHOD if aligned else f"{_METHOD}-uncertain"
    drift_ppm = (fit.scale - 1.0) * 1_000_000.0
    preliminary_anchors = tuple(
        AlignmentAnchor(
            source_time_s=_clean(match.source.time_s),
            project_time_s=_clean(match.reference.time_s),
            residual_ms=_clean(
                (
                    match.reference.time_s
                    - (fit.offset_s + fit.scale * match.source.time_s)
                )
                * 1_000.0,
                digits=9,
            ),
        )
        for match in stored_matches
    )
    state = AlignmentState(
        automatic_offset_s=_clean(fit.offset_s),
        drift_ppm=_clean(drift_ppm, digits=9),
        confidence=_clean(confidence, digits=9),
        method=method,
        residual_ms=_clean(fit.rms_s * 1_000.0, digits=9),
        anchors=preliminary_anchors,
    )

    evidence: list[AnchorEvidence] = []
    source_peak = max(source.peak, SIGNAL_FLOOR)
    reference_peak = max(reference.peak, SIGNAL_FLOOR)
    for match, anchor in zip(stored_matches, state.anchors):
        source_strength = min(1.0, match.source.strength / source_peak)
        reference_strength = min(1.0, match.reference.strength / reference_peak)
        strength_similarity = (
            min(source_strength, reference_strength)
            / max(source_strength, reference_strength)
            if max(source_strength, reference_strength) > 0
            else 0.0
        )
        residual_score = max(
            0.0, 1.0 - abs(anchor.residual_ms) / (FINAL_MATCH_TOLERANCE_S * 1_000.0)
        )
        local_confidence = (
            0.70 * residual_score + 0.30 * strength_similarity
        ) * (0.5 + 0.5 * confidence)
        evidence.append(AnchorEvidence(
            anchor=anchor,
            confidence=_clean(min(1.0, max(0.0, local_confidence)), digits=9),
            source_strength=_clean(source_strength, digits=9),
            reference_strength=_clean(reference_strength, digits=9),
        ))

    if aligned:
        reason = (
            f"Matched {len(fit.matches)} shared transients across {zones} timeline zones."
        )
    elif issues:
        reason = "Media evidence is incomplete; automatic alignment requires review."
    else:
        reason = (
            f"Only {len(fit.matches)} shared transients across {zones} timeline zones "
            "could be verified; alignment remains uncertain."
        )
    return AlignmentResult(
        state=state,
        outcome=outcome,
        reason=reason,
        anchor_evidence=tuple(evidence),
        source_transient_count=counts[0],
        reference_transient_count=counts[1],
        issues=issues,
    )


def _best_fit(
    source: Sequence[TransientPoint],
    reference: Sequence[TransientPoint],
    *,
    max_offset_s: float,
    max_drift_ppm: float,
) -> _Fit | None:
    candidates = _offset_candidates(source, reference, max_offset_s)
    if not candidates:
        return None
    total_span = max(
        source[-1].time_s - source[0].time_s,
        reference[-1].time_s - reference[0].time_s,
    )
    drift_allowance = total_span * max_drift_ppm / 1_000_000.0 / 2.0
    initial_tolerance = min(0.25, max(0.08, drift_allowance + 0.03))
    typical_gap = _typical_event_gap(source, reference)
    if typical_gap is not None:
        initial_tolerance = min(initial_tolerance, max(0.04, typical_gap * 0.30))

    best: _Fit | None = None
    best_score: tuple[float, ...] | None = None
    for offset in candidates:
        matches = _match_events(source, reference, offset, 1.0, initial_tolerance)
        if len(matches) < 2:
            continue
        fit_offset, scale = _fit_line(matches)
        if abs((scale - 1.0) * 1_000_000.0) > max_drift_ppm:
            continue
        refined = _match_events(
            source,
            reference,
            fit_offset,
            scale,
            FINAL_MATCH_TOLERANCE_S,
        )
        if len(refined) < 2:
            continue
        refined = _reject_outliers(refined, fit_offset, scale)
        if len(refined) < 2:
            continue
        fit_offset, scale = _fit_line(refined)
        if abs((scale - 1.0) * 1_000_000.0) > max_drift_ppm:
            continue
        final_matches = _match_events(
            source,
            reference,
            fit_offset,
            scale,
            FINAL_MATCH_TOLERANCE_S,
        )
        final_matches = _reject_outliers(final_matches, fit_offset, scale)
        if len(final_matches) < 2:
            continue
        fit_offset, scale = _fit_line(final_matches)
        residuals = [
            match.reference.time_s - (fit_offset + scale * match.source.time_s)
            for match in final_matches
        ]
        rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
        coverage = final_matches[-1].source.time_s - final_matches[0].source.time_s
        score = (
            float(len(final_matches)),
            coverage,
            -rms,
            -abs((scale - 1.0) * 1_000_000.0),
            -abs(fit_offset),
        )
        if best_score is None or score > best_score:
            best_score = score
            best = _Fit(fit_offset, scale, tuple(final_matches), rms)
    return best


def _offset_candidates(
    source: Sequence[TransientPoint],
    reference: Sequence[TransientPoint],
    max_offset_s: float,
) -> tuple[float, ...]:
    votes: dict[int, int] = {}
    for source_point in source:
        for reference_point in reference:
            difference = reference_point.time_s - source_point.time_s
            if abs(difference) <= max_offset_s:
                bucket = _round_half_away_from_zero(difference / OFFSET_VOTE_BIN_S)
                votes[bucket] = votes.get(bucket, 0) + 1
    ranked = sorted(votes, key=lambda item: (-votes[item], abs(item), item))
    candidates = [
        bucket * OFFSET_VOTE_BIN_S for bucket in ranked[:MAX_OFFSET_CANDIDATES]
    ]
    candidates.extend((
        reference[0].time_s - source[0].time_s,
        reference[-1].time_s - source[-1].time_s,
        statistics.median(item.time_s for item in reference)
        - statistics.median(item.time_s for item in source),
    ))
    for numerator in range(5):
        source_index = _quantile_index(len(source), numerator, 4)
        reference_index = _quantile_index(len(reference), numerator, 4)
        difference = (
            reference[reference_index].time_s - source[source_index].time_s
        )
        if abs(difference) <= max_offset_s:
            candidates.append(difference)
    unique: list[float] = []
    seen: set[int] = set()
    for value in candidates:
        if abs(value) > max_offset_s:
            continue
        key = _round_half_away_from_zero(value * 1_000_000.0)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return tuple(unique)


def _match_events(
    source: Sequence[TransientPoint],
    reference: Sequence[TransientPoint],
    offset_s: float,
    scale: float,
    tolerance_s: float,
) -> tuple[_Match, ...]:
    reference_times = [item.time_s for item in reference]
    used: set[int] = set()
    matches: list[_Match] = []
    for source_point in source:
        predicted = offset_s + scale * source_point.time_s
        insertion = bisect.bisect_left(reference_times, predicted)
        candidates: list[tuple[float, int]] = []
        left = insertion - 1
        while left >= 0 and predicted - reference_times[left] <= tolerance_s:
            if left not in used:
                candidates.append((abs(reference_times[left] - predicted), left))
            left -= 1
        right = insertion
        while right < len(reference) and reference_times[right] - predicted <= tolerance_s:
            if right not in used:
                candidates.append((abs(reference_times[right] - predicted), right))
            right += 1
        if not candidates:
            continue
        _, selected = min(candidates, key=lambda item: (item[0], item[1]))
        used.add(selected)
        matches.append(_Match(source_point, reference[selected]))
    return tuple(sorted(matches, key=lambda item: item.source.time_s))


def _fit_line(matches: Sequence[_Match]) -> tuple[float, float]:
    source_values = [item.source.time_s for item in matches]
    reference_values = [item.reference.time_s for item in matches]
    span = max(source_values) - min(source_values)
    if len(matches) < 3 or span < MIN_DRIFT_SPAN_S:
        return (
            statistics.median(
                reference - source
                for source, reference in zip(source_values, reference_values)
            ),
            1.0,
        )
    source_mean = math.fsum(source_values) / len(source_values)
    reference_mean = math.fsum(reference_values) / len(reference_values)
    variance = math.fsum(
        (value - source_mean) * (value - source_mean) for value in source_values
    )
    if variance <= 0.0:
        return statistics.median(
            reference - source
            for source, reference in zip(source_values, reference_values)
        ), 1.0
    covariance = math.fsum(
        (source - source_mean) * (reference - reference_mean)
        for source, reference in zip(source_values, reference_values)
    )
    scale = covariance / variance
    return reference_mean - scale * source_mean, scale


def _reject_outliers(
    matches: Sequence[_Match], offset_s: float, scale: float
) -> tuple[_Match, ...]:
    if len(matches) <= 3:
        return tuple(matches)
    residuals = [
        item.reference.time_s - (offset_s + scale * item.source.time_s)
        for item in matches
    ]
    center = statistics.median(residuals)
    mad = statistics.median(abs(value - center) for value in residuals)
    cutoff = min(
        FINAL_MATCH_TOLERANCE_S,
        max(0.0015, 4.0 * 1.4826 * mad + 0.0005),
    )
    return tuple(
        item
        for item, residual in zip(matches, residuals)
        if abs(residual - center) <= cutoff
    )


def _matched_zones(
    matches: Sequence[_Match], source: Sequence[TransientPoint]
) -> int:
    if not matches or not source:
        return 0
    start = source[0].time_s
    span = source[-1].time_s - start
    if span <= 0.0:
        return 1
    zones = {
        min(2, max(0, int((match.source.time_s - start) / span * 3.0)))
        for match in matches
    }
    return len(zones)


def _typical_event_gap(
    source: Sequence[TransientPoint], reference: Sequence[TransientPoint]
) -> float | None:
    gaps = [
        later.time_s - earlier.time_s
        for events in (source, reference)
        for earlier, later in zip(events, events[1:])
        if later.time_s > earlier.time_s
    ]
    return statistics.median(gaps) if gaps else None


def _uniform_select(items: Sequence, limit: int) -> tuple:
    if len(items) <= limit:
        return tuple(items)
    if limit <= 1:
        return (items[0],)
    indices = [
        (index * (len(items) - 1) + (limit - 1) // 2) // (limit - 1)
        for index in range(limit)
    ]
    return tuple(items[index] for index in indices)


def _empty_result(
    outcome: AlignmentOutcome,
    reason: str,
    *,
    source_count: int = 0,
    reference_count: int = 0,
    issues: tuple[str, ...] = (),
) -> AlignmentResult:
    suffix = outcome.value
    return AlignmentResult(
        state=AlignmentState(method=f"{_METHOD}-{suffix}"),
        outcome=outcome,
        reason=reason,
        source_transient_count=source_count,
        reference_transient_count=reference_count,
        issues=issues,
    )


def _quantile_index(length: int, numerator: int, denominator: int) -> int:
    return (numerator * (length - 1) + denominator // 2) // denominator


def _round_half_away_from_zero(value: float) -> int:
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _clean(value: float, *, digits: int = 12) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0.0 else rounded

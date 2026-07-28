"""Bounded offline tempo analysis for sealed Reference Studio media.

This module is deliberately smaller than a general music-information-retrieval
system.  It estimates one constant pulse from a collected project-media item:

* callers supply a :class:`~core.song_media_catalog.SongMediaCatalog`, never a
  caller-chosen path;
* :class:`~core.project_audio.ProjectAudioDecoder` performs all decoding and
  deterministic resampling to WebJam's 48 kHz analysis rate;
* at most four deterministic 30-second windows are decoded, and only a
  200-Hz onset envelope is retained;
* the pulse estimate uses NumPy and Python only (no network service, external
  executable, or additional codec/music-analysis dependency);
* cancellation is checked throughout decoding and lag search, and the caller
  still uses :class:`~core.studio_tempo.TempoAnalysisGuard.accept` as the
  final latest-generation publication gate.

The estimator does not claim to infer meter, downbeat, tempo changes, or beat
phase.  It carries forward the project's current meter (4/4 for a default new
project) rather than silently replacing it.  The returned report therefore
always asks the musician to review the result and provides an explicit manual
correction operation before beat-grid edits are trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import uuid

import numpy as np

from core.project_audio import (
    PROJECT_AUDIO_MAX_DECODE_FRAMES,
    PROJECT_AUDIO_SAMPLE_RATE,
    ProjectAudioDecoder,
    ProjectAudioError,
)
from core.song_media_catalog import (
    SongMediaCatalog,
    SongMediaCatalogError,
)
from core.studio_tempo import (
    CONFIDENCE_UNITS,
    TempoAnalysisCancelled,
    TempoAnalysisResult,
    TempoAnalysisToken,
    TempoMap,
    bpm_to_micros,
)


ANALYSIS_ENVELOPE_RATE = 200
ANALYSIS_HOP_FRAMES = PROJECT_AUDIO_SAMPLE_RATE // ANALYSIS_ENVELOPE_RATE
ANALYSIS_WINDOW_SECONDS = 30
MAX_ANALYSIS_WINDOWS = 4
MAX_ANALYSIS_SECONDS = ANALYSIS_WINDOW_SECONDS * MAX_ANALYSIS_WINDOWS
MAX_ANALYSIS_FRAMES = PROJECT_AUDIO_SAMPLE_RATE * MAX_ANALYSIS_SECONDS
MIN_ANALYSIS_SECONDS = 8
MIN_ANALYSIS_FRAMES = PROJECT_AUDIO_SAMPLE_RATE * MIN_ANALYSIS_SECONDS
DEFAULT_MIN_BPM = 40
DEFAULT_MAX_BPM = 220
MANUAL_REVIEW_CONFIDENCE = 600_000

_DECODE_BLOCK_FRAMES = (
    PROJECT_AUDIO_MAX_DECODE_FRAMES // ANALYSIS_HOP_FRAMES
) * ANALYSIS_HOP_FRAMES
_WINDOW_BINS = ANALYSIS_WINDOW_SECONDS * ANALYSIS_ENVELOPE_RATE
_MAX_ANALYSIS_BINS = MAX_ANALYSIS_SECONDS * ANALYSIS_ENVELOPE_RATE
_QUANTIZED_ONSET_PEAK = 1_000_000
_ANALYSIS_NAMESPACE = uuid.UUID("1cc29ec2-46fe-4f31-98e0-c1b1db5f0519")

if (
    PROJECT_AUDIO_SAMPLE_RATE % ANALYSIS_ENVELOPE_RATE
    or _DECODE_BLOCK_FRAMES <= 0
):
    raise RuntimeError("Tempo-analysis constants do not align to the project rate.")


class ProjectTempoAnalysisError(RuntimeError):
    """A path-free analysis failure with a safe manual-tempo fallback."""

    @property
    def manual_correction_available(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class ProjectTempoAnalysis:
    """One reviewable constant-tempo estimate and its project-rate map."""

    result: TempoAnalysisResult
    tempo_map: TempoMap
    analyzed_frames: int
    available_frames: int
    window_count: int
    used_representative_windows: bool
    manual_correction_recommended: bool
    manual_correction_applied: bool = False
    meter_was_inferred: bool = False

    @property
    def review_message(self) -> str:
        if self.manual_correction_applied:
            return "Manual tempo and time-signature correction applied."
        if self.manual_correction_recommended:
            return (
                "Tempo confidence is low. Enter the BPM and time signature "
                "manually before using beat-grid edits."
            )
        return (
            "Review the detected BPM and enter the time signature if needed "
            "before using beat-grid edits."
        )

    def with_manual_correction(
        self,
        *,
        bpm: object,
        numerator: int,
        denominator: int,
    ) -> "ProjectTempoAnalysis":
        """Return a corrected result and matching map without mutating analysis."""

        corrected = self.result.with_manual_override(
            bpm=bpm,
            numerator=numerator,
            denominator=denominator,
        )
        return replace(
            self,
            result=corrected,
            tempo_map=corrected.to_tempo_map(self.tempo_map.sample_rate),
            manual_correction_recommended=False,
            manual_correction_applied=True,
        )


def _validated_search_range(
    minimum_bpm: object,
    maximum_bpm: object,
) -> tuple[int, int]:
    try:
        minimum_micros = bpm_to_micros(minimum_bpm)
        maximum_micros = bpm_to_micros(maximum_bpm)
    except (TypeError, ValueError):
        raise ProjectTempoAnalysisError(
            "Tempo search limits must be valid BPM values from 20 to 400."
        ) from None
    if minimum_micros >= maximum_micros:
        raise ProjectTempoAnalysisError(
            "The minimum tempo search limit must be lower than the maximum."
        )
    # A range wider than this makes octave choices too ambiguous for this
    # deliberately small constant-pulse estimator.
    if maximum_micros > minimum_micros * 8:
        raise ProjectTempoAnalysisError(
            "Use a tempo search range no wider than three octaves."
        )
    return minimum_micros, maximum_micros


def _analysis_windows(output_frames: int) -> tuple[tuple[int, int], ...]:
    """Return hop-aligned windows bounded independently of source duration."""

    usable_bins = int(output_frames) // ANALYSIS_HOP_FRAMES
    if usable_bins * ANALYSIS_HOP_FRAMES < MIN_ANALYSIS_FRAMES:
        raise ProjectTempoAnalysisError(
            "That recording is too short for reliable tempo analysis. Enter "
            "the BPM and time signature manually."
        )
    if usable_bins <= _MAX_ANALYSIS_BINS:
        return ((0, usable_bins),)

    last_start = usable_bins - _WINDOW_BINS
    starts = tuple(
        (last_start * index) // (MAX_ANALYSIS_WINDOWS - 1)
        for index in range(MAX_ANALYSIS_WINDOWS)
    )
    return tuple((start, _WINDOW_BINS) for start in starts)


def _decode_energy_window(
    decoder: ProjectAudioDecoder,
    *,
    start_bin: int,
    bin_count: int,
    token: TempoAnalysisToken,
) -> np.ndarray:
    """Stream one window into a fixed-rate RMS envelope."""

    energies = np.empty(bin_count, dtype=np.float64)
    block = np.empty((_DECODE_BLOCK_FRAMES, 2), dtype=np.float32)
    cursor = start_bin * ANALYSIS_HOP_FRAMES
    remaining = bin_count * ANALYSIS_HOP_FRAMES
    energy_cursor = 0
    while remaining:
        token.raise_if_cancelled()
        requested = min(remaining, _DECODE_BLOCK_FRAMES)
        decoded = decoder.read_into(cursor, block[:requested])
        if decoded != requested:
            raise ProjectTempoAnalysisError(
                "The collected audio ended before its verified duration. "
                "Relink or re-import it, or enter the tempo manually."
            )
        shaped = block[:requested].reshape(
            requested // ANALYSIS_HOP_FRAMES,
            ANALYSIS_HOP_FRAMES,
            2,
        )
        # einsum returns one bounded value per analysis hop and avoids a
        # block-sized square temporary.  Stereo power prevents anti-phase
        # material from disappearing during feature extraction.
        power = np.einsum(
            "ijk,ijk->i",
            shaped,
            shaped,
            dtype=np.float64,
            optimize=False,
        )
        count = int(power.size)
        np.sqrt(
            power / float(ANALYSIS_HOP_FRAMES * 2),
            out=energies[energy_cursor : energy_cursor + count],
        )
        energy_cursor += count
        cursor += requested
        remaining -= requested
    token.raise_if_cancelled()
    return energies


def _onset_feature(energies: np.ndarray) -> tuple[np.ndarray, int, float]:
    """Return an exact integer novelty vector, onset count, and peak RMS."""

    peak_rms = float(np.max(energies, initial=0.0))
    if not math.isfinite(peak_rms) or peak_rms < 1e-5:
        raise ProjectTempoAnalysisError(
            "No usable audio pulse was found. Enter the BPM and time "
            "signature manually."
        )

    # Log compression keeps a few loud hits from erasing quieter beats.
    levels = np.log1p(energies * 1_000.0)
    novelty = np.empty_like(levels)
    novelty[0] = 0.0
    np.subtract(levels[1:], levels[:-1], out=novelty[1:])
    np.maximum(novelty, 0.0, out=novelty)
    baseline = float(np.median(novelty))
    peak = float(np.max(novelty, initial=0.0))
    dynamic = peak - baseline
    if not math.isfinite(dynamic) or dynamic <= 1e-7:
        raise ProjectTempoAnalysisError(
            "No changing pulse was found in the audio. Enter the BPM and time "
            "signature manually."
        )
    novelty -= baseline + dynamic * 0.05
    np.maximum(novelty, 0.0, out=novelty)
    retained_peak = float(np.max(novelty, initial=0.0))
    if retained_peak <= 0.0:
        raise ProjectTempoAnalysisError(
            "No usable beat onsets were found. Enter the BPM and time "
            "signature manually."
        )

    threshold = retained_peak * 0.25
    if novelty.size < 3:
        onset_count = 0
    else:
        onset_count = int(
            np.count_nonzero(
                (novelty[1:-1] >= threshold)
                & (novelty[1:-1] >= novelty[:-2])
                & (novelty[1:-1] > novelty[2:])
            )
        )
    if onset_count < 4:
        raise ProjectTempoAnalysisError(
            "Too few clear beat onsets were found. Enter the BPM and time "
            "signature manually."
        )

    novelty *= _QUANTIZED_ONSET_PEAK / retained_peak
    quantized = np.rint(novelty).astype(np.int64)
    # Center each independently sampled window so joins and overall density
    # cannot become false periodic evidence.
    quantized -= int(round(float(np.mean(quantized))))
    return quantized, onset_count, peak_rms


def _correlations(
    features: tuple[np.ndarray, ...],
    *,
    maximum_lag: int,
    token: TempoAnalysisToken,
) -> np.ndarray:
    correlations = np.zeros(maximum_lag + 1, dtype=np.float64)
    for lag in range(1, maximum_lag + 1):
        if lag % 8 == 0:
            token.raise_if_cancelled()
        numerator = 0
        left_energy = 0
        right_energy = 0
        for feature in features:
            if feature.size <= lag:
                continue
            left = feature[:-lag]
            right = feature[lag:]
            numerator += int(np.dot(left, right))
            left_energy += int(np.dot(left, left))
            right_energy += int(np.dot(right, right))
        denominator = math.sqrt(float(left_energy) * float(right_energy))
        if denominator > 0.0:
            correlations[lag] = max(0.0, float(numerator) / denominator)
    token.raise_if_cancelled()
    return correlations


def _tempo_from_features(
    features: tuple[np.ndarray, ...],
    *,
    onset_count: int,
    minimum_bpm_micros: int,
    maximum_bpm_micros: int,
    token: TempoAnalysisToken,
) -> tuple[int, int]:
    envelope_rate_micros = (
        60 * ANALYSIS_ENVELOPE_RATE * CONFIDENCE_UNITS
    )
    minimum_lag = max(
        1,
        math.ceil(envelope_rate_micros / maximum_bpm_micros),
    )
    maximum_lag = max(
        minimum_lag + 2,
        envelope_rate_micros // minimum_bpm_micros,
    )
    harmonic_maximum = min(
        max(feature.size - 1 for feature in features),
        maximum_lag * 3,
    )
    if harmonic_maximum < maximum_lag:
        raise ProjectTempoAnalysisError(
            "That recording is too short for the requested tempo range. "
            "Narrow the range or enter the tempo manually."
        )
    correlations = _correlations(
        features,
        maximum_lag=harmonic_maximum,
        token=token,
    )

    def correlation_peak_near(lag: int, radius: int = 2) -> float:
        start = max(1, lag - radius)
        stop = min(harmonic_maximum + 1, lag + radius + 1)
        return float(np.max(correlations[start:stop], initial=0.0))

    intervals: list[np.ndarray] = []
    for feature in features:
        peak = int(np.max(feature, initial=0))
        if peak <= 0 or feature.size < 3:
            continue
        threshold = max(1, peak // 4)
        locations = np.flatnonzero(
            (feature[1:-1] >= threshold)
            & (feature[1:-1] >= feature[:-2])
            & (feature[1:-1] > feature[2:])
        )
        if locations.size >= 2:
            # Offset by one is irrelevant after differencing.
            intervals.append(np.diff(locations))
    interval_total = sum(int(values.size) for values in intervals)

    def direct_interval_evidence(lag: int) -> float:
        if interval_total == 0:
            return 0.0
        matches = sum(
            int(np.count_nonzero(np.abs(values - lag) <= 2))
            for values in intervals
        )
        return matches / interval_total

    candidate_lags = np.arange(minimum_lag, maximum_lag + 1, dtype=np.int64)
    scores = np.empty(candidate_lags.size, dtype=np.float64)
    weights = np.empty(candidate_lags.size, dtype=np.float64)
    for index, raw_lag in enumerate(candidate_lags):
        lag = int(raw_lag)
        score = correlation_peak_near(lag, 1)
        weight = 1.0
        if lag * 2 <= harmonic_maximum:
            score += 0.5 * correlation_peak_near(lag * 2)
            weight += 0.5
        if lag * 3 <= harmonic_maximum:
            score += 0.25 * correlation_peak_near(lag * 3)
            weight += 0.25
        score += 0.75 * direct_interval_evidence(lag)
        weight += 0.75
        candidate_bpm = 60.0 * ANALYSIS_ENVELOPE_RATE / lag
        # A mild perceptual-tempo prior resolves half/double ambiguity without
        # preventing genuinely slow pulses: a real slow pulse still has strong
        # base-lag evidence, whereas its false half-lag has only harmonic
        # evidence.
        if candidate_bpm < 70.0 or candidate_bpm > 180.0:
            score *= 0.85
        scores[index] = score
        weights[index] = weight
    token.raise_if_cancelled()

    best_index = int(np.argmax(scores))
    best_score = float(scores[best_index])
    if not math.isfinite(best_score) or best_score <= 0.02:
        raise ProjectTempoAnalysisError(
            "No stable tempo pulse was found. Enter the BPM and time "
            "signature manually."
        )
    best_lag = float(candidate_lags[best_index])
    if 0 < best_index < scores.size - 1:
        left = float(scores[best_index - 1])
        center = best_score
        right = float(scores[best_index + 1])
        curvature = left - 2.0 * center + right
        if curvature < -1e-12:
            offset = 0.5 * (left - right) / curvature
            best_lag += max(-0.5, min(0.5, offset))

    bpm_micros = int(
        round(envelope_rate_micros / best_lag)
    )
    bpm_micros = max(
        minimum_bpm_micros,
        min(maximum_bpm_micros, bpm_micros),
    )

    peak_strength = min(
        1.0,
        best_score / float(weights[best_index]),
    )
    exclusion_start = max(0, best_index - 2)
    exclusion_end = min(scores.size, best_index + 3)
    competitors = np.concatenate(
        (scores[:exclusion_start], scores[exclusion_end:])
    )
    second = float(np.max(competitors, initial=0.0))
    separation = max(0.0, min(1.0, (best_score - second) / best_score))
    floor = float(np.median(scores))
    contrast = max(0.0, min(1.0, (best_score - floor) / best_score))
    coverage = min(1.0, onset_count / 16.0)
    confidence = (
        peak_strength
        * (0.55 + 0.45 * contrast)
        * (0.65 + 0.35 * separation)
        * coverage
    )
    confidence_millionths = int(
        round(max(0.0, min(1.0, confidence)) * CONFIDENCE_UNITS)
    )
    return bpm_micros, confidence_millionths


def analyze_project_tempo(
    catalog: SongMediaCatalog,
    media_id: str,
    token: TempoAnalysisToken,
    *,
    minimum_bpm: object = DEFAULT_MIN_BPM,
    maximum_bpm: object = DEFAULT_MAX_BPM,
) -> ProjectTempoAnalysis:
    """Estimate one constant tempo from verified collected project media.

    The caller should create ``token`` with
    :meth:`core.studio_tempo.TempoAnalysisGuard.begin_generation`, run this
    function on a worker, then publish ``report.result`` only through
    :meth:`core.studio_tempo.TempoAnalysisGuard.accept`.
    """

    if type(catalog) is not SongMediaCatalog:
        raise ProjectTempoAnalysisError(
            "Tempo analysis requires a verified saved-project media catalog."
        )
    if not isinstance(token, TempoAnalysisToken):
        raise ProjectTempoAnalysisError(
            "Tempo analysis requires a current analysis generation."
        )
    minimum_micros, maximum_micros = _validated_search_range(
        minimum_bpm,
        maximum_bpm,
    )
    token.raise_if_cancelled()

    try:
        catalog.assert_current(cancel_check=token.raise_if_cancelled)
        source = catalog.resolve(media_id)
        path = source.path
        with ProjectAudioDecoder(path) as decoder:
            # Close the open/verify race: if catalog media changed while the
            # descriptor was being bound, this identity check rejects it.
            catalog.assert_current(cancel_check=token.raise_if_cancelled)
            windows = _analysis_windows(decoder.output_frames)
            features: list[np.ndarray] = []
            onset_count = 0
            analyzed_frames = 0
            feature_failures = 0
            for start_bin, bin_count in windows:
                token.raise_if_cancelled()
                energies = _decode_energy_window(
                    decoder,
                    start_bin=start_bin,
                    bin_count=bin_count,
                    token=token,
                )
                analyzed_frames += bin_count * ANALYSIS_HOP_FRAMES
                try:
                    feature, window_onsets, _peak_rms = _onset_feature(energies)
                except ProjectTempoAnalysisError:
                    feature_failures += 1
                    continue
                features.append(feature)
                onset_count += window_onsets
            if not features:
                if feature_failures:
                    raise ProjectTempoAnalysisError(
                        "No usable pulse was found in the analyzed audio. Enter "
                        "the BPM and time signature manually."
                    )
                raise ProjectTempoAnalysisError(
                    "No audio was available for tempo analysis. Enter the BPM "
                    "and time signature manually."
                )
            bpm_micros, confidence_millionths = _tempo_from_features(
                tuple(features),
                onset_count=onset_count,
                minimum_bpm_micros=minimum_micros,
                maximum_bpm_micros=maximum_micros,
                token=token,
            )
            catalog.assert_current(cancel_check=token.raise_if_cancelled)
            available_frames = decoder.output_frames
    except TempoAnalysisCancelled:
        raise
    except ProjectTempoAnalysisError:
        raise
    except (ProjectAudioError, SongMediaCatalogError):
        raise ProjectTempoAnalysisError(
            "WebJam couldn't safely analyze the collected audio. Relink or "
            "re-import it, or enter the tempo manually."
        ) from None
    except (MemoryError, OverflowError):
        raise ProjectTempoAnalysisError(
            "Tempo analysis exceeded a safe resource limit. Enter the BPM and "
            "time signature manually."
        ) from None
    except Exception:
        # The UI must never expose a decoder/library exception containing a
        # private bundle path. Unexpected failures remain actionable and safe.
        raise ProjectTempoAnalysisError(
            "Tempo analysis couldn't finish safely. Enter the BPM and time "
            "signature manually."
        ) from None

    token.raise_if_cancelled()
    analysis_name = ":".join(
        (
            catalog.project_id,
            source.media.media_id,
            source.media.sha256,
            str(token.generation),
            str(bpm_micros),
            str(confidence_millionths),
        )
    )
    result = TempoAnalysisResult(
        analysis_id=str(uuid.uuid5(_ANALYSIS_NAMESPACE, analysis_name)),
        generation=token.generation,
        detected_bpm_micros=bpm_micros,
        confidence_millionths=confidence_millionths,
        # Meter inference is explicitly outside this estimator's contract.
        # Preserve the musician's current project setting rather than
        # resetting a 3/4, 6/8, or other project to a misleading default.
        detected_numerator=catalog.project.time_signature.numerator,
        detected_denominator=catalog.project.time_signature.denominator,
    )
    project_rate = catalog.project.project_sample_rate
    return ProjectTempoAnalysis(
        result=result,
        tempo_map=result.to_tempo_map(project_rate),
        analyzed_frames=analyzed_frames,
        available_frames=available_frames,
        window_count=len(windows),
        used_representative_windows=len(windows) > 1,
        manual_correction_recommended=(
            confidence_millionths < MANUAL_REVIEW_CONFIDENCE
        ),
    )


__all__ = [
    "ANALYSIS_ENVELOPE_RATE",
    "ANALYSIS_HOP_FRAMES",
    "ANALYSIS_WINDOW_SECONDS",
    "DEFAULT_MAX_BPM",
    "DEFAULT_MIN_BPM",
    "MANUAL_REVIEW_CONFIDENCE",
    "MAX_ANALYSIS_FRAMES",
    "MAX_ANALYSIS_SECONDS",
    "MAX_ANALYSIS_WINDOWS",
    "MIN_ANALYSIS_FRAMES",
    "MIN_ANALYSIS_SECONDS",
    "ProjectTempoAnalysis",
    "ProjectTempoAnalysisError",
    "analyze_project_tempo",
]

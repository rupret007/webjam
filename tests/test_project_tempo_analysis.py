"""Offline tempo-analysis contracts for sealed Reference Studio media."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.project_tempo_analysis as tempo_analysis
from core.project_tempo_analysis import (
    MAX_ANALYSIS_FRAMES,
    MAX_ANALYSIS_WINDOWS,
    ProjectTempoAnalysisError,
    analyze_project_tempo,
)
from core.song_media_catalog import SongMediaCatalog
from core.song_project import MediaProvenance, TimeSignature
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.studio_tempo import (
    TempoAnalysisCancelled,
    TempoAnalysisGuard,
)


def _click_track(
    sample_rate: int,
    bpm: float,
    *,
    seconds: int = 10,
) -> np.ndarray:
    samples = np.zeros(sample_rate * seconds, dtype=np.float32)
    period = sample_rate * 60.0 / bpm
    pulse_frames = max(8, sample_rate // 100)
    pulse = np.linspace(1.0, 0.0, pulse_frames, dtype=np.float32)
    for beat in range(int(seconds * bpm / 60.0) + 1):
        start = int(round(beat * period))
        usable = min(pulse_frames, samples.size - start)
        if usable > 0:
            samples[start : start + usable] += pulse[:usable]
    return samples


def _catalog(
    tmp_path: Path,
    samples: np.ndarray,
    *,
    source_rate: int = 48_000,
    project_rate: int = 48_000,
    time_signature: TimeSignature | None = None,
    name: str = "private rhythm source.wav",
) -> tuple[Path, SongMediaCatalog, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / name
    sf.write(source, samples, source_rate, subtype="FLOAT")
    bundle = tmp_path / "Private Song Session.webjam"
    created = create_project_bundle(
        bundle,
        "Tempo analysis",
        project_sample_rate=project_rate,
        time_signature=time_signature,
    )
    imported = import_project_media(
        bundle,
        created.project,
        source,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    return (
        bundle,
        SongMediaCatalog.load(saved.project, bundle),
        imported.media.media_id,
    )


def _analyze(
    catalog: SongMediaCatalog,
    media_id: str,
    **search_range: object,
):
    guard = TempoAnalysisGuard()
    token = guard.begin_generation()
    report = analyze_project_tempo(
        catalog,
        media_id,
        token,
        **search_range,
    )
    assert guard.accept(token, report.result) is report.result
    return report


@pytest.mark.parametrize(
    ("source_rate", "bpm"),
    [
        (44_100, 90.0),
        (48_000, 120.0),
        (96_000, 137.0),
    ],
)
def test_detects_constant_click_tempo_after_bounded_rate_conversion(
    tmp_path: Path,
    source_rate: int,
    bpm: float,
) -> None:
    bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(source_rate, bpm),
        source_rate=source_rate,
    )

    report = _analyze(catalog, media_id)

    detected = report.result.detected_bpm_micros / 1_000_000
    assert detected == pytest.approx(bpm, abs=0.75)
    assert 0 <= report.result.confidence_millionths <= 1_000_000
    assert report.result.confidence_millionths >= 450_000
    assert report.result.detected_numerator == 4
    assert report.result.detected_denominator == 4
    assert report.meter_was_inferred is False
    assert report.tempo_map.sample_rate == 48_000
    assert (
        report.tempo_map.tempo_points[0].bpm_micros
        == report.result.detected_bpm_micros
    )
    assert report.analyzed_frames <= MAX_ANALYSIS_FRAMES
    assert report.available_frames >= report.analyzed_frames
    assert str(bundle) not in report.review_message


def test_analysis_is_deterministic_and_uses_project_rate_for_map(
    tmp_path: Path,
) -> None:
    _bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(96_000, 123.0),
        source_rate=96_000,
        project_rate=44_100,
        time_signature=TimeSignature(6, 8),
    )
    guard = TempoAnalysisGuard()
    token = guard.begin_generation()

    first = analyze_project_tempo(catalog, media_id, token)
    second = analyze_project_tempo(catalog, media_id, token)

    assert first == second
    assert first.result.analysis_id == second.result.analysis_id
    assert first.tempo_map.sample_rate == 44_100
    assert first.meter_was_inferred is False
    assert first.result.effective_time_signature == (6, 8)
    signature = first.tempo_map.time_signature_points[0]
    assert (signature.numerator, signature.denominator) == (6, 8)
    assert guard.accept(token, first.result) == first.result


def test_stereo_antiphase_audio_does_not_cancel_the_pulse(
    tmp_path: Path,
) -> None:
    mono = _click_track(48_000, 100.0)
    antiphase = np.column_stack((mono, -mono)).astype(np.float32)
    _bundle, catalog, media_id = _catalog(tmp_path, antiphase)

    report = _analyze(catalog, media_id)

    assert report.result.detected_bpm_micros / 1_000_000 == pytest.approx(
        100.0,
        abs=0.75,
    )


def test_manual_correction_returns_matching_result_and_tempo_map(
    tmp_path: Path,
) -> None:
    _bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(48_000, 120.0),
    )
    detected = _analyze(catalog, media_id)

    corrected = detected.with_manual_correction(
        bpm="117.25",
        numerator=7,
        denominator=8,
    )

    assert detected.result.has_manual_override is False
    assert corrected.result.has_manual_override is True
    assert corrected.result.effective_bpm_micros == 117_250_000
    assert corrected.result.effective_time_signature == (7, 8)
    assert corrected.tempo_map.tempo_points[0].bpm_micros == 117_250_000
    signature = corrected.tempo_map.time_signature_points[0]
    assert (signature.numerator, signature.denominator) == (7, 8)
    assert corrected.manual_correction_applied is True
    assert corrected.manual_correction_recommended is False
    assert "applied" in corrected.review_message.lower()


def test_silent_steady_and_short_audio_have_path_free_manual_fallback(
    tmp_path: Path,
) -> None:
    cases = (
        ("silent private.wav", np.zeros(48_000 * 9, dtype=np.float32)),
        (
            "steady private.wav",
            np.full(48_000 * 9, 0.2, dtype=np.float32),
        ),
        (
            "short private.wav",
            _click_track(48_000, 120.0, seconds=3),
        ),
    )
    for index, (name, samples) in enumerate(cases):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        bundle, catalog, media_id = _catalog(
            case_root,
            samples,
            name=name,
        )
        guard = TempoAnalysisGuard()
        token = guard.begin_generation()

        with pytest.raises(ProjectTempoAnalysisError) as caught:
            analyze_project_tempo(catalog, media_id, token)

        message = str(caught.value)
        assert "manually" in message
        assert caught.value.manual_correction_available is True
        assert str(bundle) not in message
        assert str(tmp_path) not in message
        assert name not in message
        assert caught.value.__cause__ is None


def test_catalog_replacement_and_decoder_failures_never_disclose_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(48_000, 120.0),
    )
    member = catalog.resolve(media_id).path
    replacement = tmp_path / "replacement.wav"
    sf.write(
        replacement,
        _click_track(48_000, 100.0),
        48_000,
        subtype="FLOAT",
    )
    os.replace(replacement, member)
    token = TempoAnalysisGuard().begin_generation()

    with pytest.raises(ProjectTempoAnalysisError) as caught:
        analyze_project_tempo(catalog, media_id, token)

    assert str(bundle) not in str(caught.value)
    assert member.name not in str(caught.value)
    assert caught.value.__cause__ is None

    # A third-party decoder failure may itself contain a path. The public
    # boundary must still produce one path-free error without exception
    # chaining.
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    bundle, catalog, media_id = _catalog(
        clean_root,
        _click_track(48_000, 120.0),
    )

    class ExplodingDecoder:
        def __init__(self, path: Path) -> None:
            raise RuntimeError(f"decoder failed at {path}")

    monkeypatch.setattr(tempo_analysis, "ProjectAudioDecoder", ExplodingDecoder)
    with pytest.raises(ProjectTempoAnalysisError) as caught:
        analyze_project_tempo(
            catalog,
            media_id,
            TempoAnalysisGuard().begin_generation(),
        )
    assert str(bundle) not in str(caught.value)
    assert ".wav" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_stale_and_inflight_cancellation_never_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(48_000, 120.0),
    )
    guard = TempoAnalysisGuard()
    stale = guard.begin_generation()
    current = guard.begin_generation()
    with pytest.raises(TempoAnalysisCancelled):
        analyze_project_tempo(catalog, media_id, stale)

    real_decoder = tempo_analysis.ProjectAudioDecoder

    class CancellingDecoder(real_decoder):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self._did_cancel = False

        def read_into(self, start_frame, output, *, token=None):
            result = super().read_into(start_frame, output, token=token)
            if not self._did_cancel:
                self._did_cancel = True
                guard.begin_generation()
            return result

    monkeypatch.setattr(
        tempo_analysis,
        "ProjectAudioDecoder",
        CancellingDecoder,
    )
    with pytest.raises(TempoAnalysisCancelled):
        analyze_project_tempo(catalog, media_id, current)
    assert current.cancelled is True


def test_huge_source_uses_spread_representative_windows_and_fixed_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, catalog, media_id = _catalog(
        tmp_path,
        _click_track(48_000, 120.0),
    )

    class HugeDecoder:
        instances: list["HugeDecoder"] = []

        def __init__(self, _path: Path) -> None:
            self.output_frames = 48_000 * 60 * 60 * 24
            self.total_requested = 0
            self.starts: list[int] = []
            self.maximum_request = 0
            self.__class__.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            return None

        def read_into(self, start_frame: int, output: np.ndarray) -> int:
            requested = int(output.shape[0])
            self.total_requested += requested
            self.maximum_request = max(self.maximum_request, requested)
            self.starts.append(start_frame)
            positions = np.arange(
                start_frame,
                start_frame + requested,
                dtype=np.int64,
            )
            phase = positions % 24_000
            pulse = np.where(
                phase < 480,
                1.0 - phase.astype(np.float32) / 480.0,
                0.0,
            )
            output[:, 0] = pulse
            output[:, 1] = pulse
            return requested

    monkeypatch.setattr(tempo_analysis, "ProjectAudioDecoder", HugeDecoder)

    report = _analyze(catalog, media_id)
    decoder = HugeDecoder.instances[-1]

    assert report.used_representative_windows is True
    assert report.window_count == MAX_ANALYSIS_WINDOWS
    assert report.analyzed_frames == MAX_ANALYSIS_FRAMES
    assert decoder.total_requested == MAX_ANALYSIS_FRAMES
    assert decoder.maximum_request <= 4_096
    assert min(decoder.starts) == 0
    assert max(decoder.starts) > decoder.output_frames // 2
    assert report.result.detected_bpm_micros == pytest.approx(
        120_000_000,
        abs=500_000,
    )


def test_search_range_and_catalog_contract_are_strict_and_path_free(
    tmp_path: Path,
) -> None:
    source = tmp_path / "never disclose this.wav"
    sf.write(source, _click_track(48_000, 120.0), 48_000, subtype="FLOAT")
    token = TempoAnalysisGuard().begin_generation()
    with pytest.raises(ProjectTempoAnalysisError, match="verified") as caught:
        analyze_project_tempo(source, "media", token)  # type: ignore[arg-type]
    assert str(source) not in str(caught.value)

    _bundle, catalog, media_id = _catalog(
        tmp_path / "valid",
        _click_track(48_000, 120.0),
    )
    for limits in (
        {"minimum_bpm": 200, "maximum_bpm": 100},
        {"minimum_bpm": 20, "maximum_bpm": 400},
        {"minimum_bpm": "not-a-number", "maximum_bpm": 200},
    ):
        with pytest.raises(ProjectTempoAnalysisError):
            analyze_project_tempo(
                catalog,
                media_id,
                TempoAnalysisGuard().begin_generation(),
                **limits,
            )


def test_requested_range_can_resolve_slow_and_fast_pulses(
    tmp_path: Path,
) -> None:
    for index, bpm in enumerate((45.0, 200.0)):
        root = tmp_path / str(index)
        root.mkdir()
        _bundle, catalog, media_id = _catalog(
            root,
            _click_track(48_000, bpm, seconds=12),
        )
        report = _analyze(
            catalog,
            media_id,
            minimum_bpm=40,
            maximum_bpm=220,
        )
        assert report.result.detected_bpm_micros / 1_000_000 == pytest.approx(
            bpm,
            abs=0.8,
        )
        if bpm == 45.0:
            # Slow pulse estimates remain usable but honestly request review.
            assert report.manual_correction_recommended is True

from __future__ import annotations

import json
import uuid
from fractions import Fraction

import pytest

from core.studio_tempo import (
    CONFIDENCE_UNITS,
    MICRO_BPM_PER_BPM,
    TICKS_PER_QUARTER,
    FrameRounding,
    MusicalSnapMode,
    SnapTiePolicy,
    StudioTempoError,
    TempoAnalysisCancelled,
    TempoAnalysisGuard,
    TempoAnalysisResult,
    TempoMap,
    TempoPoint,
    TimeSignaturePoint,
    bpm_to_micros,
    load_tempo_map,
)


def _id(number: int) -> str:
    return str(uuid.UUID(int=number))


def _tempo(frame: int, bpm: object, number: int) -> TempoPoint:
    return TempoPoint.from_bpm(frame, bpm, point_id=_id(number))


def _signature(
    frame: int,
    numerator: int,
    denominator: int,
    number: int,
) -> TimeSignaturePoint:
    return TimeSignaturePoint(_id(number), frame, numerator, denominator)


def test_default_map_is_exact_120_bpm_4_4_and_strict_json_round_trips() -> None:
    timing = TempoMap.default(48_000)

    assert timing.frame_to_beat(0) == 0
    assert timing.frame_to_beat(48_000) == 2
    assert timing.frame_to_tick(48_000) == 2 * TICKS_PER_QUARTER
    assert timing.frame_to_bar_position(0).bar_number == 1
    assert timing.frame_to_bar_position(96_000).bar_number == 2
    assert timing.bar_position_to_frame(2) == 96_000
    assert timing.frame_to_bar_fraction(48_000) == Fraction(3, 2)

    payload = json.loads(json.dumps(timing.to_dict()))
    assert TempoMap.from_dict(payload) == timing
    assert load_tempo_map(payload, sample_rate=48_000) == timing

    payload["surprise"] = True
    with pytest.raises(StudioTempoError, match="unsupported fields"):
        TempoMap.from_dict(payload)


def test_legacy_and_missing_timing_migrate_without_float_drift() -> None:
    default = load_tempo_map(None, sample_rate=44_100)
    assert default.sample_rate == 44_100
    assert default.tempo_points[0].bpm_micros == 120 * MICRO_BPM_PER_BPM
    assert default.time_signature_points[0].numerator == 4

    legacy = load_tempo_map(
        {
            "tempo_bpm": 123.456789,
            "time_signature_numerator": 7,
            "time_signature_denominator": 8,
        },
        sample_rate=48_000,
    )
    assert legacy.tempo_points[0].bpm_micros == 123_456_789
    assert legacy.time_signature_points[0].bar_ticks == 3_360

    assert load_tempo_map(
        {
            "schema_version": 0,
            "sample_rate": 48_000,
            "bpm": "99.125",
        },
        sample_rate=48_000,
    ).tempo_points[0].bpm == Fraction(793, 8)

    with pytest.raises(StudioTempoError, match="six decimal"):
        bpm_to_micros("120.1234567")
    with pytest.raises(StudioTempoError, match="too precise or too large"):
        bpm_to_micros("1e999999999")
    with pytest.raises(StudioTempoError, match="tempo_bpm or bpm"):
        load_tempo_map(
            {"tempo_bpm": 120, "bpm": 120},
            sample_rate=48_000,
        )
    with pytest.raises(StudioTempoError, match="sample rate does not match"):
        load_tempo_map(
            {"sample_rate": 44_100, "tempo_bpm": 120},
            sample_rate=48_000,
        )


def test_constant_tempo_segments_round_trip_long_timelines_within_one_frame() -> None:
    rate = 48_000
    timing = TempoMap(
        sample_rate=rate,
        tempo_points=(
            _tempo(0, 120, 1),
            _tempo(rate * 60 * 10, "87.125", 2),
            _tempo(rate * 60 * 75, "203.333333", 3),
        ),
        time_signature_points=(_signature(0, 4, 4, 10),),
    )

    frames = (
        0,
        1,
        rate - 1,
        rate,
        rate * 60 * 10 - 1,
        rate * 60 * 10,
        rate * 60 * 60 * 12 + 12_345,
    )
    for frame in frames:
        beat = timing.frame_to_beat(frame)
        tick = timing.frame_to_tick(frame)
        assert timing.beat_to_frame(beat) == frame
        assert timing.tick_to_frame(tick) == frame

    boundary_beat = timing.frame_to_beat(rate * 60 * 10)
    assert timing.beat_to_frame(boundary_beat) == rate * 60 * 10
    assert timing.frame_to_beat(rate * 60 * 10 + 1) > boundary_beat


@pytest.mark.parametrize("sample_rate", [8_000, 44_100, 48_000, 96_000, 192_000])
def test_mixed_project_sample_rates_share_the_same_exact_musical_time(
    sample_rate: int,
) -> None:
    timing = TempoMap.default(sample_rate)
    frame = sample_rate * 75 // 2  # 37.5 seconds
    assert timing.frame_to_beat(frame) == 75
    assert timing.frame_to_tick(frame) == 72_000
    assert timing.beat_to_frame(75) == frame


def test_time_signature_changes_preserve_contiguous_one_based_bar_numbers() -> None:
    rate = 48_000
    quarter = 24_000  # 120 BPM
    bar_4_4 = quarter * 4
    change_to_3_4 = bar_4_4 * 2
    change_to_7_8 = change_to_3_4 + quarter * 3 * 2
    timing = TempoMap(
        sample_rate=rate,
        tempo_points=(_tempo(0, 120, 1),),
        time_signature_points=(
            _signature(0, 4, 4, 10),
            _signature(change_to_3_4, 3, 4, 11),
            _signature(change_to_7_8, 7, 8, 12),
        ),
    )

    assert timing.frame_to_bar_position(0).bar_number == 1
    before = timing.frame_to_bar_position(change_to_3_4 - 1)
    assert (before.bar_number, before.numerator) == (2, 4)
    at_three = timing.frame_to_bar_position(change_to_3_4)
    assert (at_three.bar_number, at_three.beat_number) == (3, 1)
    assert (at_three.numerator, at_three.denominator) == (3, 4)
    at_seven = timing.frame_to_bar_position(change_to_7_8)
    assert (at_seven.bar_number, at_seven.beat_number) == (5, 1)
    assert (at_seven.numerator, at_seven.denominator) == (7, 8)

    assert timing.bar_position_to_frame(3, 1) == change_to_3_4
    assert timing.bar_position_to_frame(4, 3) == change_to_3_4 + quarter * 5
    assert timing.bar_position_to_frame(5, 1) == change_to_7_8
    position = timing.frame_to_bar_position(change_to_7_8 + quarter // 2)
    assert (position.bar_number, position.beat_number, position.tick_in_beat) == (
        5,
        2,
        0,
    )
    assert timing.bar_position_to_frame(
        position.bar_number,
        position.beat_number,
        position.tick_in_beat,
    ) == change_to_7_8 + quarter // 2


def test_signature_changes_must_be_exact_ticks_and_prior_bar_boundaries() -> None:
    with pytest.raises(StudioTempoError, match="bar boundary"):
        TempoMap(
            sample_rate=48_000,
            tempo_points=(_tempo(0, 120, 1),),
            time_signature_points=(
                _signature(0, 4, 4, 10),
                _signature(24_000, 3, 4, 11),
            ),
        )

    with pytest.raises(StudioTempoError, match="exact musical tick"):
        TempoMap(
            sample_rate=48_000,
            tempo_points=(_tempo(0, "120.000001", 1),),
            time_signature_points=(
                _signature(0, 4, 4, 10),
                _signature(96_000, 3, 4, 11),
            ),
        )


def test_beat_bar_and_subdivision_snap_have_explicit_tie_behavior() -> None:
    timing = TempoMap.default(48_000)

    # Quarter-note beat boundaries are 24,000 frames apart.
    assert timing.snap_frame(11_999, MusicalSnapMode.BEAT) == 0
    assert timing.snap_frame(
        12_000,
        MusicalSnapMode.BEAT,
        tie_policy=SnapTiePolicy.EARLIER,
    ) == 0
    assert timing.snap_frame(
        12_000,
        MusicalSnapMode.BEAT,
        tie_policy=SnapTiePolicy.LATER,
    ) == 24_000
    assert timing.snap_frame(70_000, MusicalSnapMode.BAR) == 96_000

    # Four divisions of a quarter note produce a 6,000-frame grid.
    assert timing.snap_frame(
        8_900,
        MusicalSnapMode.SUBDIVISION,
        subdivision=4,
    ) == 6_000
    assert timing.snap_frame(
        9_000,
        MusicalSnapMode.SUBDIVISION,
        subdivision=4,
        tie_policy=SnapTiePolicy.LATER,
    ) == 12_000
    assert timing.snap_frame(9_001, MusicalSnapMode.OFF) == 9_001


def test_fractional_ticks_and_explicit_frame_rounding_are_deterministic() -> None:
    timing = TempoMap.default(48_000)
    half_frame_beat = Fraction(1, 48_000)  # 0.5 frame at 120 BPM

    assert timing.beat_to_frame(
        half_frame_beat,
        rounding=FrameRounding.NEAREST_EARLIER,
    ) == 0
    assert timing.beat_to_frame(
        half_frame_beat,
        rounding=FrameRounding.NEAREST_LATER,
    ) == 1
    assert timing.beat_to_frame(half_frame_beat, rounding=FrameRounding.FLOOR) == 0
    assert timing.beat_to_frame(half_frame_beat, rounding=FrameRounding.CEIL) == 1

    tick = timing.bar_position_to_tick(1, 1, Fraction(1, 3))
    assert tick == Fraction(1, 3)
    frame = timing.tick_to_frame(tick)
    assert abs(timing.frame_to_tick(frame) - tick) <= Fraction(
        TICKS_PER_QUARTER,
        24_000,
    )


@pytest.mark.parametrize(
    "factory, match",
    [
        (lambda: TempoPoint("bad", 0, 120_000_000), "UUID"),
        (lambda: TempoPoint(_id(1), -1, 120_000_000), "between"),
        (lambda: TempoPoint(_id(1), 0, 0), "between"),
        (
            lambda: TempoPoint(_id(1), 0, 120_000_000, curve="linear"),
            "constant",
        ),
        (lambda: TimeSignaturePoint(_id(1), 0, 0, 4), "between"),
        (lambda: TimeSignaturePoint(_id(1), 0, 4, 3), "power of two"),
    ],
)
def test_points_reject_invalid_or_nondeterministic_state(factory, match: str) -> None:
    with pytest.raises(StudioTempoError, match=match):
        factory()


def test_map_rejects_corrupt_order_ids_collections_and_schema() -> None:
    with pytest.raises(StudioTempoError, match="frame zero"):
        TempoMap(
            sample_rate=48_000,
            tempo_points=(_tempo(1, 120, 1),),
            time_signature_points=(_signature(0, 4, 4, 2),),
        )
    with pytest.raises(StudioTempoError, match="strictly increasing"):
        TempoMap(
            sample_rate=48_000,
            tempo_points=(_tempo(0, 120, 1), _tempo(0, 100, 2)),
            time_signature_points=(_signature(0, 4, 4, 3),),
        )
    with pytest.raises(StudioTempoError, match="globally unique"):
        TempoMap(
            sample_rate=48_000,
            tempo_points=(_tempo(0, 120, 1),),
            time_signature_points=(_signature(0, 4, 4, 1),),
        )

    payload = TempoMap.default(48_000).to_dict()
    payload["schema_version"] = 99
    with pytest.raises(StudioTempoError, match="unsupported schema"):
        TempoMap.from_dict(payload)
    payload = TempoMap.default(48_000).to_dict()
    payload["tempo_points"] = tuple(payload["tempo_points"])
    with pytest.raises(StudioTempoError, match="must be lists"):
        TempoMap.from_dict(payload)
    with pytest.raises(StudioTempoError, match="integer"):
        TempoMap(
            sample_rate=True,
            tempo_points=(_tempo(0, 120, 1),),
            time_signature_points=(_signature(0, 4, 4, 2),),
        )


def test_analysis_result_manual_override_is_exact_path_free_and_strict() -> None:
    result = TempoAnalysisResult(
        analysis_id=_id(100),
        generation=3,
        detected_bpm_micros=bpm_to_micros("117.25"),
        confidence_millionths=875_000,
        detected_numerator=4,
        detected_denominator=4,
    )
    assert result.confidence == Fraction(7, 8)
    assert result.has_manual_override is False

    manual = result.with_manual_override(
        bpm="118.125",
        numerator=7,
        denominator=8,
    )
    assert manual.has_manual_override is True
    assert manual.effective_bpm_micros == 118_125_000
    assert manual.effective_time_signature == (7, 8)
    timing = manual.to_tempo_map(48_000)
    assert timing.tempo_points[0].bpm == Fraction(945, 8)
    assert timing.time_signature_points[0].numerator == 7
    assert manual.clear_manual_override() == result

    payload = json.loads(json.dumps(manual.to_dict()))
    assert TempoAnalysisResult.from_dict(payload) == manual
    payload["private_path"] = "/secret/song.wav"
    with pytest.raises(StudioTempoError, match="unsupported fields"):
        TempoAnalysisResult.from_dict(payload)

    with pytest.raises(StudioTempoError, match="paired"):
        result.with_manual_override(numerator=3)
    with pytest.raises(StudioTempoError, match="between"):
        TempoAnalysisResult(
            analysis_id=_id(101),
            generation=1,
            detected_bpm_micros=120_000_000,
            confidence_millionths=CONFIDENCE_UNITS + 1,
        )


def test_analysis_guard_cancels_old_generations_and_rejects_stale_results() -> None:
    guard = TempoAnalysisGuard()
    first = guard.begin_generation()
    assert guard.is_current(first)
    second = guard.begin_generation()
    assert first.cancelled is True
    assert guard.is_current(first) is False
    with pytest.raises(TempoAnalysisCancelled):
        first.raise_if_cancelled()

    current = TempoAnalysisResult(
        analysis_id=_id(200),
        generation=second.generation,
        detected_bpm_micros=120_000_000,
        confidence_millionths=500_000,
    )
    assert guard.accept(second, current) is current

    wrong_generation = TempoAnalysisResult(
        analysis_id=_id(201),
        generation=second.generation + 1,
        detected_bpm_micros=120_000_000,
        confidence_millionths=500_000,
    )
    with pytest.raises(TempoAnalysisCancelled, match="does not match"):
        guard.accept(second, wrong_generation)

    guard.cancel_current()
    assert second.cancelled is True
    with pytest.raises(TempoAnalysisCancelled, match="stale"):
        guard.accept(second, current)

    third = guard.begin_generation()
    guard.shutdown()
    assert third.cancelled is True
    with pytest.raises(TempoAnalysisCancelled, match="shut down"):
        guard.begin_generation()

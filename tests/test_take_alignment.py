from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.take_alignment import (
    CERTIFIED_ANCHOR_RMS_TOLERANCE_MS,
    CERTIFIED_DRIFT_TOLERANCE_PPM,
    CERTIFIED_OFFSET_TOLERANCE_S,
    READ_BLOCK_FRAMES,
    AlignmentOutcome,
    TimeTransform,
    align_audio_files,
    align_media_segments,
    analyze_audio_transients,
)
from core.take_project import (
    AlignmentAnchor,
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    new_project_id,
)


def _write_clicks(
    path: Path,
    *,
    rate: int,
    times: list[float],
    duration_s: float,
    channels: int = 1,
    amplitudes: list[float] | None = None,
) -> None:
    audio = np.zeros((int(round(duration_s * rate)), channels), dtype="float32")
    levels = amplitudes or [0.82] * len(times)
    shape = np.asarray((1.0, 0.62, 0.31, 0.12), dtype="float32")
    for index, (time_s, level) in enumerate(zip(times, levels)):
        frame = int(round(time_s * rate))
        if frame < 0 or frame + len(shape) > len(audio):
            continue
        channel = index % channels
        audio[frame : frame + len(shape), channel] = shape * level
        if channels == 2:
            audio[frame : frame + len(shape), 1 - channel] = shape * level * 0.71
    sf.write(path, audio if channels > 1 else audio[:, 0], rate, subtype="PCM_24")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("source_rate", "source_channels", "offset_s"),
    ((48_000, 1, 0.13731), (44_100, 2, -0.18347)),
)
def test_non_grid_signed_offset_repeated_clicks_and_native_rate_mapping(
    tmp_path: Path,
    source_rate: int,
    source_channels: int,
    offset_s: float,
) -> None:
    source_times = [0.72, 1.31, 2.31, 3.31, 4.31, 6.31, 8.31, 11.31, 15.31, 21.17]
    reference_times = [time_s + offset_s for time_s in source_times]
    source = tmp_path / "source.wav"
    reference = tmp_path / "reference.wav"
    _write_clicks(
        source,
        rate=source_rate,
        times=source_times,
        duration_s=22.0,
        channels=source_channels,
    )
    _write_clicks(
        reference,
        rate=48_000,
        times=reference_times,
        duration_s=22.0,
    )
    source_before = _sha256(source)
    reference_before = _sha256(reference)

    result = align_audio_files(reference, source)

    assert result.outcome is AlignmentOutcome.ALIGNED
    assert result.state.automatic_offset_s == pytest.approx(
        offset_s, abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert result.state.manual_nudge_s == 0.0
    assert result.state.residual_ms <= CERTIFIED_ANCHOR_RMS_TOLERANCE_MS
    assert len(result.state.anchors) == len(source_times)
    assert all(item.confidence > 0.8 for item in result.anchor_evidence)
    transform = TimeTransform.from_state(result.state)
    assert transform.source_to_project(source_times[-1]) == pytest.approx(
        reference_times[-1], abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert transform.project_to_source(reference_times[3]) == pytest.approx(
        source_times[3], abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert _sha256(source) == source_before
    assert _sha256(reference) == reference_before


@pytest.mark.parametrize("drift_ppm", (-900.0, 900.0))
def test_known_linear_drift_over_accelerated_long_timeline(
    tmp_path: Path, drift_ppm: float
) -> None:
    source_times = [0.67, *[float(value) for value in range(2, 79, 2)], 79.21]
    offset_s = 0.21137
    scale = 1.0 + drift_ppm / 1_000_000.0
    reference_times = [offset_s + scale * time_s for time_s in source_times]
    source = tmp_path / f"source-{drift_ppm:+.0f}.wav"
    reference = tmp_path / f"reference-{drift_ppm:+.0f}.wav"
    _write_clicks(source, rate=44_100, times=source_times, duration_s=80.0)
    _write_clicks(reference, rate=48_000, times=reference_times, duration_s=80.0)

    result = align_audio_files(reference, source, max_drift_ppm=2_000.0)

    assert result.outcome is AlignmentOutcome.ALIGNED
    assert result.state.automatic_offset_s == pytest.approx(
        offset_s, abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert result.state.drift_ppm == pytest.approx(
        drift_ppm, abs=CERTIFIED_DRIFT_TOLERANCE_PPM
    )
    assert result.state.residual_ms <= CERTIFIED_ANCHOR_RMS_TOLERANCE_MS
    assert len(result.state.anchors) <= 64
    assert result.state.anchors[0].source_time_s < 1.0
    assert result.state.anchors[-1].source_time_s > 79.0


def test_declared_reconnect_gap_is_excluded_from_anchor_matching(tmp_path: Path) -> None:
    source_times = [0.8, 2.5, 5.5, 7.5, 9.5, 12.4]
    offset_s = 0.27123
    source = tmp_path / "source-gap.wav"
    reference = tmp_path / "reference-gap.wav"
    _write_clicks(source, rate=48_000, times=source_times, duration_s=13.0)
    _write_clicks(
        reference,
        rate=48_000,
        times=[item + offset_s for item in source_times],
        duration_s=14.0,
    )
    gap = GapInterval(
        start_frame=5 * 48_000,
        frame_count=2 * 48_000,
        reason="reconnect",
    )

    result = align_audio_files(reference, source, source_gaps=(gap,))

    assert result.outcome is AlignmentOutcome.ALIGNED
    assert result.state.automatic_offset_s == pytest.approx(
        offset_s, abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert result.source_transient_count == len(source_times) - 1
    assert all(
        not 5.0 <= item.anchor.source_time_s < 7.0
        for item in result.anchor_evidence
    )


def test_explicit_segments_keep_their_timeline_and_are_not_stitched(
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    offset_s = -0.11941
    source_one_times = [0.8, 3.2]
    source_two_local_times = [0.7, 2.6, 4.4]
    source_global_times = [*source_one_times, *[6.0 + item for item in source_two_local_times]]
    _write_clicks(
        media / "source-1.wav", rate=48_000, times=source_one_times, duration_s=5.0
    )
    _write_clicks(
        media / "source-2.wav",
        rate=44_100,
        times=source_two_local_times,
        duration_s=5.0,
    )
    _write_clicks(
        media / "reference.wav",
        rate=48_000,
        times=[item + offset_s for item in source_global_times],
        duration_s=11.0,
    )
    reference_segment = MediaSegment(
        segment_id=new_project_id(),
        path="media/reference.wav",
        project_start_frame=0,
        frame_count=11 * 48_000,
        sample_rate=48_000,
        channels=1,
        sample_format="PCM_24",
    )
    source_segments = (
        MediaSegment(
            segment_id=new_project_id(),
            path="media/source-1.wav",
            project_start_frame=0,
            frame_count=5 * 48_000,
            sample_rate=48_000,
            channels=1,
            sample_format="PCM_24",
        ),
        MediaSegment(
            segment_id=new_project_id(),
            path="media/source-2.wav",
            project_start_frame=6 * 48_000,
            frame_count=5 * 44_100,
            sample_rate=44_100,
            channels=1,
            sample_format="PCM_24",
        ),
    )

    result = align_media_segments(
        tmp_path,
        (reference_segment,),
        tmp_path,
        source_segments,
        project_sample_rate=48_000,
    )

    assert result.outcome is AlignmentOutcome.ALIGNED
    assert result.state.automatic_offset_s == pytest.approx(
        offset_s, abs=CERTIFIED_OFFSET_TOLERANCE_S
    )
    assert sorted(
        item.anchor.source_time_s for item in result.anchor_evidence
    ) == pytest.approx(sorted(source_global_times), abs=CERTIFIED_OFFSET_TOLERANCE_S)
    assert len({item.anchor.source_time_s for item in result.anchor_evidence}) == 5


def test_missing_segment_discloses_uncertainty_even_with_good_available_anchors(
    tmp_path: Path,
) -> None:
    times = [0.8, 2.1, 4.3, 7.2, 10.4]
    _write_clicks(tmp_path / "source.wav", rate=48_000, times=times, duration_s=11.0)
    _write_clicks(tmp_path / "reference.wav", rate=48_000, times=times, duration_s=11.0)

    def segment(path: str, status: MediaStatus, frames: int) -> MediaSegment:
        return MediaSegment(
            segment_id=new_project_id(),
            path=path,
            project_start_frame=0,
            frame_count=frames,
            sample_rate=48_000,
            channels=1,
            sample_format="PCM_24",
            media_status=status,
        )

    result = align_media_segments(
        tmp_path,
        (segment("reference.wav", MediaStatus.AVAILABLE, 11 * 48_000),),
        tmp_path,
        (
            segment("source.wav", MediaStatus.AVAILABLE, 11 * 48_000),
            segment("missing.wav", MediaStatus.MISSING, 48_000),
        ),
        project_sample_rate=48_000,
    )

    assert result.outcome is AlignmentOutcome.UNCERTAIN
    assert result.state.confidence < 0.72
    assert result.issues
    assert "incomplete" in result.reason.lower()


def test_silent_and_unrelated_audio_do_not_manufacture_alignment(tmp_path: Path) -> None:
    silence = tmp_path / "silence.wav"
    source = tmp_path / "source.wav"
    unrelated = tmp_path / "unrelated.wav"
    sf.write(silence, np.zeros(48_000, dtype="float32"), 48_000, subtype="PCM_24")
    _write_clicks(
        source,
        rate=48_000,
        times=[0.4, 1.7, 3.1, 5.8, 8.2, 11.4, 15.9, 20.3],
        duration_s=22.0,
    )
    _write_clicks(
        unrelated,
        rate=48_000,
        times=[0.9, 2.6, 4.9, 7.7, 10.1, 13.8, 17.2, 21.6],
        duration_s=23.0,
    )

    silent_result = align_audio_files(source, silence)
    unrelated_result = align_audio_files(unrelated, source)

    assert silent_result.outcome is AlignmentOutcome.NO_SIGNAL
    assert silent_result.state.confidence == 0.0
    assert unrelated_result.outcome is AlignmentOutcome.UNCERTAIN
    assert unrelated_result.state.confidence < 0.72


def test_alignment_state_reopen_and_manual_nudge_are_idempotent() -> None:
    automatic = AlignmentState(
        automatic_offset_s=-0.137,
        drift_ppm=812.5,
        confidence=0.94,
        method="fixture",
        residual_ms=0.4,
        anchors=(AlignmentAnchor(1.0, 0.8638125, 0.0),),
    )

    reopened = AlignmentState.from_dict(
        json.loads(json.dumps(automatic.to_dict(), sort_keys=True))
    )
    nudged = reopened.with_manual_nudge(0.012)
    restored = nudged.restore_automatic()

    assert reopened == automatic
    assert nudged.automatic_offset_s == automatic.automatic_offset_s
    assert nudged.manual_nudge_s == 0.012
    assert TimeTransform.from_state(nudged).source_to_project(20.0) == pytest.approx(
        TimeTransform.from_state(automatic).source_to_project(20.0) + 0.012
    )
    assert restored == automatic
    assert restored.restore_automatic() == restored
    project_frame = TimeTransform.from_state(restored).source_frame_to_project_frame(
        44_100,
        source_sample_rate=44_100,
        project_sample_rate=48_000,
    )
    assert project_frame == 41_463


def test_transient_scans_request_only_bounded_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "longer-than-one-block.wav"
    _write_clicks(path, rate=48_000, times=[0.2, 1.8, 3.2], duration_s=3.5)
    real_sound_file = sf.SoundFile
    read_requests: list[int] = []

    class TrackingSoundFile:
        def __init__(self, *args, **kwargs):
            self._delegate = real_sound_file(*args, **kwargs)
            self.samplerate = self._delegate.samplerate
            self.channels = self._delegate.channels

        def __len__(self):
            return len(self._delegate)

        def __enter__(self):
            self._delegate.__enter__()
            return self

        def __exit__(self, *args):
            return self._delegate.__exit__(*args)

        def read(self, frames, *args, **kwargs):
            read_requests.append(frames)
            return self._delegate.read(frames, *args, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", TrackingSoundFile)

    analysis = analyze_audio_transients(path)

    assert len(analysis.transients) == 3
    assert read_requests
    assert max(read_requests) <= READ_BLOCK_FRAMES

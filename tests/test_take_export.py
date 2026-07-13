from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from core.take_export import (
    TakeExportError,
    TrackMixSettings,
    export_logic_package,
)
from core.take_library import TakeInfo, TrackInfo
from core.take_project import (
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectMarker,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    new_project_id,
    write_take_project,
)


RATE = 8000


def _write(path: Path, data, *, rate: int = RATE) -> None:
    sf.write(path, np.asarray(data, dtype="float32"), rate, subtype="PCM_16")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_logic_export_aligns_signed_offsets_and_preserves_originals(tmp_path):
    take_dir = tmp_path / "Take 01"
    take_dir.mkdir()
    early = take_dir / "host-guitar.wav"
    late = take_dir / "drums.wav"
    _write(
        early,
        np.concatenate(
            (np.full(RATE // 4, 0.1), np.full(RATE // 2, 0.4))
        ),
    )
    _write(late, np.full(RATE // 2, 0.2))
    before = {_digest(early), _digest(late)}
    take = TakeInfo(
        path=take_dir,
        name="Take 01",
        tracks=[
            TrackInfo(
                early,
                "Guitar / Lead",
                offset_s=-0.25,
                duration_s=0.75,
                samplerate=RATE,
                source="local_ssl",
            ),
            TrackInfo(
                late,
                "Drums",
                offset_s=0.25,
                duration_s=0.5,
                samplerate=RATE,
            ),
        ],
    )

    result = export_logic_package(take, destination_root=tmp_path / "exports")

    assert result.frames == int(0.75 * RATE)
    assert len(result.stems) == 2
    first, first_rate = sf.read(result.stems[0], dtype="float32")
    second, second_rate = sf.read(result.stems[1], dtype="float32")
    assert first_rate == second_rate == RATE
    assert len(first) == len(second) == result.frames
    assert first[0] == pytest.approx(0.4, abs=0.01)
    assert np.max(np.abs(second[: RATE // 4])) < 1e-5
    assert second[RATE // 4] == pytest.approx(0.2, abs=0.01)
    assert sf.info(result.stems[0]).subtype == "PCM_24"
    assert {_digest(early), _digest(late)} == before

    payload = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert payload["all_stems_start_at_zero"] is True
    assert payload["original_files_modified"] is False
    assert payload["tracks"][0]["original_offset_s"] == -0.25
    assert payload["tracks"][0]["output_filename"].startswith(
        "01 Guitar - Lead"
    )
    assert "0:00" in result.instructions.read_text(encoding="utf-8")


def test_logic_export_rough_mix_honors_gain_pan_mute_and_solo(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    left = take_dir / "left.wav"
    muted = take_dir / "muted.wav"
    _write(left, np.full(RATE // 4, 0.4))
    _write(muted, np.full(RATE // 4, 0.8))
    take = TakeInfo(
        path=take_dir,
        name="Take",
        tracks=[
            TrackInfo(left, "Guitar", duration_s=0.25, samplerate=RATE),
            TrackInfo(muted, "Drums", duration_s=0.25, samplerate=RATE),
        ],
    )
    result = export_logic_package(
        take,
        destination_root=tmp_path / "exports",
        mix_settings={
            0: TrackMixSettings(gain=0.5, pan=-1.0, solo=True),
            1: TrackMixSettings(gain=1.0, pan=1.0, muted=False, solo=False),
        },
    )
    mix, rate = sf.read(result.mixdown, dtype="float32", always_2d=True)
    assert rate == RATE
    assert mix.shape[1] == 2
    assert mix[100, 0] == pytest.approx(0.2, abs=0.01)
    assert abs(float(mix[100, 1])) < 1e-5
    payload = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert payload["tracks"][0]["gain"] == 0.5
    assert payload["tracks"][0]["pan"] == -1.0
    assert payload["tracks"][0]["solo"] is True


def test_logic_export_never_overwrites_an_existing_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    track = take_dir / "guitar.wav"
    _write(track, np.ones(2048) * 0.1)
    take = TakeInfo(
        take_dir,
        "Take",
        [TrackInfo(track, "Guitar", duration_s=2048 / RATE, samplerate=RATE)],
    )
    root = tmp_path / "exports"
    first = export_logic_package(take, destination_root=root)
    second = export_logic_package(take, destination_root=root)
    assert first.folder.name == "Logic Export"
    assert second.folder.name == "Logic Export 2"
    assert first.manifest.exists()


def test_logic_export_rejects_mixed_rates_without_partial_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    first = take_dir / "a.wav"
    second = take_dir / "b.wav"
    _write(first, np.ones(2048) * 0.1, rate=8000)
    _write(second, np.ones(2048) * 0.1, rate=16000)
    take = TakeInfo(
        take_dir,
        "Take",
        [
            TrackInfo(first, "A", duration_s=0.25, samplerate=8000),
            TrackInfo(second, "B", duration_s=0.125, samplerate=16000),
        ],
    )
    root = tmp_path / "exports"
    with pytest.raises(TakeExportError, match="mixed sample rates"):
        export_logic_package(take, destination_root=root)
    assert not root.exists()


def test_logic_export_write_failure_leaves_no_visible_or_hidden_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    track = take_dir / "guitar.wav"
    _write(track, np.ones(2048) * 0.1)
    take = TakeInfo(
        take_dir,
        "Take",
        [TrackInfo(track, "Guitar", duration_s=2048 / RATE, samplerate=RATE)],
    )
    root = tmp_path / "exports"
    with patch(
        "core.take_export._write_aligned_stem",
        side_effect=OSError("disk full"),
    ), pytest.raises(OSError, match="disk full"):
        export_logic_package(take, destination_root=root)
    assert root.is_dir()
    assert list(root.iterdir()) == []


def _project_segment(path: Path, take_dir: Path, *, start: int = 0, gaps=()):
    info = sf.info(path)
    return MediaSegment(
        segment_id=new_project_id(),
        path=path.relative_to(take_dir).as_posix(),
        project_start_frame=start,
        frame_count=info.frames,
        sample_rate=info.samplerate,
        channels=info.channels,
        sample_format=info.subtype,
        media_status=MediaStatus.AVAILABLE,
        sha256=_digest(path),
        size_bytes=path.stat().st_size,
        has_signal=True,
        gaps=tuple(gaps),
    )


def _project_track(
    name: str,
    participant_id: str,
    segments,
    *,
    order: int,
    source_type=SourceType.LOCAL_ISOLATED,
    quality=SourceQuality.VERIFIED_ISOLATED,
    alignment=AlignmentState(),
):
    return ProjectTrack(
        track_id=new_project_id(),
        source_id=new_project_id(),
        participant_id=participant_id,
        name=name,
        instrument="Guitar" if order else "Drums",
        source_type=source_type,
        quality=quality,
        media_status=MediaStatus.AVAILABLE,
        order=order,
        segments=tuple(segments),
        alignment=alignment,
    )


def test_schema2_logic_handoff_resamples_drift_segments_and_writes_evidence(tmp_path):
    take_dir = tmp_path / "Project Take"
    take_dir.mkdir()
    network = take_dir / "network.wav"
    local_a = take_dir / "local-a.wav"
    local_b = take_dir / "local-b.wav"
    _write(network, np.full(24_000, 0.1), rate=48_000)
    local_fixture = np.zeros(11_025, dtype="float32")
    local_fixture[0:20] = 0.6
    _write(local_a, local_fixture, rate=44_100)
    local_fixture_b = np.zeros(8_820, dtype="float32")
    local_fixture_b[0:20] = 0.4
    _write(local_b, local_fixture_b, rate=44_100)
    before = {_digest(path) for path in (network, local_a, local_b)}

    host_id = new_project_id()
    guest_id = new_project_id()
    network_track = _project_track(
        "Live Drums",
        host_id,
        [_project_segment(network, take_dir)],
        order=0,
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        alignment=AlignmentState(confidence=1.0, method="server-origin"),
    )
    local_track = _project_track(
        "Guest Guitar",
        guest_id,
        [
            _project_segment(local_a, take_dir, start=4_800),
            _project_segment(
                local_b,
                take_dir,
                start=28_800,
                gaps=(GapInterval(100, 50, "writer queue overflow"),),
            ),
        ],
        order=1,
        alignment=AlignmentState(
            automatic_offset_s=0.05,
            manual_nudge_s=0.01,
            drift_ppm=1_000.0,
            confidence=0.91,
            method="gap-aware-transients-v1",
            residual_ms=0.8,
        ),
    )
    project = TakeProject(
        session_id=new_project_id(),
        take_id=new_project_id(),
        session_title="Sunday Session",
        take_name="Take 2",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(
            Participant(host_id, "Morgan", "Drums"),
            Participant(guest_id, "Riley", "Guitar"),
        ),
        tracks=(network_track, local_track),
        tempo_bpm=132.0,
        time_signature_numerator=3,
        time_signature_denominator=4,
        markers=(ProjectMarker(new_project_id(), 0.2, "Verse, one"),),
        warnings=("Guest reconnected once.",),
    )
    write_take_project(take_dir, project)
    take = TakeInfo(
        take_dir,
        "Take 2",
        [
            TrackInfo(network, "Live Drums", samplerate=48_000),
            TrackInfo(local_a, "Guest Guitar", samplerate=44_100),
        ],
        session_title=project.session_title,
        session_id=project.session_id,
        take_id=project.take_id,
    )

    result = export_logic_package(
        take,
        destination_root=tmp_path / "exports",
        mix_settings={1: TrackMixSettings(gain=0.75, pan=0.25)},
        include_processed_stems=True,
        chunk_frames=4096,
    )

    assert len(result.stems) == 2
    assert len(result.processed_stems) == 2
    assert result.reference_mix is not None and result.reference_mix.is_file()
    assert all(sf.info(path).samplerate == 48_000 for path in result.stems)
    assert all(sf.info(path).frames == result.frames for path in result.stems)
    local_render, rate = sf.read(result.stems[1], dtype="float32")
    assert rate == 48_000
    # 0.1 s segment placement + 0.06 s automatic/manual offset.
    assert np.max(np.abs(local_render[: int(0.159 * rate)])) < 1e-5
    assert np.max(np.abs(local_render[int(0.160 * rate) : int(0.162 * rate)])) > 0.5
    # Explicit reconnect gap remains silence between segments.
    assert np.max(np.abs(local_render[int(0.45 * rate) : int(0.64 * rate)])) < 1e-5
    assert {_digest(path) for path in (network, local_a, local_b)} == before

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["source_take_id"] == project.take_id
    assert manifest["tempo_bpm"] == 132.0
    assert manifest["time_signature"] == {"numerator": 3, "denominator": 4}
    assert manifest["tracks"][1]["musician"] == "Riley"
    assert manifest["tracks"][1]["resampling"] == "deterministic-linear-affine-v1"
    assert manifest["logic_pro_physically_verified"] is False
    assert result.alignment_report and "not a sample-perfect claim" in result.alignment_report.read_text()
    assert result.recording_report and "writer queue overflow" not in result.recording_report.read_text()
    assert "1 disclosed gap" in result.recording_report.read_text()
    assert result.analysis and len(json.loads(result.analysis.read_text())["files"]) == 6
    assert result.source_manifest and result.source_manifest.is_file()
    assert (result.folder / "MARKERS.csv").read_text().endswith('0.200000000,"Verse, one"\n')
    assert "does not claim" in result.instructions.read_text()

    checksum_lines = result.checksums.read_text().splitlines()
    names = {line.split("  ", 1)[1] for line in checksum_lines}
    assert "webjam-logic-export.json" in names
    assert "WebJam Server Reference.wav" in names
    for line in checksum_lines:
        digest, name = line.split("  ", 1)
        assert _digest(result.folder / name) == digest


def test_schema2_logic_handoff_blocks_missing_or_changed_media_atomically(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    audio = take_dir / "source.wav"
    _write(audio, np.ones(2048) * 0.1, rate=48_000)
    participant_id = new_project_id()
    track = _project_track(
        "Guitar",
        participant_id,
        [_project_segment(audio, take_dir)],
        order=0,
    )
    project = TakeProject(
        session_id=new_project_id(),
        take_id=new_project_id(),
        session_title="",
        take_name="Take",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(Participant(participant_id, "Alex"),),
        tracks=(track,),
    )
    write_take_project(take_dir, project)
    take = TakeInfo(
        take_dir,
        "Take",
        [TrackInfo(audio, "Guitar")],
        take_id=project.take_id,
    )
    # Source identity is checked again at export time, after project validation.
    audio.write_bytes(audio.read_bytes() + b"changed")
    root = tmp_path / "exports"
    with pytest.raises(TakeExportError, match="changed size"):
        export_logic_package(take, destination_root=root)
    assert root.is_dir()
    assert list(root.iterdir()) == []

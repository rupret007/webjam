"""Focused transactional and audio-truth tests for Studio arrangement export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import core.studio_export as studio_export
import core.studio_renderer as studio_renderer
from core.file_io import atomic_write_text
from core.studio_export import (
    StudioExportCancelled,
    StudioExportError,
    StudioExportPublishedError,
    export_studio_arrangement,
)
from core.studio_project import (
    FadeCurve,
    MarkerKind,
    StudioCompRange,
    StudioMarker,
    StudioMaster,
    StudioRegion,
    StudioTakeLane,
    default_studio_document,
)
from core.studio_renderer import StudioRenderer, StudioRenderStream
from core.studio_source_catalog import StudioSourceCatalog
from core.studio_store import STUDIO_STATE_FILENAME
from core.take_player import TakePlayer
from core.take_project import (
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    write_take_project,
)


RATE = 8_000


@pytest.fixture(autouse=True)
def _skip_exports_without_secure_platform_support(request) -> None:
    if (
        not studio_export._SECURE_EXPORT_PLATFORM_SUPPORTED
        and request.node.name
        not in {
            "test_studio_export_supported_reflects_secure_runtime_flags",
            "test_unsupported_platform_explicitly_fails_closed",
        }
    ):
        pytest.skip("Descriptor-bound Studio export is unavailable here.")


class _NoopSink:
    def start(self, _samplerate, _blocksize, _pull):
        pass

    def stop(self):
        pass


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _segment(
    take_dir: Path,
    number: int,
    samples: np.ndarray,
) -> tuple[MediaSegment, Path]:
    path = take_dir / f"source-{number}.wav"
    sf.write(path, np.asarray(samples, dtype=np.float32), RATE, subtype="FLOAT")
    info = sf.info(path)
    return (
        MediaSegment(
            segment_id=_id(number),
            path=path.name,
            project_start_frame=0,
            frame_count=int(info.frames),
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
            sample_format=str(info.subtype),
            media_status=MediaStatus.AVAILABLE,
            sha256=_digest(path),
            size_bytes=path.stat().st_size,
            has_signal=True,
        ),
        path,
    )


def _track(
    number: int,
    name: str,
    segment: MediaSegment,
    *,
    order: int,
    selected_for_export: bool = True,
) -> ProjectTrack:
    return ProjectTrack(
        track_id=_id(number),
        source_id=_id(number + 100),
        participant_id=None,
        name=name,
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=order,
        segments=(segment,),
        selected_for_export=selected_for_export,
    )


def _fixture(
    tmp_path: Path,
) -> tuple[Path, TakeProject, object, tuple[Path, Path]]:
    take_dir = tmp_path / "take"
    take_dir.mkdir()
    lead_segment, lead_path = _segment(
        take_dir,
        20,
        np.linspace(0.05, 0.38, 12, dtype=np.float32),
    )
    room_segment, room_path = _segment(
        take_dir,
        21,
        np.full(12, 0.1, dtype=np.float32),
    )
    lead = _track(10, "Lead / Guitar", lead_segment, order=0)
    room = _track(11, "Room", room_segment, order=1)
    project = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Studio export fixture",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(lead, room),
    )
    write_take_project(take_dir, project)

    document = default_studio_document(project)
    lead_region = next(
        item
        for item in document.regions
        if item.source_segment_id == lead_segment.segment_id
    )
    document = document.move_region(lead_region.region_id, 2)
    document = document.set_region_fades(
        lead_region.region_id,
        fade_in_frames=2,
        fade_out_frames=2,
        fade_in_curve=FadeCurve.LINEAR,
        fade_out_curve=FadeCurve.LINEAR,
    )
    document = document.update_track(
        lead.track_id,
        trim_gain=0.5,
        fader_gain=0.8,
        pan=0.25,
    )
    document = document.update_track(room.track_id, export_included=False)
    document = document.set_master(StudioMaster(gain=0.9, limiter_enabled=True))
    document = document.upsert_marker(
        StudioMarker(_id(30), 2, "Intro, count-in")
    ).upsert_marker(
        StudioMarker(
            _id(31),
            4,
            "Verse",
            kind=MarkerKind.SECTION,
            end_frame=10,
        )
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    return take_dir, project, document, (lead_path, room_path)


def _cross_take_fixture(
    tmp_path: Path,
    *,
    alternate_status: ProjectStatus = ProjectStatus.COMPLETE,
    alternate_selected: bool = True,
):
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary_root.mkdir()
    alternate_root.mkdir()
    primary_segment, primary_path = _segment(
        primary_root,
        20,
        np.full(12, 0.2, dtype=np.float32),
    )
    alternate_segment, alternate_path = _segment(
        alternate_root,
        20,
        np.full(12, 0.8, dtype=np.float32),
    )
    primary_track = _track(10, "Vocal", primary_segment, order=0)
    alternate_track = _track(
        10,
        "Vocal",
        alternate_segment,
        order=0,
        selected_for_export=alternate_selected,
    )
    alternate_track = replace(
        alternate_track,
        alignment=AlignmentState(
            automatic_offset_s=2 / RATE,
            confidence=0.99,
            method="server-timeline fixture",
        ),
    )
    primary = TakeProject(
        session_id=_id(1),
        take_id=_id(2),
        session_title="Repeated takes",
        take_name="Take 1",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(primary_track,),
    )
    alternate = TakeProject(
        session_id=primary.session_id,
        take_id=_id(3),
        session_title="Repeated takes",
        take_name="Take 2",
        status=alternate_status,
        project_sample_rate=RATE,
        participants=(),
        tracks=(alternate_track,),
    )
    write_take_project(primary_root, primary)
    write_take_project(alternate_root, alternate)

    document = default_studio_document(primary)
    alternate_region = StudioRegion(
        region_id=_id(40),
        track_id=primary_track.track_id,
        source_take_id=alternate.take_id,
        source_track_id=alternate_track.track_id,
        source_segment_id=alternate_segment.segment_id,
        source_start_frame=0,
        source_frame_count=alternate_segment.frame_count,
        timeline_start_frame=2,
        timeline_frame_count=alternate_segment.frame_count,
    )
    lane = StudioTakeLane(
        lane_id=_id(41),
        track_id=primary_track.track_id,
        source_take_id=alternate.take_id,
        source_track_id=alternate_track.track_id,
        name="Take 2",
        region_ids=(alternate_region.region_id,),
    )
    comp = StudioCompRange(
        comp_range_id=_id(42),
        track_id=primary_track.track_id,
        lane_id=lane.lane_id,
        timeline_start_frame=3,
        frame_count=6,
        fade_in_frames=3,
        fade_out_frames=3,
    )
    document = replace(
        document,
        regions=(*document.regions, alternate_region),
        take_lanes=(lane,),
        comp_ranges=(comp,),
    )
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    return (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        catalog,
        (primary_path, alternate_path),
    )


def _read(path: Path) -> np.ndarray:
    return sf.read(path, dtype="float32", always_2d=True)[0]


def _assert_pcm24_common_timeline(paths, *, frames: int) -> None:
    for path in paths:
        info = sf.info(path)
        assert info.samplerate == RATE
        assert info.channels == 2
        assert info.frames == frames
        assert info.format == "WAV"
        assert info.subtype == "PCM_24"


def test_export_is_authoritative_equal_length_and_evidence_complete(
    tmp_path: Path,
) -> None:
    take_dir, project, document, source_paths = _fixture(tmp_path)
    destination = tmp_path / "exports"
    source_hashes = tuple(_digest(path) for path in source_paths)
    manifest = take_dir / "webjam-take.json"
    state = take_dir / STUDIO_STATE_FILENAME
    manifest_hash = _digest(manifest)
    state_hash = _digest(state)

    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=destination,
        block_frames=3,
        disk_reserve_bytes=0,
    )

    assert result.folder == destination / "Studio Export"
    assert len(result.edited_stems) == 1
    assert len(result.original_stems) == 1
    assert result.frames == 14
    assert result.sample_rate == RATE
    assert not tuple(destination.glob(".webjam-studio-export-*"))
    audio_paths = (*result.edited_stems, *result.original_stems, result.rough_mix)
    _assert_pcm24_common_timeline(audio_paths, frames=result.frames)
    assert "Room" not in " ".join(path.name for path in audio_paths)

    selected_id = document.tracks[0].track_id
    edited_renderer = StudioRenderer(
        project,
        document,
        take_dir,
        track_ids=(selected_id,),
        respect_export_included=True,
        apply_master=False,
    )
    mix_renderer = StudioRenderer(
        project,
        document,
        take_dir,
        respect_export_included=True,
        apply_master=True,
    )
    original_renderer = StudioRenderer(
        project,
        default_studio_document(project),
        take_dir,
        track_ids=(selected_id,),
        apply_master=False,
    )
    tolerance = 2.0 / (2**23)
    np.testing.assert_allclose(
        _read(result.edited_stems[0]),
        edited_renderer.render_block(0, result.frames),
        atol=tolerance,
    )
    np.testing.assert_allclose(
        _read(result.rough_mix),
        mix_renderer.render_block(0, result.frames),
        atol=tolerance,
    )
    np.testing.assert_allclose(
        _read(result.original_stems[0]),
        original_renderer.render_block(0, result.frames),
        atol=tolerance,
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    assert provenance["schema_version"] == 1
    assert provenance["studio_document_revision"] == document.revision
    assert provenance["take_manifest"]["sha256"] == manifest_hash
    assert provenance["studio_state_file"]["sha256"] == state_hash
    assert provenance["timeline"] == {
        "duration_seconds": result.frames / RATE,
        "frame_count": result.frames,
        "origin_frame": 0,
        "sample_rate": RATE,
    }
    assert provenance["external_editor_validation"] == {
        "editor": None,
        "status": "NOT RUN",
        "tested": False,
    }
    assert provenance["selection"]["export_included_track_ids"] == [selected_id]
    assert provenance["tracks"][0]["region_ids"]
    assert provenance["tracks"][0]["source_segment_ids"] == [_id(20)]
    assert provenance["sources"][0]["sha256"] == source_hashes[0]
    assert provenance["selection"]["source_segment_ids"] == [_id(20)]
    assert result.source_manifest.read_bytes() == manifest.read_bytes()
    assert json.loads(result.studio_document.read_text(encoding="utf-8")) == (
        document.to_dict()
    )
    assert provenance["embedded_evidence"]["source_manifest"]["sha256"] == (
        _digest(result.source_manifest)
    )
    assert provenance["embedded_evidence"]["studio_document"]["sha256"] == (
        _digest(result.studio_document)
    )

    marker_text = result.markers_csv.read_text(encoding="utf-8")
    assert "Intro, count-in" in marker_text
    assert "section,Verse,4,10" in marker_text
    instructions = result.instructions.read_text(encoding="utf-8")
    assert "Any multitrack editor" in instructions
    assert "Logic Pro" in instructions
    assert "NOT RUN" in instructions

    checksum_lines = result.checksums.read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 8
    for line in checksum_lines:
        digest, relative = line.split("  ", 1)
        assert _digest(result.folder / relative) == digest
    assert tuple(_digest(path) for path in source_paths) == source_hashes
    assert _digest(manifest) == manifest_hash
    assert _digest(state) == state_hash


def test_player_stream_and_pcm24_export_share_one_complex_authoritative_mix(
    tmp_path: Path,
) -> None:
    """One edit-heavy fixture must sound identical in every delivery path."""

    project_rate = 1_000
    take_dir = tmp_path / "complex-take"
    take_dir.mkdir()

    def source_segment(
        number: int,
        samples: np.ndarray,
        *,
        sample_rate: int,
        gaps: tuple[GapInterval, ...] = (),
    ) -> tuple[MediaSegment, Path]:
        path = take_dir / f"complex-source-{number}.wav"
        sf.write(
            path, np.asarray(samples, dtype=np.float32), sample_rate, subtype="FLOAT"
        )
        info = sf.info(path)
        return (
            MediaSegment(
                segment_id=_id(number),
                path=path.name,
                project_start_frame=0,
                frame_count=int(info.frames),
                sample_rate=int(info.samplerate),
                channels=int(info.channels),
                sample_format=str(info.subtype),
                media_status=MediaStatus.AVAILABLE,
                sha256=_digest(path),
                size_bytes=path.stat().st_size,
                gaps=gaps,
                has_signal=True,
            ),
            path,
        )

    base_segment, _base_path = source_segment(
        120,
        np.linspace(0.04, 0.30, 20, dtype=np.float32),
        sample_rate=800,
        gaps=(GapInterval(6, 2, "authoritative source dropout"),),
    )
    alternate_segment, _alternate_path = source_segment(
        121,
        np.linspace(0.28, 0.08, 20, dtype=np.float32),
        sample_rate=800,
    )
    rhythm_segment, _rhythm_path = source_segment(
        122,
        np.sin(np.linspace(0.0, 3.0 * np.pi, 30, dtype=np.float32)) * 0.12,
        sample_rate=1_200,
        gaps=(GapInterval(11, 3, "second-source dropout", channels=(0,)),),
    )
    lead_track = ProjectTrack(
        track_id=_id(110),
        source_id=_id(210),
        participant_id=None,
        name="Lead",
        instrument="Voice",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(base_segment, alternate_segment),
        alignment=AlignmentState(
            automatic_offset_s=0.003,
            manual_nudge_s=0.002,
            drift_ppm=40_000.0,
            confidence=0.98,
            method="deterministic fixture",
        ),
    )
    rhythm_track = ProjectTrack(
        track_id=_id(111),
        source_id=_id(211),
        participant_id=None,
        name="Rhythm",
        instrument="Percussion",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=1,
        segments=(rhythm_segment,),
        alignment=AlignmentState(
            automatic_offset_s=0.004,
            drift_ppm=-40_000.0,
            confidence=0.96,
            method="deterministic fixture",
        ),
    )
    project = TakeProject(
        session_id=_id(101),
        take_id=_id(102),
        session_title="Authoritative renderer equivalence",
        take_name="Mixed rates and comp",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=project_rate,
        participants=(),
        tracks=(lead_track, rhythm_track),
    )
    write_take_project(take_dir, project)

    document = default_studio_document(project)
    regions = {item.source_segment_id: item for item in document.regions}
    base_region = regions[base_segment.segment_id]
    alternate_region = regions[alternate_segment.segment_id]
    rhythm_region = regions[rhythm_segment.segment_id]
    lane = StudioTakeLane(
        lane_id=_id(130),
        track_id=lead_track.track_id,
        source_take_id=project.take_id,
        source_track_id=lead_track.track_id,
        name="Lead alternate",
        region_ids=(alternate_region.region_id,),
    )
    document = document.upsert_take_lane(lane).select_comp_range(
        StudioCompRange(
            comp_range_id=_id(131),
            track_id=lead_track.track_id,
            lane_id=lane.lane_id,
            timeline_start_frame=11,
            frame_count=10,
            fade_in_frames=3,
            fade_out_frames=3,
        )
    )
    document = document.set_region_fades(
        base_region.region_id,
        fade_in_frames=3,
        fade_out_frames=4,
        fade_in_curve=FadeCurve.LINEAR,
        fade_out_curve=FadeCurve.LINEAR,
    ).set_region_fades(
        alternate_region.region_id,
        fade_in_frames=2,
        fade_out_frames=2,
        fade_in_curve=FadeCurve.LINEAR,
        fade_out_curve=FadeCurve.LINEAR,
    )
    document = document.trim_region(
        rhythm_region.region_id,
        timeline_start_frame=7,
        timeline_frame_count=15,
    ).move_region(rhythm_region.region_id, 38)
    document = document.set_region_fades(
        rhythm_region.region_id,
        fade_in_frames=3,
        fade_out_frames=3,
        fade_in_curve=FadeCurve.LINEAR,
        fade_out_curve=FadeCurve.LINEAR,
    )
    document = (
        document.update_track(
            lead_track.track_id,
            trim_gain=0.8,
            fader_gain=0.7,
            pan=-0.25,
        )
        .update_track(
            rhythm_track.track_id,
            trim_gain=0.9,
            fader_gain=0.65,
            pan=0.4,
        )
        .set_master(StudioMaster(gain=0.75, limiter_enabled=False))
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )

    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "complex-exports",
        block_frames=7,
        disk_reserve_bytes=0,
    )
    exported = _read(result.rough_mix)

    renderer = StudioRenderer(
        project,
        document,
        take_dir,
        respect_export_included=True,
        apply_master=True,
    )
    stream_blocks: list[np.ndarray] = []
    with renderer.open(end_frame=result.frames) as stream:
        requests = (3, 8, 5, 11)
        request_index = 0
        while stream.remaining_frames:
            count = min(
                requests[request_index % len(requests)],
                stream.remaining_frames,
            )
            stream_blocks.append(stream.read(count))
            request_index += 1
    streamed = np.concatenate(stream_blocks)

    class PullSink:
        pull = None

        def start(self, _sample_rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            pass

    sink = PullSink()
    player = TakePlayer(samplerate=project_rate, blocksize=9, sink=sink)
    player.load_studio(project, document, take_dir)
    player.play()
    assert sink.pull is not None
    player_blocks: list[np.ndarray] = []
    remaining = result.frames
    requests = (9, 4, 7)
    request_index = 0
    try:
        while remaining:
            count = min(requests[request_index % len(requests)], remaining)
            player_blocks.append(sink.pull(count))
            remaining -= count
            request_index += 1
    finally:
        player.stop()
    played = np.concatenate(player_blocks)

    assert {
        item.sample_rate for track in project.tracks for item in track.segments
    } == {
        800,
        1_200,
    }
    assert all(track.alignment.effective_offset_s != 0.0 for track in project.tracks)
    assert all(track.alignment.drift_ppm != 0.0 for track in project.tracks)
    assert (
        sum(len(item.gaps) for track in project.tracks for item in track.segments) == 2
    )
    assert len(document.take_lanes) == len(document.comp_ranges) == 1
    assert any(
        item.fade_in_frames and item.fade_out_frames for item in document.regions
    )
    assert document.region_for(rhythm_region.region_id).timeline_start_frame == 38
    assert result.frames == renderer.total_frames == len(exported) == len(streamed)
    assert len(played) == result.frames
    np.testing.assert_array_equal(streamed[31:38], np.zeros((7, 2), np.float32))

    pcm24_tolerance = 2.0 / (2**23)
    np.testing.assert_allclose(exported, streamed, atol=pcm24_tolerance, rtol=0.0)
    np.testing.assert_allclose(exported, played, atol=pcm24_tolerance, rtol=0.0)
    np.testing.assert_array_equal(streamed, played)


def test_cross_take_export_packages_unambiguous_sources_and_manifests(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        catalog,
        source_paths,
    ) = _cross_take_fixture(tmp_path)
    document = document.update_track(
        primary.tracks[0].track_id,
        trim_gain=0.45,
        fader_gain=0.6,
        pan=-0.35,
    ).set_master(StudioMaster(gain=0.5, limiter_enabled=True))
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    before = tuple(_digest(path) for path in source_paths)

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    expected = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
        apply_master=False,
    ).render_block(0, result.frames)
    np.testing.assert_allclose(
        _read(result.edited_stems[0]),
        expected,
        atol=2.0 / (2**23),
    )
    assert len(result.original_stems) == 2
    _assert_pcm24_common_timeline(result.original_stems, frames=result.frames)
    assert tuple(_digest(path) for path in source_paths) == before

    manifests = {path.name: path.read_bytes() for path in result.source_manifests}
    assert (
        manifests["source-take-manifest.json"]
        == (primary_root / "webjam-take.json").read_bytes()
    )
    assert (
        manifests[f"{alternate.take_id}.json"]
        == (alternate_root / "webjam-take.json").read_bytes()
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    originals = {item["take_id"]: item for item in provenance["original_stems"]}
    assert set(originals) == {primary.take_id, alternate.take_id}
    primary_original = result.folder / originals[primary.take_id]["relative_path"]
    alternate_original = result.folder / originals[alternate.take_id]["relative_path"]
    assert primary_original.name == "01 Vocal - aligned unity.wav"
    assert alternate.take_id in alternate_original.name
    assert alternate.tracks[0].track_id in alternate_original.name
    assert alternate.tracks[0].source_id in alternate_original.name
    alternate_expected = StudioRenderer(
        alternate,
        default_studio_document(alternate),
        alternate_root,
        track_ids=(alternate.tracks[0].track_id,),
        apply_master=False,
    ).render_block(0, result.frames)
    np.testing.assert_allclose(
        _read(alternate_original),
        alternate_expected,
        atol=2.0 / (2**23),
        rtol=0.0,
    )
    assert result.frames == 14
    np.testing.assert_array_equal(
        _read(alternate_original)[:2],
        np.zeros((2, 2), dtype=np.float32),
    )
    np.testing.assert_allclose(
        _read(alternate_original)[2:],
        np.full((12, 2), 0.8, dtype=np.float32),
        atol=2.0 / (2**23),
        rtol=0.0,
    )
    assert originals[alternate.take_id]["sha256"] == _digest(alternate_original)
    assert originals[alternate.take_id]["alignment"] == (
        alternate.tracks[0].alignment.to_dict()
    )
    assert originals[alternate.take_id]["source_segments"] == [
        {
            "channels": alternate.tracks[0].segments[0].channels,
            "frame_count": alternate.tracks[0].segments[0].frame_count,
            "gaps": [],
            "project_start_frame": 0,
            "relative_path": alternate.tracks[0].segments[0].path,
            "sample_rate": alternate.tracks[0].segments[0].sample_rate,
            "segment_id": alternate.tracks[0].segments[0].segment_id,
            "sha256": before[1],
            "size_bytes": alternate.tracks[0].segments[0].size_bytes,
        }
    ]
    assert originals[alternate.take_id]["render"] == {
        "arrangement_edits_applied": False,
        "manifest_alignment_applied": True,
        "master_processing_applied": False,
        "origin_frame": 0,
        "track_trim_fader_pan_applied": False,
    }
    source_keys = {
        (
            item["source_key"]["take_id"],
            item["source_key"]["track_id"],
            item["source_key"]["segment_id"],
        )
        for item in provenance["sources"]
    }
    # Both takes deliberately reuse track and segment IDs; take ID keeps their
    # evidence and renderer-cache identities distinct.
    assert source_keys == {
        (primary.take_id, _id(10), _id(20)),
        (alternate.take_id, _id(10), _id(20)),
    }
    assert {item["take_id"] for item in provenance["take_manifests"]} == {
        primary.take_id,
        alternate.take_id,
    }
    for item in provenance["take_manifests"]:
        assert _digest(result.folder / item["relative_path"]) == item["sha256"]
    checksum_relatives = {
        line.split("  ", 1)[1]
        for line in result.checksums.read_text(encoding="utf-8").splitlines()
    }
    assert f"source-take-manifests/{alternate.take_id}.json" in checksum_relatives
    assert originals[alternate.take_id]["relative_path"] in checksum_relatives


def test_uncomped_active_take_lane_retains_its_separate_original(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        catalog,
        _source_paths,
    ) = _cross_take_fixture(tmp_path)
    document = replace(document, comp_ranges=())
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    edited_expected = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, result.frames)
    np.testing.assert_allclose(
        _read(result.edited_stems[0]),
        edited_expected,
        atol=2.0 / (2**23),
        rtol=0.0,
    )
    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    assert {item["take_id"] for item in provenance["original_stems"]} == {
        primary.take_id,
        alternate.take_id,
    }
    assert {item["take_id"] for item in provenance["take_manifests"]} == {
        primary.take_id,
        alternate.take_id,
    }
    alternate_original = next(
        result.folder / item["relative_path"]
        for item in provenance["original_stems"]
        if item["take_id"] == alternate.take_id
    )
    expected_original = StudioRenderer(
        alternate,
        default_studio_document(alternate),
        alternate_root,
        track_ids=(alternate.tracks[0].track_id,),
        apply_master=False,
    ).render_block(0, result.frames)
    np.testing.assert_allclose(
        _read(alternate_original),
        expected_original,
        atol=2.0 / (2**23),
        rtol=0.0,
    )


def test_enabled_lane_retains_original_when_all_regions_are_tombstoned(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        catalog,
        _source_paths,
    ) = _cross_take_fixture(tmp_path)
    later_segment, later_path = _segment(
        alternate_root,
        21,
        np.full(6, 0.55, dtype=np.float32),
    )
    later_segment = replace(later_segment, project_start_frame=16)
    alternate = replace(
        alternate,
        tracks=(
            replace(
                alternate.tracks[0],
                segments=(*alternate.tracks[0].segments, later_segment),
            ),
        ),
        revision=alternate.revision + 1,
    )
    write_take_project(alternate_root, alternate)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    lane = document.take_lanes[0]
    document = replace(
        document,
        regions=tuple(
            replace(item, enabled=False, deleted=True)
            if item.region_id in lane.region_ids
            else item
            for item in document.regions
        ),
        comp_ranges=(),
    )
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    expected_takes = {primary.take_id, alternate.take_id}
    assert {item["take_id"] for item in provenance["original_stems"]} == expected_takes
    assert {item["take_id"] for item in provenance["take_manifests"]} == expected_takes
    assert {item["take_id"] for item in provenance["sources"]} == expected_takes
    assert {
        item["take_id"] for item in provenance["document_inventory"]["source_keys"]
    } == expected_takes
    assert {path.name for path in result.source_manifests} == {
        "source-take-manifest.json",
        f"{alternate.take_id}.json",
    }
    alternate_evidence = next(
        item
        for item in provenance["original_stems"]
        if item["take_id"] == alternate.take_id
    )
    alternate_original = result.folder / alternate_evidence["relative_path"]
    expected_original = StudioRenderer(
        alternate,
        default_studio_document(alternate),
        alternate_root,
        track_ids=(alternate.tracks[0].track_id,),
        apply_master=False,
    ).render_block(0, result.frames)
    np.testing.assert_allclose(
        _read(alternate_original),
        expected_original,
        atol=2.0 / (2**23),
        rtol=0.0,
    )
    assert alternate_evidence["sha256"] == _digest(alternate_original)
    assert {item["segment_id"] for item in alternate_evidence["source_segments"]} == {
        alternate.tracks[0].segments[0].segment_id,
        later_segment.segment_id,
    }
    assert next(
        item["sha256"]
        for item in alternate_evidence["source_segments"]
        if item["segment_id"] == later_segment.segment_id
    ) == _digest(later_path)
    checksum_relatives = {
        line.split("  ", 1)[1]
        for line in result.checksums.read_text(encoding="utf-8").splitlines()
    }
    assert alternate_evidence["relative_path"] in checksum_relatives
    assert f"source-take-manifests/{alternate.take_id}.json" in checksum_relatives


def test_disabled_lane_is_inventory_only_and_excluded_from_originals(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        _alternate_root,
        primary,
        alternate,
        document,
        catalog,
        _source_paths,
    ) = _cross_take_fixture(tmp_path)
    document = replace(
        document,
        take_lanes=(replace(document.take_lanes[0], enabled=False),),
        comp_ranges=(),
    )
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    assert len(result.original_stems) == 1
    assert {item["take_id"] for item in provenance["original_stems"]} == {
        primary.take_id
    }
    assert {item["take_id"] for item in provenance["take_manifests"]} == {
        primary.take_id
    }
    assert {item["take_id"] for item in provenance["sources"]} == {primary.take_id}
    assert {path.name for path in result.source_manifests} == {
        "source-take-manifest.json"
    }
    inventory_takes = {
        item["take_id"] for item in provenance["document_inventory"]["source_keys"]
    }
    assert inventory_takes == {primary.take_id, alternate.take_id}
    assert not (result.folder / "source-take-manifests").exists()


def test_unrelated_catalog_take_is_not_retained_or_packaged(tmp_path: Path) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        _catalog,
        _source_paths,
    ) = _cross_take_fixture(tmp_path)
    unused_root = tmp_path / "take-unused"
    unused_root.mkdir()
    unused_segment, _unused_path = _segment(
        unused_root,
        23,
        np.full(12, 0.6, dtype=np.float32),
    )
    unused_track = _track(12, "Unused Vocal", unused_segment, order=0)
    unused = TakeProject(
        session_id=primary.session_id,
        take_id=_id(4),
        session_title=primary.session_title,
        take_name="Unused take",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(unused_track,),
    )
    write_take_project(unused_root, unused)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root, unused_root),
    )

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        source_catalog=catalog,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    retained_takes = {item["take_id"] for item in provenance["original_stems"]}
    manifest_takes = {item["take_id"] for item in provenance["take_manifests"]}
    source_takes = {item["take_id"] for item in provenance["sources"]}
    assert (
        retained_takes
        == manifest_takes
        == source_takes
        == {
            primary.take_id,
            alternate.take_id,
        }
    )
    assert unused.take_id not in "\n".join(
        path.as_posix() for path in result.source_manifests
    )


def test_cross_take_export_applies_source_take_evidence_policy(tmp_path: Path) -> None:
    (
        primary_root,
        _alternate_root,
        primary,
        _alternate,
        document,
        catalog,
        _sources,
    ) = _cross_take_fixture(tmp_path, alternate_selected=False)

    with pytest.raises(
        StudioExportError, match="repeated-take source track is disabled"
    ):
        export_studio_arrangement(
            primary,
            document,
            primary_root,
            destination_root=tmp_path / "exports",
            source_catalog=catalog,
            disk_reserve_bytes=0,
        )

    review_fixture = tmp_path / "needs-review"
    review_fixture.mkdir()
    (
        review_primary_root,
        _review_alternate_root,
        review_primary,
        _review_alternate,
        review_document,
        review_catalog,
        _review_sources,
    ) = _cross_take_fixture(
        review_fixture,
        alternate_status=ProjectStatus.NEEDS_ATTENTION,
    )
    with pytest.raises(StudioExportError, match="still needs review"):
        export_studio_arrangement(
            review_primary,
            review_document,
            review_primary_root,
            destination_root=review_fixture / "exports",
            source_catalog=review_catalog,
            disk_reserve_bytes=0,
        )

    unaligned_fixture = tmp_path / "unaligned"
    unaligned_fixture.mkdir()
    (
        unaligned_primary_root,
        unaligned_alternate_root,
        unaligned_primary,
        unaligned_alternate,
        unaligned_document,
        _unaligned_catalog,
        _unaligned_sources,
    ) = _cross_take_fixture(unaligned_fixture)
    unaligned_track = replace(
        unaligned_alternate.tracks[0],
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.UNVERIFIED,
        alignment=AlignmentState(confidence=0.0, method="unverified"),
    )
    unaligned_alternate = replace(
        unaligned_alternate,
        tracks=(unaligned_track,),
        revision=unaligned_alternate.revision + 1,
    )
    write_take_project(unaligned_alternate_root, unaligned_alternate)
    unaligned_catalog = StudioSourceCatalog.load(
        unaligned_primary,
        unaligned_primary_root,
        additional_take_roots=(unaligned_alternate_root,),
    )
    destination = unaligned_fixture / "exports"
    with pytest.raises(StudioExportError) as failure:
        export_studio_arrangement(
            unaligned_primary,
            unaligned_document,
            unaligned_primary_root,
            destination_root=destination,
            source_catalog=unaligned_catalog,
            disk_reserve_bytes=0,
        )
    assert "no verified timeline alignment" in str(failure.value.__cause__)
    assert not destination.exists()


def test_cross_take_export_fails_closed_without_catalog(tmp_path: Path) -> None:
    (
        primary_root,
        _alternate_root,
        primary,
        _alternate,
        document,
        _catalog,
        _sources,
    ) = _cross_take_fixture(tmp_path)

    with pytest.raises(StudioExportError, match="no trusted source catalog"):
        export_studio_arrangement(
            primary,
            document,
            primary_root,
            destination_root=tmp_path / "exports",
            disk_reserve_bytes=0,
        )


def test_tombstoned_cross_take_remains_inventory_not_export_prerequisite(
    tmp_path: Path,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        alternate,
        document,
        _catalog,
        _sources,
    ) = _cross_take_fixture(tmp_path)
    document = document.remove_take_lane(document.take_lanes[0].lane_id)
    atomic_write_text(
        primary_root / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    shutil.rmtree(alternate_root)

    result = export_studio_arrangement(
        primary,
        document,
        primary_root,
        destination_root=tmp_path / "exports",
        disk_reserve_bytes=0,
    )

    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    evidence_takes = {item["take_id"] for item in provenance["sources"]}
    inventory_takes = {
        item["take_id"] for item in provenance["document_inventory"]["source_keys"]
    }
    assert evidence_takes == {primary.take_id}
    assert inventory_takes == {primary.take_id, alternate.take_id}
    assert len(result.original_stems) == 1
    assert {item["take_id"] for item in provenance["original_stems"]} == {
        primary.take_id
    }
    assert {item["take_id"] for item in provenance["take_manifests"]} == {
        primary.take_id
    }
    assert {path.name for path in result.source_manifests} == {
        "source-take-manifest.json"
    }
    assert not (result.folder / "source-take-manifests").exists()


def test_cross_take_manifest_change_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        primary_root,
        alternate_root,
        primary,
        _alternate,
        document,
        catalog,
        _sources,
    ) = _cross_take_fixture(tmp_path)
    destination = tmp_path / "exports"
    original_verify = studio_export._verify_sources

    def mutate_alternate_manifest(snapshots, cancel_event):
        original_verify(snapshots, cancel_event)
        manifest = alternate_root / "webjam-take.json"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )

    monkeypatch.setattr(studio_export, "_verify_sources", mutate_alternate_manifest)

    with pytest.raises(StudioExportError, match="take manifest changed"):
        export_studio_arrangement(
            primary,
            document,
            primary_root,
            destination_root=destination,
            source_catalog=catalog,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert not destination.exists() or not tuple(destination.iterdir())


def test_export_writes_only_bounded_renderer_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    observed: list[int] = []
    original_read = StudioRenderStream.read_with_tracks

    def recording_read(self, frame_count):
        observed.append(frame_count)
        return original_read(self, frame_count)

    monkeypatch.setattr(StudioRenderStream, "read_with_tracks", recording_read)

    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )

    assert result.frames == 14
    assert observed
    assert max(observed) <= 2
    assert sum(observed) == result.frames * 3


def test_cancellation_removes_unpublished_audio_and_temp_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, source_paths = _fixture(tmp_path)
    destination = tmp_path / "exports"
    before = tuple(_digest(path) for path in source_paths)
    cancelled = threading.Event()
    original_read = StudioRenderStream.read_with_tracks

    def cancelling_read(self, frame_count):
        block = original_read(self, frame_count)
        cancelled.set()
        return block

    monkeypatch.setattr(StudioRenderStream, "read_with_tracks", cancelling_read)

    with pytest.raises(StudioExportCancelled, match="cancelled"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
            cancel_event=cancelled,
        )

    assert not destination.exists() or not tuple(destination.iterdir())
    assert tuple(_digest(path) for path in source_paths) == before


def test_cancellation_on_publication_lock_entry_never_publishes_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    cancelled = threading.Event()
    original_lock = studio_export._export_publication_lock

    @contextmanager
    def cancelling_lock(export_root):
        with original_lock(export_root):
            cancelled.set()
            yield

    monkeypatch.setattr(studio_export, "_export_publication_lock", cancelling_lock)

    with pytest.raises(StudioExportCancelled, match="cancelled"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
            cancel_event=cancelled,
        )

    assert not tuple(destination.glob("Studio Export*"))
    assert not tuple(destination.glob(".webjam-studio-export-*"))


def test_noncooperating_process_cannot_claim_and_be_replaced_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    original_publish = studio_export._publish_directory_no_replace
    claimed: Path | None = None

    def claim_before_publish(export_root, source_name: str, final_name: str) -> None:
        nonlocal claimed
        final_folder = export_root.path / final_name
        claimed = final_folder
        final_folder.mkdir()
        (final_folder / "belongs-to-another-process.txt").write_text(
            "do not overwrite\n",
            encoding="utf-8",
        )
        original_publish(export_root, source_name, final_name)

    monkeypatch.setattr(
        studio_export,
        "_publish_directory_no_replace",
        claim_before_publish,
    )

    with pytest.raises(StudioExportError, match="claimed before publication"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert claimed is not None
    assert (claimed / "belongs-to-another-process.txt").read_text(
        encoding="utf-8"
    ) == "do not overwrite\n"
    assert not tuple(destination.glob(".webjam-studio-export-*"))


def test_post_rename_name_substitution_is_withdrawn_without_exposing_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    attacker_target = tmp_path / "attacker-published-name"
    attacker_target.mkdir()
    moved_package = destination / ".moved-published-package"
    original_publish = studio_export._publish_directory_no_replace
    substituted = False

    def substitute_after_rename(export_root, source_name: str, final_name: str) -> None:
        nonlocal substituted
        original_publish(export_root, source_name, final_name)
        final_path = export_root.path / final_name
        final_path.rename(moved_package)
        final_path.symlink_to(attacker_target, target_is_directory=True)
        substituted = True

    monkeypatch.setattr(
        studio_export,
        "_publish_directory_no_replace",
        substitute_after_rename,
    )

    with pytest.raises(StudioExportError, match="verified name") as failure:
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert substituted is True
    assert not isinstance(failure.value, StudioExportPublishedError)
    assert not hasattr(failure.value, "folder")
    assert tuple(attacker_target.iterdir()) == ()
    assert not moved_package.exists()
    assert not (destination / "Studio Export").is_symlink()
    assert not tuple(destination.glob("Studio Export*"))


def test_raced_destination_symlink_is_rejected_without_writing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    attacker_target = tmp_path / "redirected"
    original_mkdir = studio_export.os.mkdir
    raced = False

    def racing_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal raced
        if not raced and dir_fd is not None and path == destination.name:
            raced = True
            original_mkdir(attacker_target, 0o700)
            os.symlink(attacker_target, destination, target_is_directory=True)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(studio_export.os, "mkdir", racing_mkdir)

    with pytest.raises(StudioExportError, match="symbolic link"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert raced is True
    assert destination.is_symlink()
    assert tuple(attacker_target.iterdir()) == ()


def test_nested_parent_replacement_cannot_redirect_descriptor_bound_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    nested_parent = tmp_path / "nested"
    detached_parent = tmp_path / "detached-nested"
    attacker_target = tmp_path / "attacker-target"
    destination = nested_parent / "deeper" / "exports"
    original_open_directory = studio_export._open_directory_at
    replaced = False

    def replacing_open(parent_descriptor: int, name: str, *, create: bool):
        nonlocal replaced
        opened = original_open_directory(
            parent_descriptor,
            name,
            create=create,
        )
        if not replaced and name == nested_parent.name:
            replaced = True
            nested_parent.rename(detached_parent)
            attacker_target.mkdir()
            nested_parent.symlink_to(attacker_target, target_is_directory=True)
        return opened

    monkeypatch.setattr(
        studio_export,
        "_open_directory_at",
        replacing_open,
    )

    with pytest.raises(StudioExportError, match="destination"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert replaced is True
    assert nested_parent.is_symlink()
    assert tuple(attacker_target.iterdir()) == ()
    assert (detached_parent / "deeper").is_dir()
    assert not (detached_parent / "deeper" / "exports").exists()


def test_temporary_entry_substitution_is_rejected_and_dangling_link_is_unlinked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    missing_target = tmp_path / "attacker-does-not-exist"
    original_lock = studio_export._export_publication_lock
    substituted_path: Path | None = None
    preserved_path: Path | None = None

    @contextmanager
    def substituting_lock(export_root):
        nonlocal substituted_path, preserved_path
        with original_lock(export_root):
            substituted_path = next(
                item
                for item in export_root.path.iterdir()
                if item.name.startswith(".webjam-studio-export-")
                and item.name != studio_export._EXPORT_LOCK_FILENAME
            )
            preserved_path = export_root.path / ".moved-export-transaction"
            substituted_path.rename(preserved_path)
            substituted_path.symlink_to(missing_target, target_is_directory=True)
            yield

    monkeypatch.setattr(
        studio_export,
        "_export_publication_lock",
        substituting_lock,
    )

    with pytest.raises(StudioExportError, match="package tree"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert substituted_path is not None
    assert not substituted_path.is_symlink()
    assert preserved_path is not None
    assert not preserved_path.exists()
    assert not tuple(destination.glob("Studio Export*"))


def test_temp_root_rename_and_external_symlink_during_first_stem_cannot_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    attacker_target = tmp_path / "attacker-package"
    attacker_target.mkdir()
    relocated = destination / ".relocated-held-package"
    original_read = StudioRenderStream.read_with_tracks
    redirected = False

    def redirect_package_name(self, frame_count):
        nonlocal redirected
        if not redirected:
            redirected = True
            temporary = next(destination.glob(".webjam-studio-export-*"))
            temporary.rename(relocated)
            temporary.symlink_to(attacker_target, target_is_directory=True)
        return original_read(self, frame_count)

    monkeypatch.setattr(
        StudioRenderStream,
        "read_with_tracks",
        redirect_package_name,
    )

    with pytest.raises(StudioExportError, match="package tree"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert redirected is True
    assert tuple(attacker_target.iterdir()) == ()
    assert not relocated.exists()
    assert not destination.exists()


def test_stem_folder_relocation_and_symlink_cannot_publish_or_redirect_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    attacker_target = tmp_path / "attacker-stems"
    attacker_target.mkdir()
    relocated = tmp_path / "relocated-edited-stems"
    original_read = StudioRenderStream.read_with_tracks
    redirected = False

    def redirect_stem_folder(self, frame_count):
        nonlocal redirected
        if not redirected:
            redirected = True
            temporary = next(destination.glob(".webjam-studio-export-*"))
            edited = temporary / "edited-stems"
            edited.rename(relocated)
            edited.symlink_to(attacker_target, target_is_directory=True)
        return original_read(self, frame_count)

    monkeypatch.setattr(
        StudioRenderStream,
        "read_with_tracks",
        redirect_stem_folder,
    )

    with pytest.raises(StudioExportError, match="package tree"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert redirected is True
    assert tuple(attacker_target.iterdir()) == ()
    assert relocated.is_dir()
    assert tuple(relocated.iterdir()) == ()
    assert not destination.exists()


def test_relocated_open_wav_is_scrubbed_through_its_retained_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    relocated = tmp_path / "relocated-complete-stem.wav"
    original_read = StudioRenderStream.read_with_tracks
    relocated_during_write = False

    def relocate_open_stem(self, frame_count):
        nonlocal relocated_during_write
        if not relocated_during_write:
            relocated_during_write = True
            temporary = next(destination.glob(".webjam-studio-export-*"))
            open_stem = next((temporary / "edited-stems").glob("*.wav"))
            open_stem.rename(relocated)
        return original_read(self, frame_count)

    monkeypatch.setattr(
        StudioRenderStream,
        "read_with_tracks",
        relocate_open_stem,
    )

    with pytest.raises(StudioExportError, match="leaf changed"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert relocated_during_write is True
    assert relocated.is_file()
    # The attacker chose the new parent/name, so unlinking it would be unsafe.
    # Cleanup instead scrubs the exact retained inode and durably leaves no PCM.
    assert relocated.stat().st_size == 0
    with pytest.raises(RuntimeError):
        sf.info(relocated)
    assert not destination.exists()


def test_relocated_metadata_is_scrubbed_by_the_shared_leaf_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    relocated = tmp_path / "relocated-studio-document.json"
    original_write = studio_export._write_package_bytes
    moved = False

    def relocate_metadata(package, relative: Path, data: bytes) -> None:
        nonlocal moved
        original_write(package, relative, data)
        if not moved and relative == Path("studio-document.json"):
            moved = True
            (package.temporary.path / relative).rename(relocated)

    monkeypatch.setattr(
        studio_export,
        "_write_package_bytes",
        relocate_metadata,
    )

    with pytest.raises(StudioExportError):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert moved is True
    assert relocated.is_file()
    assert relocated.stat().st_size == 0
    assert relocated.read_bytes() == b""
    assert not destination.exists()


def test_success_closes_every_retained_package_leaf_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    original_close = studio_export._BoundPackage.close
    closed_leaf_counts: list[int] = []

    def checking_close(package) -> None:
        descriptors = [item.descriptor for item in package.files.values()]
        original_close(package)
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        closed_leaf_counts.append(len(descriptors))

    monkeypatch.setattr(studio_export._BoundPackage, "close", checking_close)

    export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )

    assert closed_leaf_counts == [9]


def test_cancellation_cleanup_unlinks_dangling_symlink_inside_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    cancelled = threading.Event()
    original_verify = studio_export._verify_sources
    dangling_path: Path | None = None

    def add_dangling_link(snapshots, cancel_event):
        nonlocal dangling_path
        original_verify(snapshots, cancel_event)
        temporary = next(destination.glob(".webjam-studio-export-*"))
        dangling_path = temporary / "dangling-evidence-link"
        dangling_path.symlink_to(temporary / "missing-evidence")
        cancelled.set()

    monkeypatch.setattr(studio_export, "_verify_sources", add_dangling_link)

    with pytest.raises(StudioExportCancelled, match="cancelled"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
            cancel_event=cancelled,
        )

    assert dangling_path is not None
    assert not dangling_path.is_symlink()
    assert not destination.exists()


def test_destination_rejects_lexical_parent_traversal(tmp_path: Path) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "claimed" / ".." / "exports"

    with pytest.raises(StudioExportError, match="parent-directory traversal"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert not (tmp_path / "claimed").exists()
    assert not (tmp_path / "exports").exists()


def test_missing_destination_ancestors_fsync_each_bound_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "one" / "two" / "exports"
    observed: list[tuple[int, int, int]] = []
    original_fsync_parent = studio_export._fsync_created_parent

    def recording_fsync(parent_descriptor: int) -> None:
        observed.append(studio_export._directory_identity(os.fstat(parent_descriptor)))
        original_fsync_parent(parent_descriptor)

    monkeypatch.setattr(
        studio_export,
        "_fsync_created_parent",
        recording_fsync,
    )

    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=destination,
        block_frames=2,
        disk_reserve_bytes=0,
    )

    expected = [
        studio_export._directory_identity(path.stat())
        for path in (tmp_path, tmp_path / "one", tmp_path / "one" / "two")
    ]
    assert observed[:3] == expected
    assert result.folder.is_dir()


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="Native exclusive directory rename is implemented on Darwin and Linux.",
)
def test_native_descriptor_relative_publish_never_replaces_existing_name(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "exports"
    destination.mkdir()
    root = studio_export._bind_export_root(destination)
    temporary = studio_export._create_temporary_directory(root)
    claimed = destination / "Studio Export"
    claimed.mkdir()
    marker = claimed / "belongs-to-another-process.txt"
    marker.write_text("preserve me\n", encoding="utf-8")
    try:
        with pytest.raises(StudioExportError, match="claimed before publication"):
            studio_export._publish_directory_no_replace(
                root,
                temporary.name,
                claimed.name,
            )
        assert marker.read_text(encoding="utf-8") == "preserve me\n"
        assert temporary.path.is_dir()
    finally:
        studio_export._remove_temporary_directory(root, temporary)
        temporary.close()
        root.close()


def test_studio_export_supported_reflects_secure_runtime_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(studio_export, "_SECURE_EXPORT_PLATFORM_SUPPORTED", True)
    monkeypatch.setattr(studio_export, "_SECURE_DIR_FD_SUPPORTED", True)
    assert studio_export.studio_export_supported()

    monkeypatch.setattr(studio_export, "_SECURE_EXPORT_PLATFORM_SUPPORTED", False)
    assert not studio_export.studio_export_supported()

    monkeypatch.setattr(studio_export, "_SECURE_EXPORT_PLATFORM_SUPPORTED", True)
    monkeypatch.setattr(studio_export, "_SECURE_DIR_FD_SUPPORTED", False)
    assert not studio_export.studio_export_supported()


def test_unsupported_platform_explicitly_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    monkeypatch.setattr(
        studio_export,
        "_SECURE_EXPORT_PLATFORM_SUPPORTED",
        False,
    )
    assert not studio_export.studio_export_supported()

    with pytest.raises(StudioExportError, match="unavailable on this platform"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert not destination.exists()

    monkeypatch.setattr(studio_export, "_SECURE_EXPORT_PLATFORM_SUPPORTED", True)
    monkeypatch.setattr(studio_export, "_SECURE_DIR_FD_SUPPORTED", False)
    assert not studio_export.studio_export_supported()
    with pytest.raises(StudioExportError, match="unavailable on this platform"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    assert not destination.exists()


def test_disk_preflight_fails_before_creating_an_export_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    monkeypatch.setattr(
        studio_export.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )

    with pytest.raises(StudioExportError, match="disk space"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            disk_reserve_bytes=0,
        )

    assert not destination.exists()


def test_changed_manifest_and_changed_source_fail_closed(
    tmp_path: Path,
) -> None:
    take_dir, project, document, source_paths = _fixture(tmp_path)
    destination = tmp_path / "exports"
    manifest = take_dir / "webjam-take.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["take_name"] = "Different snapshot"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StudioExportError, match="does not match"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            disk_reserve_bytes=0,
        )
    assert not destination.exists()

    write_take_project(take_dir, replace(project, revision=project.revision + 1))
    changed = bytearray(source_paths[0].read_bytes())
    changed[-1] ^= 0x01
    source_paths[0].write_bytes(changed)
    newer_project = replace(project, revision=project.revision + 1)
    newer_document = replace(document, revision=document.revision + 1)
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(newer_document.to_dict(), indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    with pytest.raises(StudioExportError, match="source changed"):
        export_studio_arrangement(
            newer_project,
            newer_document,
            take_dir,
            destination_root=destination,
            disk_reserve_bytes=0,
        )
    assert not destination.exists()


def test_metadata_failure_cleans_the_transaction_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"

    def fail_write(_package, _path, _text):
        raise OSError("disk failed")

    monkeypatch.setattr(studio_export, "_write_text", fail_write)

    with pytest.raises(StudioExportError, match="safely"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=3,
            disk_reserve_bytes=0,
        )

    assert not destination.exists() or not tuple(destination.iterdir())


def test_saved_state_must_exactly_match_the_exported_document(tmp_path: Path) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    changed = document.update_track(document.tracks[0].track_id, pan=-0.5)

    with pytest.raises(StudioExportError, match="does not match its saved state"):
        export_studio_arrangement(
            project,
            changed,
            take_dir,
            destination_root=destination,
            disk_reserve_bytes=0,
        )

    assert not destination.exists()


def test_rendered_source_symlink_is_rejected_even_when_bytes_match(
    tmp_path: Path,
) -> None:
    take_dir, project, document, source_paths = _fixture(tmp_path)
    source = source_paths[0]
    preserved = take_dir / "preserved-source.wav"
    shutil.copyfile(source, preserved)
    source.unlink()
    try:
        source.symlink_to(preserved.name)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(StudioExportError, match="symbolic link"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=tmp_path / "exports",
            disk_reserve_bytes=0,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_existing_destination_permissions_are_not_rewritten(tmp_path: Path) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)

    export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=destination,
        disk_reserve_bytes=0,
    )

    assert stat.S_IMODE(destination.stat().st_mode) == 0o755


def test_source_checksums_are_not_repeated_for_every_stem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    rendered_source_hashes = 0
    renderer_checksum_hashes = 0
    original_hash = studio_export._hash_file
    original_descriptor_hash = studio_renderer._sha256_descriptor

    def counted_hash(path, cancel_event=None, *, label="export evidence file"):
        nonlocal rendered_source_hashes
        if label == "rendered source":
            rendered_source_hashes += 1
        return original_hash(path, cancel_event, label=label)

    def counted_renderer_checksum(descriptor, cancel_check=None):
        nonlocal renderer_checksum_hashes
        renderer_checksum_hashes += 1
        return original_descriptor_hash(descriptor, cancel_check)

    monkeypatch.setattr(studio_export, "_hash_file", counted_hash)
    monkeypatch.setattr(
        studio_renderer,
        "_sha256_descriptor",
        counted_renderer_checksum,
    )

    export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "exports",
        block_frames=2,
        disk_reserve_bytes=0,
    )

    # The one selected source is snapshotted once before render and once before
    # publication, and renderer validation is reused across edited/original
    # documents instead of repeated per stem.
    assert rendered_source_hashes == 2
    assert renderer_checksum_hashes == 1


def test_state_created_mid_export_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"
    state_path = take_dir / STUDIO_STATE_FILENAME
    state_path.unlink()
    original_verify = studio_export._verify_sources

    def create_state_then_verify(snapshots, cancel_event):
        atomic_write_text(
            state_path,
            json.dumps(document.to_dict(), sort_keys=True) + "\n",
            mode=0o600,
        )
        return original_verify(snapshots, cancel_event)

    monkeypatch.setattr(studio_export, "_verify_sources", create_state_then_verify)

    with pytest.raises(StudioExportError, match="state file changed"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=3,
            disk_reserve_bytes=0,
        )

    assert not destination.exists() or not tuple(destination.iterdir())


def test_manifest_disabled_track_cannot_be_reenabled_by_studio(tmp_path: Path) -> None:
    take_dir, project, _document, _sources = _fixture(tmp_path)
    room = replace(project.tracks[1], selected_for_export=False)
    project = replace(project, tracks=(project.tracks[0], room), revision=2)
    write_take_project(take_dir, project)
    document = default_studio_document(project)
    assert document.state_for(room.track_id).export_included is False

    forged = document.update_track(room.track_id, export_included=True)
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(forged.to_dict(), sort_keys=True) + "\n",
        mode=0o600,
    )
    with pytest.raises(StudioExportError, match="disabled by the take"):
        export_studio_arrangement(
            project,
            forged,
            take_dir,
            destination_root=tmp_path / "exports",
            disk_reserve_bytes=0,
        )


def test_explicit_silence_and_unverified_local_alignment_keep_existing_blocks(
    tmp_path: Path,
) -> None:
    take_dir, project, _document, _sources = _fixture(tmp_path)
    lead = project.tracks[0]
    silent_segment = replace(lead.segments[0], has_signal=False)
    silent_project = replace(
        project,
        tracks=(replace(lead, segments=(silent_segment,)), project.tracks[1]),
        revision=2,
    )
    write_take_project(take_dir, silent_project)
    silent_document = default_studio_document(silent_project).update_track(
        project.tracks[1].track_id,
        export_included=False,
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(silent_document.to_dict(), sort_keys=True) + "\n",
        mode=0o600,
    )
    with pytest.raises(StudioExportError, match="explicitly silent segments"):
        export_studio_arrangement(
            silent_project,
            silent_document,
            take_dir,
            destination_root=tmp_path / "silent-exports",
            disk_reserve_bytes=0,
        )

    local = replace(
        lead,
        source_type=SourceType.LOCAL_ISOLATED,
        alignment=AlignmentState(confidence=0.0, method="unverified"),
    )
    local_project = replace(
        project,
        tracks=(local, project.tracks[1]),
        revision=3,
    )
    write_take_project(take_dir, local_project)
    local_document = default_studio_document(local_project).update_track(
        project.tracks[1].track_id,
        export_included=False,
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(local_document.to_dict(), sort_keys=True) + "\n",
        mode=0o600,
    )
    with pytest.raises(StudioExportError, match="no verified timeline alignment"):
        export_studio_arrangement(
            local_project,
            local_document,
            take_dir,
            destination_root=tmp_path / "local-exports",
            disk_reserve_bytes=0,
        )


def test_needs_attention_project_cannot_export(tmp_path: Path) -> None:
    take_dir, project, _document, _sources = _fixture(tmp_path)
    project = replace(project, status=ProjectStatus.NEEDS_ATTENTION, revision=2)
    write_take_project(take_dir, project)
    document = default_studio_document(project).update_track(
        project.tracks[1].track_id,
        export_included=False,
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), sort_keys=True) + "\n",
        mode=0o600,
    )

    with pytest.raises(StudioExportError, match="needs review"):
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=tmp_path / "exports",
            disk_reserve_bytes=0,
        )


def test_overload_uses_same_deterministic_delivery_clip_as_playback(
    tmp_path: Path,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    lead_id = project.tracks[0].track_id
    room_id = project.tracks[1].track_id
    document = (
        document.update_track(
            lead_id,
            trim_gain=4.0,
            fader_gain=4.0,
            pan=0.0,
        )
        .update_track(
            room_id,
            muted=True,
            export_included=False,
        )
        .set_master(StudioMaster(gain=1.0, limiter_enabled=False))
    )
    atomic_write_text(
        take_dir / STUDIO_STATE_FILENAME,
        json.dumps(document.to_dict(), sort_keys=True) + "\n",
        mode=0o600,
    )

    result = export_studio_arrangement(
        project,
        document,
        take_dir,
        destination_root=tmp_path / "exports",
        block_frames=3,
        disk_reserve_bytes=0,
    )
    provenance = json.loads(result.provenance.read_text(encoding="utf-8"))
    clip_counts = {
        item["relative_path"]: item["clipped_sample_count"]
        for item in provenance["outputs"]
    }
    assert clip_counts["rough-mix.wav"] > 0
    assert clip_counts[result.edited_stems[0].relative_to(result.folder).as_posix()] > 0
    assert np.max(np.abs(_read(result.rough_mix))) <= 1.0

    class PullSink:
        pull = None

        def start(self, _sample_rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            pass

    sink = PullSink()
    player = TakePlayer(samplerate=RATE, sink=sink)
    player.load_studio(project, document, take_dir)
    player.play()
    assert sink.pull is not None
    try:
        playback = sink.pull(result.frames)
    finally:
        player.stop()
    tolerance = 2.0 / (2**23)
    np.testing.assert_allclose(
        _read(result.rough_mix),
        playback,
        atol=tolerance,
    )


def test_concurrent_exports_publish_distinct_complete_folders(tmp_path: Path) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = tmp_path / "exports"

    def run_export():
        return export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=2,
            disk_reserve_bytes=0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: run_export(), range(2)))

    assert {item.folder.name for item in results} == {
        "Studio Export",
        "Studio Export 2",
    }
    for result in results:
        assert result.folder.is_dir()
        assert result.checksums.is_file()
        assert not tuple(result.folder.parent.glob(".webjam-studio-export-*"))


def test_post_publish_fsync_failure_reports_the_committed_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    take_dir, project, document, _sources = _fixture(tmp_path)
    destination = (tmp_path / "exports").resolve()
    original_fsync = studio_export._fsync_export_root

    def fail_final_directory(export_root) -> None:
        if export_root.path == destination and (destination / "Studio Export").exists():
            raise OSError("directory fsync failed")
        original_fsync(export_root)

    monkeypatch.setattr(studio_export, "_fsync_export_root", fail_final_directory)
    with pytest.raises(StudioExportPublishedError) as failure:
        export_studio_arrangement(
            project,
            document,
            take_dir,
            destination_root=destination,
            block_frames=3,
            disk_reserve_bytes=0,
        )

    assert failure.value.folder == destination / "Studio Export"
    assert failure.value.folder.is_dir()
    assert not tuple(destination.glob(".webjam-studio-export-*"))

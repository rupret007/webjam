"""End-to-end schema-3 mixer integration through StudioRenderer."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.song_bounce import (
    SongBounceEngine,
    SongBounceError,
    SongBounceRequest,
)
from core.song_media_catalog import SongMediaCatalog
from core.song_project import MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.studio_mixer import studio_effect_tail_frames
from core.studio_project import (
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioAutomationPoint,
    StudioEffect,
    StudioEffectKind,
    StudioMaster,
    StudioProjectError,
    StudioRegion,
    StudioSend,
    StudioTrack,
    StudioTrackKind,
    default_song_studio_document,
)
from core.studio_renderer import StudioRenderError, StudioRenderer


def _id(number: int) -> str:
    return str(uuid.UUID(int=number))


def _project(
    tmp_path: Path,
    *,
    frames: int = 4_096,
) -> tuple[Path, object, object, SongMediaCatalog]:
    phase = np.arange(frames, dtype=np.float32)
    audio = np.column_stack(
        (
            np.sin(phase * np.float32(0.019)) * np.float32(0.45),
            np.cos(phase * np.float32(0.013)) * np.float32(0.35),
        )
    ).astype(np.float32)
    source = tmp_path / "private reference.wav"
    sf.write(source, audio, 48_000, subtype="FLOAT")
    bundle = tmp_path / "Mixer Renderer.webjam"
    created = create_project_bundle(bundle, name="Mixer Renderer")
    imported = import_project_media(
        bundle,
        created.project,
        source,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    project = imported.project.designate_backing_media(imported.media.media_id)
    saved = save_project_bundle(
        bundle,
        project,
        expected_token=created.token,
    )
    document = default_song_studio_document(saved.project)
    return (
        bundle,
        saved.project,
        document,
        SongMediaCatalog.load(saved.project, bundle),
    )


def _mixed_document(document):
    source = replace(
        document.tracks[0],
        output_bus_id=_id(20),
        automation=(
            StudioAutomationLane(
                lane_id=_id(40),
                parameter=StudioAutomationParameter.VOLUME,
                points=(
                    StudioAutomationPoint(0, 0.2),
                    StudioAutomationPoint(1_000, 1.0),
                ),
            ),
        ),
        sends=(
            StudioSend(
                send_id=_id(50),
                target_bus_id=_id(21),
                gain=0.25,
            ),
        ),
    )
    bus = StudioTrack(
        track_id=_id(20),
        order=1,
        name="Band Bus",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(
            StudioEffect(
                effect_id=_id(60),
                kind=StudioEffectKind.HPF,
                hpf_frequency_hz=90.0,
            ),
            StudioEffect(
                effect_id=_id(61),
                kind=StudioEffectKind.EQ,
                eq_frequency_hz=1_200.0,
                eq_gain_db=2.0,
            ),
            StudioEffect(
                effect_id=_id(62),
                kind=StudioEffectKind.COMPRESSOR,
                compressor_threshold_db=-22.0,
                compressor_ratio=3.0,
            ),
        ),
    )
    reverb = StudioTrack(
        track_id=_id(21),
        order=2,
        name="Shared Reverb",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(
            StudioEffect(
                effect_id=_id(63),
                kind=StudioEffectKind.REVERB,
                reverb_mix=0.4,
                reverb_decay=0.35,
                reverb_delay_ms=5.0,
            ),
        ),
    )
    master_track = StudioTrack(
        track_id=_id(22),
        order=3,
        name="Mix",
        kind=StudioTrackKind.MASTER,
        channel_count=2,
        effects=(
            StudioEffect(
                effect_id=_id(64),
                kind=StudioEffectKind.GATE,
                gate_threshold_db=-90.0,
            ),
        ),
    )
    return replace(
        document,
        tracks=(source, bus, reverb, master_track),
        master=StudioMaster(gain=0.9, limiter_enabled=False),
    )


def test_renderer_and_iterator_share_automation_bus_dsp_and_reverb_tail(
    tmp_path: Path,
) -> None:
    bundle, project, original, catalog = _project(tmp_path)
    document = _mixed_document(original)
    tail = studio_effect_tail_frames(document)
    assert tail > 0
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=257,
    )
    assert renderer.timeline_end_frame == original.regions[0].timeline_end_frame + tail
    assert renderer.stem_semantics == (
        "selected-source-through-shared-routing-and-master"
    )

    small = np.concatenate(tuple(renderer.iter_blocks(block_frames=73)))
    large = np.concatenate(
        tuple(
            StudioRenderer(
                project,
                document,
                bundle,
                source_catalog=catalog,
                block_frames=2_048,
            ).iter_blocks(block_frames=2_048)
        )
    )
    np.testing.assert_array_equal(small, large)
    assert small.shape == (renderer.timeline_end_frame, 2)
    assert np.all(np.isfinite(small))
    assert float(np.max(np.abs(small[-tail:]))) > 0.0


def test_random_access_preroll_matches_full_stateful_mix(tmp_path: Path) -> None:
    bundle, project, original, catalog = _project(tmp_path)
    document = _mixed_document(original)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=127,
    )
    complete = np.concatenate(tuple(renderer.iter_blocks(block_frames=127)))
    random_access = renderer.render_block(777, 333)
    np.testing.assert_array_equal(random_access, complete[777:1_110])

    with renderer.open(
        start_frame=777,
        end_frame=1_110,
        realtime_safe=True,
    ) as stream:
        prepared = stream.read(333)
    np.testing.assert_array_equal(prepared, random_access)


def test_read_with_tracks_exposes_channel_strips_and_explicit_source_stems(
    tmp_path: Path,
) -> None:
    bundle, project, original, catalog = _project(tmp_path, frames=512)
    document = _mixed_document(original)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
    )
    assert renderer.track_ids == (document.tracks[0].track_id,)
    with renderer.open(end_frame=128) as stream:
        mix, tracks = stream.read_with_tracks(128)
    assert tuple(tracks) == tuple(track.track_id for track in document.tracks)
    assert all(value.shape == (128, 2) for value in tracks.values())
    assert float(np.max(np.abs(mix))) > 0.0

    stem = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        track_ids=(document.tracks[0].track_id,),
    )
    assert stem.track_ids == (document.tracks[0].track_id,)
    assert stem.stem_semantics == (
        "selected-source-through-shared-routing-and-master"
    )
    assert float(np.max(np.abs(stem.render_block(0, 128)))) > 0.0
    with pytest.raises(StudioRenderError, match="source tracks"):
        StudioRenderer(
            project,
            document,
            bundle,
            source_catalog=catalog,
            track_ids=(document.tracks[1].track_id,),
        )


def test_renderer_stateful_path_preserves_cancellation_exception(
    tmp_path: Path,
) -> None:
    bundle, project, original, catalog = _project(tmp_path)
    document = _mixed_document(original)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=64,
    )
    renderer.validate_media()
    calls = 0

    class Cancelled(RuntimeError):
        pass

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 8:
            raise Cancelled("stop shared mixer")

    with pytest.raises(Cancelled, match="stop shared mixer"):
        tuple(
            renderer.iter_blocks(
                start_frame=1_000,
                end_frame=1_256,
                block_frames=64,
                cancel_check=cancel,
            )
        )
    assert calls >= 8

    baseline = renderer.render_block(0, 128)
    armed = False
    remaining = 0

    def cancel_once_after_partial_dsp() -> None:
        nonlocal remaining
        if not armed:
            return
        remaining -= 1
        if remaining == 0:
            raise Cancelled("cancel one block")

    with renderer.open(
        start_frame=0,
        end_frame=128,
        cancel_check=cancel_once_after_partial_dsp,
    ) as stream:
        armed = True
        remaining = 4
        with pytest.raises(Cancelled, match="cancel one block"):
            stream.read(128)
        armed = False
        retried = stream.read(128)
    np.testing.assert_array_equal(retried, baseline)


def test_dense_effect_graph_is_offline_only_at_renderer_boundary(
    tmp_path: Path,
) -> None:
    bundle, project, original, catalog = _project(tmp_path, frames=128)
    document = _mixed_document(original)
    kinds = (
        StudioEffectKind.HPF,
        StudioEffectKind.EQ,
        StudioEffectKind.COMPRESSOR,
        StudioEffectKind.GATE,
    )
    extras = tuple(
        StudioTrack(
            track_id=_id(90 + index),
            order=4 + index,
            name=f"Offline FX {index}",
            effects=tuple(
                StudioEffect(
                    effect_id=_id(100 + index * len(kinds) + offset),
                    kind=kind,
                )
                for offset, kind in enumerate(kinds)
            ),
        )
        for index in range(2)
    )
    dense = replace(document, tracks=(*document.tracks, *extras))
    renderer = StudioRenderer(
        project,
        dense,
        bundle,
        source_catalog=catalog,
    )
    with pytest.raises(StudioRenderError, match="offline bounce"):
        renderer.open(end_frame=64, realtime_safe=True)
    assert float(np.max(np.abs(renderer.render_block(0, 64)))) > 0.0


def test_bus_and_master_tracks_cannot_own_regions(tmp_path: Path) -> None:
    _bundle, _project_value, original, _catalog = _project(tmp_path)
    bus = StudioTrack(
        _id(20),
        order=1,
        name="Bus",
        kind=StudioTrackKind.BUS,
        channel_count=2,
    )
    source_region = original.regions[0]
    with pytest.raises(StudioProjectError, match="cannot contain source regions"):
        replace(
            original,
            tracks=(original.tracks[0], bus),
            regions=(
                StudioRegion(
                    region_id=_id(80),
                    track_id=bus.track_id,
                    source_media_id=source_region.source_media_id,
                    source_frame_count=source_region.source_frame_count,
                    timeline_frame_count=source_region.timeline_frame_count,
                ),
            ),
        )


def test_bounce_stems_select_sources_and_keep_shared_nodes_in_signal_flow(
    tmp_path: Path,
) -> None:
    bundle, project, original, catalog = _project(tmp_path, frames=512)
    document = _mixed_document(original)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=97,
    )
    engine = SongBounceEngine()
    result = engine.bounce(
        renderer,
        SongBounceRequest(
            destination=tmp_path / "Routed Mix.wav",
            create_stems=True,
            block_frames=97,
            disk_reserve_bytes=0,
        ),
        generation=engine.begin(),
    )
    assert result.selected_track_ids == (document.tracks[0].track_id,)
    assert len(result.stems) == 1
    assert result.stems[0].track_id == document.tracks[0].track_id
    assert result.mix.path.exists()
    assert result.stems[0].path.exists()

    bus_engine = SongBounceEngine()
    with pytest.raises(SongBounceError, match="non-source"):
        bus_engine.bounce(
            renderer,
            SongBounceRequest(
                destination=tmp_path / "Invalid Bus.wav",
                track_ids=(document.tracks[1].track_id,),
                disk_reserve_bytes=0,
            ),
            generation=bus_engine.begin(),
        )

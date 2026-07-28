"""Shared-renderer coverage for standalone schema-3 song projects."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.song_media_catalog import SongMediaCatalog
from core.song_project import MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.studio_project import default_song_studio_document
from core.studio_renderer import StudioRenderError, StudioRenderer, iter_studio_blocks


def _project(
    tmp_path: Path,
    *,
    source_rate: int = 44_100,
    channels: int = 2,
    frames: int = 4_410,
) -> tuple[Path, object, object, np.ndarray]:
    phase = np.arange(frames, dtype=np.float32)
    left = np.sin(phase * np.float32(0.013)) * np.float32(0.4)
    if channels == 1:
        audio = left[:, np.newaxis]
    else:
        right = np.cos(phase * np.float32(0.019)) * np.float32(0.25)
        audio = np.column_stack((left, right)).astype(np.float32)
    source = tmp_path / "private source with spaces.wav"
    sf.write(source, audio, source_rate, subtype="FLOAT")
    bundle = tmp_path / "Renderer Song.webjam"
    created = create_project_bundle(bundle, name="Renderer Song")
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
    return bundle, saved.project, document, audio


def test_song_renderer_uses_sealed_catalog_and_exact_project_duration(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        block_frames=257,
    )

    expected_frames = document.regions[0].timeline_frame_count
    assert renderer.sample_rate == project.project_sample_rate
    assert renderer.timeline_start_frame == 0
    assert renderer.timeline_end_frame == expected_frames
    assert renderer.total_frames == expected_frames
    assert renderer.track_ids == (document.tracks[0].track_id,)
    rendered = np.concatenate(tuple(renderer.iter_blocks(block_frames=257)))
    assert rendered.shape == (expected_frames, 2)
    assert rendered.dtype == np.float32
    assert np.all(np.isfinite(rendered))
    assert float(np.max(np.abs(rendered))) > 0.2


def test_song_renderer_random_access_and_iterator_share_one_path(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        frames=1_024,
    )
    catalog = SongMediaCatalog.load(project, bundle)
    renderer = StudioRenderer(project, document, bundle, source_catalog=catalog)
    block = renderer.render_block(100, 300)
    iterator = np.concatenate(
        tuple(
            iter_studio_blocks(
                project,
                document,
                bundle,
                start_frame=100,
                end_frame=400,
                block_frames=127,
                source_catalog=catalog,
            )
        )
    )
    np.testing.assert_array_equal(block, iterator)


def test_song_renderer_applies_shared_track_pan_gain_mute_and_master(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        frames=512,
    )
    catalog = SongMediaCatalog.load(project, bundle)
    baseline = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
    ).render_block(0, 512)

    track = replace(document.tracks[0], trim_gain=0.5, fader_gain=0.5, pan=1.0)
    master = replace(document.master, gain=0.5, limiter_enabled=False)
    mixed_document = replace(document, tracks=(track,), master=master)
    mixed = StudioRenderer(
        project,
        mixed_document,
        bundle,
        source_catalog=catalog,
    ).render_block(0, 512)
    np.testing.assert_allclose(mixed[:, 0], 0.0, atol=1e-7)
    np.testing.assert_allclose(mixed[:, 1], baseline[:, 1] * 0.125, atol=1e-6)

    muted_document = replace(
        document,
        tracks=(replace(document.tracks[0], muted=True),),
    )
    muted = StudioRenderer(
        project,
        muted_document,
        bundle,
        source_catalog=catalog,
    ).render_block(0, 512)
    np.testing.assert_array_equal(muted, np.zeros_like(muted))


def test_song_renderer_applies_region_fades_through_existing_dsp(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        channels=1,
        frames=1_000,
    )
    region = replace(
        document.regions[0],
        fade_in_frames=100,
        fade_out_frames=100,
    )
    faded_document = replace(document, regions=(region,))
    catalog = SongMediaCatalog.load(project, bundle)
    rendered = StudioRenderer(
        project,
        faded_document,
        bundle,
        source_catalog=catalog,
    ).render_block(0, 1_000)
    assert rendered[0, 0] == pytest.approx(0.0)
    assert rendered[-1, 0] == pytest.approx(0.0)
    assert np.max(np.abs(rendered[200:800])) > 0.2
    np.testing.assert_array_equal(rendered[:, 0], rendered[:, 1])


def test_song_renderer_supports_track_stems_and_export_inclusion(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        frames=256,
    )
    catalog = SongMediaCatalog.load(project, bundle)
    excluded = replace(
        document,
        tracks=(replace(document.tracks[0], export_included=False),),
    )
    omitted = StudioRenderer(
        project,
        excluded,
        bundle,
        source_catalog=catalog,
        respect_export_included=True,
    ).render_block(0, 256)
    np.testing.assert_array_equal(omitted, np.zeros_like(omitted))
    selected = StudioRenderer(
        project,
        document,
        bundle,
        source_catalog=catalog,
        track_ids=(document.tracks[0].track_id,),
    ).render_block(0, 256)
    assert float(np.max(np.abs(selected))) > 0.2


def test_song_renderer_refuses_wrong_schema_identity_or_catalog(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    with pytest.raises(StudioRenderError, match="trusted SongMediaCatalog"):
        StudioRenderer(project, document, bundle)
    changed_project = replace(project, project_id="00000000-0000-0000-0000-000000000099")
    with pytest.raises(StudioRenderError, match="different song project"):
        StudioRenderer(
            changed_project,
            document,
            bundle,
            source_catalog=catalog,
        )


def test_song_renderer_detects_media_replacement_before_audio_is_returned(
    tmp_path: Path,
) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        frames=512,
    )
    catalog = SongMediaCatalog.load(project, bundle)
    renderer = StudioRenderer(project, document, bundle, source_catalog=catalog)
    member = bundle / project.media[0].path
    replacement = tmp_path / "replacement.wav"
    sf.write(
        replacement,
        np.zeros((512, 2), dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )
    replacement.replace(member)
    with pytest.raises(StudioRenderError, match="changed|replaced"):
        renderer.open()


def test_song_renderer_can_reuse_exact_catalog_validation(tmp_path: Path) -> None:
    bundle, project, document, _source = _project(
        tmp_path,
        source_rate=48_000,
        frames=512,
    )
    catalog = SongMediaCatalog.load(project, bundle)
    first = StudioRenderer(project, document, bundle, source_catalog=catalog)
    first.validate_media()
    second = StudioRenderer(project, document, bundle, source_catalog=catalog)
    second.reuse_media_validation(first)
    rendered = second.render_block(0, 512)
    assert float(np.max(np.abs(rendered))) > 0.2

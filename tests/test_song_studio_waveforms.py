"""Waveform activation coverage for sealed schema-3 song media."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import time

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
from core.studio_waveform import StudioWaveformError, WaveformSource
from webjam_qt.widgets.studio_waveforms import (
    StudioWaveformCoordinator,
    StudioWaveformCoordinatorError,
    StudioWaveformRegionError,
    StudioWaveformRegionTile,
)


def _fixture(tmp_path: Path):
    source = tmp_path / "reference with spaces.wav"
    sf.write(
        source,
        np.linspace(-0.8, 0.8, 2_400, dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )
    bundle = tmp_path / "Waveform Song.webjam"
    created = create_project_bundle(bundle, name="Waveform Song")
    imported = import_project_media(
        bundle,
        created.project,
        source,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    project = imported.project.designate_backing_media(imported.media.media_id)
    saved = save_project_bundle(bundle, project, expected_token=created.token)
    document = default_song_studio_document(saved.project)
    catalog = SongMediaCatalog.load(saved.project, bundle)
    return bundle, saved.project, document, catalog


def test_waveform_source_binds_song_catalog_without_path_in_cache_identity(
    tmp_path: Path,
) -> None:
    bundle, project, _document, catalog = _fixture(tmp_path)
    media = project.media[0]
    source = WaveformSource.from_song_catalog_source(catalog, media.media_id)

    assert source.source_id == media.media_id
    assert source.frame_count == media.frame_count
    assert source.sample_rate == media.sample_rate
    assert source.channels == media.channels
    assert source.catalog_key == (project.project_id, "media", media.media_id)
    assert source.trusted_root == bundle.resolve()
    assert str(bundle) not in repr(source.identity)


def test_waveform_coordinator_activates_and_publishes_song_region_tiles(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, catalog = _fixture(tmp_path)
    tiles: list[StudioWaveformRegionTile] = []
    errors: list[StudioWaveformRegionError] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        coordinator = StudioWaveformCoordinator(
            executor,
            publish_tile=tiles.append,
            publish_error=errors.append,
        )
        assert coordinator.activate(document, catalog) == 1
        region = document.regions[0]
        generation = coordinator.schedule(
            0,
            region.timeline_end_frame,
            0.5,
            (region.region_id,),
        )
        for _ in range(200):
            if coordinator.stats.pending == 0 and coordinator.stats.in_flight == 0:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("waveform work did not quiesce")
    assert coordinator.drain() > 0
    assert not errors
    assert tiles
    assert {item.generation for item in tiles} == {generation}
    assert {item.region_id for item in tiles} == {region.region_id}
    assert all(item.tile.minimum.shape[1] == 1 for item in tiles)


def test_waveform_coordinator_rejects_project_identity_mismatch(
    tmp_path: Path,
) -> None:
    _bundle, project, document, catalog = _fixture(tmp_path)
    wrong = replace(
        document,
        project_id="00000000-0000-0000-0000-000000000099",
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        coordinator = StudioWaveformCoordinator(
            executor,
            publish_tile=lambda _value: None,
            publish_error=lambda _value: None,
        )
        with pytest.raises(StudioWaveformCoordinatorError, match="do not match"):
            coordinator.activate(wrong, catalog)
    assert project.project_id != wrong.project_id


def test_song_waveform_refuses_replaced_media(tmp_path: Path) -> None:
    bundle, project, document, catalog = _fixture(tmp_path)
    member = bundle / project.media[0].path
    replacement = tmp_path / "new.wav"
    sf.write(
        replacement,
        np.zeros((2_400, 1), dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )
    replacement.replace(member)
    with pytest.raises(StudioWaveformError, match="changed"):
        WaveformSource.from_song_catalog_source(catalog, project.media[0].media_id)
    with ThreadPoolExecutor(max_workers=1) as executor:
        coordinator = StudioWaveformCoordinator(
            executor,
            publish_tile=lambda _value: None,
            publish_error=lambda _value: None,
        )
        with pytest.raises(StudioWaveformCoordinatorError, match="changed"):
            coordinator.activate(document, catalog)

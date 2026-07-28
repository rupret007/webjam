from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

from core.song_project import MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.song_studio_reconcile import reconcile_song_studio_document
from core.studio_project import (
    StudioEffect,
    StudioEffectKind,
    StudioSend,
    StudioTrack,
    StudioTrackKind,
    default_song_studio_document,
)


def _project(tmp_path: Path):
    bundle = tmp_path / "Reconcile Song.webjam"
    created = create_project_bundle(bundle, "Reconcile Song")
    project = created.project.add_track("Voice")
    saved = save_project_bundle(bundle, project, expected_token=created.token)
    return bundle, saved


def test_add_and_rename_track_preserves_existing_mix(tmp_path: Path) -> None:
    _bundle, saved = _project(tmp_path)
    document = default_song_studio_document(saved.project)
    voice = document.tracks[0]
    edited = document.update_track(voice.track_id, fader_gain=0.42, pan=-0.3)
    changed = replace(
        saved.project.tracks[0],
        name="Lead Voice",
        armed=True,
    )
    project = replace(
        saved.project,
        tracks=(
            changed,
            saved.project.add_track("Guitar").tracks[-1],
        ),
        revision=saved.project.revision + 1,
    )

    reconciled = reconcile_song_studio_document(project, edited)

    assert [item.name for item in reconciled.tracks] == ["Lead Voice", "Guitar"]
    assert reconciled.tracks[0].fader_gain == 0.42
    assert reconciled.tracks[0].pan == -0.3
    assert reconciled.tracks[0].armed is True


def test_import_backing_adds_first_class_track_without_resetting_audio(
    tmp_path: Path,
) -> None:
    bundle, saved = _project(tmp_path)
    document = default_song_studio_document(saved.project)
    voice_id = document.tracks[0].track_id
    document = document.update_track(voice_id, muted=True)
    source = tmp_path / "backing with spaces.wav"
    sf.write(source, np.zeros((4_800, 2), dtype=np.float32), 48_000)
    imported = import_project_media(
        bundle,
        saved.project,
        source,
        designate_backing=True,
        provenance=MediaProvenance.LOCAL_FILE,
    )

    reconciled = reconcile_song_studio_document(imported.project, document)

    assert reconciled.tracks[0].kind is StudioTrackKind.BACKING
    assert reconciled.tracks[1].track_id == voice_id
    assert reconciled.tracks[1].muted is True
    assert len(reconciled.regions) == 1
    assert reconciled.regions[0].source_media_id == imported.media.media_id


def test_backing_replacement_tombstones_prior_region(tmp_path: Path) -> None:
    bundle, saved = _project(tmp_path)
    first = tmp_path / "one.wav"
    second = tmp_path / "two.wav"
    sf.write(first, np.zeros(2_400, dtype=np.float32), 48_000)
    sf.write(second, np.zeros(4_800, dtype=np.float32), 48_000)
    first_import = import_project_media(
        bundle,
        saved.project,
        first,
        designate_backing=True,
    )
    first_document = default_song_studio_document(first_import.project)
    second_import = import_project_media(
        bundle,
        first_import.project,
        second,
        designate_backing=True,
    )

    reconciled = reconcile_song_studio_document(
        second_import.project,
        first_document,
    )

    active = [item for item in reconciled.regions if item.enabled and not item.deleted]
    old = [
        item
        for item in reconciled.regions
        if item.source_media_id == first_import.media.media_id
    ]
    assert len(active) == 1
    assert active[0].source_media_id == second_import.media.media_id
    assert old and old[0].deleted and not old[0].enabled


def test_reconcile_preserves_studio_owned_bus_master_and_source_routes(
    tmp_path: Path,
) -> None:
    _bundle, saved = _project(tmp_path)
    document = default_song_studio_document(saved.project)
    voice = document.tracks[0]
    bus_id = "20000000-0000-0000-0000-000000000001"
    master_id = "20000000-0000-0000-0000-000000000002"
    bus = StudioTrack(
        track_id=bus_id,
        order=1,
        name="Shared Space",
        kind=StudioTrackKind.BUS,
        channel_count=2,
        effects=(
            StudioEffect(
                effect_id="30000000-0000-0000-0000-000000000001",
                kind=StudioEffectKind.REVERB,
            ),
        ),
    )
    master = StudioTrack(
        track_id=master_id,
        order=2,
        name="Mix",
        kind=StudioTrackKind.MASTER,
        channel_count=2,
    )
    routed_voice = replace(
        voice,
        output_bus_id=bus_id,
        sends=(
            StudioSend(
                send_id="40000000-0000-0000-0000-000000000001",
                target_bus_id=bus_id,
                gain=0.2,
            ),
        ),
    )
    routed = replace(document, tracks=(routed_voice, bus, master))
    project = saved.project.add_track("Guitar")

    reconciled = reconcile_song_studio_document(project, routed)

    assert [item.kind for item in reconciled.tracks] == [
        StudioTrackKind.AUDIO,
        StudioTrackKind.AUDIO,
        StudioTrackKind.BUS,
        StudioTrackKind.MASTER,
    ]
    assert [item.order for item in reconciled.tracks] == [0, 1, 2, 3]
    assert reconciled.state_for(voice.track_id).output_bus_id == bus_id
    assert reconciled.state_for(voice.track_id).sends == routed_voice.sends
    assert reconciled.state_for(bus_id).effects == bus.effects
    assert reconciled.state_for(master_id) == replace(master, order=3)

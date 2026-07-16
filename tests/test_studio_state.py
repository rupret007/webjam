"""Focused coverage for the non-destructive schema-v2 Studio sidecar."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from core.file_io import atomic_write_text
from core.studio_state import (
    STUDIO_STATE_FILENAME,
    StudioStateError,
    StudioTrackState,
    load_studio_state,
    save_studio_state,
    studio_state_path,
)
from core.take_project import (
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    new_project_id,
    write_take_project,
)


def _project(
    *,
    session_id: str,
    take_id: str,
    track_ids: tuple[str, ...],
) -> TakeProject:
    tracks = tuple(
        ProjectTrack(
            track_id=track_id,
            source_id=new_project_id(),
            participant_id=None,
            name=f"Track {index + 1}",
            instrument="",
            source_type=SourceType.JAMULUS_SERVER,
            quality=SourceQuality.NETWORK_TRACK,
            media_status=MediaStatus.AVAILABLE,
            order=index,
            segments=(
                MediaSegment(
                    segment_id=new_project_id(),
                    path=f"media/{track_id}.wav",
                    project_start_frame=0,
                    frame_count=16,
                    sample_rate=48000,
                    channels=1,
                    sample_format="PCM_24",
                    sha256=hashlib.sha256(track_id.encode("utf-8")).hexdigest(),
                ),
            ),
        )
        for index, track_id in enumerate(track_ids)
    )
    return TakeProject(
        session_id=session_id,
        take_id=take_id,
        session_title="Band Rehearsal",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48000,
        participants=(),
        tracks=tracks,
    )


def _make_take(tmp_path: Path, track_ids: tuple[str, ...]) -> tuple[Path, TakeProject]:
    take_dir = tmp_path / "Take 01"
    media = take_dir / "media"
    media.mkdir(parents=True)
    for track_id in track_ids:
        (media / f"{track_id}.wav").write_bytes(
            b"immutable recording bytes: " + track_id.encode("ascii")
        )
    project = _project(
        session_id=new_project_id(),
        take_id=new_project_id(),
        track_ids=track_ids,
    )
    write_take_project(take_dir, project)
    return take_dir, project


def test_load_defaults_are_keyed_by_durable_track_id_and_never_write_take(tmp_path: Path):
    track_ids = (new_project_id(), new_project_id())
    take_dir, project = _make_take(tmp_path, track_ids)
    manifest = take_dir / "webjam-take.json"
    audio = take_dir / "media" / f"{track_ids[0]}.wav"
    manifest_before = manifest.read_bytes()
    audio_before = audio.read_bytes()

    state = load_studio_state(take_dir)

    assert state.session_id == project.session_id
    assert state.take_id == project.take_id
    assert tuple(item.track_id for item in state.tracks) == track_ids
    assert state.state_for(track_ids[0]) == StudioTrackState(track_ids[0])
    assert not studio_state_path(take_dir).exists()
    assert manifest.read_bytes() == manifest_before
    assert audio.read_bytes() == audio_before


def test_save_load_round_trip_is_private_atomic_sidecar_and_leaves_take_untouched(
    tmp_path: Path,
):
    track_ids = (new_project_id(), new_project_id())
    take_dir, _project_value = _make_take(tmp_path, track_ids)
    manifest = take_dir / "webjam-take.json"
    audio = take_dir / "media" / f"{track_ids[1]}.wav"
    manifest_before = manifest.read_bytes()
    audio_before = audio.read_bytes()
    changed = load_studio_state(take_dir).update_track(
        track_ids[1],
        gain=1.27,
        pan=-0.4,
        muted=True,
        solo=True,
        export_included=False,
    )

    with patch(
        "core.studio_state.atomic_write_text",
        wraps=atomic_write_text,
    ) as atomic_write:
        sidecar = save_studio_state(take_dir, changed)

    assert sidecar == take_dir / STUDIO_STATE_FILENAME
    atomic_write.assert_called_once_with(sidecar, ANY, mode=0o600)
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["take_id"] == changed.take_id
    assert payload["session_id"] == changed.session_id
    assert payload["tracks"][1] == {
        "track_id": track_ids[1],
        "gain": 1.27,
        "pan": -0.4,
        "muted": True,
        "solo": True,
        "export_included": False,
    }
    assert load_studio_state(take_dir) == changed
    assert manifest.read_bytes() == manifest_before
    assert audio.read_bytes() == audio_before


def test_reconciles_added_and_reordered_tracks_by_id_without_positional_carryover(
    tmp_path: Path,
):
    first_id, second_id, added_id = (new_project_id(), new_project_id(), new_project_id())
    take_dir, project = _make_take(tmp_path, (first_id, second_id))
    saved = load_studio_state(take_dir)
    saved = saved.update_track(first_id, gain=0.55, pan=-0.5, export_included=False)
    saved = saved.update_track(second_id, gain=1.2, muted=True, solo=True)
    save_studio_state(take_dir, saved)

    # The recorder can publish a later schema-v2 revision with tracks in a
    # different display order and a newly reconciled participant.  State must
    # follow IDs, not old lane positions.
    (take_dir / "media" / f"{added_id}.wav").write_bytes(b"new immutable audio")
    revised = _project(
        session_id=project.session_id,
        take_id=project.take_id,
        track_ids=(added_id, second_id, first_id),
    )
    revised = replace(revised, revision=project.revision + 1)
    write_take_project(take_dir, revised)

    loaded = load_studio_state(take_dir)

    assert tuple(item.track_id for item in loaded.tracks) == (
        added_id,
        second_id,
        first_id,
    )
    assert loaded.state_for(first_id).gain == pytest.approx(0.55)
    assert loaded.state_for(first_id).pan == pytest.approx(-0.5)
    assert loaded.state_for(first_id).export_included is False
    assert loaded.state_for(second_id).gain == pytest.approx(1.2)
    assert loaded.state_for(second_id).muted is True
    assert loaded.state_for(second_id).solo is True
    assert loaded.state_for(added_id) == StudioTrackState(added_id)


@pytest.mark.parametrize(
    "payload",
    (
        "not JSON",
        json.dumps(
            {
                "schema_version": 1,
                "session_id": new_project_id(),
                "take_id": new_project_id(),
                "tracks": [],
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "session_id": new_project_id(),
                "take_id": new_project_id(),
                "tracks": [
                    {
                        "track_id": new_project_id(),
                        "gain": 99,
                        "pan": 0,
                        "muted": False,
                        "solo": False,
                        "export_included": True,
                    }
                ],
            }
        ),
    ),
)
def test_malformed_or_unmatched_sidecar_is_rejected_without_rewriting_it(
    tmp_path: Path,
    payload: str,
):
    take_dir, _project_value = _make_take(tmp_path, (new_project_id(),))
    sidecar = studio_state_path(take_dir)
    sidecar.write_text(payload, encoding="utf-8")
    before = sidecar.read_bytes()

    with pytest.raises(StudioStateError):
        load_studio_state(take_dir)

    assert sidecar.read_bytes() == before


def test_save_rejects_a_state_from_another_take_and_v1_manifests(tmp_path: Path):
    first_dir, _first_project = _make_take(tmp_path / "first", (new_project_id(),))
    second_dir, _second_project = _make_take(tmp_path / "second", (new_project_id(),))

    with pytest.raises(StudioStateError, match="different take"):
        save_studio_state(first_dir, load_studio_state(second_dir))
    assert not studio_state_path(first_dir).exists()

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "webjam-take.json").write_text(
        json.dumps({"schema_version": 1, "files": []}), encoding="utf-8"
    )
    with pytest.raises(StudioStateError, match="schema-v2"):
        load_studio_state(legacy_dir)


def test_symlinked_sidecar_is_never_followed_or_replaced(tmp_path: Path):
    take_dir, _project_value = _make_take(tmp_path, (new_project_id(),))
    state = load_studio_state(take_dir)
    target = tmp_path / "outside-state.json"
    target.write_text("outside data", encoding="utf-8")
    sidecar = studio_state_path(take_dir)
    sidecar.symlink_to(target)

    with pytest.raises(StudioStateError, match="symbolic link"):
        load_studio_state(take_dir)
    with pytest.raises(StudioStateError, match="symbolic link"):
        save_studio_state(take_dir, state)

    assert sidecar.is_symlink()
    assert target.read_text(encoding="utf-8") == "outside data"


def test_track_updates_are_bounded_and_do_not_allow_unknown_lanes(tmp_path: Path):
    track_id = new_project_id()
    take_dir, _project_value = _make_take(tmp_path, (track_id,))
    state = load_studio_state(take_dir)

    with pytest.raises(StudioStateError, match="between 0 and 4"):
        state.update_track(track_id, gain=4.01)
    with pytest.raises(StudioStateError, match="between -1 and 1"):
        state.update_track(track_id, pan=-1.01)
    with pytest.raises(StudioStateError, match="must be true or false"):
        state.update_track(track_id, muted=1)
    with pytest.raises(StudioStateError, match="not part"):
        state.update_track(new_project_id(), gain=0.5)

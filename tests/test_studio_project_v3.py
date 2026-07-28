"""Schema-3 standalone song-project coverage for the Studio edit model."""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from core.song_project import (
    InputMapping,
    MediaImportMethod,
    MediaProvenance,
    SongMedia,
    SongProject,
    SongTrack,
)
from core.studio_history import StudioHistory
from core.studio_project import (
    STUDIO_PROJECT_SCHEMA_VERSION,
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioDocument,
    StudioProjectError,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
    StudioTrackKind,
    default_song_studio_document,
    studio_document_from_dict,
)


def _id(number: int) -> str:
    return str(uuid.UUID(int=number))


def _media(
    number: int,
    *,
    sample_rate: int = 44_100,
    channels: int = 2,
    frame_count: int = 44_101,
) -> SongMedia:
    return SongMedia(
        media_id=_id(number),
        path=f"Media/{_id(number)}.wav",
        sha256=f"{number:064x}",
        size_bytes=1,
        sample_rate=sample_rate,
        channels=channels,
        frame_count=frame_count,
        format="WAV",
        original_basename="private reference name.wav",
        provenance=MediaProvenance.LOCAL_FILE,
        import_method=MediaImportMethod.COPY,
    )


def _song_with_backing() -> SongProject:
    backing = _media(20)
    return SongProject(
        project_id=_id(1),
        name="Schema Three Song",
        project_sample_rate=48_000,
        tracks=(
            SongTrack(
                track_id=_id(10),
                name="Lead Vocal",
                order=0,
                input_mapping=InputMapping("portable-input", (1, 2)),
                armed=True,
                input_monitoring=True,
            ),
            SongTrack(
                track_id=_id(11),
                name="Guitar",
                order=1,
            ),
        ),
        media=(backing,),
        backing_media_id=backing.media_id,
    )


def _round_ratio(value: int, numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(value * numerator, denominator)
    return quotient + int(remainder * 2 >= denominator)


def test_schema2_dictionary_shape_and_parser_remain_exactly_legacy() -> None:
    track = StudioTrack(track_id=_id(10))
    region = StudioRegion(
        region_id=_id(20),
        track_id=track.track_id,
        source_take_id=_id(2),
        source_track_id=track.track_id,
        source_segment_id=_id(21),
        source_start_frame=0,
        source_frame_count=1_000,
        timeline_start_frame=10,
        timeline_frame_count=1_000,
    )
    document = StudioDocument(
        session_id=_id(1),
        take_id=_id(2),
        project_sample_rate=48_000,
        tracks=(track,),
        regions=(region,),
    )

    payload = document.to_dict()
    assert document.schema_version == STUDIO_PROJECT_SCHEMA_VERSION
    assert tuple(payload) == (
        "schema_version",
        "revision",
        "session_id",
        "take_id",
        "project_sample_rate",
        "snap_mode",
        "tracks",
        "regions",
        "take_lanes",
        "comp_ranges",
        "markers",
        "crossfades",
        "cycle_range",
        "master",
    )
    assert payload["tracks"] == [
        {
            "track_id": track.track_id,
            "order": 0,
            "trim_gain": 1.0,
            "fader_gain": 1.0,
            "pan": 0.0,
            "muted": False,
            "solo": False,
            "export_included": True,
        }
    ]
    assert tuple(payload["regions"][0])[:5] == (
        "region_id",
        "track_id",
        "source_take_id",
        "source_track_id",
        "source_segment_id",
    )
    assert "project_id" not in payload
    assert "source_media_id" not in payload["regions"][0]
    assert studio_document_from_dict(copy.deepcopy(payload)) == document
    assert studio_document_from_dict(payload).to_dict() == payload


def test_default_song_document_is_deterministic_path_free_and_media_backed() -> None:
    project = _song_with_backing()
    first = default_song_studio_document(project)
    second = default_song_studio_document(project)

    assert first == second
    assert first.schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION
    assert first.project_id == project.project_id
    assert first.session_id == ""
    assert first.take_id == ""
    assert tuple(track.order for track in first.tracks) == (0, 1, 2)

    backing_track, vocal, guitar = first.tracks
    assert uuid.UUID(backing_track.track_id).version == 5
    assert (
        backing_track.name,
        backing_track.kind,
        backing_track.channel_count,
    ) == ("Backing Track", StudioTrackKind.BACKING, 2)
    assert (vocal.track_id, vocal.name, vocal.kind) == (
        _id(10),
        "Lead Vocal",
        StudioTrackKind.AUDIO,
    )
    assert (vocal.channel_count, vocal.armed, vocal.input_monitoring) == (
        2,
        True,
        True,
    )
    assert (guitar.track_id, guitar.order, guitar.channel_count) == (
        _id(11),
        2,
        1,
    )

    assert len(first.regions) == 1
    region = first.regions[0]
    media = project.media[0]
    assert uuid.UUID(region.region_id).version == 5
    assert region.track_id == backing_track.track_id
    assert region.source_media_id == media.media_id
    assert (region.source_take_id, region.source_track_id, region.source_segment_id) == (
        "",
        "",
        "",
    )
    assert region.source_frame_count == media.frame_count
    assert region.timeline_start_frame == 0
    assert region.timeline_frame_count == _round_ratio(
        media.frame_count,
        project.project_sample_rate,
        media.sample_rate,
    )

    payload = first.to_dict()
    text = json.dumps(payload, sort_keys=True)
    assert "project_id" in payload
    assert "session_id" not in payload
    assert "take_id" not in payload
    assert "source_media_id" in payload["regions"][0]
    assert "source_take_id" not in payload["regions"][0]
    assert "Media/" not in text
    assert "private reference name.wav" not in text
    assert "sha256" not in text
    assert studio_document_from_dict(json.loads(text)) == first


def test_schema3_parser_is_strict_at_root_tracks_and_media_identity() -> None:
    payload = default_song_studio_document(_song_with_backing()).to_dict()

    malformed = copy.deepcopy(payload)
    malformed["session_id"] = _id(99)
    with pytest.raises(StudioProjectError, match="unsupported fields"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed.pop("project_id")
    with pytest.raises(StudioProjectError, match="project_id"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["tracks"][0]["private_path"] = "/private/song.wav"
    with pytest.raises(StudioProjectError, match="unsupported fields"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["tracks"][1].pop("name")
    with pytest.raises(StudioProjectError, match="missing required fields: name"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["regions"][0]["source_take_id"] = _id(90)
    with pytest.raises(StudioProjectError, match="unsupported fields"):
        studio_document_from_dict(malformed)

    malformed = copy.deepcopy(payload)
    malformed["regions"][0].pop("source_media_id")
    with pytest.raises(StudioProjectError, match="source_media_id"):
        studio_document_from_dict(malformed)


def test_schema_versions_forbid_missing_or_ambiguous_source_identity() -> None:
    with pytest.raises(StudioProjectError, match="exactly one"):
        StudioRegion(
            region_id=_id(1),
            track_id=_id(2),
            source_take_id=_id(3),
            source_track_id=_id(2),
            source_segment_id=_id(4),
            source_media_id=_id(5),
            source_frame_count=100,
            timeline_frame_count=100,
        )
    with pytest.raises(StudioProjectError, match="exactly one"):
        StudioRegion(
            region_id=_id(1),
            track_id=_id(2),
            source_frame_count=100,
            timeline_frame_count=100,
        )

    legacy_region = StudioRegion(
        region_id=_id(20),
        track_id=_id(10),
        source_take_id=_id(2),
        source_track_id=_id(10),
        source_segment_id=_id(21),
        source_frame_count=100,
        timeline_frame_count=100,
    )
    with pytest.raises(StudioProjectError, match="only source_media_id"):
        StudioDocument(
            project_id=_id(1),
            tracks=(StudioTrack(_id(10), name="Audio"),),
            regions=(legacy_region,),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )

    media_region = StudioRegion(
        region_id=_id(30),
        track_id=_id(10),
        source_media_id=_id(31),
        source_frame_count=100,
        timeline_frame_count=100,
    )
    with pytest.raises(StudioProjectError, match="take/track/segment"):
        StudioDocument(
            session_id=_id(1),
            take_id=_id(2),
            tracks=(StudioTrack(_id(10)),),
            regions=(media_region,),
        )
    with pytest.raises(StudioProjectError, match="not session/take"):
        StudioDocument(
            session_id=_id(9),
            project_id=_id(1),
            tracks=(StudioTrack(_id(10), name="Audio"),),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )


def test_media_backed_take_lane_survives_atomic_add_split_and_round_trip() -> None:
    track = StudioTrack(
        track_id=_id(10),
        name="Lead Vocal",
        kind=StudioTrackKind.AUDIO,
    )
    base = StudioDocument(
        project_id=_id(1),
        tracks=(track,),
        schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    )
    region = StudioRegion(
        region_id=_id(20),
        track_id=track.track_id,
        source_media_id=_id(30),
        source_start_frame=0,
        source_frame_count=1_000,
        timeline_start_frame=0,
        timeline_frame_count=1_000,
    )
    lane = StudioTakeLane(
        lane_id=_id(40),
        track_id=track.track_id,
        source_media_id=region.source_media_id,
        name="Vocal take 2",
        region_ids=(region.region_id,),
    )

    added = base.upsert_take_lane_with_regions(lane, (region,))
    split = added.split_region(region.region_id, 400, right_region_id=_id(21))
    assert tuple(item.source_media_id for item in split.regions) == (
        _id(30),
        _id(30),
    )
    assert split.take_lanes[0].region_ids == (_id(20), _id(21))
    assert studio_document_from_dict(split.to_dict()) == split

    with pytest.raises(StudioProjectError, match="media ID does not match"):
        StudioDocument(
            project_id=_id(1),
            tracks=(track,),
            regions=(region,),
            take_lanes=(
                StudioTakeLane(
                    lane_id=_id(41),
                    track_id=track.track_id,
                    source_media_id=_id(99),
                    region_ids=(region.region_id,),
                ),
            ),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
    with pytest.raises(StudioProjectError, match="cannot mix"):
        StudioTakeLane(
            lane_id=_id(42),
            track_id=track.track_id,
            source_take_id=_id(2),
            source_track_id=track.track_id,
            source_media_id=_id(30),
        )
    with pytest.raises(StudioProjectError, match="require only source_media_id"):
        StudioDocument(
            project_id=_id(1),
            tracks=(track,),
            take_lanes=(
                StudioTakeLane(
                    lane_id=_id(43),
                    track_id=track.track_id,
                ),
            ),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )


def test_song_track_roles_and_song_only_fields_are_validated() -> None:
    with pytest.raises(StudioProjectError, match="Only audio tracks"):
        StudioTrack(
            _id(1),
            name="Backing",
            kind=StudioTrackKind.BACKING,
            armed=True,
        )
    with pytest.raises(StudioProjectError, match="require a name"):
        StudioDocument(
            project_id=_id(1),
            tracks=(StudioTrack(_id(10)),),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
    with pytest.raises(StudioProjectError, match="unique order"):
        StudioDocument(
            project_id=_id(1),
            tracks=(
                StudioTrack(_id(10), name="One"),
                StudioTrack(_id(11), name="Two"),
            ),
            schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
    with pytest.raises(StudioProjectError, match="song-track fields"):
        StudioDocument(
            session_id=_id(1),
            take_id=_id(2),
            tracks=(StudioTrack(_id(10), name="Not schema 2"),),
        )


def test_large_backing_duration_mapping_is_exact_and_does_not_use_float() -> None:
    frame_count = 1_000_000_000_000_001
    backing = _media(
        20,
        sample_rate=384_000,
        channels=1,
        frame_count=frame_count,
    )
    project = SongProject(
        project_id=_id(1),
        name="Long Form",
        project_sample_rate=8_000,
        media=(backing,),
        backing_media_id=backing.media_id,
    )

    document = default_song_studio_document(project)
    region = document.regions[0]
    assert region.source_frame_count == frame_count
    assert region.timeline_frame_count == _round_ratio(
        frame_count,
        8_000,
        384_000,
    )
    assert region.mapping_source_frame_count == frame_count
    assert region.mapping_timeline_frame_count == region.timeline_frame_count
    assert studio_document_from_dict(document.to_dict()) == document


def test_schema3_common_edits_and_bounded_history_preserve_project_identity() -> None:
    initial = default_song_studio_document(_song_with_backing())
    vocal_id = _id(10)
    backing_region_id = initial.regions[0].region_id
    history = StudioHistory(initial)

    mixed = history.perform(
        "Prepare vocal",
        lambda document: document.update_track(
            vocal_id,
            name="Lead Vocal Main",
            muted=True,
            armed=False,
            input_monitoring=False,
        ),
    )
    moved = history.perform(
        "Move reference",
        lambda document: document.move_region(backing_region_id, 48_000),
    )

    assert mixed.project_id == initial.project_id
    assert moved.project_id == initial.project_id
    assert moved.revision == initial.revision + 2
    assert moved.state_for(vocal_id).name == "Lead Vocal Main"
    assert moved.region_for(backing_region_id).timeline_start_frame == 48_000
    assert history.undo() is mixed
    assert history.undo() is initial
    assert history.redo() is mixed
    assert history.redo() is moved

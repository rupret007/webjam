from __future__ import annotations

import copy
import hashlib
import math
import uuid
from dataclasses import replace

import pytest

from core.song_project import (
    MAX_MEDIA_FILE_BYTES,
    MAX_PROJECT_TRACKS,
    InputMapping,
    MediaImportMethod,
    MediaProvenance,
    SongMedia,
    SongProject,
    SongProjectError,
    SongTrack,
    TimeSignature,
    normalize_media_relative_path,
    song_project_from_dict,
)


_NAMESPACE = uuid.UUID("4d4aa5d1-7250-4bc4-acb3-18491e0f3c2b")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _media(label: str = "backing") -> SongMedia:
    contents = f"audio:{label}".encode("utf-8")
    return SongMedia(
        media_id=_id(f"media:{label}"),
        path=f"Media/{_id(f'media:{label}')}.wav",
        sha256=hashlib.sha256(contents).hexdigest(),
        size_bytes=len(contents),
        sample_rate=48_000,
        channels=2,
        frame_count=48_000,
        format="wav",
        original_basename=f"{label} source.wav",
        provenance=MediaProvenance.LOCAL_FILE,
        import_method=MediaImportMethod.COPY,
        provenance_detail="User selected a local backing track",
    )


def _project() -> SongProject:
    media = _media()
    return SongProject(
        project_id=_id("project"),
        name="Reference Song",
        project_sample_rate=48_000,
        tempo_bpm=123.5,
        time_signature=TimeSignature(7, 8),
        tracks=(
            SongTrack(
                track_id=_id("track:voice"),
                name="Lead Vocal",
                order=0,
                input_mapping=InputMapping("coreaudio:mic", (1,)),
                armed=True,
            ),
            SongTrack(
                track_id=_id("track:guitar"),
                name="Guitar",
                order=1,
                input_mapping=InputMapping("coreaudio:interface", (1, 2)),
                input_monitoring=True,
            ),
        ),
        media=(media,),
        backing_media_id=media.media_id,
        revision=4,
    )


def test_new_project_is_take_independent_and_defaults_to_48k() -> None:
    project = SongProject.new("  New   Idea  ", project_id=_id("new"))

    assert project.project_id == _id("new")
    assert project.name == "New Idea"
    assert project.project_sample_rate == 48_000
    assert project.tempo_bpm == 120.0
    assert project.time_signature == TimeSignature(4, 4)
    assert project.tracks == ()
    assert project.media == ()
    assert project.backing_media_id is None
    assert "take_id" not in project.to_dict()
    assert "session_id" not in project.to_dict()


def test_schema_one_round_trip_is_exact_and_contains_no_absolute_media_path() -> None:
    project = _project()

    value = project.to_dict()
    restored = song_project_from_dict(copy.deepcopy(value))

    assert restored == project
    assert restored.to_dict() == value
    serialized = str(value)
    assert "/Users/" not in serialized
    assert "C:\\" not in serialized
    assert value["media"][0]["path"].startswith("Media/")
    assert value["media"][0]["original_basename"] == "backing source.wav"
    assert value["media"][0]["original_read_only"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/Media/song.wav",
        "../Media/song.wav",
        "Media/../song.wav",
        "Media/nested/song.wav",
        "media/song.wav",
        r"Media\song.wav",
        "Media/",
        "song.wav",
        "Media/\x00song.wav",
    ],
)
def test_media_paths_reject_absolute_traversal_nested_and_platform_ambiguity(
    path: str,
) -> None:
    with pytest.raises(SongProjectError, match="Media"):
        normalize_media_relative_path(path)


def test_media_path_and_original_basename_keep_spaces_but_not_paths() -> None:
    assert normalize_media_relative_path("Media/a song take.wav") == (
        "Media/a song take.wav"
    )

    with pytest.raises(SongProjectError, match="basename"):
        replace(_media(), original_basename="../outside.wav")
    with pytest.raises(SongProjectError, match="basename"):
        replace(_media(), original_basename=r"C:\outside.wav")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"tempo_bpm": math.nan}, "tempo_bpm"),
        ({"tempo_bpm": math.inf}, "tempo_bpm"),
        ({"tempo_bpm": 19.999}, "tempo_bpm"),
        ({"tempo_bpm": 400.001}, "tempo_bpm"),
        ({"project_sample_rate": 7_999}, "project_sample_rate"),
        ({"project_sample_rate": 384_001}, "project_sample_rate"),
        ({"revision": True}, "revision"),
    ],
)
def test_project_numeric_bounds_are_finite_and_strict(
    change: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(SongProjectError, match=match):
        replace(_project(), **change)


@pytest.mark.parametrize(
    "signature",
    [TimeSignature(1, 1), TimeSignature(32, 32), TimeSignature(7, 8)],
)
def test_supported_time_signatures_round_trip(signature: TimeSignature) -> None:
    assert TimeSignature.from_dict(signature.to_dict()) == signature


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(0, 4), (33, 4), (4, 0), (4, 3), (4, 64)],
)
def test_time_signature_is_bounded_and_denominator_is_musical(
    numerator: int,
    denominator: int,
) -> None:
    with pytest.raises(SongProjectError, match="time_signature"):
        TimeSignature(numerator, denominator)


def test_input_mapping_uses_portable_key_and_one_based_unique_channels() -> None:
    mapping = InputMapping("asio:Interface A", (1, 2, 8))
    assert InputMapping.from_dict(mapping.to_dict()) == mapping

    with pytest.raises(SongProjectError, match="duplicates"):
        InputMapping("asio:Interface A", (1, 1))
    with pytest.raises(SongProjectError, match="channel"):
        InputMapping("asio:Interface A", (0,))
    with pytest.raises(SongProjectError, match="tuple"):
        InputMapping("asio:Interface A", [1])  # type: ignore[arg-type]
    with pytest.raises(SongProjectError, match="device_key"):
        InputMapping("", (1,))


def test_track_tuple_order_and_declared_order_cannot_disagree() -> None:
    first, second = _project().tracks

    with pytest.raises(SongProjectError, match="contiguous order"):
        replace(_project(), tracks=(second, first))
    with pytest.raises(SongProjectError, match="contiguous order"):
        replace(_project(), tracks=(replace(first, order=1),))


def test_track_and_media_ids_and_paths_are_unique() -> None:
    project = _project()
    media = project.media[0]
    first = project.tracks[0]

    with pytest.raises(SongProjectError, match="track IDs"):
        replace(project, tracks=(first, replace(first, order=1)))
    with pytest.raises(SongProjectError, match="media IDs"):
        replace(project, media=(media, media), backing_media_id=media.media_id)
    with pytest.raises(SongProjectError, match="media paths"):
        replace(
            project,
            media=(
                media,
                replace(media, media_id=_id("another-media")),
            ),
        )


def test_backing_designation_must_reference_collected_media() -> None:
    project = _project()

    with pytest.raises(SongProjectError, match="backing_media_id"):
        replace(project, backing_media_id=_id("missing"))
    cleared = project.designate_backing_media(None)
    assert cleared.backing_media_id is None
    assert cleared.revision == project.revision + 1


def test_add_track_and_media_create_durable_ids_and_increment_revision() -> None:
    project = SongProject.new("Idea", project_id=_id("edit-project"))
    with_track = project.add_track(
        "Vocal",
        track_id=_id("edit-track"),
        input_mapping=InputMapping("device:one", (1,)),
    )
    media = _media("edit")
    with_media = with_track.add_media(media, designate_backing=True)

    assert with_track.tracks[0].track_id == _id("edit-track")
    assert with_track.tracks[0].order == 0
    assert with_track.revision == 1
    assert with_media.media_by_id(media.media_id) == media
    assert with_media.backing_media_id == media.media_id
    assert with_media.revision == 2


def test_project_identity_edits_validate_and_increment_revision() -> None:
    project = _project()
    renamed = project.rename("  Late   Night  ")
    assert renamed.name == "Late Night"
    assert renamed.revision == project.revision + 1
    tempo = renamed.set_tempo(137.5)
    assert tempo.tempo_bpm == 137.5
    assert tempo.revision == renamed.revision + 1
    signature = tempo.set_time_signature(6, 8)
    assert signature.time_signature == TimeSignature(6, 8)
    assert signature.revision == tempo.revision + 1
    with pytest.raises(SongProjectError):
        project.rename("")
    with pytest.raises(SongProjectError):
        project.set_tempo(float("nan"))
    with pytest.raises(SongProjectError):
        project.set_time_signature(4, 3)


def test_imported_original_semantics_cannot_be_disabled() -> None:
    with pytest.raises(SongProjectError, match="never edits"):
        replace(_media(), original_read_only=False)
    value = _media().to_dict()
    value["original_read_only"] = False
    with pytest.raises(SongProjectError, match="never edits"):
        SongMedia.from_dict(value)


def test_provenance_detail_cannot_smuggle_an_external_path() -> None:
    with pytest.raises(SongProjectError, match="path-free"):
        replace(_media(), provenance_detail="/Users/alice/private/song.wav")
    with pytest.raises(SongProjectError, match="path-free"):
        replace(_media(), provenance_detail=r"C:\Users\alice\private\song.wav")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "A" * 64),
        ("sha256", "0" * 63),
        ("size_bytes", 0),
        ("channels", 0),
        ("frame_count", 0),
        ("format", "../../WAV"),
    ],
)
def test_media_identity_and_audio_facts_are_bounded(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SongProjectError):
        replace(_media(), **{field: value})


def test_project_aggregate_media_size_is_bounded() -> None:
    project = SongProject.new("Huge", project_id=_id("huge"))
    items = tuple(
        replace(
            _media(f"huge-{index}"),
            size_bytes=MAX_MEDIA_FILE_BYTES,
        )
        for index in range(9)
    )
    with pytest.raises(SongProjectError, match="aggregate"):
        replace(project, media=items)


def test_track_count_is_bounded_before_persistence() -> None:
    value = SongProject.new("Limit", project_id=_id("limit")).to_dict()
    template = SongTrack(_id("limit-template"), "Track", 0).to_dict()
    value["tracks"] = [template for _ in range(MAX_PROJECT_TRACKS + 1)]
    with pytest.raises(SongProjectError, match="tracks exceeds"):
        SongProject.from_dict(value)


def test_strict_root_and_nested_json_keys_reject_silent_schema_drift() -> None:
    value = _project().to_dict()
    value["unexpected"] = "future field"
    with pytest.raises(SongProjectError, match="unsupported fields"):
        SongProject.from_dict(value)

    value = _project().to_dict()
    value["tracks"][0]["unexpected"] = True
    with pytest.raises(SongProjectError, match="unsupported fields"):
        SongProject.from_dict(value)

    value = _project().to_dict()
    value["media"][0]["external_path"] = "/private/song.wav"
    with pytest.raises(SongProjectError, match="unsupported fields"):
        SongProject.from_dict(value)


def test_missing_required_json_key_is_rejected_instead_of_defaulted() -> None:
    value = _project().to_dict()
    del value["tempo_bpm"]
    with pytest.raises(SongProjectError, match="missing required"):
        SongProject.from_dict(value)

    value = _project().to_dict()
    del value["media"][0]["provenance_detail"]
    with pytest.raises(SongProjectError, match="missing required"):
        SongProject.from_dict(value)


def test_json_types_do_not_coerce_strings_numbers_or_booleans() -> None:
    value = _project().to_dict()
    value["tempo_bpm"] = "120"
    with pytest.raises(SongProjectError, match="tempo_bpm"):
        SongProject.from_dict(value)

    value = _project().to_dict()
    value["tracks"][0]["armed"] = 1
    with pytest.raises(SongProjectError, match="armed"):
        SongProject.from_dict(value)


def test_uuid_text_must_be_canonical_to_avoid_identity_aliases() -> None:
    value = _project().to_dict()
    value["project_id"] = value["project_id"].upper()
    with pytest.raises(SongProjectError, match="canonical"):
        SongProject.from_dict(value)


def test_remove_track_compacts_order_without_reusing_durable_ids() -> None:
    project = SongProject.new("Track removal", project_id=_id("remove-project"))
    project = project.add_track("One", track_id=_id("remove-one"))
    project = project.add_track("Two", track_id=_id("remove-two"))
    project = project.add_track("Three", track_id=_id("remove-three"))

    updated = project.remove_track(project.tracks[1].track_id)

    assert [item.name for item in updated.tracks] == ["One", "Three"]
    assert [item.order for item in updated.tracks] == [0, 1]
    assert _id("remove-two") not in {item.track_id for item in updated.tracks}

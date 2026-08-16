import copy
import dataclasses
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from core.recording_readiness import (
    RecordingStorageCheck,
    RecordingStorageStatus,
)
from core.local_capture import LocalCaptureTrack, bind_local_capture_logical_sources
from core.session_recording_plan import (
    GuestLocalOriginalBinding,
    InputMapBinding,
    LEGACY_CAPTURE_TRACKS,
    SessionRecordingPlan,
    SharedTrackBinding,
    resolve_capture_tracks,
)


def _storage(status=RecordingStorageStatus.READY, required=1_000_000):
    return RecordingStorageCheck(
        status=status, detail="ok", free_bytes=10_000_000, required_bytes=required
    )


def _fingerprint(label: str = "shared-track-bytes") -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _plan(**overrides) -> SessionRecordingPlan:
    values = dict(
        session_id="session-1",
        take_id="take-1",
        plan_generation=1,
        roster=(("p-host", "Jeff"), ("p-guest", "Alex")),
        expected_server_stems=("p-host", "p-guest"),
        count_in_frames=48_000,
        pre_roll_frames=4_800,
        storage=_storage(),
        expected_source_count=3,
        created_at_utc="2026-08-10T12:00:00Z",
        shared_track=SharedTrackBinding(
            source_fingerprint_sha256=_fingerprint(), playback_generation=3
        ),
        shared_track_planned=True,
        creator_profile_key="music",
        input_maps=(
            InputMapBinding(track_name="Guitar DI", channel_count=1),
            InputMapBinding(
                track_name="Vocal Mic",
                channel_count=2,
                local_original_enabled=True,
            ),
        ),
    )
    values.update(overrides)
    return SessionRecordingPlan(**values)


def test_valid_plan_binds_every_fact_and_is_immutable():
    plan = _plan()
    assert plan.roster == (("p-host", "Jeff"), ("p-guest", "Alex"))
    assert plan.shared_track is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.take_id = "other"  # type: ignore[misc]
    # Private facts never leak through repr.
    for private in ("Jeff", "Alex", "Guitar", "take-1", _fingerprint()):
        assert private not in repr(plan)
    assert repr(plan.shared_track) == "SharedTrackBinding(private=[redacted])"


def test_creator_profile_is_canonical_and_covered_by_plan_identity():
    music = _plan()
    podcast = _plan(creator_profile_key="podcast_voice")
    legacy = _plan(creator_profile_key="music_jam")

    assert legacy.creator_profile_key == "music"
    assert podcast.to_public_dict()["creator_profile_key"] == "podcast_voice"
    assert podcast.plan_fingerprint() != music.plan_fingerprint()
    with pytest.raises(ValueError, match="creator_profile_key"):
        _plan(creator_profile_key="unsupported")


def test_guest_local_original_contract_is_exact_private_and_fingerprint_bound():
    guest = GuestLocalOriginalBinding(
        participant_id="p-guest",
        track_count=2,
        map_fingerprint_sha256="cd" * 32,
        presence_generation=7,
    )
    plan = _plan(
        guest_local_originals=(guest,),
        expected_source_count=5,
    )
    public = plan.to_public_dict()
    assert public["guest_local_original_participant_count"] == 1
    assert public["guest_local_original_track_count"] == 2
    assert "p-guest" not in str(public)
    restored = SessionRecordingPlan.from_private_dict(plan.to_private_dict())
    assert restored.guest_local_originals == (guest,)
    assert restored.plan_fingerprint() == plan.plan_fingerprint()
    assert plan.plan_fingerprint() != _plan().plan_fingerprint()

    with pytest.raises(ValueError, match="planned server sources"):
        _plan(
            guest_local_originals=(
                GuestLocalOriginalBinding("not-planned", 0, "ef" * 32, 1),
            ),
        )


def test_plan_binds_stable_server_and_host_logical_sources_across_takes():
    first = _plan(
        server_channel_counts=(1, 2),
    )
    repeated = _plan(
        take_id="take-2",
        plan_generation=2,
        server_channel_counts=(1, 2),
    )
    other_session = _plan(
        session_id="session-2",
        server_channel_counts=(1, 2),
    )
    renamed = _plan(
        take_id="take-renamed",
        plan_generation=3,
        server_channel_counts=(1, 2),
        input_maps=(
            InputMapBinding(track_name="Renamed DI", channel_count=1),
            InputMapBinding(
                track_name="Renamed Voice",
                channel_count=2,
                local_original_enabled=True,
            ),
        ),
    )

    assert first.server_topology_exact
    assert first.server_logical_source_ids == repeated.server_logical_source_ids
    assert first.input_map_logical_source_ids == repeated.input_map_logical_source_ids
    assert first.input_map_logical_source_ids == renamed.input_map_logical_source_ids
    assert first.server_logical_source_ids != other_session.server_logical_source_ids
    assert first.channel_count_for_server("p-guest") == 2
    assert first.channel_count_for_server("not-planned") is None

    tracks = first.resolved_capture_tracks()
    assert len(tracks) == 1
    assert tracks[0].channel_count == 2
    assert tracks[0].logical_source_id == first.input_map_logical_source_ids[1]
    assert SessionRecordingPlan.from_private_dict(
        first.to_private_dict()
    ).server_channel_counts == (1, 2)


def test_guest_plan_binds_ordered_mono_stereo_source_slots():
    source_ids = (str(uuid.uuid4()), str(uuid.uuid4()))
    guest = GuestLocalOriginalBinding(
        participant_id="p-guest",
        track_count=2,
        map_fingerprint_sha256="ab" * 32,
        presence_generation=4,
        channel_counts=(1, 2),
        logical_source_ids=source_ids,
    )
    plan = _plan(guest_local_originals=(guest,), expected_source_count=5)

    assert guest.exact_topology
    assert SessionRecordingPlan.from_private_dict(
        plan.to_private_dict()
    ).guest_local_originals == (guest,)
    with pytest.raises(ValueError, match="mono or stereo"):
        GuestLocalOriginalBinding(
            "p-guest",
            1,
            "ab" * 32,
            1,
            channel_counts=(3,),
            logical_source_ids=(source_ids[0],),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("session_id", ""),
        ("session_id", "x" * 200),
        ("take_id", "bad\ntake"),
        ("plan_generation", 0),
        ("plan_generation", True),
        ("plan_generation", 1 << 63),
        ("roster", ()),
        ("roster", (("dup", "A"), ("dup", "B"))),
        ("roster", tuple(((f"p{i}", "N") for i in range(257)))),
        ("expected_server_stems", ("s", "s")),
        ("count_in_frames", -1),
        ("count_in_frames", 1.5),
        ("expected_source_count", 0),
        ("expected_source_count", False),
        ("expected_source_count", 290),
        ("created_at_utc", ""),
        ("shared_track", "not-a-binding"),
        ("input_maps", ("not-a-binding",)),
        (
            "input_maps",
            tuple(
                InputMapBinding(track_name=f"T{i}", channel_count=1) for i in range(33)
            ),
        ),
        (
            "input_maps",
            tuple(
                InputMapBinding(
                    track_name=f"Stereo {i}",
                    channel_count=2,
                    local_original_enabled=True,
                )
                for i in range(17)
            ),
        ),
        (
            "input_maps",
            (
                InputMapBinding(track_name="Same", channel_count=1),
                InputMapBinding(track_name="Same", channel_count=2),
            ),
        ),
    ),
)
def test_invalid_plan_facts_fail_closed(field, value):
    with pytest.raises(ValueError):
        _plan(**{field: value})


def test_action_needed_storage_can_never_be_planned():
    with pytest.raises(ValueError, match="needs attention"):
        _plan(storage=_storage(status=RecordingStorageStatus.ACTION_NEEDED))
    # WARNING storage is allowed: the musician was told and chose to record.
    plan = _plan(storage=_storage(status=RecordingStorageStatus.WARNING))
    assert plan.to_public_dict()["storage_status"] == "warning"


def test_shared_track_binding_requires_a_proven_fingerprint():
    with pytest.raises(ValueError, match="proven source fingerprint"):
        SharedTrackBinding(source_fingerprint_sha256="", playback_generation=1)
    with pytest.raises(ValueError, match="proven source fingerprint"):
        SharedTrackBinding(source_fingerprint_sha256="song.mp3", playback_generation=1)
    with pytest.raises(ValueError):
        SharedTrackBinding(
            source_fingerprint_sha256=_fingerprint(), playback_generation=0
        )
    with pytest.raises(ValueError):
        SharedTrackBinding(
            source_fingerprint_sha256=_fingerprint(), playback_generation=1 << 63
        )
    upper = SharedTrackBinding(
        source_fingerprint_sha256=_fingerprint().upper(), playback_generation=1
    )
    assert upper.source_fingerprint_sha256 == _fingerprint()


def test_input_map_binding_validates_channels_and_names():
    with pytest.raises(ValueError):
        InputMapBinding(track_name="Bass", channel_count=3)
    with pytest.raises(ValueError):
        InputMapBinding(track_name="", channel_count=1)
    with pytest.raises(ValueError):
        InputMapBinding(track_name="Bass", channel_count=True)
    with pytest.raises(ValueError):
        InputMapBinding(track_name="Bass", channel_count=1, enabled="yes")


def test_public_projection_is_path_free_and_counts_only():
    plan = _plan()
    public = plan.to_public_dict()
    assert public["roster_count"] == 2
    assert public["input_map_count"] == 2
    assert public["shared_track_bound"] is True
    encoded = str(public)
    for private in ("Jeff", "Alex", "Guitar", "Vocal", _fingerprint(), "/"):
        assert private not in encoded


def test_plan_fingerprint_is_stable_and_binds_every_fact():
    assert _plan().plan_fingerprint() == _plan().plan_fingerprint()
    baseline = _plan().plan_fingerprint()
    changed = (
        _plan(plan_generation=2),
        _plan(roster=(("p-host", "Jeff"),), expected_source_count=3),
        _plan(count_in_frames=0),
        _plan(shared_track=None),
        _plan(
            input_maps=(InputMapBinding(track_name="Guitar DI", channel_count=2),),
            expected_source_count=2,
        ),
        _plan(created_at_utc="2026-08-10T12:00:01Z"),
        _plan(
            storage=RecordingStorageCheck(
                status=RecordingStorageStatus.WARNING,
                detail="low but accepted",
                free_bytes=9_000_000,
                required_bytes=1_000_000,
            )
        ),
    )
    fingerprints = {plan.plan_fingerprint() for plan in changed}
    assert baseline not in fingerprints
    assert len(fingerprints) == len(changed)


def test_shared_track_binding_requires_the_planned_flag():
    with pytest.raises(ValueError, match="shared_track_planned"):
        _plan(shared_track_planned=False)
    with pytest.raises(ValueError):
        _plan(shared_track_planned="yes")
    planned_only = _plan(shared_track=None, shared_track_planned=True)
    assert planned_only.to_public_dict()["shared_track_planned"] is True
    assert planned_only.to_public_dict()["shared_track_bound"] is False
    # The planned flag is part of the binding digest.
    unplanned = _plan(shared_track=None, shared_track_planned=False)
    assert planned_only.plan_fingerprint() != unplanned.plan_fingerprint()


def test_private_plan_round_trip_rebuilds_every_typed_fact():
    plan = _plan()
    payload = json.loads(json.dumps(plan.to_private_dict()))

    assert set(payload) == {
        "schema_version",
        "session_id",
        "take_id",
        "plan_generation",
        "roster",
        "expected_server_stems",
        "server_logical_source_ids",
        "server_channel_counts",
        "count_in_frames",
        "pre_roll_frames",
        "storage",
        "expected_source_count",
        "created_at_utc",
        "shared_track",
        "shared_track_planned",
        "input_maps",
        "input_map_logical_source_ids",
        "guest_local_originals",
        "creator_profile_key",
        "plan_fingerprint_sha256",
    }
    assert payload["roster"][0] == {
        "participant_id": "p-host",
        "display_name": "Jeff",
    }
    assert payload["storage"] == {
        "status": "ready",
        "detail": "ok",
        "free_bytes": 10_000_000,
        "required_bytes": 1_000_000,
    }
    assert payload["plan_fingerprint_sha256"] == plan.plan_fingerprint()
    assert payload["creator_profile_key"] == "music"
    assert payload["guest_local_originals"] == []

    restored = SessionRecordingPlan.from_private_dict(
        payload,
        expected_take_id=plan.take_id,
        expected_fingerprint_sha256=plan.plan_fingerprint(),
    )

    assert restored == plan
    assert isinstance(restored.storage, RecordingStorageCheck)
    assert isinstance(restored.shared_track, SharedTrackBinding)
    assert all(isinstance(item, InputMapBinding) for item in restored.input_maps)


@pytest.mark.parametrize(
    ("path", "tampered"),
    (
        (("plan_generation",), 2),
        (("roster", 0, "display_name"), "Mallory"),
        (("storage", "required_bytes"), 2_000_000),
        (("shared_track", "playback_generation"), 4),
        (("input_maps", 1, "channel_count"), 1),
        (("creator_profile_key",), "unknown"),
        (("count_in_frames",), True),
    ),
)
def test_private_plan_deserialization_rejects_tampered_or_wrongly_typed_facts(
    path,
    tampered,
):
    payload = copy.deepcopy(_plan().to_private_dict())
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = tampered

    with pytest.raises(ValueError):
        SessionRecordingPlan.from_private_dict(payload)


def test_private_plan_deserialization_rejects_schema_and_external_binding_mismatch():
    plan = _plan()

    extra = plan.to_private_dict()
    extra["unexpected"] = "field"
    with pytest.raises(ValueError, match="schema"):
        SessionRecordingPlan.from_private_dict(extra)

    missing = plan.to_private_dict()
    missing.pop("roster")
    with pytest.raises(ValueError, match="schema"):
        SessionRecordingPlan.from_private_dict(missing)

    wrong_schema = plan.to_private_dict()
    wrong_schema["schema_version"] = 3
    with pytest.raises(ValueError, match="Unsupported"):
        SessionRecordingPlan.from_private_dict(wrong_schema)

    with pytest.raises(ValueError, match="take identity"):
        SessionRecordingPlan.from_private_dict(
            plan.to_private_dict(), expected_take_id="take-2"
        )
    with pytest.raises(ValueError, match="binding"):
        SessionRecordingPlan.from_private_dict(
            plan.to_private_dict(),
            expected_fingerprint_sha256=_fingerprint("different"),
        )


@pytest.mark.parametrize(
    "storage",
    (
        RecordingStorageCheck("ready", "ok", 1, 1),
        RecordingStorageCheck(RecordingStorageStatus.READY, "", 1, 1),
        RecordingStorageCheck(RecordingStorageStatus.READY, " ok", 1, 1),
        RecordingStorageCheck(RecordingStorageStatus.READY, "ok", -1, 1),
        RecordingStorageCheck(RecordingStorageStatus.READY, "ok", 1, True),
    ),
)
def test_plan_rejects_noncanonical_storage_facts(storage):
    with pytest.raises(ValueError):
        _plan(storage=storage)


def test_capture_resolver_preserves_logical_stereo_and_opt_out_truth():
    settings = SimpleNamespace(
        local_capture_enabled=True,
        input_maps=[
            {
                "name": "Guitar DI",
                "channels": 1,
                "enabled": True,
                "local_original_enabled": True,
            },
            {
                "name": "Skipped",
                "channels": 1,
                "enabled": False,
                "local_original_enabled": True,
            },
            {
                "name": "Room Pair",
                "channels": 2,
                "enabled": True,
                "local_original_enabled": True,
            },
        ],
    )

    resolved = resolve_capture_tracks(settings)

    assert resolved == (
        LocalCaptureTrack("local-Guitar DI", (0,), logical_source_ordinal=0),
        LocalCaptureTrack("local-Room Pair", (1, 2), logical_source_ordinal=2),
    )
    assert len(resolved) == 2
    assert tuple(track.logical_source_ordinal for track in resolved) == (0, 2)
    assert sum(track.channel_count for track in resolved) == 3
    # Historical unpacking remains available but does not split the stereo WAV.
    assert tuple(tuple(track) for track in resolved) == (
        ("local-Guitar DI", 0),
        ("local-Room Pair", 1),
    )

    first_bound = bind_local_capture_logical_sources(
        resolved,
        session_id="session-stable-slots",
        participant_id="guest-stable-slots",
    )
    room_source_id = first_bound[1].logical_source_id
    settings.input_maps[0]["local_original_enabled"] = False
    room_only = resolve_capture_tracks(settings)
    assert len(room_only) == 1
    assert room_only[0].logical_source_ordinal == 2
    assert (
        bind_local_capture_logical_sources(
            room_only,
            session_id="session-stable-slots",
            participant_id="guest-stable-slots",
        )[0].logical_source_id
        == room_source_id
    )

    settings.input_maps = [
        {
            "name": "No Original",
            "channels": 2,
            "enabled": True,
            "local_original_enabled": False,
        }
    ]
    assert resolve_capture_tracks(settings) == ()


def test_capture_resolver_enforces_32_logical_tracks_and_32_source_channels():
    def entry(index: int, channels: int = 1) -> dict[str, object]:
        return {
            "name": f"Track {index + 1}",
            "channels": channels,
            "enabled": True,
            "local_original_enabled": True,
        }

    settings = SimpleNamespace(
        local_capture_enabled=True,
        input_maps=[entry(index) for index in range(32)],
    )
    resolved = resolve_capture_tracks(settings)
    assert len(resolved) == 32
    assert resolved[-1] == LocalCaptureTrack(
        "local-Track 32", (31,), logical_source_ordinal=31
    )

    settings.input_maps = [entry(index, 2) for index in range(17)]
    assert resolve_capture_tracks(settings) == ()

    settings.input_maps = [entry(index) for index in range(30)] + [entry(30, 2)]
    resolved = resolve_capture_tracks(settings)
    assert len(resolved) == 31
    assert resolved[-1] == LocalCaptureTrack(
        "local-Track 31", (30, 31), logical_source_ordinal=30
    )


def test_capture_resolver_retains_default_pair_only_for_empty_legacy_map():
    settings = SimpleNamespace(local_capture_enabled=True, input_maps=[])
    assert resolve_capture_tracks(settings) == LEGACY_CAPTURE_TRACKS
    assert all(isinstance(track, LocalCaptureTrack) for track in LEGACY_CAPTURE_TRACKS)

    settings.local_capture_enabled = False
    assert resolve_capture_tracks(settings) == ()

    # A malformed non-empty map and a valid all-opted-out map both fail toward
    # no capture, never the historical default pair.
    settings.local_capture_enabled = True
    settings.input_maps = [{"name": "bad", "channels": 9}]
    assert resolve_capture_tracks(settings) == ()

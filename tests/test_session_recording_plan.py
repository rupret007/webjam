import dataclasses
import hashlib

import pytest

from core.recording_readiness import (
    RecordingStorageCheck,
    RecordingStorageStatus,
)
from core.session_recording_plan import (
    InputMapBinding,
    SessionRecordingPlan,
    SharedTrackBinding,
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
        expected_server_stems=("stem-p-host", "stem-p-guest"),
        count_in_frames=48_000,
        pre_roll_frames=4_800,
        storage=_storage(),
        expected_source_count=2,
        created_at_utc="2026-08-10T12:00:00Z",
        shared_track=SharedTrackBinding(
            source_fingerprint_sha256=_fingerprint(), playback_generation=3
        ),
        shared_track_planned=True,
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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("session_id", ""),
        ("session_id", "x" * 200),
        ("take_id", "bad\ntake"),
        ("plan_generation", 0),
        ("plan_generation", True),
        ("roster", ()),
        ("roster", (("dup", "A"), ("dup", "B"))),
        ("roster", tuple(((f"p{i}", "N") for i in range(257)))),
        ("expected_server_stems", ("s", "s")),
        ("count_in_frames", -1),
        ("count_in_frames", 1.5),
        ("expected_source_count", 0),
        ("expected_source_count", False),
        ("created_at_utc", ""),
        ("shared_track", "not-a-binding"),
        ("input_maps", ("not-a-binding",)),
        (
            "input_maps",
            tuple(
                InputMapBinding(track_name=f"T{i}", channel_count=1)
                for i in range(33)
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
        SharedTrackBinding(
            source_fingerprint_sha256="song.mp3", playback_generation=1
        )
    with pytest.raises(ValueError):
        SharedTrackBinding(
            source_fingerprint_sha256=_fingerprint(), playback_generation=0
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
        _plan(roster=(("p-host", "Jeff"),), expected_source_count=1),
        _plan(count_in_frames=0),
        _plan(shared_track=None),
        _plan(
            input_maps=(
                InputMapBinding(track_name="Guitar DI", channel_count=2),
            )
        ),
        _plan(created_at_utc="2026-08-10T12:00:01Z"),
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

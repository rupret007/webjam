from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SharedTrackPlaybackState,
    SharedTrackSessionSnapshot,
    TransferConflictError,
    TransferStore,
)
from core.session_transfer_runtime import HostPeerSession

pytestmark = pytest.mark.requires_local_socket


def _id() -> str:
    return str(uuid.uuid4())


def _ready_projection(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state": SharedTrackPlaybackState.READY,
        "loaded": True,
        "source_display_name": "Midnight Practice.wav",
        "position_s": 0.0,
        "duration_s": 185.25,
        "loop_start_s": 0.0,
        "loop_end_s": None,
        "count_in_active": False,
        "cleanup_pending": False,
        "needs_attention": False,
    }
    values.update(changes)
    return values


def test_shared_track_projection_round_trips_bounded_path_free_truth() -> None:
    snapshot = SharedTrackSessionSnapshot(
        generation=7,
        playback_generation=3,
        state=SharedTrackPlaybackState.PLAYING,
        loaded=True,
        source_display_name="  Midnight   Practice.wav  ",
        position_s=42.5,
        duration_s=185.25,
        loop_start_s=30.0,
        loop_end_s=60.0,
        count_in_active=True,
        cleanup_pending=False,
        needs_attention=False,
    )

    assert snapshot.source_display_name == "Midnight Practice.wav"
    assert SharedTrackSessionSnapshot.from_mapping(snapshot.to_mapping()) == snapshot
    assert not hasattr(snapshot, "can_control")
    assert not hasattr(snapshot, "audible")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"loaded": 1}, "loaded must be a boolean"),
        ({"source_display_name": "/Users/alice/demo.wav"}, "must not contain a path"),
        ({"source_display_name": "demo\u0007.wav"}, "unsupported characters"),
        ({"position_s": "1.0"}, "finite non-negative number"),
        ({"position_s": float("inf")}, "position_s is outside"),
        ({"position_s": 200.0}, "position_s must not exceed"),
        ({"loop_start_s": 60.0, "loop_end_s": 30.0}, "must be after"),
        ({"loop_end_s": 200.0}, "must not exceed duration"),
        ({"count_in_active": True}, "requires active playback"),
        ({"playback_generation": True}, "playback_generation is outside"),
    ],
)
def test_shared_track_projection_rejects_unbounded_or_contradictory_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    values = {
        "generation": 1,
        "playback_generation": 1,
        **_ready_projection(),
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        SharedTrackSessionSnapshot(**values)  # type: ignore[arg-type]


def test_legacy_session_snapshot_defaults_to_idle_shared_track(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    client = SessionPeerClient(
        "127.0.0.1",
        9,
        credentials=credentials,
    )
    registry = EnrollmentRegistry(tmp_path, credentials)
    enrollment = registry.enroll(
        _id(),
        "Guest",
        invite_token=credentials.invite_token,
    )
    client._request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "session_id": credentials.session_id,
        "generation": 0,
        "signal": "idle",
        "take_id": None,
    }

    state = client.state(enrollment)

    assert state.shared_track == SharedTrackSessionSnapshot()
    assert state.creator_profile_key == "music"


def test_creator_profile_is_authenticated_durable_session_context(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    control = SessionControlState(
        tmp_path,
        credentials.session_id,
        creator_profile_key="podcast_voice",
    )
    transfers = TransferStore(tmp_path, credentials.session_id)
    enrollment = registry.enroll(
        _id(),
        "Guest",
        invite_token=credentials.invite_token,
    )
    control.begin(_id(), started_utc="2026-08-15T12:00:00Z")
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    try:
        client = SessionPeerClient(
            server.address[0],
            server.address[1],
            credentials=credentials,
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    assert observed.creator_profile_key == "podcast_voice"
    assert SessionControlState(
        tmp_path,
        credentials.session_id,
    ).snapshot().creator_profile_key == "podcast_voice"
    with pytest.raises(ValueError, match="creator_profile_key"):
        SessionControlState(
            tmp_path / "unsupported",
            _id(),
            creator_profile_key="future_profile",
        )


def test_shared_track_publication_is_idempotent_monotonic_and_memory_only(
    tmp_path: Path,
) -> None:
    session_id = _id()
    take_id = _id()
    control = SessionControlState(tmp_path, session_id)
    recording = control.begin(take_id, started_utc="2026-08-09T12:00:00Z")
    durable_before = control.path.read_bytes()

    ready = control.publish_shared_track(**_ready_projection())
    duplicate = control.publish_shared_track(**_ready_projection())
    routing = control.publish_shared_track(
        **_ready_projection(state=SharedTrackPlaybackState.ROUTING)
    )
    playing = control.publish_shared_track(
        **_ready_projection(
            state=SharedTrackPlaybackState.PLAYING,
            position_s=0.25,
            count_in_active=True,
        )
    )

    assert ready is duplicate
    assert ready.generation == 1
    assert ready.playback_generation == 0
    assert routing.generation == 2
    assert routing.playback_generation == 1
    assert playing.generation == 3
    assert playing.playback_generation == 1
    assert control.snapshot().generation == recording.generation
    assert control.path.read_bytes() == durable_before
    with pytest.raises(TransferConflictError, match="newer Shared Track"):
        control.publish_shared_track(
            **_ready_projection(),
            playback_generation=0,
        )

    reopened = SessionControlState(tmp_path, session_id)
    assert reopened.snapshot().signal is RecordingSignal.RECORDING
    assert reopened.snapshot().shared_track == SharedTrackSessionSnapshot()


def test_authenticated_guest_receives_host_shared_track_projection(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    control = SessionControlState(tmp_path, credentials.session_id)
    transfers = TransferStore(tmp_path, credentials.session_id)
    enrollment = registry.enroll(
        _id(),
        "Guest",
        invite_token=credentials.invite_token,
    )
    expected = control.publish_shared_track(
        **_ready_projection(
            state=SharedTrackPlaybackState.PLAYING,
            position_s=44.25,
            loop_start_s=30.0,
            loop_end_s=60.0,
            count_in_active=True,
        ),
        playback_generation=11,
    )
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    try:
        client = SessionPeerClient(
            "127.0.0.1",
            server.address[1],
            credentials=credentials,
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    assert observed.shared_track == expected
    assert observed.shared_track.source_display_name == "Midnight Practice.wav"
    assert observed.shared_track.state is SharedTrackPlaybackState.PLAYING
    assert observed.shared_track.count_in_active is True


def test_finalizing_signal_is_idempotent_durable_and_available_on_host_runtime(
    tmp_path: Path,
) -> None:
    session_id = _id()
    take_id = _id()
    control = SessionControlState(tmp_path, session_id)
    control.begin(take_id, started_utc="2026-08-09T12:00:00Z")

    finalizing = control.begin_finalizing(
        take_id,
        stopped_utc="2026-08-09T12:01:00Z",
        message="  Preserving   originals  ",
    )
    duplicate = control.begin_finalizing(
        take_id,
        stopped_utc="ignored",
        message="ignored",
    )

    assert finalizing is duplicate
    assert finalizing.signal is RecordingSignal.FINALIZING
    assert finalizing.message == "Preserving originals"
    assert SessionControlState(tmp_path, session_id).snapshot() == finalizing

    host = HostPeerSession()
    assert host.publish_shared_track_state(**_ready_projection()) is None
    host.control = control
    published = host.publish_shared_track_state(**_ready_projection())
    assert published is not None
    assert control.snapshot().shared_track == published
    runtime_finalizing = host.begin_take_finalization(
        take_id,
        stopped_utc="ignored",
    )
    assert runtime_finalizing is control.snapshot()
    assert runtime_finalizing.signal is RecordingSignal.FINALIZING
    assert runtime_finalizing.generation == finalizing.generation

    complete = control.finish(take_id, stopped_utc="2026-08-09T12:01:30Z")
    assert complete.signal is RecordingSignal.COMPLETE
    assert (
        control.begin_finalizing(take_id, stopped_utc="delayed duplicate")
        is complete
    )

"""The reference video projection carried by the private peer plane.

These tests drive the real ``SessionPeerServer`` over loopback so the host
transport a follower sees is the same bytes a second computer would receive.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.reference_video import (
    ReferenceVideoFollowState,
    ReferenceVideoFollower,
    ReferenceVideoHostController,
    load_reference_video_source,
    session_identity_signer,
)
from core.session_transfer import (
    EnrollmentRegistry,
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    TransferConflictError,
    TransferStore,
)
from core.session_transfer_runtime import HostPeerSession

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def _id() -> str:
    return str(uuid.uuid4())


def _shared_projection(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state": ReferenceVideoPlaybackState.READY,
        "shared": True,
        "source_display_name": "studio-lesson.mp4",
        "identity_digest": DIGEST,
        "position_s": 0.0,
        "duration_s": 1_800.0,
        "needs_attention": False,
    }
    values.update(changes)
    return values


class FakePlayer:
    def __init__(self, duration_s: float = 1_800.0) -> None:
        self.duration_s = duration_s
        self.position = 0.0
        self.state = "idle"
        self.seeks: list[float] = []

    def load(self, path: Path) -> float:
        self.state = "ready"
        return self.duration_s

    def play(self) -> None:
        self.state = "playing"

    def pause(self) -> None:
        self.state = "paused"

    def stop(self) -> None:
        self.state = "ready"
        self.position = 0.0

    def seek(self, position_s: float) -> None:
        self.seeks.append(float(position_s))
        self.position = float(position_s)

    def position_s(self) -> float:
        return self.position

    def close(self) -> None:
        self.state = "closed"


# ---------------------------------------------------------------------------
# Wire schema
# ---------------------------------------------------------------------------


def test_reference_video_projection_round_trips_bounded_path_free_truth() -> None:
    snapshot = ReferenceVideoSessionSnapshot(
        generation=4,
        playback_generation=2,
        state=ReferenceVideoPlaybackState.PLAYING,
        shared=True,
        source_display_name="  studio   lesson.mp4  ",
        identity_digest=DIGEST,
        position_s=61.5,
        duration_s=1_800.0,
        needs_attention=False,
    )

    assert snapshot.source_display_name == "studio lesson.mp4"
    assert (
        ReferenceVideoSessionSnapshot.from_mapping(snapshot.to_mapping()) == snapshot
    )
    assert not hasattr(snapshot, "can_control")
    assert not hasattr(snapshot, "source_path")


def test_projection_never_carries_a_path_or_the_private_content_hash(
    tmp_path: Path,
) -> None:
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"art session reference bytes")
    source = load_reference_video_source(video)
    signer = session_identity_signer(session_id=_id(), session_key="invite-token")

    payload = ReferenceVideoSessionSnapshot(
        **_shared_projection(identity_digest=signer(source.content_sha256))
    ).to_mapping()

    rendered = repr(payload)
    assert source.content_sha256 not in rendered
    assert str(tmp_path) not in rendered
    assert "source_path" not in payload


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"shared": 1}, "shared must be a boolean"),
        (
            {"source_display_name": "/Users/alice/lesson.mp4"},
            "must not contain a path",
        ),
        ({"source_display_name": "lesson\u0007.mp4"}, "unsupported characters"),
        ({"position_s": "1.0"}, "finite non-negative number"),
        ({"position_s": float("inf")}, "position_s is outside"),
        ({"position_s": 4_000.0}, "position_s must not exceed"),
        ({"duration_s": 0.0}, "requires a duration"),
        ({"identity_digest": ""}, "requires a proven identity digest"),
        ({"identity_digest": "nope"}, "not a supported digest"),
        ({"identity_digest": "A" * 64}, "not a supported digest"),
        ({"playback_generation": True}, "playback_generation is outside"),
        ({"state": ReferenceVideoPlaybackState.IDLE}, "cannot be idle"),
    ],
)
def test_projection_rejects_unbounded_or_contradictory_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    values = {
        "generation": 1,
        "playback_generation": 1,
        **_shared_projection(),
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        ReferenceVideoSessionSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"source_display_name": "lesson.mp4"},
        {"identity_digest": DIGEST},
        {"position_s": 1.0},
        {"duration_s": 10.0},
    ],
)
def test_an_unshared_projection_cannot_expose_media_facts(
    changes: dict[str, object],
) -> None:
    values = {
        "shared": False,
        "state": ReferenceVideoPlaybackState.IDLE,
        "source_display_name": "",
        "identity_digest": "",
        "position_s": 0.0,
        "duration_s": 0.0,
        "needs_attention": False,
        **changes,
    }
    with pytest.raises(ValueError, match="cannot expose media facts"):
        ReferenceVideoSessionSnapshot(**values)  # type: ignore[arg-type]


def test_an_unshared_projection_may_only_be_idle_or_failed() -> None:
    for state in (
        ReferenceVideoPlaybackState.IDLE,
        ReferenceVideoPlaybackState.FAILED,
    ):
        assert ReferenceVideoSessionSnapshot(shared=False, state=state).shared is False
    for state in (
        ReferenceVideoPlaybackState.READY,
        ReferenceVideoPlaybackState.PLAYING,
        ReferenceVideoPlaybackState.PAUSED,
    ):
        with pytest.raises(ValueError, match="requires shared media"):
            ReferenceVideoSessionSnapshot(shared=False, state=state)


def test_incomplete_or_unknown_schema_payloads_are_refused() -> None:
    complete = ReferenceVideoSessionSnapshot().to_mapping()
    assert ReferenceVideoSessionSnapshot.from_mapping(None) == (
        ReferenceVideoSessionSnapshot()
    )
    with pytest.raises(ValueError, match="must be an object"):
        ReferenceVideoSessionSnapshot.from_mapping("nope")
    with pytest.raises(ValueError, match="schema is not supported"):
        ReferenceVideoSessionSnapshot.from_mapping({**complete, "schema": 2})
    partial = {key: value for key, value in complete.items() if key != "position_s"}
    with pytest.raises(ValueError, match="incomplete"):
        ReferenceVideoSessionSnapshot.from_mapping(partial)


def test_legacy_session_snapshot_defaults_to_an_unshared_reference_video(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    client = SessionPeerClient("127.0.0.1", 9, credentials=credentials)
    registry = EnrollmentRegistry(tmp_path, credentials)
    enrollment = registry.enroll(
        _id(), "Guest", invite_token=credentials.invite_token
    )
    client._request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "session_id": credentials.session_id,
        "generation": 0,
        "signal": "idle",
        "take_id": None,
    }

    state = client.state(enrollment)

    assert state.reference_video == ReferenceVideoSessionSnapshot()
    assert state.reference_video.shared is False


# ---------------------------------------------------------------------------
# Host publication
# ---------------------------------------------------------------------------


def test_publication_is_idempotent_monotonic_and_memory_only(tmp_path: Path) -> None:
    session_id = _id()
    control = SessionControlState(tmp_path, session_id)
    control.begin(_id(), started_utc="2026-08-09T12:00:00Z")
    durable_before = control.path.read_bytes()

    ready = control.publish_reference_video(**_shared_projection())
    duplicate = control.publish_reference_video(**_shared_projection())
    playing = control.publish_reference_video(
        **_shared_projection(
            state=ReferenceVideoPlaybackState.PLAYING, position_s=0.5
        )
    )
    paused = control.publish_reference_video(
        **_shared_projection(
            state=ReferenceVideoPlaybackState.PAUSED, position_s=9.0
        )
    )
    resumed = control.publish_reference_video(
        **_shared_projection(
            state=ReferenceVideoPlaybackState.PLAYING, position_s=9.0
        )
    )

    assert ready is duplicate
    assert ready.generation == 1
    assert ready.playback_generation == 0
    assert playing.generation == 2
    assert playing.playback_generation == 1
    assert paused.playback_generation == 1
    assert resumed.playback_generation == 2

    # Transport position is never fsynced into the durable recording journal.
    assert control.path.read_bytes() == durable_before
    reopened = SessionControlState(tmp_path, session_id)
    assert reopened.snapshot().signal is RecordingSignal.RECORDING
    assert reopened.snapshot().reference_video == ReferenceVideoSessionSnapshot()


def test_an_older_playback_generation_is_refused(tmp_path: Path) -> None:
    control = SessionControlState(tmp_path, _id())
    control.publish_reference_video(
        **_shared_projection(state=ReferenceVideoPlaybackState.PLAYING),
        playback_generation=9,
    )
    with pytest.raises(TransferConflictError, match="newer reference video"):
        control.publish_reference_video(
            **_shared_projection(),
            playback_generation=8,
        )


def test_withdrawing_the_video_publishes_an_unshared_projection(
    tmp_path: Path,
) -> None:
    control = SessionControlState(tmp_path, _id())
    control.publish_reference_video(
        **_shared_projection(state=ReferenceVideoPlaybackState.PLAYING)
    )
    withdrawn = control.publish_reference_video(
        state=ReferenceVideoPlaybackState.IDLE,
        shared=False,
    )
    assert withdrawn.shared is False
    assert withdrawn.identity_digest == ""
    assert withdrawn.source_display_name == ""


def test_host_runtime_publishes_only_once_a_session_owns_control(
    tmp_path: Path,
) -> None:
    host = HostPeerSession()
    assert host.publish_reference_video_state(**_shared_projection()) is None

    host.control = SessionControlState(tmp_path, _id())
    published = host.publish_reference_video_state(**_shared_projection())
    assert published is not None
    assert host.control.snapshot().reference_video == published


# ---------------------------------------------------------------------------
# Host to guest across the peer plane
# ---------------------------------------------------------------------------


def _serve(tmp_path: Path, control: SessionControlState, credentials):
    registry = EnrollmentRegistry(tmp_path, credentials)
    transfers = TransferStore(tmp_path, credentials.session_id)
    enrollment = registry.enroll(
        _id(), "Artist", invite_token=credentials.invite_token
    )
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    return server, enrollment


def test_authenticated_guest_receives_the_host_reference_video_projection(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )
    expected = control.publish_reference_video(
        **_shared_projection(
            state=ReferenceVideoPlaybackState.PLAYING, position_s=612.5
        ),
        playback_generation=4,
    )
    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    assert observed.reference_video == expected
    assert observed.reference_video.position_s == pytest.approx(612.5)
    assert observed.reference_video.identity_digest == DIGEST
    assert observed.creator_profile_key == "art"


def test_a_late_joining_artist_lands_on_the_hosts_current_position(
    tmp_path: Path,
) -> None:
    """The whole round trip: host file, peer plane, follower with its own copy."""

    payload = b"the exact same lesson bytes on both computers"
    host_copy = tmp_path / "host" / "lesson.mp4"
    host_copy.parent.mkdir()
    host_copy.write_bytes(payload)
    guest_copy = tmp_path / "guest" / "my-lesson.mp4"
    guest_copy.parent.mkdir()
    guest_copy.write_bytes(payload)

    credentials = SessionCredentials.create()
    signer = session_identity_signer(
        session_id=credentials.session_id,
        session_key=credentials.invite_token,
    )
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )

    host_player = FakePlayer(duration_s=1_800.0)
    host = ReferenceVideoHostController(
        host_player,
        identity_signer=signer,
        is_host=lambda: True,
    )
    host.share(host_copy)
    host.play()
    host_player.position = 612.5
    snapshot = host.refresh()
    control.publish_reference_video(
        state=ReferenceVideoPlaybackState.PLAYING,
        shared=snapshot.shared,
        source_display_name=snapshot.source_display_name,
        identity_digest=snapshot.identity_digest,
        position_s=snapshot.position_s,
        duration_s=snapshot.duration_s,
        needs_attention=snapshot.needs_attention,
    )

    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    guest_player = FakePlayer(duration_s=1_800.0)
    follower = ReferenceVideoFollower(
        identity_signer=session_identity_signer(
            session_id=credentials.session_id,
            session_key=credentials.invite_token,
        ),
        player=guest_player,
    )
    follower.open_local_copy(guest_copy)
    follower.observe(observed.reference_video, received_monotonic_s=1_000.0)

    landed = follower.apply(1_000.25)

    assert landed.state is ReferenceVideoFollowState.FOLLOWING
    assert landed.should_play is True
    assert landed.target_position_s == pytest.approx(612.75)
    assert guest_player.state == "playing"
    assert guest_player.seeks[-1] == pytest.approx(612.75)
    assert landed.source_display_name == "lesson.mp4"


def test_an_artist_without_the_hosts_file_stays_in_the_room_without_playing(
    tmp_path: Path,
) -> None:
    host_copy = tmp_path / "host" / "lesson.mp4"
    host_copy.parent.mkdir()
    host_copy.write_bytes(b"the host's lesson")
    wrong_copy = tmp_path / "guest" / "something-else.mp4"
    wrong_copy.parent.mkdir()
    wrong_copy.write_bytes(b"a different video entirely")

    credentials = SessionCredentials.create()
    signer = session_identity_signer(
        session_id=credentials.session_id,
        session_key=credentials.invite_token,
    )
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )
    host = ReferenceVideoHostController(
        FakePlayer(), identity_signer=signer, is_host=lambda: True
    )
    snapshot = host.share(host_copy)
    control.publish_reference_video(
        state=ReferenceVideoPlaybackState.READY,
        shared=snapshot.shared,
        source_display_name=snapshot.source_display_name,
        identity_digest=snapshot.identity_digest,
        position_s=snapshot.position_s,
        duration_s=snapshot.duration_s,
    )

    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    guest_player = FakePlayer()
    follower = ReferenceVideoFollower(
        identity_signer=session_identity_signer(
            session_id=credentials.session_id,
            session_key=credentials.invite_token,
        ),
        player=guest_player,
    )
    follower.observe(observed.reference_video, received_monotonic_s=0.0)

    with pytest.raises(Exception):
        follower.open_local_copy(wrong_copy)

    blocked = follower.apply(0.0)
    assert blocked.state is ReferenceVideoFollowState.NEEDS_FILE
    assert blocked.can_follow is False
    assert guest_player.state != "playing"

    # Hiding the video is always available and keeps the artist in the room.
    assert follower.set_hidden(True).state is ReferenceVideoFollowState.HIDDEN


def test_an_identity_from_another_session_never_matches(tmp_path: Path) -> None:
    """A digest captured in one room cannot authorize playback in another."""

    payload = b"the same bytes in two different rooms"
    video = tmp_path / "lesson.mp4"
    video.write_bytes(payload)
    source = load_reference_video_source(video)

    room_one = SessionCredentials.create()
    room_two = SessionCredentials.create()
    published_in_room_one = session_identity_signer(
        session_id=room_one.session_id, session_key=room_one.invite_token
    )(source.content_sha256)

    follower = ReferenceVideoFollower(
        identity_signer=session_identity_signer(
            session_id=room_two.session_id, session_key=room_two.invite_token
        )
    )
    follower.open_local_copy(video)
    follower.observe(
        ReferenceVideoSessionSnapshot(
            **_shared_projection(
                state=ReferenceVideoPlaybackState.PLAYING,
                identity_digest=published_in_room_one,
            )
        ),
        received_monotonic_s=0.0,
    )

    assert follower.resolve(0.0).state is ReferenceVideoFollowState.MISMATCHED_FILE

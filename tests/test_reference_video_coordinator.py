"""Session-scoped reference video ownership, driven headlessly.

The coordinator holds no Qt types, so a fake player and a fake host peer are
enough to prove that host transport reaches the peer plane and that a follower
only ever plays what it can prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.reference_video import (
    ReferenceVideoError,
    ReferenceVideoFollowState,
    ReferenceVideoState,
)
from core.session_transfer import (
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    SessionControlState,
    SessionCredentials,
)
from webjam_qt.controllers.reference_video_coordinator import (
    NOT_FOLLOWING_MESSAGE,
    NOT_HOSTING_MESSAGE,
    PLAYER_UNAVAILABLE_MESSAGE,
    ReferenceVideoCoordinator,
)

SESSION_ID = "5b3f1a2e-6d4c-4a1b-9c8d-7e6f5a4b3c2d"
SESSION_KEY = "invite-token-for-coordinator-tests"


class FakePlayer:
    def __init__(self, duration_s: float = 300.0) -> None:
        self.duration_s = duration_s
        self.position = 0.0
        self.state = "idle"
        self.seeks: list[float] = []
        self.surface = object()
        self.muted = False

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)

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


class FakeHostPeer:
    """Records what the coordinator projected onto the private peer plane."""

    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.published: list[dict] = []
        self.raises = False

    def publish_reference_video_state(self, **kwargs):
        if self.raises:
            raise RuntimeError("peer plane is unavailable")
        self.published.append(kwargs)
        return kwargs


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_coordinator(*, peer=None, players=None, clock=None):
    made: list[FakePlayer] = []
    supply = list(players or [])

    def factory():
        player = supply.pop(0) if supply else FakePlayer()
        made.append(player)
        return player

    host_snapshots: list = []
    follow_snapshots: list = []
    coordinator = ReferenceVideoCoordinator(
        player_factory=factory,
        host_peer_provider=lambda: peer,
        clock=clock or Clock(),
        on_host_snapshot=host_snapshots.append,
        on_follow_snapshot=follow_snapshots.append,
    )
    return coordinator, made, host_snapshots, follow_snapshots


def write_video(path: Path, payload: bytes = b"the shared lesson") -> Path:
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def test_an_unbound_coordinator_owns_nothing():
    coordinator, _, _, _ = make_coordinator()
    assert coordinator.role == ""
    assert coordinator.hosting is False
    assert coordinator.following is False
    assert coordinator.host_snapshot.shared is False
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    coordinator.tick()  # must be a safe no-op


def test_host_intents_are_refused_before_hosting(tmp_path):
    coordinator, _, _, _ = make_coordinator()
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    with pytest.raises(ReferenceVideoError, match=NOT_HOSTING_MESSAGE):
        coordinator.share(str(write_video(tmp_path / "a.mp4")))
    with pytest.raises(ReferenceVideoError, match=NOT_HOSTING_MESSAGE):
        coordinator.play()


def test_follower_intents_are_refused_on_a_host(tmp_path):
    coordinator, _, _, _ = make_coordinator()
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    with pytest.raises(ReferenceVideoError, match=NOT_FOLLOWING_MESSAGE):
        coordinator.open_local_copy(str(write_video(tmp_path / "a.mp4")))
    with pytest.raises(ReferenceVideoError, match=NOT_FOLLOWING_MESSAGE):
        coordinator.set_hidden(True)


def test_rebinding_a_room_releases_the_previous_role(tmp_path):
    peer = FakeHostPeer()
    coordinator, players, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "a.mp4")))
    coordinator.play()

    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)

    assert coordinator.following is True
    assert coordinator.host_snapshot.shared is False
    assert players[0].state == "closed"
    assert peer.published[-1] == {"state": "idle", "shared": False}


# ---------------------------------------------------------------------------
# Host publication
# ---------------------------------------------------------------------------


def test_host_transport_reaches_the_peer_plane(tmp_path):
    peer = FakeHostPeer()
    coordinator, players, host_snapshots, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)

    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    coordinator.play()

    assert [item["state"] for item in peer.published] == ["ready", "playing"]
    playing = peer.published[-1]
    assert playing["shared"] is True
    assert playing["source_display_name"] == "lesson.mp4"
    assert len(playing["identity_digest"]) == 64
    assert playing["duration_s"] == pytest.approx(300.0)
    assert host_snapshots[-1].state is ReferenceVideoState.PLAYING


def test_a_withdrawn_video_publishes_nothing_shared(tmp_path):
    peer = FakeHostPeer()
    coordinator, _, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    coordinator.play()

    coordinator.withdraw()

    withdrawn = peer.published[-1]
    assert withdrawn["state"] == "idle"
    assert withdrawn["shared"] is False
    assert withdrawn["identity_digest"] == ""
    assert withdrawn["source_display_name"] == ""


def test_a_failed_host_publishes_attention_without_media_facts(tmp_path):
    peer = FakeHostPeer()
    coordinator, _, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)

    coordinator.share(str(tmp_path / "absent.mp4"))

    failed = peer.published[-1]
    assert failed["state"] == "failed"
    assert failed["shared"] is False
    assert failed["needs_attention"] is True
    assert failed["identity_digest"] == ""


def test_published_projections_are_accepted_by_the_real_wire_schema(tmp_path):
    """Everything the coordinator publishes must satisfy the peer contract."""

    peer = FakeHostPeer()
    coordinator, players, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    coordinator.play()
    players[0].position = 12.0
    coordinator.tick()
    coordinator.pause()
    coordinator.seek(30.0)
    coordinator.stop()
    coordinator.withdraw()

    control = SessionControlState(tmp_path, SessionCredentials.create().session_id)
    for payload in peer.published:
        published = control.publish_reference_video(**payload)
        assert isinstance(published, ReferenceVideoSessionSnapshot)


def test_publication_is_skipped_while_the_peer_plane_is_inactive(tmp_path):
    peer = FakeHostPeer(active=False)
    coordinator, _, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    assert peer.published == []
    assert coordinator.host_snapshot.shared is True


def test_a_peer_plane_failure_never_breaks_host_transport(tmp_path):
    peer = FakeHostPeer()
    peer.raises = True
    coordinator, _, _, _ = make_coordinator(peer=peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)

    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    snapshot = coordinator.play()

    assert snapshot.state is ReferenceVideoState.PLAYING


def test_host_tick_republishes_the_moving_position(tmp_path):
    peer = FakeHostPeer()
    player = FakePlayer()
    coordinator, _, _, _ = make_coordinator(peer=peer, players=[player])
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    coordinator.play()

    player.position = 9.5
    coordinator.tick()

    assert peer.published[-1]["position_s"] == pytest.approx(9.5)


# ---------------------------------------------------------------------------
# Following
# ---------------------------------------------------------------------------


def _host_projection(digest: str, **changes):
    values = {
        "state": ReferenceVideoPlaybackState.PLAYING,
        "shared": True,
        "source_display_name": "lesson.mp4",
        "identity_digest": digest,
        "position_s": 30.0,
        "duration_s": 300.0,
        "playback_generation": 1,
        "generation": 1,
    }
    values.update(changes)
    return ReferenceVideoSessionSnapshot(**values)


class HostState:
    def __init__(self, reference_video) -> None:
        self.reference_video = reference_video


def _digest_for(tmp_path, payload=b"the shared lesson") -> str:
    from core.reference_video import (
        load_reference_video_source,
        session_identity_signer,
    )

    source = load_reference_video_source(
        write_video(tmp_path / "reference-source.mp4", payload)
    )
    return session_identity_signer(
        session_id=SESSION_ID, session_key=SESSION_KEY
    )(source.content_sha256)


def test_a_guest_follows_the_host_after_opening_the_same_file(tmp_path):
    clock = Clock()
    player = FakePlayer()
    coordinator, _, _, follow_snapshots = make_coordinator(
        players=[player], clock=clock
    )
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))

    coordinator.open_local_copy(str(write_video(tmp_path / "mine.mp4")))
    clock.now += 0.5
    coordinator.tick()

    latest = follow_snapshots[-1]
    assert latest.state is ReferenceVideoFollowState.FOLLOWING
    assert latest.target_position_s == pytest.approx(30.5)
    assert player.state == "playing"
    assert player.seeks[-1] == pytest.approx(30.5)


def test_a_guest_with_the_wrong_file_never_plays(tmp_path):
    player = FakePlayer()
    coordinator, _, _, _ = make_coordinator(players=[player])
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))

    with pytest.raises(ReferenceVideoError):
        coordinator.open_local_copy(
            str(write_video(tmp_path / "mine.mp4", b"different bytes"))
        )

    coordinator.tick()
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert player.state != "playing"


def test_a_guest_can_hide_the_video_and_stay_in_the_room(tmp_path):
    player = FakePlayer()
    coordinator, _, _, _ = make_coordinator(players=[player])
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))
    coordinator.open_local_copy(str(write_video(tmp_path / "mine.mp4")))
    coordinator.tick()
    assert player.state == "playing"

    coordinator.set_hidden(True)
    coordinator.tick()

    assert coordinator.hidden is True
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    assert player.state == "paused"


def test_a_guest_can_hide_before_ever_choosing_a_file(tmp_path):
    coordinator, players, _, _ = make_coordinator()
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))

    assert coordinator.set_hidden(True).state is ReferenceVideoFollowState.HIDDEN
    # Hiding must not force a video player to exist on this computer.
    assert players == []


def test_a_room_with_no_shared_video_stays_quiet(tmp_path):
    coordinator, players, _, _ = make_coordinator()
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(ReferenceVideoSessionSnapshot()))
    coordinator.tick()

    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    assert players == []


def test_a_missing_session_state_member_is_treated_as_no_video():
    coordinator, _, _, _ = make_coordinator()
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(object())
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO


def test_a_guest_video_is_current_only_after_this_room_observes_it(tmp_path):
    coordinator, _, _, _ = make_coordinator()
    first = HostState(_host_projection(_digest_for(tmp_path)))
    replacement = HostState(_host_projection(_digest_for(tmp_path), generation=2))
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    assert coordinator.video_is_current(first) is False
    coordinator.observe_host_state(first)
    assert coordinator.video_is_current(first) is True
    assert coordinator.video_is_current(replacement) is False
    coordinator.observe_host_state(replacement)
    assert coordinator.video_is_current(replacement) is True
    coordinator.end()
    assert coordinator.video_is_current(replacement) is False


def test_a_computer_without_a_video_player_says_so_and_stays_in_the_room(tmp_path):
    def broken_factory():
        raise RuntimeError("no QtMultimedia on this machine")

    coordinator = ReferenceVideoCoordinator(player_factory=broken_factory)
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))

    with pytest.raises(ReferenceVideoError, match="cannot play video"):
        coordinator.open_local_copy(str(write_video(tmp_path / "mine.mp4")))

    assert PLAYER_UNAVAILABLE_MESSAGE.startswith("This computer cannot play video")
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert coordinator.set_hidden(True).state is ReferenceVideoFollowState.HIDDEN


@pytest.mark.parametrize("failure", ["missing", "raising", "unproven"])
def test_a_player_must_prove_it_is_silent_before_loading(tmp_path, failure):
    player = FakePlayer()
    if failure == "missing":
        player.set_muted = None  # type: ignore[assignment]
    elif failure == "raising":
        def refuse_mute(_muted: bool) -> None:
            raise RuntimeError("audio output refused mute")

        player.set_muted = refuse_mute  # type: ignore[method-assign]
    else:
        def ignore_mute(_muted: bool) -> None:
            return None

        player.set_muted = ignore_mute  # type: ignore[method-assign]
    coordinator = ReferenceVideoCoordinator(player_factory=lambda: player)
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))

    with pytest.raises(ReferenceVideoError, match="cannot play video"):
        coordinator.open_local_copy(str(write_video(tmp_path / "mine.mp4")))

    assert player.state == "closed"


def test_ending_a_room_releases_players_and_forgets_the_role(tmp_path):
    peer = FakeHostPeer()
    player = FakePlayer()
    coordinator, _, _, _ = make_coordinator(peer=peer, players=[player])
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    coordinator.play()

    coordinator.end()

    assert coordinator.role == ""
    assert coordinator.player_surface is None
    assert player.state == "closed"
    assert peer.published[-1] == {"state": "idle", "shared": False}


def test_ending_a_guest_room_stops_and_closes_its_player(tmp_path):
    player = FakePlayer()
    coordinator, _, _, _ = make_coordinator(players=[player])
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(HostState(_host_projection(_digest_for(tmp_path))))
    coordinator.open_local_copy(str(write_video(tmp_path / "mine.mp4")))
    coordinator.tick()
    assert player.state == "playing"

    coordinator.end()

    assert coordinator.role == ""
    assert coordinator.player_surface is None
    assert player.state == "closed"


def test_the_player_surface_is_reused_for_one_room(tmp_path):
    coordinator, players, _, _ = make_coordinator()
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share(str(write_video(tmp_path / "lesson.mp4")))
    surface = coordinator.player_surface
    coordinator.share(str(write_video(tmp_path / "another.mp4", b"second lesson")))

    assert surface is not None
    assert coordinator.player_surface is surface
    assert len(players) == 1

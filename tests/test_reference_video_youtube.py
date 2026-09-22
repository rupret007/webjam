"""Online lessons retain Paint along's host authority and room identity."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.reference_video import (
    ReferenceVideoError, ReferenceVideoFollower, ReferenceVideoHostController,
    ReferenceVideoFollowState, ReferenceVideoState, session_identity_signer,
)
from core.session_transfer import ReferenceVideoSessionSnapshot
from core.youtube_lesson import YouTubeLesson
from tests.test_reference_video_coordinator import FakePlayer, FakeHostPeer, Clock, SESSION_ID, SESSION_KEY
from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator

URL = "https://youtu.be/M7lc1UVf-VE"
LESSON = YouTubeLesson("M7lc1UVf-VE")


class LessonPlayer(FakePlayer):
    def load(self, source):
        assert self.muted
        assert isinstance(source, YouTubeLesson)
        self.source = source
        return super().load(source)


def signer(key=SESSION_KEY):
    return session_identity_signer(session_id=SESSION_ID, session_key=key)


def offer(**changes):
    return ReferenceVideoSessionSnapshot(
        shared=True, state="playing", source_display_name="YouTube lesson",
        source_kind="youtube", video_id=LESSON.video_id,
        identity_digest=signer()(LESSON.content_sha256),
        duration_s=300.0, position_s=12.0, playback_generation=1, **changes,
    )


def test_host_shares_canonical_lesson_and_controls_its_transport():
    player = LessonPlayer()
    player.set_muted(True)
    host = ReferenceVideoHostController(player, identity_signer=signer(), is_host=lambda: True)
    snapshot = host.share_youtube(URL + "?t=30")
    assert snapshot.state is ReferenceVideoState.READY
    assert snapshot.source_kind == "youtube" and snapshot.video_id == LESSON.video_id
    assert snapshot.position_s == 30.0
    assert snapshot.identity_digest == signer()(LESSON.content_sha256)
    host.play()
    assert player.state == "playing"
    host.pause()
    assert player.state == "paused"
    host.seek(45)
    assert player.position == 45.0
    host.withdraw()
    assert not host.snapshot.shared and not host.snapshot.video_id


def test_guest_explicit_open_follows_host_and_stale_room_pauses():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer(), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    waiting = follower.resolve(100)
    assert waiting.source_kind == "youtube"
    assert "Open lesson" in waiting.message
    assert player.state == "idle"
    follower.open_youtube_lesson()
    current = follower.apply(101)
    assert current.can_follow and player.state == "playing"
    assert player.position == 13.0
    follower.apply(200)
    assert player.state == "paused"
    assert follower.resolve(200).state is ReferenceVideoFollowState.STALLED


def test_guest_cannot_load_unproven_lesson_identity():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer("another-room-key"), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    with pytest.raises(ReferenceVideoError):
        follower.open_youtube_lesson()
    assert player.state == "idle"


def test_guest_switching_host_lesson_needs_new_explicit_open():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer(), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    follower.open_youtube_lesson()
    follower.apply(101)
    second = YouTubeLesson("abcdefghijk")
    follower.observe(replace(offer(), video_id=second.video_id,
                             identity_digest=signer()(second.content_sha256)), received_monotonic_s=102)
    snapshot = follower.apply(102)
    assert not snapshot.can_follow and player.state == "paused"
    assert "Open lesson" in snapshot.message


def test_url_projection_roundtrips_without_changing_local_wire_shape():
    local = ReferenceVideoSessionSnapshot().to_mapping()
    assert "source_kind" not in local and "video_id" not in local
    mapping = offer().to_mapping()
    assert mapping["source_kind"] == "youtube" and mapping["video_id"] == LESSON.video_id
    assert ReferenceVideoSessionSnapshot.from_mapping(mapping) == offer()


@pytest.mark.parametrize("changes", [
    {"video_id": "https://example.com"}, {"source_kind": "website"},
    {"source_kind": "local", "video_id": LESSON.video_id},
    {"source_kind": "youtube", "video_id": ""},
])
def test_url_wire_source_rejects_invalid_or_mixed_source_facts(changes):
    with pytest.raises(ValueError):
        replace(offer(), **changes)


def test_coordinator_publishes_url_and_explicit_guest_open_uses_url_player():
    peer, clock = FakeHostPeer(), Clock()
    host_player, guest_player = LessonPlayer(), LessonPlayer()
    host = ReferenceVideoCoordinator(player_factory=FakePlayer, youtube_player_factory=lambda: host_player,
                                     host_peer_provider=lambda: peer, clock=clock)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    host.share_youtube(URL)
    host.play()
    projection = ReferenceVideoSessionSnapshot(**peer.published[-1])
    assert projection.video_id == LESSON.video_id
    guest = ReferenceVideoCoordinator(player_factory=FakePlayer, youtube_player_factory=lambda: guest_player,
                                      clock=clock)
    guest.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    guest.observe_host_state(SimpleNamespace(reference_video=projection))
    assert guest_player.state == "idle"
    guest.open_youtube_lesson()
    guest.tick()
    assert guest_player.muted and guest_player.state == "playing"
    guest.end()
    assert guest_player.state == "closed"


def test_withdraw_during_load_never_seeks_lesson_timestamp():
    player = LessonPlayer()
    player.set_muted(True)
    host = ReferenceVideoHostController(player, identity_signer=signer(), is_host=lambda: True)
    original = player.load
    def load(source):
        result = original(source)
        host.withdraw()
        return result
    player.load = load
    assert host.share_youtube(URL + "?t=30").state is ReferenceVideoState.IDLE
    assert player.seeks == []


@pytest.mark.parametrize("command", ["play", "pause", "stop", "seek"])
def test_newer_host_withdrawal_wins_during_yielding_command(command):
    player = LessonPlayer()
    player.set_muted(True)
    host = ReferenceVideoHostController(player, identity_signer=signer(), is_host=lambda: True)
    host.share_youtube(URL)
    original = getattr(player, command)
    def yielding(*args):
        setattr(player, command, original)
        original(*args)
        host.withdraw()
    setattr(player, command, yielding)
    result = getattr(host, command)(20) if command == "seek" else getattr(host, command)()
    assert result.state is ReferenceVideoState.IDLE
    assert not result.shared


def test_hide_during_guest_seek_cannot_resume_lesson():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer(), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    follower.open_youtube_lesson()
    original = player.seek
    def seek(position):
        original(position)
        follower.set_hidden(True)
    player.seek = seek
    result = follower.apply(100)
    assert result.state is ReferenceVideoFollowState.HIDDEN
    assert player.state != "playing"


def test_guest_nested_timer_during_seek_does_not_reenter_transport():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer(), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    follower.open_youtube_lesson()
    original = player.seek
    calls = []
    def seek(position):
        calls.append(position)
        original(position)
        if len(calls) == 1:
            follower.apply(100)
    player.seek = seek
    follower.apply(100)
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["host", "guest"])
def test_source_switch_close_cannot_load_into_replacement_room(tmp_path, role):
    old, lesson = FakePlayer(), LessonPlayer()
    coordinator = ReferenceVideoCoordinator(player_factory=lambda: old,
        youtube_player_factory=lambda: lesson, clock=Clock())
    begin = getattr(coordinator, "begin_" + role)
    begin(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"local video")
    if role == "host":
        coordinator.share(str(path))
    else:
        coordinator.open_local_copy(str(path))
        coordinator.observe_host_state(SimpleNamespace(reference_video=offer()))
    original = old.close
    def close():
        old.close = original
        original()
        begin(session_id=SESSION_ID, session_key="replacement-room")
    old.close = close
    if role == "host":
        coordinator.share_youtube(URL)
        assert coordinator.host_snapshot.state is ReferenceVideoState.IDLE
    else:
        coordinator.open_youtube_lesson()
        assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    assert lesson.state == "idle"
    assert coordinator.player_surface is None


def test_guest_can_leave_panel_and_return_without_reopening_lesson():
    player = LessonPlayer()
    coordinator = ReferenceVideoCoordinator(player_factory=FakePlayer,
        youtube_player_factory=lambda: player, clock=Clock())
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(SimpleNamespace(reference_video=offer()))
    coordinator.open_youtube_lesson()
    coordinator.tick()
    coordinator.set_surface_visible(False)
    coordinator.tick()
    assert player.state == "paused"
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.HIDDEN
    coordinator.set_surface_visible(True)
    coordinator.tick()
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert player.state == "playing"


def test_host_leaving_panel_pauses_lesson_for_room():
    player, peer = LessonPlayer(), FakeHostPeer()
    coordinator = ReferenceVideoCoordinator(player_factory=FakePlayer,
        youtube_player_factory=lambda: player, host_peer_provider=lambda: peer)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.share_youtube(URL)
    coordinator.play()
    coordinator.set_surface_visible(False)
    assert coordinator.host_snapshot.state is ReferenceVideoState.PAUSED
    assert peer.published[-1]["state"] == "paused"
    coordinator.set_surface_visible(True)
    assert player.state == "paused"


def test_withdrawn_online_lesson_retains_its_source_context_without_file_retry():
    player = LessonPlayer()
    player.set_muted(True)
    follower = ReferenceVideoFollower(identity_signer=signer(), player=player)
    follower.observe(offer(), received_monotonic_s=100)
    follower.open_youtube_lesson()
    follower.observe(ReferenceVideoSessionSnapshot(), received_monotonic_s=101)
    snapshot = follower.apply(101)
    assert snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    assert snapshot.source_kind == "youtube" and snapshot.can_close_local_copy


def test_guest_navigation_contains_pause_failure_and_preserves_retry_truth():
    player = LessonPlayer()
    coordinator = ReferenceVideoCoordinator(player_factory=FakePlayer,
        youtube_player_factory=lambda: player, clock=Clock())
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    coordinator.observe_host_state(SimpleNamespace(reference_video=offer()))
    coordinator.open_youtube_lesson()
    coordinator.tick()
    def fail():
        raise RuntimeError("player did not confirm pause")
    player.pause = fail
    coordinator.set_surface_visible(False)
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
    assert "Open lesson" in coordinator.follow_snapshot.message


def test_host_provider_pause_end_and_buffering_are_reported_to_followers():
    player = LessonPlayer()
    player.set_muted(True)
    observed = ["playing"]
    player.playback_state = lambda: observed[0]
    host = ReferenceVideoHostController(player, identity_signer=signer(), is_host=lambda: True)
    host.share_youtube(URL)
    host.play()
    observed[0] = "buffering"
    assert host.refresh().needs_attention
    observed[0] = "playing"
    assert not host.refresh().needs_attention
    for state in ("paused", "ended"):
        host.play()
        observed[0] = state
        assert host.refresh().state is ReferenceVideoState.PAUSED

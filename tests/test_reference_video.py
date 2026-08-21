"""Studio Visit reference video: identity, host transport, and following.

Fixtures are tiny byte blobs with video suffixes. The core primitive only
hashes bytes and drives an injected player seam, so no real media -- and no
committed binary -- is needed to prove the contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.reference_video import (  # noqa: E402
    DEFAULT_STALE_AFTER_S,
    HOST_ONLY_TRANSPORT_MESSAGE,
    MISMATCHED_FILE_MESSAGE,
    REFERENCE_VIDEO_IDENTITY_CONTEXT,
    HostVideoProjection,
    ReferenceVideoError,
    ReferenceVideoFollower,
    ReferenceVideoFollowState,
    ReferenceVideoHostController,
    ReferenceVideoPlayerError,
    ReferenceVideoState,
    identities_match,
    load_reference_video_source,
    session_identity_signer,
)

SESSION_ID = "f5b6b6a4-1c1a-4b53-9f0e-2f6b4a6a4d21"
OTHER_SESSION_ID = "0d9a7bd0-8d0f-4b4a-8f3e-6c1f0b1c2d3e"
SESSION_KEY = "invite-token-for-tests-0123456789abcdef"


def write_video(path: Path, payload: bytes = b"studio-visit-reference-bytes") -> Path:
    path.write_bytes(payload)
    return path


@pytest.fixture()
def signer():
    return session_identity_signer(session_id=SESSION_ID, session_key=SESSION_KEY)


class FakePlayer:
    """A deterministic stand-in for a real local video player."""

    def __init__(self, duration_s: float = 120.0) -> None:
        self.duration_s = duration_s
        self.loaded: Path | None = None
        self.position = 0.0
        self.state = "idle"
        self.calls: list[str] = []
        self.seeks: list[float] = []
        self.fail_on: set[str] = set()
        self.closed = False

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise RuntimeError(f"player refused to {name}")

    def load(self, path: Path) -> float:
        self._maybe_fail("load")
        self.loaded = Path(path)
        self.position = 0.0
        self.state = "ready"
        return self.duration_s

    def play(self) -> None:
        self._maybe_fail("play")
        self.state = "playing"

    def pause(self) -> None:
        self._maybe_fail("pause")
        self.state = "paused"

    def stop(self) -> None:
        self._maybe_fail("stop")
        self.state = "ready"
        self.position = 0.0

    def seek(self, position_s: float) -> None:
        self._maybe_fail("seek")
        self.seeks.append(float(position_s))
        self.position = float(position_s)

    def position_s(self) -> float:
        return self.position

    def close(self) -> None:
        self._maybe_fail("close")
        self.closed = True


class Projection:
    """Minimal host projection used where the full wire type is not needed."""

    def __init__(
        self,
        *,
        shared: bool = True,
        state: str = "playing",
        source_display_name: str = "lesson.mp4",
        identity_digest: str = "",
        position_s: float = 0.0,
        duration_s: float = 120.0,
        playback_generation: int = 1,
        needs_attention: bool = False,
    ) -> None:
        self.shared = shared
        self.state = state
        self.source_display_name = source_display_name
        self.identity_digest = identity_digest
        self.position_s = position_s
        self.duration_s = duration_s
        self.playback_generation = playback_generation
        self.needs_attention = needs_attention


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------


def test_identical_bytes_produce_one_content_hash_and_different_bytes_do_not(tmp_path):
    left = write_video(tmp_path / "a.mp4", b"same bytes")
    right = write_video(tmp_path / "b.mov", b"same bytes")
    other = write_video(tmp_path / "c.mp4", b"different bytes")

    assert (
        load_reference_video_source(left).content_sha256
        == load_reference_video_source(right).content_sha256
    )
    assert (
        load_reference_video_source(other).content_sha256
        != load_reference_video_source(left).content_sha256
    )


def test_source_hash_is_the_raw_file_bytes(tmp_path):
    import hashlib

    payload = b"exact bytes on disk"
    source = load_reference_video_source(write_video(tmp_path / "v.mp4", payload))
    assert source.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert source.byte_size == len(payload)
    assert source.display_name == "v.mp4"


def test_source_repr_never_leaks_the_path(tmp_path):
    source = load_reference_video_source(write_video(tmp_path / "private.mp4"))
    assert str(tmp_path) not in repr(source)
    assert "private.mp4" in repr(source)


@pytest.mark.parametrize(
    "name, payload",
    [
        ("notes.txt", b"text"),
        ("clip.wav", b"audio"),
        ("noextension", b"bytes"),
    ],
)
def test_unsupported_containers_are_refused(tmp_path, name, payload):
    path = write_video(tmp_path / name, payload)
    with pytest.raises(ReferenceVideoError):
        load_reference_video_source(path)


def test_missing_and_empty_files_fail_closed(tmp_path):
    with pytest.raises(ReferenceVideoError):
        load_reference_video_source(tmp_path / "absent.mp4")
    with pytest.raises(ReferenceVideoError):
        load_reference_video_source(write_video(tmp_path / "empty.mp4", b""))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_source_is_refused(tmp_path):
    real = write_video(tmp_path / "real.mp4")
    link = tmp_path / "link.mp4"
    link.symlink_to(real)
    with pytest.raises(ReferenceVideoError):
        load_reference_video_source(link)


def test_directory_is_refused(tmp_path):
    folder = tmp_path / "clip.mp4"
    folder.mkdir()
    with pytest.raises(ReferenceVideoError):
        load_reference_video_source(folder)


# ---------------------------------------------------------------------------
# Session-scoped identity
# ---------------------------------------------------------------------------


def test_same_file_in_one_session_yields_one_comparable_identity(tmp_path, signer):
    host_copy = load_reference_video_source(write_video(tmp_path / "h.mp4", b"lesson"))
    guest_copy = load_reference_video_source(write_video(tmp_path / "g.mp4", b"lesson"))
    assert identities_match(
        signer(host_copy.content_sha256), signer(guest_copy.content_sha256)
    )


def test_identity_is_scoped_to_one_session_and_is_not_the_content_hash(
    tmp_path, signer
):
    source = load_reference_video_source(write_video(tmp_path / "h.mp4", b"lesson"))
    elsewhere = session_identity_signer(
        session_id=OTHER_SESSION_ID, session_key=SESSION_KEY
    )
    other_key = session_identity_signer(
        session_id=SESSION_ID, session_key="a-different-invite-token"
    )

    digest = signer(source.content_sha256)
    assert digest != source.content_sha256
    assert digest != elsewhere(source.content_sha256)
    assert digest != other_key(source.content_sha256)
    assert len(digest) == 64


def test_identity_signer_requires_a_started_session():
    with pytest.raises(ReferenceVideoError):
        session_identity_signer(session_id="", session_key=SESSION_KEY)
    with pytest.raises(ReferenceVideoError):
        session_identity_signer(session_id=SESSION_ID, session_key="")


def test_identity_signer_rejects_anything_that_is_not_a_content_hash(signer):
    for bogus in ("", "not-a-hash", "ab" * 31, "z" * 64):
        with pytest.raises(ReferenceVideoError):
            signer(bogus)


def test_identity_context_is_domain_separated():
    assert REFERENCE_VIDEO_IDENTITY_CONTEXT == "webjam-reference-video-v1"


@pytest.mark.parametrize(
    "left, right",
    [
        ("", ""),
        ("a" * 64, ""),
        ("a" * 64, None),
        (None, "a" * 64),
        ("a" * 64, 0),
        # A digest arriving from another computer must fail closed rather
        # than raise out of the follow path.
        ("a" * 64, "\u00e9" * 64),
        ("\u00e9" * 64, "a" * 64),
    ],
)
def test_identities_match_fails_closed_on_anything_unusable(left, right):
    assert identities_match(left, right) is False


def test_identities_match_accepts_one_equal_digest(signer, tmp_path):
    digest = signer(load_reference_video_source(
        write_video(tmp_path / "v.mp4")
    ).content_sha256)
    assert identities_match(digest, digest) is True


# ---------------------------------------------------------------------------
# Host controller
# ---------------------------------------------------------------------------


def make_host(tmp_path, *, is_host=True, duration=120.0):
    player = FakePlayer(duration_s=duration)
    events: list = []
    controller = ReferenceVideoHostController(
        player,
        identity_signer=session_identity_signer(
            session_id=SESSION_ID, session_key=SESSION_KEY
        ),
        is_host=lambda: is_host,
        on_change=events.append,
    )
    return controller, player, events


def test_no_video_is_the_starting_state_not_a_failure(tmp_path):
    controller, player, _ = make_host(tmp_path)
    snapshot = controller.snapshot
    assert snapshot.state is ReferenceVideoState.IDLE
    assert snapshot.shared is False
    assert snapshot.needs_attention is False
    assert snapshot.source_display_name == ""
    assert player.loaded is None


def test_host_shares_cues_and_publishes_identity(tmp_path):
    controller, player, events = make_host(tmp_path)
    video = write_video(tmp_path / "lesson.mp4")

    snapshot = controller.share(video)

    assert snapshot.state is ReferenceVideoState.READY
    assert snapshot.shared is True
    assert snapshot.source_display_name == "lesson.mp4"
    assert snapshot.position_s == 0.0
    assert snapshot.duration_s == 120.0
    assert len(snapshot.identity_digest) == 64
    assert player.loaded == video
    assert events and events[-1] == snapshot


def test_host_snapshot_never_carries_the_private_hash_or_a_path(tmp_path):
    controller, _, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    snapshot = controller.snapshot
    private = controller.content_sha256()

    assert private and private != snapshot.identity_digest
    rendered = repr(snapshot)
    assert private not in rendered
    assert str(tmp_path) not in rendered


def test_host_transport_moves_through_play_pause_stop_and_seek(tmp_path):
    controller, player, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))

    assert controller.play().state is ReferenceVideoState.PLAYING
    player.position = 31.5
    assert controller.refresh().position_s == pytest.approx(31.5)

    paused = controller.pause()
    assert paused.state is ReferenceVideoState.PAUSED
    assert paused.position_s == pytest.approx(31.5)

    moved = controller.seek(64.25)
    assert moved.position_s == pytest.approx(64.25)
    assert player.seeks[-1] == pytest.approx(64.25)

    stopped = controller.stop()
    assert stopped.state is ReferenceVideoState.READY
    assert stopped.position_s == 0.0
    assert stopped.shared is True


def test_seek_is_clamped_to_the_shared_duration(tmp_path):
    controller, _, _ = make_host(tmp_path, duration=42.0)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    assert controller.seek(9_999.0).position_s == pytest.approx(42.0)
    with pytest.raises(ReferenceVideoError):
        controller.seek(-1.0)


def test_playback_generation_marks_each_new_play_attempt(tmp_path):
    controller, _, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    assert controller.snapshot.playback_generation == 0

    assert controller.play().playback_generation == 1
    assert controller.play().playback_generation == 1  # already playing

    controller.pause()
    assert controller.play().playback_generation == 2

    controller.stop()
    assert controller.play().playback_generation == 3


@pytest.mark.parametrize(
    "operation",
    ["play", "pause", "stop", "withdraw", "share", "seek"],
)
def test_guests_cannot_operate_host_transport(tmp_path, operation):
    controller, player, _ = make_host(tmp_path, is_host=True)
    video = write_video(tmp_path / "lesson.mp4")
    controller.share(video)
    controller.play()
    before = controller.snapshot

    controller._is_host = lambda: False  # the same object, now not the host

    with pytest.raises(ReferenceVideoError) as excinfo:
        if operation == "share":
            controller.share(video)
        elif operation == "seek":
            controller.seek(5.0)
        else:
            getattr(controller, operation)()

    assert str(excinfo.value) == HOST_ONLY_TRANSPORT_MESSAGE
    assert controller.snapshot == before


def test_missing_or_unreadable_source_fails_closed_without_sharing(tmp_path):
    controller, _, _ = make_host(tmp_path)
    snapshot = controller.share(tmp_path / "absent.mp4")
    assert snapshot.state is ReferenceVideoState.FAILED
    assert snapshot.shared is False
    assert snapshot.identity_digest == ""
    assert snapshot.needs_attention is True


def test_player_that_cannot_open_the_file_fails_closed(tmp_path):
    controller, player, _ = make_host(tmp_path)
    player.fail_on.add("load")
    snapshot = controller.share(write_video(tmp_path / "lesson.mp4"))
    assert snapshot.state is ReferenceVideoState.FAILED
    assert snapshot.shared is False


def test_zero_duration_media_is_refused(tmp_path):
    controller, player, _ = make_host(tmp_path, duration=0.0)
    snapshot = controller.share(write_video(tmp_path / "lesson.mp4"))
    assert snapshot.state is ReferenceVideoState.FAILED
    assert snapshot.shared is False


def test_transport_before_sharing_is_refused(tmp_path):
    controller, _, _ = make_host(tmp_path)
    for call in (controller.play, controller.pause, controller.stop):
        with pytest.raises(ReferenceVideoError):
            call()


def test_withdraw_returns_the_room_to_the_no_video_path(tmp_path):
    controller, player, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    controller.play()

    snapshot = controller.withdraw()

    assert snapshot.state is ReferenceVideoState.IDLE
    assert snapshot.shared is False
    assert snapshot.source_display_name == ""
    assert snapshot.identity_digest == ""
    assert snapshot.duration_s == 0.0
    assert "stop" in player.calls


def test_a_video_that_will_not_stop_is_not_reported_as_withdrawn(tmp_path):
    controller, player, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    controller.play()
    player.fail_on.add("stop")

    snapshot = controller.withdraw()

    assert snapshot.state is ReferenceVideoState.FAILED
    assert snapshot.needs_attention is True
    assert "needs attention" in snapshot.error


def test_close_releases_the_player_and_refuses_further_sharing(tmp_path):
    controller, player, _ = make_host(tmp_path)
    controller.share(write_video(tmp_path / "lesson.mp4"))
    snapshot = controller.close()
    assert snapshot.state is ReferenceVideoState.CLOSED
    assert player.closed is True
    with pytest.raises(ReferenceVideoError):
        controller.share(write_video(tmp_path / "again.mp4"))


# ---------------------------------------------------------------------------
# Follower
# ---------------------------------------------------------------------------


def make_follower(*, player=None, tolerance_s=0.75, stale_after_s=DEFAULT_STALE_AFTER_S):
    return ReferenceVideoFollower(
        identity_signer=session_identity_signer(
            session_id=SESSION_ID, session_key=SESSION_KEY
        ),
        player=player,
        tolerance_s=tolerance_s,
        stale_after_s=stale_after_s,
    )


def shared_projection(tmp_path, signer, **kwargs):
    source = load_reference_video_source(
        write_video(tmp_path / "host.mp4", b"the lesson")
    )
    kwargs.setdefault("identity_digest", signer(source.content_sha256))
    return Projection(**kwargs)


def test_a_room_with_no_video_is_first_class(tmp_path):
    follower = make_follower()
    snapshot = follower.resolve(0.0)
    assert snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    assert snapshot.can_follow is False
    assert snapshot.should_play is False
    assert snapshot.blocked is False
    assert "Talk and work as usual" in snapshot.message

    follower.observe(Projection(shared=False, state="idle"), received_monotonic_s=0.0)
    assert follower.resolve(1.0).state is ReferenceVideoFollowState.NO_VIDEO


def test_follower_without_a_local_copy_is_told_what_to_do(tmp_path, signer):
    follower = make_follower()
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    snapshot = follower.resolve(0.0)
    assert snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert snapshot.can_follow is False
    assert snapshot.blocked is True
    assert snapshot.source_display_name == "lesson.mp4"


def test_a_different_file_is_refused_rather_than_played(tmp_path, signer):
    follower = make_follower()
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    wrong = write_video(tmp_path / "mine.mp4", b"a completely different video")

    with pytest.raises(ReferenceVideoError) as excinfo:
        follower.open_local_copy(wrong)

    assert str(excinfo.value) == MISMATCHED_FILE_MESSAGE
    snapshot = follower.resolve(0.0)
    assert snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert snapshot.can_follow is False


def test_a_file_opened_before_the_host_shared_still_fails_closed_on_mismatch(
    tmp_path, signer
):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"unrelated"))
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)

    snapshot = follower.resolve(0.0)
    assert snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
    assert snapshot.can_follow is False
    assert snapshot.should_play is False


def test_the_same_file_follows_the_host(tmp_path, signer):
    follower = make_follower()
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=12.0),
        received_monotonic_s=100.0,
    )
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))

    snapshot = follower.resolve(100.0)
    assert snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert snapshot.can_follow is True
    assert snapshot.should_play is True
    assert snapshot.target_position_s == pytest.approx(12.0)


def test_a_late_joiner_lands_on_the_current_host_position(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    # The host has been playing for a while; this computer sees its first
    # projection at 44.25s and resolves 0.4s later.
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=44.25),
        received_monotonic_s=500.0,
    )

    landed = follower.resolve(500.4)
    assert landed.state is ReferenceVideoFollowState.FOLLOWING
    assert landed.should_play is True
    assert landed.target_position_s == pytest.approx(44.65)


def test_a_paused_host_holds_followers_at_the_exact_position(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="paused", position_s=30.0),
        received_monotonic_s=10.0,
    )

    snapshot = follower.resolve(19.0)
    assert snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert snapshot.should_play is False
    assert snapshot.target_position_s == pytest.approx(30.0)


def test_a_stopped_host_returns_followers_to_the_start(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="ready", position_s=0.0),
        received_monotonic_s=10.0,
    )
    snapshot = follower.resolve(12.0)
    assert snapshot.should_play is False
    assert snapshot.target_position_s == pytest.approx(0.0)


def test_extrapolated_position_never_runs_past_the_end(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(
            tmp_path, signer, state="playing", position_s=119.0, duration_s=120.0
        ),
        received_monotonic_s=0.0,
    )
    assert follower.resolve(3.0).target_position_s == pytest.approx(120.0)


def test_a_lost_host_clock_stops_following_instead_of_drifting(tmp_path, signer):
    follower = make_follower(stale_after_s=5.0)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=10.0),
        received_monotonic_s=0.0,
    )

    assert follower.resolve(4.9).state is ReferenceVideoFollowState.FOLLOWING
    stalled = follower.resolve(5.1)
    assert stalled.state is ReferenceVideoFollowState.STALLED
    assert stalled.should_play is False
    assert stalled.blocked is True

    # A fresh projection resumes following without any guest action.
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=15.0),
        received_monotonic_s=5.2,
    )
    assert follower.resolve(5.3).state is ReferenceVideoFollowState.FOLLOWING


def test_a_paused_host_is_never_treated_as_stale(tmp_path, signer):
    follower = make_follower(stale_after_s=1.0)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="paused", position_s=8.0),
        received_monotonic_s=0.0,
    )
    assert follower.resolve(600.0).state is ReferenceVideoFollowState.FOLLOWING


def test_hiding_the_video_keeps_the_artist_in_the_room(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=5.0),
        received_monotonic_s=0.0,
    )

    hidden = follower.set_hidden(True)
    assert hidden.state is ReferenceVideoFollowState.HIDDEN
    assert hidden.can_follow is False
    assert hidden.should_play is False
    assert hidden.blocked is False
    assert follower.hidden is True

    shown = follower.set_hidden(False)
    assert shown.state is ReferenceVideoFollowState.FOLLOWING


def test_hiding_works_without_ever_opening_a_local_copy(tmp_path, signer):
    follower = make_follower()
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    assert follower.set_hidden(True).state is ReferenceVideoFollowState.HIDDEN


def test_a_swapped_local_file_fails_closed_after_it_was_proven(tmp_path, signer):
    follower = make_follower()
    mine = write_video(tmp_path / "mine.mp4", b"the lesson")
    follower.open_local_copy(mine)
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=5.0),
        received_monotonic_s=0.0,
    )
    assert follower.resolve(0.0).state is ReferenceVideoFollowState.FOLLOWING

    mine.write_bytes(b"swapped for something else entirely")

    swapped = follower.resolve(0.1)
    assert swapped.state is ReferenceVideoFollowState.FILE_UNAVAILABLE
    assert swapped.can_follow is False


def test_a_deleted_local_file_fails_closed(tmp_path, signer):
    follower = make_follower()
    mine = write_video(tmp_path / "mine.mp4", b"the lesson")
    follower.open_local_copy(mine)
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    mine.unlink()
    assert follower.resolve(0.0).state is ReferenceVideoFollowState.FILE_UNAVAILABLE


def test_host_trouble_stops_followers_and_says_so(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(
            tmp_path, signer, state="failed", position_s=0.0, needs_attention=True
        ),
        received_monotonic_s=0.0,
    )
    snapshot = follower.resolve(0.0)
    assert snapshot.state is ReferenceVideoFollowState.HOST_ATTENTION
    assert snapshot.can_follow is False


def test_closing_the_local_copy_returns_to_needing_one(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    assert follower.close_local_copy().state is ReferenceVideoFollowState.NEEDS_FILE


def test_a_follower_exposes_no_transport_at_all():
    follower = make_follower()
    for forbidden in ("play", "pause", "stop", "seek", "share", "withdraw"):
        assert not hasattr(follower, forbidden), forbidden


def test_a_follower_reports_the_hosts_playback_generation(tmp_path, signer):
    follower = make_follower()
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, playback_generation=7),
        received_monotonic_s=0.0,
    )
    assert follower.resolve(0.0).playback_generation == 7


# ---------------------------------------------------------------------------
# Follower applied to a local player
# ---------------------------------------------------------------------------


def test_apply_seeks_once_per_playback_generation_then_only_on_real_drift(
    tmp_path, signer
):
    player = FakePlayer()
    follower = make_follower(player=player, tolerance_s=0.75)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(
            tmp_path, signer, state="playing", position_s=20.0, playback_generation=1
        ),
        received_monotonic_s=0.0,
    )

    follower.apply(0.0)
    assert player.seeks == [pytest.approx(20.0)]
    assert player.state == "playing"

    # Local playback keeps pace: no corrective seek inside the tolerance.
    player.position = 20.3
    follower.observe(
        shared_projection(
            tmp_path, signer, state="playing", position_s=20.0, playback_generation=1
        ),
        received_monotonic_s=0.0,
    )
    follower.apply(0.1)
    assert player.seeks == [pytest.approx(20.0)]

    # Real drift beyond the tolerance is corrected.
    player.position = 5.0
    follower.apply(0.2)
    assert player.seeks[-1] == pytest.approx(20.2)


def test_a_host_scrub_moves_followers_within_one_playback(tmp_path, signer):
    """Dragging the host's scrubber does not start a new playback attempt.

    Seeking leaves the playback generation alone, so the only thing that can
    carry a mid-play jump to a follower is the drift check. If that were
    generation-gated, an artist would keep watching the old spot.
    """

    player = FakePlayer()
    follower = make_follower(player=player, tolerance_s=0.75)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(
            tmp_path,
            signer,
            state="playing",
            position_s=10.0,
            duration_s=1_800.0,
            playback_generation=1,
        ),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    player.position = 10.0

    # The host drags to 900s. Same playback, same generation.
    follower.observe(
        shared_projection(
            tmp_path,
            signer,
            state="playing",
            position_s=900.0,
            duration_s=1_800.0,
            playback_generation=1,
        ),
        received_monotonic_s=1.0,
    )
    snapshot = follower.apply(1.0)

    assert snapshot.target_position_s == pytest.approx(900.0)
    assert player.seeks[-1] == pytest.approx(900.0)
    assert player.state == "playing"


def test_a_host_who_swaps_the_shared_file_stops_followers_on_the_old_one(
    tmp_path, signer
):
    """A follower proven against one file must not follow a different one."""

    player = FakePlayer()
    follower = make_follower(player=player)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=5.0),
        received_monotonic_s=0.0,
    )
    assert follower.apply(0.0).state is ReferenceVideoFollowState.FOLLOWING
    assert player.state == "playing"

    # The host shares a different file, so the published identity changes.
    other = load_reference_video_source(
        write_video(tmp_path / "host-second.mp4", b"a different lesson entirely")
    )
    follower.observe(
        Projection(
            state="playing",
            source_display_name="host-second.mp4",
            identity_digest=signer(other.content_sha256),
            position_s=0.0,
        ),
        received_monotonic_s=1.0,
    )
    snapshot = follower.apply(1.0)

    assert snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
    assert snapshot.can_follow is False
    assert player.state == "paused"


def test_apply_hard_seeks_when_the_host_starts_a_new_playback(tmp_path, signer):
    player = FakePlayer()
    follower = make_follower(player=player)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(
            tmp_path, signer, state="playing", position_s=50.0, playback_generation=1
        ),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    player.position = 50.0

    follower.observe(
        shared_projection(
            tmp_path, signer, state="playing", position_s=0.0, playback_generation=2
        ),
        received_monotonic_s=1.0,
    )
    follower.apply(1.0)
    assert player.seeks[-1] == pytest.approx(0.0)


def test_apply_pauses_the_local_player_when_the_host_pauses(tmp_path, signer):
    player = FakePlayer()
    follower = make_follower(player=player)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=3.0),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    assert player.state == "playing"

    follower.observe(
        shared_projection(tmp_path, signer, state="paused", position_s=6.0),
        received_monotonic_s=1.0,
    )
    follower.apply(1.0)
    assert player.state == "paused"


def test_apply_pauses_when_the_host_withdraws_the_video(tmp_path, signer):
    player = FakePlayer()
    follower = make_follower(player=player)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=3.0),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    assert player.state == "playing"

    follower.observe(Projection(shared=False, state="idle"), received_monotonic_s=1.0)
    snapshot = follower.apply(1.0)
    assert snapshot.state is ReferenceVideoFollowState.NO_VIDEO
    assert player.state == "paused"


def test_apply_pauses_when_a_local_file_stops_matching(tmp_path, signer):
    player = FakePlayer()
    follower = make_follower(player=player)
    mine = write_video(tmp_path / "mine.mp4", b"the lesson")
    follower.open_local_copy(mine)
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=3.0),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    mine.write_bytes(b"replaced")

    snapshot = follower.apply(0.1)
    assert snapshot.state is ReferenceVideoFollowState.FILE_UNAVAILABLE
    assert player.state == "paused"


def test_apply_pauses_a_hidden_video_but_keeps_resolving(tmp_path, signer):
    player = FakePlayer()
    follower = make_follower(player=player)
    follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(
        shared_projection(tmp_path, signer, state="playing", position_s=3.0),
        received_monotonic_s=0.0,
    )
    follower.apply(0.0)
    follower.set_hidden(True)
    assert follower.apply(0.1).state is ReferenceVideoFollowState.HIDDEN
    assert player.state == "paused"


def test_a_local_player_that_cannot_open_the_copy_reports_it(tmp_path, signer):
    player = FakePlayer()
    player.fail_on.add("load")
    follower = make_follower(player=player)
    with pytest.raises(ReferenceVideoPlayerError):
        follower.open_local_copy(write_video(tmp_path / "mine.mp4", b"the lesson"))
    follower.observe(shared_projection(tmp_path, signer), received_monotonic_s=0.0)
    assert follower.resolve(0.0).state is ReferenceVideoFollowState.NEEDS_FILE


def test_the_wire_snapshot_satisfies_the_follower_projection_contract():
    from core.session_transfer import ReferenceVideoSessionSnapshot

    assert isinstance(ReferenceVideoSessionSnapshot(), HostVideoProjection)

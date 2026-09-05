"""Guest copy recovery through the real coordinator, without Qt or codecs."""
from types import SimpleNamespace

import pytest

from core.reference_video import (
    LOCAL_ATTENTION_MESSAGE,
    ReferenceVideoError,
    ReferenceVideoFollower,
    ReferenceVideoFollowState as Follow,
    ReferenceVideoPlayerError,
    load_reference_video_source,
    session_identity_signer,
)
from core.session_transfer import ReferenceVideoSessionSnapshot
from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator

SESSION_ID = "b807abcb-548b-49f7-a2ac-877b38d9e51a"
SESSION_KEY = "private-copy-recovery-session"


class Player:
    def __init__(self):
        self.muted = False
        self.surface = object()
        self.state = "idle"
        self.position = 0.0
        self.loaded = None
        self.calls = []
        self.fail = set()
        self.on_load = None

    def _call(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError("PRIVATE decoder path and details")

    def set_muted(self, value):
        self.muted = value

    def load(self, path):
        self._call("load")
        self.loaded = path
        self.state = "ready"
        self.position = 0.0
        if self.on_load is not None:
            self.on_load()
        return 120.0

    def play(self):
        # A failing adapter may already have started its picture.
        self.state = "playing"
        self._call("play")

    def pause(self):
        self._call("pause")
        self.state = "paused"

    def seek(self, position):
        self._call("seek")
        self.position = position

    def position_s(self):
        self._call("position")
        return self.position

    def close(self):
        self._call("close")
        self.state = "closed"


def write_video(tmp_path, name="copy.mp4", content=b"lesson A"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def host_state(path, *, position=12.0, generation=1, state="playing", shared=True):
    signer = session_identity_signer(session_id=SESSION_ID, session_key=SESSION_KEY)
    source = load_reference_video_source(path)
    return SimpleNamespace(reference_video=ReferenceVideoSessionSnapshot(
        generation=generation, playback_generation=generation, shared=shared,
        state=state, source_display_name="Host lesson.mp4" if shared else "",
        identity_digest=signer(source.content_sha256) if shared else "",
        position_s=position if shared else 0.0, duration_s=120.0 if shared else 0.0,
    ))


def room():
    snapshots = []
    players = []

    def create_player():
        player = Player()
        players.append(player)
        return player

    coordinator = ReferenceVideoCoordinator(
        player_factory=create_player, clock=lambda: 100.0,
        on_follow_snapshot=snapshots.append,
    )
    coordinator.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    return coordinator, players, snapshots


def following(tmp_path):
    coordinator, players, snapshots = room()
    path = write_video(tmp_path)
    coordinator.observe_host_state(host_state(path))
    coordinator.open_local_copy(str(path))
    coordinator.tick()
    assert players[0].state == "playing"
    assert players[0].muted is True
    return coordinator, players[0], snapshots, path


def test_host_changes_lesson_and_guest_opens_new_copy_on_same_player(tmp_path):
    coordinator, player, snapshots, _ = following(tmp_path)
    surface = coordinator.player_surface
    second = write_video(tmp_path, "second.mp4", b"lesson B")
    coordinator.observe_host_state(host_state(second, position=48.0, generation=2))
    coordinator.tick()
    assert snapshots[-1].state is Follow.MISMATCHED_FILE
    assert player.state == "paused"

    coordinator.open_local_copy(str(second))
    coordinator.tick()

    assert snapshots[-1].state is Follow.FOLLOWING
    assert player.position == 48.0
    assert player.loaded == second
    assert player.state == "playing"
    assert coordinator.player_surface is surface
    assert player.calls.count("load") == 2


def test_moved_copy_can_be_reopened_without_closing_it_first(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    moved = path.rename(tmp_path / "moved.mp4")
    coordinator.tick()
    assert snapshots[-1].state is Follow.FILE_UNAVAILABLE

    coordinator.open_local_copy(str(moved))
    coordinator.tick()

    assert snapshots[-1].state is Follow.FOLLOWING
    assert player.loaded == moved
    assert player.state == "playing"


def test_identical_player_attachment_is_idempotent_but_different_player_is_guarded(tmp_path):
    player = Player()
    follower = ReferenceVideoFollower(identity_signer=lambda digest: digest, player=player)
    follower.open_local_copy(write_video(tmp_path))
    follower.set_player(player)
    with pytest.raises(ReferenceVideoError, match="before changing players"):
        follower.set_player(Player())
    with pytest.raises(ReferenceVideoError, match="before changing players"):
        follower.set_player(None)


def test_unreadable_candidate_keeps_working_copy_but_wrong_copy_stops_it(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    with pytest.raises(ReferenceVideoError):
        coordinator.open_local_copy(str(tmp_path / "missing.mp4"))
    assert snapshots[-1].state is Follow.FOLLOWING
    assert player.state == "playing"

    wrong = write_video(tmp_path, "wrong.mp4", b"other bytes")
    with pytest.raises(ReferenceVideoError, match="not the same file"):
        coordinator.open_local_copy(str(wrong))
    assert snapshots[-1].state is Follow.NEEDS_FILE
    assert player.state == "paused"
    assert player.loaded == path
    assert player.calls.count("load") == 1
    coordinator.open_local_copy(str(path))
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING


def test_failed_replacement_load_is_visible_immediately_and_reopen_recovers(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    player.fail.add("load")
    before = len(snapshots)
    with pytest.raises(ReferenceVideoPlayerError, match="could not continue"):
        coordinator.open_local_copy(str(path))
    assert len(snapshots) == before + 1
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert snapshots[-1].can_close_local_copy
    assert not snapshots[-1].can_follow
    assert player.state == "paused"
    assert "PRIVATE" not in repr(snapshots[-1])
    assert snapshots[-1].message == LOCAL_ATTENTION_MESSAGE
    coordinator.tick()
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION

    player.fail.clear()
    coordinator.open_local_copy(str(path))
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING


@pytest.mark.parametrize("failure", ["seek", "play", "pause", "position"])
def test_transport_fault_cannot_republish_following_and_explicit_reopen_recovers(tmp_path, failure):
    coordinator, player, snapshots, path = following(tmp_path)
    player.fail.add(failure)
    if failure == "seek":
        coordinator.observe_host_state(host_state(path, position=48.0, generation=2))
    elif failure == "play":
        coordinator.observe_host_state(host_state(path, state="paused"))
        coordinator.tick()
        coordinator.observe_host_state(host_state(path))
    elif failure == "pause":
        coordinator.observe_host_state(host_state(path, state="paused"))
    coordinator.tick()

    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert snapshots[-1].can_close_local_copy
    assert not snapshots[-1].can_follow
    assert "PRIVATE" not in repr(snapshots[-1])
    player.fail.clear()
    coordinator.tick()
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert player.state == "paused"

    coordinator.open_local_copy(str(path))
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING


@pytest.mark.parametrize("blocked", ["paused", "hidden", "withdrawn", "moved"])
def test_refused_pause_remains_a_retry_obligation(tmp_path, blocked):
    coordinator, player, snapshots, path = following(tmp_path)
    player.fail.add("pause")
    if blocked == "paused":
        coordinator.observe_host_state(host_state(path, state="paused"))
    elif blocked == "hidden":
        coordinator.set_hidden(True)
    elif blocked == "withdrawn":
        coordinator.observe_host_state(host_state(path, state="idle", shared=False))
    else:
        path.rename(tmp_path / "moved.mp4")
    coordinator.tick()
    attempts = player.calls.count("pause")
    assert player.state == "playing"
    assert not snapshots[-1].can_follow
    assert snapshots[-1].can_close_local_copy

    coordinator.tick()
    assert player.calls.count("pause") > attempts
    player.fail.clear()
    coordinator.tick()
    assert player.state == "paused"
    assert not snapshots[-1].can_follow
    coordinator.close_local_copy()
    assert not snapshots[-1].can_close_local_copy


def test_replacement_waits_for_confirmed_pause_before_loading(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    player.fail.add("pause")
    loads = player.calls.count("load")
    with pytest.raises(ReferenceVideoPlayerError, match="couldn't stop"):
        coordinator.open_local_copy(str(path))
    assert player.calls.count("load") == loads
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert snapshots[-1].can_close_local_copy
    player.fail.clear()
    coordinator.open_local_copy(str(path))
    assert player.calls.count("load") == loads + 1


def test_reentrant_tick_during_load_cannot_drive_the_changing_source(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    observed = []

    def during_load():
        before = list(player.calls)
        coordinator.tick()
        observed.append(snapshots[-1])
        assert player.calls == before
        assert not snapshots[-1].can_follow
        with pytest.raises(ReferenceVideoError, match="still opening"):
            coordinator.open_local_copy(str(path))

    player.on_load = during_load
    coordinator.open_local_copy(str(path))
    assert observed
    assert snapshots[-1].state is Follow.FOLLOWING
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING


@pytest.mark.parametrize("cancel", ["close", "end", "new_room"])
def test_close_or_end_during_load_cannot_restore_copy_or_emit_retired_snapshot(tmp_path, cancel):
    coordinator, player, snapshots, path = following(tmp_path)
    after_cancel = []

    def during_load():
        if cancel == "close":
            coordinator.close_local_copy()
        elif cancel == "end":
            coordinator.end()
        else:
            coordinator.begin_guest(session_id="other-room", session_key="other-key")
        after_cancel.append(len(snapshots))

    player.on_load = during_load
    coordinator.open_local_copy(str(path))
    assert len(snapshots) == after_cancel[0]
    if cancel == "close":
        assert not snapshots[-1].can_close_local_copy
    assert not coordinator.follow_snapshot.can_follow
    assert not coordinator.follow_snapshot.can_close_local_copy
    if cancel != "close":
        assert coordinator.player_surface is None
        assert player.state == "closed"


@pytest.mark.parametrize("change", ["removed", "replaced"])
@pytest.mark.parametrize("pause_refused", [False, True])
def test_file_change_after_load_keeps_failed_attempt_reachable_for_recovery(
    tmp_path, change, pause_refused,
):
    coordinator, player, snapshots, path = following(tmp_path)

    def during_load():
        if change == "removed":
            path.unlink()
        else:
            path.write_bytes(b"a different process video")
        if pause_refused:
            player.fail.add("pause")

    player.on_load = during_load
    with pytest.raises(ReferenceVideoError, match="moved, changed"):
        coordinator.open_local_copy(str(path))
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert snapshots[-1].can_close_local_copy
    assert not snapshots[-1].can_follow
    assert str(path) not in snapshots[-1].message
    if pause_refused:
        attempts = player.calls.count("pause")
        coordinator.tick()
        assert player.calls.count("pause") > attempts
    player.fail.clear()
    coordinator.tick()
    assert player.state == "paused"
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    player.on_load = None
    path.write_bytes(b"lesson A")
    if change == "removed":
        coordinator.close_local_copy()
        assert not snapshots[-1].can_close_local_copy
        assert snapshots[-1].state is Follow.NEEDS_FILE
    coordinator.open_local_copy(str(path))
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING
    assert player.state == "playing"


def test_close_failure_notifies_attention_immediately_and_success_clears_it(tmp_path):
    coordinator, player, snapshots, _ = following(tmp_path)
    player.fail.add("pause")
    with pytest.raises(ReferenceVideoPlayerError, match="couldn't stop"):
        coordinator.close_local_copy()
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert snapshots[-1].can_close_local_copy
    player.fail.clear()
    coordinator.close_local_copy()
    assert snapshots[-1].state is Follow.NEEDS_FILE
    assert not snapshots[-1].can_close_local_copy


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), None])
def test_invalid_loaded_duration_never_commits_a_followable_copy(tmp_path, duration):
    coordinator, player, snapshots, path = following(tmp_path)
    player.load = lambda candidate: duration
    with pytest.raises(ReferenceVideoPlayerError):
        coordinator.open_local_copy(str(path))
    assert snapshots[-1].state is Follow.LOCAL_ATTENTION
    assert not snapshots[-1].can_follow


def test_host_update_during_load_is_resolved_again_before_following(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    second = write_video(tmp_path, "next-lesson.mp4", b"lesson B")
    player.on_load = lambda: coordinator.observe_host_state(host_state(second, generation=2))
    coordinator.open_local_copy(str(path))
    assert snapshots[-1].state is Follow.MISMATCHED_FILE
    coordinator.tick()
    assert player.state != "playing"
    player.on_load = None
    coordinator.open_local_copy(str(second))
    coordinator.tick()
    assert snapshots[-1].state is Follow.FOLLOWING


def test_load_failure_after_end_is_retired_without_notifying_new_room(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    after_end = []

    def during_load():
        coordinator.end()
        after_end.append(len(snapshots))
        raise RuntimeError("PRIVATE canceled decoder error")

    player.on_load = during_load
    coordinator.open_local_copy(str(path))
    assert len(snapshots) == after_end[0]
    assert coordinator.follow_snapshot.state is Follow.NO_VIDEO
    assert coordinator.player_surface is None


def test_recovery_preserves_guests_hidden_choice_and_close_still_releases_copy(tmp_path):
    coordinator, player, snapshots, path = following(tmp_path)
    coordinator.set_hidden(True)
    coordinator.tick()
    coordinator.open_local_copy(str(path))
    assert snapshots[-1].state is Follow.HIDDEN
    assert snapshots[-1].can_close_local_copy
    assert coordinator.hidden
    coordinator.close_local_copy()
    assert snapshots[-1].state is Follow.HIDDEN
    assert not snapshots[-1].can_close_local_copy
    coordinator.set_hidden(False)
    assert snapshots[-1].state is Follow.NEEDS_FILE


@pytest.mark.parametrize("replace_inode", [False, True])
@pytest.mark.parametrize("existing_copy", [False, True])
def test_candidate_substitution_after_hash_is_rejected_before_player_load(
    tmp_path, replace_inode, existing_copy,
):
    candidate = write_video(tmp_path, "candidate.mp4")
    original = write_video(tmp_path, "original.mp4")
    signer = session_identity_signer(session_id=SESSION_ID, session_key=SESSION_KEY)
    mutate = [False]

    def sign_verified_digest(digest):
        if mutate[0]:
            if replace_inode:
                replacement = write_video(tmp_path, "replacement.mp4", b"different bytes")
                replacement.replace(candidate)
            else:
                candidate.write_bytes(b"different bytes")
        return signer(digest)

    player = Player()
    follower = ReferenceVideoFollower(identity_signer=sign_verified_digest, player=player)
    follower.observe(host_state(original).reference_video, received_monotonic_s=100.0)
    if existing_copy:
        follower.open_local_copy(original)
        follower.apply(100.0)
    calls = list(player.calls)
    mutate[0] = True

    with pytest.raises(ReferenceVideoError, match="moved, changed"):
        follower.open_local_copy(candidate)

    assert player.calls == calls
    assert follower.resolve(100.0).state is (Follow.FOLLOWING if existing_copy else Follow.NEEDS_FILE)
    assert player.loaded == (original if existing_copy else None)

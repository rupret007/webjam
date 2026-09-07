"""A host's pending video belongs to the room that began opening it."""
from pathlib import Path

import pytest

from core.reference_video import ReferenceVideoError, ReferenceVideoState
from tests.test_reference_video_coordinator import (
    SESSION_ID, SESSION_KEY, FakeHostPeer, FakePlayer,
)
from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator


class LoadingPlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.on_load = lambda: None
        self.loads = []
        self.after_load_error = False

    def load(self, path):
        self.loads.append(path)
        duration = super().load(path)
        self.on_load()
        if self.after_load_error:
            raise RuntimeError("PRIVATE decoder details and local filename")
        return duration


@pytest.fixture
def rig(tmp_path):
    from types import SimpleNamespace

    peer = FakeHostPeer()
    players, snapshots = [], []
    current_peer = [peer]

    def factory():
        player = LoadingPlayer()
        players.append(player)
        return player

    coordinator = ReferenceVideoCoordinator(
        player_factory=factory,
        host_peer_provider=lambda: current_peer[0],
        on_host_snapshot=snapshots.append,
    )
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = tmp_path / "process.mp4"
    path.write_bytes(b"synthetic host process video")
    # Construct the sole player through normal sharing before exercising a
    # replacement; the original may be playing when the next file opens.
    coordinator.share(str(path))
    coordinator.play()
    yield SimpleNamespace(
        coordinator=coordinator, peer=peer, players=players,
        snapshots=snapshots, current_peer=current_peer, path=path,
    )
    coordinator.end()


def test_opening_retires_the_previous_picture_before_the_player_yields(rig):
    coordinator, player = rig.coordinator, rig.players[0]
    observed = []

    def during_load():
        observed.append((coordinator.host_snapshot, rig.snapshots[-1]))
        coordinator.tick()
        assert not rig.peer.published[-1]["shared"]

    player.on_load = during_load
    result = coordinator.share(str(rig.path))

    for snapshot in observed[0]:
        assert snapshot.state.value == "loading"
        assert not snapshot.shared
        assert not snapshot.identity_digest
    assert result.state is ReferenceVideoState.READY
    assert rig.snapshots[-1] == result
    assert player.state == "ready" and player.muted
    assert len(rig.players) == 1


@pytest.mark.parametrize("operation", ["share", "play", "pause", "stop", "seek"])
def test_competing_host_controls_cannot_drive_the_file_being_opened(rig, operation):
    coordinator, player = rig.coordinator, rig.players[0]

    def during_load():
        if operation == "share":
            # Retire the hook so the uncorrected implementation fails by
            # accepting the nested load, rather than recursing indefinitely.
            player.on_load = lambda: None
        action = getattr(coordinator, operation)
        args = (str(rig.path),) if operation == "share" else (10.0,) if operation == "seek" else ()
        with pytest.raises(ReferenceVideoError, match="opening"):
            action(*args)

    player.on_load = during_load
    assert coordinator.share(str(rig.path)).state is ReferenceVideoState.READY
    assert len(player.loads) == 2
    assert player.seeks == []


@pytest.mark.parametrize("finish", ["success", "failure"])
@pytest.mark.parametrize("cancel", ["withdraw", "end", "new_host", "new_guest"])
def test_cancelled_open_cannot_publish_or_render_into_a_later_room(rig, finish, cancel):
    coordinator, player = rig.coordinator, rig.players[0]
    replacement = FakeHostPeer()
    after_cancel = []
    player.after_load_error = finish == "failure"

    def during_load():
        if cancel == "withdraw":
            coordinator.withdraw()
        elif cancel == "end":
            coordinator.end()
        else:
            begin = coordinator.begin_host if cancel == "new_host" else coordinator.begin_guest
            begin(session_id="replacement-room", session_key="replacement-key")
            rig.current_peer[0] = replacement
        after_cancel.append((len(rig.snapshots), len(rig.peer.published)))

    player.on_load = during_load
    result = coordinator.share(str(rig.path))

    assert not result.shared
    assert not coordinator.host_snapshot.shared
    assert (len(rig.snapshots), len(rig.peer.published)) == after_cancel[0]
    assert replacement.published == []
    if cancel != "withdraw":
        assert player.state == "closed"
    else:
        assert coordinator.host_snapshot.state is ReferenceVideoState.IDLE
        player.on_load = lambda: None
        player.after_load_error = False
        assert coordinator.share(str(rig.path)).state is ReferenceVideoState.READY


def test_a_failed_open_has_bounded_recovery_and_reuses_the_silent_player(rig):
    player = rig.players[0]
    player.after_load_error = True
    failed = rig.coordinator.share(str(rig.path))
    assert failed.state is ReferenceVideoState.FAILED
    assert "PRIVATE" not in repr(failed)
    assert rig.snapshots[-1] == failed
    assert rig.peer.published[-1]["needs_attention"]
    player.after_load_error = False
    assert rig.coordinator.share(str(rig.path)).state is ReferenceVideoState.READY
    assert len(rig.players) == 1 and player.muted


def test_an_initial_open_reports_loading_before_the_backend_is_ready(tmp_path):
    snapshots = []
    player = LoadingPlayer()
    coordinator = ReferenceVideoCoordinator(
        player_factory=lambda: player, on_host_snapshot=snapshots.append,
    )
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = Path(tmp_path) / "first.mp4"
    path.write_bytes(b"first lesson")
    seen = []
    player.on_load = lambda: seen.append(tuple(snapshots))
    try:
        coordinator.share(str(path))
        assert seen[0] and seen[0][-1].state.value == "loading"
    finally:
        coordinator.end()


@pytest.mark.parametrize("role", ["host", "guest"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_room_replaced_during_player_creation_cannot_adopt_the_retired_player(
    tmp_path, caplog, role, close_fails,
):
    player = LoadingPlayer()
    snapshots = []

    def factory():
        begin = coordinator.begin_host if role == "host" else coordinator.begin_guest
        begin(session_id="next-room", session_key="next-key")
        return player

    if close_fails:
        def fail_close():
            raise RuntimeError("PRIVATE failed factory cleanup")
        player.close = fail_close
    coordinator = ReferenceVideoCoordinator(player_factory=factory, on_host_snapshot=snapshots.append)
    coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = tmp_path / "factory.mp4"
    path.write_bytes(b"synthetic video")
    try:
        assert not coordinator.share(str(path)).shared
        assert coordinator.role == role
        assert coordinator.player_surface is None
        assert snapshots == [] and player.loads == []
        assert "PRIVATE" not in caplog.text
        if not close_fails:
            assert player.state == "closed"
    finally:
        coordinator.end()


def test_missing_file_returns_to_choose_without_publishing_a_source(rig):
    failed = rig.coordinator.share(str(rig.path.with_name("missing.mp4")))
    assert failed.state is ReferenceVideoState.FAILED
    assert not failed.shared and not failed.identity_digest
    assert rig.snapshots[-1] == failed
    assert rig.peer.published[-1]["shared"] is False
    assert rig.coordinator.share(str(rig.path)).state is ReferenceVideoState.READY


def test_withdrawal_during_ready_publication_wins_over_the_returned_result(rig):
    publish = rig.peer.publish_reference_video_state
    cancel_once = [True]

    def publish_and_cancel(**values):
        publish(**values)
        if values.get("state") == "ready" and cancel_once[0]:
            cancel_once[0] = False
            rig.coordinator.withdraw()

    rig.peer.publish_reference_video_state = publish_and_cancel
    result = rig.coordinator.share(str(rig.path))
    assert rig.snapshots[-1].state is ReferenceVideoState.IDLE
    assert rig.peer.published[-1]["shared"] is False
    assert result == rig.coordinator.host_snapshot

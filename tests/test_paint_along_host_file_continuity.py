"""Post-share host file changes retire proof before the next picture update."""

import os

import pytest

from core import reference_video
from core.reference_video import ReferenceVideoState as State
from tests.test_reference_video_coordinator import (
    FakeHostPeer, FakePlayer, SESSION_ID, SESSION_KEY, make_coordinator, write_video,
)
from tests.test_reference_video_ui import qapp as _qapp

qapp = _qapp


@pytest.mark.parametrize("state", ["ready", "playing", "paused"])
@pytest.mark.parametrize("change", ["rewrite", "replace", "remove", "unreadable"])
def test_next_tick_retires_changed_host_file(tmp_path, monkeypatch, state, change):
    peer = FakeHostPeer()
    host, players, snapshots, _ = make_coordinator(peer=peer)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = write_video(tmp_path / "private-lesson.mp4", b"original")
    original = host.share(str(path))
    if state != "ready":
        host.play()
    if state == "paused":
        host.pause()
    if change == "rewrite":
        # Same size and restored mtime: ctime still retires the old proof.
        before = path.stat()
        path.write_bytes(b"modified")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "replace":
        write_video(tmp_path / "replacement.mp4", b"new file").replace(path)
    elif change == "remove":
        path.unlink()
    else:
        def unavailable(_path):
            raise PermissionError(str(path))
        monkeypatch.setattr(reference_video, "file_identity_token", unavailable)

    start = len(peer.published)
    host.tick()

    failed = host.host_snapshot
    assert failed.state is State.FAILED
    assert failed.needs_attention and not failed.shared
    assert failed.identity_digest == failed.source_display_name == ""
    assert failed.position_s == failed.duration_s == 0
    assert str(path) not in failed.error
    assert snapshots[-1] == failed
    assert players[0].state != "playing"
    assert players[0].muted
    assert len(peer.published) == start + 1
    assert peer.published[-1]["needs_attention"]
    assert not peer.published[-1]["shared"]
    assert peer.published[-1]["identity_digest"] == ""
    host.tick()
    assert not peer.published[-1]["shared"]

    monkeypatch.undo()
    write_video(path, b"a newly chosen lesson")
    recovered = host.share(str(path))
    assert recovered.shared and not recovered.needs_attention
    assert recovered.identity_digest != original.identity_digest
    host.play()
    assert players[0].muted and players[0].state == "playing"
    host.end()


@pytest.mark.parametrize("state", ["ready", "paused"])
def test_play_rechecks_host_file_before_starting_player(tmp_path, state):
    host, players, _, _ = make_coordinator(peer=FakeHostPeer())
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = write_video(tmp_path / "lesson.mp4")
    host.share(str(path))
    if state == "paused":
        host.play()
        host.pause()
    path.write_bytes(b"changed")
    result = host.play()
    assert result.needs_attention and not result.shared
    assert players[0].state != "playing"
    host.end()


def test_unchanged_host_file_ticks_do_not_rehash_or_reload(tmp_path, monkeypatch):
    host, players, _, _ = make_coordinator(peer=FakeHostPeer())
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    original = host.share(str(write_video(tmp_path / "lesson.mp4")))

    def unexpected(*_args):
        pytest.fail("Continuity must use metadata, not reload or hash the video")

    monkeypatch.setattr(reference_video, "load_reference_video_source", unexpected)
    monkeypatch.setattr(reference_video.os, "open", unexpected)
    monkeypatch.setattr(players[0], "load", unexpected)
    for state in (State.READY, State.PLAYING, State.PAUSED):
        if state is State.PLAYING:
            host.play()
        elif state is State.PAUSED:
            host.pause()
        for _ in range(3):
            host.tick()
            assert host.host_snapshot.state is state
            assert host.host_snapshot.identity_digest == original.identity_digest
            assert host.host_snapshot.shared and players[0].muted
    host.end()


@pytest.mark.parametrize("action", ["withdraw", "end", "share"])
def test_newer_host_action_wins_during_stale_file_stop(tmp_path, monkeypatch, action):
    peer = FakeHostPeer()
    host, players, _, _ = make_coordinator(peer=peer)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = write_video(tmp_path / "lesson.mp4")
    host.share(str(path))
    host.play()
    path.unlink()
    replacement = write_video(tmp_path / "new.mp4")
    stop = players[0].stop

    def stop_and_reenter():
        monkeypatch.setattr(players[0], "stop", stop)
        stop()
        if action == "share":
            host.share(str(replacement))
        else:
            getattr(host, action)()

    monkeypatch.setattr(players[0], "stop", stop_and_reenter)
    host.tick()
    assert not host.host_snapshot.needs_attention
    assert peer.published[-1]["state"] == ("ready" if action == "share" else "idle")
    assert host.host_snapshot.shared is (action == "share")
    host.end()


def test_stop_failure_still_retires_host_proof(tmp_path, monkeypatch):
    peer = FakeHostPeer()
    host, players, _, _ = make_coordinator(peer=peer)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = write_video(tmp_path / "lesson.mp4")
    host.share(str(path))
    host.play()
    path.unlink()

    def refuse_stop():
        raise RuntimeError(str(path))

    monkeypatch.setattr(players[0], "stop", refuse_stop)
    host.tick()
    assert not host.host_snapshot.shared
    assert host.host_snapshot.needs_attention
    assert "couldn't stop" in host.host_snapshot.error
    assert str(path) not in host.host_snapshot.error
    assert not peer.published[-1]["identity_digest"]
    host.end()


def test_nested_tick_during_stop_cannot_republish_old_proof(tmp_path, monkeypatch):
    peer = FakeHostPeer()
    host, players, _, _ = make_coordinator(peer=peer)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    path = write_video(tmp_path / "lesson.mp4")
    host.share(str(path))
    host.play()
    path.unlink()
    stop = players[0].stop

    def stop_and_tick():
        assert not host.host_snapshot.shared
        host.tick()
        stop()

    monkeypatch.setattr(players[0], "stop", stop_and_tick)
    start = len(peer.published)
    host.tick()
    assert host.host_snapshot.needs_attention
    assert all(not item["shared"] and not item["identity_digest"]
               for item in peer.published[start:])
    monkeypatch.setattr(players[0], "stop", stop)
    host.end()


def test_online_lesson_never_checks_a_local_file(monkeypatch):
    from tests.test_reference_video_youtube import LessonPlayer, URL
    from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator

    def unexpected(_path):
        pytest.fail("YouTube must not use local file continuity")

    monkeypatch.setattr(reference_video, "file_identity_token", unexpected)
    player = LessonPlayer()
    host = ReferenceVideoCoordinator(
        player_factory=FakePlayer, youtube_player_factory=lambda: player,
    )
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    original = host.share_youtube(URL)
    for action in (host.tick, host.play, host.tick, host.pause, host.tick, host.play):
        action()
        assert host.host_snapshot.shared
        assert host.host_snapshot.identity_digest == original.identity_digest
        assert player.muted
    host.end()


def test_changed_host_file_shows_existing_choose_video_recovery(qapp, tmp_path):
    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    host, _, _, _ = make_coordinator(peer=FakeHostPeer())
    dialog = ReferenceVideoDialog(hosting=True)
    dialog.set_embedded(True)
    dialog.resize(720, 560)
    try:
        host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
        path = write_video(tmp_path / "lesson.mp4")
        host.share(str(path))
        host.play()
        dialog.set_host_snapshot(host.host_snapshot)
        dialog.show()
        path.write_bytes(b"changed file")
        host.tick()
        dialog.set_host_snapshot(host.host_snapshot)
        qapp.processEvents()
        assert dialog._status.text() == (
            "WebJam lost track of that video on this computer. "
            "Use Choose process video… to open a local video again."
        )
        assert dialog._share_button.isVisibleTo(dialog)
        assert dialog._share_button.isEnabled()
        assert not dialog._play_button.isVisibleTo(dialog)
        assert not dialog._pause_button.isVisibleTo(dialog)
        assert not dialog._position.isEnabled()
        assert dialog.rect().contains(dialog._share_button.geometry())

        host.share(str(path))
        dialog.set_host_snapshot(host.host_snapshot)
        assert dialog._status.text() == "Ready."
        assert dialog._play_button.isVisibleTo(dialog)
    finally:
        host.end()
        dialog.close()
        dialog.deleteLater()

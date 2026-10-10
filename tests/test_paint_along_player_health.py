"""Qt backend health reaches Paint along without decoding media under test.

The adapter sees Qt's real error/status enums and event delivery through a
controlled backend. These checks make no claim about installed codecs.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtMultimedia, QtMultimediaWidgets  # noqa: E402
from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.reference_video import ReferenceVideoPlayerError  # noqa: E402
from webjam_qt.widgets.reference_video_player import (  # noqa: E402
    QtReferenceVideoPlayer,
)

_QT_ERROR = QtMultimedia.QMediaPlayer.Error
_QT_STATUS = QtMultimedia.QMediaPlayer.MediaStatus
_PRIVATE_DETAIL = "private-local-video-path-and-backend-detail"
_PLAYBACK_FAILURE = "WebJam couldn't play that video on this computer."


class _MediaBackend:
    Error = _QT_ERROR
    MediaStatus = _QT_STATUS

    def __init__(self, parent=None):
        self.current_error = self.Error.NoError
        self.status = self.MediaStatus.NoMedia
        self.duration_ms = 0
        self.position_ms = 0
        self.source_url = QUrl()
        self.source_history = []
        self.calls = []
        self.after_call = {}
        self.on_load = None
        self.stop_error = None

    def setAudioOutput(self, output):
        self.audio = output

    def setVideoOutput(self, output):
        self.video = output

    def setSource(self, source):
        # Reassigning an unchanged URL does not reopen the source in Qt.
        if source == self.source_url:
            return
        self.source_url = QUrl(source)
        self.source_history.append(QUrl(source))
        self.current_error = self.Error.NoError
        self.status = (
            self.MediaStatus.NoMedia if source.isEmpty()
            else self.MediaStatus.LoadedMedia
        )
        self.duration_ms = 0 if source.isEmpty() else 9_000
        self.position_ms = 0
        if not source.isEmpty() and self.on_load is not None:
            self.on_load(self)

    def error(self):
        return self.current_error

    def errorString(self):
        raise AssertionError(_PRIVATE_DETAIL)

    def mediaStatus(self):
        return self.status

    def duration(self):
        return self.duration_ms

    def _record(self, command):
        self.calls.append(command)
        callback = self.after_call.get(command)
        if callback is not None:
            callback(self)

    def play(self):
        self._record("play")

    def pause(self):
        self._record("pause")

    def stop(self):
        if self.stop_error is not None:
            raise self.stop_error
        self._record("stop")

    def setPosition(self, position):
        self.position_ms = position
        self._record("seek")

    def position(self):
        self._record("position")
        return self.position_ms


class _AudioOutput:
    def __init__(self, parent=None):
        self.muted = False

    def setMuted(self, muted):
        self.muted = muted

    def isMuted(self):
        return self.muted


class _VideoSurface:
    def __init__(self, parent=None):
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def player(qapp, monkeypatch):
    monkeypatch.setattr(QtMultimedia, "QMediaPlayer", _MediaBackend)
    monkeypatch.setattr(QtMultimedia, "QAudioOutput", _AudioOutput)
    monkeypatch.setattr(QtMultimediaWidgets, "QVideoWidget", _VideoSurface)
    adapter = QtReferenceVideoPlayer()
    yield adapter
    adapter.media_player.stop_error = None
    adapter.close()


def _operate(player, operation):
    if operation == "seek":
        return player.seek(2.5)
    return getattr(player, operation)()


def _fail(backend):
    backend.current_error = _QT_ERROR.ResourceError


def test_healthy_playback_and_unloaded_position_preserve_the_silent_seam(player):
    assert player.position_s() == 0.0
    assert player.muted is True
    assert player.load(Path("lesson.mp4")) == 9.0
    player.play()
    player.seek(2.5)
    assert player.position_s() == 2.5
    assert player.muted is True


@pytest.mark.parametrize("operation", ["play", "seek", "position_s"])
@pytest.mark.parametrize("error", [
    _QT_ERROR.ResourceError,
    _QT_ERROR.FormatError,
    _QT_ERROR.NetworkError,
    _QT_ERROR.AccessDeniedError,
])
def test_backend_errors_block_further_playback_and_position_claims(
    player, operation, error,
):
    player.load(Path("lesson.mp4"))
    backend = player.media_player
    backend.current_error = error
    commands_before = list(backend.calls)

    with pytest.raises(ReferenceVideoPlayerError) as caught:
        _operate(player, operation)

    assert str(caught.value) == _PLAYBACK_FAILURE
    assert caught.value.__cause__ is None
    assert backend.calls == commands_before


@pytest.mark.parametrize("operation", ["play", "seek", "position_s"])
def test_invalid_media_is_a_failure_even_without_a_qt_error(player, operation):
    player.load(Path("lesson.mp4"))
    player.media_player.status = _QT_STATUS.InvalidMedia
    assert player.media_player.error() == _QT_ERROR.NoError

    with pytest.raises(ReferenceVideoPlayerError, match="couldn't play"):
        _operate(player, operation)


@pytest.mark.parametrize("operation, command", [
    ("play", "play"), ("seek", "seek"), ("position_s", "position"),
])
def test_failure_raised_by_a_backend_command_is_reported_immediately(
    player, operation, command,
):
    player.load(Path("lesson.mp4"))
    player.media_player.after_call[command] = _fail

    with pytest.raises(ReferenceVideoPlayerError, match="couldn't play"):
        _operate(player, operation)


@pytest.mark.parametrize("fault", ["error", "invalid_media"])
def test_async_failure_after_successful_play_is_seen_by_periodic_position(
    player, qapp, fault,
):
    player.load(Path("lesson.mp4"))
    player.play()
    backend = player.media_player

    def fail_later():
        if fault == "error":
            backend.current_error = _QT_ERROR.FormatError
        else:
            backend.status = _QT_STATUS.InvalidMedia

    QTimer.singleShot(0, fail_later)
    qapp.processEvents()

    with pytest.raises(ReferenceVideoPlayerError, match="couldn't play"):
        player.position_s()


@pytest.mark.parametrize("fault", ["error", "invalid_media", "no_duration", "async_error"])
def test_load_checks_backend_health_even_with_a_positive_duration(player, fault):
    backend = player.media_player

    def load_fault(media):
        if fault == "error":
            media.current_error = _QT_ERROR.AccessDeniedError
        elif fault == "invalid_media":
            media.status = _QT_STATUS.InvalidMedia
        elif fault == "no_duration":
            media.duration_ms = 0
            media.status = _QT_STATUS.NoMedia
        else:
            media.duration_ms = 0
            media.status = _QT_STATUS.LoadingMedia
            QTimer.singleShot(0, lambda: _fail(media))

    backend.on_load = load_fault
    with pytest.raises(ReferenceVideoPlayerError) as caught:
        player.load(Path(_PRIVATE_DETAIL + ".mp4"))

    assert _PRIVATE_DETAIL not in str(caught.value)
    assert backend.source_url.isEmpty()
    # Clearing a failed load's source must not restore an apparently healthy
    # player before the artist explicitly chooses another copy.
    with pytest.raises(ReferenceVideoPlayerError, match="couldn't play"):
        player.play()


@pytest.mark.parametrize("same_path", [False, True])
def test_explicit_load_reopens_a_source_and_recovers_a_prior_error(player, same_path):
    path = Path("lesson.mp4")
    player.load(path)
    backend = player.media_player
    backend.current_error = _QT_ERROR.FormatError
    with pytest.raises(ReferenceVideoPlayerError):
        player.position_s()

    replacement = path if same_path else Path("another-copy.mp4")
    assert player.load(replacement) == 9.0
    assert backend.source_history[-2].isEmpty()
    assert backend.source_history[-1] == QUrl.fromLocalFile(str(replacement))
    player.seek(3.0)
    player.play()
    assert player.position_s() == 3.0
    assert player.muted is True


def test_pause_stop_and_close_remain_available_after_failure(player):
    player.load(Path("lesson.mp4"))
    backend = player.media_player
    backend.current_error = _QT_ERROR.ResourceError
    with pytest.raises(ReferenceVideoPlayerError):
        player.position_s()

    player.pause()
    player.stop()
    # Even if Qt subsequently clears its error, that is not a new load.
    backend.current_error = _QT_ERROR.NoError
    backend.status = _QT_STATUS.LoadedMedia
    with pytest.raises(ReferenceVideoPlayerError):
        player.play()
    assert backend.calls[-2:] == ["pause", "stop"]

    player.close()
    assert backend.source_url.isEmpty()
    assert backend.video is None
    assert player.surface.deleted
    assert player.position_s() == 0.0
    player.stop()
    player.close()
    for operation in ["play", "pause", "seek"]:
        with pytest.raises(ReferenceVideoPlayerError, match="closed"):
            _operate(player, operation)


def test_backend_teardown_details_are_not_logged(player, caplog):
    backend = player.media_player
    backend.stop_error = RuntimeError(_PRIVATE_DETAIL)
    with caplog.at_level(logging.DEBUG, logger="webjam.qt.reference_video_player"):
        player.close()

    assert player.surface.deleted
    assert _PRIVATE_DETAIL not in caplog.text
    records = [r for r in caplog.records if r.name == "webjam.qt.reference_video_player"]
    assert len(records) == 1
    assert records[0].exc_info is None


@pytest.mark.parametrize("status, expected", [
    (_QT_STATUS.EndOfMedia, "ended"),
    (_QT_STATUS.BufferingMedia, "buffering"),
    (_QT_STATUS.StalledMedia, "buffering"),
    (_QT_STATUS.LoadingMedia, "unknown"),
    (_QT_STATUS.LoadedMedia, "unknown"),
    (_QT_STATUS.BufferedMedia, "unknown"),
    (_QT_STATUS.NoMedia, "unknown"),
])
def test_local_completion_observation_uses_media_status_not_position(player, status, expected):
    player.load(Path("lesson.mp4"))
    backend = player.media_player
    backend.position_ms = backend.duration_ms
    backend.status = status
    calls_before = list(backend.calls)

    assert player.playback_state() == expected
    assert backend.calls == calls_before  # Observation never drives transport.
    assert player.muted


@pytest.mark.parametrize("fault", ["error", "invalid_media", "closed"])
def test_local_completion_observation_fails_closed(player, fault):
    player.load(Path("lesson.mp4"))
    player.media_player.status = _QT_STATUS.EndOfMedia
    if fault == "error":
        _fail(player.media_player)
    elif fault == "invalid_media":
        player.media_player.status = _QT_STATUS.InvalidMedia
    else:
        player.close()
    with pytest.raises(ReferenceVideoPlayerError):
        player.playback_state()


@pytest.mark.parametrize("final_position_ms", [8_950, 9_000])
def test_local_completion_reaches_host_guest_and_explicit_transport(player, tmp_path, final_position_ms):
    from core.reference_video import ReferenceVideoState
    from core.session_transfer import SessionControlState
    from tests.test_reference_video_coordinator import (
        Clock, FakeHostPeer, FakePlayer, HostState, SESSION_ID, SESSION_KEY,
        make_coordinator, write_video,
    )
    from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator
    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    clock = Clock()
    peer = FakeHostPeer()
    dialog = ReferenceVideoDialog(hosting=True)
    host = ReferenceVideoCoordinator(
        player_factory=lambda: player, host_peer_provider=lambda: peer,
        clock=clock, on_host_snapshot=dialog.set_host_snapshot,
    )
    guest_player = FakePlayer(duration_s=9.0)
    guest, _, _, _ = make_coordinator(players=[guest_player], clock=clock)
    control = SessionControlState(tmp_path, SESSION_ID)

    def deliver():
        guest.observe_host_state(HostState(control.publish_reference_video(**peer.published[-1])))
        guest.tick()

    try:
        path = write_video(tmp_path / "lesson.mp4")
        host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
        guest.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
        host.share(str(path))
        host.play()
        deliver()
        guest.open_local_copy(str(path))
        guest.tick()
        assert guest_player.state == "playing"
        generation = host.host_snapshot.playback_generation

        backend = player.media_player
        backend.position_ms = final_position_ms
        backend.status = _QT_STATUS.EndOfMedia
        calls_before = list(backend.calls)
        host.tick()

        assert host.host_snapshot.state is ReferenceVideoState.PAUSED
        assert host.host_snapshot.playback_generation == generation
        assert peer.published[-1]["state"] == "paused"
        assert peer.published[-1]["shared"] is True
        assert peer.published[-1]["position_s"] == final_position_ms / 1000
        assert not peer.published[-1]["needs_attention"]
        assert backend.calls == calls_before + ["position"]
        assert dialog._status.text() == "Paused."
        assert dialog._play_button.text() == "Play"
        assert not dialog._play_button.isHidden() and dialog._play_button.isEnabled()
        assert dialog._pause_button.isHidden()
        assert dialog._stop_action.text() == "Restart from the beginning"
        assert dialog._stop_action.isVisible() and dialog._stop_action.isEnabled()
        assert dialog._position.isEnabled()
        deliver()
        assert not guest.follow_snapshot.should_play
        assert guest.follow_snapshot.target_position_s == final_position_ms / 1000
        assert guest_player.state == "paused"

        # Heartbeats retain the final frame; neither side restarts on its own.
        clock.now += 1
        host.tick()
        deliver()
        assert peer.published[-1]["position_s"] == final_position_ms / 1000
        assert not guest.follow_snapshot.should_play
        assert backend.calls == calls_before + ["position"]

        dialog.stop_requested.connect(host.stop)
        dialog.play_requested.connect(host.play)
        dialog._stop_action.trigger()
        assert host.host_snapshot.position_s == 0
        assert host.host_snapshot.state is ReferenceVideoState.READY
        dialog._play_button.click()
        assert host.host_snapshot.state is ReferenceVideoState.PLAYING
        assert host.host_snapshot.playback_generation == generation + 1
        deliver()
        assert guest.follow_snapshot.should_play
        assert guest_player.position == 0
        assert guest_player.state == "playing"
    finally:
        guest.end()
        host.end()
        dialog.deleteLater()

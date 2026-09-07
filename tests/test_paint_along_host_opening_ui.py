"""Real room actions and queued Qt input during the host's duration wait."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6 import QtMultimedia, QtMultimediaWidgets
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFileDialog, QWidget
from shiboken6 import isValid

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState as State
from tests.test_art_lan_host_recovery import host as _host, qapp as _qapp
from tests.test_paint_along_host_opening import LoadingPlayer
from tests.test_paint_along_player_health import _AudioOutput, _MediaBackend
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.widgets.reference_video_player import QtReferenceVideoPlayer
from webjam_qt.windows.reference_video import ReferenceVideoDialog

host, qapp = _host, _qapp


class SurfacePlayer(LoadingPlayer):
    def __init__(self, parent):
        super().__init__()
        self.surface = QWidget(parent)

    def close(self):
        super().close()
        if isValid(self.surface):
            self.surface.hide()
            self.surface.deleteLater()


def _primary(dialog):
    return [button for button in (
        dialog._share_button, dialog._play_button, dialog._pause_button,
        dialog._cancel_open_button, dialog._return_button,
    ) if button.isVisibleTo(dialog) and button.isEnabled()]


@pytest.mark.parametrize("state,action", [
    (State.IDLE, "Choose process video…"), (State.LOADING, "Cancel opening"),
    (State.READY, "Play"), (State.PLAYING, "Pause"),
    (State.PAUSED, "Play"), (State.FAILED, "Choose process video…"),
])
def test_host_has_one_real_next_action_at_compact_size(qapp, state, action):
    dialog = ReferenceVideoDialog(hosting=True)
    dialog.set_embedded(True)
    dialog.resize(720, 560)
    shared = state in {State.READY, State.PLAYING, State.PAUSED}
    snapshot = ReferenceVideoSnapshot(
        state=state, shared=shared, duration_s=120.0 if shared else 0.0,
        identity_digest="a" * 64 if shared else "",
        source_display_name="A long process-video filename " * 12 if shared else "",
        error="Your video could not be opened. Choose it again." if state is State.FAILED else "",
    )
    try:
        dialog.set_host_snapshot(snapshot)
        dialog.show()
        qapp.processEvents()
        assert [button.text() for button in _primary(dialog)] == [action]
        button = _primary(dialog)[0]
        assert dialog.rect().contains(button.geometry())
        assert dialog.rect().contains(dialog._hint.geometry())
        if not shared:
            assert not dialog._position.isEnabled()
        if state is State.LOADING:
            assert "Opening" in dialog._headline.text()
            assert not dialog._more_button.isVisibleTo(dialog)
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.fixture
def host_video(host, monkeypatch):
    rig = host()
    assert isinstance(rig.app, ApplicationController)
    players = []

    def factory(parent):
        player = SurfacePlayer(parent)
        players.append(player)
        return player

    monkeypatch.setattr("webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", factory)
    rig.app._open_reference_video()
    rig.video_path = rig.root / "process.mp4"
    rig.video_path.write_bytes(b"synthetic lesson")
    rig.players = players
    return rig


@pytest.mark.parametrize("operation", ["share", "play", "pause", "stop", "seek", "withdraw"])
def test_retained_host_actions_are_retired_when_room_identity_changes(host_video, operation):
    rig = host_video
    app, dialog = rig.app, rig.app._reference_video_dialog
    coordinator = app._reference_video
    original = app.host_peer.credentials
    app.host_peer.credentials = replace(original, session_id="12345678-1234-4234-9234-123456789abc")
    action = Mock()
    app._run_current_host_paint_along(coordinator, dialog, action)
    action.assert_not_called()
    assert [button.text() for button in _primary(dialog)] == ["Return to room"]
    assert not dialog._position.isEnabled()
    assert rig.players == []
    # A queued selection uses the same gate as the other transport signals.
    signals = {
        "share": (dialog.share_requested, (str(rig.video_path),)),
        "play": (dialog.play_requested, ()), "pause": (dialog.pause_requested, ()),
        "stop": (dialog.stop_requested, ()), "seek": (dialog.seek_requested, (30.0,)),
        "withdraw": (dialog.withdraw_requested, ()),
    }
    signal, args = signals[operation]
    signal.emit(*args)
    assert rig.players == []
    assert not app._reference_video.host_snapshot.shared
    app.host_peer.credentials = original


@pytest.mark.parametrize("cancel", [False, True])
def test_real_host_view_moves_from_opening_to_cancel_or_play(host_video, qapp, cancel):
    rig = host_video
    app, dialog = rig.app, rig.app._reference_video_dialog
    before_identity = app._reference_video_binding
    before_notes = app.window.session_canvas.current_notes()
    app._reference_video.share(str(rig.video_path))
    player = rig.players[0]
    states = []

    def during_load():
        qapp.processEvents()
        states.append(_primary(dialog)[0].text())
        assert dialog._attached_surface is None
        assert not app.host_peer.control.snapshot().reference_video.shared
        if cancel:
            dialog._cancel_open_button.click()
        else:
            dialog._back_button.click()

    player.on_load = during_load
    dialog.share_requested.emit(str(rig.video_path))
    assert states == ["Cancel opening"]
    assert app._reference_video_binding == before_identity
    assert app.window.session_canvas.current_notes() == before_notes
    app._open_reference_video()
    assert [button.text() for button in _primary(dialog)] == [
        "Choose process video…" if cancel else "Play"
    ]
    assert len(rig.players) == 1 and player.muted
    if not cancel:
        dialog._play_button.click()
        assert player.state == "playing"
        assert app.host_peer.control.snapshot().reference_video.state.value == "playing"
    app.bridge.launch_webex.assert_not_called()


def test_stale_native_file_chooser_cannot_load_into_another_room(host_video, monkeypatch):
    rig = host_video
    app, dialog = rig.app, rig.app._reference_video_dialog

    def choose(*args):
        app._release_reference_video()
        app._open_reference_video()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
        return str(rig.video_path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    dialog._choose_shared_video()
    assert app._reference_video_dialog is not dialog
    assert not app._reference_video.host_snapshot.shared
    assert rig.players == []


def test_end_and_new_host_during_loading_keep_the_new_room_empty(host_video, qapp):
    from tests.test_art_lan_host_recovery import _drain

    rig = host_video
    app, dialog = rig.app, rig.app._reference_video_dialog
    app._reference_video.share(str(rig.video_path))
    player = rig.players[0]
    before = app._reference_video_binding
    current = []

    def during_load():
        dialog._back_button.click()
        app.window.session_strip._audio_button.click()
        _drain(qapp, lambda: not app.audio.stopping)
        assert not app.host_peer.active
        assert app.begin_startup_journey()
        rig.tick()
        app._open_reference_video()
        current.append((app._reference_video, app._reference_video_dialog))

    player.on_load = during_load
    dialog.share_requested.emit(str(rig.video_path))
    assert current == [(app._reference_video, app._reference_video_dialog)]
    assert app._reference_video_binding != before
    assert not app._reference_video.host_snapshot.shared
    assert not app.host_peer.control.snapshot().reference_video.shared
    assert [button.text() for button in _primary(app._reference_video_dialog)] == ["Choose process video…"]
    assert player.state == "closed" and len(rig.players) == 1
    app.bridge.launch_webex.assert_not_called()


def test_posted_mouse_cancel_is_processed_inside_the_real_qt_duration_wait(
    host, qapp, monkeypatch,
):
    rig = host()
    monkeypatch.setattr(QtMultimedia, "QMediaPlayer", _MediaBackend)
    monkeypatch.setattr(QtMultimedia, "QAudioOutput", _AudioOutput)
    monkeypatch.setattr(QtMultimediaWidgets, "QVideoWidget", QWidget)
    adapters = []

    def factory(parent):
        adapter = QtReferenceVideoPlayer(parent)
        adapters.append(adapter)

        def loading(backend):
            backend.duration_ms = 0
            dialog = rig.app._reference_video_dialog
            button = dialog._cancel_open_button
            assert _primary(dialog) == [button]
            point = QPointF(button.rect().center())
            for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                  (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
                QCoreApplication.postEvent(button, QMouseEvent(
                    kind, point, point, point, Qt.MouseButton.LeftButton,
                    buttons, Qt.KeyboardModifier.NoModifier,
                ))

        adapter.media_player.on_load = loading
        return adapter

    monkeypatch.setattr("webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", factory)
    app = rig.app
    app._open_reference_video()
    path = rig.root / "cancel.mp4"
    path.write_bytes(b"synthetic delayed process video")
    app._reference_video_dialog.share_requested.emit(str(path))
    assert app._reference_video.host_snapshot.state is State.IDLE
    assert adapters[0].media_player.source_url.isEmpty()
    assert not app.host_peer.control.snapshot().reference_video.shared
    # The same player and file remain usable after cancellation.
    adapters[0].media_player.on_load = None
    app._reference_video_dialog.share_requested.emit(str(path))
    assert app._reference_video.host_snapshot.state is State.READY
    assert len(adapters) == 1 and adapters[0].muted

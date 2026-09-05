"""Real Art room and Paint along actions recover one guest's local picture."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget
from shiboken6 import isValid

from core.art_companion import ArtCompanionProjection, VideoCompanionState
from core.reference_video import (
    ReferenceVideoFollowState,
    load_reference_video_source,
    session_identity_signer,
)
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoPlaybackState, ReferenceVideoSessionSnapshot
from core.settings import AppSettings
from tests.test_art_room_controller import arm_lan, drain, invitation, state
from tests.test_reference_video_coordinator import FakePlayer
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class JourneyPlayer(FakePlayer):
    def __init__(self, parent):
        super().__init__()
        self.surface = QWidget(parent)
        self.fail_on = set()
        self.loads = []

    def _check(self, operation):
        if operation in self.fail_on:
            raise RuntimeError("PRIVATE_MEDIA_DETAIL must never reach room diagnostics")

    def load(self, path):
        self._check("load")
        self.loads.append(path)
        return super().load(path)

    def play(self):
        self._check("play")
        super().play()

    def pause(self):
        self._check("pause")
        super().pause()

    def seek(self, seconds):
        self._check("seek")
        super().seek(seconds)

    def position_s(self):
        self._check("position")
        return super().position_s()

    def close(self):
        super().close()
        if isValid(self.surface):
            self.surface.hide()
            self.surface.deleteLater()


@pytest.fixture
def journey(qapp, monkeypatch, tmp_path):
    made = []
    players = []

    def factory(parent=None):
        player = JourneyPlayer(parent)
        players.append(player)
        return player

    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", factory
    )

    def create(profile="music"):
        root = tmp_path / str(len(made))
        root.mkdir()
        invite = invitation()
        arm_lan(monkeypatch, invite)
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            last_creator_profile_key=profile, musician_name="Alex",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Making room",
        )
        app = ApplicationController(window, settings=settings, session_invite=invite)
        made.append(app)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.bridge.launch_webex = Mock()
        app.window.flash_message = Mock()
        assert app.begin_startup_journey()
        room = app._room_participant
        room.lan_guest.poll_once()
        drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)
        window.resize(760, 600)
        window.show()
        return app, invite, root, players

    yield create
    for app in reversed(made):
        for player in players:
            player.fail_on.clear()
        qapp.processEvents()
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)


def _video(path, payload):
    path.write_bytes(payload)
    return path


def _observe(app, invite, path, *, generation=1, playing=True):
    source = load_reference_video_source(path)
    signer = session_identity_signer(session_id=invite.session_id, session_key=invite.invite_token)
    video = ReferenceVideoSessionSnapshot(
        generation=generation, playback_generation=generation,
        state=ReferenceVideoPlaybackState.PLAYING if playing else ReferenceVideoPlaybackState.PAUSED,
        shared=True, source_display_name=path.name, identity_digest=signer(source.content_sha256),
        position_s=25.0, duration_s=300.0,
    )
    room = app._room_participant
    owner = room.lan_guest
    owner.client.state = lambda *_: replace(state(invite), reference_video=video)
    owner.poll_once()
    QApplication.instance().processEvents()
    app._tick_reference_video()
    return app._reference_video


def _choose(app, monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "Video files"))
    dialog = app._reference_video_dialog
    assert dialog._open_button.isVisibleTo(app.window)
    assert dialog._open_button.isEnabled()
    dialog._open_button.click()
    app._tick_reference_video()


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("change", ["host_video", "moved_copy"])
def test_guest_can_follow_the_next_copy_using_the_offered_action(
    journey, monkeypatch, profile, change,
):
    app, invite, root, players = journey(profile)
    first = _video(root / "first.mp4", b"first lesson")
    video = _observe(app, invite, first)
    _choose(app, monkeypatch, first)
    assert video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    room = app._room_participant
    room_owner, generation = room.lan_guest, room.generation
    if change == "host_video":
        next_copy = _video(root / "next.mp4", b"next lesson")
        _observe(app, invite, next_copy, generation=2)
        assert video.follow_snapshot.state is ReferenceVideoFollowState.MISMATCHED_FILE
    else:
        next_copy = first.with_name("moved.mp4")
        first.rename(next_copy)
        app._tick_reference_video()
        assert video.follow_snapshot.state is ReferenceVideoFollowState.FILE_UNAVAILABLE

    _choose(app, monkeypatch, next_copy)

    assert video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert len(players) == 1 and players[0].muted is True
    assert players[0].loads[-1] == next_copy
    assert room.lan_guest is room_owner and room.generation == generation
    assert room.state is ArtRoomState.CONNECTED
    assert app.creator_profile.key == "art"
    assert app.settings.last_creator_profile_key == profile
    assert app._reference_video_dialog._attached_surface is players[0].surface
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()


@pytest.mark.parametrize("failure", ["seek", "play", "pause", "position"])
def test_local_player_failure_has_visible_recovery_without_changing_the_room(
    journey, monkeypatch, caplog, failure,
):
    app, invite, root, players = journey()
    copy = _video(root / "private-lesson-title.mp4", b"the guest's matching copy")
    video = _observe(app, invite, copy)
    _choose(app, monkeypatch, copy)
    player = players[0]
    if failure == "play":
        _observe(app, invite, copy, generation=2, playing=False)
    player.fail_on.add(failure)
    _observe(app, invite, copy, generation=3, playing=failure != "pause")
    # A position-read fault is observed on the next steady-state tick.
    app._tick_reference_video()
    dialog = app._reference_video_dialog
    assert video.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
    assert video.follow_snapshot.blocked and not video.follow_snapshot.can_follow
    assert dialog._attached_surface is None
    assert dialog._open_button.isVisibleTo(app.window)
    assert dialog._open_button.isEnabled()
    app._sync_art_room_presence()
    overview = app.window.art_room_overview._overview
    assert overview.phase == "connected"
    assert "your" in overview.activity_label.casefold()
    assert "attention" in overview.activity_label.casefold()
    assert overview.activity_action == "video" and overview.activity_enabled
    projection = app.art_room_state()
    assert projection.video is VideoCompanionState.LOCAL_ATTENTION
    assert ArtCompanionProjection(**projection.to_public_dict()) == projection
    assert "private-lesson-title" not in repr(projection)
    assert "PRIVATE_MEDIA_DETAIL" not in caplog.text
    assert "PRIVATE_MEDIA_DETAIL" not in str(app.window.flash_message.call_args_list)

    # Cancelling the chooser preserves the failure and the current room.
    _choose(app, monkeypatch, "")
    assert video.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
    assert len(player.loads) == 1
    player.fail_on.clear()
    _choose(app, monkeypatch, copy)
    assert video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert app._reference_video_dialog._attached_surface is player.surface
    assert app._room_participant.state is ArtRoomState.CONNECTED
    app._launch_native_jamulus_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()

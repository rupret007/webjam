"""A real room receipt and visible guest actions retain one online lesson."""
from dataclasses import replace

from core.reference_video import ReferenceVideoFollowState, session_identity_signer
from core.session_transfer import ReferenceVideoSessionSnapshot
from core.youtube_lesson import YouTubeLesson
from tests.test_paint_along_guest_journey import (
    JourneyPlayer,
    journey as journey,
    qapp as qapp,
)
from tests.test_art_room_controller import state
from tests.test_art_activity_guest_journey import _click, _back_to_room, _room_button


def test_online_guest_room_next_action_open_leave_return_and_withdraw(journey, qapp, monkeypatch):
    app, invite, _, players = journey()
    lesson = YouTubeLesson("M7lc1UVf-VE")
    def factory(parent=None):
        player = JourneyPlayer(parent)
        original_play = player.play
        def play():
            assert player.surface.isVisible(), "online playback requires a visible player"
            original_play()
        player.play = play
        players.append(player)
        return player
    monkeypatch.setattr("webjam_qt.widgets.youtube_video_player.create_youtube_video_player", factory)
    video = ReferenceVideoSessionSnapshot(
        shared=True, generation=1, playback_generation=1, state="playing",
        source_kind="youtube", video_id=lesson.video_id, source_display_name="YouTube lesson",
        identity_digest=session_identity_signer(session_id=invite.session_id,
            session_key=invite.invite_token)(lesson.content_sha256),
        position_s=12, duration_s=300,
    )
    room = app._room_participant
    def publish(value):
        room.lan_guest.client.state = lambda *_: replace(state(invite), reference_video=value)
        room.lan_guest.poll_once()
        qapp.processEvents()
        app._tick_reference_video()
        app._tick_creator_start()
    publish(video)
    _back_to_room(app, qapp)
    button = _room_button(app, "video")
    assert button.text() == "Open lesson"
    _click(button, qapp)
    panel = app._reference_video_dialog
    assert panel._open_button.text() == "Open lesson"
    assert "same file" not in panel._open_button.toolTip()
    _click(panel._open_button, qapp)
    app._tick_reference_video()
    assert players[-1].loads == [lesson]
    assert players[-1].muted and players[-1].state == "playing"
    _back_to_room(app, qapp)
    assert players[-1].state == "paused"
    _click(_room_button(app, "video"), qapp)
    app._tick_reference_video()
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert players[-1].loads == [lesson] and players[-1].state == "playing"
    publish(replace(video, needs_attention=True))
    assert players[-1].state == "paused"
    publish(video)
    assert app._reference_video.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert players[-1].loads == [lesson] and players[-1].state == "playing"
    publish(ReferenceVideoSessionSnapshot(generation=2))
    assert players[-1].state == "paused"
    assert "stopped sharing the lesson" in panel._status.text()
    assert not panel._open_button.isVisible()
    assert panel._close_action.text() == "Close lesson"

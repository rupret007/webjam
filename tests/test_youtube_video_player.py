"""Player readiness is observed, not inferred from sending JavaScript."""

import pytest

from core.reference_video import ReferenceVideoPlayerError
from core.youtube_lesson import YouTubeLesson
from webjam_qt.widgets.youtube_video_player import YouTubeVideoPlayer


class Bridge:
    surface = object()

    def __init__(self):
        self.value = dict(ready=False, muted=True, duration=0, position=0, state=-1, error=0, blocked=False)
        self.clock = 0
        self.pending = None
        self.commands = []
        self.closed = False
        self.respond = True
        self.visible = True

    def load(self, lesson):
        self.pending = ("load", 0)

    def execute(self, action, position=0):
        self.commands.append((action, position))
        self.pending = (action, position)

    def read(self):
        return self.value.copy()

    def pump(self):
        self.clock += .1
        if not self.respond or self.pending is None:
            return
        action, position = self.pending
        self.pending = None
        if action == "load":
            self.value.update(ready=True, muted=True, duration=120, state=5)
        elif action == "play":
            self.value.update(state=1)
        elif action == "pause":
            self.value.update(state=2)
        elif action == "seek":
            self.value.update(position=position)

    def is_visible(self):
        return self.visible

    def clear(self):
        self.value.update(ready=False, state=-1)
        self.pending = None

    def close(self):
        self.closed = True
        self.clear()


def player(bridge=None):
    bridge = bridge or Bridge()
    return YouTubeVideoPlayer(bridge, clock=lambda: bridge.clock, pump=bridge.pump), bridge


def test_cued_lesson_proves_mute_and_duration_before_ready():
    video, bridge = player()
    assert video.muted  # no source exists yet
    assert video.load(YouTubeLesson("M7lc1UVf-VE")) == 120
    assert video.muted and not bridge.commands
    video.play()
    assert bridge.value["state"] == 1
    video.pause()
    video.seek(10)
    assert bridge.value["state"] == 2 and video.position_s() == 10
    video.close()
    assert bridge.closed


def test_queued_play_is_not_reported_as_success():
    video, bridge = player()
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    bridge.respond = False
    with pytest.raises(ReferenceVideoPlayerError):
        video.play()
    bridge.respond = True
    bridge.pump()
    assert bridge.value["state"] != 1
    assert bridge.pending is None


def test_unconfirmed_pause_retires_browser_instead_of_leaving_hidden_playback():
    video, bridge = player()
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    video.play()
    bridge.respond = False
    with pytest.raises(ReferenceVideoPlayerError):
        video.pause()
    assert bridge.value["state"] != 1
    bridge.respond = True
    bridge.pump()
    assert bridge.value["state"] != 1


@pytest.mark.parametrize("change", [
    {"muted": False}, {"error": 101}, {"blocked": True},
    {"position": float("nan")}, {"duration": float("inf")},
])
def test_unhealthy_observations_stop_truthful_playback(change):
    video, bridge = player()
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    bridge.value.update(change)
    with pytest.raises(ReferenceVideoPlayerError):
        video.position_s()
    assert bridge.value["state"] == -1


def test_hidden_player_never_starts_background_playback():
    video, bridge = player()
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    bridge.visible = False
    with pytest.raises(ReferenceVideoPlayerError):
        video.play()
    assert not any(action == "play" for action, _ in bridge.commands)


def test_close_during_metadata_wait_cannot_commit_ready():
    video, bridge = player()
    video._pump = video.close
    with pytest.raises(ReferenceVideoPlayerError):
        video.load(YouTubeLesson("M7lc1UVf-VE"))
    assert bridge.closed


def test_ready_event_precedes_mute_confirmation_and_cannot_allow_play():
    video, bridge = player()
    calls = []
    def pump():
        bridge.clock += .1
        calls.append(True)
        bridge.value.update(ready=True, state=5, duration=120, muted=len(calls) > 1)
    video._pump = pump
    assert video.load(YouTubeLesson("M7lc1UVf-VE")) == 120
    assert len(calls) == 2 and video.muted
    assert not bridge.commands

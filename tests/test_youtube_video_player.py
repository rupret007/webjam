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


@pytest.mark.parametrize("code, expected", [
    (101, "does not allow"), (150, "does not allow"),
    (153, "could not identify"), (100, "unavailable or private"), (5, "could not play"),
])
def test_provider_failure_explains_recovery_and_clears_player(code, expected):
    video, bridge = player()
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    bridge.value["error"] = code
    with pytest.raises(ReferenceVideoPlayerError, match=expected) as failure:
        video.position_s()
    if code != 100:
        assert "Watch a shared lesson" in str(failure.value)
    assert bridge.value["state"] == -1
    assert bridge.commands == []


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


@pytest.fixture
def workspace_player():
    """Real Qt page visibility, with offline browser acknowledgements."""
    from PySide6.QtWidgets import QApplication, QWidget

    from webjam_qt.widgets.youtube_video_player import NativeYouTubeBridge
    from webjam_qt.windows.conductor_window import ConductorWindow
    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    app = QApplication.instance() or QApplication([])
    window = ConductorWindow(
        mode_entries=[("art", "Art")], initial_mode_key="art", initial_title="Art",
    )
    panel = ReferenceVideoDialog(hosting=True, parent=window)
    surface = QWidget()
    surface.setFixedSize(200, 200)
    panel.attach_surface(surface)
    window.show_paint_along(panel)
    window.resize(720, 560)
    window.show()
    app.processEvents()

    # Exercise the production visibility decision without creating a networked
    # native web view; only browser readiness/command responses are simulated.
    class VisibleBridge(Bridge):
        is_visible = NativeYouTubeBridge.is_visible

    bridge = VisibleBridge()
    bridge.surface = surface
    video, bridge = player(bridge)
    video.load(YouTubeLesson("M7lc1UVf-VE"))
    try:
        yield app, window, panel, video, bridge
    finally:
        video.close()
        window.release_paint_along(panel)
        panel.deleteLater()
        window.hide()
        window.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("visible_height", [40, 100])
def test_current_paint_along_page_can_play_a_clipped_surface(workspace_player, visible_height):
    app, window, panel, video, bridge = workspace_player
    # The compact holder may clip the minimum 200x200 browser surface.
    panel._surface_holder.setFixedHeight(visible_height)
    window.activateWindow()
    panel.setFocus()
    app.processEvents()
    assert window.workspace_stack.currentWidget() is panel
    assert panel.hasFocus()
    assert bridge.surface.isVisible()
    visible = bridge.surface.visibleRegion().boundingRect()
    assert 0 < visible.width() * visible.height() <= 200 * 200 / 2

    video.play()

    assert bridge.commands == [("play", 0)]
    assert video.playback_state() == "playing"
    assert video.muted


@pytest.mark.parametrize("hidden_by", ["page", "surface", "window", "minimized"])
def test_real_hidden_surface_blocks_play_until_shown_again(workspace_player, hidden_by):
    app, window, panel, video, bridge = workspace_player
    if hidden_by == "page":
        window.hide_paint_along(panel)
        assert window.workspace_stack.currentWidget() is window.center_splitter
    elif hidden_by == "surface":
        bridge.surface.hide()
    elif hidden_by == "window":
        window.hide()
    else:
        window.showMinimized()
    app.processEvents()

    with pytest.raises(ReferenceVideoPlayerError, match="Return to Paint along"):
        video.play()
    assert bridge.commands == []

    window.showNormal()
    window.show_paint_along(panel)
    bridge.surface.show()
    app.processEvents()
    video.play()
    assert bridge.commands == [("play", 0)]
    assert video.playback_state() == "playing"

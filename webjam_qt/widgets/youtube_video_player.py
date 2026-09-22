"""Silent YouTube IFrame transport over Qt's platform-native web view.

The browser reports readiness and playback; issuing a command proves neither.
Only a validated video ID enters the HTML. No meeting or local-file bridge is
exposed to the page. The native view lives inside the existing Paint along panel.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

from core.reference_video import ReferenceVideoPlayerError
from core.youtube_lesson import YouTubeLesson

APP_ORIGIN = "https://com.webjam.app"
_UNAVAILABLE = "This YouTube lesson could not play here. Try the link again or choose a local video."
_NOT_READY = "The lesson is still loading. Try the link again when your connection is ready."


class YouTubeVideoPlayer:
    def __init__(self, bridge, *, clock=time.monotonic, pump=None):
        self._bridge = bridge
        self._clock = clock
        self._pump = pump or _pump_events
        self._closed = False
        self._loaded = False
        self._generation = 0
        self._duration = 0.0

    @property
    def surface(self):
        return self._bridge.surface

    @property
    def muted(self):
        return not self._loaded or self._snapshot()["muted"] is True

    def set_muted(self, muted):
        if muted is not True:
            raise ReferenceVideoPlayerError("Paint along videos stay silent. Talk in your meeting.")
        if self._loaded:
            self._bridge.execute("mute")

    def _snapshot(self, *, allow_error=False):
        if self._closed:
            raise ReferenceVideoPlayerError("This lesson player is closed.")
        value = self._bridge.read()
        if not isinstance(value, dict):
            self._fail(_UNAVAILABLE)
        if not allow_error:
            if value.get("error"):
                self._fail(_UNAVAILABLE)
            if value.get("blocked"):
                self._fail("The lesson did not start. Open the lesson again, then choose Play.")
            if (value.get("ready") and value.get("muted") is not True
                    and (self._loaded or value.get("state") == 1)):
                self._fail("The lesson could not stay silent. Try the link again or choose a local video.")
        for key in ("position", "duration"):
            number = value.get(key, 0)
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 86_400:
                self._fail(_UNAVAILABLE)
        return value

    def _fail(self, message):
        self._generation += 1
        self._loaded = False
        self._bridge.clear()
        raise ReferenceVideoPlayerError(message)

    def _wait(self, predicate, *, timeout=3.0, allow_error=False):
        generation = self._generation
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if generation != self._generation or self._closed:
                raise ReferenceVideoPlayerError("Opening this lesson was cancelled.")
            snapshot = self._snapshot(allow_error=allow_error)
            if predicate(snapshot):
                return snapshot
            self._pump()
        raise ReferenceVideoPlayerError(_NOT_READY)

    def load(self, lesson: YouTubeLesson):
        if not isinstance(lesson, YouTubeLesson) or self._closed:
            raise ReferenceVideoPlayerError(_UNAVAILABLE)
        self._generation += 1
        self._loaded = False
        self._bridge.load(lesson)
        try:
            value = self._wait(lambda s: s.get("ready") and s.get("muted") is True
                               and s.get("duration", 0) > 0 and s.get("state") in {5, 2}, timeout=12.0)
        except ReferenceVideoPlayerError:
            if not self._closed:
                self._bridge.clear()
            raise
        self._duration = float(value["duration"])
        self._loaded = True
        return self._duration

    def play(self):
        self._require_loaded()
        if not self._bridge.is_visible():
            raise ReferenceVideoPlayerError("Return to Paint along to play this lesson.")
        self._command("play", lambda s: s.get("state") == 1)

    def pause(self):
        if self._closed or not self._loaded:
            return
        self._command("pause", lambda s: s.get("state") in {-1, 0, 2, 5}, allow_error=True)

    def _command(self, action, predicate, *, position=0, allow_error=False):
        self._generation += 1
        generation = self._generation
        try:
            self._bridge.execute(action, position)
            self._wait(predicate, allow_error=allow_error)
        except Exception as exc:
            if not self._closed and generation == self._generation:
                # An unconfirmed pause must never leave a hidden lesson running.
                self._loaded = False
                self._bridge.clear()
            if isinstance(exc, ReferenceVideoPlayerError):
                raise
            raise ReferenceVideoPlayerError(_UNAVAILABLE) from exc

    def stop(self):
        if self._closed:
            return
        if not self._loaded:
            self._generation += 1
            self._bridge.clear()
            return
        self.pause()
        self.seek(0)

    def seek(self, position_s):
        self._require_loaded()
        target = float(position_s)
        if not math.isfinite(target):
            raise ReferenceVideoPlayerError("Choose a position in this lesson.")
        target = max(0.0, min(target, self._duration))
        self._command("seek", lambda s: abs(s.get("position", 0) - target) <= 1.0, position=target)

    def position_s(self):
        self._require_loaded()
        return float(self._snapshot()["position"])

    def playback_state(self):
        self._require_loaded()
        return {0: "ended", 1: "playing", 2: "paused", 3: "buffering", 5: "ready"}.get(
            self._snapshot().get("state"), "buffering",
        )

    def close(self):
        if self._closed:
            return
        self._generation += 1
        self._closed = True
        self._loaded = False
        self._bridge.close()

    def _require_loaded(self):
        if not self._loaded:
            raise ReferenceVideoPlayerError(_NOT_READY)
        self._snapshot()


def _pump_events():
    from PySide6.QtCore import QCoreApplication, QEventLoop, QThread

    QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 25)
    QThread.msleep(10)


def lesson_html(lesson: YouTubeLesson) -> str:
    # Values are validated identifiers, JSON encoded rather than interpolated
    # into executable URL or HTML fragments. Native app identity is the same
    # bundle identifier used by webjam.spec.
    script = r"""
let player = null, ready = false, wanted = false, error = 0, blocked = false;
const expected = VIDEO_ID;
window.onYouTubeIframeAPIReady = function () {
  player = new YT.Player('player', {
    width: '100%', height: '100%', videoId: expected,
    playerVars: {autoplay: 0, controls: 0, disablekb: 1, fs: 0,
                 playsinline: 1, origin: APP_ORIGIN},
    events: {
      onReady: function(e) { e.target.mute(); ready = true; },
      onError: function(e) { error = Number(e.data) || 1; wanted = false; },
      onAutoplayBlocked: function() { blocked = true; wanted = false; },
      onStateChange: function(e) {
        e.target.mute();
        if (!wanted && e.data === 1) e.target.pauseVideo();
      }
    }
  });
};
window.webjamVideo = {
  command: function(action, position) {
    if (!ready || !player) return;
    player.mute();
    if (action === 'play') { wanted = true; player.playVideo(); }
    if (action === 'pause') { wanted = false; player.pauseVideo(); }
    if (action === 'seek') {
      player.seekTo(position, true);
      if (!wanted) player.pauseVideo();
    }
  },
  snapshot: function() {
    if (!ready || !player) return {ready:false,muted:true,duration:0,position:0,state:-1,error:error,blocked:blocked};
    const url = new URL(player.getVideoUrl());
    if (url.searchParams.get('v') !== expected) { player.pauseVideo(); error = 2; }
    return {ready:true, muted:player.isMuted(), duration:player.getDuration(),
      position:player.getCurrentTime(), state:player.getPlayerState(),error:error,blocked:blocked};
  }
};
""".replace("VIDEO_ID", json.dumps(lesson.video_id)).replace("APP_ORIGIN", json.dumps(APP_ORIGIN))
    return ("<!doctype html><html><head><meta name='referrer' content='strict-origin-when-cross-origin'>"
            "<style>html,body,#player{margin:0;width:100%;height:100%;background:#0b0b0b;overflow:hidden}</style>"
            "</head><body><div id='player'></div><script>" + script
            + "</script><script src='https://www.youtube.com/iframe_api'></script></body></html>")


class NativeYouTubeBridge:
    def __init__(self, parent=None):
        from PySide6.QtWebView import QWebView, QWebViewSettings
        from PySide6.QtWidgets import QWidget

        self._view = QWebView()
        settings = self._view.settings()
        for attribute in (QWebViewSettings.WebAttribute.AllowFileAccess,
                          QWebViewSettings.WebAttribute.LocalContentCanAccessFileUrls):
            settings.setAttribute(attribute, False)
        self.surface = QWidget.createWindowContainer(self._view, parent)
        self.surface.setMinimumSize(200, 200)
        self.surface.setAccessibleName("Silent YouTube lesson")
        self._generation = 0
        self._closed = False
        self._pending = False
        self._last_request = 0.0
        self._last_observed = 0.0
        self._value = self._empty()

    @staticmethod
    def _empty():
        return dict(ready=False, muted=True, duration=0, position=0, state=-1, error=0, blocked=False)

    def load(self, lesson):
        from PySide6.QtCore import QUrl

        self._generation += 1
        self._pending = False
        self._value = self._empty()
        self._view.loadHtml(lesson_html(lesson), QUrl(APP_ORIGIN + "/"))

    def read(self):
        now = time.monotonic()
        if not self._closed and not self._pending and now - self._last_request >= .05:
            generation = self._generation
            self._pending = True
            self._last_request = now

            def observed(value):
                if self._closed or generation != self._generation:
                    return
                self._pending = False
                try:
                    snapshot = json.loads(value) if isinstance(value, str) else None
                except ValueError:
                    snapshot = None
                if isinstance(snapshot, dict):
                    self._value = snapshot
                    self._last_observed = time.monotonic()

            self._view.runJavaScript(
                "window.webjamVideo ? JSON.stringify(window.webjamVideo.snapshot()) : null", observed,
            )
        if self._value.get("ready") and now - self._last_observed > 3:
            return dict(self._value, error=1)
        return self._value.copy()

    def execute(self, action, position=0):
        if action not in {"play", "pause", "seek", "mute"} or self._closed:
            return
        self._view.runJavaScript(
            "window.webjamVideo && window.webjamVideo.command("
            + json.dumps(action) + "," + json.dumps(float(position), allow_nan=False) + ")",
            lambda _value: None,
        )

    def is_visible(self):
        size = self.surface.size()
        visible = self.surface.visibleRegion().boundingRect()
        return (self.surface.isVisible() and not self.surface.window().isMinimized()
                and size.width() >= 200 and size.height() >= 200
                and visible.width() * visible.height() > size.width() * size.height() / 2)

    def clear(self):
        from PySide6.QtCore import QUrl

        self._generation += 1
        self._pending = False
        self._value = self._empty()
        self._view.setUrl(QUrl("about:blank"))

    def close(self):
        if self._closed:
            return
        self.clear()
        self._closed = True
        # deleteLater already defers beyond the JavaScript callback. An extra
        # timer can be stranded by quit, leaving a live native window at exit.
        self.surface.deleteLater()


def initialize_youtube_webview():
    from PySide6.QtWebView import QtWebView

    # macOS uses the system WebKit. Qt's Windows native loadHtml discards its
    # base URL; the WebEngine backend preserves the required player identity.
    os.environ["QT_WEBVIEW_PLUGIN"] = "native" if sys.platform == "darwin" else "webengine"
    QtWebView.initialize()


def create_youtube_video_player(parent=None):
    try:
        return YouTubeVideoPlayer(NativeYouTubeBridge(parent))
    except ImportError as exc:
        raise ReferenceVideoPlayerError(
            "This build cannot open YouTube lessons. Choose a local video file instead."
        ) from exc

"""Qt rendering surface for Art's reference video.

This is the only place that touches QtMultimedia. It implements the
:class:`core.reference_video.ReferenceVideoPlayer` seam so every transport,
identity, and follow rule stays testable without a real codec, a real display,
or a real file.

The adapter deliberately owns no policy. It does not decide who may press
play, whether a file matches the host's, or where the position should be; it
only does what it is told and reports what it observes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.reference_video import ReferenceVideoPlayerError

LOGGER = logging.getLogger("webjam.qt.reference_video_player")

# Qt reports media positions in milliseconds.
_MS = 1000.0

# QMediaPlayer resolves duration asynchronously. Loading blocks briefly for it
# so the host learns a real duration before publishing anything to the room.
_DURATION_TIMEOUT_MS = 5_000
_DURATION_POLL_MS = 25


def qt_video_name_filter() -> str:
    """Return the file dialog filter for containers WebJam will accept."""

    from core.reference_video import REFERENCE_VIDEO_SUFFIXES

    patterns = " ".join(f"*{suffix}" for suffix in sorted(REFERENCE_VIDEO_SUFFIXES))
    return f"Video files ({patterns})"


class QtReferenceVideoPlayer:
    """Drive one ``QMediaPlayer`` and expose its own video surface."""

    def __init__(self, parent=None) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise ReferenceVideoPlayerError(
                "This build of WebJam cannot play video on this computer."
            ) from exc

        self._player = QMediaPlayer(parent)
        self._audio = QAudioOutput(parent)
        # Silent from the first frame. A reference video is a picture: the
        # music comes through Jamulus and the voices through whatever meeting
        # app the room is using, and this file is not routed anywhere, so
        # every computer would otherwise play its own copy of the soundtrack
        # over the conversation.
        self._audio.setMuted(True)
        self._player.setAudioOutput(self._audio)
        self._surface = QVideoWidget(parent)
        self._player.setVideoOutput(self._surface)
        self._duration_s = 0.0
        self._closed = False
        self._failed = False

    @property
    def surface(self):
        """The widget a dialog embeds to show the video."""

        return self._surface

    @property
    def media_player(self):
        return self._player

    @property
    def muted(self) -> bool:
        return bool(self._audio.isMuted())

    def set_muted(self, muted: bool) -> None:
        """Mute locally so a reference video never fights the conversation."""

        self._audio.setMuted(bool(muted))

    # -- ReferenceVideoPlayer ------------------------------------------

    def load(self, path: Path) -> float:
        from PySide6.QtCore import QUrl

        self._require_open()
        # Reset the source even when the artist chooses the same path again.
        # Qt may otherwise retain the failed source instead of reopening it.
        self._player.stop()
        self._player.setSource(QUrl())
        self._duration_s = 0.0
        self._failed = False
        try:
            self._player.setSource(QUrl.fromLocalFile(str(Path(path))))
            duration_ms = self._await_duration()
            self._require_healthy()
            if duration_ms <= 0:
                raise ReferenceVideoPlayerError(
                    "WebJam couldn't read that video's length on this computer."
                )
        except ReferenceVideoPlayerError:
            # Clearing the Qt source must not make this failed attempt appear
            # healthy. Only an explicit load begins another attempt.
            self._failed = True
            self._player.setSource(QUrl())
            raise
        self._duration_s = duration_ms / _MS
        return self._duration_s

    def play(self) -> None:
        self._require_healthy()
        self._player.play()
        self._require_healthy()

    def pause(self) -> None:
        self._require_open()
        self._player.pause()

    def stop(self) -> None:
        if self._closed:
            return
        self._player.stop()

    def seek(self, position_s: float) -> None:
        self._require_healthy()
        self._player.setPosition(int(max(0.0, float(position_s)) * _MS))
        self._require_healthy()

    def position_s(self) -> float:
        if self._closed:
            return 0.0
        self._require_healthy()
        position_s = max(0.0, self._player.position() / _MS)
        self._require_healthy()
        return position_s

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        from PySide6.QtCore import QUrl

        try:
            self._player.stop()
            self._player.setSource(QUrl())
            self._player.setVideoOutput(None)
        except Exception:  # noqa: BLE001 - teardown must not mask the reason
            LOGGER.debug("Reference video player teardown failed")
        self._surface.deleteLater()

    # -- internals -----------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise ReferenceVideoPlayerError("This reference video player is closed.")

    def _require_healthy(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        self._require_open()
        if (
            self._player.error() != QMediaPlayer.Error.NoError
            or self._player.mediaStatus() == QMediaPlayer.MediaStatus.InvalidMedia
        ):
            self._failed = True
        if self._failed:
            # Backend error strings can contain the artist's local path. The
            # core receives only this bounded failure, including after a
            # successful load when Qt discovers a later playback error.
            raise ReferenceVideoPlayerError(
                "WebJam couldn't play that video on this computer."
            )

    def _await_duration(self) -> int:
        from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop
        from PySide6.QtMultimedia import QMediaPlayer

        deadline = QDeadlineTimer(_DURATION_TIMEOUT_MS)
        while not deadline.hasExpired():
            self._require_healthy()
            duration = int(self._player.duration())
            status = self._player.mediaStatus()
            if duration > 0:
                return duration
            if status == QMediaPlayer.MediaStatus.NoMedia:
                return 0
            # User input stays excluded so pumping events to resolve a
            # duration cannot re-enter the transport that asked for it.
            QCoreApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents,
                _DURATION_POLL_MS,
            )
        self._require_healthy()
        return int(self._player.duration())


def create_qt_reference_video_player(parent=None) -> QtReferenceVideoPlayer:
    """Player factory suitable for ``ReferenceVideoCoordinator``."""

    return QtReferenceVideoPlayer(parent)


__all__ = [
    "QtReferenceVideoPlayer",
    "create_qt_reference_video_player",
    "qt_video_name_filter",
]

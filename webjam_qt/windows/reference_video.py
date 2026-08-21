"""Studio Visit's reference video panel for hosts and for followers.

The dialog renders immutable snapshots and emits semantic intent. It decides
nothing: whether a file matches, who may press play, and where the position
should be all belong to :mod:`core.reference_video`. Guests are given no
transport control at all, because they have none.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.reference_video import (
    NO_VIDEO_MESSAGE,
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from webjam_qt.theme.tokens import Space

_NO_VIDEO_HEADLINE = "No reference video"
_HOST_HINT = (
    "Optional. Share one local video file you have the right to play. "
    "Everyone in the room watches their own copy of that exact file under "
    "your transport. WebJam ships no video and downloads none."
)
_GUEST_HINT = (
    "Optional. The host controls play, pause, stop, and position. To follow "
    "along, open your own copy of the host's exact file. You can hide the "
    "video and stay in the room."
)
_SYNC_HONESTY = (
    "Position follows the host to within about a second. This is not "
    "frame-accurate review and carries no timecode."
)


def clock_text(seconds: float) -> str:
    bounded = max(0, int(float(seconds or 0.0)))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class ReferenceVideoDialog(QDialog):
    """One panel that serves the host's transport and a follower's status."""

    share_requested = Signal(str)
    withdraw_requested = Signal()
    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(float)

    open_local_copy_requested = Signal(str)
    close_local_copy_requested = Signal()
    hide_requested = Signal(bool)

    def __init__(self, *, hosting: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosting = bool(hosting)
        self._duration_s = 0.0
        self._scrubbing = False
        self.setWindowTitle("Reference Video")
        self.setModal(False)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)

        self._headline = QLabel(_NO_VIDEO_HEADLINE)
        self._headline.setObjectName("ReferenceVideoHeadline")
        self._headline.setAccessibleName("Reference video source")
        layout.addWidget(self._headline)

        self._status = QLabel(NO_VIDEO_MESSAGE)
        self._status.setWordWrap(True)
        self._status.setObjectName("ReferenceVideoStatus")
        self._status.setAccessibleName("Reference video status")
        layout.addWidget(self._status)

        self._surface_holder = QFrame()
        self._surface_holder.setObjectName("ReferenceVideoSurface")
        self._surface_holder.setMinimumHeight(240)
        self._surface_holder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._surface_layout = QVBoxLayout(self._surface_holder)
        self._surface_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._surface_holder, stretch=1)

        self._position = QSlider(Qt.Orientation.Horizontal)
        self._position.setAccessibleName("Reference video position")
        self._position.setRange(0, 0)
        self._position.setEnabled(False)
        self._position.sliderPressed.connect(self._begin_scrub)
        self._position.sliderReleased.connect(self._end_scrub)
        layout.addWidget(self._position)

        self._clock = QLabel("0:00 / 0:00")
        self._clock.setAccessibleName("Reference video position readout")
        layout.addWidget(self._clock)

        controls = QHBoxLayout()
        controls.setSpacing(Space.XS)
        if self._hosting:
            self._share_button = self._add_button(
                controls,
                "Share Video…",
                "Choose one local video file to share with the room.",
                self._choose_shared_video,
            )
            self._play_button = self._add_button(
                controls, "Play", "Play for everyone in the room.", self.play_requested.emit
            )
            self._pause_button = self._add_button(
                controls, "Pause", "Pause for everyone.", self.pause_requested.emit
            )
            self._stop_button = self._add_button(
                controls,
                "Stop",
                "Stop and return everyone to the beginning.",
                self.stop_requested.emit,
            )
            self._withdraw_button = self._add_button(
                controls,
                "Stop Sharing",
                "Return the room to conversation with no video.",
                self.withdraw_requested.emit,
            )
        else:
            self._open_button = self._add_button(
                controls,
                "Open My Copy…",
                "Open your own copy of the host's exact file to follow along.",
                self._choose_local_copy,
            )
            self._close_button = self._add_button(
                controls,
                "Close My Copy",
                "Stop using this file.",
                self.close_local_copy_requested.emit,
            )
            self._hide_button = self._add_button(
                controls,
                "Hide Video",
                "Ignore the video and keep working. You stay in the room.",
                self._toggle_hidden,
            )
        controls.addStretch(1)
        layout.addLayout(controls)

        hint = QLabel(f"{_HOST_HINT if self._hosting else _GUEST_HINT} {_SYNC_HONESTY}")
        hint.setWordWrap(True)
        hint.setObjectName("ReferenceVideoHint")
        layout.addWidget(hint)

        self._hidden = False
        if self._hosting:
            self.set_host_snapshot(ReferenceVideoSnapshot())
        else:
            self.set_follow_snapshot(ReferenceVideoFollowSnapshot())

    # -- construction helpers ------------------------------------------

    def _add_button(self, row, text: str, description: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("GhostButton")
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.setToolTip(description)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    def attach_surface(self, widget: QWidget | None) -> None:
        """Embed the player's own video surface, replacing any previous one."""

        while self._surface_layout.count():
            item = self._surface_layout.takeAt(0)
            existing = item.widget()
            if existing is not None:
                existing.setParent(None)
        if widget is not None:
            self._surface_layout.addWidget(widget)

    # -- user intent ---------------------------------------------------

    def _video_filter(self) -> str:
        from webjam_qt.widgets.reference_video_player import qt_video_name_filter

        return qt_video_name_filter()

    def _choose_shared_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Share a reference video", "", self._video_filter()
        )
        if path:
            self.share_requested.emit(path)

    def _choose_local_copy(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open your copy of the host's video", "", self._video_filter()
        )
        if path:
            self.open_local_copy_requested.emit(path)

    def _toggle_hidden(self) -> None:
        self.hide_requested.emit(not self._hidden)

    def _begin_scrub(self) -> None:
        self._scrubbing = True

    def _end_scrub(self) -> None:
        self._scrubbing = False
        if self._hosting and self._duration_s > 0.0:
            self.seek_requested.emit(float(self._position.value()))

    # -- rendering -----------------------------------------------------

    def set_host_snapshot(self, snapshot: ReferenceVideoSnapshot) -> None:
        """Render host truth. Never infers state the controller did not send."""

        if not self._hosting:
            return
        shared = bool(snapshot.shared)
        state = snapshot.state
        self._duration_s = float(snapshot.duration_s or 0.0)
        self._headline.setText(
            snapshot.source_display_name if shared else _NO_VIDEO_HEADLINE
        )
        if snapshot.error:
            self._status.setText(snapshot.error)
        elif not shared:
            self._status.setText(NO_VIDEO_MESSAGE)
        else:
            self._status.setText(
                {
                    ReferenceVideoState.READY: "Cued for the room. Nothing is playing yet.",
                    ReferenceVideoState.PLAYING: "Playing for the room.",
                    ReferenceVideoState.PAUSED: "Paused for the room.",
                }.get(state, "Shared with the room.")
            )
        self._render_position(snapshot.position_s, self._duration_s)
        self._play_button.setEnabled(shared and state is not ReferenceVideoState.PLAYING)
        self._pause_button.setEnabled(state is ReferenceVideoState.PLAYING)
        self._stop_button.setEnabled(shared)
        self._withdraw_button.setEnabled(shared)
        self._position.setEnabled(shared)

    def set_follow_snapshot(self, snapshot: ReferenceVideoFollowSnapshot) -> None:
        """Render exactly what this computer may honestly claim right now."""

        if self._hosting:
            return
        state = snapshot.state
        self._hidden = state is ReferenceVideoFollowState.HIDDEN
        sharing = state is not ReferenceVideoFollowState.NO_VIDEO
        self._duration_s = float(snapshot.duration_s or 0.0)
        self._headline.setText(
            snapshot.source_display_name if sharing else _NO_VIDEO_HEADLINE
        )
        self._status.setText(snapshot.message)
        self._render_position(snapshot.target_position_s, self._duration_s)
        self._position.setEnabled(False)
        self._open_button.setEnabled(sharing and not self._hidden)
        self._close_button.setEnabled(
            state
            in {
                ReferenceVideoFollowState.FOLLOWING,
                ReferenceVideoFollowState.MISMATCHED_FILE,
                ReferenceVideoFollowState.FILE_UNAVAILABLE,
                ReferenceVideoFollowState.STALLED,
            }
        )
        self._hide_button.setEnabled(sharing)
        self._hide_button.setText("Show Video" if self._hidden else "Hide Video")

    def _render_position(self, position_s: float, duration_s: float) -> None:
        bounded_duration = max(0, int(duration_s or 0.0))
        self._position.setRange(0, bounded_duration)
        if not self._scrubbing:
            self._position.setValue(
                min(max(0, int(position_s or 0.0)), bounded_duration)
            )
        self._clock.setText(
            f"{clock_text(position_s)} / {clock_text(duration_s)}"
        )


__all__ = ["ReferenceVideoDialog", "clock_text"]

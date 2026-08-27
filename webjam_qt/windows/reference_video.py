"""Art's Paint along surface for hosts and for followers.

The dialog renders immutable snapshots and emits semantic intent. It decides
nothing: whether a file matches, who may press play, and where the position
should be all belong to :mod:`core.reference_video`. Guests are given no
transport control at all, because they have none.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.reference_video import (
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from webjam_qt.theme.tokens import Space

_NO_VIDEO_HEADLINE = "No video yet"
_SYNC_HONESTY = (
    "Silent in WebJam · each artist uses their own copy"
)
_SYNC_DETAIL = (
    "Paint along follows the host to within about a second. It is not "
    "frame-accurate and carries no timecode. WebJam does not ship or download "
    "the video, and cannot confirm who has opened or watched it."
)

_FOLLOW_STATUS = {
    ReferenceVideoFollowState.NO_VIDEO: "Waiting for the host to share a video.",
    ReferenceVideoFollowState.NEEDS_FILE: (
        "Open your own copy of the same file to follow along."
    ),
    ReferenceVideoFollowState.MISMATCHED_FILE: (
        "That is not the same file. Open the host's exact copy."
    ),
    ReferenceVideoFollowState.FILE_UNAVAILABLE: (
        "Your copy moved or changed. Open it again to continue."
    ),
    ReferenceVideoFollowState.HOST_ATTENTION: (
        "The host needs to check the video before it can continue."
    ),
    ReferenceVideoFollowState.STALLED: (
        "The host's position is out of date, so playback paused here."
    ),
    ReferenceVideoFollowState.FOLLOWING: "Following the host.",
    ReferenceVideoFollowState.HIDDEN: (
        "Hidden on this computer. You are still in the room."
    ),
}


def clock_text(seconds: float) -> str:
    bounded = max(0, int(float(seconds or 0.0)))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class ReferenceVideoDialog(QDialog):
    """The large, quiet making surface behind Art's Paint along door."""

    share_requested = Signal(str)
    withdraw_requested = Signal()
    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(float)

    open_local_copy_requested = Signal(str)
    close_local_copy_requested = Signal()
    hide_requested = Signal(bool)
    return_requested = Signal()

    def __init__(self, *, hosting: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosting = bool(hosting)
        self._duration_s = 0.0
        self._scrubbing = False
        self._attached_surface: QWidget | None = None
        self.setObjectName("PaintAlongWindow")
        self.setWindowTitle("Paint along")
        self.setModal(False)
        self.setMinimumSize(720, 520)
        self.resize(1040, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)

        header = QHBoxLayout()
        header.setSpacing(Space.SM)
        self._title = QLabel("Paint along")
        self._title.setObjectName("PaintAlongTitle")
        self._title.setAccessibleName("Paint along")
        header.addWidget(self._title)
        header.addStretch(1)
        self._back_button = QPushButton("Back to room")
        self._back_button.setObjectName("QuietButton")
        self._back_button.setAccessibleName("Back to room")
        self._back_button.clicked.connect(self.return_requested.emit)
        self._back_button.setVisible(False)
        header.addWidget(self._back_button)
        self._role = QLabel("HOST" if self._hosting else "GUEST")
        self._role.setObjectName("PaintAlongRole")
        self._role.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._role.setFixedHeight(28)
        self._role.setAccessibleName(
            "You control the video" if self._hosting else "You follow the host"
        )
        header.addWidget(self._role)
        layout.addLayout(header)

        self._headline = QLabel(_NO_VIDEO_HEADLINE)
        self._headline.setObjectName("PaintAlongHeadline")
        self._headline.setAccessibleName("Paint along video")
        layout.addWidget(self._headline)

        self._status = QLabel(
            "Choose one local video to begin."
            if self._hosting
            else _FOLLOW_STATUS[ReferenceVideoFollowState.NO_VIDEO]
        )
        self._status.setWordWrap(True)
        self._status.setObjectName("PaintAlongStatus")
        self._status.setAccessibleName("Paint along status")
        layout.addWidget(self._status)

        self._surface_holder = QFrame()
        self._surface_holder.setObjectName("PaintAlongSurface")
        self._surface_holder.setMinimumHeight(360)
        self._surface_holder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._surface_layout = QVBoxLayout(self._surface_holder)
        self._surface_layout.setContentsMargins(0, 0, 0, 0)
        self._surface_placeholder = QLabel()
        self._surface_placeholder.setObjectName("PaintAlongPlaceholder")
        self._surface_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._surface_placeholder.setWordWrap(True)
        self._surface_placeholder.setAccessibleName("Paint along video surface")
        self._surface_layout.addWidget(self._surface_placeholder)
        layout.addWidget(self._surface_holder, stretch=1)

        self._position = QSlider(Qt.Orientation.Horizontal)
        self._position.setAccessibleName("Paint along position")
        self._position.setRange(0, 0)
        self._position.setEnabled(False)
        self._position.sliderPressed.connect(self._begin_scrub)
        self._position.sliderReleased.connect(self._end_scrub)
        self._clock = QLabel("0:00 / 0:00")
        self._clock.setObjectName("PaintAlongClock")
        self._clock.setAccessibleName("Paint along position readout")
        timeline = QHBoxLayout()
        timeline.setSpacing(Space.SM)
        timeline.addWidget(self._position, stretch=1)
        timeline.addWidget(self._clock)
        layout.addLayout(timeline)

        controls = QHBoxLayout()
        controls.setSpacing(Space.XS)
        if self._hosting:
            self._share_button = self._add_button(
                controls,
                "Share…",
                "Choose one local video file to share with the room.",
                self._choose_shared_video,
            )
            self._play_button = self._add_button(
                controls,
                "Play",
                "Start the host-controlled video.",
                self.play_requested.emit,
            )
            self._pause_button = self._add_button(
                controls, "Pause", "Pause for everyone.", self.pause_requested.emit
            )
        else:
            self._open_button = self._add_button(
                controls,
                "Open my copy…",
                "Open your own copy of the host's exact file to follow along.",
                self._choose_local_copy,
            )
            self._hide_button = self._add_button(
                controls,
                "Hide video",
                "Ignore the video and keep working. You stay in the room.",
                self._toggle_hidden,
            )
        controls.addStretch(1)
        self._more_button = QToolButton()
        self._more_button.setText("More")
        self._more_button.setObjectName("PaintAlongMore")
        self._more_button.setAccessibleName("More Paint along options")
        self._more_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._more_menu = QMenu(self._more_button)
        self._more_button.setMenu(self._more_menu)
        if self._hosting:
            self._change_action = self._add_action(
                "Choose another video…", self._choose_shared_video
            )
            self._stop_action = self._add_action(
                "Restart from the beginning",
                lambda: self.stop_requested.emit(),
            )
            self._more_menu.addSeparator()
            self._withdraw_action = self._add_action(
                "Stop sharing", lambda: self.withdraw_requested.emit()
            )
        else:
            self._close_action = self._add_action(
                "Close my copy", lambda: self.close_local_copy_requested.emit()
            )
            self._hide_action = self._add_action(
                "Hide video", self._toggle_hidden
            )
        controls.addWidget(self._more_button)
        layout.addLayout(controls)

        self._hint = QLabel(_SYNC_HONESTY)
        self._hint.setObjectName("PaintAlongHint")
        self._hint.setAccessibleName("How Paint along works")
        self._hint.setAccessibleDescription(_SYNC_DETAIL)
        self._hint.setToolTip(_SYNC_DETAIL)
        layout.addWidget(self._hint)

        self._hidden = False
        if self._hosting:
            self.set_host_snapshot(ReferenceVideoSnapshot())
        else:
            self.set_follow_snapshot(ReferenceVideoFollowSnapshot())

    # -- construction helpers ------------------------------------------

    def _add_button(self, row, text: str, description: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("PrimaryButton")
        button.setMinimumWidth(152)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.setToolTip(description)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    def _add_action(self, text: str, slot) -> QAction:
        action = QAction(text, self._more_menu)
        action.triggered.connect(lambda _checked=False: slot())
        self._more_menu.addAction(action)
        return action

    def _sync_more_button(self) -> None:
        offered = any(
            action.isVisible() and not action.isSeparator()
            for action in self._more_menu.actions()
        )
        self._more_button.setVisible(offered)

    def set_embedded(self, embedded: bool) -> None:
        """Adapt the surface for WebJam's single-window workspace stack."""

        embedded = bool(embedded)
        self._back_button.setVisible(embedded)
        if embedded:
            # The surrounding strip, HUD, and 72px room bar still need to fit
            # on WebJam's supported 760x600 floor. Normal desktops let the
            # expanding surface take all remaining room.
            self.setMinimumSize(0, 0)
            self._surface_holder.setMinimumHeight(220)
        else:
            self.setMinimumSize(720, 520)
            self._surface_holder.setMinimumHeight(360)

    def attach_surface(self, widget: QWidget | None) -> None:
        """Embed the player's own video surface, replacing any previous one."""

        existing = self._attached_surface
        if existing is widget:
            return
        if existing is not None:
            self._surface_layout.removeWidget(existing)
            # A QWidget with no parent becomes another top-level window, even
            # while hidden. Keep the sole player inside WebJam while its
            # picture is not currently truthful enough to show.
            existing.hide()
            existing.setParent(self._surface_holder)
        self._attached_surface = widget
        if widget is not None:
            self._surface_layout.addWidget(widget)
            widget.show()
        self._surface_placeholder.setVisible(widget is None)

    # -- user intent ---------------------------------------------------

    def _video_filter(self) -> str:
        from webjam_qt.widgets.reference_video_player import qt_video_name_filter

        return qt_video_name_filter()

    def _choose_shared_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a video for Paint along", "", self._video_filter()
        )
        if path:
            self.share_requested.emit(path)

    def _choose_local_copy(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open your copy for Paint along", "", self._video_filter()
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
        self._surface_placeholder.setText(
            "Video unavailable" if snapshot.error else "Share a video to begin"
        )
        if snapshot.error:
            self._status.setText(snapshot.error)
        elif not shared:
            self._status.setText("Choose one local video to begin.")
        else:
            # Deliberately states this computer's transport and nothing about
            # what anyone else is seeing, which WebJam cannot observe.
            self._status.setText(
                {
                    ReferenceVideoState.READY: "Ready.",
                    ReferenceVideoState.PLAYING: "Playing.",
                    ReferenceVideoState.PAUSED: "Paused.",
                }.get(state, "Ready.")
            )
        self._render_position(snapshot.position_s, self._duration_s)
        # Transport that cannot act on anything is hidden rather than greyed
        # out: a disabled row of buttons is a small taunt repeated every time
        # someone looks at the panel, and a room with no video is a finished
        # state rather than a broken one.
        playing = state is ReferenceVideoState.PLAYING
        self._share_button.setVisible(not shared)
        self._play_button.setVisible(shared and not playing)
        self._pause_button.setVisible(playing)
        self._play_button.setEnabled(shared and not playing)
        self._pause_button.setEnabled(playing)
        self._change_action.setVisible(shared)
        self._stop_action.setVisible(shared)
        self._withdraw_action.setVisible(shared)
        self._sync_more_button()
        self._position.setVisible(shared)
        self._position.setEnabled(shared)
        self._clock.setVisible(shared)

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
        self._status.setText(_FOLLOW_STATUS[state])
        self._surface_placeholder.setText(
            {
                ReferenceVideoFollowState.NO_VIDEO: "Waiting for the host",
                ReferenceVideoFollowState.NEEDS_FILE: "Open your copy to see the video",
                ReferenceVideoFollowState.MISMATCHED_FILE: "Open the matching copy",
                ReferenceVideoFollowState.FILE_UNAVAILABLE: "Open your copy again",
                ReferenceVideoFollowState.HOST_ATTENTION: "Waiting for the host",
                ReferenceVideoFollowState.STALLED: "Playback paused",
                ReferenceVideoFollowState.HIDDEN: "Video hidden on this computer",
            }.get(state, "Paint along")
        )
        self._render_position(snapshot.target_position_s, self._duration_s)
        self._position.setEnabled(False)
        # A guest in a room with no video has nothing to open, close, or hide,
        # so none of it is offered.
        holds_copy = state in {
            ReferenceVideoFollowState.FOLLOWING,
            ReferenceVideoFollowState.MISMATCHED_FILE,
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
            ReferenceVideoFollowState.STALLED,
        }
        needs_copy = state in {
            ReferenceVideoFollowState.NEEDS_FILE,
            ReferenceVideoFollowState.MISMATCHED_FILE,
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
        }
        following_or_hidden = state in {
            ReferenceVideoFollowState.FOLLOWING,
            ReferenceVideoFollowState.HIDDEN,
        }
        self._open_button.setVisible(needs_copy)
        self._open_button.setEnabled(needs_copy)
        self._hide_button.setVisible(following_or_hidden)
        self._hide_button.setEnabled(following_or_hidden)
        self._hide_button.setText("Show video" if self._hidden else "Hide video")
        self._close_action.setVisible(holds_copy)
        self._hide_action.setVisible(sharing and not following_or_hidden)
        self._hide_action.setText("Show video" if self._hidden else "Hide video")
        self._sync_more_button()
        timeline_visible = state in {
            ReferenceVideoFollowState.FOLLOWING,
            ReferenceVideoFollowState.STALLED,
        }
        self._position.setVisible(timeline_visible)
        self._clock.setVisible(timeline_visible)

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

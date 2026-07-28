"""Host-facing controls for the Jamulus-routed Reference Track."""

from __future__ import annotations

from time import monotonic
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


def _clock_text(seconds: float) -> str:
    bounded = max(0, int(float(seconds or 0.0)))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class ReferenceTrackDialog(QDialog):
    """Render immutable Reference Track state and emit semantic user intent."""

    load_requested = Signal(str)
    play_requested = Signal()
    pause_requested = Signal()
    restart_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(float)
    loop_requested = Signal(float, object)
    trim_requested = Signal(float)
    count_in_requested = Signal(int, float)

    _SEEK_STEPS = 10_000
    # A fast controller edit normally acknowledges synchronously or on the
    # next 250 ms refresh.  Retain optimistic keyboard values across a few
    # stale snapshots, but eventually return to controller truth if an edit
    # was rejected or the operation lock was busy.
    _PENDING_HOLD_SECONDS = 1.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._snapshot = None
        self._syncing = False
        self._rendered_state = "unavailable"
        # Controller snapshots arrive every 250 ms.  Keep a just-committed
        # keyboard edit on screen until the controller echoes it back instead
        # of briefly replacing it with the preceding snapshot.
        self._pending_seek_value: int | None = None
        self._pending_loop: tuple[bool, float, float | None] | None = None
        self._pending_trim: float | None = None
        self._pending_count_in: tuple[int, float] | None = None
        self._pending_seek_value_deadline = 0.0
        self._pending_loop_deadline = 0.0
        self._pending_trim_deadline = 0.0
        self._pending_count_in_deadline = 0.0
        self.setObjectName("ReferenceTrackDialog")
        self.setWindowTitle("Reference Track")
        self.setModal(False)
        # Keep the complete dialog inside WebJam's supported 760×600 screen
        # floor after the macOS title bar is added.
        self.setMinimumSize(500, 500)
        self.resize(620, 540)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setObjectName("ReferenceTrackScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setAccessibleName("Reference Track controls")
        self._scroll_area.setAccessibleDescription(
            "Scroll vertically to reach every Reference Track control and its "
            "routing safety guidance."
        )
        outer.addWidget(self._scroll_area)

        content = QWidget(self._scroll_area)
        content.setObjectName("ReferenceTrackContent")
        self._scroll_area.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Reference Track")
        title.setObjectName("SimpleSettingsTitle")
        root.addWidget(title)

        intro = QLabel(
            "The host can send a song through a separate “WebJam Track” "
            "Jamulus participant. Every musician controls that participant in "
            "their own mix."
        )
        intro.setObjectName("SimpleSettingsSubtitle")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(intro)

        self._status = QLabel("Checking the isolated audio route…")
        self._status.setObjectName("DialogStatus")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setAccessibleName("Reference Track status")
        self._status.setAccessibleDescription(self._status.text())
        root.addWidget(self._status)

        self._route = QLabel("")
        self._route.setObjectName("DialogHint")
        self._route.setWordWrap(True)
        self._route.setTextFormat(Qt.TextFormat.PlainText)
        self._route.setAccessibleName("Reference Track routing status")
        self._route.setAccessibleDescription("")
        root.addWidget(self._route)

        source_row = QHBoxLayout()
        self._source = QLabel("No song loaded")
        self._source.setObjectName("SimpleSettingsFieldLabel")
        self._source.setTextFormat(Qt.TextFormat.PlainText)
        self._source.setAccessibleName("Loaded Reference Track")
        source_row.addWidget(self._source, 1)
        self._load = QPushButton("Load Song…")
        self._load.setObjectName("GhostButton")
        self._load.setAccessibleName("Load a Reference Track audio file")
        self._load.clicked.connect(self._choose_source)
        source_row.addWidget(self._load)
        root.addLayout(source_row)

        time_row = QHBoxLayout()
        self._time = QLabel("0:00 / 0:00")
        self._time.setAccessibleName("Reference Track playback position")
        time_row.addWidget(self._time)
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, self._SEEK_STEPS)
        self._seek.setAccessibleName("Paused Reference Track position")
        self._seek.setToolTip("Seeking is available only while paused.")
        self._seek.sliderReleased.connect(self._emit_seek)
        self._seek.valueChanged.connect(self._emit_keyboard_seek)
        time_row.addWidget(self._seek, 1)
        root.addLayout(time_row)

        transport = QHBoxLayout()
        self._play = QPushButton("Play")
        self._play.setObjectName("PrimaryButton")
        self._play.setAccessibleName("Play Reference Track through Jamulus")
        self._play.clicked.connect(self.play_requested.emit)
        self._pause = QPushButton("Pause")
        self._pause.setObjectName("GhostButton")
        self._pause.clicked.connect(self.pause_requested.emit)
        self._restart = QPushButton("Restart")
        self._restart.setObjectName("GhostButton")
        self._restart.clicked.connect(self.restart_requested.emit)
        self._stop = QPushButton("Stop")
        self._stop.setProperty("destructive", "true")
        self._stop.clicked.connect(self.stop_requested.emit)
        transport.addWidget(self._play)
        transport.addWidget(self._pause)
        transport.addWidget(self._restart)
        transport.addWidget(self._stop)
        transport.addStretch(1)
        root.addLayout(transport)

        controls = QFormLayout()
        controls.setHorizontalSpacing(Space.MD)
        controls.setVerticalSpacing(Space.SM)

        loop_row = QHBoxLayout()
        self._loop = QCheckBox("Loop")
        self._loop.setAccessibleName("Loop a Reference Track range")
        self._loop.toggled.connect(self._emit_loop)
        self._loop_start = QDoubleSpinBox()
        self._loop_start.setRange(0.0, 0.0)
        self._loop_start.setDecimals(2)
        self._loop_start.setSuffix(" s")
        self._loop_start.setAccessibleName("Reference Track loop start")
        self._loop_start.editingFinished.connect(self._emit_loop)
        self._loop_end = QDoubleSpinBox()
        self._loop_end.setRange(0.0, 0.0)
        self._loop_end.setDecimals(2)
        self._loop_end.setSuffix(" s")
        self._loop_end.setAccessibleName("Reference Track loop end")
        self._loop_end.editingFinished.connect(self._emit_loop)
        loop_row.addWidget(self._loop)
        loop_row.addWidget(QLabel("In"))
        loop_row.addWidget(self._loop_start)
        loop_row.addWidget(QLabel("Out"))
        loop_row.addWidget(self._loop_end)
        controls.addRow("Loop range", loop_row)

        self._trim = QDoubleSpinBox()
        self._trim.setRange(-60.0, 12.0)
        self._trim.setDecimals(1)
        self._trim.setSingleStep(0.5)
        self._trim.setSuffix(" dB")
        self._trim.setAccessibleName("Reference Track source trim")
        self._trim.editingFinished.connect(self._emit_trim)
        controls.addRow("Source trim", self._trim)

        count_row = QHBoxLayout()
        self._count_in = QSpinBox()
        self._count_in.setRange(0, 8)
        self._count_in.setSuffix(" beats")
        self._count_in.setAccessibleName("Reference Track audible count-in beats")
        self._count_bpm = QDoubleSpinBox()
        self._count_bpm.setRange(40.0, 240.0)
        self._count_bpm.setDecimals(1)
        self._count_bpm.setSuffix(" BPM")
        self._count_bpm.setAccessibleName("Reference Track count-in tempo")
        self._count_in.editingFinished.connect(self._emit_count_in)
        self._count_bpm.editingFinished.connect(self._emit_count_in)
        count_row.addWidget(self._count_in)
        count_row.addWidget(self._count_bpm)
        controls.addRow("Audible count-in", count_row)
        root.addLayout(controls)

        self._safety = QLabel(
            "Jamulus-routed—not latency eliminated. The track uses Jamulus’s "
            "normal buffers, jitter handling, and network delay. The host hears "
            "it only through the primary Jamulus mix; WebJam refuses playback "
            "if route isolation or backing-client control is not proven. A "
            "server recording captures the track as its own stem."
        )
        self._safety.setObjectName("DialogHint")
        self._safety.setWordWrap(True)
        self._safety.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self._safety)
        root.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._done = QPushButton("Done")
        self._done.setObjectName("GhostButton")
        self._done.clicked.connect(self.close)
        footer.addWidget(self._done)
        root.addLayout(footer)

        # Enter commits spin-box edits; it must never activate an unrelated
        # transport or file-picker button through QDialog's auto-default rule.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

        self._set_controls_enabled(False, state="unavailable")

    def _choose_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Reference Track",
            "",
            "Audio files (*.wav *.wave *.aif *.aiff *.flac *.mp3)",
        )
        if path:
            self.load_requested.emit(path)

    def _emit_seek(self) -> None:
        if (
            self._syncing
            or self._rendered_state != "paused"
            or not self._seek.isEnabled()
        ):
            return
        snapshot = self._snapshot
        duration = float(getattr(snapshot, "duration_s", 0.0) or 0.0)
        if duration <= 0.0:
            return
        value = int(self._seek.value())
        snapshot_position = min(
            duration,
            max(
                0.0,
                float(getattr(snapshot, "position_s", 0.0) or 0.0),
            ),
        )
        snapshot_value = round(
            snapshot_position / duration * self._SEEK_STEPS
        )
        if value == self._pending_seek_value or (
            self._pending_seek_value is None and value == snapshot_value
        ):
            return
        self._pending_seek_value = value
        self._hold_pending_edit("seek_value")
        position = duration * float(value) / self._SEEK_STEPS
        self.seek_requested.emit(position)

    def _emit_keyboard_seek(self, _value: int) -> None:
        if (
            not self._syncing
            and self._seek.hasFocus()
            and not self._seek.isSliderDown()
        ):
            self._emit_seek()

    def _emit_loop(self) -> None:
        if self._syncing or not self._edits_allowed():
            return
        if not self._loop.isChecked():
            desired = (False, 0.0, None)
        else:
            desired = (
                True,
                float(self._loop_start.value()),
                float(self._loop_end.value()),
            )
        if self._loop_intents_match(desired, self._pending_loop) or (
            self._pending_loop is None
            and self._loop_intents_match(
                desired,
                self._snapshot_loop_intent(),
            )
        ):
            return
        self._pending_loop = desired
        self._hold_pending_edit("loop")
        _enabled, start, end = desired
        self.loop_requested.emit(start, end)

    def _emit_trim(self) -> None:
        if self._syncing or not self._edits_allowed():
            return
        value = float(self._trim.value())
        snapshot_value = float(
            getattr(self._snapshot, "trim_db", 0.0) or 0.0
        )
        if (
            self._pending_trim is not None
            and self._matches(self._pending_trim, value, tolerance=0.05)
        ) or (
            self._pending_trim is None
            and self._matches(snapshot_value, value, tolerance=0.05)
        ):
            return
        self._pending_trim = value
        self._hold_pending_edit("trim")
        self.trim_requested.emit(value)

    def _emit_count_in(self) -> None:
        if self._syncing or not self._edits_allowed():
            return
        beats = int(self._count_in.value())
        bpm = float(self._count_bpm.value())
        desired = (beats, bpm)
        snapshot_value = (
            int(getattr(self._snapshot, "count_in_beats", 0) or 0),
            float(
                getattr(self._snapshot, "count_in_bpm", 120.0) or 120.0
            ),
        )
        if self._count_in_intents_match(
            desired,
            self._pending_count_in,
        ) or (
            self._pending_count_in is None
            and self._count_in_intents_match(desired, snapshot_value)
        ):
            return
        self._pending_count_in = desired
        self._hold_pending_edit("count_in")
        self.count_in_requested.emit(beats, bpm)

    def _edits_allowed(self) -> bool:
        return self._rendered_state in {"ready", "paused"}

    @staticmethod
    def _matches(left: float, right: float, *, tolerance: float) -> bool:
        return abs(float(left) - float(right)) <= tolerance

    @classmethod
    def _loop_intents_match(
        cls,
        left: tuple[bool, float, float | None] | None,
        right: tuple[bool, float, float | None] | None,
    ) -> bool:
        if left is None or right is None or left[0] != right[0]:
            return False
        if not left[0]:
            return True
        return (
            cls._matches(left[1], right[1], tolerance=0.005)
            and left[2] is not None
            and right[2] is not None
            and cls._matches(left[2], right[2], tolerance=0.005)
        )

    @classmethod
    def _count_in_intents_match(
        cls,
        left: tuple[int, float] | None,
        right: tuple[int, float] | None,
    ) -> bool:
        return (
            left is not None
            and right is not None
            and left[0] == right[0]
            and cls._matches(left[1], right[1], tolerance=0.05)
        )

    def _snapshot_loop_intent(
        self,
    ) -> tuple[bool, float, float | None]:
        snapshot = self._snapshot
        duration = max(
            0.0,
            float(getattr(snapshot, "duration_s", 0.0) or 0.0),
        )
        start = min(
            duration,
            max(
                0.0,
                float(getattr(snapshot, "loop_start_s", 0.0) or 0.0),
            ),
        )
        end_raw = getattr(snapshot, "loop_end_s", None)
        end = (
            None
            if end_raw is None
            else min(duration, max(0.0, float(end_raw)))
        )
        return (end_raw is not None, start, end)

    def _hold_pending_edit(self, name: str) -> None:
        setattr(
            self,
            f"_pending_{name}_deadline",
            monotonic() + self._PENDING_HOLD_SECONDS,
        )

    def _age_pending_edit(self, name: str) -> None:
        """Clear an optimistic value after its bounded acknowledgement wait."""

        pending_name = f"_pending_{name}"
        deadline_name = f"{pending_name}_deadline"
        if getattr(self, pending_name) is None:
            setattr(self, deadline_name, 0.0)
            return
        if monotonic() >= float(getattr(self, deadline_name, 0.0)):
            setattr(self, pending_name, None)
            setattr(self, deadline_name, 0.0)

    def set_snapshot(self, snapshot: object) -> None:
        """Render one controller-owned immutable snapshot on the Qt thread."""

        self._snapshot = snapshot
        state_value = getattr(getattr(snapshot, "state", None), "value", "")
        state = str(state_value or getattr(snapshot, "state", "unavailable")).lower()
        self._rendered_state = state
        capability = getattr(snapshot, "capability", None)
        capability_available = bool(getattr(capability, "available", False))
        source_name = str(getattr(snapshot, "source_name", "") or "")
        duration = max(0.0, float(getattr(snapshot, "duration_s", 0.0) or 0.0))
        position = min(
            duration,
            max(0.0, float(getattr(snapshot, "position_s", 0.0) or 0.0)),
        )
        error = str(getattr(snapshot, "error", "") or "")
        route_detail = str(getattr(snapshot, "route_detail", "") or "")
        capability_detail = str(getattr(capability, "detail", "") or "")

        state_labels = {
            "unavailable": "Reference Track is unavailable",
            "idle": "Ready to load a song",
            "loading": "Checking and decoding the song…",
            "ready": "Song ready; routing will be proven before playback",
            "routing": "Starting the isolated Jamulus track participant…",
            "playing": "Playing through Jamulus",
            "paused": "Paused in Jamulus",
            "stopping": "Stopping the backing participant safely…",
            "failed": "Reference Track needs attention",
            "closed": "Reference Track is closed",
        }
        status = state_labels.get(state, "Checking Reference Track state…")
        if error:
            status = f"{status}. {error}"
        self._set_dynamic_status(self._status, status)
        self._set_dynamic_status(
            self._route,
            route_detail or capability_detail,
        )
        self._route.setVisible(bool(self._route.text()))
        self._source.setText(source_name or "No song loaded")
        self._time.setText(f"{_clock_text(position)} / {_clock_text(duration)}")

        snapshot_seek_value = (
            0
            if duration <= 0.0
            else round(position / duration * self._SEEK_STEPS)
        )
        loop_start = max(
            0.0, float(getattr(snapshot, "loop_start_s", 0.0) or 0.0)
        )
        loop_end_raw = getattr(snapshot, "loop_end_s", None)
        snapshot_loop = (
            loop_end_raw is not None,
            min(duration, loop_start),
            (
                None
                if loop_end_raw is None
                else min(duration, max(0.0, float(loop_end_raw)))
            ),
        )
        snapshot_trim = float(getattr(snapshot, "trim_db", 0.0) or 0.0)
        snapshot_count_in = (
            int(getattr(snapshot, "count_in_beats", 0) or 0),
            float(getattr(snapshot, "count_in_bpm", 120.0) or 120.0),
        )

        if state != "paused" or duration <= 0.0:
            self._pending_seek_value = None
            self._pending_seek_value_deadline = 0.0
        elif (
            self._pending_seek_value is not None
            and snapshot_seek_value == self._pending_seek_value
        ):
            self._pending_seek_value = None
            self._pending_seek_value_deadline = 0.0
        else:
            self._age_pending_edit("seek_value")

        if not self._edits_allowed():
            self._pending_loop = None
            self._pending_trim = None
            self._pending_count_in = None
            self._pending_loop_deadline = 0.0
            self._pending_trim_deadline = 0.0
            self._pending_count_in_deadline = 0.0
        else:
            pending_loop = self._pending_loop
            if pending_loop is not None:
                if self._loop_intents_match(pending_loop, snapshot_loop):
                    self._pending_loop = None
                    self._pending_loop_deadline = 0.0
                else:
                    self._age_pending_edit("loop")
            if (
                self._pending_trim is not None
                and self._matches(
                    self._pending_trim,
                    snapshot_trim,
                    tolerance=0.05,
                )
            ):
                self._pending_trim = None
                self._pending_trim_deadline = 0.0
            else:
                self._age_pending_edit("trim")
            if self._pending_count_in is not None:
                if self._count_in_intents_match(
                    self._pending_count_in,
                    snapshot_count_in,
                ):
                    self._pending_count_in = None
                    self._pending_count_in_deadline = 0.0
                else:
                    self._age_pending_edit("count_in")

        self._syncing = True
        try:
            if (
                not self._seek.isSliderDown()
                and self._pending_seek_value is None
            ):
                self._seek.setValue(snapshot_seek_value)
            self._loop_start.setRange(0.0, duration)
            self._loop_end.setRange(0.0, duration)
            if self._pending_loop is None:
                self._loop.setChecked(snapshot_loop[0])
                if not self._spin_box_has_focus(self._loop_start):
                    self._loop_start.setValue(snapshot_loop[1])
                if not self._spin_box_has_focus(self._loop_end):
                    self._loop_end.setValue(
                        duration
                        if snapshot_loop[2] is None
                        else snapshot_loop[2]
                    )
            if (
                self._pending_trim is None
                and not self._spin_box_has_focus(self._trim)
            ):
                self._trim.setValue(snapshot_trim)
            if self._pending_count_in is None:
                if not self._spin_box_has_focus(self._count_in):
                    self._count_in.setValue(snapshot_count_in[0])
                if not self._spin_box_has_focus(self._count_bpm):
                    self._count_bpm.setValue(snapshot_count_in[1])
        finally:
            self._syncing = False

        self._set_controls_enabled(capability_available, state=state)

    @staticmethod
    def _set_dynamic_status(label: QLabel, text: str) -> None:
        """Update a changing status once and notify assistive technology."""

        value = str(text or "")
        changed = (
            label.text() != value
            or label.accessibleDescription() != value
        )
        label.setText(value)
        label.setAccessibleDescription(value)
        if not changed:
            return
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(
                    label,
                    QAccessible.Event.DescriptionChanged,
                )
            )
        except (RuntimeError, TypeError):
            pass

    @staticmethod
    def _spin_box_has_focus(control: QSpinBox | QDoubleSpinBox) -> bool:
        """Keep an in-progress keyboard edit across periodic snapshots."""

        line_edit = control.lineEdit()
        return control.hasFocus() or (
            line_edit is not None and line_edit.hasFocus()
        )

    def _set_controls_enabled(self, capability_available: bool, *, state: str) -> None:
        busy = state in {"loading", "routing", "stopping", "closed"}
        loaded = state in {"ready", "routing", "playing", "paused", "stopping"}
        editable = loaded and not busy and state != "playing"
        self._load.setEnabled(capability_available and not busy)
        self._play.setEnabled(
            capability_available and state in {"ready", "paused"}
        )
        self._pause.setEnabled(state == "playing")
        self._restart.setEnabled(
            capability_available and state in {"ready", "playing", "paused"}
        )
        self._stop.setEnabled(state in {"routing", "playing", "paused", "failed"})
        self._seek.setEnabled(state == "paused")
        for control in (
            self._loop,
            self._loop_start,
            self._loop_end,
            self._trim,
            self._count_in,
            self._count_bpm,
        ):
            control.setEnabled(editable)

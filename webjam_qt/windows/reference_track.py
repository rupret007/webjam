"""Host-facing controls for the Jamulus-routed Reference Track."""

from __future__ import annotations

from enum import StrEnum
from time import monotonic
from typing import Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QApplication,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.reference_track import reference_track_file_filter
from webjam_qt.theme.tokens import Space

_BLACKHOLE_SETUP_URL = "https://existential.audio/blackhole/"


class ReferenceTrackPrimaryGate(StrEnum):
    """Finite application-owned readiness truth for Reference Track playback."""

    READY = "ready"
    NOT_CONNECTED = "not_connected"
    RECOVERING = "recovering"
    RECOVERY_FAILED = "recovery_failed"
    HOST_REQUIRED = "host_required"
    SESSION_CHANGING = "session_changing"


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
    recheck_route_requested = Signal()

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
        self._route_checking = False
        self._source_load_queued = False
        # The core Reference Track snapshot deliberately does not own the
        # primary Jamulus/session lifecycle. Fail closed until the application
        # controller proves one of the finite gate states below.
        self._primary_gate = ReferenceTrackPrimaryGate.NOT_CONNECTED
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
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setAccessibleName("Reference Track status")
        self._status.setAccessibleDescription(self._status.text())
        root.addWidget(self._status)

        self._route = QLabel("")
        self._route.setObjectName("DialogHint")
        self._route.setWordWrap(True)
        self._route.setMinimumWidth(0)
        self._route.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._route.setTextFormat(Qt.TextFormat.PlainText)
        self._route.setAccessibleName("Reference Track routing status")
        self._route.setAccessibleDescription("")
        root.addWidget(self._route)

        route_actions = QHBoxLayout()
        self._route_guidance = QLabel(
            "You can load and inspect a song before the playback route is ready."
        )
        self._route_guidance.setObjectName("DialogHint")
        self._route_guidance.setWordWrap(True)
        self._route_guidance.setMinimumWidth(0)
        self._route_guidance.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._route_guidance.setTextFormat(Qt.TextFormat.PlainText)
        self._route_guidance.setAccessibleName("Reference Track route guidance")
        route_actions.addWidget(self._route_guidance, 1)
        self._recheck_route = QPushButton("Recheck Route")
        self._recheck_route.setObjectName("GhostButton")
        self._recheck_route.setAccessibleName(
            "Recheck the Reference Track playback route"
        )
        self._recheck_route.setToolTip(
            "Inspect the isolated audio route again. This never starts playback."
        )
        self._recheck_route.clicked.connect(self.recheck_route_requested.emit)
        route_actions.addWidget(self._recheck_route)
        self._blackhole_setup = QPushButton("BlackHole Setup…")
        self._blackhole_setup.setObjectName("GhostButton")
        self._blackhole_setup.setAccessibleName(
            "Open the official BlackHole setup page"
        )
        self._blackhole_setup.setToolTip(
            "Open the official HTTPS setup page for a future certified pilot. "
            "WebJam will not download or install a driver, and setup cannot "
            "unlock this downloaded candidate."
        )
        self._blackhole_setup.clicked.connect(self._open_blackhole_setup)
        self._blackhole_setup.setVisible(False)
        route_actions.addWidget(self._blackhole_setup)
        root.addLayout(route_actions)

        source_row = QHBoxLayout()
        self._source = QLabel("No song loaded")
        self._source.setObjectName("SimpleSettingsFieldLabel")
        self._source.setTextFormat(Qt.TextFormat.PlainText)
        self._source.setAccessibleName("Loaded Reference Track")
        self._source.setWordWrap(True)
        self._source.setMinimumWidth(0)
        self._source.setMaximumHeight(48)
        self._source.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        source_row.addWidget(self._source, 1)
        self._load = QPushButton("Load Song…")
        self._load.setObjectName("GhostButton")
        self._load.setAccessibleName("Load a Reference Track audio file")
        self._load.clicked.connect(self._choose_source)
        source_row.addWidget(self._load)
        root.addLayout(source_row)

        self._source_details = QLabel("No source details")
        self._source_details.setObjectName("DialogHint")
        self._source_details.setTextFormat(Qt.TextFormat.PlainText)
        self._source_details.setWordWrap(True)
        self._source_details.setMinimumWidth(0)
        self._source_details.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._source_details.setAccessibleName("Reference Track source details")
        root.addWidget(self._source_details)

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
        self._play.clicked.connect(self._emit_play)
        self._pause = QPushButton("Pause")
        self._pause.setObjectName("GhostButton")
        self._pause.clicked.connect(self._emit_pause)
        self._restart = QPushButton("Restart")
        self._restart.setObjectName("GhostButton")
        self._restart.clicked.connect(self._emit_restart)
        self._stop = QPushButton("Stop")
        self._stop.setProperty("destructive", "true")
        self._stop.clicked.connect(self._emit_stop)
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
            reference_track_file_filter(),
        )
        if path:
            self.load_requested.emit(path)

    def _open_blackhole_setup(self) -> None:
        """Open only the reviewed official setup page after an explicit click."""

        if QDesktopServices.openUrl(QUrl(_BLACKHOLE_SETUP_URL)):
            return
        self._set_dynamic_status(
            self._route_guidance,
            "WebJam couldn't open the official BlackHole setup page. No driver "
            "was downloaded or installed.",
        )

    def _emit_play(self) -> None:
        if (
            self._primary_gate is ReferenceTrackPrimaryGate.READY
            and self._rendered_state in {"ready", "paused"}
        ):
            self.play_requested.emit()

    def _emit_pause(self) -> None:
        if (
            self._primary_gate is ReferenceTrackPrimaryGate.READY
            and self._rendered_state == "playing"
        ):
            self.pause_requested.emit()

    def _emit_restart(self) -> None:
        if (
            self._primary_gate is ReferenceTrackPrimaryGate.READY
            and self._rendered_state in {"playing", "paused"}
        ):
            self.restart_requested.emit()

    def _emit_stop(self) -> None:
        if (
            self._primary_gate is ReferenceTrackPrimaryGate.READY
            and self._rendered_state
            in {"routing", "playing", "paused", "failed"}
        ):
            self.stop_requested.emit()

    def _emit_seek(self) -> None:
        if (
            self._syncing
            or self._primary_gate is not ReferenceTrackPrimaryGate.READY
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
        return bool(
            self._primary_gate is ReferenceTrackPrimaryGate.READY
            and self._rendered_state in {"ready", "paused"}
        )

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

        previous_focus = QApplication.focusWidget()
        previous_state = self._rendered_state
        transport_controls = {
            self._play,
            self._pause,
            self._restart,
            self._stop,
        }
        self._snapshot = snapshot
        state_value = getattr(getattr(snapshot, "state", None), "value", "")
        state = str(state_value or getattr(snapshot, "state", "unavailable")).lower()
        self._rendered_state = state
        capability = getattr(snapshot, "capability", None)
        capability_available = bool(getattr(capability, "available", False))
        capability_reason = str(
            getattr(capability, "reason_code", "") or ""
        ).casefold()
        source_name = str(getattr(snapshot, "source_name", "") or "")
        loaded = bool(source_name)
        duration = max(0.0, float(getattr(snapshot, "duration_s", 0.0) or 0.0))
        position = min(
            duration,
            max(0.0, float(getattr(snapshot, "position_s", 0.0) or 0.0)),
        )
        error = str(getattr(snapshot, "error", "") or "")
        cleanup_pending = bool(
            getattr(snapshot, "cleanup_pending", False)
        )
        route_detail = str(getattr(snapshot, "route_detail", "") or "")
        capability_detail = str(getattr(capability, "detail", "") or "")
        source_format = str(getattr(snapshot, "source_format", "") or "").upper()
        source_samplerate = max(
            0, int(getattr(snapshot, "source_samplerate", 0) or 0)
        )
        source_channels = max(
            0, int(getattr(snapshot, "source_channels", 0) or 0)
        )

        state_labels = {
            "unavailable": "Ready to load a song",
            "idle": "Ready to load a song",
            "loading": "Checking and decoding the song…",
            "ready": "Song loaded and ready to inspect",
            "routing": "Starting the isolated Jamulus track participant…",
            "playing": "Playing through Jamulus",
            "paused": "Paused in Jamulus",
            "stopping": "Stopping the backing participant safely…",
            "failed": "Reference Track needs attention",
            "closed": "Reference Track is closed",
        }
        status = state_labels.get(state, "Checking Reference Track state…")
        if loaded and state == "ready" and not capability_available:
            status = (
                "Song loaded and ready to inspect; initial audio decoded; "
                "playback route is locked"
            )
            if capability_reason == "physical_certification_required":
                status = (
                    f"{source_format or 'Song'} loaded and decoded; Play is "
                    "locked in this downloaded candidate"
                )
        elif (
            loaded
            and state in {"ready", "playing", "paused"}
            and capability_available
        ):
            ready_prefix = {
                "ready": "Song loaded and ready to inspect",
                "playing": "Reference Track reports playback active",
                "paused": "Reference Track paused",
            }[state]
            if self._primary_gate is ReferenceTrackPrimaryGate.SESSION_CHANGING:
                status = (
                    f"{ready_prefix}; waiting for the current session change "
                    "to finish; controls are locked"
                )
            elif self._primary_gate is ReferenceTrackPrimaryGate.HOST_REQUIRED:
                status = (
                    f"{ready_prefix}; playback is available only to the host; "
                    "controls are locked"
                )
            elif self._primary_gate is ReferenceTrackPrimaryGate.NOT_CONNECTED:
                status = (
                    f"{ready_prefix}; waiting for a verified primary Jamulus "
                    "control connection; controls are locked"
                )
            elif self._primary_gate is ReferenceTrackPrimaryGate.RECOVERING:
                status = (
                    f"{ready_prefix}; waiting for band audio recovery to "
                    "finish; controls are locked"
                )
            elif self._primary_gate is ReferenceTrackPrimaryGate.RECOVERY_FAILED:
                status = (
                    f"{ready_prefix}; start a clean band audio session before "
                    "playback; controls are locked"
                )
        if cleanup_pending:
            status = (
                "Private Reference Track cleanup is still pending"
            )
        if error:
            status = f"{status}. {error}"
        self._set_dynamic_status(self._status, status)
        route_prefix = (
            "Playback locked—finish stopping. "
            if cleanup_pending
            else (
                "Playback route ready. "
                if capability_available
                else "Playback route locked. "
            )
        )
        route_message = f"{route_prefix}{route_detail or capability_detail}".strip()
        if capability_reason == "physical_certification_required":
            route_message = (
                "Playback locked in this downloaded candidate. Installing "
                "BlackHole or choosing Recheck Route cannot unlock it."
            )
        self._set_dynamic_status(self._route, route_message)
        self._route.setToolTip(capability_detail)
        self._route.setVisible(bool(self._route.text()))
        self._blackhole_setup.setVisible(
            capability_reason
            in {
                "physical_certification_required",
                "blackhole_unavailable",
            }
        )
        if (
            cleanup_pending
            and self._primary_gate
            is ReferenceTrackPrimaryGate.SESSION_CHANGING
        ):
            guidance = (
                "Finish the current End, Leave, or session-switch cleanup from "
                "WebJam's main session control. Reference Track's Stop stays "
                "locked while that single cleanup owner is active."
            )
        elif cleanup_pending:
            guidance = (
                "Choose Stop again. Loading, playback, and route rechecks stay "
                "locked until WebJam confirms its private process, profile, "
                "control, and audio-route cleanup."
            )
        elif (
            capability_available
            and self._primary_gate
            is ReferenceTrackPrimaryGate.SESSION_CHANGING
        ):
            guidance = (
                "WebJam is ending, leaving, or switching the current jam. Play "
                "and Restart stay locked until that session change finishes; "
                "loading and inspecting the song does not start another client."
            )
        elif (
            capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.HOST_REQUIRED
        ):
            guidance = (
                "Only the host can send a Reference Track to the band. This "
                "song stays loaded for inspection, but connecting as a guest "
                "will not unlock Play; start a hosted jam to use it."
            )
        elif (
            capability_available
            and self._primary_gate
            is ReferenceTrackPrimaryGate.NOT_CONNECTED
        ):
            guidance = (
                "Start or reconnect band audio, finish Jamulus sound setup, and "
                "wait for WebJam to verify its authenticated control connection. "
                "Play stays locked until that proof is current; "
                "loading and inspecting the song does not start another client."
            )
        elif (
            capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.RECOVERING
        ):
            guidance = (
                "WebJam is recovering or safely retiring the primary Jamulus "
                "client. Play and Restart stay locked until recovery finishes "
                "and a fresh authenticated control heartbeat is verified."
            )
        elif (
            capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.RECOVERY_FAILED
        ):
            guidance = (
                "Automatic band-audio recovery finished without a usable "
                "connection. Press Start Session in the main WebJam window to "
                "launch a clean Jamulus client, then finish its sound setup."
            )
        elif capability_available:
            guidance = (
                "WebJam proves the route again before playback. Loading or "
                "inspecting a song does not start the Jamulus track participant."
            )
        elif capability_reason == "physical_certification_required":
            source_truth = (
                f"{source_format or 'The song'} loaded and its first bounded "
                "audio block decoded successfully. "
                if loaded
                else "You can still load and inspect a song. "
            )
            setup = (
                "Official BlackHole 16ch or 64ch at 48 kHz is only a prerequisite "
                "for a future certified pilot/build. Installing it or choosing "
                "Recheck Route will not enable Play in this downloaded candidate. "
                "BlackHole 2ch and WebJam Bridge cannot safely isolate the return "
                "mix."
            )
            guidance = source_truth + setup
        elif capability_reason == "blackhole_unavailable":
            guidance = (
                "Install official BlackHole 16ch or 64ch manually, set it to "
                "48 kHz in Audio MIDI Setup, then choose Recheck Route. "
                "BlackHole 2ch and WebJam Bridge are not safe substitutes."
            )
        else:
            guidance = (
                "Load and inspect a song now if you want. Playback remains "
                "disabled until the setup above is complete; then choose "
                "Recheck Route."
            )
        self._set_dynamic_status(self._route_guidance, guidance)
        source_label = source_name or "No song loaded"
        self._source.setText(source_label)
        self._source.setToolTip(source_name)
        self._source.setAccessibleDescription(source_label)
        source_facts: list[str] = []
        if source_format:
            source_facts.append(source_format)
        if source_samplerate:
            rate_khz = source_samplerate / 1_000.0
            source_facts.append(f"{rate_khz:g} kHz")
        if source_channels:
            source_facts.append("mono" if source_channels == 1 else "stereo")
        if loaded and duration:
            source_facts.append(_clock_text(duration))
        self._source_details.setText(
            " · ".join(source_facts) if source_facts else "No source details"
        )
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

        self._set_controls_enabled(
            capability_available and not cleanup_pending,
            state=state,
            loaded=loaded,
            cleanup_pending=cleanup_pending,
        )
        if (
            previous_focus in transport_controls
            and (
                state != previous_state
                or not previous_focus.isEnabled()
            )
            and self.isVisible()
        ):
            preferred_target = (
                self._stop
                if state in {"routing", "stopping"} or cleanup_pending
                else self._pause
                if state == "playing"
                else self._play
                if state in {"ready", "paused"}
                else None
            )
            if self._primary_gate is ReferenceTrackPrimaryGate.SESSION_CHANGING:
                target = self._done
            else:
                target = (
                    preferred_target
                    if preferred_target is not None
                    and preferred_target.isEnabled()
                    else self._recheck_route
                    if self._recheck_route.isEnabled()
                    else self._done
                )
            if target.isEnabled():
                target.setFocus(Qt.FocusReason.TabFocusReason)

    def set_route_checking(self, checking: bool) -> None:
        """Render one coalesced route probe without enabling duplicate work."""

        self._route_checking = bool(checking)
        self._recheck_route.setText(
            "Checking…" if self._route_checking else "Recheck Route"
        )
        self._recheck_route.setAccessibleName(
            (
                "Checking the Reference Track playback route"
                if self._route_checking
                else "Recheck the Reference Track playback route"
            )
        )
        if self._snapshot is not None:
            self.set_snapshot(self._snapshot)

    def set_source_load_queued(self, queued: bool) -> None:
        """Show that one selected source will load after current safe work."""

        self._source_load_queued = bool(queued)
        self._load.setText(
            "Waiting to Load…"
            if self._source_load_queued
            else "Load Song…"
        )
        self._load.setAccessibleName(
            (
                "Selected Reference Track is waiting to load"
                if self._source_load_queued
                else "Load a Reference Track audio file"
            )
        )
        if self._snapshot is not None:
            self.set_snapshot(self._snapshot)

    def set_primary_gate(self, gate: ReferenceTrackPrimaryGate) -> None:
        """Render the controller's finite primary-session readiness truth."""

        value = ReferenceTrackPrimaryGate(gate)
        if self._primary_gate is value:
            return
        self._primary_gate = value
        if self._snapshot is not None:
            self.set_snapshot(self._snapshot)

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
    def _set_dynamic_description(widget: QWidget, text: str) -> None:
        """Update an accessible control reason without periodic event spam."""

        value = str(text or "")
        if widget.accessibleDescription() == value:
            return
        widget.setAccessibleDescription(value)
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(
                    widget,
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

    def _set_controls_enabled(
        self,
        capability_available: bool,
        *,
        state: str,
        loaded: bool = False,
        cleanup_pending: bool = False,
    ) -> None:
        busy = state in {"loading", "routing", "stopping", "closed"}
        primary_ready = self._primary_gate is ReferenceTrackPrimaryGate.READY
        editable = loaded and primary_ready and state in {"ready", "paused"}
        self._load.setEnabled(
            not busy
            and not cleanup_pending
            and not self._source_load_queued
        )
        self._recheck_route.setEnabled(
            not busy
            and not self._route_checking
            and not cleanup_pending
            and self._primary_gate
            is not ReferenceTrackPrimaryGate.SESSION_CHANGING
            and state not in {"playing", "paused"}
        )
        self._blackhole_setup.setEnabled(not busy and not cleanup_pending)
        self._play.setEnabled(
            loaded
            and capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.READY
            and state in {"ready", "paused"}
        )
        capability = getattr(self._snapshot, "capability", None)
        reason = str(getattr(capability, "reason_code", "") or "").casefold()
        if self._play.isEnabled():
            play_tooltip = "Play the loaded song through the isolated Jamulus route."
        elif (
            loaded
            and capability_available
            and self._primary_gate
            is ReferenceTrackPrimaryGate.SESSION_CHANGING
        ):
            play_tooltip = (
                "Wait for the current session change to finish before playing."
            )
        elif (
            loaded
            and capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.HOST_REQUIRED
        ):
            play_tooltip = "Only the host can play a Reference Track for the band."
        elif (
            loaded
            and capability_available
            and self._primary_gate
            is ReferenceTrackPrimaryGate.NOT_CONNECTED
        ):
            play_tooltip = (
                "Finish Jamulus sound setup and wait for WebJam to confirm your "
                "live music connection before playing."
            )
        elif (
            loaded
            and capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.RECOVERING
        ):
            play_tooltip = (
                "Wait for band audio recovery and a fresh authenticated "
                "Jamulus heartbeat before playing."
            )
        elif (
            loaded
            and capability_available
            and self._primary_gate is ReferenceTrackPrimaryGate.RECOVERY_FAILED
        ):
            play_tooltip = (
                "Press Start Session in WebJam to launch clean band audio "
                "before playing."
            )
        elif reason == "physical_certification_required":
            play_tooltip = (
                "Play is locked in this downloaded candidate because physical "
                "BlackHole/Jamulus isolation has not been certified."
            )
        elif loaded and not capability_available:
            play_tooltip = (
                "Play is unavailable until WebJam proves the isolated audio route."
            )
        else:
            play_tooltip = "Load a supported song before playing."
        self._play.setToolTip(play_tooltip)
        self._set_dynamic_description(self._play, play_tooltip)
        self._pause.setEnabled(primary_ready and state == "playing")
        if state != "playing":
            pause_tooltip = (
                "Pause becomes available while the Reference Track is playing."
            )
        elif primary_ready:
            pause_tooltip = "Pause the Reference Track through Jamulus."
        else:
            pause_tooltip = (
                "Pause is locked until the primary Jamulus session is ready."
            )
        self._pause.setToolTip(pause_tooltip)
        self._set_dynamic_description(self._pause, pause_tooltip)
        self._restart.setEnabled(
            capability_available
            and primary_ready
            and state in {"playing", "paused"}
        )
        if state not in {"playing", "paused"}:
            restart_tooltip = (
                "Restart becomes available while the Reference Track is playing "
                "or paused."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.SESSION_CHANGING:
            restart_tooltip = (
                "Wait for the current session change to finish before restarting."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.HOST_REQUIRED:
            restart_tooltip = (
                "Only the host can restart a Reference Track for the band."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.NOT_CONNECTED:
            restart_tooltip = (
                "Finish Jamulus sound setup and wait for WebJam to confirm your "
                "live music connection before restarting."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.RECOVERING:
            restart_tooltip = (
                "Wait for band audio recovery and a fresh authenticated "
                "Jamulus heartbeat before restarting."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.RECOVERY_FAILED:
            restart_tooltip = (
                "Press Start Session in WebJam to launch clean band audio "
                "before restarting."
            )
        elif self._restart.isEnabled():
            restart_tooltip = "Restart the Reference Track from the beginning."
        else:
            restart_tooltip = "Restart is unavailable until the route is proven."
        self._restart.setToolTip(restart_tooltip)
        self._set_dynamic_description(self._restart, restart_tooltip)
        self._stop.setEnabled(
            primary_ready
            and (
                cleanup_pending
                or state in {"routing", "playing", "paused", "failed"}
            )
        )
        if self._primary_gate is ReferenceTrackPrimaryGate.SESSION_CHANGING:
            stop_tooltip = (
                "Wait for the current session change to finish; its single "
                "cleanup owner is already stopping Reference Track safely."
            )
        elif self._primary_gate is ReferenceTrackPrimaryGate.RECOVERING:
            stop_tooltip = (
                "Band audio recovery already owns Reference Track cleanup. "
                "Wait for recovery to finish."
            )
        elif not primary_ready:
            stop_tooltip = (
                "Stop is locked until the primary Jamulus session is ready. "
                "End or Leave the session from WebJam's main control if cleanup "
                "is required."
            )
        else:
            stop_tooltip = (
                "Stop the Reference Track and its isolated Jamulus participant."
            )
        self._stop.setToolTip(stop_tooltip)
        self._set_dynamic_description(self._stop, stop_tooltip)
        self._seek.setEnabled(primary_ready and state == "paused")
        seek_tooltip = (
            "Seek within the paused Reference Track."
            if self._seek.isEnabled()
            else "Seeking is locked until the track is paused and primary "
            "Jamulus is ready."
        )
        self._seek.setToolTip(seek_tooltip)
        self._set_dynamic_description(self._seek, seek_tooltip)
        for control in (
            self._loop,
            self._loop_start,
            self._loop_end,
            self._trim,
            self._count_in,
            self._count_bpm,
        ):
            control.setEnabled(editable)
            edit_tooltip = (
                "Edit this setting while the loaded Reference Track is ready "
                "or paused."
                if editable
                else "This setting is locked until the loaded Reference Track "
                "is ready or paused and primary Jamulus is ready."
            )
            control.setToolTip(edit_tooltip)
            self._set_dynamic_description(control, edit_tooltip)

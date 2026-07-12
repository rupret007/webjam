"""
SessionStrip — top bar.

Shows, left to right:
  - Logo
  - Session title + mode subtitle
  - Live session timer
  - Record indicator
  - Mode picker
  - Primary actions (Start Audio, Open Webex)

Emits semantic signals; ApplicationController wires them to services.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTime, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from webjam_qt.theme.tokens import Space


class SessionStrip(QFrame):
    mode_changed = Signal(str)          # mode_key
    session_title_changed = Signal(str)
    launch_audio_requested = Signal()
    join_video_requested = Signal()
    mute_self_requested = Signal()      # toggle local-user mute
    practice_requested = Signal()       # start a solo practice session
    record_requested = Signal()         # toggle band-server multitrack recording
    ready_check_requested = Signal()    # run the pre-jam readiness report

    STRIP_HEIGHT = 72

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str = "",
        initial_title: str = "Untitled Session",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SessionStrip")
        self.setFixedHeight(self.STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._mode_entries = list(mode_entries)
        self._elapsed_seconds = 0
        self._webex_audio_mode = "talkback"

        # --- Widgets
        self._logo = QLabel("WJ")
        self._logo.setObjectName("SessionStripLogo")

        self._title_input = QLineEdit(initial_title)
        self._title_input.setObjectName("SessionStripTitle")
        self._title_input.setAccessibleName("Session title")
        self._title_input.setFrame(False)
        # Keep enough room to recognise/edit the title while leaving the
        # safety-critical live actions readable at the supported 1100px
        # window minimum.
        self._title_input.setMinimumWidth(128)
        self._title_input.editingFinished.connect(
            lambda: self.session_title_changed.emit(self._title_input.text().strip())
        )

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("SessionStripSubtitle")

        self._record_elapsed = QLabel("REC 00:00")
        self._record_elapsed.setObjectName("RecordElapsed")
        self._record_elapsed.setAccessibleName("Recording elapsed time")
        self._record_elapsed.setVisible(False)
        self._record_elapsed_seconds = 0
        self._record_clock = QTimer(self)
        self._record_clock.setInterval(1000)
        self._record_clock.timeout.connect(self._tick_recording)

        self._timer_label = QLabel("00:00:00")
        self._timer_label.setObjectName("SessionTimer")
        self._timer_label.setAccessibleName("Session elapsed time")

        self._mode_picker = QComboBox()
        self._mode_picker.setAccessibleName("Session mode")
        self._mode_picker.setMaximumWidth(140)
        for key, label in self._mode_entries:
            self._mode_picker.addItem(label, key)
        if initial_mode_key:
            idx = self._mode_picker.findData(initial_mode_key)
            if idx >= 0:
                self._mode_picker.setCurrentIndex(idx)
        self._mode_picker.currentIndexChanged.connect(self._on_mode_index_changed)
        self._sync_subtitle()

        self._audio_button = QPushButton("Start Audio")
        self._audio_button.setObjectName("AudioButton")
        self._audio_button.setAccessibleName("Launch or stop Jamulus audio")
        self._audio_button.setToolTip(
            "Launch Jamulus and connect to the band's server.\n"
            "Click again to stop. Audio settings live in Settings (Ctrl+,)."
        )
        self._audio_button.clicked.connect(self.launch_audio_requested.emit)

        self._record_button = QPushButton("● Record")
        self._record_button.setObjectName("GhostButton")
        self._record_button.setAccessibleName(
            "Start or stop band-server multitrack recording"
        )
        self._record_button.setToolTip(
            "Record the session on the band server: every musician gets\n"
            "their own track plus a ready-to-open Reaper project.\n"
            "Needs the band-server RPC set up once — see server/README.md."
        )
        self._record_button.clicked.connect(self.record_requested.emit)

        self._test_button = QToolButton()
        self._test_button.setText("Checks ▾")
        self._test_button.setObjectName("GhostButton")
        self._test_button.setAccessibleName("Readiness and practice tests")
        self._test_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        test_menu = QMenu(self._test_button)
        self._ready_action = QAction("Ready Check\tF2", test_menu)
        self._ready_action.setToolTip("Check devices, server settings, and recording readiness")
        self._ready_action.triggered.connect(self.ready_check_requested.emit)
        test_menu.addAction(self._ready_action)
        self._practice_action = QAction("Practice Solo\tCtrl+P", test_menu)
        self._practice_action.setToolTip("Start a private local Jamulus practice session")
        self._practice_action.triggered.connect(self.practice_requested.emit)
        test_menu.addAction(self._practice_action)
        self._test_button.setMenu(test_menu)

        self._mute_self_button = QPushButton("Talk Break")
        self._mute_self_button.setObjectName("GhostButton")
        self._mute_self_button.setCheckable(True)
        self._mute_self_button.setAccessibleName("Start a talk break")
        self._mute_self_button.setToolTip(
            "Mute your Jamulus send before using Webex push-to-talk.\n"
            "This control never changes the Webex microphone.\n"
            "Shortcut: Ctrl+Shift+M (the literal Control key on macOS)."
        )
        self._mute_self_button.clicked.connect(self.mute_self_requested.emit)

        self._video_button = QPushButton("Open Webex")
        self._video_button.setObjectName("PrimaryButton")
        self._video_button.setAccessibleName("Open Webex")
        self._video_button.setToolTip(
            "Open the band's meeting in the native Webex app or browser.\n"
            "WebJam cannot inspect or control the external meeting."
        )
        self._video_button.clicked.connect(self.join_video_requested.emit)

        # --- Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        # Nine controls share this strip.  Compact spacing prevents the
        # longest live states (Record Again + Unmute Jamulus Send) from being
        # silently truncated at the supported minimum window width.
        layout.setSpacing(Space.SM)

        layout.addWidget(self._logo)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title_block.addWidget(self._title_input)
        title_block.addWidget(self._subtitle)
        layout.addLayout(title_block, stretch=1)

        layout.addWidget(self._record_elapsed)
        layout.addWidget(self._timer_label)
        layout.addWidget(self._mode_picker)
        layout.addWidget(self._record_button)
        layout.addWidget(self._test_button)
        layout.addWidget(self._mute_self_button)
        layout.addWidget(self._audio_button)
        layout.addWidget(self._video_button)

        # --- Timer
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_session_clock(self) -> None:
        self._elapsed_seconds = 0
        self._update_timer_label()
        self._clock.start()

    def stop_session_clock(self) -> None:
        self._clock.stop()

    def set_audio_state(self, label: str, *, enabled: bool = True) -> None:
        self._audio_button.setText(label)
        self._audio_button.setEnabled(enabled)
        self._audio_button.setAccessibleName(label)
        self._audio_button.setAccessibleDescription(
            f"Jamulus audio action. Current action: {label}."
        )

    def set_video_state(self, label: str, *, enabled: bool = True) -> None:
        self._video_button.setText(label)
        self._video_button.setEnabled(enabled)
        self._video_button.setAccessibleName(label)
        self._video_button.setAccessibleDescription(
            f"Webex video action. Current action: {label}."
        )

    def set_recording_phase(self, phase: str, detail: str = "") -> None:
        """Render the recorder state machine without relying on transient banners.

        ``detail`` refines the chip text during validation (staged progress).
        """
        phase = str(phase or "idle").lower()
        if phase == "preflight":
            self._record_button.setText("Checking…")
            self._record_button.setEnabled(False)
            self._record_elapsed.setText("PREFLIGHT")
            self._record_elapsed.setVisible(True)
            description = "Checking server and isolated host recording inputs."
        elif phase == "starting":
            self._record_button.setText("Starting…")
            self._record_button.setEnabled(False)
            self._record_elapsed.setText("ARMING")
            self._record_elapsed.setVisible(True)
            description = "Recording is being armed on the band server."
        elif phase == "recording":
            self._record_button.setText("■ Stop Rec")
            self._record_button.setEnabled(True)
            if not self._record_clock.isActive():
                self._record_elapsed_seconds = 0
                self._update_record_elapsed()
                self._record_clock.start()
            self._record_elapsed.setVisible(True)
            description = "Recording is active. Activate to stop and verify the take."
        elif phase == "stopping":
            self._record_button.setText("Stopping…")
            self._record_button.setEnabled(False)
            self._record_clock.stop()
            self._record_elapsed.setText("SAVING…")
            self._record_elapsed.setVisible(True)
            description = "Recording stopped; WebJam is saving and checking the tracks."
        elif phase == "validating":
            self._record_button.setText("Validating…")
            self._record_button.setEnabled(False)
            self._record_clock.stop()
            self._record_elapsed.setText(detail or "CHECKING TRACKS…")
            self._record_elapsed.setVisible(True)
            description = "WebJam is waiting for stable files and validating every track."
            if detail:
                description = f"WebJam is validating the take. {detail.capitalize()}"
        elif phase == "needs_attention":
            self._record_clock.stop()
            self._record_button.setText("● Record Again")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("NEEDS ATTENTION")
            self._record_elapsed.setVisible(True)
            description = "The take was preserved but did not pass recording validation."
        elif phase == "complete":
            self._record_clock.stop()
            self._record_button.setText("● Record")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("TAKE VERIFIED")
            self._record_elapsed.setVisible(True)
            description = "The previous take passed validation. Activate to record another."
        elif phase == "error":
            self._record_clock.stop()
            self._record_button.setText("Retry Record")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("RECORD ERROR")
            self._record_elapsed.setVisible(True)
            description = "The recording request failed. Activate to try again."
        else:
            self._record_clock.stop()
            self._record_button.setText("● Record")
            self._record_button.setEnabled(True)
            self._record_elapsed.setVisible(False)
            description = "Start band-server multitrack recording."
        self._record_button.setAccessibleName(self._record_button.text())
        self._record_button.setAccessibleDescription(description)

    def set_webex_audio_mode(self, mode: str) -> None:
        """Apply role-specific wording to the Jamulus transmit control."""
        self._webex_audio_mode = (
            mode if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )
        self.set_self_muted(self._mute_self_button.isChecked())

    def set_self_muted(self, muted: bool, *, enabled: bool = True) -> None:
        """Update the Jamulus-send button state without emitting signals."""
        self._mute_self_button.blockSignals(True)
        self._mute_self_button.setChecked(muted)
        talkback = self._webex_audio_mode == "talkback"
        if talkback:
            label = "Resume Music" if muted else "Talk Break"
            accessible = "Resume Jamulus music" if muted else "Start a talk break"
            description = (
                "Jamulus send is muted. Release Spacebar and make sure Webex is "
                "muted before resuming music."
                if muted else
                "Mute Jamulus send before holding Spacebar to talk in Webex. "
                "This control never changes the Webex microphone."
            )
        else:
            label = "Unmute Jamulus Send" if muted else "Mute Jamulus Send"
            accessible = label
            description = (
                "Your Jamulus send is muted."
                if muted else "Your Jamulus send is audible."
            )
        self._mute_self_button.setText(label)
        self._mute_self_button.setEnabled(enabled)
        self._mute_self_button.setAccessibleName(accessible)
        self._mute_self_button.setAccessibleDescription(description)
        self._mute_self_button.setToolTip(description)
        self._mute_self_button.blockSignals(False)

    def current_mode_key(self) -> str:
        return self._mode_picker.currentData() or ""

    def current_title(self) -> str:
        return self._title_input.text().strip()

    def focus_title(self) -> None:
        """Focus and select the session title field (keyboard shortcut target)."""
        self._title_input.setFocus()
        self._title_input.selectAll()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _on_mode_index_changed(self, _: int) -> None:
        self._sync_subtitle()
        self.mode_changed.emit(self._mode_picker.currentData() or "")

    def _sync_subtitle(self) -> None:
        label = self._mode_picker.currentText()
        self._subtitle.setText(f"{label} · WebJam")

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        self._update_timer_label()

    def _tick_recording(self) -> None:
        self._record_elapsed_seconds += 1
        self._update_record_elapsed()

    def _update_record_elapsed(self) -> None:
        seconds = self._record_elapsed_seconds
        self._record_elapsed.setText(f"REC {seconds // 60:02d}:{seconds % 60:02d}")

    def _update_timer_label(self) -> None:
        t = QTime(0, 0).addSecs(self._elapsed_seconds)
        self._timer_label.setText(t.toString("HH:mm:ss"))

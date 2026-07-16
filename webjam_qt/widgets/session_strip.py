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

from webjam_qt.theme.brand import BrandMark
from webjam_qt.theme.tokens import Space


class SessionStrip(QFrame):
    mode_changed = Signal(str)          # mode_key
    session_title_changed = Signal(str)
    launch_audio_requested = Signal()
    join_video_requested = Signal()
    practice_requested = Signal()       # start a solo practice session
    record_requested = Signal()         # toggle band-server multitrack recording
    ready_check_requested = Signal()    # run Band Check
    invite_requested = Signal()         # copy the host address for bandmates
    reset_invite_requested = Signal()   # revoke and replace a remote invite
    test_night_requested = Signal()     # open the operator-only pilot surface
    tool_requested = Signal(str)        # progressive-disclosure destination

    STRIP_HEIGHT = 60

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str = "",
        initial_title: str = "Untitled Session",
        operator_mode: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SessionStrip")
        self.setFixedHeight(self.STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._mode_entries = list(mode_entries)
        self.operator_mode = bool(operator_mode)
        self._elapsed_seconds = 0
        # --- Widgets
        self._logo = BrandMark(28)
        self._logo.setObjectName("SessionStripLogo")

        self._title_input = QLineEdit(initial_title)
        self._title_input.setObjectName("SessionStripTitle")
        self._title_input.setAccessibleName("Session title")
        self._title_input.setFrame(False)
        # Keep enough room to recognise/edit the title while leaving the
        # safety-critical live actions readable at the supported 1100px
        # window minimum.
        self._title_input.setMinimumWidth(128)
        self._title_input.setMaximumWidth(420)
        self._title_input.editingFinished.connect(
            lambda: self.session_title_changed.emit(self._title_input.text().strip())
        )

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("SessionStripSubtitle")
        # Creative-mode metadata still persists for notes/exports, but it is
        # not a decision a musician needs in the primary rehearsal header.
        self._subtitle.setVisible(False)

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
        self._mode_picker.setVisible(False)

        self._audio_button = QPushButton("Start Session")
        self._audio_button.setObjectName("AudioButton")
        self._audio_button.setAccessibleName("Start or end the band session")
        self._audio_button.setToolTip(
            "Start or end the band's live music session. WebJam handles the engine."
        )
        self._audio_button.clicked.connect(self.launch_audio_requested.emit)
        self._audio_button.setVisible(False)

        self._record_button = QPushButton("● Record")
        self._record_button.setObjectName("GhostButton")
        self._record_button.setAccessibleName(
            "Start or stop band-server multitrack recording"
        )
        self._record_button.setToolTip(
            "Record one synchronized track per connected musician.\n"
            "Open Studio to see the tracks, waveforms, and playback mix."
        )
        self._record_button.clicked.connect(self.record_requested.emit)

        self._test_button = QToolButton()
        self._test_button.setText("Band Check ▾")
        self._test_button.setObjectName("GhostButton")
        self._test_button.setAccessibleName("Band Check and solo practice")
        self._test_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        test_menu = QMenu(self._test_button)
        self._ready_action = QAction("Band Check\tF2", test_menu)
        self._ready_action.setToolTip("Check your input, headphones, and recording")
        self._ready_action.triggered.connect(self.ready_check_requested.emit)
        test_menu.addAction(self._ready_action)
        self._practice_action = QAction("Practice Solo\tCtrl+P", test_menu)
        self._practice_action.setToolTip("Start a private local Jamulus practice session")
        self._practice_action.triggered.connect(self.practice_requested.emit)
        test_menu.addAction(self._practice_action)
        self._test_button.setMenu(test_menu)
        # Band Check remains available through F2, but the everyday workflow
        # should not look like a checklist musicians must operate.
        self._test_button.setVisible(False)

        self._video_button = QPushButton("Open Webex")
        self._video_button.setObjectName("PrimaryButton")
        self._video_button.setAccessibleName("Open Webex")
        self._video_button.setToolTip(
            "Open the band's meeting in the native Webex app or browser.\n"
            "WebJam cannot inspect or control the external meeting."
        )
        self._video_button.clicked.connect(self.join_video_requested.emit)
        self._video_button.setVisible(False)

        self._invite_button = QPushButton("Copy Invite")
        self._invite_button.setObjectName("GhostButton")
        self._invite_button.setAccessibleName("Copy band invite")
        self._invite_button.setToolTip(
            "Copy one complete link to send to a bandmate."
        )
        self._invite_button.clicked.connect(self.invite_requested.emit)
        self._invite_button.setVisible(False)

        self._tools_button = QToolButton()
        self._tools_button.setText("More ▾")
        self._tools_button.setObjectName("GhostButton")
        self._tools_button.setAccessibleName("More session options")
        self._tools_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        tools_menu = QMenu(self._tools_button)
        audio_action = QAction("Audio Settings in Jamulus", tools_menu)
        audio_action.setToolTip(
            "Bring Jamulus forward. Jamulus owns your instrument, headphones, and buffer."
        )
        audio_action.triggered.connect(
            lambda: self.tool_requested.emit("audio_settings")
        )
        conversation_action = QAction("Webex / Conversation", tools_menu)
        conversation_action.triggered.connect(self.join_video_requested.emit)
        recording_action = QAction("Recording Setup", tools_menu)
        recording_action.triggered.connect(
            lambda: self.tool_requested.emit("recording_setup")
        )
        studio_action = QAction("Studio", tools_menu)
        studio_action.triggered.connect(lambda: self.tool_requested.emit("takes"))
        notes_action = QAction("Notes", tools_menu)
        notes_action.triggered.connect(lambda: self.tool_requested.emit("canvas"))
        diagnostics_action = QAction("Band Check / Verify Sound\tF2", tools_menu)
        diagnostics_action.triggered.connect(
            lambda: self.tool_requested.emit("diagnostics")
        )
        support_action = QAction("Support", tools_menu)
        support_action.triggered.connect(lambda: self.tool_requested.emit("support"))

        tools_menu.addAction(audio_action)
        tools_menu.addAction(conversation_action)
        tools_menu.addAction(recording_action)
        tools_menu.addAction(studio_action)
        tools_menu.addAction(notes_action)
        tools_menu.addSeparator()
        tools_menu.addAction(diagnostics_action)
        tools_menu.addAction(support_action)
        self._reset_invite_action = QAction("Reset Invite", tools_menu)
        self._reset_invite_action.setToolTip(
            "Revoke the current private invitation and create a new one."
        )
        self._reset_invite_action.setVisible(False)
        self._reset_invite_action.triggered.connect(
            self.reset_invite_requested.emit
        )
        tools_menu.addAction(self._reset_invite_action)
        settings_action = QAction("WebJam Settings", tools_menu)
        settings_action.triggered.connect(lambda: self.tool_requested.emit("settings"))
        tools_menu.addAction(settings_action)
        # Backward-compatible reference used by set_video_state().  The
        # conversation action lives under More and is intentionally optional.
        self._video_action = conversation_action
        self._test_night_action: QAction | None = None
        if self.operator_mode:
            tools_menu.addSeparator()
            self._test_night_action = QAction("Test Night", tools_menu)
            self._test_night_action.setToolTip(
                "Open the operator-only closed-pilot checklist."
            )
            self._test_night_action.triggered.connect(self.test_night_requested.emit)
            tools_menu.addAction(self._test_night_action)
        self._tools_button.setMenu(tools_menu)

        # --- Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        # Compact spacing keeps the longest recorder state readable at the
        # supported minimum window width.
        layout.setSpacing(Space.SM)

        layout.addWidget(self._logo)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title_block.addWidget(self._title_input)
        title_block.addWidget(self._subtitle)
        layout.addLayout(title_block, stretch=1)

        layout.addWidget(self._record_elapsed)
        layout.addWidget(self._timer_label)
        layout.addWidget(self._record_button)
        layout.addWidget(self._audio_button)
        layout.addWidget(self._invite_button)
        layout.addWidget(self._video_button)
        layout.addWidget(self._tools_button)

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

    def reset_session_clock(self) -> None:
        self._clock.stop()
        self._elapsed_seconds = 0
        self._update_timer_label()

    def set_audio_state(self, label: str, *, enabled: bool = True) -> None:
        self._audio_button.setText(label)
        self._audio_button.setEnabled(enabled)
        # Start/retry lives in the focused stage card. The header owns only
        # the in-session End action, avoiding duplicate primary buttons.
        self._audio_button.setVisible(
            label in {"End Session", "Leave Jam", "Ending…", "Leaving…", "Stopping…"}
        )
        self._audio_button.setAccessibleName(label)
        self._audio_button.setAccessibleDescription(
            f"Band session action. Current action: {label}."
        )

    def set_video_state(self, label: str, *, enabled: bool = True) -> None:
        self._video_button.setText(label)
        self._video_button.setEnabled(enabled)
        self._video_button.setAccessibleName(label)
        self._video_button.setAccessibleDescription(
            f"Webex video action. Current action: {label}."
        )
        menu_label = (
            "Webex / Conversation"
            if label in {"Open Webex", "Open Again"}
            else label
        )
        self._video_action.setText(menu_label)
        self._video_action.setEnabled(enabled)

    def set_video_configured(self, configured: bool) -> None:
        if not configured:
            self._video_action.setText("Add Webex / Conversation")

    def set_tools_enabled(self, enabled: bool) -> None:
        self._tools_button.setEnabled(bool(enabled))

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
        elif phase == "stop_failed":
            self._record_button.setText("■ Try Stop Again")
            self._record_button.setEnabled(True)
            if not self._record_clock.isActive():
                self._record_clock.start()
            self._record_elapsed.setVisible(True)
            description = (
                "The server may still be recording. Activate to try stopping again."
            )
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

    def set_recording_available(self, available: bool) -> None:
        """Only the host owns the synchronized take; joiners are recorded there."""
        self._record_button.setVisible(bool(available))
        self._record_elapsed.setVisible(
            bool(available) and self._record_elapsed.isVisible()
        )

    def set_invite_available(self, available: bool) -> None:
        self._invite_button.setVisible(bool(available))

    def set_reset_invite_available(self, available: bool) -> None:
        """Expose revocation only while this app owns a live remote invite."""

        self._reset_invite_action.setVisible(bool(available))

    def current_mode_key(self) -> str:
        return self._mode_picker.currentData() or ""

    def current_title(self) -> str:
        return self._title_input.text().strip()

    def set_session_title(self, title: str) -> None:
        self._title_input.setText(str(title or "Band Rehearsal"))

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

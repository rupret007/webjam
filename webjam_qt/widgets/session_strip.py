"""
SessionStrip — top bar.

Shows, left to right:
  - Logo
  - Session title + mode subtitle
  - Live session timer
  - Record indicator
  - Mode picker
  - Primary actions (Start Audio, Webex, Studio)

Emits semantic signals; ApplicationController wires them to services.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTime, QTimer, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDragMoveEvent, QDropEvent
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

from core.creative_modes import CreatorProfile
from core.meeting_link import (
    MEETING_DIRECT_CAPTURE_BOUNDARY,
    RECORD_SESSION_MEETING_CAPTURE_NOTICE,
)
from webjam_qt.theme.brand import BrandMark
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.shared_track_waveform import SharedTrackWaveform


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
    shared_track_dropped = Signal(str)  # one supported host-local audio file
    shared_track_play_requested = Signal()
    shared_track_pause_requested = Signal()
    shared_track_stop_requested = Signal()

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
        self._tools_enabled = True
        self._shared_track_host = False
        self._shared_track_channel_present = False
        self._shared_track_source_change_allowed = False
        self._shared_track_snapshot_seen = False
        self._shared_track_projection_visible = False
        self._shared_track_transport_action = "play"
        self._shared_track_transport_enabled = False
        self._shared_track_stop_enabled = False
        self._recording_control_available = False
        self._recording_seen_active = False
        self._recording_phase = "idle"
        self._compact_control_labels = False
        self._creator_profile_key = "music"
        self._creator_profile_label = "Music"
        self._creator_profile_locked = False
        self._creator_profile_preview = False
        self._participant_singular = "musician"
        self._participant_plural = "musicians"
        self._session_noun = "music session"
        self._reference_audio_noun = "backing track"
        self._record_button_full_text = "● Record Session"
        self._audio_button_full_text = "Start Session"
        self._video_configured = True
        self.setAcceptDrops(True)
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

        # Backing store for the legacy creative-mode key that session metadata
        # still records. It is deliberately not a picker: what someone is
        # making is chosen once, at launch, from the creator profiles. This
        # combo is never shown, never laid out, and never enabled, so the
        # retired five-mode list cannot resurface as a second, contradictory
        # choice beside the profile they already made.
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
        self._mode_picker.setEnabled(False)

        self._audio_button = QPushButton("Start Session")
        self._audio_button.setObjectName("AudioButton")
        self._audio_button.setAccessibleName("Start or end the band session")
        self._audio_button.setToolTip(
            "Start or end the band's live music session. WebJam handles the engine."
        )
        self._audio_button.clicked.connect(self.launch_audio_requested.emit)
        self._audio_button.setVisible(False)

        self._record_button = QPushButton("● Record Session")
        # Recording is the one creative action in the live strip that must
        # remain unmistakable at a glance.  Reuse Studio's primary record
        # treatment so the same action has the same visual language in both
        # surfaces; profile-specific copy and accessibility stay semantic.
        self._record_button.setObjectName("StudioRecordButton")
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
        self._test_button.setAccessibleName("Band Check and Practice Solo")
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

        self._video_button = QPushButton("Conversation")
        self._video_button.setObjectName("GhostButton")
        self._video_button.setAccessibleName("Show conversation controls")
        self._video_button.setToolTip(
            "Show WebJam's conversation controls.\n"
            "This does not open or rejoin the meeting."
        )
        self._video_button.setAccessibleDescription(
            "Show WebJam's Conversation panel without opening the meeting link."
        )
        self._video_button.clicked.connect(
            lambda: self.tool_requested.emit("conversation")
        )

        self._studio_button = QPushButton("Studio")
        self._studio_button.setObjectName("GhostButton")
        self._studio_button.setAccessibleName("Open Studio")
        self._studio_button.setAccessibleDescription(
            "Open completed take review during a live jam or the song project "
            "workspace when WebJam was opened in Reference Studio."
        )
        self._studio_button.setToolTip(
            "Open Studio to review completed takes and work on the song."
        )
        self._studio_button.clicked.connect(
            lambda: self.tool_requested.emit("takes")
        )

        self._shared_track_surface = QFrame()
        self._shared_track_surface.setObjectName("SharedTrackLiveDeck")
        self._shared_track_surface.setAccessibleName("Shared Track live controls")
        self._shared_track_surface.setAccessibleDescription(
            "No Shared Track loaded"
        )
        self._shared_track_surface.setMaximumWidth(390)
        self._shared_track_surface.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        shared_layout = QHBoxLayout(self._shared_track_surface)
        shared_layout.setContentsMargins(Space.XS, 0, Space.XS, 0)
        shared_layout.setSpacing(Space.XS)

        self._reference_track_button = QPushButton("＋ Shared Track")
        self._reference_track_button.setObjectName("GhostButton")
        self._reference_track_button.setAccessibleName("Open Shared Track")
        self._reference_track_button.setAccessibleDescription(
            "Open the host-controlled Shared Track player. Adding and inspecting "
            "a song does not start playback."
        )
        self._reference_track_button.setToolTip(
            "Open Shared Track to load and inspect a song.\n"
            "Playback remains locked until its isolated Jamulus route is proven."
        )
        self._reference_track_button.clicked.connect(
            lambda: self.tool_requested.emit("reference_track")
        )
        self._reference_track_button.setMaximumWidth(128)
        self._reference_track_button.setVisible(False)
        shared_layout.addWidget(self._reference_track_button)
        self._shared_track_waveform = SharedTrackWaveform(
            self._shared_track_surface,
            compact=True,
        )
        shared_layout.addWidget(self._shared_track_waveform)
        self._shared_track_transport = QPushButton("▶")
        self._shared_track_transport.setObjectName("SharedTrackTransportButton")
        self._shared_track_transport.setFixedSize(32, 32)
        self._shared_track_transport.setAccessibleName("Play Shared Track")
        self._shared_track_transport.setToolTip("Play Shared Track")
        self._shared_track_transport.clicked.connect(
            self._emit_shared_track_transport
        )
        self._shared_track_transport.setVisible(False)
        shared_layout.addWidget(self._shared_track_transport)
        self._shared_track_stop = QPushButton("■")
        self._shared_track_stop.setObjectName("SharedTrackTransportButton")
        self._shared_track_stop.setFixedSize(32, 32)
        self._shared_track_stop.setAccessibleName("Stop Shared Track")
        self._shared_track_stop.setToolTip("Stop Shared Track")
        self._shared_track_stop.clicked.connect(
            self.shared_track_stop_requested.emit
        )
        self._shared_track_stop.setVisible(False)
        shared_layout.addWidget(self._shared_track_stop)
        self._shared_track_state = QLabel("Not loaded")
        self._shared_track_state.setObjectName("SharedTrackLiveState")
        self._shared_track_state.setAccessibleName("Shared Track status")
        self._shared_track_state.setMaximumWidth(82)
        shared_layout.addWidget(self._shared_track_state)
        self._shared_track_surface.setVisible(False)

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
        self._audio_settings_action = QAction("Sound Settings…", tools_menu)
        self._audio_settings_action.setToolTip(
            "Bring Jamulus forward. Jamulus owns your instrument, headphones, and buffer."
        )
        self._audio_settings_action.triggered.connect(
            lambda: self.tool_requested.emit("audio_settings")
        )
        jamulus_updates_action = QAction("Check for Updates…", tools_menu)
        jamulus_updates_action.setToolTip(
            "Check for a WebJam-approved Jamulus component without interrupting "
            "the current session."
        )
        jamulus_updates_action.triggered.connect(
            lambda: self.tool_requested.emit("jamulus_updates")
        )
        conversation_action = QAction("Conversation", tools_menu)
        conversation_action.setToolTip(
            "Show Conversation controls without opening the meeting."
        )
        conversation_action.triggered.connect(
            lambda: self.tool_requested.emit("conversation")
        )
        self._recording_setup_action = QAction("Recording Setup…", tools_menu)
        self._recording_setup_action.triggered.connect(
            lambda: self.tool_requested.emit("recording_setup")
        )
        self._reference_track_action = QAction("Shared Track…", tools_menu)
        self._reference_track_action.setToolTip(
            "Route a host-controlled song into the jam as its own Jamulus participant."
        )
        self._reference_track_action.triggered.connect(
            lambda: self.tool_requested.emit("reference_track")
        )
        self._reference_video_action = QAction("Reference Video…", tools_menu)
        self._reference_video_action.setToolTip(
            "Optional. Watch one local video the host plays, paused and moved "
            "for everyone. Each computer plays its own copy of the same file."
        )
        self._reference_video_action.triggered.connect(
            lambda: self.tool_requested.emit("reference_video")
        )
        self._reference_video_action.setVisible(False)
        self._notes_action = QAction("Notes", tools_menu)
        self._notes_action.triggered.connect(
            lambda: self.tool_requested.emit("canvas")
        )
        self._pocket_stage_action = QAction("Use iPhone as Pocket Stage…", tools_menu)
        self._pocket_stage_action.setToolTip(
            "Pair an iPhone as a secure instrument-side session remote."
        )
        self._pocket_stage_action.triggered.connect(
            lambda: self.tool_requested.emit("pocket_stage")
        )
        self._diagnostics_action = QAction(
            "Band Check / Verify Sound\tF2", tools_menu
        )
        self._diagnostics_action.triggered.connect(
            lambda: self.tool_requested.emit("diagnostics")
        )
        help_action = QAction("Help", tools_menu)
        help_action.triggered.connect(lambda: self.tool_requested.emit("help"))
        support_action = QAction("Support", tools_menu)
        support_action.triggered.connect(lambda: self.tool_requested.emit("support"))
        about_action = QAction("About WebJam", tools_menu)
        about_action.triggered.connect(lambda: self.tool_requested.emit("about"))

        # Grouped by what the musician is trying to do, not by which
        # component implements it. Studio is not repeated here because it is
        # already a first-class button on the session bar.
        # Sound
        tools_menu.addAction(self._audio_settings_action)
        tools_menu.addAction(self._diagnostics_action)
        tools_menu.addSeparator()
        # Meeting
        tools_menu.addAction(conversation_action)
        tools_menu.addSeparator()
        # This session
        tools_menu.addAction(self._recording_setup_action)
        tools_menu.addAction(self._reference_track_action)
        tools_menu.addAction(self._reference_video_action)
        tools_menu.addAction(self._notes_action)
        tools_menu.addAction(self._pocket_stage_action)
        # Resetting the invite acts on this session, so it belongs with the
        # session group rather than trailing the About item.
        self._reset_invite_action = QAction("Reset Invite", tools_menu)
        self._reset_invite_action.setToolTip(
            "Revoke the current private invitation and create a new one."
        )
        self._reset_invite_action.setVisible(False)
        self._reset_invite_action.triggered.connect(
            self.reset_invite_requested.emit
        )
        tools_menu.addAction(self._reset_invite_action)
        tools_menu.addSeparator()
        # WebJam itself
        settings_action = QAction("Settings…", tools_menu)
        settings_action.triggered.connect(lambda: self.tool_requested.emit("settings"))
        tools_menu.addAction(settings_action)
        tools_menu.addAction(jamulus_updates_action)
        tools_menu.addAction(help_action)
        tools_menu.addAction(support_action)
        tools_menu.addAction(about_action)
        # Backward-compatible reference used by set_video_state(). Both this
        # menu item and the direct Conversation button navigate through the same
        # side-effect-free Conversation route.
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
        layout.addWidget(self._shared_track_surface)
        layout.addWidget(self._studio_button)
        layout.addWidget(self._tools_button)

        # --- Timer
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_creator_profile(
        self,
        profile: CreatorProfile,
        *,
        locked: bool = False,
    ) -> None:
        """Apply creator vocabulary without changing recording authority."""

        if not isinstance(profile, CreatorProfile):
            raise TypeError("profile must be a CreatorProfile")
        self._creator_profile_key = profile.key
        self._creator_profile_label = profile.label
        self._creator_profile_locked = bool(locked)
        self._creator_profile_preview = profile.is_preview
        self._participant_singular = profile.vocabulary.participant_singular
        self._participant_plural = profile.vocabulary.participant_plural
        self._session_noun = profile.vocabulary.session_noun
        self._reference_audio_noun = profile.vocabulary.reference_audio_noun
        reference_video = bool(profile.capabilities.shared_reference_video)
        self._reference_video_action.setVisible(reference_video)
        self._reference_video_action.setEnabled(reference_video)
        self._reference_video_action.setText(
            f"{profile.vocabulary.reference_video_noun.title()}…"
        )
        self._sync_subtitle()
        self._subtitle.setVisible(True)
        self._title_input.setAccessibleDescription(
            f"Title for this {profile.vocabulary.session_noun}."
        )
        self._sync_creator_profile_controls()

    def _sync_creator_profile_controls(self) -> None:
        """Apply live-shell vocabulary without changing native ownership truth."""

        profile_key = self._creator_profile_key
        if profile_key == "music":
            audio_name = "Start or end the band session"
            audio_tip = (
                "Start or end the band's live music session. WebJam handles the engine."
            )
            record_name = "Start or stop band-server multitrack recording"
            record_tip = (
                "Record one synchronized track per connected musician.\n"
                "Open Studio to see the tracks, waveforms, and playback mix."
            )
            invite_name = "Copy band invite"
            invite_tip = "Copy one complete link to send to a bandmate."
            studio_name = "Open Studio"
            studio_description = (
                "Open completed take review during a live jam or the song project "
                "workspace when WebJam was opened in Reference Studio."
            )
            studio_tip = "Open Studio to review completed takes and work on the song."
            conversation_name = "Show conversation controls"
            check_label = "Band Check"
            check_menu_label = "Band Check / Verify Sound"
            practice_label = "Practice Solo"
            practice_tip = "Start a private local Jamulus practice session"
            audio_settings_tip = (
                "Bring Jamulus forward. Jamulus owns your instrument, headphones, "
                "and buffer."
            )
            recording_setup_tip = (
                "Configure band-server recording and optional Local Originals."
            )
            reference_description = (
                "Open the host-controlled Shared Track player. Adding and inspecting "
                "a song does not start playback."
            )
            reference_tip = (
                "Open Shared Track to load and inspect a song.\n"
                "Playback remains locked until its isolated Jamulus route is proven."
            )
            reference_action_tip = (
                "Route a host-controlled song into the jam as its own Jamulus "
                "participant."
            )
            pocket_stage_tip = (
                "Pair an iPhone as a secure instrument-side session remote."
            )
        elif profile_key == "podcast_voice":
            audio_name = "Start or end the recording session"
            audio_tip = (
                "Start or end the speakers' live WebJam audio session. WebJam "
                "controls the native Jamulus engine; the meeting app remains an "
                f"external handoff. {MEETING_DIRECT_CAPTURE_BOUNDARY}"
            )
            record_name = "Start or stop synchronized voice recording"
            record_tip = (
                "Record one synchronized Jamulus-server track per connected speaker, "
                "plus explicitly enabled Local Originals.\n"
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE} Open Studio to review "
                "the tracks, waveforms, and playback mix."
            )
            invite_name = "Copy recording session invite"
            invite_tip = "Copy one complete WebJam link to send to another speaker."
            studio_name = "Open Podcast & Voice Studio"
            studio_description = (
                "Open completed voice-take review during a live recording session or "
                "the local Podcast & Voice project workspace."
            )
            studio_tip = (
                "Open Studio to review completed voice takes or work on the recording."
            )
            conversation_name = "Show recording-session conversation controls"
            check_label = "Sound Check"
            check_menu_label = "Sound Check / Verify Voice"
            practice_label = "Solo Voice"
            practice_tip = "Start a private local Jamulus voice session"
            audio_settings_tip = (
                "Bring Jamulus forward. Jamulus owns your microphone, headphones, "
                "and buffer."
            )
            recording_setup_tip = (
                "Configure synchronized voice recording and optional Local Originals."
            )
            reference_description = (
                "Open the host-controlled Shared Track player. Adding and inspecting "
                "reference audio does not start playback."
            )
            reference_tip = (
                "Open Shared Track to load and inspect reference audio.\n"
                "Playback remains locked until its isolated Jamulus route is proven."
            )
            reference_action_tip = (
                "Route host-controlled reference audio into the recording session as "
                "its own Jamulus participant."
            )
            pocket_stage_tip = (
                "Pair an iPhone as a secure recording-session remote."
            )
        else:
            audio_name = "Start or end the review session"
            audio_tip = (
                "Start or end the participants' live WebJam audio session. WebJam "
                "controls the native Jamulus engine; the meeting app remains an "
                f"external handoff. {MEETING_DIRECT_CAPTURE_BOUNDARY}"
            )
            record_name = "Start or stop synchronized WebJam audio recording"
            record_tip = (
                "Record one synchronized Jamulus-server track per connected "
                "participant, plus explicitly enabled Local Originals.\n"
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE} Preview take review "
                "supports playback only; editing and track export are unavailable."
            )
            invite_name = "Copy review session invite"
            invite_tip = "Copy one complete WebJam link to send to another participant."
            studio_name = "Open completed take review"
            studio_description = (
                "Open completed WebJam-audio takes for playback review. This Preview "
                "does not support take editing, local projects, or track export."
            )
            studio_tip = (
                "Review completed WebJam-audio takes in playback-only Preview mode."
            )
            conversation_name = "Show review-session conversation controls"
            check_label = "Session Check"
            check_menu_label = "Session Check / Verify Audio"
            practice_label = "Private Review Audio"
            practice_tip = "Start a private local Jamulus audio session for review"
            audio_settings_tip = (
                "Bring Jamulus forward. Jamulus owns your audio input, headphones, "
                "and buffer."
            )
            recording_setup_tip = (
                "Configure live WebJam-path recording. Local projects, take editing, "
                "and track export remain unavailable in this Preview."
            )
            reference_description = (
                "Open the host-controlled Shared Track player. Adding and inspecting "
                "reference audio does not start playback."
            )
            reference_tip = (
                "Open Shared Track to load and inspect reference audio.\n"
                "Playback remains locked until its isolated Jamulus route is proven."
            )
            reference_action_tip = (
                "Route host-controlled reference audio into the review session as its "
                "own Jamulus participant."
            )
            pocket_stage_tip = "Pair an iPhone as a secure review-session remote."

        self._audio_button.setAccessibleName(audio_name)
        self._audio_button.setToolTip(audio_tip)
        self._record_button.setAccessibleName(record_name)
        self._record_button.setToolTip(record_tip)
        self._invite_button.setAccessibleName(invite_name)
        self._invite_button.setToolTip(invite_tip)
        self._studio_button.setAccessibleName(studio_name)
        self._studio_button.setAccessibleDescription(studio_description)
        self._studio_button.setToolTip(studio_tip)
        self._video_button.setAccessibleName(conversation_name)
        self._test_button.setText(f"{check_label} ▾")
        self._test_button.setAccessibleName(f"{check_label} and {practice_label}")
        self._ready_action.setText(f"{check_label}\tF2")
        self._ready_action.setToolTip(
            "Check your input, headphones, connection, and recording readiness"
        )
        self._practice_action.setText(f"{practice_label}\tCtrl+P")
        self._practice_action.setToolTip(practice_tip)
        self._audio_settings_action.setToolTip(audio_settings_tip)
        self._recording_setup_action.setToolTip(recording_setup_tip)
        self._reference_track_button.setAccessibleDescription(reference_description)
        self._reference_track_button.setToolTip(reference_tip)
        self._reference_track_action.setToolTip(reference_action_tip)
        self._pocket_stage_action.setToolTip(pocket_stage_tip)
        self._diagnostics_action.setText(f"{check_menu_label}\tF2")
        self._notes_action.setToolTip(
            "Open local session notes. Notes stay on this computer and are not "
            "media-timecode synchronized."
        )
        self._tools_button.setAccessibleDescription(
            f"Open sound settings, {check_label}, conversation, recording, Shared "
            "Track, local notes, and WebJam support options."
        )
        self._sync_conversation_presentation()
        self._sync_audio_action_accessibility()
        self._sync_recording_profile_accessibility()

    def _conversation_policy_copy(self) -> str:
        if self._creator_profile_key == "podcast_voice":
            return RECORD_SESSION_MEETING_CAPTURE_NOTICE
        if self._creator_profile_key == "review_rehearsal":
            return (
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE} "
                "Visual media and media timecode are not synchronized."
            )
        return ""

    def _sync_conversation_presentation(self) -> None:
        self._video_action.setText(
            "Conversation" if self._video_configured else "Set Up Conversation"
        )
        tooltip = (
            "Show WebJam's conversation controls.\n"
            "This does not open or rejoin the meeting."
            if self._video_configured
            else "Show Conversation controls and add a public HTTPS meeting link."
        )
        policy = self._conversation_policy_copy()
        if policy:
            tooltip = f"{tooltip}\n{policy}"
        self._video_button.setToolTip(tooltip)
        self._video_action.setToolTip(tooltip)
        self._sync_conversation_accessibility()

    def _sync_conversation_accessibility(self) -> None:
        description = (
            "Show WebJam's Conversation panel without opening the meeting link."
        )
        raw_status = self._video_button.property("webexLaunchAction")
        if raw_status:
            status = "Open Meeting" if raw_status == "Open Webex" else str(raw_status)
            description = f"{description} External handoff status: {status}."
        policy = self._conversation_policy_copy()
        if policy:
            description = f"{description} {policy}"
        self._video_button.setAccessibleDescription(description)

    def _sync_audio_action_accessibility(self) -> None:
        label = self._audio_button_full_text
        if self._creator_profile_key == "music":
            description = f"Band session action. Current action: {label}."
        else:
            description = (
                f"{self._session_noun.capitalize()} action. Current action: {label}. "
                "WebJam controls the native Jamulus audio path; the meeting app "
                f"remains an external handoff. {MEETING_DIRECT_CAPTURE_BOUNDARY}"
            )
        self._audio_button.setAccessibleDescription(description)

    def _sync_recording_profile_accessibility(self) -> None:
        if self._recording_phase == "starting":
            description = (
                "Recording is being armed on the band server."
                if self._creator_profile_key == "music"
                else "Recording is being armed on the WebJam-hosted Jamulus server."
            )
            self._record_button.setAccessibleDescription(description)
        elif self._recording_phase == "idle":
            description = (
                "Start band-server multitrack recording."
                if self._creator_profile_key == "music"
                else f"Start synchronized WebJam audio recording for this {self._session_noun}."
            )
            self._record_button.setAccessibleDescription(description)

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
        self._audio_button_full_text = str(label)
        self._audio_button.setText(self._compact_control_label(label))
        self._audio_button.setEnabled(enabled)
        # Start/retry lives in the focused stage card. The header owns only
        # the in-session End action, avoiding duplicate primary buttons.
        self._audio_button.setVisible(
            label
            in {
                "End Session",
                "Leave Jam",
                "Ending…",
                "Leaving…",
                "Stopping…",
                "Try End Session",
                "Try Leave Jam",
            }
        )
        self._audio_button.setAccessibleName(label)
        self._sync_audio_action_accessibility()

    @staticmethod
    def _compact_control_text(label: str) -> str:
        """Keep safety actions legible at the supported 720 px window floor."""

        return {
            "Copy Invite": "Invite",
            "Conversation": "Talk",
            "● Record Session": "● Record",
            "■ Stop Recording": "■ Stop",
            "■ Finish Stop": "■ Finish",
            "Retry Record": "Retry",
            "End Session": "End",
            "Leave Jam": "Leave",
            "Try End Session": "Try End",
            "Try Leave Jam": "Try Leave",
        }.get(str(label), str(label))

    def _compact_control_label(self, label: str) -> str:
        return (
            self._compact_control_text(label)
            if self._compact_control_labels
            else str(label)
        )

    def _set_record_button_label(self, label: str) -> None:
        self._record_button_full_text = str(label)
        self._record_button.setText(self._compact_control_label(label))
        # The shortened compact label is visual density only; assistive
        # technology always receives the complete semantic action.
        self._record_button.setAccessibleName(str(label))

    def set_compact_control_labels(self, compact: bool) -> None:
        """Adapt bottom-bar copy without hiding any live-session action."""

        self._compact_control_labels = bool(compact)
        self._invite_button.setText(
            self._compact_control_label("Copy Invite")
        )
        self._video_button.setText(
            self._compact_control_label("Conversation")
        )
        self._record_button.setText(
            self._compact_control_label(self._record_button_full_text)
        )
        self._audio_button.setText(
            self._compact_control_label(self._audio_button_full_text)
        )

    def set_video_state(self, label: str, *, enabled: bool = True) -> None:
        """Retain external-launch status without changing navigation semantics.

        The direct button always means "show Conversation". Launch progress is
        rendered by :class:`WebexEmbed`; disabling this navigation button
        while a handoff is in progress would make that truthful status harder
        to reach.
        """

        self._video_button.setProperty("webexLaunchAction", label)
        self._video_button.setEnabled(self._tools_enabled)
        self._sync_conversation_accessibility()
        self._video_action.setEnabled(self._tools_enabled)

    def set_video_configured(self, configured: bool) -> None:
        self._video_configured = bool(configured)
        self._sync_conversation_presentation()

    def set_tools_enabled(self, enabled: bool) -> None:
        self._tools_enabled = bool(enabled)
        self._tools_button.setEnabled(self._tools_enabled)
        self._video_button.setEnabled(self._tools_enabled)
        self._reference_track_button.setEnabled(
            self._tools_enabled and self._shared_track_host
        )
        self._shared_track_transport.setEnabled(
            self._tools_enabled and self._shared_track_transport_enabled
        )
        self._shared_track_stop.setEnabled(
            self._tools_enabled and self._shared_track_stop_enabled
        )
        self._studio_button.setEnabled(self._tools_enabled)
        self._video_action.setEnabled(self._tools_enabled)

    def set_recording_phase(self, phase: str, detail: str = "") -> None:
        """Render the recorder state machine without relying on transient banners.

        ``detail`` refines the chip text during validation (staged progress).
        """
        previous_phase = self._recording_phase
        phase = str(phase or "idle").lower()
        self._recording_phase = phase
        if phase == "preflight":
            self._set_record_button_label("Preparing…")
            self._record_button.setEnabled(False)
            self._record_elapsed.setText("PREPARING")
            self._record_elapsed.setVisible(True)
            description = "Checking server and isolated host recording inputs."
        elif phase == "starting":
            self._set_record_button_label("Preparing…")
            self._record_button.setEnabled(False)
            self._record_elapsed.setText("PREPARING")
            self._record_elapsed.setVisible(True)
            description = (
                "Recording is being armed on the band server."
                if self._creator_profile_key == "music"
                else "Recording is being armed on the WebJam-hosted Jamulus server."
            )
        elif phase == "count_in":
            self._recording_seen_active = True
            self._set_record_button_label("■ Stop Recording")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("COUNT-IN")
            self._record_elapsed.setVisible(True)
            description = (
                "Recording is active while the Shared Track count-in plays."
            )
        elif phase == "recording":
            self._recording_seen_active = True
            self._set_record_button_label("■ Stop Recording")
            self._record_button.setEnabled(True)
            if not self._record_clock.isActive():
                self._record_elapsed_seconds = 0
                self._update_record_elapsed()
                self._record_clock.start()
            self._record_elapsed.setVisible(True)
            description = "Recording is active. Activate to stop and verify the take."
        elif phase == "stop_failed":
            self._set_record_button_label("■ Finish Stop")
            self._record_button.setEnabled(True)
            if not self._record_clock.isActive():
                self._record_clock.start()
            self._record_elapsed.setText("CLEANUP PENDING")
            self._record_elapsed.setVisible(True)
            description = (
                "The server may still be recording. Activate to try stopping again."
            )
        elif phase == "stopping":
            self._set_record_button_label("Stopping…")
            self._record_button.setEnabled(False)
            self._record_clock.stop()
            self._record_elapsed.setText("STOPPING")
            self._record_elapsed.setVisible(True)
            description = (
                "Stop was requested; the server may still be recording while it "
                "closes the recorder. WebJam will finalize only after confirmation."
            )
        elif phase in {"finalizing", "validating"}:
            self._set_record_button_label("Finalizing…")
            self._record_button.setEnabled(False)
            self._record_clock.stop()
            self._record_elapsed.setText(detail or "FINALIZING…")
            self._record_elapsed.setVisible(True)
            description = "WebJam is waiting for stable files and validating every track."
            if detail:
                description = f"WebJam is validating the take. {detail.capitalize()}"
        elif phase == "needs_attention":
            self._record_clock.stop()
            self._set_record_button_label("● Record Session")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("NEEDS ATTENTION")
            self._record_elapsed.setVisible(True)
            description = "The take was preserved but did not pass recording validation."
        elif phase == "complete":
            self._record_clock.stop()
            self._set_record_button_label("● Record Session")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("READY · TAKE SAVED")
            self._record_elapsed.setVisible(True)
            description = "The previous take passed validation. Activate to record another."
        elif phase == "error":
            self._record_clock.stop()
            self._set_record_button_label("Retry Record")
            self._record_button.setEnabled(True)
            self._record_elapsed.setText("RECORD ERROR")
            self._record_elapsed.setVisible(True)
            description = "The recording request failed. Activate to try again."
        else:
            self._record_clock.stop()
            self._set_record_button_label("● Record Session")
            self._record_button.setEnabled(True)
            if (
                not self._recording_control_available
                and self._recording_seen_active
                and previous_phase not in {"complete", "needs_attention"}
            ):
                self._record_elapsed.setText("RECORDING STOPPED")
                self._record_elapsed.setVisible(True)
            else:
                self._record_elapsed.setVisible(False)
            description = (
                "Start band-server multitrack recording."
                if self._creator_profile_key == "music"
                else f"Start synchronized WebJam audio recording for this {self._session_noun}."
            )
        self._record_button.setAccessibleName(self._record_button_full_text)
        self._record_button.setAccessibleDescription(description)
        self._record_button.setVisible(self._recording_control_available)
        if not self._recording_control_available:
            # Guests still need the host's explicit terminal Ready/attention
            # truth.  Only an ensuing idle transition decides whether a
            # stopped chip should remain (salvaged active capture) or clear
            # (a completed/attention take whose session has now ended).
            if phase != "idle":
                self._record_elapsed.setVisible(True)

    def set_recording_available(self, available: bool) -> None:
        """Only the host owns the synchronized take; joiners are recorded there."""
        self._recording_control_available = bool(available)
        self._record_button.setVisible(self._recording_control_available)
        if not self._recording_control_available and not self._recording_seen_active:
            self._record_elapsed.setVisible(False)

    def clear_recording_session_status(self) -> None:
        """Retire terminal take copy after its owning audio session ends."""

        self._record_clock.stop()
        self._recording_phase = "idle"
        self._recording_seen_active = False
        self._record_elapsed.setVisible(False)

    def set_reference_track_available(self, host: bool) -> None:
        """Grant host controls while retaining bounded guest-visible state."""

        self._shared_track_host = bool(host)
        if self._shared_track_host and not self._shared_track_snapshot_seen:
            self._shared_track_source_change_allowed = True
        self._reference_track_action.setVisible(self._shared_track_host)
        self._reference_track_action.setEnabled(self._shared_track_host)
        self._reference_track_button.setVisible(self._shared_track_host)
        self._reference_track_button.setEnabled(
            self._shared_track_host and self._tools_enabled
        )
        self._shared_track_transport.setVisible(False)
        self._shared_track_stop.setVisible(False)
        self._shared_track_surface.setVisible(
            self._shared_track_host
            or self._shared_track_channel_present
            or self._shared_track_projection_visible
        )

    def set_shared_track_snapshot(self, snapshot: object) -> None:
        """Render the host-owned source/transport truth in the live mini deck."""

        self._shared_track_snapshot_seen = True
        state_value = getattr(getattr(snapshot, "state", None), "value", "")
        state = str(state_value or getattr(snapshot, "state", "idle")).lower()
        loaded = bool(str(getattr(snapshot, "source_name", "") or ""))
        cleanup_pending = bool(getattr(snapshot, "cleanup_pending", False))
        count_in_active = bool(getattr(snapshot, "count_in_active", False))
        labels = {
            "unavailable": "Route locked",
            "idle": "Not loaded",
            "loading": "Loading…",
            "ready": "Ready",
            "routing": "Starting…",
            "playing": "Count-in" if count_in_active else "Playing",
            "paused": "Paused",
            "stopping": "Stopping…",
            "failed": "Needs attention",
            "closed": "Closed",
        }
        label = "Cleanup pending" if cleanup_pending else labels.get(state, "Checking…")
        self._shared_track_state.setText(label)
        self._shared_track_state.setAccessibleDescription(label)
        self._shared_track_waveform.set_snapshot(snapshot)
        self._shared_track_source_change_allowed = state in {
            "idle",
            "ready",
            "failed",
            "unavailable",
        } and not cleanup_pending
        self._reference_track_button.setText(
            "Shared Track" if loaded else "＋ Shared Track"
        )
        self._shared_track_transport_action = (
            "pause" if state == "playing" else "play"
        )
        self._shared_track_transport.setText(
            "Ⅱ" if self._shared_track_transport_action == "pause" else "▶"
        )
        transport_name = (
            "Pause Shared Track"
            if self._shared_track_transport_action == "pause"
            else "Resume Shared Track"
            if state == "paused"
            else "Play Shared Track"
        )
        self._shared_track_transport.setAccessibleName(transport_name)
        self._shared_track_transport.setToolTip(transport_name)
        can_play = bool(getattr(snapshot, "can_play", state == "ready"))
        self._shared_track_transport_enabled = bool(
            self._shared_track_host
            and loaded
            and not cleanup_pending
            and (
                state in {"playing", "paused"}
                or (state == "ready" and can_play)
            )
        )
        self._shared_track_stop_enabled = bool(
            self._shared_track_host
            and (
                state in {"routing", "playing", "paused", "stopping"}
                or cleanup_pending
            )
        )
        self._shared_track_transport.setVisible(
            self._shared_track_host and loaded
        )
        self._shared_track_transport.setEnabled(
            self._tools_enabled and self._shared_track_transport_enabled
        )
        self._shared_track_stop.setVisible(
            self._shared_track_host
            and (loaded or self._shared_track_stop_enabled)
        )
        self._shared_track_stop.setEnabled(
            self._tools_enabled and self._shared_track_stop_enabled
        )
        source_name = str(getattr(snapshot, "source_name", "") or "")
        description = f"Shared Track: {label}."
        if source_name:
            description = f"Shared Track {source_name}: {label}."
        self._shared_track_surface.setAccessibleDescription(description)
        self._shared_track_surface.setToolTip(description)
        self._shared_track_projection_visible = bool(
            loaded or state not in {"idle", "unavailable", "closed"}
        )
        self._shared_track_surface.setVisible(
            self._shared_track_host
            or self._shared_track_channel_present
            or self._shared_track_projection_visible
        )

    def clear_shared_track_projection(self) -> None:
        """Retire host-published guest truth at the session ownership boundary."""

        self._shared_track_snapshot_seen = False
        self._shared_track_projection_visible = False
        self._shared_track_source_change_allowed = self._shared_track_host
        self._shared_track_transport_action = "play"
        self._shared_track_transport_enabled = False
        self._shared_track_stop_enabled = False
        self._reference_track_button.setText("＋ Shared Track")
        self._shared_track_transport.setText("▶")
        self._shared_track_transport.setAccessibleName("Play Shared Track")
        self._shared_track_transport.setToolTip("Play Shared Track")
        self._shared_track_transport.setVisible(False)
        self._shared_track_transport.setEnabled(False)
        self._shared_track_stop.setVisible(False)
        self._shared_track_stop.setEnabled(False)
        self._shared_track_state.setText("Not loaded")
        self._shared_track_state.setAccessibleDescription("Not loaded")
        description = "No Shared Track is published for this session."
        self._shared_track_surface.setAccessibleDescription(description)
        self._shared_track_surface.setToolTip(description)
        self._shared_track_waveform.clear(description)
        self._shared_track_surface.setVisible(
            self._shared_track_host or self._shared_track_channel_present
        )
        if self._shared_track_channel_present and not self._shared_track_host:
            # Preserve only the weak authenticated-roster observation. The old
            # host filename, transport, timing, and waveform are already gone.
            self.set_shared_track_channel_present(True)

    def set_shared_track_channel_present(self, present: bool) -> None:
        """Show a bounded guest observation without claiming host transport truth."""

        self._shared_track_channel_present = bool(present)
        if not self._shared_track_host:
            self._reference_track_button.setVisible(False)
            self._shared_track_transport.setVisible(False)
            self._shared_track_stop.setVisible(False)
            self._shared_track_surface.setVisible(
                self._shared_track_channel_present
                or self._shared_track_projection_visible
            )
            if self._shared_track_channel_present and not self._shared_track_snapshot_seen:
                detail = "Channel visible"
                self._shared_track_state.setText(detail)
                self._shared_track_state.setAccessibleDescription(detail)
                description = (
                    "A Shared Track channel is visible in Jamulus. This client "
                    "does not claim that it is audible or currently playing."
                )
                self._shared_track_surface.setAccessibleDescription(description)
                self._shared_track_surface.setToolTip(description)
                self._shared_track_waveform.clear(description)

    def _emit_shared_track_transport(self) -> None:
        if not self._shared_track_host or not self._shared_track_transport_enabled:
            return
        if self._shared_track_transport_action == "pause":
            self.shared_track_pause_requested.emit()
        else:
            self.shared_track_play_requested.emit()

    @staticmethod
    def _dropped_shared_track_path(mime_data) -> str:
        if mime_data is None or not mime_data.hasUrls():
            return ""
        urls = list(mime_data.urls())
        if len(urls) != 1 or not urls[0].isLocalFile():
            return ""
        path = urls[0].toLocalFile()
        if not path:
            return ""
        from core.reference_track import reference_track_supported_extensions

        lowered = path.casefold()
        return (
            path
            if any(
                lowered.endswith(extension)
                for extension in reference_track_supported_extensions()
            )
            else ""
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if (
            self._shared_track_host
            and self._shared_track_source_change_allowed
            and self._dropped_shared_track_path(event.mimeData())
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._dropped_shared_track_path(event.mimeData())
        if (
            not self._shared_track_host
            or not self._shared_track_source_change_allowed
            or not path
        ):
            event.ignore()
            return
        event.acceptProposedAction()
        self.shared_track_dropped.emit(path)

    def set_invite_available(self, available: bool) -> None:
        self._invite_button.setVisible(bool(available))

    def set_reset_invite_available(self, available: bool) -> None:
        """Expose revocation only while this app owns a live remote invite."""

        self._reset_invite_action.setVisible(bool(available))

    def set_pocket_stage_state(self, state: str) -> None:
        """Render the opt-in mobile listener without implying phone truth."""

        normalized = str(state or "off").lower()
        if normalized == "starting":
            self._pocket_stage_action.setText("iPhone Pocket Stage (Starting…)")
            self._pocket_stage_action.setEnabled(False)
        elif normalized == "stopping":
            self._pocket_stage_action.setText("iPhone Pocket Stage (Stopping…)")
            self._pocket_stage_action.setEnabled(False)
        elif normalized == "on":
            self._pocket_stage_action.setText("iPhone Pocket Stage (On)…")
            self._pocket_stage_action.setEnabled(True)
        elif normalized == "stop_failed":
            self._pocket_stage_action.setText("iPhone Sharing Stop Unresolved")
            self._pocket_stage_action.setEnabled(False)
        else:
            self._pocket_stage_action.setText("Use iPhone as Pocket Stage…")
            self._pocket_stage_action.setEnabled(True)

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
        if hasattr(self, "_creator_profile_label"):
            if self._creator_profile_preview:
                status = (
                    "Preview · Host profile"
                    if self._creator_profile_locked
                    else "Preview"
                )
            else:
                status = "Host profile" if self._creator_profile_locked else "Ready"
            self._subtitle.setText(
                f"{self._creator_profile_label} · {status}"
            )
            self._subtitle.setAccessibleName(
                f"Creator profile: {self._creator_profile_label}; {status}"
            )
            return
        label = self._mode_picker.currentText()
        self._subtitle.setText(f"{label} · WebJam")

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        self._update_timer_label()

    def _tick_recording(self) -> None:
        self._record_elapsed_seconds += 1
        self._update_record_elapsed()

    def _update_record_elapsed(self) -> None:
        if self._recording_phase == "count_in":
            self._record_elapsed.setText("COUNT-IN")
            return
        if self._recording_phase == "stop_failed":
            self._record_elapsed.setText("CLEANUP PENDING")
            return
        seconds = self._record_elapsed_seconds
        self._record_elapsed.setText(f"REC {seconds // 60:02d}:{seconds % 60:02d}")

    def _update_timer_label(self) -> None:
        t = QTime(0, 0).addSecs(self._elapsed_seconds)
        self._timer_label.setText(t.toString("HH:mm:ss"))

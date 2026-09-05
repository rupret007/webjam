"""
ConductorWindow — the single-window Conductor shell.

Layout (horizontal, left to right):

    ┌──────────────────────────────────────────────────────────┐
    │                   SessionStrip (top)                     │
    ├────┬─────────────────────────────┬───────────────────────┤
    │    │                             │                       │
    │Side│     Stage (participant      │   SessionCanvas       │
    │Rail│      cards + Webex embed)   │   (right panel)       │
    │    │                             │                       │
    ├────┴─────────────────────────────┴───────────────────────┤
    │                    Status Bar                            │
    └──────────────────────────────────────────────────────────┘

The window owns the layout and exposes signals; it does NOT own state.
Wiring to services happens in ApplicationController.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.creative_modes import CreatorProfile, get_creator_profile_by_key_or_default
from core.meeting_link import (
    RECORD_SESSION_MEETING_CAPTURE_NOTICE,
    STUDIO_MEETING_CAPTURE_NOTICE,
)
from webjam_qt.theme import Color
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets import (
    ParticipantGrid,
    RecordingStudio,
    ReferenceStudioShell,
    SessionCanvas,
    SessionHud,
    SessionStrip,
    SideRail,
    WebexEmbed,
    SongOverlay,
)
from webjam_qt.widgets.room_help import RoomHelpPanel


class ConductorWindow(QMainWindow):
    close_requested = Signal()
    test_night_requested = Signal()

    # Fallback only.  A real session sizes itself from the display through
    # fit_to_screen(); this is what a screenless/headless host gets.
    DEFAULT_WIDTH = 1440
    DEFAULT_HEIGHT = 900
    OFFLINE_INVITATION_GUIDANCE = (
        "Invitation not used — Reference Studio stays offline. "
        "To join the jam, close WebJam and open the invitation again."
    )

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str,
        initial_title: str,
        operator_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from webjam_qt import __version__

        self._creator_profile = get_creator_profile_by_key_or_default("music")
        self.setWindowTitle(f"WebJam — Band Session (v{__version__})")
        self.setAcceptDrops(True)
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        # Reserve 40 px around the client area for native title-bar/frame
        # chrome so the complete meeting surface fits a physical 760×600
        # display. Child layouts own adaptation below the generous default.
        self.setMinimumSize(720, 560)
        # Controller-injected veto (e.g. "a recording is running — quit?").
        self.confirm_close: Callable[[], bool] | None = None
        # A second synchronous gate owns teardown that can still fail after
        # the musician confirms closing (for example, an unsaved Studio
        # document or an unproved Reference Track process stop).  A Qt signal
        # cannot return that result, so closeEvent must call this callback
        # directly before accepting the native close.
        self.finalize_close: Callable[[], bool] | None = None
        self.operator_mode = bool(operator_mode)
        self._reference_studio_only = False

        # --- Central widgets
        self.session_strip = SessionStrip(
            mode_entries=mode_entries,
            initial_mode_key=initial_mode_key,
            initial_title=initial_title,
            operator_mode=self.operator_mode,
        )
        self.session_strip.test_night_requested.connect(self.test_night_requested.emit)
        self.session_hud = SessionHud()
        self.side_rail = SideRail()
        self.participant_grid = ParticipantGrid()
        self.webex_embed = WebexEmbed()
        self.session_canvas = SessionCanvas()
        # A direct, non-modal preview keeps troubleshooting usable while a
        # musician is still working through setup. Never mix it with saved
        # notes, and never expose it without the existing lab opt-in.
        self._room_help_dialog = QDialog(self)
        self._room_help_dialog.setWindowTitle("Session help — Development preview")
        self._room_help_dialog.setModal(False)
        self._room_help_dialog.resize(380, 340)
        self._room_help_dialog.setMinimumWidth(300)
        self.room_help = RoomHelpPanel(self._room_help_dialog)
        help_layout = QVBoxLayout(self._room_help_dialog)
        help_layout.setContentsMargins(0, 0, 0, 0)
        help_layout.addWidget(self.room_help)
        self._room_help_button = QPushButton("Help")
        self._room_help_button.setObjectName("GhostButton")
        self._room_help_button.setAccessibleName("Open temporary session help preview")
        self._room_help_button.setToolTip("Temporary help with your secure peer; development preview")
        self._room_help_button.setVisible(False)
        self._room_help_button.clicked.connect(self._show_room_help)
        self.recording_studio = RecordingStudio()
        self.reference_studio = ReferenceStudioShell(self.recording_studio)
        # Compact chrome that can sit beside a free Webex window: a fixed
        # column rather than a dialog, so opening it never takes the
        # foreground away from the meeting. Hidden until Music asks for it.
        self.song_overlay = SongOverlay()
        # Video, notes, Studio, and Settings are session tools.  They remain
        # available from one menu without competing with the live session.
        self.side_rail.setVisible(False)
        self.webex_embed.setVisible(False)
        self.session_canvas.setVisible(False)

        # Familiar meeting controls live in one predictable bottom rail. The
        # widgets remain owned by SessionStrip so all existing controller
        # signals and state methods continue to have a single source of truth.
        self.session_controls = QFrame()
        self.session_controls.setObjectName("SessionControlBar")
        self.session_controls.setAccessibleName("Session controls")
        self.session_controls.setFixedHeight(72)
        controls_layout = QHBoxLayout(self.session_controls)
        controls_layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        controls_layout.setSpacing(Space.SM)
        controls_layout.addStretch(1)
        self.session_strip._invite_button.setText("Copy Invite")
        self.session_strip._invite_button.setAccessibleName("Copy invite link")
        controls_layout.addWidget(self.session_strip._invite_button)
        controls_layout.addWidget(self.session_strip._record_elapsed)
        controls_layout.addWidget(self.session_strip._record_button)
        controls_layout.addWidget(self.session_strip._video_button)
        # Song sits beside Studio: the same class of in-session surface, on
        # the bar a musician already uses, rather than inside a menu.
        controls_layout.addWidget(self.session_strip._song_button)
        controls_layout.addWidget(self.session_strip._studio_button)
        controls_layout.addWidget(self._room_help_button)
        self.session_strip._tools_button.setText("More ▾")
        self.session_strip._tools_button.setAccessibleName("More session options")
        controls_layout.addWidget(self.session_strip._tools_button)
        self._session_end_gap = QSpacerItem(
            Space.MD,
            1,
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Minimum,
        )
        controls_layout.addItem(self._session_end_gap)
        self.session_strip._audio_button.setProperty("destructive", "true")
        controls_layout.addWidget(self.session_strip._audio_button)
        controls_layout.addStretch(1)

        # Stage combines participant grid + webex embed vertically
        stage_container = QWidget()
        stage_layout = QVBoxLayout(stage_container)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        stage_layout.addWidget(self.participant_grid, stretch=1)
        stage_layout.addWidget(self.webex_embed)

        self.center_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.center_splitter.addWidget(stage_container)
        self.center_splitter.addWidget(self.session_canvas)
        self.center_splitter.setStretchFactor(0, 3)
        self.center_splitter.setStretchFactor(1, 1)
        self.center_splitter.setSizes(
            [int(self.DEFAULT_WIDTH * 0.76), int(self.DEFAULT_WIDTH * 0.24)]
        )
        # Never collapse a pane to zero — a hidden stage or canvas mid-jam
        # looks like data loss and has no obvious restore affordance.
        self.center_splitter.setCollapsible(0, False)
        self.center_splitter.setCollapsible(1, False)
        self.center_splitter.setHandleWidth(1)

        self.workspace_stack = QStackedWidget()
        self.workspace_stack.setObjectName("WorkspaceStack")
        self.workspace_stack.addWidget(self.center_splitter)
        self.workspace_stack.addWidget(self.reference_studio)
        self.workspace_stack.setCurrentWidget(self.center_splitter)
        self._paint_along_widget: QWidget | None = None
        self._paint_along_return_widget: QWidget = self.center_splitter

        body_container = QWidget()
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(self.workspace_stack, stretch=1)
        body_layout.addWidget(self.song_overlay)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.session_strip)
        central_layout.addWidget(self.session_hud)
        central_layout.addWidget(body_container, stretch=1)
        central_layout.addWidget(self.session_controls)

        self.setCentralWidget(central)

        # --- Status bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        # Server-recording indicator — hidden until the server's recorder
        # is actually rolling (Jamulus multitrack recording, one track per
        # musician). Deliberately loud: the whole band should know.
        self._status_recording = QLabel("● REC", self._status_bar)
        self._status_recording.setObjectName("StatusRecording")
        self._status_recording.setStyleSheet(
            f"color: {Color.ACCENT_RECORD}; font-weight: 700; letter-spacing: 1px;"
        )
        self._status_recording.setToolTip(
            "The Jamulus server is recording this session — every musician "
            "gets their own track."
        )
        self._status_recording.setVisible(False)

        # Hosted band server truth — visible only when this Mac hosts it.
        self._status_server = QLabel("Server: —", self._status_bar)
        self._status_server.setToolTip(
            "This Mac is running the band's Jamulus server. It keeps running "
            "through Stop Audio and stops only when WebJam quits."
        )
        self._status_server.setVisible(False)

        self._status_audio = QLabel("Audio: —", self._status_bar)
        self._status_video = QLabel("Video: —", self._status_bar)
        self._status_latency = QLabel("Session: —", self._status_bar)
        self._status_routing = QLabel("", self._status_bar)
        self._status_audio.setVisible(False)
        self._status_video.setVisible(False)
        self._status_latency.setVisible(False)
        self._status_routing.setVisible(False)
        self._status_bar.addPermanentWidget(self._status_recording)
        self._status_bar.clearMessage()
        self._status_bar.setVisible(False)
        # Reset any temporary flash_message() color once its timed message
        # clears (QStatusBar emits messageChanged with an empty string).
        self._status_bar.messageChanged.connect(self._on_status_message_changed)

        # --- Accessibility names
        self.session_strip.setAccessibleName("Session controls strip")
        self.side_rail.setAccessibleName("Navigation rail")
        self.participant_grid.setAccessibleName("Participant mixer grid")
        self.webex_embed.setAccessibleName("External meeting launch and audio role")
        self.session_canvas.setAccessibleName("Session notes canvas")
        self.recording_studio.setAccessibleName("Multitrack recording studio")

        # --- Keyboard shortcuts
        self._setup_shortcuts()
        self.participant_grid.participants_changed.connect(self._setup_tab_order)
        self._setup_tab_order()

    def set_room_help_enabled(self, enabled: bool) -> None:
        """Expose the temporary help preview only after the lab opt-in."""

        self._room_help_enabled = enabled is True
        self._room_help_button.setVisible(self._room_help_enabled)
        self._sync_room_help_density()
        if not self._room_help_enabled:
            self._room_help_dialog.hide()

    def _sync_room_help_density(self) -> None:
        """Make space for preview Help without hiding or clipping other actions."""

        compact = bool(getattr(self, "_room_help_enabled", False)) and self.width() < 900
        bar = self.session_controls
        if bar.property("helpPreviewCompact") == compact:
            return
        bar.setProperty("helpPreviewCompact", compact)
        layout = bar.layout()
        margin = Space.SM if compact else Space.LG
        layout.setContentsMargins(margin, Space.SM, margin, Space.SM)
        layout.setSpacing(Space.XS if compact else Space.SM)
        end_gap = Space.XS if compact else Space.MD
        self._session_end_gap.changeSize(
            end_gap,
            1,
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Minimum,
        )
        layout.invalidate()
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.updateGeometry()

    def _show_room_help(self) -> None:
        if not getattr(self, "_room_help_enabled", False):
            return
        self._room_help_dialog.show()
        self._room_help_dialog.raise_()
        self._room_help_dialog.activateWindow()

    def _setup_shortcuts(self) -> None:
        # On macOS, "Ctrl" in QKeySequence parses to Cmd (Qt.ControlModifier).
        # Cmd+M is the system "Minimize Window" shortcut, which causes
        # WebJam's Ctrl+M (mute-all) to BOTH minimize the window AND fire
        # the shortcut.  To avoid this on macOS only, we map our mute
        # shortcuts to literal Control (Qt.MetaModifier on macOS).  On
        # Windows/Linux, Ctrl+M means Ctrl+M as expected.
        import sys

        on_mac = sys.platform == "darwin"

        def _ctrl(key_str: str) -> QKeySequence:
            """Build a QKeySequence that uses literal Control on macOS."""
            if on_mac:
                # Qt.MetaModifier == Control key on macOS.  In PySide6 the
                # int(modifier) | int(key) idiom doesn't work; multiply Qt
                # enums via int(.value) or use the QKeyCombination overload.
                key_enum = getattr(Qt.Key, f"Key_{key_str}", None)
                if key_enum is not None:
                    return QKeySequence(
                        Qt.KeyboardModifier.MetaModifier.value | key_enum.value
                    )
            return QKeySequence(f"Ctrl+{key_str}")

        def _live_shortcut(sequence: QKeySequence) -> QShortcut:
            """Bind a session-only shortcut below the live workspace.

            Reference Studio has its own familiar DAW shortcuts (Save, Open,
            Split, Mixer).  Parenting live-mix shortcuts to the whole window
            made both commands eligible while Studio had focus, so Qt could
            report an ambiguous shortcut or run the wrong session action.
            """

            shortcut = QShortcut(sequence, self.center_splitter)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            return shortcut

        # Cmd/Ctrl+L — focus session title
        self._title_shortcut = QShortcut(
            QKeySequence("Ctrl+L"), self, lambda: self.session_strip.focus_title()
        )
        # Cmd/Ctrl+Shift+F — retile WebJam onto its share of the screen
        self._fit_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+F"), self, self.fit_to_screen
        )
        # F2 — Band Check (signal consumed by controller)
        self._ready_check_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        # F11 — fullscreen toggle
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen)
        # Escape returns from an embedded making surface before it falls back
        # to its ordinary job of leaving full screen.
        self._escape_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self, self._handle_escape
        )
        # Cmd/Ctrl+, — open settings wizard (signal consumed by controller)
        self._settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        # Cmd/Ctrl+S — save mix; Cmd/Ctrl+O — load mix (consumed by controller)
        self._save_mix_shortcut = _live_shortcut(QKeySequence("Ctrl+S"))
        self._load_mix_shortcut = _live_shortcut(QKeySequence("Ctrl+O"))
        # Cmd/Ctrl+Shift+S — "Save Mix As..."; Cmd/Ctrl+Shift+O — "Load Mix..."
        # Multi-slot mix support: pick an arbitrary file path so users can
        # keep one mix per song / per band-mate setup.  Uses the macOS-safe
        # binder so Cmd+Shift+S doesn't collide with system shortcuts.
        if on_mac:
            self._save_mix_as_shortcut = _live_shortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_S.value
                )
            )
            self._load_mix_from_shortcut = _live_shortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_O.value
                )
            )
        else:
            self._save_mix_as_shortcut = _live_shortcut(QKeySequence("Ctrl+Shift+S"))
            self._load_mix_from_shortcut = _live_shortcut(QKeySequence("Ctrl+Shift+O"))
        # Cmd/Ctrl+T — insert timestamp into session canvas
        self._timestamp_shortcut = _live_shortcut(QKeySequence("Ctrl+T"))
        self._timestamp_shortcut.activated.connect(self.session_canvas.insert_timestamp)
        # Mute-all uses the macOS-safe binder so it does not collide with
        # system minimize (Cmd+M).
        self._practice_shortcut = _live_shortcut(_ctrl("P"))
        self._mute_all_shortcut = _live_shortcut(_ctrl("M"))
        # Cmd/Ctrl+Shift+D — copy diagnostics summary to clipboard
        if on_mac:
            self._diagnostics_shortcut = QShortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_D.value
                ),
                self,
            )
        else:
            self._diagnostics_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        # Cmd/Ctrl+Shift+R — reset all faders to 0 dB (with confirmation)
        if on_mac:
            self._reset_faders_shortcut = _live_shortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_R.value
                )
            )
        else:
            self._reset_faders_shortcut = _live_shortcut(QKeySequence("Ctrl+Shift+R"))
        # F1 — show help dialog
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, self.show_help)
        self._navigation_shortcuts = (
            QShortcut(
                QKeySequence("Ctrl+1"),
                self,
                lambda: self.side_rail.trigger("stage"),
            ),
            QShortcut(
                QKeySequence("Ctrl+2"),
                self,
                lambda: self.side_rail.trigger("canvas"),
            ),
            QShortcut(
                QKeySequence("Ctrl+3"),
                self,
                lambda: self.side_rail.trigger("takes"),
            ),
            # Cmd/Ctrl+4 — Song, next in the same run as the other surfaces.
            QShortcut(
                QKeySequence("Ctrl+4"),
                self,
                lambda: self.session_strip.tool_requested.emit("song_tools"),
            ),
        )

    def _setup_tab_order(self) -> None:
        """Keep keyboard traversal aligned with the visible workflow."""
        strip = self.session_strip
        order = [
            strip._title_input,
            strip._reference_track_button,
            strip._shared_track_transport,
            strip._shared_track_stop,
            self.session_hud._input,
            self.session_hud._secondary_action,
            self.session_hud._action,
            self.participant_grid,
            self.participant_grid._empty_primary,
            self.participant_grid._empty_practice,
            self.participant_grid._empty_ready,
        ]
        for card in self.participant_grid.cards():
            order.extend([card._fader, card._mute_button, card._solo_button])
        order.extend(
            [
                self.webex_embed.bring_forward_button(),
                self.webex_embed.mute_button(),
                self.webex_embed.fallback_button(),
                self.webex_embed.change_link_button(),
                self.webex_embed.install_button(),
                self.webex_embed.recheck_button(),
                strip._invite_button,
                strip._record_button,
                strip._video_button,
                # Song sits where it sits on the bar, so tabbing matches what
                # a musician sees rather than the order things were built in.
                strip._song_button,
                strip._studio_button,
                self._room_help_button,
                strip._tools_button,
                strip._audio_button,
            ]
        )
        for current, following in itertools.pairwise(order):
            QWidget.setTabOrder(current, following)
        QWidget.setTabOrder(order[-1], order[0])

    def show_reference_studio_only(self) -> None:
        """Present the offline song workspace without live-session chrome."""

        from webjam_qt import __version__

        self._reference_studio_only = True
        self.setWindowTitle(f"WebJam — Reference Studio (v{__version__})")
        self.session_strip.hide()
        self.session_hud.hide()
        self.session_controls.hide()
        self.side_rail.hide()
        self._status_bar.setVisible(
            self._status_bar.currentMessage() == self.OFFLINE_INVITATION_GUIDANCE
        )
        self._title_shortcut.setEnabled(False)
        self._ready_check_shortcut.setEnabled(False)
        for shortcut in self._navigation_shortcuts:
            shortcut.setEnabled(False)
        self.workspace_stack.setCurrentWidget(self.reference_studio)
        self.reference_studio.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_offline_invitation_guidance(self) -> None:
        """Persist the safe next step when an invite arrives in offline Studio."""

        self.flash_message(self.OFFLINE_INVITATION_GUIDANCE, ms=0)
        self._status_bar.setAccessibleName("Offline invitation guidance")
        self._status_bar.setAccessibleDescription(self.OFFLINE_INVITATION_GUIDANCE)

    def show_help(self) -> None:
        """Display the same short workflow the live screen presents."""
        import sys

        from PySide6.QtWidgets import QMessageBox

        from webjam_qt import __version__

        if sys.platform == "darwin":
            navigation_shortcuts = "⌘1 / ⌘2 / ⌘3"
            song_shortcut = "⌘4"
            mix_shortcuts = "⌘S / ⌘O"
            reset_shortcut = "Control+Shift+R"
        else:
            navigation_shortcuts = "Ctrl+1 / Ctrl+2 / Ctrl+3"
            song_shortcut = "Ctrl+4"
            mix_shortcuts = "Ctrl+S / Ctrl+O"
            reset_shortcut = "Ctrl+Shift+R"
        profile = self._creator_profile
        # Song exists only in Music, so only Music advertises its shortcut.
        navigation_line = (
            f"{navigation_shortcuts} / {song_shortcut} — "
            "Live / Notes / Studio / Song"
            if profile.key == "music"
            else f"{navigation_shortcuts} — Live / Notes / Studio"
        )
        if self._reference_studio_only and profile.key == "podcast_voice":
            body = (
                f"<b>WebJam v{__version__} — Podcast & Voice Studio</b><br>"
                "<i>Record and edit an episode or voice project offline.</i><br><br>"
                "<b>1.</b> Choose <b>Record Voice</b>, <b>New Local Recording</b>, "
                "or <b>Open Project</b>.<br>"
                "<b>2.</b> Import reference audio you own or may use.<br>"
                "<b>3.</b> Add voice tracks, map microphone inputs, and arrange "
                "chapters.<br>"
                "<b>4.</b> Save the project, then use <b>Bounce</b> to export "
                "the episode or recording.<br><br>"
                "Podcast & Voice Studio is independent of Jamulus live-audio "
                f"settings. {STUDIO_MEETING_CAPTURE_NOTICE}<br><br>"
                "F11 / Esc — Enter / leave full screen"
            )
        elif self._reference_studio_only and profile.key == "review_rehearsal":
            body = (
                f"<b>WebJam v{__version__} — Review & Rehearsal Preview</b><br>"
                "<i>Standalone projects are unavailable in this Preview.</i><br><br>"
                "Use Host Review or Join Review for live WebJam audio, local notes, "
                "and playback-only review of completed session takes. Notes stay "
                "local; visual media and media timecode are not synchronized. "
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            )
        elif self._reference_studio_only:
            body = (
                f"<b>WebJam v{__version__} — Reference Studio</b><br>"
                "<i>Build and rehearse a song offline.</i><br><br>"
                "<b>1.</b> Choose <b>Play Along / Record</b>, "
                "<b>New Project</b>, or <b>Open Project</b>.<br>"
                "<b>2.</b> Import a backing track you own or may use.<br>"
                "<b>3.</b> Add tracks, map recording inputs, and arrange regions.<br>"
                "<b>4.</b> Save the project, then use <b>Bounce</b> to export "
                "your demo.<br><br>"
                "Reference Studio audio is separate from Jamulus live audio "
                "and settings.<br><br>"
                "F11 / Esc — Enter / leave full screen"
            )
        elif profile.key == "podcast_voice":
            body = (
                f"<b>WebJam v{__version__} — Podcast & Voice</b><br>"
                "<i>Host. Invite. Record. Review.</i><br><br>"
                "<b>1.</b> Choose <b>Host Remote Recording</b> or "
                "<b>Join Recording</b>.<br>"
                "<b>2.</b> The host presses <b>Copy Invite</b> and sends the link.<br>"
                "<b>3.</b> Speak. Each speaker tile shows real connection and level truth.<br>"
                "<b>4.</b> The host presses <b>Record Session</b> for synchronized "
                f"WebJam tracks. {RECORD_SESSION_MEETING_CAPTURE_NOTICE}<br>"
                "<b>5.</b> Choose <b>Studio</b> to review, edit, and export the episode.<br>"
                "<b>6.</b> Choose <b>Conversation</b> for an optional external "
                "meeting handoff. Native verification and app focus remain "
                "Webex-only.<br>"
                "<b>7.</b> Use <b>Shared Track</b> for reference audio after its "
                "isolated Jamulus route is proven.<br>"
                "<b>8.</b> Press <b>End Session</b> when recording is finished.<br><br>"
                "<b>Useful shortcuts</b><br>"
                "F2 — Sound Check<br>"
                f"{navigation_line}<br>"
                f"{mix_shortcuts} — Save / load your monitor mix while Live is open<br>"
                f"{reset_shortcut} — Reset every fader to 0 dB<br>"
                "F11 / Esc — Enter / leave full screen"
            )
        elif profile.key == "review_rehearsal":
            body = (
                f"<b>WebJam v{__version__} — Review & Rehearsal Preview</b><br>"
                "<i>Talk. Record WebJam audio. Capture local decisions.</i><br><br>"
                "<b>1.</b> Choose <b>Host Review</b> or <b>Join Review</b>.<br>"
                "<b>2.</b> The host presses <b>Copy Invite</b> and sends the link.<br>"
                "<b>3.</b> Each participant tile shows real WebJam-audio truth.<br>"
                "<b>4.</b> The host may press <b>Record Session</b>. "
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}<br>"
                "<b>5.</b> Choose <b>Studio</b> for playback-only take review. "
                "Editing and track export are unavailable in this Preview.<br>"
                "<b>6.</b> <b>Notes</b> stay private to this computer; they are "
                "not shared or media-timecode synchronized.<br>"
                "<b>7.</b> <b>Conversation</b> hands an optional public HTTPS "
                "meeting link to its service without claiming join or mute.<br>"
                "<b>8.</b> Press <b>End Session</b> when the review is over.<br><br>"
                "<b>Useful shortcuts</b><br>"
                "F2 — Session Check (Preview)<br>"
                f"{navigation_line}<br>"
                "F11 / Esc — Enter / leave full screen"
            )
        else:
            body = (
                f"<b>WebJam v{__version__}</b><br>"
                "<i>Host. Share. Join. Play.</i><br><br>"
                "<b>1.</b> Choose <b>Host a Jam</b> or <b>Join a Jam</b>.<br>"
                "<b>2.</b> The host presses <b>Copy Invite</b> and sends the link.<br>"
                "<b>3.</b> Play. Each musician tile shows real connection and level truth.<br>"
                "<b>4.</b> The host presses <b>Record Session</b> for synchronized tracks.<br>"
                "<b>5.</b> Choose <b>Studio</b> to build a song project or "
                "review completed session takes.<br>"
                "<b>6.</b> Choose <b>Conversation</b> to show meeting controls. "
                "<b>Show Webex App</b> brings the verified application forward "
                "without reopening a meeting link; Webex chooses which of its "
                "windows is shown. Only "
                "<b>Join / Open Meeting</b> opens the saved meeting link.<br>"
                "<b>7.</b> The host can choose <b>Shared Track</b> to load "
                "and inspect a song. If Play is not ready, the strip says "
                "<b>Set up the audio device</b> and opens Shared Track for "
                "<b>Set Up Shared Track…</b> and <b>Recheck Route</b>.<br>"
                "<b>8.</b> Press <b>End Session</b> when the jam is over.<br><br>"
                "<b>Useful shortcuts</b><br>"
                "F2 — Band Check<br>"
                f"{navigation_line}<br>"
                f"{mix_shortcuts} — Save / load your monitor mix while Live is open<br>"
                f"{reset_shortcut} — Reset every fader to 0 dB<br>"
                "F11 / Esc — Enter / leave full screen"
            )
        box = QMessageBox()
        box.setWindowTitle("WebJam Help")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        from webjam_qt.theme.brand import render_brand_pixmap

        box.setIconPixmap(render_brand_pixmap(64))
        self._exec_message_box_on_screen(box)

    def show_about(self) -> None:
        """Show privacy-safe package identity and the candidate trust boundary."""

        from PySide6.QtWidgets import QMessageBox

        from core.build_info import build_id, desktop_target
        from webjam_qt import __version__

        commit = build_id()
        short_build = commit[:12] if commit else "unavailable"
        target = desktop_target() or "unknown target"
        if target.startswith("macos-"):
            trust_detail = (
                "This macOS test build is ad-hoc signed and is not Apple-notarized."
            )
        elif target == "windows-x64":
            trust_detail = (
                "This Windows test build is unsigned and is for private testing only."
            )
        elif target == "linux-x64":
            trust_detail = (
                "This Linux build is an unsigned portable private test candidate."
            )
        else:
            trust_detail = (
                "This build is an untrusted private test candidate; verify its "
                "package identity before opening it."
            )
        body = (
            f"<b>WebJam v{__version__}</b><br>"
            "Live audio and multitrack collaboration for creators.<br><br>"
            f"<b>Build:</b> {short_build}<br>"
            f"<b>Target:</b> {target}<br>"
            "<b>Trust:</b> Private test candidate<br><br>"
            f"{trust_detail}"
        )
        box = QMessageBox()
        box.setWindowTitle("About WebJam")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        if commit:
            box.setDetailedText(f"Full build ID: {commit}")
        from webjam_qt.theme.brand import render_brand_pixmap

        box.setIconPixmap(render_brand_pixmap(64))
        self._exec_message_box_on_screen(box)

    def _exec_message_box_on_screen(self, box) -> int:
        """Execute Help/About visibly even if the main window is off-screen."""

        from webjam_qt.controllers.window_layout import centered_window_rect

        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.ensurePolished()
        box.adjustSize()

        # Prefer the display that really contains WebJam. A stale geometry
        # from a disconnected monitor has no screenAt() result, so fall back
        # to the current primary display rather than attaching a Cocoa sheet
        # to an unreachable parent.
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        def place_and_foreground() -> None:
            if screen is not None:
                target = centered_window_rect(
                    screen.availableGeometry(),
                    box.sizeHint().expandedTo(box.minimumSizeHint()),
                )
                if not target.isEmpty():
                    box.resize(target.size())
                    box.move(target.topLeft())
            box.raise_()
            box.activateWindow()

        place_and_foreground()
        # Cocoa may finish native window sizing only when exec() starts. Clamp
        # once more on its first event-loop turn, then bring it forward.
        QTimer.singleShot(0, place_and_foreground)
        return int(box.exec())

    def fit_to_screen(self):
        """Snap WebJam onto its share of the usable screen.

        Returns the :class:`SessionLayout` that was applied, or ``None`` when
        there is no screen to fit (headless hosts) or the display is too
        small to tile.  The Webex rectangle in the returned layout is where
        the meeting window belongs; placing it needs macOS Accessibility and
        is wired separately.

        The frame -- not the client area -- is what lands on the target, so
        the title bar does not push the window off the bottom of the screen.
        """

        from webjam_qt.controllers.window_layout import split_screen

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return None
        layout = split_screen(screen.availableGeometry())
        target = layout.webjam
        if target.isEmpty():
            return None

        # Convert the frame-space target into the client-space geometry Qt
        # sets.  On a window that has never been shown these are equal, which
        # simply makes the correction a no-op.
        frame = self.frameGeometry()
        inner = self.geometry()
        self.setGeometry(
            target.x() + (inner.x() - frame.x()),
            target.y() + (inner.y() - frame.y()),
            max(target.width() - (frame.width() - inner.width()), self.minimumWidth()),
            max(
                target.height() - (frame.height() - inner.height()),
                self.minimumHeight(),
            ),
        )
        return layout

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    def _handle_escape(self) -> None:
        panel = self._paint_along_widget
        if panel is not None and self.workspace_stack.currentWidget() is panel:
            self.hide_paint_along(panel)
            return
        self._exit_fullscreen()

    # ------------------------------------------------------------------
    # Public helpers for ApplicationController
    # ------------------------------------------------------------------
    def show_paint_along(self, panel: QWidget) -> None:
        """Use WebJam's existing window as the Paint along surface.

        The meeting already owns the other top-level window. Embedding this
        panel keeps the promised two-window layout and makes the video the
        WebJam workspace instead of opening a third preview beside it.
        """

        if panel is not self._paint_along_widget:
            previous = self._paint_along_widget
            if previous is not None and self.workspace_stack.indexOf(previous) >= 0:
                self.workspace_stack.removeWidget(previous)
            panel.setParent(self.workspace_stack)
            panel.setWindowFlags(Qt.WindowType.Widget)
            self.workspace_stack.addWidget(panel)
            self._paint_along_widget = panel
        current = self.workspace_stack.currentWidget()
        if current is not panel:
            self._paint_along_return_widget = current or self.center_splitter
        set_embedded = getattr(panel, "set_embedded", None)
        if callable(set_embedded):
            set_embedded(True)
        self.workspace_stack.setCurrentWidget(panel)
        panel.show()

    def hide_paint_along(self, panel: QWidget | None = None) -> None:
        """Return from the embedded Paint along surface to the prior workspace."""

        active = self._paint_along_widget
        if active is None or (panel is not None and panel is not active):
            return
        if self.workspace_stack.currentWidget() is active:
            target = self._paint_along_return_widget
            if self.workspace_stack.indexOf(target) < 0:
                target = self.center_splitter
            self.workspace_stack.setCurrentWidget(target)
        active.hide()

    def release_paint_along(self, panel: QWidget | None = None) -> None:
        """Forget an embedded surface before its deferred Qt deletion."""

        active = self._paint_along_widget
        if active is None or (panel is not None and panel is not active):
            return
        self.hide_paint_along(active)
        if self.workspace_stack.indexOf(active) >= 0:
            self.workspace_stack.removeWidget(active)
        self._paint_along_widget = None
        self._paint_along_return_widget = self.center_splitter

    def set_creator_profile(
        self,
        profile: CreatorProfile,
        *,
        locked: bool = False,
    ) -> None:
        """Apply one truthful creator presentation across the shared shell."""

        if not isinstance(profile, CreatorProfile):
            raise TypeError("profile must be a CreatorProfile")
        from webjam_qt import __version__

        self._creator_profile = profile
        suffix = " · Preview" if profile.is_preview else ""
        self.setWindowTitle(f"WebJam — {profile.label}{suffix} (v{__version__})")
        self.setAccessibleName(f"WebJam {profile.label} workspace{suffix}")
        self.session_strip.set_creator_profile(profile, locked=locked)
        if getattr(self, "song_overlay", None) is not None:
            # A host can impose a profile mid-session. Song has no meaning
            # outside Music, so the panel leaves with it rather than sitting
            # open showing another maker a song they do not have.
            if profile.key == "music":
                self.song_overlay.set_creator_profile(profile)
            else:
                self.song_overlay.setVisible(False)
        self.participant_grid.set_creator_profile(profile)
        self.recording_studio.set_creator_profile(profile)
        self.session_canvas.set_creator_profile(profile)
        self.webex_embed.set_creator_profile(profile)
        if profile.key != "art":
            self.session_canvas.setAccessibleDescription(
                "Local notes and separate live chat. Notes are not shared or "
                "media timecode synchronized."
            )
        if profile.key == "podcast_voice":
            recording_tip = (
                "The Jamulus server is recording this session — every speaker "
                "gets a synchronized WebJam track. "
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            )
        elif profile.key == "review_rehearsal":
            recording_tip = (
                "The Jamulus server is recording WebJam audio — every participant "
                f"gets a synchronized track. {RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            )
        elif profile.key == "art":
            recording_tip = "This room is not recorded."
        else:
            recording_tip = (
                "The Jamulus server is recording this session — every musician "
                "gets their own track."
            )
        self._status_recording.setToolTip(recording_tip)
        self._status_recording.setAccessibleDescription(recording_tip)

    def set_status_recording(self, active: bool) -> None:
        """Show/hide the red ● REC chip in the status bar."""
        self._status_recording.setVisible(bool(active))
        if active:
            self._status_bar.setVisible(True)
        elif not self._status_bar.currentMessage():
            self._status_bar.setVisible(False)

    def set_status_server(self, text: str) -> None:
        """Retain hosted-server text for diagnostics, not the live surface."""
        self._status_server.setText(f"Band: {text}" if text else "Band: —")
        # SessionHud already owns this truth. Keeping the legacy label hidden
        # avoids duplicate chrome and, because it is parented, can never turn
        # into a stray top-level macOS window.
        self._status_server.setVisible(False)

    def set_status_audio(self, text: str) -> None:
        self._status_audio.setText(f"Audio: {text}")

    def set_status_video(self, text: str) -> None:
        self._status_video.setText(f"Video: {text}")

    def set_status_latency(self, text: str) -> None:
        self._status_latency.setText(f"Session: {text}")

    def set_status_routing(self, text: str) -> None:
        # Routing is automatic and intentionally absent from the musician UI.
        self._status_routing.setText(str(text or ""))

    def flash_message(
        self, text: str, *, ms: int = 4000, color: str | None = None
    ) -> None:
        """Show a temporary status-bar message, optionally tinted.

        ``color`` accepts any Qt stylesheet color value.
        highlights attention-worthy banners (reconnect warnings, etc.). The
        tint is cleared automatically once the message times out or is
        replaced — see ``_on_status_message_changed``.
        """
        self._status_bar.setStyleSheet(
            f"QStatusBar{{color: {color};}}" if color else ""
        )
        self._status_bar.setVisible(True)
        self._status_bar.showMessage(text, ms)

    def _on_status_message_changed(self, text: str) -> None:
        """Clear any flash_message() color tint once its message clears."""
        if not text:
            self._status_bar.setStyleSheet("")
            if not self._status_recording.isVisible():
                self._status_bar.setVisible(False)

    def resizeEvent(self, event) -> None:
        """Keep every bottom-bar action readable on compact live windows."""

        super().resizeEvent(event)
        if hasattr(self, "session_strip"):
            self.session_strip.set_compact_control_labels(self.width() < 900)
        if hasattr(self, "session_controls"):
            self._sync_room_help_density()
        if getattr(self, "song_overlay", None) is not None:
            self.song_overlay.set_available_width(self.width())

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Let a host drop one Shared Track anywhere on the live window."""

        self.session_strip.dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self.session_strip.dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.session_strip.dropEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.confirm_close is not None and not self.confirm_close():
            event.ignore()
            return
        if self.finalize_close is not None and not self.finalize_close():
            event.ignore()
            return
        self.close_requested.emit()
        super().closeEvent(event)

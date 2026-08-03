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

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme import Color
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets import (
    ParticipantGrid,
    SessionCanvas,
    SessionStrip,
    SideRail,
    WebexEmbed,
    RecordingStudio,
    ReferenceStudioShell,
    SessionHud,
)


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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        from webjam_qt import __version__

        self.setWindowTitle(f"WebJam — Band Session (v{__version__})")
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        # Reserve 40 px around the client area for native title-bar/frame
        # chrome so the complete meeting surface fits a physical 760×600
        # display. Child layouts own adaptation below the generous default.
        self.setMinimumSize(720, 560)
        # Controller-injected veto (e.g. "a recording is running — quit?").
        self.confirm_close: Optional[Callable[[], bool]] = None
        # A second synchronous gate owns teardown that can still fail after
        # the musician confirms closing (for example, an unsaved Studio
        # document or an unproved Reference Track process stop).  A Qt signal
        # cannot return that result, so closeEvent must call this callback
        # directly before accepting the native close.
        self.finalize_close: Optional[Callable[[], bool]] = None
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
        self.recording_studio = RecordingStudio()
        self.reference_studio = ReferenceStudioShell(self.recording_studio)
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
        controls_layout.addWidget(self.session_strip._studio_button)
        self.session_strip._tools_button.setText("More ▾")
        self.session_strip._tools_button.setAccessibleName("More session options")
        controls_layout.addWidget(self.session_strip._tools_button)
        controls_layout.addSpacing(Space.MD)
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

        body_container = QWidget()
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(self.workspace_stack, stretch=1)

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
        self.webex_embed.setAccessibleName("Webex external launch and audio role")
        self.session_canvas.setAccessibleName("Session notes canvas")
        self.recording_studio.setAccessibleName("Multitrack recording studio")

        # --- Keyboard shortcuts
        self._setup_shortcuts()
        self.participant_grid.participants_changed.connect(self._setup_tab_order)
        self._setup_tab_order()

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
        # Escape — exit fullscreen
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen)
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
        )

    def _setup_tab_order(self) -> None:
        """Keep keyboard traversal aligned with the visible workflow."""
        strip = self.session_strip
        order = [
            strip._title_input,
            strip._reference_track_button,
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
                strip._studio_button,
                strip._tools_button,
                strip._audio_button,
            ]
        )
        for current, following in zip(order, order[1:]):
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
            mix_shortcuts = "⌘S / ⌘O"
            reset_shortcut = "Control+Shift+R"
        else:
            navigation_shortcuts = "Ctrl+1 / Ctrl+2 / Ctrl+3"
            mix_shortcuts = "Ctrl+S / Ctrl+O"
            reset_shortcut = "Ctrl+Shift+R"
        if self._reference_studio_only:
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
        else:
            body = (
                f"<b>WebJam v{__version__}</b><br>"
                "<i>Host. Share. Join. Play.</i><br><br>"
                "<b>1.</b> Choose <b>Host a Jam</b> or <b>Join a Jam</b>.<br>"
                "<b>2.</b> The host presses <b>Copy Invite</b> and sends the link.<br>"
                "<b>3.</b> Play. Each musician tile shows real connection and level truth.<br>"
                "<b>4.</b> The host presses <b>Record</b> for synchronized tracks.<br>"
                "<b>5.</b> Choose <b>Studio</b> to build a song project or "
                "review completed session takes.<br>"
                "<b>6.</b> Choose <b>Webex Controls</b> to show Conversation. "
                "<b>Show Webex App</b> brings the verified application forward "
                "without reopening a meeting link; Webex chooses which of its "
                "windows is shown. Only "
                "<b>Join / Open Meeting</b> opens the saved meeting link.<br>"
                "<b>7.</b> The host can choose <b>Reference Track</b> to load "
                "and inspect a song; Play stays locked until its isolated "
                "Jamulus route is proven.<br>"
                "<b>8.</b> Press <b>End Session</b> when the jam is over.<br><br>"
                "<b>Useful shortcuts</b><br>"
                "F2 — Band Check<br>"
                f"{navigation_shortcuts} — Live / Notes / Studio<br>"
                f"{mix_shortcuts} — Save / load your monitor mix while Live is open<br>"
                f"{reset_shortcut} — Reset every fader to 0 dB<br>"
                "F11 / Esc — Enter / leave full screen"
            )
        box = QMessageBox(self)
        box.setWindowTitle("WebJam Help")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        from webjam_qt.theme.brand import render_brand_pixmap

        box.setIconPixmap(render_brand_pixmap(64))
        box.exec()

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
            "Unified creative collaboration for musicians.<br><br>"
            f"<b>Build:</b> {short_build}<br>"
            f"<b>Target:</b> {target}<br>"
            "<b>Trust:</b> Private test candidate<br><br>"
            f"{trust_detail}"
        )
        box = QMessageBox(self)
        box.setWindowTitle("About WebJam")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        if commit:
            box.setDetailedText(f"Full build ID: {commit}")
        from webjam_qt.theme.brand import render_brand_pixmap

        box.setIconPixmap(render_brand_pixmap(64))
        box.exec()

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

    # ------------------------------------------------------------------
    # Public helpers for ApplicationController
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_close is not None and not self.confirm_close():
            event.ignore()
            return
        if self.finalize_close is not None and not self.finalize_close():
            event.ignore()
            return
        self.close_requested.emit()
        super().closeEvent(event)

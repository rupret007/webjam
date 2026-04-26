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

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.widgets import (
    ParticipantGrid,
    SessionCanvas,
    SessionStrip,
    SideRail,
    WebexEmbed,
)


class ConductorWindow(QMainWindow):
    close_requested = Signal()

    DEFAULT_WIDTH = 1440
    DEFAULT_HEIGHT = 900

    def __init__(
        self,
        *,
        mode_entries: list[tuple[str, str]],
        initial_mode_key: str,
        initial_title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        from webjam_qt import __version__
        self.setWindowTitle(f"WebJam — Conductor (v{__version__})")
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setMinimumSize(1100, 720)

        # --- Central widgets
        self.session_strip = SessionStrip(
            mode_entries=mode_entries,
            initial_mode_key=initial_mode_key,
            initial_title=initial_title,
        )
        self.side_rail = SideRail()
        self.participant_grid = ParticipantGrid()
        self.webex_embed = WebexEmbed()
        self.session_canvas = SessionCanvas()

        # Stage combines participant grid + webex embed vertically
        stage_container = QWidget()
        stage_layout = QVBoxLayout(stage_container)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        stage_header = QLabel("STAGE")
        stage_header.setObjectName("StageHeader")
        stage_layout.addWidget(stage_header)
        stage_layout.addWidget(self.participant_grid, stretch=3)
        stage_layout.addWidget(self.webex_embed, stretch=2)

        self.center_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.center_splitter.addWidget(stage_container)
        self.center_splitter.addWidget(self.session_canvas)
        self.center_splitter.setStretchFactor(0, 3)
        self.center_splitter.setStretchFactor(1, 1)
        self.center_splitter.setSizes([int(self.DEFAULT_WIDTH * 0.72), int(self.DEFAULT_WIDTH * 0.28)])
        self.center_splitter.setCollapsible(0, True)
        self.center_splitter.setCollapsible(1, True)
        self.center_splitter.setHandleWidth(1)

        body_container = QWidget()
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(self.center_splitter, stretch=1)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.session_strip)
        central_layout.addWidget(body_container, stretch=1)

        self.setCentralWidget(central)

        # --- Status bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        self._status_audio   = QLabel("Audio: —")
        self._status_video   = QLabel("Video: —")
        self._status_latency = QLabel("Session: —")
        self._status_routing = QLabel("Routing: checking…")
        self._status_bar.addPermanentWidget(self._status_audio)
        self._status_bar.addPermanentWidget(self._status_video)
        self._status_bar.addPermanentWidget(self._status_latency)
        self._status_bar.addPermanentWidget(self._status_routing)
        self._status_bar.showMessage("Ready")

        # --- Accessibility names
        self.session_strip.setAccessibleName("Session controls strip")
        self.side_rail.setAccessibleName("Navigation rail")
        self.participant_grid.setAccessibleName("Participant mixer grid")
        self.webex_embed.setAccessibleName("Webex video conference pane")
        self.session_canvas.setAccessibleName("Session notes canvas")

        # --- Keyboard shortcuts
        self._setup_shortcuts()

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

        # Cmd/Ctrl+L — focus session title
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.session_strip.focus_title())
        # F11 — fullscreen toggle
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen)
        # Escape — exit fullscreen
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen)
        # Cmd/Ctrl+, — open settings wizard (signal consumed by controller)
        self._settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        # Cmd/Ctrl+S — save mix; Cmd/Ctrl+O — load mix (consumed by controller)
        self._save_mix_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._load_mix_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        # Cmd/Ctrl+Shift+S — "Save Mix As..."; Cmd/Ctrl+Shift+O — "Load Mix..."
        # Multi-slot mix support: pick an arbitrary file path so users can
        # keep one mix per song / per band-mate setup.  Uses the macOS-safe
        # binder so Cmd+Shift+S doesn't collide with system shortcuts.
        if on_mac:
            self._save_mix_as_shortcut = QShortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_S.value
                ),
                self,
            )
            self._load_mix_from_shortcut = QShortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_O.value
                ),
                self,
            )
        else:
            self._save_mix_as_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
            self._load_mix_from_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
        # Cmd/Ctrl+T — insert timestamp into session canvas
        QShortcut(
            QKeySequence("Ctrl+T"), self,
            lambda: self.session_canvas.insert_timestamp(),
        )
        # Mute-all and mute-self use the macOS-safe binder so they don't
        # collide with system minimize (Cmd+M).
        self._mute_all_shortcut = QShortcut(_ctrl("M"), self)
        if on_mac:
            # Ctrl+Shift+M with literal Control key on macOS
            self._mute_self_shortcut = QShortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_M.value
                ),
                self,
            )
        else:
            self._mute_self_shortcut = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
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
            self._reset_faders_shortcut = QShortcut(
                QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.KeyboardModifier.ShiftModifier.value
                    | Qt.Key.Key_R.value
                ),
                self,
            )
        else:
            self._reset_faders_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        # F1 — show help dialog
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, self._show_help)

    def _show_help(self) -> None:
        """Display a keyboard-shortcut and getting-started reference."""
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt import __version__
        import sys
        # On macOS, our mute shortcuts use the literal Control key (not Cmd)
        # to avoid clashing with Cmd+M = system minimize.  Other platforms
        # use the standard Ctrl+M / Ctrl+Shift+M bindings.
        on_mac = sys.platform == "darwin"
        mute_all_label = "⌃M (literal Control, not Cmd)" if on_mac else "Ctrl+M"
        mute_self_label = "⌃⇧M (literal Control)" if on_mac else "Ctrl+Shift+M"
        body = (
            f"<b>WebJam — Conductor UI</b> &nbsp;<i>v{__version__}</i><br>"
            "<i>One window for band audio (Jamulus) + video (Webex).</i><br><br>"
            "<b>Keyboard shortcuts:</b><br>"
            "&nbsp;&nbsp;<b>Ctrl+L</b> — Focus session title<br>"
            "&nbsp;&nbsp;<b>Ctrl+S</b> — Save mixer state (default slot)<br>"
            "&nbsp;&nbsp;<b>Ctrl+O</b> — Load mixer state (default slot)<br>"
            "&nbsp;&nbsp;<b>Ctrl+Shift+S</b> — Save Mix As... (named file)<br>"
            "&nbsp;&nbsp;<b>Ctrl+Shift+O</b> — Load Mix... (pick a file)<br>"
            f"&nbsp;&nbsp;<b>{mute_all_label}</b> — Mute / unmute all<br>"
            f"&nbsp;&nbsp;<b>{mute_self_label}</b> — Mute / unmute yourself<br>"
            "&nbsp;&nbsp;<b>Ctrl+T</b> — Insert timestamp in canvas<br>"
            "&nbsp;&nbsp;<b>Ctrl+Shift+R</b> — Reset all faders to 0 dB<br>"
            "&nbsp;&nbsp;<b>Ctrl+Shift+D</b> — Copy diagnostics to clipboard<br>"
            "&nbsp;&nbsp;<b>Ctrl+,</b> — Open Settings<br>"
            "&nbsp;&nbsp;<b>F11</b> — Toggle fullscreen<br>"
            "&nbsp;&nbsp;<b>Esc</b> — Exit fullscreen<br>"
            "&nbsp;&nbsp;<b>F1</b> — Show this help<br>"
            "&nbsp;&nbsp;<b>Double-click fader</b> — Reset to 0 dB<br><br>"
            "<b>Getting started:</b><br>"
            "1. Click <b>Launch Audio</b> (gold button) to start Jamulus.<br>"
            "2. Click <b>Join Video</b> (teal button) to open Webex.<br>"
            "3. Adjust faders as your band joins.<br>"
            "4. Click the same buttons again to stop / leave.<br><br>"
            "<b>Troubleshooting — log files:</b><br>"
            "&nbsp;&nbsp;~/.webjam.log — WebJam diagnostics<br>"
            "&nbsp;&nbsp;~/.webjam_jamulus.log — Jamulus stdout/stderr<br><br>"
            "<a href='https://github.com/rupret007/webjam'>github.com/rupret007/webjam</a>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("WebJam Help")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()

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
    def set_status_audio(self, text: str) -> None:
        self._status_audio.setText(f"Audio: {text}")

    def set_status_video(self, text: str) -> None:
        self._status_video.setText(f"Video: {text}")

    def set_status_latency(self, text: str) -> None:
        self._status_latency.setText(f"Session: {text}")

    def set_status_routing(self, text: str) -> None:
        self._status_routing.setText(f"Routing: {text}")

    def flash_message(self, text: str, *, ms: int = 4000) -> None:
        self._status_bar.showMessage(text, ms)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.close_requested.emit()
        super().closeEvent(event)

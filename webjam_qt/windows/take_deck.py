"""
TakeDeck — review and mix a recorded take (the Demo Deck, Level 1).

A non-modal dialog that lists the takes captured by the Record button and
plays any of them back with a full mixer — reusing the very same
ParticipantGrid console the live session uses, except the "participants"
here are the recorded tracks (Dave's bass, your guitar). Transport bar on
top; track console below.

Deliberately review-only: play, scrub, and dial a rough mix. Editing lives
in the DAW (the take keeps its Reaper-project escape hatch).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_output_devices
from core.take_library import TakeValidationResult, discover_takes, validate_take
from core.take_player import PlaybackError, SoundDeviceSink, TakePlayer
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.widgets.participant_grid import ParticipantGrid

LOGGER = logging.getLogger("webjam.qt.take_deck")


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class TakeDeck(QDialog):
    """Take library + playback console. Owns a TakePlayer for the current take."""

    closed = Signal()

    def __init__(self, takes_dir: str, parent: Optional[QWidget] = None,
                 player: Optional[TakePlayer] = None,
                 output_device_name: str = "",
                 on_output_device_changed: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.setObjectName("TakeDeck")
        self.setWindowTitle("WebJam — Take Deck")
        self.resize(880, 620)
        # Tear the C++ dialog down on close so reopening doesn't leak hidden
        # QDialogs (the controller builds a fresh one each open).
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._takes_dir = takes_dir
        self._takes = []
        self._current = None

        # Cross-thread state, initialised before anything can touch it.
        self._pending_levels: dict[int, float] = {}
        self._finished_flag = False

        # A caller can inject a player (tests use a headless sink); otherwise
        # the default player builds a real sounddevice sink lazily on play().
        # We always own the callbacks (single wiring site — works the same
        # for a default or an injected player).
        self._player_injected = player is not None
        self._output_device_name = str(output_device_name or "")
        self._output_fallback_message = ""
        self._on_output_device_changed = on_output_device_changed
        self._player = player or TakePlayer(
            sink=SoundDeviceSink(self._output_device_name)
        )
        self._player._on_position = None  # position is polled from the player
        self._player._on_levels = self._on_levels_bg
        self._player._on_finished = self._on_finished_bg

        self._build_ui()
        self.reload()

        # UI-thread poll for transport position/levels (audio callbacks fire
        # on the audio thread; we marshal via a timer to stay Qt-safe).
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(60)
        self._ui_timer.timeout.connect(self._tick_ui)
        self._ui_timer.start()

    # -- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # Left: take list
        left = QVBoxLayout()
        left.addWidget(QLabel("Takes"))
        self._take_list = QListWidget()
        self._take_list.setMinimumWidth(220)
        self._take_list.currentRowChanged.connect(self._on_take_selected)
        left.addWidget(self._take_list, stretch=1)
        self._reload_btn = QPushButton("Refresh")
        self._reload_btn.clicked.connect(self.reload)
        left.addWidget(self._reload_btn)
        self._reveal_btn = QPushButton("Reveal in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_current)
        left.addWidget(self._reveal_btn)
        root.addLayout(left)

        # Right: transport + console
        right = QVBoxLayout()

        self._title = QLabel("Select a take")
        self._title.setObjectName("TakeTitle")
        right.addWidget(self._title)

        transport = QHBoxLayout()
        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)
        transport.addWidget(self._play_btn)

        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        transport.addWidget(self._stop_btn)

        transport.addWidget(QLabel("Output:"))
        self._output_picker = QComboBox()
        self._output_picker.setAccessibleName("Take Deck playback output")
        self._populate_output_devices()
        self._output_picker.currentIndexChanged.connect(self._on_output_changed)
        transport.addWidget(self._output_picker)

        self._pos_label = QLabel("0:00 / 0:00")
        transport.addWidget(self._pos_label)

        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, 1000)
        self._scrub.setEnabled(False)
        self._scrub.sliderReleased.connect(self._on_scrub_released)
        self._scrub.sliderPressed.connect(self._on_scrub_pressed)
        self._scrubbing = False
        transport.addWidget(self._scrub, stretch=1)
        right.addLayout(transport)

        self._console = ParticipantGrid()
        self._console.set_empty_state(
            "no_take",
            "Select a take to review",
            "Recorded tracks and playback controls will appear here.",
            show_primary=False,
            show_ready_check=False,
        )
        self._console.fader_changed.connect(self._on_fader)
        self._console.mute_toggled.connect(self._on_mute)
        self._console.solo_toggled.connect(self._on_solo)
        right.addWidget(self._console, stretch=1)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setObjectName("TakeHint")
        if self._output_fallback_message:
            self._hint.setText(self._output_fallback_message)
        right.addWidget(self._hint)

        root.addLayout(right, stretch=1)

    # -- take library -----------------------------------------------------
    def reload(self) -> None:
        self._player.stop()
        self._takes = discover_takes(self._takes_dir) if self._takes_dir else []
        self._take_list.clear()
        if not self._takes:
            self._hint.setText(
                "No takes found yet. Press ● Record during a jam to capture "
                "one (needs the band-server recorder — see server/README.md). "
                f"Looking in: {self._takes_dir or '(no takes folder set)'}"
            )
            self._title.setText("Select a take")
            self._play_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._scrub.setEnabled(False)
            self._console.set_participants([])
            self._console.set_empty_state(
                "no_takes",
                "No recorded takes yet",
                "Record a short test from the Live workspace, then refresh this list.",
                show_primary=False,
                show_ready_check=False,
            )
            self._reveal_btn.setEnabled(False)
            return
        self._hint.setText("")
        for take in self._takes:
            status = {
                "complete": "Verified",
                "needs_attention": "Needs Attention",
                "validating": "Checking…",
            }.get(take.validation_status, "Unchecked")
            label = (
                f"{take.name}  ·  {status}  ·  {take.track_count} tracks"
                f"  ·  {_fmt_time(take.duration_s)}"
            )
            self._take_list.addItem(QListWidgetItem(label))
        self._take_list.setCurrentRow(0)

    def _on_take_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._takes):
            return
        take = self._takes[row]
        self._current = take
        self._player.load(take)
        self._console.set_participants([
            ParticipantPresentation(
                channel_id=t.channel_id,
                name=t.name,
                role=(
                    ("Isolated SSL input" if t.source == "local_ssl" else "Jamulus server track")
                    + (f" · starts {_fmt_time(t.offset_s)}" if t.offset_s > 0.5 else "")
                    + (f" · trimmed {abs(t.offset_s):.2f}s lead-in" if t.offset_s < -0.05 else "")
                ),
                fader_level=100,
            )
            for t in self._player.tracks
        ])
        self._play_btn.setEnabled(True)
        self._play_btn.setText("▶ Play")
        self._stop_btn.setEnabled(True)
        self._scrub.setEnabled(True)
        self._scrub.setValue(0)
        self._update_pos_label(0.0, self._player.duration_s)
        self._reveal_btn.setEnabled(True)
        if take.validation_status in ("complete", "needs_attention") and take.manifest_path:
            # Findings were recorded at validation time; reuse them instead
            # of re-probing every WAV on the UI thread.
            self._show_take_health(take, TakeValidationResult(
                take, take.manifest_errors, take.manifest_warnings,
                take.manifest_path,
            ))
        else:
            # Legacy manifest-less take: probe it now. validate_take is
            # bounded (header reads + three 4096-frame windows per track),
            # so this stays interactive even for long takes.
            try:
                result = validate_take(take.path)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Take health check failed for %s", take.path)
                result = TakeValidationResult(
                    take, ("The take could not be checked — see ~/.webjam.log.",)
                )
            self._show_take_health(take, result)

    def _show_take_health(self, take, validation: TakeValidationResult) -> None:
        self._title.setText(f"{take.name}  ·  {validation.summary}")
        messages = list(validation.errors)
        messages.extend(f"Warning: {warning}" for warning in validation.warnings)
        self._hint.setText("\n".join(messages))

    # -- transport --------------------------------------------------------
    def _toggle_play(self) -> None:
        if self._player.is_playing:
            self._player.pause()
            self._play_btn.setText("▶ Play")
        else:
            try:
                self._player.play()
            except PlaybackError as exc:
                self._hint.setText(str(exc))
                self._play_btn.setText("▶ Play")
                return
            self._play_btn.setText("⏸ Pause")

    def _on_stop(self) -> None:
        self._player.stop()
        self._play_btn.setText("▶ Play")
        self._scrub.setValue(0)
        self._update_pos_label(0.0, self._player.duration_s)

    def _on_scrub_pressed(self) -> None:
        self._scrubbing = True

    def _on_scrub_released(self) -> None:
        self._scrubbing = False
        dur = self._player.duration_s
        if dur > 0:
            self._player.seek(self._scrub.value() / 1000.0 * dur)

    # -- console -> player -------------------------------------------------
    def _on_fader(self, channel_id: int, level: int) -> None:
        self._player.set_gain(channel_id, level / 100.0)

    def _on_mute(self, channel_id: int, muted: bool) -> None:
        self._player.set_muted(channel_id, muted)

    def _on_solo(self, channel_id: int, solo: bool) -> None:
        self._player.set_solo(channel_id, solo)

    # -- background callbacks (audio thread) ------------------------------
    def _on_levels_bg(self, levels: dict) -> None:
        # Rebind (atomic under the GIL); the UI thread swaps it out wholesale.
        self._pending_levels = dict(levels)

    def _on_finished_bg(self) -> None:
        self._finished_flag = True

    # -- UI thread poll ----------------------------------------------------
    def _tick_ui(self) -> None:
        # levels -> meters (atomic snapshot-and-clear to avoid a torn read)
        pending, self._pending_levels = self._pending_levels, {}
        for cid, lvl in pending.items():
            self._console.update_level(cid, lvl)
        self._console.tick_all_meters()

        dur = self._player.duration_s
        pos = self._player.position_s
        if not self._scrubbing and dur > 0:
            self._scrub.setValue(int(pos / dur * 1000))
        self._update_pos_label(pos, dur)

        if self._finished_flag:
            self._finished_flag = False
            # H3: the player finishes on the audio thread but must not stop
            # its own sink from inside the callback — do it here on the UI
            # thread so the stream + file handles are actually released.
            self._player.stop()
            self._scrub.setValue(0)
            self._update_pos_label(0.0, self._player.duration_s)
            self._play_btn.setText("▶ Play")

    def _update_pos_label(self, pos: float, dur: float) -> None:
        self._pos_label.setText(f"{_fmt_time(pos)} / {_fmt_time(dur)}")

    def _populate_output_devices(self) -> None:
        self._output_picker.blockSignals(True)
        self._output_picker.addItem("System default", "")
        for device in list_output_devices():
            self._output_picker.addItem(
                f"{device['name']} ({device['channels']} ch)", device["name"]
            )
        target = self._output_picker.findData(self._output_device_name)
        self._output_picker.setCurrentIndex(target if target >= 0 else 0)
        if self._output_device_name and target < 0:
            self._output_fallback_message = (
                f"Saved output '{self._output_device_name}' is unavailable; "
                "using the system default."
            )
        self._output_picker.blockSignals(False)

    def _on_output_changed(self, _index: int) -> None:
        name = str(self._output_picker.currentData() or "")
        self._output_device_name = name
        if self._on_output_device_changed is not None:
            self._on_output_device_changed(name)
        if self._player_injected:
            return
        current = self._current
        self._player.stop()
        self._play_btn.setText("▶ Play")
        self._scrub.setValue(0)
        self._player = TakePlayer(sink=SoundDeviceSink(name))
        self._player._on_levels = self._on_levels_bg
        self._player._on_finished = self._on_finished_bg
        if current is not None:
            self._player.load(current)
            self._update_pos_label(0.0, self._player.duration_s)

    def _reveal_current(self) -> None:
        if self._current is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current.path)))

    # -- lifecycle --------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._ui_timer.stop()
            self._player.stop()
        finally:
            self.closed.emit()
            super().closeEvent(event)

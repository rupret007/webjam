from __future__ import annotations

import os
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.windows.reference_track import ReferenceTrackDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _State(str, Enum):
    UNAVAILABLE = "unavailable"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"


def _snapshot(
    state: _State,
    *,
    available: bool = True,
    error: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        capability=SimpleNamespace(
            available=available,
            detail=(
                "BlackHole is ready."
                if available
                else "Install and verify BlackHole before loading a song."
            ),
            route_name="BlackHole 16ch" if available else "",
        ),
        source_name="Rehearsal Reference.flac" if state is not _State.UNAVAILABLE else "",
        duration_s=120.0 if state is not _State.UNAVAILABLE else 0.0,
        position_s=30.0 if state is not _State.UNAVAILABLE else 0.0,
        loop_start_s=8.0,
        loop_end_s=24.0,
        trim_db=-3.0,
        count_in_beats=4,
        count_in_bpm=112.0,
        route_detail="Isolated backing route verified." if available else "",
        error=error,
        warning="",
    )


def test_reference_track_dialog_fits_supported_compact_screen() -> None:
    dialog = ReferenceTrackDialog()
    try:
        assert dialog.minimumWidth() <= 760
        # Leave room for the native macOS title bar inside a 600-pixel screen.
        assert dialog.minimumHeight() <= 540
        assert dialog.height() <= 540
        assert "Jamulus-routed" in dialog.findChildren(type(dialog._status))[-1].text()
    finally:
        dialog.close()


def test_styled_reference_track_controls_remain_reachable_at_compact_size() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(620, 540)
        dialog.show()
        _app.processEvents()

        assert dialog.width() <= 760
        assert dialog.height() <= 540
        assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog._scroll_area.verticalScrollBar().maximum() > 0

        dialog._scroll_area.ensureWidgetVisible(dialog._done)
        _app.processEvents()
        top_left = dialog._done.mapTo(
            dialog._scroll_area.viewport(),
            QPoint(0, 0),
        )
        assert top_left.y() >= 0
        assert top_left.y() + dialog._done.height() <= (
            dialog._scroll_area.viewport().height()
        )
    finally:
        dialog.close()


def test_unavailable_snapshot_is_truthful_and_fail_closed() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_snapshot(_snapshot(_State.UNAVAILABLE, available=False))

        assert "unavailable" in dialog._status.text().lower()
        assert "BlackHole" in dialog._route.text()
        assert dialog._load.isEnabled() is False
        assert dialog._play.isEnabled() is False
        assert dialog._pause.isEnabled() is False
        assert dialog._stop.isEnabled() is False
    finally:
        dialog.close()


def test_ready_and_playing_snapshots_enable_only_valid_controls() -> None:
    dialog = ReferenceTrackDialog()
    plays: list[bool] = []
    pauses: list[bool] = []
    dialog.play_requested.connect(lambda: plays.append(True))
    dialog.pause_requested.connect(lambda: pauses.append(True))
    try:
        dialog.set_snapshot(_snapshot(_State.READY))
        assert dialog._source.text() == "Rehearsal Reference.flac"
        assert dialog._time.text() == "0:30 / 2:00"
        assert dialog._play.isEnabled() is True
        assert dialog._pause.isEnabled() is False
        assert dialog._seek.isEnabled() is False
        dialog._play.click()
        assert plays == [True]

        dialog.set_snapshot(_snapshot(_State.PLAYING))
        assert "through Jamulus" in dialog._status.text()
        assert dialog._play.isEnabled() is False
        assert dialog._pause.isEnabled() is True
        assert dialog._trim.isEnabled() is False
        dialog._pause.click()
        assert pauses == [True]
    finally:
        dialog.close()


def test_paused_seek_and_loop_emit_bounded_semantic_intent() -> None:
    dialog = ReferenceTrackDialog()
    seeks: list[float] = []
    loops: list[tuple[float, object]] = []
    dialog.seek_requested.connect(seeks.append)
    dialog.loop_requested.connect(lambda start, end: loops.append((start, end)))
    try:
        dialog.set_snapshot(_snapshot(_State.PAUSED))
        assert dialog._seek.isEnabled() is True
        dialog._seek.setValue(dialog._SEEK_STEPS // 2)
        dialog._seek.sliderReleased.emit()
        assert seeks == [60.0]

        dialog._loop_start.setValue(10.0)
        dialog._loop_end.setValue(22.0)
        dialog._loop.setChecked(True)
        dialog._loop_start.editingFinished.emit()
        assert loops[-1] == (10.0, 22.0)

        dialog._loop.setChecked(False)
        assert loops[-1] == (0.0, None)
    finally:
        dialog.close()


def test_keyboard_seek_and_in_progress_trim_survive_snapshot_refresh() -> None:
    dialog = ReferenceTrackDialog()
    seeks: list[float] = []
    trims: list[float] = []
    dialog.seek_requested.connect(seeks.append)
    dialog.trim_requested.connect(trims.append)
    try:
        dialog.set_snapshot(_snapshot(_State.PAUSED))
        dialog.show()
        _app.processEvents()
        dialog._seek.setFocus()
        _app.processEvents()
        QTest.keyClick(dialog._seek, Qt.Key.Key_Right)
        assert seeks
        assert seeks[-1] > 30.0

        dialog._trim.setFocus()
        dialog._trim.lineEdit().selectAll()
        QTest.keyClicks(dialog._trim.lineEdit(), "-4.5")
        dialog.set_snapshot(_snapshot(_State.PAUSED))
        assert dialog._trim.lineEdit().text().startswith("-4.5")

        QTest.keyClick(dialog._trim.lineEdit(), Qt.Key.Key_Return)
        assert trims[-1] == -4.5
    finally:
        dialog.close()


def test_load_picker_accepts_only_supported_audio_and_emits_path() -> None:
    dialog = ReferenceTrackDialog()
    loaded: list[str] = []
    dialog.load_requested.connect(loaded.append)
    try:
        dialog.set_snapshot(_snapshot(_State.READY))
        with patch(
            "webjam_qt.windows.reference_track.QFileDialog.getOpenFileName",
            return_value=("/private/music/Reference Song.mp3", "Audio files"),
        ) as picker:
            dialog._load.click()

        assert loaded == ["/private/music/Reference Song.mp3"]
        file_filter = picker.call_args.args[3]
        for extension in ("*.wav", "*.aiff", "*.flac", "*.mp3"):
            assert extension in file_filter
        assert "/private/music" not in dialog._source.text()
    finally:
        dialog.close()


def test_failure_snapshot_shows_safe_controller_message() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_snapshot(
            _snapshot(
                _State.FAILED,
                error="The isolated route was lost; playback stopped.",
            )
        )
        assert "needs attention" in dialog._status.text().lower()
        assert "route was lost" in dialog._status.text()
        assert dialog._play.isEnabled() is False
        assert dialog._stop.isEnabled() is True
    finally:
        dialog.close()

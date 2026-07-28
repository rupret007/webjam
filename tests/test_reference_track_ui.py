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
        for width in (620, 760):
            dialog.resize(width, 540)
            dialog.show()
            _app.processEvents()

            assert dialog.width() <= 760
            assert dialog.height() <= 540
            assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
            vertical = dialog._scroll_area.verticalScrollBar()
            assert vertical.maximum() > 0
            assert "Scroll vertically" in (
                dialog._scroll_area.accessibleDescription()
            )

            vertical.setValue(vertical.maximum())
            _app.processEvents()
            for control in (dialog._safety, dialog._done):
                top_left = control.mapTo(
                    dialog._scroll_area.viewport(),
                    QPoint(0, 0),
                )
                assert top_left.y() >= 0
                assert top_left.y() + control.height() <= (
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


def test_committed_keyboard_edits_survive_stale_snapshot_until_acknowledged() -> None:
    dialog = ReferenceTrackDialog()
    seeks: list[float] = []
    loops: list[tuple[float, object]] = []
    trims: list[float] = []
    counts: list[tuple[int, float]] = []
    dialog.seek_requested.connect(seeks.append)
    dialog.loop_requested.connect(lambda start, end: loops.append((start, end)))
    dialog.trim_requested.connect(trims.append)
    dialog.count_in_requested.connect(
        lambda beats, bpm: counts.append((beats, bpm))
    )
    stale = _snapshot(_State.PAUSED)
    try:
        dialog.set_snapshot(stale)
        dialog.show()
        _app.processEvents()

        dialog._seek.setFocus()
        QTest.keyClick(dialog._seek, Qt.Key.Key_Right)
        pending_seek_value = dialog._seek.value()
        assert seeks
        dialog._done.setFocus()
        dialog.set_snapshot(stale)
        assert dialog._seek.value() == pending_seek_value

        seek_ack = _snapshot(_State.PAUSED)
        seek_ack.position_s = seeks[-1]
        dialog.set_snapshot(seek_ack)
        assert dialog._pending_seek_value is None

        dialog._loop_start.setFocus()
        dialog._loop_start.setValue(10.0)
        dialog._loop_start.editingFinished.emit()
        dialog._done.setFocus()
        dialog.set_snapshot(stale)
        assert loops[-1] == (10.0, 24.0)
        assert dialog._loop_start.value() == 10.0

        loop_ack = _snapshot(_State.PAUSED)
        loop_ack.loop_start_s = 10.0
        dialog.set_snapshot(loop_ack)
        assert dialog._pending_loop is None

        dialog._trim.setFocus()
        dialog._trim.setValue(-4.5)
        dialog._trim.editingFinished.emit()
        dialog._done.setFocus()
        dialog.set_snapshot(stale)
        assert trims[-1] == -4.5
        assert dialog._trim.value() == -4.5

        trim_ack = _snapshot(_State.PAUSED)
        trim_ack.trim_db = -4.5
        dialog.set_snapshot(trim_ack)
        assert dialog._pending_trim is None

        dialog._count_in.setFocus()
        dialog._count_in.setValue(6)
        dialog._count_in.editingFinished.emit()
        dialog._done.setFocus()
        dialog.set_snapshot(stale)
        assert counts[-1] == (6, 112.0)
        assert dialog._count_in.value() == 6

        count_ack = _snapshot(_State.PAUSED)
        count_ack.count_in_beats = 6
        dialog.set_snapshot(count_ack)
        assert dialog._pending_count_in is None

        later = _snapshot(_State.PAUSED)
        later.position_s = 45.0
        later.loop_start_s = 12.0
        later.trim_db = -6.0
        later.count_in_beats = 2
        dialog.set_snapshot(later)
        assert dialog._seek.value() == 3_750
        assert dialog._loop_start.value() == 12.0
        assert dialog._trim.value() == -6.0
        assert dialog._count_in.value() == 2
    finally:
        dialog.close()


def test_seek_intent_is_rejected_unless_snapshot_is_paused() -> None:
    dialog = ReferenceTrackDialog()
    seeks: list[float] = []
    dialog.seek_requested.connect(seeks.append)
    try:
        for state in (_State.READY, _State.PLAYING, _State.FAILED):
            dialog.set_snapshot(_snapshot(state))
            # Exercise the semantic guard directly; programmatic enabling must
            # not turn a stale or malicious activation into a seek.
            dialog._seek.setEnabled(True)
            dialog._seek.setValue(dialog._SEEK_STEPS // 2)
            dialog._emit_seek()

        assert seeks == []
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
        assert dialog._status.accessibleDescription() == dialog._status.text()
        assert dialog._route.accessibleDescription() == dialog._route.text()
    finally:
        dialog.close()


def test_unchanged_snapshot_does_not_repeat_accessibility_announcement() -> None:
    dialog = ReferenceTrackDialog()
    snapshot = _snapshot(_State.READY)
    try:
        with patch(
            "webjam_qt.windows.reference_track.QAccessible.updateAccessibility"
        ) as announce:
            dialog.set_snapshot(snapshot)
            first_count = announce.call_count
            assert first_count >= 1

            dialog.set_snapshot(snapshot)

        assert announce.call_count == first_count
    finally:
        dialog.close()

from __future__ import annotations

import os
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.windows.reference_track import (  # noqa: E402
    ReferenceTrackDialog,
    ReferenceTrackPrimaryGate,
)

_app = QApplication.instance() or QApplication([])


class _State(str, Enum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    READY = "ready"
    ROUTING = "routing"
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"


def _snapshot(
    state: _State,
    *,
    available: bool = True,
    error: str = "",
) -> SimpleNamespace:
    loaded = state not in {_State.UNAVAILABLE, _State.IDLE}
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
            reason_code="ready" if available else "unavailable",
        ),
        source_name="Rehearsal Reference.flac" if loaded else "",
        duration_s=120.0 if loaded else 0.0,
        position_s=30.0 if loaded else 0.0,
        loop_start_s=8.0,
        loop_end_s=24.0,
        trim_db=-3.0,
        count_in_beats=4,
        count_in_bpm=112.0,
        source_format="FLAC" if loaded else "",
        source_samplerate=44_100 if loaded else 0,
        source_channels=2 if loaded else 0,
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
        # The safety note tells the musician what the delay means for them
        # rather than naming the transport that causes it.
        safety = dialog.findChildren(type(dialog._status))[-1].text()
        assert "same delay" in safety
        assert "not a click track" in safety
        assert "Jamulus" not in safety
    finally:
        dialog.close()


def test_styled_reference_track_controls_remain_reachable_at_compact_size() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.setStyleSheet(load_stylesheet())
        locked = _snapshot(_State.UNAVAILABLE, available=False)
        locked.capability.detail = (
            "Reference Track is locked until BlackHole 16ch is installed and "
            "its isolated send-only route is verified. Playback remains "
            "disabled to prevent feedback or direct-monitor doubling."
        )
        dialog.set_snapshot(locked)
        for width, height in ((500, 500), (620, 540), (760, 540)):
            dialog.resize(width, height)
            dialog.show()
            _app.processEvents()
            vertical = dialog._scroll_area.verticalScrollBar()
            vertical.setValue(0)
            _app.processEvents()

            assert dialog.width() <= 760
            assert dialog.height() <= 540
            assert dialog._route.isVisibleTo(dialog)
            assert "locked until BlackHole" in dialog._route.text()
            assert dialog._route.width() <= dialog._scroll_area.viewport().width()
            assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
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


def test_unavailable_route_allows_loading_but_keeps_playback_fail_closed() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_snapshot(_snapshot(_State.IDLE, available=False))

        assert "load a song" in dialog._status.text().lower()
        assert "BlackHole" in dialog._route.text()
        assert "Playback route locked" in dialog._route.text()
        assert "Load and inspect" in dialog._route_guidance.text()
        assert dialog._load.isEnabled() is True
        assert dialog._recheck_route.isEnabled() is True
        assert dialog._play.isEnabled() is False
        assert dialog._pause.isEnabled() is False
        assert dialog._stop.isEnabled() is False
    finally:
        dialog.close()


def test_loaded_mp3_is_distinguished_from_physical_route_blocker() -> None:
    dialog = ReferenceTrackDialog()
    plays: list[bool] = []
    dialog.play_requested.connect(lambda: plays.append(True))
    snapshot = _snapshot(_State.READY, available=False)
    snapshot.source_name = "Band Reference.mp3"
    snapshot.source_format = "MP3"
    snapshot.capability.reason_code = "physical_certification_required"
    snapshot.capability.detail = (
        "Reference Track needs the official BlackHole 16ch or 64ch device at "
        "48 kHz."
    )
    try:
        dialog.set_snapshot(snapshot)

        # Certification is earned per machine, so a blocked musician is told
        # the prerequisite they can satisfy -- not that nothing can unlock it.
        assert dialog._route.text() == (
            f"Playback route locked. {snapshot.capability.detail}"
        )
        assert dialog._route.toolTip() == snapshot.capability.detail
        assert "MP3 loaded and its first bounded audio block decoded" in (
            dialog._route_guidance.text()
        )
        assert dialog._status.text() == (
            "MP3 loaded and decoded; Play needs the isolated audio device set "
            "up first"
        )
        assert "then choose Recheck Route" in dialog._route_guidance.text()
        assert "BlackHole 2ch" in dialog._route_guidance.text()
        assert "WebJam Bridge" in dialog._route_guidance.text()
        # Nothing may promise playback is permanently impossible.
        for surface in (
            dialog._route.text(),
            dialog._route_guidance.text(),
            dialog._status.text(),
            dialog._blackhole_setup.toolTip(),
            dialog._play.toolTip(),
        ):
            assert "downloaded candidate" not in surface
            assert "cannot unlock" not in surface
            assert "will not enable Play" not in surface
        assert dialog._blackhole_setup.isHidden() is False
        assert dialog._blackhole_setup.text() == "Set Up Reference Track…"
        assert "never downloads or installs" in dialog._blackhole_setup.toolTip()
        with patch(
            "webjam_qt.windows.reference_track.QDesktopServices.openUrl",
            return_value=True,
        ) as opener:
            dialog._blackhole_setup.click()
        opened = opener.call_args.args[0]
        assert opened.scheme() == "https"
        assert opened.host() == "existential.audio"
        assert opened.path() == "/blackhole/"
        assert dialog._load.isEnabled() is True
        assert dialog._play.isEnabled() is False
        assert "Set Up Reference Track" in dialog._play.toolTip()
        dialog._play.click()
        assert plays == []
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "source_name",
    (
        ("Very Long Rehearsal Name " * 10)[:250] + ".wav",
        ("秘密のリハーサル曲🎸" * 20)[:250] + ".flac",
        "A" * 250 + ".wav",
    ),
)
def test_long_source_names_never_push_dialog_actions_offscreen(
    source_name: str,
) -> None:
    dialog = ReferenceTrackDialog()
    snapshot = _snapshot(_State.READY)
    snapshot.source_name = source_name
    try:
        dialog.set_snapshot(snapshot)
        for width, height in ((500, 500), (760, 540)):
            dialog.resize(width, height)
            dialog.show()
            _app.processEvents()

            viewport = dialog._scroll_area.viewport()
            assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
            for control in (
                dialog._recheck_route,
                dialog._load,
                dialog._done,
            ):
                left = control.mapTo(viewport, control.rect().topLeft()).x()
                right = control.mapTo(
                    viewport,
                    control.rect().bottomRight(),
                ).x()
                assert left >= 0
                assert right < viewport.width()
            assert dialog._source.toolTip() == source_name
            assert dialog._source.accessibleDescription() == source_name
    finally:
        dialog.close()


def test_unbroken_route_and_error_text_never_create_horizontal_scroll() -> None:
    dialog = ReferenceTrackDialog()
    snapshot = _snapshot(_State.FAILED)
    snapshot.route_detail = "R" * 1024
    snapshot.error = "E" * 1024
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.set_snapshot(snapshot)
        dialog.resize(500, 500)
        dialog.show()
        _app.processEvents()

        viewport = dialog._scroll_area.viewport()
        assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
        for control in (
            dialog._recheck_route,
            dialog._load,
            dialog._done,
        ):
            left = control.mapTo(viewport, control.rect().topLeft()).x()
            right = control.mapTo(
                viewport,
                control.rect().bottomRight(),
            ).x()
            assert left >= 0
            assert right < viewport.width()
    finally:
        dialog.close()


def test_route_checking_is_visible_single_flight_state() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_snapshot(_snapshot(_State.IDLE, available=False))
        dialog.set_route_checking(True)
        assert dialog._recheck_route.text() == "Checking…"
        assert dialog._recheck_route.isEnabled() is False
        assert "Checking" in dialog._recheck_route.accessibleName()

        dialog.set_route_checking(False)
        assert dialog._recheck_route.text() == "Recheck Route"
        assert dialog._recheck_route.isEnabled() is True
    finally:
        dialog.close()


def test_transport_focus_follows_start_pause_and_resume_state() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.show()
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(_snapshot(_State.READY))
        dialog._play.setFocus()
        _app.processEvents()

        dialog.set_snapshot(_snapshot(_State.ROUTING))
        _app.processEvents()
        assert QApplication.focusWidget() is dialog._stop

        dialog.set_snapshot(_snapshot(_State.PLAYING))
        _app.processEvents()
        assert QApplication.focusWidget() is dialog._pause

        dialog.set_snapshot(_snapshot(_State.PAUSED))
        _app.processEvents()
        assert QApplication.focusWidget() is dialog._play
    finally:
        dialog.close()


def test_cleanup_pending_never_claims_route_ready() -> None:
    dialog = ReferenceTrackDialog()
    snapshot = _snapshot(
        _State.FAILED,
        available=True,
        error="WebJam could not confirm the participant stopped.",
    )
    snapshot.cleanup_pending = True
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(snapshot)
        assert "cleanup is still pending" in dialog._status.text()
        assert dialog._route.text().startswith(
            "Playback locked—finish stopping."
        )
        assert "Playback route ready" not in dialog._route.text()
        assert dialog._play.isEnabled() is False
        assert dialog._load.isEnabled() is False
        assert dialog._recheck_route.isEnabled() is False
        assert dialog._blackhole_setup.isEnabled() is False
        assert dialog._stop.isEnabled() is True
    finally:
        dialog.close()


def test_ready_and_playing_snapshots_enable_only_valid_controls() -> None:
    dialog = ReferenceTrackDialog()
    plays: list[bool] = []
    pauses: list[bool] = []
    dialog.play_requested.connect(lambda: plays.append(True))
    dialog.pause_requested.connect(lambda: pauses.append(True))
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(_snapshot(_State.READY))
        assert dialog._source.text() == "Rehearsal Reference.flac"
        assert dialog._source_details.text() == "FLAC · 44.1 kHz · stereo · 2:00"
        assert dialog._time.text() == "0:30 / 2:00"
        assert dialog._play.isEnabled() is True
        assert dialog._restart.isEnabled() is False
        assert dialog._pause.isEnabled() is False
        assert dialog._seek.isEnabled() is False
        dialog._play.click()
        assert plays == [True]

        dialog.set_snapshot(_snapshot(_State.PLAYING))
        # Mid-jam status names the song, not the transport carrying it.
        assert dialog._status.text() == "Playing to the band"
        assert "Jamulus" not in dialog._status.text()
        assert dialog._play.isEnabled() is False
        assert dialog._pause.isEnabled() is True
        assert dialog._trim.isEnabled() is False
        dialog._pause.click()
        assert pauses == [True]
    finally:
        dialog.close()


def test_loaded_song_waits_visibly_for_primary_jamulus_connection() -> None:
    dialog = ReferenceTrackDialog()
    plays: list[bool] = []
    dialog.play_requested.connect(lambda: plays.append(True))
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.NOT_CONNECTED)
        dialog.set_snapshot(_snapshot(_State.READY))

        assert "waiting for a verified primary Jamulus control connection" in (
            dialog._status.text()
        )
        assert "finish Jamulus sound setup" in (
            dialog._route_guidance.text()
        )
        assert dialog._play.isEnabled() is False
        assert "Finish Jamulus sound setup" in dialog._play.toolTip()
        dialog._play.click()
        assert plays == []

        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        assert dialog._play.isEnabled() is True
        assert "waiting for a verified primary Jamulus control connection" not in (
            dialog._status.text()
        )
        dialog._play.click()
        assert plays == [True]
    finally:
        dialog.close()


def test_session_change_gate_is_distinct_and_moves_focus_safely() -> None:
    dialog = ReferenceTrackDialog()
    plays: list[bool] = []
    stops: list[bool] = []
    dialog.play_requested.connect(lambda: plays.append(True))
    dialog.stop_requested.connect(lambda: stops.append(True))
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(_snapshot(_State.PAUSED))
        dialog.show()
        dialog._play.setFocus()
        _app.processEvents()
        assert dialog._play.hasFocus()

        dialog.set_primary_gate(ReferenceTrackPrimaryGate.SESSION_CHANGING)
        _app.processEvents()

        assert dialog._play.isEnabled() is False
        assert "current session change" in dialog._status.text()
        assert "ending, leaving, or switching" in dialog._route_guidance.text()
        assert "session change" in dialog._play.toolTip()
        assert "session change" in dialog._play.accessibleDescription()
        assert "session change" in dialog._restart.toolTip()
        assert "session change" in dialog._restart.accessibleDescription()
        assert "Finish Jamulus sound setup" not in dialog._route_guidance.text()
        assert dialog._recheck_route.isEnabled() is False
        assert dialog._stop.isEnabled() is False
        assert "single cleanup owner" in dialog._stop.toolTip()
        assert "single cleanup owner" in dialog._stop.accessibleDescription()
        assert dialog._play.hasFocus() is False
        assert dialog._done.hasFocus()
        dialog._play.click()
        dialog._stop.click()
        assert plays == []
        assert stops == []

        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        assert dialog._play.isEnabled() is True
    finally:
        dialog.close()


def test_host_required_gate_never_claims_connection_will_unlock_play() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.HOST_REQUIRED)
        dialog.set_snapshot(_snapshot(_State.READY))

        assert dialog._play.isEnabled() is False
        assert "only to the host" in dialog._status.text()
        assert "Only the host" in dialog._route_guidance.text()
        assert "will not unlock Play" in dialog._route_guidance.text()
        assert "Only the host" in dialog._play.accessibleDescription()
        assert "primary Jamulus connection" not in dialog._status.text()
        assert "Play will unlock automatically" not in (
            dialog._route_guidance.text()
        )
    finally:
        dialog.close()


@pytest.mark.parametrize(
    ("gate", "status_text", "guidance_text"),
    (
        (
            ReferenceTrackPrimaryGate.RECOVERING,
            "waiting for band audio recovery",
            "recovering or safely retiring",
        ),
        (
            ReferenceTrackPrimaryGate.RECOVERY_FAILED,
            "start a clean band audio session",
            "Press Start Session",
        ),
    ),
)
def test_recovery_gates_keep_transport_locked_with_truthful_next_step(
    gate: ReferenceTrackPrimaryGate,
    status_text: str,
    guidance_text: str,
) -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_primary_gate(gate)
        dialog.set_snapshot(_snapshot(_State.PAUSED))

        assert status_text in dialog._status.text()
        assert guidance_text in dialog._route_guidance.text()
        assert dialog._play.isEnabled() is False
        assert dialog._restart.isEnabled() is False
        assert dialog._play.accessibleDescription()
        assert dialog._restart.accessibleDescription()
    finally:
        dialog.close()


@pytest.mark.parametrize("state", (_State.PLAYING, _State.PAUSED))
@pytest.mark.parametrize(
    ("gate", "status_text"),
    (
        (
            ReferenceTrackPrimaryGate.RECOVERING,
            "band audio recovery",
        ),
        (
            ReferenceTrackPrimaryGate.SESSION_CHANGING,
            "current session change",
        ),
    ),
)
def test_active_track_locks_every_mutation_when_primary_is_not_ready(
    state: _State,
    gate: ReferenceTrackPrimaryGate,
    status_text: str,
) -> None:
    dialog = ReferenceTrackDialog()
    intents: list[str] = []
    dialog.play_requested.connect(lambda: intents.append("play"))
    dialog.pause_requested.connect(lambda: intents.append("pause"))
    dialog.restart_requested.connect(lambda: intents.append("restart"))
    dialog.stop_requested.connect(lambda: intents.append("stop"))
    dialog.seek_requested.connect(lambda _value: intents.append("seek"))
    dialog.loop_requested.connect(
        lambda _start, _end: intents.append("loop")
    )
    dialog.trim_requested.connect(lambda _value: intents.append("trim"))
    dialog.count_in_requested.connect(
        lambda _beats, _bpm: intents.append("count-in")
    )
    try:
        dialog.set_primary_gate(gate)
        dialog.set_snapshot(_snapshot(state))

        assert status_text in dialog._status.text()
        assert "controls are locked" in dialog._status.text()
        for control in (
            dialog._play,
            dialog._pause,
            dialog._restart,
            dialog._stop,
            dialog._seek,
            dialog._loop,
            dialog._loop_start,
            dialog._loop_end,
            dialog._trim,
            dialog._count_in,
            dialog._count_bpm,
        ):
            assert control.isEnabled() is False

        # Handler guards remain authoritative even if a stale accessibility or
        # queued callback reaches them after the widgets were disabled.
        dialog._emit_play()
        dialog._emit_pause()
        dialog._emit_restart()
        dialog._emit_stop()
        dialog._emit_seek()
        dialog._emit_loop()
        dialog._emit_trim()
        dialog._emit_count_in()
        assert intents == []
    finally:
        dialog.close()


def test_paused_seek_and_loop_emit_bounded_semantic_intent() -> None:
    dialog = ReferenceTrackDialog()
    seeks: list[float] = []
    loops: list[tuple[float, object]] = []
    dialog.seek_requested.connect(seeks.append)
    dialog.loop_requested.connect(lambda start, end: loops.append((start, end)))
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
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
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
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
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
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


def test_typed_edits_emit_once_and_remain_bounded_across_focus_changes() -> None:
    dialog = ReferenceTrackDialog()
    loops: list[tuple[float, object]] = []
    trims: list[float] = []
    counts: list[tuple[int, float]] = []
    dialog.loop_requested.connect(lambda start, end: loops.append((start, end)))
    dialog.trim_requested.connect(trims.append)
    dialog.count_in_requested.connect(
        lambda beats, bpm: counts.append((beats, bpm))
    )
    stale = _snapshot(_State.PAUSED)

    def type_and_commit(control, text: str) -> None:
        control.setFocus()
        control.lineEdit().selectAll()
        QTest.keyClicks(control.lineEdit(), text)
        QTest.keyClick(control.lineEdit(), Qt.Key.Key_Return)
        _app.processEvents()

    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(stale)
        dialog.show()
        _app.processEvents()

        type_and_commit(dialog._loop_start, "10.25")
        type_and_commit(dialog._loop_end, "23.75")
        type_and_commit(dialog._trim, "-4.5")
        type_and_commit(dialog._count_in, "6")
        type_and_commit(dialog._count_bpm, "126.5")
        dialog._done.setFocus()
        _app.processEvents()

        # Enter and the subsequent focus loss both produce editingFinished in
        # Qt. They represent one edit and must not issue duplicate commands.
        assert loops == [(10.25, 24.0), (10.25, 23.75)]
        assert trims == [-4.5]
        assert counts == [(6, 112.0), (6, 126.5)]

        dialog.set_snapshot(stale)
        assert dialog._loop_start.value() == 10.25
        assert dialog._loop_end.value() == 23.75
        assert dialog._trim.value() == -4.5
        assert dialog._count_in.value() == 6
        assert dialog._count_bpm.value() == 126.5

        # Mouse/stepper-style committed values use the same handlers, and Qt
        # clamps every emitted value to the production control contract.
        dialog._trim.setValue(-999.0)
        dialog._trim.editingFinished.emit()
        dialog._count_in.setValue(999)
        dialog._count_bpm.setValue(999.0)
        dialog._count_in.editingFinished.emit()
        assert trims[-1] == -60.0
        assert counts[-1] == (8, 240.0)
    finally:
        dialog.close()


def test_rejected_optimistic_edit_returns_to_controller_truth() -> None:
    dialog = ReferenceTrackDialog()
    stale = _snapshot(_State.PAUSED)
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(stale)
        with patch(
            "webjam_qt.windows.reference_track.monotonic",
            side_effect=(100.0, 100.25, 101.01),
        ):
            dialog._trim.setValue(-4.5)
            dialog._trim.editingFinished.emit()
            dialog._done.setFocus()

            dialog.set_snapshot(stale)
            assert dialog._trim.value() == -4.5

            dialog.set_snapshot(stale)
        assert dialog._pending_trim is None
        assert dialog._trim.value() == -3.0
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
        for extension in ("*.wav", "*.wave", "*.aiff", "*.flac"):
            assert extension in file_filter
        from core.reference_track import reference_track_file_filter

        assert file_filter == reference_track_file_filter()
        assert "/private/music" not in dialog._source.text()
    finally:
        dialog.close()


def test_loaded_source_remains_editable_when_route_is_unavailable() -> None:
    dialog = ReferenceTrackDialog()
    rechecks: list[bool] = []
    dialog.recheck_route_requested.connect(lambda: rechecks.append(True))
    try:
        snapshot = _snapshot(_State.READY, available=False)
        snapshot.source_name = "Loaded Song.WAVE"
        snapshot.source_format = "WAV"
        snapshot.source_samplerate = 48_000
        snapshot.source_channels = 1
        snapshot.route_detail = ""
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(snapshot)

        assert "loaded and ready to inspect" in dialog._status.text().lower()
        assert dialog._source.text() == "Loaded Song.WAVE"
        assert dialog._source_details.text() == "WAV · 48 kHz · mono · 2:00"
        assert dialog._load.isEnabled() is True
        assert dialog._trim.isEnabled() is True
        assert dialog._loop.isEnabled() is True
        assert dialog._play.isEnabled() is False
        dialog._recheck_route.click()
        assert rechecks == [True]
    finally:
        dialog.close()


def test_failure_snapshot_shows_safe_controller_message() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
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


def test_dialog_opens_clear_of_the_stage_then_respects_the_host() -> None:
    """A parented dialog centres itself, landing on top of the musicians.

    The Reference Track controls opened directly over the participant grid.
    They now anchor to the session window's leading edge on first open, and
    stay wherever the host drags them afterwards.
    """

    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(1587, 1330)
    parent.move(0, 25)
    parent.show()
    _app.processEvents()

    dialog = ReferenceTrackDialog(parent=parent)
    try:
        dialog.show()
        _app.processEvents()

        frame = parent.frameGeometry()
        dialog_centre = dialog.x() + dialog.width() // 2
        parent_centre = frame.left() + frame.width() // 2
        assert abs(dialog_centre - parent_centre) > 60, (
            "the dialog still opens centred on the stage"
        )
        assert dialog.x() >= frame.left()
        assert dialog.y() + dialog.height() <= frame.bottom() + 1

        # Once the host places it, reopening must not move it back.
        dialog.move(400, 400)
        dialog.hide()
        dialog.show()
        _app.processEvents()
        assert (dialog.x(), dialog.y()) == (400, 400)
    finally:
        dialog.close()
        parent.close()


def _drop_mime(paths: list[str], *, local: bool = True):
    from PySide6.QtCore import QMimeData, QUrl

    mime = QMimeData()
    urls = []
    for path in paths:
        if local:
            urls.append(QUrl.fromLocalFile(path))
        else:
            urls.append(QUrl(path))
    mime.setUrls(urls)
    return mime


def test_dropped_single_supported_audio_file_requests_load(tmp_path) -> None:
    dialog = ReferenceTrackDialog()
    try:
        assert dialog.acceptDrops()
        song = tmp_path / "Band Reference.flac"
        song.write_bytes(b"")
        received: list[str] = []
        dialog.load_requested.connect(received.append)

        for suffix in (".wav", ".wave", ".aif", ".aiff", ".flac"):
            candidate = tmp_path / f"song{suffix}"
            candidate.write_bytes(b"")
            assert dialog._dropped_audio_path(
                _drop_mime([str(candidate)])
            ) == str(candidate)

        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QDropEvent

        # QDropEvent stores a raw pointer to the mime data; keep the Python
        # reference alive for the lifetime of the event.
        mime = _drop_mime([str(song)])
        event = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        dialog.dropEvent(event)
        assert received == [str(song)]
    finally:
        dialog.deleteLater()


def test_drop_rejects_multi_file_remote_and_unsupported_payloads(
    tmp_path,
) -> None:
    dialog = ReferenceTrackDialog()
    try:
        received: list[str] = []
        dialog.load_requested.connect(received.append)
        song_a = tmp_path / "a.wav"
        song_b = tmp_path / "b.wav"
        text = tmp_path / "notes.txt"
        for item in (song_a, song_b, text):
            item.write_bytes(b"")

        assert dialog._dropped_audio_path(None) == ""
        from PySide6.QtCore import QMimeData

        assert dialog._dropped_audio_path(QMimeData()) == ""
        assert dialog._dropped_audio_path(
            _drop_mime([str(song_a), str(song_b)])
        ) == ""
        assert dialog._dropped_audio_path(_drop_mime([str(text)])) == ""
        assert dialog._dropped_audio_path(
            _drop_mime(["https://example.com/song.wav"], local=False)
        ) == ""
        assert received == []
    finally:
        dialog.deleteLater()


def test_playing_snapshot_with_underruns_warns_about_dropouts() -> None:
    dialog = ReferenceTrackDialog()
    try:
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        quiet = _snapshot(_State.PLAYING)
        quiet.underrun_frames = 0
        dialog.set_snapshot(quiet)
        assert "dropouts" not in dialog._status.text().lower()

        noisy = _snapshot(_State.PLAYING)
        noisy.underrun_frames = 48_000
        dialog.set_snapshot(noisy)
        assert "dropouts" in dialog._status.text().lower()

        idle = _snapshot(_State.READY)
        idle.underrun_frames = 48_000
        dialog.set_snapshot(idle)
        assert "dropouts" not in dialog._status.text().lower()
    finally:
        dialog.deleteLater()

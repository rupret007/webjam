from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.creative_modes import get_creator_profile_by_key_or_default  # noqa: E402
from core.recording_readiness_presentation import (  # noqa: E402
    RecordingChannelTopology,
    RecordingReadinessPresentation,
    RecordingReadinessSource,
    RecordingSourceKind,
    RecordingSourceReadiness,
    RecordingStoragePresentation,
    RecordingStorageReadiness,
    SharedTrackPresentation,
    SharedTrackReadiness,
)
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.widgets.session_strip import SessionStrip  # noqa: E402
from webjam_qt.windows.recording_readiness import (  # noqa: E402
    RecordingReadinessDialog,
)


APP = QApplication.instance() or QApplication([])


def _source(
    source_id: str,
    participant: str,
    source_label: str,
    kind: RecordingSourceKind,
    topology: RecordingChannelTopology,
    *,
    required: bool = True,
    readiness: RecordingSourceReadiness = RecordingSourceReadiness.READY,
    meter_percent: int | None = 50,
    detail: str = "Signal is stable.",
) -> RecordingReadinessSource:
    return RecordingReadinessSource(
        source_id=source_id,
        participant_label=participant,
        source_label=source_label,
        kind=kind,
        topology=topology,
        required=required,
        readiness=readiness,
        meter_percent=meter_percent,
        detail=detail,
    )


def _snapshot(*, blocked: bool = False) -> RecordingReadinessPresentation:
    sources = (
        _source(
            "server:alice",
            "Alice",
            "Server vocal",
            RecordingSourceKind.SERVER,
            RecordingChannelTopology.MONO,
            readiness=(
                RecordingSourceReadiness.ACTION_NEEDED
                if blocked
                else RecordingSourceReadiness.READY
            ),
            detail="Restore Alice’s server signal." if blocked else "Signal is stable.",
            meter_percent=0 if blocked else 68,
        ),
        _source(
            "local:host:1",
            "Host Mac",
            "Interface inputs 1–2",
            RecordingSourceKind.LOCAL_ORIGINAL,
            RecordingChannelTopology.STEREO,
            required=False,
            meter_percent=44,
        ),
        _source(
            "shared:reference",
            "Session",
            "Shared Track",
            RecordingSourceKind.SHARED_TRACK,
            RecordingChannelTopology.STEREO,
            meter_percent=None,
            detail="Isolated route is verified.",
        ),
    )
    return RecordingReadinessPresentation(
        profile_label="Music",
        sources=sources,
        storage=RecordingStoragePresentation(
            readiness=RecordingStorageReadiness.READY,
            summary="48.2 GB available",
            detail="Enough space for the expected take.",
        ),
        shared_track=SharedTrackPresentation(
            readiness=SharedTrackReadiness.READY,
            required=True,
            summary="Reference mix · stereo",
            detail="Included as its own exact source.",
        ),
    )


def _show_dialog(
    snapshot: RecordingReadinessPresentation,
    width: int,
    height: int,
) -> RecordingReadinessDialog:
    dialog = RecordingReadinessDialog(snapshot)
    dialog.setStyleSheet(load_stylesheet())
    dialog.resize(width, height)
    dialog.show()
    APP.processEvents()
    APP.processEvents()
    return dialog


def test_ready_dialog_shows_exact_sources_and_accepts_the_same_snapshot() -> None:
    snapshot = _snapshot()
    dialog = _show_dialog(snapshot, 600, 500)
    emitted: list[RecordingReadinessPresentation] = []
    dialog.start_requested.connect(emitted.append)
    try:
        assert dialog.presentation is snapshot
        assert len(dialog.source_rows) == 3
        assert [row.kind_label.text() for row in dialog.source_rows] == [
            "Server track",
            "Local Original",
            "Shared Track",
        ]
        assert [row.topology_label.text() for row in dialog.source_rows] == [
            "Mono",
            "Stereo",
            "Stereo",
        ]
        assert [row.obligation_label.text() for row in dialog.source_rows] == [
            "Required",
            "Optional",
            "Required",
        ]
        assert dialog._storage_card._summary.text().startswith("Ready ·")
        assert dialog._shared_track_card._summary.text().startswith("Ready ·")
        assert dialog._start_button.isEnabled()
        assert dialog._start_button.isDefault()

        QTest.mouseClick(dialog._start_button, Qt.MouseButton.LeftButton)

        assert emitted == [snapshot]
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_blocked_dialog_fails_closed_and_exposes_textual_blockers() -> None:
    snapshot = _snapshot(blocked=True)
    dialog = _show_dialog(snapshot, 600, 500)
    emitted: list[RecordingReadinessPresentation] = []
    dialog.start_requested.connect(emitted.append)
    try:
        assert not dialog._start_button.isEnabled()
        assert not dialog._start_button.isDefault()
        assert dialog._blockers.isVisibleTo(dialog)
        assert "Restore Alice’s server signal" in dialog._blockers_text.text()
        assert "Unavailable until 1" in dialog._start_button.accessibleDescription()
        assert "blocked by 1" in dialog.accessibleDescription()

        dialog.accept()

        assert emitted == []
        assert dialog.result() != dialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_async_whole_snapshot_refresh_replaces_rows_and_unlocks_start() -> None:
    blocked = _snapshot(blocked=True)
    ready = _snapshot()
    dialog = _show_dialog(blocked, 600, 500)
    try:
        stale_rows = dialog.source_rows
        dialog.set_presentation(ready)
        APP.processEvents()

        assert dialog.presentation is ready
        assert dialog.source_rows != stale_rows
        assert [row.source for row in dialog.source_rows] == list(ready.sources)
        assert dialog._start_button.isEnabled()
        assert dialog._source_count.text() == "3/3 ready"
        assert not dialog._blockers.isVisibleTo(dialog)
    finally:
        dialog.close()


@pytest.mark.parametrize(("width", "height"), [(600, 500), (760, 600)])
def test_readiness_sheet_fits_supported_compact_geometry(
    width: int,
    height: int,
) -> None:
    dialog = _show_dialog(_snapshot(blocked=True), width, height)
    try:
        assert dialog.size().width() == width
        assert dialog.size().height() == height
        assert dialog.minimumSizeHint().width() <= width
        assert dialog.minimumSizeHint().height() <= height
        assert dialog._scroll.horizontalScrollBar().maximum() == 0
        assert (
            dialog._scroll.geometry().bottom() < dialog._cancel_button.geometry().top()
        )
        assert (
            dialog._cancel_button.geometry().right()
            < dialog._start_button.geometry().left()
        )
        assert dialog._start_button.geometry().right() <= dialog.contentsRect().right()
        assert (
            dialog._start_button.geometry().bottom() <= dialog.contentsRect().bottom()
        )
        for row in dialog.source_rows:
            assert row.width() <= dialog._scroll.viewport().width()
            assert row.accessibleName()
            assert row.accessibleDescription()
            assert row.meter.accessibleName()
    finally:
        dialog.close()


def _strip() -> SessionStrip:
    return SessionStrip(mode_entries=[("rehearsal", "Rehearsal")])


def test_live_record_action_reuses_studio_primary_treatment() -> None:
    strip = _strip()
    try:
        strip.setStyleSheet(load_stylesheet())
        strip.set_recording_available(True)
        strip.show()
        APP.processEvents()

        assert strip._record_button.objectName() == "StudioRecordButton"
        assert strip._record_button.text() == "● Record Session"
        assert strip._record_button.accessibleName() == (
            "Start or stop band-server multitrack recording"
        )
    finally:
        strip.close()


def test_live_record_action_stays_distinct_and_reachable_at_720px() -> None:
    strip = _strip()
    try:
        strip.setStyleSheet(load_stylesheet())
        strip.set_recording_available(True)
        strip.set_compact_control_labels(True)
        strip.resize(720, strip.STRIP_HEIGHT)
        strip.show()
        APP.processEvents()

        visible_controls = [
            strip._logo,
            strip._title_input,
            strip._timer_label,
            strip._record_button,
            strip._video_button,
            strip._studio_button,
            strip._tools_button,
        ]
        assert all(control.isVisibleTo(strip) for control in visible_controls)
        assert max(control.geometry().right() for control in visible_controls) < 720
        for left, right in zip(visible_controls, visible_controls[1:]):
            assert left.geometry().right() < right.geometry().left()
        assert strip._record_button.text() == "● Record"
        assert strip._record_button.accessibleName() == (
            "Start or stop band-server multitrack recording"
        )
    finally:
        strip.close()


@pytest.mark.parametrize(
    ("profile_key", "expected_name", "expected_tooltip"),
    [
        ("music", "band-server multitrack", "connected musician"),
        ("podcast_voice", "synchronized voice", "connected speaker"),
        ("review_rehearsal", "synchronized WebJam audio", "connected participant"),
    ],
)
def test_record_treatment_preserves_creator_profile_copy_and_accessibility(
    profile_key: str,
    expected_name: str,
    expected_tooltip: str,
) -> None:
    strip = _strip()
    try:
        strip.set_creator_profile(get_creator_profile_by_key_or_default(profile_key))

        assert expected_name in strip._record_button.accessibleName()
        assert expected_tooltip in strip._record_button.toolTip()
    finally:
        strip.close()


def test_finalizing_is_identical_to_legacy_validating_phase() -> None:
    strip = _strip()
    try:
        presentations: dict[str, tuple[str, bool, str, str]] = {}
        for phase in ("validating", "finalizing"):
            strip.set_recording_phase(phase, detail="WAITING FOR SERVER FILES…")
            presentations[phase] = (
                strip._record_button.text(),
                strip._record_button.isEnabled(),
                strip._record_elapsed.text(),
                strip._record_button.accessibleDescription(),
            )

        assert presentations["finalizing"] == presentations["validating"]
        assert presentations["finalizing"][:3] == (
            "Finalizing…",
            False,
            "WAITING FOR SERVER FILES…",
        )
    finally:
        strip.close()


def test_blocked_local_inputs_open_setup_without_starting_recording():
    from dataclasses import replace
    from core.recording_readiness_presentation import RecordingReadinessRecovery
    snapshot = replace(_snapshot(blocked=True), recovery=RecordingReadinessRecovery.OPEN_RECORDING_SETUP)
    dialog = _show_dialog(snapshot, 600, 500)
    started = []
    dialog.start_requested.connect(started.append)
    try:
        assert not dialog._start_button.isEnabled()
        assert dialog._setup_button.isVisibleTo(dialog)
        assert "Fix the selected inputs" in dialog._setup_button.accessibleDescription()
        assert dialog.rect().contains(dialog._setup_button.mapTo(dialog, dialog._setup_button.rect().bottomRight()))
        assert dialog._setup_button.hasFocus()
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        assert dialog.result() == dialog.DialogCode.Rejected
        assert dialog.recovery_requested is RecordingReadinessRecovery.OPEN_RECORDING_SETUP
        assert started == []
    finally:
        dialog.deleteLater()


def test_application_returns_setup_intent_only_for_the_exact_blocked_sheet(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget
    from core.recording_readiness_presentation import RecordingReadinessRecovery
    from webjam_qt.controllers.application_controller import ApplicationController
    snapshot = replace(_snapshot(blocked=True), recovery=RecordingReadinessRecovery.OPEN_RECORDING_SETUP)
    def choose_setup(dialog):
        dialog._setup_button.click()
        return dialog.result()
    monkeypatch.setattr(RecordingReadinessDialog, "exec", choose_setup)
    window = QWidget()
    try:
        controller = SimpleNamespace(window=window)
        result = ApplicationController._confirm_recording_readiness(controller, snapshot)
        assert result is RecordingReadinessRecovery.OPEN_RECORDING_SETUP
        # A ready sheet cannot emit a setup intent or consent on this path.
        assert ApplicationController._confirm_recording_readiness(controller, _snapshot()) is False
    finally:
        window.deleteLater()

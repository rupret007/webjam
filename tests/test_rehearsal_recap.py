"""End-of-rehearsal recap card for Music sessions."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from core.rehearsal_recap import (
    RehearsalRecapSnapshot,
    take_status_label_for_recap,
)
from core.session_conductor import (
    SessionConductorFacts,
    SessionRole,
    TakeValidationState,
)
from core.session_intelligence import (
    ParticipantSignal,
    SessionAction,
    SessionPulse,
    build_session_pulse,
)
from tests.test_art_room_controller import controllers as _controllers_fixture
from tests.test_art_room_controller import qapp as _qapp_fixture
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.rehearsal_recap import RehearsalRecapPanel

controllers = _controllers_fixture
qapp = _qapp_fixture


def _sample_pulse() -> SessionPulse:
    return build_session_pulse(
        mode_key="music_jam",
        creator_profile_key="music",
        title="Tuesday jam",
        notes=(
            "Decision: keep the bridge short\n"
            "Decision: repeat the chorus\n"
            "Action: @Lee export a scratch mix"
        ),
        participants=({"is_local": True}, {"is_local": False}),
    )


def _ready_facts() -> SessionConductorFacts:
    return SessionConductorFacts(
        role=SessionRole.HOST,
        take_validation=TakeValidationState.VALID,
        take_available=True,
        take_path="/tmp/Take-1",
    )


def test_take_status_label_only_when_a_take_exists() -> None:
    assert take_status_label_for_recap(_ready_facts()) == "Ready"
    needs = replace(
        _ready_facts(),
        take_validation=TakeValidationState.NEEDS_ATTENTION,
    )
    assert take_status_label_for_recap(needs) == "Needs attention"
    empty = SessionConductorFacts(role=SessionRole.HOST)
    assert take_status_label_for_recap(empty) is None


def test_recap_shows_signal_line_and_open_studio_for_ready_take(
    controllers, qapp,
) -> None:
    app = controllers(hosting=True)
    pulse = _sample_pulse()
    assert pulse.signal_line == "2 decisions · 1 actions · 0 blockers"
    app._current_session_pulse = pulse
    app.recording.last_completed_take = Path("/tmp/Take-1")
    app.recording.last_validation = SimpleNamespace(ok=True)
    app.window.session_strip._elapsed_seconds = 95
    app.window.show()
    app._capture_rehearsal_recap_before_stop()
    app._present_rehearsal_recap_after_stop()
    qapp.processEvents()

    recap = app.window.rehearsal_recap
    assert recap.isVisibleTo(app.window)
    assert "2 decisions · 1 actions · 0 blockers" in recap._signals.text()
    assert recap._open_studio_button.isVisible() and recap._open_studio_button.isEnabled()
    app.shutdown()


def test_recap_without_take_hides_studio_and_take_line(controllers, qapp) -> None:
    app = controllers(hosting=True)
    app._current_session_pulse = _sample_pulse()
    app._capture_rehearsal_recap_before_stop()
    app._present_rehearsal_recap_after_stop()
    qapp.processEvents()

    recap = app.window.rehearsal_recap
    assert not recap._take_status.isVisible()
    assert not recap._open_studio_button.isVisible()
    app.shutdown()


def test_export_recap_writes_pulse_markdown_only(qapp, tmp_path) -> None:
    pulse = SessionPulse(
        mode_key="music_jam",
        mode_label="Music Jam",
        title="Handoff",
        stage="Decision",
        summary="Latest decision: keep tempo",
        next_step="Assign an owner",
        decisions=("keep tempo",),
        actions=(SessionAction("bounce rough mix", owner="Lee"),),
        blockers=(),
        questions=(),
        references=(),
        participant_signal=ParticipantSignal(count=2),
        checkpoint="first run",
    )
    panel = RehearsalRecapPanel()
    panel.apply_snapshot(
        RehearsalRecapSnapshot(duration_seconds=60, pulse=pulse, take_status=None)
    )
    target = tmp_path / "recap.md"
    with mock.patch(
        "webjam_qt.widgets.rehearsal_recap.QFileDialog.getSaveFileName",
        return_value=(str(target), "Markdown (*.md)"),
    ):
        panel._export_button.click()
    written = target.read_text(encoding="utf-8")
    assert written == pulse.to_markdown()
    assert "SECRET_RAW_NOTE_BODY" not in written
    panel.deleteLater()


def test_art_session_end_does_not_show_recap(controllers, qapp) -> None:
    app = controllers(profile="art", hosting=True)
    app._current_session_pulse = _sample_pulse()
    app._capture_rehearsal_recap_before_stop()
    assert app._pending_rehearsal_recap is None
    app._present_rehearsal_recap_after_stop()
    qapp.processEvents()
    recap = app.window.findChild(QWidget, "RehearsalRecap")
    assert recap is not None and not recap.isVisible()
    app.shutdown()


def test_recap_layout_leaves_end_session_controls_visible(controllers, qapp) -> None:
    app = controllers(hosting=True)
    window = app.window
    window.setStyleSheet(load_stylesheet())
    window.show()
    app._pending_rehearsal_recap = RehearsalRecapSnapshot(
        duration_seconds=600,
        pulse=_sample_pulse(),
        take_status="Ready",
    )
    app._present_rehearsal_recap_after_stop()
    window.resize(1000, 740)
    for _ in range(10):
        qapp.processEvents()
    window.session_strip.set_audio_state("End Session")
    button = window.session_strip._audio_button
    assert button.isVisibleTo(window)
    top_left = button.mapTo(window, QPoint())
    assert window.rect().contains(QRect(top_left, button.size()))
    assert window.rehearsal_recap.isVisibleTo(window)
    app.shutdown()

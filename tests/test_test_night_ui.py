"""Focused coverage for the operator-only Test Night surface."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv[:1])


def _mode_entries() -> list[tuple[str, str]]:
    return [("music_jam", "Music Jam")]


def test_operator_argument_is_read_without_mutating_user_arguments() -> None:
    from webjam_qt.app import (
        TEST_NIGHT_ARGUMENT,
        qt_arguments_without_test_night,
        test_night_mode_from_arguments,
    )

    user_arguments = (
        "WebJam",
        TEST_NIGHT_ARGUMENT,
        "webjam://join?host=127.0.0.1",
        "--style=Fusion",
    )

    assert test_night_mode_from_arguments(user_arguments) is True
    assert qt_arguments_without_test_night(user_arguments) == [
        "WebJam",
        "--style=Fusion",
    ]
    assert user_arguments[1] == TEST_NIGHT_ARGUMENT
    assert test_night_mode_from_arguments(("WebJam",)) is False


def test_app_plumbing_passes_operator_mode_only_when_flagged() -> None:
    from core.settings import AppSettings
    from webjam_qt import app as app_module
    from webjam_qt.windows.launch_dialog import LaunchDialog

    initial = AppSettings(config_file="/missing.json")
    saved = AppSettings(config_file="/saved.json")
    launcher = MagicMock()
    launcher.exec.return_value = LaunchDialog.DialogCode.Accepted
    launcher.selected_role = "host"
    launcher.session_name = "Band Rehearsal"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    original_arguments = ("WebJam", "--test-night")

    with patch.object(sys, "argv", list(original_arguments)), patch.object(
        app_module, "load_settings", side_effect=[initial, saved]
    ), patch.object(app_module, "LaunchDialog", return_value=launcher), patch.object(
        app_module.QApplication, "instance", return_value=qt_app
    ), patch.object(app_module, "load_stylesheet", return_value=""), patch.object(
        app_module, "make_brand_icon", return_value=MagicMock()
    ), patch.object(app_module, "_configure_default_font"), patch.object(
        app_module, "ConductorWindow", return_value=MagicMock()
    ) as window_class, patch.object(
        app_module, "ApplicationController", return_value=controller
    ), patch.object(app_module.QTimer, "singleShot"), patch.dict(
        os.environ, {}, clear=False
    ):
        os.environ.pop("WEBJAM_SMOKE_AUTOSTART_AUDIO", None)
        assert app_module._run_app() == 0
        assert sys.argv == list(original_arguments)

    assert window_class.call_args.kwargs["operator_mode"] is True


def test_normal_session_strip_has_no_test_night_entry(qapp) -> None:
    from webjam_qt.widgets.session_strip import SessionStrip

    strip = SessionStrip(mode_entries=_mode_entries(), operator_mode=False)

    assert strip._test_night_action is None
    assert "Test Night" not in [action.text() for action in strip._tools_button.menu().actions()]


def test_operator_session_strip_exposes_test_night_from_more_menu(qapp) -> None:
    from webjam_qt.widgets.session_strip import SessionStrip

    strip = SessionStrip(mode_entries=_mode_entries(), operator_mode=True)
    opened: list[str] = []
    strip.test_night_requested.connect(lambda: opened.append("opened"))

    assert strip._test_night_action is not None
    assert strip._test_night_action.text() == "Test Night"
    strip._test_night_action.trigger()

    assert opened == ["opened"]


def test_conductor_window_forwards_operator_test_night_signal(qapp) -> None:
    from webjam_qt.windows.conductor_window import ConductorWindow

    window = ConductorWindow(
        mode_entries=_mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Pilot",
        operator_mode=True,
    )
    opened: list[str] = []
    window.test_night_requested.connect(lambda: opened.append("opened"))

    assert window.operator_mode is True
    assert window.session_strip._test_night_action is not None
    window.session_strip._test_night_action.trigger()

    assert opened == ["opened"]
    window.close()


def test_test_night_dialog_emits_intent_but_does_not_create_outcomes(qapp) -> None:
    from webjam_qt.windows.test_night import TestNightDialog

    dialog = TestNightDialog()
    received: list[tuple[str, object]] = []
    dialog.start_requested.connect(lambda: received.append(("start", None)))
    dialog.pause_requested.connect(lambda: received.append(("pause", None)))
    dialog.resume_requested.connect(lambda: received.append(("resume", None)))
    dialog.abandon_requested.connect(lambda: received.append(("abandon", None)))
    dialog.restart_requested.connect(lambda: received.append(("restart", None)))
    dialog.export_report_requested.connect(lambda: received.append(("export", None)))
    dialog.manual_outcome_requested.connect(
        lambda key, outcome: received.append(("manual", (key, outcome)))
    )

    assert dialog.run_state == "not_started"
    assert dialog._check_status_labels["hear_each_other"].text() == "Waiting"
    dialog._start.click()
    dialog.set_run_state("running")
    dialog._pause.click()
    dialog._record_manual.click()
    # The click only emits a request; a controller response is required to
    # render the outcome into the checklist.
    assert dialog._check_status_labels["hear_each_other"].text() == "Waiting"
    dialog.set_check_status("hear_each_other", "verified")
    assert dialog._check_status_labels["hear_each_other"].text() == "Verified"

    dialog._abandon.click()
    dialog.set_run_state("paused")
    dialog._resume.click()
    dialog.set_run_state("abandoned")
    dialog._restart.click()
    dialog.set_export_available(True)
    dialog.set_run_state("completed")
    dialog._export.click()

    assert received[0:3] == [
        ("start", None),
        ("pause", None),
        ("manual", ("hear_each_other", "verified")),
    ]
    assert ("resume", None) in received
    assert ("abandon", None) in received
    assert ("restart", None) in received
    assert ("export", None) in received
    dialog.close()

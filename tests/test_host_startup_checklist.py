from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.host_startup_checklist import (
    HOST_STARTUP_CHECKLIST_STEPS,
    HOST_STARTUP_PROFILE_NOTE,
    WIN32_MUSIC_HOST_REASON,
    format_host_startup_checklist,
)
from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.widgets.session_hud import SessionHud
from webjam_qt.windows.launch_dialog import LaunchDialog


pytestmark = pytest.mark.requires_local_socket


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_native_sound_setup_hud_shows_three_step_checklist(qapp, tmp_path: Path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Native setup checklist",
    )
    controller = ApplicationController(window, settings=settings)
    try:
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("guest")
        controller._startup_attempt = {
            "generation": 1,
            "role": "guest",
            "phase": "native_sound_setup",
            "native_setup_deadline": 9_999_999.0,
            "conductor_token": token,
        }
        controller._render_startup_journey()
        window.show()
        qapp.processEvents()
        hud = window.session_hud
        assert hud.checklist_lines() == (
            f"1. {HOST_STARTUP_CHECKLIST_STEPS[0]}",
            f"2. {HOST_STARTUP_CHECKLIST_STEPS[1]}",
            f"3. {HOST_STARTUP_CHECKLIST_STEPS[2]}",
        )
        assert hud.property("currentStep") == 2
        assert hud._checklist_note.text() == HOST_STARTUP_PROFILE_NOTE
        assert hud._action.isVisibleTo(window)
        assert hud._action.text().replace("&", "") == "Bring Jamulus Forward"
    finally:
        window.deleteLater()


def test_invite_ready_host_marks_step_three_and_copy_invite(qapp, tmp_path: Path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Host checklist",
    )
    controller = ApplicationController(window, settings=settings)
    try:
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("host")
        controller._startup_attempt = {
            "generation": 1,
            "role": "host",
            "phase": "invite_ready",
            "conductor_token": token,
        }
        controller._render_startup_journey()
        window.show()
        qapp.processEvents()
        hud = window.session_hud
        assert hud.property("currentStep") == 3
        assert hud._action.text().replace("&", "") == "Copy Invite"
        assert hud._action.isVisibleTo(window)
        assert len(hud.checklist_lines()) == 3
        assert hud.checklist_lines()[-1].startswith("3.")
    finally:
        window.deleteLater()


def test_identical_hud_updates_do_not_churn_accessible_description(qapp):
    hud = SessionHud()
    plain, rich = format_host_startup_checklist(2)
    kwargs = {
        "status": "Set up your sound in Jamulus",
        "detail": plain,
        "action_text": "Bring Jamulus Forward",
        "action_visible": True,
        "action_kind": "bring_jamulus",
        "checklist_current_step": 2,
        "checklist_detail_rich": rich,
        "checklist_note": HOST_STARTUP_PROFILE_NOTE,
    }
    hud.set_state(**kwargs)
    first = hud.accessibleDescription()
    hud.set_state(**kwargs)
    assert hud.accessibleDescription() == first


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_non_macos_music_shows_host_reason_on_choice_page(
    qapp, tmp_path: Path, platform: str
):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(sys, "platform", platform):
        dialog = LaunchDialog(settings)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.selected_creator_profile_key == "music"
        assert not dialog._host_button.isEnabled()
        assert dialog._join_button.isEnabled()
        reason = dialog._host_reason
        assert reason.isVisibleTo(dialog._choice_page)
        assert reason.text() == WIN32_MUSIC_HOST_REASON
        harvested = harvest_first_screen(dialog)
        assert_no_banned_first_screen_words(harvested)
    finally:
        dialog.close()

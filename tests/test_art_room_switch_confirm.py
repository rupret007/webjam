"""W14: Art-active invite-switch confirm and failure HUD use room wording.

Music copy must stay exactly as before; only the Art-active branch should
say "room" instead of "jam".
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from core.network_invite import create_invite_link
from core.settings import AppSettings, save_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _controller(tmp_path, *, profile: str, hosting: bool = False):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=hosting,
        jamulus_server="127.0.0.1",
        last_creator_profile_key=profile,
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Old Session",
    )
    controller = ApplicationController(window, settings=settings)
    return controller


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


@pytest.mark.parametrize(
    ("profile", "expected_title", "expected_body"),
    [
        (
            "art",
            "Join this room?",
            "WebJam will safely end your current room, then join the new one.",
        ),
        (
            "music",
            "Join this jam?",
            "WebJam will safely end your current jam, then join the new one.",
        ),
    ],
)
def test_switch_confirm_dialog_matches_active_profile(
    qapp, tmp_path, profile, expected_title, expected_body
):
    controller = _controller(tmp_path, profile=profile, hosting=False)
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    question = MagicMock(return_value=QMessageBox.StandardButton.No)
    link = create_invite_link("192.168.1.42", session_name="New Session")

    with patch.object(QMessageBox, "question", question):
        assert controller.accept_invite_url(link) is False

    question.assert_called_once()
    args = question.call_args.args
    assert args[1] == expected_title
    assert args[2] == expected_body
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_switch_failure_hud_uses_room_wording_when_art_active(qapp, tmp_path):
    controller = _controller(tmp_path, profile="art", hosting=True)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)
    controller.recording.stop_server_recording_for_shutdown = MagicMock(
        side_effect=[False, True]
    )
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller.bridge.stop_hosted_server = MagicMock(return_value=True)
    controller.begin_startup_journey = MagicMock()
    controller.window.flash_message = MagicMock()

    link = create_invite_link("192.168.1.42", session_name="New Session")

    with (
        patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ),
        patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
        ),
        patch.object(
            controller._ui_invoker, "invoke", side_effect=lambda callback: callback()
        ),
    ):
        assert controller.accept_invite_url(link) is True

    assert controller.audio.cleanup_retry_required is True
    assert controller.window.session_hud._status.text() == (
        "WebJam couldn’t open the new room safely"
    )
    assert "jam" not in controller.window.session_hud._status.text()
    flash_text = controller.window.flash_message.call_args.args[0]
    assert flash_text.startswith("The room switch did not finish safely.")
    assert "jam" not in flash_text
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_switch_failure_hud_keeps_jam_wording_when_music_active(qapp, tmp_path):
    controller = _controller(tmp_path, profile="music", hosting=True)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)
    controller.recording.stop_server_recording_for_shutdown = MagicMock(
        side_effect=[False, True]
    )
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller.bridge.stop_hosted_server = MagicMock(return_value=True)
    controller.begin_startup_journey = MagicMock()
    controller.window.flash_message = MagicMock()

    link = create_invite_link("192.168.1.42", session_name="New Session")

    with (
        patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ),
        patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
        ),
        patch.object(
            controller._ui_invoker, "invoke", side_effect=lambda callback: callback()
        ),
    ):
        assert controller.accept_invite_url(link) is True

    assert controller.audio.cleanup_retry_required is True
    assert controller.window.session_hud._status.text() == (
        "WebJam couldn’t open the new jam safely"
    )
    flash_text = controller.window.flash_message.call_args.args[0]
    assert flash_text.startswith("The jam switch did not finish safely.")
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()

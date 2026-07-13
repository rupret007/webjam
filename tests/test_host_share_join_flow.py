from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QLineEdit,
    QMessageBox,
)

from core.network_invite import (
    InviteLinkError,
    create_invite_link,
    parse_invite_link,
)
from core.settings import AppSettings, save_settings
from webjam_qt.widgets.session_hud import SessionHud
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_invite_link_round_trip_contains_only_public_connection_data():
    link = create_invite_link(
        "192.168.1.42", port=22124, session_name="Sunday Rehearsal"
    )
    assert link.startswith("webjam://join?")
    assert "secret" not in link.lower()
    assert "record" not in link.lower()
    invite = parse_invite_link(link)
    assert invite.host == "192.168.1.42"
    assert invite.port == 22124
    assert invite.session_name == "Sunday Rehearsal"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/join?host=192.168.1.42",
        "webjam://join?host=192.168.1.42&command=rm",
        "webjam://join?host=192.168.1.42&host=192.168.1.43",
        "webjam://join?host=192.168.1.42%3A9999&port=22124",
        "webjam://join?v=99&host=192.168.1.42",
        "webjam://join?host=127.0.0.1&port=nope",
    ],
)
def test_invite_parser_rejects_wrong_or_ambiguous_links(value):
    with pytest.raises(InviteLinkError):
        parse_invite_link(value)


def test_launch_initially_shows_only_host_and_join_actions(qapp, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog.show()
    qapp.processEvents()
    visible_actions = [
        button
        for button in dialog.findChildren(QAbstractButton)
        if button.isVisibleTo(dialog)
    ]
    assert [button.accessibleName() for button in visible_actions] == [
        "Host a Jam",
        "Join a Jam",
    ]
    assert dialog.showing_choices
    assert not dialog._invite_input.isVisibleTo(dialog)
    dialog.close()


def test_host_is_one_click_and_derives_every_technical_default(qapp, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
        dialog._host_button.click()
    data = json.loads(Path(settings.config_file).read_text(encoding="utf-8"))
    assert dialog.selected_role == "host"
    assert data["host_server_enabled"] is True
    assert data["jamulus_server"] == "127.0.0.1"
    assert data["jamulus_port"] == 22124
    assert data["audio_input_device_index"] == -1
    assert data["local_capture_enabled"] is False
    assert data["musician_name"] != "WebJam Musician"


def test_host_launch_preserves_explicit_recording_setup(qapp, tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        audio_input_device_index=7,
        take_playback_output_device="SSL 2+",
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
        dialog._host_button.click()
    data = json.loads(Path(settings.config_file).read_text(encoding="utf-8"))
    assert data["local_capture_enabled"] is True
    assert data["audio_input_device_index"] == 7
    assert data["take_playback_output_device"] == "SSL 2+"


def test_join_asks_for_one_link_and_applies_it(qapp, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog.show()
    qapp.processEvents()
    visible_fields = [
        field
        for field in dialog.findChildren(QLineEdit)
        if field.isVisibleTo(dialog)
    ]
    assert visible_fields == [dialog._invite_input]
    dialog._invite_input.setText(
        create_invite_link(
            "192.168.1.42", session_name="Drummer Test"
        )
    )
    dialog._join_button_primary.click()
    data = json.loads(Path(settings.config_file).read_text(encoding="utf-8"))
    assert dialog.selected_role == "join"
    assert dialog.session_name == "Drummer Test"
    assert data["host_server_enabled"] is False
    assert data["jamulus_server"] == "192.168.1.42"
    assert data["jamulus_port"] == 22124


def test_session_hud_has_semantic_copy_and_retry_actions(qapp):
    hud = SessionHud()
    copied = MagicMock()
    retried = MagicMock()
    hud.invite_requested.connect(copied)
    hud.retry_requested.connect(retried)
    hud.set_state(
        "Ready to share",
        "Waiting for bandmates.",
        invite_url="webjam://join?host=192.168.1.42",
    )
    hud._action.click()
    copied.assert_called_once()
    retried.assert_not_called()
    hud.set_state(
        "Something needs attention",
        "Try again.",
        action_text="Try Again",
        action_visible=True,
        action_kind="retry",
    )
    hud._action.click()
    retried.assert_called_once()


def test_host_invite_stays_hidden_until_real_server_readiness(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
        takes_directory=str(tmp_path / "takes"),
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Readiness Test",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=False)
    controller._update_session_hud()
    assert controller.window.session_hud.invite_url() == ""
    assert controller.window.session_hud._action.isHidden()
    controller.bridge.hosted_server_alive.return_value = True
    with patch(
        "core.network_invite.local_band_address", return_value="192.168.1.42"
    ):
        controller._update_session_hud()
    assert controller.window.session_hud.invite_url().startswith("webjam://join?")
    assert controller.window.session_hud._action.isHidden()
    assert not controller.window.session_strip._invite_button.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_one_local_host_is_ready_to_share_not_a_bandmate(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.widgets.participant_card import ParticipantPresentation
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Local Truth",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller._jamulus_connected = True
    controller.participants = {
        0: ParticipantPresentation(0, "Jeff", "You", is_local=True)
    }
    with patch(
        "core.network_invite.local_band_address", return_value="192.168.1.42"
    ):
        controller._update_session_hud()
    assert controller.window.session_hud._status.text() == "Ready to share"
    assert "Bandmate" not in controller.window.session_hud._status.text()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_role_label_hides_empty_jamulus_sentinels():
    from webjam_qt.controllers.application_controller import ApplicationController

    person = SimpleNamespace(
        channel_id=0,
        is_local=True,
        instrument="None",
        skill_level="null",
    )
    assert ApplicationController._role_label(person) == "You"


def test_connection_timeout_replaces_spinner_with_one_retry(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_server="192.168.1.42",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Timeout Test",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.stop_jamulus = MagicMock()
    with patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
    ):
        controller._on_connection_timeout()
    controller._update_session_hud()
    assert controller.audio.connection_timed_out is True
    # Recovery has one primary action in the stage.  The HUD explains the
    # problem but must not grow a second Retry button.
    assert controller.window.participant_grid._empty_primary.text() == "Try Again"
    assert controller.window.participant_grid._empty_primary.isEnabled()
    assert controller.window.session_hud._action.isHidden()
    assert "same Wi-Fi" in controller.window.session_hud._detail.text()
    controller.bridge.jamulus_launch_intended = False
    controller.shutdown()


def test_default_input_meter_does_not_claim_session_audio_ready(qapp, tmp_path):
    from types import SimpleNamespace

    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.widgets.participant_card import ParticipantPresentation
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_server="192.168.1.42",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Meter Truth Test",
    )
    controller = ApplicationController(window, settings=settings)
    controller.participants = {
        0: ParticipantPresentation(0, "Me", "You", is_local=True)
    }
    engine = MagicMock()
    engine.diagnostics.return_value = SimpleNamespace(backend="sounddevice")
    engine.has_level_override.return_value = False
    engine.get_level.side_effect = lambda channel_id: 0.7 if channel_id == -1 else 0.0
    controller.jamulus.audio_engine = engine

    controller._poll_levels()

    assert controller._local_audio_seen is False
    engine.has_level_override.return_value = True
    engine.get_level.side_effect = lambda _channel_id: 0.7
    controller._poll_levels()
    assert controller._local_audio_seen is True
    controller.shutdown()


def test_host_requires_its_own_roster_entry_before_connected(qapp, tmp_path):
    from jamulus_controller import JamulusParticipant
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Host Roster Truth",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    with patch(
        "core.network_invite.local_band_address", return_value="192.168.1.42"
    ):
        controller._apply_jamulus_participants([
            JamulusParticipant(channel_id=7, name="Guest", is_local=False)
        ])
    assert controller._jamulus_connected is False
    assert 7 in controller.participants
    assert controller._connection_timer.isActive()
    assert controller.window.session_hud._status.text() == "Connecting your audio…"
    assert "Bandmate connected" not in controller.window.session_hud._status.text()

    with patch(
        "core.network_invite.local_band_address", return_value="192.168.1.42"
    ):
        controller._apply_jamulus_participants([
            JamulusParticipant(channel_id=3, name="Host", is_local=True),
            JamulusParticipant(channel_id=7, name="Guest", is_local=False),
        ])
    assert controller._jamulus_connected is True
    assert not controller._connection_timer.isActive()
    controller.bridge.jamulus_launch_intended = False
    controller.shutdown()


def test_running_app_accepts_invite_and_reconfigures_join(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        jamulus_server="192.168.1.10",
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Old Jam",
    )
    controller = ApplicationController(window, settings=settings)
    controller._on_launch_audio = MagicMock()
    link = create_invite_link(
        "192.168.1.42", session_name="New Jam"
    )
    assert controller.accept_invite_url(link) is True
    assert controller.settings.jamulus_server == "192.168.1.42"
    assert controller.settings.host_server_enabled is False
    assert window.session_strip.current_title() == "New Jam"
    controller._on_launch_audio.assert_called_once()
    controller.shutdown()


def test_running_host_finalizes_recording_before_switching_invites(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Old Host Jam",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)
    events: list[str] = []
    controller.recording.stop_server_recording_for_shutdown = MagicMock(
        side_effect=lambda: events.append("recorder-stop") or True
    )
    controller.recording.on_audio_session_stopped = MagicMock(
        side_effect=lambda: events.append("recording-reset")
    )
    controller.bridge.stop_jamulus = MagicMock(
        side_effect=lambda: events.append("client-stop") or True
    )
    controller.bridge.stop_hosted_server = MagicMock(
        side_effect=lambda: events.append("server-stop") or True
    )
    controller._on_launch_audio = MagicMock(
        side_effect=lambda: events.append("new-join-launch")
    )
    link = create_invite_link("192.168.1.42", session_name="New Join Jam")
    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as question, patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
    ), patch.object(
        controller._ui_invoker, "invoke", side_effect=lambda callback: callback()
    ):
        assert controller.accept_invite_url(link) is True

    assert question.call_args.args[4] == QMessageBox.StandardButton.No
    assert events == [
        "recorder-stop",
        "client-stop",
        "server-stop",
        "recording-reset",
        "new-join-launch",
    ]
    assert controller.settings.host_server_enabled is False
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_running_host_must_finish_take_before_switching_invites(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Recording Host Jam",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)
    controller._server_recording = True
    controller._recorder_armed = True
    link = create_invite_link("192.168.1.42", session_name="New Join Jam")
    with patch.object(QMessageBox, "information") as information, patch.object(
        QMessageBox, "question"
    ) as question:
        assert controller.accept_invite_url(link) is False
    information.assert_called_once()
    question.assert_not_called()
    controller._server_recording = False
    controller._recorder_armed = False
    controller.bridge.hosted_server_alive.return_value = False
    controller.bridge.jamulus_state = "Stopped"
    controller.shutdown()


def test_returning_user_still_gets_host_join_gate_and_autostart(qapp):
    from webjam_qt import app as app_module

    initial = AppSettings(config_file="/already/configured.json")
    saved = AppSettings(config_file="/already/configured.json")
    launcher = MagicMock()
    launcher.exec.return_value = LaunchDialog.DialogCode.Accepted
    launcher.selected_role = "host"
    launcher.session_name = "Band Rehearsal"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    with patch.dict(os.environ, {}, clear=False), patch.object(
        app_module, "load_settings", side_effect=[initial, saved]
    ), patch.object(
        app_module, "LaunchDialog", return_value=launcher
    ) as launcher_class, patch.object(
        app_module.QApplication, "instance", return_value=qt_app
    ), patch.object(
        app_module, "load_stylesheet", return_value=""
    ), patch.object(
        app_module, "ConductorWindow", return_value=MagicMock()
    ), patch.object(
        app_module, "ApplicationController", return_value=controller
    ), patch.object(app_module.QTimer, "singleShot") as single_shot:
        os.environ.pop("WEBJAM_SMOKE_AUTOSTART_AUDIO", None)
        assert app_module.run() == 0
    launcher_class.assert_called_once_with(initial, initial_invite_url="")
    single_shot.assert_called_once_with(0, controller._on_launch_audio)


def test_cold_launch_passes_command_line_invite_to_gate(qapp):
    from webjam_qt import app as app_module

    link = create_invite_link("192.168.1.42")
    initial = AppSettings(config_file="/missing.json")
    saved = AppSettings(config_file="/saved.json")
    launcher = MagicMock()
    launcher.exec.return_value = LaunchDialog.DialogCode.Accepted
    launcher.selected_role = "join"
    launcher.session_name = "Band Rehearsal"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    with patch.object(sys, "argv", ["WebJam", link]), patch.object(
        app_module, "load_settings", side_effect=[initial, saved]
    ), patch.object(
        app_module, "LaunchDialog", return_value=launcher
    ) as launcher_class, patch.object(
        app_module.QApplication, "instance", return_value=qt_app
    ), patch.object(
        app_module, "load_stylesheet", return_value=""
    ), patch.object(
        app_module, "ConductorWindow", return_value=MagicMock()
    ), patch.object(
        app_module, "ApplicationController", return_value=MagicMock()
    ), patch.object(app_module.QTimer, "singleShot"):
        assert app_module.run() == 0
    launcher_class.assert_called_once_with(initial, initial_invite_url=link)


def test_macos_bundle_registers_webjam_invitation_scheme():
    spec = Path("webjam.spec").read_text(encoding="utf-8")
    assert '"CFBundleURLTypes"' in spec
    assert '"CFBundleURLSchemes": ["webjam"]' in spec


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()

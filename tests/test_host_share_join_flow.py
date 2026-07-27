from __future__ import annotations

import json
import os
import sys
import time
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
from webjam_qt.windows.launch_dialog import apply_join_invite


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


def test_windows_clean_install_exposes_the_bundled_jamulus_installer(
    qapp,
    tmp_path,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_candidates=[],
    )
    with patch.object(sys, "platform", "win32"), patch(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
        return_value="C:/WebJam/_internal/Jamulus/jamulus_3.12.2_win.exe",
    ), patch(
        "services.bridge_service._is_pinned_jamulus_installer",
        return_value=True,
    ), patch("webjam_qt.windows.launch_dialog.subprocess.Popen") as popen:
        dialog = LaunchDialog(settings)
        dialog.show()
        qapp.processEvents()
        assert dialog._install_jamulus_button.isVisibleTo(dialog)
        dialog._install_jamulus_button.click()

    popen.assert_called_once_with(
        ["C:/WebJam/_internal/Jamulus/jamulus_3.12.2_win.exe"],
        shell=False,
    )
    assert not dialog._install_jamulus_button.isEnabled()
    assert "Finish the Jamulus installer" in dialog._choice_helper.text()
    dialog.close()


def test_windows_installer_button_stays_hidden_when_jamulus_is_installed(
    qapp,
    tmp_path,
):
    installed = tmp_path / "Jamulus.exe"
    installed.write_bytes(b"stub")
    settings = AppSettings(jamulus_candidates=[str(installed)])
    with patch.object(sys, "platform", "win32"), patch(
        "services.bridge_service._bundled_jamulus_installer",
        return_value="C:/WebJam/_internal/Jamulus/jamulus_3.12.2_win.exe",
    ):
        dialog = LaunchDialog(settings)
        dialog.show()
        qapp.processEvents()
    assert not dialog._install_jamulus_button.isVisibleTo(dialog)
    dialog.close()


def test_windows_installer_launch_failure_is_actionable(qapp, tmp_path):
    settings = AppSettings(jamulus_candidates=[])
    with patch.object(sys, "platform", "win32"), patch(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
        return_value="C:/WebJam/_internal/Jamulus/jamulus_3.12.2_win.exe",
    ), patch(
        "services.bridge_service._is_pinned_jamulus_installer",
        return_value=True,
    ), patch(
        "webjam_qt.windows.launch_dialog.subprocess.Popen",
        side_effect=OSError("blocked"),
    ):
        dialog = LaunchDialog(settings)
        dialog._install_jamulus_button.click()
    assert "couldn’t open" in dialog._choice_error.text()
    assert dialog._install_jamulus_button.isEnabled()


def test_windows_installer_replacement_is_rejected_before_launch(qapp, tmp_path):
    settings = AppSettings(jamulus_candidates=[])
    with patch.object(sys, "platform", "win32"), patch(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
        return_value="C:/WebJam/_internal/Jamulus/jamulus_3.12.2_win.exe",
    ), patch(
        "services.bridge_service._is_pinned_jamulus_installer",
        return_value=False,
    ), patch("webjam_qt.windows.launch_dialog.subprocess.Popen") as popen:
        dialog = LaunchDialog(settings)
        dialog._install_jamulus_button.click()

    popen.assert_not_called()
    assert "failed its integrity check" in dialog._choice_error.text()
    assert not dialog._install_jamulus_button.isEnabled()


def test_host_choice_persists_role_without_a_webjam_audio_form(qapp, tmp_path):
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
    visible_text = " ".join(
        child.text()
        for child in dialog.findChildren(QAbstractButton)
        if hasattr(child, "text")
    )
    assert "Band input" not in visible_text
    assert "Band output" not in visible_text


def test_host_choice_is_a_single_decision_without_a_modal_chain(qapp, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog._host()
    saved = json.loads(Path(settings.config_file).read_text(encoding="utf-8"))
    assert dialog.selected_role == "host"
    assert saved["host_server_enabled"] is True
    assert dialog.findChildren(QLineEdit) == [dialog._invite_input]


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


def test_join_preserves_explicit_local_original_recording_preference(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        audio_input_device_index=7,
    )
    apply_join_invite(
        settings,
        parse_invite_link(create_invite_link("192.168.1.42")),
    )
    assert settings.local_capture_enabled is True
    assert settings.audio_input_device_index == 7


def test_join_asks_for_one_link_then_starts_the_native_journey(qapp, tmp_path):
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
    requested: list[str] = []
    hud.invite_requested.connect(copied)
    hud.retry_requested.connect(retried)
    hud.action_requested.connect(requested.append)
    hud.set_state(
        "Ready to share",
        "Waiting for bandmates.",
        invite_available=True,
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
    assert requested == ["invite", "retry"]


def test_session_hud_primary_action_is_generic_and_accessible(qapp):
    hud = SessionHud()
    copied = MagicMock()
    retried = MagicMock()
    requested: list[str] = []
    hud.invite_requested.connect(copied)
    hud.retry_requested.connect(retried)
    hud.action_requested.connect(requested.append)

    hud.set_state(
        "Ready when you are",
        "Start when the band is ready.",
        action_text="Start & Invite",
        action_kind="primary",
    )

    assert not hud._action.isHidden()
    assert hud._action.text().replace("&&", "&") == "Start & Invite"
    assert hud._action.accessibleName() == "Start & Invite"
    assert "Start & Invite" in hud._action.accessibleDescription()
    hud._action.click()
    assert requested == ["primary"]
    copied.assert_not_called()
    retried.assert_not_called()


def test_host_invite_uses_hud_copy_action_only_after_real_server_readiness(
    qapp, tmp_path
):
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
    assert controller.window.session_hud._invite_available is False
    assert controller.window.session_hud._action.isHidden()
    controller.bridge.hosted_server_alive.return_value = True
    with patch.object(
        controller,
        "_current_invite_url",
        return_value=create_invite_link("192.168.1.42"),
    ):
        controller._update_session_hud()
    assert controller.window.session_hud._invite_available is True
    assert controller.window.session_hud._status.text() == "Invite ready"
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_hud._action.text() == "Copy Invite"
    # The conductor owns the one clear next step.  The older strip and stage
    # controls must not repeat the same Copy Invite action.
    assert controller.window.session_strip._invite_button.isHidden()
    assert controller.window.participant_grid._empty_primary.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_remote_host_copy_and_reset_stay_under_owned_progressive_disclosure(
    qapp, tmp_path
):
    from services.remote_invitation_owner import RemoteInvitationOwner
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    class Registrar:
        def __init__(self):
            self.registered = []
            self.revoked = []

        def register_invitation(self, invitation):
            self.registered.append(invitation)

        def revoke_invitation(self, invitation):
            self.revoked.append(invitation)

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Remote Host",
    )
    controller = ApplicationController(window, settings=settings)
    registrar = Registrar()
    owner = RemoteInvitationOwner(
        registrar,
        profile_id="reference-local",
        allowed_profiles=frozenset({"reference-local"}),
        host_spki_sha256=bytes.fromhex("44" * 32),
        clock=lambda: 1_800_000_000,
    )
    owner.start(session_reference=bytes.fromhex("11" * 16))
    controller._remote_invite_owner = owner
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller._current_invite_url = MagicMock(
        side_effect=AssertionError("remote host must not serialize a LAN invite")
    )

    controller._update_session_hud()
    controller._copy_band_invite()
    copied = QApplication.clipboard().text()

    assert copied.startswith("webjam://join?v=3")
    assert copied not in repr(vars(controller))
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_hud._action.text() == "Copy Invite"
    assert controller.window.session_strip._invite_button.isHidden()
    assert controller.window.session_strip._reset_invite_action.isVisible()
    assert "same Wi-Fi" not in controller.window.session_hud._detail.text()

    old = owner.invitation
    controller._reset_remote_invite()
    assert registrar.revoked == [old]
    assert owner.invitation is not old
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_peer_bind_failure_keeps_jamulus_invite_with_persistent_plain_warning(
    qapp,
    tmp_path,
):
    from core.session_transfer import SessionTransferError
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
        initial_title="Fallback Truth",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge._port_free = MagicMock(return_value=False)
    controller._jamulus_connected = True
    controller.host_peer.start = MagicMock(
        side_effect=SessionTransferError("address already in use")
    )

    with patch(
        "core.network_invite.local_band_address", return_value="192.168.1.42"
    ):
        controller._update_session_hud()
        invite = parse_invite_link(controller._current_invite_url())
        controller._update_session_hud()

    assert not invite.peer_enabled
    assert invite.host == "192.168.1.42"
    assert controller.window.session_hud._status.text() == (
        "Automatic Local Originals are off"
    )
    detail = controller.window.session_hud._detail.text()
    assert "can still join and play" in detail
    assert "record separately" in detail
    assert not controller.window.session_strip._invite_button.isHidden()
    assert controller.host_peer.start.call_count >= 1

    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_host_never_serializes_lan_invite_until_expected_udp_port_is_bound(
    qapp, tmp_path
):
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
        initial_title="Listener Gate",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    # `_port_free=True` means no listener owns the expected Jamulus UDP port.
    controller.bridge._port_free = MagicMock(return_value=True)

    with patch("core.network_invite.local_band_address", return_value="192.168.1.42"):
        assert controller._current_invite_url() == ""
        controller._update_session_hud()

    assert controller.window.session_hud._status.text() == "Preparing the invite"
    assert "verifying the host session" in controller.window.session_hud._detail.text()
    assert controller.window.session_hud._action.isHidden()
    assert controller.window.session_strip._invite_button.isHidden()
    assert controller.window.participant_grid._empty_primary.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_host_handoff_keeps_the_connect_to_wifi_recovery_action(qapp, tmp_path):
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
        initial_title="Network Recovery",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge._port_free = MagicMock(return_value=False)

    with patch("core.network_invite.local_band_address", return_value=""):
        controller._update_session_hud()

    assert controller.window.session_hud._status.text() == "Connect to Wi-Fi"
    assert controller.window.session_hud._action.text() == "Connect to Wi-Fi"
    assert not controller.window.session_hud._action.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_host_handoff_keeps_the_port_inspection_retry_action(qapp, tmp_path):
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
        initial_title="Port Recovery",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller.bridge._port_free = MagicMock(side_effect=OSError("probe unavailable"))

    with patch("core.network_invite.local_band_address", return_value="192.168.1.42"):
        controller._update_session_hud()

    assert controller.window.session_hud._status.text() == "Getting your jam ready"
    assert controller.window.session_hud._action.text() == "Try Again"
    assert not controller.window.session_hud._action.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_native_startup_handoff_uses_host_share_gate_before_showing_copy_invite(
    qapp, tmp_path
):
    """A proven local client is not enough to make a LAN invite shareable."""

    from core.session_conductor import SessionRole
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
        initial_title="Startup Handoff",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    # `_port_free=True` is the observable proof that nobody owns the expected
    # Jamulus UDP listener yet.  The host must not receive a Copy Invite UI.
    controller.bridge._port_free = MagicMock(return_value=True)
    controller._jamulus_connected = True
    controller.participants = {
        1: ParticipantPresentation(
            channel_id=1,
            name="You",
            role="Musician",
            is_local=True,
        )
    }
    token = controller._start_session_conductor_attempt(SessionRole.HOST)
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "conductor_token": token,
        "phase": "confirm_sound",
    }

    with patch("core.network_invite.local_band_address", return_value="192.168.1.42"):
        controller._show_startup_invite_ready(1)

    assert controller._startup_attempt is None
    assert controller.window.session_hud._status.text() == "Preparing the invite"
    assert controller.window.session_hud._action.isHidden()
    assert controller.window.session_strip._invite_button.isHidden()
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_host_calls_out_a_copied_lan_invite_after_wifi_address_changes(
    qapp, tmp_path
):
    from core.host_share_readiness import (
        HostShareReadiness,
        HostShareReadinessStatus,
    )
    from core.session_lifecycle import SessionLifecyclePhase
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
        initial_title="Network Change",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller._transition_lifecycle(SessionLifecyclePhase.STARTING_HOST)
    controller._transition_lifecycle(SessionLifecyclePhase.READY_TO_SHARE)
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    controller._last_shared_lan_address = "192.168.1.42"
    controller._host_share_readiness = MagicMock(
        return_value=HostShareReadiness(
            HostShareReadinessStatus.READY_PRIVATE_LAN, "192.168.1.43"
        )
    )
    controller._current_invite_url = MagicMock(
        return_value=create_invite_link("192.168.1.43")
    )

    with patch(
        "webjam_qt.platform_permissions.microphone_permission_status",
        return_value="granted",
    ):
        controller._update_session_hud()

    assert controller.window.session_hud._status.text() == "Your Wi-Fi changed"
    assert controller.window.session_hud._action.text() == "Copy New Invite"
    assert controller.window.session_hud._invite_available is True
    assert controller.session_lifecycle.phase is SessionLifecyclePhase.DEGRADED
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_copying_replacement_lan_invite_acknowledges_current_address(qapp, tmp_path):
    from core.host_share_readiness import (
        HostShareReadiness,
        HostShareReadinessStatus,
    )
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
        initial_title="Fresh Invite",
    )
    controller = ApplicationController(window, settings=settings)
    readiness = HostShareReadiness(
        HostShareReadinessStatus.READY_PRIVATE_LAN, "192.168.1.43"
    )
    controller._last_shared_lan_address = "192.168.1.42"
    controller._host_share_readiness = MagicMock(return_value=readiness)
    controller._current_invite_url = MagicMock(
        return_value=create_invite_link("192.168.1.43")
    )

    controller._copy_band_invite()

    assert controller._last_shared_lan_address == "192.168.1.43"
    assert not controller._lan_invite_needs_refresh(readiness)
    controller.bridge.hosted_server_alive = MagicMock(return_value=False)
    controller.shutdown()


def test_wake_gap_revalidates_live_connection_before_claiming_it_is_connected(
    qapp, tmp_path
):
    from core.session_lifecycle import SessionLifecyclePhase
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_server="192.168.1.42",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Wake Revalidation",
    )
    controller = ApplicationController(window, settings=settings)
    alive = MagicMock()
    alive.poll.return_value = None
    controller.bridge.jamulus_process = alive
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.attempt_auto_reconnects = MagicMock()
    controller._transition_lifecycle(SessionLifecyclePhase.JOINING)
    controller._transition_lifecycle(SessionLifecyclePhase.CONNECTED)
    controller._jamulus_connected = True
    controller._last_reconnect_tick_monotonic = (
        time.monotonic() - controller._WAKE_REVALIDATION_GAP_SECONDS - 1
    )

    controller._on_reconnect_tick()

    assert controller._jamulus_connected is False
    assert controller.audio.recovering is True
    assert controller.session_lifecycle.phase is SessionLifecyclePhase.RECONNECTING
    assert controller.window._status_audio.text() == "Audio: Checking connection…"
    controller.bridge.attempt_auto_reconnects.assert_called_once()
    controller.shutdown()


def test_peer_take_update_is_marshaled_into_open_studio(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "takes"),
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Late Transfer",
    )
    controller = ApplicationController(window, settings=settings)
    controller.window.recording_studio.refresh_take = MagicMock()
    controller.window.flash_message = MagicMock()
    take_dir = tmp_path / "takes" / "Take 01"

    controller._on_peer_take_updated("take-id", take_dir, True)
    qapp.processEvents()

    controller.window.recording_studio.refresh_take.assert_called_once_with(take_dir)
    assert "visible in Studio" in controller.window.flash_message.call_args.args[0]
    controller.shutdown()


def test_host_invite_is_ready_without_its_own_roster_entry(qapp, tmp_path):
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
        initial_title="Local Truth",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=True)
    # A reachable host may safely make an invite before this Mac's own
    # Jamulus client has appeared in the local roster.  That is invite-ready,
    # not a claim that a bandmate or live music path is connected.
    assert controller.participants == {}
    assert controller._jamulus_connected is False
    with patch.object(
        controller,
        "_current_invite_url",
        return_value=create_invite_link("192.168.1.42"),
    ):
        controller._update_session_hud()
    assert controller.window.session_hud._status.text() == "Invite ready"
    assert controller.window.session_hud._action.text() == "Copy Invite"
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_strip._invite_button.isHidden()
    assert "connected" not in controller.window.session_hud._status.text().lower()
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
    # Recovery has one clear next action in the HUD.  The passive stage must
    # not grow a second retry button with a competing label.
    assert controller.window.participant_grid._empty_primary.isHidden()
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_hud._action.text() == "Try Reconnect"
    assert controller.window.session_hud._status.text() == "Session needs attention"
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
    with patch.object(
        controller,
        "_current_invite_url",
        return_value=create_invite_link("192.168.1.42"),
    ):
        controller._apply_jamulus_participants([
            JamulusParticipant(channel_id=7, name="Guest", is_local=False)
        ])
    assert controller._jamulus_connected is False
    assert 7 in controller.participants
    assert controller._connection_timer.isActive()
    assert controller.window.session_hud._status.text() == "Joining the jam"
    assert controller.window.session_hud._action.isHidden()
    assert "Connected to the jam" not in controller.window.session_hud._status.text()

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
    controller.begin_startup_journey = MagicMock()
    stale_visible = True
    stale_dialog = MagicMock()
    stale_dialog.isVisible.side_effect = lambda: stale_visible
    stale_dialog._start_session_when_ready = True

    def close_stale_dialog() -> None:
        nonlocal stale_visible
        stale_visible = False

    stale_dialog.close.side_effect = close_stale_dialog
    controller._ready_check_dialog = stale_dialog
    old_generation = controller._settings_generation
    link = create_invite_link(
        "192.168.1.42", session_name="New Jam"
    )
    assert controller.accept_invite_url(link) is True
    assert controller.settings.jamulus_server == "192.168.1.42"
    assert controller.settings.host_server_enabled is False
    assert window.session_strip.current_title() == "New Jam"
    assert controller._settings_generation == old_generation + 1
    stale_dialog.close.assert_called_once_with()
    controller.begin_startup_journey.assert_called_once()
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
    def stop_hosted_server() -> bool:
        events.append("server-stop")
        controller.bridge.hosted_server_alive.return_value = False
        return True

    controller.bridge.stop_hosted_server = MagicMock(
        side_effect=stop_hosted_server
    )
    controller.begin_startup_journey = MagicMock(
        side_effect=lambda: events.append("new-join-start")
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
        "new-join-start",
    ]
    assert controller.settings.host_server_enabled is False
    controller.bridge.hosted_server_alive.return_value = False
    controller.shutdown()


def test_running_invite_switch_save_failure_returns_to_recoverable_ui(
    qapp,
    tmp_path,
):
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
        initial_title="Old Join Jam",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=False)
    controller.bridge.hosted_server_owned = MagicMock(return_value=False)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller.begin_startup_journey = MagicMock()
    link = create_invite_link("192.168.1.42", session_name="New Join Jam")

    with (
        patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ),
        patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
        ),
        patch.object(
            controller._ui_invoker,
            "invoke",
            side_effect=lambda callback: callback(),
        ),
        patch("core.settings.save_settings", side_effect=OSError("read only")),
    ):
        # The invitation was accepted for asynchronous switching even though
        # its later settings write could not complete.
        assert controller.accept_invite_url(link) is True

    assert controller._invite_switch_in_flight is False
    assert controller.audio.stopping is False
    assert controller.audio.cleanup_retry_required is False
    assert window.session_strip._tools_button.isEnabled()
    assert window.session_strip._audio_button.text() == "Start Session"
    assert not window.session_strip._audio_button.isEnabled()
    controller.begin_startup_journey.assert_not_called()
    assert "did not finish" in window.statusBar().currentMessage()

    controller.bridge.jamulus_state = "Stopped"
    controller.shutdown()


def test_running_invite_switch_is_single_flight(qapp, tmp_path):
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_server="192.168.1.10",
    )
    save_settings(settings)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Current Jam",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = MagicMock(return_value=False)
    controller.bridge.hosted_server_owned = MagicMock(return_value=False)
    first = create_invite_link("192.168.1.42", session_name="First New Jam")
    second = create_invite_link("192.168.1.43", session_name="Second New Jam")

    with (
        patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question,
        patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            side_effect=lambda *args, **kwargs: _DeferredThread(*args, **kwargs),
        ),
        patch.object(window, "flash_message") as flash,
    ):
        assert controller.accept_invite_url(first) is True
        assert controller.accept_invite_url(second) is False

    assert controller._invite_switch_in_flight is True
    assert controller.audio.stopping is True
    assert question.call_count == 1
    assert "still ending, leaving, or switching" in flash.call_args.args[0]

    # The worker is deliberately suspended in this regression; restore the
    # fixture to a settled state before exercising normal shutdown.
    controller._invite_switch_in_flight = False
    controller.audio.stopping = False
    controller.bridge.jamulus_state = "Stopped"
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


def test_returning_user_gets_host_join_gate_then_native_startup_journey(qapp):
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
    launcher_class.assert_called_once_with(initial, initial_invitation=None)
    single_shot.assert_called_once_with(
        0, controller.begin_startup_journey
    )


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
    launcher_class.assert_called_once_with(
        initial,
        initial_invitation=parse_invite_link(link),
    )


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


class _DeferredThread:
    def __init__(self, *args, target=None, **kwargs):
        self._target = target

    def start(self):
        pass

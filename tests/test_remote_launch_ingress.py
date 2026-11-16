from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QTimer, QUrl
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from core.network_invite import create_invite_link
from core.remote_invitation import RemoteInvitation, issue_remote_invitation
from core.settings import AppSettings
from webjam_qt.app import (
    WebJamApplication,
    _invite_from_arguments,
    _show_live_invitation_error,
    qt_arguments_without_test_night,
)
from webjam_qt.startup_dialog import exec_startup_dialog
from webjam_qt.windows.launch_dialog import LaunchDialog


PROFILE = "reference-local"
ALLOWED = frozenset({PROFILE})


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _issued():
    return issue_remote_invitation(
        PROFILE,
        allowed_profiles=ALLOWED,
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=1_800_000_000,
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    )


def _current_issued():
    return issue_remote_invitation(
        PROFILE,
        allowed_profiles=ALLOWED,
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=int(time.time()),
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    )


def test_remote_paste_clears_field_and_persists_no_invitation_material(
    qapp, tmp_path: Path
) -> None:
    issued = _issued()
    raw = issued.private_link.reveal_for_clipboard()
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog._invite_input.setText(raw)

    dialog._join()

    assert dialog.selected_role == "join"
    assert dialog.band_invite is None
    assert dialog.remote_invitation is not None
    assert dialog.remote_invitation.session_reference == issued.invitation.session_reference
    assert dialog._invite_input.text() == ""
    persisted = Path(settings.config_file).read_text(encoding="utf-8")
    data = json.loads(persisted)
    for forbidden in (
        raw,
        PROFILE,
        issued.invitation.capability_for_enrollment().hex(),
        issued.invitation.host_spki_sha256.hex(),
    ):
        assert forbidden not in persisted
    assert data["host_server_enabled"] is False
    assert data["jamulus_server"] == "127.0.0.1"
    assert data["jamulus_port"] == 22124
    moved = dialog.take_remote_invitation()
    assert moved is not None
    assert dialog.remote_invitation is None
    assert dialog.take_remote_invitation() is None
    dialog.deleteLater()
    qapp.processEvents()


def test_malformed_v3_is_never_restored_to_the_join_field(qapp, tmp_path: Path) -> None:
    raw = "webjam://join?v=3&r=reference-local&i=PRIVATE-SENTINEL"
    dialog = LaunchDialog(
        AppSettings(config_file=str(tmp_path / "settings.json"))
    )
    dialog._invite_input.setText(raw)

    assert dialog.accept_invite(raw) is False

    assert dialog._invite_input.text() == ""
    assert "PRIVATE-SENTINEL" not in dialog._join_error.text()
    dialog.reject()
    assert dialog._invite_input.text() == ""
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("role", ["", "join"])
def test_native_acceptance_without_confirmed_choice_keeps_launcher_open(
    qapp, tmp_path: Path, role: str,
) -> None:
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.show_join()
    dialog._invite_input.setText("webjam://join?v=1")
    dialog.selected_role = role
    dialog.accept()
    assert accepted == []
    assert dialog.result() == LaunchDialog.DialogCode.Rejected
    assert dialog.selected_role == ""
    assert dialog._invite_input.text() == ""
    assert not Path(settings.config_file).exists()
    assert not dialog._submitting
    dialog.reject()
    dialog.deleteLater()
    qapp.processEvents()


def test_malformed_invitation_button_never_accepts_join(qapp, tmp_path: Path) -> None:
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.show_join()
    dialog._invite_input.setText("webjam://join?v=1")
    dialog._join_button_primary.click()
    assert accepted == []
    assert dialog.selected_role == ""
    assert not Path(settings.config_file).exists()
    dialog.reject()
    dialog.deleteLater()
    qapp.processEvents()


def test_legacy_hostname_paste_remains_an_explicit_typed_join(qapp, tmp_path: Path) -> None:
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog._invite_input.setText("legacy-rehearsal.example.test:22124")
    dialog._join_button_primary.click()
    assert dialog.result() == LaunchDialog.DialogCode.Accepted
    assert dialog.selected_role == "join"
    assert dialog.band_invite.host == "legacy-rehearsal.example.test"
    assert dialog.remote_invitation is None
    dialog.deleteLater()
    qapp.processEvents()


def test_bearer_process_arguments_are_ignored_before_launch_dialog() -> None:
    remote = _issued().private_link.reveal_for_clipboard()
    private = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )
    legacy = create_invite_link("192.168.1.43")

    assert _invite_from_arguments(["WebJam", remote]) is None
    assert _invite_from_arguments(["WebJam", private]) is None
    parsed = _invite_from_arguments(["WebJam", private, legacy])
    assert parsed is not None and parsed.version == 1


def test_qt_arguments_drop_all_webjam_urls_without_mutating_source() -> None:
    remote = _issued().private_link.reveal_for_clipboard()
    private = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )
    legacy = create_invite_link("192.168.1.43")
    raw = (
        "WebJam",
        "--style=Fusion",
        legacy,
        private,
        remote,
        f"--handoff={private}",
    )

    qt_arguments = qt_arguments_without_test_night(raw)

    assert qt_arguments == ["WebJam", "--style=Fusion"]
    assert raw == (
        "WebJam",
        "--style=Fusion",
        legacy,
        private,
        remote,
        f"--handoff={private}",
    )
    assert all("webjam://" not in argument.lower() for argument in qt_arguments)


def test_macos_file_open_is_parsed_immediately_and_only_typed_state_remains() -> None:
    raw = _issued().private_link.reveal_for_clipboard()
    fake_application = SimpleNamespace(
        _pending_invitation=None,
        _pending_invitation_error="",
        invitation_received=MagicMock(),
        invitation_error=MagicMock(),
    )
    event = SimpleNamespace(
        type=lambda: QEvent.Type.FileOpen,
        url=lambda: SimpleNamespace(toString=lambda: raw),
    )

    with patch("webjam_qt.app.sys.platform", "darwin"):
        assert WebJamApplication.event(fake_application, event) is True

    invitation = fake_application._pending_invitation
    assert isinstance(invitation, RemoteInvitation)
    fake_application.invitation_received.emit.assert_called_once_with(invitation)
    assert raw not in repr(vars(fake_application))
    assert WebJamApplication.take_pending_invitation(fake_application) is invitation
    assert fake_application._pending_invitation is None


def test_invalid_file_open_retains_only_fixed_error_copy() -> None:
    raw = "webjam://join?v=3&r=reference-local&i=PRIVATE-SENTINEL"
    fake_application = SimpleNamespace(
        _pending_invitation=None,
        _pending_invitation_error="",
        invitation_received=MagicMock(),
        invitation_error=MagicMock(),
    )
    event = SimpleNamespace(
        type=lambda: QEvent.Type.FileOpen,
        url=lambda: SimpleNamespace(toString=lambda: raw),
    )

    with patch("webjam_qt.app.sys.platform", "darwin"):
        assert WebJamApplication.event(fake_application, event) is True

    assert fake_application._pending_invitation is None
    assert "PRIVATE-SENTINEL" not in fake_application._pending_invitation_error
    assert raw not in repr(vars(fake_application))
    fake_application.invitation_error.emit.assert_called_once()


def test_private_music_url_routes_only_through_the_beta_file_open_event() -> None:
    request = "a" * 32
    url = f"webjam-beta://hangout-music/{request}"
    app = SimpleNamespace(
        _startup_music_host_guidance=None,
        activeModalWidget=lambda: None,
        topLevelWidgets=lambda: [],
        hangout_music_requested=MagicMock(),
    )

    assert WebJamApplication.event(app, QFileOpenEvent(QUrl(url))) is True
    app.hangout_music_requested.emit.assert_called_once_with(request)
    assert WebJamApplication.event(app, QFileOpenEvent(QUrl(f"{url}?redirect=bad"))) is True
    app.hangout_music_requested.emit.assert_called_once()
    assert qt_arguments_without_test_night(["WebJam", url]) == ["WebJam"]


def test_cold_music_request_retains_only_guidance_until_the_launcher_opens() -> None:
    request = "b" * 32
    url = f"webjam-beta://hangout-music/{request}"
    app = SimpleNamespace(
        _startup_music_host_guidance=False,
        hangout_music_requested=MagicMock(),
    )

    assert WebJamApplication.event(app, QFileOpenEvent(QUrl(url))) is True
    assert WebJamApplication.take_startup_music_host_guidance(app) is True
    assert WebJamApplication.take_startup_music_host_guidance(app) is False
    app.hangout_music_requested.emit.assert_not_called()
    assert request not in repr(vars(app))


def test_music_request_reaches_the_nonmodal_mac_startup_dialog(qapp, tmp_path) -> None:
    app = SimpleNamespace(
        _startup_music_host_guidance=None,
        activeModalWidget=qapp.activeModalWidget,
        topLevelWidgets=qapp.topLevelWidgets,
        hangout_music_requested=MagicMock(),
    )
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    observed = []

    def receive_from_browser():
        try:
            dialog.show_join()
            dialog._invite_input.setText("existing draft")
            url = QUrl(f"webjam-beta://hangout-music/{'c' * 32}")
            WebJamApplication.event(app, QFileOpenEvent(url))
            observed.append((
                qapp.activeModalWidget(),
                dialog._music_host_guidance.isVisible(),
                dialog._invite_input.text(),
                dialog.selected_role,
            ))
        finally:
            dialog.reject()

    QTimer.singleShot(0, dialog, receive_from_browser)
    with patch("sys.platform", "darwin"), patch.object(QApplication, "platformName", return_value="cocoa"):
        exec_startup_dialog(qapp, dialog)

    assert observed == [(None, True, "existing draft", "")]
    assert not Path(settings.config_file).exists()
    app.hangout_music_requested.emit.assert_not_called()


def test_music_request_on_the_launcher_keeps_the_existing_draft(qapp, tmp_path) -> None:
    dialog = LaunchDialog(AppSettings(config_file=str(tmp_path / "settings.json")))
    app = SimpleNamespace(
        _startup_music_host_guidance=None,
        activeModalWidget=lambda: dialog,
        hangout_music_requested=MagicMock(),
    )
    try:
        dialog.show()
        dialog.show_join()
        dialog._invite_input.setText("existing draft")
        url = QUrl(f"webjam-beta://hangout-music/{'c' * 32}")
        assert WebJamApplication.event(app, QFileOpenEvent(url)) is True
        assert dialog._music_host_guidance.isVisible()
        assert "Music" in dialog._music_host_guidance.text()
        assert "Hang Out" in dialog._music_host_guidance.text()
        assert dialog._pages.currentWidget() is dialog._join_page
        assert dialog._invite_input.text() == "existing draft"
        assert not Path(dialog._settings.config_file).exists()
        app.hangout_music_requested.emit.assert_not_called()
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


def test_live_invitation_error_uses_keyword_only_flash_duration() -> None:
    window = SimpleNamespace(flash_message=MagicMock())

    _show_live_invitation_error(window, "That invitation is no longer valid.")

    window.flash_message.assert_called_once_with(
        "That invitation is no longer valid.",
        ms=5_000,
    )


def test_run_ignores_v3_argv_instead_of_passing_raw_text(qapp) -> None:
    from webjam_qt import app as app_module

    raw = _issued().private_link.reveal_for_clipboard()
    initial = AppSettings(config_file="/missing.json")
    saved = AppSettings(config_file="/saved.json")
    launcher = MagicMock()
    launcher.exec.return_value = LaunchDialog.DialogCode.Accepted
    launcher.selected_role = "host"
    launcher.session_name = "Band Rehearsal"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    with patch.object(app_module.sys, "argv", ["WebJam", raw]), patch.object(
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
    ), patch.object(app_module.QTimer, "singleShot"), patch.dict(
        os.environ, {}, clear=False
    ):
        os.environ.pop("WEBJAM_SMOKE_AUTOSTART_AUDIO", None)
        assert app_module.run() == 0

    launcher_class.assert_called_once_with(initial, initial_invitation=None)
    assert raw not in repr(launcher_class.call_args)


def test_controller_accepts_typed_remote_invite_without_legacy_launch(
    qapp, tmp_path: Path
) -> None:
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    invitation = _current_issued().invitation
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Remote Join",
    )
    controller = ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            jamulus_server="127.0.0.1",
        ),
    )
    controller._begin_remote_join = MagicMock()
    controller.bridge.launch_jamulus = MagicMock()

    assert controller.accept_invitation(invitation) is True
    assert controller._remote_invitation is invitation
    controller._begin_remote_join.assert_called_once_with()
    controller.bridge.launch_jamulus.assert_not_called()
    assert window.session_hud._status.text() == "Preparing your jam"
    assert invitation.capability_for_enrollment().hex() not in repr(vars(controller))
    controller.shutdown()


def test_controller_rejects_expired_remote_invite_before_transport(
    qapp, tmp_path: Path
) -> None:
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    invitation = issue_remote_invitation(
        PROFILE,
        allowed_profiles=ALLOWED,
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=1,
        ttl_seconds=1,
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    ).invitation
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Expired Join",
    )
    controller = ApplicationController(
        window,
        settings=AppSettings(config_file=str(tmp_path / "settings.json")),
    )
    controller._begin_remote_join = MagicMock()
    controller.window.flash_message = MagicMock()

    assert controller.accept_invitation(invitation) is False
    controller._begin_remote_join.assert_not_called()
    assert "expired" in controller.window.flash_message.call_args.args[0].lower()
    controller.shutdown()

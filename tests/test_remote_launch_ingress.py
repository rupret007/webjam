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

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from core.remote_invitation import RemoteInvitation, issue_remote_invitation
from core.settings import AppSettings, save_settings
from webjam_qt.app import WebJamApplication, _invite_from_arguments
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

    def confirm(candidate: AppSettings, **_kwargs) -> bool:
        save_settings(candidate)
        return True

    with patch.object(dialog, "_confirm_sound_setup", side_effect=confirm):
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


def test_v3_process_argument_is_ignored_before_launch_dialog() -> None:
    raw = _issued().private_link.reveal_for_clipboard()
    assert _invite_from_arguments(["WebJam", raw]) is None


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

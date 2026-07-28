from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.windows.first_run_setup import FirstRunSetupDialog
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def settings(tmp_path):
    return AppSettings(
        config_file=str(tmp_path / "config.json"),
        jamulus_candidates=[],
        musician_name="WebJam Musician",
    )


def make_dialog(settings, *, client="/bundle/Jamulus", server="/bundle/Server"):
    with patch.object(sys, "platform", "darwin"), patch(
        "services.bridge_service._bundled_jamulus_candidate", return_value=client,
    ), patch(
        "services.bridge_service._bundled_jamulus_server_candidate",
        return_value=server,
    ), patch.object(Path, "is_file", return_value=False):
        return FirstRunSetupDialog(settings)


def choose_host(dialog):
    dialog._host_card.click()
    dialog._name.setText("Jeff — Guitar")


def choose_join(dialog):
    dialog._join_card.click()
    dialog._name.setText("Sam — Drums")
    dialog._server_address.setText("198.51.100.23:22124")


def test_requires_explicit_role_and_name(qapp, settings):
    dialog = make_dialog(settings)
    assert not dialog._primary.isEnabled()
    dialog._host_card.click()
    assert not dialog._primary.isEnabled()
    dialog._name.setText("Jeff — Guitar")
    assert dialog._primary.isEnabled()


def test_role_cards_are_keyboard_accessible(qapp, settings):
    dialog = make_dialog(settings)
    dialog._host_card.setFocus()
    QTest.keyClick(dialog._host_card, Qt.Key.Key_Space)
    assert dialog._host_card.isChecked()
    assert "Host" in dialog._host_card.accessibleName()
    assert dialog._host_card.accessibleDescription()
    dialog._join_card.setFocus()
    QTest.keyClick(dialog._join_card, Qt.Key.Key_Space)
    assert dialog._join_card.isChecked()
    assert not dialog._host_card.isChecked()


def test_host_hides_address_and_reports_components(qapp, settings):
    dialog = make_dialog(settings)
    dialog._host_card.click()
    assert dialog._server_address.isHidden()
    assert "nothing to install" in dialog._component_status.text()


def test_join_shows_single_server_address(qapp, settings):
    dialog = make_dialog(settings)
    dialog._join_card.click()
    assert not dialog._server_address.isHidden()
    assert dialog._server_address.accessibleName() == "Band server address"
    assert not dialog._primary.isEnabled()
    dialog._name.setText("Sam — Drums")
    dialog._server_address.setText("https://wrong.example.com")
    assert not dialog._primary.isEnabled()
    dialog._role_page_valid(show_error=True)
    assert "not a URL" in dialog._server_error.text()


def test_host_is_unavailable_off_macos(qapp, settings):
    with patch.object(sys, "platform", "win32"), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/bundle/Jamulus",
    ), patch.object(Path, "is_file", return_value=False):
        dialog = FirstRunSetupDialog(settings)
    assert not dialog._host_card.isEnabled()
    assert "macOS" in dialog._host_card.description()


def test_missing_server_blocks_host(qapp, settings):
    dialog = make_dialog(settings, server=None)
    choose_host(dialog)
    assert not dialog._primary.isEnabled()
    assert "missing" in dialog._component_status.text().lower()


def test_second_step_is_compact_and_defaults_to_talkback(qapp, settings):
    dialog = make_dialog(settings)
    choose_host(dialog)
    dialog._primary.click()
    assert dialog._step == 1
    assert dialog._progress.text() == "2 of 2"
    assert dialog._primary.text() == "Finish Setup"
    assert "JAMULUS" in dialog.findChild(type(dialog._title), "FirstRunSignalFlow").text()
    assert not dialog._capture.isChecked()
    assert dialog._device.isHidden()


def test_supplemental_capture_is_removed_from_simple_setup(qapp, settings):
    dialog = make_dialog(settings)
    choose_host(dialog)
    dialog._primary.click()
    assert dialog._capture.isHidden()
    assert dialog._device.isHidden()
    assert not dialog._capture.isChecked()


def test_optional_video_can_be_left_blank(qapp, settings):
    dialog = make_dialog(settings)
    choose_host(dialog)
    dialog._primary.click()
    assert dialog._webex_url.text() == ""
    assert dialog._primary.isEnabled()
    assert dialog._webex_error.isHidden()


def test_host_save_is_atomic_private_and_derives_defaults(qapp, settings):
    dialog = make_dialog(settings)
    choose_host(dialog)
    dialog._primary.click()
    dialog._webex_url.setText("cisco.webex.com/meet/bandroom")
    dialog._primary.click()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["host_server_enabled"] is True
    assert data["jamulus_server"] == "127.0.0.1"
    assert data["jamulus_port"] == 22124
    assert data["jamulus_rpc_port"] == 22222
    assert data["server_rpc_port"] == 22240
    assert "webex_audio_mode" not in data
    assert data["local_capture_enabled"] is False
    assert "JamulusServer" in data["server_rpc_secret_file"]
    assert "/bundle/Jamulus" not in data["jamulus_candidates"]
    assert stat.S_IMODE(Path(settings.config_file).stat().st_mode) == 0o600


def test_join_save_parses_optional_port(qapp, settings):
    dialog = make_dialog(settings)
    choose_join(dialog)
    dialog._primary.click()
    dialog._webex_url.setText("https://company.webex.com/meet/bandroom")
    dialog._primary.click()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["host_server_enabled"] is False
    assert data["jamulus_server"] == "198.51.100.23"
    assert data["jamulus_port"] == 22124


def test_save_failure_stays_open_with_actionable_error(qapp, settings):
    dialog = make_dialog(settings)
    choose_host(dialog)
    dialog._primary.click()
    dialog._webex_url.setText("https://company.webex.com/meet/bandroom")
    with patch("core.file_io.atomic_write_text", side_effect=OSError("read only")):
        dialog._primary.click()
    assert dialog.result is None
    assert "couldn't save" in dialog._webex_error.text()
    assert not dialog._webex_error.isHidden()
    assert not Path(settings.config_file).exists()


def test_cancel_creates_no_configuration(qapp, settings):
    dialog = make_dialog(settings)
    dialog.reject()
    assert not Path(settings.config_file).exists()


@pytest.mark.parametrize("size", [(560, 520), (680, 560), (900, 700)])
def test_geometry_has_no_clipping_at_supported_sizes(qapp, settings, size):
    dialog = make_dialog(settings)
    font = QFont(dialog.font())
    font.setPointSize(font.pointSize() + 2)
    dialog.setFont(font)
    dialog.resize(*size)
    dialog.show()
    choose_host(dialog)
    qapp.processEvents()
    assert dialog.width() >= 560
    assert dialog.height() >= 520
    assert dialog._primary.geometry().bottom() <= dialog.contentsRect().bottom()
    assert dialog._host_card.geometry().right() <= dialog._pages.width()
    dialog._primary.click()
    qapp.processEvents()
    assert dialog._device.isHidden()
    assert not dialog.grab().isNull()
    dialog.close()


def test_startup_always_asks_host_or_join_then_opens_native_journey(qapp):
    from webjam_qt import app as app_module

    initial = AppSettings(config_file="/missing/config.json")
    saved = AppSettings(config_file="/saved/config.json")
    launcher = MagicMock()
    launcher.exec.return_value = SimpleSettingsDialog.DialogCode.Accepted
    launcher.selected_role = "host"
    launcher.session_name = "Band Rehearsal"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    window = MagicMock()
    with patch.object(app_module, "load_settings", side_effect=[initial, saved]), \
         patch.object(
             app_module, "LaunchDialog", return_value=launcher,
         ) as launcher_class, patch.object(
             app_module.QApplication, "instance", return_value=qt_app,
         ), patch.object(app_module, "load_stylesheet", return_value=""), \
         patch.object(app_module, "ConductorWindow", return_value=window), \
         patch.object(
             app_module, "ApplicationController", return_value=controller,
         ), patch.object(app_module.QTimer, "singleShot") as single_shot:
        assert app_module.run() == 0
    launcher_class.assert_called_once_with(initial, initial_invitation=None)
    qt_app.aboutToQuit.connect.assert_called_once_with(controller.shutdown)
    single_shot.assert_called_once_with(
        0, controller.begin_startup_journey
    )


def test_packaged_smoke_hook_schedules_real_audio_start_and_bounded_quit(qapp):
    from webjam_qt import app as app_module

    settings = AppSettings(config_file="/configured.json")
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    window = MagicMock()
    with patch.dict(os.environ, {
             "WEBJAM_SMOKE_AUTOSTART_AUDIO": "1",
             "WEBJAM_SMOKE_EXIT_MS": "15000",
         }), \
         patch.object(app_module, "load_settings", return_value=settings), \
         patch.object(Path, "exists", return_value=True), \
         patch.object(app_module, "LaunchDialog") as launcher_class, \
         patch.object(app_module.QApplication, "instance", return_value=qt_app), \
         patch.object(app_module, "load_stylesheet", return_value=""), \
         patch.object(app_module, "ConductorWindow", return_value=window), \
         patch.object(
             app_module, "ApplicationController", return_value=controller,
         ), patch.object(app_module.QTimer, "singleShot") as single_shot:
        assert app_module.run() == 0
    launcher_class.assert_not_called()
    qt_app.aboutToQuit.connect.assert_called_once_with(controller.shutdown)
    assert len(single_shot.call_args_list) == 2
    assert single_shot.call_args_list[0] == call(0, controller._on_launch_audio)
    assert single_shot.call_args_list[1].args[0] == 15000
    single_shot.call_args_list[1].args[1]()
    assert window.confirm_close() is True
    window.close.assert_called_once_with()
    qt_app.quit.assert_not_called()


def test_frozen_launch_only_smoke_closes_real_role_dialog_cleanly(qapp):
    from webjam_qt import app as app_module

    settings = AppSettings(config_file="/configured.json")
    launcher = MagicMock()
    dialog_code = app_module.LaunchDialog.DialogCode
    launcher.exec.return_value = dialog_code.Rejected
    with patch.dict(os.environ, {
             "WEBJAM_SMOKE_LAUNCH_ONLY": "1",
             "WEBJAM_SMOKE_EXIT_MS": "4000",
         }), patch.object(
             app_module.sys, "frozen", True, create=True,
         ), patch.object(
             app_module, "load_settings", return_value=settings,
         ), patch.object(
             app_module, "LaunchDialog", return_value=launcher,
         ) as launcher_class, patch.object(
             app_module.QTimer, "singleShot",
         ) as single_shot:
        launcher_class.DialogCode = dialog_code
        assert app_module.run() == 0

    single_shot.assert_called_once_with(4000, launcher.reject)
    launcher.exec.assert_called_once_with()


def test_frozen_pocket_stage_runtime_smoke_bypasses_interactive_qt(qapp, tmp_path):
    from webjam_qt import app as app_module

    with (
        patch.object(app_module.sys, "frozen", True, create=True),
        patch.dict(
            os.environ,
            {
                "WEBJAM_SMOKE_POCKET_STAGE_RUNTIME": "1",
                "WEBJAM_SMOKE_POCKET_STAGE_RESULT": str(tmp_path / "result.txt"),
            },
            clear=False,
        ),
        patch(
            "services.pocket_stage_packaged_smoke.run_frozen_pocket_stage_smoke",
            return_value=0,
        ) as smoke,
        patch.object(app_module, "_run_app") as interactive,
    ):
        assert app_module.run() == 0

    smoke.assert_called_once_with(result_path=tmp_path / "result.txt")
    interactive.assert_not_called()

"""Musician-facing Webex settings remain external-only and truthful."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

from core.jamulus_name import JAMULUS_NAME_HELP  # noqa: E402
from core.settings import AppSettings, load_settings  # noqa: E402
from webjam_qt.windows.simple_settings import SimpleSettingsDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _dialog(tmp_path, *, opener):
    return SimpleSettingsDialog(
        AppSettings(config_file=str(tmp_path / "settings.json")),
        webex_opener=opener,
    )


def test_settings_names_meeting_or_personal_room_and_derives_site(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog._conversation_toggle.setChecked(True)
    dialog._video.setText(
        "https://team.webex.com/meet/private-room?token=private"
    )

    labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Meeting link (Webex, Zoom, Teams, Meet, or FaceTime)" in labels
    assert dialog._video_site.text() == "Webex site: team.webex.com"
    assert "private-room" not in dialog._video_site.text()
    assert "private" not in dialog._video_site.text()


def test_settings_opens_on_named_identity_field_not_structural_scroll_area(
    tmp_path,
):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog.show()
    _app.processEvents()
    try:
        assert dialog._settings_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert dialog._name.hasFocus()
        assert dialog._name.accessibleName() == "Your musician name"
    finally:
        dialog.close()


def test_settings_offers_explicit_official_webex_installer_handoff(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    requested: list[bool] = []
    dialog.install_webex_requested.connect(lambda: requested.append(True))
    dialog._conversation_toggle.setChecked(True)

    dialog._get_webex.click()

    assert requested == [True]
    assert dialog._get_webex.text() == "Get Webex from Cisco"
    assert "does not" in dialog._get_webex.accessibleDescription()


def test_open_action_normalizes_and_reports_external_handoff(tmp_path):
    opened: list[str] = []
    dialog = _dialog(tmp_path, opener=lambda url: opened.append(url) or True)
    dialog._video.setText("team.webex.com/meet/bandroom")

    with patch(
        "webjam_qt.windows.simple_settings.QAccessible.updateAccessibility"
    ) as announce:
        dialog._open_webex.click()

    assert opened == ["https://team.webex.com/meet/bandroom"]
    assert dialog._webex_status.text() == (
        "Opened externally—finish joining in Webex. Choose Save to keep "
        "this link in WebJam."
    )
    assert "joined" not in dialog._webex_status.text().lower()
    assert dialog._webex_status.accessibleDescription() == (
        dialog._webex_status.text()
    )
    announce.assert_called_once()


def test_invalid_link_never_reaches_external_opener(tmp_path):
    opened: list[str] = []
    dialog = _dialog(tmp_path, opener=lambda url: opened.append(url) or True)
    dialog.show()
    _app.processEvents()
    dialog._video.setText("http://example.test/private")

    with patch(
        "webjam_qt.windows.simple_settings.QAccessible.updateAccessibility"
    ) as announce:
        dialog._open_webex.click()

    assert opened == []
    assert "Check this link before opening" in dialog._webex_status.text()
    assert dialog._webex_status.accessibleDescription() == (
        dialog._webex_status.text()
    )
    assert dialog._video.hasFocus()
    announce.assert_called_once()
    dialog.close()


def test_invalid_save_announces_current_error_and_focuses_webex_field(
    tmp_path,
):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog.show()
    _app.processEvents()
    dialog._conversation_toggle.setChecked(True)
    dialog._video.setText("http://example.test/private")

    with patch(
        "webjam_qt.windows.simple_settings.QAccessible.updateAccessibility"
    ) as announce:
        assert dialog._save() is False

    assert dialog._error.isVisibleTo(dialog)
    assert dialog._error.accessibleDescription() == dialog._error.text()
    assert dialog._video.hasFocus()
    announce.assert_called_once()

    dialog._video.setText("https://team.webex.com/meet/bandroom")
    assert dialog._error.isHidden()
    assert dialog._error.accessibleDescription() == ""
    dialog.close()


def test_editing_link_clears_stale_accessible_test_result(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog._video.setText("team.webex.com/meet/bandroom")
    dialog._open_webex.click()
    assert dialog._webex_status.accessibleDescription()

    dialog._video.setText("team.webex.com/meet/new-room")

    assert dialog._webex_status.isHidden()
    assert dialog._webex_status.text() == ""
    assert dialog._webex_status.accessibleDescription() == ""


def test_opener_failure_log_never_contains_room_or_exception_text(
    tmp_path, caplog
):
    def fail(_url: str) -> bool:
        raise RuntimeError("private-room token=private")

    dialog = _dialog(tmp_path, opener=fail)
    dialog._video.setText(
        "https://team.webex.com/meet/private-room?token=private"
    )

    dialog._open_webex.click()

    output = caplog.text
    assert "RuntimeError" in output
    assert "private-room" not in output
    assert "token=private" not in output
    assert "could not be opened" in dialog._webex_status.text()


def test_open_failure_is_announced_and_keeps_retry_action_focused(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: False)
    dialog._conversation_toggle.setChecked(True)
    dialog._video.setText("team.webex.com/meet/bandroom")
    dialog.show()
    _app.processEvents()
    dialog._open_webex.setFocus()

    with patch(
        "webjam_qt.windows.simple_settings.QAccessible.updateAccessibility"
    ) as announce:
        dialog._open_webex.click()

    assert "could not be opened" in dialog._webex_status.text()
    assert (
        dialog._webex_status.accessibleDescription()
        == dialog._webex_status.text()
    )
    assert dialog._open_webex.hasFocus()
    announce.assert_called_once()
    dialog.close()


def test_save_failure_is_announced_without_leaking_or_moving_from_retry_action(
    tmp_path,
    caplog,
):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog.show()
    _app.processEvents()
    save = next(
        button
        for button in dialog.findChildren(type(dialog._open_webex))
        if button.text() == "Save"
    )
    save.setFocus()

    with (
        patch(
            "webjam_qt.windows.simple_settings.save_settings",
            side_effect=OSError(
                "/Users/private/WebJam/settings.json contained secret-token"
            ),
        ),
        patch(
            "webjam_qt.windows.simple_settings.QAccessible.updateAccessibility"
        ) as announce,
    ):
        save.click()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert "couldn't save" in dialog._error.text()
    assert dialog._error.accessibleDescription() == dialog._error.text()
    assert save.hasFocus()
    announce.assert_called_once()
    assert "OSError" in caplog.text
    assert "/Users/private" not in caplog.text
    assert "secret-token" not in caplog.text
    dialog.close()


def test_settings_store_no_webex_identity_or_password_fields(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    fields = dialog.findChildren(QLineEdit)

    assert fields == [dialog._name, dialog._video]
    assert all(
        field.echoMode() is QLineEdit.EchoMode.Normal
        for field in fields
    )
    copy = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Webex handles sign-in" in copy
    assert "does not change your Webex identity" in copy


def test_settings_name_preview_and_validation_share_jamulus_contract(
    tmp_path,
):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog.show()
    _app.processEvents()

    dialog._name.setText("123456789")
    assert "12345678 / 9" in dialog._name_preview.text()
    assert JAMULUS_NAME_HELP in dialog._name.accessibleDescription()

    with patch(
        "webjam_qt.windows.simple_settings.save_settings"
    ) as save:
        dialog._name.setText("12345678901234567")
        assert dialog._save() is False

    save.assert_not_called()
    assert "too long" in dialog._error.text()
    assert dialog._name.hasFocus()
    dialog.close()


def test_save_persists_only_normalized_link_for_webex(tmp_path):
    dialog = _dialog(tmp_path, opener=lambda _url: True)
    dialog._video.setText("team.webex.com/meet/bandroom")

    assert dialog._save() is True

    data = json.loads(
        Path(dialog._settings.config_file).read_text(encoding="utf-8")
    )
    assert data["webex_url"] == "https://team.webex.com/meet/bandroom"
    for retired in (
        "webex_config_file",
        "webex_display_name",
        "webex_audio_mode",
        "webex_guest_issuer_id",
        "webex_guest_issuer_secret",
    ):
        assert retired not in data


def test_save_merges_visible_fields_into_latest_controller_settings(tmp_path):
    path = str(tmp_path / "settings.json")
    opening = AppSettings(
        config_file=path,
        musician_name="Opening Name",
        host_server_enabled=False,
        jamulus_server="192.168.1.10",
    )
    latest = AppSettings(
        config_file=path,
        musician_name="Invite Name",
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
        jamulus_port=43123,
    )
    dialog = SimpleSettingsDialog(
        opening,
        webex_opener=lambda _url: True,
        settings_provider=lambda: latest,
    )
    dialog._name.setText("Saved Name")
    dialog._video.setText("team.webex.com/meet/bandroom")

    assert dialog._save() is True

    saved = load_settings(path)
    assert saved.musician_name == "Saved Name"
    assert saved.webex_url == "https://team.webex.com/meet/bandroom"
    assert saved.host_server_enabled is True
    assert saved.jamulus_server == "127.0.0.1"
    assert saved.jamulus_port == 43123

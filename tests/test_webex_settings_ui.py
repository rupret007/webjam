"""Musician-facing Webex settings remain external-only and truthful."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

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
    assert "Meeting or Personal Room link" in labels
    assert dialog._video_site.text() == "Webex site: team.webex.com"
    assert "private-room" not in dialog._video_site.text()
    assert "private" not in dialog._video_site.text()


def test_open_action_normalizes_and_reports_external_handoff(tmp_path):
    opened: list[str] = []
    dialog = _dialog(tmp_path, opener=lambda url: opened.append(url) or True)
    dialog._video.setText("team.webex.com/meet/bandroom")

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


def test_invalid_link_never_reaches_external_opener(tmp_path):
    opened: list[str] = []
    dialog = _dialog(tmp_path, opener=lambda url: opened.append(url) or True)
    dialog.show()
    _app.processEvents()
    dialog._video.setText("http://example.test/private")

    dialog._open_webex.click()

    assert opened == []
    assert "Check this link before opening" in dialog._webex_status.text()
    assert dialog._webex_status.accessibleDescription() == (
        dialog._webex_status.text()
    )
    assert dialog._video.hasFocus()
    dialog.close()


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

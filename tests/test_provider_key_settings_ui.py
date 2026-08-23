"""Settings rows for optional keys: never shown back, never written to disk.

The dialog's own Save writes ``~/.webjam_config.json``. A key must not be able
to ride along with it, which is why saving a key is its own button against the
OS credential store and not part of the dialog's accept path at all.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit

from core.provider_credentials import (
    AUDIO_PROVIDER_IDS,
    TEXT_PROVIDER_IDS,
    ProviderCredentials,
)
from core.secret_store import NoSecretStore, set_default_secret_store
from core.settings import AppSettings
from tests.support.fake_secret_store import FakeSecretStore
from webjam_qt.widgets.provider_keys import INTRO, ProviderKeyPanel
from webjam_qt.windows.simple_settings import SimpleSettingsDialog

ALL_IDS = (*AUDIO_PROVIDER_IDS, *TEXT_PROVIDER_IDS)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store():
    fake = FakeSecretStore()
    set_default_secret_store(fake)
    yield fake
    set_default_secret_store(None)


def _panel(app, store, *, settings=None) -> ProviderKeyPanel:
    widget = ProviderKeyPanel(
        ALL_IDS,
        credentials=ProviderCredentials(store=store, settings=settings),
    )
    return widget


# ----------------------------------------------------------------------
# The rows
# ----------------------------------------------------------------------
def test_the_panel_says_nothing_here_is_required(app, store):
    panel = _panel(app, store)

    assert "plays without any of these" in INTRO
    assert "no key at all" in INTRO
    assert panel.status_text("openai").startswith("Not set.")


def test_every_field_is_a_password_field(app, store):
    panel = _panel(app, store)

    for provider_id in ALL_IDS:
        field = panel.field(provider_id)
        assert field is not None, provider_id
        assert field.echoMode() == QLineEdit.EchoMode.Password


def test_saving_a_key_stores_it_and_clears_the_field(app, store):
    panel = _panel(app, store)
    panel.field("openai").setText("sk-typed-here")

    panel._save("openai")

    assert store.items["openai"] == "sk-typed-here"
    assert panel.field("openai").text() == ""
    assert panel.status_text("openai") == (
        "Saved in this computer's credential store."
    )


def test_a_saved_key_is_never_read_back_into_the_field(app, store):
    store.items["anthropic"] = "sk-ant-already-saved"

    panel = _panel(app, store)

    assert panel.field("anthropic").text() == ""
    assert "sk-ant-already-saved" not in panel.status_text("anthropic")


def test_a_refused_save_shows_the_reason_and_keeps_the_field(app):
    refusing = FakeSecretStore(fail_write="Keychain access was denied.")
    panel = ProviderKeyPanel(
        ALL_IDS, credentials=ProviderCredentials(store=refusing)
    )
    panel.field("xai").setText("xai-value")

    panel._save("xai")

    assert panel.status_text("xai") == "Keychain access was denied."
    assert panel.field("xai").text() == "xai-value"


def test_a_key_set_in_the_environment_is_reported_and_left_alone(app, store):
    with patch.dict(os.environ, {"OPENAI_API_KEY": "from-shell"}):
        panel = _panel(app, store)

    assert "Set in your environment" in panel.status_text("openai")
    assert panel.field("openai").isEnabled() is False
    assert store.items == {}


def test_an_older_music_ai_key_is_offered_a_move_not_silently_dropped(app, store):
    panel = _panel(
        app, store, settings=AppSettings(music_ai_api_key="from-the-old-file")
    )

    assert "older settings file" in panel.status_text("music_ai")
    assert "credential store" in panel.status_text("music_ai")


def test_removing_clears_the_stored_key(app, store):
    store.items["minimax"] = "value"
    panel = _panel(app, store)

    panel._remove("minimax")

    assert store.items == {}
    assert panel.status_text("minimax").startswith("Not set.")


def test_a_computer_with_no_store_is_told_the_truth_not_given_a_dead_button(app):
    panel = ProviderKeyPanel(
        ALL_IDS,
        credentials=ProviderCredentials(store=NoSecretStore("Nothing here.")),
    )

    assert "Nothing here." in panel._storage_line.text()
    assert "plain text" in panel._storage_line.text()
    for provider_id in ALL_IDS:
        assert panel.field(provider_id).isEnabled() is False


# ----------------------------------------------------------------------
# The dialog around them
# ----------------------------------------------------------------------
def test_settings_offers_the_keys_collapsed_and_last(app, store):
    dialog = SimpleSettingsDialog(AppSettings(), show_band_check_action=False)
    try:
        assert dialog._keys_toggle.isChecked() is False
        assert dialog._keys_panel.isHidden()

        dialog.show_optional_keys()

        assert dialog._keys_toggle.isChecked() is True
        assert dialog._keys_panel.isHidden() is False
    finally:
        dialog.deleteLater()


def test_moving_a_legacy_music_ai_key_then_saving_settings_leaves_no_plaintext(
    app, store, tmp_path
):
    """Save key + dialog Save must not write the migrated Music AI key back."""

    config = tmp_path / "settings.json"
    settings = AppSettings(
        config_file=str(config),
        musician_name="Jeff",
        music_ai_api_key="from-the-old-file",
    )
    dialog = SimpleSettingsDialog(settings, show_band_check_action=False)
    try:
        dialog._keys_panel.field("music_ai").setText("from-the-old-file")
        dialog._keys_panel._save("music_ai")
        assert settings.music_ai_api_key == ""
        assert store.items["music_ai"] == "from-the-old-file"
        assert dialog._save() is True
    finally:
        dialog.deleteLater()

    written = json.loads(config.read_text())
    assert written.get("music_ai_api_key", "") == ""
    assert "from-the-old-file" not in config.read_text()


def test_the_dialog_save_writes_settings_without_any_key(app, store, tmp_path):
    """Typing a key and pressing the dialog's Save must not persist it."""

    config = tmp_path / "settings.json"
    dialog = SimpleSettingsDialog(
        AppSettings(config_file=str(config), musician_name="Jeff"),
        show_band_check_action=False,
    )
    try:
        dialog._keys_panel.field("openai").setText("sk-never-written")
        assert dialog._save() is True
    finally:
        dialog.deleteLater()

    written = config.read_text()
    assert "sk-never-written" not in written
    assert "openai" not in json.loads(written)


def test_the_dialog_never_reads_a_key_out_of_the_settings_object(app, store):
    """There is no provider field on AppSettings for it to read."""

    fields = set(AppSettings.__dataclass_fields__)
    for provider_id in TEXT_PROVIDER_IDS:
        assert not any(provider_id in field for field in fields)


def test_host_and_join_never_mention_a_model_provider():
    """No provider picker at launch: the choice belongs inside write-help."""

    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    launch = (
        repo_root / "webjam_qt" / "windows" / "launch_dialog.py"
    ).read_text().lower()
    for forbidden in (
        "openai",
        "anthropic",
        "xai",
        "minimax",
        "provider_credentials",
        "providerkeypanel",
        "model",
    ):
        assert forbidden not in launch, forbidden

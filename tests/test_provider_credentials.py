"""Where a musician's own keys live, and everywhere they must not.

The load-bearing claim of BYOK is negative: a key never lands in WebJam's
settings file, never lands in git, and never appears in a diagnostic. These
tests hold that line, and they hold the positive one too — that a key which is
present is found in a predictable order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.provider_credentials import (
    AUDIO_PROVIDER_IDS,
    PROVIDERS,
    SOURCE_ENVIRONMENT,
    SOURCE_LEGACY_SETTINGS,
    SOURCE_STORE,
    TEXT_PROVIDER_IDS,
    ProviderCredentials,
    no_store_reason,
    provider_spec,
    storage_note,
    validate_key,
)
from core.redaction import redact_mapping
from core.secret_store import (
    NoSecretStore,
    SecretStoreError,
    StoreOutcome,
    set_default_secret_store,
)
from core.settings import AppSettings, save_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeSecretStore:
    """An in-memory stand-in for Keychain / Credential Manager."""

    name = "fake"

    def __init__(self, *, usable: bool = True, fail_write: str = "") -> None:
        self.items: dict[str, str] = {}
        self._usable = usable
        self._fail_write = fail_write
        self.writes: list[str] = []

    def usable(self) -> bool:
        return self._usable

    def get(self, account: str) -> str:
        return self.items.get(account, "")

    def put(self, account: str, secret: str) -> StoreOutcome:
        self.writes.append(account)
        if self._fail_write:
            return StoreOutcome(stored=False, reason=self._fail_write)
        self.items[account] = secret
        return StoreOutcome(stored=True)

    def delete(self, account: str) -> bool:
        return self.items.pop(account, None) is not None


@pytest.fixture(autouse=True)
def _no_ambient_store():
    """No test may reach this machine's real credential store."""

    set_default_secret_store(NoSecretStore("test"))
    yield
    set_default_secret_store(None)


@pytest.fixture
def clean_env():
    with patch.dict(os.environ, {}, clear=True):
        yield


# ----------------------------------------------------------------------
# The registry Art has to match
# ----------------------------------------------------------------------
def test_the_text_provider_ids_are_the_agreed_four():
    assert TEXT_PROVIDER_IDS == ("openai", "anthropic", "xai", "minimax")
    assert AUDIO_PROVIDER_IDS == ("music_ai",)
    assert set(PROVIDERS) == set(TEXT_PROVIDER_IDS) | set(AUDIO_PROVIDER_IDS)


def test_music_ai_is_a_separate_audio_facts_key_not_a_text_one():
    """Stems and chords are measurements. A text model is not asked for them."""

    assert provider_spec("music_ai").kind == "audio"
    assert "music_ai" not in TEXT_PROVIDER_IDS
    for provider_id in TEXT_PROVIDER_IDS:
        assert provider_spec(provider_id).is_text


def test_every_provider_names_its_console_and_environment_variables():
    for spec in PROVIDERS.values():
        assert spec.console_url.startswith("https://"), spec.id
        assert spec.env_vars, spec.id
        assert spec.env_vars[0].startswith("WEBJAM_"), spec.id


# ----------------------------------------------------------------------
# Resolution order
# ----------------------------------------------------------------------
def test_the_environment_wins_over_the_store(clean_env):
    store = FakeSecretStore()
    store.items["openai"] = "stored-value"
    os.environ["OPENAI_API_KEY"] = "environment-value"

    resolved = ProviderCredentials(store=store).resolve("openai")

    assert resolved.value == "environment-value"
    assert resolved.source == SOURCE_ENVIRONMENT


def test_the_webjam_prefixed_variable_wins_over_the_vendor_one(clean_env):
    os.environ["OPENAI_API_KEY"] = "vendor"
    os.environ["WEBJAM_OPENAI_API_KEY"] = "webjam"

    assert ProviderCredentials(store=FakeSecretStore()).api_key("openai") == "webjam"


def test_the_store_is_used_when_the_environment_is_empty(clean_env):
    store = FakeSecretStore()
    store.items["anthropic"] = "stored-value"

    resolved = ProviderCredentials(store=store).resolve("anthropic")

    assert resolved.value == "stored-value"
    assert resolved.source == SOURCE_STORE


def test_an_older_music_ai_key_in_the_settings_file_still_works(clean_env):
    """Upgrading must not silently lose a key that was already working."""

    credentials = ProviderCredentials(
        store=FakeSecretStore(),
        settings=AppSettings(music_ai_api_key="from-the-old-file"),
    )
    resolved = credentials.resolve("music_ai")

    assert resolved.value == "from-the-old-file"
    assert resolved.source == SOURCE_LEGACY_SETTINGS


def test_a_stored_music_ai_key_wins_over_the_older_settings_file(clean_env):
    store = FakeSecretStore()
    store.items["music_ai"] = "from-the-keychain"

    credentials = ProviderCredentials(
        store=store, settings=AppSettings(music_ai_api_key="from-the-old-file")
    )

    assert credentials.api_key("music_ai") == "from-the-keychain"


def test_a_text_provider_has_no_settings_file_fallback_at_all(clean_env):
    """There is no field to fall back to, so there is nothing to migrate."""

    for provider_id in TEXT_PROVIDER_IDS:
        assert provider_spec(provider_id).legacy_settings_field == ""


def test_nothing_is_configured_on_a_fresh_machine(clean_env):
    credentials = ProviderCredentials(store=FakeSecretStore())

    assert credentials.configured_text_ids() == ()
    assert credentials.configured_ids() == ()
    assert credentials.api_key("openai") == ""
    assert credentials.has_key("music_ai") is False


def test_configured_ids_lists_only_providers_with_a_key(clean_env):
    store = FakeSecretStore()
    store.items["xai"] = "x"
    store.items["music_ai"] = "m"

    credentials = ProviderCredentials(store=store)

    assert credentials.configured_text_ids() == ("xai",)
    assert set(credentials.configured_ids("audio")) == {"music_ai"}


def test_an_unknown_provider_resolves_to_nothing_rather_than_raising(clean_env):
    resolved = ProviderCredentials(store=FakeSecretStore()).resolve("hal9000")

    assert resolved.value == ""
    assert resolved.present is False


# ----------------------------------------------------------------------
# Saving
# ----------------------------------------------------------------------
def test_saving_puts_the_key_in_the_os_store(clean_env):
    store = FakeSecretStore()

    result = ProviderCredentials(store=store).save("openai", " sk-abc123 ")

    assert result.saved
    assert store.items["openai"] == "sk-abc123"


def test_saving_without_a_store_refuses_and_names_the_environment_variable(clean_env):
    store = NoSecretStore("This computer has no credential store WebJam can use.")

    result = ProviderCredentials(store=store).save("openai", "sk-abc123")

    assert result.failed
    assert "OPENAI_API_KEY" in result.reason
    assert "plain text" in result.reason


def test_a_store_that_refuses_reports_its_own_reason(clean_env):
    store = FakeSecretStore(fail_write="Keychain access was denied.")

    result = ProviderCredentials(store=store).save("anthropic", "sk-ant-1")

    assert result.failed
    assert result.reason == "Keychain access was denied."
    assert store.items == {}


def test_clearing_removes_a_stored_key(clean_env):
    store = FakeSecretStore()
    store.items["minimax"] = "value"

    assert ProviderCredentials(store=store).clear("minimax") is True
    assert store.items == {}


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "sk with spaces", "sk-abc\ndef", "sk-\u00e9", "x" * 600],
)
def test_a_pasted_paragraph_is_not_a_key(bad):
    with pytest.raises(SecretStoreError):
        validate_key(bad)


def test_validation_explains_the_common_mistake():
    with pytest.raises(SecretStoreError) as excinfo:
        validate_key("sk-abc def")
    assert "spaces" in str(excinfo.value)


def test_an_unknown_provider_cannot_be_saved(clean_env):
    result = ProviderCredentials(store=FakeSecretStore()).save("hal9000", "k")

    assert result.failed
    assert "does not know" in result.reason


# ----------------------------------------------------------------------
# Never plaintext, never in git, never in a diagnostic
# ----------------------------------------------------------------------
def test_no_text_provider_key_is_a_settings_field():
    """The settings file is JSON on disk. Text keys never go near it."""

    fields = set(AppSettings.__dataclass_fields__)
    for provider_id in TEXT_PROVIDER_IDS:
        assert f"{provider_id}_api_key" not in fields
        assert not any(provider_id in field for field in fields), provider_id


def test_saving_settings_never_writes_a_model_key(tmp_path, clean_env):
    store = FakeSecretStore()
    credentials = ProviderCredentials(store=store)
    for provider_id in TEXT_PROVIDER_IDS:
        assert credentials.save(provider_id, f"sk-{provider_id}-secret").saved

    config = tmp_path / "settings.json"
    save_settings(AppSettings(config_file=str(config)))
    written = config.read_text()

    for provider_id in TEXT_PROVIDER_IDS:
        assert f"sk-{provider_id}-secret" not in written
        assert provider_id not in json.loads(written)


def test_a_model_key_is_redacted_from_diagnostics():
    redacted = redact_mapping(
        {
            "openai_api_key": "sk-live-value",
            "anthropic_api_key": "sk-ant-live",
            "xai_api_key": "xai-live",
            "minimax_api_key": "mm-live",
            "jamulus_port": 22124,
        }
    )
    for field in (
        "openai_api_key",
        "anthropic_api_key",
        "xai_api_key",
        "minimax_api_key",
    ):
        assert redacted[field] == "[redacted]"
    assert redacted["jamulus_port"] == 22124


def test_a_resolved_key_never_prints_its_value(clean_env):
    store = FakeSecretStore()
    store.items["openai"] = "sk-do-not-print-me"

    resolved = ProviderCredentials(store=store).resolve("openai")

    assert "sk-do-not-print-me" not in repr(resolved)
    assert resolved.describe_source() == (
        "saved in this computer's credential store"
    )


def test_no_model_key_is_committed_to_this_repository():
    """Nothing that reads as a real vendor key may be checked in."""

    import re

    vendor_shapes = (
        re.compile(r"sk-[A-Za-z0-9]{24,}"),
        re.compile(r"sk-ant-[A-Za-z0-9-]{24,}"),
        re.compile(r"xai-[A-Za-z0-9]{24,}"),
    )
    offenders: list[str] = []
    for directory in ("core", "webjam_qt", "services", "api", "ui", "tests", "docs"):
        root = REPO_ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".json"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for shape in vendor_shapes:
                if shape.search(text):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_the_storage_note_is_honest_about_where_a_key_goes():
    note = storage_note()
    assert "credential store" in note
    assert "plain text" in note
    assert no_store_reason(NoSecretStore()) == (
        "This computer has no credential store WebJam can use."
    )

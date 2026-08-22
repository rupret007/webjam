"""The OS credential store adapters, exercised without an OS credential store.

The ``ctypes`` declarations can only run on the platform they bind to, so each
backend is built against a tiny injected binding and every branch that decides
what a musician is told — duplicate item, locked keychain, denied access,
missing credential — is covered here on any machine.
"""

from __future__ import annotations

import sys

import pytest

from core.secret_store import (
    MAX_SECRET_BYTES,
    SERVICE_NAME,
    KeyringSecretStore,
    MacKeychainSecretStore,
    NoSecretStore,
    SecretStoreError,
    WindowsCredentialSecretStore,
    build_secret_store,
    default_secret_store,
    set_default_secret_store,
)

_ERR_SUCCESS = 0
_ERR_ITEM_NOT_FOUND = -25300
_ERR_DUPLICATE_ITEM = -25299
_ERR_USER_CANCELED = -128
_ERR_INTERACTION_NOT_ALLOWED = -25308


@pytest.fixture(autouse=True)
def _reset_default_store():
    yield
    set_default_secret_store(None)


# ----------------------------------------------------------------------
# No store: the honest refusal
# ----------------------------------------------------------------------
def test_no_store_refuses_to_write_and_says_why():
    store = NoSecretStore("Nothing here.")

    assert store.usable() is False
    assert store.get("openai") == ""
    outcome = store.put("openai", "value")
    assert outcome.failed
    assert outcome.reason == "Nothing here."
    assert store.delete("openai") is False


def test_no_store_still_validates_its_inputs():
    store = NoSecretStore()

    with pytest.raises(SecretStoreError):
        store.get("../../etc/passwd")
    with pytest.raises(SecretStoreError):
        store.put("openai", "")


def test_a_secret_longer_than_any_key_is_refused():
    store = NoSecretStore()

    with pytest.raises(SecretStoreError):
        store.put("openai", "x" * (MAX_SECRET_BYTES + 1))


# ----------------------------------------------------------------------
# keyring, when a musician has it
# ----------------------------------------------------------------------
class _FakeKeyring:
    def __init__(self, *, raises: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self._raises = raises

    def get_password(self, service, account):
        if self._raises:
            raise RuntimeError("locked")
        return self.items.get((service, account))

    def set_password(self, service, account, secret):
        if self._raises:
            raise RuntimeError("locked")
        self.items[(service, account)] = secret

    def delete_password(self, service, account):
        if self._raises:
            raise RuntimeError("locked")
        del self.items[(service, account)]


def test_keyring_round_trips_under_one_service_name():
    module = _FakeKeyring()
    store = KeyringSecretStore(module)

    assert store.put("openai", "sk-value").stored
    assert module.items == {(SERVICE_NAME, "openai"): "sk-value"}
    assert store.get("openai") == "sk-value"
    assert store.delete("openai") is True
    assert store.get("openai") == ""


def test_a_locked_keyring_reports_rather_than_crashing_the_session():
    store = KeyringSecretStore(_FakeKeyring(raises=True))

    assert store.get("openai") == ""
    outcome = store.put("openai", "sk-value")
    assert outcome.failed
    assert "refused" in outcome.reason
    assert store.delete("openai") is False


# ----------------------------------------------------------------------
# macOS Keychain
# ----------------------------------------------------------------------
class _FakeKeychain:
    def __init__(self, *, add_status: int | None = None) -> None:
        self.items: dict[tuple[str, str], bytes] = {}
        self._add_status = add_status
        self.calls: list[str] = []

    def find(self, service, account):
        self.calls.append("find")
        key = (service, account)
        if key not in self.items:
            return _ERR_ITEM_NOT_FOUND, b""
        return _ERR_SUCCESS, self.items[key]

    def add(self, service, account, secret):
        self.calls.append("add")
        if self._add_status is not None:
            return self._add_status
        key = (service, account)
        if key in self.items:
            return _ERR_DUPLICATE_ITEM
        self.items[key] = secret
        return _ERR_SUCCESS

    def update(self, service, account, secret):
        self.calls.append("update")
        key = (service, account)
        if key not in self.items:
            return _ERR_ITEM_NOT_FOUND
        self.items[key] = secret
        return _ERR_SUCCESS

    def delete(self, service, account):
        self.calls.append("delete")
        return (
            _ERR_SUCCESS
            if self.items.pop((service, account), None) is not None
            else _ERR_ITEM_NOT_FOUND
        )


def test_the_keychain_round_trips_a_key():
    binding = _FakeKeychain()
    store = MacKeychainSecretStore(binding)

    assert store.put("music_ai", "abc123").stored
    assert store.get("music_ai") == "abc123"
    assert store.delete("music_ai") is True
    assert store.get("music_ai") == ""


def test_replacing_a_key_updates_the_existing_keychain_item():
    """A second Save must not fail with "duplicate item" and lose the change."""

    binding = _FakeKeychain()
    store = MacKeychainSecretStore(binding)
    store.put("openai", "first")

    assert store.put("openai", "second").stored
    assert store.get("openai") == "second"
    assert "update" in binding.calls


def test_a_missing_keychain_item_reads_as_empty_not_as_an_error():
    assert MacKeychainSecretStore(_FakeKeychain()).get("openai") == ""


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (_ERR_USER_CANCELED, "denied"),
        (_ERR_INTERACTION_NOT_ALLOWED, "locked"),
        (-1, "would not save"),
    ],
)
def test_a_refused_keychain_write_explains_itself(status, fragment):
    store = MacKeychainSecretStore(_FakeKeychain(add_status=status))

    outcome = store.put("openai", "value")

    assert outcome.failed
    assert fragment in outcome.reason


# ----------------------------------------------------------------------
# Windows Credential Manager
# ----------------------------------------------------------------------
class _FakeCredentialManager:
    def __init__(self, *, writable: bool = True) -> None:
        self.items: dict[str, bytes] = {}
        self._writable = writable

    def read(self, target):
        return self.items.get(target, b"")

    def write(self, target, secret):
        if not self._writable:
            return False
        self.items[target] = secret
        return True

    def delete(self, target):
        return self.items.pop(target, None) is not None


def test_credential_manager_round_trips_under_a_namespaced_target():
    binding = _FakeCredentialManager()
    store = WindowsCredentialSecretStore(binding)

    assert store.put("xai", "xai-value").stored
    assert list(binding.items) == [f"{SERVICE_NAME}:xai"]
    assert store.get("xai") == "xai-value"
    assert store.delete("xai") is True


def test_a_refused_credential_write_explains_itself():
    store = WindowsCredentialSecretStore(_FakeCredentialManager(writable=False))

    outcome = store.put("xai", "value")

    assert outcome.failed
    assert "Credential Manager" in outcome.reason


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------
def test_this_linux_test_runner_gets_an_honest_refusal_not_a_plaintext_file():
    """There is deliberately no encrypted-file fallback to fall back to."""

    store = build_secret_store()

    if sys.platform.startswith("linux") and store.name == "none":
        assert store.usable() is False
        assert "keyring" in store.reason


def test_the_default_store_is_resolved_once_and_replaceable():
    sentinel = NoSecretStore("sentinel")
    set_default_secret_store(sentinel)

    assert default_secret_store() is sentinel
    assert default_secret_store() is sentinel

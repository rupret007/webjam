"""Where an optional model key lives on this computer: the OS store, or nowhere.

WebJam plays without any of these keys. When a musician does bring one, it goes
into the operating system's own credential store — Keychain on macOS, Credential
Manager on Windows — and nowhere else. It is never written into
``~/.webjam_config.json``, never printed, and never committed.

There is deliberately no encrypted-file fallback. A file WebJam could read
without the OS unlocking it is a file anything running as this user could read,
and calling that "secure storage" in the Settings dialog would be a lie. When no
store is available the answer is an honest refusal plus the environment-variable
route, which at least does not leave a secret on disk.

Every backend is written against a small injected binding so the branching,
encoding, and error handling are exercised by ordinary tests on any platform.
The binding itself — the ``ctypes`` declarations — is the only part that has to
run on the real operating system, and it is kept as thin as it can be.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Protocol

LOGGER = logging.getLogger("webjam.core.secret_store")

# One service name for every WebJam credential, so a musician sees a single
# tidy group in Keychain Access or Credential Manager rather than five.
SERVICE_NAME = "WebJam"

# Long enough for any published API key format, short enough that a pasted
# document cannot become a keychain entry.
MAX_SECRET_BYTES = 4096

_MAC_ERR_SUCCESS = 0
_MAC_ERR_ITEM_NOT_FOUND = -25300
_MAC_ERR_DUPLICATE_ITEM = -25299
_MAC_ERR_AUTH_FAILED = -25293
_MAC_ERR_USER_CANCELED = -128
_MAC_ERR_INTERACTION_NOT_ALLOWED = -25308

_WIN_CRED_TYPE_GENERIC = 1
_WIN_CRED_PERSIST_LOCAL_MACHINE = 2
_WIN_ERROR_NOT_FOUND = 1168


class SecretStoreError(RuntimeError):
    """A store operation failed for a reason worth showing a musician."""


@dataclass(frozen=True, slots=True)
class StoreOutcome:
    """Whether a write landed in the OS store, and what to say if it did not."""

    stored: bool
    reason: str = ""

    @property
    def failed(self) -> bool:
        return not self.stored


class SecretStore(Protocol):
    """The seam the credentials module and its tests both talk to."""

    name: str

    def usable(self) -> bool: ...

    def get(self, account: str) -> str: ...

    def put(self, account: str, secret: str) -> StoreOutcome: ...

    def delete(self, account: str) -> bool: ...


def _account(value: str) -> str:
    """Return a bounded, printable account name, or raise.

    Account names are WebJam's own provider ids, so this is a guard against a
    future caller passing user text into a keychain query rather than a
    validator for musician input.
    """

    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > 64
        or not all(character.isalnum() or character in "-_." for character in candidate)
    ):
        raise SecretStoreError("WebJam refused an unusable credential name.")
    return candidate


def _secret_bytes(secret: str) -> bytes:
    data = str(secret or "").encode("utf-8")
    if not data:
        raise SecretStoreError("WebJam will not store an empty key.")
    if len(data) > MAX_SECRET_BYTES:
        raise SecretStoreError("That key is longer than WebJam will store.")
    return data


class NoSecretStore:
    """The honest answer when this computer has no credential store."""

    name = "none"

    def __init__(self, reason: str = "") -> None:
        self.reason = str(reason or "").strip() or (
            "This computer has no credential store WebJam can use."
        )

    def usable(self) -> bool:
        return False

    def get(self, account: str) -> str:
        _account(account)
        return ""

    def put(self, account: str, secret: str) -> StoreOutcome:
        _account(account)
        _secret_bytes(secret)
        return StoreOutcome(stored=False, reason=self.reason)

    def delete(self, account: str) -> bool:
        _account(account)
        return False


class KeyringSecretStore:
    """Uses the ``keyring`` package when a musician has installed it.

    Not a WebJam dependency: the release bundle is pinned by hashed lock files
    and this module would rather ship its own thin platform bindings than move
    that floor. When ``keyring`` is present it is preferred anyway, because it
    is better maintained than anything here and already knows the odd corners
    of every desktop's secret service.
    """

    name = "keyring"

    def __init__(self, module: Any) -> None:
        self._keyring = module

    def usable(self) -> bool:
        return self._keyring is not None

    def get(self, account: str) -> str:
        name = _account(account)
        try:
            value = self._keyring.get_password(SERVICE_NAME, name)
        except Exception:  # noqa: BLE001 - a locked store must not break the UI
            LOGGER.debug("Keyring read failed")
            return ""
        return str(value or "")

    def put(self, account: str, secret: str) -> StoreOutcome:
        name = _account(account)
        _secret_bytes(secret)
        try:
            self._keyring.set_password(SERVICE_NAME, name, str(secret))
        except Exception as exc:  # noqa: BLE001 - report, never leak the value
            LOGGER.warning("Keyring write failed (%s)", type(exc).__name__)
            return StoreOutcome(
                stored=False,
                reason="Your system credential store refused to save that key.",
            )
        return StoreOutcome(stored=True)

    def delete(self, account: str) -> bool:
        name = _account(account)
        try:
            self._keyring.delete_password(SERVICE_NAME, name)
        except Exception:  # noqa: BLE001 - absent is the same as removed
            return False
        return True


class MacKeychainSecretStore:
    """macOS login Keychain through the generic-password functions.

    ``SecKeychainAddGenericPassword`` and friends take plain C buffers, so this
    needs no CoreFoundation object plumbing and the secret never appears in an
    argument vector the way a ``/usr/bin/security`` subprocess would put it.
    """

    name = "macos-keychain"

    def __init__(self, binding: Any) -> None:
        self._binding = binding

    def usable(self) -> bool:
        return self._binding is not None

    def get(self, account: str) -> str:
        name = _account(account)
        status, data = self._binding.find(SERVICE_NAME, name)
        if status == _MAC_ERR_ITEM_NOT_FOUND:
            return ""
        if status != _MAC_ERR_SUCCESS:
            LOGGER.debug("Keychain read returned status %d", status)
            return ""
        try:
            return data.decode("utf-8")
        except (AttributeError, UnicodeDecodeError):
            return ""

    def put(self, account: str, secret: str) -> StoreOutcome:
        name = _account(account)
        payload = _secret_bytes(secret)
        status = self._binding.add(SERVICE_NAME, name, payload)
        if status == _MAC_ERR_DUPLICATE_ITEM:
            status = self._binding.update(SERVICE_NAME, name, payload)
        if status == _MAC_ERR_SUCCESS:
            return StoreOutcome(stored=True)
        return StoreOutcome(stored=False, reason=_mac_reason(status))

    def delete(self, account: str) -> bool:
        name = _account(account)
        return self._binding.delete(SERVICE_NAME, name) == _MAC_ERR_SUCCESS


def _mac_reason(status: int) -> str:
    if status in {_MAC_ERR_USER_CANCELED, _MAC_ERR_AUTH_FAILED}:
        return "Keychain access was denied, so the key was not saved."
    if status == _MAC_ERR_INTERACTION_NOT_ALLOWED:
        return "The Keychain is locked, so the key was not saved."
    return "macOS Keychain would not save that key."


class WindowsCredentialSecretStore:
    """Windows Credential Manager through ``advapi32``'s generic credentials."""

    name = "windows-credential-manager"

    def __init__(self, binding: Any) -> None:
        self._binding = binding

    def usable(self) -> bool:
        return self._binding is not None

    def _target(self, account: str) -> str:
        return f"{SERVICE_NAME}:{_account(account)}"

    def get(self, account: str) -> str:
        data = self._binding.read(self._target(account))
        if not data:
            return ""
        try:
            return bytes(data).decode("utf-8")
        except (TypeError, UnicodeDecodeError):
            return ""

    def put(self, account: str, secret: str) -> StoreOutcome:
        payload = _secret_bytes(secret)
        if self._binding.write(self._target(account), payload):
            return StoreOutcome(stored=True)
        return StoreOutcome(
            stored=False,
            reason="Windows Credential Manager would not save that key.",
        )

    def delete(self, account: str) -> bool:
        return bool(self._binding.delete(self._target(account)))


# ----------------------------------------------------------------------
# Real platform bindings
# ----------------------------------------------------------------------
def _load_mac_binding() -> Any:
    """Return a Keychain binding, or ``None`` when this is not a usable Mac."""

    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("Security")
        if not path:
            return None
        security = ctypes.CDLL(path, use_errno=True)
    except (ImportError, OSError, AttributeError):
        LOGGER.debug("macOS Security framework unavailable")
        return None

    class _MacKeychainBinding:
        def __init__(self, library: Any, ctypes_module: Any) -> None:
            self._lib = library
            self._ctypes = ctypes_module

        def _encoded(self, value: str) -> bytes:
            return str(value).encode("utf-8")

        def find(self, service: str, account: str) -> tuple[int, bytes]:
            ct = self._ctypes
            service_bytes = self._encoded(service)
            account_bytes = self._encoded(account)
            length = ct.c_uint32(0)
            data = ct.c_void_p()
            item = ct.c_void_p()
            status = int(
                self._lib.SecKeychainFindGenericPassword(
                    None,
                    len(service_bytes),
                    service_bytes,
                    len(account_bytes),
                    account_bytes,
                    ct.byref(length),
                    ct.byref(data),
                    ct.byref(item),
                )
            )
            if status != _MAC_ERR_SUCCESS or not data:
                return status, b""
            try:
                payload = ct.string_at(data, int(length.value))
            finally:
                self._lib.SecKeychainItemFreeContent(None, data)
                if item:
                    self._release(item)
            return status, payload

        def add(self, service: str, account: str, secret: bytes) -> int:
            service_bytes = self._encoded(service)
            account_bytes = self._encoded(account)
            return int(
                self._lib.SecKeychainAddGenericPassword(
                    None,
                    len(service_bytes),
                    service_bytes,
                    len(account_bytes),
                    account_bytes,
                    len(secret),
                    secret,
                    None,
                )
            )

        def update(self, service: str, account: str, secret: bytes) -> int:
            ct = self._ctypes
            service_bytes = self._encoded(service)
            account_bytes = self._encoded(account)
            length = ct.c_uint32(0)
            data = ct.c_void_p()
            item = ct.c_void_p()
            status = int(
                self._lib.SecKeychainFindGenericPassword(
                    None,
                    len(service_bytes),
                    service_bytes,
                    len(account_bytes),
                    account_bytes,
                    ct.byref(length),
                    ct.byref(data),
                    ct.byref(item),
                )
            )
            if status != _MAC_ERR_SUCCESS:
                return status
            try:
                if data:
                    self._lib.SecKeychainItemFreeContent(None, data)
                return int(
                    self._lib.SecKeychainItemModifyAttributesAndData(
                        item, None, len(secret), secret
                    )
                )
            finally:
                if item:
                    self._release(item)

        def delete(self, service: str, account: str) -> int:
            ct = self._ctypes
            service_bytes = self._encoded(service)
            account_bytes = self._encoded(account)
            length = ct.c_uint32(0)
            data = ct.c_void_p()
            item = ct.c_void_p()
            status = int(
                self._lib.SecKeychainFindGenericPassword(
                    None,
                    len(service_bytes),
                    service_bytes,
                    len(account_bytes),
                    account_bytes,
                    ct.byref(length),
                    ct.byref(data),
                    ct.byref(item),
                )
            )
            if status != _MAC_ERR_SUCCESS:
                return status
            try:
                if data:
                    self._lib.SecKeychainItemFreeContent(None, data)
                return int(self._lib.SecKeychainItemDelete(item))
            finally:
                if item:
                    self._release(item)

        def _release(self, item: Any) -> None:
            try:
                import ctypes.util

                core_path = ctypes.util.find_library("CoreFoundation")
                if not core_path:
                    return
                self._ctypes.CDLL(core_path).CFRelease(item)
            except (ImportError, OSError, AttributeError):
                LOGGER.debug("CFRelease unavailable for a keychain item")

    return _MacKeychainBinding(security, ctypes)


def _load_windows_binding() -> Any:
    """Return a Credential Manager binding, or ``None`` off Windows."""

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except (ImportError, OSError, AttributeError):
        LOGGER.debug("Windows credential API unavailable")
        return None

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    class _WindowsCredentialBinding:
        def read(self, target: str) -> bytes:
            pointer = ctypes.POINTER(_CREDENTIAL)()
            if not advapi32.CredReadW(
                target, _WIN_CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
            ):
                if ctypes.get_last_error() != _WIN_ERROR_NOT_FOUND:
                    LOGGER.debug("Credential Manager read failed")
                return b""
            try:
                record = pointer.contents
                return ctypes.string_at(
                    record.CredentialBlob, int(record.CredentialBlobSize)
                )
            finally:
                advapi32.CredFree(pointer)

        def write(self, target: str, secret: bytes) -> bool:
            buffer = ctypes.create_string_buffer(secret, len(secret))
            record = _CREDENTIAL()
            record.Flags = 0
            record.Type = _WIN_CRED_TYPE_GENERIC
            record.TargetName = target
            record.CredentialBlobSize = len(secret)
            record.CredentialBlob = ctypes.cast(
                buffer, ctypes.POINTER(ctypes.c_char)
            )
            record.Persist = _WIN_CRED_PERSIST_LOCAL_MACHINE
            record.UserName = SERVICE_NAME
            return bool(advapi32.CredWriteW(ctypes.byref(record), 0))

        def delete(self, target: str) -> bool:
            return bool(
                advapi32.CredDeleteW(target, _WIN_CRED_TYPE_GENERIC, 0)
            )

    return _WindowsCredentialBinding()


def _load_keyring_module() -> Any:
    try:
        import keyring  # noqa: PLC0415 - optional, discovered at runtime
    except Exception:  # noqa: BLE001 - a broken backend must not break WebJam
        return None
    return keyring


def build_secret_store() -> SecretStore:
    """Return the best real store this computer offers, or an honest refusal."""

    module = _load_keyring_module()
    if module is not None:
        store = KeyringSecretStore(module)
        if store.usable():
            return store

    mac = _load_mac_binding()
    if mac is not None:
        return MacKeychainSecretStore(mac)

    windows = _load_windows_binding()
    if windows is not None:
        return WindowsCredentialSecretStore(windows)

    if sys.platform.startswith("linux"):
        return NoSecretStore(
            "This Linux desktop has no credential store WebJam can reach. "
            "Install the keyring package, or use an environment variable."
        )
    return NoSecretStore()


_DEFAULT_STORE: SecretStore | None = None


def default_secret_store() -> SecretStore:
    """Return the process-wide store, built once.

    Binding a native library is not free and the answer cannot change while
    WebJam is running, so this is resolved on first use and kept.
    """

    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = build_secret_store()
    return _DEFAULT_STORE


def set_default_secret_store(store: SecretStore | None) -> None:
    """Replace or reset the process-wide store. Tests own this."""

    global _DEFAULT_STORE
    _DEFAULT_STORE = store


__all__ = [
    "MAX_SECRET_BYTES",
    "SERVICE_NAME",
    "KeyringSecretStore",
    "MacKeychainSecretStore",
    "NoSecretStore",
    "SecretStore",
    "SecretStoreError",
    "StoreOutcome",
    "WindowsCredentialSecretStore",
    "build_secret_store",
    "default_secret_store",
    "set_default_secret_store",
]

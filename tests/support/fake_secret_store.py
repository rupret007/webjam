"""An in-memory stand-in for Keychain / Credential Manager, shared by tests.

Kept out of the test modules themselves so every suite that needs a musician to
"have a key saved" agrees on what that means, and so no test can accidentally
reach the machine it is running on.
"""

from __future__ import annotations

from core.secret_store import StoreOutcome


class FakeSecretStore:
    """Holds secrets in a dict and records what was written."""

    name = "fake"

    def __init__(self, *, usable: bool = True, fail_write: str = "") -> None:
        self.items: dict[str, str] = {}
        self.writes: list[str] = []
        self._usable = usable
        self._fail_write = fail_write

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


__all__ = ["FakeSecretStore"]

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolate_provider_credentials():
    """No test may read a developer's real keys, or write to their Keychain.

    Both halves matter. Without the store override, running the suite on a Mac
    with a saved key would silently exercise a different code path than CI.
    Without the environment scrub, a developer with OPENAI_API_KEY exported
    would see write-help offered in tests that are asserting it is not.
    """

    from core.provider_credentials import PROVIDERS
    from core.secret_store import NoSecretStore, set_default_secret_store

    saved = {}
    for spec in PROVIDERS.values():
        for name in spec.env_vars:
            if name in os.environ:
                saved[name] = os.environ.pop(name)
    set_default_secret_store(NoSecretStore("No credential store under test."))
    try:
        yield
    finally:
        set_default_secret_store(None)
        os.environ.update(saved)


def make_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def cleanup_temp_file(path: str) -> None:
    import time
    candidates = [
        path,
        str(Path(path).with_name(f"{Path(path).stem}_admin_bootstrap.txt")),
    ]
    for candidate in candidates:
        for _ in range(10):
            try:
                os.remove(candidate)
                break
            except FileNotFoundError:
                break
            except (PermissionError, OSError):
                time.sleep(0.05)

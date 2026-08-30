from __future__ import annotations

import os
import sys
import tempfile
from itertools import count
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _collected_sources_contain(request, *markers: str) -> bool:
    paths = {Path(str(item.path)) for item in request.session.items}
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(marker in source for marker in markers):
            return True
    return False


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


@pytest.fixture(scope="session", autouse=True)
def isolate_application_controller_repository(request, tmp_path_factory):
    """Keep controller tests out of the developer's persistent database.

    Many legacy Qt suites construct ``ApplicationController`` from
    ``unittest.setUpClass``, before a function-scoped fixture could replace its
    default repository. Several legacy modules import the controller inside
    ``setUpClass``, after session fixtures have started, so import the
    non-running controller module here and patch its repository factory before
    any test setup. Give every default construction its own temporary database
    so both combined and per-module runs stay order-independent without
    rewriting HOME.
    """

    if not _collected_sources_contain(request, "ApplicationController"):
        yield
        return

    from webjam_qt.controllers import (
        application_controller as controller_module,
    )
    from webjam_qt.controllers import session_persistence as persistence_module

    repository_type = controller_module.WebJamRepository
    repository_root = tmp_path_factory.mktemp("webjam-controller-repository")
    repository_sequence = count(1)
    persistence_home = tmp_path_factory.mktemp("webjam-session-persistence")

    def isolated_repository(db_path=None):
        selected_path = (
            repository_root
            / f"webjam-app-{next(repository_sequence):04d}.db"
            if db_path is None
            else Path(db_path)
        )
        return repository_type(str(selected_path))

    controller_module.WebJamRepository = isolated_repository
    original_persistence_home = persistence_module._persistence_home
    persistence_module._persistence_home = lambda: persistence_home
    try:
        yield
    finally:
        persistence_module._persistence_home = original_persistence_home
        controller_module.WebJamRepository = repository_type


@pytest.fixture(scope="session", autouse=True)
def isolate_jamulus_rpc_secret_path(request, tmp_path_factory):
    """Redirect the import-time Jamulus RPC secret without changing HOME."""

    if not _collected_sources_contain(
        request,
        "ApplicationController",
        "BridgeService",
        "JamulusRpcClient",
        "DEFAULT_SECRET_PATH",
        "launch_jamulus",
    ):
        yield
        return

    # Some legacy suites import BridgeService inside a test helper, after all
    # session fixtures have started. Import these two non-UI modules here so
    # their shared import-time constant can be redirected before any launch.
    from core import jamulus_rpc_client
    from services import bridge_service

    target_modules = [jamulus_rpc_client, bridge_service]
    bridge_module = bridge_service

    runtime_home = tmp_path_factory.mktemp("webjam-native-runtime")
    secret_path = (
        runtime_home
        / "Library"
        / "Application Support"
        / "WebJam"
        / "JamulusClient"
        / "webjam_client_rpc.secret"
    )
    original_paths = [module.DEFAULT_SECRET_PATH for module in target_modules]
    for module in target_modules:
        module.DEFAULT_SECRET_PATH = secret_path
    original_runtime_home = bridge_module.BridgeService._runtime_home
    bridge_module.BridgeService._runtime_home = lambda _bridge: runtime_home
    try:
        yield
    finally:
        bridge_module.BridgeService._runtime_home = original_runtime_home
        for module, original_path in zip(target_modules, original_paths, strict=True):
            module.DEFAULT_SECRET_PATH = original_path


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

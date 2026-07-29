"""Cross-process exclusion between Jamulus runtimes and component updates."""

from __future__ import annotations

from contextlib import contextmanager
import gc
import multiprocessing
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch
import weakref

from core.component_lock import (
    ComponentLockTimeout,
    InterProcessComponentLock,
    RUNTIME_ACTIVE_LOCK_NAME,
)
from services.bridge_service import BridgeService


def _hold_component_lock(
    path: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    with InterProcessComponentLock(path, timeout=5.0):
        ready.set()
        release.wait(15.0)


def _probe_component_lock(
    path: str,
    result: multiprocessing.queues.Queue,
) -> None:
    try:
        with InterProcessComponentLock(path, timeout=0.0):
            result.put("acquired")
    except ComponentLockTimeout:
        result.put("blocked")


@contextmanager
def _external_component_lock(path: Path) -> Iterator[None]:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_component_lock,
        args=(str(path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10.0), f"lock holder exited with {process.exitcode}"
        yield
    finally:
        release.set()
        process.join(10.0)
        if process.is_alive():
            process.terminate()
            process.join(5.0)
        assert process.exitcode == 0


def _cross_process_probe(path: Path) -> str:
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(
        target=_probe_component_lock,
        args=(str(path), result),
    )
    process.start()
    process.join(10.0)
    if process.is_alive():
        process.terminate()
        process.join(5.0)
    assert process.exitcode == 0
    return str(result.get(timeout=2.0))


def _bridge(component_store_root: Path) -> BridgeService:
    settings = MagicMock()
    settings.jamulus_server = "band.example.test"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_port = 22240
    settings.server_rpc_secret_file = ""
    settings.takes_directory = ""
    settings.jamulus_candidates = []
    settings.host_server_enabled = False
    settings.musician_name = "Lease Test"
    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=MagicMock(),
        settings=settings,
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=component_store_root,
    )
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    return bridge


def test_runtime_lock_uses_the_updater_contract_filename(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path / "components")

    assert RUNTIME_ACTIVE_LOCK_NAME == ".runtime-active.lock"
    assert bridge._runtime_component_lock_path == (
        tmp_path / "components" / ".runtime-active.lock"
    )


def test_other_process_blocks_every_runtime_before_resolution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "components"
    lock_path = root / RUNTIME_ACTIVE_LOCK_NAME
    client = _bridge(root)
    practice = _bridge(root)
    hosted = _bridge(root)
    hosted.settings.host_server_enabled = True
    hosted._port_free = MagicMock(return_value=True)
    client.find_jamulus = MagicMock(return_value="/unused/Jamulus")
    practice.find_jamulus = MagicMock(return_value="/unused/Jamulus")
    hosted.find_jamulus_server_with_source = MagicMock(
        return_value=("/unused/JamulusServer", "managed")
    )

    with _external_component_lock(lock_path), patch(
        "services.bridge_service.threading.Thread"
    ) as thread_class, patch(
        "services.bridge_service.subprocess.Popen"
    ) as popen:
        assert client.launch_jamulus(manual=True) is False
        assert practice.launch_practice_session() is False
        ok, detail = hosted.ensure_hosted_server()

    assert ok is False
    assert "Another WebJam window or a Jamulus update" in detail
    client.find_jamulus.assert_not_called()
    practice.find_jamulus.assert_not_called()
    hosted.find_jamulus_server_with_source.assert_not_called()
    thread_class.assert_not_called()
    popen.assert_not_called()
    assert client.show_actionable_error.call_args.args[0] == "Band audio is busy"
    assert (
        practice.show_actionable_error.call_args.args[0]
        == "Practice audio is busy"
    )


def test_multiple_owned_roles_share_one_lease_until_all_stop(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    lock_path = bridge._runtime_component_lock_path

    assert bridge._acquire_runtime_component_lease("practice")[0]
    assert bridge._acquire_runtime_component_lease("client")[0]
    assert _cross_process_probe(lock_path) == "blocked"

    bridge._release_runtime_component_lease("practice")
    assert _cross_process_probe(lock_path) == "blocked"

    bridge._release_runtime_component_lease("client")
    assert _cross_process_probe(lock_path) == "acquired"


def test_failed_client_preflight_releases_lease(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path / "components")
    bridge.find_jamulus = MagicMock(return_value=None)

    assert bridge.launch_jamulus(manual=True) is False

    assert bridge.runtime_component_lease_active is False
    assert _cross_process_probe(bridge._runtime_component_lock_path) == "acquired"


def test_reconnect_intent_holds_lease_after_process_exit_until_clean_stop(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    assert bridge._acquire_runtime_component_lease("client")[0]
    exited_process = MagicMock()
    exited_process.poll.return_value = 1
    bridge.jamulus_process = exited_process
    bridge.jamulus_launch_intended = True

    assert _cross_process_probe(bridge._runtime_component_lock_path) == "blocked"

    assert bridge.stop_jamulus() is True
    assert bridge.runtime_component_lease_active is False
    assert _cross_process_probe(bridge._runtime_component_lock_path) == "acquired"


def test_hosted_restart_keeps_server_pin_and_lease_between_processes(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    assert bridge._acquire_runtime_component_lease("server")[0]
    pinned_component = MagicMock()
    bridge._active_server_component = pinned_component
    exited_process = MagicMock()
    exited_process.poll.return_value = 1
    bridge.hosted_server_process = exited_process
    bridge._hosted_restart_inflight = True

    assert bridge.stop_hosted_server() is True

    assert bridge._active_server_component is pinned_component
    assert bridge.runtime_component_lease_active is True
    assert _cross_process_probe(bridge._runtime_component_lock_path) == "blocked"

    bridge._hosted_restart_inflight = False
    assert bridge.stop_hosted_server() is True
    assert bridge.runtime_component_lease_active is False
    assert _cross_process_probe(bridge._runtime_component_lock_path) == "acquired"


def test_unreachable_bridge_never_unlocks_under_a_live_owned_process(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    lock_path = bridge._runtime_component_lock_path
    assert bridge._acquire_runtime_component_lease("client")[0]
    live_process = MagicMock()
    live_process.poll.return_value = None
    bridge.jamulus_process = live_process
    bridge.jamulus_launch_intended = True
    lease = bridge._runtime_component_lease
    reference = weakref.ref(bridge)

    del bridge
    gc.collect()

    assert reference() is None
    assert _cross_process_probe(lock_path) == "blocked"
    assert lease is not None
    lease.__exit__(None, None, None)
    assert _cross_process_probe(lock_path) == "acquired"


def test_idle_unreachable_bridge_may_release_abandoned_descriptor(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    lock_path = bridge._runtime_component_lock_path
    assert bridge._acquire_runtime_component_lease("client")[0]
    reference = weakref.ref(bridge)

    del bridge
    gc.collect()

    assert reference() is None
    assert _cross_process_probe(lock_path) == "acquired"

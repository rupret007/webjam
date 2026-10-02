"""Opt-in diagnostics for synthetic CI processes, never a product telemetry path.

The private control file contains only owned fixture log locations. CI uploads
the supervisor's bounded projections, not this file, environments or RPC data.
"""
from __future__ import annotations

import faulthandler
import json
import os
import re
import signal
import stat
import threading
import time
from pathlib import Path

ROOT_ENV = "WEBJAM_JACK_DIAGNOSTICS"
MAX_LOG_BYTES = 32 * 1024
_lock = threading.RLock()
_state: dict = {"processes": [], "events": []}
_stack_file = None
_test = ""


def supervised() -> bool:
    return os.name == "posix" and bool(os.environ.get(ROOT_ENV))


def _root() -> Path | None:
    value = os.environ.get(ROOT_ENV)
    return Path(value) if value else None


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, sort_keys=True)
    os.replace(temporary, path)


def phase(name: str) -> None:
    root = _root()
    if root is None:
        return
    # Callers provide fixed phase labels, never RPC parameters or user content.
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name):
        raise ValueError("Invalid integration diagnostic phase")
    with _lock:
        event = {"phase": name, "test": _test, "monotonic": time.monotonic()}
        _state["events"] = [*_state["events"][-63:], event]
        _write(root / "phase.json", {"current": event, "events": _state["events"]})
        _write(root / ".control.json", {"processes": _state["processes"]})


def register_process(name: str, pid: int, log_path: Path) -> None:
    if _root() is None:
        return
    with _lock:
        label = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:60]
        _state["processes"] = [*_state["processes"][-63:], {
            "name": label, "pid": pid, "log_path": str(log_path),
        }]
        phase("process.started")


def archive_logs(root: Path | None = None) -> None:
    """Keep bounded tails before normal cleanup removes private fixture files."""
    root = root or _root()
    if root is None:
        return
    try:
        control = root / ".control.json"
        if control.stat().st_size > 128 * 1024:
            return
        records = json.loads(control.read_text(encoding="utf-8"))["processes"][-64:]
    except (OSError, ValueError, TypeError, KeyError):
        return
    destination = root / "process-logs"
    destination.mkdir(exist_ok=True)
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(item.get("name", "process")))[:60]
        try:
            descriptor = os.open(item["log_path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                handle.seek(max(0, info.st_size - MAX_LOG_BYTES))
                text = handle.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
            text = re.sub(r"(?im)^.*(?:secret|password|authorization|token|cookie).*$",
                          "<sensitive diagnostic omitted>", text)
            text = re.sub(r"(?:[A-Za-z]:[/\\]|/)[^\s\"']+", "<path>", text)
            target = destination / f"{index:02d}-{name}.log"
            target.write_bytes(text.encode("utf-8")[-MAX_LOG_BYTES:])
            target.chmod(0o600)
        except (OSError, ValueError, TypeError, KeyError):
            continue


def pytest_configure(config) -> None:
    del config
    global _stack_file
    root = _root()
    if root is None:
        return
    _stack_file = (root / "stacks.log").open("w", encoding="utf-8")
    os.chmod(root / "stacks.log", 0o600)
    faulthandler.register(signal.SIGUSR1, file=_stack_file, all_threads=True)
    _write(root / "stack-ready.json", {"pid": os.getpid()})
    phase("pytest.configured")


def pytest_unconfigure(config) -> None:
    del config
    global _stack_file
    if _stack_file is not None:
        faulthandler.unregister(signal.SIGUSR1)
        _stack_file.close()
        _stack_file = None
        root = _root()
        if root is not None:
            (root / "stack-ready.json").unlink(missing_ok=True)


def pytest_runtest_setup(item) -> None:
    global _test
    # Drop parametrized values; only a source-relative test name is retained.
    _test = item.nodeid.split("[", 1)[0][-240:]
    phase("pytest.setup")


def pytest_runtest_call(item) -> None:
    del item
    phase("pytest.call")


def pytest_runtest_teardown(item) -> None:
    del item
    phase("pytest.teardown")

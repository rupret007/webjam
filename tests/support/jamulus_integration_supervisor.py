"""Bound one synthetic Jamulus pytest process tree, preserving first failure.

Run as ``python -m tests.support.jamulus_integration_supervisor --artifacts DIR
--timeout-seconds 240 -- python -m pytest -p
tests.support.jamulus_integration_diagnostics ...``. The child owns a new POSIX
process group; supervised harness processes stay in it. No process-name search,
shell, retry, environment dump, profile, media or RPC payload is collected.
"""
from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from tests.support.jamulus_integration_diagnostics import ROOT_ENV, archive_logs

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_STACK_BYTES = 256 * 1024


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _members(group: int) -> list[dict]:
    """Inspect only numeric ownership facts; no command lines or environments."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat="], capture_output=True,
        text=True, timeout=1, check=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[2] == str(group):
            rows.append({"pid": int(fields[0]), "parent": int(fields[1]),
                         "group": group, "state": fields[3][:8]})
    return rows[:128]


def _live(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not row["state"].startswith("Z")]


def _signal_group(group: int, signum: int) -> None:
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def _cleanup(proc: subprocess.Popen, grace: float) -> dict:
    """Independent TERM/KILL deadlines apply even when a native close is stuck."""
    before = _members(proc.pid)
    signals = []
    if _live(before):
        for signum in (signal.SIGTERM, signal.SIGKILL):
            signals.append(signal.Signals(signum).name)
            _signal_group(proc.pid, signum)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                proc.poll()
                if not _live(_members(proc.pid)):
                    break
                time.sleep(0.05)
            if not _live(_members(proc.pid)):
                break
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    after = _members(proc.pid)
    return {"before": before, "after": after, "signals": signals,
            "all_owned_processes_stopped": not _live(after)}


def supervise(command: list[str], artifacts: Path, *, timeout: float,
              grace: float = 2.0, stack_grace: float = 0.5) -> int:
    if os.name != "posix":
        raise ValueError("Jamulus/JACK integration supervision requires POSIX")
    if not command or not 0 < timeout <= 600 or not 0 < grace <= 3 or not 0 <= stack_grace <= 2:
        raise ValueError("Invalid integration supervisor bounds")
    root = artifacts.absolute()
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    _json(root / "result.json", {"status": "starting", "success": False})
    env = os.environ.copy()
    env[ROOT_ENV] = str(root)
    env["PYTHONUNBUFFERED"] = "1"
    started = time.monotonic()
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    output_size = 0
    timed_out = False
    stack_dump_requested = False
    error = None
    cleanup = {"all_owned_processes_stopped": False}
    try:
        with (root / "pytest.log").open("wb") as log:
            while True:
                for key, _mask in selector.select(timeout=0.05):
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    keep = chunk[:max(0, MAX_OUTPUT_BYTES - output_size)]
                    log.write(keep)
                    sys.stdout.buffer.write(keep)
                    sys.stdout.buffer.flush()
                    output_size += len(keep)
                if proc.poll() is not None:
                    if not selector.get_map() or not selector.select(timeout=0):
                        break
                if time.monotonic() - started >= timeout:
                    timed_out = True
                    if (root / "stack-ready.json").exists() and proc.poll() is None:
                        proc.send_signal(signal.SIGUSR1)
                        stack_dump_requested = True
                        time.sleep(stack_grace)
                    break
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        error = type(exc).__name__
    finally:
        selector.close()
        try:
            archive_logs(root)
        except OSError as exc:
            error = type(exc).__name__
        try:
            cleanup = _cleanup(proc, grace)
        except (OSError, subprocess.SubprocessError) as exc:
            error = type(exc).__name__
            # Observation failure must not prevent killing our exact group.
            _signal_group(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        try:
            archive_logs(root)
        except OSError as exc:
            error = type(exc).__name__
        if proc.stdout is not None:
            proc.stdout.close()
        for name, limit in (("stacks.log", MAX_STACK_BYTES),):
            path = root / name
            if path.exists():
                with path.open("r+b") as handle:
                    handle.truncate(min(path.stat().st_size, limit))
        for name in (".control.json", ".control.json.tmp", "stack-ready.json"):
            (root / name).unlink(missing_ok=True)
    leaked = bool(cleanup.get("signals")) and not timed_out
    status = ("timeout" if timed_out else "supervisor_error" if error else
              "cleanup_failed" if not cleanup["all_owned_processes_stopped"] else
              "leaked_processes" if leaked else "passed" if proc.returncode == 0 else "failed")
    success = status == "passed"
    _json(root / "cleanup.json", cleanup)
    _json(root / "result.json", {
        "status": status, "success": success, "child_exit_code": proc.returncode,
        "timeout_seconds": timeout, "elapsed_seconds": time.monotonic() - started,
        "process_group": proc.pid, "stack_dump_requested": stack_dump_requested,
        "termination_grace_seconds": grace, "stack_grace_seconds": stack_grace,
        "output_limit_bytes": MAX_OUTPUT_BYTES, "output_limit_reached": output_size == MAX_OUTPUT_BYTES,
        "supervisor_error": error, "cleanup": cleanup,
    })
    print(f"\nJamulus integration supervisor: {status}; owned cleanup={cleanup['all_owned_processes_stopped']}", flush=True)
    return 0 if success else 124 if timed_out else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--timeout-seconds", required=True, type=float)
    parser.add_argument("--termination-grace", default=2.0, type=float)
    parser.add_argument("--stack-grace", default=0.5, type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    # A manual runner cancellation still enters our independently bounded
    # cleanup. SIGKILL or host loss cannot be handled by any in-process code.
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    return supervise(command, args.artifacts, timeout=args.timeout_seconds,
                     grace=args.termination_grace, stack_grace=args.stack_grace)


if __name__ == "__main__":
    raise SystemExit(main())

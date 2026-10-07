"""Exercise the CI deadline outside pytest's potentially blocked native thread."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.support.jamulus_integration_diagnostics import MAX_LOG_BYTES, archive_logs
from tests.support.jamulus_integration_supervisor import MAX_OUTPUT_BYTES

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux integration supervisor")
REPO = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path, source: str, *, timeout: float = 8) -> tuple[subprocess.CompletedProcess, Path]:
    fixture = tmp_path / "test_owned_fixture.py"
    fixture.write_text(textwrap.dedent(source), encoding="utf-8")
    artifacts = tmp_path / "diagnostics"
    env = os.environ.copy()
    # The generated fixture has no repository conftest or third-party plugins.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [
        sys.executable, "-m", "tests.support.jamulus_integration_supervisor",
        "--artifacts", str(artifacts), "--timeout-seconds", str(timeout),
        "--termination-grace", "0.3", "--stack-grace", "0.2", "--",
        sys.executable, "-m", "pytest", "-p", "tests.support.jamulus_integration_diagnostics",
        "--confcutdir", str(tmp_path), str(fixture), "-v", "-s",
    ]
    try:
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True,
                                text=True, timeout=timeout + 10, check=False)
        return result, artifacts
    finally:
        # Keep a failing regression test from stranding its synthetic children.
        ownership = tmp_path / "owned.json"
        if ownership.exists():
            group = json.loads(ownership.read_text())["group"]
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _result(root: Path) -> dict:
    return json.loads((root / "result.json").read_text())


def test_normal_cleanup_preserves_success_and_owned_log(tmp_path: Path) -> None:
    run, root = _run(tmp_path, """
        import json, os, sys
        from pathlib import Path
        from tests.support.jamulus_jack_harness import ManagedProcess

        def test_owned_cleanup():
            here = Path(__file__).parent
            process = ManagedProcess("synthetic-client", [sys.executable, "-u", "-c",
                "import time; print('client ready', flush=True); time.sleep(30)"],
                env=os.environ.copy(), log_path=here / "client.log")
            (here / "owned.json").write_text(json.dumps({"group": os.getpgrp()}))
            assert os.getpgid(process.proc.pid) == os.getpgrp() == os.getpid()
            import time
            until = time.monotonic() + 2
            while 'client ready' not in process.tail() and time.monotonic() < until:
                time.sleep(.01)
            process.stop()
            assert process.proc.poll() is not None
    """)
    assert run.returncode == 0, run.stdout + run.stderr
    result = _result(root)
    assert result["status"] == "passed"
    assert result["cleanup"]["all_owned_processes_stopped"]
    assert result["cleanup"]["signals"] == []
    assert not result["stack_dump_requested"]
    assert "client ready" in next((root / "process-logs").glob("*.log")).read_text()
    assert not (root / ".control.json").exists()


def test_disconnect_reconnect_pauses_only_the_selected_supervised_client(tmp_path: Path) -> None:
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 start_new_session=True)
    try:
        run, root = _run(tmp_path, """
            import json, os, subprocess, sys, time
            from pathlib import Path
            from tests.support.jamulus_jack_harness import ManagedProcess, JamulusJackHarness

            def test_owned_client_pause_and_resume():
                here = Path(__file__).parent
                children = []
                def ticks(child):
                    return child.tail().count('tick')
                def state(child):
                    return subprocess.check_output(
                        ['ps', '-o', 'stat=', '-p', str(child.proc.pid)], text=True).strip()
                try:
                    child_code = chr(10).join((
                        'import time', 'while True:',
                        "    print('tick', flush=True)", '    time.sleep(.02)',
                    ))
                    for name in ('sibling', 'target'):
                        child = ManagedProcess(name, [sys.executable, '-u', '-c', child_code],
                            env=os.environ.copy(), log_path=here / (name + '.log'))
                        children.append(child)
                    (here / 'owned.json').write_text(json.dumps({'group': os.getpgrp()}))
                    sibling, target = children
                    assert os.getpgid(target.proc.pid) == os.getpgid(sibling.proc.pid) == os.getpgrp()
                    deadline = time.monotonic() + 2
                    while not all(ticks(child) for child in children):
                        assert time.monotonic() < deadline, 'synthetic clients did not start'
                        time.sleep(.01)
                    harness = JamulusJackHarness.__new__(JamulusJackHarness)
                    harness.include_reference_track = False
                    harness.server_process = sibling
                    harness.client_processes = children
                    observed = {}
                    def roster_while_paused():
                        if not state(target).startswith('T'):
                            return None
                        target_ticks = ticks(target)
                        sibling_ticks = ticks(sibling)
                        deadline = time.monotonic() + 2
                        while ticks(sibling) <= sibling_ticks:
                            assert ticks(target) == target_ticks, 'selected client did not stay paused'
                            assert time.monotonic() < deadline, 'sibling stopped with selected client'
                            time.sleep(.01)
                        assert ticks(target) == target_ticks, 'selected client did not stay paused'
                        observed['paused_ticks'] = target_ticks
                        return {'connections': 1, 'clients': [{'name': harness.CLIENT_A_NAME}]}
                    def roster_after_resume():
                        if state(target).startswith('T') or ticks(target) <= observed['paused_ticks']:
                            return None
                        observed['resumed'] = True
                        return tuple({'name': name} for name in harness.expected_client_names)
                    # Only roster observations are controlled. The actual
                    # reconnect method, wait loop and process signals execute.
                    harness._server_roster = roster_while_paused
                    harness._connected_clients = roster_after_resume
                    recovered = harness.exercise_disconnect_reconnect(client_index=1, timeout_s=2)
                    assert {entry['name'] for entry in recovered} == set(harness.expected_client_names)
                    assert observed['resumed']
                    assert all(child.proc.poll() is None for child in children)
                finally:
                    for child in reversed(children):
                        child.stop()
        """, timeout=12)
        assert run.returncode == 0, run.stdout + run.stderr
        result = _result(root)
        assert result["status"] == "passed"
        assert result["cleanup"]["all_owned_processes_stopped"]
        assert result["cleanup"]["signals"] == []
        assert len(list((root / "process-logs").glob("*.log"))) == 2
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=3)


def test_first_assertion_failure_is_retained_without_retry(tmp_path: Path) -> None:
    run, root = _run(tmp_path, """
        from pathlib import Path
        def test_original_assertion():
            with (Path(__file__).parent / "attempts").open("a") as out:
                out.write("attempt\n")
            assert False, "original audio identity assertion"
    """.replace('"attempt\n"', '"attempt\\n"'))
    assert run.returncode == 1
    assert _result(root)["status"] == "failed"
    assert (tmp_path / "attempts").read_text().splitlines() == ["attempt"]
    assert "original audio identity assertion" in (root / "pytest.log").read_text()


@pytest.mark.parametrize("blocked_method", ["deactivate", "close"])
def test_native_close_stall_has_stacks_and_independent_owned_cleanup(
    tmp_path: Path, blocked_method: str,
) -> None:
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 start_new_session=True)
    started = time.monotonic()
    try:
        run, root = _run(tmp_path, f"""
            import ctypes, json, os, sys, time
            from pathlib import Path
            from tests.support.jamulus_jack_harness import ManagedProcess, JackBoundary, JamulusJackHarness

            class NativeClient:
                def deactivate(self):
                    if {blocked_method!r} == 'deactivate':
                        while True:
                            ctypes.CDLL(None).pause()
                def close(self):
                    if {blocked_method!r} == 'close':
                        while True:
                            ctypes.CDLL(None).pause()

            def test_stalled_native_cleanup():
                here = Path(__file__).parent
                children = []
                for index in range(2):
                    child = ManagedProcess('owned-' + str(index), [sys.executable, '-u', '-c',
                        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"],
                        env=os.environ.copy(), log_path=here / ('client-' + str(index) + '.log'))
                    children.append(child)
                (here / 'owned.json').write_text(json.dumps({{'group': os.getpgrp()}}))
                until = time.monotonic() + 2
                while not all('ready' in child.tail() for child in children):
                    assert time.monotonic() < until, 'synthetic children did not start'
                    time.sleep(.01)
                boundary = JackBoundary.__new__(JackBoundary)
                boundary.client = NativeClient()
                harness = JamulusJackHarness.__new__(JamulusJackHarness)
                harness._closed = False
                harness.server_rpc = None
                harness.boundary = boundary
                harness.processes = children
                harness.close()
                assert False, 'native stall unexpectedly returned'
        """, timeout=3)
        assert run.returncode == 124, run.stdout + run.stderr
        assert time.monotonic() - started < 10
        result = _result(root)
        assert result["status"] == "timeout"
        assert result["stack_dump_requested"]
        phase = json.loads((root / "phase.json").read_text())["current"]
        assert phase["phase"] == "jack." + blocked_method
        assert phase["test"].endswith("::test_stalled_native_cleanup")
        stacks = (root / "stacks.log").read_text()
        assert f"in {blocked_method}" in stacks
        assert "jamulus_jack_harness.py" in stacks
        cleanup = result["cleanup"]
        assert len(cleanup["before"]) >= 3
        assert cleanup["signals"] == ["SIGTERM", "SIGKILL"]
        assert cleanup["all_owned_processes_stopped"]
        assert all(row["group"] == result["process_group"] for row in cleanup["before"])
        assert len(list((root / "process-logs").glob("*.log"))) == 2
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=3)


def test_successful_pytest_with_leaked_child_is_a_failure(tmp_path: Path) -> None:
    run, root = _run(tmp_path, """
        import json, os, sys
        from pathlib import Path
        from tests.support.jamulus_jack_harness import ManagedProcess
        def test_accidentally_leaked_child():
            here = Path(__file__).parent
            child = ManagedProcess("leaked-client", [sys.executable, "-c", "import time; time.sleep(30)"],
                env=os.environ.copy(), log_path=here / "client.log")
            (here / "owned.json").write_text(json.dumps({"group": os.getpgrp()}))
            assert child.proc.poll() is None
    """)
    assert run.returncode == 1, run.stdout + run.stderr
    result = _result(root)
    assert result["child_exit_code"] == 0
    assert result["status"] == "leaked_processes"
    assert result["cleanup"]["all_owned_processes_stopped"]


def test_bootstrap_stall_does_not_claim_a_stack_dump(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics"
    command = [sys.executable, "-m", "tests.support.jamulus_integration_supervisor",
               "--artifacts", str(root), "--timeout-seconds", ".2",
               "--termination-grace", ".2", "--", sys.executable,
               "-c", "import time; time.sleep(30)"]
    run = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=5)
    assert run.returncode == 124, run.stdout + run.stderr
    result = _result(root)
    assert not result["stack_dump_requested"]
    assert result["cleanup"]["all_owned_processes_stopped"]
    assert not (root / "stacks.log").exists()


def test_output_is_bounded_without_changing_success(tmp_path: Path) -> None:
    run, root = _run(tmp_path, f"""
        def test_verbose_child():
            print('a' * {MAX_OUTPUT_BYTES + 100_000})
    """)
    assert run.returncode == 0
    assert _result(root)["output_limit_reached"]
    assert (root / "pytest.log").stat().st_size == MAX_OUTPUT_BYTES


def test_process_log_projection_bounds_and_redacts_private_details(tmp_path: Path) -> None:
    log = tmp_path / "raw.log"
    log.write_bytes(b"old-output\n" * MAX_LOG_BYTES + b"\xff" * MAX_LOG_BYTES)
    with log.open("ab") as handle:
        handle.write(b"\npassword=synthetic-secret\nopened /private/fixture/settings.xml\nready\n")
    pipe = tmp_path / "not-a-log"
    os.mkfifo(pipe)
    (tmp_path / ".control.json").write_text(json.dumps({"processes": [
        {"name": "fixture", "log_path": str(log)},
        {"name": "pipe", "log_path": str(pipe)},
    ]}))
    archive_logs(tmp_path)
    projected = tmp_path / "process-logs" / "00-fixture.log"
    assert projected.stat().st_size <= MAX_LOG_BYTES
    content = projected.read_text(errors="replace")
    assert "synthetic-secret" not in content
    assert "/private/fixture" not in content
    assert "<sensitive diagnostic omitted>" in content
    assert "<path>" in content
    assert content.endswith("ready\n")
    assert not (tmp_path / "process-logs" / "01-pipe.log").exists()

"""Frozen Reference Studio runtime-smoke contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path
import subprocess
import sys

import pytest

from services.reference_studio_packaged_smoke import (
    SUCCESS_MARKER,
    run_frozen_reference_studio_smoke,
)


def test_reference_studio_packaged_smoke_exercises_complete_core_path() -> None:
    with tempfile.TemporaryDirectory(
        prefix="webjam-reference-studio-smoke-"
    ) as directory:
        result = Path(directory) / "result.txt"

        assert run_frozen_reference_studio_smoke(result_path=result) == 0

        assert result.read_text(encoding="utf-8") == SUCCESS_MARKER + "\n"
        trace = (result.parent / "diagnostics.log").read_text(encoding="utf-8")
        assert "Continuity: complete; portability: begin" in trace
        assert "Portability: initial Library Open complete" in trace
        assert trace.endswith("Success marker: complete; hook return\n")


def test_reference_studio_packaged_smoke_rejects_unowned_result_path(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.txt"

    with pytest.raises(RuntimeError, match="result path is invalid"):
        run_frozen_reference_studio_smoke(result_path=result)

    assert not result.exists()


def test_windowed_smoke_keeps_flushed_phase_after_abrupt_process_exit():
    with tempfile.TemporaryDirectory(prefix="webjam-reference-studio-smoke-") as directory:
        result = Path(directory) / "result.txt"
        child = subprocess.run([
            sys.executable, "-c",
            "import os, sys, faulthandler\n"
            "from pathlib import Path\n"
            "from services.packaged_smoke_diagnostics import checkpoint, diagnostic_trace\n"
            "sys.frozen = True\n"
            "sys.stderr = None\n"
            "with diagnostic_trace(Path(sys.argv[1])):\n"
            "    assert faulthandler.is_enabled()\n"
            "    checkpoint('Owned phase before abrupt exit')\n"
            "    os._exit(73)\n",
            str(result),
        ], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
        assert child.returncode == 73, child.stderr
        assert not result.exists()
        assert (result.parent / "diagnostics.log").read_text() == "Owned phase before abrupt exit\n"


@pytest.mark.parametrize("frozen", [False, True])
def test_smoke_keeps_native_worker_abort_reason(frozen):
    with tempfile.TemporaryDirectory(prefix="webjam-reference-studio-smoke-") as directory:
        result = Path(directory) / "result.txt"
        child = subprocess.run([
            sys.executable, "-c",
            "import os, sys, threading\n"
            "if os.name != 'nt':\n"
            "    import resource\n"
            "    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
            "from pathlib import Path\n"
            "from PySide6.QtCore import qFatal\n"
            "from services.packaged_smoke_diagnostics import checkpoint, diagnostic_trace\n"
            "sys.frozen = sys.argv[2] == 'frozen'\n"
            "sys.stderr = None\n"
            "with diagnostic_trace(Path(sys.argv[1])):\n"
            "    checkpoint('Owned phase before worker abort')\n"
            "    worker = threading.Thread(target=lambda: qFatal('Owned native worker abort'))\n"
            "    worker.start()\n"
            "    worker.join()\n",
            str(result), "frozen" if frozen else "source",
        ], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
        assert child.returncode != 0
        assert not result.exists()
        trace = (result.parent / "diagnostics.log").read_text()
        assert "Owned phase before worker abort\n" in trace
        assert "Owned native worker abort\n" in trace
        assert "Qt QtFatalMsg:" in trace


@pytest.mark.parametrize("frozen", [False, True])
def test_smoke_forwards_and_restores_existing_qt_handler(monkeypatch, frozen):
    from PySide6.QtCore import qInstallMessageHandler, qWarning
    from services.packaged_smoke_diagnostics import diagnostic_trace

    received = []
    previous = qInstallMessageHandler(lambda _kind, _context, message: received.append(message))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    try:
        with tempfile.TemporaryDirectory(prefix="webjam-reference-studio-smoke-") as directory:
            result = Path(directory) / "result.txt"
            with diagnostic_trace(result):
                qWarning("Owned diagnostic warning")
            qWarning("Restored diagnostic warning")
            trace = (result.parent / "diagnostics.log").read_text()
            assert "Owned diagnostic warning" in trace
            assert "Restored diagnostic warning" not in trace
        assert received == ["Owned diagnostic warning", "Restored diagnostic warning"]
    finally:
        qInstallMessageHandler(previous)


@pytest.mark.parametrize("failure", ["native_abort", "timeout"])
def test_frozen_runner_reports_owned_phase_when_child_cannot_report(monkeypatch, tmp_path, failure):
    from tests.support import run_frozen_reference_studio_smoke as runner
    binary = tmp_path / "owned-binary"
    binary.write_bytes(b"test runner boundary")
    monkeypatch.setattr(sys, "argv", ["smoke", "--binary", str(binary)])

    def child(*args, **kwargs):
        result = Path(kwargs["env"]["WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT"])
        (result.parent / "diagnostics.log").write_text("Last completed phase\n")
        assert kwargs["timeout"] == 60
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 60)
        return subprocess.CompletedProcess(args[0], 3221226505, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", child)
    with pytest.raises(SystemExit, match="Last completed phase"):
        runner.main()


@pytest.mark.parametrize("exit_code,marker,dumps,timed_out,expected", [
    (0, "", [], False, "DIAGNOSTIC_FAILED_WITHOUT_NATIVE_DUMP"),
    (3, SUCCESS_MARKER + "\n", [], False, "DIAGNOSTIC_FAILED_WITHOUT_NATIVE_DUMP"),
    (0, SUCCESS_MARKER + "\n", ["abort.dmp"], False, "NATIVE_FAILURE_CAPTURED"),
    (0, SUCCESS_MARKER + "\n", [], True, "DIAGNOSTIC_TIMEOUT"),
    (0, SUCCESS_MARKER + "\n", [], False, "FAILURE_NOT_REPRODUCED_UNDER_DEBUGGER"),
])
def test_native_debugger_exit_code_cannot_replace_application_proof(
    exit_code, marker, dumps, timed_out, expected,
):
    from tests.support.diagnose_windows_reference_studio import classify

    record = {"debugger_returncode": exit_code, "native_dumps": dumps, "timed_out": timed_out}
    assert classify(record, marker) == expected


@pytest.mark.parametrize("tree_kill_times_out", [False, True])
def test_native_diagnostic_timeout_reaps_owned_debugger_tree(monkeypatch, tmp_path, tree_kill_times_out):
    from tests.support import diagnose_windows_reference_studio as diagnostic

    class Process:
        pid = 1234
        returncode = None
        waits = []

        def wait(self, timeout):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("owned debugger", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = Process()
    tree_kills = []
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *args, **kwargs: process)

    def kill_tree(argv, **kwargs):
        tree_kills.append((argv, kwargs["timeout"]))
        if tree_kill_times_out:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(diagnostic.subprocess, "run", kill_tree)
    arguments = dict(debugger=tmp_path / "cdb.exe", argv=["owned.exe"],
                     output=tmp_path / "diagnostics", environment={}, cwd=tmp_path, timeout=120)
    if tree_kill_times_out:
        with pytest.raises(subprocess.TimeoutExpired):
            diagnostic.capture(**arguments)
    else:
        record = diagnostic.capture(**arguments)
        assert record["timed_out"] is True
        assert record["native_dumps"] == []
    assert tree_kills == [(["taskkill", "/PID", "1234", "/T", "/F"], 15)]
    assert process.returncode == -9
    assert process.waits == [120, 10]


def test_native_debugger_rejects_command_metacharacters_before_launch(tmp_path):
    from tests.support.diagnose_windows_reference_studio import commands

    with pytest.raises(ValueError, match="simple absolute path"):
        commands(tmp_path / 'unowned"; q')


@pytest.mark.parametrize("damage", [
    None, "header_only", "directory_outside_file", "missing_modules",
    "empty_threads", "stack_outside_file", "truncated_context",
])
def test_native_abort_control_requires_retained_thread_stack_and_modules(tmp_path, damage):
    import struct
    from tests.support.diagnose_windows_reference_studio import has_native_stack_data

    # A bounded structural fixture: two directory entries, one thread and
    # module record, followed by the thread's stack and register-context data.
    data = bytearray(252)
    struct.pack_into("<4sIII", data, 0, b"MDMP", 0xA793, 2, 32)
    struct.pack_into("<III", data, 32, 3, 52, 56)
    struct.pack_into("<III", data, 44, 4, 112, 108)
    struct.pack_into("<I", data, 56, 1)
    struct.pack_into("<IIII", data, 92, 16, 220, 16, 236)
    struct.pack_into("<I", data, 108, 1)
    if damage == "header_only":
        data = data[:32]
    elif damage == "directory_outside_file":
        struct.pack_into("<I", data, 12, 10000)
    elif damage == "missing_modules":
        struct.pack_into("<I", data, 44, 9)
    elif damage == "empty_threads":
        struct.pack_into("<I", data, 56, 0)
    elif damage == "stack_outside_file":
        struct.pack_into("<I", data, 96, 10000)
    elif damage == "truncated_context":
        data = data[:244]
    path = tmp_path / "abort.dmp"
    path.write_bytes(data)
    assert has_native_stack_data(path) is (damage is None)


@pytest.mark.parametrize("change", [None, "executable", "package_marker", "source_head"])
def test_native_diagnostics_bind_extracted_binary_to_retained_package(tmp_path, change):
    import zipfile
    from tests.support.diagnose_windows_reference_studio import package_binding

    head = "a" * 40
    app = tmp_path / "WebJam"
    (app / "_internal").mkdir(parents=True)
    binary = app / "WebJam.exe"
    binary.write_bytes(b"original")
    (app / "_internal/webjam-build-id.txt").write_text(head + "\n")
    package = tmp_path / "WebJam-windows-x64.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("WebJam/WebJam.exe", b"original")
        archive.writestr("WebJam/_internal/webjam-build-id.txt",
                         ("b" * 40 if change == "package_marker" else head) + "\n")
    if change == "executable":
        binary.write_bytes(b"tampered")  # Same size; identity requires bytes.
    if change == "source_head":
        head = "c" * 40
    if change:
        with pytest.raises(ValueError, match="differ"):
            package_binding(binary, package, head)
    else:
        assert package_binding(binary, package, head)["packaged_and_extracted_source_head"] == head

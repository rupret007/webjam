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

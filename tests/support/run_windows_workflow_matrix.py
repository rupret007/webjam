"""One explicit, bounded diagnostic matrix; never retries a failed attempt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from tests.support import diagnose_windows_reference_studio as diagnostic
from tests.support.windows_owned_process import run_owned


def experiment_plan() -> list[dict]:
    return [{"id": f"{kind}-{number}", "kind": kind, "backend": backend, "worker_gc": mode}
            for number, (backend, mode) in enumerate(
                [(backend, mode) for backend in ("plain", "cdb")
                 for mode in ("ordinary", "before", "after")], 1)
            for kind in ("source", "frozen")]


def verify_attempt(record: dict, private: Path, pid: int | None) -> None:
    if not pid or record["process"]["timed_out"] or not record["process"]["cleanup_verified"]:
        raise RuntimeError("Workflow process identity, deadline or cleanup failed")
    if record["process"]["returncode"] != 0 or record["process"]["members_before_cleanup"]:
        raise RuntimeError("Workflow exited unsuccessfully or left owned descendants")
    capture = record.get("capture", {})
    if capture.get("native_dumps") or capture.get("invalid_native_dumps"):
        raise RuntimeError("Workflow produced a native failure dump")
    if record["backend"] == "cdb" and capture.get("debuggee_returncode") != 0:
        raise RuntimeError("Debuggee exit code is nonzero or unproven")
    if (private / "result.txt").read_text(encoding="utf-8") != diagnostic.SUCCESS_MARKER + "\n":
        raise RuntimeError("Workflow success marker is missing or incorrect")
    if not (private / "diagnostics.log").read_text(encoding="utf-8").endswith(
            "Success marker: complete; hook return\n"):
        raise RuntimeError("Complete workflow terminal phase is missing")
    if record["kind"] == "source":
        source = json.loads((private / "source-process.json").read_text(encoding="utf-8"))
        if source["pid"] != pid or source["worker_gc"] != record["worker_gc"]:
            raise RuntimeError("Native child identity or GC mode differs")
        record["source_runtime"] = source
    probe = private / "gc-probe.jsonl"
    if record["worker_gc"] == "ordinary":
        if probe.exists():
            raise RuntimeError("Ordinary attempt unexpectedly injected GC")
    else:
        events = [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines()]
        before = [event for event in events if event["phase"] == "before-collection"]
        after = [event for event in events if event["phase"] == "after-collection"]
        if (not after or len(after) != len(before)
                or [event["event"] for event in before] != [event["event"] for event in after]
                or any(event["pid"] != pid or event["thread"] == "MainThread"
                       or event["mode"] != record["worker_gc"] for event in events)):
            raise RuntimeError("Requested worker-GC collections are unproven")
        record["verified_worker_collections"] = len(after)


def attempt(record: dict, binary: Path, debugger: Path, output: Path) -> None:
    output.mkdir()
    private = Path(tempfile.mkdtemp(prefix="webjam-reference-studio-smoke-"))
    record.update(private_root=str(private), started_utc=datetime.now(timezone.utc).isoformat())
    environment = diagnostic.diagnostic_environment()
    environment.update(QT_QPA_PLATFORM="offscreen", WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME="1",
                       WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT=str(private / "result.txt"),
                       WEBJAM_SMOKE_WORKER_GC=record["worker_gc"],
                       WEBJAM_LOG_FILE=str(private / "app.log"))
    for key in ("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME", "WEBJAM_SMOKE_LAUNCH_ONLY", "WEBJAM_SMOKE_AUTOSTART_AUDIO"):
        environment.pop(key, None)
    record["qt_fatal_environment"] = {key: environment.get(key) for key in
                                       ("QT_FATAL_WARNINGS", "QT_FATAL_CRITICALS")}
    if record["kind"] == "source":
        argv = [sys.executable, "-X", "dev", "-m", "tests.support.run_source_reference_studio_smoke",
                "--result", str(private / "result.txt")]
        cwd = diagnostic.ROOT
    else:
        argv, cwd = [str(binary)], binary.parent
    record["argv"] = argv
    try:
        if record["backend"] == "plain":
            record["process"] = run_owned(argv, cwd=cwd, environment=environment,
                                           console=output / "console.log", timeout=60)
            pid = record["process"]["pid"]
        else:
            record["capture"] = diagnostic.capture(debugger, argv, output / "cdb",
                                                    cwd=cwd, environment=environment, timeout=120)
            record["process"] = record["capture"]["owned_process"]
            pid = record["capture"]["debuggee_pid"]
        record["workflow_pid"] = pid
        verify_attempt(record, private, pid)
        record["status"] = "PASSED"
    except Exception as exc:
        record.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for name in ("diagnostics.log", "result.txt", "app.log", "gc-probe.jsonl", "source-process.json"):
            source = private / name
            if source.is_file() and not source.is_symlink():
                shutil.copyfile(source, output / name)
        if record.get("process", {}).get("cleanup_verified"):
            shutil.rmtree(private)
            record["private_root_removed_after_capture"] = True
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "attempt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This matrix requires actual Windows")
    output, binary, package = args.output.resolve(), args.binary.resolve(), args.package.resolve()
    diagnostic.commands(output)
    output.mkdir(parents=True)  # Exclusive: no unnoticed restart/overwrite.
    report = {"status": "STARTING", "source_head": args.source_head, "plan": experiment_plan(),
              "attempts": [], "original_gate": "NOT_RUN_BY_MATRIX",
              "limits": "Six attempts per kind; stop on first unexpected failure. No automatic retries.",
              "gc_after_boundary": "After operation return, before terminal-result publication; not thread retirement."}
    receipt = output / "matrix.json"

    def save():
        receipt.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        report["source"] = diagnostic.source_binding(args.source_head)
        report["package"] = diagnostic.package_binding(binary, package, args.source_head)
        debugger, report["debugger"] = diagnostic.debugger_identity()
        report["control"] = diagnostic.run_control(debugger, output, diagnostic.diagnostic_environment())
        save()
        for planned in report["plan"]:
            record = {**planned, "status": "STARTING"}
            report["attempts"].append(record)
            save()
            try:
                attempt(record, binary, debugger, output / record["id"])
                if diagnostic.source_binding(args.source_head) != report["source"]:
                    raise ValueError("Source changed during matrix")
                if diagnostic.package_binding(binary, package, args.source_head) != report["package"]:
                    raise ValueError("Package changed during matrix")
            except Exception as exc:
                record.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                attempt_path = output / record["id"]
                if attempt_path.is_dir():
                    (attempt_path / "attempt.json").write_text(
                        json.dumps(record, indent=2) + "\n", encoding="utf-8")
                save()
                print(json.dumps({key: record[key] for key in ("id", "status", "backend", "worker_gc")}), flush=True)
        report["status"] = "BOUNDED_MATRIX_PASSED_CAUSE_UNRESOLVED"
    except Exception as exc:
        report.update(status="STOPPED_FIRST_UNEXPECTED_FAILURE", error=f"{type(exc).__name__}: {exc}")
    finally:
        report["unexecuted"] = report["plan"][len(report["attempts"]):]
        report["evidence"] = {path.relative_to(output).as_posix():
                              {"bytes": path.stat().st_size, "sha256": diagnostic.digest(path)}
                              for path in output.rglob("*") if path.is_file() and not path.is_symlink()
                              and path != receipt}
        save()
    return 0 if report["status"] == "BOUNDED_MATRIX_PASSED_CAUSE_UNRESOLVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

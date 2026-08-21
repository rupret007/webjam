#!/usr/bin/env python3
"""Run WebJam's deterministic multitrack proof matrix in fresh processes.

This is test orchestration, not a recorder or product runtime.  It deliberately
keeps the matrix fixed, gives every iteration a new pytest process and basetemp,
and writes only bounded, path-free aggregate evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ITERATIONS = 20
MIN_FREE_BYTES = 750 * 1024 * 1024
MAX_TEMP_BYTES = 250 * 1024 * 1024
MAX_REPORT_BYTES = 100 * 1024
DEFAULT_TIMEOUT_SECONDS = 30 * 60
REPORT_SCHEMA = "webjam.multitrack-proof-runner.v1"

# Keep this list narrow and reviewable.  The end-to-end module owns the broad
# deterministic matrix; these existing node IDs pin the production boundaries
# most likely to regress independently of that orchestration.
PROOF_MATRIX: tuple[str, ...] = (
    "tests/test_multitrack_proof_lab.py",
    "tests/test_v026_podcast_voice_journey.py",
    (
        "tests/test_session_recording_plan.py::"
        "test_plan_binds_stable_server_and_host_logical_sources_across_takes"
    ),
    (
        "tests/test_session_recording_plan.py::"
        "test_capture_resolver_preserves_logical_stereo_and_opt_out_truth"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_guest_capture_arm_acknowledges_before_server_start_path"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_guest_capture_arm_timeout_retires_take_without_server_start"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_guest_capture_arm_rechecks_authority_after_ack"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_planned_shared_track_transaction_waits_for_playback_and_cleanup"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_shared_track_loss_during_preflight_refuses_recording_start"
    ),
    (
        "tests/test_session_transfer_shared_track.py::"
        "test_finalizing_signal_is_idempotent_durable_and_available_on_host_runtime"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_relaunch_reconciles_partial_server_filename_staging_without_guessing"
    ),
    (
        "tests/test_server_rpc_and_record_button.py::TestRecordButtonWiring::"
        "test_wrong_checksum_staging_cannot_claim_or_retire_linked_journal"
    ),
    (
        "tests/test_recording_studio.py::"
        "test_completed_take_automatically_stacks_only_exact_repeated_source"
    ),
    (
        "tests/test_recording_studio.py::"
        "test_repeated_take_lane_comp_audition_export_and_reopen"
    ),
    (
        "tests/test_studio_export.py::"
        "test_studio_export_preserves_stereo_local_original_as_one_source"
    ),
    (
        "tests/test_studio_export.py::"
        "test_cancellation_removes_unpublished_audio_and_temp_folder"
    ),
)

_SUMMARY_PATTERN = re.compile(
    r"(?P<count>[0-9]+)\s+"
    r"(?P<kind>passed|failed|skipped|errors?|xfailed|xpassed)\b"
)


@dataclass(frozen=True)
class _PytestInvocation:
    returncode: int
    stdout: str
    timed_out: bool = False


def _positive_int(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= DEFAULT_ITERATIONS:
        raise argparse.ArgumentTypeError(
            f"value must be between 1 and {DEFAULT_ITERATIONS}"
        )
    return parsed


def _timeout_seconds(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 2 * 60 * 60:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 7200 seconds")
    return parsed


def _available_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _tree_size_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return total
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            try:
                total += (current_path / name).lstat().st_size
            except FileNotFoundError:
                continue
    return total


def _remove_tree(root: Path) -> bool:
    """Remove one run-owned tree and report the verified outcome."""

    if not root.exists():
        return True
    try:
        shutil.rmtree(root, ignore_errors=False)
    except OSError:
        return False
    return not root.exists()


def _lab_report_digest(root: Path) -> str:
    """Return the one bounded inner lab report digest, or fail closed."""

    matches = tuple(root.rglob("proof-report.json")) if root.exists() else ()
    if len(matches) != 1 or not matches[0].is_file():
        return ""
    try:
        payload = matches[0].read_bytes()
        if not payload or len(payload) >= MAX_REPORT_BYTES:
            return ""
        document = json.loads(payload)
    except (OSError, json.JSONDecodeError):
        return ""
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != "webjam.multitrack-proof-lab.v1"
        or document.get("overall_status") != "passed"
    ):
        return ""
    return hashlib.sha256(payload).hexdigest()


def _source_sha() -> str:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if re.fullmatch(r"[0-9a-f]{40}", value):
        return value
    return "unavailable"


def _source_tree_facts() -> tuple[bool, str]:
    """Bind evidence to relevant tracked and proof-lab working-tree bytes."""

    def is_intended_untracked(relative: str) -> bool:
        if relative == "_to_delete/" or relative.startswith("_to_delete/"):
            return True
        return relative.endswith(".bundle")

    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="strict")
        diff = subprocess.check_output(
            ["git", "diff", "--binary", "HEAD", "--"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False, "unavailable"
    relevant_untracked: list[Path] = []
    relevant_status: list[str] = []
    for line in status.splitlines():
        if line.startswith("?? "):
            relative = line[3:]
            if is_intended_untracked(relative):
                continue
            path = ROOT / relative
            if path.is_file():
                relevant_untracked.append(path)
            relevant_status.append(line[:2])
            continue
        relevant_status.append(line[:2])
    digest = hashlib.sha256(diff)
    for path in sorted(
        relevant_untracked, key=lambda item: item.relative_to(ROOT).as_posix()
    ):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    clean = not relevant_status
    return clean, digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _matrix_digest() -> str:
    payload = json.dumps(PROOF_MATRIX, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _summary_counts(output: str) -> dict[str, int]:
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    for match in _SUMMARY_PATTERN.finditer(output[-4096:]):
        kind = match.group("kind")
        if kind in {"error", "errors"}:
            kind = "errors"
        counts[kind] = int(match.group("count"))
    return counts


def _run_pytest(basetemp: Path, timeout_seconds: int) -> _PytestInvocation:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "QT_QPA_PLATFORM": "offscreen",
        }
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        "--basetemp",
        str(basetemp),
        *PROOF_MATRIX,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = ""
        if isinstance(exc.stdout, str):
            output = exc.stdout
        return _PytestInvocation(returncode=124, stdout=output, timed_out=True)
    except OSError:
        return _PytestInvocation(returncode=125, stdout="")
    return _PytestInvocation(
        returncode=int(completed.returncode),
        stdout=f"{completed.stdout}\n{completed.stderr}",
    )


def _atomic_write_report(path: Path, report: dict[str, object]) -> None:
    payload = (
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    if len(payload) >= MAX_REPORT_BYTES:
        raise ValueError("proof report exceeded its fixed size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _base_report(iterations: int, free_before: int) -> dict[str, object]:
    source_tree_clean, source_diff_sha256 = _source_tree_facts()
    return {
        "schema_version": REPORT_SCHEMA,
        "classification": "automated_source_evidence",
        "physical_status": "not_run",
        "source_sha": _source_sha(),
        "source_tree_clean": source_tree_clean,
        "source_diff_sha256": source_diff_sha256,
        "matrix_sha256": _matrix_digest(),
        "matrix_target_count": len(PROOF_MATRIX),
        "environment": {
            "os": platform.system().lower() or "unknown",
            "architecture": platform.machine().lower() or "unknown",
            "python": platform.python_version(),
        },
        "limits": {
            "minimum_free_bytes": MIN_FREE_BYTES,
            "maximum_iteration_temp_bytes": MAX_TEMP_BYTES,
            "maximum_report_bytes": MAX_REPORT_BYTES,
        },
        "requested_iterations": iterations,
        "qualification_iterations": DEFAULT_ITERATIONS,
        "qualification_complete": False,
        "started_utc": _utc_now(),
        "finished_utc": "",
        "overall_status": "running",
        "free_bytes_before": free_before,
        "free_bytes_after": free_before,
        "total_temp_bytes": 0,
        "peak_temp_bytes": 0,
        "run_root_cleanup_ok": True,
        "iterations": [],
        "limitations": [
            "synthetic_inputs_only",
            "real_jamulus_not_exercised",
            "physical_audio_not_run",
            "hardware_and_accessibility_not_run",
            "package_trust_not_run",
        ],
    }


def run_lab(
    *,
    iterations: int,
    report_path: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """Execute the fixed proof matrix and write sanitized aggregate evidence."""

    free_before = _available_bytes(ROOT)
    report = _base_report(iterations, free_before)
    if free_before < MIN_FREE_BYTES:
        report["overall_status"] = "blocked_disk_floor"
        report["finished_utc"] = _utc_now()
        _atomic_write_report(report_path, report)
        return 2

    run_root = Path(tempfile.mkdtemp(prefix="webjam-multitrack-proof-"))
    cumulative_temp_bytes = 0
    peak_temp_bytes = 0
    exit_code = 0
    try:
        run_records = report["iterations"]
        assert isinstance(run_records, list)
        for iteration in range(1, iterations + 1):
            if _available_bytes(ROOT) < MIN_FREE_BYTES:
                report["overall_status"] = "blocked_disk_floor"
                exit_code = 2
                break

            basetemp = run_root / f"iteration-{iteration:02d}"
            started = time.monotonic()
            invocation: _PytestInvocation | None = None
            artifact_bytes = 0
            report_sha256 = ""
            cleanup_ok = False
            try:
                invocation = _run_pytest(basetemp, timeout_seconds)
                artifact_bytes = _tree_size_bytes(basetemp)
                cumulative_temp_bytes += artifact_bytes
                peak_temp_bytes = max(peak_temp_bytes, artifact_bytes)
                report_sha256 = _lab_report_digest(basetemp)
            finally:
                cleanup_ok = _remove_tree(basetemp)

            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            assert invocation is not None
            counts = _summary_counts(invocation.stdout)
            status = "passed" if invocation.returncode == 0 else "failed"
            if invocation.timed_out:
                status = "timed_out"
            if invocation.returncode == 0 and not report_sha256:
                status = "proof_report_missing"
            if artifact_bytes >= MAX_TEMP_BYTES:
                status = "temp_limit_exceeded"
            if not cleanup_ok:
                status = "cleanup_failed"
            run_records.append(
                {
                    "iteration": iteration,
                    "status": status,
                    "exit_code": invocation.returncode,
                    "elapsed_ms": elapsed_ms,
                    "artifact_bytes": artifact_bytes,
                    "report_sha256": report_sha256,
                    "cleanup_ok": cleanup_ok,
                    **counts,
                }
            )

            free_after_iteration = _available_bytes(ROOT)
            if free_after_iteration < MIN_FREE_BYTES:
                report["overall_status"] = "blocked_disk_floor"
                exit_code = 2
                break
            if status != "passed":
                report["overall_status"] = status
                exit_code = 1
                break
        else:
            report["overall_status"] = "passed"
            report["qualification_complete"] = bool(
                iterations == DEFAULT_ITERATIONS and report["source_tree_clean"]
            )
    finally:
        report["run_root_cleanup_ok"] = _remove_tree(run_root)

    report["total_temp_bytes"] = cumulative_temp_bytes
    report["peak_temp_bytes"] = peak_temp_bytes
    report["free_bytes_after"] = _available_bytes(ROOT)
    report["finished_utc"] = _utc_now()
    if report["free_bytes_after"] < MIN_FREE_BYTES:
        report["overall_status"] = "blocked_disk_floor"
        report["qualification_complete"] = False
        exit_code = 2
    if not report["run_root_cleanup_ok"]:
        report["overall_status"] = "cleanup_failed"
        report["qualification_complete"] = False
        exit_code = 1
    _atomic_write_report(report_path, report)
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run WebJam's fixed deterministic multitrack proof matrix."
    )
    parser.add_argument(
        "--iterations",
        type=_positive_int,
        default=DEFAULT_ITERATIONS,
        help="fresh subprocess iterations (default: 20; lower values are smoke only)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_timeout_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="per-iteration timeout (default: 1800)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(tempfile.gettempdir()) / "webjam-multitrack-proof-lab.json",
        help="sanitized JSON report destination",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return run_lab(
            iterations=arguments.iterations,
            report_path=arguments.report.expanduser().resolve(),
            timeout_seconds=arguments.timeout_seconds,
        )
    except (OSError, ValueError):
        print(
            "Multitrack proof runner stopped at a bounded safety gate.", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

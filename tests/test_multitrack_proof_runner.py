from __future__ import annotations

import json
import stat

from tools import run_multitrack_proof_lab as proof


_SAFE_FREE_BYTES = proof.MIN_FREE_BYTES + 64 * 1024 * 1024


def _passed_invocation() -> proof._PytestInvocation:
    return proof._PytestInvocation(
        returncode=0,
        stdout="18 passed, 2 skipped in 0.50s",
    )


def test_fixed_matrix_covers_each_required_production_boundary() -> None:
    matrix = "\n".join(proof.PROOF_MATRIX)
    assert "tests/test_multitrack_proof_lab.py" in proof.PROOF_MATRIX
    assert "tests/test_v026_podcast_voice_journey.py" in proof.PROOF_MATRIX
    for required in (
        "session_recording_plan",
        "guest_capture_arm",
        "shared_track",
        "relaunch_reconciles",
        "recording_studio",
        "studio_export",
    ):
        assert required in matrix


def test_proof_docs_publish_commands_and_keep_physical_truth_not_run() -> None:
    documents = {
        path: (proof.ROOT / path).read_text(encoding="utf-8")
        for path in (
            "DUAL_MUSICIAN_REHEARSAL_LAB.md",
            "TEST_PROCEDURE.md",
            "DEVELOPMENT.md",
            "docs/README.md",
        )
    }
    combined = "\n".join(documents.values())
    assert "tools/run_multitrack_proof_lab.py" in combined
    assert "tests/test_multitrack_proof_lab.py" in combined
    assert "qualification_complete=true" in combined
    assert "seven-source" in combined
    assert "physical_status=not_run" in combined
    assert "remain **NOT RUN**" in documents["TEST_PROCEDURE.md"]


def test_one_iteration_is_sanitized_bounded_smoke_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "proof.json"
    private_marker = str(tmp_path)

    monkeypatch.setattr(proof, "_available_bytes", lambda _path: _SAFE_FREE_BYTES)
    monkeypatch.setattr(proof, "_source_sha", lambda: "a" * 40)

    def fake_pytest(basetemp, _timeout_seconds):
        basetemp.mkdir(parents=True)
        (basetemp / "bounded-fixture.bin").write_bytes(b"fixture")
        (basetemp / "proof-report.json").write_text(
            json.dumps(
                {
                    "schema_version": "webjam.multitrack-proof-lab.v1",
                    "overall_status": "passed",
                }
            ),
            encoding="utf-8",
        )
        return _passed_invocation()

    monkeypatch.setattr(proof, "_run_pytest", fake_pytest)
    assert proof.run_lab(iterations=1, report_path=report_path) == 0

    serialized = report_path.read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert len(serialized.encode("utf-8")) < proof.MAX_REPORT_BYTES
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert private_marker not in serialized
    assert report["overall_status"] == "passed"
    assert report["qualification_complete"] is False
    assert isinstance(report["source_tree_clean"], bool)
    assert len(report["source_diff_sha256"]) == 64
    assert report["physical_status"] == "not_run"
    assert report["requested_iterations"] == 1
    assert report["iterations"] == [
        {
            "artifact_bytes": report["iterations"][0]["artifact_bytes"],
            "cleanup_ok": True,
            "elapsed_ms": report["iterations"][0]["elapsed_ms"],
            "errors": 0,
            "exit_code": 0,
            "failed": 0,
            "iteration": 1,
            "passed": 18,
            "report_sha256": report["iterations"][0]["report_sha256"],
            "skipped": 2,
            "status": "passed",
            "xfailed": 0,
            "xpassed": 0,
        }
    ]
    assert len(report["iterations"][0]["report_sha256"]) == 64
    assert report["peak_temp_bytes"] == report["iterations"][0]["artifact_bytes"]
    assert report["run_root_cleanup_ok"] is True


def test_failure_stops_before_a_second_subprocess(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "failed.json"
    calls = 0
    monkeypatch.setattr(proof, "_available_bytes", lambda _path: _SAFE_FREE_BYTES)
    monkeypatch.setattr(proof, "_source_sha", lambda: "b" * 40)

    def fake_failure(_basetemp, _timeout_seconds):
        nonlocal calls
        calls += 1
        return proof._PytestInvocation(
            returncode=1,
            stdout="1 failed, 17 passed in 0.25s",
        )

    monkeypatch.setattr(proof, "_run_pytest", fake_failure)
    assert proof.run_lab(iterations=3, report_path=report_path) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert calls == 1
    assert report["overall_status"] == "failed"
    assert len(report["iterations"]) == 1
    assert report["qualification_complete"] is False


def test_low_disk_starts_no_subprocess_and_records_no_path(
    tmp_path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "blocked.json"
    private_marker = str(tmp_path)
    called = False
    monkeypatch.setattr(
        proof, "_available_bytes", lambda _path: proof.MIN_FREE_BYTES - 1
    )
    monkeypatch.setattr(proof, "_source_sha", lambda: "c" * 40)

    def forbidden_run(_basetemp, _timeout_seconds):
        nonlocal called
        called = True
        return _passed_invocation()

    monkeypatch.setattr(proof, "_run_pytest", forbidden_run)
    assert proof.run_lab(iterations=1, report_path=report_path) == 2
    serialized = report_path.read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert called is False
    assert private_marker not in serialized
    assert report["overall_status"] == "blocked_disk_floor"
    assert report["iterations"] == []


def test_source_tree_classification_ignores_only_preserved_recovery_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(proof, "ROOT", tmp_path)
    (tmp_path / "new.py").write_text("print('proof')\n", encoding="utf-8")
    status = b"?? _to_delete/\n?? preserved.bundle\n"

    def fake_check_output(command, **_kwargs):
        if command[1] == "status":
            return status
        if command[1] == "diff":
            return b""
        raise AssertionError(command)

    monkeypatch.setattr(proof.subprocess, "check_output", fake_check_output)
    clean, digest = proof._source_tree_facts()
    assert clean is True
    assert len(digest) == 64

    status = b"?? _to_delete/\n?? preserved.bundle\n?? new.py\n"
    clean, changed_digest = proof._source_tree_facts()
    assert clean is False
    assert len(changed_digest) == 64
    assert changed_digest != digest


def test_process_launch_failure_is_sanitized_and_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    def fail_launch(*_args, **_kwargs):
        raise OSError(f"private launch path: {tmp_path}")

    monkeypatch.setattr(proof.subprocess, "run", fail_launch)
    invocation = proof._run_pytest(tmp_path / "basetemp", 1)
    assert invocation.returncode == 125
    assert invocation.stdout == ""
    assert invocation.timed_out is False


def test_cleanup_failure_is_reported_after_owned_artifacts_are_removed(
    tmp_path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "cleanup-failed.json"
    monkeypatch.setattr(proof, "_available_bytes", lambda _path: _SAFE_FREE_BYTES)
    monkeypatch.setattr(proof, "_source_sha", lambda: "e" * 40)
    monkeypatch.setattr(proof, "_run_pytest", lambda *_args: _passed_invocation())
    remove_tree = proof._remove_tree
    calls = 0

    def remove_but_fail_first_verification(path):
        nonlocal calls
        calls += 1
        removed = remove_tree(path)
        return False if calls == 1 else removed

    monkeypatch.setattr(proof, "_remove_tree", remove_but_fail_first_verification)
    assert proof.run_lab(iterations=2, report_path=report_path) == 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "cleanup_failed"
    assert report["qualification_complete"] is False
    assert report["run_root_cleanup_ok"] is True
    assert report["iterations"][0]["cleanup_ok"] is False


def test_temp_cap_failure_is_cleaned_and_cannot_qualify(
    tmp_path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "cap.json"
    monkeypatch.setattr(proof, "_available_bytes", lambda _path: _SAFE_FREE_BYTES)
    monkeypatch.setattr(proof, "_source_sha", lambda: "d" * 40)
    monkeypatch.setattr(proof, "_run_pytest", lambda *_args: _passed_invocation())
    monkeypatch.setattr(proof, "_tree_size_bytes", lambda _path: proof.MAX_TEMP_BYTES)

    assert proof.run_lab(iterations=2, report_path=report_path) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "temp_limit_exceeded"
    assert report["run_root_cleanup_ok"] is True
    assert report["qualification_complete"] is False

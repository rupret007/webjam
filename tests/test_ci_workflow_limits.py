"""Keep CI parseable by GitHub, not just by PyYAML.

GitHub rejects a workflow whose single expression exceeds 21,000
characters. It refuses the file before creating any job, so the only
symptom is a run that fails in 0 seconds with no logs, no annotations, and
no failing step -- while the file parses locally and looks identical to the
last one that worked.

That cost days of CI here: every push from 2026-07-30 died this way after a
step grew past the ceiling. A local check turns a silent, near-undiagnosable
outage into a test failure that names the step.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# GitHub's hard limit for one expression.
GITHUB_MAX_EXPRESSION = 21_000
# Fail earlier than GitHub does, so a step that is merely close to the
# ceiling is caught while it can still be fixed deliberately.
SAFE_MAX = 20_500


def _run_blocks():
    for path in WORKFLOWS:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (document.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                body = step.get("run")
                if isinstance(body, str):
                    name = step.get("name") or f"step {index}"
                    yield path.name, job_id, name, body


def test_workflows_exist_to_check() -> None:
    assert WORKFLOWS, "no workflow files found to check"


@pytest.mark.parametrize("limit", [GITHUB_MAX_EXPRESSION, SAFE_MAX])
def test_no_run_block_approaches_github_expression_limit(limit: int) -> None:
    offenders = [
        f"{fname}: {job}/{name} is {len(body)} chars"
        for fname, job, name, body in _run_blocks()
        if len(body) > limit
    ]

    assert not offenders, (
        "GitHub refuses a workflow whose expression exceeds "
        f"{GITHUB_MAX_EXPRESSION} characters, and reports it only as a run "
        "that fails in 0s with no logs. Move the body into a script under "
        ".github/scripts/ and call it instead: " + "; ".join(offenders)
    )

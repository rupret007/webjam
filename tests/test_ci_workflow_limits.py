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


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# GitHub's hard limit for one expression.
GITHUB_MAX_EXPRESSION = 21_000
# Fail earlier than GitHub does, so a step that is merely close to the
# ceiling is caught while it can still be fixed deliberately.
SAFE_MAX = 20_500


def _run_blocks():
    """Measure each `run: |` block from the raw text.

    Deliberately not PyYAML: CI installs only requirements.txt plus pytest,
    ruff, and pip-audit, so importing yaml here fails collection on the
    runner. The repository's other workflow tests read ci.yml as text for
    the same reason.
    """

    for path in WORKFLOWS:
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        name = "unnamed step"
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if stripped.startswith("- name:"):
                name = stripped[len("- name:") :].strip()
            if stripped in {"run: |", "run: |-"}:
                indent = len(line) - len(line.lstrip())
                body: list[str] = []
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if candidate.strip() and (
                        len(candidate) - len(candidate.lstrip())
                    ) <= indent:
                        break
                    body.append(candidate)
                    index += 1
                # YAML strips the block's own indentation; match it, or
                # every measurement is inflated by the nesting depth.
                margin = min(
                    (
                        len(entry) - len(entry.lstrip())
                        for entry in body
                        if entry.strip()
                    ),
                    default=0,
                )
                yield (
                    path.name,
                    name,
                    "\n".join(entry[margin:] for entry in body),
                )
                continue
            index += 1


def test_workflows_exist_to_check() -> None:
    assert WORKFLOWS, "no workflow files found to check"


@pytest.mark.parametrize("limit", [GITHUB_MAX_EXPRESSION, SAFE_MAX])
def test_no_run_block_approaches_github_expression_limit(limit: int) -> None:
    offenders = [
        f"{fname}: {name} is {len(body)} chars"
        for fname, name, body in _run_blocks()
        if len(body) > limit
    ]

    assert not offenders, (
        "GitHub refuses a workflow whose expression exceeds "
        f"{GITHUB_MAX_EXPRESSION} characters, and reports it only as a run "
        "that fails in 0s with no logs. Move the body into a script under "
        ".github/scripts/ and call it instead: " + "; ".join(offenders)
    )

"""Consistency contracts for the merge and release map."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "docs/MERGE_AND_RELEASE.md"
MAP_TEXT = MAP_PATH.read_text(encoding="utf-8")
FLAT_MAP_TEXT = " ".join(MAP_TEXT.split())
WORKFLOW_TEXT = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Matrix jobs render their target into the name, so the map's concrete names are
# matched against the workflow's name template instead of a literal.
REQUIRED_CI_JOBS = (
    ("Build Desktop (windows-x64)", "name: Build Desktop ("),
    ("Pocket Stage (iOS app)", "name: Pocket Stage (iOS app)"),
    (
        "Transport (Go security and cross-build)",
        "name: Transport (Go security and cross-build)",
    ),
    (
        "Reference service (protocol and container)",
        "name: Reference service (protocol and container)",
    ),
)
DESKTOP_TARGETS = ("windows-x64", "macos-arm64", "macos-x64", "linux-x64")
NOT_RUN_CI_JOB_NAMES = (
    "Certify Jamulus/JACK (one hour, manual)",
    "Windows Release Trust (windows-x64)",
    "macOS Release Trust",
    "Jamulus 3.12.3 HEADLESS evidence",
)
DOCS_PASS_FILES = (
    "USER_GUIDE.md",
    "README.md",
    "QUICK_HELP_MAP.md",
    "CHANGELOG.md",
    "HELP_ROUTING_MAP.md",
)


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for heading in HEADING_RE.findall(text):
        clean = re.sub(r"[^\w\- ]", "", heading.casefold(), flags=re.UNICODE)
        anchors.add(re.sub(r"[\s\-]+", "-", clean).strip("-"))
    return anchors


def test_map_lands_the_audio_core_then_each_room_after_a_rebase() -> None:
    positions = [FLAT_MAP_TEXT.index(pull) for pull in ("#14", "#15", "#17")]
    assert positions == sorted(positions), "land order must read #14, #15, #17"
    for pull in ("#15", "#17"):
        assert FLAT_MAP_TEXT.index(f"Rebase [{pull}]") < FLAT_MAP_TEXT.index(
            f"| 3 | Land {pull}" if pull == "#15" else f"| 5 | Land {pull}"
        ), pull
    assert "#16" in FLAT_MAP_TEXT and "already on `master`" in FLAT_MAP_TEXT


def test_map_keeps_the_merge_button_attended() -> None:
    assert FLAT_MAP_TEXT.count("Jeff merges") >= 3
    assert "Bob does not merge unattended" in FLAT_MAP_TEXT
    assert "no force-push over someone else's product branch" in FLAT_MAP_TEXT


def test_map_names_real_required_ci_jobs_and_fails_closed() -> None:
    required_section, _, not_run_section = MAP_TEXT.partition(
        "**NOT RUN** unless real evidence exists"
    )
    assert not_run_section, "the map must keep a NOT RUN section"

    for job_name, workflow_name in REQUIRED_CI_JOBS:
        assert job_name in required_section, job_name
        assert workflow_name in WORKFLOW_TEXT, workflow_name
    for target in DESKTOP_TARGETS:
        assert f"({target})" in required_section, target
        assert f"target: {target}" in WORKFLOW_TEXT, target
    assert "\n  test:\n" in WORKFLOW_TEXT and "- `test`" in required_section
    assert "Red means stop" in required_section


def test_map_lists_unproven_gates_as_not_run_only() -> None:
    not_run_section = MAP_TEXT.partition("**NOT RUN** unless real evidence exists")[2]
    for job_name in NOT_RUN_CI_JOB_NAMES:
        assert job_name in not_run_section, job_name
        assert job_name in WORKFLOW_TEXT, job_name
        assert MAP_TEXT.count(job_name) == 1, f"{job_name} must appear only as NOT RUN"
    for gate in (
        "Two-Mac Art room video and Drawpile",
        "Live Music AI",
        "Physical and hardware checklist rows",
    ):
        assert gate in not_run_section, gate


def test_map_docs_pass_targets_exist_and_stay_kiss() -> None:
    for relative_path in DOCS_PASS_FILES:
        assert (ROOT / relative_path).is_file(), relative_path
        assert relative_path in MAP_TEXT, relative_path

    assert "**Art** and **Music**" in FLAT_MAP_TEXT
    assert 'No "Studio Visit" wording' in FLAT_MAP_TEXT
    assert "No add-on" in FLAT_MAP_TEXT
    assert "No integration wall" in FLAT_MAP_TEXT


def test_map_local_links_resolve_and_the_docs_index_points_at_it() -> None:
    failures: list[str] = []
    for raw_target in LINK_RE.findall(MAP_TEXT):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if target.startswith(("https://", "http://", "mailto:")):
            continue
        path_part, separator, fragment = target.partition("#")
        resolved = (
            MAP_PATH if not path_part else MAP_PATH.parent / unquote(path_part)
        ).resolve()
        if not resolved.exists():
            failures.append(f"missing {target}")
        elif separator and fragment and resolved.is_file():
            anchors = _heading_anchors(resolved.read_text(encoding="utf-8"))
            if unquote(fragment).casefold() not in anchors:
                failures.append(f"missing anchor {target}")
    assert not failures, "\n".join(failures)

    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    assert "(MERGE_AND_RELEASE.md)" in index

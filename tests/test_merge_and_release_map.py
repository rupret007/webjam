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
    positions = [FLAT_MAP_TEXT.index(f"| {step} |") for step in range(1, 4)]
    assert positions == sorted(positions), "the remaining land-order steps must read in order"
    for landed in ("#14", "#15", "#16", "#19"):
        assert landed in FLAT_MAP_TEXT, landed
    assert "already on `master`" in FLAT_MAP_TEXT
    assert FLAT_MAP_TEXT.index("Rebase [#17]") < FLAT_MAP_TEXT.index(
        "| 2 | Land #17"
    )


def test_map_gates_landing_and_release_on_the_ten_second_read() -> None:
    gate_section = MAP_TEXT.partition("## 1. Ten-second UX gate")[2].partition(
        "## 2."
    )[0]
    assert gate_section, "the map must keep the UX gate ahead of the land order"
    flat_gate = " ".join(gate_section.split())

    assert "not ready to land" in flat_gate
    assert "not ready to be called released" in flat_gate

    doors = [line for line in gate_section.splitlines() if line.startswith("| ")]
    art, music = (line for line in doors if line.startswith(("| Art ", "| Music ")))
    for door in ("**Talk & make**", "**Paint together**", "**Paint along**"):
        assert door in art, door
    assert "**Host** / **Join**" in art and "**Host** / **Join**" in music
    assert "nothing else" in music

    banned = (
        "Studio Visit",
        "Drawpile",
        "Jamulus",
        "host-clocked",
        "Moises",
        "BYOK",
        "Preview caveats",
        "API",
    )
    banned_line = flat_gate.partition("Banned on the first screen:")[2]
    assert banned_line, "the map must keep the banned first-screen list"
    for term in banned:
        assert term in banned_line, term
        assert term not in art and term not in music, term

    assert "#19" in flat_gate and "#15 is not the Art door" in flat_gate


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

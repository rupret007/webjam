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
JAMULUS_INTEGRATION_VERSIONS = ("3.12.2", "3.12.3")
JAMULUS_UPDATE_TARGETS = ("windows-x64", "macos-universal")
NOT_RUN_CI_JOB_NAMES = (
    "Certify Jamulus/JACK (one hour, manual)",
    "Windows Release Trust (windows-x64)",
    "macOS Release Trust",
    "Jamulus 3.12.3 HEADLESS evidence",
    "Publish GitHub Release",
)
DOCS_PASS_FILES = (
    "CHANGELOG.md",
    "USER_GUIDE.md",
    "README.md",
    "README_SIMPLE.md",
    "QUICK_HELP_MAP.md",
    "HELP_ROUTING_MAP.md",
    "FIRST_JAM.md",
    "ARCHITECTURE.md",
    "docs/PROJECT_BRIEF.md",
)
CURRENT_ART_DOOR_GUIDES = (
    "README.md",
    "README_SIMPLE.md",
    "USER_GUIDE.md",
    "FIRST_JAM.md",
    "QUICK_HELP_MAP.md",
    "HELP_ROUTING_MAP.md",
    "CREATIVE_MODES_MVP_SPEC.md",
    "docs/PROJECT_BRIEF.md",
)
CURRENT_MUSIC_DOOR_MARKERS = {
    "README.md": "Music uses **Host** / **Join**",
    "README_SIMPLE.md": "Music uses Host/Join",
    "USER_GUIDE.md": "Music is **Host** or **Join** only",
    "FIRST_JAM.md": "Music is **Host** or **Join** only",
    "QUICK_HELP_MAP.md": "Music is **Host** / **Join** only",
}
ROUND_CONTROL_FILES = (
    "docs/MERGE_AND_RELEASE.md",
    "tests/test_merge_and_release_map.py",
)
LOCAL_SUITE_MARKERS = (
    "`ruff check webjam_qt/ core/ ui/ services/ api/`",
    "dependency audits:",
    "`python -m compileall -q core webjam_qt ui services api tests`",
    "`python ux_smoke_test.py`",
    "every tracked `tests/test_*.py` module",
)


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for heading in HEADING_RE.findall(text):
        clean = re.sub(r"[^\w\- ]", "", heading.casefold(), flags=re.UNICODE)
        anchors.add(re.sub(r"[\s\-]+", "-", clean).strip("-"))
    return anchors


def test_map_records_the_published_round_and_the_parked_leftovers() -> None:
    positions = [FLAT_MAP_TEXT.index(f"| {step} |") for step in range(1, 7)]
    assert positions == sorted(positions), "the remaining steps must read in order"
    for landed in ("#14", "#15", "#16", "#17", "#19"):
        assert landed in FLAT_MAP_TEXT, landed
    assert "already on `master`" in FLAT_MAP_TEXT
    assert "merged 2026-08-22" in FLAT_MAP_TEXT
    assert "no open product branch" in FLAT_MAP_TEXT

    # #17 merged as `5ca6ba5`; the map must not present it as open work.
    assert "Rebase [#17]" not in FLAT_MAP_TEXT
    assert "Land #17" not in FLAT_MAP_TEXT
    assert "still a draft" not in FLAT_MAP_TEXT
    assert "is the open product branch" not in FLAT_MAP_TEXT

    # The parked branches and published releases remain untouched.
    assert "#37 and #49 stay parked" in FLAT_MAP_TEXT
    assert "do not retag, replace, or mutate" in FLAT_MAP_TEXT

    # The release and the later source-truth draft are distinct boundaries.
    assert "Published testing boundary:" in FLAT_MAP_TEXT
    assert "9c6ca3de96aa7eb261c65b7dee768ab48144169c" in FLAT_MAP_TEXT
    assert "379360694" in FLAT_MAP_TEXT
    assert "33317581250" in FLAT_MAP_TEXT
    assert "33327104322" in FLAT_MAP_TEXT
    assert "lightweight tag" in FLAT_MAP_TEXT
    assert "not publish-green" in FLAT_MAP_TEXT
    assert "Merged source-only review" in FLAT_MAP_TEXT
    assert "[#60](https://github.com/rupret007/webjam/pull/60)" in FLAT_MAP_TEXT
    assert "Standing procedure after the completed product land:" in FLAT_MAP_TEXT
    assert "For any later source-only correction, start from current `master`" in (
        FLAT_MAP_TEXT
    )
    assert "Open one draft PR for Karen" in FLAT_MAP_TEXT
    assert "Stop without merging, tagging, publishing, or altering releases" in (
        FLAT_MAP_TEXT
    )
    for stale_process_claim in (
        "This docs-and-tests branch",
        "What remains in this named round",
        "The current stop is one post-release truth draft",
        "this docs PR does not rerun or publish",
        "Codex prepares this correction branch",
    ):
        assert stale_process_claim not in FLAT_MAP_TEXT
    assert "No v0.27.2 tag" not in FLAT_MAP_TEXT
    assert "GitHub **Latest** remains v0.27.1" not in FLAT_MAP_TEXT


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
    for door in ("**Make together**", "**Paint along**"):
        assert door in art, door
    assert "**Talk & make**" not in art
    assert "**Paint together**" not in art
    assert "**Host** / **Join**" in art and "**Host** / **Join**" in music
    assert "nothing else" in music

    banned = (
        "Studio Visit",
        "Drawpile",
        "Krita",
        "Jamulus",
        "Webex",
        "host-clocked",
        "Moises",
        "Music AI",
        "stems",
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
    assert "Karen and Jeff retain the attended review/merge decision" in FLAT_MAP_TEXT
    assert "Jeff presses merge" in FLAT_MAP_TEXT
    assert "Codex does not merge unattended" in FLAT_MAP_TEXT
    assert "does not tag, publish, or alter a release" in FLAT_MAP_TEXT
    assert "no force-push over someone else's product branch" in FLAT_MAP_TEXT


def test_map_requires_the_complete_local_suite_before_hosted_ci() -> None:
    local_section = MAP_TEXT.partition("### Complete local suite first")[2].partition(
        "### Complete hosted suite second"
    )[0]
    assert local_section, "the complete local suite must precede hosted CI"
    flat_local_section = " ".join(local_section.split())
    positions = [flat_local_section.index(marker) for marker in LOCAL_SUITE_MARKERS]
    assert positions == sorted(positions), "local release gates must stay in order"
    for marker in (
        "`python -m pip check`",
        "`python tools/runtime_dependency_policy.py --check`",
        "`pip-audit`",
        "every supported native dependency lock",
        "one fresh Python process per module",
        "no retry",
        "`git diff --check`",
    ):
        assert marker in flat_local_section, marker


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
    integration_template = (
        "name: Integration (real Jamulus ${{ matrix.jamulus_version }})"
    )
    assert integration_template in WORKFLOW_TEXT
    for version in JAMULUS_INTEGRATION_VERSIONS:
        assert f"Integration (real Jamulus {version})" in required_section, version
        assert f'jamulus_version: "{version}"' in WORKFLOW_TEXT, version
    update_template = "name: Jamulus 3.12.3 update input (${{ matrix.target }})"
    assert update_template in WORKFLOW_TEXT
    for target in JAMULUS_UPDATE_TARGETS:
        assert f"Jamulus 3.12.3 update input ({target})" in required_section, target
        assert f"target: {target}" in WORKFLOW_TEXT, target
    assert "\n  test:\n" in WORKFLOW_TEXT and "- `test`" in required_section
    assert "all 12 required hosted jobs" in required_section
    assert "Red means stop" in required_section
    assert "Do not re-run a job to change its result" in required_section


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
    for boundary in ("signing", "notarization", "physical machines"):
        assert boundary in not_run_section, boundary


def test_map_docs_pass_targets_exist_and_stay_kiss() -> None:
    for relative_path in DOCS_PASS_FILES + ROUND_CONTROL_FILES:
        assert (ROOT / relative_path).is_file(), relative_path
        assert relative_path in MAP_TEXT, relative_path

    assert "**Art** and **Music**" in FLAT_MAP_TEXT
    assert "No add-on" in FLAT_MAP_TEXT
    assert "No integration wall" in FLAT_MAP_TEXT


def test_current_guides_keep_the_two_start_art_and_host_join_music_doors() -> None:
    """Current help must not inherit an older Art or Music start door.

    The changelog, dated quality review, and release-round record intentionally
    preserve earlier labels. These files describe the door a person sees now.
    """

    for relative_path in CURRENT_ART_DOOR_GUIDES:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "**Make together**" in text, relative_path
        assert "**Paint along**" in text, relative_path
        assert "**Talk & make**" not in text, relative_path
        assert "**Paint together**" not in text, relative_path

    normalized = {
        relative_path: " ".join(
            (ROOT / relative_path).read_text(encoding="utf-8").split()
        )
        for relative_path in CURRENT_ART_DOOR_GUIDES
    }
    assert "It offers two visible starts and no more" in normalized[
        "docs/PROJECT_BRIEF.md"
    ]
    assert "Launch shows exactly two cards for Art" in normalized[
        "CREATIVE_MODES_MVP_SPEC.md"
    ]
    assert "you pick one of two ways to start, and nothing more" in normalized[
        "USER_GUIDE.md"
    ]
    for relative_path, marker in CURRENT_MUSIC_DOOR_MARKERS.items():
        assert marker in normalized[relative_path], relative_path


def test_changelog_moves_final_art_door_work_into_v0272() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.partition("## [Unreleased]")[2].partition("## [0.27.2]")[0]
    v0272 = changelog.partition("## [0.27.2]")[2].partition("## [0.27.1]")[0]
    heading = "### Art starts with fewer choices"
    assert heading not in unreleased
    assert heading in v0272
    assert "exactly two start cards" in v0272
    assert "Music remains\n  **Host** / **Join** only" in v0272
    assert "Its two starts" in v0272
    assert "Its three starts" not in v0272


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

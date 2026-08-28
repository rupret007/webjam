"""Published v0.27.1 and post-tag documentation truth contracts."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
CURRENT_GUIDES = (
    "README.md",
    "README_SIMPLE.md",
    "ARCHITECTURE.md",
    "DEVELOPMENT.md",
    "FIRST_JAM.md",
    "USER_GUIDE.md",
    "RECORDING_AND_STUDIO.md",
    "HELP_ROUTING_MAP.md",
    "QUICK_HELP_MAP.md",
    "SECURITY.md",
    "TEST_PROCEDURE.md",
    "WEBEX_AUDIO_MODES.md",
    "CREATIVE_MODES_MVP_SPEC.md",
    "UX_ACCEPTANCE_CHECKLIST.md",
    "V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md",
    "CHANGELOG.md",
    "docs/README.md",
    "docs/MERGE_AND_RELEASE.md",
    "docs/DESKTOP_RELEASE_RUNBOOK.md",
    "docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md",
    "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
    "docs/PROJECT_BRIEF.md",
    "requirements-lock/README.md",
    "ios/README.md",
)
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _normalized(relative_path: str) -> str:
    return " ".join((ROOT / relative_path).read_text(encoding="utf-8").split())


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for heading in HEADING_RE.findall(text):
        clean = re.sub(r"[^\w\- ]", "", heading.casefold(), flags=re.UNICODE)
        anchors.add(re.sub(r"[\s\-]+", "-", clean).strip("-"))
    return anchors


def test_current_guides_state_v0271_latest_and_physical_boundary_truthfully() -> (
    None
):
    for relative_path in (
        "README.md",
        "README_SIMPLE.md",
        "FIRST_JAM.md",
        "USER_GUIDE.md",
        "RECORDING_AND_STUDIO.md",
        "SECURITY.md",
        "TEST_PROCEDURE.md",
        "docs/README.md",
        "docs/MERGE_AND_RELEASE.md",
        "docs/DESKTOP_RELEASE_RUNBOOK.md",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
        "docs/PROJECT_BRIEF.md",
        "CHANGELOG.md",
    ):
        text = _normalized(relative_path)
        assert "v0.27.1" in text, relative_path
        assert "Latest" in text, relative_path
        assert "NOT RUN" in text, relative_path

    combined = " ".join(_normalized(path) for path in CURRENT_GUIDES)
    for marker in (
        "https://github.com/rupret007/webjam/releases/tag/v0.27.1",
        "377614785",
        "2026-08-27T06:56:11Z",
        "WebJam-v0.27.1-SHA256SUMS.txt",
        "1fc25f87c3386b1cd94303ecb407cdaff6509d1f",
    ):
        assert marker.casefold() in combined.casefold()

    for stale_claim in (
        "No v0.26.0 tag",
        "no v0.26.0 package",
        "unpublished v0.26.0",
        "deliberately inert, fail-closed publication stub",
        "before v0.26.0 can replace",
        "v0.25.0 remains GitHub **Latest**",
        "GitHub **Latest** remains immutable v0.25.0",
        "Immutable v0.25.0 is the GitHub **Latest**",
        "this checkout identifies itself as v0.26.0",
        "this checkout identifies itself as v0.27.0",
        "v0.26.0 is also the current source identity",
        "v0.27.0 is also the current source identity",
        "Current source line | v0.26.0",
        "Current source line | v0.27.0",
        "GitHub **Latest** remains immutable v0.27.0",
        "Immutable v0.27.0 remains GitHub **Latest**",
        "unpublished v0.27.1",
        "No v0.27.1 tag",
        "No v0.27.1 release ID",
    ):
        assert stale_claim.casefold() not in combined.casefold(), stale_claim

    assert "post-v0.27.1" in combined.casefold()
    assert "not publish-green" in combined.casefold()
    assert "sealed at exact webjam v0.22.5" in combined.casefold()


def test_required_honesty_docs_separate_v0271_release_from_post_tag_source() -> None:
    """Jeff-facing pass: v0.27.1 is Latest, but current master is post-tag source."""

    required = (
        "README.md",
        "CHANGELOG.md",
        "docs/MERGE_AND_RELEASE.md",
        "docs/DESKTOP_RELEASE_RUNBOOK.md",
    )
    forbidden = (
        "this checkout identifies itself as v0.26.0",
        "this checkout identifies itself as v0.27.0",
        "Current source line | v0.26.0",
        "Current source line | v0.27.0",
        "v0.26.0 is also the current source identity",
        "v0.27.0 is also the current source identity",
        "the current source tree reports **v0.26.0**",
        "the current source tree reports **v0.27.0**",
        "Latest is this unpublished",
        "this unpublished checkout is GitHub Latest",
        "GitHub **Latest** remains immutable v0.27.0",
        "unpublished v0.27.1",
        "No v0.27.1 release ID",
        "No v0.27.1 tag",
    )
    for relative_path in required:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        folded = normalized.casefold()
        assert "v0.27.1" in text, relative_path
        assert "latest" in folded and "377614785" in text, relative_path
        assert "publish-green" in folded, relative_path
        assert "WebJam-v0.27.1-SHA256SUMS.txt" in text, relative_path
        for claim in forbidden:
            assert claim.casefold() not in folded, (relative_path, claim)


def test_v026_checklist_verifies_only_automated_release_identity() -> None:
    checklist = (ROOT / "V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md").read_text(
        encoding="utf-8"
    )
    identity = checklist.split("## Exact candidate identity\n", 1)[1].split(
        "\n## A. Native packages, clean start, and trust", 1
    )[0]
    assert identity.count("**VERIFIED \u2014 automated release evidence:**") == 6
    assert "| I07 |" in identity and "**NOT RUN" in identity
    assert "| I08 |" in identity and "**NOT RUN" in identity

    physical_rows = [
        line
        for line in checklist.splitlines()
        if re.match(r"^\| [A-F][0-9]{2} \|", line)
    ]
    assert len(physical_rows) >= 50
    assert all(line.endswith("| **NOT RUN** |") for line in physical_rows)

    decision = checklist.split("## Release decision summary\n", 1)[1]
    decision_rows = [
        line
        for line in decision.splitlines()
        if line.startswith("| ") and "Gate family" not in line and "---" not in line
    ]
    assert len(decision_rows) == 11
    assert all(line.endswith("| **NOT RUN** |") for line in decision_rows)
    assert "Release recommendation: **NOT RUN**" in decision


def test_v026_guides_cover_exact_recording_and_creator_boundaries() -> None:
    recording = _normalized("RECORDING_AND_STUDIO.md")
    creator = _normalized("USER_GUIDE.md")
    changelog = _normalized("CHANGELOG.md")
    combined = " ".join((recording, creator, changelog))
    for marker in (
        "Record Session Readiness",
        "path-free",
        "server track",
        "Local Original",
        "Shared Track",
        "required/optional",
        "mono",
        "stereo",
        "logical-source ID",
        "take-scoped arm",
        "authenticated acknowledgement",
        "Jamulus recording start is withheld",
        "recovery-only",
        "Preparing",
        "Count-in",
        "Recording",
        "Stopping",
        "Finalizing",
        "Ready",
        "Needs attention",
        "automatically stacks",
        "same session",
        "verified timing",
        "Podcast & Voice",
        "Host-mono",
        "Guest-stereo",
        "chapter",
        "Bounce Episode",
        "PCM-24",
        "Review & Rehearsal",
        "read-only",
    ):
        assert marker.casefold() in combined.casefold(), marker

    meeting = " ".join(
        _normalized(path)
        for path in ("README.md", "USER_GUIDE.md", "WEBEX_AUDIO_MODES.md")
    )
    for provider in ("Webex", "Zoom", "Teams", "Google Meet", "FaceTime"):
        assert provider in meeting
    assert "directly or automatically taps" in meeting.casefold()
    assert "meeting service's own recording" in meeting.casefold()


def test_v026_current_guide_local_links_and_anchors_resolve() -> None:
    failures: list[str] = []
    for relative_path in CURRENT_GUIDES:
        source = ROOT / relative_path
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("https://", "http://", "mailto:")):
                continue
            path_part, separator, fragment = target.partition("#")
            target_path = (
                source if not path_part else source.parent / unquote(path_part)
            )
            target_path = target_path.resolve()
            if not target_path.exists():
                failures.append(f"{relative_path}: missing {target}")
                continue
            if separator and fragment and target_path.is_file():
                anchors = _heading_anchors(target_path.read_text(encoding="utf-8"))
                if unquote(fragment).casefold() not in anchors:
                    failures.append(f"{relative_path}: missing anchor {target}")
    assert not failures, "\n".join(failures)

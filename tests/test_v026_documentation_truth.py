"""v0.28.3 candidate source; published Latest remains v0.28.1 until publish."""

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


def test_current_guides_separate_v0280_latest_from_historical_v0272_truthfully() -> None:
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
        assert "v0.27.2" in text, relative_path
        assert "Latest" in text, relative_path
        assert "NOT RUN" in text, relative_path

    combined = " ".join(_normalized(path) for path in CURRENT_GUIDES)
    for marker in (
        "https://github.com/rupret007/webjam/releases/tag/v0.28.1",
        "393030220",
        "2026-09-21T14:30:22Z",
        "WebJam-v0.28.1-SHA256SUMS.txt",
        "200cac9eb04d01611696cdc147957b36daef257f",
        "db44247a3ceefb97f4cac6e623deef1bd32648a8",
        "deleted by owner",
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
        "three commits ahead",
        "three post-tag commits ahead",
        "#50–#52",
        "Current private test release: **v0.27.0**",
        "GitHub **Latest** is the unsigned/ad-hoc v0.27.1",
        "GitHub **Latest** remains v0.27.1",
        "GitHub Latest remains the immutable unsigned/ad-hoc v0.27.1",
        "Current private test release: **v0.27.1**",
        "unsigned v0.27.2 source candidate",
        "v0.27.2 source candidate",
        "No v0.27.2 tag",
        "No v0.27.2 release",
        "No v0.27.2 package",
        "No annotated `v0.28.0` tag",
        "candidate prep for unsigned",
        "Unsigned private test candidate (unpublished)",
        "still GitHub Latest until publish",
        "still GitHub Latest until v0.28.0 publish",
        "source-eligible-no-package-published",
        "GitHub **Latest** remains immutable unsigned/ad-hoc v0.27.2",
        "GitHub **Latest** remains v0.27.2",
        "immutable unsigned/ad-hoc v0.27.2 remains GitHub Latest",
        "Current published private test release (GitHub Latest): **v0.27.2**",
        "source candidate is unpublished **v0.28.0**",
        "GitHub Latest): **v0.27.2**; source candidate is unpublished",
        "GitHub **Latest** is still immutable v0.28.0",
        "still GitHub **Latest**",
    ):
        assert stale_claim.casefold() not in combined.casefold(), stale_claim

    assert "v0.28.1" in combined.casefold()
    assert "393030220" in combined
    assert "deleted by owner" in combined.casefold()
    assert "sealed at exact webjam v0.22.5" in combined.casefold()
    for historical_marker in (
        "377614785",
        "WebJam-v0.27.1-SHA256SUMS.txt",
        "1fc25f87c3386b1cd94303ecb407cdaff6509d1f",
    ):
        assert historical_marker.casefold() in combined.casefold()


def test_required_honesty_docs_lock_v0281_latest_and_deleted_historical() -> None:
    """Jeff-facing pass: v0.28.1 is Latest; older releases deleted by owner."""

    required = (
        "README.md",
        "CHANGELOG.md",
        "docs/MERGE_AND_RELEASE.md",
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
        "`master` still reports v0.27.1",
        "source tree's package identity remains `0.27.1`",
        "Current source:** post-release v0.27.1",
        "GitHub **Latest** is the unsigned/ad-hoc v0.27.1",
        "GitHub **Latest** remains v0.27.1",
        "GitHub Latest remains the immutable unsigned/ad-hoc v0.27.1",
        "Current private test release: **v0.27.1**",
        "unsigned v0.27.2 source candidate",
        "v0.27.2 source candidate",
        "No v0.27.2 tag",
        "No v0.27.2 release",
        "No v0.27.2 package",
        "No annotated `v0.28.0` tag",
        "candidate prep for unsigned",
        "still GitHub Latest until publish",
        "still GitHub Latest until v0.28.0 publish",
        "GitHub **Latest** remains immutable unsigned/ad-hoc v0.27.2",
        "Unsigned private test candidate (unpublished)",
        "Current published private test release (GitHub Latest): **v0.27.2**",
        "source candidate is unpublished **v0.28.0**",
        "GitHub **Latest** is still immutable v0.28.0",
        "immutable v0.28.0 release `388045385` stays GitHub **Latest**",
    )
    for relative_path in required:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        folded = normalized.casefold()
        assert "v0.28.1" in text, relative_path
        assert "latest" in folded and "393030220" in text, relative_path
        assert "200cac9eb04d01611696cdc147957b36daef257f" in text, relative_path
        assert "seven packages" in folded, relative_path
        assert "WebJam-v0.28.1-SHA256SUMS.txt" in text, relative_path
        assert "NOT RUN" in text, relative_path
        assert "deleted" in folded, relative_path
        for claim in forbidden:
            assert claim.casefold() not in folded, (relative_path, claim)


def test_demo_and_mobile_docs_match_published_v0281_latest_honesty() -> None:
    """DEMO/MOBILE keep Latest tip claims honest for v0.28.1 published release."""

    demo = _normalized("DEMO.md")
    assert "every v0.28.1" in demo.casefold()
    assert "physical and platform-trust gate remains" in demo.casefold()
    assert "**not run**" in demo.casefold()
    assert "Every v0.27 physical".casefold() not in demo.casefold()

    mobile = (ROOT / "docs" / "MOBILE.md").read_text(encoding="utf-8")
    folded = " ".join(mobile.split()).casefold()
    assert "200cac9eb04d01611696cdc147957b36daef257f" in mobile
    assert "393030220" in mobile
    assert "v0.28.1" in mobile
    assert "v0.28.1 published as latest" in folded
    assert "tip MATCH:** `origin/master` `828aef0d`".casefold() not in folded
    assert "NOT RUN" in mobile


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

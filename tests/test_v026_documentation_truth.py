"""Current-facing v0.26 documentation and local-link truth contracts."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
CURRENT_GUIDES = (
    "README.md",
    "README_SIMPLE.md",
    "FIRST_JAM.md",
    "USER_GUIDE.md",
    "RECORDING_AND_STUDIO.md",
    "HELP_ROUTING_MAP.md",
    "QUICK_HELP_MAP.md",
    "SECURITY.md",
    "TEST_PROCEDURE.md",
    "WEBEX_AUDIO_MODES.md",
    "CREATIVE_MODES_MVP_SPEC.md",
    "CHANGELOG.md",
    "docs/README.md",
    "docs/DESKTOP_RELEASE_RUNBOOK.md",
    "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
    "docs/PROJECT_BRIEF.md",
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


def test_current_guides_state_candidate_and_latest_identities_truthfully() -> None:
    for relative_path in (
        "README.md",
        "README_SIMPLE.md",
        "FIRST_JAM.md",
        "USER_GUIDE.md",
        "RECORDING_AND_STUDIO.md",
        "SECURITY.md",
        "TEST_PROCEDURE.md",
        "docs/README.md",
        "docs/DESKTOP_RELEASE_RUNBOOK.md",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
        "docs/PROJECT_BRIEF.md",
    ):
        text = _normalized(relative_path)
        assert "v0.26.0" in text, relative_path
        assert "v0.25.0" in text and "Latest" in text, relative_path
        assert "NOT RUN" in text, relative_path

    combined = " ".join(_normalized(path) for path in CURRENT_GUIDES)
    for claim in (
        "No v0.26.0 tag",
        "no v0.26.0 package",
        "unpublished v0.26.0",
        "v0.25.0 remains GitHub **Latest**",
    ):
        assert claim.casefold() in combined.casefold()


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
            target_path = source if not path_part else source.parent / unquote(path_part)
            target_path = target_path.resolve()
            if not target_path.exists():
                failures.append(f"{relative_path}: missing {target}")
                continue
            if separator and fragment and target_path.is_file():
                anchors = _heading_anchors(target_path.read_text(encoding="utf-8"))
                if unquote(fragment).casefold() not in anchors:
                    failures.append(f"{relative_path}: missing anchor {target}")
    assert not failures, "\n".join(failures)

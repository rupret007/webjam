"""Published v0.26.0 identity and immutable prior-release contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path

from core.jamulus_compatibility import ComponentTarget, JamulusRole
from tools.create_jamulus_component_catalog import build_payload
from tools.runtime_dependency_policy import application_version


ROOT = Path(__file__).resolve().parents[1]
VERSION_SOURCE = (ROOT / "webjam_qt" / "__init__.py").read_text(encoding="utf-8")
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
CI_WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
COMPONENT_RUNBOOK = (ROOT / "docs" / "JAMULUS_COMPONENT_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)
SBOM = json.loads(
    (ROOT / "packaging" / "WebJam-runtime-sbom.cdx.json").read_text(encoding="utf-8")
)
COMPONENT_SBOM = json.loads(
    (ROOT / "packaging" / "Jamulus-component-sbom.cdx.json").read_text(encoding="utf-8")
)
COMPONENT_UPDATE_SOURCE = (ROOT / "services" / "jamulus_component_update.py").read_text(
    encoding="utf-8"
)


def test_v0260_is_current_published_identity_without_rewriting_v025() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.26.0"
    assert application_version() == "0.26.0"
    assert README.startswith(
        "# WebJam\n\n## Native creator collaboration and multitrack recording"
    )
    assert "## [0.22.3]" in CHANGELOG
    assert "## [0.22.2]" in CHANGELOG
    assert "## [0.22.1]" in CHANGELOG
    assert "## [0.22.0]" in CHANGELOG
    normalized = " ".join(README.split())
    assert "v0.20.0 history must not be moved" in normalized
    assert "v0.21.0 history must not be moved" in normalized
    assert "v0.22.0 annotated tag and tagged bytes remain immutable" in normalized
    assert "published v0.22.1 tag, assets, and checksums likewise" in normalized
    assert "v0.22.4 is likewise a new source and package identity" in normalized
    assert "v0.22.5 is a new source and package identity" in normalized
    assert "Only the exact tag, release assets, checksum manifest" in normalized
    assert (
        "v0.25.0 is a new creator-multitrack source and package identity" in normalized
    )
    assert "v0.24.0 bytes" in normalized
    assert "Immutable v0.26.0 GitHub Latest private test release" in normalized
    assert "release ID `371442375`" in normalized
    assert "Immutable v0.25.0 is a historical private test candidate" in normalized
    assert "release ID `371028390`" in normalized
    assert "v0.24.0 release" in normalized
    assert (
        "published the exact frozen packages with the reviewed embedded" in normalized
    )
    assert (
        "real-world MP3, Reference Track, and first-demo reliability closeout"
        in normalized
    )


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.26.0",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.26.0",
        "type": "application",
        "version": "0.26.0",
    }


def test_component_sbom_names_the_exact_desktop_version() -> None:
    component = COMPONENT_SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:github/rupret007/webjam@0.26.0",
        "group": "rupret007",
        "name": "WebJam",
        "purl": "pkg:github/rupret007/webjam@0.26.0",
        "type": "application",
        "version": "0.26.0",
    }


def test_candidate_catalog_payload_tracks_v0260_without_rewriting_v0225() -> None:
    # Exercise deterministic source metadata with an unpublished, synthetic
    # next sequence. Sequence 6 remains the sealed v0.22.5 public catalog and
    # is never reused for this in-memory payload.
    synthetic_sequence = 7
    payload = build_payload(
        sequence=synthetic_sequence,
        issued_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        validity_days=30,
    )
    components = payload["components"]
    assert payload["webjam_version"] == "0.26.0"
    assert payload["sequence"] == synthetic_sequence
    assert isinstance(components, list)
    expected = {
        (role.value, target.value)
        for role in (JamulusRole.CLIENT, JamulusRole.SERVER)
        for target in ComponentTarget
    }
    assert len(components) == len(expected) == 8
    assert {
        (component["role"], component["target"]) for component in components
    } == expected
    assert all(component["component_id"] == "jamulus" for component in components)
    assert all(component["version"] == "3.12.3" for component in components)
    assert all(component["variant"] == "official" for component in components)
    assert all(
        component["webjam_range"]["maximum"] == "0.26.0" for component in components
    )


def test_current_guides_name_v026_latest_and_keep_prior_history() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.26.0",
        "CHANGELOG.md": (
            "## [0.25.0] — Creator profiles and authoritative multitrack "
            "private test candidate"
        ),
        "CLOSED_PILOT_PLAYBOOK.md": "v0.22.5 private test candidate",
        "CREATIVE_MODES_MVP_SPEC.md": ("# Creator profiles — v0.26.0 release contract"),
        "DEVELOPMENT.md": "# Developing WebJam v0.26.0",
        "FIRST_JAM.md": "# First Session — WebJam v0.26.0",
        "HELP_ROUTING_MAP.md": "# WebJam help routing — v0.26.0",
        "QUICK_HELP_MAP.md": "# WebJam quick help — v0.26.0",
        "README.md": "Immutable v0.26.0 GitHub Latest private test release",
        "README_SIMPLE.md": "use the exact release tag and attached checksum manifest",
        "RECORDING_AND_STUDIO.md": "# Recording and Studio — v0.26.0",
        "SECURITY.md": "Immutable v0.26.0 is the GitHub **Latest**",
        "TEST_PROCEDURE.md": "# WebJam v0.26.0 private-test-release procedure",
        "USER_GUIDE.md": "# WebJam creator guide — v0.26.0",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.26.0 UX acceptance checklist",
        "V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md": (
            "v0.25.0 was GitHub **Latest**"
        ),
        "WEBEX_AUDIO_MODES.md": "# Meeting-platform companion guidance — v0.26.0",
        "docs/DESKTOP_RELEASE_RUNBOOK.md": (
            "v0.26.0 pinned promotion status — completed"
        ),
        "docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md": (
            "v0.26.0 published fallback-only desktop state"
        ),
        "docs/PROJECT_BRIEF.md": "immutable v0.26.0 is the GitHub Latest",
        "docs/README.md": (
            "Current testing release:** immutable GitHub **Latest** is v0.26.0"
        ),
        "ios/README.md": "v0.26.0 private test release",
        "requirements-lock/README.md": "The v0.26.0 release uses",
        "packaging/windows/README-WINDOWS.txt": (
            "WebJam v0.26.0 private test candidate for Windows x64"
        ),
        "packaging/linux/README-LINUX.txt": (
            "WEBJAM v0.26.0 PRIVATE TEST CANDIDATE FOR LINUX x64"
        ),
        "packaging/macos/READ ME FIRST.txt": ("WEBJAM v0.26.0 PRIVATE TEST CANDIDATE"),
        "WEBJAM_V0225_DEMO_READINESS.md": "# WebJam v0.22.5 two-musician demo readiness",
        "V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md": (
            "Immutable historical release `367773776`, tag `v0.23.0`"
        ),
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md": "v0.26.0 private-test-release guide",
    }
    for relative_path, marker in expected.items():
        assert marker in (ROOT / relative_path).read_text(encoding="utf-8")


def test_reference_track_play_story_is_route_gated_not_locked() -> None:
    """Current guides must tell one story: Play is route-proof gated.

    Through v0.22.2 playback was locked outright; since v0.22.4 it is
    fail-closed behind machine-derived route proof.  A current document
    claiming playback simply "remains locked" would send a release run or a
    musician chasing behavior the source no longer has.
    """

    current_documents = (
        "README.md",
        "USER_GUIDE.md",
        "HELP_ROUTING_MAP.md",
        "QUICK_HELP_MAP.md",
        "docs/DESKTOP_RELEASE_RUNBOOK.md",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
    )
    for relative_path in current_documents:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert "remains deliberately **locked" not in normalized, relative_path
        assert "Play remains locked" not in normalized, relative_path
        assert "playback remains locked" not in normalized, relative_path


def test_changelog_marks_v026_published_and_keeps_prior_history() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert (
        "## [0.26.0] — Demo-proven creator multitrack private test release "
        "(2026-08-16)" in changelog
    )
    assert "Published on 2026-08-16 as immutable GitHub **Latest** release" in changelog
    assert "https://github.com/rupret007/webjam/releases/tag/v0.26.0" in changelog
    assert "No v0.26.0 tag, native package" not in changelog
    assert (
        "## [0.25.0] — Creator profiles and authoritative multitrack "
        "private test candidate (2026-08-15)" in changelog
    )
    assert "Published on 2026-08-15 as the immutable GitHub **Latest**" in changelog
    assert "public redownload verification passed" in changelog
    assert "## [0.24.0] — Recording-first workstation" in changelog
    assert "Published on 2026-08-11 as the immutable GitHub **Latest**" in changelog
    assert "## [0.23.0] — Shared Track and native multitrack" in changelog
    assert "exact publication state is authoritative" in changelog
    assert "## [0.22.5] — 2026-08-07" in changelog
    assert "Published as the immutable GitHub **Latest**" in changelog
    assert "## [0.22.4] — 2026-08-04" in changelog


def test_v0240_physical_checklist_is_linked_and_every_result_is_not_run() -> None:
    checklist_name = "V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md"
    checklist = (ROOT / checklist_name).read_text(encoding="utf-8")
    assert checklist_name in README
    assert "v0.24.0" in checklist
    result_rows = [
        line
        for line in checklist.splitlines()
        if re.match(r"^\| [A-Z][0-9]{2} \|", line)
    ]
    assert len(result_rows) >= 10
    assert all(line.endswith("| **NOT RUN** |") for line in result_rows)
    identity_section = checklist.split("## Exact candidate identity\n", 1)[1].split(
        "\n## A. Package and clean-start boundary", 1
    )[0]
    assert "| Host asset filename and SHA-256 | **NOT RUN" in identity_section
    assert "| Guest asset filename(s) and SHA-256 | **NOT RUN" in identity_section
    assert "Physical client/server identity is **NOT RUN**" in identity_section
    decision_section = checklist.split("## Release decision summary\n", 1)[1]
    decision_rows = [
        line
        for line in decision_section.splitlines()
        if line.startswith("| ") and "Gate family" not in line and "---" not in line
    ]
    assert len(decision_rows) == 11
    assert all("| **NOT RUN** | None |" in line for line in decision_rows)
    assert "Release recommendation: **NOT RUN" in decision_section


def test_v0250_physical_checklist_is_linked_and_every_result_is_not_run() -> None:
    checklist_name = "V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md"
    checklist = (ROOT / checklist_name).read_text(encoding="utf-8")
    assert checklist_name in README
    assert "v0.25.0" in checklist
    result_rows = [
        line
        for line in checklist.splitlines()
        if re.match(r"^\| [A-Z][0-9]{2} \|", line)
    ]
    assert len(result_rows) >= 20
    assert all(line.endswith("| **NOT RUN** |") for line in result_rows)
    identity_section = checklist.split("## Exact candidate identity\n", 1)[1].split(
        "\n## A. Package and clean-start boundary", 1
    )[0]
    assert identity_section.count("**VERIFIED — automated release evidence:**") == 9
    assert (
        "| Physical client/server Jamulus identity and package build IDs | "
        "**NOT RUN — no physical package run recorded** |"
    ) in identity_section
    for marker in (
        "004549d59af9020da886df29b26ed71f646d09b8",
        "251aa4ce8e936e021eeba50e28a297fbe5a8a765",
        "31878786472",
        "31879936789",
        "95003611103",
        "5db6a45d8b019671759a84027da92889ac7a4a0e",
        "31881581088",
        "31882801893",
        "95007614475",
        "95007634063",
        "371028390",
        "2026-08-15T11:45:43Z",
        "f4d83872e4ea482dcb4c0bc330675b8e14de70304bfe8086e1bfd9c5d42dd5bd",
        "4afae8ce6f9df58e7ce153756cabfafdaa7258ca0680f741315500d69962e917",
        "515615810",
        "515615817",
        "de6f12ffb2eb9df43f2fb636dbc9854d583d10767765c1f12676f44ba2efa9d0",
    ):
        assert marker in identity_section
    decision_section = checklist.split("## Release decision summary\n", 1)[1]
    decision_rows = [
        line
        for line in decision_section.splitlines()
        if line.startswith("| ") and "Gate family" not in line and "---" not in line
    ]
    assert len(decision_rows) == 15
    assert all("| **NOT RUN** |" in line for line in decision_rows)
    assert "Release recommendation: **NOT RUN**" in checklist


def test_v0260_checklist_verifies_release_identity_not_physical_results() -> None:
    checklist_name = "V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md"
    checklist = (ROOT / checklist_name).read_text(encoding="utf-8")
    assert checklist_name in CHANGELOG
    assert "v0.26.0" in checklist
    normalized = " ".join(checklist.replace(">", "").split())
    assert "Immutable v0.26.0 is the GitHub **Latest**" in normalized
    result_rows = [
        line
        for line in checklist.splitlines()
        if re.match(r"^\| [A-F][0-9]{2} \|", line)
    ]
    assert len(result_rows) >= 50
    assert all(line.endswith("| **NOT RUN** |") for line in result_rows)
    identity_section = checklist.split("## Exact candidate identity\n", 1)[1].split(
        "\n## A. Native packages, clean start, and trust", 1
    )[0]
    assert identity_section.count("**VERIFIED — automated release evidence:**") == 6
    assert "| I07 |" in identity_section and "**NOT RUN" in identity_section
    assert "| I08 |" in identity_section and "**NOT RUN" in identity_section
    for marker in (
        "3989baadaaa00b4655115e23cf900ea2c1c7fd4c",
        "4b5208098981943df8ddaf1fac31aa36c15146bb",
        "31971991226",
        "31973256062",
        "95231413287",
        "6b944ea1ef4693c85f4c9af453b56af38e0af8aa",
        "31975672599",
        "31976890936",
        "95237620181",
        "95237650912",
        "5936210571",
        "16891234364",
        "371442375",
        "2026-08-16T22:40:56Z",
        "404c5378017a37df6c5813d39348d16c386492a7acccd23797a3659495dea4da",
        "e6c49c6568877961ce484fa9dc477d8939c8bf881dfd568497da5752199d3aa3",
        "c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e",
        "https://github.com/rupret007/webjam/releases/tag/v0.26.0",
    ):
        assert marker in identity_section
    decision_section = checklist.split("## Release decision summary\n", 1)[1]
    decision_rows = [
        line
        for line in decision_section.splitlines()
        if line.startswith("| ") and "Gate family" not in line and "---" not in line
    ]
    assert len(decision_rows) == 11
    assert all(line.endswith("| **NOT RUN** |") for line in decision_rows)
    for marker in (
        "two physical computers",
        "one real multichannel audio interface",
        "Music journey and persistence",
        "Podcast & Voice journey and persistence",
        "Review & Rehearsal Preview boundary",
        "Generic meeting handoff and native Webex boundary",
        "Shared Track audibility and source identity",
        "guest capture",
        "Failure recovery",
        "Studio editing, persistence, and export",
        "compact layout, and accessibility",
    ):
        assert marker in normalized
    assert "Release recommendation: **NOT RUN**" in checklist


def test_v0250_guides_hold_the_creator_profile_and_recording_boundaries() -> None:
    profile_documents = (
        "README.md",
        "USER_GUIDE.md",
        "RECORDING_AND_STUDIO.md",
        "CREATIVE_MODES_MVP_SPEC.md",
        "docs/PROJECT_BRIEF.md",
    )
    for relative_path in profile_documents:
        normalized = " ".join(
            (ROOT / relative_path).read_text(encoding="utf-8").split()
        )
        folded = normalized.casefold()
        assert "Music" in normalized, relative_path
        assert "Podcast & Voice" in normalized, relative_path
        assert "Review & Rehearsal" in normalized, relative_path
        assert "playback/read-only" in folded, relative_path
        for blocked in (
            "standalone project",
            "track export",
            "shared notes",
            "media timecode",
        ):
            assert blocked in folded, (relative_path, blocked)
        assert "directly or automatically taps" in folded, relative_path
        assert "meeting or system" in folded, relative_path
        assert "do not route" in folded or "must not route" in folded, relative_path
        assert "visual sync" in folded or "visual synchronization" in folded, (
            relative_path
        )

    readme = " ".join(README.split())
    recording = " ".join(
        (ROOT / "RECORDING_AND_STUDIO.md").read_text(encoding="utf-8").split()
    )
    for text in (readme, recording):
        assert "true two-channel" in text
        assert "Shared Track" in text and "fingerprint" in text
        assert "guest Local Original" in text
        assert "directly or automatically taps" in text.casefold()
        assert "local originals" in text.casefold()
        assert "input devices" in text.casefold()


def test_v0250_local_notes_are_profile_scoped_bounded_and_local_only() -> None:
    for relative_path in (
        "README.md",
        "ARCHITECTURE.md",
        "SECURITY.md",
        "CREATIVE_MODES_MVP_SPEC.md",
    ):
        normalized = " ".join(
            (ROOT / relative_path).read_text(encoding="utf-8").split()
        )
        assert "profile-scoped" in normalized, relative_path
        assert "1 MiB" in normalized, relative_path
        assert "no-follow" in normalized, relative_path
        assert "never shared" in normalized, relative_path


def test_v0260_publication_evidence_is_exact() -> None:
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook.split("## v0.26.0 pinned promotion status — completed\n", 1)[
        1
    ].split("\n## Supported targets", 1)[0]
    for marker in (
        "3989baadaaa00b4655115e23cf900ea2c1c7fd4c",
        "4b5208098981943df8ddaf1fac31aa36c15146bb",
        "31971991226",
        "31973256062`, attempt 1; release job `95231413287",
        "6b944ea1ef4693c85f4c9af453b56af38e0af8aa",
        "31975672599",
        "31976890936",
        "95237620181",
        "95237650912",
        "5936210571",
        "16891234364",
        "371442375",
        "2026-08-16T22:40:56Z",
        "404c5378017a37df6c5813d39348d16c386492a7acccd23797a3659495dea4da",
        "e6c49c6568877961ce484fa9dc477d8939c8bf881dfd568497da5752199d3aa3",
        "c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e",
        "https://github.com/rupret007/webjam/releases/tag/v0.26.0",
    ):
        assert marker in section

    expected_assets = (
        (
            "517251779",
            "WebJam-linux-x64.zip",
            "168211648",
            "sha256:9b7216fa8591de0edb5e34dc45bb0b1a59e413bf9572c8e7c6c3c018ef72082e",
        ),
        (
            "517251778",
            "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
            "216225400",
            "sha256:9c92fa23ba334166b5d3fac6f26965d3a59519af6707f3f7fb5c2abdca04a80b",
        ),
        (
            "517251781",
            "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
            "222536890",
            "sha256:e3d3a1875cedcd232fba6ed4ba22d99e8016d6bd717736f4b66c9757c3691da3",
        ),
        (
            "517251780",
            "WebJam-v0.26.0-macos-arm64-ADHOC-TEST-ONLY.dmg",
            "217302612",
            "sha256:92ea140b1f5f820cae525f35b76e68af7c3d8a8d4fb330f200a3c40ec6659163",
        ),
        (
            "517251786",
            "WebJam-v0.26.0-macos-x64-ADHOC-TEST-ONLY.dmg",
            "223532070",
            "sha256:043339f5f45858ab7eec0df0a884a50acd841056103303e320108f2f8b9abbe7",
        ),
        (
            "517251782",
            "WebJam-v0.26.0-SHA256SUMS.txt",
            "749",
            "sha256:c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e",
        ),
        (
            "517251783",
            "WebJam-v0.26.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
            "144846325",
            "sha256:a3ec7711500836ced1bd0168107c441ef88681f1d48f770e31188cc9ed01b03d",
        ),
        (
            "517251787",
            "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
            "165555420",
            "sha256:0a1df1d8868e3b687824b84ff0bf75af2d1b07ba4fdb2bc0e0870e530658df32",
        ),
    )
    asset_rows = re.findall(
        r"(?m)^\| `(\d+)` \| `([^`]+)` \| `(\d+)` \| `(sha256:[0-9a-f]{64})` \|$",
        section,
    )
    assert asset_rows == list(expected_assets)

    checksum_block = section.split(
        "The manifest contains exactly seven package entries:\n\n```text\n", 1
    )[1].split("\n```", 1)[0]
    expected_checksums = [
        (digest.removeprefix("sha256:"), name)
        for _asset_id, name, _size, digest in expected_assets
        if name != "WebJam-v0.26.0-SHA256SUMS.txt"
    ]
    assert [tuple(line.split("  ", 1)) for line in checksum_block.splitlines()] == (
        expected_checksums
    )


def test_v0250_publication_evidence_is_exact() -> None:
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook.split(
        "### v0.25.0 creator-multitrack candidate — published Latest record\n", 1
    )[1]
    for marker in (
        "004549d59af9020da886df29b26ed71f646d09b8",
        "251aa4ce8e936e021eeba50e28a297fbe5a8a765",
        "31878786472",
        "31879936789`, attempt 1",
        "95003611103",
        "5db6a45d8b019671759a84027da92889ac7a4a0e",
        "31881581088",
        "31882801893",
        "95007614475",
        "95007634063",
        "371028390",
        "2026-08-15T11:45:43Z",
        "f4d83872e4ea482dcb4c0bc330675b8e14de70304bfe8086e1bfd9c5d42dd5bd",
        "4afae8ce6f9df58e7ce153756cabfafdaa7258ca0680f741315500d69962e917",
        "https://github.com/rupret007/webjam/releases/tag/v0.25.0",
    ):
        assert marker in section

    expected_assets = (
        (
            "515615814",
            "WebJam-linux-x64.zip",
            "168124665",
            "sha256:5e70a319af7e59a929fb197485b2403dd39d8d101c79a7eb04dbb1c88d82dc60",
        ),
        (
            "515615813",
            "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
            "216137815",
            "sha256:1da9615811f3669d09f344545077ac0c0d323091785377b8c7d9f16fb4355498",
        ),
        (
            "515615811",
            "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
            "222449885",
            "sha256:5eb202b326bf4a2f1ce991c2b962fc192853a56b847b33fd84aa4e8c0304e9ac",
        ),
        (
            "515615816",
            "WebJam-v0.25.0-SHA256SUMS.txt",
            "749",
            "sha256:de6f12ffb2eb9df43f2fb636dbc9854d583d10767765c1f12676f44ba2efa9d0",
        ),
        (
            "515615815",
            "WebJam-v0.25.0-macos-arm64-ADHOC-TEST-ONLY.dmg",
            "217200096",
            "sha256:90b4e765b3b45437b16c99cbf3423e6df29ba8ccf6f1c536befa3f74d977880a",
        ),
        (
            "515615812",
            "WebJam-v0.25.0-macos-x64-ADHOC-TEST-ONLY.dmg",
            "223463879",
            "sha256:3235110843ef70cb4ea3872792ccb1a8be161de6efefed5d9db94d1443501795",
        ),
        (
            "515615810",
            "WebJam-v0.25.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
            "144764425",
            "sha256:f60b5743997488041294b3c7008d40534400d9664b3fad47de878dbe3d921b08",
        ),
        (
            "515615817",
            "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
            "165469225",
            "sha256:10079dc6f0fab3f32c10c2a5d69a6305e16394158c1a81a1d678b58234bcaa62",
        ),
    )
    asset_rows = re.findall(
        r"(?m)^\| `(\d+)` \| `([^`]+)` \| `(\d+)` \| `(sha256:[0-9a-f]{64})` \|$",
        section,
    )
    assert asset_rows == list(expected_assets)

    checksum_block = section.split(
        "Its seven package entries are exactly:\n\n```text\n", 1
    )[1].split("\n```", 1)[0]
    expected_checksums = [
        (digest.removeprefix("sha256:"), name)
        for _asset_id, name, _size, digest in expected_assets
        if name != "WebJam-v0.25.0-SHA256SUMS.txt"
    ]
    assert [tuple(line.split("  ", 1)) for line in checksum_block.splitlines()] == (
        expected_checksums
    )


def test_v0240_publication_evidence_is_exact_and_current_guides_are_post_release() -> (
    None
):
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook.split(
        "### v0.24.0 recording-first candidate — published Latest record\n", 1
    )[1].split(
        "\n### v0.25.0 creator-multitrack candidate — published Latest record", 1
    )[0]
    for marker in (
        "99cb3798a925a39b70159e3a1a56166e98b5c316",
        "9edada8613b5aca6fec6a4110e2322611ad6658e",
        "31540572960",
        "31542495182`, attempt 2",
        "93953326611",
        "28c9d673985f81729b316f352f13704ffd0e845e",
        "31544471336",
        "31546157181",
        "93959002476",
        "93959070227",
        "368897541",
        "2026-08-11T23:23:12Z",
        "7eeee822a22929289d3d6aee792050e34633366b4f6708a5c9592f4a97315487",
        "83f9724cb83c79087c14e07beb873ef690ed43ac7a1d83218af1a0dc786a4184",
        "https://github.com/rupret007/webjam/releases/tag/v0.24.0",
    ):
        assert marker in section

    expected_assets = (
        (
            "510747174",
            "WebJam-linux-x64.zip",
            "168017509",
            "sha256:a8d4dd3bc0d6d3b8244baa85bd26fc12cf7e81bcd4187267c41a16bf471591c9",
        ),
        (
            "510747172",
            "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
            "216031863",
            "sha256:4f95e0e7de5ae59a9aec296869f1fd4d5f8c598e76a95a45981b7827f28cabc4",
        ),
        (
            "510747168",
            "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
            "222343926",
            "sha256:91d2dd05024ea558bd81b2a596a09c545ad9f72ae690c2ef7bce1d6d33360da5",
        ),
        (
            "510747169",
            "WebJam-v0.24.0-SHA256SUMS.txt",
            "749",
            "sha256:e24810b3d73c4032bc578f8eb236f64f450152c907843763830bbf8300b081d1",
        ),
        (
            "510747170",
            "WebJam-v0.24.0-macos-arm64-ADHOC-TEST-ONLY.dmg",
            "217132079",
            "sha256:1d6c698aab8382a8098a96b6602345e4bcb98770aaab6e56397a33f02d1d951a",
        ),
        (
            "510747175",
            "WebJam-v0.24.0-macos-x64-ADHOC-TEST-ONLY.dmg",
            "223311523",
            "sha256:1af795ab85ee246cf2c36785400e86a7f35b91883ed03a2097616e48039feac8",
        ),
        (
            "510747173",
            "WebJam-v0.24.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
            "144648416",
            "sha256:b463ddefb753f3ee745dcf7a58e20d2b69274d3814c9c1daf54c7a46aaf5b4bc",
        ),
        (
            "510747171",
            "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
            "165359997",
            "sha256:422b457f02291fbe5ecd55728b4d66ee4cde5112526d1461b8c1fa792639b79c",
        ),
    )
    asset_rows = re.findall(
        r"(?m)^\| `(\d+)` \| `([^`]+)` \| `(\d+)` \| `(sha256:[0-9a-f]{64})` \|$",
        section,
    )
    assert asset_rows == list(expected_assets)

    checksum_block = section.split(
        "Its seven package entries are exactly:\n\n```text\n", 1
    )[1].split("\n```", 1)[0]
    expected_checksums = [
        (digest.removeprefix("sha256:"), name)
        for _asset_id, name, _size, digest in expected_assets
        if name != "WebJam-v0.24.0-SHA256SUMS.txt"
    ]
    assert [tuple(line.split("  ", 1)) for line in checksum_block.splitlines()] == (
        expected_checksums
    )

    current_documents = (
        "README.md",
        "README_SIMPLE.md",
        "CHANGELOG.md",
        "ARCHITECTURE.md",
        "CREATIVE_MODES_MVP_SPEC.md",
        "DEVELOPMENT.md",
        "FIRST_JAM.md",
        "HELP_ROUTING_MAP.md",
        "QUICK_HELP_MAP.md",
        "RECORDING_AND_STUDIO.md",
        "SECURITY.md",
        "TEST_PROCEDURE.md",
        "USER_GUIDE.md",
        "UX_ACCEPTANCE_CHECKLIST.md",
        "V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md",
        "WEBEX_AUDIO_MODES.md",
        "docs/DESKTOP_RELEASE_RUNBOOK.md",
        "docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md",
        "docs/PROJECT_BRIEF.md",
        "docs/README.md",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
        "ios/README.md",
        "requirements-lock/README.md",
    )
    forbidden = (
        "GitHub **Latest** remains immutable v0.23.0",
        "v0.23.0 remains GitHub Latest",
        "until protected v0.24 promotion",
        "until v0.24.0's protected promotion",
        "If no v0.24.0 release exists yet",
        "GitHub **Latest** remains immutable v0.24.0",
        "Immutable v0.24.0 remains GitHub **Latest**",
        "Current private test release: **v0.24.0**",
        "v0.25.0 is an unpublished",
        "unpublished v0.25.0 source",
        "No v0.25.0 release ID",
        "no v0.25.0 kit is published yet",
    )
    for relative_path in current_documents:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(marker in content for marker in forbidden), relative_path


def test_candidate_package_copy_is_explicit_about_platform_trust() -> None:
    windows_readme = (ROOT / "packaging" / "windows" / "README-WINDOWS.txt").read_text(
        encoding="utf-8"
    )
    macos_readme = (ROOT / "packaging" / "macos" / "READ ME FIRST.txt").read_text(
        encoding="utf-8"
    )
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    assert "unsigned private test candidate" in windows_readme
    assert "ad-hoc signed and is NOT notarized" in macos_readme
    for package_copy in (windows_readme, macos_readme):
        normalized = " ".join(package_copy.split())
        assert "PRIVATE TEST CANDIDATE" in normalized
        assert "exact filename appears" in normalized
        assert "Do not use the immutable v0.25.0 checksum manifest" in normalized
        assert "sealed v0.22.5" in normalized
        assert "embedded Jamulus 3.12.2 fallback" in normalized
    inventory = runbook.split("The exact v0.22.4 published inventory is:\n", 1)[
        1
    ].split("\nThe separate `jamulus-components-v2`", 1)[0]
    assert re.findall(r"(?m)^- `([^`]+)`$", inventory) == [
        "WebJam-v0.22.4-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "WebJam-v0.22.4-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-v0.22.4-macos-x64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "WebJam-linux-x64.zip",
        "WebJam-v0.22.4-SHA256SUMS.txt",
    ]
    assert "explicit **Latest** setting" in runbook


def test_component_catalog_current_public_state_is_sealed() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert (
        "public v3 channel is immutable sequence 6 for exact WebJam 0.22.5"
    ) in normalized
    assert "2026-09-05T14:13:12Z" in COMPONENT_RUNBOOK
    assert (
        "57eed122607c0859e82c4b7121cd5e4aaba397f4722b18c36189f1660225eb68"
        in COMPONENT_RUNBOOK
    )
    assert "one immutable asset" in normalized
    assert "non-Latest prerelease" in normalized
    assert "new fixed catalog URL" in normalized
    assert "Never move or replace that tag" in normalized


def test_v0225_publication_evidence_is_exact() -> None:
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    inventory = runbook.split("The exact published release inventory is:\n", 1)[
        1
    ].split("\nThe original promotion contract", 1)[0]
    assert re.findall(r"(?m)^- `([^`]+)`$", inventory) == [
        "WebJam-v0.22.5-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "WebJam-v0.22.5-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-v0.22.5-macos-x64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "WebJam-linux-x64.zip",
        "WebJam-v0.22.5-SHA256SUMS.txt",
    ]
    for marker in (
        "d7d0039759e8334407fe2e6ed9e42edf0d7ef639",
        "31206070715",
        "31208008965",
        "31208271585",
        "31210531934",
        "366957478",
        "366930115",
    ):
        assert marker in runbook


def test_v0225_uses_a_new_component_channel_boundary() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert "jamulus-components-v3" in COMPONENT_UPDATE_SOURCE
    assert "jamulus-components-v2" not in COMPONENT_UPDATE_SOURCE
    assert "v0.22.5 versioned-channel transition" in COMPONENT_RUNBOOK
    assert "new fixed catalog URL" in normalized
    assert "signed sequence 6 for exact WebJam 0.22.5" in normalized
    assert "must never move or replace v1/v2" in normalized


def test_component_catalog_historical_promotion_record_is_preserved() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert "creates the unpublished desktop draft" in normalized
    assert "Do not run **Publish Verified WebJam Release** yet." in normalized
    assert "Public verification before desktop promotion" in COMPONENT_RUNBOOK
    assert "exact verified v0.22.2 Mac draft package" in normalized
    assert "only after steps 1–6 pass" in normalized
    assert "jamulus-components-v2" in COMPONENT_RUNBOOK
    assert "sealed v0.22.4 non-Latest prerelease" in normalized
    assert "sequence 3" in normalized
    assert "Never move or replace that tag" in normalized


def test_draft_release_notes_explain_the_jamulus_update_boundary() -> None:
    release_job = CI_WORKFLOW.split("  release:\n", 1)[1]
    body = release_job.split("          body: |\n", 1)[1].split(
        "          fail_on_unmatched_files:", 1
    )[0]
    normalized = " ".join(body.lower().split())
    assert "managed updates require" in normalized
    assert "exactly authorizes this webjam version" in normalized
    assert "component catalog" in normalized
    assert "separate" in normalized
    assert "jamulus 3.12.2" in normalized
    assert "fallback" in normalized
    assert "jamulus 3.12.3 updates are authorized" not in normalized
    assert "approval" in normalized
    assert "active" in normalized
    assert "interrupt" in normalized


def test_linux_ci_isolates_native_qt_state_without_retrying_tests() -> None:
    test_step = CI_WORKFLOW.split("      - name: Run test suite\n", 1)[1].split(
        "\n  # ------------------------------------------------------------------", 1
    )[0]
    assert "git ls-files 'tests/test_*.py'" in test_step
    assert 'for test_file in "${test_files[@]}"' in test_step
    assert 'python -m pytest "$test_file" -v' in test_step
    assert "pytest tests/ -v" not in test_step
    assert "--reruns" not in test_step
    assert "pytest-rerunfailures" not in test_step


def test_reference_studio_late_import_graph_is_explicitly_frozen() -> None:
    modules = (
        "core.song_project",
        "core.song_project_store",
        "core.song_project_controller",
        "core.song_media_catalog",
        "core.song_studio_store",
        "core.song_studio_controller",
        "core.song_studio_reconcile",
        "core.song_studio_clone",
        "core.project_audio",
        "core.project_playback",
        "core.project_recording",
        "core.project_recording_commit",
        "core.project_tempo_analysis",
        "core.song_bounce",
        "core.studio_tempo",
        "core.studio_mixer",
        "webjam_qt.controllers.reference_studio_application",
        "webjam_qt.widgets.reference_studio_shell",
        "webjam_qt.widgets.reference_studio_workspace",
        "webjam_qt.widgets.studio_project_home",
        "webjam_qt.widgets.studio_waveforms",
        "webjam_qt.windows.reference_studio_tools",
        "webjam_qt.windows.reference_studio_mixer",
        "services.reference_studio_packaged_smoke",
    )
    for module in modules:
        assert f'"{module}"' in SPEC


def test_reference_studio_runtime_licenses_and_inventory_are_packaged() -> None:
    for relative_path in (
        "THIRD_PARTY_NOTICES_RUNTIME.md",
        "packaging/WebJam-runtime-sbom.cdx.json",
        "packaging/runtime-dependency-policy.json",
        "licenses/SOUNDFILE_LICENSE.txt",
        "licenses/SOUNDFILE_WHEEL_LICENSE_NOTES.md",
    ):
        assert relative_path.rsplit("/", 1)[-1] in SPEC

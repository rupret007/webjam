"""v0.24.0 source identity and immutable prior-release contracts."""

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
CI_WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
COMPONENT_RUNBOOK = (
    ROOT / "docs" / "JAMULUS_COMPONENT_RELEASE_RUNBOOK.md"
).read_text(encoding="utf-8")
SBOM = json.loads(
    (ROOT / "packaging" / "WebJam-runtime-sbom.cdx.json").read_text(
        encoding="utf-8"
    )
)
COMPONENT_SBOM = json.loads(
    (ROOT / "packaging" / "Jamulus-component-sbom.cdx.json").read_text(
        encoding="utf-8"
    )
)
COMPONENT_UPDATE_SOURCE = (
    ROOT / "services" / "jamulus_component_update.py"
).read_text(encoding="utf-8")


def test_v0240_is_the_published_source_identity() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.24.0"
    assert application_version() == "0.24.0"
    assert README.startswith("# WebJam\n\n## Unified creative collaboration for live music")
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
    assert "v0.24.0 is a new recording-first identity" in normalized
    assert "v0.23.0 bytes" in normalized
    assert "Immutable v0.24.0 GitHub Latest private test candidate" in normalized
    assert "release ID `368897541`" in normalized
    assert "published the exact frozen packages with the reviewed embedded" in normalized
    assert (
        "real-world MP3, Reference Track, and first-demo reliability closeout"
        in normalized
    )


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.24.0",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.24.0",
        "type": "application",
        "version": "0.24.0",
    }


def test_component_sbom_names_the_exact_desktop_version() -> None:
    component = COMPONENT_SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:github/rupret007/webjam@0.24.0",
        "group": "rupret007",
        "name": "WebJam",
        "purl": "pkg:github/rupret007/webjam@0.24.0",
        "type": "application",
        "version": "0.24.0",
    }


def test_candidate_catalog_payload_tracks_v0240_without_rewriting_v0225() -> None:
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
    assert payload["webjam_version"] == "0.24.0"
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
        component["webjam_range"]["maximum"] == "0.24.0"
        for component in components
    )


def test_current_guides_separate_v0240_release_from_v0230_history() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.24.0 private test release",
        "CLOSED_PILOT_PLAYBOOK.md": "v0.22.5 private test candidate",
        "DEVELOPMENT.md": "# Developing WebJam v0.24.0",
        "FIRST_JAM.md": "# First Jam — WebJam v0.24.0 private test release",
        "README_SIMPLE.md": "use the exact release tag and attached checksum manifest",
        "SECURITY.md": "Immutable v0.24.0 is the current GitHub **Latest**",
        "TEST_PROCEDURE.md": "# WebJam v0.24.0 release and physical test procedure",
        "USER_GUIDE.md": "# WebJam musician guide — v0.24.0 private test release",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.24.0 UX acceptance checklist",
        "RECORDING_AND_STUDIO.md": (
            "# Recording and Studio — v0.24.0 private test release"
        ),
        "WEBEX_AUDIO_MODES.md": (
            "# Conversation companion guidance — v0.24.0 private test release"
        ),
        "ios/README.md": "exact v0.24.0 Mac test assets",
        "requirements-lock/README.md": (
            "The immutable v0.24.0 private test release uses the exact dependency locks"
        ),
        "WEBJAM_V0225_DEMO_READINESS.md": "# WebJam v0.22.5 two-musician demo readiness",
        "V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md": (
            "Immutable historical release `367773776`, tag `v0.23.0`"
        ),
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md": (
            "immutable GitHub **Latest** v0.24.0"
        ),
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


def test_changelog_marks_v024_candidate_and_keeps_prior_history() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
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
        line for line in checklist.splitlines()
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


def test_v0240_publication_evidence_is_exact_and_current_guides_are_post_release() -> None:
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    section = runbook.split(
        "### v0.24.0 recording-first candidate — published Latest record\n", 1
    )[1]
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
        "ARCHITECTURE.md",
        "DEVELOPMENT.md",
        "FIRST_JAM.md",
        "HELP_ROUTING_MAP.md",
        "QUICK_HELP_MAP.md",
        "README_SIMPLE.md",
        "SECURITY.md",
        "RECORDING_AND_STUDIO.md",
        "TEST_PROCEDURE.md",
        "USER_GUIDE.md",
        "UX_ACCEPTANCE_CHECKLIST.md",
        "WEBEX_AUDIO_MODES.md",
        "docs/PROJECT_BRIEF.md",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md",
    )
    forbidden = (
        "GitHub **Latest** remains immutable v0.23.0",
        "v0.23.0 remains GitHub Latest",
        "until protected v0.24 promotion",
        "until v0.24.0's protected promotion",
        "If no v0.24.0 release exists yet",
    )
    for relative_path in current_documents:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(marker in content for marker in forbidden), relative_path


def test_candidate_package_copy_is_explicit_about_platform_trust() -> None:
    windows_readme = (
        ROOT / "packaging" / "windows" / "README-WINDOWS.txt"
    ).read_text(encoding="utf-8")
    macos_readme = (
        ROOT / "packaging" / "macos" / "READ ME FIRST.txt"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    assert "unsigned private test candidate" in windows_readme
    assert "ad-hoc signed and is NOT notarized" in macos_readme
    for package_copy in (windows_readme, macos_readme):
        normalized = " ".join(package_copy.split())
        assert "PRIVATE TEST CANDIDATE" in normalized
        assert "exact filename appears" in normalized
        assert "Do not use the immutable v0.23.0 checksum manifest" in normalized
        assert "sealed v0.22.5" in normalized
        assert "embedded Jamulus 3.12.2 fallback" in normalized
    inventory = runbook.split(
        "The exact v0.22.4 published inventory is:\n", 1
    )[1].split("\nThe separate `jamulus-components-v2`", 1)[0]
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
    runbook = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    inventory = runbook.split(
        "The exact published release inventory is:\n", 1
    )[1].split("\nThe original promotion contract", 1)[0]
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

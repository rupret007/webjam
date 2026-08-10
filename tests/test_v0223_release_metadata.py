"""v0.23.0 source identity and immutable v0.22.5 release contracts."""

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


def test_v0230_is_the_unpublished_source_candidate_identity() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.23.0"
    assert application_version() == "0.23.0"
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
    assert "v0.23.0 is the separate Shared Track" in normalized
    assert "publishes the exact frozen packages with the reviewed embedded" in normalized
    assert (
        "real-world MP3, Reference Track, and first-demo reliability closeout"
        in normalized
    )


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.23.0",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.23.0",
        "type": "application",
        "version": "0.23.0",
    }


def test_component_sbom_names_the_exact_desktop_version() -> None:
    component = COMPONENT_SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:github/rupret007/webjam@0.23.0",
        "group": "rupret007",
        "name": "WebJam",
        "purl": "pkg:github/rupret007/webjam@0.23.0",
        "type": "application",
        "version": "0.23.0",
    }


def test_candidate_catalog_payload_tracks_v0230_without_rewriting_v0225() -> None:
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
    assert payload["webjam_version"] == "0.23.0"
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
        component["webjam_range"]["maximum"] == "0.23.0"
        for component in components
    )


def test_current_guides_separate_v0230_source_from_v0225_history() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.23.0 source candidate",
        "CLOSED_PILOT_PLAYBOOK.md": "v0.22.5 private test candidate",
        "DEVELOPMENT.md": "# Developing WebJam v0.23.0",
        "FIRST_JAM.md": "# First Jam — WebJam v0.23.0 source candidate",
        "README_SIMPLE.md": "use the exact release tag and attached checksum manifest",
        "TEST_PROCEDURE.md": "# WebJam v0.23.0 source and physical test procedure",
        "USER_GUIDE.md": "# WebJam musician guide — v0.23.0 source candidate",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.23.0 UX acceptance checklist",
        "RECORDING_AND_STUDIO.md": (
            "# Recording and Studio — v0.23.0 source candidate"
        ),
        "WEBEX_AUDIO_MODES.md": "# Webex companion guidance — v0.22.5",
        "ios/README.md": "matching immutable v0.22.5",
        "requirements-lock/README.md": (
            "The v0.23.0 candidate inherits the exact dependency locks"
        ),
        "WEBJAM_V0225_DEMO_READINESS.md": "# WebJam v0.22.5 two-musician demo readiness",
        "docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md": (
            "immutable historical v0.22.5"
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


def test_changelog_marks_v023_candidate_and_keeps_future_work_unreleased() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert "## [0.23.0] — Shared Track and native multitrack" in changelog
    assert "exact publication state is authoritative" in changelog
    assert "## [0.22.5] — 2026-08-07" in changelog
    assert "Published as the immutable GitHub **Latest**" in changelog
    assert "## [0.22.4] — 2026-08-04" in changelog


def test_v0230_physical_checklist_is_linked_and_every_result_is_not_run() -> None:
    checklist_name = "V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md"
    checklist = (ROOT / checklist_name).read_text(encoding="utf-8")
    assert checklist_name in README
    assert "v0.23.0" in checklist
    result_rows = [
        line for line in checklist.splitlines()
        if re.match(r"^\| [A-Z][0-9]{2} \|", line)
    ]
    assert len(result_rows) >= 10
    assert all(line.endswith("| **NOT RUN** |") for line in result_rows)


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
        assert "Do not use the immutable v0.22.5 checksum manifest" in normalized
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

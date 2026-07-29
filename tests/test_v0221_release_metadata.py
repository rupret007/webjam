"""Release identity and frozen Reference Studio packaging contracts."""

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


def test_v0221_is_the_single_packaged_candidate_identity() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.22.1"
    assert application_version() == "0.22.1"
    assert README.startswith("# WebJam v0.22.1 unsigned private test candidate")
    assert "## [0.22.1]" in CHANGELOG
    assert "## [0.22.0]" in CHANGELOG
    assert "v0.20.0 history must not be moved" in README
    assert "v0.21.0 history must not be moved" in README
    assert "v0.22.0 annotated tag and tagged bytes remain immutable" in README
    assert "only that obsolete draft is deleted by release ID" in README


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.22.1",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.22.1",
        "type": "application",
        "version": "0.22.1",
    }


def test_component_sbom_names_the_exact_desktop_version() -> None:
    component = COMPONENT_SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:github/rupret007/webjam@0.22.1",
        "group": "rupret007",
        "name": "WebJam",
        "purl": "pkg:github/rupret007/webjam@0.22.1",
        "type": "application",
        "version": "0.22.1",
    }


def test_signed_catalog_has_every_v0221_client_server_target_once() -> None:
    payload = build_payload(
        sequence=2,
        issued_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        validity_days=30,
    )
    components = payload["components"]
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


def test_current_candidate_guides_report_v0221_consistently() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.22.1",
        "CLOSED_PILOT_PLAYBOOK.md": "current v0.22.1 private test candidate",
        "DEVELOPMENT.md": "# Developing WebJam v0.22.1",
        "FIRST_JAM.md": "# First Jam — WebJam v0.22.1",
        "README_SIMPLE.md": "Current source candidate: **v0.22.1",
        "TEST_PROCEDURE.md": "# WebJam v0.22.1 source and physical test procedure",
        "USER_GUIDE.md": "# WebJam musician guide — v0.22.1",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.22.1 UX acceptance checklist",
        "WEBEX_AUDIO_MODES.md": "# Webex companion guidance — v0.22.1",
        "ios/README.md": "matching v0.22.1 Mac candidate",
        "requirements-lock/README.md": "The v0.22.1 candidate locks",
    }
    for relative_path, marker in expected.items():
        assert marker in (ROOT / relative_path).read_text(encoding="utf-8")


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
    inventory = runbook.split(
        "The exact v0.22.1 draft inventory is:\n", 1
    )[1].split("\nThe checksum file", 1)[0]
    assert re.findall(r"(?m)^- `([^`]+)`$", inventory) == [
        "WebJam-v0.22.1-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "WebJam-v0.22.1-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-v0.22.1-macos-x64-ADHOC-TEST-ONLY.dmg",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "WebJam-linux-x64.zip",
        "WebJam-v0.22.1-SHA256SUMS.txt",
    ]
    assert "explicit **Latest** setting" in runbook


def test_component_catalog_is_live_and_verified_before_latest_promotion() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert "creates the unpublished desktop draft" in normalized
    assert "Do not run **Publish Verified WebJam Release** yet." in normalized
    assert "Public verification before desktop promotion" in COMPONENT_RUNBOOK
    assert "exact verified v0.22.1 Mac draft package" in normalized
    assert "only after steps 1–6 pass" in normalized
    assert "jamulus-components-v1" in COMPONENT_RUNBOOK
    assert "remains a public non-Latest prerelease" in normalized
    assert "sequence 2" in normalized
    assert "Never move or replace that tag" in normalized


def test_draft_release_notes_explain_the_jamulus_update_boundary() -> None:
    release_job = CI_WORKFLOW.split("  release:\n", 1)[1]
    body = release_job.split("          body: |\n", 1)[1].split(
        "          fail_on_unmatched_files:", 1
    )[0]
    normalized = " ".join(body.lower().split())
    assert "jamulus 3.12.3" in normalized
    assert "component catalog" in normalized
    assert "separate" in normalized
    assert "jamulus 3.12.2" in normalized
    assert "fallback" in normalized
    assert "approval" in normalized
    assert "active" in normalized
    assert "interrupt" in normalized


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

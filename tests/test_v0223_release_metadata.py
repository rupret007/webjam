"""v0.22.5 candidate identity and immutable release-history contracts."""

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


def test_v0225_is_the_single_packaged_candidate_identity() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.22.5"
    assert application_version() == "0.22.5"
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
    assert "v0.22.5 is a new candidate source and package identity" in normalized
    assert "master is the v0.22.5 release-candidate line" in normalized
    assert "Reference Track and first-demo reliability closeout" in normalized


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.22.5",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.22.5",
        "type": "application",
        "version": "0.22.5",
    }


def test_component_sbom_names_the_exact_desktop_version() -> None:
    component = COMPONENT_SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:github/rupret007/webjam@0.22.5",
        "group": "rupret007",
        "name": "WebJam",
        "purl": "pkg:github/rupret007/webjam@0.22.5",
        "type": "application",
        "version": "0.22.5",
    }


def test_signed_catalog_has_every_v0225_client_server_target_once() -> None:
    payload = build_payload(
        sequence=6,
        issued_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        validity_days=30,
    )
    components = payload["components"]
    assert payload["webjam_version"] == "0.22.5"
    assert payload["sequence"] == 6
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


def test_current_release_guides_report_v0225_consistently() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.22.5",
        "CLOSED_PILOT_PLAYBOOK.md": "v0.22.5 private test candidate",
        "DEVELOPMENT.md": "# Developing WebJam v0.22.5",
        "FIRST_JAM.md": "# First Jam — WebJam v0.22.5",
        "README_SIMPLE.md": "master` is the v0.22.5 candidate line",
        "TEST_PROCEDURE.md": "# WebJam v0.22.5 source and physical test procedure",
        "USER_GUIDE.md": "# WebJam musician guide — v0.22.5",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.22.5 UX acceptance checklist",
        "WEBEX_AUDIO_MODES.md": "# Webex companion guidance — v0.22.5",
        "ios/README.md": "matching v0.22.5 Mac candidate",
        "requirements-lock/README.md": "The v0.22.5 candidate locks",
        "WEBJAM_V0225_DEMO_READINESS.md": "# WebJam v0.22.5 two-musician demo readiness",
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
    assert "PRE-PUBLICATION TEST CANDIDATE" in windows_readme
    assert "WebJam-v0.22.5-SHA256SUMS.txt" in windows_readme
    assert "PRE-PUBLICATION TEST CANDIDATE" in macos_readme
    assert "WebJam-v0.22.5-SHA256SUMS.txt" in macos_readme
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
        "current public v2 channel is immutable sequence 5 for exact WebJam "
        "0.22.4"
    ) in normalized
    assert "2026-09-03T12:00:00Z" in COMPONENT_RUNBOOK
    assert (
        "c5b034dad933a7ffea670cccecaf308947f5ab93f7fedeb0cde0ce8f9e34e83f"
        in COMPONENT_RUNBOOK
    )
    assert "one immutable asset" in normalized
    assert "non-Latest prerelease" in normalized
    assert "new fixed catalog URL" in normalized
    assert "Never move or replace that tag" in normalized


def test_v0225_uses_a_new_component_channel_boundary() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert "jamulus-components-v3" in COMPONENT_UPDATE_SOURCE
    assert "jamulus-components-v2" not in COMPONENT_UPDATE_SOURCE
    assert "v0.22.5 versioned-channel transition" in COMPONENT_RUNBOOK
    assert "new fixed catalog URL" in normalized
    assert "exact sequence 6" in normalized
    assert "must never move or replace v1/v2" in normalized


def test_component_catalog_historical_promotion_record_is_preserved() -> None:
    normalized = " ".join(COMPONENT_RUNBOOK.split())
    assert "creates the unpublished desktop draft" in normalized
    assert "Do not run **Publish Verified WebJam Release** yet." in normalized
    assert "Public verification before desktop promotion" in COMPONENT_RUNBOOK
    assert "exact verified v0.22.2 Mac draft package" in normalized
    assert "only after steps 1–6 pass" in normalized
    assert "jamulus-components-v2" in COMPONENT_RUNBOOK
    assert "remains a public non-Latest prerelease" in normalized
    assert "sequence 3" in normalized
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

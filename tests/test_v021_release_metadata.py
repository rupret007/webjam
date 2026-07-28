"""Release identity and frozen Reference Studio packaging contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.runtime_dependency_policy import application_version


ROOT = Path(__file__).resolve().parents[1]
VERSION_SOURCE = (ROOT / "webjam_qt" / "__init__.py").read_text(encoding="utf-8")
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
SBOM = json.loads(
    (ROOT / "packaging" / "WebJam-runtime-sbom.cdx.json").read_text(
        encoding="utf-8"
    )
)


def test_v021_is_the_single_packaged_candidate_identity() -> None:
    match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        VERSION_SOURCE,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == "0.21.0"
    assert application_version() == "0.21.0"
    assert README.startswith("# WebJam v0.21.0 unsigned private test candidate")
    assert "## [0.21.0]" in CHANGELOG
    assert "v0.20.0 history must not be moved" in README


def test_runtime_sbom_names_the_exact_desktop_version() -> None:
    component = SBOM["metadata"]["component"]
    assert component == {
        "bom-ref": "pkg:generic/webjam@0.21.0",
        "name": "WebJam",
        "purl": "pkg:generic/webjam@0.21.0",
        "type": "application",
        "version": "0.21.0",
    }


def test_current_candidate_guides_report_v021_consistently() -> None:
    expected = {
        "ARCHITECTURE.md": "# WebJam architecture — v0.21.0",
        "CLOSED_PILOT_PLAYBOOK.md": "current v0.21.0 private test candidate",
        "DEVELOPMENT.md": "# Developing WebJam v0.21.0",
        "FIRST_JAM.md": "# First Jam — WebJam v0.21.0",
        "README_SIMPLE.md": "Current source candidate: **v0.21.0",
        "TEST_PROCEDURE.md": "# WebJam v0.21.0 source and physical test procedure",
        "USER_GUIDE.md": "# WebJam musician guide — v0.21.0",
        "UX_ACCEPTANCE_CHECKLIST.md": "# WebJam v0.21.0 UX acceptance checklist",
        "WEBEX_AUDIO_MODES.md": "# Webex companion guidance — v0.21.0",
        "ios/README.md": "matching v0.21.0 Mac candidate",
        "requirements-lock/README.md": "The v0.21.0 candidate locks",
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
    assert "WebJam-v0.21.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe" in runbook
    assert "WebJam-v0.21.0-macos-arm64-ADHOC-TEST-ONLY.dmg" in runbook
    assert "WebJam-v0.21.0-macos-x64-ADHOC-TEST-ONLY.dmg" in runbook
    assert "WebJam-v0.21.0-SHA256SUMS.txt" in runbook
    assert "explicit **Latest** setting" in runbook


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

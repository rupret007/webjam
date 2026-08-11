"""Focused Jamulus component SBOM and quarantined-evidence contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.jamulus_compatibility import ComponentTarget, JamulusRole
from tools.create_jamulus_component_catalog import (
    APPROVED_COMPONENT_VERSION,
    CATALOG_FILENAME,
    EXPECTED_COMPONENT_COUNT,
    build_payload,
)
from tools.jamulus_windows_runtime_contract import manifest_payload

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
MACOS = PACKAGING / "macos"
SBOM_PATH = PACKAGING / "Jamulus-component-sbom.cdx.json"
SCHEMA_PATH = MACOS / "jamulus-headless-component-evidence.schema.json"
GENERATOR_PATH = MACOS / "create-jamulus-headless-component-evidence.py"
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

_SPEC = importlib.util.spec_from_file_location(
    "jamulus_headless_component_evidence", GENERATOR_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_EVIDENCE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVIDENCE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_material(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def _candidate(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "JamulusHeadlessClient.app"
    binary = app / "Contents" / "MacOS" / "JamulusHeadlessClient"
    binary_sha = _write_material(binary, b"reviewed executable")
    binary.chmod(0o755)
    license_root = app / "Contents" / "Resources" / "THIRD_PARTY_LICENSES"
    license_sha = _write_material(
        license_root / "JAMULUS_COPYING.txt", b"AGPL and GPL text"
    )
    patch_sha = _write_material(
        license_root / "jamulus-headless-r3_12_3.patch", b"reviewed patch"
    )
    source_sha = _write_material(
        license_root / "JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz",
        b"complete source archive",
    )
    qt_sha = _write_material(
        license_root / "qtbase-everywhere-src-6.10.2.tar.xz",
        b"exact Qt source",
    )
    framework = app / "Contents" / "Frameworks" / "QtCore.framework"
    (framework / "Versions" / "A").mkdir(parents=True)
    os.symlink("A", framework / "Versions" / "Current")
    os.symlink("Versions/Current/QtCore", framework / "QtCore")
    _write_material(framework / "Versions" / "A" / "QtCore", b"framework")
    provenance = {
        "format": "1",
        "component": "JamulusHeadlessClient",
        "version": "3.12.3",
        "profile": "r3_12_3",
        "source_repository": "https://github.com/jamulussoftware/jamulus.git",
        "source_commit": "74dc422116983a2173eb917cb4d6a403886b31e5",
        "source_tag": "r3_12_3",
        "source_tree": "1" * 40,
        "source_archive_commit": "2" * 40,
        "corresponding_source_sha256": source_sha,
        "patch_sha256": patch_sha,
        "license_sha256": license_sha,
        "qt_version": "6.10.2",
        "qt_source_archive_sha256": qt_sha,
        "aqtinstall_version": "3.3.0",
        "architecture": "arm64",
        "deployment_target": "13.0",
        "apple_clang_version": "Apple clang version fixture",
        "macos_sdk_version": "15.0",
        "build_mode": "headless-client",
        "server_only": "false",
    }
    (license_root / "JamulusHeadlessClient-PROVENANCE.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in provenance.items()),
        encoding="utf-8",
    )
    manifest = tmp_path / "JamulusHeadlessClient.sha256"
    manifest.write_text(
        f"{binary_sha}  JamulusHeadlessClient.app/Contents/MacOS/"
        "JamulusHeadlessClient\n",
        encoding="utf-8",
    )
    archive = (
        tmp_path
        / "JamulusHeadlessClient-r3_12_3-macos-arm64-UNAPPROVED-EVIDENCE.zip"
    )
    archive.write_bytes(b"exact CI container bytes")
    return archive, app, manifest


def test_component_sbom_pins_official_assets_and_separates_statuses() -> None:
    sbom = json.loads(SBOM_PATH.read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    components = {component["bom-ref"]: component for component in sbom["components"]}
    expected_scopes = {
        "jamulus:r3_12_2:windows-x64:installer": "required",
        "jamulus:r3_12_2:linux-x64:package": "required",
        "jamulus:r3_12_2:macos-universal:disk-image": "required",
        "jamulus-headless:r3_12_2:macos:embedded": "required",
        "jamulus:r3_12_3:windows-x64:installer": "optional",
        "jamulus:r3_12_3:linux-x64:package": "optional",
        "jamulus-headless:r3_12_3:linux-x64:package": "excluded",
        "jamulus:r3_12_3:macos-universal:disk-image": "optional",
        "jamulus-headless:r3_12_3:macos:unapproved-evidence": "excluded",
    }
    assert {
        bom_ref: component.get("scope")
        for bom_ref, component in components.items()
    } == expected_scopes
    expected = {
        "jamulus:r3_12_2:windows-x64:installer": (
            "4e7cef6a70fe4525f0e7ea1f1c3301d7298047d9456283b7e12035f3ab5ba7b9",
            "embedded-offline-fallback",
        ),
        "jamulus:r3_12_2:linux-x64:package": (
            "029f8858f21a5fb36da5144046473575caa2a26f2c7d8db162953b89d8c8ccc9",
            "embedded-offline-fallback",
        ),
        "jamulus:r3_12_2:macos-universal:disk-image": (
            "adf185aaf78e27d9f603daa6895e7698b4bdffee18fe29ad789cd7c1021d6bd0",
            "embedded-offline-fallback",
        ),
        "jamulus:r3_12_3:windows-x64:installer": (
            "008918b1564b2a46f1a371d7e3df661a0d710689383dab5c61b80be3c4aaf5a1",
            "approved-platform-update",
        ),
        "jamulus:r3_12_3:linux-x64:package": (
            "100af7bcf6edb5729df03ac38bbbdbb4f02014d50b32e0a0e11e55bffba783d3",
            "approved-platform-update",
        ),
        "jamulus:r3_12_3:macos-universal:disk-image": (
            "9502b78c3b13d1e58a6ae417ecb1b5c6ebdf9a3c18e7ec4e23e23230890900cb",
            "approved-managed-update-input",
        ),
    }
    for reference, (digest, status) in expected.items():
        component = components[reference]
        assert component["hashes"] == [{"alg": "SHA-256", "content": digest}]
        properties = {
            item["name"]: item["value"] for item in component["properties"]
        }
        assert properties["webjam:status"] == status
    proposed = components["jamulus-headless:r3_12_3:macos:unapproved-evidence"]
    proposed_properties = {
        item["name"]: item["value"] for item in proposed["properties"]
    }
    windows_properties = {
        item["name"]: item["value"]
        for item in components[
            "jamulus:r3_12_3:windows-x64:installer"
        ]["properties"]
    }
    assert windows_properties["webjam:publisher-trust"] == (
        "upstream-unsigned-exact-hash-user-approval-required"
    )
    assert windows_properties["webjam:installed-runtime-path"] == "Jamulus.exe"
    assert windows_properties["webjam:installed-runtime-size"] == "3111424"
    assert windows_properties["webjam:installed-runtime-sha256"] == (
        "25c3dacaece705a233d9d2a1b7ddb00bb5dfcd10fb3af7ed98f024c56b473295"
    )
    assert windows_properties["webjam:installed-runtime-architecture"] == (
        "pe32+-x86-64"
    )
    linux_properties = {
        item["name"]: item["value"]
        for item in components[
            "jamulus:r3_12_3:linux-x64:package"
        ]["properties"]
    }
    assert linux_properties["webjam:installed-runtime-path"] == "usr/bin/jamulus"
    assert linux_properties["webjam:installed-runtime-size"] == "3430688"
    assert linux_properties["webjam:installed-runtime-sha256"] == (
        "f576bb7139b4f48ae8331cff46641dc5a0350e6afbd11cd93411fbf36834c983"
    )
    assert linux_properties["webjam:installed-runtime-architecture"] == (
        "elf64-x86-64"
    )
    assert proposed_properties["webjam:status"] == "not-approved"
    assert proposed_properties["webjam:catalog-signing"].startswith("forbidden")


def test_3123_license_copy_is_exact_and_not_substituted_for_fallback() -> None:
    fallback = ROOT / "licenses" / "JAMULUS_COPYING.txt"
    current = ROOT / "licenses" / "JAMULUS_COPYING-r3_12_3.txt"
    assert hashlib.sha256(fallback.read_bytes()).hexdigest() == (
        "3e36f90ec56f95f41f172fc71821aa10b9c7c098b74acc93a7d0ed74e9393f94"
    )
    assert hashlib.sha256(current.read_bytes()).hexdigest() == (
        "c6537f438d2d7410a87df17f5fed9ee47993b73b9754bf50629bc0cec9daeb6e"
    )
    assert b"GNU AFFERO GENERAL PUBLIC LICENSE" not in fallback.read_bytes()
    assert b"GNU AFFERO GENERAL PUBLIC LICENSE" in current.read_bytes()


def test_official_platform_inputs_are_verified_without_silent_execution() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    job = workflow.split("  jamulus-platform-update-inputs:\n", 1)[1].split(
        "\n  # ------------------------------------------------------------------", 1
    )[0]
    assert "jamulus_3.12.3_win.exe" in job
    assert "84406464" in job
    assert "Get-AuthenticodeSignature" in job
    assert "SignatureStatus]::NotSigned" in job
    assert "Start-Process" not in job
    assert "& 7z x -y -aou" in job
    assert "jamulus_windows_runtime_contract export" in job
    assert "jamulus_windows_runtime_contract verify" in job
    assert "$manifest.loadable_count -ne 27" in job
    assert "@($manifest.files).Count -ne 27" in job
    assert "Get-PeMachine" in job
    assert "$matches.Count -ne 1" in job
    assert "$machines[0] -ne 0x014c" in job
    assert "0x8664" in job
    assert "Jamulus.exe --version" not in job
    assert "jamulus_3.12.3_mac.dmg" in job
    assert "88923220" in job
    assert 'hdiutil verify "$asset"' in job
    assert "hdiutil attach" not in job
    assert "yes |" not in job
    assert "ActivationMode.PLATFORM_APPROVAL" in job
    build_needs = workflow.split("  build-desktop:\n", 1)[1].split(
        "\n    runs-on:", 1
    )[0]
    assert "jamulus-platform-update-inputs" in build_needs


def test_windows_x64_loadable_contract_is_signed_and_single_sourced() -> None:
    manifest = manifest_payload()
    files = manifest["files"]
    assert isinstance(files, list)
    assert manifest["schema"] == 1
    assert manifest["version"] == APPROVED_COMPONENT_VERSION
    assert manifest["target"] == ComponentTarget.WINDOWS_X64.value
    assert manifest["installer_sha256"] == (
        "008918b1564b2a46f1a371d7e3df661a0d710689383dab5c61b80be3c4aaf5a1"
    )
    assert manifest["loadable_count"] == len(files) == 27
    paths = [item["relative_path"] for item in files]
    assert len({path.casefold() for path in paths}) == 27
    assert paths[0] == "Jamulus.exe"
    assert sum(bool(item["executable"]) for item in files) == 1
    assert all(
        path == "Jamulus.exe" or path.casefold().endswith(".dll")
        for path in paths
    )

    payload = build_payload(
        sequence=1,
        issued_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        validity_days=30,
    )
    windows = [
        component
        for component in payload["components"]
        if component["target"] == ComponentTarget.WINDOWS_X64.value
    ]
    assert {component["role"] for component in windows} == {
        JamulusRole.CLIENT.value,
        JamulusRole.SERVER.value,
    }
    assert len(windows) == 2
    assert all(component["runtime_files"] == files for component in windows)


def test_evidence_injects_container_digest_but_cannot_approve_activation(
    tmp_path: Path,
) -> None:
    archive, app, manifest = _candidate(tmp_path)
    evidence = _EVIDENCE.create_evidence(
        archive=archive,
        app=app,
        manifest=manifest,
        target="macos-arm64",
    )
    assert evidence["archive_sha256"] == _sha(archive.read_bytes())
    assert evidence["archive_size"] == archive.stat().st_size
    assert evidence["activation_approved"] is False
    assert evidence["catalog_injection_required"] is True
    assert evidence["catalog_signing_automatic"] is False
    assert evidence["desktop_release_inventory"] is False
    assert evidence["legal_gate"].startswith("pending-qualified-agpl-13")
    assert any(item["kind"] == "symlink" for item in evidence["runtime_inventory"])


def test_evidence_rejects_unsafe_symlink_and_wrong_architecture(tmp_path: Path) -> None:
    archive, app, manifest = _candidate(tmp_path)
    unsafe = app / "Contents" / "Frameworks" / "escape"
    os.symlink("../../outside", unsafe)
    with pytest.raises(_EVIDENCE.EvidenceError, match="symlink target"):
        _EVIDENCE.create_evidence(
            archive=archive,
            app=app,
            manifest=manifest,
            target="macos-arm64",
        )
    unsafe.unlink()
    with pytest.raises(_EVIDENCE.EvidenceError, match="archive identity"):
        _EVIDENCE.create_evidence(
            archive=archive,
            app=app,
            manifest=manifest,
            target="macos-x64",
        )


def test_schema_and_notices_keep_sla_and_symlink_policy_fail_closed() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]
    assert properties["activation_approved"] == {"const": False}
    assert properties["catalog_signing_automatic"] == {"const": False}
    assert properties["desktop_release_inventory"] == {"const": False}
    normalized_notices = " ".join(NOTICES.split())
    assert "must not mount, extract, or stage" in normalized_notices
    assert "explicitly accepts that agreement" in normalized_notices
    assert "Downloading is never treated as acceptance" in normalized_notices
    assert "explicit Agree action" in normalized_notices
    assert "for that one verified image" in normalized_notices
    assert "expected Qt framework symlinks" in normalized_notices
    assert "deeply verified upstream signature" in normalized_notices


def test_release_catalog_payload_is_exact_expiring_and_excludes_headless() -> None:
    payload = build_payload(
        sequence=17,
        issued_at=datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc),
        validity_days=30,
    )
    assert payload["sequence"] == 17
    assert payload["webjam_version"] == "0.24.0"
    assert payload["issued_at"] == "2026-07-28T20:00:00Z"
    assert payload["expires_at"] == "2026-08-27T20:00:00Z"
    components = payload["components"]
    assert len(components) == EXPECTED_COMPONENT_COUNT == 8
    assert {item["version"] for item in components} == {
        APPROVED_COMPONENT_VERSION
    }
    assert {item["role"] for item in components} == {
        JamulusRole.CLIENT.value,
        JamulusRole.SERVER.value,
    }
    assert all(item["activation_mode"] == "platform-approval" for item in components)
    assert all(item["variant"] == "official" for item in components)
    assert all(
        item["webjam_range"]["maximum"] == "0.24.0" for item in components
    )
    for item in components:
        target = item["target"]
        capabilities = set(item["capabilities"])
        if target in {"macos-arm64", "macos-x64"}:
            assert "webjam-route-profile" not in capabilities
            assert "recording" not in capabilities
        elif item["role"] == JamulusRole.CLIENT.value:
            assert "webjam-route-profile" in capabilities
        else:
            assert "recording" in capabilities


@pytest.mark.parametrize(
    ("sequence", "validity_days"),
    [(0, 30), (1, 0), (1, 31), (True, 1)],
)
def test_release_catalog_payload_rejects_unsafe_sequence_or_lifetime(
    sequence,
    validity_days,
) -> None:
    with pytest.raises(ValueError):
        build_payload(
            sequence=sequence,
            issued_at=datetime.now(timezone.utc),
            validity_days=validity_days,
        )


def test_catalog_release_tool_has_fixed_asset_name_and_no_secret_environment() -> None:
    source = (
        ROOT / "tools" / "create_jamulus_component_catalog.py"
    ).read_text(encoding="utf-8")
    assert CATALOG_FILENAME == "WebJam-Jamulus-components-v1.json"
    assert "private_key_path=arguments.private_key" in source
    assert "os.environ" not in source
    assert "print(private" not in source


@pytest.mark.parametrize(
    "module",
    [
        "tools.create_jamulus_component_catalog",
        "tools.verify_jamulus_component_catalog",
    ],
)
def test_catalog_runbook_module_commands_work_from_repo_root(module: str) -> None:
    runbook = (
        ROOT / "docs" / "JAMULUS_COMPONENT_RELEASE_RUNBOOK.md"
    ).read_text(encoding="utf-8")
    assert f".venv/bin/python -m {module}" in runbook
    assert f".venv/bin/python {module.replace('.', '/')}.py" not in runbook

    completed = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stderr

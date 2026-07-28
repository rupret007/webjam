"""Static release-contract checks for the Windows installer definition."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = ROOT / "packaging" / "windows"
SCRIPT_PATH = WINDOWS_PACKAGING / "WebJam.iss"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
README = (WINDOWS_PACKAGING / "README-WINDOWS.txt").read_text(encoding="utf-8")


def test_installer_wraps_the_complete_pyinstaller_onedir_tree() -> None:
    assert '#define WebJamSourceDir "..\\..\\dist\\WebJam"' in SCRIPT
    assert 'Source: "{#WebJamSourceDir}\\*"' in SCRIPT
    assert "recursesubdirs createallsubdirs" in SCRIPT
    assert 'Filename: "{app}\\{#WebJamExeName}"' in SCRIPT


def test_release_identity_and_output_are_define_driven() -> None:
    for name in (
        "WebJamAppName",
        "WebJamAppVersion",
        "WebJamVersionInfoVersion",
        "WebJamSourceDir",
        "WebJamOutputDir",
        "WebJamOutputBaseFilename",
    ):
        assert f"#ifndef {name}" in SCRIPT
        assert f"{{#{name}}}" in SCRIPT

    assert "AppId={#WebJamAppId}" in SCRIPT
    assert "OutputBaseFilename={#WebJamOutputBaseFilename}" in SCRIPT
    assert "VersionInfoVersion={#WebJamVersionInfoVersion}" in SCRIPT


def test_publisher_build_can_sign_setup_and_embedded_uninstaller() -> None:
    assert "#ifdef WebJamSignTool" in SCRIPT
    assert "SignTool={#WebJamSignTool}" in SCRIPT
    assert "SignedUninstaller=yes" in SCRIPT


def test_install_is_per_user_and_x64_compatible() -> None:
    assert "PrivilegesRequired=lowest" in SCRIPT
    assert "PrivilegesRequiredOverridesAllowed" not in SCRIPT
    assert "DefaultDirName={localappdata}\\Programs\\{#WebJamAppName}" in SCRIPT
    assert "ArchitecturesAllowed=x64compatible" in SCRIPT
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in SCRIPT


def test_shortcuts_are_explicit_and_desktop_is_opt_in() -> None:
    assert 'Name: "{autoprograms}\\{#WebJamAppName}"' in SCRIPT
    assert 'Name: "desktopicon"' in SCRIPT
    assert "Flags: unchecked" in SCRIPT
    assert 'Name: "{autodesktop}\\{#WebJamAppName}"' in SCRIPT
    assert "Tasks: desktopicon" in SCRIPT


def test_installer_never_auto_runs_and_does_not_broadly_delete() -> None:
    section_headers = {
        line.strip().casefold()
        for line in SCRIPT.splitlines()
        if line.strip().startswith("[") and line.strip().endswith("]")
    }
    assert "[run]" not in section_headers
    assert "[uninstalldelete]" not in section_headers
    assert "RestartApplications=no" in SCRIPT


def test_upgrade_replaces_only_the_owned_pyinstaller_tree() -> None:
    assert "[InstallDelete]" in SCRIPT
    install_delete = SCRIPT.split("[InstallDelete]", 1)[1].split("[Icons]", 1)[0]
    entries = [line for line in install_delete.splitlines() if line.startswith("Type:")]
    assert entries
    assert all("Check: IsVerifiedExistingWebJamInstall" in line for line in entries)
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in install_delete
    assert 'Type: files; Name: "{app}\\{#WebJamExeName}"' in install_delete
    assert "{app}\\*" not in SCRIPT
    assert "function IsVerifiedExistingWebJamInstall(): Boolean;" in SCRIPT
    assert "RegQueryStringValue(" in SCRIPT
    assert "'InstallLocation'" in SCRIPT
    assert "CompareText(AddBackslash(ExistingLocation)" in SCRIPT
    assert "FileExists(ExistingExecutable)" in SCRIPT


def test_user_facing_legal_and_managed_pc_context_are_included() -> None:
    assert "LicenseFile=..\\..\\LICENSE" in SCRIPT
    assert 'Source: "..\\..\\THIRD_PARTY_NOTICES.md"' in SCRIPT
    assert 'Source: "..\\..\\THIRD_PARTY_NOTICES_RUNTIME.md"' in SCRIPT
    assert 'DestName: "WebJam-runtime-sbom.cdx.json"' in SCRIPT
    assert 'Source: "README-WINDOWS.txt"' in SCRIPT
    assert "does not require administrator access" in README
    assert "may require IT approval" in README
    assert "preserves your WebJam settings" in README

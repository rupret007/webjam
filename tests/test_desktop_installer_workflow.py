"""Static contracts for the native desktop installer release path."""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
DMG_SCRIPT_PATH = ROOT / "packaging" / "macos" / "create-dmg.sh"
DMG_SCRIPT = DMG_SCRIPT_PATH.read_text(encoding="utf-8")


def test_macos_dmg_builder_is_executable_and_preserves_the_app_bundle() -> None:
    assert os.access(DMG_SCRIPT_PATH, os.X_OK)
    assert 'ditto "$source_app" "$stage_root/WebJam.app"' in DMG_SCRIPT
    assert 'ln -s /Applications "$stage_root/Applications"' in DMG_SCRIPT
    assert "-format UDZO" in DMG_SCRIPT
    assert 'hdiutil verify "$output_dmg"' in DMG_SCRIPT


def test_macos_dmg_builder_refuses_ambiguous_or_destructive_outputs() -> None:
    assert '"$(basename "$source_app")" == "WebJam.app"' in DMG_SCRIPT
    assert '"$output_dmg" == *.dmg' in DMG_SCRIPT
    assert '[[ ! -e "$output_dmg" ]]' in DMG_SCRIPT
    assert "trap cleanup EXIT" in DMG_SCRIPT
    assert 'chmod 755 "$stage_root"' in DMG_SCRIPT
    assert 'if [[ "$image_complete" != 1 ]]' in DMG_SCRIPT
    assert 'rm -f -- "$output_dmg"' in DMG_SCRIPT


def test_macos_ci_verifies_the_mounted_deliverable_not_only_the_source() -> None:
    assert "Build and verify macOS disk image" in WORKFLOW
    assert 'dmg="out/WebJam-v${version}-${{ matrix.target }}.dmg"' in WORKFLOW
    assert 'hdiutil attach "$dmg" -readonly -nobrowse' in WORKFLOW
    assert 'test -L "$mount_dir/Applications"' in WORKFLOW
    assert 'stat -f \'%Lp\' "$mount_dir"' in WORKFLOW
    assert 'ditto "$mount_dir/WebJam.app" "$copy_dir/WebJam.app"' in WORKFLOW
    assert 'codesign --verify --deep --strict "$copied_app"' in WORKFLOW
    assert '--build-id "$build_id"' in WORKFLOW
    assert 'webjam-build-id.txt")" = "$build_id"' in WORKFLOW
    assert '-verify_arch "$expected_machine"' in WORKFLOW
    assert "'Print :CFBundleVersion'" in WORKFLOW
    assert '"$copied_app/Contents/MacOS/WebJam"' in WORKFLOW


def test_windows_ci_builds_and_exercises_the_direct_setup_executable() -> None:
    assert "Build, install, launch, and uninstall Windows setup" in WORKFLOW
    assert "- os: windows-2025" in WORKFLOW
    assert "$isccFileVersion" in WORKFLOW
    assert '$outputBase = "WebJam-v$version-windows-x64-setup"' in WORKFLOW
    assert '"packaging\\windows\\WebJam.iss"' in WORKFLOW
    assert "$setupVersionInfo.ProductMajorPart" in WORKFLOW
    assert "$setupVersionInfo.ProductPrivatePart" in WORKFLOW
    assert '"/TASKS=desktopicon"' in WORKFLOW
    assert '"/DIR=`"$installDir`""' in WORKFLOW
    assert "obsolete-owned-canary.dll" in WORKFLOW
    assert "Get-PeMachine" in WORKFLOW
    assert "0x8664" in WORKFLOW
    assert "$installedBuildId -ne $buildId" in WORKFLOW
    assert "Installed transport manifest mismatch" in WORKFLOW
    assert 'Get-ChildItem $installDir -Filter "unins*.exe"' in WORKFLOW
    assert "Test-Path $uninstallRegistry" in WORKFLOW
    assert "Uninstall left owned payload paths" in WORKFLOW
    assert "Uninstall removed an unowned file" in WORKFLOW


def test_signed_windows_release_covers_setup_payload_and_uninstaller() -> None:
    assert '"/Swebjamsign=$signCommand"' in WORKFLOW
    assert '"/DWebJamSignTool=webjamsign"' in WORKFLOW
    assert "Setup Authenticode verification failed" in WORKFLOW
    assert "Installed payload lost its Authenticode signature" in WORKFLOW
    assert "Embedded WebJam uninstaller is not signed" in WORKFLOW


def test_all_direct_and_portable_assets_are_uploaded_for_the_release_job() -> None:
    assert "Verify expected release deliverables" in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}-setup.exe"' in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}.dmg"' in WORKFLOW
    assert "path: out/WebJam*${{ matrix.target }}*" in WORKFLOW
    assert "files: release-assets/*" in WORKFLOW

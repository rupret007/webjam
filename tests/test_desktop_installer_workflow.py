"""Static contracts for the native desktop installer release path."""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
DMG_SCRIPT_PATH = ROOT / "packaging" / "macos" / "create-dmg.sh"
DMG_SCRIPT = DMG_SCRIPT_PATH.read_text(encoding="utf-8")
POCKET_STAGE_KIT_PATH = ROOT / "packaging" / "ios" / "prepare-pocket-stage-kit.sh"
POCKET_STAGE_KIT = POCKET_STAGE_KIT_PATH.read_text(encoding="utf-8")
POCKET_STAGE_OPEN_PATH = (
    ROOT / "packaging" / "ios" / "Open Pocket Stage in Xcode.command"
)
POCKET_STAGE_OPEN = POCKET_STAGE_OPEN_PATH.read_text(encoding="utf-8")
WINDOWS_CERTIFICATE_PATH = ROOT / "packaging" / "windows" / "release-certificate.ps1"
WINDOWS_CERTIFICATE = WINDOWS_CERTIFICATE_PATH.read_text(encoding="utf-8")
RELEASE_LOCK_ROOT = ROOT / "requirements-lock"
LINUX_README = (ROOT / "packaging" / "linux" / "README-LINUX.txt").read_text(
    encoding="utf-8"
)
PROJECT_README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
VERSION_SOURCE = (ROOT / "webjam_qt" / "__init__.py").read_text(encoding="utf-8")
THIRD_PARTY_NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
TEST_PROCEDURE = (ROOT / "TEST_PROCEDURE.md").read_text(encoding="utf-8")
DESKTOP_RELEASE_RUNBOOK = (
    ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md"
).read_text(encoding="utf-8")
MACOS_README = (ROOT / "packaging" / "macos" / "READ ME FIRST.txt").read_text(
    encoding="utf-8"
)


def _workflow_job(name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match is not None
    return match.group(0)


def _workflow_step(name: str) -> str:
    marker = f"      - name: {name}\n"
    start = WORKFLOW.index(marker)
    end = WORKFLOW.find("\n      - name:", start + len(marker))
    return WORKFLOW[start : end if end >= 0 else len(WORKFLOW)]


def test_current_candidate_identity_cannot_be_confused_with_latest_old_release() -> None:
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', VERSION_SOURCE, re.M)
    assert match is not None
    version = match.group(1)
    assert version == "0.22.2"
    assert PROJECT_README.startswith(f"# WebJam v{version} unsigned private test candidate")
    assert f"## [{version}]" in CHANGELOG
    assert "v0.20.0 history must not be moved" in PROJECT_README
    assert "standalone Reference Studio" in PROJECT_README
    assert re.search(r"Pocket Stage iPhone\s+Setup", PROJECT_README)


def test_macos_dmg_builder_is_executable_and_preserves_the_app_bundle() -> None:
    assert os.access(DMG_SCRIPT_PATH, os.X_OK)
    assert 'ditto "$source_app" "$stage_root/WebJam.app"' in DMG_SCRIPT
    assert 'ln -s /Applications "$stage_root/Applications"' in DMG_SCRIPT
    assert '"Install WebJam.command"' in DMG_SCRIPT
    assert '"Install WebJam - Remove Quarantine.command"' in DMG_SCRIPT
    assert '"WebJam Candidate Info.txt"' in DMG_SCRIPT
    assert '"Pocket Stage iPhone Setup"' in DMG_SCRIPT
    assert "Open Pocket Stage in Xcode.command" in DMG_SCRIPT
    assert "Pocket Stage iPhone setup kit contains a symbolic link" in DMG_SCRIPT
    assert "-format UDZO" in DMG_SCRIPT
    assert 'hdiutil verify "$output_dmg"' in DMG_SCRIPT


def test_macos_readme_uses_the_working_app_bundle_approval_path() -> None:
    assert "Drag WebJam.app onto the Applications shortcut" in MACOS_README
    assert "open WebJam from Applications" in MACOS_README
    assert "Open Anyway for WebJam" in MACOS_README
    assert "Recent macOS versions can block downloaded" in MACOS_README
    assert "Control-click the helper" not in MACOS_README
    assert "/bin/bash " in MACOS_README
    assert "Webex on the main session rail" in MACOS_README
    assert "only Join / Open hands off the saved link" in MACOS_README
    assert "Webex is not bundled with WebJam" in MACOS_README
    assert "~/Library/Application Support/WebJam" in MACOS_README
    assert "must not ask WebJam for permission to access data from other apps" in (
        MACOS_README
    )
    assert "separately ask for microphone access" in MACOS_README
    assert re.search(
        r"do not add\s+WebJam to Full Disk\s+Access",
        MACOS_README,
    )
    assert "Choose Allow so WebJam" not in MACOS_README
    assert "macOS may ask again after you quit" not in MACOS_README


def test_current_macos_docs_require_permissionless_jamulus_profiles() -> None:
    assert "does not declare `NSAppDataUsageDescription`" in PROJECT_README
    assert "no longer declares `NSAppDataUsageDescription`" in CHANGELOG
    assert "`NSAppDataUsageDescription` must be absent everywhere" in TEST_PROCEDURE
    assert "do not click **Allow**" in TEST_PROCEDURE
    assert "Mac bundle must omit `NSAppDataUsageDescription`" in (
        DESKTOP_RELEASE_RUNBOOK
    )
    assert "Full Disk Access or Other Application Data" in THIRD_PARTY_NOTICES


def test_macos_dmg_builder_refuses_ambiguous_or_destructive_outputs() -> None:
    assert '"$(basename "$source_app")" == "WebJam.app"' in DMG_SCRIPT
    assert '"$output_dmg" == *.dmg' in DMG_SCRIPT
    assert '[[ ! -e "$output_dmg" ]]' in DMG_SCRIPT
    assert "trap cleanup EXIT" in DMG_SCRIPT
    assert 'chmod 755 "$stage_root"' in DMG_SCRIPT
    assert 'if [[ "$image_complete" != 1 ]]' in DMG_SCRIPT
    assert 'rm -f -- "$output_dmg"' in DMG_SCRIPT


def test_macos_ci_verifies_the_mounted_deliverable_not_only_the_source() -> None:
    build_step = _workflow_step("Build desktop artifact")
    fresh_zip_step = build_step.split(
        'ditto -x -k "out/WebJam-${{ matrix.target }}.zip" "$fresh_dir"',
        1,
    )[1]
    mounted_step = _workflow_step("Verify mounted macOS disk image")
    assert "Build macOS disk image" in WORKFLOW
    assert "Build, sign, notarize, and staple protected macOS disk image" in WORKFLOW
    assert "Verify mounted macOS disk image" in WORKFLOW
    assert 'dmg="out/WebJam-v${version}-${{ matrix.target }}.dmg"' in WORKFLOW
    assert 'hdiutil attach "$dmg" -readonly -nobrowse' in WORKFLOW
    assert 'test -L "$mount_dir/Applications"' in WORKFLOW
    assert 'test -x "$mount_dir/Install WebJam.command"' in WORKFLOW
    assert 'test -x "$mount_dir/Install WebJam - Remove Quarantine.command"' in WORKFLOW
    assert 'test -f "$mount_dir/WebJam Candidate Info.txt"' in WORKFLOW
    assert (
        'test -x "$mount_dir/Pocket Stage iPhone Setup/'
        'Open Pocket Stage in Xcode.command"' in WORKFLOW
    )
    assert (
        'test -f "$mount_dir/Pocket Stage iPhone Setup/'
        'WebJamPocketStage.xcodeproj/project.pbxproj"' in WORKFLOW
    )
    assert "stat -f '%Lp' \"$mount_dir\"" in WORKFLOW
    assert 'ditto "$mount_dir/WebJam.app" "$copy_dir/WebJam.app"' in WORKFLOW
    assert 'codesign --verify --deep --strict "$copied_app"' in mounted_step
    assert "'Print :NSAppDataUsageDescription'" in mounted_step
    assert "! /usr/libexec/PlistBuddy" in mounted_step
    assert '"$copied_app"' in mounted_step
    assert (
        '"$copied_app/Contents/Resources/JamulusHeadlessClient.app"'
        in mounted_step
    )
    assert 'codesign --verify --deep --strict "$fresh_dir/WebJam.app"' in (
        fresh_zip_step
    )
    assert "'Print :NSAppDataUsageDescription'" in fresh_zip_step
    assert "! /usr/libexec/PlistBuddy" in fresh_zip_step
    assert '"$fresh_dir/WebJam.app"' in fresh_zip_step
    assert (
        '"$fresh_dir/WebJam.app/Contents/Resources/'
        'JamulusHeadlessClient.app"' in fresh_zip_step
    )
    assert "WebJam accesses Jamulus app data" not in WORKFLOW
    assert '--build-id "$build_id"' in WORKFLOW
    assert 'webjam-build-id.txt")" = "$build_id"' in WORKFLOW
    assert '-verify_arch "$expected_machine"' in WORKFLOW
    assert "'Print :CFBundleVersion'" in WORKFLOW
    assert '"$copied_app/Contents/MacOS/WebJam"' in WORKFLOW
    assert 'xattr -w com.apple.quarantine "$quarantine" "$fresh_dir/WebJam.app"' in WORKFLOW
    assert '"$fresh_dir/Install WebJam.command"' in WORKFLOW
    assert '"$fresh_dir/Install WebJam - Remove Quarantine.command"' in WORKFLOW
    assert 'xattr -p com.apple.quarantine "$guided_dest"' in WORKFLOW
    assert '! xattr -lr "$advanced_dest" | grep -Fq com.apple.quarantine' in WORKFLOW
    assert 'xattr -p com.apple.quarantine "$unrelated"' in WORKFLOW


def test_pocket_stage_setup_kit_is_generated_compiled_and_carried_by_mac_packages() -> None:
    assert os.access(POCKET_STAGE_KIT_PATH, os.X_OK)
    assert os.access(POCKET_STAGE_OPEN_PATH, os.X_OK)
    assert "Build full Pocket Stage app for iOS Simulator" in WORKFLOW
    assert "Prepare self-contained Pocket Stage owner-device setup kit" in WORKFLOW
    assert 'packaging/ios/prepare-pocket-stage-kit.sh' in WORKFLOW
    assert 'name: webjam-pocket-stage-ios-setup-${{ github.sha }}' in WORKFLOW
    assert "Download Pocket Stage owner-device setup kit" in WORKFLOW
    assert (
        'ditto "$RUNNER_TEMP/pocket-stage-kit/Pocket Stage iPhone Setup"' in WORKFLOW
    )
    assert '"Pocket Stage iPhone Setup"' in DMG_SCRIPT
    assert "desktop_version=$expected_version" in WORKFLOW
    assert "desktop_build_id=$build_id" in WORKFLOW


def test_pocket_stage_owner_device_kit_has_a_bounded_non_privileged_open_path() -> None:
    assert "WebJamPocketStage.xcodeproj/project.pbxproj" in POCKET_STAGE_KIT
    assert '"PocketStage/Info.plist"' in POCKET_STAGE_KIT
    assert '"Sources/PocketStageProtocol/PocketStageProtocol.swift"' in POCKET_STAGE_KIT
    assert "Pocket Stage Build Info.txt" in POCKET_STAGE_KIT
    assert "Refusing to replace an existing Pocket Stage setup kit" in POCKET_STAGE_KIT
    assert "contains a symbolic link" in POCKET_STAGE_KIT
    assert "/usr/bin/xcodebuild -version" in POCKET_STAGE_OPEN
    assert 'xcode_app="/Applications/Xcode.app"' in POCKET_STAGE_OPEN
    assert 'DEVELOPER_DIR="$xcode_app/Contents/Developer"' in POCKET_STAGE_OPEN
    assert '/usr/bin/open -a "$xcode_app" "$project"' in POCKET_STAGE_OPEN
    assert "free Personal Team" in POCKET_STAGE_OPEN
    combined = POCKET_STAGE_KIT + POCKET_STAGE_OPEN
    assert "sudo" not in combined
    assert "spctl --master-disable" not in combined
    assert "xattr" not in combined
    assert "xcodegen" not in POCKET_STAGE_OPEN.lower()


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
    assert '"WebJam Windows Fresh With Spaces"' in WORKFLOW
    assert '"WebJam Setup Source With Spaces"' in WORKFLOW
    assert '"WebJam Installed With Spaces"' in WORKFLOW


def test_signed_windows_release_covers_setup_payload_and_uninstaller() -> None:
    assert '"/Swebjamsign=$signCommand"' in WORKFLOW
    assert '"/DWebJamSignTool=webjamsign"' in WORKFLOW
    assert "Assert-WebJamSignature $setup" in WORKFLOW
    assert "foreach ($binary in @($app, $fabric))" in WORKFLOW
    assert "Assert-WebJamSignature $uninstaller.FullName" in WORKFLOW
    assert "TimeStamperCertificate" in WORKFLOW
    assert "WEBJAM_WINDOWS_CODESIGN_SUBJECT" in WORKFLOW


def test_windows_signing_secrets_are_isolated_to_a_protected_job() -> None:
    build = _workflow_job("build-desktop")
    trust = _workflow_job("windows-release-trust")
    assert "environment:\n      name: windows-release" in trust
    assert "deployment: false" in trust
    assert "needs: build-desktop" in trust
    condition = " ".join(
        trust.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0].split()
    )
    assert condition == (
        "github.event_name == 'workflow_dispatch' && "
        "inputs.windows_signing_rehearsal"
    )
    assert "windows_signing_rehearsal:" in WORKFLOW
    for name in ("WINDOWS_CODESIGN_PFX", "WINDOWS_CODESIGN_PASSWORD"):
        assert WORKFLOW.count(f"secrets.{name}") == 2
        assert f"secrets.{name}" not in build
    assert WORKFLOW.count("vars.WINDOWS_CODESIGN_SUBJECT") == 2
    assert "vars.WINDOWS_CODESIGN_SUBJECT" not in build
    assert "Verify Windows release helper fails closed without credentials" in build
    assert "Management.Automation.Language.Parser" in build


def test_windows_release_certificate_validation_is_fail_closed() -> None:
    assert WINDOWS_CERTIFICATE_PATH.is_file()
    assert 'ValidateSet("Validate", "Prepare", "Cleanup")' in WINDOWS_CERTIFICATE
    for name in (
        "WINDOWS_CODESIGN_PFX",
        "WINDOWS_CODESIGN_PASSWORD",
        "WINDOWS_CODESIGN_SUBJECT",
    ):
        assert f'Get-RequiredEnvironmentValue "{name}"' in WINDOWS_CERTIFICATE
    assert "Expected exactly one certificate with a private key" in WINDOWS_CERTIFICATE
    assert "$certificate.Subject -cne $expectedSubject" in WINDOWS_CERTIFICATE
    assert '"1.3.6.1.5.5.7.3.3"' in WINDOWS_CERTIFICATE
    assert "forbidden SHA-1 certificate signature" in WINDOWS_CERTIFICATE
    assert "Signing certificate must use RSA" in WINDOWS_CERTIFICATE
    assert "RSA signing key must be between 2048 and 4096 bits" in WINDOWS_CERTIFICATE
    assert "ECDsaCertificateExtensions" not in WINDOWS_CERTIFICATE
    assert "Cert:\\CurrentUser\\My\\$thumbprint" in WINDOWS_CERTIFICATE
    assert "Import-PfxCertificate" in WINDOWS_CERTIFICATE
    assert "-Exportable" not in WINDOWS_CERTIFICATE
    assert "Remove-PreparedCertificate" in WINDOWS_CERTIFICATE
    assert (
        "certificate cleanup state contains an invalid thumbprint"
        in WINDOWS_CERTIFICATE
    )
    assert "certificate $thumbprint remains in CurrentUser\\\\My" in WINDOWS_CERTIFICATE
    assert "-DeleteKey -Force -ErrorAction Stop" in WINDOWS_CERTIFICATE
    assert "temporary signing file remains" in WINDOWS_CERTIFICATE
    assert "Windows release certificate cleanup failed" in WINDOWS_CERTIFICATE
    assert 'Write-GitHubEnvironment "WEBJAM_WINDOWS_CODESIGN_THUMBPRINT"' in (
        WINDOWS_CERTIFICATE
    )
    assert "WINDOWS_CODESIGN_PASSWORD=" not in WINDOWS_CERTIFICATE


def test_windows_runner_exercises_certificate_and_private_key_cleanup() -> None:
    build = _workflow_job("build-desktop")
    assert "Exercise disposable Windows certificate lifecycle" in build
    assert "CertificateRequest" in build
    assert "[Security.Cryptography.RSA]::Create(2048)" in build
    assert "GetRSAPrivateKey" in build
    assert "Microsoft\\Crypto\\Keys" in build
    assert "Microsoft\\Crypto\\RSA" in build
    assert "Cleanup left the imported private-key file" in build
    assert "webjam-release-codesign.pfx" in build
    assert "webjam-release-certificate-thumbprints.txt" in build


def test_native_release_build_uses_exact_hashed_binary_dependency_locks() -> None:
    build = _workflow_job("build-desktop")
    expected_python = {
        "windows-x64": "3.11.9",
        "macos-x64": "3.11.9",
        "macos-arm64": "3.11.9",
        "linux-x64": "3.11.15",
    }
    expected_setuptools = {
        "windows-x64": "83.0.0",
        "macos-x64": "81.0.0",
        "macos-arm64": "81.0.0",
        "linux-x64": "83.0.0",
    }
    assert 'python-version: "${{ matrix.python }}"' in build
    assert "--require-hashes --only-binary=:all:" in build
    assert "--force-reinstall --require-hashes" in build
    assert "requirements-lock/${{ matrix.target }}.txt" in build
    assert "python -m pip check" in build
    assert "python -VV" in build
    assert "python -m pip freeze --all" in build
    assert "Verify macOS PyInstaller pkg_resources compatibility" in build
    assert 'version("setuptools") == "81.0.0"' in build
    assert 'hasattr(pkg_resources, "NullProvider")' in build
    assert "pip install --upgrade pip" not in build
    assert "pip install -r requirements.txt" not in build
    for target, python_version in expected_python.items():
        assert f'target: {target}\n            python: "{python_version}"' in build
        lock = RELEASE_LOCK_ROOT / f"{target}.txt"
        contents = lock.read_text(encoding="utf-8")
        assert lock.is_file()
        assert "--hash=sha256:" in contents
        assert "pyinstaller-hooks-contrib==2026.6" in contents
        assert f"setuptools=={expected_setuptools[target]}" in contents
        assert not re.search(r"(?m)^[A-Za-z0-9_.-]+\s*[~<>!]", contents)
    bootstrap = (RELEASE_LOCK_ROOT / "bootstrap.txt").read_text(encoding="utf-8")
    assert "pip==26.1.2" in bootstrap
    assert "--hash=sha256:" in bootstrap


def test_native_release_locks_are_audited_with_a_narrow_macos_exception() -> None:
    test_job = _workflow_job("test")
    assert "Audit native release dependency locks" in test_job
    assert "for target in windows-x64 linux-x64" in test_job
    assert "for target in macos-x64 macos-arm64" in test_job
    assert "--disable-pip --no-deps" in test_job
    assert "--ignore-vuln PYSEC-2026-3447" in test_job
    assert "GHSA-h35f-9h28-mq5c affects sdist creation" in test_job


def test_source_job_is_bounded_and_runs_required_environment_gates() -> None:
    test_job = _workflow_job("test")

    assert "timeout-minutes: 30" in test_job
    assert "python -m pip check" in test_job
    assert "python -m compileall -q core webjam_qt ui services api tests" in test_job
    assert "git diff --check HEAD" in test_job


def test_protected_windows_release_is_verified_after_key_cleanup() -> None:
    trust = _workflow_job("windows-release-trust")
    source = trust.index(
        "      - name: Verify downloaded Windows source before credential access"
    )
    tools = trust.index(
        "      - name: Verify protected Windows packaging tools before credential access"
    )
    validate = trust.index(
        "      - name: Validate protected Windows release certificate"
    )
    prepare = trust.index("      - name: Prepare protected Windows release certificate")
    sign = trust.index("      - name: Sign Windows payload and build signed Setup")
    cleanup = trust.index("      - name: Remove protected Windows release certificate")
    exercise = trust.index(
        "      - name: Install, launch, inventory, and uninstall signed Windows Setup"
    )
    upload = trust.index("      - name: Upload protected Windows release artifact")
    assert source < tools < validate < prepare < sign < cleanup < exercise < upload
    assert "if: always()" in trust[cleanup:exercise]
    assert 'Status -ne "NotSigned"' in trust[source:validate]
    assert "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip" in trust[source:validate]
    assert "UNSIGNED-TEST-ONLY-setup.exe" in trust[source:validate]
    assert "Start-Process" not in trust[source:validate]
    assert "WEBJAM_SMOKE_LAUNCH_ONLY" not in trust[source:validate]
    assert "python -c" not in trust[source:validate]
    assert 'GITHUB_REF_NAME -cne "v$version"' in trust[source:tools]
    assert "7-Zip is not the reviewed version 26.02" in trust[tools:validate]
    assert "Inno Setup compiler is not the reviewed version 6.7.1" in trust
    assert "/tr https://timestamp.digicert.com /td sha256 /fd sha256" in trust
    assert "TimeStamperCertificate" in trust
    assert "publisher subject mismatch" in trust
    assert "certificate thumbprint mismatch" in trust
    assert "Assert-WebJamSignature $uninstaller.FullName" in trust
    assert "pe-signature-inventory.csv" in trust
    assert "PE signature inventory is unexpectedly incomplete" in trust
    assert "invalid or indeterminate signature states" in trust
    assert "$ownedAfterUninstall" in trust
    assert "Signed uninstall left owned payload paths" in trust
    assert "post-cleanup-release-verification" in trust
    assert "name: webjam-release-windows-x64" in trust
    assert "out/WebJam-v*-windows-x64-setup.exe" in trust
    assert "name: webjam-windows-signing-evidence" in trust
    release = _workflow_job("release")
    assert "name: webjam-windows-x64" in release
    assert "name: webjam-release-windows-x64" not in release


def test_all_direct_and_portable_assets_are_uploaded_for_the_release_job() -> None:
    assert "Verify expected release deliverables" in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}-setup.exe"' in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}.dmg"' in WORKFLOW
    assert "Upload Windows candidate artifact" in WORKFLOW
    assert "name: webjam-windows-x64" in WORKFLOW
    assert (
        "out/WebJam-v*-windows-x64-UNSIGNED-TEST-ONLY-setup.exe" in WORKFLOW
    )
    assert "out/WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip" in WORKFLOW
    assert "out/WebJam-v*-windows-x64-SHA256SUMS.txt" in WORKFLOW
    assert "retention-days: 90" in WORKFLOW
    assert "Upload non-Windows build artifact" in WORKFLOW
    assert "path: out/WebJam*${{ matrix.target }}*" in WORKFLOW
    assert "Mark unsigned platform source artifacts as test-only" in WORKFLOW
    assert "UNSIGNED-TEST-ONLY" in WORKFLOW
    assert "ADHOC-TEST-ONLY" in WORKFLOW
    assert 'install -m 755 "packaging/macos/Install WebJam.command"' in WORKFLOW
    assert '"$candidate_extras/Pocket Stage iPhone Setup"' in WORKFLOW
    assert 'ditto -c -k --sequesterRsrc "$candidate_root"' in WORKFLOW


def test_windows_actions_artifact_has_exact_verified_container_manifest() -> None:
    build = _workflow_job("build-desktop")
    assert "Generate and verify Windows candidate checksum manifest" in build
    assert (
        'checksum_file="WebJam-v${version}-windows-x64-SHA256SUMS.txt"' in build
    )
    assert (
        '"WebJam-v${version}-windows-x64-UNSIGNED-TEST-ONLY-setup.exe"' in build
    )
    assert '"WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip"' in build
    assert 'test "${#candidate_assets[@]}" -eq "${#assets[@]}"' in build
    assert 'sha256sum --binary -- "${assets[@]}" > "$checksum_file"' in build
    assert "1s/^[^*]*\\*//p" in build
    assert "2s/^[^*]*\\*//p" in build
    assert 'sha256sum --check --strict "$checksum_file"' in build
    assert 'test "${#uploaded_files[@]}" -eq 3' in build
    upload = build.split("      - name: Upload Windows candidate artifact\n", 1)[
        1
    ].split("\n      - name:", 1)[0]
    assert set(re.findall(r"^\s+(out/WebJam[^\n]+)$", upload, re.MULTILINE)) == {
        "out/WebJam-v*-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "out/WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "out/WebJam-v*-windows-x64-SHA256SUMS.txt",
    }
    assert "name: webjam-windows-x64" in upload
    assert "retention-days: 90" in upload

    trust = _workflow_job("windows-release-trust")
    assert "Windows source checksum manifest must contain exactly two entries" in trust
    assert r"(?<hash>[0-9a-f]{64}) \*(?<name>[^/\\]+)" in trust
    assert "Unexpected or duplicate Windows source checksum entry" in trust
    assert "Windows source checksum mismatch" in trust
    assert "$sourceFiles.Count -ne 3" in trust


def test_signing_rehearsals_do_not_implicitly_start_the_one_hour_soak() -> None:
    certification = _workflow_job("certify-jamulus-one-hour")
    assert "run_one_hour_certification:" in WORKFLOW
    assert "inputs.run_one_hour_certification" in certification
    assert "inputs.macos_signing_rehearsal" not in certification
    assert "inputs.windows_signing_rehearsal" not in certification


def test_every_external_action_is_pinned_to_an_immutable_commit() -> None:
    external_uses = re.findall(r"(?m)^\s+uses: ([^\s#]+)", WORKFLOW)
    assert external_uses
    for reference in external_uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), reference
    assert set(external_uses) == {
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "softprops/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228",
    }


def test_release_generates_and_verifies_checksum_manifest_for_exact_assets() -> None:
    release_job = WORKFLOW.split("  release:\n", 1)[1]
    assert "Generate and verify release checksum manifest" in release_job
    expected_assets = {
        "WebJam-linux-x64.zip",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "WebJam-v${version}-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-v${version}-macos-x64-ADHOC-TEST-ONLY.dmg",
        "WebJam-v${version}-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
    }
    asset_block = release_job.split("          assets=(\n", 1)[1].split(
        "          )\n", 1
    )[0]
    assert set(re.findall(r'^\s+"(WebJam-[^"]+)"$', asset_block, re.MULTILINE)) == (
        expected_assets
    )
    assert 'checksum_file="WebJam-${GITHUB_REF_NAME}-SHA256SUMS.txt"' in release_job
    assert (
        'windows_checksum="WebJam-v${version}-windows-x64-SHA256SUMS.txt"'
        in release_job
    )
    assert 'sha256sum --check --strict "$windows_checksum"' in release_job
    assert "1s/^[^*]*\\*//p" in release_job
    assert "2s/^[^*]*\\*//p" in release_job
    assert 'rm -- "$windows_checksum"' in release_job
    assert 'sha256sum -- "${assets[@]}" > "$checksum_file"' in release_job
    assert 'test "$(wc -l < "$checksum_file")" -eq "${#assets[@]}"' in release_job
    assert 'sha256sum --check --strict "$checksum_file"' in release_job
    assert "shopt -s nullglob dotglob" in release_job
    assert "downloaded=(*)" in release_job
    assert 'test "${#downloaded[@]}" -eq "${#assets[@]}"' in release_job
    expected_uploads = {
        "release-assets/WebJam-linux-x64.zip",
        "release-assets/WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "release-assets/WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "release-assets/WebJam-${{ github.ref_name }}-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-macos-x64-ADHOC-TEST-ONLY.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "release-assets/WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "release-assets/WebJam-${{ github.ref_name }}-SHA256SUMS.txt",
    }
    upload_block = release_job.split("          files: |\n", 1)[1]
    assert "          fail_on_unmatched_files: true\n" in release_job
    assert (
        set(
            re.findall(
                r"^\s+(release-assets/WebJam-[^\n]+)$", upload_block, re.MULTILINE
            )
        )
        == expected_uploads
    )


def test_release_refuses_every_existing_draft_or_published_match() -> None:
    release_job = WORKFLOW.split("  release:\n", 1)[1]
    probe = release_job.split(
        "      - name: Refuse any existing draft or published release\n", 1
    )[1].split("\n      - name:", 1)[0]
    assert "gh api" in probe
    assert "--paginate" in probe
    assert "--slurp" in probe
    assert "repos/$GITHUB_REPOSITORY/releases?per_page=100" in probe
    assert "[ .[][] | select(.tag_name == $tag) ] | length == 0" in probe
    assert "draft or published release already uses" in probe
    assert "releases/tags/$GITHUB_REF_NAME" not in probe
    assert "|| true" not in probe


def test_linux_release_claims_only_the_certified_ubuntu_target() -> None:
    claims = "\n".join((LINUX_README, PROJECT_README, THIRD_PARTY_NOTICES))
    assert "certified only for 64-bit Ubuntu 22.04" in LINUX_README
    assert "Ubuntu 22.04 x64 ZIP" in PROJECT_README
    assert "certified only for Ubuntu 22.04 x64" in THIRD_PARTY_NOTICES
    assert "22.04 or newer" not in claims

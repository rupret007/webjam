"""Static contracts for the native desktop installer release path."""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
DMG_SCRIPT_PATH = ROOT / "packaging" / "macos" / "create-dmg.sh"
DMG_SCRIPT = DMG_SCRIPT_PATH.read_text(encoding="utf-8")
WINDOWS_CERTIFICATE_PATH = ROOT / "packaging" / "windows" / "release-certificate.ps1"
WINDOWS_CERTIFICATE = WINDOWS_CERTIFICATE_PATH.read_text(encoding="utf-8")
RELEASE_LOCK_ROOT = ROOT / "requirements-lock"
LINUX_README = (ROOT / "packaging" / "linux" / "README-LINUX.txt").read_text(
    encoding="utf-8"
)
PROJECT_README = (ROOT / "README.md").read_text(encoding="utf-8")
THIRD_PARTY_NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")


def _workflow_job(name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match is not None
    return match.group(0)


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
    assert "Build macOS disk image" in WORKFLOW
    assert "Build, sign, notarize, and staple protected macOS disk image" in WORKFLOW
    assert "Verify mounted macOS disk image" in WORKFLOW
    assert 'dmg="out/WebJam-v${version}-${{ matrix.target }}.dmg"' in WORKFLOW
    assert 'hdiutil attach "$dmg" -readonly -nobrowse' in WORKFLOW
    assert 'test -L "$mount_dir/Applications"' in WORKFLOW
    assert "stat -f '%Lp' \"$mount_dir\"" in WORKFLOW
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
        "startsWith(github.ref, 'refs/tags/v') || "
        "(github.event_name == 'workflow_dispatch' && "
        "inputs.windows_signing_rehearsal)"
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
    assert "name: webjam-release-windows-x64" in release


def test_all_direct_and_portable_assets_are_uploaded_for_the_release_job() -> None:
    assert "Verify expected release deliverables" in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}-setup.exe"' in WORKFLOW
    assert 'test -s "out/WebJam-v${version}-${target}.dmg"' in WORKFLOW
    assert "path: out/WebJam*${{ matrix.target }}*" in WORKFLOW
    assert "Mark unsigned platform source artifacts as test-only" in WORKFLOW
    assert "UNSIGNED-TEST-ONLY" in WORKFLOW
    assert "ADHOC-TEST-ONLY" in WORKFLOW


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
        "WebJam-macos-arm64.zip",
        "WebJam-macos-x64.zip",
        "WebJam-v${version}-macos-arm64.dmg",
        "WebJam-v${version}-macos-x64.dmg",
        "WebJam-v${version}-windows-x64-setup.exe",
        "WebJam-windows-x64.zip",
    }
    asset_block = release_job.split("          assets=(\n", 1)[1].split(
        "          )\n", 1
    )[0]
    assert set(re.findall(r'^\s+"(WebJam-[^"]+)"$', asset_block, re.MULTILINE)) == (
        expected_assets
    )
    assert 'checksum_file="WebJam-${GITHUB_REF_NAME}-SHA256SUMS.txt"' in release_job
    assert 'sha256sum -- "${assets[@]}" > "$checksum_file"' in release_job
    assert 'test "$(wc -l < "$checksum_file")" -eq "${#assets[@]}"' in release_job
    assert 'sha256sum --check --strict "$checksum_file"' in release_job
    assert "shopt -s nullglob dotglob" in release_job
    assert "downloaded=(*)" in release_job
    assert 'test "${#downloaded[@]}" -eq "${#assets[@]}"' in release_job
    expected_uploads = {
        "release-assets/WebJam-linux-x64.zip",
        "release-assets/WebJam-macos-arm64.zip",
        "release-assets/WebJam-macos-x64.zip",
        "release-assets/WebJam-${{ github.ref_name }}-macos-arm64.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-macos-x64.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-windows-x64-setup.exe",
        "release-assets/WebJam-windows-x64.zip",
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


def test_release_existence_probe_fails_closed_except_for_an_actual_404() -> None:
    release_job = WORKFLOW.split("  release:\n", 1)[1]
    probe = release_job.split(
        "      - name: Refuse mutation of an already-published release\n", 1
    )[1].split("\n      - name:", 1)[0]
    assert "gh api --include" in probe
    assert "probe_exit=$?" in probe
    assert 'http_status="$(sed -n' in probe
    assert 'elif [[ "$http_status" != "404" ]]' in probe
    assert "Could not prove whether release" in probe
    assert "2>/dev/null || true" not in probe


def test_linux_release_claims_only_the_certified_ubuntu_target() -> None:
    claims = "\n".join((LINUX_README, PROJECT_README, THIRD_PARTY_NOTICES))
    assert "certified only for 64-bit Ubuntu 22.04" in LINUX_README
    assert "Ubuntu 22.04 x64 ZIP" in PROJECT_README
    assert "certified only for Ubuntu 22.04 x64" in THIRD_PARTY_NOTICES
    assert "22.04 or newer" not in claims

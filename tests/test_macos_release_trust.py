"""Fail-closed contracts for the macOS Developer ID release path."""

from __future__ import annotations

import ast
import base64
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_APPDATA_SCRIPT = (
    ROOT / ".github" / "scripts" / "assert-no-appdata-usage.sh"
).read_text(encoding="utf-8")

SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
KEYCHAIN_PATH = ROOT / "packaging" / "macos" / "release-keychain.sh"
TRUST_PATH = ROOT / "packaging" / "macos" / "release-trust.sh"
WEBJAM_ENTITLEMENTS_PATH = ROOT / "packaging" / "macos" / "WebJam.entitlements"
JAMULUS_ENTITLEMENTS_PATH = ROOT / "packaging" / "macos" / "Jamulus.entitlements"
KEYCHAIN = KEYCHAIN_PATH.read_text(encoding="utf-8")
TRUST = TRUST_PATH.read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8")

MACOS_SECRETS = (
    "MACOS_DEVELOPER_ID_P12",
    "MACOS_DEVELOPER_ID_P12_PASSWORD",
    "APPLE_NOTARY_KEY_P8",
    "APPLE_NOTARY_KEY_ID",
    "APPLE_NOTARY_ISSUER_ID",
)


def _workflow_step(name: str) -> str:
    marker = f"      - name: {name}\n"
    start = WORKFLOW.index(marker)
    end = WORKFLOW.find("\n      - name:", start + len(marker))
    return WORKFLOW[start : end if end >= 0 else len(WORKFLOW)]


def _workflow_job(name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match is not None
    return match.group(0)


def _bash_function(source: str, name: str) -> str:
    marker = f"{name}() {{\n"
    start = source.index(marker)
    end = source.index("\n}\n", start + len(marker))
    return source[start : end + 3]


def _bash_array(source: str, name: str) -> tuple[str, ...]:
    match = re.search(
        rf"(?m)^{re.escape(name)}=\(\n(?P<body>.*?)^\)\n",
        source,
        re.DOTALL,
    )
    assert match is not None
    return tuple(re.findall(r"(?m)^\s{2}([A-Z][A-Z0-9_]+)\s*$", match["body"]))


def _mapped_secrets(step: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        re.findall(
            r"(?m)^\s+([A-Z0-9_]+): \$\{\{ secrets\.([A-Z0-9_]+) \}\}$",
            step,
        )
    )


def _python_heredoc_after(source: str, marker: str) -> str:
    marker_start = source.index(marker)
    start = source.index("<<'PY'\n", marker_start) + len("<<'PY'\n")
    end = source.index("\nPY\n", start)
    return source[start:end]


def _release_entitlement_policy() -> tuple[dict[str, bool], ...]:
    tree = ast.parse(_python_heredoc_after(TRUST, "validate_source_entitlements()"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "expected"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, tuple)
            return value
    raise AssertionError("release entitlement policy was not found")


def _valid_credentials() -> dict[str, str]:
    # validate intentionally performs only cheap structural checks. These are
    # inert test bytes; prepare is responsible for importing a real PKCS#12.
    dummy_der = b"\x30\x03\x02\x01\x00"
    dummy_pem = b"-----BEGIN PRIVATE KEY-----\nAA==\n-----END PRIVATE KEY-----\n"
    return {
        "MACOS_DEVELOPER_ID_P12": base64.b64encode(dummy_der).decode("ascii"),
        "MACOS_DEVELOPER_ID_P12_PASSWORD": "not-a-real-p12-password",
        "APPLE_NOTARY_KEY_P8": base64.b64encode(dummy_pem).decode("ascii"),
        "APPLE_NOTARY_KEY_ID": "ABC123DEF4",
        "APPLE_NOTARY_ISSUER_ID": "01234567-89ab-cdef-0123-456789abcdef",
        "APPLE_DEVELOPER_TEAM_ID": "TEAMID1234",
    }


def _run_keychain_validate(credentials: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for name in MACOS_SECRETS:
        env.pop(name, None)
    env.pop("APPLE_DEVELOPER_TEAM_ID", None)
    env.update(credentials)
    env["WEBJAM_PYTHON_BIN"] = sys.executable
    return subprocess.run(
        [str(KEYCHAIN_PATH), "validate"],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=5,
    )


def test_workflow_gates_exactly_five_macos_secrets_to_manual_rehearsals() -> None:
    trust_job = _workflow_job("macos-release-trust")
    build_job = _workflow_job("build-desktop")
    validate = _workflow_step("Validate protected macOS release credentials")
    prepare = _workflow_step("Prepare ephemeral macOS release keychain")
    expected_mapping = tuple((name, name) for name in MACOS_SECRETS)

    assert _mapped_secrets(validate) == expected_mapping
    assert _mapped_secrets(prepare) == expected_mapping
    assert _bash_array(KEYCHAIN, "required_names") == MACOS_SECRETS
    for name in MACOS_SECRETS:
        reference = rf"\$\{{\{{ secrets\.{re.escape(name)} \}}\}}"
        assert len(re.findall(reference, WORKFLOW)) == 2
    assert (
        WORKFLOW.count("APPLE_DEVELOPER_TEAM_ID: ${{ vars.APPLE_DEVELOPER_TEAM_ID }}")
        == 2
    )

    condition = " ".join(
        trust_job.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0].split()
    )
    assert condition == (
        "github.event_name == 'workflow_dispatch' && "
        "inputs.macos_signing_rehearsal"
    )
    dispatch = WORKFLOW.split("  workflow_dispatch:\n", 1)[1].split(
        "\n\n# Default:", 1
    )[0]
    assert "macos_signing_rehearsal:" in dispatch
    assert "type: boolean" in dispatch
    assert "default: false" in dispatch
    assert "environment:\n      name: macos-release" in trust_job
    assert "deployment: false" in trust_job
    assert "needs: build-desktop" in trust_job
    assert not any(f"secrets.{name}" in build_job for name in MACOS_SECRETS)
    assert not any(f"vars.{name}" in build_job for name in MACOS_SECRETS)


def test_ordinary_macos_builds_are_secret_free_ad_hoc_artifacts() -> None:
    build_job = _workflow_job("build-desktop")
    build = _workflow_step("Build desktop artifact")
    assert "codesign_identity=None" in SPEC
    assert "entitlements_file=None" in SPEC
    assert "codesign --force --deep --sign -" in build
    assert "codesign --force --sign - dist/WebJam.app" in build
    assert not any(f"secrets.{name}" in build for name in MACOS_SECRETS)
    assert "release-keychain.sh" not in build_job
    assert "release-trust.sh" not in build_job
    assert "WEBJAM_MACOS_RELEASE_TRUST" not in build_job
    assert "\n    environment:" not in build_job


def test_release_trust_workflow_order_and_unconditional_cleanup() -> None:
    step_names = (
        "Download tested ad-hoc macOS source artifact",
        "Verify downloaded macOS source before credential access",
        "Validate protected macOS release credentials",
        "Prepare ephemeral macOS release keychain",
        "Sign, notarize, and staple protected macOS app",
        "Build, sign, notarize, and staple protected macOS disk image",
        "Remove protected macOS credentials and keychain",
        "Verify and launch protected macOS release containers",
        "Upload protected macOS release artifact",
        "Upload protected macOS notarization evidence",
    )
    positions = [WORKFLOW.index(f"      - name: {name}\n") for name in step_names]
    assert positions == sorted(positions)

    source = _workflow_step("Verify downloaded macOS source before credential access")
    prepare = _workflow_step("Prepare ephemeral macOS release keychain")
    app = _workflow_step("Sign, notarize, and staple protected macOS app")
    dmg = _workflow_step("Build, sign, notarize, and staple protected macOS disk image")
    mounted = _workflow_step("Verify and launch protected macOS release containers")
    artifact = _workflow_step("Upload protected macOS release artifact")
    evidence = _workflow_step("Upload protected macOS notarization evidence")
    cleanup = _workflow_step("Remove protected macOS credentials and keychain")
    release_app = _bash_function(TRUST, "release_app")
    assert not any(f"secrets.{name}" in source for name in MACOS_SECRETS)
    assert "WebJam-${target}-ADHOC-TEST-ONLY.zip" in source
    assert "ADHOC-TEST-ONLY.dmg" in source
    assert "codesign --verify --deep --strict" in source
    # The check moved into .github/scripts/assert-no-appdata-usage.sh so the step stayed under GitHub's
    # 21,000-character expression limit; assert the step still runs it.
    assert "assert-no-appdata-usage.sh" in source
    assert "'Print :NSAppDataUsageDescription'" in (
        Path(".github/scripts/assert-no-appdata-usage.sh").read_text(encoding="utf-8")
    )
    assert "! /usr/libexec/PlistBuddy" in _APPDATA_SCRIPT
    assert 'assert-no-appdata-usage.sh "$app"' in source
    # The bundle list moved into the script with the check, so assert the
    # script covers the nested components rather than the step inlining them.
    assert "JamulusHeadlessClient.app" in _APPDATA_SCRIPT
    assert "WebJam accesses Jamulus app data" not in source
    assert "verify_packaged_transport" not in source
    assert "/usr/bin/shasum -a 256" in source
    assert "/usr/bin/file" in source
    assert "python -m" not in source
    assert "python -c" not in source
    assert '"$GITHUB_REF_NAME" != "v${version}"' in source
    assert "WEBJAM_SMOKE_LAUNCH_ONLY" not in source
    assert "packaging/macos/release-keychain.sh prepare" in prepare
    assert release_app.index('validate_component_policy "$app"') < (
        release_app.index("require_signing_environment")
    )
    assert "packaging/macos/release-trust.sh app" in app
    assert dmg.index("packaging/macos/create-dmg.sh") < dmg.index(
        "packaging/macos/release-trust.sh dmg"
    )
    assert dmg.count("packaging/macos/release-trust.sh dmg") == 1
    assert mounted.count("packaging/macos/release-trust.sh verify-app") == 2
    assert "xcrun stapler validate" in mounted
    assert "spctl --assess --type open" in mounted
    assert "WEBJAM_SMOKE_LAUNCH_ONLY=1" in mounted
    assert "name: webjam-release-${{ matrix.target }}" in artifact
    assert "out/WebJam-${{ matrix.target }}.zip" in artifact
    assert "out/WebJam-v*-${{ matrix.target }}.dmg" in artifact
    assert "if: always()" in evidence
    assert "name: webjam-notarization-${{ matrix.target }}" in evidence
    assert "path: out/notarization/${{ matrix.target }}/" in evidence
    assert "if-no-files-found: error" in evidence
    assert "retention-days: 90" in evidence
    assert "if: always()" in cleanup
    assert "packaging/macos/release-keychain.sh cleanup" in cleanup
    assert WORKFLOW.index("Remove protected macOS credentials and keychain") < (
        WORKFLOW.index("Verify and launch protected macOS release containers")
    )
    assert WORKFLOW.index("Remove protected macOS credentials and keychain") < (
        WORKFLOW.index("Upload protected macOS release artifact")
    )


def test_candidate_release_is_a_tag_only_draft_from_tested_builds() -> None:
    release = _workflow_job("release")
    assert "needs: build-desktop" in release
    assert "windows-release-trust" not in release
    assert "macos-release-trust" not in release
    assert "if: startsWith(github.ref, 'refs/tags/v')" in release
    assert "name: webjam-macos-arm64" in release
    assert "name: webjam-macos-x64" in release
    create = _workflow_step("Create GitHub Release")
    assert (
        "uses: softprops/action-gh-release@"
        "3d0d9888cb7fd7b750713d6e236d1fcb99157228" in create
    )
    assert "draft: true" in create
    assert "prerelease: false" in create
    assert "unsigned private test candidate" in create
    assert "fail_on_unmatched_files: true" in create
    uploads = set(
        re.findall(
            r"(?m)^\s+(release-assets/WebJam-[^\n]+)$",
            create.split("          files: |\n", 1)[1],
        )
    )
    assert uploads == {
        "release-assets/WebJam-linux-x64.zip",
        "release-assets/WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "release-assets/WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "release-assets/WebJam-${{ github.ref_name }}-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-macos-x64-ADHOC-TEST-ONLY.dmg",
        "release-assets/WebJam-${{ github.ref_name }}-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "release-assets/WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "release-assets/WebJam-${{ github.ref_name }}-SHA256SUMS.txt",
    }


def test_release_entitlements_are_exact_and_component_specific() -> None:
    with WEBJAM_ENTITLEMENTS_PATH.open("rb") as stream:
        webjam = plistlib.load(stream)
    with JAMULUS_ENTITLEMENTS_PATH.open("rb") as stream:
        jamulus = plistlib.load(stream)
    assert webjam == {
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.microphone": True,
    }
    assert jamulus == {"com.apple.security.device.audio-input": True}
    assert _release_entitlement_policy() == (webjam, jamulus)
    assert "com.apple.security.app-sandbox" not in webjam
    assert "com.apple.security.app-sandbox" not in jamulus
    assert (
        '"$JAMULUS_APP"|"$JAMULUS_SERVER_APP"|"$JAMULUS_HEADLESS_APP")'
        in TRUST
    )
    assert 'sign_target "$target" "$JAMULUS_ENTITLEMENTS"' in TRUST
    assert 'verify_headless_manifest "$app"' in TRUST
    assert "QtWebEngineProcess.entitlements" not in TRUST
    assert "NSCameraUsageDescription" not in SPEC
    excludes = SPEC.split("excludes=[", 1)[1].split(
        "],\n    win_no_prefer_redirects=", 1
    )[0]
    assert '"PySide6.QtWebEngineCore"' in excludes
    assert "# Webex is external-only." in SPEC


def test_release_bundle_layout_dependencies_are_pinned() -> None:
    assert "PySide6==6.11.1" in REQUIREMENTS
    assert "pyinstaller==6.21.0" in REQUIREMENTS
    assert "PySide6>=" not in REQUIREMENTS
    assert "pyinstaller>=" not in REQUIREMENTS


def test_release_scripts_are_executable_strict_and_never_trace_secrets() -> None:
    xtrace = re.compile(r"(?m)^\s*set\s+(?:-[A-Za-z]*x[A-Za-z]*|-o\s+xtrace)\s*$")
    for path, source in ((KEYCHAIN_PATH, KEYCHAIN), (TRUST_PATH, TRUST)):
        assert os.access(path, os.X_OK)
        assert source.startswith("#!/usr/bin/env bash\n")
        assert "\nset -euo pipefail\n" in source
        assert xtrace.search(source) is None


def test_keychain_is_ephemeral_and_exports_no_password() -> None:
    prepare = _bash_function(KEYCHAIN, "prepare_keychain")
    cleanup = _bash_function(KEYCHAIN, "cleanup_credentials")
    assert "trap cleanup_credentials EXIT" in prepare
    assert 'create-keychain -p "$keychain_password" "$KEYCHAIN_PATH"' in prepare
    assert 'import "$P12_PATH"' in prepare
    assert '-T "$CODESIGN_BIN"' in prepare
    assert " -A " not in prepare
    assert "Expected exactly one valid Developer ID Application identity" in prepare
    assert "notarytool history" in prepare
    assert '>> "$GITHUB_ENV"' in prepare
    assert "WEBJAM_MACOS_CODESIGN_IDENTITY=" in prepare
    assert "WEBJAM_MACOS_CODESIGN_TEAM_ID=" in prepare
    assert "WEBJAM_MACOS_KEYCHAIN_PASSWORD=" not in prepare
    assert 'delete-keychain "$KEYCHAIN_PATH"' in cleanup
    assert '"$KEYCHAIN_PATH" "$P12_PATH" "$P8_PATH" "$NOTARY_HISTORY_PATH"' in cleanup
    assert "list-keychains -s" not in KEYCHAIN


def test_release_signs_inside_out_without_deep_signing() -> None:
    sign_target = _bash_function(TRUST, "sign_target")
    sign_app = _bash_function(TRUST, "sign_app_inside_out")
    assert "--options runtime" in sign_target
    assert "--timestamp" in sign_target
    assert '--keychain "$SIGNING_KEYCHAIN"' in sign_target
    assert '--sign "$SIGNING_IDENTITY"' in sign_target
    assert "--deep" not in sign_target
    for line in TRUST.splitlines():
        if "--deep" in line:
            assert "--verify" in line

    leaves = sign_app.index('find "$app" -type f -print0')
    bundles = sign_app.index('bundle_inventory "$app"')
    manifest = sign_app.index('> "$fabric_manifest"')
    outer = sign_app.index('sign_target "$app" "$WEBJAM_ENTITLEMENTS"')
    assert leaves < bundles < manifest < outer
    assert "*.app|*.xpc|*.appex)" in sign_app
    assert "refusing to sign an app/helper outside the explicit policy" in sign_app


def test_release_verifies_identity_runtime_timestamp_and_entitlements() -> None:
    verify_signature = _bash_function(TRUST, "verify_signature")
    verify_app = _bash_function(TRUST, "verify_app_core")
    assert "--verify --all-architectures --strict --verbose=4" in verify_signature
    assert "Authority=Developer ID Application:" in verify_signature
    assert "TeamIdentifier=$SIGNING_TEAM_ID" in verify_signature
    assert "runtime" in verify_signature
    assert "Timestamp=" in verify_signature
    assert "Signature=adhoc" in verify_signature
    assert "--verify --all-architectures --deep --strict" in verify_app
    assert "verify_entitlements_exact" in verify_app
    assert "com.apple.security.get-task-allow" in verify_app
    assert "verify_no_entitlements" in verify_app
    assert "verify_transport_manifest" in verify_app


def test_notary_results_and_logs_must_be_cleanly_accepted() -> None:
    submit = _bash_function(TRUST, "notary_submit")
    assert "${label}-notary-submit.json" in submit
    assert "${label}-notary-log.json" in submit
    assert "notarytool submit" in submit
    assert "--wait --timeout 45m --output-format json" in submit
    assert submit.index("notarytool submit") < submit.index("notarytool log")
    assert submit.index("notarytool log") < submit.index("submit_rc == 0")
    assert 'result.get("status") != "Accepted"' in submit
    assert 'log.get("status") != "Accepted"' in submit
    assert 'log.get("issues") not in (None, [])' in submit
    assert "--timeout 45m" in submit


def test_app_and_dmg_are_independently_notarized_stapled_and_assessed() -> None:
    app = _bash_function(TRUST, "release_app")
    verify_stapled = _bash_function(TRUST, "verify_stapled_app")
    dmg = _bash_function(TRUST, "release_dmg")

    assert app.index('notary_submit "$submission_zip" app') < app.index(
        'stapler staple "$app"'
    )
    assert app.index('stapler staple "$app"') < app.index(
        '"$DITTO_BIN" -c -k --sequesterRsrc --keepParent "$app" "$final_zip"'
    )
    assert '"$DITTO_BIN" -x -k "$final_zip" "$fresh_dir"' in app
    assert "app-submission-zip.sha256" in app
    assert "app-final-zip.sha256" in app
    assert 'stapler validate "$app"' in verify_stapled
    assert '"$SYSPOLICY_CHECK_BIN" distribution "$app"' in verify_stapled
    assert '"$SPCTL_BIN" --assess --type exec --verbose=4 "$app"' in verify_stapled
    assert '"$SYSPOLICY_CHECK_BIN" notary-submission "$app"' in app

    assert dmg.index('notary_submit "$dmg" dmg') < dmg.index('stapler staple "$dmg"')
    assert dmg.index('stapler staple "$dmg"') < dmg.index('stapler validate "$dmg"')
    assert '"$HDIUTIL_BIN" verify "$dmg"' in dmg
    assert "--force --all-architectures --timestamp" in dmg
    assert "--assess --type open" in dmg
    assert "--context context:primary-signature" in dmg
    assert "dmg-signed.sha256" in dmg
    assert "dmg-final-stapled.sha256" in dmg


def test_keychain_validate_rejects_missing_credentials() -> None:
    result = _run_keychain_validate({})
    assert result.returncode != 0
    assert "macOS release trust credentials are missing:" in result.stderr
    assert all(name in result.stderr for name in MACOS_SECRETS)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    (
        ("MACOS_DEVELOPER_ID_P12", "not-base64", "is not valid base64"),
        (
            "APPLE_NOTARY_KEY_P8",
            base64.b64encode(b"not a PEM key").decode("ascii"),
            "is not a PEM private key",
        ),
        ("APPLE_NOTARY_KEY_ID", "short", "must be 10 uppercase letters/digits"),
        ("APPLE_NOTARY_ISSUER_ID", "not-a-uuid", "must be a UUID"),
        (
            "APPLE_DEVELOPER_TEAM_ID",
            "short",
            "must be 10 uppercase letters/digits",
        ),
    ),
)
def test_keychain_validate_rejects_malformed_credentials(
    name: str,
    value: str,
    message: str,
) -> None:
    credentials = _valid_credentials()
    credentials[name] = value
    result = _run_keychain_validate(credentials)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert message in result.stderr
    assert credentials["MACOS_DEVELOPER_ID_P12_PASSWORD"] not in output


def test_keychain_validate_accepts_structurally_valid_credentials() -> None:
    result = _run_keychain_validate(_valid_credentials())
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_keychain_cleanup_removes_every_credential_file_even_if_security_fails(
    tmp_path: Path,
) -> None:
    paths = (
        tmp_path / "webjam-release-signing.keychain-db",
        tmp_path / "webjam-developer-id.p12",
        tmp_path / "webjam-notary-key.p8",
        tmp_path / "webjam-notary-history.json",
    )
    for path in paths:
        path.write_bytes(b"credential fixture")
    env = os.environ.copy()
    env["RUNNER_TEMP"] = str(tmp_path)
    env["WEBJAM_SECURITY_BIN"] = "/bin/false"
    result = subprocess.run(
        [str(KEYCHAIN_PATH), "cleanup"],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not any(path.exists() for path in paths)

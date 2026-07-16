"""Fail-closed contracts for the not-yet-credentialed macOS release path."""
from __future__ import annotations

import plistlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)
ENTITLEMENTS_PATH = ROOT / "packaging" / "macos" / "WebJam.entitlements"


def _macos_trust_guard() -> str:
    start = WORKFLOW.index("      - name: Refuse untrusted tagged macOS release\n")
    end = WORKFLOW.index("\n      - name:", start + 1)
    return WORKFLOW[start:end]


def test_tagged_macos_build_fails_closed_before_packaging() -> None:
    guard = _macos_trust_guard()
    assert (
        "if: startsWith(matrix.target, 'macos-') && "
        "startsWith(github.ref, 'refs/tags/v')" in guard
    )
    assert WORKFLOW.index("Refuse untrusted tagged macOS release") < WORKFLOW.index(
        "      - name: Build desktop artifact\n"
    )
    assert "Refusing to produce an ad-hoc tagged macOS release." in guard
    # Even provisioning the secrets must not make the unfinished trust path
    # silently pass. The final command in the guard is an explicit failure.
    assert guard.rstrip().endswith("exit 1")


def test_tagged_macos_guard_requires_the_exact_five_secrets() -> None:
    guard = _macos_trust_guard()
    expected = {
        "MACOS_DEVELOPER_ID_P12",
        "MACOS_DEVELOPER_ID_P12_PASSWORD",
        "APPLE_NOTARY_KEY_P8",
        "APPLE_NOTARY_KEY_ID",
        "APPLE_NOTARY_ISSUER_ID",
    }
    mapped = set(
        re.findall(
            r"^\s+([A-Z0-9_]+): \$\{\{ secrets\.\1 \}\}$",
            guard,
            re.MULTILINE,
        )
    )
    assert mapped == expected
    required_block = guard.split("          required=(\n", 1)[1].split(
        "          )\n", 1
    )[0]
    required = set(re.findall(r"^\s+([A-Z0-9_]+)$", required_block, re.MULTILINE))
    assert required == expected
    assert 'if [[ -z "${!name:-}" ]]' in guard
    assert 'if [[ "${#missing[@]}" -gt 0 ]]' in guard


def test_ordinary_macos_builds_remain_explicitly_ad_hoc() -> None:
    assert "codesign_identity=None" in SPEC
    assert "entitlements_file=None" in SPEC
    assert "codesign --force --deep --sign -" in WORKFLOW
    assert "codesign --force --sign - dist/WebJam.app" in WORKFLOW
    assert "ordinary branch builds\n# remain ad-hoc signed test artifacts" in SPEC


def test_webjam_entitlements_are_minimal_camera_and_microphone_access() -> None:
    with ENTITLEMENTS_PATH.open("rb") as stream:
        entitlements = plistlib.load(stream)
    assert entitlements == {
        "com.apple.security.device.camera": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.microphone": True,
    }
    assert "NSCameraUsageDescription" in SPEC
    assert "NSMicrophoneUsageDescription" in SPEC
    assert '"embedded Webex video companion."' in SPEC


def test_docs_use_notarytool_and_preserve_the_unimplemented_boundary() -> None:
    assert "xcrun altool" not in SPEC
    assert "xcrun notarytool submit" in SPEC
    for secret_name in (
        "MACOS_DEVELOPER_ID_P12",
        "MACOS_DEVELOPER_ID_P12_PASSWORD",
        "APPLE_NOTARY_KEY_P8",
        "APPLE_NOTARY_KEY_ID",
        "APPLE_NOTARY_ISSUER_ID",
    ):
        assert f"`{secret_name}`" in RUNBOOK
    for contract in (
        "ephemeral\nkeychain",
        "Qt\nWebEngine helper",
        "explicitly re-signed and verified",
        "packaging/macos/WebJam.entitlements",
        "retain its audio-input entitlement",
        "xcrun notarytool\nsubmit ... --wait",
        "inspect the notary log",
        "spctl",
        "unconditional cleanup step",
        "deliberate tag stop",
    ):
        assert contract in RUNBOOK

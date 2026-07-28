"""Release-contract tests for frozen dependency attribution and policy."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from tools.runtime_dependency_policy import (
    DEFAULT_LOCK_ROOT,
    DEFAULT_NOTICE,
    DEFAULT_POLICY,
    DEFAULT_SBOM,
    PolicyError,
    RuntimeInventory,
    render_notice,
    render_sbom,
    validate_policy,
    verify_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "webjam.spec").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
WINDOWS_INSTALLER = (
    ROOT / "packaging" / "windows" / "WebJam.iss"
).read_text(encoding="utf-8")


def _write_policy(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_generated_notice_and_sbom_are_current_and_deterministic() -> None:
    inventory = validate_policy()
    assert DEFAULT_NOTICE.read_text(encoding="utf-8") == render_notice(inventory)
    assert DEFAULT_SBOM.read_text(encoding="utf-8") == render_sbom(inventory)
    assert render_notice(inventory) == render_notice(validate_policy())
    assert render_sbom(inventory) == render_sbom(validate_policy())

    sbom = json.loads(render_sbom(inventory))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert "timestamp" not in sbom["metadata"]


def test_every_locked_distribution_has_a_reviewed_classification() -> None:
    inventory = validate_policy()
    entries = inventory.policy["packages"]
    locked_names = {package.name for package in inventory.packages}
    assert set(entries) == locked_names
    assert {entry["scope"] for entry in entries.values()} == {
        "build",
        "excluded",
        "runtime",
    }


def test_every_runtime_distribution_is_attributed_in_both_artifacts() -> None:
    inventory = validate_policy()
    notice = render_notice(inventory)
    sbom = json.loads(render_sbom(inventory))
    components = {
        (component["name"], component.get("version"))
        for component in sbom["components"]
    }

    for package in inventory.packages:
        entry = inventory.policy["packages"][package.name]
        assert f"`{package.name}`" in notice
        assert entry["homepage"] in notice
        if entry["scope"] != "runtime":
            continue
        for _target, version in package.versions:
            assert (package.name, version) in components


def test_unreviewed_locked_distribution_fails_closed(tmp_path: Path) -> None:
    lock_root = tmp_path / "requirements-lock"
    shutil.copytree(DEFAULT_LOCK_ROOT, lock_root)
    linux_lock = lock_root / "linux-x64.txt"
    linux_lock.write_text(
        linux_lock.read_text(encoding="utf-8")
        + "\nunreviewed-audio-plugin==1.0.0 \\\n"
        + "    --hash=sha256:"
        + ("0" * 64)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="lack reviewed policy"):
        validate_policy(lock_root=lock_root)


@pytest.mark.parametrize("license_expression", ["GPL-3.0-only", "AGPL-3.0-only"])
def test_gpl_and_agpl_runtime_selections_are_rejected(
    tmp_path: Path,
    license_expression: str,
) -> None:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    policy["packages"]["numpy"]["license_expression"] = license_expression
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path, policy)

    with pytest.raises(PolicyError, match="forbidden copyleft"):
        validate_policy(policy_path=policy_path)


def test_build_only_pyinstaller_exception_does_not_weaken_runtime_rule() -> None:
    inventory = validate_policy()
    policy = inventory.policy["packages"]
    assert policy["pyinstaller"]["scope"] == "build"
    assert policy["pyinstaller"]["license_expression"].startswith("GPL-")
    for name, entry in policy.items():
        if entry["scope"] == "runtime":
            identifiers = entry["license_expression"].replace("LGPL", "")
            assert "GPL-" not in identifiers, name
            assert "AGPL-" not in identifiers, name


def test_soundfile_native_payload_and_mp3_claims_are_truthful() -> None:
    inventory = validate_policy()
    policy = inventory.policy
    native = {
        entry["name"]: entry
        for entry in policy["soundfile_bundled_native_components"]
    }
    assert policy["packages"]["numpy"]["license_expression"] == (
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"
    )
    assert policy["packages"]["soundfile"]["license_expression"] == "BSD-3-Clause"
    assert native["libsndfile"]["version"] == "1.2.2"
    assert native["libsndfile"]["license_expression"] == "LGPL-2.1-or-later"
    assert native["libmp3lame"]["license_expression"] == "LGPL-2.0-or-later"
    assert native["libmpg123"]["license_expression"] == "LGPL-2.1-only"
    assert policy["mp3_capability"]["import"] == {
        "availability": "runtime-probed",
        "probe": "soundfile.check_format('MP3')",
    }
    assert policy["mp3_capability"]["bounce"] == {
        "availability": "disabled-by-default",
        "bundled_default_adapter": False,
    }
    assert policy["mp3_capability"]["bundled_standalone_encoder"] is False
    notice = render_notice(inventory)
    assert "does not ship FFmpeg or a separate MP3 executable" in notice
    assert "the presence of a SoundFile wheel is not itself an MP3 guarantee" in notice
    assert "MP3 bounce is a separate capability and is disabled" in notice
    assert "SoundFile import capability alone must never enable MP3 bounce" in notice


def test_reviewed_soundfile_license_files_are_exact_wheel_evidence() -> None:
    expected = {
        ROOT / "licenses" / "SOUNDFILE_LICENSE.txt": (
            "0dd2e411fed553ba891845077e5a32c5b726d1bbc46ff31a2118ae1ccf816752"
        ),
        ROOT / "licenses" / "SOUNDFILE_WHEEL_LICENSE_NOTES.md": (
            "35630e1f59b5c54b0bcbe2ec173bcf5a63b727a6e2f7834ca6d35a1af91a2b5f"
        ),
    }
    for path, digest in expected.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_bundle_verifier_checks_generated_files_and_license_hashes(
    tmp_path: Path,
) -> None:
    inventory = validate_policy()
    policy = deepcopy(inventory.policy)
    evidence_bytes = b"reviewed license evidence\n"
    evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
    policy["packaged_license_evidence"] = [
        {
            "component": "test evidence",
            "path_suffix": "THIRD_PARTY_LICENSES/TEST_LICENSE.txt",
            "sha256": evidence_hash,
        }
    ]
    test_inventory = RuntimeInventory(policy=policy, packages=inventory.packages)
    licenses = tmp_path / "THIRD_PARTY_LICENSES"
    licenses.mkdir()
    (licenses / "THIRD_PARTY_NOTICES_RUNTIME.md").write_text(
        render_notice(test_inventory),
        encoding="utf-8",
    )
    (licenses / "WebJam-runtime-sbom.cdx.json").write_text(
        render_sbom(test_inventory),
        encoding="utf-8",
    )
    (licenses / "TEST_LICENSE.txt").write_bytes(evidence_bytes)

    verify_bundle(tmp_path, test_inventory)
    (licenses / "TEST_LICENSE.txt").write_bytes(b"tampered\n")
    with pytest.raises(PolicyError, match="checksum failed"):
        verify_bundle(tmp_path, test_inventory)


def test_pyinstaller_and_native_ci_package_generated_policy_artifacts() -> None:
    for name in (
        "THIRD_PARTY_NOTICES_RUNTIME.md",
        "WebJam-runtime-sbom.cdx.json",
        "runtime-dependency-policy.json",
        "SOUNDFILE_LICENSE.txt",
        "SOUNDFILE_WHEEL_LICENSE_NOTES.md",
    ):
        assert name in SPEC
    assert "tools/runtime_dependency_policy.py --check" in CI
    assert "tools/runtime_dependency_policy.py" in CI
    assert '--verify-bundle "$dependency_bundle_root"' in CI
    assert 'Source: "..\\..\\THIRD_PARTY_NOTICES_RUNTIME.md"' in WINDOWS_INSTALLER
    assert 'DestName: "WebJam-runtime-sbom.cdx.json"' in WINDOWS_INSTALLER

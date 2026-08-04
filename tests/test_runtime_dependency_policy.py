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
    main as policy_main,
    validate_policy,
    verify_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTES = (ROOT / ".gitattributes").read_text(encoding="utf-8")
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


def test_cryptography_floor_and_platform_openssl_inventory_are_exact() -> None:
    inventory = validate_policy()
    cryptography = next(
        package for package in inventory.packages if package.name == "cryptography"
    )
    assert dict(cryptography.versions) == {
        target: "50.0.0" for target in inventory.policy["targets"]
    }

    platform_components = inventory.policy["platform_bundled_native_components"]
    assert platform_components == [
        {
            "name": "openssl",
            "version": "4.0.1",
            "license_expression": "Apache-2.0",
            "homepage": "https://www.openssl-library.org/",
            "targets": ["linux-x64", "macos-arm64", "windows-x64"],
            "embedded_by": "cryptography 50.0.0 upstream wheels",
            "source_sha256": (
                "2db3f3a0d6ea4b59e1f094ace2c8cd536dffb87cdc39084c5afa1e6f7f37dd09"
            ),
            "provenance": "CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt",
        },
        {
            "name": "openssl",
            "version": "3.5.7",
            "license_expression": "Apache-2.0",
            "homepage": "https://www.openssl-library.org/",
            "targets": ["macos-x64"],
            "embedded_by": "cryptography 50.0.0",
            "source_sha256": (
                "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8"
            ),
            "provenance": "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt",
        }
    ]

    notice = render_notice(inventory)
    for value in (
        "Platform-specific native payload",
        "`openssl`",
        "`4.0.1`",
        "`3.5.7`",
        "`linux-x64,macos-arm64,windows-x64`",
        "`macos-x64`",
        "`cryptography 50.0.0 upstream wheels`",
        "`cryptography 50.0.0`",
        "`Apache-2.0`",
        "`CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt`",
        "`CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt`",
    ):
        assert value in notice

    sbom = json.loads(render_sbom(inventory))
    openssl = [
        component
        for component in sbom["components"]
        if component["name"].lower() == "openssl"
    ]
    assert len(openssl) == 2
    expected = {
        "4.0.1": {
            "bom-ref": (
                "webjam:native:openssl@4.0.1?targets="
                "linux-x64,macos-arm64,windows-x64"
            ),
            "webjam:targets": "linux-x64,macos-arm64,windows-x64",
            "webjam:embedded-by": "cryptography 50.0.0 upstream wheels",
            "webjam:source-sha256": (
                "2db3f3a0d6ea4b59e1f094ace2c8cd536dffb87cdc39084c5afa1e6f7f37dd09"
            ),
            "webjam:provenance": "CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt",
        },
        "3.5.7": {
            "bom-ref": "webjam:native:openssl@3.5.7?targets=macos-x64",
            "webjam:targets": "macos-x64",
            "webjam:embedded-by": "cryptography 50.0.0",
            "webjam:source-sha256": (
                "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8"
            ),
            "webjam:provenance": "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt",
        },
    }
    for component in openssl:
        component_expected = expected[component["version"]]
        assert component["bom-ref"] == component_expected["bom-ref"]
        assert component["scope"] == "required"
        assert component["licenses"] == [{"expression": "Apache-2.0"}]
        assert {
            item["name"]: item["value"] for item in component["properties"]
        } == {
            name: value
            for name, value in component_expected.items()
            if name != "bom-ref"
        }


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("targets", [], "invalid targets"),
        ("source_sha256", "0" * 63, "source SHA-256"),
        ("provenance", "../unreviewed.txt", "unsafe provenance"),
        ("license_expression", "GPL-3.0-only", "forbidden copyleft"),
        ("version", "3.5.7", "identities must be unique"),
    ],
)
def test_platform_native_component_policy_fails_closed(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    policy["platform_bundled_native_components"][0][field] = replacement
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path, policy)

    with pytest.raises(PolicyError, match=message):
        validate_policy(policy_path=policy_path)


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


@pytest.mark.parametrize(
    "replacement",
    [
        {},
        {"sha256": "0" * 64, "sha256_by_target": {}},
        {"sha256_by_target": {"linux-x64": "0" * 64}},
        {
            "sha256_by_target": {
                "linux-x64": "0" * 64,
                "macos-arm64": "0" * 64,
                "macos-x64": "0" * 64,
                "windows-x64": "0" * 64,
                "freebsd-x64": "0" * 64,
            }
        },
        {
            "sha256_by_target": {
                "linux-x64": "not-a-digest",
                "macos-arm64": "0" * 64,
                "macos-x64": "0" * 64,
                "windows-x64": "0" * 64,
            }
        },
    ],
)
def test_packaged_license_hash_policy_fails_closed(
    tmp_path: Path,
    replacement: dict[str, object],
) -> None:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    evidence = policy["packaged_license_evidence"][0]
    evidence.pop("sha256", None)
    evidence.pop("sha256_by_target", None)
    evidence.update(replacement)
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path, policy)

    with pytest.raises(PolicyError, match="SHA-256|target-bound"):
        validate_policy(policy_path=policy_path)


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


def test_reviewed_cryptography_openssl_license_and_provenance_are_exact() -> None:
    expected = {
        ROOT / "licenses" / "OPENSSL_LICENSE.txt": (
            "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a"
        ),
        ROOT
        / "packaging"
        / "macos"
        / "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt": (
            "ccb4c67a88cda7e5ff3cea340bef12b2ca4e9035fd7a91e54123824b9672d52f"
        ),
        ROOT / "packaging" / "CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt": (
            "774757f50ed9ea59229b02906bdc6cf7fa23b883a37b1cda0dd1e0e18b292350"
        ),
    }
    inventory = validate_policy()
    evidence = {
        item["path_suffix"]: item["sha256"]
        for item in inventory.policy["packaged_license_evidence"]
        if item["component"].startswith(
            ("OpenSSL", "Intel cryptography", "cryptography upstream")
        )
    }
    assert evidence == {
        "THIRD_PARTY_LICENSES/OPENSSL_LICENSE.txt": expected[
            ROOT / "licenses" / "OPENSSL_LICENSE.txt"
        ],
        "THIRD_PARTY_LICENSES/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt": expected[
            ROOT
            / "packaging"
            / "macos"
            / "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt"
        ],
        "THIRD_PARTY_LICENSES/CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt": (
            expected[
                ROOT / "packaging" / "CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt"
            ]
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


def test_bundle_verifier_binds_reviewed_license_evidence_to_platform(
    tmp_path: Path,
) -> None:
    inventory = validate_policy()
    policy = deepcopy(inventory.policy)
    evidence_by_target = {
        target: f"reviewed {target} wheel license evidence\n".encode()
        for target in inventory.policy["targets"]
    }
    policy["packaged_license_evidence"] = [
        {
            "component": "test platform evidence",
            "path_suffix": "THIRD_PARTY_LICENSES/TEST_LICENSE.txt",
            "sha256_by_target": {
                target: hashlib.sha256(evidence).hexdigest()
                for target, evidence in evidence_by_target.items()
            },
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
    evidence = licenses / "TEST_LICENSE.txt"

    for target, reviewed in evidence_by_target.items():
        evidence.write_bytes(reviewed)
        verify_bundle(tmp_path, test_inventory, target=target)
    evidence.write_bytes(evidence_by_target["windows-x64"])
    with pytest.raises(PolicyError, match="checksum failed"):
        verify_bundle(tmp_path, test_inventory, target="linux-x64")
    with pytest.raises(PolicyError, match="target is required"):
        verify_bundle(tmp_path, test_inventory)
    with pytest.raises(PolicyError, match="unknown packaged bundle target"):
        verify_bundle(tmp_path, test_inventory, target="freebsd-x64")
    evidence.write_bytes(b"unreviewed wheel license evidence\n")
    with pytest.raises(PolicyError, match="checksum failed"):
        verify_bundle(tmp_path, test_inventory, target="windows-x64")


@pytest.mark.parametrize("artifact", ["notice", "sbom"])
def test_generated_artifact_check_rejects_crlf_bytes(
    tmp_path: Path,
    artifact: str,
) -> None:
    inventory = validate_policy()
    notice = tmp_path / "THIRD_PARTY_NOTICES_RUNTIME.md"
    sbom = tmp_path / "WebJam-runtime-sbom.cdx.json"
    rendered = {
        "notice": render_notice(inventory).encode("utf-8"),
        "sbom": render_sbom(inventory).encode("utf-8"),
    }
    notice.write_bytes(rendered["notice"])
    sbom.write_bytes(rendered["sbom"])
    selected = notice if artifact == "notice" else sbom
    selected.write_bytes(selected.read_bytes().replace(b"\n", b"\r\n"))

    assert (
        policy_main(
            [
                "--notice",
                str(notice),
                "--sbom",
                str(sbom),
                "--check",
            ]
        )
        == 1
    )


def test_numpy_license_evidence_maps_exact_reviewed_wheel_variants() -> None:
    inventory = validate_policy()
    numpy_evidence = next(
        item
        for item in inventory.policy["packaged_license_evidence"]
        if item["component"] == "NumPy 2.4.6 and bundled sources"
    )
    assert numpy_evidence["sha256_by_target"] == {
        "linux-x64": (
            "4860083caa0de2ac3292ca98bd074bd8f45d8b32624e37b1e70a240bff61e488"
        ),
        "macos-arm64": (
            "649a589ab31076e79e9ce62ba24eae9959598124176640dbc95f87bd10f34fcf"
        ),
        "macos-x64": (
            "649a589ab31076e79e9ce62ba24eae9959598124176640dbc95f87bd10f34fcf"
        ),
        "windows-x64": (
            "a804dff0ead9fadc5293456410bcbfc32bf024be9c4513459663fb7b442d2341"
        ),
    }


def test_pyinstaller_and_native_ci_package_generated_policy_artifacts() -> None:
    for name in (
        "THIRD_PARTY_NOTICES_RUNTIME.md",
        "WebJam-runtime-sbom.cdx.json",
        "runtime-dependency-policy.json",
        "SOUNDFILE_LICENSE.txt",
        "SOUNDFILE_WHEEL_LICENSE_NOTES.md",
        "OPENSSL_LICENSE.txt",
        "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt",
        "CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt",
    ):
        assert name in SPEC
    assert "tools/runtime_dependency_policy.py --check" in CI
    assert "tools/runtime_dependency_policy.py" in CI
    assert '--verify-bundle "$dependency_bundle_root"' in CI
    assert '--target "${{ matrix.target }}"' in CI
    assert 'Source: "..\\..\\THIRD_PARTY_NOTICES_RUNTIME.md"' in WINDOWS_INSTALLER
    assert 'DestName: "WebJam-runtime-sbom.cdx.json"' in WINDOWS_INSTALLER
    for path in (
        "THIRD_PARTY_NOTICES_RUNTIME.md",
        "packaging/WebJam-runtime-sbom.cdx.json",
        "packaging/runtime-dependency-policy.json",
        "licenses/SOUNDFILE_LICENSE.txt",
        "licenses/SOUNDFILE_WHEEL_LICENSE_NOTES.md",
        "licenses/OPENSSL_LICENSE.txt",
        "packaging/macos/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt",
        "packaging/CRYPTOGRAPHY-UPSTREAM-WHEEL-PROVENANCE.txt",
    ):
        assert f"{path} text eol=lf" in ATTRIBUTES

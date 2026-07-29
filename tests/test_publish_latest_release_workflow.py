"""Static fail-closed contracts for verified GitHub release promotion."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-latest-release.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
VERIFIER_LOCK = (
    ROOT / "requirements-lock" / "component-catalog-verifier-linux-x64.txt"
).read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)


def test_latest_promotion_is_manual_serialized_and_permission_bounded() -> None:
    assert "workflow_dispatch:" in WORKFLOW
    assert "tag:" in WORKFLOW
    assert "contents: write" in WORKFLOW
    assert "publish-webjam-release" in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW
    assert "environment:\n      name: release-latest" in WORKFLOW
    assert "schedule:" not in WORKFLOW
    assert "push:" not in WORKFLOW


def test_latest_promotion_requires_matching_immutable_semver_tag() -> None:
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in WORKFLOW
    assert 'git cat-file -t "$tag_ref"' in WORKFLOW
    assert '!= "tag"' in WORKFLOW
    assert 'tag_object="$(git rev-parse "$tag_ref")"' in WORKFLOW
    assert 'tag_commit="$(git rev-parse "${tag_ref}^{commit}")"' in WORKFLOW
    assert "refs/remotes/origin/master" in WORKFLOW
    assert 'if [[ "$tag_commit" != "$master_commit" ]]' in WORKFLOW
    assert 'git checkout --detach "$tag_commit"' in WORKFLOW
    assert '[[ "$(git rev-parse HEAD)" == "$tag_commit" ]]' in WORKFLOW
    assert '[[ "$(git rev-parse HEAD)" == "$EXPECTED_TAG_COMMIT" ]]' in WORKFLOW
    assert 'git show "${tag_commit}:webjam_qt/__init__.py"' in WORKFLOW
    assert '[[ "$tag" == "v${packaged_version}" ]]' in WORKFLOW
    assert "remote_tag_object" in WORKFLOW
    assert "EXPECTED_TAG_OBJECT" in WORKFLOW
    assert "EXPECTED_TAG_COMMIT" in WORKFLOW
    assert "EXPECTED_MASTER_COMMIT" in WORKFLOW


def test_latest_promotion_accepts_only_an_unpublished_candidate_draft() -> None:
    assert "Require one unpublished non-prerelease draft" in WORKFLOW
    assert "--paginate" in WORKFLOW
    assert "--slurp" in WORKFLOW
    assert "repos/$GITHUB_REPOSITORY/releases?per_page=100" in WORKFLOW
    assert "[ .[][] | select(.tag_name == $tag) ] as $matches" in WORKFLOW
    assert "($matches | length) != 1" in WORKFLOW
    assert "$matches[0].draft != true" in WORKFLOW
    assert "$matches[0].prerelease != false" in WORKFLOW
    assert "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" not in WORKFLOW


def test_latest_promotion_verifies_exact_eight_asset_inventory_and_checksums() -> None:
    required = {
        "WebJam-linux-x64.zip",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "WebJam-v${VERSION}-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-v${VERSION}-macos-x64-ADHOC-TEST-ONLY.dmg",
        "WebJam-v${VERSION}-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "WebJam-v${VERSION}-SHA256SUMS.txt",
    }
    for name in required:
        assert f'"{name}"' in WORKFLOW
    assert "find . -maxdepth 1 -type f" in WORKFLOW
    assert "find . -maxdepth 1 -type f -size +0c" not in WORKFLOW
    assert 'for asset in "${expected[@]}"; do' in WORKFLOW
    assert '[[ -s "$asset" ]]' in WORKFLOW
    assert '[[ "$(wc -l < "$checksum_file")" -eq 7 ]]' in WORKFLOW
    assert 'printf \'%s\\n\' "${expected[@]:0:7}"' in WORKFLOW
    assert "checksummed" in WORKFLOW
    assert 'sha256sum --check --strict "$checksum_file"' in WORKFLOW
    assert 'sha256sum -- "${expected[@]}"' in WORKFLOW
    assert "release-assets-before.sha256" in WORKFLOW


def test_latest_promotion_sets_and_then_independently_verifies_latest() -> None:
    assert "Revalidate identities and publish exact draft as Latest" in WORKFLOW
    assert "--method PATCH" in WORKFLOW
    assert '"repos/$GITHUB_REPOSITORY/releases/$release_id"' in WORKFLOW
    assert "-F draft=false" in WORKFLOW
    assert "-F prerelease=false" in WORKFLOW
    assert "-f make_latest=true" in WORKFLOW
    assert "repos/$GITHUB_REPOSITORY/releases/latest" in WORKFLOW
    assert ".tag_name == $tag and .draft == false and .prerelease == false" in WORKFLOW
    assert "{id,name,size,digest}" in WORKFLOW
    assert "published-release-assets" in WORKFLOW
    assert "sha256sum --check --strict" in WORKFLOW
    assert '"$RUNNER_TEMP/release-assets-before.sha256"' in WORKFLOW


def test_latest_promotion_requires_the_live_signed_component_catalog() -> None:
    assert "Require live signed Jamulus catalog before desktop promotion" in WORKFLOW
    assert 'component_tag="jamulus-components-v1"' in WORKFLOW
    assert 'component_asset="WebJam-Jamulus-components-v1.json"' in WORKFLOW
    assert "$matches[0].draft != false" in WORKFLOW
    assert "$matches[0].prerelease != true" in WORKFLOW
    assert "$matches[0].assets[0].size <= 0" in WORKFLOW
    assert "tools.verify_jamulus_component_catalog" in WORKFLOW
    assert "--webjam-version \"$VERSION\"" in WORKFLOW
    assert "component-catalog-before.sha256" in WORKFLOW
    assert "component-release-immediately-before.json" in WORKFLOW
    assert 'component_tag_ref="refs/webjam-component-tags/$component_tag"' in (
        WORKFLOW
    )
    assert 'component_tag_type="$(git cat-file -t "$component_tag_ref")"' in WORKFLOW
    assert '"$component_tag_type" != "commit"' in WORKFLOW
    assert '"$component_tag_type" != "tag"' in WORKFLOW
    assert 'component_tag_commit="$(git rev-parse "${component_tag_ref}^{commit}")"' in (
        WORKFLOW
    )
    assert '"$component_tag_commit" != "$EXPECTED_DESKTOP_COMMIT"' in WORKFLOW
    assert "EXPECTED_COMPONENT_TAG_OBJECT" in WORKFLOW
    assert "EXPECTED_COMPONENT_TAG_COMMIT" in WORKFLOW
    assert '"$EXPECTED_COMPONENT_TAG_COMMIT" == "$EXPECTED_TAG_COMMIT"' in WORKFLOW


def test_catalog_verifier_uses_only_its_minimal_hash_locked_environment() -> None:
    assert 'python-version: "3.12"' in WORKFLOW
    assert "component-catalog-verifier-linux-x64.txt" in WORKFLOW
    assert "--require-hashes" in WORKFLOW
    assert "--only-binary=:all:" in WORKFLOW
    assert "--no-deps" in WORKFLOW
    assert "python -m pip check" in WORKFLOW
    assert 'pip install --disable-pip-version-check "cryptography' not in WORKFLOW
    assert re.findall(r"(?m)^([a-z0-9-]+)==([^ ]+) [\\]$", VERIFIER_LOCK) == [
        ("cffi", "2.1.0"),
        ("cryptography", "48.0.1"),
        ("pycparser", "3.0"),
    ]
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", VERIFIER_LOCK)
    assert hashes == [
        "1e9f50d192a3e525b15a75ab5114e442d83d657b7ec29182a991bc9a88fd3a66",
        "3752f2dbc8f07a30aad2932c986cea495b03bb554887828225da104f732852b6",
        "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
    ]


def test_runbook_requires_the_verified_latest_promotion_gate() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "Publish Verified WebJam Release" in RUNBOOK
    assert "seven packages plus" in RUNBOOK
    assert "checksum manifest" in RUNBOOK
    assert "/releases/latest" in RUNBOOK
    assert "Never publish the draft directly from the web page" in normalized

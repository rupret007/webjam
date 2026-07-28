"""Static fail-closed contracts for verified GitHub release promotion."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-latest-release.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
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
    assert 'git rev-parse --verify "${tag}^{commit}"' in WORKFLOW
    assert 'git show "${tag}:webjam_qt/__init__.py"' in WORKFLOW
    assert '[[ "$tag" == "v${packaged_version}" ]]' in WORKFLOW


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
    assert "find . -maxdepth 1 -type f -size +0c" in WORKFLOW
    assert '[[ "$(wc -l < "$checksum_file")" -eq 7 ]]' in WORKFLOW
    assert 'printf \'%s\\n\' "${expected[@]:0:7}"' in WORKFLOW
    assert "checksummed" in WORKFLOW
    assert 'sha256sum --check --strict "$checksum_file"' in WORKFLOW


def test_latest_promotion_sets_and_then_independently_verifies_latest() -> None:
    assert 'gh release edit "$TAG"' in WORKFLOW
    assert "--verify-tag" in WORKFLOW
    assert "--draft=false" in WORKFLOW
    assert "--prerelease=false" in WORKFLOW
    assert "--latest" in WORKFLOW
    assert "repos/$GITHUB_REPOSITORY/releases/latest" in WORKFLOW
    assert ".tag_name == $tag and .draft == false and .prerelease == false" in WORKFLOW
    assert "before_assets=" in WORKFLOW
    assert "latest_assets=" in WORKFLOW


def test_runbook_requires_the_verified_latest_promotion_gate() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "Publish Verified WebJam Release" in RUNBOOK
    assert "seven packages plus" in RUNBOOK
    assert "checksum manifest" in RUNBOOK
    assert "/releases/latest" in RUNBOOK
    assert "Never publish the draft directly from the web page" in normalized

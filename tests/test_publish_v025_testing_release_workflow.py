"""Static fail-closed contract for the exact v0.25.0 testing release."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "publish-v025-testing-release.yml"
).read_text(encoding="utf-8")
HEADER, JOBS = WORKFLOW.split("\njobs:\n", maxsplit=1)
PROOF, PUBLISH = JOBS.split("\n  publish-latest:\n", maxsplit=1)
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")


def test_lane_is_manual_exact_version_and_serialized() -> None:
    assert "workflow_dispatch:" in HEADER
    assert "push:" not in HEADER
    assert "schedule:" not in HEADER
    assert "EXPECTED_TAG: v0.25.0" in HEADER
    assert "EXPECTED_VERSION: 0.25.0" in HEADER
    assert "publish-webjam-release" in HEADER
    assert "cancel-in-progress: false" in HEADER
    assert "permissions:\n  contents: read\n  actions: read" in HEADER
    assert '[[ "$REQUESTED_TAG" == "$EXPECTED_TAG" ]]' in PROOF
    assert '[[ "$GITHUB_REF" == "refs/heads/master" ]]' in PROOF
    assert '[[ "$packaged_version" == "$EXPECTED_VERSION" ]]' in PROOF
    assert "UNSET_POST_TAG_" not in WORKFLOW
    assert "if: ${{ false }}" not in WORKFLOW


def test_lane_pins_annotated_tag_and_requires_descendant_master() -> None:
    assert "PINNED_TAG_OBJECT: 004549d59af9020da886df29b26ed71f646d09b8" in HEADER
    assert "PINNED_TAG_COMMIT: 251aa4ce8e936e021eeba50e28a297fbe5a8a765" in HEADER
    assert 'git cat-file -t "$tag_ref"' in PROOF
    assert 'tag_object="$(git rev-parse "$tag_ref")"' in PROOF
    assert 'tag_commit="$(git rev-parse "${tag_ref}^{commit}")"' in PROOF
    assert '[[ "$tag_object" == "$PINNED_TAG_OBJECT" ]]' in PROOF
    assert '[[ "$tag_commit" == "$PINNED_TAG_COMMIT" ]]' in PROOF
    assert 'git merge-base --is-ancestor "$tag_commit" "$master_commit"' in PROOF
    assert "git ls-remote --refs origin" in PROOF
    assert '[[ "$(git rev-parse HEAD)" == "$master_commit" ]]' in PROOF
    assert "persist-credentials: false" in PROOF
    assert "persist-credentials: false" in PUBLISH


def test_lane_proves_old_catalog_valid_then_rejects_it_for_v025() -> None:
    assert "V0225_TAG_OBJECT: 88d48b518c582fdc219efa8d62bf996b625372df" in HEADER
    assert "V0225_TAG_COMMIT: d7d0039759e8334407fe2e6ed9e42edf0d7ef639" in HEADER
    assert "SEALED_COMPONENT_TAG: jamulus-components-v3" in HEADER
    assert "SEALED_COMPONENT_SEQUENCE: 6" in HEADER
    assert "requirements-lock/component-catalog-verifier-linux-x64.txt" in PROOF
    assert 'git archive "$V0225_TAG_COMMIT"' in PROOF
    assert 'cd "$historical_source"' in PROOF
    assert "--webjam-version 0.22.5" in PROOF
    assert '--webjam-version "$EXPECTED_VERSION"' in PROOF
    assert "unexpectedly authorized v0.25.0" in PROOF
    assert 'EMBEDDED_FALLBACK_VERSION = "3.12.2"' in PROOF
    assert "official_jamulus_compatibility_registry" in PROOF
    assert '{"3.12.2", "3.12.3"}.issubset(versions)' in PROOF
    assert "COMPONENT_CHANNEL_TAG: jamulus-components-v3" not in PUBLISH


def test_read_only_lane_binds_the_unique_successful_tag_ci() -> None:
    assert "PINNED_TAG_CI_RUN_ID: 31879936789" in HEADER
    assert "expected one unique successful v0.25.0 tag CI run" in PROOF
    assert '.path == ".github/workflows/ci.yml"' in PROOF
    assert ".head_branch == $tag" in PROOF
    assert ".head_sha == $commit" in PROOF
    assert '.conclusion == "success"' in PROOF
    assert '--argjson run_id "$PINNED_TAG_CI_RUN_ID"' in PROOF
    assert ".id == $run_id" not in PROOF
    assert "elif $matches[0].id != $run_id then" in PROOF
    assert "successful v0.25.0 tag CI run ID changed" in PROOF
    assert '[[ "$tag_ci_run_id" == "$PINNED_TAG_CI_RUN_ID" ]]' in PROOF
    assert "repos/$GITHUB_REPOSITORY/releases" not in PROOF


def test_protected_lane_binds_draft_and_all_exact_assets() -> None:
    assert "PINNED_RELEASE_ID: 371028390" in HEADER
    assert (
        "PINNED_INVENTORY_SHA256: "
        "4afae8ce6f9df58e7ce153756cabfafdaa7258ca0680f741315500d69962e917" in HEADER
    )
    assert (
        "PINNED_BODY_SHA256: "
        "f4d83872e4ea482dcb4c0bc330675b8e14de70304bfe8086e1bfd9c5d42dd5bd" in HEADER
    )
    for asset in (
        "WebJam-linux-x64.zip",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
        "windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "SHA256SUMS.txt",
    ):
        assert asset in PUBLISH
    assert '(.digest | test("^sha256:[0-9a-f]{64}$") | not)' in PUBLISH
    assert "sha256sum --check --strict" in PUBLISH
    assert "PINNED_INVENTORY_SHA256" in PUBLISH
    assert PUBLISH.count("PINNED_BODY_SHA256") == 3


def test_only_protected_publish_job_can_write_and_make_latest() -> None:
    assert "permissions:\n      contents: read\n      actions: read" in PROOF
    assert "permissions:\n      contents: write" in PUBLISH
    assert "environment:\n      name: release-latest" in PUBLISH
    assert "environment:\n      name: release-latest" not in PROOF
    assert "-F draft=false" in PUBLISH
    assert "-F prerelease=false" in PUBLISH
    assert "-f make_latest=true" in PUBLISH
    assert ".immutable == true" in PUBLISH
    assert "Redownload and verify GitHub Latest identity and bytes" in PUBLISH
    assert PUBLISH.count("sha256sum --check --strict") == 2


def test_tag_draft_notes_describe_fallback_without_false_catalog_claim() -> None:
    assert "keeps the reviewed embedded Jamulus 3.12.2" in CI
    assert "that exactly authorizes this WebJam version" in CI
    assert "Jamulus 3.12.3 updates are authorized" not in CI


def test_runbook_records_pinned_prepublication_boundary() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "v0.25.0 pinned promotion status" in RUNBOOK
    assert "GitHub Latest remains v0.24.0" in normalized
    assert "release-latest" in RUNBOOK
    for value in (
        "004549d59af9020da886df29b26ed71f646d09b8",
        "251aa4ce8e936e021eeba50e28a297fbe5a8a765",
        "31879936789",
        "371028390",
        "f4d83872e4ea482dcb4c0bc330675b8e14de70304bfe8086e1bfd9c5d42dd5bd",
        "4afae8ce6f9df58e7ce153756cabfafdaa7258ca0680f741315500d69962e917",
    ):
        assert value in RUNBOOK
    assert "still an unpublished draft" in normalized

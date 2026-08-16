"""Static fail-closed contract for the exact v0.26.0 testing release."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "publish-v026-testing-release.yml"
).read_text(encoding="utf-8")
HEADER, JOBS = WORKFLOW.split("\njobs:\n", maxsplit=1)
PROOF, PUBLISH = JOBS.split("\n  publish-latest:\n", maxsplit=1)
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")


def test_lane_is_manual_exact_version_and_serialized() -> None:
    assert "workflow_dispatch:" in HEADER
    assert "push:" not in HEADER
    assert "schedule:" not in HEADER
    assert "EXPECTED_TAG: v0.26.0" in HEADER
    assert "EXPECTED_VERSION: 0.26.0" in HEADER
    assert "publish-webjam-release" in HEADER
    assert "cancel-in-progress: false" in HEADER
    assert "permissions:\n  contents: read\n  actions: read" in HEADER
    assert '[[ "$REQUESTED_TAG" == "$EXPECTED_TAG" ]]' in PROOF
    assert '[[ "$GITHUB_REF" == "refs/heads/master" ]]' in PROOF
    assert '[[ "$packaged_version" == "$EXPECTED_VERSION" ]]' in PROOF
    assert "UNSET_POST_TAG_" not in WORKFLOW
    assert "if: ${{ false }}" not in WORKFLOW
    assert "Placeholder (INERT)" not in WORKFLOW


def test_lane_pins_annotated_tag_and_exact_tagged_source() -> None:
    assert "PINNED_TAG_OBJECT: 3989baadaaa00b4655115e23cf900ea2c1c7fd4c" in HEADER
    assert "PINNED_TAG_COMMIT: 4b5208098981943df8ddaf1fac31aa36c15146bb" in HEADER
    assert 'git cat-file -t "$tag_ref"' in PROOF
    assert 'tag_object="$(git rev-parse "$tag_ref")"' in PROOF
    assert 'tag_commit="$(git rev-parse "${tag_ref}^{commit}")"' in PROOF
    assert '[[ "$tag_object" == "$PINNED_TAG_OBJECT" ]]' in PROOF
    assert '[[ "$tag_commit" == "$PINNED_TAG_COMMIT" ]]' in PROOF
    assert 'git merge-base --is-ancestor "$tag_commit" "$master_commit"' in PROOF
    assert "git ls-remote --refs origin" in PROOF
    assert '[[ "$(git rev-parse HEAD)" == "$master_commit" ]]' in PROOF
    assert 'git archive "$tag_commit" | tar -x -C "$tagged_source"' in PROOF
    assert '"$tagged_source/webjam_qt/__init__.py"' in PROOF
    assert "persist-credentials: false" in PROOF
    assert "persist-credentials: false" in PUBLISH


def test_read_only_proof_rejects_old_catalog_for_exact_tag_source() -> None:
    assert "V0225_TAG_OBJECT: 88d48b518c582fdc219efa8d62bf996b625372df" in HEADER
    assert "V0225_TAG_COMMIT: d7d0039759e8334407fe2e6ed9e42edf0d7ef639" in HEADER
    assert "SEALED_COMPONENT_TAG: jamulus-components-v3" in HEADER
    assert "SEALED_COMPONENT_SEQUENCE: 6" in HEADER
    assert 'git archive "$V0225_TAG_COMMIT"' in PROOF
    assert '--webjam-version "$EXPECTED_VERSION"' in PROOF
    assert "unexpectedly authorized v0.26.0" in PROOF
    assert 'EMBEDDED_FALLBACK_VERSION = "3.12.2"' in PROOF
    assert 'cd "$tagged_source"' in PROOF
    assert "official_jamulus_compatibility_registry" in PROOF
    assert '{"3.12.2", "3.12.3"}.issubset(versions)' in PROOF
    assert "COMPONENT_CHANNEL_TAG: jamulus-components-v3" not in PUBLISH


def test_read_only_proof_binds_unique_successful_tag_ci_attempt() -> None:
    assert "PINNED_TAG_CI_RUN_ID: 31973256062" in HEADER
    assert "PINNED_TAG_CI_RUN_ATTEMPT: 1" in HEADER
    assert "expected one unique successful v0.26.0 tag CI run" in PROOF
    assert '.path == ".github/workflows/ci.yml"' in PROOF
    assert ".head_branch == $tag" in PROOF
    assert ".head_sha == $commit" in PROOF
    assert ".head_repository.full_name == $repo" in PROOF
    assert ".repository.full_name == $repo" in PROOF
    assert '.conclusion == "success"' in PROOF
    assert "elif $matches[0].id != $run_id then" in PROOF
    assert "elif $matches[0].run_attempt != $run_attempt then" in PROOF
    assert "successful v0.26.0 tag CI attempt changed" in PROOF
    assert '[[ "$tag_ci_run_id" == "$PINNED_TAG_CI_RUN_ID" ]]' in PROOF
    assert '[[ "$tag_ci_run_attempt" == "$PINNED_TAG_CI_RUN_ATTEMPT" ]]' in PROOF
    assert "repos/$GITHUB_REPOSITORY/releases" not in PROOF
    assert "permissions:\n      contents: read\n      actions: read" in PROOF


def test_protected_lane_pins_draft_body_and_all_exact_assets() -> None:
    assert "PINNED_RELEASE_ID: 371442375" in HEADER
    assert (
        "PINNED_INVENTORY_SHA256: "
        "e6c49c6568877961ce484fa9dc477d8939c8bf881dfd568497da5752199d3aa3" in HEADER
    )
    assert (
        "PINNED_BODY_SHA256: "
        "404c5378017a37df6c5813d39348d16c386492a7acccd23797a3659495dea4da" in HEADER
    )
    assert (
        "PINNED_CHECKSUM_SHA256: "
        "c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e" in HEADER
    )
    expected_assets = {
        517251779: (
            "WebJam-linux-x64.zip",
            168211648,
            "9b7216fa8591de0edb5e34dc45bb0b1a59e413bf9572c8e7c6c3c018ef72082e",
        ),
        517251778: (
            "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
            216225400,
            "9c92fa23ba334166b5d3fac6f26965d3a59519af6707f3f7fb5c2abdca04a80b",
        ),
        517251781: (
            "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
            222536890,
            "e3d3a1875cedcd232fba6ed4ba22d99e8016d6bd717736f4b66c9757c3691da3",
        ),
        517251780: (
            "WebJam-v0.26.0-macos-arm64-ADHOC-TEST-ONLY.dmg",
            217302612,
            "92ea140b1f5f820cae525f35b76e68af7c3d8a8d4fb330f200a3c40ec6659163",
        ),
        517251786: (
            "WebJam-v0.26.0-macos-x64-ADHOC-TEST-ONLY.dmg",
            223532070,
            "043339f5f45858ab7eec0df0a884a50acd841056103303e320108f2f8b9abbe7",
        ),
        517251782: (
            "WebJam-v0.26.0-SHA256SUMS.txt",
            749,
            "c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e",
        ),
        517251783: (
            "WebJam-v0.26.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
            144846325,
            "a3ec7711500836ced1bd0168107c441ef88681f1d48f770e31188cc9ed01b03d",
        ),
        517251787: (
            "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
            165555420,
            "0a1df1d8868e3b687824b84ff0bf75af2d1b07ba4fdb2bc0e0870e530658df32",
        ),
    }
    for asset_id, (name, size, digest) in expected_assets.items():
        assert f'{{"id":{asset_id},"name":"{name}","size":{size}' in HEADER
        assert f'"digest":"sha256:{digest}"' in HEADER
    assert 'actual_inventory" == "$expected_inventory' in PUBLISH
    assert PUBLISH.count('actual_inventory" == "$expected_inventory') == 3
    assert 'final_actual_inventory" == "$final_expected_inventory' in PUBLISH
    assert '(.digest | test("^sha256:[0-9a-f]{64}$") | not)' in PUBLISH
    assert 'contains("NOT RUN")' in PUBLISH
    assert 'target_commitish != "master"' in PUBLISH
    assert "published_at != null" in PUBLISH


def test_lane_refuses_promotion_unless_latest_is_exact_immutable_v025() -> None:
    for marker in (
        "PREVIOUS_LATEST_TAG: v0.25.0",
        "PREVIOUS_LATEST_RELEASE_ID: 371028390",
        'PREVIOUS_LATEST_PUBLISHED_AT: "2026-08-15T11:45:43Z"',
        "PREVIOUS_LATEST_INVENTORY_SHA256: "
        "4afae8ce6f9df58e7ce153756cabfafdaa7258ca0680f741315500d69962e917",
        "PREVIOUS_LATEST_BODY_SHA256: "
        "f4d83872e4ea482dcb4c0bc330675b8e14de70304bfe8086e1bfd9c5d42dd5bd",
    ):
        assert marker in HEADER
    assert '"repos/$GITHUB_REPOSITORY/releases/latest"' in PUBLISH
    assert "and .tag_name == $tag" in PUBLISH
    assert "and .immutable == true" in PUBLISH
    assert "and .published_at == $published_at" in PUBLISH
    assert '"$previous_body_sha256" == "$PREVIOUS_LATEST_BODY_SHA256"' in PUBLISH
    assert "PREVIOUS_LATEST_INVENTORY_SHA256" in PUBLISH


def test_only_protected_job_can_publish_and_cannot_mutate_assets() -> None:
    assert WORKFLOW.count("contents: write") == 1
    assert "permissions:\n      contents: write\n      actions: read" in PUBLISH
    assert "environment:\n      name: release-latest" in PUBLISH
    assert "environment:\n      name: release-latest" not in PROOF
    assert WORKFLOW.count("--method PATCH") == 1
    assert "-F draft=false" in PUBLISH
    assert "-F prerelease=false" in PUBLISH
    assert "-f make_latest=true" in PUBLISH
    for forbidden in (
        "--method POST",
        "--method DELETE",
        "gh release",
        "uploads.github.com",
        "upload_url",
        "delete-asset",
    ):
        assert forbidden not in WORKFLOW


def test_final_mutable_identity_guard_is_adjacent_to_the_only_patch() -> None:
    byte_gate = PUBLISH.index('sha256sum --check --strict "$checksum"')
    publish_step = PUBLISH.index(
        "- name: Publish immutable v0.26.0 private test candidate as Latest"
    )
    patch = PUBLISH.index("--method PATCH")
    assert byte_gate < publish_step < patch
    final_guard = PUBLISH[publish_step:patch]
    assert (
        final_guard.index("final-tag-ci-pages.json")
        < final_guard.index("final-previous-latest.json")
        < final_guard.index("remote_master_commit=")
        < final_guard.index("final-draft.json")
        < final_guard.index('[[ "$final_inventory_sha256" ==')
    )
    final_previous = final_guard[final_guard.index("final-previous-latest.json") :]
    final_draft = final_guard[final_guard.index("final-draft.json") :]
    for marker in (
        '[[ "$GITHUB_REF" == "refs/heads/master" ]]',
        'remote_master_commit="$(git ls-remote --refs origin',
        '[[ "$remote_master_commit" == "$EXPECTED_MASTER_COMMIT" ]]',
        'final_tag_ref="refs/webjam-final-release-tags/$EXPECTED_TAG"',
        'git cat-file -t "$final_tag_ref"',
        'git rev-parse "${final_tag_ref}^{commit}"',
        '"$RUNNER_TEMP/final-tag-ci-pages.json"',
        "($matches | length) == 1",
        "$matches[0].id == $run_id",
        "$matches[0].run_attempt == $run_attempt",
        '"repos/$GITHUB_REPOSITORY/releases/$PINNED_RELEASE_ID"',
        '"$RUNNER_TEMP/final-draft.json"',
        '"$final_body_sha256" == "$PINNED_BODY_SHA256"',
        '"$final_actual_inventory" == "$final_expected_inventory"',
        '"$final_inventory_sha256" == "$PINNED_INVENTORY_SHA256"',
    ):
        assert marker in final_guard
    for marker in (
        ".id == $release_id",
        ".tag_name == $tag",
        ".draft == false",
        ".prerelease == false",
        ".immutable == true",
        ".published_at == $published_at",
        '"$final_previous_body_sha256" ==',
        '"$PREVIOUS_LATEST_BODY_SHA256"',
        '"$final_previous_inventory_sha256" ==',
        '"$PREVIOUS_LATEST_INVENTORY_SHA256"',
    ):
        assert marker in final_previous
    for marker in (
        ".id == $release_id",
        ".tag_name == $tag",
        '.target_commitish == "master"',
        ".name == $expected_name",
        ".draft == true",
        ".prerelease == false",
        ".immutable == false",
        ".published_at == null",
        "(.assets | length) == 8",
        '(.digest | test("^sha256:[0-9a-f]{64}$"))',
        '.state == "uploaded"',
    ):
        assert marker in final_draft
    assert "releases/assets/$asset_id" not in final_guard
    assert "sha256sum --check --strict" not in final_guard
    assert "--method POST" not in final_guard
    assert "--method DELETE" not in final_guard
    final_write_boundary = final_guard.split(
        '[[ "$final_inventory_sha256" == "$PINNED_INVENTORY_SHA256" ]]',
        maxsplit=1,
    )[1]
    assert final_write_boundary.strip() == "gh api \\"


def test_publication_and_latest_revalidate_exact_immutable_bytes() -> None:
    assert "Publish immutable v0.26.0 private test candidate as Latest" in PUBLISH
    assert "Redownload and verify immutable GitHub Latest identity and bytes" in PUBLISH
    assert PUBLISH.count("PINNED_BODY_SHA256") == 4
    assert PUBLISH.count("PINNED_INVENTORY_SHA256") == 4
    assert PUBLISH.count("sha256sum --check --strict") == 2
    assert PUBLISH.count("and .immutable == true") == 4
    assert PUBLISH.count("and .published_at != null") == 2
    assert PUBLISH.count('"repos/$GITHUB_REPOSITORY/releases/assets/$asset_id"') == 2
    assert PUBLISH.count("PINNED_CHECKSUM_SHA256") == 3
    assert PUBLISH.count("actions/workflows/ci.yml/runs?") == 2
    assert "$matches[0].run_attempt == $run_attempt" in PUBLISH


def test_tag_draft_notes_describe_fallback_without_false_catalog_claim() -> None:
    assert "keeps the reviewed embedded Jamulus 3.12.2" in CI
    assert "that exactly authorizes this WebJam version" in CI
    assert "Jamulus 3.12.3 updates are authorized" not in CI


def test_runbook_records_pinned_unpublished_v026_boundary() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "v0.26.0 pinned promotion status" in RUNBOOK
    assert "Immutable v0.25.0 remains GitHub **Latest**" in normalized
    assert "release-latest" in RUNBOOK
    for value in (
        "3989baadaaa00b4655115e23cf900ea2c1c7fd4c",
        "4b5208098981943df8ddaf1fac31aa36c15146bb",
        "31973256062",
        "371442375",
        "404c5378017a37df6c5813d39348d16c386492a7acccd23797a3659495dea4da",
        "e6c49c6568877961ce484fa9dc477d8939c8bf881dfd568497da5752199d3aa3",
        "c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e",
    ):
        assert value in RUNBOOK
    assert "still an unpublished draft" in normalized
    assert "all rows remain **NOT RUN**" in normalized
    assert "the same shell" in normalized
    assert "immediately before the PATCH" in normalized

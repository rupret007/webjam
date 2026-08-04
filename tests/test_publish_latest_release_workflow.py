"""Static fail-closed contracts for verified GitHub release promotion."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-latest-release.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW_HEADER, JOBS = WORKFLOW.split("\njobs:\n", maxsplit=1)
SMOKE_JOB, PUBLISH_JOB = JOBS.split("\n  publish-latest:\n", maxsplit=1)
VERIFIER_LOCK = (
    ROOT / "requirements-lock" / "component-catalog-verifier-linux-x64.txt"
).read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)


def test_latest_promotion_is_manual_serialized_and_permission_bounded() -> None:
    assert "workflow_dispatch:" in WORKFLOW_HEADER
    assert "tag:" in WORKFLOW_HEADER
    assert "permissions:\n  contents: read" in WORKFLOW_HEADER
    assert "publish-webjam-release" in WORKFLOW_HEADER
    assert "cancel-in-progress: false" in WORKFLOW_HEADER
    assert "schedule:" not in WORKFLOW_HEADER
    assert "push:" not in WORKFLOW_HEADER

    assert "  frozen-package-smoke:" in JOBS
    assert "    permissions:\n      contents: read" in SMOKE_JOB
    assert "      actions: read" in SMOKE_JOB
    assert "    needs: frozen-package-smoke" in PUBLISH_JOB
    assert "    permissions:\n      contents: write" in PUBLISH_JOB
    assert "      actions: read" not in PUBLISH_JOB
    assert "environment:\n      name: release-latest" not in SMOKE_JOB
    assert "environment:\n      name: release-latest" in PUBLISH_JOB
    assert WORKFLOW.count("persist-credentials: false") == 2
    assert "persist-credentials: true" not in WORKFLOW


def test_latest_promotion_accepts_only_an_exact_version_tag_from_master() -> None:
    strict_tag = (
        r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\."
        r"(0|[1-9][0-9]*)$"
    )
    assert strict_tag in SMOKE_JOB
    assert strict_tag in PUBLISH_JOB
    assert '[[ "$tag" == "v${packaged_version}" ]]' in SMOKE_JOB
    assert '[[ "$REQUESTED_TAG" == "$TAG" ]]' in PUBLISH_JOB
    assert '[[ "$TAG" == "v${VERSION}" ]]' in PUBLISH_JOB
    assert 'if [[ "$GITHUB_REF" != "refs/heads/master" ]]' in SMOKE_JOB
    assert '[[ "$GITHUB_REF" == "refs/heads/master" ]]' in PUBLISH_JOB
    assert "EXPECTED_PROMOTION_TAG" not in WORKFLOW
    assert "v0.22.1 only" not in WORKFLOW


def test_latest_promotion_requires_matching_immutable_annotated_tag() -> None:
    assert 'git cat-file -t "$tag_ref"' in SMOKE_JOB
    assert '!= "tag"' in SMOKE_JOB
    assert 'tag_object="$(git rev-parse "$tag_ref")"' in SMOKE_JOB
    assert 'tag_commit="$(git rev-parse "${tag_ref}^{commit}")"' in SMOKE_JOB
    assert "refs/remotes/origin/master" in SMOKE_JOB
    assert 'if [[ "$tag_commit" != "$master_commit" ]]' in SMOKE_JOB
    assert 'git checkout --detach "$tag_commit"' in SMOKE_JOB
    assert '[[ "$(git rev-parse HEAD)" == "$tag_commit" ]]' in SMOKE_JOB
    assert 'git show "${tag_commit}:webjam_qt/__init__.py"' in SMOKE_JOB
    assert '[[ "$tag" == "v${packaged_version}" ]]' in SMOKE_JOB
    assert "remote_tag_object" in SMOKE_JOB

    for output in (
        "tag_object",
        "tag_commit",
        "master_commit",
        "workflow_commit",
    ):
        assert f"needs['frozen-package-smoke'].outputs.{output}" in PUBLISH_JOB
    assert '[[ "$(git rev-parse HEAD)" == "$EXPECTED_TAG_COMMIT" ]]' in (
        PUBLISH_JOB
    )
    assert '"$EXPECTED_TAG_COMMIT" == "$EXPECTED_MASTER_COMMIT"' in PUBLISH_JOB
    assert '"$EXPECTED_WORKFLOW_COMMIT" == "$EXPECTED_MASTER_COMMIT"' in (
        PUBLISH_JOB
    )
    assert 'if [[ "$workflow_commit" != "$master_commit" ]]' in SMOKE_JOB


def test_latest_promotion_pins_blocked_v0220_tag_object_and_commit() -> None:
    tag_object = "663075ec53aab36cc9de5d1b84aaec0b3733290b"
    tag_commit = "bf64c1165486a654d923c4e3cb6ede69e6458320"
    assert f"V0220_TAG_OBJECT: {tag_object}" in WORKFLOW_HEADER
    assert f"V0220_TAG_COMMIT: {tag_commit}" in WORKFLOW_HEADER
    for job in (SMOKE_JOB, PUBLISH_JOB):
        assert 'historical_ref="refs/webjam-historical-tags/v0.22.0"' in job
        assert 'git cat-file -t "$historical_ref"' in job
        assert '"$(git rev-parse "$historical_ref")" == "$V0220_TAG_OBJECT"' in job
        assert (
            '"$(git rev-parse "${historical_ref}^{commit}")" == \\\n'
            '            "$V0220_TAG_COMMIT"'
        ) in job
        assert "git ls-remote --refs origin refs/tags/v0.22.0" in job


def test_read_only_job_discovers_one_successful_tag_build_and_artifact_set() -> None:
    assert "Discover and bind the unique successful tag build artifacts" in SMOKE_JOB
    assert "EXPECTED_TAG_CI_RUN_ID" not in WORKFLOW_HEADER
    assert "EXPECTED_TAG_CI_WORKFLOW_ID" not in WORKFLOW_HEADER
    assert "EXPECTED_LINUX_ARTIFACT_ID" not in WORKFLOW_HEADER
    assert "EXPECTED_LINUX_ARTIFACT_DIGEST" not in WORKFLOW_HEADER
    assert "EXPECTED_LINUX_PACKAGE_SHA256" not in WORKFLOW_HEADER
    assert (
        '"repos/$GITHUB_REPOSITORY/actions/workflows/ci.yml/runs?'
        'branch=$TAG&event=push&status=success&per_page=100"'
    ) in SMOKE_JOB
    assert (
        '"repos/$GITHUB_REPOSITORY/actions/workflows/ci.yml"' in SMOKE_JOB
    )
    assert '.name == "WebJam CI"' in SMOKE_JOB
    assert '.state == "active"' in SMOKE_JOB
    assert ".workflow_id == $workflow_id" in SMOKE_JOB
    assert '[[ "$workflow_id" == "$ci_workflow_id" ]]' in SMOKE_JOB
    assert '.path == ".github/workflows/ci.yml"' in SMOKE_JOB
    assert '.event == "push"' in SMOKE_JOB
    assert ".head_branch == $tag" in SMOKE_JOB
    assert ".head_sha == $commit" in SMOKE_JOB
    assert '.conclusion == "success"' in SMOKE_JOB
    assert ".run_attempt >= 1" in SMOKE_JOB
    assert 'error("expected exactly one successful tag CI run")' in SMOKE_JOB
    assert (
        '"repos/$GITHUB_REPOSITORY/actions/runs/'
        '$run_id/artifacts?per_page=100"'
    ) in SMOKE_JOB
    for artifact in (
        "webjam-windows-x64",
        "webjam-macos-arm64",
        "webjam-macos-x64",
        "webjam-linux-x64",
    ):
        assert f'"{artifact}"' in SMOKE_JOB
    assert "([ $matches[].name ]" not in SMOKE_JOB
    assert "([$matches[].name] | unique | length)" in SMOKE_JOB
    assert "([$matches[].id] | unique | length)" in SMOKE_JOB
    assert "([$matches[].digest] | unique | length)" in SMOKE_JOB
    assert ".expired != false" in SMOKE_JOB
    assert '.digest | test("^sha256:[0-9a-f]{64}$")' in SMOKE_JOB
    assert ".workflow_run.id != $run_id" in SMOKE_JOB
    assert "release_artifact_bindings:" in SMOKE_JOB
    assert "tag_ci_run_id:" in SMOKE_JOB
    assert "tag_ci_workflow_id:" in SMOKE_JOB
    assert "tag_ci_run_attempt:" in SMOKE_JOB
    assert "$matches[0].draft != true" not in SMOKE_JOB
    assert "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" not in WORKFLOW


def test_read_only_job_binds_all_tag_packages_before_linux_smoke() -> None:
    assert "Download and bind the exact tag-build release packages" in SMOKE_JOB
    assert (
        '"repos/$GITHUB_REPOSITORY/actions/artifacts/$artifact_id/zip"'
        in SMOKE_JOB
    )
    assert '-H "Accept: application/vnd.github+json"' in SMOKE_JOB
    assert (
        '[[ "sha256:$artifact_sha256" == "$digest" ]]'
        in SMOKE_JOB
    )
    assert '[[ "$(stat -c \'%s\' "$artifact_archive")" == "$artifact_size" ]]' in (
        SMOKE_JOB
    )
    assert "set(names) != expected_names" in SMOKE_JOB
    assert "len(names) != len(set(names))" in SMOKE_JOB
    assert "stat.S_ISLNK(mode)" in SMOKE_JOB
    assert "member.file_size <= 0" in SMOKE_JOB
    assert "member.file_size > maximum_member_size" in SMOKE_JOB
    assert "total_size > maximum_artifact_size" in SMOKE_JOB
    assert "PurePosixPath(member.filename).name" in SMOKE_JOB
    assert "target.open(\"xb\")" in SMOKE_JOB
    assert 'sha256sum --check --strict "$windows_checksum"' in SMOKE_JOB
    assert "package_bindings:" in SMOKE_JOB
    assert "package_bindings_sha256:" in SMOKE_JOB
    assert "length == 7" in SMOKE_JOB
    assert "Require live catalog proof from exact frozen Linux package" in SMOKE_JOB
    assert "tests/support/run_frozen_component_catalog_smoke.py" in SMOKE_JOB
    assert (
        '--archive "$RUNNER_TEMP/frozen-packages/WebJam-linux-x64.zip"'
        in SMOKE_JOB
    )
    assert "--expected-version \"$VERSION\"" in SMOKE_JOB
    assert '--expected-sequence "$CATALOG_SEQUENCE"' in SMOKE_JOB
    assert "--expected-target linux-x64" in SMOKE_JOB
    assert "--expected-jamulus-version 3.12.3" in SMOKE_JOB
    assert "--expected-catalog-envelope-sha256" in SMOKE_JOB
    assert "--expected-catalog-payload-sha256" in SMOKE_JOB
    assert "--expected-signer-fingerprint-sha256" in SMOKE_JOB

    assert "run_frozen_component_catalog_smoke.py" not in PUBLISH_JOB
    assert "Install exact frozen-package smoke runtime" not in PUBLISH_JOB
    assert "apt-get" not in PUBLISH_JOB


def test_catalog_proof_binds_envelope_payload_signer_and_asset_identity() -> None:
    assert "Verify and bind the live signed Jamulus catalog" in SMOKE_JOB
    assert 'component_tag="jamulus-components-v1"' in SMOKE_JOB
    assert 'component_asset="WebJam-Jamulus-components-v1.json"' in SMOKE_JOB
    assert "$matches[0].draft != false" in SMOKE_JOB
    assert "$matches[0].prerelease != true" in SMOKE_JOB
    assert "$matches[0].assets[0].size <= 0" in SMOKE_JOB
    assert "tools.verify_jamulus_component_catalog" in SMOKE_JOB
    assert "--webjam-version \"$VERSION\"" in SMOKE_JOB
    assert "MINIMUM_COMPONENT_CATALOG_SEQUENCE: 4" in WORKFLOW_HEADER
    assert (
        '--minimum-sequence "$MINIMUM_COMPONENT_CATALOG_SEQUENCE"'
        in SMOKE_JOB
    )
    assert ".sequence >= $minimum" in SMOKE_JOB
    assert ".webjam_version == $version" in SMOKE_JOB
    assert ".component_count == 8" in SMOKE_JOB
    assert ".payload_sha256" in SMOKE_JOB
    assert ".signer_fingerprint_sha256" in SMOKE_JOB
    assert '"sha256:$catalog_envelope_sha256"' in SMOKE_JOB
    for output in (
        "component_asset_id",
        "component_asset_digest",
        "catalog_sequence",
        "catalog_envelope_sha256",
        "catalog_payload_sha256",
        "signer_fingerprint_sha256",
    ):
        assert f"needs['frozen-package-smoke'].outputs.{output}" in PUBLISH_JOB


def test_component_channel_tag_is_immutable_and_ancestry_bounded() -> None:
    assert 'component_tag_ref="refs/webjam-component-tags/$component_tag"' in (
        SMOKE_JOB
    )
    assert 'component_tag_type="$(git cat-file -t "$component_tag_ref")"' in (
        SMOKE_JOB
    )
    assert '"$component_tag_type" != "commit"' in SMOKE_JOB
    assert (
        'component_tag_commit="$(git rev-parse "${component_tag_ref}^{commit}")"'
        in SMOKE_JOB
    )
    anchor = "bf64c1165486a654d923c4e3cb6ede69e6458320"
    assert f"COMPONENT_CHANNEL_ANCHOR: {anchor}" in WORKFLOW_HEADER
    assert '"$component_tag_object" != "$COMPONENT_CHANNEL_ANCHOR"' in SMOKE_JOB
    assert '"$component_tag_commit" != "$COMPONENT_CHANNEL_ANCHOR"' in SMOKE_JOB
    assert (
        'git merge-base --is-ancestor \\\n'
        '            "$component_tag_commit" "$EXPECTED_DESKTOP_COMMIT"'
    ) in SMOKE_JOB
    assert (
        '"$EXPECTED_COMPONENT_TAG_OBJECT" == "$COMPONENT_CHANNEL_ANCHOR"'
        in PUBLISH_JOB
    )
    assert (
        '"$EXPECTED_COMPONENT_TAG_COMMIT" == "$COMPONENT_CHANNEL_ANCHOR"'
        in PUBLISH_JOB
    )


def test_write_job_revalidates_read_only_proof_before_publication() -> None:
    assert "Revalidate exact source identities from the read-only proof" in PUBLISH_JOB
    assert "Download and verify exact draft inventory and checksums" in PUBLISH_JOB
    assert "Reverify exact live component catalog from the read-only proof" in (
        PUBLISH_JOB
    )
    assert "Revalidate all bound identities and publish exact draft as Latest" in (
        PUBLISH_JOB
    )
    assert (
        '[[ "$linux_package_sha256" == "$EXPECTED_LINUX_PACKAGE_SHA256" ]]'
        in PUBLISH_JOB
    )
    assert (
        "EXPECTED_PACKAGE_BINDINGS: "
        "${{ needs['frozen-package-smoke'].outputs.package_bindings }}"
    ) in PUBLISH_JOB
    assert (
        '[[ "$actual_package_bindings" == "$EXPECTED_PACKAGE_BINDINGS" ]]'
        in PUBLISH_JOB
    )
    assert (
        '[[ "$bindings_sha256" == "$EXPECTED_PACKAGE_BINDINGS_SHA256" ]]'
        in PUBLISH_JOB
    )
    assert "$matches[0].draft != true" in PUBLISH_JOB
    assert "$matches[0].prerelease != false" in PUBLISH_JOB
    assert (
        '"sha256:$EXPECTED_LINUX_PACKAGE_SHA256"' in PUBLISH_JOB
    )
    assert 'echo "release_id=$release_id" >> "$GITHUB_OUTPUT"' in PUBLISH_JOB
    assert PUBLISH_JOB.count(
        "EXPECTED_RELEASE_ID: ${{ steps.draft.outputs.release_id }}"
    ) == 2
    assert "steps.draft.outputs.release_id" not in SMOKE_JOB
    assert (
        '"$catalog_envelope_sha256" == \\\n'
        '            "$EXPECTED_CATALOG_ENVELOPE_SHA256"'
    ) in PUBLISH_JOB
    assert ".payload_sha256 == $payload" in PUBLISH_JOB
    assert ".signer_fingerprint_sha256 == $signer" in PUBLISH_JOB
    assert ".sequence == $sequence" in PUBLISH_JOB
    assert PUBLISH_JOB.index(
        "Download and verify exact draft inventory and checksums"
    ) < PUBLISH_JOB.index(
        "Revalidate all bound identities and publish exact draft as Latest"
    )


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
        assert f'"{name}"' in PUBLISH_JOB
    assert "find . -maxdepth 1 -type f" in PUBLISH_JOB
    assert "find . -maxdepth 1 -type f -size +0c" not in PUBLISH_JOB
    assert PUBLISH_JOB.count('for asset in "${expected[@]}"; do') == 2
    assert '[[ -s "$asset" && ! -L "$asset" ]]' in PUBLISH_JOB
    assert '[[ "$(wc -l < "$checksum_file")" -eq 7 ]]' in PUBLISH_JOB
    assert 'printf \'%s\\n\' "${expected[@]:0:7}"' in PUBLISH_JOB
    assert "checksummed" in PUBLISH_JOB
    assert 'sha256sum --check --strict "$checksum_file"' in PUBLISH_JOB
    assert 'sha256sum -- "${expected[@]}"' in PUBLISH_JOB
    assert "release-assets-before.sha256" in PUBLISH_JOB
    assert "(.assets | length) == 8" in PUBLISH_JOB
    assert '.digest | test("^sha256:[0-9a-f]{64}$")' in PUBLISH_JOB


def test_latest_promotion_sets_and_independently_verifies_latest() -> None:
    assert "--method PATCH" in PUBLISH_JOB
    assert '"repos/$GITHUB_REPOSITORY/releases/$release_id"' in PUBLISH_JOB
    assert "-F draft=false" in PUBLISH_JOB
    assert "-F prerelease=false" in PUBLISH_JOB
    assert "-f make_latest=true" in PUBLISH_JOB
    assert "repos/$GITHUB_REPOSITORY/releases/latest" in PUBLISH_JOB
    assert ".draft == false" in PUBLISH_JOB
    assert ".prerelease == false" in PUBLISH_JOB
    assert (
        ".draft == false and .prerelease == false and .immutable == true"
        in PUBLISH_JOB
    )
    assert "release-publish-response.json" in PUBLISH_JOB
    assert PUBLISH_JOB.count(".immutable == true") == 2
    assert "{id,name,size,digest}" in PUBLISH_JOB
    assert "published-release-assets" in PUBLISH_JOB
    assert '"$RUNNER_TEMP/release-assets-before.sha256"' in PUBLISH_JOB


def test_catalog_verifier_uses_only_its_minimal_hash_locked_environment() -> None:
    assert WORKFLOW.count('python-version: "3.12"') == 2
    assert WORKFLOW.count(
        "component-catalog-verifier-linux-x64.txt"
    ) == 2
    assert WORKFLOW.count("--require-hashes") == 2
    assert WORKFLOW.count("--only-binary=:all:") == 2
    assert WORKFLOW.count("--no-deps") == 2
    assert WORKFLOW.count("python -m pip check") == 2
    assert 'pip install --disable-pip-version-check "cryptography' not in WORKFLOW
    assert re.findall(r"(?m)^([a-z0-9-]+)==([^ ]+) [\\]$", VERIFIER_LOCK) == [
        ("cffi", "2.1.0"),
        ("cryptography", "50.0.0"),
        ("pycparser", "3.0"),
    ]
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", VERIFIER_LOCK)
    assert hashes == [
        "1e9f50d192a3e525b15a75ab5114e442d83d657b7ec29182a991bc9a88fd3a66",
        "b42a28c1844fd9de8f3f7d540e36b66f3a9c83fceac7170ebc7a6a19edd9dcae",
        "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
    ]


def test_runbook_requires_the_verified_latest_promotion_gate() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "Publish Verified WebJam Release" in RUNBOOK
    assert "seven packages plus" in RUNBOOK
    assert "checksum manifest" in RUNBOOK
    assert "/releases/latest" in RUNBOOK
    assert "Never publish the draft directly from the web page" in normalized

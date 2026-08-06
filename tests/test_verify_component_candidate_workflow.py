"""Static fail-closed contracts for pre-tag frozen component verification."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "verify-component-candidate.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
HEADER, JOBS = WORKFLOW.split("\njobs:\n", maxsplit=1)
BIND_JOB, NATIVE_JOB = JOBS.split("\n  frozen-package-smoke:\n", maxsplit=1)
NATIVE_JOB, FINAL_JOB = NATIVE_JOB.split("\n  revalidate-candidate:\n", maxsplit=1)
DESKTOP_RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)
COMPONENT_RUNBOOK = (
    ROOT / "docs" / "JAMULUS_COMPONENT_RELEASE_RUNBOOK.md"
).read_text(encoding="utf-8")


def test_workflow_is_manual_read_only_and_has_no_mutation_path() -> None:
    assert "workflow_dispatch:" in HEADER
    assert "source_run_id:" in HEADER
    assert "expected_sha:" in HEADER
    assert HEADER.count("required: true") == 2
    assert "permissions:\n  contents: read\n  actions: read" in HEADER
    assert "cancel-in-progress: false" in HEADER
    assert "push:" not in HEADER
    assert "pull_request:" not in HEADER
    assert "schedule:" not in HEADER
    assert "workflow_call:" not in HEADER
    assert "repository_dispatch:" not in HEADER

    for forbidden in (
        "contents: write",
        "actions: write",
        "id-token: write",
        "packages: write",
        "environment:",
        "secrets.",
        "git tag ",
        "git push",
        "gh release ",
        "--method POST",
        "--method PATCH",
        "--method DELETE",
        "actions/upload-artifact",
        "softprops/action-gh-release",
    ):
        assert forbidden not in WORKFLOW
    assert WORKFLOW.count("permissions:\n      contents: read\n      actions: read") == 3


def test_actions_and_checkout_credentials_are_reviewed_and_pinned() -> None:
    uses_lines = [line.strip() for line in WORKFLOW.splitlines() if "uses:" in line]
    assert uses_lines == [
        "uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0",
        "uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0",
        "uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0",
        "uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0",
        "uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
        "uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0",
    ]
    assert WORKFLOW.count("persist-credentials: false") == 3
    assert "persist-credentials: true" not in WORKFLOW


def test_dispatch_binds_exact_current_master_and_same_repository_push_run() -> None:
    assert 'if [[ "$GITHUB_REF" != "refs/heads/master" ]]' in BIND_JOB
    assert '[[ ! "$REQUESTED_RUN_ID" =~ ^[1-9][0-9]*$ ]]' in BIND_JOB
    assert '[[ ! "$REQUESTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in BIND_JOB
    assert '"$workflow_commit" == "$REQUESTED_SHA"' in BIND_JOB
    assert '"$master_commit" == "$REQUESTED_SHA"' in BIND_JOB
    assert "+refs/heads/master:refs/remotes/origin/master" in BIND_JOB
    assert '"repos/$GITHUB_REPOSITORY/actions/workflows/ci.yml"' in BIND_JOB
    assert '.name == "WebJam CI"' in BIND_JOB
    assert '.path == ".github/workflows/ci.yml"' in BIND_JOB
    assert '.state == "active"' in BIND_JOB
    assert '"repos/$GITHUB_REPOSITORY/actions/runs/$REQUESTED_RUN_ID"' in BIND_JOB
    for required in (
        ".id == $run_id",
        ".workflow_id == $workflow_id",
        '.event == "push"',
        '.head_branch == "master"',
        ".head_sha == $sha",
        '.status == "completed"',
        '.conclusion == "success"',
        ".repository.full_name == $repository",
        ".head_repository.full_name == $repository",
    ):
        assert required in BIND_JOB
    assert 'packaged_version" == "$EXPECTED_WEBJAM_VERSION' in BIND_JOB
    assert "DEFAULT_COMPONENT_CATALOG_URL" in BIND_JOB
    assert "EXPECTED_COMPONENT_CATALOG_URL" in BIND_JOB
    assert "Frozen source component-catalog URL is not exact." in BIND_JOB
    isolated_check = BIND_JOB.split(
        "version_source=", maxsplit=1
    )[0].rsplit("run: |", maxsplit=1)[1]
    assert "unset GH_TOKEN GITHUB_TOKEN" in isolated_check
    assert BIND_JOB.index("unset GH_TOKEN GITHUB_TOKEN") < BIND_JOB.index(
        "python -I - <<'PY'"
    )
    assert "merge-base --is-ancestor \"$REQUESTED_SHA\"" not in BIND_JOB


def test_only_four_desktop_artifacts_plus_exact_pocket_stage_are_allowed() -> None:
    for artifact in (
        "webjam-windows-x64",
        "webjam-macos-arm64",
        "webjam-macos-x64",
        "webjam-linux-x64",
    ):
        assert f'"{artifact}"' in BIND_JOB
    assert '"webjam-pocket-stage-ios-setup-$EXPECTED_SHA"' in BIND_JOB
    assert "[ .[].artifacts[] ] as $all" in BIND_JOB
    assert "($all | length) != 5" in BIND_JOB
    assert "($matches | length) != 4" in BIND_JOB
    assert "($pocket_matches | length) != 1" in BIND_JOB
    assert "([$matches[].name] | unique | length) != 4" in BIND_JOB
    assert "([$matches[].id] | unique | length) != 4" in BIND_JOB
    assert "([$matches[].digest] | unique | length) != 4" in BIND_JOB
    assert ".expired != false" in BIND_JOB
    assert "(.expires_at | fromdateiso8601) <= now" in BIND_JOB
    assert '.digest | test("^sha256:[0-9a-f]{64}$")' in BIND_JOB
    assert ".workflow_run.id != $run_id" in BIND_JOB
    assert '.workflow_run.head_branch != "master"' in BIND_JOB
    assert ".workflow_run.head_sha != $sha" in BIND_JOB
    assert "expected four desktop artifacts plus only Pocket Stage" in BIND_JOB


def test_component_identity_is_fixed_immutable_and_not_operator_supplied() -> None:
    assert "COMPONENT_CHANNEL_TAG: jamulus-components-v3" in HEADER
    assert (
        "COMPONENT_CHANNEL_ANCHOR: b1de2d826afe01d6696677b14c2dd5efafa87b5b"
        in HEADER
    )
    assert "EXPECTED_WEBJAM_VERSION: 0.22.5" in HEADER
    assert "EXPECTED_CATALOG_SEQUENCE: 6" in HEADER
    assert "EXPECTED_JAMULUS_VERSION: 3.12.3" in HEADER
    inputs = HEADER.split("inputs:\n", maxsplit=1)[1].split("\npermissions:", maxsplit=1)[0]
    assert "catalog" not in inputs.lower()
    assert "component" not in inputs.lower()
    assert "url" not in inputs.lower()

    assert 'git cat-file -t "$component_ref"' in BIND_JOB
    assert '== commit' in BIND_JOB
    assert '"$component_tag_object" == "$COMPONENT_CHANNEL_ANCHOR"' in BIND_JOB
    assert '"$component_tag_commit" == "$COMPONENT_CHANNEL_ANCHOR"' in BIND_JOB
    assert "git ls-remote --refs origin" in BIND_JOB
    assert "git merge-base --is-ancestor" in BIND_JOB
    assert "$COMPONENT_CHANNEL_ANCHOR\" \"$EXPECTED_SHA" in BIND_JOB
    assert "($matches | length) != 1" in BIND_JOB
    assert "$matches[0].id <= 0" in BIND_JOB
    assert '$matches[0].name != "WebJam Jamulus component catalog v3"' in BIND_JOB
    assert "$matches[0].draft != false" in BIND_JOB
    assert "$matches[0].prerelease != true" in BIND_JOB
    assert "$matches[0].immutable != true" in BIND_JOB
    assert "$matches[0].assets[0].state != \"uploaded\"" in BIND_JOB
    assert "$matches[0].assets[0].size > 1048576" in BIND_JOB
    assert "($matches[0].assets | length) != 1" in BIND_JOB
    assert '!= "WebJam-Jamulus-components-v1.json"' in BIND_JOB
    assert 'releases/latest" --jq \'.tag_name\'' in BIND_JOB
    assert "Accept: application/octet-stream" in BIND_JOB
    assert '[[ "$(stat -c \'%s\' "$catalog")" == "$asset_size" ]]' in BIND_JOB
    assert '[[ "sha256:$catalog_envelope_sha256" == "$asset_digest" ]]' in BIND_JOB
    assert "requirements-lock/component-catalog-verifier-linux-x64.txt" in BIND_JOB
    assert "--require-hashes" in BIND_JOB
    assert "tools.verify_jamulus_component_catalog" in BIND_JOB
    assert "unset GH_TOKEN GITHUB_TOKEN" in BIND_JOB
    assert '--webjam-version "$EXPECTED_WEBJAM_VERSION"' in BIND_JOB
    assert '--minimum-sequence "$EXPECTED_CATALOG_SEQUENCE"' in BIND_JOB
    assert ".sequence == $sequence" in BIND_JOB
    assert ".component_count == 8" in BIND_JOB
    assert "(.expires_at | fromdateiso8601) > (now + 3600)" in BIND_JOB
    assert (
        "catalog_expires_at: ${{ steps.component.outputs.catalog_expires_at }}"
        in BIND_JOB
    )
    assert "catalog_expires_at=$catalog_expires_at" in BIND_JOB


def test_native_matrix_is_exact_and_wrapper_download_is_digest_enforced() -> None:
    for value in (
        "ubuntu-22.04",
        "linux-x64",
        "windows-2025",
        "windows-x64",
        "macos-14",
        "macos-arm64",
        "macos-15-intel",
        "macos-x64",
        "WebJam-linux-x64.zip",
        "WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip",
        "WebJam-macos-arm64-ADHOC-TEST-ONLY.zip",
        "WebJam-macos-x64-ADHOC-TEST-ONLY.zip",
    ):
        assert value in NATIVE_JOB
    assert 'echo "- catalog expires at: \\`$CATALOG_EXPIRES_AT\\`"' in NATIVE_JOB
    assert "fail-fast: false" in NATIVE_JOB
    assert "artifact-ids: ${{ steps.artifact.outputs.artifact_id }}" in NATIVE_JOB
    assert "run-id: ${{ needs['bind-candidate'].outputs.source_run_id }}" in NATIVE_JOB
    assert "digest-mismatch: error" in NATIVE_JOB
    assert "merge-multiple: true" in NATIVE_JOB
    assert '"repos/$GITHUB_REPOSITORY/actions/artifacts/$ARTIFACT_ID"' in NATIVE_JOB
    assert ".digest == $digest" in NATIVE_JOB
    assert ".size_in_bytes == $size" in NATIVE_JOB
    assert ".expired == false" in NATIVE_JOB
    assert ".workflow_run.id == $run_id" in NATIVE_JOB
    assert ".workflow_run.head_sha == $sha" in NATIVE_JOB
    assert "needs.bind-candidate" not in WORKFLOW


def test_every_container_is_hash_bound_but_only_portable_zips_are_launched() -> None:
    for container in (
        "WebJam-v0.22.5-windows-x64-UNSIGNED-TEST-ONLY-setup.exe",
        "WebJam-v0.22.5-macos-arm64-ADHOC-TEST-ONLY.dmg",
        "WebJam-v0.22.5-macos-x64-ADHOC-TEST-ONLY.dmg",
    ):
        assert container in NATIVE_JOB
    assert "file_bindings = []" in NATIVE_JOB
    assert '"sha256": entry_digest' in NATIVE_JOB
    assert '"size": details.st_size' in NATIVE_JOB
    assert "inventory_binding_sha256" in NATIVE_JOB
    assert "file_bindings={serialized_bindings}" in NATIVE_JOB
    assert "Candidate artifact inventory is too large." in NATIVE_JOB
    assert "Bound artifact entry size changed." in NATIVE_JOB
    assert "Bound artifact entry checksum changed." in NATIVE_JOB
    assert "Bound portable package checksum changed." in NATIVE_JOB
    assert "verify_frozen_portable_archive.py" in NATIVE_JOB
    assert "Preflight portable ZIP paths, types, symlinks, and size bounds" in NATIVE_JOB
    assert NATIVE_JOB.count("--extracted-parent") == 2
    assert "Verify exact Windows two-entry checksum manifest" in NATIVE_JOB
    assert 'test "$(wc -l < "$checksum")" -eq 2' in NATIVE_JOB
    assert 'sha256sum --check --strict "$checksum"' in NATIVE_JOB
    assert "Expand-Archive -LiteralPath $archive" in NATIVE_JOB
    assert 'ditto -x -k "$archive" "$destination"' in NATIVE_JOB
    assert 'codesign --verify --deep --strict "$app"' in NATIVE_JOB
    assert "--archive \"$RUNNER_TEMP/candidate-artifact/${{ matrix.package }}\"" in NATIVE_JOB
    assert "run_frozen_component_catalog_smoke.py" in NATIVE_JOB
    assert NATIVE_JOB.count("--expected-version") == 3
    assert NATIVE_JOB.count("--expected-sequence") == 3
    assert NATIVE_JOB.count("--expected-jamulus-version") == 3
    assert NATIVE_JOB.count("--expected-catalog-envelope-sha256") == 3
    assert NATIVE_JOB.count("--expected-catalog-payload-sha256") == 3
    assert NATIVE_JOB.count("--expected-signer-fingerprint-sha256") == 3
    assert "--expected-target linux-x64" in NATIVE_JOB
    assert "--expected-target windows-x64" in NATIVE_JOB
    assert '--expected-target "${{ matrix.target }}"' in NATIVE_JOB
    windows_launch = NATIVE_JOB.split(
        "- name: Launch Windows package against live signed catalog", maxsplit=1
    )[1].split("- name: Launch macOS package against live signed catalog", maxsplit=1)[0]
    macos_launch = NATIVE_JOB.split(
        "- name: Launch macOS package against live signed catalog", maxsplit=1
    )[1].split("- name: Record bounded read-only proof summary", maxsplit=1)[0]
    assert "setup.exe" not in windows_launch
    assert ".dmg" not in macos_launch
    for value in (
        "COMPONENT_RELEASE_ID",
        "COMPONENT_ASSET_ID",
        "COMPONENT_ASSET_DIGEST",
        "CATALOG_EXPIRES_AT",
        "CATALOG_ENVELOPE_SHA256",
        "CATALOG_PAYLOAD_SHA256",
        "SIGNER_FINGERPRINT_SHA256",
        "FILE_BINDINGS",
    ):
        assert value in NATIVE_JOB


def test_final_job_revalidates_every_remote_identity_after_native_matrix() -> None:
    assert "needs: [bind-candidate, frozen-package-smoke]" in FINAL_JOB
    assert '[[ "$GITHUB_REF" == "refs/heads/master" ]]' in FINAL_JOB
    assert '"$(git rev-parse HEAD)" == "$EXPECTED_SHA"' in FINAL_JOB
    assert "actions/workflows/ci.yml" in FINAL_JOB
    assert "actions/runs/$EXPECTED_RUN_ID" in FINAL_JOB
    assert '.conclusion == "success"' in FINAL_JOB
    assert ".repository.full_name == $repository" in FINAL_JOB
    assert "actions/runs/$EXPECTED_RUN_ID/artifacts?per_page=100" in FINAL_JOB
    assert "$current_bindings == $expected_bindings" in FINAL_JOB
    assert "($all | length) == 5" in FINAL_JOB
    assert "($desktop | length) == 4" in FINAL_JOB
    assert "($pocket | length) == 1" in FINAL_JOB
    assert "git cat-file -t \"$component_ref\"" in FINAL_JOB
    assert '"$(git rev-parse "$component_ref")" == "$COMPONENT_CHANNEL_ANCHOR"' in (
        FINAL_JOB
    )
    assert "releases/tags/$COMPONENT_CHANNEL_TAG" in FINAL_JOB
    assert ".id == $release_id" in FINAL_JOB
    assert ".assets[0].id == $asset_id" in FINAL_JOB
    assert '.assets[0].state == "uploaded"' in FINAL_JOB
    assert ".assets[0].digest == $digest" in FINAL_JOB
    assert "EXPECTED_CATALOG_EXPIRES_AT" in FINAL_JOB
    assert ".payload.expires_at == $expires_at" in FINAL_JOB
    assert "(.payload.expires_at | fromdateiso8601) > (now + 3600)" in FINAL_JOB
    assert (
        'echo "- catalog expires at: \\`$EXPECTED_CATALOG_EXPIRES_AT\\`"'
        in FINAL_JOB
    )
    assert "EXPECTED_CATALOG_ENVELOPE_SHA256" in FINAL_JOB
    assert "releases/latest" in FINAL_JOB


def test_runbooks_require_post_evidence_final_sha_dispatch_before_tag() -> None:
    command = "gh workflow run verify-component-candidate.yml"
    for runbook in (DESKTOP_RUNBOOK, COMPONENT_RUNBOOK):
        assert command in runbook
        assert "--ref master" in runbook
        assert "-f source_run_id=" in runbook
        assert "-f expected_sha=" in runbook
        assert "post-evidence" in runbook
        assert "without another source commit" in runbook
        assert "portable ZIP" in runbook
        assert "DMG" in runbook
        assert "Setup" in runbook
        assert "final read-only identity-revalidation" in runbook
    assert "before the annotated desktop tag" in COMPONENT_RUNBOOK
    assert "separate explicit verification-dispatch approval" in DESKTOP_RUNBOOK
    assert "landed cannot pass" in COMPONENT_RUNBOOK
    assert "stale by construction" in DESKTOP_RUNBOOK

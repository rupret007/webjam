"""Fail-closed contract for the deliberately inert v0.26.0 publisher stub."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "publish-v026-testing-release.yml"
).read_text(encoding="utf-8")
HEADER, JOBS = WORKFLOW.split("\njobs:\n", maxsplit=1)


def test_placeholder_is_manual_exact_version_and_read_only() -> None:
    assert "workflow_dispatch:" in HEADER
    assert "push:" not in HEADER
    assert "schedule:" not in HEADER
    assert "EXPECTED_TAG: v0.26.0" in HEADER
    assert "EXPECTED_VERSION: 0.26.0" in HEADER
    assert "permissions:\n  contents: read\n  actions: read" in HEADER
    assert "contents: write" not in WORKFLOW
    assert "release-latest" not in WORKFLOW


def test_placeholder_has_no_post_tag_identity_or_publication_capability() -> None:
    for marker in (
        "PINNED_TAG_OBJECT: UNSET_POST_TAG_OBJECT",
        "PINNED_TAG_COMMIT: UNSET_POST_TAG_COMMIT",
        "PINNED_TAG_CI_RUN_ID: UNSET_POST_TAG_CI_RUN_ID",
        "PINNED_RELEASE_ID: UNSET_POST_TAG_RELEASE_ID",
        "PINNED_INVENTORY_SHA256: UNSET_POST_TAG_INVENTORY_SHA256",
        "PINNED_BODY_SHA256: UNSET_POST_TAG_BODY_SHA256",
    ):
        assert marker in HEADER
    for mutating_marker in (
        "gh release",
        "gh api",
        "--method POST",
        "--method PATCH",
        "--method DELETE",
        "draft=false",
        "make_latest",
        "actions/checkout",
    ):
        assert mutating_marker not in WORKFLOW


def test_placeholder_always_fails_closed_instead_of_reporting_publish_success() -> None:
    assert "refuse-unpinned-publication:" in JOBS
    assert "REQUESTED_TAG: ${{ inputs.tag }}" in JOBS
    assert '[[ "$REQUESTED_TAG" != "$EXPECTED_TAG" ]]' in JOBS
    assert "exact post-tag pins are unset" in JOBS
    assert JOBS.count("exit 1") == 2
    assert "exit 0" not in JOBS

"""Fail-closed contract for the unpublished v0.25.0 promotion placeholder."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "publish-v025-testing-release.yml"
).read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DESKTOP_RELEASE_RUNBOOK.md").read_text(
    encoding="utf-8"
)


def test_placeholder_is_manual_read_only_and_forced_inert() -> None:
    header, jobs = WORKFLOW.split("\njobs:\n", maxsplit=1)
    assert "workflow_dispatch:" in header
    assert "push:" not in header
    assert "schedule:" not in header
    assert "EXPECTED_TAG: v0.25.0" in header
    assert "EXPECTED_VERSION: 0.25.0" in header
    assert "publish-webjam-release" in header
    assert "cancel-in-progress: false" in header
    assert "permissions:\n  contents: read\n  actions: read" in header
    assert "if: ${{ false }}" in jobs
    assert "contents: write" not in WORKFLOW
    assert "make_latest" not in WORKFLOW
    assert "draft=false" not in WORKFLOW
    assert "actions/checkout" not in WORKFLOW
    assert "gh api" not in WORKFLOW


def test_placeholder_has_no_invented_post_tag_identity() -> None:
    for marker in (
        "UNSET_POST_TAG_TAG_OBJECT",
        "UNSET_POST_TAG_TAG_COMMIT",
        "UNSET_POST_TAG_TAG_CI_RUN_ID",
        "UNSET_POST_TAG_RELEASE_ID",
        "UNSET_POST_TAG_BODY_SHA256",
        "UNSET_POST_TAG_INVENTORY_SHA256",
    ):
        assert marker in WORKFLOW
    assert "SEALED_COMPONENT_TAG: jamulus-components-v3" in WORKFLOW
    assert "SEALED_COMPONENT_SEQUENCE: 6" in WORKFLOW


def test_runbook_requires_post_tag_pins_before_enabling_promotion() -> None:
    normalized = " ".join(RUNBOOK.split())
    assert "v0.25.0 pre-promotion procedure" in RUNBOOK
    assert "publisher is deliberately non-executable" in normalized
    assert "Record the tag-object SHA and peeled tag-commit SHA" in normalized
    assert "unique successful tag CI run ID" in normalized
    assert "exact draft release ID" in normalized
    assert "canonical release-body" in normalized
    assert "sorted `{id,name,size,digest}` inventory SHA-256" in normalized
    assert "Replace every explicit `UNSET_POST_TAG_*` placeholder" in normalized
    assert "release-latest" in RUNBOOK

"""Static contract for the dedicated Cloudflare Worker CI workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "cloudflare-worker-tests.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKER = ROOT / "deploy" / "relay" / "cloudflare-worker"
PACKAGE = WORKER / "package.json"
LOCKFILE = WORKER / "package-lock.json"

_NODE_VERSION = re.search(
    r'node-version:\s*"(?P<version>[^"]+)"',
    WORKFLOW,
)

# Curated Node.js release tags from nodejs.org/dist; extend when bumping CI node-version.
_PUBLISHED_NODE_VERSIONS: frozenset[str] = frozenset(
    {
        "v22.22.0",
        "v22.22.1",
        "v22.22.2",
        "v22.22.3",
        "v22.23.0",
        "v22.23.1",
        "v22.23.2",
        "v22.23.3",
    }
)


def _published_node_versions() -> set[str]:
    return set(_PUBLISHED_NODE_VERSIONS)


def test_workflow_runs_worker_tests_on_pr_push_and_dispatch() -> None:
    assert WORKFLOW_PATH.is_file()
    assert "name: Cloudflare Worker tests" in WORKFLOW
    assert "pull_request:" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    assert "branches:\n      - master" in WORKFLOW
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in WORKFLOW
    assert "permissions:\n  contents: read" in WORKFLOW


def test_workflow_pins_node_installs_dependencies_then_runs_node_test() -> None:
    assert _NODE_VERSION is not None
    assert "working-directory: deploy/relay/cloudflare-worker" in WORKFLOW
    assert "npm ci" in WORKFLOW
    assert "node --test" in WORKFLOW
    install_index = WORKFLOW.index("npm ci")
    test_index = WORKFLOW.index("node --test")
    setup_index = WORKFLOW.index("actions/setup-node@")
    assert setup_index < install_index < test_index


def test_published_node_version_catalog_is_static_and_well_formed() -> None:
    for entry in _PUBLISHED_NODE_VERSIONS:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", entry)


def test_pinned_node_version_is_a_published_release() -> None:
    assert _NODE_VERSION is not None
    version = _NODE_VERSION.group("version")
    if version.endswith(".x"):
        major = version.removesuffix(".x")
        assert major.isdigit()
        published = _published_node_versions()
        assert any(entry.startswith(f"v{major}.") for entry in published)
        return

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert f"v{version}" in _published_node_versions()


def test_worker_lockfile_matches_package_json_for_npm_ci() -> None:
    assert PACKAGE.is_file()
    assert LOCKFILE.is_file()
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    assert lock["name"] == package["name"]
    assert lock["lockfileVersion"] >= 3

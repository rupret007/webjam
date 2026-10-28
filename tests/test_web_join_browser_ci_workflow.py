"""Static contract for the Web Join browser CI workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "web-join-tests.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKER = ROOT / "deploy" / "relay" / "cloudflare-worker"
WEB_JOIN = ROOT / "web" / "join"
WORKER_LOCK = WORKER / "package-lock.json"
WEB_JOIN_LOCK = WEB_JOIN / "package-lock.json"

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


def test_workflow_runs_web_join_tests_on_pr_push_and_dispatch() -> None:
    assert WORKFLOW_PATH.is_file()
    assert "name: Web Join browser tests" in WORKFLOW
    assert "pull_request:" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    assert "branches:\n      - master" in WORKFLOW
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in WORKFLOW
    assert "permissions:\n  contents: read" in WORKFLOW


def test_workflow_installs_worker_and_web_join_then_runs_node_test() -> None:
    assert _NODE_VERSION is not None
    assert "working-directory: deploy/relay/cloudflare-worker" in WORKFLOW
    assert "working-directory: web/join" in WORKFLOW
    assert WORKFLOW.count("npm ci") == 2
    assert "node --test" in WORKFLOW
    assert "npm test" in WORKFLOW
    worker_test_index = WORKFLOW.index("node --test")
    web_join_test_index = WORKFLOW.index("npm test")
    setup_index = WORKFLOW.index("actions/setup-node@")
    assert setup_index < worker_test_index < web_join_test_index


def test_pinned_node_version_is_a_published_release() -> None:
    assert _NODE_VERSION is not None
    version = _NODE_VERSION.group("version")
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert f"v{version}" in _published_node_versions()


def test_worker_lockfile_matches_package_json_for_npm_ci() -> None:
    package = WORKER / "package.json"
    assert package.is_file()
    assert WORKER_LOCK.is_file()
    lock = json.loads(WORKER_LOCK.read_text(encoding="utf-8"))
    meta = json.loads(package.read_text(encoding="utf-8"))
    assert lock["name"] == meta["name"]
    assert lock["lockfileVersion"] >= 3


def test_web_join_lockfile_matches_package_json_for_npm_ci() -> None:
    package = WEB_JOIN / "package.json"
    assert package.is_file()
    assert WEB_JOIN_LOCK.is_file()
    lock = json.loads(WEB_JOIN_LOCK.read_text(encoding="utf-8"))
    meta = json.loads(package.read_text(encoding="utf-8"))
    assert lock["name"] == meta["name"]
    assert lock["lockfileVersion"] >= 3

"""Changelog fragments: the PR check is strict and combine is lossless."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import changelog_fragments as frag

CHANGELOG_TEXT = "# WebJam Changelog\n\n## [Unreleased]\n\n- Older entry.\n\n## [0.1.0]\n\n- First.\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "ci")
    _write(tmp_path, "CHANGELOG.md", CHANGELOG_TEXT)
    _write(tmp_path, "changelog.d/README.md", "how to\n")
    _write(tmp_path, "app.py", "x = 1\n")
    _commit(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def test_pr_with_fragment_passes(repo):
    _write(repo, "app.py", "x = 2\n")
    _write(repo, "changelog.d/x-two.md", "- X is now two.\n")
    _commit(repo)
    assert frag.check("master", repo) == []


def test_pr_with_reasoned_skip_passes(repo):
    _write(repo, "app.py", "x = 2\n")
    _write(repo, "changelog.d/refactor.skip", "Internal refactor, no user-visible change.\n")
    _commit(repo)
    assert frag.check("master", repo) == []


def test_pr_without_fragment_fails(repo):
    _write(repo, "app.py", "x = 2\n")
    _commit(repo)
    assert any("no changelog fragment" in p for p in frag.check("master", repo))


@pytest.mark.parametrize("name,text", [("e.md", "   \n"), ("e.skip", ""), ("e.md", "Not a bullet\n")])
def test_empty_or_malformed_fragment_fails(repo, name, text):
    _write(repo, f"changelog.d/{name}", text)
    _commit(repo)
    assert frag.check("master", repo)


def test_direct_changelog_edit_fails_even_with_fragment(repo):
    _write(repo, "CHANGELOG.md", CHANGELOG_TEXT.replace("- Older entry.", "- New.\n- Older entry."))
    _write(repo, "changelog.d/new.md", "- New.\n")
    _commit(repo)
    assert any("edited directly" in p for p in frag.check("master", repo))


def test_readme_and_nested_files_are_not_fragments(repo):
    _write(repo, "changelog.d/README.md", "how to, edited\n")
    _write(repo, "changelog.d/sub/x.md", "- nested\n")
    _commit(repo)
    assert any("no changelog fragment" in p for p in frag.check("master", repo))


def test_combine_inserts_in_name_order_and_deletes(repo):
    _write(repo, "changelog.d/b.md", "- Bee.\n")
    _write(repo, "changelog.d/a.md", "- Ay.\n  continued.\n")
    _write(repo, "changelog.d/c.skip", "CI only.\n")
    moved = frag.combine(repo)
    assert moved == ["a.md", "b.md", "c.skip"]
    text = (repo / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n- Ay.\n  continued.\n- Bee.\n- Older entry.\n" in text
    assert "## [0.1.0]\n\n- First.\n" in text
    assert sorted(p.name for p in (repo / "changelog.d").iterdir()) == ["README.md"]


def test_combine_with_no_fragments_leaves_changelog_untouched(repo):
    frag.combine(repo)
    assert (repo / "CHANGELOG.md").read_text() == CHANGELOG_TEXT


def test_this_repository_has_a_readme_and_unreleased_heading():
    root = frag.REPO_ROOT
    assert (root / "changelog.d" / "README.md").is_file()
    assert frag.UNRELEASED in (root / "CHANGELOG.md").read_text(encoding="utf-8")

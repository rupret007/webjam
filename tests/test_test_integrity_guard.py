"""The test-integrity guard only ever adds failures; prove each rule fires."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.gates import test_integrity_guard as guard


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_global_config_does_not_fake_the_clock():
    assert guard.find_fake_clock() == []


@pytest.mark.parametrize(
    ("rel", "text", "rule"),
    [
        (".github/workflows/ci.yml", "env:\n  TZ: Pacific/Auckland\n", "global TZ override"),
        (".github/workflows/ci.yml", "run: TZ=UTC python -m pytest\n", "global TZ override"),
        (".github/workflows/ci.yml", "env:\n  LD_PRELOAD: /usr/lib/faketime.so\n", "LD_PRELOAD injection"),
        (".github/workflows/ci.yml", "run: faketime '2026-07-01' pytest\n", "faketime"),
        ("pytest.ini", "[pytest]\nenv =\n    FAKETIME=@2026-07-01\n", "faketime"),
        ("tests/conftest.py", "from freezegun import freeze_time\n", "freezegun in conftest"),
        ("tests/conftest.py", "import time_machine\n", "time-machine in conftest"),
        ("tests/conftest.py", "os.environ['TZ'] = 'UTC'\n", "TZ override in conftest"),
        ("tests/conftest.py", "monkeypatch.setenv(\"TZ\", \"UTC\")\n", "TZ override in conftest"),
        ("web/vitest.config.ts", "export default { test: { fakeTimers: { now: 0 } } }\n", "global fake timers"),
        ("web/vitest.setup.ts", "vi.useFakeTimers()\n", "global fake timers"),
    ],
)
def test_global_fake_clock_is_rejected(tmp_path, rel, text, rule):
    path = _write(tmp_path, rel, text)
    findings = guard.find_fake_clock(tmp_path, [path])
    assert any(rule in finding for finding in findings), findings


def test_per_test_clock_control_stays_allowed(tmp_path):
    test_file = _write(
        tmp_path,
        "tests/test_clock.py",
        "def test_x(monkeypatch):\n    monkeypatch.setenv('TZ', 'UTC')\n    monkeypatch.setenv('LD_PRELOAD', '/x')\n",
    )
    workflow = _write(tmp_path, ".github/workflows/ci.yml", "# TZ: UTC is never set here\nrun: pytest\n")
    assert guard._global_config_files(tmp_path) == [workflow]
    assert guard.find_fake_clock(tmp_path) == []
    assert test_file.exists()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "ci")
    _write(tmp_path, "tests/test_a.py", "def test_one():\n    assert 1\n\ndef test_two():\n    assert 2\n")
    _write(tmp_path, "tests/test_b.py", "def test_three():\n    assert 3\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")


def test_unchanged_or_added_tests_pass(repo):
    _write(repo, "tests/test_c.py", "def test_four():\n    assert 4\n")
    _commit(repo)
    assert guard.find_loosening("master", repo) == []


def test_deleted_test_file_is_rejected(repo):
    (repo / "tests/test_b.py").unlink()
    _commit(repo)
    findings = guard.find_loosening("master", repo)
    assert any("test file deleted" in f for f in findings)
    assert any("dropped from" in f for f in findings)


def test_removed_test_function_is_rejected(repo):
    _write(repo, "tests/test_a.py", "def test_one():\n    assert 1\n")
    _commit(repo)
    assert any("dropped from 2 to 1" in f for f in guard.find_loosening("master", repo))


def test_new_skip_marker_is_rejected(repo):
    _write(
        repo,
        "tests/test_b.py",
        "import pytest\n\n@pytest.mark.skip(reason='flaky')\ndef test_three():\n    assert 3\n",
    )
    _commit(repo)
    assert any("skip/xfail" in f for f in guard.find_loosening("master", repo))


def test_comparison_is_against_master_not_the_branch_history(repo):
    # A branch that first deletes a test and then restores it is not weaker
    # than master, so it must pass even though it differs from its own
    # earlier commit.
    (repo / "tests/test_b.py").unlink()
    _commit(repo)
    _write(repo, "tests/test_b.py", "def test_three():\n    assert 3\n")
    _commit(repo)
    assert guard.find_loosening("master", repo) == []


def test_renamed_test_file_keeps_its_tests(repo):
    _git(repo, "mv", "tests/test_b.py", "tests/test_b_renamed.py")
    _commit(repo)
    assert guard.find_loosening("master", repo) == []

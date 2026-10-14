"""Add-only CI guards for test integrity.

Two checks, both of which can only make CI stricter:

1. ``fake-clock``: global test configuration must not fake the clock.
   Faking time or the time zone inside an individual test (for example
   ``monkeypatch.setenv`` within a test function) stays allowed. What is
   rejected is a fake clock applied to the whole run: ``faketime`` /
   ``libfaketime``, ``LD_PRELOAD`` / ``DYLD_INSERT_LIBRARIES`` injection or a
   ``TZ`` override in a workflow or test config, a freezegun / time-machine
   import in a ``conftest.py``, or a global vitest/jest fake-timer setting.

2. ``loosening``: compared with the merge base on ``origin/master`` (never the
   PR's own earlier commits), a change must not delete test files, reduce the
   number of test functions, or add skip / xfail markers to tests.

Usage::

    python tools/gates/test_integrity_guard.py fake-clock
    python tools/gates/test_integrity_guard.py loosening --base origin/master
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Files whose settings apply to a whole test run.
GLOBAL_CONFIG_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "**/conftest.py",
    "**/vitest.config.*",
    "**/vitest.setup.*",
    "**/vitest.workspace.*",
    "**/jest.config.*",
    "**/jest.setup.*",
)

EXCLUDED_PARTS = {"node_modules", ".git", "legacy", ".venv", "venv"}

# (rule name, pattern, applies-to predicate)
_ANY = lambda path: True  # noqa: E731
_WORKFLOW_OR_INI = lambda path: path.suffix in {".yml", ".yaml", ".ini", ".cfg", ".toml"}  # noqa: E731
_CONFTEST = lambda path: path.name == "conftest.py"  # noqa: E731
_JS_CONFIG = lambda path: path.name.startswith(("vitest.", "jest."))  # noqa: E731

FAKE_CLOCK_RULES: tuple[tuple[str, re.Pattern[str], object], ...] = (
    ("faketime", re.compile(r"\b(lib)?faketime\b|FAKETIME", re.IGNORECASE), _ANY),
    ("LD_PRELOAD injection", re.compile(r"\bLD_PRELOAD\s*[:=]"), _WORKFLOW_OR_INI),
    ("DYLD_INSERT_LIBRARIES injection", re.compile(r"\bDYLD_INSERT_LIBRARIES\s*[:=]"), _WORKFLOW_OR_INI),
    ("global TZ override", re.compile(r"(^|[\s\"'{,])TZ\s*[:=]"), _WORKFLOW_OR_INI),
    ("freezegun in conftest", re.compile(r"\bfreezegun\b|\bfreeze_time\b"), _CONFTEST),
    ("time-machine in conftest", re.compile(r"\btime_machine\b"), _CONFTEST),
    ("TZ override in conftest", re.compile(r"""(setenv|environ)\s*[\[(]\s*["']TZ["']|\btzset\s*\("""), _CONFTEST),
    ("global fake timers", re.compile(r"\bfakeTimers\b|\buseFakeTimers\s*\(|\bsetSystemTime\s*\("), _JS_CONFIG),
)


def _global_config_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    for pattern in GLOBAL_CONFIG_GLOBS:
        for path in root.glob(pattern):
            rel = path.relative_to(root)
            if path.is_file() and not EXCLUDED_PARTS.intersection(rel.parts):
                seen.add(path)
    return sorted(seen)


def _strip_comment(line: str, path: Path) -> str:
    marker = "//" if path.suffix in {".js", ".ts", ".mjs", ".cjs", ".mts", ".cts"} else "#"
    stripped = line.lstrip()
    return "" if stripped.startswith(marker) else line


def find_fake_clock(root: Path = REPO_ROOT, files: Iterable[Path] | None = None) -> list[str]:
    """Return one human-readable finding per offending line."""
    findings: list[str] = []
    guard_self = Path(__file__).resolve()
    for path in files if files is not None else _global_config_files(root):
        if path.resolve() == guard_self:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        for number, raw in enumerate(text.splitlines(), start=1):
            line = _strip_comment(raw, path)
            if not line:
                continue
            for rule, pattern, applies in FAKE_CLOCK_RULES:
                if applies(path) and pattern.search(line):
                    findings.append(f"{rel}:{number}: {rule}: {raw.strip()}")
    return findings


# ---------------------------------------------------------------- loosening

TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?)$")
TEST_FUNC = re.compile(r"^\s*(async\s+)?def\s+test_\w*\s*\(|^\s*(it|test)\s*\(\s*['\"`]", re.MULTILINE)
SKIP_MARKER = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\s*\(|unittest\.skip|@skip(If|Unless)?\b"
    r"|\b(it|test|describe)\.(skip|todo)\s*\(|\bx(it|describe)\s*\("
)


def _code_only(path: str, text: str) -> str:
    """Drop Python string literals and comments so quoted examples never count."""

    if not path.endswith(".py") or not text:
        return text
    try:
        tokens = [
            tok for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type not in (tokenize.STRING, tokenize.COMMENT)
        ]
        return tokenize.untokenize(tokens)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text


def _is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path)) and not path.startswith("tools/gates/")


def _git(*args: str, root: Path = REPO_ROOT) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _show(rev: str, path: str, root: Path) -> str:
    try:
        return _git("show", f"{rev}:{path}", root=root)
    except subprocess.CalledProcessError:
        return ""


def find_loosening(base: str = "origin/master", root: Path = REPO_ROOT) -> list[str]:
    merge_base = _git("merge-base", base, "HEAD", root=root).strip()
    changed = _git("diff", "--name-status", "--find-renames", merge_base, "HEAD", root=root)
    findings: list[str] = []
    before_total = after_total = 0
    for row in changed.splitlines():
        parts = row.split("\t")
        status, old = parts[0], parts[1]
        new = parts[-1]
        if not (_is_test_file(old) or _is_test_file(new)):
            continue
        before = _show(merge_base, old, root) if not status.startswith("A") else ""
        after = "" if status.startswith("D") else _show("HEAD", new, root)
        if status.startswith("D"):
            findings.append(f"{old}: test file deleted")
        before_total += len(TEST_FUNC.findall(before))
        after_total += len(TEST_FUNC.findall(after))
        added_skips = len(SKIP_MARKER.findall(_code_only(new, after))) - len(
            SKIP_MARKER.findall(_code_only(old, before))
        )
        if added_skips > 0:
            findings.append(f"{new}: {added_skips} new skip/xfail marker(s)")
    if after_total < before_total:
        findings.append(
            f"test functions in changed files dropped from {before_total} to {after_total}"
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="check", required=True)
    sub.add_parser("fake-clock")
    loosen = sub.add_parser("loosening")
    loosen.add_argument("--base", default="origin/master")
    args = parser.parse_args(argv)

    if args.check == "fake-clock":
        findings = find_fake_clock()
        what = "Global test configuration fakes the clock"
        fix = "Fake time only inside the individual test that needs it."
    else:
        findings = find_loosening(args.base)
        what = f"Tests are weaker than {args.base}"
        fix = "Fix the code instead of deleting, skipping, or xfailing tests."
    if findings:
        print(f"::error::{what}. {fix}")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"OK: {args.check} guard found nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
